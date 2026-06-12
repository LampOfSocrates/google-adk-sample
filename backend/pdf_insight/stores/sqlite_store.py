"""SQLite backend — the per-document store for LLM_MAKES_SQL_FROM_CHAT.

One PDF's tables go into one SQLite file (one table `t<index>` each, all TEXT).
Ingestion is read-write; queries use a connection pinned read-only with
`PRAGMA query_only` — SQLite has no transaction-level read-only, so the PRAGMA
plus the inherited `validate_select` guard are the trust boundary.

Also exposes the Text2SQL agent's tools (`list_sql_schema`, `run_sql`) and the
ingestion entry point (`ingest_tables_to_sqlite`) — thin adapters over SQLiteStore.
"""
from __future__ import annotations

import os
import re
import sqlite3

from backend.shared import pdf_extractor as pdf
from google.adk.tools import ToolContext

from .base import SqlStore


def _sql_ident(name: str, fallback: str) -> str:
    """Coerce a header into a safe SQLite identifier.

    Deliberately not the same as duckdb_store._sql_ident. Here (single-doc) we
    preserve case and replace each non-word char 1:1 (`\\W`), e.g.
    'Vega($k)' -> 'Vega__k_' — names are only read back by the model, not joined
    across docs. The DuckDB corpus version lower-snakes for stable cross-report SQL.
    """
    ident = re.sub(r"\W", "_", name)
    if not ident or ident[0].isdigit():
        ident = f"c_{ident}" if ident else fallback
    return ident


class SQLiteStore(SqlStore):
    """A single-document SQLite database of extracted PDF tables."""

    # Cells are TEXT with thousands-commas, so SUM/AVG needs REPLACE + CAST.
    dialect_hint = (
        "Cells are stored as TEXT with thousands-commas, so before SUM/AVG/CAST a "
        "numeric column, strip commas first: CAST(REPLACE(\"col\", ',', '') AS REAL)."
    )

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect_readonly(self):
        con = sqlite3.connect(self.db_path)
        con.execute("PRAGMA query_only = ON")  # engine-level read-only, Windows-safe
        return con

    def ingest_pdf(self, pdf_path: str, report_date: str | None = None,
                   strategy: str = "lines", title_for=None) -> dict:
        """Parse every PDF table into its own SQLite table t<index> (deterministic).

        Header cells -> column names (sanitized + de-duped); data rows are TEXT.
        This is the per-document (REPLACE) store, so the corpus-only `report_date`/
        `title_for` args are ignored — see SqlStore.ingest_pdf.
        """
        tables = pdf.extract_tables(pdf_path, strategy=strategy)
        parent = os.path.dirname(self.db_path)
        if parent:  # create the storage dir on first ingest
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
                    while col in seen:  # keep unique after sanitizing
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
        """Return the tables + columns of the ingested db."""
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


# --- ADK function tools (thin adapters; db path from session state) ---
def ingest_tables_to_sqlite(pdf_path: str, db_path: str) -> dict:
    """Ingestion entry point the coordinator runs before Text2SQL."""
    return SQLiteStore(db_path).ingest_pdf(pdf_path)


def list_sql_schema(tool_context: ToolContext) -> dict:
    """Return the ingested SQLite schema (Text2SQL calls this first)."""
    db_path = tool_context.state.get("db_path")
    if not db_path:
        return {"status": "error", "error_message": "No db_path in session state."}
    return SQLiteStore(db_path).list_schema()


def run_sql(query: str, tool_context: ToolContext) -> dict:
    """Run one read-only SELECT against the ingested db.

    Trust boundary: SQLiteStore validates the model-supplied `query` and runs it
    read-only, so it can't mutate data.
    """
    db_path = tool_context.state.get("db_path")
    if not db_path:
        return {"status": "error", "error_message": "No db_path in session state."}
    return SQLiteStore(db_path).run_select(query)
