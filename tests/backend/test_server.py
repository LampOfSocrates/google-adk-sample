"""FastAPI server smoke + contract tests (offline, mock backend).

Drives backend/server.py through FastAPI's TestClient: list apps, create a
session, stream a turn (SSE frames), read/edit/reset agent overlays, and
round-trip a conversation. Everything runs on LLM_BACKEND=mock (the autouse
_backend_env fixture), so no keys and no network.
"""
import json

import pytest
from fastapi.testclient import TestClient

from backend import conversations, overrides, server
from backend.service import RunnerManager


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Redirect on-disk state to a temp dir and give each test a fresh manager so
    # cached runners / overlay rebuilds don't leak between tests.
    monkeypatch.setattr(conversations, "ROOT", str(tmp_path / "conv"))
    monkeypatch.setattr(overrides, "_DIR", str(tmp_path / "ov"))
    monkeypatch.setattr(server, "manager", RunnerManager())
    return TestClient(server.app)


def _frames(resp) -> list[dict]:
    """Parse an SSE body into its JSON frames."""
    return [json.loads(line[6:]) for line in resp.text.splitlines()
            if line.startswith("data: ")]


def test_health_and_list_apps(client):
    assert client.get("/health").json() == {"ok": True}
    body = client.get("/apps").json()
    assert set(body["apps"]) == {"pdf_insight", "travel_planner",
                                 "graph_builder", "text_to_diagram"}
    assert "mock" in body["backends"]


def test_create_session_and_stream_a_turn(client):
    sid = client.post("/apps/text_to_diagram/sessions",
                      json={"backend": "mock"}).json()["session_id"]
    resp = client.post(f"/apps/text_to_diagram/sessions/{sid}/messages",
                       json={"message": "Paris is the capital of France"})
    assert resp.status_code == 200
    frames = _frames(resp)
    kinds = [f["kind"] for f in frames]
    assert "text_delta" in kinds            # the diagram streamed back
    assert kinds[-1] == "final"             # exactly one final, last
    final = frames[-1]
    assert "session_info" in final and "debug_snapshots" in final
    assert len(final["debug_snapshots"]) >= 1


def test_unknown_app_is_404(client):
    assert client.post("/apps/nope/sessions", json={"backend": "mock"}).status_code == 404


def test_agents_endpoint_lists_editable_agents(client):
    body = client.get("/apps/pdf_insight/agents", params={"backend": "mock"}).json()
    assert body["mermaid"].startswith("flowchart")
    names = {a["name"] for a in body["editable"]}
    assert "pdf_router" in names and "pdfbytes" in names


def test_overrides_roundtrip_and_apply(client):
    app = "pdf_insight"
    put = client.put(f"/apps/{app}/overrides",
                     json={"pdf_router": {"instruction": "ROUTER EDITED"}})
    assert put.json()["ok"] is True
    assert client.get(f"/apps/{app}/overrides").json()["pdf_router"]["instruction"] == "ROUTER EDITED"

    # the rebuilt agent reflects the overlay
    editable = client.get(f"/apps/{app}/agents", params={"backend": "mock"}).json()["editable"]
    router = next(a for a in editable if a["name"] == "pdf_router")
    assert router["instruction"] == "ROUTER EDITED"

    assert client.delete(f"/apps/{app}/overrides").json()["ok"] is True
    assert client.get(f"/apps/{app}/overrides").json() == {}


def test_conversations_crud(client):
    msgs = [{"role": "user", "content": "what is the total vega?"},
            {"role": "assistant", "content": "6,384"}]
    cid = client.post("/conversations",
                      json={"app": "pdf_insight", "backend": "mock", "messages": msgs}).json()["id"]
    listed = client.get("/conversations").json()["conversations"]
    assert any(c["id"] == cid for c in listed)

    got = client.get(f"/conversations/{cid}").json()
    assert got["messages"] == msgs

    assert client.delete(f"/conversations/{cid}").json()["ok"] is True
    assert all(c["id"] != cid for c in client.get("/conversations").json()["conversations"])


def test_conversations_save_with_debug_snapshot_dicts(client):
    """Regression: the client round-trips already-serialized snapshot *dicts*, so
    save must not call dataclasses.asdict on them (that 500'd and the client saw a
    non-JSON body)."""
    debug_turns = [{
        "prompt": "hi", "latency": 0.4, "session": {"state": {}},
        "snapshots": [{
            "seq": 0, "author": "pdf_router", "timestamp": 1.0, "is_final": True,
            "partial": False, "invocation_id": "i1",
            "parts": [{"kind": "text", "tool_name": None, "tool_args": None,
                       "tool_result": None, "text": "hello"}],
            "usage": {"total": 5}, "raw": {"x": 1},
        }],
    }]
    body = {"app": "pdf_insight", "backend": "mock",
            "messages": [{"role": "user", "content": "hi"}], "debug_turns": debug_turns}
    cid = client.post("/conversations", json=body).json()["id"]      # must not 500
    got = client.get(f"/conversations/{cid}").json()                 # and round-trips
    assert got["debug_turns"][0]["snapshots"][0]["author"] == "pdf_router"
