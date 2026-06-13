"""Versioned agent-definition history in DuckDB.

Every edit to an agent (instruction/model/description) appends a row here, so the
UI can show the timeline and restore a past version. The *live* overlay the agent
actually loads still lives in backend/overrides.py (the JSON store); this DuckDB is
the history, kept in sync on every update (server writes both). Read/history/restore
read from here.
"""
from __future__ import annotations

import os
from datetime import datetime

import duckdb

DB_PATH = os.environ.get("AGENT_DEFS_DB", os.path.join("data", "agent_definitions.duckdb"))
FIELDS = ("instruction", "model", "description")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = duckdb.connect(DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS agent_defs("
        "app VARCHAR, agent VARCHAR, version BIGINT, "
        "instruction VARCHAR, model VARCHAR, description VARCHAR, updated_at TIMESTAMP)"
    )
    return con


def _row(r) -> dict:
    return {"version": r[0], "instruction": r[1], "model": r[2],
            "description": r[3], "updated_at": str(r[4])}


def record(app: str, agent: str, fields: dict) -> dict:
    """Append the next version for (app, agent) and return it."""
    con = _conn()
    try:
        nxt = con.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM agent_defs WHERE app=? AND agent=?",
            [app, agent]).fetchone()[0]
        con.execute(
            "INSERT INTO agent_defs VALUES (?,?,?,?,?,?,?)",
            [app, agent, nxt, fields.get("instruction"), fields.get("model"),
             fields.get("description"), datetime.now()])
        return {"app": app, "agent": agent, **_row(
            (nxt, fields.get("instruction"), fields.get("model"),
             fields.get("description"), datetime.now()))}
    finally:
        con.close()


def latest(app: str, agent: str) -> dict | None:
    """The most recent stored version for (app, agent), or None."""
    con = _conn()
    try:
        r = con.execute(
            "SELECT version, instruction, model, description, updated_at FROM agent_defs "
            "WHERE app=? AND agent=? ORDER BY version DESC LIMIT 1", [app, agent]).fetchone()
    finally:
        con.close()
    return _row(r) if r else None


def history(app: str, agent: str) -> list[dict]:
    """All stored versions for (app, agent), newest first."""
    con = _conn()
    try:
        rows = con.execute(
            "SELECT version, instruction, model, description, updated_at FROM agent_defs "
            "WHERE app=? AND agent=? ORDER BY version DESC", [app, agent]).fetchall()
    finally:
        con.close()
    return [_row(r) for r in rows]


def get_version(app: str, agent: str, version: int) -> dict | None:
    """One specific version, for restore."""
    con = _conn()
    try:
        r = con.execute(
            "SELECT version, instruction, model, description, updated_at FROM agent_defs "
            "WHERE app=? AND agent=? AND version=?", [app, agent, version]).fetchone()
    finally:
        con.close()
    return _row(r) if r else None
