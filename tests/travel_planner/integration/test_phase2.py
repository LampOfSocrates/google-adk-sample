"""Phase 2 — session state. The fast tests call the tool functions directly
(no API, no quota) to prove the state logic; the live test proves state really
persists across turns through the model.
"""
import os

# The live test below binds the agent's model at import; re-assert the live
# backend (set by conftest from --backend) so the offline modules can't clobber it.
os.environ["LLM_BACKEND"] = os.environ.get("LIVE_BACKEND", "gemini")

import pytest  # noqa: E402

from backend.travel_planner.agent import (  # noqa: E402
    get_weather,
    root_agent,
    set_preferred_units,
)


class _Ctx:
    """Minimal stand-in for ToolContext — the tools only touch `.state`."""

    def __init__(self, state=None):
        self.state = state or {}


# --- fast, deterministic (no API) ---


def test_default_units_are_celsius():
    assert "22" in get_weather("Tokyo", _Ctx())["report"]


def test_set_units_writes_state():
    ctx = _Ctx()
    set_preferred_units("Fahrenheit", ctx)
    assert ctx.state["preferred_units"] == "fahrenheit"


def test_state_changes_the_reading():
    ctx = _Ctx({"preferred_units": "fahrenheit"})
    assert "72" in get_weather("Tokyo", ctx)["report"]


def test_unknown_city_is_error():
    assert get_weather("Paris", _Ctx())["status"] == "error"


def test_bad_units_rejected():
    assert set_preferred_units("kelvin", _Ctx())["status"] == "error"


# --- live: state survives across real turns ---


@pytest.mark.live
async def test_state_persists_across_turns(converse):
    answers, state = await converse(
        root_agent,
        ["Switch me to Fahrenheit.", "What's the weather in Tokyo?"],
    )
    assert state.get("preferred_units") == "fahrenheit"
    assert "72" in answers[1] or "fahrenheit" in answers[1].lower() or "°f" in answers[1].lower()
