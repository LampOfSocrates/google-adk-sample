"""Stash query mode: LLM_QUERIES_STASH.

Unlike the SQL mode (which ingests ONE uploaded PDF into a per-session SQLite),
this mode reads the PERSISTENT, multi-PDF DuckDB that scripts/pdf_to_duckdb.py
builds from the weekly report stash — so a question can span every ingested report
(e.g. week-over-week trends), not just one file.

No ingestion step, so this is a plain LlmAgent (not a custom BaseAgent): the stash
already exists on disk; the agent just lists its schema and writes read-only SQL.
"""
from __future__ import annotations

from shared.model import get_model

from google.adk.agents import LlmAgent

from .. import config
from ..stores.duckdb_store import list_stash_schema, run_stash_sql

def _stash_agent() -> LlmAgent:
    """Fresh stash specialist per call (one parent per ADK agent — see sql.py)."""
    return LlmAgent(
        name="stash_sql_agent",
        model=get_model(),
        description="Answers questions across the whole report stash via read-only DuckDB SQL.",
        instruction=(
            "You answer questions over a DuckDB of PDF report tables spanning many weekly "
            "reports.\n"
            "1) Call list_stash_schema FIRST — it returns the table registry (index -> title "
            "-> columns) and the report_date range covered.\n"
            "2) Write ONE read-only DuckDB SELECT (a WITH/CTE is fine), call run_stash_sql, "
            "then answer from the rows.\n"
            "Tables t00..t15 each accumulate rows across weeks: filter by report_date for a "
            "single week, or GROUP BY report_date for a trend. Exclude each table's own "
            "subtotal with `WHERE NOT is_total` before SUM/AVG. Never write or modify data."
        ),
        tools=[list_stash_schema, run_stash_sql],
    )


def build() -> dict:
    """Return {mode_constant: agent} for the stash query strategy."""
    return {config.QUERY_STASH: _stash_agent()}
