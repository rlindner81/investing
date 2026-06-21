#!/usr/bin/env python3
"""
volume_profile.py — Compute the Volume Profile Point of Control (POC).

Loads 2 years of hourly candles, splits the full price range into 100 equal
buckets, and finds the bucket with the most cumulative volume. Result is cached
for the calendar month.

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

WINDOW_DAYS = 730
BUCKET_COUNT = 100
CANDLE_INTERVAL = "1h"


def compute_poc(ticker: str) -> tuple[float, float] | None:
    """Return (poc_low, poc_high) for the dominant volume bucket over the past 2 years.

    Loads hourly candles, filters to a 2-year window anchored at the first of the
    current month, builds a 100-bucket volume profile, and returns the price range
    of the bucket with the highest cumulative volume. Result is cached for the month.
    """
    anchor_month = date.today().strftime("%Y-%m")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{ticker}.json"

    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        if cached.get("anchor_month") == anchor_month:
            return cached["poc_low"], cached["poc_high"]

    hourly_path = PRICES_HOURLY / f"{ticker}.csv"
    if not hourly_path.exists():
        return None

    df = pd.read_csv(hourly_path, index_col="Datetime", parse_dates=True)
    df = df[~df.index.duplicated(keep="last")]

    today = date.today()
    anchor_date = date(today.year, today.month, 1)
    window_start = pd.Timestamp(anchor_date - timedelta(days=WINDOW_DAYS), tz="UTC")
    df = df[df.index >= window_start]
    df = df[df["Volume"] > 0]

    if len(df) < BUCKET_COUNT:
        return None

    price_low = float(df["Low"].min())
    price_high = float(df["High"].max())
    if price_high <= price_low:
        return None

    edges = np.linspace(price_low, price_high, BUCKET_COUNT + 1)
    mid_prices = (df["High"].to_numpy() + df["Low"].to_numpy()) / 2
    volumes = df["Volume"].to_numpy()

    # np.digitize returns 1-based bucket indices; clip to valid range
    indices = np.digitize(mid_prices, edges) - 1
    indices = np.clip(indices, 0, BUCKET_COUNT - 1)

    bucket_volumes = np.zeros(BUCKET_COUNT)
    np.add.at(bucket_volumes, indices, volumes)

    poc_idx = int(bucket_volumes.argmax())
    poc_low = float(edges[poc_idx])
    poc_high = float(edges[poc_idx + 1])
    poc_volume = int(bucket_volumes[poc_idx])

    payload = {
        "anchor_month": anchor_month,
        "window_days": WINDOW_DAYS,
        "bucket_count": BUCKET_COUNT,
        "candle_interval": CANDLE_INTERVAL,
        "price_range_low": round(price_low, 6),
        "price_range_high": round(price_high, 6),
        "poc_bucket_index": poc_idx,
        "poc_low": round(poc_low, 6),
        "poc_high": round(poc_high, 6),
        "poc_volume": poc_volume,
    }
    cache_path.write_text(json.dumps(payload, indent=2))

    return poc_low, poc_high
