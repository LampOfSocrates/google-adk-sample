"""DuckDB backend — the whole-corpus store for LLM_QUERIES_CORPUS.

Reads the PERSISTENT, multi-PDF DuckDB that scripts/pdf_to_duckdb.py builds from
the weekly report corpus, so a question can span every ingested report. There is
NO per-session ingestion here; the store only reads (connection opened
read_only). Schema is richer than SQLite's: a `pdf_tables` registry maps table
index -> title/columns and a `documents` table records report coverage.

This module is just the `DuckDBStore` engine; the backend-neutral corpus tools
live in `corpus_tools.py` and pick this store (or Postgres) by config.
"""
from __future__ import annotations

import json
import os

import duckdb

from .base import SqlStore, jsonable

# Back-compat alias: tests refer to this module's coercion helper as `_jsonable`.
_jsonable = jsonable


class DuckDBStore(SqlStore):
    """The single whole-corpus DuckDB database (read-only, multi-report corpus)."""

    dialect_hint = ""  # corpus columns are typed DOUBLE/DATE; queries are ANSI-clean

    def __init__(self, db_path: str):
        self.db_path = db_path

    def available(self) -> str | None:
        if not os.path.exists(self.db_path):
            return f"No corpus DB at {self.db_path}. Run scripts/pdf_to_duckdb.py first."
        return None

    def _connect_readonly(self):
        return duckdb.connect(self.db_path, read_only=True)

    def list_schema(self) -> dict:
        """List what's queryable: the table registry + report coverage. Call FIRST."""
        unavailable = self.available()
        if unavailable:
            return {"status": "error", "error_message": unavailable}
        con = self._connect_readonly()
        try:
            reg = con.execute(
                "SELECT table_index, table_name, title, columns FROM pdf_tables "
                "ORDER BY table_index"
            ).fetchall()
            n_docs, d_min, d_max = con.execute(
                "SELECT COUNT(*), MIN(report_date), MAX(report_date) FROM documents"
            ).fetchone()
            tables = [
                {"index": r[0], "table": r[1], "title": r[2], "columns": json.loads(r[3])}
                for r in reg
            ]
        except duckdb.Error as e:  # noqa: BLE001 - malformed/empty DB -> error dict
            return {"status": "error", "error_message": f"Corpus DB error: {e}"}
        finally:
            con.close()
        return {
            "status": "success",
            "documents": {"count": n_docs, "from": jsonable(d_min), "to": jsonable(d_max)},
            "note": "Each tNN accumulates rows across weeks (filter/GROUP BY report_date). "
                    "Exclude each table's subtotal with `WHERE NOT is_total` before SUM/AVG.",
            "tables": tables,
        }

# The corpus function tools live in corpus_tools.py now — backend-neutral, so they
# pick DuckDB or Postgres by config instead of hardcoding this engine.
