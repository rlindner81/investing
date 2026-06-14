#!/usr/bin/env python3
"""
fetch_prices.py — Download OHLCV price data via yfinance.

Daily data is stored under prices/daily/<TICKER>.csv with a plain Date index.
Hourly data is stored under prices/hourly/<TICKER>.csv with a UTC Datetime index.
Hourly includes pre- and post-market bars (volume is 0 outside regular session —
a Yahoo Finance limitation). History depth is ~2-3 years for hourly.

On first run the full history from START_DATE is fetched. On subsequent runs
the missing tail is appended — unless a split occurred since the last stored
date, in which case the full file is re-downloaded so prices stay consistent.

Usage:
  uv run fetch-prices                   # both daily and hourly, all tickers from TICKERS.yml
  uv run fetch-prices --daily           # daily only
  uv run fetch-prices --hourly          # hourly only
  uv run fetch-prices SPY QQQ           # both, specific tickers
  uv run fetch-prices --hourly SPY QQQ  # hourly only, specific tickers
"""

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml
import yfinance as yf

from investing.lib import REPO_ROOT

START_DATE = date(2025, 1, 1)
TICKERS_FILE = REPO_ROOT / "TICKERS.yml"


def load_tickers() -> list[str]:
    with TICKERS_FILE.open() as f:
        data = yaml.safe_load(f)
    b = data.get("benchmarks", {})
    etfs  = [e["symbol"] for e in b.get("etfs",  [])]
    peers = [p["symbol"] for p in b.get("peers", [])]
    stocks = [s["symbol"] for s in data.get("stocks", [])]
    return etfs + peers + stocks


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


def fetch_ticker(ticker: str, *, prices_dir: Path, interval: str, prepost: bool) -> None:
    path = prices_dir / f"{ticker}.csv"
    index_col = "Datetime" if prepost else "Date"
    last = load_last_date(path, index_col)

    if last is not None:
        fetch_start = last + timedelta(days=1)
        if fetch_start > date.today():
            print(f"  {ticker}: already up to date ({last})")
            return

        split_dates = splits_since(ticker, last)
        if split_dates:
            print(f"  {ticker}: split(s) detected on {split_dates} — full re-download")
            df = download(ticker, START_DATE, interval=interval, prepost=prepost)
            if df.empty:
                print(f"  {ticker}: no data returned")
                return
            df.to_csv(path, float_format="%.6g")
            print(f"  {ticker}: rewrote {len(df)} rows → {path.relative_to(REPO_ROOT)}")
            return
    else:
        fetch_start = START_DATE

    print(f"  {ticker}: appending {fetch_start} → today")
    df = download(ticker, fetch_start, interval=interval, prepost=prepost)
    if df.empty:
        print(f"  {ticker}: no data returned")
        return

    df = df[~df.index.duplicated(keep="last")]
    df.to_csv(path, mode="a" if path.exists() else "w", header=not path.exists(), float_format="%.6g")
    print(f"  {ticker}: wrote {len(df)} rows → {path.relative_to(REPO_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch OHLCV price data.")
    parser.add_argument("tickers", nargs="*", help="Tickers to fetch (default: TICKERS.yml)")
    parser.add_argument("--daily", action="store_true", help="Fetch daily candles only")
    parser.add_argument("--hourly", action="store_true", help="Fetch hourly candles only")
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers] if args.tickers else load_tickers()

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
