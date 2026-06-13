"""Editable agent overlays — change any ADK agent's prompt/model without code.

Every app builds its graph in code (`module.root_agent`). This module overlays two
fields per agent — `instruction` (the system prompt) and `model` — from a per-app
JSON file (`data/agent_overrides/<app>.json`), so prompts/models can be tuned live
and the change survives a restart.

Flow:
  1. A client edits fields and the server calls `save(app, overrides)`.
  2. The app recreates its agents (fresh import) and calls `apply(root_agent,
     load(app))`, which mutates the freshly-built agents. ADK agents are plain
     pydantic models, so setting `.instruction`/`.model` is all it takes.

Generic on purpose: walks sub_agents and AgentTool-wrapped agents, so it works for
every app. An override naming an agent that no longer exists is ignored.

Pure logic — no Streamlit. (Was `apps/pages/agent_overrides.py`; the rendering
moved to the client, the logic moved to the backend when the app split.)
"""
from __future__ import annotations

import json
import os
from typing import Iterator

_DIR = os.path.join("data", "agent_overrides")


def _path(app: str) -> str:
    return os.path.join(_DIR, f"{app}.json")


def load(app: str) -> dict:
    """Read the saved overrides for `app`, or {} if none / unreadable."""
    try:
        with open(_path(app), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(app: str, overrides: dict) -> str:
    """Write `overrides` ({agent_name: {instruction?, model?}}) and return the path.
    Agents with no overridden field are dropped so the file stays minimal."""
    pruned = {name: ov for name, ov in overrides.items() if ov}
    os.makedirs(_DIR, exist_ok=True)
    path = _path(app)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(pruned, fh, indent=2, ensure_ascii=False)
    return path


def clear(app: str) -> None:
    """Delete the app's overlay file (revert to pure code defaults)."""
    try:
        os.remove(_path(app))
    except FileNotFoundError:
        pass


def iter_agents(agent, _seen: set | None = None) -> Iterator:
    """Yield every distinct agent reachable from `agent`: itself, its sub_agents
    (transfer targets), and any agents wrapped by AgentTools. De-duplicated by id."""
    if _seen is None:
        _seen = set()
    if agent is None or id(agent) in _seen:
        return
    _seen.add(id(agent))
    yield agent
    for sub in getattr(agent, "sub_agents", None) or []:
        yield from iter_agents(sub, _seen)
    for tool in getattr(agent, "tools", None) or []:
        inner = getattr(tool, "agent", None)
        if inner is not None:
            yield from iter_agents(inner, _seen)


def instruction_of(agent) -> str | None:
    """The agent's instruction if it's a plain string we can edit, else None.
    (ADK also allows callable InstructionProviders, which aren't text-editable.)"""
    instr = getattr(agent, "instruction", None)
    return instr if isinstance(instr, str) else None


def model_id_of(agent) -> str | None:
    """A human-readable model id for the agent: the string itself, the lazy proxy's
    live id (e.g. 'mock'/'gemini-…'), or the class name as a last resort."""
    model = getattr(agent, "model", None)
    if model is None:
        return None
    if isinstance(model, str):
        return model or None
    inner = getattr(model, "model", None)
    return inner if isinstance(inner, str) else type(model).__name__


def is_editable(agent) -> bool:
    """True when the agent exposes a prompt or a model worth editing."""
    return instruction_of(agent) is not None or getattr(agent, "model", None) is not None


def editable_list(root_agent) -> list[dict]:
    """Serializable view of every editable agent for the Agents editor — name,
    type, current model id, instruction, description. The client renders this."""
    out = []
    for ag in iter_agents(root_agent):
        if not is_editable(ag):
            continue
        out.append({
            "name": getattr(ag, "name", "?"),
            "type": type(ag).__name__,
            "model": model_id_of(ag),
            "instruction": instruction_of(ag),
            "description": getattr(ag, "description", None),
        })
    return out


def apply(root_agent, overrides: dict) -> list[str]:
    """Overlay `overrides` onto the live agent graph. Returns the names applied.

    Mutates in place: sets `.instruction` and/or `.model` on each named agent.
    Unknown names and absent fields are skipped. A blank model is ignored here
    (the freshly-built agent already carries its lazy default)."""
    if not overrides:
        return []
    applied: list[str] = []
    for ag in iter_agents(root_agent):
        ov = overrides.get(getattr(ag, "name", None))
        if not ov:
            continue
        touched = False
        instr = ov.get("instruction")
        if isinstance(instr, str) and hasattr(ag, "instruction"):
            ag.instruction = instr
            touched = True
        model = ov.get("model")
        if model:
            ag.model = model
            touched = True
        desc = ov.get("description")
        if isinstance(desc, str) and hasattr(ag, "description"):
            ag.description = desc
            touched = True
        if touched:
            applied.append(ag.name)
    return applied
