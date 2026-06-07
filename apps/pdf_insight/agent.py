"""pdf_insight app: one coordinator, four PDF strategies, hybrid routing.

This module just assembles the graph ADK discovers (`root_agent`). The pieces:

  * PdfCoordinator (custom BaseAgent) -- the base/router agent that decides which
    mode agent to use. Pinned mode -> deterministic dispatch; `auto` -> reasoning
    LlmAgent. See `coordinator.py`.
  * Per-mode specialists, one module each under `modes/`:
      tables.py -> LLM_GETS_ALL_TABLES_AS_TEXT, LLM_GETS_SOME_TABLES_AS_TEXT
      sql.py    -> LLM_MAKES_SQL_FROM_CHAT  (tables -> SQLite -> Text2SQL agent)
      native.py -> LLM_GETS_PDF_BYTES       (native multimodal upload; Gemini only)
    `modes.build_dispatch()` merges their registries into the dispatch map.

Why custom BaseAgents instead of more LlmAgents? Mode-selection-when-pinned, table
extraction, and SQLite ingestion are all deterministic. Deterministic work belongs
in code/tools, never in an LlmAgent. `root_agent` is what ADK discovers.
"""
from __future__ import annotations

from .coordinator import PdfCoordinator, router_agent
from .modes import build_dispatch

root_agent = PdfCoordinator(
    name="pdf_insight",
    router=router_agent,
    dispatch=build_dispatch(),
)
