#!/usr/bin/env python3
"""
fetch_prices.py — Download OHLCV price data via yfinance.

Daily data is stored under prices/daily/<TICKER>.csv with a plain Date index.
Hourly data is stored under prices/hourly/<TICKER>.csv with a UTC Datetime index.
Hourly includes pre- and post-market bars (volume is 0 outside regular session —
a Yahoo Finance limitation). History depth is ~2-3 years for hourly.

On first run the full history from START_DATE_DAILY / START_DATE_HOURLY is fetched. On subsequent runs
the missing tail is appended — unless a split occurred since the last stored
date, in which case the full file is re-downloaded so prices stay consistent.

Usage:
  uv run fetch-prices                   # both daily and hourly, all tickers from TICKERS.yml
  uv run fetch-prices --daily           # daily only
  uv run fetch-prices --hourly          # hourly only
  uv run fetch-prices SPY QQQ           # both, specific tickers
  uv run fetch-prices --hourly SPY QQQ  # hourly only, specific tickers

Naming a tracked stock also fetches its benchmarks (from TICKERS.yml), so a
subsequent `check-prices <stock>` finds every file it needs already present.
"""

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml
import yfinance as yf

from investing.lib import REPO_ROOT

START_DATE_DAILY  = date(2000, 1, 1)
START_DATE_HOURLY = date(2024, 1, 1)
TICKERS_FILE = REPO_ROOT / "TICKERS.yml"


def load_config() -> dict:
    with TICKERS_FILE.open() as f:
        return yaml.safe_load(f)


def expand_with_benchmarks(tickers: list[str]) -> list[str]:
    """For any requested ticker that is a known stock, append its benchmarks so
    a later `check-prices <ticker>` finds every file it needs already fetched.
    Order-preserving and de-duplicated; benchmarks follow their stock."""
    benchmarks = {
        s["symbol"]: s.get("benchmarks", []) for s in load_config().get("stocks", [])
    }
    result: list[str] = []
    for t in tickers:
        for sym in [t, *benchmarks.get(t, [])]:
            if sym not in result:
                result.append(sym)
    return result


def load_last_date(path: Path, index_col: str) -> date | None:
    """Return the date of the last stored row, regardless of whether the index
    is a plain date or a full UTC datetime string."""
    if not path.exists():
        return None
    with path.open() as f:
        last = None
        for row in csv.DictReader(f):
            last = row[index_col]
    return date.fromisoformat(last[:10]) if last else None


def splits_since(ticker: str, since: date) -> list[date]:
    s = yf.Ticker(ticker).splits
    return list(s.index.date[s.index.date > since])


def _same_boundary(old_tail: pd.DataFrame, fresh_tail: pd.DataFrame) -> bool:
    """True when the stored boundary row equals the freshly-fetched one, compared
    at the 6-significant-digit precision the CSV is written with (so we don't
    rewrite the file over floating-point noise). Only the first (boundary) row
    matters — new days are handled separately."""
    if old_tail.empty or fresh_tail.empty:
        return False

    def fmt(v: float) -> str:
        return f"{float(v):.6g}"

    old = old_tail.iloc[0]
    new = fresh_tail.iloc[0]
    cols = ["Open", "High", "Low", "Close", "Volume"]
    return all(fmt(old[c]) == fmt(new[c]) for c in cols)


def download(ticker: str, start: date, *, interval: str, prepost: bool) -> pd.DataFrame:
    df = yf.download(
        ticker,
        start=start.isoformat(),
        interval=interval,
        auto_adjust=True,
        prepost=prepost,
        progress=False,
    )
    if df.empty:
        return df
    df = df[["Open", "High", "Low", "Close", "Volume"]]
    df.columns = df.columns.droplevel("Ticker")
    if prepost:
        df.index = df.index.tz_convert("UTC")
        df.index.name = "Datetime"
    else:
        df.index = df.index.date
        df.index.name = "Date"
    return df


def fetch_live_price(ticker: str) -> float | None:
    """Return the current market price via yfinance fast_info (not saved to disk)."""
    try:
        price = yf.Ticker(ticker).fast_info.last_price
        return float(price) if price else None
    except Exception:
        return None


def fetch_iv(ticker: str, price: float) -> float | None:
    """Return the ATM annualized IV for ticker from the nearest options expiry."""
    try:
        t = yf.Ticker(ticker)
        exps = t.options
        if not exps:
            return None
        today_d = date.today()
        exp_dates = [date.fromisoformat(e) for e in exps]
        future = [e for e in exp_dates if (e - today_d).days >= 3]
        if not future:
            return None
        exp = min(future, key=lambda e: abs((e - today_d).days - 7))
        chain = t.option_chain(exp.isoformat())
        calls, puts = chain.calls, chain.puts
        atm = calls.loc[(calls["strike"] - price).abs().idxmin(), "strike"]
        c_iv = calls.loc[calls["strike"] == atm, "impliedVolatility"].values
        p_iv = puts.loc[puts["strike"] == atm, "impliedVolatility"].values
        if len(c_iv) and len(p_iv):
            return (c_iv[0] + p_iv[0]) / 2
    except Exception:
        pass
    return None


def fetch_ticker(ticker: str, *, prices_dir: Path, interval: str, prepost: bool, quiet: bool = False) -> None:
    path = prices_dir / f"{ticker}.csv"
    index_col = "Datetime" if prepost else "Date"
    start_date = START_DATE_HOURLY if prepost else START_DATE_DAILY
    last = load_last_date(path, index_col)

    if last is not None:
        # Re-fetch FROM the last stored day (inclusive), not the day after. The
        # boundary row may have been written from a partial/live bar (e.g. a
        # mid-session Monday pull → truncated volume and a non-final close); by
        # re-downloading it we heal that stale bar once the session has closed.
        fetch_start = last

        split_dates = splits_since(ticker, last)
        if split_dates:
            print(f"  {ticker}: split(s) detected on {split_dates} — full re-download")
            df = download(ticker, start_date, interval=interval, prepost=prepost)
            if df.empty:
                print(f"  {ticker}: no data returned")
                return
            df.to_csv(path, float_format="%.6g")
            print(f"  {ticker}: rewrote {len(df)} rows → {path.relative_to(REPO_ROOT)}")
            return
    else:
        fetch_start = start_date
        if prepost:
            # Yahoo Finance caps hourly history at 730 days
            earliest = date.today() - timedelta(days=729)
            fetch_start = max(fetch_start, earliest)

    fresh = download(ticker, fetch_start, interval=interval, prepost=prepost)
    if fresh.empty:
        if not quiet:
            print(f"  {ticker}: no new data ({last})" if last else f"  {ticker}: no data returned")
        return

    fresh = fresh[~fresh.index.duplicated(keep="last")]

    if last is not None:
        # Splice: keep on-disk rows strictly before the boundary, then take the
        # freshly-fetched rows (boundary + any new days). This overwrites the
        # boundary row rather than appending a duplicate, so a stale partial bar
        # is corrected in place.
        existing = pd.read_csv(path, index_col=index_col)
        existing_dates = pd.to_datetime(pd.Series(existing.index)).dt.date.to_numpy()
        head = existing[existing_dates < last]

        fresh_dates = pd.to_datetime(pd.Series(fresh.index)).dt.date.to_numpy()
        tail = fresh[fresh_dates >= last]
        if tail.empty:
            if not quiet:
                print(f"  {ticker}: already up to date ({last})")
            return

        # No-op guard: if the refreshed boundary+tail is identical to what's on
        # disk (same dates and rounded values), don't rewrite the file.
        old_tail = existing[existing_dates >= last]
        boundary_changed = not _same_boundary(old_tail, tail)
        n_new = int((fresh_dates > last).sum())
        if not boundary_changed and n_new == 0:
            if not quiet:
                print(f"  {ticker}: already up to date ({last})")
            return

        combined = pd.concat([head, tail])
        combined.index.name = index_col
        combined.to_csv(path, float_format="%.6g")
        healed = " (healed boundary bar)" if boundary_changed else ""
        print(f"  {ticker}: refreshed {last}{healed}, +{n_new} new → {path.relative_to(REPO_ROOT)}")
        return

    fresh.to_csv(path, mode="w", header=True, float_format="%.6g")
    print(f"  {ticker}: wrote {len(fresh)} rows → {path.relative_to(REPO_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch OHLCV price data.")
    parser.add_argument("tickers", nargs="+", help="Tickers to fetch")
    parser.add_argument("--daily", action="store_true", help="Fetch daily candles only")
    parser.add_argument("--hourly", action="store_true", help="Fetch hourly candles only")
    args = parser.parse_args()

    tickers = expand_with_benchmarks([t.upper() for t in args.tickers])

    modes = []
    if not args.daily and not args.hourly:
        modes = [("daily", "1d", False), ("hourly", "1h", True)]
    elif args.daily:
        modes = [("daily", "1d", False)]
    else:
        modes = [("hourly", "1h", True)]

    for name, interval, prepost in modes:
        prices_dir = REPO_ROOT / "prices" / name
        prices_dir.mkdir(parents=True, exist_ok=True)
        for ticker in tickers:
            fetch_ticker(ticker, prices_dir=prices_dir, interval=interval, prepost=prepost)


if __name__ == "__main__":
    main()
