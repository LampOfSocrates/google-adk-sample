"""SQLite ingestion + query tools for the LLM_MAKES_SQL_FROM_CHAT mode.

ADK idiom note on the trust boundary: `run_sql` is the security gate. The model
writes the SQL, so the *tool* — not the prompt — enforces read-only access. Never
relax the single-SELECT guard to "be helpful"; a prompt can be talked into
writing DELETE, a guarded tool cannot.

`ingest_tables_to_sqlite` is deterministic and is run by the coordinator (not the
LLM) to build the database before the Text2SQL agent ever sees the schema.
"""
from __future__ import annotations

import re
import sqlite3

from shared import pdf
from google.adk.tools import ToolContext

_MAX_ROWS = 100  # cap rows returned to the model, regardless of the query


def _sql_ident(name: str, fallback: str) -> str:
    """Coerce an arbitrary header into a safe SQLite identifier."""
    ident = re.sub(r"\W", "_", name)
    if not ident or ident[0].isdigit():
        ident = f"c_{ident}" if ident else fallback
    return ident


def ingest_tables_to_sqlite(pdf_path: str, db_path: str) -> dict:
    """Parse every table from the PDF into its own SQLite table (deterministic).

    One detected PDF table -> one SQLite table named t<index>. Header cells become
    column names (sanitized + de-duplicated); data rows are inserted as TEXT.

    Returns:
        {"status": "success", "db_path": ..., "tables": [{table, columns, rows}]}.
    """
    tables = pdf.extract_tables(pdf_path)
    conn = sqlite3.connect(db_path)
    summary = []
    try:
        cur = conn.cursor()
        for t in tables:
            tname = f"t{t['index']}"
            cols, seen = [], set()
            for i, raw in enumerate(t["header"]):
                col = _sql_ident(raw, f"col_{i}")
                while col in seen:  # keep unique after SQL-sanitizing
                    col = f"{col}_x"
                seen.add(col)
                cols.append(col)
            col_defs = ", ".join(f'"{c}" TEXT' for c in cols)
            cur.execute(f'DROP TABLE IF EXISTS "{tname}"')
            cur.execute(f'CREATE TABLE "{tname}" ({col_defs})')
            placeholders = ", ".join("?" for _ in cols)
            for row in t["rows"]:
                padded = (list(row) + [None] * len(cols))[: len(cols)]
                cur.execute(f'INSERT INTO "{tname}" VALUES ({placeholders})', padded)
            summary.append({"table": tname, "columns": cols, "rows": len(t["rows"])})
        conn.commit()
    finally:
        conn.close()
    return {"status": "success", "db_path": db_path, "tables": summary}


def list_sql_schema(tool_context: ToolContext) -> dict:
    """Return the schema (tables + columns) of the ingested SQLite database.

    The Text2SQL agent calls this FIRST so it knows what it can query. Reads the
    db path from session state (`db_path`), set during ingestion.
    """
    db_path = tool_context.state.get("db_path")
    if not db_path:
        return {"status": "error", "error_message": "No db_path in session state."}
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        schema = []
        for (tname,) in cur.fetchall():
            cur.execute(f'PRAGMA table_info("{tname}")')
            cols = [r[1] for r in cur.fetchall()]
            schema.append({"table": tname, "columns": cols})
    finally:
        conn.close()
    return {"status": "success", "schema": schema}


def run_sql(query: str, tool_context: ToolContext) -> dict:
    """Execute a single read-only SELECT against the ingested database.

    Trust boundary: rejects anything that is not exactly one SELECT statement and
    caps the rows returned. The model supplies `query`; this guard is what keeps
    it from mutating data.
    """
    db_path = tool_context.state.get("db_path")
    if not db_path:
        return {"status": "error", "error_message": "No db_path in session state."}

    q = query.strip().rstrip(";").strip()
    if ";" in q:
        return {"status": "error", "error_message": "Only a single statement is allowed."}
    if not re.match(r"(?is)^\s*select\b", q):
        return {"status": "error", "error_message": "Only SELECT queries are allowed."}
    if re.search(r"(?i)\b(insert|update|delete|drop|alter|create|attach|pragma|replace)\b", q):
        return {"status": "error", "error_message": "Write/DDL keywords are not allowed."}

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(q)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchmany(_MAX_ROWS)]
    except sqlite3.Error as e:
        return {"status": "error", "error_message": f"SQL error: {e}"}
    finally:
        conn.close()
    return {"status": "success", "columns": columns, "rows": rows, "row_count": len(rows)}
