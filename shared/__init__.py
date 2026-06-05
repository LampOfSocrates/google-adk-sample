"""Shared infrastructure reused by every app in this repo.

Importing rule of thumb:
    apps/<app>/agent.py  ->  from shared.model import get_model
The repo root must be on sys.path for `import shared` to resolve. That happens
automatically when you run `pytest` (see conftest.py) or `adk web apps/` from the
repo root.
"""
