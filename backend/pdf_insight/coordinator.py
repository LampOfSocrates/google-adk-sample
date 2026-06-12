"""PdfInsightAgent: the base agent that picks which mode agent runs.

Resolves the mode (request > session > env > default), records it + a UI banner,
then dispatches: a pinned mode goes straight to its specialist (no routing LLM);
`auto` defers to the reasoning router. Dispatch map comes from build_dispatch().
"""
from __future__ import annotations

import os
from typing import AsyncGenerator

from backend.shared.model import get_model

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from . import config
from .ingest import ingest_pdf_everywhere
from .modes._common import _resolve_pdf_path, _text_event, _user_text
from .stores import list_corpus_schema, run_corpus_sql
from .tools import extract_tables, set_pdf_mode

def build_router() -> LlmAgent:
    """Fresh auto-mode router per build.

    An ADK agent attaches to ONE parent, and the UI reloads this module to rebind
    the model — so a module-level singleton would hit 'already has a parent'.
    """
    return LlmAgent(
        name="pdf_router",
        model=get_model(),
        description="Auto mode: answers from the active PDF or the whole-corpus DB, "
                    "whichever the question needs.",
        # Router picks the source by question shape: single-doc -> active PDF
        # (extract_tables); cross-report / over-time -> corpus DB. Keep extract_tables
        # FIRST so the offline MockLlm (no corpus branch) still defaults to single-PDF.
        instruction=(
            "You answer questions about PDF documents of ANY kind — reports, financial "
            "statements, invoices, contracts, forms, research papers, manuals — whatever "
            "the user uploaded. Don't assume a domain; read what's actually there. Choose "
            "the data source from the question:\n"
            "- About THE current/uploaded document (this file, a named section or table, a "
            "single point in time) -> call extract_tables, then answer from its tables. When "
            "you aggregate across rows, ignore any subtotal/'Total' row so you don't double "
            "count, and keep the units shown in the column headers.\n"
            "- Spanning MANY documents or asking about change OVER TIME (a trend, timeseries, "
            "week-over-week, 'since', 'history', 'each week/month') -> use the corpus: call "
            "list_corpus_schema FIRST, then write ONE read-only SELECT and call "
            "run_corpus_sql (filter or GROUP BY report_date; exclude subtotals with "
            "WHERE NOT is_total before SUM/AVG), then answer from the rows.\n"
            "If the tables don't contain the answer, say so plainly rather than guessing. "
            "You may also pin a strategy for the rest of the session with set_pdf_mode."
        ),
        tools=[extract_tables, set_pdf_mode, list_corpus_schema, run_corpus_sql],
    )


class PdfInsightAgent(BaseAgent):
    """Config/reasoning router — see module docstring."""

    router: LlmAgent
    dispatch: dict

    def __init__(self, name, router, dispatch):
        super().__init__(name=name, router=router, dispatch=dispatch,
                         sub_agents=[router, *dispatch.values()])

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        text = _user_text(ctx)
        state = ctx.session.state
        mode = config.resolve_mode(
            config.parse_request_override(text), state, os.environ
        )
        path = _resolve_pdf_path(text, state)

        # Visible in-process (children read state) AND durable (state_delta on the banner).
        state["pdf_path"] = path
        state["active_pdf_mode"] = mode
        yield _text_event(
            self.name, f"▸ mode: {mode}  ·  pdf: {path}",
            state_delta={"active_pdf_mode": mode, "pdf_path": path},
        )

        # A new active PDF = an upload: ingest into SQLite (replace) + corpus (append),
        # whatever mode runs. Gated on `ingested_pdf` so it's once per doc. Failures
        # are reported, not fatal.
        if path and state.get("ingested_pdf") != path:
            try:
                summary = ingest_pdf_everywhere(path, state)
                banner = (f"▸ ingested {os.path.basename(path)} → "
                          f"sqlite:{summary['sqlite']['status']} "
                          f"corpus:{summary['corpus']['status']}")
            except Exception as e:  # noqa: BLE001 - surface, don't crash the turn
                banner = f"▸ could not ingest {path}: {e}"
            yield _text_event(self.name, banner, state_delta={
                "db_path": state.get("db_path"), "db_source_pdf": state.get("db_source_pdf"),
                "ingested_pdf": state.get("ingested_pdf")})

        target = self.router if mode == config.AUTO else self.dispatch[mode]
        async for ev in target.run_async(ctx):
            yield ev
