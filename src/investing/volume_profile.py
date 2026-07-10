#!/usr/bin/env python3
"""
volume_profile.py — Compute the Volume Profile Point of Control (POC).

Loads hourly candles and, for each requested lookback window, splits that
window's full price range into 100 equal buckets and finds the bucket with the
most cumulative volume. Returns the midpoint of the winning bucket per window.
Results are cached for the calendar month.

Cache files: prices/volume_profile/<TICKER>.json
"""

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from investing.lib import REPO_ROOT

PRICES_HOURLY = REPO_ROOT / "prices" / "hourly"
CACHE_DIR = REPO_ROOT / "prices" / "volume_profile"

BUCKET_COUNT = 100
CANDLE_INTERVAL = "1h"

# Lookback windows per view mode, mirroring the SMA/rvol periods, which count
# days (daily bars). The label IS the day count.
WEEKS_WINDOWS = (5, 10, 20)
MONTHS_WINDOWS = (20, 50, 200)


def _poc_midpoint(df: pd.DataFrame, window_days: int, anchor_date: date) -> float | None:
    """Midpoint of the highest-volume bucket over the trailing ``window_days``."""
    window_start = pd.Timestamp(anchor_date - timedelta(days=window_days), tz="UTC")
    win = df[df.index >= window_start]

    if win.empty:
        return None

    price_low = float(win["Low"].min())
    price_high = float(win["High"].max())
    if price_high <= price_low:
        return None

    edges = np.linspace(price_low, price_high, BUCKET_COUNT + 1)
    mid_prices = (win["High"].to_numpy() + win["Low"].to_numpy()) / 2
    volumes = win["Volume"].to_numpy()

    # np.digitize returns 1-based bucket indices; clip to valid range
    indices = np.digitize(mid_prices, edges) - 1
    indices = np.clip(indices, 0, BUCKET_COUNT - 1)

    bucket_volumes = np.zeros(BUCKET_COUNT)
    np.add.at(bucket_volumes, indices, volumes)

    poc_idx = int(bucket_volumes.argmax())
    return float((edges[poc_idx] + edges[poc_idx + 1]) / 2)


def compute_poc(ticker: str, mode: str = "months") -> list[tuple[int, float | None]] | None:
    """Return POC midpoints for the three lookback windows of ``mode``.

    ``mode`` is ``"weeks"`` (5/10/20-week windows) or ``"months"``
    (20/50/200-month windows). Each entry is ``(window_label, poc_midpoint)``
    where the label is the period count (weeks or months). Loads hourly candles
    and, per window, builds a 100-bucket volume profile over that window's price
    range. Results are cached for the calendar month.
    """
    windows = WEEKS_WINDOWS if mode == "weeks" else MONTHS_WINDOWS
    anchor_month = date.today().strftime("%Y-%m")
    cache_key = f"{mode}"

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{ticker}.json"

    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        entry = cached.get(cache_key)
        if entry is not None and entry.get("anchor_month") == anchor_month:
            return [(w["label"], w["poc"]) for w in entry["windows"]]

    hourly_path = PRICES_HOURLY / f"{ticker}.csv"
    if not hourly_path.exists():
        return None

    df = pd.read_csv(hourly_path, index_col="Datetime", parse_dates=True)
    df = df[~df.index.duplicated(keep="last")]
    df = df[df["Volume"] > 0]

    today = date.today()
    anchor_date = date(today.year, today.month, 1)

    results: list[tuple[int, float | None]] = []
    window_payload = []
    for window_days in windows:
        poc = _poc_midpoint(df, window_days, anchor_date)
        results.append((window_days, poc))
        window_payload.append(
            {
                "label": window_days,
                "window_days": window_days,
                "poc": round(poc, 6) if poc is not None else None,
            }
        )

    cached = {}
    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
    cached[cache_key] = {
        "anchor_month": anchor_month,
        "bucket_count": BUCKET_COUNT,
        "candle_interval": CANDLE_INTERVAL,
        "windows": window_payload,
    }
    cache_path.write_text(json.dumps(cached, indent=2))

    return results
