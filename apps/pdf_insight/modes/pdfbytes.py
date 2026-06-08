"""Native bytes mode: LLM_GETS_PDF_BYTES.

Placeholder for native multimodal PDF upload. Gemini-only; not wired in the
first pass — refuses gracefully on other backends and reports "planned" on gemini.
"""
from __future__ import annotations

from typing import AsyncGenerator

from shared.model import backend, is_gemini

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from .. import config
from ._common import _text_event


class PdfBytesAgent(BaseAgent):
    """LLM_GETS_PDF_BYTES placeholder. Gemini-only; not wired in the first pass."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        if not is_gemini():
            msg = ("LLM_GETS_PDF_BYTES needs the gemini backend (native PDF upload "
                   f"can't run on the {backend()} backend). Pick another mode or "
                   "set LLM_BACKEND=gemini.")
        else:
            msg = "LLM_GETS_PDF_BYTES (native upload) is planned for a later phase."
        yield _text_event(self.name, msg)


def build() -> dict:
    """Return {mode_constant: agent} for the native-bytes strategy."""
    return {config.PDF_BYTES: PdfBytesAgent(name="pdfbytes")}
