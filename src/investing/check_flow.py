"""check-flow — volume-first comparative view of trading activity.

Where check-reaction and check-shape lead with price, check-flow leads with
**volume** and reads it comparatively across two grids:

- an HOURLY grid (this first cut): rows are trading days (newest on top),
  columns are the regular-session hours in ET (09:30 … 15:30). Each cell shows
  the hour's volume, its **day-over-day** change vs. the same hour the day below,
  a **day-to-hour** cumulative share (this hour's volume as a running fraction of
  the day so far), and the hour's close price (dimmed).

- a DAILY grid (to come): rows are weeks, columns are weekdays, with a
  week-over-week comparison.

Hourly bars come from ``prices/hourly/<TICKER>.csv`` (UTC, see fetch-prices).
Pre/post-market bars carry zero volume (a Yahoo limitation) and are dropped —
only the seven regular-session ET hours are kept. Times are converted to
America/New_York so the session buckets stay fixed across DST.

    uv run check-flow NFLX
    uv run check-flow NFLX --days 15
"""

from __future__ import annotations

import argparse
import csv
import io
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from rich.console import Console
from rich.table import Table

from investing.fetch_prices import fetch_ticker

REPO_ROOT = Path(__file__).resolve().parents[2]
HOURLY_DIR = REPO_ROOT / "prices" / "hourly"
DAILY_DIR = REPO_ROOT / "prices" / "daily"
ET = ZoneInfo("America/New_York")
console = Console()

# Weekday columns for the daily grid (Mon–Fri). Holidays leave gaps (NaN).
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]

# The seven regular-session bar starts in ET. 09:30 and 15:30 are 30-minute
# stubs (open auction hour and the last half hour into the 16:00 close); the
# middle five are full hours. Fixed year-round because we bucket in ET, so the
# columns line up across DST.
SESSION_HOURS = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]


# ---------------------------------------------------------------------------
# Silent price refresh
# ---------------------------------------------------------------------------

def refresh_prices(ticker: str) -> None:
    """Pull the latest daily + hourly bars for `ticker` before reading them, so a
    live check-flow reflects today's session (e.g. intraday hourly bars). Reuses
    fetch-prices' fetch/splice logic, silenced (its progress prints are swallowed)
    and best-effort: a network error just leaves the on-disk history as-is."""
    for name, interval, prepost in (("daily", "1d", False), ("hourly", "1h", True)):
        prices_dir = REPO_ROOT / "prices" / name
        prices_dir.mkdir(parents=True, exist_ok=True)
        try:
            with redirect_stdout(io.StringIO()):
                fetch_ticker(ticker, prices_dir=prices_dir,
                             interval=interval, prepost=prepost, quiet=True)
        except Exception:
            pass                       # offline / rate-limited → use what we have


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

@dataclass
class HourGrid:
    """Regular-session hourly bars pivoted to a (day × hour) grid. Each channel
    is a 2-D array [n_days, 7]; row 0 is the OLDEST day (ascending), aligned with
    `days`. Missing cells (e.g. a half day) are NaN."""
    days: list[date]
    close: np.ndarray          # [n_days, 7]
    volume: np.ndarray         # [n_days, 7]


def load_hourly(ticker: str) -> HourGrid:
    path = HOURLY_DIR / f"{ticker}.csv"
    if not path.exists():
        raise SystemExit(
            f"No hourly file at {path.relative_to(REPO_ROOT)} — "
            f"run `uv run fetch-prices --hourly {ticker}` first."
        )
    # (day, hour) -> (close, volume). Only regular-session, volume-bearing bars.
    close_by: dict[tuple[date, str], float] = {}
    vol_by: dict[tuple[date, str], float] = {}
    hour_index = {h: i for i, h in enumerate(SESSION_HOURS)}
    with path.open() as f:
        for row in csv.DictReader(f):
            try:
                vol = float(row["Volume"])
                if vol <= 0:
                    continue                       # pre/post-market bar
                dt = datetime.fromisoformat(row["Datetime"]).astimezone(ET)
                hh = dt.strftime("%H:%M")
                if hh not in hour_index:
                    continue                       # off-grid (rare DST edge)
                key = (dt.date(), hh)
                close_by[key] = float(row["Close"])
                vol_by[key] = vol
            except (ValueError, KeyError):
                continue
    if not vol_by:
        raise SystemExit(f"{ticker}: no regular-session hourly bars in {path.name}.")

    days = sorted({d for (d, _) in vol_by})
    n = len(days)
    day_index = {d: i for i, d in enumerate(days)}
    close = np.full((n, len(SESSION_HOURS)), np.nan)
    volume = np.full((n, len(SESSION_HOURS)), np.nan)
    for (d, hh), v in vol_by.items():
        i, j = day_index[d], hour_index[hh]
        volume[i, j] = v
        close[i, j] = close_by[(d, hh)]
    return HourGrid(days=days, close=close, volume=volume)


@dataclass
class WeekGrid:
    """Daily bars pivoted to a (week × weekday) grid. Each channel is [n_weeks, 5];
    row 0 is the OLDEST week (ascending), aligned with `weeks` (each the Monday
    date of that ISO week). Missing weekdays (holidays) are NaN."""
    weeks: list[date]          # Monday of each week, ascending
    close: np.ndarray          # [n_weeks, 5]
    volume: np.ndarray         # [n_weeks, 5]


def _week_monday(d: date) -> date:
    return d.fromordinal(d.toordinal() - d.weekday())


def load_daily(ticker: str) -> WeekGrid:
    path = DAILY_DIR / f"{ticker}.csv"
    if not path.exists():
        raise SystemExit(
            f"No daily file at {path.relative_to(REPO_ROOT)} — "
            f"run `uv run fetch-prices {ticker}` first."
        )
    close_by: dict[tuple[date, int], float] = {}
    vol_by: dict[tuple[date, int], float] = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            try:
                d = date.fromisoformat(row["Date"][:10])
                wd = d.weekday()
                if wd > 4:
                    continue                       # weekend bar (rare)
                key = (_week_monday(d), wd)
                close_by[key] = float(row["Close"])
                vol_by[key] = float(row["Volume"])
            except (ValueError, KeyError):
                continue
    if not vol_by:
        raise SystemExit(f"{ticker}: no usable daily rows in {path.name}.")

    weeks = sorted({w for (w, _) in vol_by})
    n = len(weeks)
    week_index = {w: i for i, w in enumerate(weeks)}
    close = np.full((n, len(WEEKDAYS)), np.nan)
    volume = np.full((n, len(WEEKDAYS)), np.nan)
    for (w, wd), v in vol_by.items():
        i = week_index[w]
        volume[i, wd] = v
        close[i, wd] = close_by[(w, wd)]
    return WeekGrid(weeks=weeks, close=close, volume=volume)


# ---------------------------------------------------------------------------
# Flow metrics
# ---------------------------------------------------------------------------

def flow_metrics(volume: np.ndarray, baseline_n: int = 5):
    """Period-over-period change and cumulative fill vs a median full period.

    `volume` is [n_rows, n_cols], rows ascending (oldest first); a row is a day
    (columns = hours) or a week (columns = weekdays).

    - change[i,j] = volume[i,j] / volume[i-1,j] - 1 — this cell vs the same
      column the prior row (day-over-day / week-over-week).
    - fill[i,j] = (cumulative volume through column j in row i) / baseline —
      how full the period is by that column, measured against a normal full
      period. Meaningful even for a still-running row: it just reads partial.
    - baseline = median total volume over the last `baseline_n` FULL rows (rows
      with every column present). The current partial row has missing columns so
      it is naturally excluded from the baseline.

    Returns (change, fill, baseline).
    """
    change = np.full_like(volume, np.nan)
    change[1:] = volume[1:] / volume[:-1] - 1.0

    full_mask = np.isfinite(volume).all(axis=1)      # rows with every column
    totals = np.nansum(volume, axis=1)
    full_totals = totals[full_mask]
    baseline = (float(np.median(full_totals[-baseline_n:]))
                if full_totals.size else np.nan)

    cum = np.nancumsum(volume, axis=1)
    cum[~np.isfinite(volume)] = np.nan               # don't carry over empty cols
    if np.isfinite(baseline) and baseline > 0:
        fill = cum / baseline
    else:
        fill = np.full_like(volume, np.nan)
    return change, fill, baseline


# ---------------------------------------------------------------------------
# Formatting (mirrors check_reaction's px / vol / rel cell style)
# ---------------------------------------------------------------------------

def _fmt_px(x: float) -> str:
    return "[dim]·[/]" if not np.isfinite(x) else f"[dim]{x:.2f}[/]"


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
    return f"[{color}]{x:+.0%}[/]"


def _fmt_share(x: float) -> str:
    return "[dim]·[/]" if not np.isfinite(x) else f"[dim]{x:.0%}[/]"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report_hourly(ticker: str, grid: HourGrid, n_days: int) -> None:
    close, volume, days = grid.close, grid.volume, grid.days
    n = len(days)

    # dod = same-hour prior day; fill = cumulative volume through the hour vs the
    # median full day (last 5 complete days), so a still-running day reads partial
    # instead of a meaningless 100%.
    dod, fill, baseline = flow_metrics(volume)

    console.print()
    console.print(
        f"[bold]{ticker}[/] — hourly volume flow "
        f"(newest {min(n_days, n)} of {n} days; ET session hours)"
    )
    console.print(
        "[dim]per cell: volume · Δ day-over-day (same hour) · fill % vs 5-day "
        f"median day ({_fmt_vol(baseline)}) · close[/]"
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("day", justify="left", no_wrap=True)
    table.add_column("", justify="left")
    for h in SESSION_HOURS:
        table.add_column(h, justify="right")

    order = list(range(n - 1, -1, -1))[:n_days]     # newest first
    for i in order:
        vol_cells = [_fmt_vol(v) for v in volume[i]]
        dod_cells = [_fmt_rel(v) for v in dod[i]]
        fill_cells = [_fmt_share(v) for v in fill[i]]
        px_cells = [_fmt_px(v) for v in close[i]]

        d = days[i]
        lbl = f"[bold]{d.strftime('%a')}[/] [dim]{d.isoformat()}[/]"
        table.add_row(lbl, "vol", *vol_cells)
        table.add_row("", "Δ dod", *dod_cells)
        table.add_row("", "fill%", *fill_cells)
        table.add_row("", "px", *px_cells)
        table.add_row("")

    console.print(table)


def report_daily(ticker: str, grid: WeekGrid, n_weeks: int) -> None:
    close, volume, weeks = grid.close, grid.volume, grid.weeks
    n = len(weeks)

    # wow = same-weekday prior week; fill = cumulative volume through the weekday
    # vs the median full week (last 5 complete Mon–Fri weeks), so the running week
    # reads partial instead of 100%.
    wow, fill, baseline = flow_metrics(volume)

    console.print()
    console.print(
        f"[bold]{ticker}[/] — daily volume flow "
        f"(newest {min(n_weeks, n)} of {n} weeks; Mon–Fri)"
    )
    console.print(
        "[dim]per cell: volume · Δ week-over-week (same weekday) · fill % vs 5-week "
        f"median week ({_fmt_vol(baseline)}) · close[/]"
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("week of", justify="left", no_wrap=True)
    table.add_column("", justify="left")
    for wd in WEEKDAYS:
        table.add_column(wd, justify="right")

    order = list(range(n - 1, -1, -1))[:n_weeks]     # newest first
    for i in order:
        vol_cells = [_fmt_vol(v) for v in volume[i]]
        wow_cells = [_fmt_rel(v) for v in wow[i]]
        fill_cells = [_fmt_share(v) for v in fill[i]]
        px_cells = [_fmt_px(v) for v in close[i]]

        w = weeks[i]
        lbl = f"[bold]{w.isoformat()}[/]"
        table.add_row(lbl, "vol", *vol_cells)
        table.add_row("", "Δ wow", *wow_cells)
        table.add_row("", "fill%", *fill_cells)
        table.add_row("", "px", *px_cells)
        table.add_row("")

    console.print(table)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="check-flow",
        description="Volume-first comparative view: hourly (day-over-day) and "
                    "daily (week-over-week) grids.",
    )
    p.add_argument("ticker", help="ticker symbol (needs hourly prices)")
    p.add_argument("--days", type=int, default=4,
                   help="hourly rows (trading days), newest first (default 4)")
    p.add_argument("--weeks", type=int, default=4,
                   help="daily-table rows (weeks), newest first (default 4)")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    ticker = args.ticker.upper()
    refresh_prices(ticker)
    report_hourly(ticker, load_hourly(ticker), args.days)
    report_daily(ticker, load_daily(ticker), args.weeks)


if __name__ == "__main__":
    main()
