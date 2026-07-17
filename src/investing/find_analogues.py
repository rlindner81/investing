#!/usr/bin/env python3
"""
find_analogues.py — Find historical chart windows that match a current pattern.

Given a query window (the last L trading days of a repo ticker), search a large
historical universe (every Stooq ``prices-historic/d_*_txt.zip`` bundle — US, JP,
UK, HK, World) for windows whose *shape* — in both price and volume — most
closely matches, then report what happened next.

Matching is scale-invariant: each window is z-normalized, so a move from 10→11
matches one from 100→110. Price and volume are normalized separately and their
distances combined with a weight (``--price-weight``). The current pattern is
found via MASS (Mueen's Algorithm for Similarity Search): a z-normalized
Euclidean distance profile computed in one FFT pass per series.

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
follows, including the deviation of the past bars from the query's actuals. Each
bar carries its actual ISO date (the daily feed has no intraday time) so a match
can be opened directly in an external chart; the window-end date sits after each
ticker as the anchor.

Search results are cached under ``prices-historic/recent/`` for one hour, keyed
on every search-affecting input plus a hash of the query bars — so iterating on
display (``--show``, layout) re-renders instantly while a real input change or a
data re-fetch recomputes. ``--refresh`` forces recomputation; stale (>1h) entries
are pruned automatically.

Usage:
  uv run find-analogues ODD                       # last 30 bars of ODD, default search
  uv run find-analogues ODD --query-len 40 --forward 20 --top-k 25 --show 10
  uv run find-analogues ODD --budget-secs 120 --price-weight 0.7
  uv run find-analogues ODD --segments "nyse stocks,nasdaq stocks"
  uv run find-analogues ODD --markets us,uk --segments "nyse stocks,lse stocks"

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
    """Rolling mean and (population) std of every length-m window of `series`."""
    cumsum = np.concatenate(([0.0], np.cumsum(series)))
    cumsum2 = np.concatenate(([0.0], np.cumsum(series ** 2)))
    seg_sum = cumsum[m:] - cumsum[:-m]
    seg_sum2 = cumsum2[m:] - cumsum2[:-m]
    mean = seg_sum / m
    var = seg_sum2 / m - mean ** 2
    var = np.clip(var, 0.0, None)
    return mean, np.sqrt(var)


def mass(query: np.ndarray, series: np.ndarray) -> np.ndarray:
    """Distance profile: z-normalized Euclidean distance from `query` (length m)
    to every length-m subsequence of `series`. Lower is more similar.

    Returns an array of length ``len(series) - m + 1``. Windows whose std is ~0
    (flat, degenerate) get +inf. Implementation follows Mueen's MASS via FFT.
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

    # Sliding dot product QT[i] = sum_j query[j] * series[i+j] via FFT.
    rev = query[::-1]
    fft_len = 1
    while fft_len < n + m:
        fft_len <<= 1
    prod = np.fft.irfft(np.fft.rfft(series, fft_len) * np.fft.rfft(rev, fft_len), fft_len)
    qt = prod[m - 1 : n]  # length n - m + 1

    # z-normalized Euclidean distance^2 = 2*m*(1 - (QT - m*mean*q_mean)/(m*std*q_std))
    denom = m * std * q_std
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = (qt - m * mean * q_mean) / denom
    dist_sq = 2 * m * (1 - corr)
    dist_sq = np.clip(dist_sq, 0.0, None)
    dist = np.sqrt(dist_sq)
    dist[std < 1e-10] = np.inf  # flat windows can't match a varying query
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


def combined_profile(
    q_close: np.ndarray,
    q_volume: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    price_weight: float,
) -> np.ndarray | None:
    """Weighted price+volume distance profile for one series, or None if too short."""
    m = len(q_close)
    if len(close) < m:
        return None
    d_price = mass(q_close, close)
    if d_price.size == 0:
        return None
    d_vol = mass(q_volume, volume)
    return price_weight * d_price + (1.0 - price_weight) * d_vol


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

    start = time.monotonic()
    scanned = 0
    matched = 0
    budget_hit = False

    for ticker, dates, close, volume in iter_universe(segments, markets):
        scanned += 1
        profile = combined_profile(q_close, q_volume, close, volume, args.price_weight)
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

            fut_close = close[end_idx + 1 : end_idx + 1 + args.forward]
            fut_volume = volume[end_idx + 1 : end_idx + 1 + args.forward]
            if args.require_forward and len(fut_close) < args.forward:
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
    "segments", "markets", "min_distance", "budget_secs", "require_forward",
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


def report(query: Series, results: list[Match], stats: dict, args) -> None:
    console.print()
    console.print(
        f"[bold]{query.ticker}[/] — query = last {len(query.close)} bars "
        f"ending [cyan]{query.dates[-1]}[/]  "
        f"(price weight {args.price_weight:.2f}, forward {args.forward} bars)"
    )
    note = "[yellow]budget hit[/]" if stats["budget_hit"] else "full scan"
    origin = "[green]cached[/] (<1h)" if stats.get("cached") else f"{stats['elapsed']:.1f}s ({note})"
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

    # Column layout: label | last-5 window bars | future bars.
    table = Table(show_header=True, header_style="bold", pad_edge=False)
    table.add_column("match", justify="left", no_wrap=True)
    for k in range(show_past, 0, -1):
        table.add_column(f"-{k}", justify="right")
    table.add_column("│", justify="center")
    for k in range(1, fut_cols + 1):
        table.add_column(f"+{k}", justify="right", style="cyan")

    def _mmdd(iso: str) -> str:
        return iso[5:] if iso else ""  # MM-DD, compact

    def add_pair(label: str, px: np.ndarray, vol: np.ndarray,
                 fpx: np.ndarray | None, fvol: np.ndarray | None,
                 wdates: np.ndarray | None, fdates: np.ndarray | None,
                 style: str = "") -> None:
        px_cells = [_fmt_px(v) for v in px[-show_past:]]
        vol_cells = [_fmt_vol(v) for v in vol[-show_past:]]
        fpx_cells = [_fmt_px(v) for v in (fpx[:fut_cols] if fpx is not None else [])]
        fvol_cells = [_fmt_vol(v) for v in (fvol[:fut_cols] if fvol is not None else [])]
        fpx_cells += ["[dim]·[/]"] * (fut_cols - len(fpx_cells))
        fvol_cells += ["[dim]·[/]"] * (fut_cols - len(fvol_cells))
        lbl = f"[{style}]{label}[/]" if style else label
        table.add_row(lbl, *px_cells, "px", *fpx_cells)
        table.add_row("", *vol_cells, "vol", *fvol_cells)
        # date row (MM-DD) so each bar is openable in an external chart
        wd = [f"[dim]{_mmdd(d)}[/]" for d in (wdates[-show_past:] if wdates is not None else [])]
        wd += ["[dim]·[/]"] * (show_past - len(wd))
        fd = [f"[dim]{_mmdd(d)}[/]" for d in (fdates[:fut_cols] if fdates is not None else [])]
        fd += ["[dim]·[/]"] * (fut_cols - len(fd))
        table.add_row("[dim]  date[/]", *wd, "[dim]dt[/]", *fd)

    # Reference: the query's actual last-5 (no future).
    add_pair(f"{query.ticker} (ref)", query.close, query.volume, None, None,
             query.dates, None, style="bold green")
    table.add_row("")

    for mtch in results[:n_show]:
        win_px, win_vol, fut_px, fut_vol = _proj(mtch, query)
        label = f"{mtch.ticker}  d={mtch.score:.2f}  [dim]{mtch.end_date}[/]"
        add_pair(label, win_px, win_vol, fut_px, fut_vol,
                 mtch.win_dates, mtch.fut_dates)

    console.print(table)
    console.print(
        f"[dim]Candidate values are z-denormalized onto {query.ticker}'s scale "
        f"(px = projected price, vol = projected volume, date = actual bar date). "
        f"Columns -5..-1 overlap {query.ticker}'s window; +1..+{fut_cols} are the projection. "
        f"The date after each ticker is its window-end — the anchor to open in your chart.[/]"
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
                  f"(mean ± std, denormalized to {query.ticker}):")

    st = Table(show_header=True, header_style="bold", pad_edge=False)
    st.add_column("", justify="left", no_wrap=True)
    for k in range(show_past, 0, -1):
        st.add_column(f"-{k}", justify="right")
    st.add_column("│", justify="center")
    for k in range(1, fut_cols + 1):
        st.add_column(f"+{k}", justify="right", style="cyan")

    def stat_rows(label: str, win: np.ndarray, fut: np.ndarray, fmt, actual: np.ndarray | None):
        wmean, wstd = np.nanmean(win, axis=0), np.nanstd(win, axis=0)
        fmean, fstd = np.nanmean(fut, axis=0), np.nanstd(fut, axis=0)
        st.add_row(f"{label} mean", *[fmt(v) for v in wmean], "│", *[fmt(v) for v in fmean])
        st.add_row(f"{label} std",  *[fmt(v) for v in wstd],  "│", *[fmt(v) for v in fstd])
        if actual is not None:
            # deviation of the candidate mean from the query's actual bar
            dev = wmean - actual[-show_past:]
            st.add_row(f"[dim]{label} Δ vs {query.ticker}[/]",
                       *[f"[dim]{fmt(v)}[/]" for v in dev], "│",
                       *["[dim]·[/]"] * fut_cols)

    stat_rows("px", px_win, px_fut, _fmt_px, query.close)
    stat_rows("vol", vol_win, vol_fut, _fmt_vol, query.volume)
    console.print(st)


def main() -> None:
    p = argparse.ArgumentParser(description="Find historical chart analogues of a current pattern.")
    p.add_argument("ticker", help="Repo ticker whose recent window is the query")
    p.add_argument("--query-len", type=int, default=30, help="Query window length in bars (default 30)")
    p.add_argument("--forward", type=int, default=20, help="Forward-outcome horizon in bars (default 20)")
    p.add_argument("--top-k", type=int, default=20,
                   help="Matches to keep for the per-bar statistics pool (default 20)")
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
    p.add_argument("--require-forward", action="store_true",
                   help="Only keep matches that have a full forward window to measure")
    p.add_argument("--refresh", action="store_true",
                   help="Ignore the 1h cache and recompute the search from scratch")
    args = p.parse_args()

    args.ticker = args.ticker.upper()
    if not (0.0 <= args.price_weight <= 1.0):
        raise SystemExit("--price-weight must be between 0 and 1")
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
