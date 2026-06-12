"""SQL stores for pdf_insight.

One `SqlStore` abstraction, one read-only guard, three engines: SQLiteStore
(per-document), DuckDBStore (whole corpus), PostgresStore (skeleton — slots in
by implementing two methods). Re-exports the store classes and the guard.
"""
from .base import SqlStore, jsonable, validate_select
from .corpus_tools import get_corpus_store, list_corpus_schema, run_corpus_sql
from .duckdb_store import DuckDBStore
from .postgres_store import PostgresStore
from .sqlite_store import SQLiteStore

__all__ = [
    "SqlStore", "validate_select", "jsonable",
    "SQLiteStore", "DuckDBStore", "PostgresStore",
    "get_corpus_store", "list_corpus_schema", "run_corpus_sql",
]
