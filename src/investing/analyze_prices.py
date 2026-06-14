#!/usr/bin/env python3
"""
analyze_prices.py — Compare tracked stocks against their benchmarks.

Columns: current month (MTD) plus three prior full calendar months.
Each ticker gets its own row group:
  - price  (stock only)
  - return over the period
  - SMA alignment: sign prefix + period order high→low (e.g. ↑ 10/20/50)

Usage:
  uv run analyze-prices            # all tracked stocks
  uv run analyze-prices ODD SNAP   # specific stocks
"""

import sys
import argparse
from datetime import date

import pandas as pd
import yaml
from dateutil.relativedelta import relativedelta
from rich.console import Console
from rich.table import Table

from investing.lib import REPO_ROOT

PRICES_DAILY = REPO_ROOT / "prices" / "daily"
TICKERS_FILE = REPO_ROOT / "TICKERS.yml"

console = Console()


def load_config() -> dict:
    with TICKERS_FILE.open() as f:
        return yaml.safe_load(f)


def load_prices(ticker: str) -> pd.DataFrame | None:
    path = PRICES_DAILY / f"{ticker}.csv"
    if not path.exists():
        console.print(f"  [yellow]warning:[/yellow] no price file for {ticker}")
        return None
    df = pd.read_csv(path, index_col="Date", parse_dates=True)
    return df[~df.index.duplicated(keep="last")]


def month_bounds(df: pd.DataFrame, year: int, month: int) -> tuple:
    days = df.index[(df.index.year == year) & (df.index.month == month)]
    return (days[0], days[-1]) if len(days) else (None, None)


def nearest_on_or_before(df: pd.DataFrame, target: date) -> pd.Timestamp | None:
    ts = pd.Timestamp(target)
    prior = df.index[df.index <= ts]
    return prior[-1] if len(prior) else None


def fmt_price(v: float) -> str:
    if v >= 100:
        return f"{v:.0f}"
    if v >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}"


def trend_label(a: float, b: float, c: float, tol: float = 0.005) -> str:
    """↑/↓/~ for the sequence a→b→c.
    Values within tol (0.5%) of each other are treated as equal.
    Mixed only when one gap clearly goes up and the other clearly goes down."""
    def cmp(x: float, y: float) -> int:
        diff = abs(x - y) / max(abs(x), abs(y))
        if diff < tol:
            return 0
        return 1 if x > y else -1

    d1 = cmp(a, b)
    d2 = cmp(b, c)
    if (d1 == 1 and d2 == -1) or (d1 == -1 and d2 == 1):
        return "~"
    if d1 >= 0 and d2 >= 0:
        return "↑"
    return "↓"


def fmt_tuple(a: float, b: float, c: float) -> str:
    s1 = ">" if a >= b else "<"
    s2 = ">" if b >= c else "<"
    return f"{fmt_price(a)}{s1}{fmt_price(b)}{s2}{fmt_price(c)}"


def sma_alignment(df: pd.DataFrame, as_of: pd.Timestamp) -> str:
    loc = df.index.get_loc(as_of)
    if loc < 49:
        return "n/a"
    close = df["Close"].iloc
    sma10 = close[loc - 9  : loc + 1].mean()
    sma20 = close[loc - 19 : loc + 1].mean()
    sma50 = close[loc - 49 : loc + 1].mean()

    t = trend_label(sma10, sma20, sma50)
    vals = fmt_tuple(sma10, sma20, sma50)
    if t == "↑":
        return f"[green]↑ {vals}[/green]"
    if t == "↓":
        return f"[red]↓ {vals}[/red]"
    return vals


def rvol(df: pd.DataFrame, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> str:
    loc = df.index.get_loc(end_ts)
    if loc < 49:
        return "n/a"
    vol = df["Volume"].iloc
    period_avg = vol[df.index.get_loc(start_ts) : loc + 1].mean()
    v10 = vol[loc - 9  : loc + 1].mean()
    v20 = vol[loc - 19 : loc + 1].mean()
    v50 = vol[loc - 49 : loc + 1].mean()

    r10, r20, r50 = period_avg/v10, period_avg/v20, period_avg/v50
    s1 = ">" if r10 >= r20 else "<"
    s2 = ">" if r20 >= r50 else "<"
    ratios = f"{r10*100:.0f}%{s1}{r20*100:.0f}%{s2}{r50*100:.0f}%"

    t = trend_label(v10, v20, v50)
    if t == "↑":
        return f"[green]↑ {ratios}[/green]"
    if t == "↓":
        return f"[red]↓ {ratios}[/red]"
    return ratios


def period_snapshot(df: pd.DataFrame, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> tuple:
    if start_ts is None or end_ts is None or start_ts >= end_ts:
        return None, None, None, None
    start_price = float(df["Close"].iloc[df.index.get_loc(start_ts)])
    end_price   = float(df["Close"].iloc[df.index.get_loc(end_ts)])
    pct = (end_price / start_price - 1) * 100
    return end_price, pct, sma_alignment(df, end_ts), rvol(df, start_ts, end_ts)


def fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "—"
    return f"[green]+{pct:.1f}%[/green]" if pct >= 0 else f"[red]{pct:.1f}%[/red]"


def analyze_stock(symbol: str, benchmarks: list[str], as_of: date | None = None) -> None:
    today = as_of or date.today()
    cur = date(today.year, today.month, 1)
    months = [cur - relativedelta(months=i) for i in range(4)]

    tickers = [symbol] + benchmarks
    data = {t: load_prices(t) for t in tickers}

    ref_df = data[symbol]
    if ref_df is None:
        return

    # Build column definitions using the stock's own calendar as reference
    columns = []
    for i, m in enumerate(months):
        year, month = m.year, m.month
        ref_start, ref_end = month_bounds(ref_df, year, month)
        if i == 0:
            ref_end = nearest_on_or_before(ref_df, today)
            label = (f"{m.strftime('%b')} MTD\n"
                     f"{ref_start.strftime('%b %-d') if ref_start else '?'}"
                     f" → {ref_end.strftime('%b %-d') if ref_end else '?'}")
        else:
            label = (f"{m.strftime('%b %Y')}\n"
                     f"{ref_start.strftime('%b %-d') if ref_start else '?'}"
                     f" → {ref_end.strftime('%b %-d') if ref_end else '?'}")
        columns.append((year, month, i == 0, label))

    # Pre-compute all cells per ticker
    cells = {}
    for ticker in tickers:
        is_stock = ticker == symbol
        df = data[ticker]
        price_cells, return_cells, sma_cells, vol_cells = [], [], [], []

        for year, month, is_current, _ in columns:
            if df is None:
                price_cells.append("—")
                return_cells.append("—")
                sma_cells.append("—")
                vol_cells.append("—")
                continue
            t_start, t_end = month_bounds(df, year, month)
            if is_current:
                t_end = nearest_on_or_before(df, today)
            price, pct, sma, vol = period_snapshot(df, t_start, t_end)
            price_cells.append(f"{price:.2f}" if price is not None else "—")
            return_cells.append(fmt_pct(pct))
            sma_cells.append(sma or "—")
            vol_cells.append(vol or "—")

        cells[ticker] = dict(price=price_cells, return_=return_cells, sma=sma_cells, vol=vol_cells)

    as_of_note = f" [dim](as of {today})[/dim]" if as_of else ""
    table = Table(
        title=f"[bold cyan]{symbol}[/bold cyan] vs benchmarks{as_of_note}",
        show_header=True,
        header_style="bold",
        show_lines=False,
    )
    table.add_column("", min_width=8)     # metric label
    table.add_column("", min_width=9)     # ticker
    for *_, label in columns:
        table.add_column(label, justify="right", min_width=20)

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
        label = "SMA" if i == 0 else ("10/20/50" if i == 1 else "")
        table.add_row(label, ticker, *cells[ticker]["sma"], style=style, end_section=is_last)

    # vol — all tickers
    for i, ticker in enumerate(tickers):
        is_last = i == len(tickers) - 1
        style = "bold" if ticker == symbol else ""
        label = "rvol" if i == 0 else ("10/20/50" if i == 1 else "")
        table.add_row(label, ticker, *cells[ticker]["vol"], style=style, end_section=is_last)

    console.print(table)
    console.print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyse prices vs benchmarks.")
    parser.add_argument("tickers", nargs="*", help="Stocks to analyse (default: all)")
    parser.add_argument("--as-of", metavar="DATE", help="Simulate analysis as of this date (YYYY-MM-DD)")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    requested = {t.upper() for t in args.tickers}

    config = load_config()
    for stock in config.get("stocks", []):
        symbol = stock["symbol"]
        if requested and symbol not in requested:
            continue
        analyze_stock(symbol, stock.get("benchmarks", []), as_of=as_of)


if __name__ == "__main__":
    main()
