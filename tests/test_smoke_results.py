"""Assert the canned live-smoke results are all green.

`tests/smoke-results/<backend>.txt` is the committed evidence from
`python scripts/smoke.py <backend>` against the real providers. This test makes
NO API calls — it just guards that the checked-in artifacts show every agent
passing for each provider, so a regression (or a failing run committed by
mistake) trips the offline suite.

Refresh the artifacts by re-running the smoke script for that backend.
"""
import glob
import os
import re

import pytest

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "smoke-results")
EXPECTED_BACKENDS = {"gemini", "openai", "deepseek", "bedrock"}

_SUMMARY = re.compile(r"^summary:\s*(\d+)\s*/\s*(\d+)\s+PASS", re.MULTILINE)


def _result_files():
    return sorted(glob.glob(os.path.join(RESULTS_DIR, "*.txt")))


def test_expected_providers_have_results():
    have = {os.path.splitext(os.path.basename(p))[0] for p in _result_files()}
    missing = EXPECTED_BACKENDS - have
    assert not missing, f"no smoke-results for: {sorted(missing)}"


@pytest.mark.parametrize(
    "path", _result_files(), ids=lambda p: os.path.basename(p)
)
def test_smoke_result_all_pass(path):
    text = open(path, encoding="utf-8").read()
    m = _SUMMARY.search(text)
    assert m, f"{os.path.basename(path)}: no 'summary: N/M PASS' line"
    passed, total = int(m.group(1)), int(m.group(2))
    assert total >= 3, f"{os.path.basename(path)}: expected >=3 agents, got {total}"
    assert passed == total, f"{os.path.basename(path)}: only {passed}/{total} passed"
    assert "[FAIL]" not in text, f"{os.path.basename(path)}: contains a [FAIL] entry"
