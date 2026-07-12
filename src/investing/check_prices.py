#!/usr/bin/env python3
"""
check_prices.py — Compare tracked stocks against their benchmarks.

By default, shows both weekly (WTD + 3 prior weeks, SMAs 5/10/20) and monthly
(MTD + 3 prior months, SMAs 20/50/200) views, weekly first.

Usage:
  uv run check-prices ODD SNAP        # both views
  uv run check-prices ODD --weeks     # weekly view only
  uv run check-prices ODD --months    # monthly view only
"""

import re
import sys
import math
import argparse
from datetime import date, timedelta

import pandas as pd
import yaml
from dateutil.relativedelta import relativedelta
from rich.console import Console
from rich.table import Table

from investing.lib import REPO_ROOT
from investing.fetch_prices import fetch_ticker, fetch_iv, fetch_live_price, load_last_date
from investing.volume_profile import compute_poc

PRICES_DAILY = REPO_ROOT / "prices" / "daily"
TICKERS_FILE = REPO_ROOT / "TICKERS.yml"

SMA_WEEKS  = (5, 10, 20)
SMA_MONTHS = (20, 50, 200)

console = Console()


def load_config() -> dict:
    with TICKERS_FILE.open() as f:
        return yaml.safe_load(f)


def load_prices(ticker: str, auto_fetch: bool = False) -> pd.DataFrame | None:
    from datetime import date as date_
    path = PRICES_DAILY / f"{ticker}.csv"
    if not path.exists():
        if auto_fetch:
            console.print(f"  [dim]fetching {ticker}...[/dim]")
            PRICES_DAILY.mkdir(parents=True, exist_ok=True)
            fetch_ticker(ticker, prices_dir=PRICES_DAILY, interval="1d", prepost=False)
        if not path.exists():
            console.print(f"  [yellow]warning:[/yellow] no price file for {ticker}")
            return None
    elif auto_fetch:
        last = load_last_date(path, "Date")
        if last is not None and last < date_.today() - timedelta(days=1):
            fetch_ticker(ticker, prices_dir=PRICES_DAILY, interval="1d", prepost=False, quiet=True)
    df = pd.read_csv(path, index_col="Date", parse_dates=True)
    return df[~df.index.duplicated(keep="last")]


def month_bounds(df: pd.DataFrame, year: int, month: int) -> tuple:
    days = df.index[(df.index.year == year) & (df.index.month == month)]
    return (days[0], days[-1]) if len(days) else (None, None)


def week_bounds(df: pd.DataFrame, monday: date) -> tuple:
    friday = monday + timedelta(days=4)
    m_ts, f_ts = pd.Timestamp(monday), pd.Timestamp(friday)
    days = df.index[(df.index >= m_ts) & (df.index <= f_ts)]
    return (days[0], days[-1]) if len(days) else (None, None)


def nearest_on_or_before(df: pd.DataFrame, target: date) -> pd.Timestamp | None:
    ts = pd.Timestamp(target)
    prior = df.index[df.index <= ts]
    return prior[-1] if len(prior) else None


def fmt_price(v: float) -> str:
    if v >= 100:
        return f"{v:4.0f}"
    if v >= 10:
        return f"{v:4.1f}"
    return f"{v:4.2f}"


def trend_label(a: float, b: float, c: float, tol: float = 0.005) -> str:
    """↑/↓/~ for the sequence a→b→c (used by heuristics)."""
    def cmp(x: float, y: float) -> int:
        diff = abs(x - y) / max(abs(x), abs(y))
        if diff < tol: return 0
        return 1 if x > y else -1
    d1, d2 = cmp(a, b), cmp(b, c)
    if (d1 == 1 and d2 == -1) or (d1 == -1 and d2 == 1): return "~"
    if d1 >= 0 and d2 >= 0: return "↑"
    return "↓"


def pair_arrows(a: float, b: float, c: float, tol: float = 0.005) -> tuple[str, str]:
    """Two-arrow prefix and color for the a/b and b/c pairs.
    ↑↑ green, ↓↓ red, anything mixed or flat → yellow."""
    def cmp(x: float, y: float) -> int:
        diff = abs(x - y) / max(abs(x), abs(y))
        if diff < tol: return 0
        return 1 if x > y else -1
    sym = {1: "↑", -1: "↓", 0: "→"}
    d1, d2 = cmp(a, b), cmp(b, c)
    prefix = sym[d1] + sym[d2]
    if d1 == 1 and d2 == 1:   return prefix, "green"
    if d1 == -1 and d2 == -1: return prefix, "red"
    return prefix, "yellow"


def fmt_tuple(a: float, b: float, c: float) -> str:
    s1 = ">" if a >= b else "<"
    s2 = ">" if b >= c else "<"
    return f"{fmt_price(a)} {s1} {fmt_price(b)} {s2} {fmt_price(c)}"


def sma_alignment(df: pd.DataFrame, as_of: pd.Timestamp, periods: tuple = SMA_MONTHS) -> str:
    p1, p2, p3 = periods
    loc = df.index.get_loc(as_of)
    if loc < p3 - 1:
        return "n/a"
    close = df["Close"].iloc
    sma1 = close[loc - p1 + 1 : loc + 1].mean()
    sma2 = close[loc - p2 + 1 : loc + 1].mean()
    sma3 = close[loc - p3 + 1 : loc + 1].mean()

    prefix, color = pair_arrows(sma1, sma2, sma3)
    vals = fmt_tuple(sma1, sma2, sma3)
    return f"[{color}]{prefix} {vals}[/{color}]"


def rvol(df: pd.DataFrame, start_ts: pd.Timestamp, end_ts: pd.Timestamp, periods: tuple = SMA_MONTHS) -> str:
    p1, p2, p3 = periods
    loc = df.index.get_loc(end_ts)
    if loc < p3 - 1:
        return "n/a"
    vol = df["Volume"].iloc
    period_avg = vol[df.index.get_loc(start_ts) : loc + 1].mean()
    v1 = vol[loc - p1 + 1 : loc + 1].mean()
    v2 = vol[loc - p2 + 1 : loc + 1].mean()
    v3 = vol[loc - p3 + 1 : loc + 1].mean()

    r1, r2, r3 = period_avg/v1, period_avg/v2, period_avg/v3
    s1 = ">" if r1 >= r2 else "<"
    s2 = ">" if r2 >= r3 else "<"
    ratios = f"{r1*100:3.0f}% {s1} {r2*100:3.0f}% {s2} {r3*100:3.0f}%"

    prefix, color = pair_arrows(v1, v2, v3)
    return f"[{color}]{prefix} {ratios}[/{color}]"


def period_snapshot(df: pd.DataFrame, start_ts: pd.Timestamp, end_ts: pd.Timestamp, periods: tuple = SMA_MONTHS) -> tuple:
    if start_ts is None or end_ts is None or start_ts >= end_ts:
        return None, None, None, None
    start_price = float(df["Close"].iloc[df.index.get_loc(start_ts)])
    end_price   = float(df["Close"].iloc[df.index.get_loc(end_ts)])
    pct = (end_price / start_price - 1) * 100
    return end_price, pct, sma_alignment(df, end_ts, periods), rvol(df, start_ts, end_ts, periods)


def fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "—"
    return f"[green]+{pct:.1f}%[/green]" if pct >= 0 else f"[red]{pct:.1f}%[/red]"


def _pct(s: str) -> float | None:
    m = re.search(r'([+-]?\d+\.?\d*)%', s)
    return float(m.group(1)) if m else None

def _trend(s: str) -> str:
    if "↑↑" in s: return "↑"
    if "↓↓" in s: return "↓"
    return "·"

def print_heuristics(symbol: str, tags: list, cells: dict, tickers: list) -> None:
    hints = []

    stock_mtd  = _pct(cells[symbol]["return_"][0])
    stock_sma  = _trend(cells[symbol]["sma"][0])
    stock_rvol = _trend(cells[symbol]["vol"][0])

    # Overextension vs SPY
    if "SPY" in tickers and "SPY" in cells:
        spy_mtd = _pct(cells["SPY"]["return_"][0])
        if stock_mtd is not None and spy_mtd is not None:
            diff = stock_mtd - spy_mtd
            if diff > 10:
                hints.append(
                    f"MTD return is [bold]{diff:.1f}pp[/bold] ahead of SPY — "
                    f"historically predicts near-term mean reversion."
                )

    # Small-cap: rvol ↑ into a decline → exhaustion signal
    if "small_cap" in tags and stock_mtd is not None and stock_mtd < 0 and stock_rvol == "↑":
        hints.append(
            "Small-cap with falling price and [bold]↑ rvol[/bold] — "
            "elevated volume into the selloff may signal selling exhaustion."
        )

    # SMA ↓ + quiet rvol → trend continuation, no bounce
    if stock_sma == "↓" and stock_rvol == "·":
        hints.append(
            "[bold]↓ SMA[/bold] with neutral volume — no panic selling detected, "
            "suggests downtrend continuation rather than reversal."
        )

    if hints:
        console.print()
        for h in hints:
            console.print(f"  [dim]▸[/dim] {h}")


def implied_move(ticker: str, price: float, mode: str) -> str:
    iv = fetch_iv(ticker, price)
    if iv is None:
        return "n/a"
    periods_per_year = 52 if mode == "weeks" else 12
    move = iv * math.sqrt(1 / periods_per_year) * 100
    suffix = "wk" if mode == "weeks" else "mo"
    return f"±{move:.1f}%/{suffix}"


def analyze_stock(symbol: str, benchmarks: list[str], as_of: date | None = None, tags: list | None = None, mode: str = "weeks", auto_fetch: bool = False) -> None:
    today = as_of or date.today()
    periods = SMA_WEEKS if mode == "weeks" else SMA_MONTHS
    sma_label = "/".join(str(p) for p in periods)

    tickers = [symbol] + benchmarks
    data = {t: load_prices(t, auto_fetch=auto_fetch) for t in tickers}
    live_prices: dict[str, float | None] = (
        {} if as_of else {t: fetch_live_price(t) for t in tickers}
    )

    ref_df = data[symbol]
    if ref_df is None:
        return

    # Build column definitions using the stock's own calendar as reference
    columns = []
    if mode == "weeks":
        this_monday = today - timedelta(days=today.weekday())
        week_mondays = [this_monday - timedelta(weeks=i) for i in range(4)]
        for i, monday in enumerate(week_mondays):
            is_current = i == 0
            ref_start, ref_end = week_bounds(ref_df, monday)
            if is_current:
                ref_end = nearest_on_or_before(ref_df, today) if as_of else pd.Timestamp(today)
                if ref_start is None and not as_of:
                    ref_start = pd.Timestamp(monday)
            friday = monday + timedelta(days=4)
            period_str = "WTD" if is_current else f"{monday.strftime('%b %-d')}–{friday.strftime('%-d')}"
            trading_str = (
                f"{ref_start.strftime('%b %-d') if ref_start else '?'}"
                f" → {ref_end.strftime('%b %-d') if ref_end else '?'}"
            )
            label = f"{period_str}\n{trading_str}"
            columns.append({"type": "week", "monday": monday, "is_current": is_current, "label": label})
    else:
        cur = date(today.year, today.month, 1)
        months_list = [cur - relativedelta(months=i) for i in range(4)]
        for i, m in enumerate(months_list):
            year, month = m.year, m.month
            ref_start, ref_end = month_bounds(ref_df, year, month)
            is_current = i == 0
            if is_current:
                ref_end = nearest_on_or_before(ref_df, today) if as_of else pd.Timestamp(today)
                period_str = f"{m.strftime('%b')} MTD"
            else:
                period_str = f"{m.strftime('%b %Y')}"
            trading_str = (
                f"{ref_start.strftime('%b %-d') if ref_start else '?'}"
                f" → {ref_end.strftime('%b %-d') if ref_end else '?'}"
            )
            label = f"{period_str}\n{trading_str}"
            columns.append({"type": "month", "year": year, "month": month, "is_current": is_current, "label": label})

    # Pre-compute all cells per ticker
    cells = {}
    for ticker in tickers:
        df = data[ticker]
        price_cells, return_cells, sma_cells, vol_cells = [], [], [], []

        for col in columns:
            if df is None:
                price_cells.append("—")
                return_cells.append("—")
                sma_cells.append("—")
                vol_cells.append("—")
                continue

            if col["type"] == "week":
                t_start, t_end = week_bounds(df, col["monday"])
            else:
                t_start, t_end = month_bounds(df, col["year"], col["month"])

            if col["is_current"]:
                t_end = nearest_on_or_before(df, today)

            price, pct, sma, vol = period_snapshot(df, t_start, t_end, periods)

            if col["is_current"]:
                live = live_prices.get(ticker)
                if live is not None:
                    price = live
                    if t_start is not None:
                        start_price = float(df["Close"].iloc[df.index.get_loc(t_start)])
                        pct = (live / start_price - 1) * 100

            price_cells.append(f"{price:.2f}" if price is not None else "—")
            return_cells.append(fmt_pct(pct))
            sma_cells.append(sma or "—")
            vol_cells.append(vol or "—")

        cells[ticker] = dict(price=price_cells, return_=return_cells, sma=sma_cells, vol=vol_cells)

    if not as_of:
        for ticker in tickers:
            df = data[ticker]
            iv_str = implied_move(ticker, float(df["Close"].iloc[-1]), mode) if df is not None else "n/a"
            cells[ticker]["iv"] = [iv_str] + ["—"] * (len(columns) - 1)

    if not as_of:
        if auto_fetch:
            hourly_dir = REPO_ROOT / "prices" / "hourly"
            hourly_dir.mkdir(parents=True, exist_ok=True)
            for ticker in tickers:
                if not (hourly_dir / f"{ticker}.csv").exists():
                    console.print(f"  [dim]fetching {ticker} hourly...[/dim]")
                    fetch_ticker(ticker, prices_dir=hourly_dir, interval="1h", prepost=True, quiet=True)
        for ticker in tickers:
            poc = compute_poc(ticker, mode=mode)
            mids = [m for _, m in poc] if poc is not None else []
            if len(mids) == 3 and all(m is not None for m in mids):
                poc_str = fmt_tuple(*mids)
            else:
                poc_str = "n/a"
            cells[ticker]["poc"] = [poc_str] + ["—"] * (len(columns) - 1)

    as_of_note = f" [dim](as of {today})[/dim]" if as_of else ""
    tags_note = f"\n[dim]{' · '.join(tags)}[/dim]" if tags else ""
    mode_label = "[dim]weekly[/dim]" if mode == "weeks" else "[dim]monthly[/dim]"
    table = Table(
        title=f"[bold cyan]{symbol}[/bold cyan]{' vs benchmarks' if benchmarks else ''}  {mode_label}{as_of_note}{tags_note}",
        show_header=True,
        header_style="bold",
        show_lines=False,
    )
    table.add_column("", min_width=8)     # metric label
    table.add_column("", min_width=9)     # ticker
    for col in columns:
        table.add_column(col["label"], justify="right", min_width=20)

    # price — stock only
    table.add_row("price", symbol, *cells[symbol]["price"], style="bold", end_section=True)

    # return — all tickers
    for i, ticker in enumerate(tickers):
        is_last = i == len(tickers) - 1
        style = "bold" if ticker == symbol else ""
        label = "return" if i == 0 else ""
        table.add_row(label, ticker, *cells[ticker]["return_"], style=style, end_section=is_last)

    # SMA — all tickers
    for i, ticker in enumerate(tickers):
        is_last = i == len(tickers) - 1
        style = "bold" if ticker == symbol else ""
        label = "SMA" if i == 0 else (sma_label if i == 1 else "")
        table.add_row(label, ticker, *cells[ticker]["sma"], style=style, end_section=is_last)

    # vol — all tickers
    for i, ticker in enumerate(tickers):
        is_last = i == len(tickers) - 1
        style = "bold" if ticker == symbol else ""
        label = "rvol" if i == 0 else (sma_label if i == 1 else "")
        table.add_row(label, ticker, *cells[ticker]["vol"], style=style, end_section=is_last)

    if not as_of:
        for i, ticker in enumerate(tickers):
            is_last = i == len(tickers) - 1
            style = "bold" if ticker == symbol else ""
            label = "IV" if i == 0 else ""
            table.add_row(label, ticker, *cells[ticker]["iv"], style=style, end_section=is_last)

    if not as_of:
        for i, ticker in enumerate(tickers):
            is_last = i == len(tickers) - 1
            style = "bold" if ticker == symbol else ""
            label = "POC" if i == 0 else (sma_label if i == 1 else "")
            table.add_row(label, ticker, *cells[ticker]["poc"], style=style, end_section=is_last)

    console.print(table)
    print_heuristics(symbol, tags or [], cells, tickers)
    console.print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyse prices vs benchmarks.")
    parser.add_argument("tickers", nargs="+", help="Stocks to analyse")
    parser.add_argument("--as-of", metavar="DATE", help="Simulate analysis as of this date (YYYY-MM-DD)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--weeks", action="store_true", help="Weekly view only")
    group.add_argument("--months", action="store_true", help="Monthly view only")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    if args.weeks:
        modes = ["weeks"]
    elif args.months:
        modes = ["months"]
    else:
        modes = ["weeks", "months"]
    requested = {t.upper() for t in args.tickers}

    config = load_config()
    known = {stock["symbol"]: stock for stock in config.get("stocks", [])}

    for symbol in requested:
        stock = known.get(symbol)
        for mode in modes:
            if stock:
                analyze_stock(symbol, stock.get("benchmarks", []), as_of=as_of, tags=stock.get("tags", []), mode=mode, auto_fetch=True)
            else:
                analyze_stock(symbol, [], as_of=as_of, mode=mode, auto_fetch=True)


if __name__ == "__main__":
    main()
