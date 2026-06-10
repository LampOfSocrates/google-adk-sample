"""Adversarial demo for the conversational graph builder (the moat test).

Feeds ONE session four turns from three sources (incident, wiki, email) that name
the SAME component four different ways — auth-service / login backend / AuthN /
Identity Provider — plus a distractor (user-service) that must NOT be merged into
it. The question the demo answers: does the graph ACCRETE onto one canonical node,
or fork into many?

    python scripts/graph_builder/graph_demo.py                 # offline mock: shows WRONG-SPLIT
    LLM_BACKEND=gemini python scripts/graph_builder/graph_demo.py   # real resolver: should MERGE

On mock the resolver can't reason, so every mention becomes a new node — that's the
failure you'd expect, and the reason the real test needs a real model. On gemini,
the four surface names should collapse to one auth-service node carrying five
claims from three sources, with user-service kept separate.
"""
import asyncio
import os
import sys

import certifi

# Stray SSL_CERT_FILE on this box points at a missing CA bundle and crashes the
# google-genai HTTPS client the moment LLM_BACKEND=gemini makes a real call.
# Force a known-good bundle before any google.* import. See scripts/shared/smoke.py.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ.pop("SSL_CERT_DIR", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Windows consoles default to cp1252, which can't encode the arrows/bullets below.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from dotenv import load_dotenv

load_dotenv()

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from apps.graph_builder.agent import root_agent  # noqa: E402  (model binds at import)

APP = "graph_builder"

TURNS = [
    "[incident INC-1742] During the checkout outage, auth-service returned HTTP 500s. "
    "Its users-db connection pool on Postgres was exhausted.",
    "[wiki] The login backend owns password hashing and issues JWT tokens. "
    "It reads from the users-db.",
    "[email] AuthN migration: we are moving AuthN off the shared Postgres onto a "
    "dedicated instance next sprint. The Identity Provider team owns this work.",
    "[incident INC-1801] Another auth-service timeout. The user-service that calls "
    "auth-service degraded as a result.",
]


async def _ask(runner, session_id, text):
    msg = types.Content(role="user", parts=[types.Part(text=text)])
    final = ""
    async for event in runner.run_async(
        user_id="you", session_id=session_id, new_message=msg
    ):
        if event.is_final_response() and event.content:
            final = event.content.parts[0].text
    return final


def _dump_graph(graph):
    nodes = graph.get("nodes", {})
    edges = graph.get("edges", [])
    print(f"\n{'=' * 70}\nFINAL GRAPH: {len(nodes)} nodes, {len(edges)} edges")
    print("=" * 70)
    for nid, n in nodes.items():
        aliases = ", ".join(n.get("aliases", [])) or "—"
        srcs = sorted({c["source"] for c in n.get("claims", [])})
        print(f"\n[{nid}] {n['canonical_name']}  (kind={n.get('kind')})")
        print(f"      aliases : {aliases}")
        print(f"      claims  : {len(n.get('claims', []))} from {srcs or ['—']}")
        for c in n.get("claims", []):
            print(f"        · ({c['source']}/t{c['turn']}) {c['text']}")
    if edges:
        print("\nedges:")
        for e in edges:
            print(f"  {e['from']} --{e['predicate']}--> {e['to']}  ({e['source']})")


async def main():
    backend = os.environ.get("LLM_BACKEND", "mock")
    print(f"backend = {backend}")
    if backend == "mock":
        print("NOTE: mock can't resolve — expect WRONG-SPLIT (one component, many nodes).")
        print("      Run with LLM_BACKEND=gemini to test real resolution.\n")

    runner = InMemoryRunner(agent=root_agent, app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id="you")

    for i, turn in enumerate(TURNS, 1):
        print(f"\n{'#' * 70}\n# TURN {i}: {turn}\n{'#' * 70}")
        reply = await _ask(runner, session.id, turn)
        print(reply)

    refreshed = await runner.session_service.get_session(
        app_name=APP, user_id="you", session_id=session.id
    )
    _dump_graph(refreshed.state.get("graph", {}))


if __name__ == "__main__":
    asyncio.run(main())
