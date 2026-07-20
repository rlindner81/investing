#!/usr/bin/env python3
"""
find_shape.py — Find historical chart windows that match a current pattern.

Given a query window (the last L trading days of a repo ticker), search a large
historical universe (every Stooq ``prices-historic/d_*_txt.zip`` bundle — US, JP,
UK, HK, World) for windows whose *shape* — in both price and volume — most
closely matches, then report what happened next.

Matching is scale-invariant: each window is z-normalized, so a move from 10→11
matches one from 100→110. Price and volume are normalized separately and their
distances combined with a weight (``--price-weight``). The current pattern is
found via MASS (Mueen's Algorithm for Similarity Search): a z-normalized
Euclidean distance profile computed in one FFT pass per series.

Scale-invariance has a blind spot: it also matches instruments that are nothing
like the query — a delisted stub whose split-adjusted price reads $70B/share, or
a dead ticker trading 0 shares/day, has the same *shape* as a real large-cap and
scores identically. ``--max-scale-ratio`` re-imposes the discarded scale: a
candidate window whose price OR volume mean diverges from the query's by more
than that factor (either direction) is rejected. This keeps legitimately
different-priced but liquid analogues while dropping the phantom split-artifacts
and zero-volume husks.

The search is *anytime*: it streams tickers out of the zips (never unpacking
them), keeps a bounded top-k of the best matches so far with early-abandoning,
and stops when a wall-clock ``--budget-secs`` is hit — returning the best matches
found up to that point. This makes the enormous universe tractable. By default
only real equity segments are searched (``DEFAULT_SEGMENTS``); narrow further with
``--segments`` (instrument class) and ``--markets`` (country).

Output is not statistical-summary but a denormalized projection: each match's
last 5 window bars and next ``--forward`` bars are z-denormalized onto the query's
own price/volume scale (invert the candidate's z-scores through the query's
window mean/std). The query's actual last 5 bars head the table as a reference,
so match quality is directly eyeballable and the future bars read as a projected
path in the query's own dollars. Per-bar mean±std across the full ``--top-k`` pool
follows, including the deviation of the past bars from the query's actuals. The
window-start date (the -5 bar) sits after each ticker — including the reference —
as the anchor to open the match in an external chart (the daily feed has no
intraday time). Only matches with a full ``--forward`` window are kept.

Search results are cached under ``prices-historic/recent/`` for one hour, keyed
on every search-affecting input plus a hash of the query bars — so iterating on
display (``--show``, layout) re-renders instantly while a real input change or a
data re-fetch recomputes. ``--refresh`` forces recomputation; stale (>1h) entries
are pruned automatically.

Usage:
  uv run find-shape ODD                       # last 30 bars of ODD, default search
  uv run find-shape ODD --query-len 40 --forward 20 --top-k 25 --show 10
  uv run find-shape ODD --budget-secs 120 --price-weight 0.7
  uv run find-shape ODD --segments "nyse stocks,nasdaq stocks"
  uv run find-shape ODD --markets us,uk --segments "nyse stocks,lse stocks"

Query source is the repo CSV ``prices/daily/<TICKER>.csv`` (see fetch-prices).
Matches may come from any historical ticker including the query ticker itself;
near-perfect matches (combined distance below ``--min-distance``) are dropped so
the query's own recent window, a duplicate, or a cross-market dual listing of the
same series can't surface as a trivial self-match — independent of ticker.
"""

import argparse
import csv
import hashlib
import heapq
import io
import json
import pickle
import time
import zipfile
from datetime import date
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from rich.console import Console
from rich.table import Table

from investing.lib import REPO_ROOT

PRICES_DIR = REPO_ROOT / "prices" / "daily"
ARCHIVE_DIR = REPO_ROOT / "prices-historic"
CACHE_DIR = ARCHIVE_DIR / "recent"
CACHE_TTL_SECS = 3600  # search results are reused for an hour, then recomputed

# Country suffixes stripped from a file stem to recover a bare symbol. World
# indices carry no suffix; crypto/bonds use .v/.b.
_SUFFIXES = (".us", ".jp", ".uk", ".hk", ".v", ".b")

# Default universe: real equity segments only. Excludes warrants, CBBCs, ETFs,
# options, bonds, crypto, FX, indices — instruments whose z-normalized shape
# matches a stock but whose behaviour is nothing like it. Override with --segments.
DEFAULT_SEGMENTS = (
    "nasdaq stocks", "nyse stocks", "nysemkt stocks",  # us
    "lse stocks", "lse stocks intl",                   # uk
    "tse stocks",                                      # jp
    "hkex stocks",                                     # hk
)

console = Console(width=160)


def archives() -> list[Path]:
    """Every Stooq daily bundle in prices-historic (d_<market>_txt.zip)."""
    return sorted(ARCHIVE_DIR.glob("d_*_txt.zip"))


# ---------------------------------------------------------------------------
# Distance core: MASS (z-normalized Euclidean distance profile via FFT)
# ---------------------------------------------------------------------------

def _sliding_mean_std(series: np.ndarray, m: int) -> tuple[np.ndarray, np.ndarray]:
    """Rolling mean and (population) std of every length-m window of `series`.

    Variance is computed as E[x²] − E[x]² from cumulative sums (fast, one pass).
    For a genuinely flat window that subtraction suffers catastrophic
    cancellation and leaves a tiny positive residual (~1e-7 × value²) instead of
    exactly 0. Left alone, that fake std is above an absolute flat-window
    threshold and slips a dead, carried-forward price stretch through as a
    "perfect" match. So snap any variance below a *relative* floor (a negligible
    fraction of the window's mean²) down to 0, marking such windows truly flat."""
    cumsum = np.concatenate(([0.0], np.cumsum(series)))
    cumsum2 = np.concatenate(([0.0], np.cumsum(series ** 2)))
    seg_sum = cumsum[m:] - cumsum[:-m]
    seg_sum2 = cumsum2[m:] - cumsum2[:-m]
    mean = seg_sum / m
    var = seg_sum2 / m - mean ** 2
    var = np.clip(var, 0.0, None)
    # Kill cancellation residue: variance under ~(1e-5·mean)² is really zero.
    var[var < (1e-5 * np.abs(mean)) ** 2] = 0.0
    return mean, np.sqrt(var)


def _sliding_dot(series: np.ndarray, kernel: np.ndarray, m: int, n: int) -> np.ndarray:
    """Sliding dot product: out[i] = sum_j kernel[j] * series[i+j], for each
    length-m window. Length n - m + 1. Computed via one FFT convolution."""
    rev = kernel[::-1]
    fft_len = 1
    while fft_len < n + m:
        fft_len <<= 1
    prod = np.fft.irfft(np.fft.rfft(series, fft_len) * np.fft.rfft(rev, fft_len), fft_len)
    return prod[m - 1 : n]  # length n - m + 1


def mass(query: np.ndarray, series: np.ndarray, weights: np.ndarray | None = None,
         min_cv: float = 0.0) -> np.ndarray:
    """Distance profile: z-normalized Euclidean distance from `query` (length m)
    to every length-m subsequence of `series`. Lower is more similar.

    Returns an array of length ``len(series) - m + 1``. Windows whose std is ~0
    (flat, degenerate) get +inf. Follows Mueen's MASS via FFT.

    `weights` (length m, non-negative) applies a per-bar weight to the squared
    error at each position, so some bars in the window count more toward the
    match than others. With `weights=None` (all ones) this is the standard
    unweighted MASS. Both the query and every window are z-normalized the same
    way (unweighted mean/std over the window) — only the *distance* is weighted,
    so the weighting reshapes what "close" means without distorting the scale
    normalization. The weighted form still resolves to a few FFT convolutions,
    so it costs the same as plain MASS.

    `min_cv` rejects near-flat, low-information windows: any window whose
    coefficient of variation (std/|mean|) is below `min_cv` gets +inf, using the
    per-window mean/std already computed here — no extra pass. 0 disables it
    (only exactly-flat windows are dropped, as before).
    """
    m = len(query)
    n = len(series)
    if n < m:
        return np.array([])

    q_mean = query.mean()
    q_std = query.std()
    if q_std < 1e-10:
        # Degenerate query — nothing meaningful to match on this channel.
        return np.zeros(n - m + 1)

    mean, std = _sliding_mean_std(series, m)
    a = (query - q_mean) / q_std  # z-normalized query (constant across windows)

    if weights is None:
        # Standard MASS: dist^2 = 2*m*(1 - corr), corr from a single dot product.
        qt = _sliding_dot(series, query, m, n)
        denom = m * std * q_std
        with np.errstate(divide="ignore", invalid="ignore"):
            corr = (qt - m * mean * q_mean) / denom
        dist_sq = 2 * m * (1 - corr)
    else:
        # Weighted z-normalized distance^2 = sum_j w_j (a_j - b_j)^2 with
        # b_j = (x_j - mean)/std the z-normalized window. Expanded into sliding
        # sums, each an FFT convolution against a fixed kernel:
        #   sum w*a^2                                    -> scalar A
        #   sum w*a*x  (kernel w*a),  sum w*a (scalar WA)
        #   sum w*x    (kernel w),    sum w*x^2 (kernel w),  sum w (scalar W)
        w = weights
        A = float(np.sum(w * a * a))
        WA = float(np.sum(w * a))
        W = float(np.sum(w))
        s_wax = _sliding_dot(series, w * a, m, n)
        s_wx = _sliding_dot(series, w, m, n)
        s_wx2 = _sliding_dot(series ** 2, w, m, n)
        with np.errstate(divide="ignore", invalid="ignore"):
            cross = (s_wax - mean * WA) / std              # sum w*a*b
            sq_b = (s_wx2 - 2 * mean * s_wx + mean ** 2 * W) / (std ** 2)  # sum w*b^2
            # Flat windows (std ~ 0) yield inf/nan here; masked to +inf below.
            dist_sq = A - 2 * cross + sq_b

    dist_sq = np.clip(dist_sq, 0.0, None)
    dist = np.sqrt(dist_sq)
    # Drop flat/near-flat windows: std under an absolute floor (exactly flat) or
    # under min_cv of the window's own scale (low-information, barely moving).
    floor = np.maximum(1e-10, min_cv * np.abs(mean))
    dist[std < floor] = np.inf
    return dist


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

@dataclass
class Series:
    ticker: str
    dates: np.ndarray       # ISO date strings
    close: np.ndarray
    volume: np.ndarray


def load_query(ticker: str, query_len: int) -> Series:
    path = PRICES_DIR / f"{ticker}.csv"
    if not path.exists():
        raise SystemExit(
            f"No price file at {path.relative_to(REPO_ROOT)} — run `uv run fetch-prices {ticker}` first."
        )
    dates, close, volume = [], [], []
    with path.open() as f:
        for row in csv.DictReader(f):
            dates.append(row["Date"][:10])
            close.append(float(row["Close"]))
            volume.append(float(row["Volume"]))
    if len(close) < query_len:
        raise SystemExit(f"{ticker} has only {len(close)} rows; need at least {query_len}.")
    return Series(
        ticker=ticker,
        dates=np.array(dates[-query_len:]),
        close=np.array(close[-query_len:], dtype=float),
        volume=np.array(volume[-query_len:], dtype=float),
    )


def warn_query_quality(query: Series) -> None:
    """Flag implausible bars in the query so a corrupt input print (e.g. a
    truncated volume) doesn't silently distort the z-normalized match. Detection
    only — the matching itself is left untouched.

    A bar is suspicious when its volume sits far from the window's median: below
    20% or above 5x. (Price gaps beyond 40% vs the prior close are also flagged.)
    Volume is the sensitive channel — a single 7x-off print skews the whole
    z-score — so it's checked against a robust median rather than the mean.
    """
    vol = query.volume
    med = np.median(vol[vol > 0]) if np.any(vol > 0) else 0.0
    flags: list[str] = []
    if med > 0:
        for d, v in zip(query.dates, vol):
            if v < 0.2 * med:
                flags.append(f"  {d}: volume {v:,.0f} is {v / med:.0%} of the window median ({med:,.0f}) — likely a truncated/partial bar")
            elif v > 5 * med:
                flags.append(f"  {d}: volume {v:,.0f} is {v / med:.1f}x the window median ({med:,.0f})")

    close = query.close
    for i in range(1, len(close)):
        if close[i - 1] > 0 and abs(close[i] / close[i - 1] - 1) > 0.40:
            flags.append(f"  {query.dates[i]}: close {close[i]:.2f} gaps {(close[i] / close[i - 1] - 1):+.0%} vs prior — check for a split/adjustment glitch")

    if flags:
        console.print(
            f"[yellow]⚠ query data quality[/] — {len(flags)} suspicious bar(s) in "
            f"{query.ticker}'s last {len(query.close)} days; these can distort the "
            f"z-normalized match:"
        )
        for line in flags:
            console.print(f"[yellow]{line}[/]")
        console.print()


def _parse_stooq_member(text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Parse a Stooq .txt member into (dates, close, volume). None if unusable."""
    lines = text.splitlines()
    if len(lines) < 2:
        return None
    dates, close, volume = [], [], []
    for line in lines[1:]:  # skip header
        parts = line.split(",")
        if len(parts) < 9:
            continue
        d = parts[2]
        dates.append(f"{d[:4]}-{d[4:6]}-{d[6:8]}")
        close.append(float(parts[7]))
        volume.append(float(parts[8]))
    if not close:
        return None
    return np.array(dates), np.array(close, dtype=float), np.array(volume, dtype=float)


def _member_meta(name: str) -> tuple[str, str, str] | None:
    """From an archive member path return (country, segment, ticker), or None if
    the path isn't a data member.

    Handles both layouts:
      data/daily/<country>/<segment>/<bucket>/<ticker><suffix>.txt   (us/jp/uk/hk)
      data/daily/<country>/<segment>/<ticker>.txt                    (world, no bucket)
    """
    if not name.endswith(".txt"):
        return None
    rel = name.split("data/daily/", 1)[-1]
    parts = rel.split("/")
    if len(parts) < 3:
        return None
    country, segment = parts[0], parts[1]
    stem = Path(parts[-1]).stem  # drops trailing ".txt"
    lower = stem.lower()
    for suf in _SUFFIXES:
        if lower.endswith(suf):
            stem = stem[: -len(suf)]
            break
    return country, segment, stem.upper()


def iter_universe(segments: list[str] | None, markets: list[str] | None):
    """Yield (ticker, dates, close, volume) across every archive, streaming.

    `segments` filters by the "<segment>" directory (e.g. "nyse stocks",
    "tse stocks"); `markets` filters by country ("us", "jp", "uk", "hk",
    "world"). None means no filter on that axis.
    """
    for archive in archives():
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                meta = _member_meta(name)
                if meta is None:
                    continue
                country, segment, ticker = meta
                if markets is not None and country not in markets:
                    continue
                if segments is not None and segment not in segments:
                    continue
                with z.open(name) as fh:
                    text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace").read()
                parsed = _parse_stooq_member(text)
                if parsed is None:
                    continue
                dates, close, volume = parsed
                yield ticker, dates, close, volume


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@dataclass(order=True)
class Match:
    # Ordered by -score so heapq (a min-heap) evicts the *worst* kept match.
    sort_key: float
    score: float = field(compare=False)
    ticker: str = field(compare=False)
    start_idx: int = field(compare=False)
    end_date: str = field(compare=False)
    # Raw candidate bars (the matched window and what followed), used for the
    # denormalized projection onto the query's scale.
    win_close: np.ndarray = field(default=None, compare=False)   # full matched window
    win_volume: np.ndarray = field(default=None, compare=False)
    fut_close: np.ndarray = field(default=None, compare=False)   # next N bars after window
    fut_volume: np.ndarray = field(default=None, compare=False)
    win_dates: np.ndarray = field(default=None, compare=False)   # ISO dates of the window bars
    fut_dates: np.ndarray = field(default=None, compare=False)   # ISO dates of the future bars


def recency_weights(m: int, bucket: int, floor: float) -> np.ndarray | None:
    """Per-bar weights (length m) that make the *recent* end of the window matter
    more in the match. Bars are grouped into `bucket`-sized blocks from the end;
    weights ramp *linearly* across blocks from 1.0 (most recent block) down to
    `floor` (oldest block), so the tail on display is weighted up without the
    recent bars swamping everything.

    With the default 5-bar buckets and floor 0.5 on a 30-bar window (6 blocks),
    the blocks carry weights 1.0, 0.9, 0.8, 0.7, 0.6, 0.5 from recent to old.
    Returns None (→ unweighted) when the weighting is a no-op (floor >= 1)."""
    if floor >= 1.0:
        return None
    idx_from_end = np.arange(m)[::-1]           # 0 for the last bar, m-1 for the first
    block = idx_from_end // max(1, bucket)      # 0 = most-recent block, higher = older
    n_blocks = int(block.max())                 # oldest block index
    if n_blocks == 0:                           # single block → uniform
        return None
    return 1.0 - (1.0 - floor) * (block.astype(float) / n_blocks)


def _scale_reject(series: np.ndarray, q_mean: float, m: int, max_ratio: float) -> np.ndarray:
    """Boolean mask (length len(series)-m+1) of windows whose mean is too far from
    the query channel's mean to be a comparable instrument.

    The match distance is z-normalized, so a window's price/volume *level* is
    divided out before scoring — a dead $70B-per-share split artifact or a
    zero-volume stub matches a real $244 stock's shape perfectly. This gate
    re-introduces the discarded scale: reject any window whose mean diverges from
    the query's by more than `max_ratio`× in either direction. Applied to the
    price and volume channels independently (means only). 0/None disables.

    Zero/near-zero means (dead-volume stubs) yield a ratio of 0 or inf and are
    rejected; a zero query mean disables the gate for that channel (no scale to
    compare against)."""
    if not max_ratio or max_ratio <= 0 or q_mean == 0:
        return np.zeros(len(series) - m + 1, dtype=bool)
    mean, _ = _sliding_mean_std(series, m)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.abs(mean) / abs(q_mean)
    # Reject outside [1/max_ratio, max_ratio]; NaN (0/0) also rejected.
    return ~((ratio >= 1.0 / max_ratio) & (ratio <= max_ratio))


def combined_profile(
    q_close: np.ndarray,
    q_volume: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    price_weight: float,
    weights: np.ndarray | None = None,
    min_cv: float = 0.0,
    max_scale_ratio: float = 0.0,
) -> np.ndarray | None:
    """Weighted price+volume distance profile for one series, or None if too short.

    `weights` is an optional per-bar recency weighting applied to both channels.
    `min_cv` drops near-flat, low-information windows (see `mass`); it is applied
    to the *price* channel — a barely-moving price is uninformative — and, since
    an inf in either channel already sinks the combined score, that alone kicks
    the window out. Volume is left unfiltered (it's naturally spiky).

    `max_scale_ratio` gates on raw scale: a window whose price OR volume mean
    diverges from the query's by more than this factor (either direction) is set
    to +inf. Kills z-normalization's blind spot — split-adjusted phantom prices
    and dead-volume stubs whose *shape* matches but whose scale is nonsensical.
    0 disables (see `_scale_reject`)."""
    m = len(q_close)
    if len(close) < m:
        return None
    d_price = mass(q_close, close, weights, min_cv=min_cv)
    if d_price.size == 0:
        return None
    d_vol = mass(q_volume, volume, weights)
    combined = price_weight * d_price + (1.0 - price_weight) * d_vol
    if max_scale_ratio:
        reject = (_scale_reject(close, q_close.mean(), m, max_scale_ratio)
                  | _scale_reject(volume, q_volume.mean(), m, max_scale_ratio))
        combined[reject] = np.inf
    return combined


def search(query: Series, args) -> tuple[list[Match], dict]:
    q_close, q_volume = query.close, query.volume
    m = len(q_close)
    if args.segments:
        segments = [s.strip() for s in args.segments.split(",") if s.strip()]
    else:
        segments = list(DEFAULT_SEGMENTS)  # equity segments only by default
    markets = None
    if args.markets:
        markets = [s.strip().lower() for s in args.markets.split(",") if s.strip()]

    heap: list[Match] = []  # min-heap on sort_key=-score → root is worst kept
    top_k = args.top_k
    min_distance = args.min_distance
    # Weekday of the query's bar 0 ("today"); candidates must land on the same
    # weekday so the day-of-week phase lines up (holidays can still desync the
    # rest of the window). Disable with --any-dow.
    query_dow = date.fromisoformat(query.dates[-1]).weekday() if args.match_dow else None

    # Per-bar recency weighting: the recent (on-display) end of the window is
    # weighted up so the tail must match tightly while older bars only need the
    # broad shape. None → uniform weighting (plain MASS).
    weights = recency_weights(m, args.recency_bucket, args.recency_ramp)

    start = time.monotonic()
    scanned = 0
    matched = 0
    budget_hit = False

    for ticker, dates, close, volume in iter_universe(segments, markets):
        scanned += 1
        profile = combined_profile(q_close, q_volume, close, volume, args.price_weight,
                                    weights, min_cv=args.min_cv,
                                    max_scale_ratio=args.max_scale_ratio)
        if profile is None:
            continue

        # Order candidate windows best-first so early-abandon fills the heap fast.
        order = np.argsort(profile)
        for idx in order:
            score = profile[idx]
            if not np.isfinite(score):
                break  # rest are inf/nan
            if len(heap) >= top_k and score >= -heap[0].sort_key:
                break  # early abandon: no remaining window in this series can enter

            # Drop (near-)perfect matches regardless of ticker: a distance below
            # `min_distance` means the window is essentially the query itself —
            # the query's own recent bars, a duplicate listing, or a cross-market
            # dual listing of the same series. Ordered best-first, so keep
            # scanning past these rather than abandoning.
            if score < min_distance:
                continue

            start_idx = int(idx)
            end_idx = start_idx + m - 1
            end_date = dates[end_idx]

            # Day-of-week phase: candidate's bar 0 must be the query's weekday.
            if query_dow is not None and date.fromisoformat(end_date).weekday() != query_dow:
                continue

            fut_close = close[end_idx + 1 : end_idx + 1 + args.forward]
            fut_volume = volume[end_idx + 1 : end_idx + 1 + args.forward]
            # A match must have the full forward window — a window ending too near
            # the end of its own series has nothing to project and is useless here.
            if len(fut_close) < args.forward:
                continue

            match = Match(
                sort_key=-score, score=score, ticker=ticker,
                start_idx=start_idx, end_date=end_date,
                win_close=close[start_idx : end_idx + 1].copy(),
                win_volume=volume[start_idx : end_idx + 1].copy(),
                fut_close=fut_close.copy(),
                fut_volume=fut_volume.copy(),
                win_dates=dates[start_idx : end_idx + 1].copy(),
                fut_dates=dates[end_idx + 1 : end_idx + 1 + args.forward].copy(),
            )
            if len(heap) < top_k:
                heapq.heappush(heap, match)
                matched += 1
            elif score < -heap[0].sort_key:
                heapq.heapreplace(heap, match)
                matched += 1
            break  # only the single best (non-excluded) window per ticker

        if time.monotonic() - start > args.budget_secs:
            budget_hit = True
            break

    results = sorted(heap, key=lambda x: x.score)
    stats = {
        "scanned": scanned,
        "matched": matched,
        "elapsed": time.monotonic() - start,
        "budget_hit": budget_hit,
    }
    return results, stats


# ---------------------------------------------------------------------------
# Cache: fix the (slow) search result for an hour so UX iteration is instant
# ---------------------------------------------------------------------------

# Args that change the match set; --show and layout are NOT here so they stay
# instant on re-run. Note: --forward changes how many future bars are stored, so
# it is a key input; --budget-secs changes how much of the universe gets scanned.
_CACHE_KEY_ARGS = (
    "ticker", "query_len", "forward", "top_k", "price_weight",
    "segments", "markets", "min_distance", "budget_secs", "match_dow",
    "recency_bucket", "recency_ramp", "min_cv", "max_scale_ratio",
)


def _cache_key(query: Series, args) -> str:
    payload = {a: getattr(args, a) for a in _CACHE_KEY_ARGS}
    # Fold the query bars in so a re-fetch (new data) invalidates the cache.
    payload["query_close"] = query.close.tolist()
    payload["query_volume"] = query.volume.tolist()
    payload["query_dates"] = query.dates.tolist()
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha1(blob).hexdigest()[:16]


def _prune_stale_cache() -> None:
    """Delete cache files older than the TTL so `recent/` doesn't accumulate."""
    if not CACHE_DIR.exists():
        return
    now = time.time()
    for f in CACHE_DIR.glob("*.pkl"):
        if now - f.stat().st_mtime > CACHE_TTL_SECS:
            f.unlink(missing_ok=True)


def load_cached(query: Series, args) -> tuple[list[Match], dict] | None:
    """Return a cached (results, stats) if a fresh entry exists, else None."""
    _prune_stale_cache()
    path = CACHE_DIR / f"{_cache_key(query, args)}.pkl"
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > CACHE_TTL_SECS:
        return None
    try:
        with path.open("rb") as f:
            results, stats = pickle.load(f)
    except Exception:
        return None  # corrupt/incompatible cache — recompute
    stats = {**stats, "cached": True}
    return results, stats


def save_cached(query: Series, args, results: list[Match], stats: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(query, args)}.pkl"
    tmp = path.with_suffix(".pkl.tmp")
    with tmp.open("wb") as f:
        pickle.dump((results, stats), f)
    tmp.replace(path)  # atomic


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def denormalize(values: np.ndarray, src_ref: np.ndarray, dst_mean: float, dst_std: float) -> np.ndarray:
    """Project `values` onto the query's scale by z-score inversion.

    The candidate's own window (`src_ref`) defines the z-normalization the match
    was computed under; we invert those z-scores through the query channel's
    `dst_mean`/`dst_std`. For a perfect match this reproduces the query's bars.
    """
    src_mean = src_ref.mean()
    src_std = src_ref.std()
    if src_std < 1e-10:
        return np.full_like(values, dst_mean, dtype=float)
    z = (values - src_mean) / src_std
    return z * dst_std + dst_mean


def _proj(match: Match, query: Series) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return (win_px, win_vol, fut_px, fut_vol) for `match`, denormalized onto
    the query's price/volume scale. Future arrays are padded with NaN to
    `forward` length so all rows align."""
    qc, qv = query.close, query.volume
    win_px = denormalize(match.win_close, match.win_close, qc.mean(), qc.std())
    win_vol = denormalize(match.win_volume, match.win_volume, qv.mean(), qv.std())
    fut_px = denormalize(match.fut_close, match.win_close, qc.mean(), qc.std())
    fut_vol = denormalize(match.fut_volume, match.win_volume, qv.mean(), qv.std())
    return win_px, win_vol, fut_px, fut_vol


def _fmt_px(x: float) -> str:
    return "[dim]·[/]" if not np.isfinite(x) else f"{x:.2f}"


def _fmt_vol(x: float) -> str:
    if not np.isfinite(x):
        return "[dim]·[/]"
    if abs(x) >= 1e6:
        return f"{x / 1e6:.1f}M"
    if abs(x) >= 1e3:
        return f"{x / 1e3:.0f}K"
    return f"{x:.0f}"


def _fmt_rel(x: float) -> str:
    """Signed percent return, coloured by direction; · for a missing/NaN bar."""
    if not np.isfinite(x):
        return "[dim]·[/]"
    color = "green" if x > 0 else "red" if x < 0 else "dim"
    return f"[{color}]{x:+.1%}[/]"


def _rel_cells(values: np.ndarray | None, base: float | None) -> list[str]:
    """Percent return of each raw close in `values` vs `base` (this row's bar 0).
    `base` None or non-positive → all cells blank. Bar 0 itself reads +0%."""
    if values is None or base is None or base <= 0:
        return ["[dim]·[/]"] * (len(values) if values is not None else 0)
    return [_fmt_rel(v / base - 1.0) for v in values]


def channel_transform(cand_win: np.ndarray, q_win: np.ndarray) -> tuple[float, float] | None:
    """The affine map that z-denormalizes a candidate window onto the query's axis,
    expressed as (gain, offset) so ``projected = real × gain + offset`` reproduces
    the table cells exactly.

    Derivation: the projection is ``(x − Cm)/Cs × Ks + Km`` (re-center by the
    candidate mean/std, re-scale to the query's). Collecting terms in ``x``:
        gain   = Ks / Cs         (query std ÷ candidate std)
        offset = Km − Cm × gain  (in the query's units)
    Returns None if the candidate window is flat (Cs≈0) — no reproducible map."""
    Cm, Cs = cand_win.mean(), cand_win.std()
    Km, Ks = q_win.mean(), q_win.std()
    if Cs < 1e-10:
        return None
    gain = Ks / Cs
    return gain, Km - Cm * gain


def _fmt_transform(tf: tuple[float, float] | None, offset_fmt=None) -> str:
    """Render a channel's (gain, offset) affine map as ``×g +c`` — multiply the
    candidate's real value by the gain, then add the offset, to land on the
    query's axis (reproduces the projected cell exactly). `offset_fmt` formats the
    offset in the channel's units (e.g. M/K for volume); defaults to `%.3g`. The
    gain is a unitless multiplier. Blank for the reference row (tf None). Dimmed
    so it reads as an annotation, not data."""
    if tf is None:
        return ""
    gain, offset = tf
    sign = "+" if offset >= 0 else "−"
    body = offset_fmt(abs(offset)) if offset_fmt else f"{abs(offset):.3g}"
    return f"[dim]×{gain:.3g} {sign}{body}[/]"


def _shade_vol(cell: str) -> str:
    # Render vol numbers dimmed — same shade as the date in each row label — so
    # they sit visually below the (brighter) price row above them.
    return cell if cell.startswith("[dim]") else f"[dim]{cell}[/]"


def report(query: Series, results: list[Match], stats: dict, args) -> None:
    console.print()
    console.print(
        f"[bold]{query.ticker}[/] — query = last {len(query.close)} bars "
        f"ending [cyan]{query.dates[-1]}[/]  "
        f"(price weight {args.price_weight:.2f}, forward {args.forward} bars)"
    )
    note = "[yellow]budget hit[/]" if stats["budget_hit"] else "full scan"
    origin = "cached (<1h)" if stats.get("cached") else f"{stats['elapsed']:.1f}s ({note})"
    console.print(
        f"scanned [bold]{stats['scanned']:,}[/] tickers in {origin}; {len(results)} matches"
    )

    if not results:
        console.print("\n[dim]No matches found.[/]\n")
        return

    n_show = min(args.show, len(results))
    show_past = 5                     # last 5 window bars we display
    fwd = args.forward
    fut_cols = min(5, fwd)            # future bars we display (5 by default)

    # Column layout: label | px/vol | past bars (-(n-1)..0, 0 = today) | future bars.
    table = Table(show_header=True, header_style="bold", pad_edge=False)
    table.add_column("match", justify="left", no_wrap=True)
    table.add_column("", justify="left")
    for k in range(show_past - 1, -1, -1):
        table.add_column("0" if k == 0 else f"-{k}", justify="right")
    for k in range(1, fut_cols + 1):
        table.add_column(f"+{k}", justify="right", style="cyan")

    def add_pair(label: str, px: np.ndarray, vol: np.ndarray,
                 fpx: np.ndarray | None, fvol: np.ndarray | None,
                 style: str = "",
                 px_tf: tuple[float, float] | None = None,
                 vol_tf: tuple[float, float] | None = None,
                 raw_win: np.ndarray | None = None,
                 raw_fut: np.ndarray | None = None,
                 show_rel: bool = True) -> None:
        px_cells = [_fmt_px(v) for v in px[-show_past:]]
        vol_cells = [_shade_vol(_fmt_vol(v)) for v in vol[-show_past:]]
        fpx_cells = [_fmt_px(v) for v in (fpx[:fut_cols] if fpx is not None else [])]
        fvol_cells = [_shade_vol(_fmt_vol(v)) for v in (fvol[:fut_cols] if fvol is not None else [])]
        fpx_cells += ["[dim]·[/]"] * (fut_cols - len(fpx_cells))
        fvol_cells += ["[dim]·[/]"] * (fut_cols - len(fvol_cells))
        lbl = f"[{style}]{label}[/]" if style else label
        # Channel-label column carries the affine map (×gain +offset) that takes
        # this row's REAL price/volume onto the query's axis — reproduces the
        # projected cells exactly: projected = real × gain + offset.
        table.add_row(lbl, f"px {_fmt_transform(px_tf)}", *px_cells, *fpx_cells)
        # Relative performance: each forward bar's % return vs this row's own bar 0
        # (the window end). Computed from the candidate's RAW closes — a true
        # return, scale-invariant, so it reads the same regardless of the
        # z-projection. Sits right under px (it is the price channel's return).
        # Only the forward bars carry a value; the window bars and bar 0 itself
        # are blank — a backward-running return in the window reads oddly, and
        # bar 0 is always +0%. Skipped for the query ref row (`show_rel=False`),
        # which has no forward projection to measure.
        if show_rel:
            base = raw_win[-1] if raw_win is not None and len(raw_win) else None
            rel_fut = _rel_cells(raw_fut[:fut_cols], base) if raw_fut is not None else []
            rel_fut += ["[dim]·[/]"] * (fut_cols - len(rel_fut))
            table.add_row("", "rel px", *["[dim]·[/]"] * show_past, *rel_fut)
        table.add_row("", f"vol {_fmt_transform(vol_tf, offset_fmt=_fmt_vol)}", *vol_cells, *fvol_cells)

    # Fixed-width label so the d= and date columns line up regardless of ticker
    # length. Rich markup has zero display width, so pad the plain text first.
    tw = max([len(m.ticker) for m in results[:n_show]] + [len(query.ticker)])

    def _label(ticker: str, dist: str, iso_date: str) -> str:
        # Show bar 0's weekday alongside its date. With --match-dow it's constant
        # (documents the alignment); with --any-dow it varies, so it's essential.
        dow = date.fromisoformat(iso_date).strftime("%a")
        return f"{ticker:<{tw}}  {dist:<7}  [dim]{iso_date} {dow}[/]"

    # Reference: the query's actual last-5 (no future). The date is bar 0 —
    # each row's "today" / window-end — the unambiguous anchor to open in a chart.
    add_pair(_label(query.ticker, "(ref)", query.dates[-1]),
             query.close, query.volume, None, None, style="bold green",
             show_rel=False)
    table.add_row("")

    for mtch in results[:n_show]:
        win_px, win_vol, fut_px, fut_vol = _proj(mtch, query)
        # Affine map (×gain +offset) taking the candidate's REAL price/volume onto
        # the query's axis — the exact transform behind the projected cells.
        px_tf = channel_transform(mtch.win_close, query.close)
        vol_tf = channel_transform(mtch.win_volume, query.volume)
        add_pair(_label(mtch.ticker, f"d={mtch.score:.2f}", mtch.end_date),
                 win_px, win_vol, fut_px, fut_vol,
                 px_tf=px_tf, vol_tf=vol_tf,
                 raw_win=mtch.win_close, raw_fut=mtch.fut_close)

    console.print(table)
    console.print(
        f"[dim]px/vol are z-denormalized onto {query.ticker}'s scale; col 0 is today, "
        f"-{show_past - 1}..0 the {args.query_len}-bar window, +1..+{fut_cols} the projection. "
        f"×g +c is the affine map from the candidate's REAL value onto {query.ticker}'s axis (real×g+c = cell). "
        f"rel px = each forward bar's raw % return vs bar 0. Date after the ticker is bar 0 (chart anchor).[/]"
    )

    _print_bar_stats(query, results, args, show_past, fut_cols)
    console.print()


def _print_bar_stats(query: Series, results: list[Match], args, show_past: int, fut_cols: int) -> None:
    """Per-bar mean±std of the denormalized candidates over the FULL result set,
    for both the overlapping window bars (vs the query's actual) and the future
    projection."""
    px_win, vol_win, px_fut, vol_fut = [], [], [], []
    for mtch in results:
        wpx, wvol, fpx, fvol = _proj(mtch, query)
        px_win.append(wpx[-show_past:])
        vol_win.append(wvol[-show_past:])
        # pad future to fut_cols with NaN so stacking aligns
        fp = np.full(fut_cols, np.nan); fp[:min(fut_cols, len(fpx))] = fpx[:fut_cols]
        fv = np.full(fut_cols, np.nan); fv[:min(fut_cols, len(fvol))] = fvol[:fut_cols]
        px_fut.append(fp); vol_fut.append(fv)

    px_win = np.vstack(px_win); vol_win = np.vstack(vol_win)
    px_fut = np.vstack(px_fut); vol_fut = np.vstack(vol_fut)

    console.print(f"\n[bold]Per-bar stats[/] across all {len(results)} matches "
                  f"(mean denormalized to {query.ticker}; cv% = std/mean, the spread across matches):")

    st = Table(show_header=True, header_style="bold", pad_edge=False)
    st.add_column("", justify="left", no_wrap=True)
    for k in range(show_past - 1, -1, -1):
        st.add_column("0" if k == 0 else f"-{k}", justify="right")
    for k in range(1, fut_cols + 1):
        st.add_column(f"+{k}", justify="right", style="cyan")

    def stat_rows(label: str, win: np.ndarray, fut: np.ndarray, fmt, actual: np.ndarray | None):
        wmean, wstd = np.nanmean(win, axis=0), np.nanstd(win, axis=0)
        fmean, fstd = np.nanmean(fut, axis=0), np.nanstd(fut, axis=0)
        # Coefficient of variation (std / |mean|) — a unitless % so the spread
        # across matches is comparable between price and volume: the "volatility"
        # of the analogue set at each bar.
        def _cv(std, mean):
            return [
                "[dim]·[/]" if not np.isfinite(m) or abs(m) < 1e-12 else f"{100 * s / abs(m):.0f}%"
                for s, m in zip(std, mean)
            ]

        if actual is not None:
            # the query's own actual bars — the reference the candidates fit to
            st.add_row(f"[bold]{label} {query.ticker} ref[/]",
                       *[f"[bold]{fmt(v)}[/]" for v in actual[-show_past:]],
                       *["[dim]·[/]"] * fut_cols)
        st.add_row(f"{label} mean", *[fmt(v) for v in wmean], *[fmt(v) for v in fmean])
        st.add_row(f"{label} cv%",  *_cv(wstd, wmean),        *_cv(fstd, fmean))
        if actual is not None:
            # deviation of the candidate mean from the query's actual bar
            dev = wmean - actual[-show_past:]
            st.add_row(f"[dim]{label} Δ vs {query.ticker}[/]",
                       *[f"[dim]{fmt(v)}[/]" for v in dev],
                       *["[dim]·[/]"] * fut_cols)

    stat_rows("px", px_win, px_fut, _fmt_px, query.close)

    # Return-distribution buckets: for each future bar, how many of the matches
    # landed in each price-return band (return measured from bar 0, raw closes →
    # scale-invariant). Distinct, exhaustive bands. Past columns are blank.
    n = len(results)
    ret = np.full((n, fut_cols), np.nan)  # match × future-bar return from bar 0
    for i, mtch in enumerate(results):
        base = mtch.win_close[-1]
        k = min(fut_cols, len(mtch.fut_close))
        if base > 0:
            ret[i, :k] = mtch.fut_close[:k] / base - 1.0

    buckets = [
        ("px ≥+1%",      lambda r: r >= 0.01,                "green"),
        ("px -1..+1%",   lambda r: (r > -0.01) & (r < 0.01), "yellow"),
        ("px ≤-1%",      lambda r: r <= -0.01,               "red"),
    ]
    for name, pred, color in buckets:
        counts = [int(np.sum(pred(ret[:, c]) & np.isfinite(ret[:, c]))) for c in range(fut_cols)]
        cells = [f"[{color}]{c}[/]" if c else "[dim]0[/]" for c in counts]
        st.add_row(f"[{color}]{name}[/]", *["[dim]·[/]"] * show_past, *cells)

    stat_rows("vol", vol_win, vol_fut, _fmt_vol, query.volume)
    console.print(st)
    console.print(
        f"[dim]Buckets: count of the {n} matches whose price return from bar 0 fell in each "
        f"band at +1..+{fut_cols} (bands are distinct and cover all outcomes).[/]"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Find historical chart analogues of a current pattern.")
    p.add_argument("ticker", help="Repo ticker whose recent window is the query")
    p.add_argument("--query-len", type=int, default=20, help="Query window length in bars (default 20)")
    p.add_argument("--forward", type=int, default=5, help="Forward-outcome horizon in bars (default 5)")
    p.add_argument("--top-k", type=int, default=30,
                   help="Matches to keep for the per-bar statistics pool (default 30)")
    p.add_argument("--show", type=int, default=10,
                   help="How many top matches to print in the detail table (default 10)")
    p.add_argument("--price-weight", type=float, default=0.6,
                   help="Weight on price vs volume distance, 0..1 (default 0.6)")
    p.add_argument("--budget-secs", type=float, default=60.0,
                   help="Wall-clock search budget; returns best-so-far (default 60)")
    p.add_argument("--segments", type=str, default=None,
                   help='Comma-separated Stooq segments to search, e.g. "nyse stocks,nasdaq stocks" '
                        '(default: real equity segments only). See prices-historic/STRUCTURE.md.')
    p.add_argument("--markets", type=str, default=None,
                   help='Comma-separated markets to search: us,jp,uk,hk,world (default: all)')
    p.add_argument("--min-distance", type=float, default=0.05,
                   help="Drop matches with combined distance below this — they are the query "
                        "itself, a duplicate or dual listing (default: 0.05)")
    p.add_argument("--any-dow", dest="match_dow", action="store_false",
                   help="Allow any day-of-week (default: candidate's bar 0 must be the "
                        "query's weekday)")
    p.add_argument("--recency-bucket", type=int, default=5,
                   help="Bar-block size for recency weighting; bars are grouped into "
                        "blocks of this size from the window end (default 5)")
    p.add_argument("--min-cv", type=float, default=0.005,
                   help="Reject near-flat windows whose price coefficient of variation "
                        "(std/|mean|) over the window is below this — low-information, "
                        "barely-moving stretches. 0 disables (default 0.005 = 0.5%%).")
    p.add_argument("--recency-ramp", dest="recency_ramp", type=float, default=0.5,
                   help="Weight floor for the oldest block, 0..1: weights ramp linearly "
                        "from 1.0 (most recent block) down to this for the oldest. "
                        "1.0 = uniform (older bars matter as much as recent). Default 0.5.")
    p.add_argument("--max-scale-ratio", type=float, default=100.0,
                   help="Reject a candidate window whose price OR volume mean diverges "
                        "from the query's by more than this factor (either direction). "
                        "The match is scale-invariant (z-normalized), so this re-imposes "
                        "a scale bound to drop split-adjusted phantom prices and "
                        "dead-volume stubs. 0 disables (default 100).")
    p.add_argument("--refresh", action="store_true",
                   help="Ignore the 1h cache and recompute the search from scratch")
    args = p.parse_args()

    args.ticker = args.ticker.upper()
    if not (0.0 <= args.price_weight <= 1.0):
        raise SystemExit("--price-weight must be between 0 and 1")
    if not (0.0 < args.recency_ramp <= 1.0):
        raise SystemExit("--recency-ramp must be in (0, 1]")
    if args.recency_bucket < 1:
        raise SystemExit("--recency-bucket must be >= 1")
    if args.min_cv < 0:
        raise SystemExit("--min-cv must be >= 0")
    if args.max_scale_ratio < 0:
        raise SystemExit("--max-scale-ratio must be >= 0 (0 disables)")
    if 0 < args.max_scale_ratio < 1:
        raise SystemExit("--max-scale-ratio must be >= 1 (it is a fold-divergence, applied both ways)")
    if not archives():
        raise SystemExit(f"No d_*_txt.zip archives found in {ARCHIVE_DIR.relative_to(REPO_ROOT)}")

    query = load_query(args.ticker, args.query_len)
    warn_query_quality(query)

    cached = None if args.refresh else load_cached(query, args)
    if cached is not None:
        results, stats = cached
    else:
        results, stats = search(query, args)
        save_cached(query, args, results, stats)

    report(query, results, stats, args)


if __name__ == "__main__":
    main()
