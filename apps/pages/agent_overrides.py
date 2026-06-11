"""Editable agent overlays — change any ADK agent's prompt/model without code.

Every app here builds its graph in code (`module.root_agent`). This module lets
the Streamlit UI overlay two fields per agent — `instruction` (the system prompt)
and `model` — from a per-app JSON file, so prompts/models can be tuned live and
the change survives a restart.

Flow:
  1. The UI edits fields and calls `save(app, overrides)` -> writes
     `data/agent_overrides/<app>.json` ({agent_name: {instruction?, model?}}).
  2. The app recreates its agents (a fresh package import) and calls
     `apply(root_agent, load(app))`, which mutates the freshly-built agents.
     ADK agents are plain pydantic models, so setting `.instruction`/`.model`
     on them is all it takes — no rebuild of the class, no code edit.

Generic on purpose: it walks sub_agents and AgentTool-wrapped agents, so it works
for pdf_insight, travel_planner, graph_builder, and text_to_diagram alike. An
override naming an agent that no longer exists is simply ignored.

Model note: agents default to a lazy proxy that follows the sidebar Backend
selector (see shared.model). A non-empty `model` override pins THAT agent to a
concrete id (e.g. 'gemini-2.5-pro'), bypassing the selector; blank keeps the proxy.
"""
from __future__ import annotations

import json
import os
from typing import Iterator

_DIR = os.path.join("data", "agent_overrides")


def _path(app: str) -> str:
    return os.path.join(_DIR, f"{app}.json")


def load(app: str) -> dict:
    """Read the saved overrides for `app`, or {} if none / unreadable."""
    try:
        with open(_path(app), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(app: str, overrides: dict) -> str:
    """Write `overrides` ({agent_name: {instruction?, model?}}) and return the path.
    Agents with no overridden field are dropped so the file stays minimal."""
    pruned = {name: ov for name, ov in overrides.items() if ov}
    os.makedirs(_DIR, exist_ok=True)
    path = _path(app)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(pruned, fh, indent=2, ensure_ascii=False)
    return path


def clear(app: str) -> None:
    """Delete the app's overlay file (revert to pure code defaults)."""
    try:
        os.remove(_path(app))
    except FileNotFoundError:
        pass


def iter_agents(agent, _seen: set | None = None) -> Iterator:
    """Yield every distinct agent reachable from `agent`: itself, its sub_agents
    (transfer targets), and any agents wrapped by AgentTools. De-duplicated by id."""
    if _seen is None:
        _seen = set()
    if agent is None or id(agent) in _seen:
        return
    _seen.add(id(agent))
    yield agent
    for sub in getattr(agent, "sub_agents", None) or []:
        yield from iter_agents(sub, _seen)
    for tool in getattr(agent, "tools", None) or []:
        inner = getattr(tool, "agent", None)
        if inner is not None:
            yield from iter_agents(inner, _seen)


def instruction_of(agent) -> str | None:
    """The agent's instruction if it's a plain string we can edit, else None.
    (ADK also allows callable InstructionProviders, which aren't text-editable.)"""
    instr = getattr(agent, "instruction", None)
    return instr if isinstance(instr, str) else None


def model_id_of(agent) -> str | None:
    """A human-readable model id for the agent: the string itself, the lazy proxy's
    live id (e.g. 'mock'/'gemini-…'), or the class name as a last resort."""
    model = getattr(agent, "model", None)
    if model is None:
        return None
    if isinstance(model, str):
        return model or None
    inner = getattr(model, "model", None)
    return inner if isinstance(inner, str) else type(model).__name__


def is_editable(agent) -> bool:
    """True when the agent exposes a prompt or a model worth editing."""
    return instruction_of(agent) is not None or getattr(agent, "model", None) is not None


def apply(root_agent, overrides: dict) -> list[str]:
    """Overlay `overrides` onto the live agent graph. Returns the names applied.

    Mutates in place: sets `.instruction` and/or `.model` on each named agent.
    Unknown names and absent fields are skipped. A blank model is ignored here
    (the freshly-built agent already carries its lazy default)."""
    if not overrides:
        return []
    applied: list[str] = []
    for ag in iter_agents(root_agent):
        ov = overrides.get(getattr(ag, "name", None))
        if not ov:
            continue
        touched = False
        instr = ov.get("instruction")
        if isinstance(instr, str) and hasattr(ag, "instruction"):
            ag.instruction = instr
            touched = True
        model = ov.get("model")
        if model:
            ag.model = model
            touched = True
        if touched:
            applied.append(ag.name)
    return applied


# ------------------------------------------------------------------- render ---
def render_agents_tab(root_agent, app: str, backend_name: str) -> None:
    """Draw the agent editor. Reads the live (already-overlaid) graph for current
    values; on Save it writes the JSON overlay and asks the app to recreate the
    agents from it (keeping the conversation). `backend_name` is shown for context
    so it's clear which backend a blank-model agent will follow."""
    import streamlit as st

    if root_agent is None:
        st.info("No agent loaded.")
        return

    editable = [a for a in iter_agents(root_agent) if is_editable(a)]
    if not editable:
        st.info("This app's agents expose no editable prompt or model.")
        return

    saved = load(app)
    overlaid = sum(1 for a in editable if a.name in saved)
    st.caption(
        f"Editing **{app}** · backend **{backend_name}** · "
        f"{len(editable)} agent(s), {overlaid} overlaid. "
        "Saving writes `data/agent_overrides/" + app + ".json` and recreates the "
        "agents from it — your conversation is kept."
    )

    with st.form(f"agents::{app}"):
        fields: dict[str, tuple] = {}
        for ag in editable:
            name = ag.name
            st.markdown(f"### 🧠 {name} · `{type(ag).__name__}`")
            desc = getattr(ag, "description", None)
            if desc:
                st.caption(desc)

            live_id = model_id_of(ag) or "—"
            model_val = st.text_input(
                "Model override",
                value=saved.get(name, {}).get("model", ""),
                placeholder=f"default: {live_id} (follows Backend)",
                key=f"model::{app}::{name}",
                help="A concrete model id (e.g. 'gemini-2.5-pro') pins this agent and "
                     "bypasses the Backend selector. Blank = follow the sidebar backend.",
            )

            live_instr = instruction_of(ag)
            if live_instr is not None:
                instr_val = st.text_area(
                    "Instruction (system prompt)", value=live_instr,
                    key=f"instr::{app}::{name}", height=180,
                )
            else:
                instr_val = None
                st.caption("_Structural agent — no editable prompt._")

            fields[name] = (model_val, instr_val, live_instr)
            st.divider()

        c1, c2 = st.columns(2)
        save_clicked = c1.form_submit_button(
            "💾 Save & apply", width="stretch", type="primary")
        reset_clicked = c2.form_submit_button(
            "↩️ Reset to code defaults", width="stretch")

    if save_clicked:
        new = dict(saved)
        for name, (model_val, instr_val, live_instr) in fields.items():
            entry = dict(saved.get(name, {}))
            # Only pin an instruction the user actually changed — leaves untouched
            # prompts following the code, while keeping any prior overlay intact.
            if instr_val is not None and instr_val != live_instr:
                entry["instruction"] = instr_val
            model_val = model_val.strip()
            if model_val:
                entry["model"] = model_val
            else:
                entry.pop("model", None)
            if entry:
                new[name] = entry
            else:
                new.pop(name, None)
        save(app, new)
        _request_recreate(st)

    if reset_clicked:
        clear(app)
        _request_recreate(st)


def _request_recreate(st) -> None:
    """Drop the cached runner so the app recreates the agents (re-import + overlay)
    on the next run, but keep the chat visible across the swap."""
    st.session_state["_keep_history"] = True
    for k in ("runner", "session_id", "app", "backend"):
        st.session_state.pop(k, None)
    st.rerun()
