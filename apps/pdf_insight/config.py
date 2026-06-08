"""Mode resolution for pdf_insight.

The app answers PDF questions in one of five MODES. Which one runs is resolved
from a precedence chain, then (only when nothing is pinned) by LLM reasoning:

    request  >  session  >  env  >  default("auto" -> let the model decide)

  * request: a per-turn override. Parsed from the user message via a
    `mode: <NAME>` directive (convenient in `adk web`) or passed programmatically.
    Per-turn only — it does NOT mutate session state.
  * session: `state["pdf_mode"]`, set by the set_pdf_mode tool; lasts the session.
  * env: `PDF_MODE` in the environment / .env; the baseline default.

Mode names are deliberately verbose so the active strategy is unambiguous in logs
and in the UI banner.
"""
from __future__ import annotations

import re

# The real strategies + the `auto` sentinel meaning "no pin, the LLM routes".
ALL_TABLES_AS_TEXT = "LLM_GETS_ALL_TABLES_AS_TEXT"
PDF_BYTES = "LLM_GETS_PDF_BYTES"
SQL_FROM_TEXT = "LLM_MAKES_SQL_FROM_CHAT"
SOME_TABLES_AS_TEXT = "LLM_GETS_SOME_TABLES_AS_TEXT"
# Whole-corpus query mode: read-only DuckDB across every ingested report (not a
# single PDF). Backed by data/pdf_corpus.duckdb (scripts/pdf_to_duckdb.py) and the
# the DuckDB store, so questions can span weeks. Needs no per-request pdf_path.
QUERY_CORPUS = "LLM_QUERIES_CORPUS"
AUTO = "auto"

MODES = {ALL_TABLES_AS_TEXT, PDF_BYTES, SQL_FROM_TEXT, SOME_TABLES_AS_TEXT,
         QUERY_CORPUS, AUTO}

_DIRECTIVE = re.compile(r"\bmode\s*[:=]\s*([A-Za-z_]+)", re.IGNORECASE)


def parse_request_override(text: str) -> str | None:
    """Pull a per-turn `mode: <NAME>` directive out of a user message, or None."""
    if not text:
        return None
    m = _DIRECTIVE.search(text)
    if not m:
        return None
    candidate = m.group(1).strip().upper() if m.group(1).upper() != "AUTO" else AUTO
    return candidate if candidate in MODES else None


def resolve_mode(request_override, state, env) -> str:
    """Apply the precedence chain. Returns a member of MODES (never None)."""
    for candidate in (request_override,            # 1. per-request (highest)
                      (state or {}).get("pdf_mode"),  # 2. per-session
                      (env or {}).get("PDF_MODE")):   # 3. env baseline
        if candidate and candidate in MODES:
            return candidate
    return AUTO                                    # 4. default -> reasoning
