"""Where the pdf_insight SQL backends read and write — one place, one rule.

Both answering modes that use SQL — the per-document SQLite mode
(`LLM_MAKES_SQL_FROM_CHAT`) and the whole-corpus DuckDB mode (`LLM_QUERIES_CORPUS`)
— resolve their storage location HERE, through the SAME precedence so the knobs
are predictable and documented in a single spot:

    session-state override  >  environment variable  >  persistent default

Think "data source" (a DSN), not "file path". Today the embedded engines resolve
to a file under data/; the identical three-tier resolution returns a real
connection URL the day a backend is a server (Postgres). Adding that backend is
then ONE resolver here, not a change scattered through the mode agents — see the
`postgres_dsn` sketch at the bottom.

Everything defaults under PDF_DATA_DIR (./data) so all databases live together
and the whole lot relocates with a single env var.
"""
from __future__ import annotations

import os


def _data_dir() -> str:
    """Root for on-disk databases. Override to relocate every backend at once."""
    return os.environ.get("PDF_DATA_DIR", "data")


def _resolve(state, state_key: str, env_key: str, default: str) -> str:
    """The shared precedence every backend follows: state > env > default."""
    return (state or {}).get(state_key) or os.environ.get(env_key) or default


def sqlite_dsn(pdf_path: str, state: dict | None = None) -> str:
    """Per-document SQLite database — one file per source PDF.

    SQLite is the single-document backend, so the *configurable* part is the
    directory; the filename is derived from the PDF so a mid-session document
    switch can never answer from the previous doc's database. Persistent (under
    data/sqlite by default, NOT the OS temp dir) so re-runs reuse the ingest,
    exactly like the DuckDB corpus.

        state['sqlite_dir']  >  PDF_SQLITE_DIR  >  <data_dir>/sqlite
    """
    sqlite_dir = _resolve(state, "sqlite_dir", "PDF_SQLITE_DIR",
                          os.path.join(_data_dir(), "sqlite"))
    return os.path.join(sqlite_dir, os.path.basename(pdf_path) + ".sqlite")


def duckdb_dsn(state: dict | None = None) -> str:
    """The single whole-corpus DuckDB database — one corpus across all reports.

        state['corpus_db']  >  PDF_CORPUS_DB  >  <data_dir>/pdf_corpus.duckdb
    """
    return _resolve(state, "corpus_db", "PDF_CORPUS_DB",
                    os.path.join(_data_dir(), "pdf_corpus.duckdb"))


def postgres_dsn(state: dict | None = None) -> str:
    """The corpus served by Postgres — same shape as duckdb_dsn, but a connection
    URL instead of a file path.

        state['pg_dsn']  >  PDF_PG_DSN  >  postgresql://localhost:5432/pdf_insight
    """
    return _resolve(state, "pg_dsn", "PDF_PG_DSN",
                    "postgresql://localhost:5432/pdf_insight")
