"""Unit tests for the Agents-tab tree helpers (apps/pages/ui_agent.py).

Pure logic only — the render functions are exercised by monkeypatching
`components.html` to capture the HTML they build, so no Streamlit runtime is needed.
The fixture mirrors what backend/events.build_agent_tree emits: sub_agents carry
`relation="sub_agent (transfer)"`, AgentTool-wrapped agents `"AgentTool (call)"`,
and function tools are leaf `kind="tool"` nodes.
"""
from __future__ import annotations

import pytest

from apps.pages import ui_agent

# coordinator --transfer--> weather_agent -> (get_weather, set_preferred_units)
#             --call-----> search_agent   -> (web_search)
TREE = {
    "kind": "agent", "name": "coordinator", "model": "gemini-2.5-flash",
    "description": "routes requests",
    "instruction": "You are the coordinator. Route every request.", "children": [
        {"kind": "agent", "name": "weather_agent", "model": "gemini-2.5-flash",
         "description": "weather specialist", "instruction": "Answer weather questions.",
         "relation": "sub_agent (transfer)", "children": [
             {"kind": "tool", "name": "get_weather",
              "description": "Return the weather for a city.", "children": []},
             {"kind": "tool", "name": "set_preferred_units",
              "description": "Store the unit preference.", "children": []},
         ]},
        {"kind": "agent", "name": "search_agent", "model": None,
         "description": "web search", "instruction": "Search the web.",
         "relation": "AgentTool (call)", "children": [
             {"kind": "tool", "name": "web_search",
              "description": "Search the web offline.", "children": []},
         ]},
    ],
}


# ------------------------------------------------------------- _count_nodes ---
def test_count_nodes_includes_agents_and_tools():
    # coordinator + weather + 2 tools + search + 1 tool = 6
    assert ui_agent._count_nodes(TREE) == 6


def test_count_nodes_single_leaf():
    assert ui_agent._count_nodes({"kind": "tool", "name": "x", "children": []}) == 1


# ------------------------------------------------------------- _tree_depth ----
def test_tree_depth():
    # coordinator(1) -> weather_agent(2) -> get_weather(3)
    assert ui_agent._tree_depth(TREE) == 3


def test_tree_depth_single_node():
    assert ui_agent._tree_depth({"kind": "agent", "name": "solo", "children": []}) == 1


# -------------------------------------------------------- _agent_details_map --
def test_details_map_keys_are_agents_only():
    dm = ui_agent._agent_details_map(TREE)
    assert set(dm) == {"coordinator", "weather_agent", "search_agent"}
    # tools never become keys
    assert "get_weather" not in dm


def test_details_map_tools_and_metadata():
    dm = ui_agent._agent_details_map(TREE)
    assert dm["weather_agent"]["tools"] == ["get_weather", "set_preferred_units"]
    assert dm["weather_agent"]["model"] == "gemini-2.5-flash"
    assert dm["weather_agent"]["description"] == "weather specialist"
    # coordinator's children are agents, so it exposes no direct tools
    assert dm["coordinator"]["tools"] == []


def test_details_map_classifies_transfer_vs_call():
    connects = ui_agent._agent_details_map(TREE)["coordinator"]["connects"]
    assert connects == [
        {"name": "weather_agent", "rel": "transfer"},
        {"name": "search_agent", "rel": "call"},
    ]


def test_details_map_dedups_repeated_agent_names():
    # same agent reachable twice -> first occurrence kept, no duplicate/overwrite
    shared_tool = {"kind": "tool", "name": "t", "children": []}
    dup = {"kind": "agent", "name": "root", "children": [
        {"kind": "agent", "name": "shared", "children": [shared_tool]},
        {"kind": "agent", "name": "shared", "children": []},  # 2nd, no tools
    ]}
    dm = ui_agent._agent_details_map(dup)
    assert dm["shared"]["tools"] == ["t"]  # kept the first, richer, entry


# ----------------------------------------------------------- render helpers ---
@pytest.fixture
def capture_html(monkeypatch):
    """Replace components.html with a capture so render helpers run without a
    Streamlit runtime. Returns the dict populated on call."""
    box: dict = {}
    monkeypatch.setattr(
        ui_agent.components, "html",
        lambda html, height=None, scrolling=None: box.update(html=html, height=height),
    )
    return box


def test_explorer_tree_substitutes_and_embeds(capture_html):
    ui_agent._explorer_tree(TREE, 200)
    html = capture_html["html"]
    assert not any(p in html for p in
                   ("__TREE__", "__HEIGHT__", "__DETAILJS__", "__PANEL_CSS__"))
    assert "coordinator" in html and "get_weather" in html
    assert "Return the weather for a city." in html  # tool docstring embedded
    assert capture_html["height"] == 208  # height + 8 iframe slack


def test_zoomable_tree_substitutes_and_embeds(capture_html):
    ui_agent._zoomable_agent_tree("flowchart TD\n  n1", TREE, 300)
    html = capture_html["html"]
    assert not any(p in html for p in
                   ("__CODE__", "__TREE__", "__HEIGHT__", "__DETAILJS__", "__PANEL_CSS__"))
    assert "flowchart TD" in html and "weather_agent" in html
    assert "You are the coordinator" in html  # system prompt embedded
    assert capture_html["height"] == 308


def test_templates_carry_their_placeholders():
    # guards against a future edit dropping a placeholder the helper relies on
    assert all(p in ui_agent._TREE_HTML
               for p in ("__HEIGHT__", "__CODE__", "__TREE__", "__DETAILJS__", "__PANEL_CSS__"))
    assert all(p in ui_agent._EXPLORER_HTML
               for p in ("__HEIGHT__", "__TREE__", "__DETAILJS__", "__PANEL_CSS__"))
    assert "renderDetail" in ui_agent._DETAIL_JS
