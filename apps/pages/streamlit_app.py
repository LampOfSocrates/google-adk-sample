"""Claude-style chat UI over the ADK agents in this repo.

    streamlit run apps/pages/streamlit_app.py

Pick an app in the sidebar, then chat. Tool calls and reasoning show up in a
collapsible "Thinking" block per turn (like Claude); the answer streams in token
by token. Uses LLM_BACKEND from .env (mock by default — free, offline; live
backends stream for real).

All ADK contact is funneled through `shared.ui_stream.stream_ui_events`, which
turns raw events into flat UIEvents. This file only knows how to draw UIEvents.
"""
import asyncio
import hashlib
import importlib
import json
import os
import re
import sys
import time

import certifi

# `streamlit run` puts THIS file's dir (apps/pages) on sys.path, not the repo
# root, so the absolute `apps.*`/`shared.*` imports below would miss. Put the repo
# root (two levels up) first. (local_run.sh also exports PYTHONPATH=root; this
# makes a bare `streamlit run apps/pages/streamlit_app.py` work too.)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Stray SSL_CERT_FILE on this box breaks the HTTPS clients; force certifi.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ.pop("SSL_CERT_DIR", None)

from dotenv import load_dotenv

load_dotenv()

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from streamlit_mermaid import st_mermaid  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402

from apps.pages import agent_overrides, conversations  # noqa: E402
from apps.pages.ui_debug import fetch_session_info, render_debug_tab, snapshot_event  # noqa: E402
from shared.model import backend  # noqa: E402
from shared.ui_stream import stream_ui_events  # noqa: E402

from apps.pdf_insight import config as pdf_config  # noqa: E402
from apps.pdf_insight.ingest import ingest_pdf_everywhere  # noqa: E402
from apps.pdf_insight.stores import SQLiteStore, get_corpus_store  # noqa: E402

# name -> module exposing `root_agent`. Each is a separate ADK app/session.
APPS = {
    "pdf_insight": "apps.pdf_insight.agent",
    "travel_planner": "apps.travel_planner.agent",
    "graph_builder": "apps.graph_builder.agent",
    "text_to_diagram": "apps.text_to_diagram.agent",
}
USER_ID = "you"
BACKENDS = ["mock", "gemini", "openai", "deepseek", "bedrock"]
UPLOAD_DIR = os.path.join("data", "uploads")

# Friendly label -> mode constant the coordinator understands (via a `mode:` directive).
PDF_MODES = {
    "auto — let the agent decide": pdf_config.AUTO,
    "all tables → text": pdf_config.ALL_TABLES_AS_TEXT,
    "some tables → text": pdf_config.SOME_TABLES_AS_TEXT,
    "SQL over THIS pdf (SQLite)": pdf_config.SQL_FROM_TEXT,
    "SQL over the WHOLE corpus (DuckDB)": pdf_config.QUERY_CORPUS,
    "raw pdf bytes (native)": pdf_config.PDF_BYTES,
}


def _loop() -> asyncio.AbstractEventLoop:
    """One persistent event loop for the session. InMemoryRunner's async session
    service must be created and driven on the SAME loop, so we can't use a fresh
    asyncio.run() per turn."""
    if "loop" not in st.session_state:
        st.session_state.loop = asyncio.new_event_loop()
    return st.session_state.loop


def _get_runner(app: str, backend_name: str):
    """Build (or reuse) the runner + session for the selected app + backend.

    The model binds lazily now (`shared.model.get_model()` returns a proxy that reads
    LLM_BACKEND per turn), so a backend switch no longer needs a reload just to rebind
    it. We still purge + re-import the app package on a switch because backend-GATED
    *structure* is fixed at build/import time — `is_gemini()` picks google_search vs
    the offline stand-in, `supports_output_schema()` picks controlled-generation vs
    prompt-parse — so the agent must rebuild to match the new backend.
    """
    if (
        st.session_state.get("app") == app
        and st.session_state.get("backend") == backend_name
        and "runner" in st.session_state
    ):
        return st.session_state.runner, st.session_state.session_id

    os.environ["LLM_BACKEND"] = backend_name
    # Reload the WHOLE app package, not just agent.py. `importlib.reload(agent)`
    # keeps cached coordinator/modes/stores, so edits there (e.g. the router's
    # tool list) are missed -> stale 'Tool X not found. Available: ...'. Purging
    # the package makes the re-import fully fresh so the agent rebuilds with the
    # current backend-gated tools/instructions (the model itself rebinds lazily) AND
    # any code edits. (Runs only on app/backend switch or 'New conversation', not
    # every rerun.)
    pkg = APPS[app].rsplit(".", 1)[0]  # "apps.pdf_insight.agent" -> "apps.pdf_insight"
    for _name in [m for m in list(sys.modules) if m == pkg or m.startswith(pkg + ".")]:
        del sys.modules[_name]
    module = importlib.import_module(APPS[app])
    # Overlay the saved prompt/model edits (data/agent_overrides/<app>.json) onto
    # the freshly-built agents. Re-applied on every rebuild, so edits persist.
    agent_overrides.apply(module.root_agent, agent_overrides.load(app))
    runner = InMemoryRunner(agent=module.root_agent, app_name=app)
    session = _loop().run_until_complete(
        runner.session_service.create_session(app_name=app, user_id=USER_ID)
    )
    st.session_state.app = app
    st.session_state.backend = backend_name
    st.session_state.runner = runner
    st.session_state.session_id = session.id
    # A rebuild normally starts a clean chat; an agent-edit rebuild keeps it (the
    # Agents tab sets _keep_history) so prompt tweaks don't wipe the conversation.
    if st.session_state.pop("_keep_history", False):
        st.session_state.setdefault("messages", [])
        st.session_state.setdefault("debug_turns", [])
    else:
        st.session_state.messages = []
        st.session_state.debug_turns = []
    return runner, session.id


def _pretty_tool(name: str, args: dict | None) -> str:
    if name == "transfer_to_agent" and args:
        return f"Delegating to **{args.get('agent_name', '?')}**"
    return f"Using **{name}**"


_MERMAID_RE = re.compile(r"```mermaid\s*\n?(.*?)```", re.DOTALL)


def _render_answer(text: str, key_prefix: str) -> None:
    """Render assistant text in the current container, drawing any ```mermaid
    fenced blocks (what text_to_diagram emits) as real diagrams. `key_prefix` must
    be unique per message so the mermaid components get stable keys across reruns."""
    pos, n, matched = 0, 0, False
    for m in _MERMAID_RE.finditer(text):
        matched = True
        before = text[pos:m.start()].strip()
        # text_to_diagram's extractor stage leaks its raw triad JSON right before
        # the diagram; it's intermediate output, not an answer -> drop it.
        if before and not (before.startswith("{") and before.endswith("}")):
            st.markdown(before)
        diagram = m.group(1).strip()
        # Explicit height: streamlit-mermaid renders the SVG asynchronously and
        # measures the iframe BEFORE it finishes, so height="auto" collapses to a
        # blank frame. Size it from the diagram's line count instead.
        height = f"{min(900, max(280, (diagram.count(chr(10)) + 1) * 46))}px"
        st_mermaid(diagram, height=height, key=f"{key_prefix}-mmd{n}")
        pos, n = m.end(), n + 1
    if not matched:
        st.markdown(text)
    elif text[pos:].strip():
        st.markdown(text[pos:])


# Tool names whose {columns, rows} result we visualize as a table + chart.
_SQL_TOOLS = ("run_sql", "run_corpus_sql")


def _to_number(v):
    """Coerce a cell to float, or None. Handles SQLite TEXT cells ('2,765', '8%')
    and DuckDB's already-numeric scalars; non-numeric strings -> None."""
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
    """Columns whose every non-empty cell parses as a number."""
    numeric = []
    for i, col in enumerate(columns):
        vals = [r[i] for r in rows if i < len(r) and r[i] not in (None, "")]
        if vals and all(_to_number(v) is not None for v in vals):
            numeric.append(col)
    return numeric


def _sql_result_payload(tool_result) -> dict | None:
    """Pull a {columns, rows} success result out of a tool response, or None.
    ADK passes a dict tool return straight through, but wraps some in {'result':…}."""
    r = tool_result
    if isinstance(r, dict) and "columns" not in r and isinstance(r.get("result"), dict):
        r = r["result"]
    if (isinstance(r, dict) and r.get("status") == "success"
            and r.get("columns") and r.get("rows")):
        return {"columns": list(r["columns"]), "rows": [list(row) for row in r["rows"]]}
    return None


def _render_sql_results(results: list[dict]) -> None:
    """For each captured SQL result, draw the rows as a table plus an auto-chosen
    chart: a line chart when a report_date column is present (time-series), else a
    bar chart of the first category column against its numeric measures."""
    for res in results:
        columns, rows = res["columns"], res["rows"]
        st.caption(f"📊 `{res['tool_name']}` — {len(rows)} row(s)")
        st.dataframe([dict(zip(columns, r)) for r in rows],
                     width="stretch", hide_index=True)
        if len(rows) < 2:
            continue  # a single point isn't worth a chart
        numeric = _numeric_columns(columns, rows)
        date_col = next((c for c in columns
                         if c.lower() == "report_date" and c not in numeric), None)
        measures = [c for c in numeric if c != date_col]
        if not measures:
            continue
        frame = {
            c: ([_to_number(r[i]) if i < len(r) else None for r in rows] if c in numeric
                else [r[i] if i < len(r) else None for r in rows])
            for i, c in enumerate(columns)
        }
        df = pd.DataFrame(frame)
        if date_col:
            st.line_chart(df.sort_values(date_col), x=date_col, y=measures)
        else:
            category = next((c for c in columns if c not in numeric), None)
            if category:
                st.bar_chart(df, x=category, y=measures)
            else:
                st.bar_chart(df[measures])


def _meta_caption(latency: float | None, usage: dict | None) -> str:
    """The small '⏱ 2.3s · 1,240 tokens' line under a response. Backends that don't
    report usage (mock) have total=0 -> show 'tokens n/a'."""
    parts = []
    if latency is not None:
        parts.append(f"⏱ {latency:.1f}s")
    total = (usage or {}).get("total", 0)
    parts.append(f"{total:,} tokens" if total else "tokens n/a")
    return " · ".join(parts)


def _render_steps(steps: list[dict], container) -> None:
    """Draw the recorded tool/thinking steps inside a status/expander."""
    for s in steps:
        if s["kind"] == "thinking":
            container.markdown(s["text"])
        elif s["kind"] == "tool_call":
            container.markdown(f"🔧 {_pretty_tool(s['tool_name'], s['tool_args'])}")
            if s["tool_args"]:
                container.json(s["tool_args"])
        elif s["kind"] == "tool_result":
            container.markdown(f"↳ **{s['tool_name']}** returned")
            container.json(s["tool_result"])


def _ingest_uploads(files) -> None:
    """Ingest any not-yet-seen uploaded PDFs into BOTH backends (SQLite replace +
    corpus append), set the active PDF for the coordinator (PDF_PATH), and remember
    which files we've processed so reruns don't re-ingest. Idempotent either way."""
    seen = st.session_state.setdefault("pdf_uploaded", set())
    pdf_state = st.session_state.setdefault("pdf_state", {})
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    for f in files or []:
        if f.name in seen:
            continue
        path = os.path.join(UPLOAD_DIR, f.name)
        with open(path, "wb") as out:
            out.write(f.getbuffer())
        try:
            summary = ingest_pdf_everywhere(path, pdf_state)
            st.sidebar.success(
                f"📄 {f.name}: sqlite {summary['sqlite']['status']}, "
                f"corpus {summary['corpus']['status']}")
        except Exception as e:  # noqa: BLE001 - surface, keep the app alive
            st.sidebar.error(f"Could not ingest {f.name}: {e}")
        os.environ["PDF_PATH"] = path  # the coordinator's default active PDF
        seen.add(f.name)


def _pdf_overview() -> None:
    """Show what's queryable right now: the corpus coverage + table registry, and
    the active single-PDF SQLite schema. Helps the user see what to ask."""
    with st.expander("📄 What's queryable", expanded=True):
        active = os.environ.get("PDF_PATH")
        st.caption(f"Active PDF (single-doc modes): **{os.path.basename(active)}**"
                   if active else "No PDF uploaded yet — upload one in the sidebar.")
        pdf_state = st.session_state.get("pdf_state", {})

        corpus = get_corpus_store(pdf_state).list_schema()
        if corpus["status"] == "success":
            docs = corpus["documents"]
            st.markdown(f"**Corpus** — {docs['count']} report(s), "
                        f"{docs['from']} → {docs['to']}")
            st.dataframe(
                [{"table": t["table"], "title": t["title"],
                  "columns": ", ".join(t["columns"])} for t in corpus["tables"]],
                width="stretch", hide_index=True)
        else:
            st.caption(f"Corpus: {corpus['error_message']}")

        db = pdf_state.get("db_path")
        if db:
            sql = SQLiteStore(db).list_schema()
            if sql["status"] == "success":
                st.markdown("**This PDF (SQLite)** — "
                            + ", ".join(t["table"] for t in sql["schema"]))


# ------------------------------------------------------------- conversations ---
def _msgs_hash(msgs: list[dict]) -> str:
    """Content hash of the transcript, so autosave skips unchanged sessions."""
    return hashlib.md5(json.dumps(msgs, default=str, sort_keys=True).encode()).hexdigest()


def _persist(conv_id: str, app: str, backend_name: str, title: str | None,
             pdf_mode_label: str | None = None) -> str:
    """Write the current session to its folder and mark it saved."""
    ss = st.session_state
    folder = conversations.save(
        conv_id, app=app, backend=backend_name,
        messages=ss.get("messages", []), debug_turns=ss.get("debug_turns", []),
        title=title or conversations.title_from(ss.get("messages", [])),
        extra={"pdf_path": os.environ.get("PDF_PATH"), "pdf_mode": pdf_mode_label},
    )
    ss["_autosave_hash"] = _msgs_hash(ss.get("messages", []))
    return folder


def _new_conversation() -> None:
    """Reset to a blank session (drops runner so _get_runner rebuilds fresh)."""
    for k in ("runner", "session_id", "messages", "app", "backend", "debug_turns",
              "conv_id", "conv_title", "_autosave_hash"):
        st.session_state.pop(k, None)


def _load_conversation(conv_id: str) -> None:
    """Callback: load a saved conversation. Runs BEFORE the rerun so it may set the
    app/backend *widget* keys (forbidden in the main script body after the widgets
    exist). _keep_history makes the runner rebuild keep the loaded transcript."""
    ss = st.session_state
    data = conversations.load(conv_id)
    meta = data["meta"]
    if meta.get("app") in APPS:
        ss["app_select"] = meta["app"]
    if meta.get("backend") in BACKENDS:
        ss["backend_select"] = meta["backend"]
    ss["messages"] = data["messages"]
    ss["debug_turns"] = data["debug_turns"]
    ss["conv_id"] = meta.get("id", conv_id)
    ss["conv_title"] = meta.get("title", conv_id)
    ss["_autosave_hash"] = _msgs_hash(data["messages"])
    ss["_keep_history"] = True
    for k in ("runner", "session_id", "app", "backend"):
        ss.pop(k, None)


def _delete_conversation(conv_id: str) -> None:
    """Callback: delete a saved conversation; if it's the open one, go blank."""
    conversations.delete(conv_id)
    ss = st.session_state
    if ss.get("conv_id") == conv_id:
        for k in ("conv_id", "conv_title", "_autosave_hash"):
            ss.pop(k, None)
    ss.pop("conv_pick", None)  # clear the picker (its option just vanished)


# ---------------------------------------------------------------- page setup ---
st.set_page_config(page_title="ADK Chat", page_icon="🤖", layout="centered")


@st.fragment(run_every=conversations.AUTOSAVE_INTERVAL_SECONDS)
def _autosave_tick() -> None:
    """Self-rerunning every AUTOSAVE_INTERVAL_SECONDS: persist the session if it
    changed. Auto-names an unsaved conversation on first write. Renders nothing."""
    ss = st.session_state
    msgs = ss.get("messages") or []
    if not msgs or ss.get("_autosave_hash") == _msgs_hash(msgs):
        return
    conv_id = ss.get("conv_id")
    if not conv_id:
        conv_id = conversations.new_id(ss.get("app", "chat"), conversations.title_from(msgs))
        ss["conv_id"] = conv_id
        ss["conv_title"] = conversations.title_from(msgs)
    _persist(conv_id, ss.get("app", "chat"), ss.get("backend", "mock"), ss.get("conv_title"))

with st.sidebar:
    st.title("🤖 ADK Chat")
    app = st.selectbox("App", list(APPS), key="app_select")
    cur = backend()
    chosen = st.selectbox(
        "Backend", BACKENDS,
        index=BACKENDS.index(cur) if cur in BACKENDS else 0,
        key="backend_select",
    )
    if chosen == "mock":
        st.caption("_mock — offline, free, streaming simulated_")
    else:
        st.caption(f"_live: **{chosen}** — uses its API key + real tokens_")

    # Query mode sits right under Backend: it's a top-level control that governs
    # every turn (with or without an upload), not a PDF-upload afterthought.
    pdf_mode_label = None
    if app == "pdf_insight":
        pdf_mode_label = st.selectbox("Query mode", list(PDF_MODES), key="pdf_mode")

    # --- Conversations: save / new / open / delete -------------------------
    st.divider()
    st.subheader("💾 Conversations")
    cur_title = st.session_state.get("conv_title")
    st.caption(f"Open: **{cur_title}**" if cur_title else "Open: _unsaved draft_")
    cs, cn = st.columns(2)
    if cs.button("💾 Save", width="stretch", type="primary",
                 disabled=not st.session_state.get("messages")):
        cid = st.session_state.get("conv_id") or conversations.new_id(
            app, conversations.title_from(st.session_state.get("messages", [])))
        title = st.session_state.get("conv_title") or conversations.title_from(
            st.session_state.get("messages", []))
        folder = _persist(cid, app, chosen, title, pdf_mode_label)
        st.session_state["conv_id"] = cid
        st.session_state["conv_title"] = title
        st.success(f"Saved → `{folder}`")
    cn.button("🆕 New", width="stretch", on_click=_new_conversation)

    saved = conversations.list_conversations()
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

    if app == "pdf_insight":
        st.divider()
        st.subheader("📄 PDF Insight")
        uploads = st.file_uploader("Upload PDF(s)", type="pdf",
                                   accept_multiple_files=True, key="pdf_uploads")
        _ingest_uploads(uploads)
        st.caption("Each upload replaces the single-PDF SQLite and **appends** to the "
                   "growing corpus.")

runner, session_id = _get_runner(app, chosen)
st.session_state.setdefault("debug_turns", [])

# Defined in the MAIN BODY (not inside a tab) so Streamlit pins it to the bottom
# of the viewport; history then scrolls above it like a normal chat app.
prompt = st.chat_input("Message…")

tab_chat, tab_debug, tab_agents = st.tabs(["💬 Chat", "🔍 Debug", "🛠️ Agents"])

with tab_chat:
    if app == "pdf_insight":
        _pdf_overview()

    # Replay history (each turn keeps its collapsed "Thinking" block).
    for i, m in enumerate(st.session_state.messages):
        with st.chat_message(m["role"]):
            if m["role"] == "assistant":
                if m.get("steps"):
                    with st.expander("💭 Thinking", expanded=False):
                        _render_steps(m["steps"], st)
                _render_answer(m["content"], key_prefix=f"hist{i}")
                if m.get("sql_results"):
                    _render_sql_results(m["sql_results"])
                if m.get("latency") is not None or m.get("usage"):
                    st.caption(_meta_caption(m.get("latency"), m.get("usage")))
            else:
                st.markdown(m["content"])

    # ------------------------------------------------------------- new turn ---
    if prompt:
        # For pdf_insight, fold the sidebar's mode choice into the message as a
        # `mode:` directive (the coordinator parses it); display stays clean. Always
        # explicit — including `mode: auto` — so the dropdown is the authoritative
        # selection every turn, not a default the precedence chain falls through to.
        send_text = prompt
        if app == "pdf_insight" and pdf_mode_label:
            mode = PDF_MODES[pdf_mode_label]
            send_text = f"mode: {mode} {prompt}"

        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            status = st.status("Thinking…", expanded=False)
            answer_box = st.empty()
            answer, steps, usage = "", [], None
            sql_results = []          # {columns, rows} from run_sql/run_corpus_sql
            debug_snaps = []          # raw-event snapshots for the Debug tab
            t_start = time.perf_counter()

            events = stream_ui_events(
                runner,
                user_id=USER_ID,
                session_id=session_id,
                message=send_text,
                simulate_stream=(chosen == "mock"),
                debug_sink=debug_snaps,
                snapshot_fn=snapshot_event,
            )
            # Consume the WHOLE stream inside ONE run_until_complete. Stepping the
            # async generator with a separate run_until_complete per item (the old
            # _drain) makes ADK's OpenTelemetry spans attach in one context and
            # detach in another -> "Token was created in a different Context". We
            # still render live: this runs on the main thread, so widget updates
            # between `await`s show up as they happen.
            acc = {"answer": "", "usage": None}

            async def _consume(events=events, acc=acc):
                async for ev in events:
                    if ev.kind == "thinking_delta":
                        steps.append({"kind": "thinking", "text": ev.text})
                        status.markdown(ev.text)
                    elif ev.kind == "tool_call":
                        status.update(label=f"{_pretty_tool(ev.tool_name, ev.tool_args)}…")
                        steps.append({"kind": "tool_call",
                                      "tool_name": ev.tool_name, "tool_args": ev.tool_args})
                        status.markdown(f"🔧 {_pretty_tool(ev.tool_name, ev.tool_args)}")
                        if ev.tool_args:
                            status.json(ev.tool_args)
                    elif ev.kind == "tool_result":
                        steps.append({"kind": "tool_result",
                                      "tool_name": ev.tool_name, "tool_result": ev.tool_result})
                        if ev.tool_name in _SQL_TOOLS:
                            payload = _sql_result_payload(ev.tool_result)
                            if payload:
                                sql_results.append({"tool_name": ev.tool_name, **payload})
                        status.markdown(f"↳ **{ev.tool_name}** returned")
                        status.json(ev.tool_result)
                    elif ev.kind == "text_delta":
                        status.update(label="Responding…")
                        acc["answer"] += ev.text
                        answer_box.markdown(acc["answer"] + " ▌")
                    elif ev.kind == "error":
                        status.update(label="Error", state="error")
                        st.error(ev.text)
                    elif ev.kind == "final":
                        acc["usage"] = ev.usage

            _loop().run_until_complete(_consume())
            answer, usage = acc["answer"], acc["usage"]

            latency = time.perf_counter() - t_start
            status.update(
                label="Done" if answer else "Done (no text reply)",
                state="complete", expanded=False,
            )
            answer_box.empty()  # drop the streaming placeholder...
            _render_answer(answer, key_prefix=f"turn{len(st.session_state.messages)}")
            if sql_results:
                _render_sql_results(sql_results)
            st.caption(_meta_caption(latency, usage))

        st.session_state.messages.append(
            {"role": "assistant", "content": answer, "steps": steps,
             "sql_results": sql_results, "latency": latency, "usage": usage}
        )
        st.session_state.debug_turns.append({
            "prompt": prompt,
            "snapshots": debug_snaps,
            "latency": latency,
            "session": _loop().run_until_complete(
                fetch_session_info(runner, app, USER_ID, session_id)
            ),
        })

        # Per-turn save insurance: if this conversation is already named, flush it
        # to disk now (autosave handles the unnamed case on its 5-min timer).
        if st.session_state.get("conv_id"):
            _persist(st.session_state["conv_id"], app, chosen,
                     st.session_state.get("conv_title"), pdf_mode_label)

with tab_debug:
    render_debug_tab(st.session_state.debug_turns, root_agent=getattr(runner, "agent", None))

with tab_agents:
    agent_overrides.render_agents_tab(getattr(runner, "agent", None), app, chosen)

# Periodic autosave (every conversations.AUTOSAVE_INTERVAL_SECONDS). Self-reruns on
# its own timer without a full-page rerun; renders nothing.
_autosave_tick()
