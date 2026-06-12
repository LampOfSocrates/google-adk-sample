"""Framework-neutral UI stream vocabulary.

This module defines the small event contract the product UI consumes:
`text_delta`, `thinking_delta`, `tool_call`, `tool_result`, `final`, and `error`.
It intentionally has no Google ADK dependency. Runtime-specific adapters, such as
`backend.shared.adk_ui_stream`, translate their native event streams into these
`UIEvent`s before the FastAPI layer frames them as HTTP Server-Sent Events.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Literal


UIKind = Literal[
    "text_delta",      # a chunk of the assistant's answer
    "thinking_delta",  # a chunk of model reasoning
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
    usage: dict | None = None     # token counts {prompt, candidates, total}; on final


async def typewriter(text: str, words_per_chunk: int) -> AsyncGenerator[str, None]:
    """Split text into small chunks, preserving whitespace, for a streamed feel."""
    tokens = re.findall(r"\S+\s*", text) or [text]
    for i in range(0, len(tokens), words_per_chunk):
        yield "".join(tokens[i : i + words_per_chunk])
