"""Claude-style chat UI — a thin client over the FastAPI agent server.

    ./local_run.sh server      # start the backend (uvicorn :8000) first
    streamlit run apps/pages/streamlit_app.py

This file is orchestration only: page setup, the sidebar (app/backend/mode, PDF
upload, conversations), session management, and tab wiring. The tabs themselves
live in `ui_chat`, `ui_debug`, `ui_agent`. It holds NO ADK objects and touches NO
disk — everything goes through `api_client` to `backend/server.py`. Point at
another server with the API_BASE_URL env var.
"""
import json

import streamlit as st

from apps.pages import api_client, ui_agent, ui_chat, ui_debug

# pdf_insight query modes: friendly label -> the mode constant the server passes
# through to the coordinator. Mirrors backend/pdf_insight/config.py.
PDF_MODES = {
    "auto — let the agent decide": "auto",
    "all tables → text": "LLM_GETS_ALL_TABLES_AS_TEXT",
    "some tables → text": "LLM_GETS_SOME_TABLES_AS_TEXT",
    "SQL over THIS pdf (SQLite)": "LLM_MAKES_SQL_FROM_CHAT",
    "SQL over the WHOLE corpus (DuckDB)": "LLM_QUERIES_CORPUS",
    "raw pdf bytes (native)": "LLM_GETS_PDF_BYTES",
}


# --------------------------------------------------------------- session ----
def _ensure_session(app: str, backend: str) -> str:
    """Reuse the session for (app, backend), or create a fresh one server-side.
    A fresh session clears the transcript unless `_keep_history` is set (agent
    edits / conversation loads keep it)."""
    ss = st.session_state
    ss.setdefault("messages", [])
    ss.setdefault("debug_turns", [])
    if ss.get("app") == app and ss.get("backend") == backend and ss.get("session_id"):
        if not ss.get("agents_mermaid"):  # backfill if a prior fetch failed
            ss["agents_mermaid"] = api_client.get_agents(app, backend).get("mermaid")
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
st.set_page_config(page_title="ADK Chat", page_icon="🤖", layout="wide")

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
pdf_mode = PDF_MODES.get(pdf_mode_label) if pdf_mode_label else None

tab_chat, tab_debug, tab_agents = st.tabs(["💬 Chat", "🔍 Debug", "🛠️ Agents"])

with tab_chat:
    ui_chat.render_chat_tab(app, session_id, pdf_mode, prompt)

# Per-turn save insurance once the conversation is named (the turn just landed in
# session_state during the chat render above).
if prompt and st.session_state.get("conv_id"):
    api_client.save_conversation(_save_payload(app, chosen, pdf_mode_label))
    st.session_state["_autosave_hash"] = _msgs_hash(st.session_state.messages)

with tab_debug:
    ui_debug.render_debug_tab(st.session_state.get("debug_turns", []),
                              st.session_state.get("agents_mermaid"))

with tab_agents:
    ui_agent.render_agents_tab(app, chosen, _on_agents_rebuilt)

_autosave_tick()
