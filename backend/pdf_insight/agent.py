"""Assembles `root_agent` (what ADK discovers): one coordinator + per-mode specialists.

PdfInsightAgent routes to a pinned mode's specialist or, for `auto`, the reasoning
router. Specialists live in `modes/` and register via `modes.build_dispatch()`.
Deterministic work (pinned dispatch, table extract, ingest) stays out of LlmAgents.
"""
from __future__ import annotations

from .coordinator import PdfInsightAgent, build_router
from .modes import build_dispatch

root_agent = PdfInsightAgent(
    name="pdf_insight",
    router=build_router(),
    dispatch=build_dispatch(),
)
