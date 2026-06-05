import os

import certifi

# A stray SSL_CERT_FILE/SSL_CERT_DIR in the shell can point at a cert file that
# doesn't exist, which makes the Google client crash while building its SSL
# context. Force the bundled certifi CA file (we know it exists) before any
# google.* import creates that context.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ.pop("SSL_CERT_DIR", None)

from google.adk.agents import Agent
from google.adk.tools import ToolContext
from google.adk.tools.agent_tool import AgentTool

from .model import get_model, get_search_tool

# Single place to swap the model. Chosen by LLM_BACKEND in .env:
# mock (default, free/offline) | gemini (API quota) | bedrock (AWS). See model.py.
# One shared instance feeds all three agents below.
MODEL = get_model()


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
    model=MODEL,
    description="Handles weather lookups and temperature-unit preferences.",
    instruction=(
        "You answer weather questions. Call get_weather with the city name. "
        "If the user states a Celsius/Fahrenheit preference, call "
        "set_preferred_units. If a tool returns an error, relay it politely. "
        "Never invent weather data."
    ),
    tools=[get_weather, set_preferred_units],
)


# --- Specialist 2: web search. The real google_search is a BUILT-IN tool, and
# ADK forbids built-in tools inside a sub-agent — so this one is reached via
# AgentTool, not sub_agents. That contrast (sub-agent vs. AgentTool) is the point
# of Phase 4. Offline backends swap in a function-tool stand-in (see model.py). ---
search_agent = Agent(
    name="search_agent",
    model=MODEL,
    description="Searches the web for current, general information.",
    instruction=(
        "You are a web search specialist. Use the search tool to find current, "
        "factual information and return a concise answer."
    ),
    tools=[get_search_tool()],
)


# --- Coordinator / root: owns no domain logic. It routes each request to the
# right specialist — transferring control to weather_agent (a sub-agent) or
# calling search_agent (an AgentTool). ADK looks for `root_agent` in this file. ---
root_agent = Agent(
    name="coordinator",
    model=MODEL,
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
