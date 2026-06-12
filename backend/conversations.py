"""Conversation persistence — save / load / list chat sessions on disk.

One conversation per folder under `data/conversations/<id>/`:
    meta.json         # id, title, app, backend, created_at, updated_at, turn_count, …
    messages.json     # the UI transcript (st.session_state.messages)
    debug_turns.json  # raw ADK event snapshots (optional; gated by SAVE_DEBUG_TURNS)

The transcript in `messages` is the source of truth for *display*; the ADK
InMemoryRunner session (the model's own memory) isn't serialized here, so a
reopened conversation redraws fully but starts the model with fresh context (a
documented follow-up in docs/plans/conversations.md).

Pure persistence — no Streamlit import. The Streamlit app owns the buttons,
autosave timer, and session_state wiring; this module just reads/writes files.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
from datetime import datetime
from typing import Any

ROOT = os.path.join("data", "conversations")

# --- the config knobs (change here) ---------------------------------------
AUTOSAVE_INTERVAL_SECONDS = 300   # autosave cadence (5 min). Configurable via code.
SAVE_DEBUG_TURNS = True           # also persist raw event snapshots (can be large).


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s[:40]


def title_from(messages: list[dict]) -> str:
    """A human title from the first user message, else 'chat'."""
    for m in messages or []:
        if m.get("role") == "user" and m.get("content"):
            return m["content"][:60]
    return "chat"


def new_id(app: str, title: str) -> str:
    """A sortable, filesystem-safe id: '<ts>_<app>_<title-slug>'."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{ts}_{app}_{_slug(title) or 'chat'}"


def _folder(conv_id: str) -> str:
    return os.path.join(ROOT, conv_id)


def _write_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# --- debug snapshots <-> json --------------------------------------------
def serialize_debug_turns(turns: list[dict]) -> list[dict]:
    """EventSnapshot dataclasses -> plain dicts for JSON."""
    out = []
    for t in turns or []:
        snaps = [dataclasses.asdict(s) for s in t.get("snapshots", [])]
        out.append({**t, "snapshots": snaps})
    return out


def deserialize_debug_turns(turns: list[dict]) -> list[dict]:
    """Rebuild EventSnapshot/PartSummary objects so the Debug tab can render them."""
    from backend.events import EventSnapshot, PartSummary

    out = []
    for t in turns or []:
        snaps = []
        for d in t.get("snapshots", []):
            d = dict(d)
            parts = [PartSummary(**p) for p in d.pop("parts", [])]
            snaps.append(EventSnapshot(parts=parts, **d))
        out.append({**t, "snapshots": snaps})
    return out


# --- public API -----------------------------------------------------------
def save(conv_id: str, *, app: str, backend: str, messages: list[dict],
         debug_turns: list[dict] | None = None, title: str | None = None,
         extra: dict | None = None) -> str:
    """Write the conversation to its folder; returns the folder path. Re-saving the
    same id overwrites in place and preserves the original created_at."""
    folder = _folder(conv_id)
    os.makedirs(folder, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")

    meta_path = os.path.join(folder, "meta.json")
    created = (_read_json(meta_path) or {}).get("created_at", now)
    meta = {
        "id": conv_id,
        "title": title or title_from(messages),
        "app": app,
        "backend": backend,
        "created_at": created,
        "updated_at": now,
        "turn_count": sum(1 for m in messages if m.get("role") == "user"),
        **(extra or {}),
    }
    _write_json(meta_path, meta)
    _write_json(os.path.join(folder, "messages.json"), messages)
    if SAVE_DEBUG_TURNS and debug_turns is not None:
        _write_json(os.path.join(folder, "debug_turns.json"),
                    serialize_debug_turns(debug_turns))
    return folder


def load(conv_id: str) -> dict:
    """Return {meta, messages, debug_turns} for a saved conversation."""
    folder = _folder(conv_id)
    meta = _read_json(os.path.join(folder, "meta.json")) or {}
    messages = _read_json(os.path.join(folder, "messages.json")) or []
    raw_debug = _read_json(os.path.join(folder, "debug_turns.json"))
    debug = deserialize_debug_turns(raw_debug) if raw_debug else []
    return {"meta": meta, "messages": messages, "debug_turns": debug}


def list_conversations() -> list[dict]:
    """All saved conversations' meta, newest-updated first. Tolerates a corrupt or
    missing meta.json (falls back to the folder name)."""
    if not os.path.isdir(ROOT):
        return []
    metas = []
    for name in os.listdir(ROOT):
        if not os.path.isdir(_folder(name)):
            continue
        meta = _read_json(os.path.join(_folder(name), "meta.json"))
        metas.append(meta or {"id": name, "title": name, "updated_at": ""})
    metas.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
    return metas


def delete(conv_id: str) -> None:
    """Remove a conversation's folder."""
    shutil.rmtree(_folder(conv_id), ignore_errors=True)
