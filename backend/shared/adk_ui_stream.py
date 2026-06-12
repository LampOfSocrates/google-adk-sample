"""Adapt ADK runner events into the framework-neutral UI event stream.

This module is deliberately the ADK-facing edge. It knows how to:
  * build a `google.genai.types.Content` user message for `runner.run_async`;
  * request ADK token streaming with `RunConfig(streaming_mode=SSE)`;
  * translate ADK event parts into `UIEvent`s.

The UI-facing contract lives in `backend.shared.ui_stream` and has no Google ADK
dependency. Keep ADK imports here so a different runner can provide the same
`UIEvent` vocabulary later without touching the HTTP/UI layer.
"""
from __future__ import annotations

from typing import AsyncGenerator

from google.genai import types

from .ui_stream import UIEvent, typewriter

try:  # SSE token streaming is optional - degrade to whole-message if unavailable.
    from google.adk.agents.run_config import RunConfig, StreamingMode

    _SSE = RunConfig(streaming_mode=StreamingMode.SSE)
except Exception:  # pragma: no cover - depends on ADK version
    _SSE = None


async def stream_ui_events(
    runner,
    *,
    user_id: str,
    session_id: str,
    message: str,
    simulate_stream: bool = False,
    words_per_chunk: int = 3,
    debug_sink: list | None = None,
    snapshot_fn=None,
) -> AsyncGenerator[UIEvent, None]:
    """Yield flat `UIEvent`s for a chat turn driven by an ADK runner.

    If `debug_sink` is a list AND a `snapshot_fn(event, seq)` is given, every raw
    ADK event is also snapshotted into it for the debug tab. That side channel
    does not change the UI event stream.
    """
    msg = types.Content(role="user", parts=[types.Part(text=message)])
    kwargs = {"user_id": user_id, "session_id": session_id, "new_message": msg}
    if _SSE is not None:
        kwargs["run_config"] = _SSE

    saw_partial_text = False
    seq = 0
    usage_totals = {"prompt": 0, "candidates": 0, "total": 0}
    try:
        async for event in runner.run_async(**kwargs):
            if debug_sink is not None and snapshot_fn is not None:
                debug_sink.append(snapshot_fn(event, seq))
                seq += 1
            um = getattr(event, "usage_metadata", None)
            if um is not None:
                usage_totals["prompt"] += getattr(um, "prompt_token_count", 0) or 0
                usage_totals["candidates"] += getattr(um, "candidates_token_count", 0) or 0
                usage_totals["total"] += getattr(um, "total_token_count", 0) or 0
            author = event.author or "agent"
            partial = bool(getattr(event, "partial", False))
            parts = (event.content.parts if event.content else None) or []

            for part in parts:
                if getattr(part, "thought", False) and getattr(part, "text", None):
                    yield UIEvent("thinking_delta", author, text=part.text)
                elif getattr(part, "function_call", None):
                    fc = part.function_call
                    yield UIEvent(
                        "tool_call", author,
                        tool_name=fc.name,
                        tool_args=dict(fc.args or {}),
                    )
                elif getattr(part, "function_response", None):
                    fr = part.function_response
                    yield UIEvent(
                        "tool_result", author,
                        tool_name=fr.name,
                        tool_result=fr.response,
                    )
                elif getattr(part, "text", None):
                    if partial:
                        saw_partial_text = True
                        yield UIEvent("text_delta", author, text=part.text)
                    elif not saw_partial_text:
                        if simulate_stream:
                            async for chunk in typewriter(part.text, words_per_chunk):
                                yield UIEvent("text_delta", author, text=chunk)
                        else:
                            yield UIEvent("text_delta", author, text=part.text)

            if event.is_final_response():
                yield UIEvent("final", author, usage=dict(usage_totals))
    except Exception as e:  # surface backend/quota errors in-chat instead of 500ing
        yield UIEvent("error", text=f"{type(e).__name__}: {e}")
