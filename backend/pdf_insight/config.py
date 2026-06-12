"""Mode resolution: pick one of five MODES via a precedence chain.

    request  >  session  >  env  >  default("auto" -> let the model decide)

request is a per-turn `mode: <NAME>` directive (doesn't touch session state);
session is state["pdf_mode"] (set by set_pdf_mode); env is PDF_MODE. Mode names
are verbose on purpose so the active strategy is unambiguous in logs/UI.
"""
from __future__ import annotations

import re

# The real strategies + the `auto` sentinel (no pin, LLM routes).
ALL_TABLES_AS_TEXT = "LLM_GETS_ALL_TABLES_AS_TEXT"
PDF_BYTES = "LLM_GETS_PDF_BYTES"
SQL_FROM_TEXT = "LLM_MAKES_SQL_FROM_CHAT"
SOME_TABLES_AS_TEXT = "LLM_GETS_SOME_TABLES_AS_TEXT"
# Whole-corpus mode: read-only DuckDB across every ingested report (data/pdf_corpus.duckdb),
# so questions can span weeks. No per-request pdf_path needed.
QUERY_CORPUS = "LLM_QUERIES_CORPUS"
AUTO = "auto"

MODES = {ALL_TABLES_AS_TEXT, PDF_BYTES, SQL_FROM_TEXT, SOME_TABLES_AS_TEXT,
         QUERY_CORPUS, AUTO}

_DIRECTIVE = re.compile(r"\bmode\s*[:=]\s*([A-Za-z_]+)", re.IGNORECASE)


def parse_request_override(text: str) -> str | None:
    """Pull a per-turn `mode: <NAME>` directive from the message, or None."""
    if not text:
        return None
    m = _DIRECTIVE.search(text)
    if not m:
        return None
    candidate = m.group(1).strip().upper() if m.group(1).upper() != "AUTO" else AUTO
    return candidate if candidate in MODES else None


def resolve_mode(request_override, state, env) -> str:
    """Apply the precedence chain. Returns a member of MODES (never None)."""
    for candidate in (request_override,               # request (highest)
                      (state or {}).get("pdf_mode"),  # session
                      (env or {}).get("PDF_MODE")):   # env
        if candidate and candidate in MODES:
            return candidate
    return AUTO                                       # default -> reasoning
