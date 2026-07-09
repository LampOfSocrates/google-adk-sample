"""build_agent_tree should surface code documentation: each tool's docstring and
each agent's system prompt (instruction). Uses duck-typed fakes — no ADK needed.
"""
from __future__ import annotations

from backend.events import build_agent_tree


def sample_tool(city: str) -> dict:
    """Look up the weather for a city and return a report."""
    return {}


class _Tool:  # an object-style tool that carries its own description
    name = "fancy_tool"
    description = "Does a fancy thing."


class _Agent:
    def __init__(self, name, description, instruction, tools=(), sub_agents=()):
        self.name = name
        self.description = description
        self.instruction = instruction
        self.model = None
        self.tools = list(tools)
        self.sub_agents = list(sub_agents)


def test_tool_node_captures_function_docstring():
    tree = build_agent_tree(_Agent("a", "short", "prompt", tools=[sample_tool]))
    tool = next(c for c in tree["children"] if c["kind"] == "tool")
    assert tool["name"] == "sample_tool"
    assert "weather" in tool["description"].lower()


def test_tool_node_prefers_description_attr_over_docstring():
    tree = build_agent_tree(_Agent("a", "short", "prompt", tools=[_Tool()]))
    tool = tree["children"][0]
    assert tool["name"] == "fancy_tool"
    assert tool["description"] == "Does a fancy thing."


def test_agent_node_carries_instruction_and_description():
    tree = build_agent_tree(_Agent("a", "short desc", "the full system prompt"))
    assert tree["instruction"] == "the full system prompt"
    assert tree["description"] == "short desc"


def test_non_string_instruction_becomes_none():
    # ADK InstructionProvider callables aren't JSON-able text -> stored as None
    tree = build_agent_tree(_Agent("a", "d", instruction=lambda ctx: "dynamic"))
    assert tree["instruction"] is None


def test_tool_without_docstring_has_none_description():
    def bare(x):
        return x
    tree = build_agent_tree(_Agent("a", "d", "p", tools=[bare]))
    assert tree["children"][0]["description"] is None
