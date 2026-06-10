"""graph_builder offline (mock): proves the property text_to_diagram lacks —
the graph is STATEFUL and accretes across turns, with provenance on every claim.

The mock can't resolve (stage 2 returns no resolutions, so every entity becomes a
new node — wrong-split). That's expected here; resolution QUALITY is a live test.
What we lock in offline is the plumbing: accumulation + provenance + render.
"""
import os

os.environ["LLM_BACKEND"] = "mock"  # must precede the agent import (model binds then)

from apps.graph_builder.agent import root_agent  # noqa: E402


async def test_graph_accretes_across_turns_with_provenance(converse):
    answers, state = await converse(
        root_agent,
        [
            "[incident] auth-service returned 500s; its users-db pool was exhausted.",
            "[wiki] The order-service reads from the users-db.",
        ],
    )

    # Each turn renders a diagram + a resolution log.
    assert "```mermaid" in answers[-1]
    assert "Resolution log" in answers[-1]

    graph = state["graph"]
    # State PERSISTED and grew across both turns (the core new property).
    assert graph["turn"] == 2
    assert len(graph["nodes"]) >= 3  # auth-service, users-db, order-service (+maybe more)

    # Every accreted claim carries provenance: which source, which turn.
    claims = [c for n in graph["nodes"].values() for c in n["claims"]]
    assert claims, "expected at least one claim to be accreted"
    assert all({"text", "source", "turn"} <= c.keys() for c in claims)
    # Turn 2's claim is tagged to the wiki source, not the incident.
    assert any(c["source"] == "wiki" and c["turn"] == 2 for c in claims)
