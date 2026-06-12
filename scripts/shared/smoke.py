"""One live smoke test per agent against a chosen LLM backend — via the server.

    ./local_run.sh server                  # start the server first
    python scripts/shared/smoke.py deepseek
    python scripts/shared/smoke.py bedrock
    python scripts/shared/smoke.py         # defaults to deepseek

Drives each agent through the FastAPI server (so it smoke-tests the whole stack:
HTTP + SSE + the agent on the requested backend). Each agent is isolated so one
failure doesn't block the rest. Writes a per-provider artifact under
tests/smoke-results/<backend>.txt and EXITS NON-ZERO if any agent did not PASS — so
it can gate CI / be asserted on. Point at another server with API_BASE_URL.
"""
import datetime
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO_ROOT, ".env"))

from apps.pages import api_client  # noqa: E402  (pure-httpx client SDK; no ADK)

BACKEND = (sys.argv[1] if len(sys.argv) > 1 else "deepseek").strip().lower()
RESULTS = []  # (label, status, prompt, detail)


def _model_id(app: str) -> str:
    """Resolved model id for `app` on this backend, read off the agent tree."""
    try:
        editable = api_client.get_agents(app, BACKEND).get("editable", [])
        models = [a["model"] for a in editable if a.get("model")]
        return models[0] if models else BACKEND
    except Exception:
        return BACKEND


def smoke(label: str, app: str, prompt: str, expect: str | None = None):
    print(f"\n=== {label} === (prompt: {prompt!r})")
    status, detail = "PASS", ""
    try:
        sid = api_client.create_session(app, BACKEND)
        res = api_client.run_turn(app, sid, prompt)
        reply = (res["error"] or res["text"] or "").strip()
        ok = bool(reply) and not res["error"]
        if expect:
            ok = ok and expect.lower() in reply.lower()
        status = "PASS" if ok else "CHECK"
        detail = reply
    except Exception as e:  # noqa: BLE001 - smoke test: record, don't crash
        status = "FAIL"
        detail = f"{type(e).__name__}: {e}"
    RESULTS.append((label, status, prompt, detail))
    print(f"[{status}] {label}")
    print("  reply:", detail[:400])


def _write_artifact(model_id: str) -> int:
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
        lines += [f"[{status}] {label}", f"  prompt: {prompt!r}",
                  f"  result: {detail[:800]}", ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nwrote {path}")
    return passed


def main():
    if not api_client.health():
        sys.exit(f"server unreachable at {api_client.BASE_URL} — start it with "
                 "`./local_run.sh server`")
    model_id = _model_id("travel_planner")
    print(f"backend = {BACKEND}  model = {model_id}  server = {api_client.BASE_URL}")
    smoke("travel_planner", "travel_planner", "What's the weather in Tokyo?", expect="tokyo")
    smoke("text_to_diagram", "text_to_diagram", "Paris is the capital of France.",
          expect="```mermaid")
    smoke("pdf_insight", "pdf_insight", "What is in this statement?")

    passed = _write_artifact(model_id)
    total = len(RESULTS)
    print(f"=== {BACKEND}: {passed}/{total} PASS ===")
    if passed != total:  # assert: non-zero exit gates CI / scripts
        sys.exit(1)


if __name__ == "__main__":
    main()
