"""text_to_diagram app: prose -> knowledge-graph triads -> mermaid diagram.

A two-stage `SequentialAgent` pipeline that deliberately contrasts the two kinds
of ADK agent:

  Stage 1  triad_extractor  -> LlmAgent. Extracting (subject, predicate, object)
           triads from free text needs judgment, so it IS a model call. It uses
           `output_schema=TriadList` (controlled generation: ADK forces+validates
           JSON) and `output_key="triads"` to publish the result into
           session.state["triads"] — the canonical, parse-free hand-off.
           NOTE: an LlmAgent with output_schema CANNOT use tools or transfer.

  Stage 2  MermaidAgent     -> custom BaseAgent. Rendering triads as mermaid is
           deterministic string templating, so it is NOT an LlmAgent — it reads
           state["triads"] and emits the diagram with zero tokens. This is the
           right pattern whenever a "step" is mechanical: a custom BaseAgent, not
           a model.

`root_agent` (the SequentialAgent) is what ADK discovers.

ADK version note: `SequentialAgent` is marked deprecated in adk 2.x in favor of
the graph-based `google.adk.workflow.Workflow` (nodes + edges). We keep
SequentialAgent here on purpose — for a fixed two-stage pipeline it is far
clearer than a graph, and it still runs. Reach for `Workflow` only when you need
real branching / fan-out / joins between steps.
"""
from __future__ import annotations

import json
import re
from typing import AsyncGenerator

from pydantic import ValidationError

from shared.model import get_model, supports_output_schema
from shared.schemas import TriadList

from google.adk.agents import BaseAgent, LlmAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types

from .render import render_mermaid

_BASE_INSTRUCTION = (
    "Extract every (subject, predicate, object) triad expressed in the user's "
    "text. Keep entities short and canonical."
)


class MermaidAgent(BaseAgent):
    """Deterministic renderer stage: reads state['triads'], emits a mermaid diagram.

    This is a custom agent (not an LlmAgent) on purpose — see module docstring.
    Subclass `BaseAgent` and implement `_run_async_impl`, yielding `Event`s; the
    final text event becomes the pipeline's response.
    """

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        triads = ctx.session.state.get("triads")
        diagram = render_mermaid(triads)
        yield Event(
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=diagram)]),
        )


def _parse_triads(raw: str) -> dict:
    """Best-effort parse of the extractor's raw text into a TriadList dict.

    Used only on the fallback (non-schema) path. Tolerates code fences and
    surrounding prose; returns {"triads": []} if nothing valid is found.
    """
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[A-Za-z]*\n?|\n?```$", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    candidate = m.group(0) if m else raw
    try:
        return TriadList.model_validate(json.loads(candidate)).model_dump()
    except (json.JSONDecodeError, ValidationError):
        return {"triads": []}


class TriadParseAgent(BaseAgent):
    """Validate the extractor's raw JSON into state['triads'].

    The schema-constrained path (Gemini/mock) writes state['triads'] directly via
    output_schema. Providers that can't do schema-constrained generation
    (openai/deepseek/bedrock) emit raw JSON text into state['triads_raw']; this
    stage parses + validates it so the renderer sees the same shape either way.
    """

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        triads = _parse_triads(ctx.session.state.get("triads_raw"))
        ctx.session.state["triads"] = triads  # in-process read by the next stage
        yield Event(
            author=self.name,
            actions=EventActions(state_delta={"triads": triads}),
        )


mermaid_builder = MermaidAgent(name="mermaid_builder")


# Backend-aware extractor. Gemini and the offline mock honor output_schema (ADK
# controlled generation); the LiteLLM providers don't, so we ask for JSON in the
# prompt and validate it in the TriadParseAgent stage instead.
if supports_output_schema():
    triad_extractor = LlmAgent(
        name="triad_extractor",
        model=get_model(),
        description="Extracts (subject, predicate, object) triads from text as JSON.",
        instruction=_BASE_INSTRUCTION + " Return JSON only — no prose.",
        output_schema=TriadList,  # controlled generation: constrained to TriadList
        output_key="triads",      # validated result written to session.state["triads"]
    )
    _stages = [triad_extractor, mermaid_builder]
else:
    triad_extractor = LlmAgent(
        name="triad_extractor",
        model=get_model(),
        description="Extracts (subject, predicate, object) triads from text as JSON.",
        instruction=(
            _BASE_INSTRUCTION
            + ' Return ONLY a JSON object of exactly this shape, with no prose and '
              'no code fence: {"triads": [{"subject": "...", "predicate": "...", '
              '"object": "..."}]}'
        ),
        output_key="triads_raw",  # raw model text; validated by TriadParseAgent
    )
    _stages = [triad_extractor, TriadParseAgent(name="triad_parser"), mermaid_builder]


root_agent = SequentialAgent(
    name="text_to_diagram",
    description="Turns free text into a mermaid knowledge-graph diagram.",
    sub_agents=_stages,
)
