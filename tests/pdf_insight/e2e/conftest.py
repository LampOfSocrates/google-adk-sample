"""Fixtures for the browser-driven UI e2e test (`test_ui_chat.py`).

This spins up the REAL surface the user clicks through — the FastAPI agent server
plus the Streamlit client — as subprocesses, then lets Playwright drive a browser
against them. Distinct from `test_live_modes.py`, which exercises the agent graph
directly via the `converse` fixture (no browser, no server).

Topology (mirrors `local_run.sh`):

    uvicorn backend.server:app   (the agent server the client calls)
        ^  API_BASE_URL
    streamlit run apps/pages/streamlit_app.py   (the chat UI)
        ^  Playwright `page`

The corpus mode question is cross-document by definition, so we pre-seed a small
multi-week DuckDB corpus and point the server at it via PDF_CORPUS_DB. The single
uploaded PDF (risk_report.pdf) appends to that same corpus on upload.

These tests hit a real Bedrock model, so the whole module skips unless AWS
credentials are present. They are auto-marked `live`/`e2e` by the root conftest
(folder = the source of truth), so the default offline run never starts a server.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from scripts.pdf_insight.pdf_to_duckdb import ingest_dir
from scripts.pdf_insight.weekly_report import generate_for

REPO_ROOT = str(Path(__file__).resolve().parents[3])
FIXTURE_PDF = str(Path(__file__).resolve().parents[1] / "fixtures" / "risk_report.pdf")


# --------------------------------------------------------------- process utils ---
def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_http(url: str, timeout: float, check) -> None:
    """Poll `url` until `check(status, body)` is true, or raise after `timeout`."""
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:  # noqa: S310 - localhost
                if check(r.status, r.read().decode("utf-8", "replace")):
                    return
        except Exception as e:  # noqa: BLE001 - server not up yet
            last_err = e
        time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for {url}: {last_err}")


def _terminate(proc: subprocess.Popen) -> None:
    """Kill a server process and its children (Streamlit/uvicorn spawn some)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


# --------------------------------------------------------------------- fixtures ---
@pytest.fixture(scope="session", autouse=True)
def _require_bedrock() -> None:
    """The whole UI e2e module needs Bedrock creds; skip cleanly without them."""
    if not (os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE")):
        pytest.skip("UI e2e needs AWS Bedrock credentials (AWS_ACCESS_KEY_ID / AWS_PROFILE)")


@pytest.fixture(scope="session")
def seeded_corpus(tmp_path_factory) -> dict:
    """A small 3-week DuckDB corpus of dated risk reports, for the temporal,
    cross-document question. Returns {db, dates} where `dates` are ISO strings the
    corpus parses out of each filename (`risk_report_<date>.pdf`)."""
    d = tmp_path_factory.mktemp("ui_corpus")
    weeks = [dt.date(2026, 5, 15), dt.date(2026, 5, 22), dt.date(2026, 5, 29)]
    for wk in weeks:
        generate_for(wk, str(d))
    db = str(d / "corpus.duckdb")
    ingest_dir(db, str(d))
    return {"db": db, "dates": [w.isoformat() for w in weeks]}


@pytest.fixture(scope="session")
def agent_server(seeded_corpus, tmp_path_factory) -> dict:
    """Launch `uvicorn backend.server:app` and wait for /health. The corpus points
    at the pre-seeded DB; the per-document SQLite goes to a temp dir so a test run
    never pollutes the repo's data/."""
    port = _free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = REPO_ROOT
    env["PDF_CORPUS_DB"] = seeded_corpus["db"]
    env["PDF_SQLITE_DIR"] = str(tmp_path_factory.mktemp("ui_sqlite"))
    env["LLM_BACKEND"] = "bedrock"  # UI also selects it per session; keep the default aligned
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.server:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=REPO_ROOT, env=env,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_http(f"{url}/health", 60, lambda s, b: s == 200 and "true" in b.lower())
        yield {"url": url, "port": port}
    finally:
        _terminate(proc)


@pytest.fixture(scope="session")
def streamlit_ui(agent_server) -> dict:
    """Launch the Streamlit client pointed at the agent server; wait for it to be
    healthy. Returns its base URL for Playwright to navigate to."""
    port = _free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = REPO_ROOT
    env["API_BASE_URL"] = agent_server["url"]
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "apps/pages/streamlit_app.py",
         "--server.address", "127.0.0.1", "--server.port", str(port),
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=REPO_ROOT, env=env,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_http(f"{url}/_stcore/health", 60, lambda s, b: s == 200 and b.strip() == "ok")
        yield {"url": url, "port": port}
    finally:
        _terminate(proc)
