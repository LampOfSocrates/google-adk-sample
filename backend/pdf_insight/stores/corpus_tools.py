"""Backend-neutral corpus query tools + the config-time backend selector.

Which engine holds the corpus (DuckDB today, Postgres tomorrow) is a deploy-time
choice via CORPUS_BACKEND. `get_corpus_store` resolves it; the two tools below
are thin engine-agnostic adapters the corpus agent binds.
"""
from __future__ import annotations

import os

from google.adk.tools import ToolContext

from .. import storage
from .base import SqlStore
from .duckdb_store import DuckDBStore
from .postgres_store import PostgresStore


def get_corpus_store(state: dict | None = None) -> SqlStore:
    """Return the configured corpus store. Backend fixed via CORPUS_BACKEND
    (default 'duckdb'); storage.py resolves the DSN (state > env > default)."""
    backend = os.environ.get("CORPUS_BACKEND", "duckdb")
    if backend == "duckdb":
        return DuckDBStore(storage.duckdb_dsn(state))
    if backend == "postgres":
        return PostgresStore(storage.postgres_dsn(state))
    raise ValueError(
        f"Unknown CORPUS_BACKEND {backend!r}; expected 'duckdb' or 'postgres'.")


# --- ADK function tools (resolve the configured store, delegate) ---
def list_corpus_schema(tool_context: ToolContext) -> dict:
    """List the corpus registry + coverage (the corpus agent calls this first)."""
    return get_corpus_store(tool_context.state).list_schema()


def run_corpus_sql(query: str, tool_context: ToolContext) -> dict:
    """Run one read-only SELECT/WITH against the corpus. Trust boundary: store
    opens read-only and the shared guard rejects writes/multi-statements."""
    return get_corpus_store(tool_context.state).run_select(query)
