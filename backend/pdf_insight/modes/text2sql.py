"""SQL mode: LLM_MAKES_SQL_FROM_CHAT.

Ingest tables -> SQLite, then delegate to the Text2SQL LlmAgent
(NL -> one read-only SELECT -> run -> answer).
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
    "You answer questions over the tables of whatever PDF was uploaded — any kind "
    "of document, so don't assume a domain. First call list_sql_schema to see the "
    "tables and columns (read the column names to learn what the data means). Then "
    "write a single SQLite SELECT, call run_sql with it, and answer from the rows. "
    "Exclude any subtotal/'Total' row (e.g. WHERE label <> 'Total') before SUM/AVG "
    "so you don't double count. Never write or modify data."
)


def _text2sql_agent() -> LlmAgent:
    """Fresh Text2SQL agent per call — an ADK agent attaches to only one parent,
    so no module-level singleton (a second build(), e.g. in a test, would hit a
    parent-conflict error).

    Appends SQLite's dialect_hint (like corpus.py): cells are TEXT with
    thousands-commas, so without the hint a live model SUMs them as garbage.
    """
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
    """Ingest into SQLite, then delegate to the Text2SQL LlmAgent."""

    text2sql: LlmAgent

    def __init__(self, name: str, text2sql: LlmAgent):
        super().__init__(name=name, text2sql=text2sql, sub_agents=[text2sql])

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        path = state.get("pdf_path")
        if not path:  # basename(None) would crash; report it like any other failure
            yield _text_event(self.name, "Could not ingest: no PDF path provided.")
            return
        # Cache the db per source PDF: re-ingest when the active PDF changes.
        # Gating on "db_path exists" alone would answer a new PDF from the old
        # document's db after a mid-session switch.
        if state.get("db_source_pdf") != path:
            # Location resolved centrally (storage.py) so every backend configures alike.
            db_path = storage.sqlite_dsn(path, state)
            try:
                ingest_tables_to_sqlite(path, db_path)
            except Exception as e:  # noqa: BLE001
                yield _text_event(self.name, f"Could not ingest {path}: {e}")
                return
            # in-process so the tools read them this turn; state_delta persists them
            state["db_path"] = db_path
            state["db_source_pdf"] = path
            yield Event(author=self.name,
                        actions=EventActions(
                            state_delta={"db_path": db_path, "db_source_pdf": path}))
        async for ev in self.text2sql.run_async(ctx):
            yield ev


def build() -> dict:
    """Return {mode_constant: agent} for the SQL mode."""
    return {config.SQL_FROM_TEXT: SqlModeAgent("text2sql", _text2sql_agent())}
