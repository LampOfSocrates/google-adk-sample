"""Backend-test isolation.

`backend/registry.build_root_agent` purges `backend.<app>.*` from sys.modules and
reimports so each (app, backend) gets the backend-correct agent structure. That's
correct at runtime, but inside a single pytest process it leaves *duplicate* class
objects behind — so a later test that does `isinstance(x, SomeStore)` against a
freshly re-imported class can fail against an instance built from the pre-purge
class. This autouse fixture snapshots the `backend.*` modules and restores them
after each server test, containing the purge's effect to the test that caused it.
"""
import sys

import pytest


@pytest.fixture(autouse=True)
def _restore_backend_modules():
    saved = {k: v for k, v in sys.modules.items() if k.startswith("backend.")}
    yield
    for k in [k for k in sys.modules if k.startswith("backend.")]:
        if k not in saved:
            del sys.modules[k]
    sys.modules.update(saved)
