"""Helpers shared by the coordinator and the per-mode agents.

Pulled out of agent.py so each mode module (tables/sql/native) and the
coordinator can reuse them without importing each other.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types

_DEFAULT_PDF = os.path.join("tests", "fixtures", "risk_report.pdf")


def _user_text(ctx: InvocationContext) -> str:
    content = ctx.user_content
    if content and content.parts:
        for p in content.parts:
            if p.text:
                return p.text
    return ""


def _resolve_pdf_path(text: str, state: dict) -> str:
    """Precedence: a .pdf path in the message > state['pdf_path'] > env > default."""
    m = re.search(r"(\S+\.pdf)\b", text or "", re.IGNORECASE)
    if m:
        return m.group(1)
    return state.get("pdf_path") or os.environ.get("PDF_PATH") or _DEFAULT_PDF


def _parse_table_indices(text: str) -> Optional[list[int]]:
    """Pull explicit table indices ('table 0', 'tables 0,2') out of the message."""
    m = re.search(r"tables?\s+([\d,\s]+)", text or "", re.IGNORECASE)
    if not m:
        return None
    nums = [int(n) for n in re.findall(r"\d+", m.group(1))]
    return nums or None


def _text_event(author: str, text: str, state_delta: dict | None = None) -> Event:
    return Event(
        author=author,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        actions=EventActions(state_delta=state_delta or {}),
    )
