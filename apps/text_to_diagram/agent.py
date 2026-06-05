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

from typing import AsyncGenerator

from shared.model import get_model
from shared.schemas import TriadList

from google.adk.agents import BaseAgent, LlmAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types

from .render import render_mermaid


triad_extractor = LlmAgent(
    name="triad_extractor",
    model=get_model(),
    description="Extracts (subject, predicate, object) triads from text as JSON.",
    instruction=(
        "Extract every (subject, predicate, object) triad expressed in the user's "
        "text. Keep entities short and canonical. Return JSON only — no prose."
    ),
    output_schema=TriadList,  # controlled generation: model is constrained to TriadList
    output_key="triads",      # validated result is written to session.state["triads"]
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


mermaid_builder = MermaidAgent(name="mermaid_builder")


root_agent = SequentialAgent(
    name="text_to_diagram",
    description="Turns free text into a mermaid knowledge-graph diagram.",
    sub_agents=[triad_extractor, mermaid_builder],
)
