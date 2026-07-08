#!/usr/bin/env python3
"""
check_valuation.py — Fundamentals and valuation multiples from official reports.

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
  uv run check-valuation              # all stocks in TICKERS.yml
  uv run check-valuation ODD BARK     # specific tickers
  uv run check-valuation NVDA         # ad-hoc ticker (yfinance fallback)
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


def fetch_fx_rate(currency: str) -> float:
    """Return the number of `currency` units per 1 USD (e.g. ~7.2 for CNY).
    Returns 1.0 for USD or if the lookup fails."""
    aliases = {"RMB": "CNY"}
    currency = aliases.get(currency.upper(), currency.upper())
    if currency == "USD":
        return 1.0
    try:
        rate = yf.Ticker(f"USD{currency}=X").fast_info.last_price
        if rate:
            return float(rate)
    except Exception:
        pass
    console.print(f"[yellow]warning: could not fetch USD{currency}=X rate; using 1.0[/yellow]")
    return 1.0


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


def prior_year_id(qid: str) -> str:
    """q1-2024 → q1-2023, FY-2025 → FY-2024."""
    fy = fy_token(qid)
    idx = qid.rfind(fy)
    return qid[:idx] + str(int(fy) - 1) + qid[idx + 4:]


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


def capex_keys(quarters: list[dict]) -> list[str]:
    """Every `ytd_capex_*` key present in the data, PP&E first (it drives company FCF).
    `strict` FCF subtracts them all; `company` FCF subtracts only PP&E."""
    keys = {k for q in quarters for k in q if k.startswith("ytd_capex")}
    ordered = sorted(keys)
    if "ytd_capex_ppe" in ordered:
        ordered.remove("ytd_capex_ppe")
        ordered.insert(0, "ytd_capex_ppe")
    return ordered


def capex_label(key: str) -> str:
    suffix = key[len("ytd_capex_"):]
    return "PP&E" if suffix == "ppe" else suffix


def col_capex(quarters: list[dict], i: int, keys: list[str]) -> tuple[dict, float | None]:
    """Standalone capex per key for quarter i, plus their sum (the `strict` deduction).
    The sum is None if any key present in that quarter can't be differenced."""
    q, out, total, ok = quarters[i], {}, 0.0, True
    for k in keys:
        if k in q:
            v = standalone(quarters, i, k)
            out[k] = v
            if v is None:
                ok = False
            else:
                total += v
    return out, (total if ok else None)


def ttm_metrics(quarters: list[dict], j: int, keys: list[str]) -> dict | None:
    """Trailing-twelve-month revenue / OCF / PP&E / total-capex / SBC ending at index j."""
    if j < 3:
        return None
    idxs = range(j - 3, j + 1)
    rev = [quarters[k].get("revenue") for k in idxs]
    if any(v is None for v in rev):
        return None
    ocf = [standalone(quarters, k, "ytd_operating_cf") for k in idxs]
    ppe = [standalone(quarters, k, "ytd_capex_ppe") for k in idxs]
    totals = [col_capex(quarters, k, keys)[1] for k in idxs]
    sbc = [standalone(quarters, k, "ytd_sbc") for k in idxs]
    return {
        "rev": sum(rev),
        "ocf": None if None in ocf else sum(ocf),
        "ppe": None if None in ppe else sum(ppe),
        "capex_total": None if None in totals else sum(totals),
        "sbc": None if any(v is None for v in sbc) else sum(sbc),
    }


def quarter_col(quarters: list[dict], i: int, keys: list[str]) -> dict:
    """A single standalone-quarter display column."""
    q = quarters[i]
    ocf = standalone(quarters, i, "ytd_operating_cf")
    capex, total = col_capex(quarters, i, keys)
    ppe = capex.get("ytd_capex_ppe")
    fcf_co = ocf - ppe if ocf is not None and ppe is not None else None
    fcf_strict = ocf - total if ocf is not None and total is not None else None
    sbc = standalone(quarters, i, "ytd_sbc")
    fcf_co_sbc = fcf_co - sbc if fcf_co is not None and sbc is not None else None
    fcf_strict_sbc = fcf_strict - sbc if fcf_strict is not None and sbc is not None else None
    r3 = [quarters[k].get("revenue") for k in range(i - 2, i + 1)] if i >= 2 else [None]
    rev3 = None if any(v is None for v in r3) else sum(r3)
    return {
        "id": q["id"], "is_fy": False, "revenue": q.get("revenue"),
        "ocf": ocf, "capex": capex, "fcf_co": fcf_co, "fcf_strict": fcf_strict,
        "sbc": sbc, "fcf_co_sbc": fcf_co_sbc, "fcf_strict_sbc": fcf_strict_sbc,
        "shares": q.get("shares_outstanding"),
        "cash": q.get("cash"), "total_debt": q.get("total_debt"),
        "report_date": as_date(q.get("report_date")),
        "ttm": ttm_metrics(quarters, i, keys),  # trailing basis for this column's multiples
        "rev3": rev3,  # last three quarters' revenue, for the next-quarter forward TTM
        # guidance / estimate as issued at THIS quarter's report; *_hint = why none was given
        "guid_nq": midpoint(q, "guidance_nq_revenue"),   # next-quarter revenue
        "guid_nq_hint": q.get("guidance_nq_hint"),
        "guid_fy": midpoint(q, "guidance_fy_revenue"),
        "guid_fy_hint": q.get("guidance_fy_hint"),
        "est_fy": midpoint(q, "est_fy_revenue"),
    }


def fy_col(quarters: list[dict], fy: str, keys: list[str]) -> dict:
    """An aggregated full-fiscal-year display column (needs all four quarters)."""
    qs = [q for q in quarters if fy_token(q["id"]) == fy]
    by_n = {q_num(q["id"]): q for q in qs}
    q4 = by_n.get(4)
    revenue = sum(q["revenue"] for q in qs if q.get("revenue") is not None) or None
    # for a complete year the Q4 year-to-date lines ARE the full-year totals
    ocf = q4.get("ytd_operating_cf") if q4 else None
    capex = {k: q4.get(k) for k in keys if q4 and k in q4}
    ppe = capex.get("ytd_capex_ppe")
    total = sum(v for v in capex.values() if v is not None) if capex else None
    fcf_co = ocf - ppe if ocf is not None and ppe is not None else None
    fcf_strict = ocf - total if ocf is not None and total is not None else None
    sbc = q4.get("ytd_sbc") if q4 else None
    fcf_co_sbc = fcf_co - sbc if fcf_co is not None and sbc is not None else None
    fcf_strict_sbc = fcf_strict - sbc if fcf_strict is not None and sbc is not None else None
    # final full-year guidance/estimate for this FY = last of Q1–Q3 that gave one
    guid = est = None
    for n in (3, 2, 1):
        q = by_n.get(n)
        if q is None:
            continue
        guid = guid or midpoint(q, "guidance_fy_revenue")
        est = est or midpoint(q, "est_fy_revenue")
    return {
        "id": f"FY-{fy}", "is_fy": True, "revenue": revenue,
        "ocf": ocf, "capex": capex, "fcf_co": fcf_co, "fcf_strict": fcf_strict,
        "sbc": sbc, "fcf_co_sbc": fcf_co_sbc, "fcf_strict_sbc": fcf_strict_sbc,
        "shares": q4.get("shares_outstanding") if q4 else None,
        "cash": q4.get("cash") if q4 else None,
        "total_debt": q4.get("total_debt") if q4 else None,
        "report_date": as_date(q4.get("report_date")) if q4 else None,
        # at year-end the trailing window IS the full year
        "ttm": {"rev": revenue, "ocf": ocf, "ppe": ppe, "capex_total": total, "sbc": sbc},
        "guid_fy": guid, "guid_fy_withdrawn": False, "est_fy": est,
    }


def compute_official(ticker: str, data: dict, price: float) -> dict:
    mult = UNIT_MULT.get(data.get("unit", "thousands"), 1_000)
    currency = data.get("currency", "USD").upper()
    fx_rate = fetch_fx_rate(currency)   # native currency units per 1 USD (e.g. ~7.2 for CNY)
    fmult = mult / fx_rate              # converts stored values → USD

    quarters = sorted(data["quarters"], key=lambda q: q["end_date"])
    latest = quarters[-1]

    shares = latest.get("shares_outstanding")
    mktcap = price * shares * mult if shares else None  # shares need no FX conversion

    keys = capex_keys(quarters)
    result: dict = {"ticker": ticker, "source": "report", "price": price,
                    "mult": mult, "fmult": fmult, "currency": currency, "fx_rate": fx_rate,
                    "mktcap": mktcap, "as_of": latest["id"], "capex_keys": keys}

    # ---- display columns: current partial-year quarters + last 2 complete fiscal years ----
    DISPLAY_FULL_YEARS = 2

    by_fy: dict[str, list[dict]] = {}
    for q in quarters:
        by_fy.setdefault(fy_token(q["id"]), []).append(q)
    complete = {fy for fy, qs in by_fy.items()
                if len({q_num(q["id"]) for q in qs}) == 4}

    def qcols(fy):
        return [quarter_col(quarters, quarters.index(q), keys)
                for q in sorted(by_fy[fy], key=lambda q: q_num(q["id"]), reverse=True)]

    sorted_fys = sorted(by_fy, reverse=True)
    complete_fys = [fy for fy in sorted_fys if fy in complete]
    latest_fy = sorted_fys[0]

    cols = []
    if latest_fy not in complete:
        cols += qcols(latest_fy)          # partial current year
    for fy in complete_fys[:DISPLAY_FULL_YEARS]:
        cols.append(fy_col(quarters, fy, keys))
        cols += qcols(fy)
    result["cols"] = cols

    # ---- Y/Y growth: look up same quarter / same FY one year earlier ----
    all_qcols = {quarters[i]["id"]: quarter_col(quarters, i, keys) for i in range(len(quarters))}
    all_fycols = {f"FY-{fy}": fy_col(quarters, fy, keys)
                  for fy, qs in by_fy.items()
                  if len({q_num(q["id"]) for q in qs}) == 4}

    def _yoy(cur, prv):
        return ((cur - prv) / abs(prv)) if cur is not None and prv else None

    for c in cols:
        pid = prior_year_id(c["id"])
        prior = all_fycols.get(pid) if c.get("is_fy") else all_qcols.get(pid)
        c["rev_yoy"] = _yoy(c.get("revenue"), prior.get("revenue") if prior else None)
        c["fcf_yoy"] = _yoy(c.get("fcf_co"), prior.get("fcf_co") if prior else None)

    # ---- NQ guidance vs next quarter's actual (retrospective accuracy) ----
    col_by_id = {c["id"]: c for c in cols}
    for c in cols:
        if c.get("is_fy"):
            c["vs_nq_guid"] = None
            continue
        nq = c.get("guid_nq")
        if not nq:
            c["vs_nq_guid"] = None
            continue
        n, fy = q_num(c["id"]), int(fy_token(c["id"]))
        next_id = f"q1-{fy + 1}" if n == 4 else f"q{n + 1}-{fy}"
        nxt = col_by_id.get(next_id)
        rev = nxt.get("revenue") if nxt else None
        c["vs_nq_guid"] = (rev / ((nq[0] + nq[1]) / 2) - 1) if rev is not None else None

    # ---- FY guidance vs actual full-year revenue (retrospective accuracy) ----
    # Q1-Q3 give updated guidance for the current FY; Q4 gives initial guidance for FY+1.
    for c in cols:
        if c.get("is_fy"):
            c["vs_fy_guid"] = None
            continue
        fy_guid = c.get("guid_fy")
        if not fy_guid:
            c["vs_fy_guid"] = None
            continue
        n, fy = q_num(c["id"]), int(fy_token(c["id"]))
        target_fy = str(fy + 1) if n == 4 else str(fy)
        fy_actual = all_fycols.get(f"FY-{target_fy}")
        actual_rev = fy_actual.get("revenue") if fy_actual else None
        mid = (fy_guid[0] + fy_guid[1]) / 2
        c["vs_fy_guid"] = (actual_rev / mid - 1) if actual_rev is not None else None

    # ---- per-column valuation: close on the report date × then-current data ----
    closes = load_closes(ticker)
    for c in cols:
        price = close_on(closes, c.get("report_date"))
        c["ref_price"] = price
        c["mktcap"] = price * c["shares"] * mult if price and c.get("shares") else None
        # valuation rows are shown only for dated quarter columns (not FY aggregates)
        c["show_val"] = price is not None and not c.get("is_fy")
        t = c.get("ttm")
        c["ps"] = c["mktcap"] / (t["rev"] * fmult) if c["mktcap"] and t and t.get("rev") else None

        # P/S on this column's own full-year guidance / estimate (revenue range → P/S range)
        def ps_rng(rev, cap=c["mktcap"]):
            if not rev or not cap:
                return None
            lo, hi = rev
            return (cap / (hi * fmult), cap / (lo * fmult))
        c["guid_ps"] = ps_rng(c.get("guid_fy"))
        c["est_ps"] = ps_rng(c.get("est_fy"))
        # forward-TTM P/S: last 3 actual quarters + the guided next quarter
        nq, base = c.get("guid_nq"), c.get("rev3")
        c["nq_ps"] = None
        if c["mktcap"] and nq and base is not None:
            b = base * fmult
            c["nq_ps"] = (c["mktcap"] / (b + nq[1] * fmult), c["mktcap"] / (b + nq[0] * fmult))

        cash, td = c.get("cash"), c.get("total_debt")
        c["cash_val"] = cash * fmult if cash is not None else None
        c["debt_val"] = td * fmult if td is not None else None

        c["pfcf_co"] = c["pfcf_strict"] = None
        if c["mktcap"] and t and t.get("ocf") is not None and t.get("ppe") is not None:
            fcf_co = (t["ocf"] - t["ppe"]) * fmult
            c["pfcf_co"] = c["mktcap"] / fcf_co if fcf_co > 0 else None
            if t.get("capex_total") is not None:
                fcf_strict = (t["ocf"] - t["capex_total"]) * fmult
                c["pfcf_strict"] = c["mktcap"] / fcf_strict if fcf_strict > 0 else None

        c["pfcf_co_sbc"] = c["pfcf_strict_sbc"] = None
        if c["mktcap"] and t and t.get("ocf") is not None and t.get("ppe") is not None and t.get("sbc") is not None:
            fcf_co_sbc = (t["ocf"] - t["ppe"] - t["sbc"]) * fmult
            c["pfcf_co_sbc"] = c["mktcap"] / fcf_co_sbc if fcf_co_sbc > 0 else None
            if t.get("capex_total") is not None:
                fcf_strict_sbc = (t["ocf"] - t["capex_total"] - t["sbc"]) * fmult
                c["pfcf_strict_sbc"] = c["mktcap"] / fcf_strict_sbc if fcf_strict_sbc > 0 else None

    # ---- trailing TTM (last four reported quarters, regardless of display) ----
    if len(quarters) >= 4:
        window = [quarter_col(quarters, i, keys) for i in range(len(quarters) - 1, len(quarters) - 5, -1)]

        def agg(fn):
            vals = [fn(c) for c in window]
            return None if any(v is None for v in vals) else sum(vals) * fmult

        ttm = {
            "revenue": agg(lambda c: c["revenue"]),
            "ocf": agg(lambda c: c["ocf"]),
            "capex": {k: agg(lambda c, k=k: c["capex"].get(k)) for k in keys},
            "fcf_co": agg(lambda c: c["fcf_co"]),
            "fcf_strict": agg(lambda c: c["fcf_strict"]),
            "sbc": agg(lambda c: c.get("sbc")),
            "fcf_co_sbc": agg(lambda c: c.get("fcf_co_sbc")),
            "fcf_strict_sbc": agg(lambda c: c.get("fcf_strict_sbc")),
            "shares": shares,
        }
        result["ttm"] = ttm
        result["rev_ttm"] = ttm["revenue"]
        lc, ltd = latest.get("cash"), latest.get("total_debt")
        result["cash_val"] = lc * fmult if lc is not None else None
        result["debt_val"] = ltd * fmult if ltd is not None else None
        result["ps"] = mktcap / ttm["revenue"] if mktcap and ttm["revenue"] else None
        if mktcap and ttm["fcf_co"] and ttm["fcf_co"] > 0:
            result["pfcf_co"] = mktcap / ttm["fcf_co"]
        if mktcap and ttm["fcf_strict"] and ttm["fcf_strict"] > 0:
            result["pfcf_strict"] = mktcap / ttm["fcf_strict"]
        if mktcap and ttm.get("fcf_co_sbc") and ttm["fcf_co_sbc"] > 0:
            result["pfcf_co_sbc"] = mktcap / ttm["fcf_co_sbc"]
        if mktcap and ttm.get("fcf_strict_sbc") and ttm["fcf_strict_sbc"] > 0:
            result["pfcf_strict_sbc"] = mktcap / ttm["fcf_strict_sbc"]

    # ---- forward P/S at TODAY's price from the latest full-year guidance / estimate ----
    def cur_ps(rev):
        if not rev or not mktcap:
            return None
        lo, hi = rev
        return (mktcap / (hi * fmult), mktcap / (lo * fmult))

    result["fwd_ps_guid"] = cur_ps(midpoint(latest, "guidance_fy_revenue"))
    result["fwd_ps_est"] = cur_ps(midpoint(latest, "est_fy_revenue"))

    # forward-TTM P/S at today's price: last 3 actual quarters + latest next-quarter guide
    latest_rev3 = [q.get("revenue") for q in quarters[-3:]]
    nq, base = midpoint(latest, "guidance_nq_revenue"), (
        None if any(v is None for v in latest_rev3) else sum(latest_rev3))
    result["fwd_ps_nq"] = None
    if mktcap and nq and base is not None:
        b = base * mult
        result["fwd_ps_nq"] = (mktcap / (b + nq[1] * mult), mktcap / (b + nq[0] * mult))
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


def fmt_yoy(v: float | None) -> str:
    if v is None:
        return "[dim]—[/dim]"
    s = f"{v * 100:+.1f}%"
    return f"[green]{s}[/green]" if v >= 0 else f"[red]{s}[/red]"


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


def guid_cell(rng: tuple | None, hint: str | None, mult: int) -> str:
    """Guidance range if given; else the hint (why it wasn't); else a dash."""
    if rng:
        return rng_cell(rng, mult)
    if hint:
        return f"[yellow]{hint}[/yellow]"
    return "[dim]—[/dim]"


def render_ticker(r: dict) -> None:
    mult = r["mult"]
    fmult = r.get("fmult", mult)   # converts stored values to USD; equals mult for USD tickers
    cols = r.get("cols", [])
    ttm = r.get("ttm", {})

    src_note = "  [yellow](yfinance fallback)[/yellow]" if r["source"] == "yahoo" else ""
    currency = r.get("currency", "USD")
    fx_note = (f"  [dim](reports in {currency}; 1 USD = {r['fx_rate']:.2f} {currency})[/dim]"
               if currency != "USD" else "")
    title = f"[bold cyan]{r['ticker']}[/bold cyan]{src_note}{fx_note}"

    table = Table(title=title, header_style="bold", show_lines=False)
    agg = "bold magenta"  # header colour for the aggregate columns (TTM, full year)
    table.add_column("", min_width=19, no_wrap=True)
    table.add_column("TTM", justify="right", style="bold", header_style=agg, min_width=9)
    for c in cols:
        table.add_column(c["id"], justify="right", min_width=12,
                         header_style=agg if c.get("is_fy") else None)

    blank = ["" for _ in cols]
    # `company` and `strict` FCF differ only when there's capex beyond PP&E
    two_fcf = any(k != "ytd_capex_ppe" for k in r.get("capex_keys", []))
    has_sbc = any(c.get("sbc") is not None for c in cols) or ttm.get("sbc") is not None
    # the estimate rows are shown only when FY estimates are actually maintained
    has_est = any(c.get("est_fy") for c in cols)
    # the NQ actual/guide row is useless without at least one numeric NQ guidance
    has_nq_guid = any(c.get("guid_nq") for c in cols)
    # the FY actual/guide row is useless without at least one numeric FY guidance
    has_fy_guid = any(c.get("guid_fy") for c in cols)

    def fund_row(label, ttm_cell, key, end_section=False, m=None):
        cells = [num(c.get(key), fmult if m is None else m) for c in cols]
        table.add_row(label, ttm_cell, *cells, end_section=end_section)

    # two FCF rows whenever there are multiple capex lines OR SBC is present
    show_fcf_strict = two_fcf or has_sbc

    # --- fundamentals: continuous waterfall ---
    # OCF → (−PP&E) → FCF company → (−other capex) → (−SBC) → FCF strict ex SBC
    fund_row("Revenue ($M)", num(ttm.get("revenue"), 1), "revenue")
    table.add_row("  Rev Y/Y", "", *[fmt_yoy(c.get("rev_yoy")) for c in cols])
    fund_row("Operating CF ($M)", num(ttm.get("ocf"), 1), "ocf")
    ttm_capex = ttm.get("capex") or {}
    ppe_cells = [num((c.get("capex") or {}).get("ytd_capex_ppe"), fmult) for c in cols]
    if "ytd_capex_ppe" in r.get("capex_keys", []):
        table.add_row("CapEx PP&E ($M)", num(ttm_capex.get("ytd_capex_ppe"), 1), *ppe_cells)
    if show_fcf_strict:
        fund_row("FCF company ($M)", num(ttm.get("fcf_co"), 1), "fcf_co")
        table.add_row("  FCF Y/Y", "", *[fmt_yoy(c.get("fcf_yoy")) for c in cols])
        for k in r.get("capex_keys", []):
            if k == "ytd_capex_ppe":
                continue
            cells = [num((c.get("capex") or {}).get(k), fmult) for c in cols]
            table.add_row(f"CapEx {capex_label(k)} ($M)", num(ttm_capex.get(k), 1), *cells)
        if has_sbc:
            fund_row("SBC ($M)", num(ttm.get("sbc"), 1), "sbc")
            fund_row("FCF strict ex SBC ($M)", num(ttm.get("fcf_strict_sbc"), 1), "fcf_strict_sbc")
        else:
            fund_row("FCF strict ($M)", num(ttm.get("fcf_strict"), 1), "fcf_strict")
    else:
        fund_row("FCF ($M)", num(ttm.get("fcf_co"), 1), "fcf_co")
        table.add_row("  FCF Y/Y", "", *[fmt_yoy(c.get("fcf_yoy")) for c in cols])
    fund_row("Shares out (M)", num(ttm.get("shares"), mult), "shares", end_section=True, m=mult)

    # --- guidance / estimate as it stood at each report (quarter columns only) ---
    table.add_row("NQ Rev guidance ($M)", "",
                  *["" if c.get("is_fy") else guid_cell(c.get("guid_nq"), c.get("guid_nq_hint"), fmult)
                    for c in cols])
    if has_nq_guid:
        table.add_row("  NQ actual/guide", "",
                      *["" if c.get("is_fy") else fmt_yoy(c.get("vs_nq_guid")) for c in cols])
    table.add_row("FY Rev guidance ($M)", "",
                  *["" if c.get("is_fy") else guid_cell(c.get("guid_fy"), c.get("guid_fy_hint"), fmult)
                    for c in cols], end_section=not has_fy_guid and not has_est)
    if has_fy_guid:
        table.add_row("  FY actual/guide", "",
                      *["" if c.get("is_fy") else fmt_yoy(c.get("vs_fy_guid")) for c in cols],
                      end_section=not has_est)
    if has_est:
        table.add_row("FY Rev estimate ($M)", "",
                      *["" if c.get("is_fy") else rng_cell(c.get("est_fy"), fmult) for c in cols],
                      end_section=True)

    # --- valuation, per column (report-date close × then-current data);
    #     blank in the FY column, where it would just duplicate Q4 ---
    def val_row(label, ttm_cell, fn, **kw):
        table.add_row(label, ttm_cell,
                      *[fn(c) if c.get("show_val") else "" for c in cols], **kw)

    val_row("Ref date", fmt_date(date.today()), lambda c: fmt_date(c.get("report_date")))
    val_row("Ref price ($)", fmt_price(r.get("price")), lambda c: fmt_price(c.get("ref_price")))
    val_row("Market cap", fmt_money(r.get("mktcap")), lambda c: fmt_money(c.get("mktcap")))
    val_row("Cash", fmt_money(r.get("cash_val")), lambda c: fmt_money(c.get("cash_val")))
    val_row("Debt", fmt_money(r.get("debt_val")), lambda c: fmt_money(c.get("debt_val")))
    val_row("P / S", fmt_mult(r.get("ps")), lambda c: fmt_mult(c.get("ps")))
    if show_fcf_strict:
        val_row("P / FCF company", fmt_mult(r.get("pfcf_co")), lambda c: fmt_mult(c.get("pfcf_co")))
        if has_sbc:
            val_row("P / FCF strict ex SBC", fmt_mult(r.get("pfcf_strict_sbc")),
                    lambda c: fmt_mult(c.get("pfcf_strict_sbc")), end_section=True)
        else:
            val_row("P / FCF strict", fmt_mult(r.get("pfcf_strict")),
                    lambda c: fmt_mult(c.get("pfcf_strict")), end_section=True)
    else:
        val_row("P / FCF", fmt_mult(r.get("pfcf_co")),
                lambda c: fmt_mult(c.get("pfcf_co")), end_section=True)

    val_row("FW P/S NQ guidance", fmt_mult_rng(r.get("fwd_ps_nq")),
            lambda c: fmt_mult_rng(c.get("nq_ps")))
    val_row("FW P/S FY guidance", fmt_mult_rng(r.get("fwd_ps_guid")),
            lambda c: fmt_mult_rng(c.get("guid_ps")))
    if has_est:
        val_row("FW P/S FY estimate", fmt_mult_rng(r.get("fwd_ps_est")),
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
