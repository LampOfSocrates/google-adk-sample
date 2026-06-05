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

# name -> module exposing `root_agent`. Each is a separate ADK app/session.
APPS = {
    "travel_planner": "apps.travel_planner.agent",
    "graph_builder": "apps.graph_builder.agent",
    "text_to_diagram": "apps.text_to_diagram.agent",
}
USER_ID = "you"
BACKENDS = ["mock", "gemini", "openai", "deepseek", "bedrock"]


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
    module = importlib.reload(importlib.import_module(APPS[app]))
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


def _drain(agen):
    """Pump an async generator on the persistent loop, yielding items synchronously
    so Streamlit can render each as it arrives."""
    loop = _loop()
    while True:
        try:
            yield loop.run_until_complete(agen.__anext__())
        except StopAsyncIteration:
            return


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
    if st.button("🗑️ New conversation", use_container_width=True):
        for k in ("runner", "session_id", "messages", "app", "backend", "debug_turns"):
            st.session_state.pop(k, None)

runner, session_id = _get_runner(app, chosen)
st.session_state.setdefault("debug_turns", [])

# Defined in the MAIN BODY (not inside a tab) so Streamlit pins it to the bottom
# of the viewport; history then scrolls above it like a normal chat app.
prompt = st.chat_input("Message…")

tab_chat, tab_debug = st.tabs(["💬 Chat", "🔍 Debug"])

with tab_chat:
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
                message=prompt,
                simulate_stream=(chosen == "mock"),
                debug_sink=debug_snaps,
            )
            for ev in _drain(events):
                if ev.kind == "thinking_delta":
                    steps.append({"kind": "thinking", "text": ev.text})
                    status.markdown(ev.text)
                elif ev.kind == "tool_call":
                    status.update(label=f"{_pretty_tool(ev.tool_name, ev.tool_args)}…")
                    steps.append({
                        "kind": "tool_call",
                        "tool_name": ev.tool_name, "tool_args": ev.tool_args,
                    })
                    status.markdown(f"🔧 {_pretty_tool(ev.tool_name, ev.tool_args)}")
                    if ev.tool_args:
                        status.json(ev.tool_args)
                elif ev.kind == "tool_result":
                    steps.append({
                        "kind": "tool_result",
                        "tool_name": ev.tool_name, "tool_result": ev.tool_result,
                    })
                    status.markdown(f"↳ **{ev.tool_name}** returned")
                    status.json(ev.tool_result)
                elif ev.kind == "text_delta":
                    status.update(label="Responding…")
                    answer += ev.text
                    answer_box.markdown(answer + " ▌")
                elif ev.kind == "error":
                    status.update(label="Error", state="error")
                    st.error(ev.text)
                elif ev.kind == "final":
                    usage = ev.usage

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
