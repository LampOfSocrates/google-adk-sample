"""Native bytes mode: LLM_GETS_PDF_BYTES.

Hands the raw PDF to the model — no extraction, no SQL. A before_model_callback
injects the PDF onto the outgoing request so we reuse the normal LlmAgent path
(streaming, event shaping, UI). Per backend:

  * gemini  — native inline document part.
  * bedrock — Anthropic reads it via ADK's LiteLlm data-URI document content.
  * openai / azure — need a Files-API upload referenced by id. ADK would upload
    our inline bytes but drops the filename, so OpenAI can't detect the PDF MIME
    and rejects it; we upload it ourselves with a real filename instead.
  * deepseek — no document input, so it rejects; error surfaces in chat. Use a
    tables/SQL mode there.
  * mock — ignores the bytes, returns its offline placeholder.
"""
from __future__ import annotations

import os

from backend.shared.model import backend, get_model

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from .. import config

# Providers whose chat API needs a Files-API upload, not inline bytes (mirrors
# ADK lite_llm._FILE_ID_REQUIRED_PROVIDERS). We upload with a filename so the
# MIME is detectable.
_FILE_ID_BACKENDS = {"openai", "azure"}


def _append_part(llm_request: LlmRequest, part: types.Part) -> None:
    """Append `part` to the final user turn (so it travels with the question),
    or start a fresh user turn if there's none."""
    contents = llm_request.contents
    if contents and contents[-1].role == "user":
        contents[-1].parts.append(part)
    else:
        contents.append(types.Content(role="user", parts=[part]))


def _inject_pdf(llm_request: LlmRequest, path: str | None) -> bool:
    """Attach the PDF as an inline `application/pdf` part (every backend but the
    file-id ones). Returns False if there's no readable file — the answerer's
    instruction then says the document is missing.
    """
    if not path or not os.path.exists(path):
        return False
    with open(path, "rb") as fh:
        blob = types.Blob(
            data=fh.read(),
            mime_type="application/pdf",
            display_name=os.path.basename(path),
        )
    _append_part(llm_request, types.Part(inline_data=blob))
    return True


async def _inject_uploaded_pdf(llm_request: LlmRequest, path: str | None) -> bool:
    """Upload the PDF to the Files API and attach its id (openai/azure).

    Upload with a real filename so the provider detects the PDF MIME — ADK's own
    inline upload omits the name and gets rejected. Attaches a `file_data` part
    with the `file-…` id, which ADK forwards as-is.
    """
    if not path or not os.path.exists(path):
        return False
    import litellm  # present whenever a LiteLLM backend is active

    with open(path, "rb") as fh:
        data = fh.read()
    name = os.path.basename(path)
    upload_kwargs = dict(
        file=(name, data, "application/pdf"),
        purpose="user_data",
        custom_llm_provider=backend(),
    )
    # Pass the key explicitly so the repo's OPENAI_KEY alias works here too —
    # litellm.acreate_file only reads OPENAI_API_KEY otherwise.
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_KEY")
    if api_key:
        upload_kwargs["api_key"] = api_key
    uploaded = await litellm.acreate_file(**upload_kwargs)
    _append_part(
        llm_request,
        types.Part(file_data=types.FileData(
            file_uri=uploaded.id, mime_type="application/pdf", display_name=name)),
    )
    return True


async def _attach_pdf(callback_context: CallbackContext, llm_request: LlmRequest):
    """before_model_callback: inject the active PDF before each model call.

    Reads the path the coordinator pinned in state, routes inline vs upload by
    backend. Returns None so the model call proceeds.
    """
    path = callback_context.state.get("pdf_path")
    if backend() in _FILE_ID_BACKENDS:
        await _inject_uploaded_pdf(llm_request, path)
    else:
        _inject_pdf(llm_request, path)
    return None


def _make_answerer(name: str) -> LlmAgent:
    """One answerer instance per parent (an ADK agent has a single parent)."""
    return LlmAgent(
        name=name,
        model=get_model(),
        description="Answers a question by reading the raw PDF natively (no extraction).",
        instruction=(
            "The full PDF document is attached to this turn. It may be ANY kind of "
            "document — a report, statement, invoice, contract, form, slide deck, or "
            "research paper — so don't assume a domain; read what's actually there. "
            "Read it directly, including prose, tables, figures, and layout, and answer "
            "the user's question from its contents. When you add up numbers from a table, "
            "skip any subtotal/'Total' row and keep the stated units. If no document is "
            "attached or the answer isn't in it, say so plainly."
        ),
        before_model_callback=_attach_pdf,
    )


def build() -> dict:
    """Return {mode_constant: agent} for the native-bytes mode."""
    return {config.PDF_BYTES: _make_answerer("pdfbytes")}
