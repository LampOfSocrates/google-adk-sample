"""Native bytes mode: LLM_GETS_PDF_BYTES.

Hands the raw PDF to the model and lets it read the document itself — no table
extraction, no SQL. Works on any backend whose model accepts a document part:

  * gemini  — native PDF understanding (inline document part).
  * bedrock — Anthropic reads it as a document; ADK's LiteLlm maps an inline
    `application/pdf` part to its data-URI document content.
  * openai / azure — these require the file be uploaded to the Files API first
    and referenced by id. ADK *would* upload our inline bytes, but it does so
    without a filename, so OpenAI can't detect the PDF MIME and rejects it. We
    upload it ourselves with a real filename and attach the returned `file-…` id,
    which ADK passes straight through.
  * deepseek — has no document input, so the provider rejects it; the error is
    surfaced in the chat (not a crash). Use a tables/SQL mode there instead.
  * mock — ignores the bytes and returns its offline placeholder, so the graph
    still runs without a key.

Rather than re-extract anything, an `LlmAgent` carries the question and an async
`before_model_callback` injects the PDF onto the outgoing request. Going through a
normal LlmAgent (not a hand-rolled model call) means ADK's streaming, event
shaping, and the UI rendering are all reused unchanged.
"""
from __future__ import annotations

import os

from backend.shared.model import backend, get_model

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from .. import config

# Providers whose chat API can't take an inline PDF — the file must be uploaded
# and referenced by id (mirrors ADK lite_llm._FILE_ID_REQUIRED_PROVIDERS). For
# these we upload with a filename ourselves so the MIME is detectable.
_FILE_ID_BACKENDS = {"openai", "azure"}


def _append_part(llm_request: LlmRequest, part: types.Part) -> None:
    """Append `part` to the final user turn (so it travels with the question),
    or start a fresh user content when the request has none."""
    contents = llm_request.contents
    if contents and contents[-1].role == "user":
        contents[-1].parts.append(part)
    else:
        contents.append(types.Content(role="user", parts=[part]))


def _inject_pdf(llm_request: LlmRequest, path: str | None) -> bool:
    """Attach the PDF at `path` as an inline `application/pdf` document part.

    Used by every backend except the file-id ones. Returns True when a part was
    added, False when there's no readable file — in which case the answerer's
    instruction makes it say the document is missing.
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
    """Upload the PDF to the provider's Files API and attach its id.

    For openai/azure, whose chat API needs a file id rather than inline bytes.
    We upload with a real filename so the provider detects the PDF MIME (ADK's
    own inline upload omits the name and the upload is rejected), then attach a
    `file_data` part holding the `file-…` id, which ADK forwards as-is.
    """
    if not path or not os.path.exists(path):
        return False
    import litellm  # available whenever a LiteLLM backend is active

    with open(path, "rb") as fh:
        data = fh.read()
    name = os.path.basename(path)
    upload_kwargs = dict(
        file=(name, data, "application/pdf"),
        purpose="user_data",
        custom_llm_provider=backend(),
    )
    # Thread the key explicitly so the repo's OPENAI_KEY alias works for the
    # upload too — litellm.acreate_file otherwise only reads OPENAI_API_KEY,
    # unlike the chat client which shared.model gives the alias to.
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

    Reads the path the coordinator pinned in session state and routes to the
    inline or upload path by backend. Returns None so the model call proceeds
    with the (now PDF-bearing) request.
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
            "The full PDF document is attached to this turn. Read it directly — "
            "including tables, figures, and layout — and answer the user's question "
            "from its contents. If no document is attached or the answer isn't in "
            "it, say so plainly."
        ),
        before_model_callback=_attach_pdf,
    )


def build() -> dict:
    """Return {mode_constant: agent} for the native-bytes strategy."""
    return {config.PDF_BYTES: _make_answerer("pdfbytes")}
