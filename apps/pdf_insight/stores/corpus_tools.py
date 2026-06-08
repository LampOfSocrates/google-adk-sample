"""Backend-neutral corpus query tools + the config-time backend selector.

The corpus mode answers over the whole-report corpus. WHICH engine holds it —
DuckDB today, Postgres tomorrow — is a DEPLOY-time choice via CORPUS_BACKEND, not
something the mode hardcodes. `get_corpus_store` resolves that choice once; the
two function tools below are thin adapters the corpus agent binds regardless of
engine (they were previously hardcoded to DuckDB inside duckdb_store.py).
"""
from __future__ import annotations

import os

from google.adk.tools import ToolContext

from .. import storage
from .base import SqlStore
from .duckdb_store import DuckDBStore
from .postgres_store import PostgresStore


def get_corpus_store(state: dict | None = None) -> SqlStore:
    """Return the configured corpus store. Backend is fixed at config time via
    CORPUS_BACKEND (default 'duckdb'); the DSN is resolved by storage.py with the
    usual state > env > default precedence."""
    backend = os.environ.get("CORPUS_BACKEND", "duckdb")
    if backend == "duckdb":
        return DuckDBStore(storage.duckdb_dsn(state))
    if backend == "postgres":
        return PostgresStore(storage.postgres_dsn(state))
    raise ValueError(
        f"Unknown CORPUS_BACKEND {backend!r}; expected 'duckdb' or 'postgres'.")


# --- ADK function tools (engine-agnostic: resolve the configured store, delegate) ---
def list_corpus_schema(tool_context: ToolContext) -> dict:
    """List the corpus registry + coverage (the corpus agent calls this FIRST)."""
    return get_corpus_store(tool_context.state).list_schema()


def run_corpus_sql(query: str, tool_context: ToolContext) -> dict:
    """Execute a single read-only SELECT/WITH against the corpus. Trust boundary:
    the store opens read-only and the shared guard rejects writes/multi-statements."""
    return get_corpus_store(tool_context.state).run_select(query)
