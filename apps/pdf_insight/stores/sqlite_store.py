"""SQLite backend — the per-document store for LLM_MAKES_SQL_FROM_CHAT.

One PDF's tables are ingested into one SQLite file (one table `t<index>` per
detected table, all columns TEXT). Ingestion is read-write; queries go through a
connection pinned read-only with `PRAGMA query_only` (SQLite has no
transaction-level read-only, so the PRAGMA plus the inherited `validate_select`
guard are the trust boundary).

The module also exposes the ADK function tools the Text2SQL agent binds
(`list_sql_schema`, `run_sql`) and the ingestion entry point the coordinator
calls (`ingest_tables_to_sqlite`) — all thin adapters over `SQLiteStore`.
"""
from __future__ import annotations

import os
import re
import sqlite3

from shared import pdf
from google.adk.tools import ToolContext

from .base import SqlStore


def _sql_ident(name: str, fallback: str) -> str:
    """Coerce an arbitrary header into a safe SQLite identifier."""
    ident = re.sub(r"\W", "_", name)
    if not ident or ident[0].isdigit():
        ident = f"c_{ident}" if ident else fallback
    return ident


class SQLiteStore(SqlStore):
    """A single-document SQLite database of extracted PDF tables."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect_readonly(self):
        con = sqlite3.connect(self.db_path)
        con.execute("PRAGMA query_only = ON")  # engine-level read-only, Windows-safe
        return con

    def ingest(self, pdf_path: str) -> dict:
        """Parse every table from the PDF into its own SQLite table (deterministic).

        One detected PDF table -> one SQLite table named t<index>. Header cells
        become column names (sanitized + de-duplicated); data rows are TEXT.
        """
        tables = pdf.extract_tables(pdf_path)
        parent = os.path.dirname(self.db_path)
        if parent:  # create the storage dir (e.g. data/sqlite) on first ingest
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.db_path)  # read-write: ingestion writes
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
        return {"status": "success", "db_path": self.db_path, "tables": summary}

    def list_schema(self) -> dict:
        """Return the tables + columns of the ingested database."""
        con = self._connect_readonly()
        try:
            cur = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            schema = []
            for (tname,) in cur.fetchall():
                cols = [r[1] for r in con.execute(f'PRAGMA table_info("{tname}")').fetchall()]
                schema.append({"table": tname, "columns": cols})
        finally:
            con.close()
        return {"status": "success", "schema": schema}


# --- ADK function tools (thin adapters; read the db path from session state) ---
def ingest_tables_to_sqlite(pdf_path: str, db_path: str) -> dict:
    """Deterministic ingestion entry point the coordinator runs before Text2SQL."""
    return SQLiteStore(db_path).ingest(pdf_path)


def list_sql_schema(tool_context: ToolContext) -> dict:
    """Return the schema of the ingested SQLite database (Text2SQL calls this FIRST)."""
    db_path = tool_context.state.get("db_path")
    if not db_path:
        return {"status": "error", "error_message": "No db_path in session state."}
    return SQLiteStore(db_path).list_schema()


def run_sql(query: str, tool_context: ToolContext) -> dict:
    """Execute a single read-only SELECT against the ingested database.

    Trust boundary: the SQLiteStore validates the query and runs it under a
    read-only connection. The model supplies `query`; this guard keeps it from
    mutating data.
    """
    db_path = tool_context.state.get("db_path")
    if not db_path:
        return {"status": "error", "error_message": "No db_path in session state."}
    return SQLiteStore(db_path).run_select(query)
