"""A zero-cost, offline stand-in for a real LLM.

`MockLlm` implements ADK's `BaseLlm` interface, so an `Agent(model=MockLlm())`
runs the full Runner loop — tool calls, sub-agent transfers, session state —
without ever touching the network or spending a token.

It is *heuristic*, not a real model: it inspects which tools the current agent
has available (ADK passes them in `llm_request.tools_dict`) to figure out which
agent it is standing in for, then returns a plausible function call or final
text. That's enough to keep building and exercising the agent graph while you
have no API quota. For exact, scripted replies in a test, pass `replies=[...]`
(consumed in order for the turns that would otherwise produce free-text).
"""
from __future__ import annotations

import re
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

# Cities the real weather tool knows about — used only to spot a weather intent
# and pull a city out of the user's message. Kept in sync with _WEATHER_DB.
_KNOWN_CITIES = ("london", "tokyo", "new york")
_WEATHER_WORDS = ("weather", "temperature", "forecast", "hot", "cold", "rain")
_UNIT_WORDS = ("celsius", "fahrenheit", "°c", "°f", "units", "unit")


def web_search(query: str) -> dict:
    """Offline stand-in for the built-in google_search tool.

    The real google_search only works on Gemini models, so under LLM_BACKEND=mock
    the search specialist uses this instead — a deterministic placeholder that
    keeps the agent graph runnable without a network call.
    """
    return {
        "status": "success",
        "result": f"[mock search] Offline placeholder result for: {query!r}",
    }


def _text_part(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def _call_part(name: str, args: dict) -> LlmResponse:
    fc = types.FunctionCall(name=name, args=args)
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(function_call=fc)])
    )


def _is_synthetic(text: str) -> bool:
    """ADK injects 'For context:' / '[agent] ...' user turns when transferring
    between agents. Those aren't the human's request, so the mock skips them."""
    t = text.lstrip()
    return t.startswith("For context:") or t.startswith("[")


def _current_turn(contents):
    """Return (user_text, fresh_function_response) for the turn in progress.

    The session history accumulates across turns, so we anchor on the LAST
    genuine (non-synthetic) user message and only look at what came after it.
    That keeps a tool result from an earlier turn from being mistaken for the
    current one.
    """
    contents = contents or []
    anchor = -1
    user_text = ""
    for i, content in enumerate(contents):
        if getattr(content, "role", None) == "user":
            for part in content.parts or []:
                if part.text and not _is_synthetic(part.text):
                    anchor, user_text = i, part.text
                    break

    fr = None
    for content in contents[anchor + 1 :]:
        for part in content.parts or []:
            if part.function_response is not None:
                fr = part.function_response
    return user_text, fr


def _extract_city(text: str) -> str:
    low = text.lower()
    for city in _KNOWN_CITIES:
        if city in low:
            return city.title()
    m = re.search(r"\b(?:in|for|at)\s+([A-Za-z][A-Za-z\s]+?)(?:[?.!,]|$)", text)
    return m.group(1).strip().title() if m else "London"


def _summarize_tool_result(fr) -> str:
    r = fr.response if isinstance(fr.response, dict) else {}
    for key in ("report", "message", "error_message", "result", "answer", "text"):
        if r.get(key):
            return str(r[key])
    return str(r) if r else "Done."


class MockLlm(BaseLlm):
    """Deterministic, network-free LLM for offline development.

    Set `LLM_BACKEND=mock` (the default) to use it everywhere via the model
    factory in `weather_agent.model`.
    """

    model: str = "mock"
    # Optional: exact free-text replies to return in order (for scripted tests).
    replies: list[str] = []
    # Print each routing decision to stderr — handy while iterating.
    verbose: bool = False

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield self._decide(llm_request)

    # --- the whole "model" lives here -------------------------------------
    def _decide(self, req: LlmRequest) -> LlmResponse:
        available = set((req.tools_dict or {}).keys())
        user_text, fr = _current_turn(req.contents)
        low = user_text.lower()

        # A tool (other than a transfer) just returned -> wrap it up as the
        # final answer. Transfers are control-flow, not data, so we skip them
        # and let the freshly-activated agent take its turn instead.
        if fr is not None and fr.name != "transfer_to_agent":
            return self._log("summarize", _text_part(_summarize_tool_result(fr)))

        weatherish = (
            any(w in low for w in _WEATHER_WORDS)
            or any(c in low for c in _KNOWN_CITIES)
            or any(u in low for u in _UNIT_WORDS)
        )
        wants_units = any(u in low for u in _UNIT_WORDS)

        # --- I am the weather specialist (has the domain tools) ---
        if "get_weather" in available:
            has_weather = any(w in low for w in _WEATHER_WORDS) or any(
                c in low for c in _KNOWN_CITIES
            )
            if wants_units and not has_weather:
                units = "fahrenheit" if "fahrenheit" in low or "°f" in low else "celsius"
                return self._log("set_units", _call_part("set_preferred_units", {"units": units}))
            if has_weather:
                return self._log("get_weather", _call_part("get_weather", {"city": _extract_city(user_text)}))
            # ADK keeps this agent active across turns; a non-weather follow-up
            # lands here. Deflect rather than fabricate a get_weather call.
            return self._log(
                "deflect",
                _text_part("I only handle weather lookups and temperature units. Ask me about the weather in a city."),
            )

        # --- I am the search specialist (offline web_search stand-in) ---
        if "web_search" in available:
            return self._log("web_search", _call_part("web_search", {"query": user_text}))

        # --- I am the coordinator (routes, owns no domain logic) ---
        if "transfer_to_agent" in available and weatherish:
            return self._log("transfer", _call_part("transfer_to_agent", {"agent_name": "weather_agent"}))
        if "search_agent" in available:
            return self._log("delegate_search", _call_part("search_agent", {"request": user_text}))

        # --- nothing matched: scripted reply, else a generic answer ---
        if self.replies:
            return self._log("scripted", _text_part(self.replies.pop(0)))
        return self._log("fallback", _text_part(f"[mock] I received: {user_text!r}"))

    def _log(self, decision: str, resp: LlmResponse) -> LlmResponse:
        if self.verbose:
            import sys

            print(f"[MockLlm] -> {decision}", file=sys.stderr)
        return resp
