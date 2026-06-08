# pdf_insight — test-quality plan

**Framing.** The suite proves the agent *graph runs* offline; it barely proves the
agent *answers correctly*. Two assets make that cheap to fix now:
`tests/fixtures/risk_report.pdf` + `risk_report.golden.json` (known-truth data) and
`shared/mock_pdf_llm.py` (`MockPdfLlm`, golden-accurate offline answers). Every gap
below is closable with tooling that already exists.

Three themes: **coverage** (hit the dead branches), **mode matrix** (all five modes,
end to end), **corpus DB** (the new DuckDB trust boundary + cross-week correctness).

## Gaps, by severity

**1 — Corpus mode (`LLM_QUERIES_CORPUS`) had zero tests. Biggest hole.**
The corpus path (`run_corpus_sql` over DuckDB) is a SQL trust boundary on
model-written SQL — `SELECT`/`WITH`-only check, multi-statement rejection,
write/DDL blocklist, `read_only` connection, missing-DB error path, `jsonable`
date/Decimal coercion — and `QUERY_CORPUS` wasn't in the phase-4 routing matrix.
For a guard on model-written SQL this is where bugs hide.
- Boundary tests (no LLM, call the tool directly): `DELETE`/`DROP`/`ATTACH`/`COPY`
  rejected; `SELECT 1; SELECT 2` rejected; `SELECT` / `WITH … SELECT` allowed;
  missing-DB → clean error dict, not an exception; a `DATE` column round-trips via
  `jsonable` as an ISO string.
- Since the `SqlStore` refactor, `run_sql` (SQLite) and `run_corpus_sql` (DuckDB)
  share ONE guard — `validate_select` in `stores/base.py` (single-statement +
  leading-`SELECT`/`WITH` + write/DDL blocklist together). Pin it so a future edit
  can't silently weaken it for either backend. (The blocklist deliberately allows
  the read-only `REPLACE()` *function*; regression-test that `REPLACE()` is allowed
  while `REPLACE INTO` / `INSERT OR REPLACE` stay rejected by the leading-SELECT +
  single-statement rules.)

**2 — Corpus DB correctness was unverified — the whole reason DuckDB exists.**
The value proposition is *cross-week* questions; nothing checked they're right.
Against a small fixed temp corpus (2–3 dated reports ingested), assert vs golden:
per-week `SUM(vega_k) WHERE NOT is_total` == that week's `golden.facts.totals.vega`;
a trend query returns one row per `report_date` in order; `is_total` actually
excludes subtotals (sum-with == 2× sum-without, since the Total row duplicates the
sum); the `pdf_tables` registry maps index→title for all 16 tables.

**3 — One happy-path PDF; error branches never fire.**
No malformed / encrypted / zero-table fixture, so the `except` paths in
`modes/pdfpart.py` and `modes/text2sql.py` ("Could not read/ingest …"), `extract_tables`'s
error dict, and the "No db_path" guard are dead in tests. Add a broken-PDF (and a
no-tables) fixture; assert each surfaces a graceful error string, not a traceback.

**4 — The golden file exists but nothing asserted against it.**
`risk_report.pdf` is now referenced by phase 2, but `risk_report.golden.json` isn't.
Pair it with `MockPdfLlm` to turn wiring-deep checks into **correctness-deep** ones:
tables-as-text → "total vega" = `6,384`, "net delta" = `9,152`, "most vega" =
`Americas`; SQL mode → same answers via SQLite (guards the comma-coercion bug where
`"2,765"` must not sum as `2`). Assert *numbers*, not just `"Table 0" in answer`.

**5 — Integration assertions are wiring-deep by design — keep, don't over-trust.**
Phase 4's `"Table 0" in answer` / `"db_path" in state` checks verify routing and
plumbing under a dumb mock — correct, but silent on answer quality. The correctness
layer (gap 4) sits *alongside* them. Green there ≠ "the agent answers correctly."

**6 — Mode matrix incomplete.**
The routing test should cover all five modes pinned via `mode:` directive:
`ALL_TABLES_AS_TEXT`, `SOME_TABLES_AS_TEXT`, `MAKES_SQL_FROM_CHAT`, `QUERIES_CORPUS`,
`GETS_PDF_BYTES` (assert the guarded gemini-only placeholder on mock), plus `auto`.

**7 — Untested branches in the refactored code.**
Per-request index override (`_parse_table_indices` + `select` override in
`modes/pdfpart.py`), multi-index select (`[0,2]`), `_resolve_pdf_path` precedence
(message path > state > env > default), `extract_tables`'s error dict. Cheap
table-driven units.

## Test files
- `tests/pdf/conftest.py` — `run_agent` fixture (one-shot agent → final text).
- `tests/pdf/test_duckdb_tools.py` — corpus guards + `_jsonable` + cross-week
  correctness vs golden (gaps 1, 2). No LLM.
- `tests/pdf/test_golden_answers.py` — `MockPdfLlm` correctness across text / SQL /
  corpus modes (gap 4).
- *(follow-ups)* extend `test_phase4_*` to the full 5-mode matrix + a broken-PDF
  fixture (gaps 3, 6); `test_units.py` for the leftover branches (gap 7).

## Status
**All gaps closed.**
- 1, 2, 4 — `test_duckdb_tools.py` (corpus guards + cross-week correctness) and
  `test_golden_answers.py` (MockPdfLlm correctness across text/SQL/corpus).
- 3 — `test_errors.py`: malformed-PDF fires the tool error dict + the tables/sql
  "Could not read/ingest" branches.
- 6 — `test_phase4_agent.py` extended to the full 5-mode matrix (added
  `SOME_TABLES_AS_TEXT` and `QUERY_CORPUS` routing).
- 7 — `test_units.py`: index parsing, `_resolve_pdf_path` precedence, single/multi
  index select.

Bug found + fixed while testing: `text2sql.build()`/`corpus.build()` reused module-level
agent singletons, so a second `build()` hit a parent-conflict `ValidationError` —
both now construct fresh agents per call, honoring the registry contract. Also
`run_sql` no longer blocklists the read-only `REPLACE()` function (the SELECT-prefix
+ single-statement guards already block write statements).

Offline suite: 101 passed (`pytest -m "not live" tests/pdf`).
