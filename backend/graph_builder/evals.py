"""Eval set for the resolver — the moat's report card.

Resolution quality has two failure modes, and we score both explicitly:
  - UNDER-merge (wrong-split): surface names that denote one component end up on
    different nodes. Measured by `must_merge` clusters.
  - OVER-merge: distinct components get fused into one node. Measured by
    `must_not_merge` pairs (the traps).

A scenario also tracks `missed` names (never extracted at all). Gold labels are
hand-written and deliberately conservative — e.g. "Identity Provider team" is the
TEAM, not the auth service, so it is NOT in the auth must_merge cluster (both
deepseek and gpt-4o-mini agree, which is why it's encoded that way).

Pure data + scorer here (no ADK), so it's unit-testable; scripts/graph_builder/graph_eval.py
runs the pipeline and feeds the resulting graph in.
"""
from __future__ import annotations


SCENARIOS = [
    {
        "name": "auth",
        "turns": [
            "[incident INC-1742] During the checkout outage, auth-service returned HTTP "
            "500s. Its users-db connection pool on Postgres was exhausted.",
            "[wiki] The login backend owns password hashing and issues JWT tokens. "
            "It reads from the users-db.",
            "[email] AuthN migration: we are moving AuthN off the shared Postgres onto a "
            "dedicated instance next sprint. The Identity Provider team owns this work.",
            "[incident INC-1801] Another auth-service timeout. The user-service that "
            "calls auth-service degraded as a result.",
        ],
        # all three name the auth service; should collapse to one node
        "must_merge": [["auth-service", "login backend", "AuthN"]],
        # traps: these are genuinely different components and must stay separate
        "must_not_merge": [
            ["auth-service", "user-service"],
            ["auth-service", "users-db"],
        ],
    },
    {
        "name": "payments",
        "turns": [
            "[incident INC-2201] payments-service returned 502s during checkout; orders "
            "piled up in the orders-queue.",
            "[wiki] The payment gateway (aka payments-service) authorizes cards via Stripe.",
            "[email] Next quarter we are splitting payouts-service out of payments-service.",
        ],
        "must_merge": [["payments-service", "payment gateway"]],
        "must_not_merge": [
            ["payments-service", "payouts-service"],
            ["payments-service", "orders-queue"],
        ],
    },
]


def _norm(name: str) -> str:
    """Loose match key: lowercase, hyphens→spaces, collapse whitespace."""
    return " ".join(str(name).lower().replace("-", " ").split())


def _index(graph: dict) -> dict[str, str]:
    """Map every surface form (canonical name + each alias) → its node id."""
    idx: dict[str, str] = {}
    for nid, n in (graph or {}).get("nodes", {}).items():
        for form in [n.get("canonical_name", ""), *n.get("aliases", [])]:
            if form:
                idx.setdefault(_norm(form), nid)
    return idx


def score(graph: dict, scenario: dict) -> dict:
    """Score one scenario's resulting graph against its gold labels."""
    idx = _index(graph)

    def node_of(name: str):
        return idx.get(_norm(name))

    merges, missed = [], []
    for cluster in scenario["must_merge"]:
        ids = {name: node_of(name) for name in cluster}
        missed += [name for name, nid in ids.items() if nid is None]
        found = [nid for nid in ids.values() if nid is not None]
        merged = len(found) >= 2 and len(set(found)) == 1 and None not in ids.values()
        merges.append({"cluster": cluster, "merged": merged, "resolved_to": ids})

    overmerges = []
    for a, b in scenario["must_not_merge"]:
        na, nb = node_of(a), node_of(b)
        if na is not None and nb is not None and na == nb:
            overmerges.append([a, b, na])

    n_merge_ok = sum(1 for m in merges if m["merged"])
    return {
        "scenario": scenario["name"],
        "merges_ok": n_merge_ok,
        "merges_total": len(merges),
        "overmerge_violations": overmerges,
        "missed": sorted(set(missed)),
        "node_count": len((graph or {}).get("nodes", {})),
        "passed": n_merge_ok == len(merges) and not overmerges,
        "detail": merges,
    }
