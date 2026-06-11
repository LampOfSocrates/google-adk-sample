"""Postgres backend — skeleton, not yet wired to a mode.

Present so the abstraction is real rather than aspirational: a server engine
differs from the embedded ones in exactly two places — how you open a read-only
handle, and how you describe the schema. Everything else (the trust-boundary
guard, `run_select`, the result shape) is inherited from `SqlStore` unchanged.

To make it live: add `psycopg` to requirements, implement the two methods below,
resolve the DSN via a `storage.postgres_dsn(state)` (mirroring `duckdb_dsn`), and
register a mode that binds list/run tools just like the SQLite and DuckDB modes.
"""
from __future__ import annotations

from .base import SqlStore


class PostgresStore(SqlStore):
    """A server-backed store of extracted PDF tables (placeholder)."""

    dialect_hint = (
        "Use Postgres syntax: ILIKE for case-insensitive match, date_trunc('week', "
        "report_date) for period grouping, and :: casts (e.g. col::numeric)."
    )

    def __init__(self, dsn: str):
        self.dsn = dsn

    def _connect_readonly(self):
        # Real impl: a psycopg connection, then enforce read-only at the engine —
        #   con = psycopg.connect(self.dsn)
        #   con.execute("SET TRANSACTION READ ONLY")   # or connect as a read-only role
        #   return con
        raise NotImplementedError(
            "PostgresStore is a placeholder: add psycopg and implement "
            "_connect_readonly (open the DSN, SET TRANSACTION READ ONLY).")

    def list_schema(self) -> dict:
        # Real impl: query information_schema.columns for the ingested tNN tables,
        # shaped like DuckDBStore.list_schema (a single corpus target).
        raise NotImplementedError(
            "PostgresStore is a placeholder: implement list_schema via "
            "information_schema when psycopg is added.")

    def ingest_pdf(self, pdf_path: str, report_date: str | None = None,
                   strategy: str = "lines", title_for=None) -> dict:
        # Real impl: COPY/INSERT the extracted tables into the same canonical
        # schema DuckDBStore.ingest_pdf builds (documents + doc_tables + families +
        # per-doc physical tables + union views).
        raise NotImplementedError(
            "PostgresStore is a placeholder: implement ingest_pdf when psycopg "
            "is added (write the canonical documents/doc_tables/families schema).")
