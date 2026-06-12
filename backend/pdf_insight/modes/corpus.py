"""Corpus query mode: LLM_QUERIES_CORPUS.

Unlike the SQL mode (which ingests ONE uploaded PDF into a per-session SQLite),
this mode reads the PERSISTENT, multi-PDF corpus that scripts/pdf_insight/pdf_to_duckdb.py
builds from the weekly report corpus — so a question can span every ingested
report (e.g. week-over-week trends), not just one file.

No ingestion step, so this is a plain LlmAgent. The agent is ONE factory
parameterized by the configured store (DuckDB today, Postgres tomorrow): the
prompt is engine-neutral except for the store's own `dialect_hint`, and the tools
are the backend-neutral corpus tools. The backend is fixed at build (config) time
via CORPUS_BACKEND — you don't swap engines mid-conversation.
"""
from __future__ import annotations

from backend.shared.model import get_model

from google.adk.agents import LlmAgent

from .. import config
from ..stores import SqlStore, get_corpus_store, list_corpus_schema, run_corpus_sql

# Engine-neutral guidance. The schema specifics (the registry, report_date,
# is_total, the union views) are about the data model, the same on any SQL engine.
_CORPUS_GUIDANCE = (
    "You answer questions over a SQL database of tables extracted from many PDF "
    "reports — possibly different KINDS of report.\n"
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
    """One corpus agent, parameterized by the configured store. The store's
    `dialect_hint` (empty for DuckDB) is appended so the prompt stays engine-neutral
    except for whatever SQL-dialect note the backend actually needs."""
    instruction = _CORPUS_GUIDANCE + (f"\n{store.dialect_hint}" if store.dialect_hint else "")
    return LlmAgent(
        name="corpus",
        model=get_model(),
        description="Answers questions across the whole report corpus via read-only SQL.",
        instruction=instruction,
        tools=[list_corpus_schema, run_corpus_sql],
    )


def build() -> dict:
    """Return {mode_constant: agent}; backend fixed here at build time (CORPUS_BACKEND)."""
    return {config.QUERY_CORPUS: build_corpus_agent(get_corpus_store())}
