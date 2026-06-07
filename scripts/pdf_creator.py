"""Generate a synthetic, multi-table test PDF for the pdf_insight test suite.

WHY this exists: pdf_insight parses tables out of PDFs (pdfplumber), ingests them
into SQLite, and answers questions / runs Text2SQL over them. To test that end to
end we need PDFs whose *true* contents we know exactly. This module builds one
seeded synthetic dataset and renders it as up to 16 aggregation tables across up
to 4 pages, then emits a `golden_answers.json` so tests can assert against ground
truth instead of eyeballing.

WRITER vs READER: this uses reportlab to *write*; the app uses pdfplumber to
*read*. They share no code, so a bug in one can't mask a bug in the other.

DOMAIN: a multi-region derivatives desk. The base fact table is position-level
risk; every printed table is an aggregation of it. Facts are the Greeks
(delta/gamma/vega/theta/rho) plus notional/var/pnl. Dimensions are region, desk,
asset_class, underlying, book, currency, tenor_bucket. Values are deliberately
*signed* (long/short delta), theta is mostly negative (long options bleed), so the
SQL/answer tests exercise SUM/AVG/ABS over signed data, not just happy-path counts.

STYLES: tables render either "ruled" (gridlines -> pdfplumber strategy="lines") or
"borderless" (whitespace-aligned -> strategy="text"). The fixture mixes both on
purpose, so the suite covers the app's table-detection strategy switch. Each
table's style is recorded in the golden file so the test picks the right strategy.
(Caveat: pdfplumber's text strategy fragments borderless cells, so borderless
tables aren't golden-verifiable — the committed fixture is all-ruled; borderless
is for a degradation/robustness test only.)

16-TABLE LAYOUT (index -> table, grouped by page; the `--tables N` flag keeps the
first N, `--pages` re-balances them). Builders live in build_tables():

  Page 1 — overview
    0  Risk Summary by Region           GROUP BY region        (full Greeks + Total)
    1  Risk Summary by Asset Class      GROUP BY asset_class    (full Greeks + Total)
    2  Risk Summary by Desk             GROUP BY desk           (full Greeks + Total)
    3  Portfolio KPI Totals             grand totals            (Metric | Value)
  Page 2 — sensitivities (one cross-tab per Greek)
    4  Vega by Region x Asset Class     pivot, measure=vega
    5  Delta by Desk x Tenor            pivot, measure=delta
    6  Gamma by Asset Class x Tenor     pivot, measure=gamma
    7  Rho by Region x Currency         pivot, measure=rho
  Page 3 — rankings & term structure
    8  Top 8 Underlyings by |Vega|      top-N, ORDER BY abs(vega) DESC LIMIT 8
    9  Top 8 Books by 1d VaR            top-N, ORDER BY var DESC LIMIT 8
    10 Greeks by Tenor Bucket           GROUP BY tenor          (term structure)
    11 Risk by Region x Asset Class     two-level GROUP BY       (tall detail)
  Page 4 — time & detail
    12 Daily Risk Trend                 GROUP BY date           (time series)
    13 Vega Ladder by Tenor (SPX)       filtered GROUP BY tenor  (single underlying)
    14 Risk Summary by Currency         GROUP BY currency       (full Greeks + Total)
    15 Stress P&L by Scenario           derived from aggregate Greeks (spot/vol/rate)

CLI:
    python scripts/pdf_creator.py --out tests/fixtures/risk_report.pdf \
        --golden tests/fixtures/risk_report.golden.json --pages 4 --tables 16 --seed 42
"""
from __future__ import annotations

import argparse
import json
import os
import random
from dataclasses import dataclass, field
from typing import Callable

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter

# Make output byte-reproducible: pin reportlab's embedded creation timestamp + doc
# ID so the same seed yields identical PDF bytes (committed fixture won't churn).
rl_config.invariant = 1
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table as RLTable,
    TableStyle,
)

# --------------------------------------------------------------- domain config ---
REGIONS = ["Americas", "EMEA", "APAC"]
REGION_WEIGHTS = [5, 4, 3]                       # Americas is the biggest book
REGION_CCY = {"Americas": ["USD"], "EMEA": ["EUR", "GBP"], "APAC": ["JPY", "USD"]}
ASSET_CLASSES = ["Equity", "Rates", "FX", "Credit", "Commodity"]
ASSET_WEIGHTS = [5, 4, 3, 2, 2]
UNDERLYINGS = {
    "Equity": ["SPX", "ESTOXX", "NKY", "FTSE"],
    "Rates": ["UST10Y", "Bund", "JGB10Y", "Gilt"],
    "FX": ["EUR/USD", "USD/JPY", "GBP/USD"],
    "Credit": ["CDX-IG", "iTraxx-Main"],
    "Commodity": ["WTI", "Gold", "Brent"],
}
DESK_FOR_ASSET = {
    "Equity": ["Flow Equity", "Exotics"],
    "Rates": ["Rates"],
    "FX": ["FX Options"],
    "Credit": ["Credit"],
    "Commodity": ["Exotics"],
}
TENORS = ["0-1M", "1-3M", "3-6M", "6-12M", "1Y+"]
# Ten consecutive weekdays (2025-06-02 Mon .. 2025-06-13 Fri) for the time series.
TRADING_DATES = [f"2025-06-{d:02d}" for d in (2, 3, 4, 5, 6, 9, 10, 11, 12, 13)]

# Greek/risk facts carried by every position (all in $k except notional in $m).
MEAS = ["notional", "delta", "gamma", "vega", "theta", "rho", "var", "pnl"]
GREEKS = ["delta", "gamma", "vega", "theta", "rho"]


# ------------------------------------------------------------------- dataset ---
def build_dataset(seed: int, n_positions: int) -> list[dict]:
    """Build the seeded position-level fact table. Pure: same seed -> same rows."""
    rng = random.Random(seed)
    rows: list[dict] = []
    for i in range(n_positions):
        region = rng.choices(REGIONS, weights=REGION_WEIGHTS)[0]
        asset = rng.choices(ASSET_CLASSES, weights=ASSET_WEIGHTS)[0]
        underlying = rng.choice(UNDERLYINGS[asset])
        desk = rng.choice(DESK_FOR_ASSET[asset])
        currency = rng.choice(REGION_CCY[region])
        tenor = rng.choice(TENORS)
        book = f"{asset[:3].upper()}-{desk.split()[0][:3].upper()}-{rng.randint(1, 4)}"
        rows.append({
            "position_id": f"P{i:05d}",
            "date": rng.choice(TRADING_DATES),
            "region": region,
            "desk": desk,
            "asset_class": asset,
            "underlying": underlying,
            "book": book,
            "currency": currency,
            "tenor_bucket": tenor,
            # Signed Greeks. theta skewed negative; vega mostly long-vol.
            "notional": rng.randint(1, 40),       # $m
            "delta": rng.randint(-40, 55),        # $k
            "gamma": rng.randint(0, 12),          # $k per 1% move
            "vega": rng.randint(-4, 16),          # $k per vol pt
            "theta": rng.randint(-5, 1),          # $k / day (mostly negative)
            "rho": rng.randint(-3, 3),            # $k per 1% rate
            "var": rng.randint(0, 9),             # $k 1-day (summed; see note below)
            "pnl": rng.randint(-8, 9),            # $k daily P&L
        })
    return rows


# NOTE on VaR: real VaR is sub-additive (diversification), so summing it overstates
# portfolio risk. We SUM it here anyway to keep the golden file exactly reproducible
# by a plain SQL SUM; realism is traded for testability. Flagged so nobody "fixes" it.


# --------------------------------------------------------------- aggregation ---
def agg_by(rows: list[dict], keyfn: Callable[[dict], tuple], measures=MEAS) -> dict:
    """Group rows by keyfn, summing each measure. Also counts rows as '__count'."""
    out: dict = {}
    for r in rows:
        k = keyfn(r)
        bucket = out.get(k)
        if bucket is None:
            bucket = out[k] = {"__count": 0, **{m: 0 for m in measures}}
        bucket["__count"] += 1
        for m in measures:
            bucket[m] += r[m]
    return out


def _fmt(v) -> str:
    """Thousands-separated; one decimal only if genuinely fractional."""
    if isinstance(v, float) and not float(v).is_integer():
        return f"{v:,.1f}"
    return f"{int(round(v)):,}"


@dataclass
class Table:
    index: int
    title: str
    columns: list[str]
    rows: list[list[str]]          # formatted cell strings, total row last if any
    style: str                      # "ruled" | "borderless"
    has_total: bool = False


# ---- table builders ---------------------------------------------------------
_SUMMARY_COLS = ["Pos", "Notional($m)", "Delta($k)", "Gamma($k)", "Vega($k)",
                 "Theta($k/d)", "Rho($k)", "VaR($k)", "PnL($k)"]


def _summary_row(agg_bucket: dict) -> list[str]:
    b = agg_bucket
    return [_fmt(b["__count"])] + [_fmt(b[m]) for m in MEAS]


def summary_table(index, title, rows, key, key_label, order, style) -> Table:
    """A GROUP BY <key> summary carrying the FULL Greek set + a Total row."""
    grouped = agg_by(rows, lambda r: r[key])
    ordered = [k for k in order if k in grouped] + \
              [k for k in grouped if k not in order]
    out_rows = [[k] + _summary_row(grouped[k]) for k in ordered]
    total = {"__count": sum(g["__count"] for g in grouped.values()),
             **{m: sum(g[m] for g in grouped.values()) for m in MEAS}}
    out_rows.append(["Total"] + _summary_row(total))
    return Table(index, title, [key_label] + _SUMMARY_COLS, out_rows, style, has_total=True)


def multilevel_table(index, title, rows, k1, k2, l1, l2, o1, o2, style) -> Table:
    """Two-level GROUP BY (k1, k2) over the Greeks + a Total row (tall detail)."""
    grouped = agg_by(rows, lambda r: (r[k1], r[k2]))
    cols = [l1, l2] + ["Delta($k)", "Gamma($k)", "Vega($k)", "Theta($k/d)", "Rho($k)"]
    out_rows = []
    for a in o1:
        for b in o2:
            g = grouped.get((a, b))
            if g:
                out_rows.append([a, b] + [_fmt(g[m]) for m in GREEKS])
    total = {m: sum(g[m] for g in grouped.values()) for m in GREEKS}
    out_rows.append(["Total", ""] + [_fmt(total[m]) for m in GREEKS])
    return Table(index, title, cols, out_rows, style, has_total=True)


def pivot_table(index, title, rows, row_key, row_order, col_key, col_order,
                measure, row_label, style) -> Table:
    """Cross-tab of one measure: row_key down, col_key across, with margins."""
    cells: dict = {}
    for r in rows:
        cells.setdefault((r[row_key], r[col_key]), 0)
        cells[(r[row_key], r[col_key])] += r[measure]
    cols = [row_label] + list(col_order) + ["Total"]
    out_rows = []
    col_totals = {c: 0 for c in col_order}
    grand = 0
    for rv in row_order:
        vals = [cells.get((rv, c), 0) for c in col_order]
        rtot = sum(vals)
        for c, v in zip(col_order, vals):
            col_totals[c] += v
        grand += rtot
        out_rows.append([rv] + [_fmt(v) for v in vals] + [_fmt(rtot)])
    out_rows.append(["Total"] + [_fmt(col_totals[c]) for c in col_order] + [_fmt(grand)])
    return Table(index, title, cols, out_rows, style, has_total=True)


def topn_table(index, title, rows, key, key_label, measure, n, by_abs, style) -> Table:
    """Top-N <key> ranked by <measure> (or |measure|), carrying full Greeks + VaR."""
    grouped = agg_by(rows, lambda r: r[key])
    keyer = (lambda kv: abs(kv[1][measure])) if by_abs else (lambda kv: kv[1][measure])
    ranked = sorted(grouped.items(), key=keyer, reverse=True)[:n]
    cols = ["Rank", key_label, "Delta($k)", "Gamma($k)", "Vega($k)",
            "Theta($k/d)", "Rho($k)", "VaR($k)"]
    out_rows = [
        [str(i + 1), k] + [_fmt(g[m]) for m in GREEKS] + [_fmt(g["var"])]
        for i, (k, g) in enumerate(ranked)
    ]
    return Table(index, title, cols, out_rows, style)


def kpi_table(index, title, rows, style) -> Table:
    """Portfolio KPI totals as a Metric | Value table."""
    tot = {m: sum(r[m] for r in rows) for m in MEAS}
    data = [
        ("Positions", _fmt(len(rows))),
        ("Notional ($m)", _fmt(tot["notional"])),
        ("Net Delta ($k)", _fmt(tot["delta"])),
        ("Gamma ($k)", _fmt(tot["gamma"])),
        ("Vega ($k)", _fmt(tot["vega"])),
        ("Theta ($k/day)", _fmt(tot["theta"])),
        ("Rho ($k)", _fmt(tot["rho"])),
        ("1d VaR ($k)", _fmt(tot["var"])),
        ("Daily P&L ($k)", _fmt(tot["pnl"])),
    ]
    return Table(index, title, ["Metric", "Value"], [list(d) for d in data], style)


def daily_trend_table(index, title, rows, style) -> Table:
    grouped = agg_by(rows, lambda r: r["date"])
    cols = ["Date", "Pos", "Notional($m)", "Vega($k)", "VaR($k)", "PnL($k)"]
    out_rows = [
        [d, _fmt(grouped[d]["__count"]), _fmt(grouped[d]["notional"]),
         _fmt(grouped[d]["vega"]), _fmt(grouped[d]["var"]), _fmt(grouped[d]["pnl"])]
        for d in sorted(grouped)
    ]
    return Table(index, title, cols, out_rows, style)


def vega_ladder_table(index, title, rows, underlying, style) -> Table:
    sub = [r for r in rows if r["underlying"] == underlying]
    grouped = agg_by(sub, lambda r: r["tenor_bucket"])
    cols = ["Tenor", "Pos", "Vega($k)", "Gamma($k)", "Delta($k)"]
    out_rows = [
        [t, _fmt(grouped[t]["__count"]), _fmt(grouped[t]["vega"]),
         _fmt(grouped[t]["gamma"]), _fmt(grouped[t]["delta"])]
        for t in TENORS if t in grouped
    ]
    return Table(index, f"{title} ({underlying})", cols, out_rows, style)


def scenario_table(index, title, rows, style) -> Table:
    """First-order stress P&L derived from the portfolio's aggregate Greeks.

    pnl = delta*spot_shock(%) + vega*vol_shock(pt) + rho*rate_shock(%). gamma
    convexity is intentionally omitted to keep the numbers cleanly reproducible.
    """
    d = sum(r["delta"] for r in rows)
    v = sum(r["vega"] for r in rows)
    rho = sum(r["rho"] for r in rows)
    scen = [
        ("Spot +5%", d * 5),
        ("Spot -5%", -d * 5),
        ("Vol +3pt", v * 3),
        ("Vol -3pt", -v * 3),
        ("Rates +50bp", rho * 0.5),
        ("Rates -50bp", -rho * 0.5),
    ]
    out_rows = [[name, _fmt(val)] for name, val in scen]
    return Table(index, title, ["Scenario", "P&L Impact($k)"], out_rows, style)


# The canonical 16-table layout (order = page order; first `n` are kept).
def build_tables(rows: list[dict], style_for: Callable[[int], str]) -> list[Table]:
    ccy_order = sorted({r["currency"] for r in rows})
    specs = [
        # page 1 — overview
        lambda i: summary_table(i, "Risk Summary by Region", rows, "region", "Region", REGIONS, style_for(i)),
        lambda i: summary_table(i, "Risk Summary by Asset Class", rows, "asset_class", "Asset Class", ASSET_CLASSES, style_for(i)),
        lambda i: summary_table(i, "Risk Summary by Desk", rows, "desk", "Desk", list(DESK_FOR_ASSET), style_for(i)),
        lambda i: kpi_table(i, "Portfolio KPI Totals", rows, style_for(i)),
        # page 2 — sensitivities (pivots), one per Greek
        lambda i: pivot_table(i, "Vega by Region x Asset Class ($k)", rows, "asset_class", ASSET_CLASSES, "region", REGIONS, "vega", "Asset Class", style_for(i)),
        lambda i: pivot_table(i, "Delta by Desk x Tenor ($k)", rows, "desk", list(DESK_FOR_ASSET), "tenor_bucket", TENORS, "delta", "Desk", style_for(i)),
        lambda i: pivot_table(i, "Gamma by Asset Class x Tenor ($k)", rows, "asset_class", ASSET_CLASSES, "tenor_bucket", TENORS, "gamma", "Asset Class", style_for(i)),
        lambda i: pivot_table(i, "Rho by Region x Currency ($k)", rows, "region", REGIONS, "currency", ccy_order, "rho", "Region", style_for(i)),
        # page 3 — rankings & term structure
        lambda i: topn_table(i, "Top 8 Underlyings by |Vega|", rows, "underlying", "Underlying", "vega", 8, True, style_for(i)),
        lambda i: topn_table(i, "Top 8 Books by 1d VaR", rows, "book", "Book", "var", 8, False, style_for(i)),
        lambda i: summary_table(i, "Greeks by Tenor Bucket", rows, "tenor_bucket", "Tenor", TENORS, style_for(i)),
        lambda i: multilevel_table(i, "Risk by Region x Asset Class", rows, "region", "asset_class", "Region", "Asset Class", REGIONS, ASSET_CLASSES, style_for(i)),
        # page 4 — time & detail
        lambda i: daily_trend_table(i, "Daily Risk Trend", rows, style_for(i)),
        lambda i: vega_ladder_table(i, "Vega Ladder by Tenor", rows, "SPX", style_for(i)),
        lambda i: summary_table(i, "Risk Summary by Currency", rows, "currency", "Currency", ccy_order, style_for(i)),
        lambda i: scenario_table(i, "Stress P&L by Scenario", rows, style_for(i)),
    ]
    return [spec(i) for i, spec in enumerate(specs)]


# ------------------------------------------------------------------ golden ---
def compute_golden(rows: list[dict], tables: list[Table], seed: int) -> dict:
    """Ground-truth facts + the exact rendered tables, for tests to assert on."""
    tot = {m: sum(r[m] for r in rows) for m in MEAS}
    by_region_vega = agg_by(rows, lambda r: r["region"], ["vega", "delta"])
    by_asset_vega = agg_by(rows, lambda r: r["asset_class"], ["vega"])
    by_underlying = agg_by(rows, lambda r: r["underlying"], ["vega"])
    by_desk_var = agg_by(rows, lambda r: r["desk"], ["var"])
    facts = {
        "n_positions": len(rows),
        "totals": {m: tot[m] for m in MEAS},
        "region_max_vega": max(by_region_vega, key=lambda k: by_region_vega[k]["vega"]),
        "asset_class_max_vega": max(by_asset_vega, key=lambda k: by_asset_vega[k]["vega"]),
        "underlying_max_abs_vega": max(by_underlying, key=lambda k: abs(by_underlying[k]["vega"])),
        "desk_max_var": max(by_desk_var, key=lambda k: by_desk_var[k]["var"]),
        "regions_negative_delta": sorted(
            k for k, v in by_region_vega.items() if v["delta"] < 0
        ),
    }
    return {
        "seed": seed,
        "facts": facts,
        "tables": [
            {"index": t.index, "title": t.title, "style": t.style,
             "columns": t.columns, "rows": t.rows, "has_total": t.has_total}
            for t in tables
        ],
    }


# ------------------------------------------------------------------- render ---
def _paginate(tables: list[Table], pages: int) -> list[int]:
    """Assign each table to a page, balancing by estimated height (row count +
    title/header overhead) so a fixed `pages` budget isn't blown by tall tables."""
    weights = [len(t.rows) + 3 for t in tables]   # +3 ≈ title + header + spacer
    total = sum(weights) or 1
    target = total / max(1, pages)
    assign, cum, page = [], 0, 0
    for w in weights:
        assign.append(page)
        cum += w
        if page < pages - 1 and cum >= target * (page + 1):
            page += 1
    return assign


def _render(tables: list[Table], out_path: str, pages: int) -> None:
    doc = SimpleDocTemplate(
        out_path, pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        title="Derivatives Desk Risk Report",
    )
    styles = getSampleStyleSheet()
    h = ParagraphStyle("tbl", parent=styles["Heading4"], spaceAfter=3, spaceBefore=1)
    assign = _paginate(tables, pages)

    story = []
    for n, t in enumerate(tables):
        if n and assign[n] != assign[n - 1]:
            story.append(PageBreak())
        story.append(Paragraph(f"Table {t.index}. {t.title}", h))
        data = [t.columns] + t.rows
        tbl = RLTable(data, hAlign="LEFT")
        tbl.setStyle(_table_style(t))
        story.append(tbl)
        story.append(Spacer(1, 0.12 * inch))
    doc.build(story)


def _table_style(t: Table) -> TableStyle:
    cmds = [
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),     # header
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),                # numeric cols right
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]
    if t.style == "ruled":
        cmds += [
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ]
    else:  # borderless -> no lines; pdfplumber must use strategy="text"
        cmds += [("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.white)]
    if t.has_total:
        cmds += [
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("LINEABOVE", (0, -1), (-1, -1), 0.4, colors.grey if t.style == "ruled" else colors.white),
        ]
    return TableStyle(cmds)


# ------------------------------------------------------------------- public ---
def create_test_pdf(out_path: str, *, pages: int = 4, tables: int = 16,
                    seed: int = 42, style: str = "mixed",
                    positions: int = 1100, golden_path: str | None = None) -> dict:
    """Generate the test PDF (and optionally the golden file). Returns the golden dict.

    Args:
        out_path: where to write the .pdf.
        pages: target page count (tables are split evenly across them).
        tables: how many of the 16 canonical tables to emit (1..16).
        seed: RNG seed -> deterministic, byte-stable output.
        style: "mixed" (alternate), "ruled", or "borderless".
        positions: size of the synthetic base fact table.
        golden_path: if given, write the golden JSON here too.
    """
    tables = max(1, min(16, tables))

    def style_for(i: int) -> str:
        if style in ("ruled", "borderless"):
            return style
        return "ruled" if i % 2 == 0 else "borderless"

    rows = build_dataset(seed, positions)
    all_tables = build_tables(rows, style_for)[:tables]

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    _render(all_tables, out_path, pages)

    golden = compute_golden(rows, all_tables, seed)
    if golden_path:
        os.makedirs(os.path.dirname(os.path.abspath(golden_path)), exist_ok=True)
        with open(golden_path, "w", encoding="utf-8") as f:
            json.dump(golden, f, indent=2)
    return golden


def _main() -> None:
    p = argparse.ArgumentParser(description="Generate a synthetic risk-report test PDF.")
    p.add_argument("--out", default=os.path.join("tests", "fixtures", "risk_report.pdf"))
    p.add_argument("--golden", default=None, help="path to also write golden_answers.json")
    p.add_argument("--pages", type=int, default=4)
    p.add_argument("--tables", type=int, default=16)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--positions", type=int, default=1100)
    p.add_argument("--style", choices=["mixed", "ruled", "borderless"], default="mixed")
    a = p.parse_args()
    golden = create_test_pdf(
        a.out, pages=a.pages, tables=a.tables, seed=a.seed,
        style=a.style, positions=a.positions, golden_path=a.golden,
    )
    n_ruled = sum(1 for t in golden["tables"] if t["style"] == "ruled")
    print(f"Wrote {a.out}: {len(golden['tables'])} tables across {a.pages} page(s) "
          f"({n_ruled} ruled / {len(golden['tables']) - n_ruled} borderless), seed={a.seed}.")
    if a.golden:
        print(f"Wrote golden -> {a.golden}")
    print("Totals:", golden["facts"]["totals"])


if __name__ == "__main__":
    _main()
