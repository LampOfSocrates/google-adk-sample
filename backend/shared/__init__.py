"""Shared infrastructure reused by every app in this repo.

Importing rule of thumb:
    backend/<app>/agent.py  ->  from backend.shared.model import get_model
The repo root must be on sys.path for `import backend.shared` to resolve. That happens
automatically when you run `pytest` (see conftest.py) or `adk web backend/` from the
repo root.
"""
