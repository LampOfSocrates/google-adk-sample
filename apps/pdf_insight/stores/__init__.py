"""SQL backend stores for pdf_insight.

One `SqlStore` abstraction, one read-only trust boundary, three engines:
  * SQLiteStore  — per-document, ingest-on-demand (LLM_MAKES_SQL_FROM_CHAT)
  * DuckDBStore  — whole-corpus, read-only corpus (LLM_QUERIES_CORPUS)
  * PostgresStore — server backend, skeleton (slots in by implementing two methods)

The mode agents import the function tools from the engine module they use; this
package re-exports the store classes and the shared guard for callers that want
them directly.
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
