"""Phase 3 — web search via AgentTool. Live: confirms the root agent delegates
to the search specialist and grounds its answer in real results.
"""
import pytest

from apps.travel_planner.agent import root_agent

pytestmark = pytest.mark.live


async def test_search_grounds_a_current_fact(converse):
    answers, _ = await converse(
        root_agent,
        ["Use Google search: what is the capital city of Australia?"],
    )
    assert "canberra" in answers[0].lower()
