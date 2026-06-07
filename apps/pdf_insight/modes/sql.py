"""SQL mode: LLM_MAKES_SQL_FROM_CHAT.

Deterministic SQLite ingestion (tables -> SQLite) -> delegate to the Text2SQL
LlmAgent (NL -> one read-only SELECT -> run -> answer).
"""
from __future__ import annotations

import os
import tempfile
from typing import AsyncGenerator

from shared.model import get_model

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from .. import config
from ..sql_tools import ingest_tables_to_sqlite, list_sql_schema, run_sql
from ._common import _text_event

def _text2sql_agent() -> LlmAgent:
    """Fresh Text2SQL specialist per call. An ADK agent may attach to only ONE
    parent, so build() must not reuse a module-level singleton (else a second
    build() — e.g. in a test — fails with a parent-conflict ValidationError)."""
    return LlmAgent(
        name="text2sql_agent",
        model=get_model(),
        description="Writes ONE read-only SQLite SELECT to answer questions over tables.",
        instruction=(
            "First call list_sql_schema to see the tables and columns. Then write a "
            "single SQLite SELECT, call run_sql with it, and answer from the rows. "
            "Never write or modify data."
        ),
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
        path = ctx.session.state.get("pdf_path")
        # Keep the SQLite file out of the source tree — derive it under the OS temp
        # dir from the PDF name so re-runs reuse it but the repo stays clean.
        default_db = os.path.join(tempfile.gettempdir(), os.path.basename(path) + ".sqlite")
        db_path = ctx.session.state.get("db_path") or default_db
        if not ctx.session.state.get("db_path"):
            try:
                ingest_tables_to_sqlite(path, db_path)
            except Exception as e:  # noqa: BLE001
                yield _text_event(self.name, f"Could not ingest {path}: {e}")
                return
            ctx.session.state["db_path"] = db_path  # in-process: tools read it now
            yield Event(author=self.name,  # persist so ingestion is reused next turn
                        actions=EventActions(state_delta={"db_path": db_path}))
        async for ev in self.text2sql.run_async(ctx):
            yield ev


def build() -> dict:
    """Return {mode_constant: agent} for the SQL strategy."""
    return {config.SQL_FROM_TEXT: SqlModeAgent("sql_mode", _text2sql_agent())}
