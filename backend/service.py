"""RunnerManager — owns the ADK runners, sessions, and the turn stream.

This is the server's stateful core: it builds (and caches) one `InMemoryRunner`
per `(app, backend)`, tracks which `(app, backend)` each session belongs to, and
exposes `run()` — an async generator of wire frames (the `UIEvent`s from
`stream_ui_events`, with a final frame enriched with session state + raw-event
snapshots). The Streamlit turn loop used to do all of this in-process; it now lives
here behind the FastAPI server.

Single-process, single-user assumption: the backend is selected by writing
`LLM_BACKEND` before each run (the lazy model reads it per turn). Concurrent
sessions on *different* backends would race on that env var — fine for local dev.
"""
from __future__ import annotations

import dataclasses
import os
from typing import Any, AsyncGenerator

from google.adk.runners import InMemoryRunner

from backend import registry
from backend.events import (
    agent_tree_mermaid,
    build_agent_tree,
    snapshot_event,
)
from backend.overrides import editable_list
from backend.shared.adk_ui_stream import stream_ui_events


async def fetch_session_info(runner, app: str, user_id: str, session_id: str) -> dict:
    """Pull live session state, event count, and artifact keys off the runner's
    services. Best-effort — services/methods vary by ADK version."""
    info: dict[str, Any] = {"state": {}, "event_count": None, "artifacts": []}
    try:
        session = await runner.session_service.get_session(
            app_name=app, user_id=user_id, session_id=session_id
        )
        if session is not None:
            info["state"] = dict(getattr(session, "state", {}) or {})
            events = getattr(session, "events", None)
            if events is not None:
                info["event_count"] = len(events)
    except Exception as e:  # pragma: no cover - depends on ADK version
        info["state_error"] = f"{type(e).__name__}: {e}"
    try:
        artifact_service = getattr(runner, "artifact_service", None)
        if artifact_service is not None:
            keys = await artifact_service.list_artifact_keys(
                app_name=app, user_id=user_id, session_id=session_id
            )
            info["artifacts"] = list(keys or [])
    except Exception:  # artifacts are optional; never block on them
        pass
    return info


class RunnerManager:
    """Builds/caches runners per (app, backend); maps sessions to their runner."""

    def __init__(self) -> None:
        self._runners: dict[tuple[str, str], InMemoryRunner] = {}
        self._sessions: dict[str, tuple[str, str]] = {}

    # --- runners -----------------------------------------------------------
    def runner(self, app: str, backend_name: str) -> InMemoryRunner:
        key = (app, backend_name)
        if key not in self._runners:
            root = registry.build_root_agent(app, backend_name)
            self._runners[key] = InMemoryRunner(agent=root, app_name=app)
        return self._runners[key]

    def rebuild(self, app: str) -> None:
        """Drop every cached runner (and its sessions) for `app` so the next use
        rebuilds from code + the current overlays. Called after an overlay edit."""
        for key in [k for k in self._runners if k[0] == app]:
            self._runners.pop(key, None)
        self._sessions = {s: ab for s, ab in self._sessions.items() if ab[0] != app}

    def root_agent(self, app: str, backend_name: str):
        return self.runner(app, backend_name).agent

    # --- sessions ----------------------------------------------------------
    async def create_session(self, app: str, backend_name: str) -> str:
        runner = self.runner(app, backend_name)
        os.environ["LLM_BACKEND"] = backend_name
        session = await runner.session_service.create_session(
            app_name=app, user_id=registry.USER_ID
        )
        self._sessions[session.id] = (app, backend_name)
        return session.id

    def _resolve(self, session_id: str) -> tuple[str, str]:
        if session_id not in self._sessions:
            raise KeyError(f"unknown session {session_id!r}")
        return self._sessions[session_id]

    # --- the turn stream ---------------------------------------------------
    async def run(self, session_id: str, message: str,
                  pdf_mode: str | None = None) -> AsyncGenerator[dict, None]:
        """Yield wire frames for one turn: live UIEvent frames, then one enriched
        `final` frame carrying usage, session state, and raw-event snapshots."""
        app, backend_name = self._resolve(session_id)
        runner = self.runner(app, backend_name)
        os.environ["LLM_BACKEND"] = backend_name

        # pdf_insight reads a per-turn `mode:` directive (the dropdown's choice).
        send = message
        if app == "pdf_insight" and pdf_mode:
            send = f"mode: {pdf_mode} {message}"

        debug: list = []
        final_usage = None
        async for ev in stream_ui_events(
            runner, user_id=registry.USER_ID, session_id=session_id,
            message=send, simulate_stream=(backend_name == "mock"),
            debug_sink=debug, snapshot_fn=snapshot_event,
        ):
            if ev.kind == "final":
                final_usage = ev.usage
                continue  # held back; re-emitted enriched below
            yield dataclasses.asdict(ev)

        info = await fetch_session_info(runner, app, registry.USER_ID, session_id)
        yield {
            "kind": "final",
            "usage": final_usage,
            "session_info": info,
            "debug_snapshots": [dataclasses.asdict(s) for s in debug],
        }

    # --- introspection -----------------------------------------------------
    def agents(self, app: str, backend_name: str) -> dict:
        root = self.root_agent(app, backend_name)
        tree = build_agent_tree(root)
        return {
            "tree": tree,
            "mermaid": agent_tree_mermaid(tree),
            "editable": editable_list(root),
        }
