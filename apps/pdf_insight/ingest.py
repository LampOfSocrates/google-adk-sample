"""Upload handler: ingest one PDF into BOTH backends.

This is the single place that defines what "uploading a PDF" means for the app,
shared by the coordinator (a new active PDF on a turn) and the Streamlit UI (a
file_uploader event):

  * SQLite  — the per-document store holds the LATEST PDF only, so we (re)ingest,
    replacing the active db and pointing session state at it.
  * Corpus  — the multi-report store GROWS, so we APPEND this report (idempotent
    per report_date). Whichever engine is configured (DuckDB today, Postgres
    later) is resolved by get_corpus_store; an unimplemented engine is reported,
    not fatal.

Returns a per-backend summary so callers (UI) can show what landed; never raises
on a corpus engine that can't ingest yet.
"""
from __future__ import annotations

from . import storage
from .stores import SQLiteStore, get_corpus_store


def ingest_pdf_everywhere(pdf_path: str, state: dict | None = None) -> dict:
    """Ingest `pdf_path` into the per-document SQLite (replace) and the corpus
    (append). Mutates `state` so the mode agents reuse the result this session."""
    state = state if state is not None else {}
    summary: dict = {"pdf": pdf_path}

    # SQLite: latest-PDF-only. Replace the active per-document db.
    sqlite_store = SQLiteStore(storage.sqlite_dsn(pdf_path, state))
    summary["sqlite"] = sqlite_store.ingest_pdf(pdf_path)
    state["db_path"] = sqlite_store.db_path
    state["db_source_pdf"] = pdf_path

    # Corpus: append this report so the corpus grows richer with each upload.
    try:
        summary["corpus"] = get_corpus_store(state).ingest_pdf(pdf_path)
    except NotImplementedError as e:  # e.g. Postgres backend not implemented yet
        summary["corpus"] = {"status": "skipped", "error_message": str(e)}

    state["ingested_pdf"] = pdf_path
    return summary
