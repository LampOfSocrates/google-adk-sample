"""Phase 1 — the deterministic mermaid renderer. No LLM, no ADK runner."""
from apps.text_to_diagram.render import render_mermaid


def test_empty_triads_yield_placeholder():
    out = render_mermaid([])
    assert "```mermaid" in out and "no triads" in out


def test_nodes_are_deduplicated():
    triads = [
        {"subject": "Paris", "predicate": "capital of", "object": "France"},
        {"subject": "France", "predicate": "located in", "object": "Europe"},
    ]
    out = render_mermaid(triads)
    # 3 unique nodes (Paris, France, Europe) -> France declared once.
    assert out.count('["France"]') == 1
    assert out.count('["Paris"]') == 1
    assert out.count('["Europe"]') == 1


def test_edges_carry_predicate_labels():
    out = render_mermaid([{"subject": "A", "predicate": "knows", "object": "B"}])
    assert "-->|knows|" in out


def test_accepts_triadlist_dict_shape():
    out = render_mermaid({"triads": [{"subject": "A", "predicate": "r", "object": "B"}]})
    assert "-->|r|" in out


def test_pipe_in_predicate_is_sanitized():
    out = render_mermaid([{"subject": "A", "predicate": "a|b", "object": "B"}])
    assert "|a b|" in out and "a|b" not in out.replace("-->", "")
