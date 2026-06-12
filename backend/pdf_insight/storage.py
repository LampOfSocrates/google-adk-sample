"""Where the SQL backends read and write — one place, one rule.

Both SQL modes (SQLite per-doc, DuckDB corpus) resolve their storage here through
the same precedence:

    session-state override  >  env var  >  persistent default

These are DSNs, not file paths: today they're files under data/, but the same
resolution returns a connection URL once a backend is a server (see postgres_dsn).
Everything defaults under PDF_DATA_DIR (./data) so it all relocates with one env var.
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

    Only the directory is configurable; the filename is derived from the PDF so a
    mid-session doc switch can't answer from the previous doc's db. Persistent (not
    the OS temp dir) so re-runs reuse the ingest.

        state['sqlite_dir']  >  PDF_SQLITE_DIR  >  <data_dir>/sqlite
    """
    sqlite_dir = _resolve(state, "sqlite_dir", "PDF_SQLITE_DIR",
                          os.path.join(_data_dir(), "sqlite"))
    return os.path.join(sqlite_dir, os.path.basename(pdf_path) + ".sqlite")


def duckdb_dsn(state: dict | None = None) -> str:
    """The whole-corpus DuckDB database — one corpus across all reports.

        state['corpus_db']  >  PDF_CORPUS_DB  >  <data_dir>/pdf_corpus.duckdb
    """
    return _resolve(state, "corpus_db", "PDF_CORPUS_DB",
                    os.path.join(_data_dir(), "pdf_corpus.duckdb"))


def postgres_dsn(state: dict | None = None) -> str:
    """Corpus served by Postgres — like duckdb_dsn but a connection URL, not a file.

        state['pg_dsn']  >  PDF_PG_DSN  >  postgresql://localhost:5432/pdf_insight
    """
    return _resolve(state, "pg_dsn", "PDF_PG_DSN",
                    "postgresql://localhost:5432/pdf_insight")
