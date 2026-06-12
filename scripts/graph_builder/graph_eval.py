"""Run the resolver eval set against a chosen backend and print a scorecard.

    ./local_run.sh server                                   # start the server first
    LLM_BACKEND=openai   python scripts/graph_builder/graph_eval.py
    LLM_BACKEND=deepseek python scripts/graph_builder/graph_eval.py
    LLM_BACKEND=gemini   python scripts/graph_builder/graph_eval.py

Each scenario runs in its OWN server session (fresh graph), accreting its turns;
the final graph (read from the session state the server returns) is scored for
correct merges (no wrong-split) and over-merges (no fusing distinct components).
The mock backend can't resolve, so it fails every merge — that's the floor, not a
regression. The scoring (`score`, `SCENARIOS`) is pure and imported directly.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from apps.pages import api_client  # noqa: E402  (pure-httpx client SDK; no ADK)
from backend.graph_builder.evals import SCENARIOS, score  # noqa: E402  (pure; no agent)

APP = "graph_builder"


def _run_scenario(backend: str, turns) -> dict:
    """Run one scenario's turns in a fresh session; return its final graph."""
    session_id = api_client.create_session(APP, backend)
    state = {}
    for turn in turns:
        state = api_client.run_turn(APP, session_id, turn)["session_info"].get("state", {})
    return state.get("graph", {})


def main():
    if not api_client.health():
        sys.exit(f"server unreachable at {api_client.BASE_URL} — start it with "
                 "`./local_run.sh server`")
    backend = os.environ.get("LLM_BACKEND", "mock")
    print(f"backend = {backend}\n{'=' * 64}")
    results = []
    for sc in SCENARIOS:
        graph = _run_scenario(backend, sc["turns"])
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
    main()
