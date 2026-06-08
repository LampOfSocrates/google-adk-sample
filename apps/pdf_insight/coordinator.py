"""PdfInsightAgent — the base agent that decides which mode agent to use.

Hybrid config/reasoning router: it resolves the mode from the precedence chain
(request > session > env > default), records it on the session + a one-line UI
banner, then dispatches:
  * a pinned mode -> straight to its specialist (DETERMINISTIC, no routing LLM);
  * `auto`        -> defers to the reasoning `router_agent` LlmAgent.

The dispatch map is supplied by `modes.build_dispatch()` so the coordinator never
hard-codes the mode list.
"""
from __future__ import annotations

import os
from typing import AsyncGenerator

from shared.model import get_model

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from . import config
from .ingest import ingest_pdf_everywhere
from .modes._common import _resolve_pdf_path, _text_event, _user_text
from .tools import extract_tables, set_pdf_mode

def build_router() -> LlmAgent:
    """Fresh auto-mode router per build. An ADK agent attaches to ONE parent, and
    the Streamlit UI reloads the agent module (to rebind the model on a backend
    switch) which re-builds root_agent — so the router must NOT be a module-level
    singleton, else the second build hits 'already has a parent'."""
    return LlmAgent(
        name="pdf_router",
        model=get_model(),
        description="Auto mode: reasons about the question and pulls tables as needed.",
        instruction=(
            "Answer questions about the active PDF. Call extract_tables to read its "
            "tables, then answer from them. You may pin a strategy with set_pdf_mode."
        ),
        tools=[extract_tables, set_pdf_mode],
    )


class PdfInsightAgent(BaseAgent):
    """Hybrid config/reasoning router (see module docstring)."""

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

        # Make path/mode visible immediately (children read state in-process) and
        # durable (state_delta on the banner event the UI shows).
        state["pdf_path"] = path
        state["active_pdf_mode"] = mode
        yield _text_event(
            self.name, f"▸ mode: {mode}  ·  pdf: {path}",
            state_delta={"active_pdf_mode": mode, "pdf_path": path},
        )

        # Treat a NEW active PDF as an upload: eagerly ingest it into the
        # per-document SQLite (replace) AND append it to the corpus, regardless of
        # which mode runs this turn. Gated on `ingested_pdf` so it happens once per
        # document. Ingestion failures are reported, not fatal — the turn proceeds.
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
