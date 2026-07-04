#!/usr/bin/env python3
"""
show_multiples.py — Price-to-Sales and Price-to-FCF multiples from official reports.

Numbers come from each ticker's `<TICKER>/FINANCIALS.yml`, hand-entered from the
SEC reports under `<TICKER>/quarters/` (see CLAUDE.md for the schema). Only the
live share price is pulled from yfinance; every fundamental is from the filings.

Trailing multiples use a rolling TTM (last four standalone quarters). Cash-flow
lines are stored as reported (year-to-date) and differenced into standalone
quarters, resetting at each fiscal-year Q1. Two FCF flavours are shown:
  - company : OCF − PP&E capex        (matches the FCF companies usually print)
  - strict  : OCF − PP&E − capitalized software  (fully-loaded capex)

Forward multiples use the most recent quarter's revenue guidance and/or your own
estimate (both optional, both entered as low/high ranges):
  - Fwd P/S (FY)    : market cap / full-year revenue
  - Fwd-TTM P/S     : market cap / (last 3 actual quarters + next-quarter revenue)

Tickers without a FINANCIALS.yml fall back to yfinance's own figures, clearly
flagged as such.

Usage:
  uv run show-multiples              # all stocks in TICKERS.yml
  uv run show-multiples ODD BARK     # specific tickers
  uv run show-multiples NVDA         # ad-hoc ticker (yfinance fallback)
"""

import argparse
import csv
import re
from datetime import date

import yaml
import yfinance as yf
from rich.console import Console
from rich.table import Table

from investing.lib import REPO_ROOT
from investing.fetch_prices import fetch_live_price

TICKERS_FILE = REPO_ROOT / "TICKERS.yml"

UNIT_MULT = {"thousands": 1_000, "millions": 1_000_000, "units": 1}

console = Console()


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_stocks() -> list[str]:
    with TICKERS_FILE.open() as f:
        data = yaml.safe_load(f)
    return [s["symbol"] for s in data.get("stocks", [])]


def load_financials(ticker: str) -> dict | None:
    path = REPO_ROOT / ticker / "FINANCIALS.yml"
    if not path.exists():
        return None
    with path.open() as f:
        return yaml.safe_load(f)


def load_closes(ticker: str) -> list[tuple[date, float]] | None:
    """Daily closing prices from prices/daily/<TICKER>.csv, sorted ascending."""
    path = REPO_ROOT / "prices" / "daily" / f"{ticker}.csv"
    if not path.exists():
        return None
    rows = []
    with path.open() as f:
        for r in csv.DictReader(f):
            try:
                rows.append((date.fromisoformat(r["Date"][:10]), float(r["Close"])))
            except (ValueError, KeyError):
                continue
    rows.sort()
    return rows or None


def as_date(v) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        return date.fromisoformat(v[:10])
    return None


def close_on(closes: list[tuple[date, float]] | None, target: date | None) -> float | None:
    """The close on `target`, or the last trading day before it."""
    if not closes or target is None:
        return None
    prior = [c for d, c in closes if d <= target]
    return prior[-1] if prior else None


# --------------------------------------------------------------------------- #
# Fiscal-period parsing
# --------------------------------------------------------------------------- #
def q_num(qid: str) -> int | None:
    m = re.search(r"q([1-4])", qid.lower())
    return int(m.group(1)) if m else None


def fy_token(qid: str) -> str | None:
    """The fiscal-year label — the last run of digits in the id (e.g. q4-fy2026 → 2026)."""
    m = re.findall(r"(\d{4})", qid)
    return m[-1] if m else None


def next_period(qid: str) -> tuple[str, str]:
    """Forward full-year and next-quarter labels relative to the latest reported quarter."""
    fy, qn = int(fy_token(qid)), q_num(qid)
    if qn == 4:
        return f"FY-{fy + 1}", f"Q1-{fy + 1}"
    return f"FY-{fy}", f"Q{qn + 1}-{fy}"


def standalone(quarters: list[dict], idx: int, key: str) -> float | None:
    """Turn a year-to-date cash-flow line into a standalone-quarter value by
    differencing against the previous quarter of the same fiscal year."""
    q = quarters[idx]
    val = q.get(key)
    if val is None:
        return None
    n = q_num(q["id"])
    if n == 1:
        return float(val)
    prev = next(
        (p for p in quarters
         if fy_token(p["id"]) == fy_token(q["id"]) and q_num(p["id"]) == n - 1),
        None,
    )
    if prev is None or prev.get(key) is None:
        return None
    return float(val) - float(prev[key])


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def midpoint(q: dict, prefix: str) -> tuple[float, float] | None:
    lo, hi = q.get(f"{prefix}_low"), q.get(f"{prefix}_high")
    vals = [v for v in (lo, hi) if v is not None]
    return (min(vals), max(vals)) if vals else None


def ttm_at(quarters: list[dict], j: int) -> dict | None:
    """Trailing-twelve-month revenue and cash-flow lines ending at quarter index j."""
    if j < 3:
        return None
    idxs = range(j - 3, j + 1)
    rev = [quarters[k].get("revenue") for k in idxs]
    if any(v is None for v in rev):
        return None
    ocf = [standalone(quarters, k, "ytd_operating_cf") for k in idxs]
    ppe = [standalone(quarters, k, "ytd_capex_ppe") for k in idxs]
    sw = [standalone(quarters, k, "ytd_capex_software") for k in idxs]
    return {
        "rev": sum(rev),
        "ocf": None if None in ocf else sum(ocf),
        "ppe": None if None in ppe else sum(ppe),
        "sw": None if None in sw else sum(sw),
    }


def quarter_col(quarters: list[dict], i: int) -> dict:
    """A single standalone-quarter display column."""
    q = quarters[i]
    ocf = standalone(quarters, i, "ytd_operating_cf")
    ppe = standalone(quarters, i, "ytd_capex_ppe")
    sw = standalone(quarters, i, "ytd_capex_software")
    fcf_co = ocf - ppe if ocf is not None and ppe is not None else None
    fcf_strict = fcf_co - sw if fcf_co is not None and sw is not None else None
    return {
        "id": q["id"], "is_fy": False, "revenue": q.get("revenue"),
        "ocf": ocf, "ppe": ppe, "sw": sw, "fcf_co": fcf_co, "fcf_strict": fcf_strict,
        "shares": q.get("shares_outstanding"),
        "report_date": as_date(q.get("report_date")),
        "ttm": ttm_at(quarters, i),  # trailing basis for this column's multiples
        # full-year guidance / estimate as issued at THIS quarter's report
        "guid_fy": midpoint(q, "guidance_fy_revenue"),
        "guid_fy_withdrawn": bool(q.get("guidance_fy_revenue_withdrawn")),
        "est_fy": midpoint(q, "est_fy_revenue"),
    }


def fy_col(quarters: list[dict], fy: str) -> dict:
    """An aggregated full-fiscal-year display column (needs all four quarters)."""
    qs = [q for q in quarters if fy_token(q["id"]) == fy]
    by_n = {q_num(q["id"]): q for q in qs}
    q4 = by_n.get(4)
    revenue = sum(q["revenue"] for q in qs if q.get("revenue") is not None) or None
    # for a complete year the Q4 year-to-date lines ARE the full-year totals
    ocf = q4.get("ytd_operating_cf") if q4 else None
    ppe = q4.get("ytd_capex_ppe") if q4 else None
    sw = q4.get("ytd_capex_software") if q4 else None
    fcf_co = ocf - ppe if ocf is not None and ppe is not None else None
    fcf_strict = fcf_co - sw if fcf_co is not None and sw is not None else None
    # final full-year guidance/estimate for this FY = last of Q1–Q3 that guided it
    guid = est = None
    for n in (3, 2, 1):
        q = by_n.get(n)
        if q is None:
            continue
        if guid is None and not q.get("guidance_fy_revenue_withdrawn"):
            guid = midpoint(q, "guidance_fy_revenue")
        if est is None:
            est = midpoint(q, "est_fy_revenue")
    return {
        "id": f"FY-{fy}", "is_fy": True, "revenue": revenue,
        "ocf": ocf, "ppe": ppe, "sw": sw, "fcf_co": fcf_co, "fcf_strict": fcf_strict,
        "shares": q4.get("shares_outstanding") if q4 else None,
        "report_date": as_date(q4.get("report_date")) if q4 else None,
        # at year-end the trailing window IS the full year
        "ttm": {"rev": revenue, "ocf": ocf, "ppe": ppe, "sw": sw},
        "guid_fy": guid, "guid_fy_withdrawn": False, "est_fy": est,
    }


def compute_official(ticker: str, data: dict, price: float) -> dict:
    mult = UNIT_MULT.get(data.get("unit", "thousands"), 1_000)
    quarters = sorted(data["quarters"], key=lambda q: q["end_date"])
    latest = quarters[-1]

    shares = latest.get("shares_outstanding")
    mktcap = price * shares * mult if shares else None

    result: dict = {"ticker": ticker, "source": "report", "price": price,
                    "mult": mult, "mktcap": mktcap, "as_of": latest["id"]}

    # ---- display columns: current partial-year quarters, then every complete
    #      fiscal year (aggregate) followed by its four quarters, newest first ----
    by_fy: dict[str, list[dict]] = {}
    for q in quarters:
        by_fy.setdefault(fy_token(q["id"]), []).append(q)
    complete = {fy for fy, qs in by_fy.items() if len({q_num(q["id"]) for q in qs}) == 4}

    def qcols(fy):
        return [quarter_col(quarters, quarters.index(q))
                for q in sorted(by_fy[fy], key=lambda q: q_num(q["id"]), reverse=True)]

    cols = []
    for fy in sorted((fy for fy in by_fy if fy not in complete), reverse=True):
        cols += qcols(fy)
    for fy in sorted(complete, reverse=True):
        cols.append(fy_col(quarters, fy))
        cols += qcols(fy)
    result["cols"] = cols

    # ---- per-column valuation: close on the report date × then-current data ----
    closes = load_closes(ticker)
    for c in cols:
        price = close_on(closes, c.get("report_date"))
        c["ref_price"] = price
        c["mktcap"] = price * c["shares"] * mult if price and c.get("shares") else None
        # valuation rows are shown only for dated quarter columns (not FY aggregates
        # or history-only quarters that carry no report date)
        c["show_val"] = price is not None and not c.get("is_fy")
        t = c.get("ttm")
        c["ps"] = c["mktcap"] / (t["rev"] * mult) if c["mktcap"] and t and t.get("rev") else None

        # P/S on this column's own full-year guidance / estimate (revenue range → P/S range)
        def ps_rng(rev, cap=c["mktcap"]):
            if not rev or not cap:
                return None
            lo, hi = rev
            return (cap / (hi * mult), cap / (lo * mult))
        c["guid_ps"] = ps_rng(c.get("guid_fy"))
        c["est_ps"] = ps_rng(c.get("est_fy"))

        c["pfcf_co"] = c["pfcf_strict"] = None
        if c["mktcap"] and t and t.get("ocf") is not None and t.get("ppe") is not None:
            fcf_co = (t["ocf"] - t["ppe"]) * mult
            c["pfcf_co"] = c["mktcap"] / fcf_co if fcf_co > 0 else None
            if t.get("sw") is not None:
                fcf_strict = fcf_co - t["sw"] * mult
                c["pfcf_strict"] = c["mktcap"] / fcf_strict if fcf_strict > 0 else None

    # ---- trailing TTM (last four reported quarters, regardless of display) ----
    if len(quarters) >= 4:
        window = [quarter_col(quarters, i) for i in range(len(quarters) - 1, len(quarters) - 5, -1)]
        rev = [c["revenue"] for c in window]
        ocf = [c["ocf"] for c in window]
        ppe = [c["ppe"] for c in window]
        sw = [c["sw"] for c in window]

        rev_ttm = sum(rev) * mult if None not in rev else None
        ttm = {"revenue": rev_ttm, "shares": shares}
        result["ps"] = mktcap / rev_ttm if mktcap and rev_ttm else None
        if None not in ocf and None not in ppe:
            ttm["ocf"] = sum(ocf) * mult
            ttm["ppe"] = sum(ppe) * mult
            fcf_co = (sum(ocf) - sum(ppe)) * mult
            ttm["fcf_co"] = fcf_co
            result["pfcf_co"] = mktcap / fcf_co if mktcap and fcf_co > 0 else None
            if None not in sw:
                ttm["sw"] = sum(sw) * mult
                fcf_strict = fcf_co - sum(sw) * mult
                ttm["fcf_strict"] = fcf_strict
                result["pfcf_strict"] = mktcap / fcf_strict if mktcap and fcf_strict > 0 else None
        result["ttm"] = ttm
        result["rev_ttm"] = rev_ttm

    # ---- forward P/S at TODAY's price from the latest full-year guidance / estimate ----
    def cur_ps(rev):
        if not rev or not mktcap:
            return None
        lo, hi = rev
        return (mktcap / (hi * mult), mktcap / (lo * mult))

    result["fwd_ps_guid"] = cur_ps(midpoint(latest, "guidance_fy_revenue"))
    result["fwd_ps_est"] = cur_ps(midpoint(latest, "est_fy_revenue"))
    return result


def compute_yfinance(ticker: str) -> dict:
    info = yf.Ticker(ticker).info
    mktcap = info.get("marketCap")
    rev = info.get("totalRevenue")
    fcf = info.get("freeCashflow")
    return {
        "ticker": ticker, "source": "yahoo", "price": info.get("currentPrice"),
        "mult": 1, "mktcap": mktcap, "cols": [], "forward": {}, "as_of": "yahoo TTM",
        "ttm": {"revenue": rev, "fcf_co": fcf},
        "rev_ttm": rev,
        "ps": mktcap / rev if mktcap and rev else None,
        "pfcf_co": mktcap / fcf if mktcap and fcf and fcf > 0 else None,
    }


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def fmt_money(v: float | None) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1e9:
        return f"${v / 1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def fmt_mult(v: float | None) -> str:
    if v is None:
        return "[dim]n/m[/dim]"
    return f"{v:.1f}x"


def fmt_price(v: float | None) -> str:
    return f"{v:.2f}" if v is not None else "[dim]—[/dim]"


def fmt_date(d: date | None) -> str:
    return d.strftime("%y-%m-%d") if d else "[dim]—[/dim]"


def fmt_mult_rng(r: tuple | None) -> str:
    """A (low, high) P/S range → '5.3x' when it rounds to one value, else '1.4–1.5x'."""
    if not r or r[0] is None or r[1] is None:
        return "[dim]n/m[/dim]"
    lo, hi = r
    if round(lo, 1) == round(hi, 1):
        return f"{lo:.1f}x"
    return f"{lo:.1f}–{hi:.1f}x"


def num(v: float | None, mult: int, decimals: int = 1) -> str:
    """A stored monetary/share value → millions, red if negative."""
    if v is None:
        return "[dim]—[/dim]"
    m = v * mult / 1e6
    s = f"{m:,.{decimals}f}"
    return f"[red]{s}[/red]" if v < 0 else s


def rng_cell(r: tuple | None, mult: int) -> str:
    """A (low, high) revenue range → '$M' string, e.g. '169–181'."""
    if not r:
        return "[dim]—[/dim]"
    lo, hi = r[0] * mult / 1e6, r[1] * mult / 1e6
    if abs(hi - lo) < 1e-9:
        return f"{lo:,.0f}"
    return f"{lo:,.0f}–{hi:,.0f}"


def fy_guid_cell(c: dict, mult: int) -> str:
    if c.get("guid_fy_withdrawn"):
        return "[yellow]withdrawn[/yellow]"
    return rng_cell(c.get("guid_fy"), mult)


def render_ticker(r: dict) -> None:
    mult = r["mult"]
    cols = r.get("cols", [])
    ttm = r.get("ttm", {})

    src_note = "  [yellow](yfinance fallback)[/yellow]" if r["source"] == "yahoo" else ""
    title = f"[bold cyan]{r['ticker']}[/bold cyan]{src_note}"

    table = Table(title=title, header_style="bold", show_lines=False)
    agg = "bold magenta"  # header colour for the aggregate columns (TTM, full year)
    table.add_column("", min_width=19, no_wrap=True)
    table.add_column("TTM", justify="right", style="bold", header_style=agg, min_width=9)
    for c in cols:
        table.add_column(c["id"], justify="right", min_width=9,
                         header_style=agg if c.get("is_fy") else None)

    blank = ["" for _ in cols]

    def fund_row(label, ttm_cell, key, end_section=False):
        cells = [num(c.get(key), mult) for c in cols]
        table.add_row(label, ttm_cell, *cells, end_section=end_section)

    # --- fundamentals ---
    fund_row("Revenue ($M)", num(ttm.get("revenue"), 1), "revenue")
    fund_row("Operating CF ($M)", num(ttm.get("ocf"), 1), "ocf")
    fund_row("CapEx PP&E ($M)", num(ttm.get("ppe"), 1), "ppe")
    fund_row("CapEx software ($M)", num(ttm.get("sw"), 1), "sw")
    fund_row("FCF company ($M)", num(ttm.get("fcf_co"), 1), "fcf_co")
    fund_row("FCF strict ($M)", num(ttm.get("fcf_strict"), 1), "fcf_strict")
    fund_row("Shares out (M)", num(ttm.get("shares"), mult), "shares", end_section=True)

    # --- guidance / estimate as it stood at each report (quarter columns only) ---
    table.add_row("FY Rev guidance ($M)", "",
                  *["" if c.get("is_fy") else fy_guid_cell(c, mult) for c in cols])
    table.add_row("FY Rev estimate ($M)", "",
                  *["" if c.get("is_fy") else rng_cell(c.get("est_fy"), mult) for c in cols],
                  end_section=True)

    # --- valuation, per column (report-date close × then-current data);
    #     blank in the FY column, where it would just duplicate Q4 ---
    def val_row(label, ttm_cell, fn, **kw):
        table.add_row(label, ttm_cell,
                      *[fn(c) if c.get("show_val") else "" for c in cols], **kw)

    val_row("Ref date", fmt_date(date.today()), lambda c: fmt_date(c.get("report_date")))
    val_row("Ref price ($)", fmt_price(r.get("price")), lambda c: fmt_price(c.get("ref_price")))
    val_row("Market cap", fmt_money(r.get("mktcap")), lambda c: fmt_money(c.get("mktcap")))
    val_row("P / S", fmt_mult(r.get("ps")), lambda c: fmt_mult(c.get("ps")))
    val_row("P / FCF company", fmt_mult(r.get("pfcf_co")), lambda c: fmt_mult(c.get("pfcf_co")))
    val_row("P / FCF strict", fmt_mult(r.get("pfcf_strict")),
            lambda c: fmt_mult(c.get("pfcf_strict")), end_section=True)

    val_row("FW P/S guidance", fmt_mult_rng(r.get("fwd_ps_guid")),
            lambda c: fmt_mult_rng(c.get("guid_ps")))
    val_row("FW P/S estimate", fmt_mult_rng(r.get("fwd_ps_est")),
            lambda c: fmt_mult_rng(c.get("est_ps")))

    console.print(table)
    console.print()


def render(results: list[dict]) -> None:
    for r in results:
        render_ticker(r)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="Show P/S and P/FCF multiples from official reports.")
    parser.add_argument("tickers", nargs="*", help="Tickers (default: all stocks in TICKERS.yml)")
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers] if args.tickers else load_stocks()

    results = []
    for ticker in tickers:
        data = load_financials(ticker)
        price = fetch_live_price(ticker)
        if data and price:
            results.append(compute_official(ticker, data, price))
        elif data and not price:
            console.print(f"  [yellow]warning:[/yellow] no live price for {ticker}; skipping")
        else:
            console.print(f"  [dim]{ticker}: no FINANCIALS.yml — falling back to yfinance[/dim]")
            results.append(compute_yfinance(ticker))

    if results:
        render(results)


if __name__ == "__main__":
    main()
