"""Generate a *dated* synthetic risk report into the weekly samples corpus.

Built to run on a schedule (Windows Task Scheduler, every Friday) so that
tests/pdf/samples/ accumulates one report per week:

    risk_report_2026-06-05.pdf   (+ risk_report_2026-06-05.golden.json)

The RNG seed is DERIVED FROM THE DATE (YYYYMMDD), so each week's report differs
but any given date is reproducible. scripts/pdf_to_duckdb.py then ingests this
corpus into a local DuckDB for the pdf_insight SQL mode.

    python scripts/weekly_report.py                 # this week's Friday
    python scripts/weekly_report.py --date 2026-06-05
    python scripts/weekly_report.py --backfill 6    # seed the corpus: last 6 Fridays

Register the Friday schedule (PowerShell, run once):

    $py = "C:\\Users\\soura\\code\\2026\\google-adk-sample\\.venv\\Scripts\\python.exe"
    $wd = "C:\\Users\\soura\\code\\2026\\google-adk-sample"
    $action  = New-ScheduledTaskAction -Execute $py -Argument "scripts\\weekly_report.py" -WorkingDirectory $wd
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At 6pm
    Register-ScheduledTask -TaskName "ADK weekly risk report" -Action $action -Trigger $trigger
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.pdf_creator import create_test_pdf  # noqa: E402

SAMPLES_DIR = os.path.join("tests", "pdf", "samples")


def most_recent_friday(today: dt.date | None = None) -> dt.date:
    """The Friday on or before `today` (Mon=0 .. Fri=4 .. Sun=6)."""
    today = today or dt.date.today()
    return today - dt.timedelta(days=(today.weekday() - 4) % 7)


def seed_for(date: dt.date) -> int:
    """Date -> deterministic seed. 2026-06-05 -> 20260605."""
    return int(date.strftime("%Y%m%d"))


def generate_for(date: dt.date, out_dir: str = SAMPLES_DIR) -> str:
    """Generate one dated report (+ golden) and return the PDF path."""
    stamp = date.isoformat()
    pdf_path = os.path.join(out_dir, f"risk_report_{stamp}.pdf")
    golden_path = os.path.join(out_dir, f"risk_report_{stamp}.golden.json")
    # Ruled style: pdfplumber extracts ruled tables exactly (the corpus must parse
    # cleanly for the DuckDB ingestion). See scripts/pdf_creator.py for why.
    create_test_pdf(
        pdf_path, pages=4, tables=16, seed=seed_for(date),
        style="ruled", golden_path=golden_path,
    )
    return pdf_path


def _main() -> None:
    p = argparse.ArgumentParser(description="Generate dated weekly risk report(s).")
    p.add_argument("--date", help="report date YYYY-MM-DD (default: this week's Friday)")
    p.add_argument("--backfill", type=int, default=0,
                   help="also generate the N most recent Fridays before --date")
    p.add_argument("--dir", default=SAMPLES_DIR, help="output corpus folder")
    a = p.parse_args()

    base = dt.date.fromisoformat(a.date) if a.date else most_recent_friday()
    dates = [base - dt.timedelta(weeks=k) for k in range(max(1, a.backfill))]
    for d in sorted(dates):
        path = generate_for(d, a.dir)
        print(f"wrote {path}  (seed={seed_for(d)})")
    print(f"{len(dates)} report(s) in {a.dir}")


if __name__ == "__main__":
    _main()
