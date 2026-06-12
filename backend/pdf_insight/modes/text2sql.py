"""SQL mode: LLM_MAKES_SQL_FROM_CHAT.

Deterministic SQLite ingestion (tables -> SQLite) -> delegate to the Text2SQL
LlmAgent (NL -> one read-only SELECT -> run -> answer).
"""
from __future__ import annotations

from typing import AsyncGenerator

from backend.shared.model import get_model

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from .. import config, storage
from ..stores.sqlite_store import (
    SQLiteStore,
    ingest_tables_to_sqlite,
    list_sql_schema,
    run_sql,
)
from ._common import _text_event

_TEXT2SQL_GUIDANCE = (
    "First call list_sql_schema to see the tables and columns. Then write a "
    "single SQLite SELECT, call run_sql with it, and answer from the rows. "
    "Never write or modify data."
)


def _text2sql_agent() -> LlmAgent:
    """Fresh Text2SQL specialist per call. An ADK agent may attach to only ONE
    parent, so build() must not reuse a module-level singleton (else a second
    build() — e.g. in a test — fails with a parent-conflict ValidationError).

    The store's dialect_hint is appended (same contract as corpus.py): SQLite
    stores cells as TEXT with thousands-commas, so without the hint a live model
    would SUM them as 0/garbage. This mode is always SQLite, so we read the hint
    off the class."""
    instruction = _TEXT2SQL_GUIDANCE + (
        f"\n{SQLiteStore.dialect_hint}" if SQLiteStore.dialect_hint else "")
    return LlmAgent(
        name="text2sql_agent",
        model=get_model(),
        description="Writes ONE read-only SQLite SELECT to answer questions over tables.",
        instruction=instruction,
        tools=[list_sql_schema, run_sql],
    )


class SqlModeAgent(BaseAgent):
    """Deterministic SQLite ingestion -> delegate to the Text2SQL LlmAgent."""

    text2sql: LlmAgent

    def __init__(self, name: str, text2sql: LlmAgent):
        super().__init__(name=name, text2sql=text2sql, sub_agents=[text2sql])

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        path = state.get("pdf_path")
        if not path:  # basename(None) would crash; report it like every other failure
            yield _text_event(self.name, "Could not ingest: no PDF path provided.")
            return
        # Cache the ingested db PER SOURCE PDF: re-ingest whenever the active PDF
        # changes. Gating only on "db_path exists" would silently answer a new PDF
        # from the previous document's db after a mid-session switch.
        if state.get("db_source_pdf") != path:
            # Location resolved centrally (storage.py) so SQLite and DuckDB — and
            # one day Postgres — all configure the same way.
            db_path = storage.sqlite_dsn(path, state)
            try:
                ingest_tables_to_sqlite(path, db_path)
            except Exception as e:  # noqa: BLE001
                yield _text_event(self.name, f"Could not ingest {path}: {e}")
                return
            # in-process so the tools read them this turn; state_delta persists them.
            state["db_path"] = db_path
            state["db_source_pdf"] = path
            yield Event(author=self.name,
                        actions=EventActions(
                            state_delta={"db_path": db_path, "db_source_pdf": path}))
        async for ev in self.text2sql.run_async(ctx):
            yield ev


def build() -> dict:
    """Return {mode_constant: agent} for the SQL strategy."""
    return {config.SQL_FROM_TEXT: SqlModeAgent("text2sql", _text2sql_agent())}
