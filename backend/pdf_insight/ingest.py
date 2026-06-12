"""Upload handler: ingest one PDF into both backends.

The single definition of what "uploading a PDF" means, shared by the coordinator
and the Streamlit UI. SQLite holds the latest PDF only (replace); the corpus grows
(append, idempotent per report_date). A corpus engine that can't ingest yet is
reported, not fatal. Returns a per-backend summary.
"""
from __future__ import annotations

from . import storage
from .stores import SQLiteStore, get_corpus_store


def ingest_pdf_everywhere(pdf_path: str, state: dict | None = None) -> dict:
    """Ingest `pdf_path` into SQLite (replace) and the corpus (append).

    Mutates `state` so the mode agents reuse the result this session.
    """
    state = state if state is not None else {}
    summary: dict = {"pdf": pdf_path}

    # SQLite: latest-PDF-only, so replace the active per-document db.
    sqlite_store = SQLiteStore(storage.sqlite_dsn(pdf_path, state))
    summary["sqlite"] = sqlite_store.ingest_pdf(pdf_path)
    state["db_path"] = sqlite_store.db_path
    state["db_source_pdf"] = pdf_path

    # Corpus: append so it grows with each upload.
    try:
        summary["corpus"] = get_corpus_store(state).ingest_pdf(pdf_path)
    except NotImplementedError as e:  # e.g. Postgres backend not done yet
        summary["corpus"] = {"status": "skipped", "error_message": str(e)}

    state["ingested_pdf"] = pdf_path
    return summary
