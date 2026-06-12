"""Offline unit tests for the resolver scorer (pure, no LLM). Locks the eval
LOGIC so a green scorecard means what we think it means."""
from backend.graph_builder.evals import score

_SCENARIO = {
    "name": "t",
    "turns": [],
    "must_merge": [["auth-service", "login backend"]],
    "must_not_merge": [["auth-service", "user-service"]],
}


def _graph(nodes):
    return {"nodes": nodes, "edges": [], "turn": 1}


def test_correct_merge_and_separation_passes():
    g = _graph(
        {
            "n0": {"canonical_name": "auth-service", "aliases": ["login backend"], "claims": []},
            "n1": {"canonical_name": "user-service", "aliases": [], "claims": []},
        }
    )
    r = score(g, _SCENARIO)
    assert r["passed"] and r["merges_ok"] == 1 and not r["overmerge_violations"]


def test_wrong_split_fails_the_merge():
    # the two aliases landed on different nodes
    g = _graph(
        {
            "n0": {"canonical_name": "auth-service", "aliases": [], "claims": []},
            "n1": {"canonical_name": "login backend", "aliases": [], "claims": []},
        }
    )
    r = score(g, _SCENARIO)
    assert not r["passed"] and r["merges_ok"] == 0


def test_over_merge_is_flagged():
    # user-service wrongly folded into auth-service as an alias
    g = _graph(
        {
            "n0": {
                "canonical_name": "auth-service",
                "aliases": ["login backend", "user-service"],
                "claims": [],
            },
        }
    )
    r = score(g, _SCENARIO)
    assert not r["passed"]
    assert r["overmerge_violations"] == [["auth-service", "user-service", "n0"]]


def test_missing_name_counts_as_missed():
    g = _graph({"n0": {"canonical_name": "auth-service", "aliases": [], "claims": []}})
    r = score(g, _SCENARIO)
    assert "login backend" in r["missed"] and not r["passed"]
