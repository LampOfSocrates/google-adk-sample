"""FastAPI server — the single backend the Streamlit client talks to.

Exposes the agents over a REST + SSE API (OpenAPI docs at `/docs`):
  * run a turn and stream UIEvents (SSE),
  * inspect / edit each agent's prompt & model (overlays),
  * ingest PDFs and read the queryable schema,
  * save / load / delete conversations.

The ADK runners, sessions, and all `data/` persistence live here; the client holds
no ADK objects and touches no disk. Run with:

    uvicorn backend.server:app --reload --port 8000
"""
from __future__ import annotations

import json
import os

import certifi

# Match the rest of the repo: a stray SSL_CERT_FILE breaks the HTTPS clients.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ.pop("SSL_CERT_DIR", None)

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from backend import agent_defs, conversations, overrides, registry  # noqa: E402
from backend.pdf_insight.ingest import ingest_pdf_everywhere  # noqa: E402
from backend.pdf_insight.stores import SQLiteStore, get_corpus_store  # noqa: E402
from backend.service import RunnerManager  # noqa: E402

app = FastAPI(title="ADK Agent Server", version="1.0")
manager = RunnerManager()

UPLOAD_DIR = os.path.join("data", "uploads")
# Single active PDF for the single-doc modes (mirrors the old UI's per-session
# pdf_state; one user, one active PDF on this dev server).
_pdf_state: dict = {}


# --- wire models -----------------------------------------------------------
class CreateSession(BaseModel):
    backend: str = "mock"


class Message(BaseModel):
    message: str
    pdf_mode: str | None = None


class AgentDefUpdate(BaseModel):
    app: str
    agent: str
    instruction: str | None = None
    model: str | None = None        # "" clears the pin (follow the Backend selector)
    description: str | None = None


class SaveConversation(BaseModel):
    id: str | None = None
    app: str
    backend: str
    title: str | None = None
    messages: list[dict] = []
    debug_turns: list[dict] = []
    extra: dict | None = None


# --- apps + agents ---------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/apps")
def list_apps():
    return {"apps": list(registry.APPS), "backends": registry.BACKENDS}


@app.get("/apps/{app}/agents")
def get_agents(app: str, backend: str = "mock"):
    _require_app(app)
    return manager.agents(app, backend)


@app.get("/apps/{app}/overrides")
def get_overrides(app: str):
    _require_app(app)
    return overrides.load(app)


@app.put("/apps/{app}/overrides")
def put_overrides(app: str, body: dict):
    _require_app(app)
    overrides.save(app, body)
    manager.rebuild(app)
    return {"ok": True, "saved": overrides.load(app)}


@app.delete("/apps/{app}/overrides")
def delete_overrides(app: str):
    _require_app(app)
    overrides.clear(app)
    manager.rebuild(app)
    return {"ok": True}


# --- agent definitions (DuckDB-versioned, per-agent edit + history) ---------
def _write_agent_def(app: str, agent: str, fields: dict) -> dict:
    """Append a version to DuckDB AND sync the live JSON overlay, then rebuild so
    the agent reloads the new fields. Keeps the two stores consistent."""
    rec = agent_defs.record(app, agent, fields)
    ov = overrides.load(app)
    entry = dict(ov.get(agent, {}))
    for key, val in fields.items():
        if val:                       # non-empty -> pin it
            entry[key] = val
        else:                         # blank -> drop the pin (follow code/backend)
            entry.pop(key, None)
    if entry:
        ov[agent] = entry
    else:
        ov.pop(agent, None)
    overrides.save(app, ov)
    manager.rebuild(app)
    return {"ok": True, "version": rec["version"]}


@app.get("/agent_definition/read")
def agent_def_read(app: str, agent: str, backend: str = "mock"):
    """Current effective fields for one agent (code defaults + live overlay), the
    live overlay itself, and the most recent stored version."""
    _require_app(app)
    live = next((a for a in manager.agents(app, backend)["editable"]
                 if a["name"] == agent), None)
    if live is None:
        raise HTTPException(status_code=404, detail=f"no editable agent {agent!r} in {app}")
    return {"app": app, "agent": agent, "live": live,
            "overlay": overrides.load(app).get(agent, {}),
            "latest": agent_defs.latest(app, agent)}


@app.post("/agent_definition/update")
def agent_def_update(body: AgentDefUpdate):
    _require_app(body.app)
    fields = {k: v for k, v in (("instruction", body.instruction),
                                ("model", body.model),
                                ("description", body.description)) if v is not None}
    return _write_agent_def(body.app, body.agent, fields)


@app.get("/agent_definition/history")
def agent_def_history(app: str, agent: str):
    _require_app(app)
    return {"history": agent_defs.history(app, agent)}


@app.post("/agent_definition/restore")
def agent_def_restore(app: str, agent: str, version: int):
    """Re-apply a past version as a new latest entry (history stays append-only)."""
    _require_app(app)
    old = agent_defs.get_version(app, agent, version)
    if old is None:
        raise HTTPException(status_code=404, detail=f"no version {version} for {agent}")
    return _write_agent_def(app, agent, {k: old.get(k) for k in agent_defs.FIELDS})


# --- sessions + chat -------------------------------------------------------
@app.post("/apps/{app}/sessions")
async def create_session(app: str, body: CreateSession):
    _require_app(app)
    sid = await manager.create_session(app, body.backend)
    return {"session_id": sid}


@app.post("/apps/{app}/sessions/{sid}/messages")
async def send_message(app: str, sid: str, body: Message):
    _require_app(app)

    async def gen():
        try:
            async for frame in manager.run(sid, body.message, body.pdf_mode):
                yield f"data: {json.dumps(frame, default=str)}\n\n"
        except KeyError:
            err = {"kind": "error", "text": f"unknown session {sid}"}
            yield f"data: {json.dumps(err)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- pdf_insight -----------------------------------------------------------
@app.post("/apps/pdf_insight/uploads")
async def upload_pdf(file: UploadFile = File(...)):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    path = os.path.join(UPLOAD_DIR, file.filename)
    with open(path, "wb") as out:
        out.write(await file.read())
    summary = ingest_pdf_everywhere(path, _pdf_state)
    os.environ["PDF_PATH"] = path  # the coordinator's default active PDF
    return {"file": file.filename, "summary": summary}


@app.get("/apps/pdf_insight/schema")
def pdf_schema():
    out: dict = {"active_pdf": os.environ.get("PDF_PATH"), "corpus": None, "sqlite": None}
    out["corpus"] = get_corpus_store(_pdf_state).list_schema()
    db = _pdf_state.get("db_path")
    if db:
        out["sqlite"] = SQLiteStore(db).list_schema()
    return out


# --- conversations ---------------------------------------------------------
@app.get("/conversations")
def conv_list():
    return {"conversations": conversations.list_conversations()}


@app.post("/conversations")
def conv_save(body: SaveConversation):
    conv_id = body.id or conversations.new_id(body.app, conversations.title_from(body.messages))
    folder = conversations.save(
        conv_id, app=body.app, backend=body.backend, messages=body.messages,
        debug_turns=body.debug_turns, title=body.title, extra=body.extra,
    )
    return {"id": conv_id, "folder": folder}


@app.get("/conversations/{conv_id}")
def conv_get(conv_id: str):
    return conversations.load(conv_id)


@app.delete("/conversations/{conv_id}")
def conv_delete(conv_id: str):
    conversations.delete(conv_id)
    return {"ok": True}


def _require_app(app: str) -> None:
    if app not in registry.APPS:
        raise HTTPException(status_code=404, detail=f"unknown app {app!r}")
