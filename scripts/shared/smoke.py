"""One live smoke test per agent against a chosen LLM backend.

    python scripts/shared/smoke.py deepseek
    python scripts/shared/smoke.py bedrock
    python scripts/shared/smoke.py            # defaults to deepseek

Makes real (usually cheap) API calls. Each agent is isolated so one failure
doesn't block the rest. Writes a per-provider artifact `smoke_<backend>_results.txt`
and EXITS NON-ZERO if any agent did not PASS — so it can gate CI / be asserted on.
"""
import asyncio
import datetime
import os
import sys
import traceback

import certifi

# Stray SSL_CERT_FILE on this box breaks the HTTPS clients; force certifi.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ.pop("SSL_CERT_DIR", None)

# Backend MUST be set before importing any agent (model binds at import time).
BACKEND = (sys.argv[1] if len(sys.argv) > 1 else "deepseek").strip().lower()
os.environ["LLM_BACKEND"] = BACKEND

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO_ROOT, ".env"))

from shared.model import get_model  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

RESULTS = []  # (label, status, prompt, detail)


def _model_id() -> str:
    m = get_model()
    return getattr(m, "model", None) or (m if isinstance(m, str) else type(m).__name__)


async def _run(agent, prompt, app, retries=4):
    runner = InMemoryRunner(agent=agent, app_name=app)
    session = await runner.session_service.create_session(app_name=app, user_id="smoke")
    msg = types.Content(role="user", parts=[types.Part(text=prompt)])
    for attempt in range(retries):
        try:
            final = ""
            async for ev in runner.run_async(
                user_id="smoke", session_id=session.id, new_message=msg
            ):
                if ev.is_final_response() and ev.content and ev.content.parts:
                    final = ev.content.parts[0].text or final
            return final
        except Exception as e:  # noqa: BLE001
            s = str(e)
            transient = any(
                t in s for t in ("RESOURCE_EXHAUSTED", "429", "503", "UNAVAILABLE")
            )
            if transient and attempt < retries - 1:
                await asyncio.sleep(20)  # rate limits / transient overload (e.g. Gemini)
            else:
                raise


async def smoke(label, import_agent, prompt, expect=None):
    print(f"\n=== {label} === (prompt: {prompt!r})")
    status, detail = "PASS", ""
    try:
        agent = import_agent()
        reply = await asyncio.wait_for(_run(agent, prompt, label), timeout=180)
        ok = bool(reply and reply.strip())
        if expect:
            ok = ok and expect.lower() in reply.lower()
        status = "PASS" if ok else "CHECK"
        detail = (reply or "").strip()
    except Exception as e:  # noqa: BLE001 - smoke test: record, don't crash
        status = "FAIL"
        detail = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    RESULTS.append((label, status, prompt, detail))
    print(f"[{status}] {label}")
    print("  reply:", detail[:400])


def _travel():
    from apps.travel_planner.agent import root_agent
    return root_agent


def _diagram():
    from apps.text_to_diagram.agent import root_agent
    return root_agent


def _pdf():
    from apps.pdf_insight.agent import root_agent
    return root_agent


def _write_artifact(model_id):
    results_dir = os.path.join(REPO_ROOT, "tests", "smoke-results")
    os.makedirs(results_dir, exist_ok=True)
    path = os.path.join(results_dir, f"{BACKEND}.txt")
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    passed = sum(1 for _, s, _, _ in RESULTS if s == "PASS")
    lines = [
        f"smoke results — backend={BACKEND}  model={model_id}",
        f"timestamp: {stamp}",
        f"summary: {passed}/{len(RESULTS)} PASS",
        "",
    ]
    for label, status, prompt, detail in RESULTS:
        lines.append(f"[{status}] {label}")
        lines.append(f"  prompt: {prompt!r}")
        lines.append(f"  result: {detail[:800]}")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nwrote {path}")
    return passed


async def main():
    model_id = _model_id()
    print(f"backend = {BACKEND}  model = {model_id}")
    await smoke("travel_planner", _travel, "What's the weather in Tokyo?", expect="tokyo")
    await smoke("text_to_diagram", _diagram, "Paris is the capital of France.",
                expect="```mermaid")
    await smoke("pdf_insight", _pdf, "What is in this statement?")

    passed = _write_artifact(model_id)
    total = len(RESULTS)
    print(f"=== {BACKEND}: {passed}/{total} PASS ===")
    if passed != total:  # assert: non-zero exit gates CI / scripts
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
