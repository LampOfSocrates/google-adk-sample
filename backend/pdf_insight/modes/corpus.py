"""Corpus query mode: LLM_QUERIES_CORPUS.

Queries the persistent multi-PDF corpus (vs SQL mode's single per-session PDF),
so a question can span every ingested report. No ingestion step, so it's a plain
LlmAgent parameterized by the configured store (DuckDB today, Postgres tomorrow);
backend is fixed at build time via CORPUS_BACKEND.
"""
from __future__ import annotations

from backend.shared.model import get_model

from google.adk.agents import LlmAgent

from .. import config
from ..stores import SqlStore, get_corpus_store, list_corpus_schema, run_corpus_sql

# Engine-neutral: the schema specifics are about the data model, same on any SQL engine.
_CORPUS_GUIDANCE = (
    "You answer questions over a SQL database of tables extracted from many PDF "
    "documents — possibly different KINDS of document (reports, statements, forms). "
    "Don't assume a domain; read each view's columns to learn what it holds.\n"
    "1) Call list_corpus_schema FIRST — it returns the queryable tables (each a view "
    "with a `table` name, title and columns) and the report_date range covered.\n"
    "2) Write ONE read-only SELECT (a WITH/CTE is fine) against a table name from the "
    "registry, call run_corpus_sql, then answer from the rows.\n"
    "Each registry entry is a VIEW that unions every report sharing that table's "
    "shape, so its rows span reports: filter by report_date for a single report, or "
    "GROUP BY report_date for a trend. Exclude each table's own subtotal with "
    "`WHERE NOT is_total` before SUM/AVG. Never write or modify data."
)


def build_corpus_agent(store: SqlStore) -> LlmAgent:
    """Corpus agent for the given store; appends the store's dialect_hint
    (empty for DuckDB) so the prompt stays engine-neutral otherwise."""
    instruction = _CORPUS_GUIDANCE + (f"\n{store.dialect_hint}" if store.dialect_hint else "")
    return LlmAgent(
        name="corpus",
        model=get_model(),
        description="Answers questions across the whole report corpus via read-only SQL.",
        instruction=instruction,
        tools=[list_corpus_schema, run_corpus_sql],
    )


def build() -> dict:
    """Return {mode_constant: agent}; backend fixed here (CORPUS_BACKEND)."""
    return {config.QUERY_CORPUS: build_corpus_agent(get_corpus_store())}
