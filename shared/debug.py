"""Debug/introspection panel for the Streamlit app — the `adk web` dev tools,
in a tab.

The chat UI only ever sees flat `UIEvent`s (see `shared.ui_stream`). That hides
ADK internals on purpose, which is great for the chat and useless for debugging.
This module is the other half: it snapshots the *raw* ADK `Event`s as they stream
by (token usage, timestamps, raw JSON) and renders them — plus live session state
and artifacts — into a Streamlit tab.

Wiring:
- `stream_ui_events(..., debug_sink=turn)` appends a `snapshot_event` per raw event.
- After a turn, the app calls `fetch_session_info(...)` for state/artifacts.
- `render_debug_tab(turns)` draws everything.

Nothing here touches Streamlit session_state; the app owns the `turns` list.
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


async def fetch_session_info(runner, app: str, user_id: str, session_id: str) -> dict:
    """Pull live session state, event count, and artifact keys off the runner's
    services. Best-effort — services/methods vary by ADK version."""
    info: dict[str, Any] = {"state": {}, "event_count": None, "artifacts": []}
    try:
        session = await runner.session_service.get_session(
            app_name=app, user_id=user_id, session_id=session_id
        )
        if session is not None:
            info["state"] = dict(getattr(session, "state", {}) or {})
            events = getattr(session, "events", None)
            if events is not None:
                info["event_count"] = len(events)
    except Exception as e:  # pragma: no cover - depends on ADK version
        info["state_error"] = f"{type(e).__name__}: {e}"

    try:
        artifact_service = getattr(runner, "artifact_service", None)
        if artifact_service is not None:
            keys = await artifact_service.list_artifact_keys(
                app_name=app, user_id=user_id, session_id=session_id
            )
            info["artifacts"] = list(keys or [])
    except Exception:  # artifacts are optional; never block the debug view on them
        pass

    return info


# -------------------------------------------------------------- agent tree ---
def _model_label(model) -> str | None:
    """Human label for an Agent.model (str for Gemini, BaseLlm instance otherwise)."""
    if model is None:
        return None
    if isinstance(model, str):
        return model
    inner = getattr(model, "model", None)
    return inner if isinstance(inner, str) else type(model).__name__


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
    return {"kind": "tool", "name": name, "children": []}


def build_agent_tree(agent) -> dict:
    """Walk an ADK Agent into a plain nested dict: sub_agents (transfer targets)
    and tools (functions / built-ins / AgentTool-wrapped agents)."""
    node = {
        "kind": "agent",
        "name": getattr(agent, "name", "?"),
        "model": _model_label(getattr(agent, "model", None)),
        "description": getattr(agent, "description", None),
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


def agent_tree_mermaid(root_agent) -> str:
    """Render the topology as a Mermaid flowchart: agents are boxes, tools are
    stadiums, edges labelled transfer/call. Matches `adk web`'s visual graph."""
    tree = build_agent_tree(root_agent)
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


def agent_tree_text(root_agent) -> str:
    """Monospace ASCII tree — the always-works fallback (no overlap, no HTML)."""
    tree = build_agent_tree(root_agent)
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


def _tree_node_count(root_agent) -> int:
    def count(node: dict) -> int:
        return 1 + sum(count(c) for c in node["children"])
    return count(build_agent_tree(root_agent))


def render_agent_tree(root_agent) -> None:
    """Draw the static agent topology — the `adk web` agent-tree equivalent.

    Renders a static Mermaid diagram (no pan/zoom controls); falls back to a
    monospace ASCII tree if the streamlit-mermaid component isn't installed."""
    import streamlit as st

    if root_agent is None:
        st.info("Agent tree unavailable for this runner.")
        return

    try:
        from streamlit_mermaid import st_mermaid

        height = max(240, _tree_node_count(root_agent) * 60)
        st_mermaid(
            agent_tree_mermaid(root_agent),
            height=f"{height}px",
            pan=False,
            zoom=False,
            show_controls=False,
            key=f"agent-tree-{getattr(root_agent, 'name', 'root')}",
        )
    except Exception:
        st.code(agent_tree_text(root_agent), language=None)


# ------------------------------------------------------------------ render ---
def _turn_totals(snapshots: list[EventSnapshot]) -> dict:
    tool_calls = sum(1 for s in snapshots for p in s.parts if p.kind == "tool_call")
    tokens = 0
    for s in snapshots:
        if s.usage and s.usage.get("total"):
            tokens += s.usage["total"]
    return {"events": len(snapshots), "tool_calls": tool_calls, "tokens": tokens}


_FLOW_ICON = {"thinking": "💭", "tool_call": "🔧", "tool_result": "↳", "text": "💬"}


def _event_flow(snapshots: list[EventSnapshot]) -> str:
    """One-line sequence of the turn's events — the shape of the turn at a glance.
    e.g. 🔧 → ↳ → 🔧 → ↳ → 💬 → 💬✅"""
    out = []
    for s in snapshots:
        mark = "".join(_FLOW_ICON.get(k, "•") for k in s.kinds) or "·"
        if s.is_final:
            mark += "✅"
        out.append(mark)
    return "  →  ".join(out) or "—"


def _events_by_author(snapshots: list[EventSnapshot]) -> dict:
    """How many events each agent emitted this turn."""
    out: dict[str, int] = {}
    for s in snapshots:
        out[s.author] = out.get(s.author, 0) + 1
    return out


_KIND_BADGE = {
    "thinking": "💭 thinking",
    "tool_call": "🔧 tool_call",
    "tool_result": "↳ tool_result",
    "text": "💬 text",
}


def render_debug_tab(turns: list[dict], root_agent=None) -> None:
    """Draw the debug tab. `turns` is a list of dicts the app accumulates, each:
        {"prompt": str, "snapshots": [EventSnapshot], "latency": float,
         "session": {...from fetch_session_info...}}
    `root_agent` (optional) drives the static agent-tree view.
    """
    import streamlit as st

    with st.expander("🌳 Agent tree", expanded=not turns):
        render_agent_tree(root_agent)

    if not turns:
        st.info("No turns yet — send a message in the **💬 Chat** tab.")
        return

    # Aggregate across the whole conversation.
    grand = {"events": 0, "tool_calls": 0, "tokens": 0}
    for t in turns:
        tt = _turn_totals(t["snapshots"])
        for k in grand:
            grand[k] += tt[k]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Turns", len(turns))
    c2.metric("Events", grand["events"])
    c3.metric("Tool calls", grand["tool_calls"])
    c4.metric("Total tokens", grand["tokens"] or "—")

    st.divider()

    # Turn picker — default to the latest.
    labels = [
        f"{i + 1}. {t['prompt'][:48]}{'…' if len(t['prompt']) > 48 else ''}"
        for i, t in enumerate(turns)
    ]
    idx = st.selectbox(
        "Turn", range(len(turns)), index=len(turns) - 1,
        format_func=lambda i: labels[i],
    )
    turn = turns[idx]
    snaps = turn["snapshots"]
    tt = _turn_totals(snaps)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Latency", f"{turn.get('latency', 0):.2f}s")
    m2.metric("Events", tt["events"])
    m3.metric("Tool calls", tt["tool_calls"])
    m4.metric("Tokens", tt["tokens"] or "—")

    # At-a-glance shape of the turn, next to the counts above.
    st.caption("Event flow")
    st.markdown(_event_flow(snaps))
    by_author = _events_by_author(snaps)
    st.caption(
        "Events per agent — "
        + " · ".join(f"**{a}**: {n}" for a, n in by_author.items())
    )

    # Session state + artifacts.
    session = turn.get("session") or {}
    with st.expander("🗃️ Session state", expanded=False):
        if session.get("state_error"):
            st.warning(session["state_error"])
        st.json(session.get("state", {}))
        arts = session.get("artifacts", [])
        st.caption(f"Artifacts: {', '.join(arts) if arts else 'none'}")

    # Per-event timeline.
    st.subheader("Events")
    t0 = next((s.timestamp for s in snaps if s.timestamp), None)
    for s in snaps:
        badges = " ".join(_KIND_BADGE.get(k, k) for k in s.kinds) or "—"
        rel = f"+{s.timestamp - t0:.2f}s" if (s.timestamp and t0) else ""
        flags = " ".join(f for f, on in (("partial", s.partial), ("final", s.is_final)) if on)
        # usage_metadata is only set when the event is the product of a real model
        # round-trip -> it's the cleanest "this was an LLM call" signal (tool
        # results, injected by the runner, never carry it).
        llm = "🤖 LLM · " if s.usage else ""
        title = f"#{s.seq} · {llm}{s.author} · {badges}"
        if rel:
            title += f" · {rel}"
        if flags:
            title += f" · {flags}"
        with st.expander(title, expanded=False):
            if s.usage:
                st.caption(
                    "tokens — "
                    + ", ".join(f"{k}: {v}" for k, v in s.usage.items() if v)
                )
            for p in s.parts:
                if p.kind == "tool_call":
                    st.markdown(f"🔧 **{p.tool_name}**")
                    st.json(p.tool_args or {})
                elif p.kind == "tool_result":
                    st.markdown(f"↳ **{p.tool_name}** returned")
                    st.json(p.tool_result)
                elif p.kind in ("thinking", "text"):
                    st.markdown(f"_{p.kind}_: {p.text}")
            with st.expander("raw event JSON", expanded=False):
                st.json(s.raw)

    # Export — the `adk web` "download" equivalent.
    payload = json.dumps([s.raw for s in snaps], indent=2, default=str)
    st.download_button(
        "⬇️ Download raw events (JSON)",
        data=payload,
        file_name=f"adk_turn_{idx + 1}_events.json",
        mime="application/json",
        use_container_width=True,
    )
