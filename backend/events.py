"""Server-side event + agent-introspection logic (no Streamlit).

Two jobs, both pure data — the client renders the results:
  * snapshot a raw ADK `Event` into a JSON-able `EventSnapshot` (the Debug view).
  * walk a live ADK `Agent` graph into a plain dict / Mermaid string (the agent
    tree shown in Debug + the Agents editor).

These used to live in `apps/pages/ui_debug.py`; they moved here when the app split
into a FastAPI backend (owns ADK objects) and a thin client (renders JSON). The
client's `render_*` helpers import `EventSnapshot`/`PartSummary` from here to
rebuild snapshots that arrive over the wire.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# ----------------------------------------------------------------- capture ---
@dataclass
class PartSummary:
    kind: str                      # "thinking" | "tool_call" | "tool_result" | "text"
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: Any = None
    text: str | None = None


@dataclass
class EventSnapshot:
    """One raw ADK event, flattened just enough for inspection."""
    seq: int
    author: str
    timestamp: float | None
    is_final: bool
    partial: bool
    invocation_id: str | None
    parts: list[PartSummary] = field(default_factory=list)
    usage: dict | None = None       # prompt/candidates/thoughts/total token counts
    raw: Any = None                 # best-effort model_dump for the JSON view

    @property
    def kinds(self) -> list[str]:
        return [p.kind for p in self.parts]


def _usage(event) -> dict | None:
    um = getattr(event, "usage_metadata", None)
    if not um:
        return None
    out = {
        "prompt": getattr(um, "prompt_token_count", None),
        "candidates": getattr(um, "candidates_token_count", None),
        "thoughts": getattr(um, "thoughts_token_count", None),
        "total": getattr(um, "total_token_count", None),
    }
    return out if any(v for v in out.values()) else None


def _raw(event) -> Any:
    """Best-effort JSON-able dump of the event (ADK Events are pydantic models)."""
    try:
        return event.model_dump(mode="json", exclude_none=True)
    except Exception:
        try:
            return json.loads(json.dumps(event, default=str))
        except Exception:
            return {"repr": repr(event)}


def snapshot_event(event, seq: int) -> EventSnapshot:
    """Flatten a raw ADK `Event` into an `EventSnapshot`. Pure; no side effects."""
    parts_summary: list[PartSummary] = []
    parts = (event.content.parts if getattr(event, "content", None) else None) or []
    for part in parts:
        if getattr(part, "thought", False) and getattr(part, "text", None):
            parts_summary.append(PartSummary("thinking", text=part.text))
        elif getattr(part, "function_call", None):
            fc = part.function_call
            parts_summary.append(
                PartSummary("tool_call", tool_name=fc.name, tool_args=dict(fc.args or {}))
            )
        elif getattr(part, "function_response", None):
            fr = part.function_response
            parts_summary.append(
                PartSummary("tool_result", tool_name=fr.name, tool_result=fr.response)
            )
        elif getattr(part, "text", None):
            parts_summary.append(PartSummary("text", text=part.text))

    is_final = False
    try:
        is_final = bool(event.is_final_response())
    except Exception:
        pass

    return EventSnapshot(
        seq=seq,
        author=getattr(event, "author", None) or "agent",
        timestamp=getattr(event, "timestamp", None),
        is_final=is_final,
        partial=bool(getattr(event, "partial", False)),
        invocation_id=getattr(event, "invocation_id", None),
        parts=parts_summary,
        usage=_usage(event),
        raw=_raw(event),
    )


# -------------------------------------------------------------- agent tree ---
def _model_label(model) -> str | None:
    """Human label for an Agent.model (str for Gemini, BaseLlm instance otherwise)."""
    if model is None:
        return None
    if isinstance(model, str):
        return model
    inner = getattr(model, "model", None)
    return inner if isinstance(inner, str) else type(model).__name__


def _tool_description(tool) -> str | None:
    """Verbose 'what it does' for a tool: ADK's `tool.description`, else the
    wrapped/raw callable's docstring. This is the code documentation surfaced in
    the Agents-tab detail panel, so richer docstrings = richer descriptions."""
    d = getattr(tool, "description", None)
    if isinstance(d, str) and d.strip():
        return d.strip()
    func = getattr(tool, "func", None) or (tool if callable(tool) else None)
    doc = getattr(func, "__doc__", None)
    return doc.strip() if isinstance(doc, str) and doc.strip() else None


def _tool_node(tool) -> dict:
    """A tool entry is either an AgentTool (wraps an agent -> recurse) or a leaf."""
    inner = getattr(tool, "agent", None)
    if inner is not None:  # AgentTool: coordinator CALLS it, keeps control
        node = build_agent_tree(inner)
        node["relation"] = "AgentTool (call)"
        return node
    name = (
        getattr(tool, "name", None)
        or getattr(tool, "__name__", None)
        or type(tool).__name__
    )
    return {"kind": "tool", "name": name,
            "description": _tool_description(tool), "children": []}


def build_agent_tree(agent) -> dict:
    """Walk an ADK Agent into a plain nested dict: sub_agents (transfer targets)
    and tools (functions / built-ins / AgentTool-wrapped agents)."""
    instr = getattr(agent, "instruction", None)
    node = {
        "kind": "agent",
        "name": getattr(agent, "name", "?"),
        "model": _model_label(getattr(agent, "model", None)),
        "description": getattr(agent, "description", None),
        # The system prompt — a str, or None when it's an InstructionProvider callable.
        "instruction": instr if isinstance(instr, str) else None,
        "children": [],
    }
    for sub in getattr(agent, "sub_agents", None) or []:
        child = build_agent_tree(sub)
        child["relation"] = "sub_agent (transfer)"
        node["children"].append(child)
    for tool in getattr(agent, "tools", None) or []:
        node["children"].append(_tool_node(tool))
    return node


def _rel_short(relation: str | None) -> str:
    if not relation:
        return ""
    if "transfer" in relation:
        return "transfer"
    if "AgentTool" in relation:
        return "call"
    return relation


def agent_tree_mermaid(root) -> str:
    """Render a topology dict (or live agent) as a Mermaid flowchart: agents are
    boxes, tools are stadiums, edges labelled transfer/call. Matches `adk web`."""
    tree = root if isinstance(root, dict) else build_agent_tree(root)
    lines = ["flowchart TD"]
    counter = {"i": 0}

    def nid() -> str:
        counter["i"] += 1
        return f"n{counter['i']}"

    def esc(s: str) -> str:
        return str(s).replace('"', "'")

    def walk(node: dict, my_id: str) -> None:
        label = ("🧠 " if node["kind"] == "agent" else "🔧 ") + node["name"]
        if node["kind"] == "agent":
            lines.append(f'    {my_id}["{esc(label)}"]')
        else:
            lines.append(f'    {my_id}(["{esc(label)}"])')
        for child in node["children"]:
            cid = nid()
            rel = _rel_short(child.get("relation"))
            edge = f" -->|{rel}| " if rel else " --> "
            lines.append(f"    {my_id}{edge}{cid}")
            walk(child, cid)

    walk(tree, nid())
    return "\n".join(lines)


def agent_tree_text(root) -> str:
    """Monospace ASCII tree — the always-works fallback (no overlap, no HTML)."""
    tree = root if isinstance(root, dict) else build_agent_tree(root)
    out: list[str] = []

    def walk(node: dict, prefix: str, is_last: bool, is_root: bool) -> None:
        icon = "🧠" if node["kind"] == "agent" else "🔧"
        tags = []
        if node.get("model"):
            tags.append(node["model"])
        rel = _rel_short(node.get("relation"))
        if rel:
            tags.append(rel)
        suffix = f"  [{', '.join(tags)}]" if tags else ""
        if is_root:
            out.append(f"{icon} {node['name']}{suffix}")
            child_prefix = ""
        else:
            branch = "└─ " if is_last else "├─ "
            out.append(f"{prefix}{branch}{icon} {node['name']}{suffix}")
            child_prefix = prefix + ("   " if is_last else "│  ")
        kids = node["children"]
        for i, child in enumerate(kids):
            walk(child, child_prefix, i == len(kids) - 1, False)

    walk(tree, "", True, True)
    return "\n".join(out)


def tree_node_count(tree: dict) -> int:
    """Total nodes in a topology dict (for sizing the rendered diagram)."""
    return 1 + sum(tree_node_count(c) for c in tree["children"])
