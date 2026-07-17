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
found up to that point. This makes the enormous universe tractable. Narrow the
universe with ``--segments`` (instrument class) and ``--markets`` (country).

For each match, the forward return over the next ``--forward`` bars (the bars
*after* the matched window) is measured, and the distribution across matches is
summarized (mean / median / hit-rate) so you can read the "effective outcome".

Usage:
  uv run find-analogues ODD                       # last 30 bars of ODD, default search
  uv run find-analogues ODD --query-len 40 --forward 20 --top-k 25
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
import heapq
import io
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

# Country suffixes stripped from a file stem to recover a bare symbol. World
# indices carry no suffix; crypto/bonds use .v/.b.
_SUFFIXES = (".us", ".jp", ".uk", ".hk", ".v", ".b")

console = Console()


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
    fwd_return: float | None = field(default=None, compare=False)


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


def forward_return(close: np.ndarray, end_idx: int, forward: int) -> float | None:
    """Return over `forward` bars after window ending at end_idx (inclusive)."""
    last = end_idx
    fut = end_idx + forward
    if fut >= len(close):
        return None
    base = close[last]
    if base <= 0:
        return None
    return close[fut] / base - 1.0


def search(query: Series, args) -> tuple[list[Match], dict]:
    q_close, q_volume = query.close, query.volume
    m = len(q_close)
    segments = None
    if args.segments:
        segments = [s.strip() for s in args.segments.split(",") if s.strip()]
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

            end_idx = int(idx) + m - 1
            end_date = dates[end_idx]

            fwd = forward_return(close, end_idx, args.forward)
            if args.require_forward and fwd is None:
                continue

            match = Match(
                sort_key=-score, score=score, ticker=ticker,
                start_idx=int(idx), end_date=end_date, fwd_return=fwd,
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
# Reporting
# ---------------------------------------------------------------------------

def report(query: Series, results: list[Match], stats: dict, args) -> None:
    console.print()
    console.print(
        f"[bold]{query.ticker}[/] — query = last {len(query.close)} bars "
        f"ending [cyan]{query.dates[-1]}[/]  "
        f"(price weight {args.price_weight:.2f}, forward {args.forward} bars)"
    )
    note = "[yellow]budget hit[/]" if stats["budget_hit"] else "full scan"
    console.print(
        f"scanned [bold]{stats['scanned']:,}[/] tickers in "
        f"{stats['elapsed']:.1f}s ({note}); showing top {len(results)}"
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Ticker")
    table.add_column("Window ends")
    table.add_column("Distance", justify="right")
    table.add_column(f"Fwd {args.forward}d", justify="right")

    fwds = []
    for i, mtch in enumerate(results, 1):
        if mtch.fwd_return is None:
            fwd_str = "[dim]n/a[/]"
        else:
            fwds.append(mtch.fwd_return)
            color = "green" if mtch.fwd_return >= 0 else "red"
            fwd_str = f"[{color}]{mtch.fwd_return:+.1%}[/]"
        table.add_row(
            str(i), mtch.ticker, mtch.end_date, f"{mtch.score:.2f}", fwd_str
        )
    console.print(table)

    if fwds:
        arr = np.array(fwds)
        hit = (arr > 0).mean()
        console.print(
            f"\n[bold]Outcome[/] over {len(arr)} matches with a forward window:  "
            f"mean [bold]{arr.mean():+.1%}[/]  median [bold]{np.median(arr):+.1%}[/]  "
            f"hit-rate [bold]{hit:.0%}[/]  "
            f"(min {arr.min():+.1%}, max {arr.max():+.1%})"
        )
    else:
        console.print("\n[dim]No matches had a full forward window to measure.[/]")
    console.print()


def main() -> None:
    p = argparse.ArgumentParser(description="Find historical chart analogues of a current pattern.")
    p.add_argument("ticker", help="Repo ticker whose recent window is the query")
    p.add_argument("--query-len", type=int, default=30, help="Query window length in bars (default 30)")
    p.add_argument("--forward", type=int, default=20, help="Forward-outcome horizon in bars (default 20)")
    p.add_argument("--top-k", type=int, default=20, help="Number of matches to keep (default 20)")
    p.add_argument("--price-weight", type=float, default=0.6,
                   help="Weight on price vs volume distance, 0..1 (default 0.6)")
    p.add_argument("--budget-secs", type=float, default=60.0,
                   help="Wall-clock search budget; returns best-so-far (default 60)")
    p.add_argument("--segments", type=str, default=None,
                   help='Comma-separated Stooq segments to search, e.g. "nyse stocks,nasdaq stocks" '
                        '(default: all). See prices-historic/STRUCTURE.md for the full list.')
    p.add_argument("--markets", type=str, default=None,
                   help='Comma-separated markets to search: us,jp,uk,hk,world (default: all)')
    p.add_argument("--min-distance", type=float, default=0.05,
                   help="Drop matches with combined distance below this — they are the query "
                        "itself, a duplicate or dual listing (default: 0.05)")
    p.add_argument("--require-forward", action="store_true",
                   help="Only keep matches that have a full forward window to measure")
    args = p.parse_args()

    args.ticker = args.ticker.upper()
    if not (0.0 <= args.price_weight <= 1.0):
        raise SystemExit("--price-weight must be between 0 and 1")
    if not archives():
        raise SystemExit(f"No d_*_txt.zip archives found in {ARCHIVE_DIR.relative_to(REPO_ROOT)}")

    query = load_query(args.ticker, args.query_len)
    warn_query_quality(query)
    results, stats = search(query, args)
    report(query, results, stats, args)


if __name__ == "__main__":
    main()
