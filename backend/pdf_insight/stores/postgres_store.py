"""Postgres backend — skeleton, not yet wired to a mode.

Proves the abstraction is real: a server engine differs in just two places —
opening a read-only handle and describing the schema. Everything else (the
guard, `run_select`, result shape) is inherited from `SqlStore`.

To make it live: add `psycopg`, implement the two methods below, add a
`storage.postgres_dsn(state)`, and register a mode like the SQLite/DuckDB ones.
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
        # Real impl: psycopg.connect(self.dsn), then SET TRANSACTION READ ONLY
        # (or connect as a read-only role).
        raise NotImplementedError(
            "PostgresStore is a placeholder: add psycopg and implement "
            "_connect_readonly (open the DSN, SET TRANSACTION READ ONLY).")

    def list_schema(self) -> dict:
        # Real impl: query information_schema.columns, shaped like
        # DuckDBStore.list_schema.
        raise NotImplementedError(
            "PostgresStore is a placeholder: implement list_schema via "
            "information_schema when psycopg is added.")

    def ingest_pdf(self, pdf_path: str, report_date: str | None = None,
                   strategy: str = "lines", title_for=None) -> dict:
        # Real impl: COPY/INSERT into the same canonical schema
        # DuckDBStore.ingest_pdf builds (documents + doc_tables + families +
        # per-doc tables + union views).
        raise NotImplementedError(
            "PostgresStore is a placeholder: implement ingest_pdf when psycopg "
            "is added (write the canonical documents/doc_tables/families schema).")
