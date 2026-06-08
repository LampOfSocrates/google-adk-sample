"""Claude-style chat UI over the ADK agents in this repo.

    streamlit run streamlit_app.py

Pick an app in the sidebar, then chat. Tool calls and reasoning show up in a
collapsible "Thinking" block per turn (like Claude); the answer streams in token
by token. Uses LLM_BACKEND from .env (mock by default — free, offline; live
backends stream for real).

All ADK contact is funneled through `shared.ui_stream.stream_ui_events`, which
turns raw events into flat UIEvents. This file only knows how to draw UIEvents.
"""
import asyncio
import importlib
import os
import re
import sys
import time

import certifi

# Stray SSL_CERT_FILE on this box breaks the HTTPS clients; force certifi.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ.pop("SSL_CERT_DIR", None)

from dotenv import load_dotenv

load_dotenv()

import streamlit as st  # noqa: E402
from streamlit_mermaid import st_mermaid  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402

from shared.debug import fetch_session_info, render_debug_tab  # noqa: E402
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
    "raw pdf bytes (gemini only)": pdf_config.PDF_BYTES,
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

    Agents bind their model at import time (`root_agent = Agent(model=get_model())`),
    so switching backend means setting LLM_BACKEND *and* reloading the module so the
    agent (and any backend-gated tools, e.g. google_search) rebuild for the new one.
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
    # the package makes the re-import fully fresh so the agent rebinds the new
    # model AND the current tools/instructions. (Runs only on app/backend switch
    # or after 'New conversation', not every rerun.)
    pkg = APPS[app].rsplit(".", 1)[0]  # "apps.pdf_insight.agent" -> "apps.pdf_insight"
    for _name in [m for m in list(sys.modules) if m == pkg or m.startswith(pkg + ".")]:
        del sys.modules[_name]
    module = importlib.import_module(APPS[app])
    runner = InMemoryRunner(agent=module.root_agent, app_name=app)
    session = _loop().run_until_complete(
        runner.session_service.create_session(app_name=app, user_id=USER_ID)
    )
    st.session_state.app = app
    st.session_state.backend = backend_name
    st.session_state.runner = runner
    st.session_state.session_id = session.id
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


# ---------------------------------------------------------------- page setup ---
st.set_page_config(page_title="ADK Chat", page_icon="🤖", layout="centered")

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

    if st.button("🗑️ New conversation", width="stretch"):
        for k in ("runner", "session_id", "messages", "app", "backend", "debug_turns"):
            st.session_state.pop(k, None)

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

tab_chat, tab_debug = st.tabs(["💬 Chat", "🔍 Debug"])

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
            debug_snaps = []          # raw-event snapshots for the Debug tab
            t_start = time.perf_counter()

            events = stream_ui_events(
                runner,
                user_id=USER_ID,
                session_id=session_id,
                message=send_text,
                simulate_stream=(chosen == "mock"),
                debug_sink=debug_snaps,
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
            st.caption(_meta_caption(latency, usage))

        st.session_state.messages.append(
            {"role": "assistant", "content": answer, "steps": steps,
             "latency": latency, "usage": usage}
        )
        st.session_state.debug_turns.append({
            "prompt": prompt,
            "snapshots": debug_snaps,
            "latency": latency,
            "session": _loop().run_until_complete(
                fetch_session_info(runner, app, USER_ID, session_id)
            ),
        })

with tab_debug:
    render_debug_tab(st.session_state.debug_turns, root_agent=getattr(runner, "agent", None))
