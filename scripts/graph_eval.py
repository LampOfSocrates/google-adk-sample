"""Run the resolver eval set against a chosen backend and print a scorecard.

    LLM_BACKEND=openai   python scripts/graph_eval.py
    LLM_BACKEND=deepseek python scripts/graph_eval.py
    LLM_BACKEND=gemini   python scripts/graph_eval.py

Each scenario runs in its OWN session (fresh graph), accreting its turns, then the
final graph is scored for correct merges (no wrong-split) and over-merges (no
fusing distinct components). Use this to measure the resolver as you tune its
prompt. The mock backend can't resolve, so it will fail every merge — that's the
floor, not a regression.
"""
import asyncio
import os
import sys

import certifi

# Stray SSL_CERT_FILE on this box breaks the HTTPS client; force a good bundle.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ.pop("SSL_CERT_DIR", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from apps.graph_builder.agent import root_agent  # noqa: E402
from apps.graph_builder.evals import SCENARIOS, score  # noqa: E402

APP = "graph_builder"


async def _run_scenario(turns):
    runner = InMemoryRunner(agent=root_agent, app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id="eval")
    for turn in turns:
        msg = types.Content(role="user", parts=[types.Part(text=turn)])
        async for _ in runner.run_async(
            user_id="eval", session_id=session.id, new_message=msg
        ):
            pass
    refreshed = await runner.session_service.get_session(
        app_name=APP, user_id="eval", session_id=session.id
    )
    return refreshed.state.get("graph", {})


async def main():
    backend = os.environ.get("LLM_BACKEND", "mock")
    print(f"backend = {backend}\n{'=' * 64}")
    results = []
    for sc in SCENARIOS:
        graph = await _run_scenario(sc["turns"])
        r = score(graph, sc)
        results.append(r)
        status = "PASS" if r["passed"] else "FAIL"
        print(f"\n[{status}] {r['scenario']}  ({r['node_count']} nodes)")
        print(f"  merges  : {r['merges_ok']}/{r['merges_total']} clusters merged correctly")
        for m in r["detail"]:
            if not m["merged"]:
                print(f"    ✗ split: {m['cluster']} -> {m['resolved_to']}")
        if r["overmerge_violations"]:
            for a, b, nid in r["overmerge_violations"]:
                print(f"    ✗ OVER-MERGE: '{a}' and '{b}' both landed on {nid}")
        if r["missed"]:
            print(f"  missed  : {r['missed']} (never extracted)")

    passed = sum(1 for r in results if r["passed"])
    print(f"\n{'=' * 64}\nTOTAL: {passed}/{len(results)} scenarios passed")


if __name__ == "__main__":
    asyncio.run(main())
