"""Chat tab — PDF overview, transcript replay, and the live streaming turn.

Draws the flat frames (text/thinking/tool/final) that arrive over SSE from the
server. Appends each finished turn to `st.session_state.messages`/`debug_turns`;
the caller handles persistence. No ADK, no disk.
"""
from __future__ import annotations

import os
import re
import time

import pandas as pd
import streamlit as st
from streamlit_mermaid import st_mermaid

from apps.pages import api_client

_SQL_TOOLS = ("run_sql", "run_corpus_sql")
_MERMAID_RE = re.compile(r"```mermaid\s*\n?(.*?)```", re.DOTALL)


def _pretty_tool(name: str, args: dict | None) -> str:
    if name == "transfer_to_agent" and args:
        return f"Delegating to **{args.get('agent_name', '?')}**"
    return f"Using **{name}**"


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


def _pdf_overview() -> None:
    with st.expander("📄 What's queryable", expanded=False):
        try:
            schema = api_client.pdf_schema()
        except Exception as e:  # noqa: BLE001
            st.caption(f"schema unavailable: {e}")
            return
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


def render_chat_tab(app: str, session_id: str, pdf_mode: str | None, prompt: str | None) -> None:
    """Draw the chat: PDF overview (pdf_insight only), transcript, then the new turn
    if `prompt` was submitted. The finished turn lands in session_state; the caller
    persists it."""
    if app == "pdf_insight":
        _pdf_overview()

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

    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status = st.status("Thinking…", expanded=False)
        answer_box = st.empty()
        answer, steps, usage = "", [], None
        sql_results, debug_snaps, session_info = [], [], {}
        t_start = time.perf_counter()

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
