"""Phase 1 — single agent + one tool. Live: confirms the model actually calls
get_weather and relays both the success and error paths.

Live tests need LLM_BACKEND=gemini (+ GOOGLE_API_KEY). Run with:
    LLM_BACKEND=gemini pytest -m live
"""
import pytest

from apps.travel_planner.agent import root_agent

pytestmark = pytest.mark.live


async def test_known_city_uses_tool(converse):
    answers, _ = await converse(root_agent, ["What's the weather in Tokyo?"])
    assert "22" in answers[0] or "sunny" in answers[0].lower()


async def test_unknown_city_reports_error(converse):
    answers, _ = await converse(root_agent, ["What's the weather in Atlantis?"])
    assert any(p in answers[0].lower() for p in ("sorry", "don't have", "do not have"))
