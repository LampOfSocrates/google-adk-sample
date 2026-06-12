"""Client-side renderers — draw server JSON with Streamlit widgets.

No ADK, no disk: the Debug tab renders raw-event snapshot *dicts* that arrive in
each turn's `final` frame, and the Agents tab renders the server's editable-agent
list, writing edits back through the API. (The logic these used to call lives in
backend/events.py + backend/overrides.py now.)
"""
from __future__ import annotations

import json

import streamlit as st
from streamlit_mermaid import st_mermaid

from apps.pages import api_client

_FLOW_ICON = {"thinking": "💭", "tool_call": "🔧", "tool_result": "↳", "text": "💬"}
_KIND_BADGE = {"thinking": "💭 thinking", "tool_call": "🔧 tool_call",
               "tool_result": "↳ tool_result", "text": "💬 text"}


def _toks(n) -> str:
    n = n or 0
    return f"{n:,} tokens" if n else "tokens n/a"


def _kinds(snap: dict) -> list[str]:
    return [p["kind"] for p in snap.get("parts", [])]


def _turn_totals(snaps: list[dict]) -> dict:
    tool_calls = sum(1 for s in snaps for p in s.get("parts", []) if p["kind"] == "tool_call")
    tokens = sum((s.get("usage") or {}).get("total", 0) or 0 for s in snaps)
    return {"events": len(snaps), "tool_calls": tool_calls, "tokens": tokens}


def _event_flow(snaps: list[dict]) -> str:
    out = []
    for s in snaps:
        mark = "".join(_FLOW_ICON.get(k, "•") for k in _kinds(s)) or "·"
        if s.get("is_final"):
            mark += "✅"
        out.append(mark)
    return "  →  ".join(out) or "—"


def _events_by_author(snaps: list[dict]) -> dict:
    out: dict[str, int] = {}
    for s in snaps:
        out[s["author"]] = out.get(s["author"], 0) + 1
    return out


# ------------------------------------------------------------------ debug ---
def render_debug_tab(turns: list[dict], mermaid: str | None) -> None:
    """`turns`: [{prompt, snapshots:[snapshot dict], latency, session}]. `mermaid`:
    the agent-tree flowchart string from the server (or None)."""
    with st.expander("🌳 Agent tree", expanded=not turns):
        if mermaid:
            st_mermaid(mermaid, height="320px", key="dbg-agent-tree")
        else:
            st.info("Agent tree unavailable.")

    if not turns:
        st.info("No turns yet — send a message in the **💬 Chat** tab.")
        return

    grand = {"events": 0, "tool_calls": 0, "tokens": 0}
    for t in turns:
        tt = _turn_totals(t["snapshots"])
        for k in grand:
            grand[k] += tt[k]
    st.caption(f"📊 **{len(turns)}** turn(s) · {grand['events']} events · "
               f"{grand['tool_calls']} tool calls · {_toks(grand['tokens'])}")

    labels = [f"{i + 1}. {t['prompt'][:48]}{'…' if len(t['prompt']) > 48 else ''}"
              for i, t in enumerate(turns)]
    idx = st.selectbox("Turn", range(len(turns)), index=len(turns) - 1,
                       format_func=lambda i: labels[i], label_visibility="collapsed")
    turn = turns[idx]
    snaps = turn["snapshots"]
    tt = _turn_totals(snaps)
    by_author = _events_by_author(snaps)
    st.caption(f"⏱ {turn.get('latency', 0):.2f}s · {tt['events']} events · "
               f"{tt['tool_calls']} tools · {_toks(tt['tokens'])}  \n"
               f"{_event_flow(snaps)}  \n"
               "agents — " + " · ".join(f"**{a}**: {n}" for a, n in by_author.items()))

    session = turn.get("session") or {}
    left, right = st.columns([3, 1])
    with left.expander("🗃️ Session state", expanded=False):
        if session.get("state_error"):
            st.warning(session["state_error"])
        st.json(session.get("state", {}))
        arts = session.get("artifacts", [])
        st.caption(f"Artifacts: {', '.join(arts) if arts else 'none'}")
    right.download_button("⬇️ JSON", data=json.dumps([s["raw"] for s in snaps], indent=2, default=str),
                          file_name=f"adk_turn_{idx + 1}_events.json", mime="application/json",
                          use_container_width=True, help="Download this turn's raw ADK events")

    t0 = next((s["timestamp"] for s in snaps if s.get("timestamp")), None)
    for s in snaps:
        kinds = _kinds(s)
        badges = " ".join(_KIND_BADGE.get(k, k) for k in kinds) or "—"
        rel = f"+{s['timestamp'] - t0:.2f}s" if (s.get("timestamp") and t0) else ""
        flags = " ".join(f for f, on in (("partial", s.get("partial")), ("final", s.get("is_final"))) if on)
        llm = "🤖 " if s.get("usage") else ""
        title = f"#{s['seq']} · {llm}{s['author']} · {badges}"
        if rel:
            title += f" · {rel}"
        if flags:
            title += f" · {flags}"
        with st.expander(title, expanded=False):
            if s.get("usage"):
                st.caption("tokens — " + ", ".join(f"{k}: {v}" for k, v in s["usage"].items() if v))
            for p in s.get("parts", []):
                if p["kind"] == "tool_call":
                    st.markdown(f"🔧 **{p['tool_name']}**")
                    st.json(p.get("tool_args") or {})
                elif p["kind"] == "tool_result":
                    st.markdown(f"↳ **{p['tool_name']}** returned")
                    st.json(p.get("tool_result"))
                elif p["kind"] in ("thinking", "text"):
                    st.markdown(f"_{p['kind']}_: {p.get('text')}")
            with st.popover("raw event JSON"):
                st.json(s.get("raw"))


# ----------------------------------------------------------------- agents ---
def render_agents_tab(app: str, backend_name: str, on_rebuilt) -> None:
    """Edit each agent's prompt/model via the server. Save → PUT overrides;
    Reset → DELETE. Both rebuild the agent server-side, so we call `on_rebuilt()`
    to refresh the session, then rerun."""
    try:
        data = api_client.get_agents(app, backend_name)
    except Exception as e:  # noqa: BLE001
        st.error(f"Couldn't reach the server: {e}")
        return
    editable = data.get("editable", [])
    saved = api_client.get_overrides(app)
    if not editable:
        st.info("This app's agents expose no editable prompt or model.")
        return

    overlaid = sum(1 for a in editable if a["name"] in saved)
    st.caption(f"Editing **{app}** · backend **{backend_name}** · "
               f"{len(editable)} agent(s), {overlaid} overlaid. "
               "Saving recreates the agents from the overlay — your conversation is kept.")

    with st.form(f"agents::{app}"):
        fields = {}
        for ag in editable:
            name = ag["name"]
            st.markdown(f"### 🧠 {name} · `{ag['type']}`")
            if ag.get("description"):
                st.caption(ag["description"])
            model_val = st.text_input(
                "Model override", value=saved.get(name, {}).get("model", ""),
                placeholder=f"default: {ag.get('model') or '—'} (follows Backend)",
                key=f"model::{app}::{name}",
                help="A concrete model id pins this agent; blank follows the Backend selector.")
            instr = ag.get("instruction")
            if instr is not None:
                instr_val = st.text_area("Instruction (system prompt)", value=instr,
                                         key=f"instr::{app}::{name}", height=180)
            else:
                instr_val = None
                st.caption("_Structural agent — no editable prompt._")
            fields[name] = (model_val, instr_val, instr)
            st.divider()
        c1, c2 = st.columns(2)
        save = c1.form_submit_button("💾 Save & apply", width="stretch", type="primary")
        reset = c2.form_submit_button("↩️ Reset to code defaults", width="stretch")

    if save:
        new = dict(saved)
        for name, (model_val, instr_val, live_instr) in fields.items():
            entry = dict(saved.get(name, {}))
            if instr_val is not None and instr_val != live_instr:
                entry["instruction"] = instr_val
            if model_val.strip():
                entry["model"] = model_val.strip()
            else:
                entry.pop("model", None)
            if entry:
                new[name] = entry
            else:
                new.pop(name, None)
        api_client.put_overrides(app, new)
        on_rebuilt()
        st.rerun()
    if reset:
        api_client.delete_overrides(app)
        on_rebuilt()
        st.rerun()
