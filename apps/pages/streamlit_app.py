"""Claude-style chat UI — a thin client over the FastAPI agent server.

    ./local_run.sh server      # start the backend (uvicorn :8000) first
    streamlit run apps/pages/streamlit_app.py

This file holds NO ADK objects and touches NO disk. Everything — running a turn,
editing agents, conversations, PDF ingest — goes through `api_client` to
`backend/server.py`. The live turn streams over SSE; this file just draws the
flat frames (text/thinking/tool/final) it receives. Point at another server with
the API_BASE_URL env var.
"""
import json
import os
import re
import time

import pandas as pd
import streamlit as st
from streamlit_mermaid import st_mermaid

from apps.pages import api_client, render

USER_ID = "you"

# pdf_insight query modes: friendly label -> the mode constant the server passes
# through to the coordinator. Mirrors backend/pdf_insight/config.py (kept here so
# the client imports nothing from the backend).
PDF_MODES = {
    "auto — let the agent decide": "auto",
    "all tables → text": "LLM_GETS_ALL_TABLES_AS_TEXT",
    "some tables → text": "LLM_GETS_SOME_TABLES_AS_TEXT",
    "SQL over THIS pdf (SQLite)": "LLM_MAKES_SQL_FROM_CHAT",
    "SQL over the WHOLE corpus (DuckDB)": "LLM_QUERIES_CORPUS",
    "raw pdf bytes (native)": "LLM_GETS_PDF_BYTES",
}


# ----------------------------------------------------------- chat rendering ---
def _pretty_tool(name: str, args: dict | None) -> str:
    if name == "transfer_to_agent" and args:
        return f"Delegating to **{args.get('agent_name', '?')}**"
    return f"Using **{name}**"


_MERMAID_RE = re.compile(r"```mermaid\s*\n?(.*?)```", re.DOTALL)


def _render_answer(text: str, key_prefix: str) -> None:
    """Render assistant text, drawing any ```mermaid fenced blocks as diagrams."""
    pos, n, matched = 0, 0, False
    for m in _MERMAID_RE.finditer(text):
        matched = True
        before = text[pos:m.start()].strip()
        if before and not (before.startswith("{") and before.endswith("}")):
            st.markdown(before)
        diagram = m.group(1).strip()
        height = f"{min(900, max(280, (diagram.count(chr(10)) + 1) * 46))}px"
        st_mermaid(diagram, height=height, key=f"{key_prefix}-mmd{n}")
        pos, n = m.end(), n + 1
    if not matched:
        st.markdown(text)
    elif text[pos:].strip():
        st.markdown(text[pos:])


_SQL_TOOLS = ("run_sql", "run_corpus_sql")


def _to_number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace(",", "").replace("%", "").strip()
        if s in ("", "-", "—", "n/a", "N/A"):
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _numeric_columns(columns: list[str], rows: list[list]) -> list[str]:
    numeric = []
    for i, col in enumerate(columns):
        vals = [r[i] for r in rows if i < len(r) and r[i] not in (None, "")]
        if vals and all(_to_number(v) is not None for v in vals):
            numeric.append(col)
    return numeric


def _sql_result_payload(tool_result) -> dict | None:
    r = tool_result
    if isinstance(r, dict) and "columns" not in r and isinstance(r.get("result"), dict):
        r = r["result"]
    if (isinstance(r, dict) and r.get("status") == "success"
            and r.get("columns") and r.get("rows")):
        return {"columns": list(r["columns"]), "rows": [list(row) for row in r["rows"]]}
    return None


def _render_sql_results(results: list[dict]) -> None:
    for res in results:
        columns, rows = res["columns"], res["rows"]
        st.caption(f"📊 `{res['tool_name']}` — {len(rows)} row(s)")
        st.dataframe([dict(zip(columns, r)) for r in rows], width="stretch", hide_index=True)
        if len(rows) < 2:
            continue
        numeric = _numeric_columns(columns, rows)
        date_col = next((c for c in columns if c.lower() == "report_date" and c not in numeric), None)
        measures = [c for c in numeric if c != date_col]
        if not measures:
            continue
        frame = {c: ([_to_number(r[i]) if i < len(r) else None for r in rows] if c in numeric
                     else [r[i] if i < len(r) else None for r in rows])
                 for i, c in enumerate(columns)}
        df = pd.DataFrame(frame)
        if date_col:
            st.line_chart(df.sort_values(date_col), x=date_col, y=measures)
        else:
            category = next((c for c in columns if c not in numeric), None)
            if category:
                st.bar_chart(df, x=category, y=measures)
            else:
                st.bar_chart(df[measures])


def _meta_caption(latency, usage) -> str:
    parts = []
    if latency is not None:
        parts.append(f"⏱ {latency:.1f}s")
    total = (usage or {}).get("total", 0)
    parts.append(f"{total:,} tokens" if total else "tokens n/a")
    return " · ".join(parts)


def _render_steps(steps: list[dict]) -> None:
    for s in steps:
        if s["kind"] == "thinking":
            st.markdown(s["text"])
        elif s["kind"] == "tool_call":
            st.markdown(f"🔧 {_pretty_tool(s['tool_name'], s['tool_args'])}")
            if s["tool_args"]:
                st.json(s["tool_args"])
        elif s["kind"] == "tool_result":
            st.markdown(f"↳ **{s['tool_name']}** returned")
            st.json(s["tool_result"])


# --------------------------------------------------------------- session ----
def _ensure_session(app: str, backend: str) -> str:
    """Reuse the session for (app, backend), or create a fresh one server-side.
    A fresh session clears the transcript unless `_keep_history` is set (agent
    edits / conversation loads keep it)."""
    ss = st.session_state
    if ss.get("app") == app and ss.get("backend") == backend and ss.get("session_id"):
        return ss["session_id"]
    sid = api_client.create_session(app, backend)
    keep = ss.pop("_keep_history", False)
    ss["app"], ss["backend"], ss["session_id"] = app, backend, sid
    ss["agents_mermaid"] = api_client.get_agents(app, backend).get("mermaid")
    if keep:
        ss.setdefault("messages", [])
        ss.setdefault("debug_turns", [])
    else:
        ss["messages"], ss["debug_turns"] = [], []
    return sid


# --------------------------------------------------- conversations (server) ---
def _msgs_hash(msgs: list[dict]) -> str:
    return json.dumps(msgs, default=str, sort_keys=True)


def _save_payload(app: str, backend: str, pdf_mode_label: str | None) -> dict:
    ss = st.session_state
    return {
        "id": ss.get("conv_id"),
        "app": app, "backend": backend,
        "title": ss.get("conv_title"),
        "messages": ss.get("messages", []),
        "debug_turns": ss.get("debug_turns", []),
        "extra": {"pdf_mode": pdf_mode_label},
    }


def _new_conversation() -> None:
    for k in ("session_id", "app", "backend", "messages", "debug_turns",
              "conv_id", "conv_title", "_autosave_hash", "agents_mermaid"):
        st.session_state.pop(k, None)


def _load_conversation(conv_id: str) -> None:
    """Callback: load a saved conversation (sets widget keys before the rerun)."""
    ss = st.session_state
    data = api_client.load_conversation(conv_id)
    meta = data.get("meta", {})
    if meta.get("app") in APPS_LIST:
        ss["app_select"] = meta["app"]
    if meta.get("backend") in BACKENDS:
        ss["backend_select"] = meta["backend"]
    ss["messages"] = data.get("messages", [])
    ss["debug_turns"] = data.get("debug_turns", [])
    ss["conv_id"] = meta.get("id", conv_id)
    ss["conv_title"] = meta.get("title", conv_id)
    ss["_autosave_hash"] = _msgs_hash(ss["messages"])
    ss["_keep_history"] = True
    ss.pop("session_id", None)  # force a fresh server session for the loaded app


def _delete_conversation(conv_id: str) -> None:
    api_client.delete_conversation(conv_id)
    ss = st.session_state
    if ss.get("conv_id") == conv_id:
        for k in ("conv_id", "conv_title", "_autosave_hash"):
            ss.pop(k, None)
    ss.pop("conv_pick", None)


def _on_agents_rebuilt() -> None:
    """After a Save/Reset rebuilt the agent server-side, the old session was
    dropped — start a fresh one but keep the transcript."""
    st.session_state["_keep_history"] = True
    st.session_state.pop("session_id", None)
    st.session_state.pop("agents_mermaid", None)


# ---------------------------------------------------------------- page setup ---
st.set_page_config(page_title="ADK Chat", page_icon="🤖", layout="centered")

if not api_client.health():
    st.error(f"Can't reach the agent server at `{api_client.BASE_URL}`. "
             "Start it with `./local_run.sh server` (or set `API_BASE_URL`).")
    st.stop()

_meta = api_client.list_apps()
APPS_LIST = _meta["apps"]
BACKENDS = _meta["backends"]


@st.fragment(run_every=300)  # autosave cadence (5 min)
def _autosave_tick() -> None:
    ss = st.session_state
    msgs = ss.get("messages") or []
    if not msgs or ss.get("_autosave_hash") == _msgs_hash(msgs):
        return
    payload = _save_payload(ss.get("app", "pdf_insight"), ss.get("backend", "mock"), None)
    if not payload["id"]:
        # name it on first autosave so subsequent saves overwrite the same folder
        res = api_client.save_conversation(payload)
        ss["conv_id"] = res["id"]
    else:
        api_client.save_conversation(payload)
    ss["_autosave_hash"] = _msgs_hash(msgs)


with st.sidebar:
    st.title("🤖 ADK Chat")
    app = st.selectbox("App", APPS_LIST, key="app_select")
    chosen = st.selectbox("Backend", BACKENDS, key="backend_select")
    if chosen == "mock":
        st.caption("_mock — offline, free, streaming simulated_")
    else:
        st.caption(f"_live: **{chosen}** — uses its API key + real tokens_")

    pdf_mode_label = None
    if app == "pdf_insight":
        pdf_mode_label = st.selectbox("Query mode", list(PDF_MODES), key="pdf_mode")

    # --- Conversations -----------------------------------------------------
    st.divider()
    st.subheader("💾 Conversations")
    cur_title = st.session_state.get("conv_title")
    st.caption(f"Open: **{cur_title}**" if cur_title else "Open: _unsaved draft_")
    cs, cn = st.columns(2)
    if cs.button("💾 Save", width="stretch", type="primary",
                 disabled=not st.session_state.get("messages")):
        res = api_client.save_conversation(_save_payload(app, chosen, pdf_mode_label))
        st.session_state["conv_id"] = res["id"]
        st.session_state["conv_title"] = (st.session_state.get("conv_title")
                                          or res["id"])
        st.session_state["_autosave_hash"] = _msgs_hash(st.session_state.get("messages", []))
        st.success(f"Saved → `{res['folder']}`")
    cn.button("🆕 New", width="stretch", on_click=_new_conversation)

    saved = api_client.list_conversations()
    if saved:
        opts = {f"{m.get('title', m['id'])}  ·  {m.get('updated_at', '')[:16]}": m["id"]
                for m in saved}
        pick = st.selectbox("Open saved", list(opts), index=None,
                            placeholder=f"{len(saved)} saved…", key="conv_pick")
        if pick:
            cid = opts[pick]
            bo, bd = st.columns(2)
            bo.button("📂 Open", width="stretch", on_click=_load_conversation, args=(cid,))
            bd.button("🗑️ Delete", width="stretch", on_click=_delete_conversation, args=(cid,))

    # --- PDF Insight upload ------------------------------------------------
    if app == "pdf_insight":
        st.divider()
        st.subheader("📄 PDF Insight")
        uploads = st.file_uploader("Upload PDF(s)", type="pdf",
                                   accept_multiple_files=True, key="pdf_uploads")
        seen = st.session_state.setdefault("pdf_uploaded", set())
        for f in uploads or []:
            if f.name in seen:
                continue
            try:
                res = api_client.upload_pdf(f.name, f.getvalue())
                s = res["summary"]
                st.success(f"📄 {f.name}: sqlite {s['sqlite']['status']}, "
                           f"corpus {s['corpus']['status']}")
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not ingest {f.name}: {e}")
            seen.add(f.name)
        st.caption("Each upload replaces the single-PDF SQLite and **appends** to the corpus.")


session_id = _ensure_session(app, chosen)

# Pinned to the bottom of the viewport; history scrolls above.
prompt = st.chat_input("Message…")

tab_chat, tab_debug, tab_agents = st.tabs(["💬 Chat", "🔍 Debug", "🛠️ Agents"])

with tab_chat:
    if app == "pdf_insight":
        with st.expander("📄 What's queryable", expanded=False):
            try:
                schema = api_client.pdf_schema()
            except Exception as e:  # noqa: BLE001
                schema = None
                st.caption(f"schema unavailable: {e}")
            if schema:
                active = schema.get("active_pdf")
                st.caption(f"Active PDF: **{os.path.basename(active)}**" if active
                           else "No PDF uploaded yet — upload one in the sidebar.")
                corpus = schema.get("corpus") or {}
                if corpus.get("status") == "success":
                    docs = corpus["documents"]
                    st.markdown(f"**Corpus** — {docs['count']} report(s), {docs['from']} → {docs['to']}")
                    st.dataframe([{"table": t["table"], "title": t["title"],
                                   "columns": ", ".join(t["columns"])} for t in corpus["tables"]],
                                 width="stretch", hide_index=True)
                sqlite = schema.get("sqlite")
                if sqlite and sqlite.get("status") == "success":
                    st.markdown("**This PDF (SQLite)** — " + ", ".join(t["table"] for t in sqlite["schema"]))

    for i, m in enumerate(st.session_state.get("messages", [])):
        with st.chat_message(m["role"]):
            if m["role"] == "assistant":
                if m.get("steps"):
                    with st.expander("💭 Thinking", expanded=False):
                        _render_steps(m["steps"])
                _render_answer(m["content"], key_prefix=f"hist{i}")
                if m.get("sql_results"):
                    _render_sql_results(m["sql_results"])
                if m.get("latency") is not None or m.get("usage"):
                    st.caption(_meta_caption(m.get("latency"), m.get("usage")))
            else:
                st.markdown(m["content"])

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            status = st.status("Thinking…", expanded=False)
            answer_box = st.empty()
            answer, steps, usage = "", [], None
            sql_results, debug_snaps, session_info = [], [], {}
            t_start = time.perf_counter()

            pdf_mode = PDF_MODES.get(pdf_mode_label) if pdf_mode_label else None
            try:
                for fr in api_client.stream_message(app, session_id, prompt, pdf_mode):
                    kind = fr["kind"]
                    if kind == "thinking_delta":
                        steps.append({"kind": "thinking", "text": fr["text"]})
                        status.markdown(fr["text"])
                    elif kind == "tool_call":
                        status.update(label=f"{_pretty_tool(fr['tool_name'], fr['tool_args'])}…")
                        steps.append({"kind": "tool_call", "tool_name": fr["tool_name"],
                                      "tool_args": fr["tool_args"]})
                        status.markdown(f"🔧 {_pretty_tool(fr['tool_name'], fr['tool_args'])}")
                        if fr["tool_args"]:
                            status.json(fr["tool_args"])
                    elif kind == "tool_result":
                        steps.append({"kind": "tool_result", "tool_name": fr["tool_name"],
                                      "tool_result": fr["tool_result"]})
                        if fr["tool_name"] in _SQL_TOOLS:
                            payload = _sql_result_payload(fr["tool_result"])
                            if payload:
                                sql_results.append({"tool_name": fr["tool_name"], **payload})
                        status.markdown(f"↳ **{fr['tool_name']}** returned")
                        status.json(fr["tool_result"])
                    elif kind == "text_delta":
                        status.update(label="Responding…")
                        answer += fr["text"]
                        answer_box.markdown(answer + " ▌")
                    elif kind == "error":
                        status.update(label="Error", state="error")
                        st.error(fr["text"])
                    elif kind == "final":
                        usage = fr.get("usage")
                        session_info = fr.get("session_info", {})
                        debug_snaps = fr.get("debug_snapshots", [])
            except Exception as e:  # noqa: BLE001 - surface transport errors in-chat
                status.update(label="Error", state="error")
                st.error(f"{type(e).__name__}: {e}")

            latency = time.perf_counter() - t_start
            status.update(label="Done" if answer else "Done (no text reply)",
                          state="complete", expanded=False)
            answer_box.empty()
            _render_answer(answer, key_prefix=f"turn{len(st.session_state.messages)}")
            if sql_results:
                _render_sql_results(sql_results)
            st.caption(_meta_caption(latency, usage))

        st.session_state.messages.append(
            {"role": "assistant", "content": answer, "steps": steps,
             "sql_results": sql_results, "latency": latency, "usage": usage})
        st.session_state.debug_turns.append(
            {"prompt": prompt, "snapshots": debug_snaps, "latency": latency, "session": session_info})

        # Per-turn save insurance once the conversation is named.
        if st.session_state.get("conv_id"):
            api_client.save_conversation(_save_payload(app, chosen, pdf_mode_label))
            st.session_state["_autosave_hash"] = _msgs_hash(st.session_state.messages)

with tab_debug:
    render.render_debug_tab(st.session_state.get("debug_turns", []),
                            st.session_state.get("agents_mermaid"))

with tab_agents:
    render.render_agents_tab(app, chosen, _on_agents_rebuilt)

_autosave_tick()
