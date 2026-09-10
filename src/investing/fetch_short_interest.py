#!/usr/bin/env python3
"""
fetch_short_interest.py — Fill each quarter's short interest in FINANCIALS.yml
from FINRA's official semi-monthly reports.

Source: FINRA's `consolidatedShortInterest` dataset. "Consolidated" means summed
across all venues — every short position reported by every member firm for the
symbol, rather than split per listing exchange. It is a gross point-in-time count
of shares sold short on the settlement date; longs do not net against it, and it
is NOT float-adjusted, so it can exceed the float in heavily shorted names.

Two properties of the feed drive this script:

  - Settlements are semi-monthly (the 15th and month end), so a fiscal quarter
    end usually — but not always — coincides with one. When month end falls on a
    weekend the settlement drifts a few days earlier. We take the reading NEAREST
    each quarter's `end_date` and record which date it came from, so the pairing
    stays honest and check-valuation can show the drift.

  - Figures publish ~8 days AFTER the settlement date. A just-closed quarter may
    have no reading yet; those quarters are reported as pending and left alone.

FINRA keys by US trading symbol for every ticker in this repo, including foreign
filers that need home-exchange overrides elsewhere (KGC, GTBIF) — so no `_meta`
mapping is needed, unlike fetch-transcript.

Values are written in the file's own `unit` (thousands / millions / units) to
match `shares_outstanding`, which the "% of shares out" row divides by. Existing
readings are never overwritten.

Usage:
  # Fill every quarter that is missing a reading
  uv run fetch-short-interest ODD

  # Several tickers
  uv run fetch-short-interest ODD BARK NFLX

  # Show what would be written, without touching the file
  uv run fetch-short-interest ODD --dry-run
"""

import argparse
import csv
import io
import json
import re
import urllib.error
import urllib.request
from datetime import date

import yaml
from rich.console import Console
from rich.table import Table

from investing.lib import REPO_ROOT, get_logger, setup_logging

log = get_logger(__name__)
console = Console()

FINRA_URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
}

# Settlements land on the 15th and month end, so a quarter end is at most ~3 days
# from one (weekend drift). ±6 accepts genuine drift while rejecting a match from
# the wrong fortnight, which would silently pair a quarter with stale data.
MAX_LAG_DAYS = 6

UNIT_DIV = {"thousands": 1_000, "millions": 1_000_000, "units": 1}


def fetch_settlements(ticker: str) -> list[tuple[date, int]]:
    """Every (settlement_date, shares_short) FINRA has for `ticker`, oldest first.

    The endpoint returns the full history since listing in one call — there is no
    per-quarter URL to record, which is why this is a script and not a SOURCES.yml
    entry. Responses are CSV despite the JSON request body.
    """
    body = json.dumps({
        "limit": 1000,
        "compareFilters": [
            {"fieldName": "symbolCode", "fieldValue": ticker, "compareType": "equal"}
        ],
    }).encode()
    req = urllib.request.Request(FINRA_URL, data=body, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        log.error("FINRA request for %s failed: HTTP %s %s", ticker, e.code, e.reason)
        return []
    except (urllib.error.URLError, TimeoutError) as e:
        log.error("FINRA request for %s failed: %s", ticker, e)
        return []

    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            rows.append((date.fromisoformat(r["settlementDate"]),
                         int(r["currentShortPositionQuantity"])))
        except (ValueError, KeyError, TypeError):
            continue
    rows.sort()
    return rows


def nearest(settlements: list[tuple[date, int]], target: date) -> tuple[date, int] | None:
    """The settlement closest to `target`, or None if the nearest is too far off.
    Ties break toward the earlier date, which is the one that actually drifted."""
    if not settlements:
        return None
    best = min(settlements, key=lambda s: (abs((s[0] - target).days), s[0]))
    return best if abs((best[0] - target).days) <= MAX_LAG_DAYS else None


def as_date(v) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def insert_fields(lines: list[str], updates: dict[str, tuple[int, date]]) -> list[str]:
    """Write `shares_short` + `short_settlement_date` into the raw YAML text.

    Edited as text rather than via a yaml round-trip so hand-written comments,
    blank lines and key order survive untouched. The pair is anchored after
    `shares_outstanding` where present (they belong together — one divides the
    other), else after `end_date`, which every quarter has.
    """
    # find each quarter's anchor line up front: shares_outstanding if it has one,
    # else end_date (which every quarter has), so insertion is a single pass
    anchors: dict[str, int] = {}
    qid = None
    for i, line in enumerate(lines):
        m = re.match(r"\s*-\s+id:\s*(\S+)", line)
        if m:
            qid = m.group(1)
            continue
        if qid not in updates:
            continue
        name = re.match(r"\s*(\w+):", line)
        if not name:
            continue
        if name.group(1) == "end_date":
            anchors.setdefault(qid, i)
        elif name.group(1) == "shares_outstanding":
            anchors[qid] = i  # preferred anchor — overrides end_date

    insert_at = {i: qid for qid, i in anchors.items()}
    out = []
    for i, line in enumerate(lines):
        out.append(line)
        qid = insert_at.get(i)
        if qid:
            indent = re.match(r"(\s*)", line).group(1)
            short, sd = updates[qid]
            out.append(f"{indent}shares_short: {short}")
            out.append(f"{indent}short_settlement_date: {sd}")
    return out


def process(ticker: str, dry_run: bool) -> None:
    path = REPO_ROOT / ticker / "FINANCIALS.yml"
    if not path.exists():
        console.print(f"  [yellow]{ticker}:[/yellow] no FINANCIALS.yml — skipping")
        return

    raw = path.read_text()
    data = yaml.safe_load(raw)
    quarters = data.get("quarters") or []
    div = UNIT_DIV.get(data.get("unit", "thousands"), 1_000)

    settlements = fetch_settlements(ticker)
    if not settlements:
        console.print(f"  [yellow]{ticker}:[/yellow] FINRA returned no short interest")
        return
    newest = settlements[-1][0]

    table = Table(title=f"[bold cyan]{ticker}[/bold cyan]  "
                        f"[dim]{len(settlements)} settlements through {newest}[/dim]",
                  header_style="bold")
    table.add_column("quarter")
    table.add_column("period end", justify="right")
    table.add_column("settlement", justify="right")
    table.add_column("lag", justify="right")
    table.add_column("shares short", justify="right")
    table.add_column("status")

    updates: dict[str, tuple[int, date]] = {}
    for q in quarters:
        qid, end = q.get("id"), as_date(q.get("end_date"))
        if not qid or not end:
            continue
        if q.get("shares_short") is not None:
            continue  # never overwrite a value already in the file

        hit = nearest(settlements, end)
        if hit is None:
            # distinguish "not published yet" from "before this symbol existed"
            if end > newest:
                status = "[dim]pending (not published)[/dim]"
            elif end < settlements[0][0]:
                status = "[dim]pre-listing[/dim]"
            else:
                status = f"[yellow]no settlement within {MAX_LAG_DAYS}d[/yellow]"
            table.add_row(qid, str(end), "—", "—", "—", status)
            continue

        sd, shares = hit
        lag = (sd - end).days
        updates[qid] = (round(shares / div), sd)
        table.add_row(qid, str(end), str(sd),
                      f"{lag:+d}d" if lag else "0d",
                      f"{shares:,}",
                      "[green]will add[/green]" if not dry_run else "[dim]dry-run[/dim]")

    if not updates:
        console.print(table)
        console.print(f"  [dim]{ticker}: nothing to add[/dim]\n")
        return

    console.print(table)
    if dry_run:
        console.print(f"  [dim]{ticker}: dry run — no changes written[/dim]\n")
        return

    lines = insert_fields(raw.splitlines(), updates)
    path.write_text("\n".join(lines) + "\n")
    console.print(f"  [green]{ticker}: added {len(updates)} reading(s)[/green]\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fill missing short interest in FINANCIALS.yml from FINRA.")
    parser.add_argument("tickers", nargs="+", help="Tickers")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="Show what would be written without changing the file")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose logging (show debug detail and tracebacks)")
    args = parser.parse_args()
    setup_logging(args.verbose)

    for ticker in (t.upper() for t in args.tickers):
        process(ticker, args.dry_run)


if __name__ == "__main__":
    main()
