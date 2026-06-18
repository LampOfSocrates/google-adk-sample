#!/usr/bin/env python3
"""
nabu — command-line client for the Nabu Agent Control Plane.

Install:
  pip install typer httpx

Config:
  NABU_BASE_URL    base URL of the control plane (default http://localhost:8000)
  NABU_USER        default user id for commands that need one
  NABU_TOKEN       optional bearer token

Discover commands:
  ./nabu.py --help
  ./nabu.py sessions --help
  ./nabu.py debug --help
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import httpx
import typer

BASE_URL = os.getenv("NABU_BASE_URL", "http://localhost:8000")
DEFAULT_USER = os.getenv("NABU_USER", "cli-user")
TOKEN = os.getenv("NABU_TOKEN")

app = typer.Typer(help="Nabu Agent Control Plane CLI", no_args_is_help=True)
apps_app      = typer.Typer(help="Manage NabuApps")
variants_app  = typer.Typer(help="Manage NabuAppVariants")
sessions_app  = typer.Typer(help="NabuAppSessions: chat, browse, fork, replay, tags")
turns_app     = typer.Typer(help="Per-turn replay surgery")
debug_app     = typer.Typer(help="Live session debugging (debug_mode only)")
bulk_app      = typer.Typer(help="Bulk evaluation jobs")
search_app    = typer.Typer(help="Cross-session search and error clustering")
eval_app      = typer.Typer(help="EvaluationCases and EvaluationSuites")
rate_app      = typer.Typer(help="Rubrics and Ratings")
comment_app   = typer.Typer(help="Comments and bookmarks")
share_app     = typer.Typer(help="Sharing")
tools_app     = typer.Typer(help="NabuToolSpec registry")

for name, sub in [
    ("apps", apps_app), ("variants", variants_app), ("sessions", sessions_app),
    ("turns", turns_app), ("debug", debug_app), ("bulk", bulk_app),
    ("search", search_app), ("eval", eval_app), ("rate", rate_app),
    ("comment", comment_app), ("share", share_app), ("tools", tools_app),
]:
    app.add_typer(sub, name=name)


# ----- helpers --------------------------------------------------------------

def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _client(timeout: float | None = 30.0) -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, headers=_headers(), timeout=timeout)


def _emit(data) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


def _load_json_arg(value: str) -> dict:
    """Inline JSON, or @path/to/file.json."""
    if value.startswith("@"):
        return json.loads(Path(value[1:]).read_text())
    return json.loads(value)


def _request(method: str, path: str, **kw) -> dict:
    with _client() as c:
        r = c.request(method, path, **kw)
        if r.status_code >= 400:
            typer.echo(f"HTTP {r.status_code}: {r.text}", err=True)
            raise typer.Exit(1)
        return r.json() if r.content else {}


def _stream_sse(path: str, json_body: dict | None = None):
    method = "POST" if json_body is not None else "GET"
    with httpx.Client(base_url=BASE_URL, headers=_headers(), timeout=None) as c:
        with c.stream(method, path, json=json_body) as r:
            if r.status_code >= 400:
                typer.echo(f"HTTP {r.status_code}: {r.read().decode()}", err=True)
                raise typer.Exit(1)
            for line in r.iter_lines():
                if line.startswith("data: "):
                    yield json.loads(line[6:])


# ===== 7.1 NabuApps =========================================================

@apps_app.command("create")
def apps_create(name: str = typer.Option(...), owner: str = typer.Option(...)):
    _emit(_request("POST", "/nabu-apps", json={"name": name, "owner": owner}))

@apps_app.command("list")
def apps_list():
    _emit(_request("GET", "/nabu-apps"))

@apps_app.command("get")
def apps_get(nabu_app_id: str):
    _emit(_request("GET", f"/nabu-apps/{nabu_app_id}"))

@apps_app.command("rename")
def apps_rename(nabu_app_id: str, name: str = typer.Option(...)):
    _emit(_request("PATCH", f"/nabu-apps/{nabu_app_id}", json={"name": name}))

@apps_app.command("delete")
def apps_delete(nabu_app_id: str):
    _emit(_request("DELETE", f"/nabu-apps/{nabu_app_id}"))

@apps_app.command("auto-eval-policy")
def apps_auto_eval_policy(
    nabu_app_id: str,
    suite_ids: str = typer.Option(..., help="comma-separated suite ids"),
    trigger: str = typer.Option("on_variant_create"),
):
    _emit(_request("POST", f"/nabu-apps/{nabu_app_id}/auto-eval-policy",
                   json={"suite_ids": suite_ids.split(","), "trigger": trigger}))


# ===== 7.2 NabuAppVariants ==================================================

@variants_app.command("create")
def variants_create(
    nabu_app_id: str,
    entrypoint: str = typer.Option(..., help="module.path:factory_fn"),
    config: str = typer.Option(..., help="inline JSON or @file.json"),
    code_ref: str = typer.Option(...),
    display_name: Optional[str] = typer.Option(None),
    notes: str = typer.Option(""),
):
    body = {"entrypoint": entrypoint, "config": _load_json_arg(config),
            "code_ref": code_ref, "notes": notes}
    if display_name:
        body["display_name"] = display_name
    _emit(_request("POST", f"/nabu-apps/{nabu_app_id}/variants", json=body))

@variants_app.command("list")
def variants_list(nabu_app_id: str):
    _emit(_request("GET", f"/nabu-apps/{nabu_app_id}/variants"))

@variants_app.command("get")
def variants_get(variant_id: str):
    _emit(_request("GET", f"/nabu-app-variants/{variant_id}"))

@variants_app.command("clone")
def variants_clone(
    variant_id: str,
    config_patch: str = typer.Option(..., help="inline JSON or @file.json"),
    display_name: Optional[str] = typer.Option(None),
):
    body = {"config_patch": _load_json_arg(config_patch)}
    if display_name:
        body["display_name"] = display_name
    _emit(_request("POST", f"/nabu-app-variants/{variant_id}/clone", json=body))

@variants_app.command("rename")
def variants_rename(variant_id: str, display_name: str = typer.Option(...)):
    _emit(_request("PATCH", f"/nabu-app-variants/{variant_id}",
                   json={"display_name": display_name}))

@variants_app.command("archive")
def variants_archive(variant_id: str):
    _emit(_request("POST", f"/nabu-app-variants/{variant_id}/archive"))


# ===== 7.3 / 7.4 / 7.5 / 7.14 Sessions =======================================

@sessions_app.command("create")
def sessions_create(
    nabu_app_id: str = typer.Option(..., "--app"),
    variant_id: str = typer.Option(..., "--variant"),
    user_id: str = typer.Option(DEFAULT_USER, "--user"),
):
    _emit(_request("POST", "/nabu-app-sessions", json={
        "nabu_app_id": nabu_app_id, "variant_id": variant_id, "user_id": user_id,
    }))

@sessions_app.command("chat")
def sessions_chat(session_id: str, text: str):
    """Send a user message and stream the agent's response."""
    for ev in _stream_sse(f"/nabu-app-sessions/{session_id}/turn",
                          json_body={"user_text": text}):
        kind = ev.get("type")
        if kind == "delta":
            typer.echo(ev["text"], nl=False); sys.stdout.flush()
        elif kind == "tool_call":
            typer.echo(f"\n[tool {ev['name']}({json.dumps(ev['args'])})]", err=True)
        elif kind == "assistant_message":
            typer.echo("")
        elif kind == "user_message":
            typer.echo(f"> {ev['content']}", err=True)

@sessions_app.command("list")
def sessions_list(
    nabu_app_id: Optional[str] = typer.Option(None, "--app"),
    variant_id: Optional[str] = typer.Option(None, "--variant"),
    user_id: Optional[str] = typer.Option(None, "--user"),
    status: Optional[str] = typer.Option(None),
    parent: Optional[str] = typer.Option(None, help="parent_session_id"),
    tag: Optional[str] = typer.Option(None),
):
    params = {k: v for k, v in {
        "nabu_app_id": nabu_app_id, "variant_id": variant_id, "user_id": user_id,
        "status": status, "parent_session_id": parent, "tag": tag,
    }.items() if v is not None}
    _emit(_request("GET", "/nabu-app-sessions", params=params))

@sessions_app.command("get")
def sessions_get(session_id: str):
    _emit(_request("GET", f"/nabu-app-sessions/{session_id}"))

@sessions_app.command("messages")
def sessions_messages(session_id: str):
    _emit(_request("GET", f"/nabu-app-sessions/{session_id}/messages"))

@sessions_app.command("trace-events")
def sessions_trace_events(
    session_id: str,
    turn: Optional[int] = typer.Option(None, help="restrict to one turn"),
):
    path = f"/nabu-app-sessions/{session_id}/trace-events"
    if turn is not None:
        path = f"/nabu-app-sessions/{session_id}/turns/{turn}/trace-events"
    _emit(_request("GET", path))

@sessions_app.command("trace")
def sessions_trace(session_id: str, turn: int = typer.Option(...)):
    """OpenTelemetry-style trace for one turn (LLM + tool spans)."""
    _emit(_request("GET", f"/nabu-app-sessions/{session_id}/turns/{turn}/trace"))

@sessions_app.command("lineage")
def sessions_lineage(session_id: str):
    _emit(_request("GET", f"/nabu-app-sessions/{session_id}/lineage"))

@sessions_app.command("delete")
def sessions_delete(session_id: str):
    _emit(_request("DELETE", f"/nabu-app-sessions/{session_id}"))

@sessions_app.command("replay")
def sessions_replay(
    session_id: str,
    variant_id: str = typer.Option(..., "--variant"),
    user_id: str = typer.Option(DEFAULT_USER, "--user"),
):
    _emit(_request("POST", f"/nabu-app-sessions/{session_id}/replay",
                   json={"target_variant_id": variant_id, "user_id": user_id}))

@sessions_app.command("fork")
def sessions_fork(
    session_id: str,
    variant_id: str = typer.Option(..., "--variant"),
    user_id: str = typer.Option(DEFAULT_USER, "--user"),
    no_copy_state: bool = typer.Option(False, "--no-copy-state"),
):
    _emit(_request("POST", f"/nabu-app-sessions/{session_id}/fork", json={
        "target_variant_id": variant_id, "user_id": user_id,
        "copy_state": not no_copy_state,
    }))

@sessions_app.command("compare")
def sessions_compare(ids: list[str]):
    """Side-by-side aligned comparison of 2+ sessions."""
    _emit(_request("GET", "/nabu-app-sessions/compare", params={"ids": ",".join(ids)}))

@sessions_app.command("tag")
def sessions_tag(session_id: str, tag: str):
    _emit(_request("POST", f"/nabu-app-sessions/{session_id}/tags", json={"tag": tag}))

@sessions_app.command("untag")
def sessions_untag(session_id: str, tag: str):
    _emit(_request("DELETE", f"/nabu-app-sessions/{session_id}/tags/{tag}"))


# ===== 7.12 Turn-level replay surgery =======================================

@turns_app.command("replay")
def turns_replay(session_id: str, turn: int = typer.Option(..., "--turn")):
    _emit(_request("POST", f"/nabu-app-sessions/{session_id}/turns/{turn}/replay"))

@turns_app.command("replay-edit")
def turns_replay_edit(
    session_id: str,
    turn: int = typer.Option(..., "--turn"),
    user_text: Optional[str] = typer.Option(None),
    tool_overrides: Optional[str] = typer.Option(None, help="inline JSON or @file.json"),
):
    body = {}
    if user_text is not None:
        body["user_text"] = user_text
    if tool_overrides is not None:
        body["tool_overrides"] = _load_json_arg(tool_overrides)
    _emit(_request("POST",
                   f"/nabu-app-sessions/{session_id}/turns/{turn}/replay-with-edit",
                   json=body))

@turns_app.command("replay-overrides")
def turns_replay_overrides(
    session_id: str,
    config_patch: str = typer.Option(..., help="inline JSON or @file.json"),
):
    """Replay a session under transient variant overrides without minting a new variant."""
    _emit(_request("POST",
                   f"/nabu-app-sessions/{session_id}/replay-with-overrides",
                   json={"config_patch": _load_json_arg(config_patch)}))


# ===== 7.11 Live debug ======================================================

@debug_app.command("pause")
def debug_pause(session_id: str):
    _emit(_request("POST", f"/nabu-app-sessions/{session_id}/pause"))

@debug_app.command("resume")
def debug_resume(session_id: str):
    _emit(_request("POST", f"/nabu-app-sessions/{session_id}/resume"))

@debug_app.command("abort")
def debug_abort(session_id: str):
    _emit(_request("POST", f"/nabu-app-sessions/{session_id}/abort"))

@debug_app.command("state-get")
def debug_state_get(session_id: str):
    _emit(_request("GET", f"/nabu-app-sessions/{session_id}/state"))

@debug_app.command("state-patch")
def debug_state_patch(
    session_id: str,
    patch: str = typer.Option(..., help="inline JSON or @file.json"),
):
    _emit(_request("PATCH", f"/nabu-app-sessions/{session_id}/state",
                   json=_load_json_arg(patch)))

@debug_app.command("step")
def debug_step(session_id: str):
    _emit(_request("POST", f"/nabu-app-sessions/{session_id}/step"))

@debug_app.command("inject-tool")
def debug_inject_tool(
    session_id: str,
    call_id: str = typer.Option(...),
    result: str = typer.Option(..., help="inline JSON or @file.json"),
):
    _emit(_request("POST", f"/nabu-app-sessions/{session_id}/inject-tool-result",
                   json={"tool_call_id": call_id, "result": _load_json_arg(result)}))

@debug_app.command("inject-llm")
def debug_inject_llm(session_id: str, content: str = typer.Option(...)):
    _emit(_request("POST", f"/nabu-app-sessions/{session_id}/inject-llm-response",
                   json={"content": content}))

@debug_app.command("stream")
def debug_stream(session_id: str):
    """Tail live events from a running session."""
    for ev in _stream_sse(f"/nabu-app-sessions/{session_id}/stream"):
        _emit(ev)


# ===== 7.6 / 7.10 Bulk jobs =================================================

@bulk_app.command("submit")
def bulk_submit(
    variant_id: str = typer.Option(..., "--variant"),
    sessions: str = typer.Option(..., help="comma-separated session ids"),
    user_id: str = typer.Option(DEFAULT_USER, "--user"),
):
    _emit(_request("POST", "/bulk-jobs", json={
        "target_variant_id": variant_id,
        "source_session_ids": sessions.split(","),
        "user_id": user_id,
    }))

@bulk_app.command("list")
def bulk_list(
    status: Optional[str] = typer.Option(None),
    variant_id: Optional[str] = typer.Option(None, "--variant"),
):
    params = {k: v for k, v in {"status": status, "target_variant_id": variant_id}.items() if v}
    _emit(_request("GET", "/bulk-jobs", params=params))

@bulk_app.command("get")
def bulk_get(job_id: str):
    _emit(_request("GET", f"/bulk-jobs/{job_id}"))

@bulk_app.command("sessions")
def bulk_sessions(job_id: str):
    _emit(_request("GET", f"/bulk-jobs/{job_id}/sessions"))

@bulk_app.command("watch")
def bulk_watch(job_id: str):
    """Tail bulk job progress."""
    for ev in _stream_sse(f"/bulk-jobs/{job_id}/stream"):
        _emit(ev)

@bulk_app.command("cancel")
def bulk_cancel(job_id: str):
    _emit(_request("DELETE", f"/bulk-jobs/{job_id}"))

@bulk_app.command("metrics")
def bulk_metrics(job_id: str):
    _emit(_request("GET", f"/bulk-jobs/{job_id}/metrics"))

@bulk_app.command("compare-to-parents")
def bulk_compare_to_parents(job_id: str):
    _emit(_request("GET", f"/bulk-jobs/{job_id}/compare-to-parents"))

@bulk_app.command("compare")
def bulk_compare(ids: list[str]):
    """Head-to-head metric comparison of 2+ bulk jobs (same source set)."""
    _emit(_request("GET", "/bulk-jobs/compare", params={"ids": ",".join(ids)}))


# ===== 7.7 Ratings & rubrics ================================================

@rate_app.command("rubric-create")
def rubric_create(
    name: str = typer.Option(...),
    scale: str = typer.Option(..., help="e.g. '1-5' or 'pass|fail'"),
    description: str = typer.Option(""),
):
    _emit(_request("POST", "/rubrics", json={
        "name": name, "scale": scale, "description": description,
    }))

@rate_app.command("rubrics")
def rubrics_list():
    _emit(_request("GET", "/rubrics"))

@rate_app.command("rubric-get")
def rubric_get(rubric_id: str):
    _emit(_request("GET", f"/rubrics/{rubric_id}"))

@rate_app.command("score")
def rate_score(
    target_type: str = typer.Option(..., help="session | turn | message"),
    target_id: str = typer.Option(...),
    rubric_id: str = typer.Option(...),
    score: str = typer.Option(...),
    notes: str = typer.Option(""),
):
    _emit(_request("POST", "/ratings", json={
        "target_type": target_type, "target_id": target_id,
        "rubric_id": rubric_id, "score": score, "notes": notes,
    }))

@rate_app.command("list")
def rate_list(
    target_type: Optional[str] = typer.Option(None),
    target_id: Optional[str] = typer.Option(None),
    rubric_id: Optional[str] = typer.Option(None),
):
    params = {k: v for k, v in {
        "target_type": target_type, "target_id": target_id, "rubric_id": rubric_id,
    }.items() if v is not None}
    _emit(_request("GET", "/ratings", params=params))

@rate_app.command("update")
def rate_update(rating_id: str, score: Optional[str] = None, notes: Optional[str] = None):
    body = {k: v for k, v in {"score": score, "notes": notes}.items() if v is not None}
    _emit(_request("PATCH", f"/ratings/{rating_id}", json=body))

@rate_app.command("delete")
def rate_delete(rating_id: str):
    _emit(_request("DELETE", f"/ratings/{rating_id}"))


# ===== 7.8 Comments & bookmarks =============================================

@comment_app.command("add")
def comment_add(
    target_type: str = typer.Option(...),
    target_id: str = typer.Option(...),
    body: str = typer.Option(...),
):
    _emit(_request("POST", "/comments", json={
        "target_type": target_type, "target_id": target_id, "body": body,
    }))

@comment_app.command("list")
def comment_list(
    target_type: str = typer.Option(...),
    target_id: str = typer.Option(...),
):
    _emit(_request("GET", "/comments",
                   params={"target_type": target_type, "target_id": target_id}))

@comment_app.command("update")
def comment_update(comment_id: str, body: str = typer.Option(...)):
    _emit(_request("PATCH", f"/comments/{comment_id}", json={"body": body}))

@comment_app.command("delete")
def comment_delete(comment_id: str):
    _emit(_request("DELETE", f"/comments/{comment_id}"))

@comment_app.command("bookmark-add")
def bookmark_add(
    session_id: str = typer.Option(...),
    turn: int = typer.Option(...),
    label: str = typer.Option(""),
):
    _emit(_request("POST", "/bookmarks", json={
        "session_id": session_id, "turn_index": turn, "label": label,
    }))

@comment_app.command("bookmarks")
def bookmarks_list():
    _emit(_request("GET", "/bookmarks"))

@comment_app.command("bookmark-delete")
def bookmark_delete(bookmark_id: str):
    _emit(_request("DELETE", f"/bookmarks/{bookmark_id}"))


# ===== 7.9 Sharing ==========================================================

@share_app.command("grant")
def share_grant(
    session_id: str,
    user_id: str = typer.Option(..., "--user"),
    role: str = typer.Option("viewer", help="viewer | editor"),
):
    _emit(_request("POST", f"/nabu-app-sessions/{session_id}/shares",
                   json={"user_id": user_id, "role": role}))

@share_app.command("list")
def share_list(session_id: str):
    _emit(_request("GET", f"/nabu-app-sessions/{session_id}/shares"))

@share_app.command("revoke")
def share_revoke(share_id: str):
    _emit(_request("DELETE", f"/shares/{share_id}"))

@share_app.command("link")
def share_link(session_id: str):
    _emit(_request("POST", f"/nabu-app-sessions/{session_id}/share-links"))

@share_app.command("link-revoke")
def share_link_revoke(token: str):
    _emit(_request("DELETE", f"/share-links/{token}"))


# ===== 7.13 Search ==========================================================

@search_app.command("messages")
def search_messages(
    q: str,
    nabu_app_id: Optional[str] = typer.Option(None, "--app"),
    variant_id: Optional[str] = typer.Option(None, "--variant"),
):
    params = {"q": q}
    if nabu_app_id: params["nabu_app_id"] = nabu_app_id
    if variant_id: params["variant_id"] = variant_id
    _emit(_request("GET", "/search/messages", params=params))

@search_app.command("trace-events")
def search_trace_events(
    tool: Optional[str] = typer.Option(None),
    author: Optional[str] = typer.Option(None),
    min_latency_ms: Optional[int] = typer.Option(None),
    error_type: Optional[str] = typer.Option(None),
):
    params = {k: v for k, v in {
        "tool": tool, "author": author,
        "min_latency_ms": min_latency_ms, "error_type": error_type,
    }.items() if v is not None}
    _emit(_request("GET", "/search/trace-events", params=params))

@search_app.command("errors")
def search_errors():
    _emit(_request("GET", "/errors"))

@search_app.command("error-sessions")
def search_error_sessions(fingerprint: str):
    _emit(_request("GET", f"/errors/{fingerprint}/sessions"))


# ===== 7.15 NabuToolSpec registry ===========================================

@tools_app.command("register")
def tools_register(
    name: str = typer.Option(...),
    owner: str = typer.Option(...),
    entrypoint: str = typer.Option(...),
    schema: str = typer.Option(..., help="inline JSON or @file.json"),
    allowed_variants: Optional[str] = typer.Option(None, help="comma-separated"),
):
    body = {
        "name": name, "owner": owner, "entrypoint": entrypoint,
        "json_schema": _load_json_arg(schema),
    }
    if allowed_variants:
        body["allowed_variant_ids"] = allowed_variants.split(",")
    _emit(_request("POST", "/nabu-tool-specs", json=body))

@tools_app.command("list")
def tools_list():
    _emit(_request("GET", "/nabu-tool-specs"))

@tools_app.command("get")
def tools_get(name: str):
    _emit(_request("GET", f"/nabu-tool-specs/{name}"))

@tools_app.command("update")
def tools_update(name: str, allowed_variants: str = typer.Option(..., help="comma-separated")):
    _emit(_request("PATCH", f"/nabu-tool-specs/{name}",
                   json={"allowed_variant_ids": allowed_variants.split(",")}))

@tools_app.command("delete")
def tools_delete(name: str):
    _emit(_request("DELETE", f"/nabu-tool-specs/{name}"))

@tools_app.command("invoke")
def tools_invoke(name: str, args: str = typer.Option(..., help="inline JSON or @file.json")):
    _emit(_request("POST", f"/nabu-tool-specs/{name}/invoke",
                   json={"args": _load_json_arg(args)}))


# ===== 7.16 Evaluation suites ===============================================

@eval_app.command("case-create")
def eval_case_create(
    session_id: str = typer.Option(..., "--from-session"),
    turn: Optional[int] = typer.Option(None, "--turn"),
    expected: str = typer.Option(..., help="inline JSON or @file.json"),
    name: Optional[str] = typer.Option(None),
):
    body = {"source_session_id": session_id, "expected": _load_json_arg(expected)}
    if turn is not None: body["turn_index"] = turn
    if name: body["name"] = name
    _emit(_request("POST", "/evaluation-cases", json=body))

@eval_app.command("cases")
def eval_cases():
    _emit(_request("GET", "/evaluation-cases"))

@eval_app.command("case-delete")
def eval_case_delete(case_id: str):
    _emit(_request("DELETE", f"/evaluation-cases/{case_id}"))

@eval_app.command("suite-create")
def eval_suite_create(name: str = typer.Option(...)):
    _emit(_request("POST", "/evaluation-suites", json={"name": name}))

@eval_app.command("suites")
def eval_suites():
    _emit(_request("GET", "/evaluation-suites"))

@eval_app.command("suite-get")
def eval_suite_get(suite_id: str):
    _emit(_request("GET", f"/evaluation-suites/{suite_id}"))

@eval_app.command("suite-add")
def eval_suite_add(suite_id: str, case_id: str):
    _emit(_request("POST", f"/evaluation-suites/{suite_id}/cases",
                   json={"case_id": case_id}))

@eval_app.command("suite-remove")
def eval_suite_remove(suite_id: str, case_id: str):
    _emit(_request("DELETE", f"/evaluation-suites/{suite_id}/cases/{case_id}"))

@eval_app.command("run")
def eval_run(suite_id: str, variant_id: str = typer.Option(..., "--variant")):
    _emit(_request("POST", f"/evaluation-suites/{suite_id}/runs",
                   params={"variant_id": variant_id}))

@eval_app.command("run-show")
def eval_run_show(run_id: str):
    _emit(_request("GET", f"/evaluation-suite-runs/{run_id}"))


# ===== entrypoint ===========================================================

if __name__ == "__main__":
    app()
