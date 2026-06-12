"""Travel-planner app: a coordinator that routes to specialist agents.

ADK idioms demonstrated here (read before changing the topology):

  * Function tool  -> a plain Python function (get_weather, set_preferred_units).
        Deterministic logic the agent *calls*. Returns a {"status": ...} dict.
  * Sub-agent      -> weather_agent is listed in `sub_agents`. The coordinator
        TRANSFERS control to it; it then drives the conversation until done.
        Only agents whose tools are all transfer-safe can be sub-agents.
  * AgentTool      -> search_agent is wrapped in AgentTool and listed in `tools`.
        The coordinator CALLS it like a function and gets the result back, never
        ceding control. We must use AgentTool (not sub_agents) for the search
        specialist because ADK forbids a *built-in* tool (google_search) inside a
        sub-agent. That sub-agent-vs-AgentTool contrast is the whole point here.

Backend awareness: google_search is Gemini-only, so on every non-Gemini backend
(mock, openai, deepseek, bedrock) we swap in the offline `web_search` stand-in.
`root_agent` is what ADK discovers.
"""
from backend.shared.model import get_model, is_gemini
from backend.shared.mock_llm import web_search

from google.adk.agents import Agent
from google.adk.tools import ToolContext, google_search
from google.adk.tools.agent_tool import AgentTool

# Store raw data so we can format it in whichever units the user prefers.
_WEATHER_DB = {
    "london": {"celsius": 15, "condition": "cloudy"},
    "tokyo": {"celsius": 22, "condition": "sunny"},
    "new york": {"celsius": 18, "condition": "partly cloudy"},
}


def set_preferred_units(units: str, tool_context: ToolContext) -> dict:
    """Stores the user's preferred temperature units in session state.

    Args:
        units: Either "celsius" or "fahrenheit".

    Returns:
        A dict with a "status" key, plus "message" or "error_message".
    """
    normalized = units.strip().lower()
    if normalized not in ("celsius", "fahrenheit"):
        return {
            "status": "error",
            "error_message": f"Unknown units '{units}'. Use 'celsius' or 'fahrenheit'.",
        }
    # Writing to tool_context.state persists it for the rest of the session.
    tool_context.state["preferred_units"] = normalized
    return {"status": "success", "message": f"Preferred units set to {normalized}."}


def get_weather(city: str, tool_context: ToolContext) -> dict:
    """Returns the current weather report for a given city.

    Formats the temperature using "preferred_units" from session state
    (defaults to celsius if the user hasn't set a preference).

    Args:
        city: Name of the city to look up, e.g. "London".

    Returns:
        A dict with a "status" key. On success it also has a "report" string;
        on failure it has an "error_message" string.
    """
    data = _WEATHER_DB.get(city.strip().lower())
    if not data:
        return {
            "status": "error",
            "error_message": f"Sorry, I don't have weather data for '{city}'.",
        }

    units = tool_context.state.get("preferred_units", "celsius")
    if units == "fahrenheit":
        temp = round(data["celsius"] * 9 / 5 + 32)
        report = f"It's {temp}°F and {data['condition']} in {city.title()}."
    else:
        report = f"It's {data['celsius']}°C and {data['condition']} in {city.title()}."
    return {"status": "success", "report": report}


# --- Specialist 1: weather. Plain function tools, so it can be a true sub-agent. ---
weather_agent = Agent(
    name="weather_agent",
    model=get_model(),
    description="Handles weather lookups and temperature-unit preferences.",
    instruction=(
        "You answer weather questions. Call get_weather with the city name. "
        "If the user states a Celsius/Fahrenheit preference, call "
        "set_preferred_units. If a tool returns an error, relay it politely. "
        "Never invent weather data."
    ),
    tools=[get_weather, set_preferred_units],
)


# --- Specialist 2: web search. google_search is a BUILT-IN tool (Gemini only),
# and ADK forbids built-in tools inside a sub-agent — so this one is reached via
# AgentTool, not sub_agents. Only Gemini can run google_search; every other
# backend gets the offline web_search stand-in. ---
search_agent = Agent(
    name="search_agent",
    model=get_model(),
    description="Searches the web for current, general information.",
    instruction=(
        "You are a web search specialist. Use your search tool to find current, "
        "factual information and return a concise answer."
    ),
    tools=[google_search] if is_gemini() else [web_search],
)


# --- Coordinator / root: owns no domain logic. It routes each request to the
# right specialist — transferring control to weather_agent (a sub-agent) or
# calling search_agent (an AgentTool). ADK looks for `root_agent` in this file. ---
root_agent = Agent(
    name="travel_planner",
    model=get_model(),
    description="Top-level travel assistant that routes to specialist agents.",
    instruction=(
        "You are a travel assistant coordinator; you do not answer directly. "
        "Route every request:\n"
        "- Weather questions or Celsius/Fahrenheit preferences -> transfer to "
        "weather_agent.\n"
        "- Anything needing current or general web info (what to pack, events, "
        "news, facts) -> call the search_agent tool.\n"
        "Hand off promptly without chit-chat."
    ),
    sub_agents=[weather_agent],
    tools=[AgentTool(agent=search_agent)],
)
