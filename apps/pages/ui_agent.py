"""Agents tab — pick one agent, edit its fields, see/restore its history.

Reads the agent's current fields (code default + live overlay) from the server,
saves edits through /agent_definition/update (which versions them in DuckDB and
syncs the live overlay), and lists past versions with a restore button. No ADK,
no disk — all over the API. `on_rebuilt()` refreshes the session after a change.
"""
from __future__ import annotations

import streamlit as st

from apps.pages import api_client


def _excerpt(text: str | None, n: int = 90) -> str:
    return (text or "").replace("\n", " ").strip()[:n] or "—"


def render_agents_tab(app: str, backend_name: str, on_rebuilt) -> None:
    try:
        editable = api_client.get_agents(app, backend_name).get("editable", [])
    except Exception as e:  # noqa: BLE001
        st.error(f"Couldn't reach the server: {e}")
        return
    if not editable:
        st.info("This app's agents expose no editable prompt or model.")
        return

    names = [a["name"] for a in editable]
    agent = st.selectbox(f"Agent in **{app}** (backend **{backend_name}**)", names,
                         key=f"agent_sel::{app}")
    defn = api_client.read_agent_def(app, agent, backend_name)
    live, overlay = defn.get("live") or {}, defn.get("overlay") or {}
    pinned = [f for f in ("instruction", "model", "description") if f in overlay]
    st.caption(f"`{live.get('type', '?')}` · "
               + (f"overridden: {', '.join(pinned)}" if pinned else "all code defaults"))

    # --- edit ---------------------------------------------------------------
    with st.form(f"agent::{app}::{agent}"):
        has_instr = live.get("instruction") is not None
        instr_val = st.text_area("Instruction (system prompt)",
                                 value=live.get("instruction") or "",
                                 key=f"instr::{app}::{agent}", height=200,
                                 disabled=not has_instr) if has_instr else None
        if not has_instr:
            st.caption("_Structural agent — no editable prompt._")
        model_val = st.text_input(
            "Model override", value=overlay.get("model", ""),
            placeholder=f"default: {live.get('model') or '—'} (follows Backend)",
            key=f"model::{app}::{agent}",
            help="A concrete model id pins this agent; blank follows the Backend selector.")
        desc_val = st.text_input("Description", value=live.get("description") or "",
                                 key=f"desc::{app}::{agent}")
        saved = st.form_submit_button("💾 Save & apply", width="stretch", type="primary")

    if saved:
        api_client.update_agent_def(app, agent, instruction=instr_val,
                                    model=model_val.strip(), description=desc_val)
        on_rebuilt()
        st.rerun()

    # --- history ------------------------------------------------------------
    with st.expander("🕓 History", expanded=False):
        hist = api_client.agent_def_history(app, agent)
        if not hist:
            st.caption("No saved versions yet — edits you save here will show up.")
            return
        for v in hist:
            c1, c2 = st.columns([5, 1])
            c1.markdown(
                f"**v{v['version']}** · {v['updated_at'][:19]} · "
                f"model `{v['model'] or '—'}`  \n"
                f"_instr_: {_excerpt(v['instruction'])} · _desc_: {_excerpt(v['description'], 50)}")
            if c2.button("↩️ Restore", key=f"restore::{agent}::{v['version']}",
                         width="stretch"):
                api_client.restore_agent_def(app, agent, v["version"])
                on_rebuilt()
                st.rerun()
            st.divider()
