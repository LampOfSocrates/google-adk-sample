"""Read-only DuckDB tools for the stash query mode (LLM_QUERIES_STASH).

These mirror sql_tools.py but over the PERSISTENT, multi-PDF DuckDB that
scripts/pdf_to_duckdb.py builds from the weekly report stash — so questions can
span every ingested report (e.g. week-over-week trends), not just one uploaded PDF.

Key difference from sql_tools: there is NO per-session ingestion. The stash DB
already exists on disk; these tools just read it. The connection is opened
read_only, AND `run_stash_sql` rejects anything that isn't a single SELECT/WITH —
two independent guards on the same trust boundary (the model writes the SQL).

Stash shape (see scripts/pdf_to_duckdb.py): a `pdf_tables` registry maps table
index -> title/columns; tables t00..t15 each accumulate rows across weeks, keyed by
`report_date`, with an `is_total` flag on each table's own subtotal row.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

import duckdb
from google.adk.tools import ToolContext

_DEFAULT_DB = os.path.join("data", "pdf_stash.duckdb")
_MAX_ROWS = 200
# DML/DDL keywords rejected even though the connection is already read_only.
_FORBIDDEN = re.compile(
    r"(?i)\b(insert|update|delete|drop|alter|create|attach|copy|pragma|replace|install|load|export)\b"
)


def _db_path(tool_context: ToolContext) -> str:
    return (
        tool_context.state.get("stash_db")
        or os.environ.get("PDF_STASH_DB")
        or _DEFAULT_DB
    )


def _jsonable(v):
    """DuckDB returns date/datetime/Decimal; coerce to JSON-safe scalars so the
    value survives ADK's function_response serialization."""
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, (int, float, str)) or v is None:
        return v
    return str(v)


def list_stash_schema(tool_context: ToolContext) -> dict:
    """List what's queryable in the PDF stash: the table registry + coverage.

    Call this FIRST. Returns the `pdf_tables` registry (table index -> physical
    table name, human title, and column names) plus how many reports are loaded and
    the report_date range they cover.
    """
    db = _db_path(tool_context)
    if not os.path.exists(db):
        return {"status": "error",
                "error_message": f"No stash DB at {db}. Run scripts/pdf_to_duckdb.py first."}
    con = duckdb.connect(db, read_only=True)
    try:
        reg = con.execute(
            "SELECT table_index, table_name, title, columns FROM pdf_tables ORDER BY table_index"
        ).fetchall()
        n_docs, d_min, d_max = con.execute(
            "SELECT COUNT(*), MIN(report_date), MAX(report_date) FROM documents"
        ).fetchone()
        tables = [
            {"index": r[0], "table": r[1], "title": r[2], "columns": json.loads(r[3])}
            for r in reg
        ]
    except duckdb.Error as e:  # noqa: BLE001 - surface a malformed/empty DB to the agent
        return {"status": "error", "error_message": f"Stash DB error: {e}"}
    finally:
        con.close()
    return {
        "status": "success",
        "documents": {"count": n_docs, "from": _jsonable(d_min), "to": _jsonable(d_max)},
        "note": "Each tNN accumulates rows across weeks (filter/GROUP BY report_date). "
                "Exclude each table's subtotal with `WHERE NOT is_total` before SUM/AVG.",
        "tables": tables,
    }


def run_stash_sql(query: str, tool_context: ToolContext) -> dict:
    """Execute a single read-only SELECT (or WITH ... SELECT) against the stash.

    Trust boundary: opens the DB read_only and rejects multiple statements or any
    write/DDL keyword. The model supplies `query`; these guards keep it harmless.
    """
    db = _db_path(tool_context)
    if not os.path.exists(db):
        return {"status": "error",
                "error_message": f"No stash DB at {db}. Run scripts/pdf_to_duckdb.py first."}

    q = query.strip().rstrip(";").strip()
    if ";" in q:
        return {"status": "error", "error_message": "Only a single statement is allowed."}
    if not re.match(r"(?is)^\s*(select|with)\b", q):
        return {"status": "error", "error_message": "Only SELECT / WITH queries are allowed."}
    if _FORBIDDEN.search(q):
        return {"status": "error", "error_message": "Write/DDL keywords are not allowed."}

    con = duckdb.connect(db, read_only=True)
    try:
        cur = con.execute(q)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [[_jsonable(c) for c in r] for r in cur.fetchmany(_MAX_ROWS)]
    except duckdb.Error as e:
        return {"status": "error", "error_message": f"SQL error: {e}"}
    finally:
        con.close()
    return {"status": "success", "columns": columns, "rows": rows, "row_count": len(rows)}
