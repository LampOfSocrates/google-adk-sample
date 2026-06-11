"""pdf_insight app: one coordinator, four PDF strategies, hybrid routing.

This module just assembles the graph ADK discovers (`root_agent`). The pieces:

  * PdfInsightAgent (custom BaseAgent) -- the base/router agent that decides which
    mode agent to use. Pinned mode -> deterministic dispatch; `auto` -> reasoning
    LlmAgent. See `coordinator.py`.
  * Per-mode specialists, one module each under `modes/`:
      pdfpart.py  -> LLM_GETS_ALL_TABLES_AS_TEXT, LLM_GETS_SOME_TABLES_AS_TEXT
      text2sql.py -> LLM_MAKES_SQL_FROM_CHAT  (tables -> SQLite -> Text2SQL agent)
      pdfbytes.py -> LLM_GETS_PDF_BYTES       (raw PDF to the model; any doc-capable backend)
      corpus.py   -> LLM_QUERIES_CORPUS       (whole-corpus SQL store)
    `modes.build_dispatch()` merges their registries into the dispatch map.

Why custom BaseAgents instead of more LlmAgents? Mode-selection-when-pinned, table
extraction, and SQLite ingestion are all deterministic. Deterministic work belongs
in code/tools, never in an LlmAgent. `root_agent` is what ADK discovers.
"""
from __future__ import annotations

from .coordinator import PdfInsightAgent, build_router
from .modes import build_dispatch

root_agent = PdfInsightAgent(
    name="pdf_insight",
    router=build_router(),
    dispatch=build_dispatch(),
)
