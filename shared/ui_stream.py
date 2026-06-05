"""Normalize ADK's raw event stream into a small UI vocabulary.

`runner.run_async()` yields `google.adk.events.Event` objects whose `content.parts`
mix text, thinking, tool calls and tool results. A chat UI doesn't want to know any
of that — it wants a flat stream of "render this" instructions. `stream_ui_events`
is that translation layer: feed it a runner + a message, get back `UIEvent`s.

This is the whole backend contract the UI depends on. Swap ADK internals, keep the
UIEvent shape, and the UI never changes.

Streaming notes:
- We ask for token-by-token text via `RunConfig(streaming_mode=SSE)`. On live
  backends (Gemini, LiteLLM) text then arrives as many `event.partial` chunks,
  followed by one aggregated non-partial copy — we emit the partials and drop the
  duplicate aggregate.
- MockLlm does NOT stream: it yields one complete response, so there are no
  partials. Pass `simulate_stream=True` (the UI does this on the mock backend) to
  fake the typewriter effect by chunking the final text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Literal

from google.genai import types

try:  # SSE token streaming is optional — degrade to whole-message if unavailable.
    from google.adk.agents.run_config import RunConfig, StreamingMode

    _SSE = RunConfig(streaming_mode=StreamingMode.SSE)
except Exception:  # pragma: no cover - depends on ADK version
    _SSE = None


UIKind = Literal[
    "text_delta",      # a chunk of the assistant's answer
    "thinking_delta",  # a chunk of model reasoning (Gemini thought parts)
    "tool_call",       # the agent invoked a tool / transferred to a sub-agent
    "tool_result",     # a tool returned
    "final",           # the user-facing reply is complete
    "error",           # the run blew up
]


@dataclass
class UIEvent:
    kind: UIKind
    author: str = ""              # which agent emitted it -> "Agent X is working"
    text: str | None = None       # for text_delta / thinking_delta / error
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: Any = None
    usage: dict | None = None     # token counts {prompt, candidates, total}; on 'final'


async def _typewriter(text: str, words_per_chunk: int) -> AsyncGenerator[str, None]:
    """Split text into small chunks (whitespace preserved) for a streamed feel."""
    tokens = re.findall(r"\S+\s*", text) or [text]
    for i in range(0, len(tokens), words_per_chunk):
        yield "".join(tokens[i : i + words_per_chunk])


async def stream_ui_events(
    runner,
    *,
    user_id: str,
    session_id: str,
    message: str,
    simulate_stream: bool = False,
    words_per_chunk: int = 3,
    debug_sink: list | None = None,
) -> AsyncGenerator[UIEvent, None]:
    """Yields flat `UIEvent`s for the chat. If `debug_sink` is a list, every raw
    ADK event is also snapshotted into it (for the debug tab) — a pure side
    channel that doesn't change what's yielded."""
    msg = types.Content(role="user", parts=[types.Part(text=message)])
    kwargs = {"user_id": user_id, "session_id": session_id, "new_message": msg}
    if _SSE is not None:
        kwargs["run_config"] = _SSE

    saw_partial_text = False
    seq = 0
    # Tokens are reported per model call (on the non-partial event for that call);
    # a turn may span several calls — coordinator + sub-agents — so we sum them.
    usage_totals = {"prompt": 0, "candidates": 0, "total": 0}
    try:
        async for event in runner.run_async(**kwargs):
            if debug_sink is not None:
                from .debug import snapshot_event  # local import: chat path stays clean

                debug_sink.append(snapshot_event(event, seq))
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
                        tool_name=fc.name, tool_args=dict(fc.args or {}),
                    )
                elif getattr(part, "function_response", None):
                    fr = part.function_response
                    yield UIEvent(
                        "tool_result", author,
                        tool_name=fr.name, tool_result=fr.response,
                    )
                elif getattr(part, "text", None):
                    if partial:
                        saw_partial_text = True
                        yield UIEvent("text_delta", author, text=part.text)
                    elif not saw_partial_text:
                        # No partials streamed (mock, or non-SSE) -> this is the
                        # whole answer. Chunk it ourselves if asked.
                        if simulate_stream:
                            async for chunk in _typewriter(part.text, words_per_chunk):
                                yield UIEvent("text_delta", author, text=chunk)
                        else:
                            yield UIEvent("text_delta", author, text=part.text)
                    # else: aggregated duplicate of streamed partials -> drop.

            if event.is_final_response():
                yield UIEvent("final", author, usage=dict(usage_totals))
    except Exception as e:  # surface backend/quota errors in the chat instead of 500ing
        yield UIEvent("error", text=f"{type(e).__name__}: {e}")
