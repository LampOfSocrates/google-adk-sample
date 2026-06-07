# pdf_insight — test-quality plan

**Framing.** The suite proves the agent *graph runs* offline; it barely proves the
agent *answers correctly*. Two assets make that cheap to fix now:
`tests/fixtures/risk_report.pdf` + `risk_report.golden.json` (known-truth data) and
`shared/mock_pdf_llm.py` (`MockPdfLlm`, golden-accurate offline answers). Every gap
below is closable with tooling that already exists.

Three themes: **coverage** (hit the dead branches), **mode matrix** (all five modes,
end to end), **stash DB** (the new DuckDB trust boundary + cross-week correctness).

## Gaps, by severity

**1 — Stash mode (`LLM_QUERIES_STASH`) had zero tests. Biggest hole.**
`duckdb_tools.py` adds a *second* SQL trust boundary (`run_stash_sql`) with its own
guards — `_FORBIDDEN` blocklist, `SELECT`/`WITH`-only check, multi-statement
rejection, `read_only` connection, missing-DB error path, `_jsonable` date/Decimal
coercion — and `QUERY_STASH` isn't in the phase-4 routing matrix. For a guard on
model-written SQL this is where bugs hide.
- Boundary tests (no LLM, call the tool directly): `DELETE`/`DROP`/`ATTACH`/`COPY`
  rejected; `SELECT 1; SELECT 2` rejected; `SELECT` / `WITH … SELECT` allowed;
  missing-DB → clean error dict, not an exception; a `DATE` column round-trips via
  `_jsonable` as an ISO string.
- The two boundaries differ in philosophy — `run_stash_sql` leans on keyword
  blocklisting, `run_sql` on the `SELECT`-prefix + single-statement invariant. Pin
  **both** so a future edit can't silently weaken either. (`run_sql` was narrowed to
  stop blocking the read-only `REPLACE()` *function*; regression-test that `REPLACE()`
  is allowed while `REPLACE INTO` / `INSERT OR REPLACE` stay rejected.)

**2 — Stash DB correctness was unverified — the whole reason DuckDB exists.**
The value proposition is *cross-week* questions; nothing checked they're right.
Against a small fixed temp stash (2–3 dated reports ingested), assert vs golden:
per-week `SUM(vega_k) WHERE NOT is_total` == that week's `golden.facts.totals.vega`;
a trend query returns one row per `report_date` in order; `is_total` actually
excludes subtotals (sum-with == 2× sum-without, since the Total row duplicates the
sum); the `pdf_tables` registry maps index→title for all 16 tables.

**3 — One happy-path PDF; error branches never fire.**
No malformed / encrypted / zero-table fixture, so the `except` paths in
`modes/tables.py` and `modes/sql.py` ("Could not read/ingest …"), `extract_tables`'s
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
`ALL_TABLES_AS_TEXT`, `SOME_TABLES_AS_TEXT`, `MAKES_SQL_FROM_CHAT`, `QUERIES_STASH`,
`GETS_PDF_BYTES` (assert the guarded gemini-only placeholder on mock), plus `auto`.

**7 — Untested branches in the refactored code.**
Per-request index override (`_parse_table_indices` + `select` override in
`modes/tables.py`), multi-index select (`[0,2]`), `_resolve_pdf_path` precedence
(message path > state > env > default), `extract_tables`'s error dict. Cheap
table-driven units.

## Test files
- `tests/pdf/conftest.py` — `run_agent` fixture (one-shot agent → final text).
- `tests/pdf/test_duckdb_tools.py` — stash guards + `_jsonable` + cross-week
  correctness vs golden (gaps 1, 2). No LLM.
- `tests/pdf/test_golden_answers.py` — `MockPdfLlm` correctness across text / SQL /
  stash modes (gap 4).
- *(follow-ups)* extend `test_phase4_*` to the full 5-mode matrix + a broken-PDF
  fixture (gaps 3, 6); `test_units.py` for the leftover branches (gap 7).

## Status
Gaps 1, 2, 4 implemented in the first pass (highest severity + the golden-correctness
win unlocked by `MockPdfLlm`). Gaps 3, 6, 7 remain.
