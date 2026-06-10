"""Function tools for pdf_insight.

ADK idiom note: these are *tools* — deterministic functions an agent calls — not
agents. They self-serve their inputs from session state (the PDF path, set by the
coordinator) rather than taking them as model-supplied args, so the LLM never has
to know or hallucinate a file path. `tool_context` is auto-injected by ADK; it is
never a model-visible parameter.
"""
from __future__ import annotations

from shared import pdf_extractor as pdf
from google.adk.tools import ToolContext

from .config import MODES


def extract_tables(tool_context: ToolContext) -> dict:
    """Extract ALL tables from the active PDF and return them as text.

    Reads the PDF path from session state (`pdf_path`). Used by the auto router
    for the all-tables-as-text strategy.

    Returns:
        {"status": "success", "count": N, "text": "..."} or an error dict.
    """
    path = tool_context.state.get("pdf_path")
    if not path:
        return {"status": "error", "error_message": "No pdf_path in session state."}
    try:
        tables = pdf.extract_tables(path)
    except Exception as e:  # noqa: BLE001 - surface parse failures to the agent
        return {"status": "error", "error_message": f"Failed to read PDF: {e}"}
    text = pdf.tables_as_text(tables)
    tool_context.state["pdf_tables_text"] = text  # so an answering agent can reuse it
    return {"status": "success", "count": len(tables), "text": text}


def set_pdf_mode(mode: str, tool_context: ToolContext) -> dict:
    """Pin the PDF strategy for the rest of the session (the 'session' tier of
    the precedence chain). Pass 'auto' to hand mode selection back to the model.

    Args:
        mode: one of the MODES constants (LLM_GETS_* / LLM_MAKES_* /
            LLM_QUERIES_*), or 'auto'.
    """
    if mode not in MODES:
        return {
            "status": "error",
            "error_message": f"Unknown mode '{mode}'. Use one of: {sorted(MODES)}.",
        }
    tool_context.state["pdf_mode"] = mode
    return {"status": "success", "message": f"PDF mode pinned to {mode}."}
