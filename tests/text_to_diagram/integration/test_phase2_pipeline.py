"""Phase 2 — the full SequentialAgent under MockLlm: text -> triads -> mermaid.

Forces the offline backend so the extractor's controlled-generation step returns
the canned triads from MockLlm and the whole pipeline runs with no API calls.
"""
import os

os.environ["LLM_BACKEND"] = "mock"  # must precede the agent import (model is bound then)

from backend.text_to_diagram.agent import root_agent  # noqa: E402


async def test_pipeline_emits_mermaid_from_triads(converse):
    answers, state = await converse(root_agent, ["Paris is the capital of France."])
    final = answers[-1]
    # Mermaid diagram is the pipeline's output...
    assert "```mermaid" in final
    # ...built from the triads the extractor published to session state.
    assert "triads" in state
    # canned mock triads -> these nodes/edges must appear.
    assert '["Paris"]' in final and '["France"]' in final
    assert "-->|capital of|" in final
