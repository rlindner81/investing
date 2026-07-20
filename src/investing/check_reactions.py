"""check-reactions — how each past earnings was received in the market.

For one ticker, walk its reported quarters (from ``<TICKER>/FINANCIALS.yml``) and
show, per quarter, the price and volume around the announcement date: a few bars
BEFORE the report as context, the report bar itself (day 0), and the first days
AFTER. A ``rel px`` row gives each post-report bar's return relative to day 0's
close, so the reaction reads as a clean percentage regardless of the absolute
price level.

Rows are one earnings each, most recent first (capped by ``--show``). Columns are
trading days relative to the report bar: ``-N..0`` before/at the report, ``+1..``
after. Day 0 is the ``report_date`` bar (the announcement-date close, the same
reference check-valuation prices off) — or the first trading day on/after it.

Requires ``<TICKER>/FINANCIALS.yml`` (for the report dates) and
``prices/daily/<TICKER>.csv`` (see fetch-prices). Prices are z-agnostic here — no
projection or matching; each row shows the ticker's own raw prices/volumes.

    uv run check-reactions NFLX
    uv run check-reactions NFLX --before 4 --after 5 --show 10
"""

from __future__ import annotations

import argparse
import csv
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import yaml
from rich.console import Console
from rich.table import Table

REPO_ROOT = Path(__file__).resolve().parents[2]
PRICES_DIR = REPO_ROOT / "prices" / "daily"
console = Console()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

@dataclass
class Prices:
    dates: list[date]          # ascending trading days
    close: np.ndarray
    volume: np.ndarray


def load_prices(ticker: str) -> Prices:
    path = PRICES_DIR / f"{ticker}.csv"
    if not path.exists():
        raise SystemExit(
            f"No price file at {path.relative_to(REPO_ROOT)} — "
            f"run `uv run fetch-prices {ticker}` first."
        )
    dates, close, volume = [], [], []
    with path.open() as f:
        for row in csv.DictReader(f):
            try:
                dates.append(date.fromisoformat(row["Date"][:10]))
                close.append(float(row["Close"]))
                volume.append(float(row["Volume"]))
            except (ValueError, KeyError):
                continue
    if not dates:
        raise SystemExit(f"{ticker}: no usable rows in {path.name}.")
    # Sort ascending by date, keeping the channels aligned.
    order = sorted(range(len(dates)), key=lambda i: dates[i])
    return Prices(
        dates=[dates[i] for i in order],
        close=np.array([close[i] for i in order], dtype=float),
        volume=np.array([volume[i] for i in order], dtype=float),
    )


@dataclass
class Quarter:
    """One reported quarter's anchoring info. `anchor` is the day 0 bar date —
    the SEC announcement date when we have it, else the legacy report_date.
    `session` is pre-open / after-close / None (unknown). `has_announce` records
    whether the precise SEC fields were present (vs. a report_date fallback)."""
    id: str
    anchor: date
    session: str | None
    has_announce: bool


def load_quarters(ticker: str) -> list[Quarter]:
    """Anchoring info per quarter from FINANCIALS.yml, ascending by anchor date.

    Day 0 anchors on `announce_date` (the SEC 8-K item-2.02 acceptance date, the
    true announcement) when present, together with `announce_session` (pre-open /
    after-close) which fixes the reaction bar exactly. Quarters lacking those fall
    back to `report_date` with an unknown session (the reaction bar is then
    inferred). Quarters with neither date (pre-IPO reach-back periods) are
    skipped — no market reaction to show."""
    path = REPO_ROOT / ticker / "FINANCIALS.yml"
    if not path.exists():
        raise SystemExit(
            f"No {path.relative_to(REPO_ROOT)} — this script needs the ticker's "
            f"FINANCIALS.yml for its report dates."
        )
    data = yaml.safe_load(path.read_text()) or {}
    out: list[Quarter] = []
    for q in data.get("quarters", []):
        ann = _as_date(q.get("announce_date"))
        rep = _as_date(q.get("report_date"))
        anchor = ann or rep
        if anchor is None:
            continue
        session = q.get("announce_session") if ann else None
        out.append(Quarter(str(q.get("id", "?")), anchor, session, ann is not None))
    out.sort(key=lambda x: x.anchor)
    return out


def _as_date(v) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def bar_index(dates: list[date], target: date) -> int | None:
    """Index of the first trading bar on or after `target`. None if `target` is
    past the end of the series (report newer than our price history)."""
    i = bisect_left(dates, target)
    return i if i < len(dates) else None


# ---------------------------------------------------------------------------
# Reaction-bar offset (relative to day 0 = the anchor bar)
# ---------------------------------------------------------------------------

def reaction_offset(q: Quarter, prices: Prices, before: int) -> int:
    """How many sessions after day 0 (the anchor bar) the reaction trades.

    With a known session it's exact: an `after-close` release can't trade until
    the NEXT session (+1); a `pre-open` release trades the announce bar itself
    (0). Without a session (report_date fallback) we infer from the volume spike —
    whichever of bar 0 / +1 is heavier relative to a quiet pre-report baseline."""
    if q.session == "after-close":
        return 1
    if q.session == "pre-open":
        return 0
    # Fallback: infer from volume.
    c = bar_index(prices.dates, q.anchor)
    if c is None or c + 1 >= len(prices.volume):
        return 0
    lo = max(0, c - before)
    baseline = prices.volume[lo:c]
    base = np.median(baseline[baseline > 0]) if np.any(baseline > 0) else 0.0
    if base <= 0:
        return 0
    return 1 if prices.volume[c + 1] > prices.volume[c] else 0


# ---------------------------------------------------------------------------
# Formatting (mirrors find_shape's px / rel px / vol cell style)
# ---------------------------------------------------------------------------

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
    if not np.isfinite(x):
        return "[dim]·[/]"
    color = "green" if x > 0 else "red" if x < 0 else "dim"
    return f"[{color}]{x:+.1%}[/]"


def _shade_vol(cell: str) -> str:
    return cell if cell.startswith("[dim]") else f"[dim]{cell}[/]"


def _window(arr: np.ndarray, center: int, before: int, after: int) -> np.ndarray:
    """Slice `arr` at offsets -before..+after around `center`, padding out-of-range
    positions with NaN so every row is the same width."""
    out = np.full(before + after + 1, np.nan)
    for k, off in enumerate(range(-before, after + 1)):
        j = center + off
        if 0 <= j < len(arr):
            out[k] = arr[j]
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report(ticker: str, prices: Prices, quarters: list[Quarter], args) -> None:
    before, after, n_show = args.before, args.after, args.show
    width = before + after + 1
    zero = before                        # index of day 0 (the announce bar) in a row

    console.print()
    console.print(
        f"[bold]{ticker}[/] — earnings reactions "
        f"([green]-{before}[/]..[cyan]+{after}[/] trading days around each announcement; "
        f"newest {n_show} of {len(quarters)})"
    )
    # Session summary across the quarters we can place — where the reaction bar
    # sits (day 0 for pre-open, +1 for after-close) and how it was determined.
    placed = [q for q in reversed(quarters)
              if bar_index(prices.dates, q.anchor) is not None][:n_show]
    n_ann = sum(q.has_announce for q in placed)
    sessions = {q.session for q in placed if q.session}
    if n_ann == len(placed) and len(sessions) == 1:
        s = next(iter(sessions))
        rb = "the announce bar (day 0)" if s == "pre-open" else "the next session (day +1)"
        console.print(
            f"[dim]reports land [/][bold]{s}[/][dim] (per SEC filing) → reaction "
            f"on {rb}, marked ★.[/]"
        )
    else:
        detail = f"{n_ann}/{len(placed)} from SEC filings" if n_ann else "inferred from volume"
        console.print(
            f"[dim]reaction bar ★ = announce bar (pre-open) or next session "
            f"(after-close); {detail}. Day 0 is the announcement bar.[/]"
        )

    table = Table(show_header=True, header_style="bold", pad_edge=False)
    table.add_column("announce", justify="left", no_wrap=True)
    table.add_column("", justify="left")
    for off in range(-before, after + 1):
        head = "0" if off == 0 else f"{off:+d}"
        style = "cyan" if off > 0 else ""
        table.add_column(head, justify="right", style=style)

    # Report-label lines, one per (px / rel px / vol) row: the fiscal-quarter id
    # (same label check-valuation uses) on top, then the announce date with its
    # weekday leading. When the announcement fell on a non-trading day, the
    # anchored day-0 bar goes on a third line, prefixed → to flag the shift.
    def _label_lines(qid: str, ann: date, bar: date, session: str | None) -> list[str]:
        id_line = f"[bold]{qid}[/]"
        sess = f" [dim]{session}[/]" if session else ""
        report_line = f"[dim]{ann.strftime('%a')} {ann.isoformat()}[/]{sess}"
        if bar == ann:
            return [id_line, report_line, ""]
        anchor_line = f"[dim]→ {bar.strftime('%a')} {bar.isoformat()}[/]"
        return [id_line, report_line, anchor_line]

    def _mark_react(cells: list[str], k: int) -> list[str]:
        """Bold-magenta the reaction cell and prefix ★ so the reaction bar stands
        out wherever it lands per row (its column varies with the session)."""
        if 0 <= k < len(cells):
            cells = list(cells)
            cells[k] = f"[bold magenta]★{cells[k]}[/]"
        return cells

    shown = 0
    for q in reversed(quarters):         # most recent first
        if shown >= n_show:
            break
        center = bar_index(prices.dates, q.anchor)
        if center is None:
            continue                     # announcement newer than our price history
        bar = prices.dates[center]
        react = zero + reaction_offset(q, prices, before)

        px = _window(prices.close, center, before, after)
        vol = _window(prices.volume, center, before, after)
        # Baseline = the last PRE-news close, i.e. the bar right before the
        # reaction. The reaction is the move INTO the reaction bar: for a pre-open
        # release the news trades the announce bar, so bar -1→0; for an
        # after-close release day 0 is still pre-news, so 0→+1.
        base = px[react - 1] if react - 1 >= 0 else np.nan

        px_cells = [_fmt_px(v) for v in px]
        vol_cells = [_shade_vol(_fmt_vol(v)) for v in vol]
        # rel px: the reaction bar and every bar after it, vs that pre-news close.
        # The reaction bar carries the headline move; earlier bars stay blank.
        rel_cells = ["[dim]·[/]"] * width
        if np.isfinite(base) and base > 0:
            for k in range(react, width):
                if np.isfinite(px[k]):
                    rel_cells[k] = _fmt_rel(px[k] / base - 1.0)

        px_cells = _mark_react(px_cells, react)
        rel_cells = _mark_react(rel_cells, react)

        lbl = _label_lines(q.id, q.anchor, bar, q.session)
        table.add_row(lbl[0], "px", *px_cells)
        table.add_row(lbl[1], "rel px", *rel_cells)
        table.add_row(lbl[2], "vol", *vol_cells)
        table.add_row("")                # spacer between earnings
        shown += 1

    if shown == 0:
        console.print(
            "\n[dim]No announcement dates fall within the available price history.[/]\n"
        )
        return

    console.print(table)
    console.print(
        f"[dim]Day 0 = the announcement bar (announce_date close, or first trading "
        f"day after). ★ marks the reaction bar — the announce bar for a pre-open "
        f"release, the next session for an after-close one. rel px runs from ★ "
        f"onward as the % return vs the last pre-news close (the bar just before "
        f"★), so ★ itself carries the headline move. A →date on the second label "
        f"line marks an announcement on a non-trading day.[/]\n"
    )


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="check-reactions",
        description="Show how each past earnings was received (price + volume "
                    "around the report date).",
    )
    p.add_argument("ticker", help="ticker symbol (needs FINANCIALS.yml + prices)")
    p.add_argument("--before", type=int, default=4,
                   help="trading days of context BEFORE the announcement (default 4)")
    p.add_argument("--after", type=int, default=5,
                   help="trading days AFTER the announcement (default 5)")
    p.add_argument("--show", type=int, default=10,
                   help="max earnings rows, newest first (default 10)")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    ticker = args.ticker.upper()
    if args.before < 0 or args.after < 0:
        raise SystemExit("--before and --after must be non-negative.")
    prices = load_prices(ticker)
    quarters = load_quarters(ticker)
    if not quarters:
        raise SystemExit(f"{ticker}: no report/announce dates in FINANCIALS.yml.")
    report(ticker, prices, quarters, args)


if __name__ == "__main__":
    main()
