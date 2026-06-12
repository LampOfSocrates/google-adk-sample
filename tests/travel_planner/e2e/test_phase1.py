"""Phase 1 — single agent + one tool. Live: confirms the model actually calls
get_weather and relays both the success and error paths.

Live tests pick their backend via --backend (default gemini). Run with:
    pytest -m live                      # gemini
    pytest -m live --backend deepseek   # DeepSeek (needs DEEPSEEK_KEY in .env)
"""
import os

# Re-assert the live backend right before the agent binds its model, so the
# offline modules' LLM_BACKEND=mock (set during collection) can't reach us.
os.environ["LLM_BACKEND"] = os.environ.get("LIVE_BACKEND", "gemini")

import pytest  # noqa: E402

from backend.travel_planner.agent import root_agent  # noqa: E402

pytestmark = pytest.mark.live


async def test_known_city_uses_tool(converse):
    answers, _ = await converse(root_agent, ["What's the weather in Tokyo?"])
    assert "22" in answers[0] or "sunny" in answers[0].lower()


async def test_unknown_city_reports_error(converse):
    answers, _ = await converse(root_agent, ["What's the weather in Atlantis?"])
    assert any(p in answers[0].lower() for p in ("sorry", "don't have", "do not have"))
