#!/usr/bin/env python3
"""
discover_filings.py — Auto-discover a ticker's past quarterly SEC filings.

Where `fetch_report` needs a hand-built `sources.json` (one entry per quarter,
with the announcement date and quarter label filled in by hand), this script
*generates* that file: it resolves the CIK, enumerates every 10-Q / 10-K in a
date window from EDGAR's submissions feed, derives the fiscal quarter label from
each filing's period-end and the company's fiscal-year-end, writes the merged
`sources.json`, and then downloads each report via the existing pipeline.

The raw statement markdown is produced automatically; transcribing the numbers
into `FINANCIALS.yml` is still a manual step (per CLAUDE.md).

Usage:
  uv run discover-filings ABNB                 # last 3 fiscal years
  uv run discover-filings ABNB --years 5       # last 5 fiscal years
  uv run discover-filings ABNB --since 2023-01-01
  uv run discover-filings ABNB --no-download   # only write sources.json

Output: <TICKER>/sources.json  +  <TICKER>/quarters/<date>_<quarter>-report.md
"""

import sys
import time
from datetime import datetime

from investing.lib import REPO_ROOT, load_sources
from investing.fetch_report import (
    EDGAR_ARCHIVE,
    EDGAR_DATA,
    REQUEST_DELAY,
    download,
    fetch_json,
    get_or_resolve_cik,
    save_sources,
)

QUARTERLY_FORMS = {"10-Q", "10-K", "20-F"}


# ---------------------------------------------------------------------------
# Fiscal-quarter labelling
# ---------------------------------------------------------------------------

def fiscal_label(period_end: str, fiscal_year_end: str) -> tuple[int, str, str]:
    """Map a period-end date to (quarter_number, fy_year_label, quarter_id).

    `fiscal_year_end` is EDGAR's MMDD string (e.g. "1231", "0630").
    Returns e.g. (3, "2025", "q3-2025") for calendar filers, or
    (1, "2026", "q1-fy2026") when the fiscal year is off-calendar.
    """
    pe = datetime.strptime(period_end, "%Y-%m-%d")
    fye_month = int(fiscal_year_end[:2]) if fiscal_year_end else 12

    # Quarters since the start of the fiscal year (Q after fye is Q1).
    qnum = ((pe.month - fye_month - 1) % 12) // 3 + 1

    # Fiscal-year label = the calendar year in which the fiscal year ends.
    fy_year = pe.year if pe.month <= fye_month else pe.year + 1

    calendar_aligned = fye_month == 12
    qid = f"q{qnum}-{fy_year}" if calendar_aligned else f"q{qnum}-fy{fy_year}"
    return qnum, str(fy_year), qid


# ---------------------------------------------------------------------------
# Filing enumeration
# ---------------------------------------------------------------------------

def enumerate_filings(cik: str, since: datetime) -> list[dict]:
    """Return quarterly/annual filings on/after `since`, newest first."""
    url = f"{EDGAR_DATA}/submissions/CIK{cik}.json"
    time.sleep(REQUEST_DELAY)
    data = fetch_json(url)
    if not data:
        return []

    fiscal_year_end = data.get("fiscalYearEnd", "1231")
    print(f"  Fiscal year end: {fiscal_year_end}")

    blocks = [data.get("filings", {}).get("recent", {})]
    # Older filings live in separate files; pull them only if the window
    # reaches past what "recent" covers.
    for older in data.get("filings", {}).get("files", []):
        oldest_recent = blocks[0].get("filingDate", [""])[-1]
        if oldest_recent and oldest_recent < since.strftime("%Y-%m-%d"):
            break
        time.sleep(REQUEST_DELAY)
        older_data = fetch_json(f"{EDGAR_DATA}/submissions/{older['name']}")
        if older_data:
            blocks.append(older_data)

    out: list[dict] = []
    for block in blocks:
        forms = block.get("form", [])
        accessions = block.get("accessionNumber", [])
        filing_dates = block.get("filingDate", [])
        report_dates = block.get("reportDate", [])
        for form, accn, filed, period in zip(forms, accessions, filing_dates, report_dates):
            if form not in QUARTERLY_FORMS or not period:
                continue
            try:
                filed_dt = datetime.strptime(filed, "%Y-%m-%d")
            except ValueError:
                continue
            if filed_dt < since:
                continue
            qnum, fy_year, qid = fiscal_label(period, fiscal_year_end)
            out.append({
                "form": form,
                "accession": accn,
                "filed": filed,
                "period_end": period,
                "quarter": qid,
            })

    out.sort(key=lambda f: f["filed"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    years, since_str, do_download = 3, None, True

    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--years":
            years = int(args[i + 1]); i += 2
        elif a == "--since":
            since_str = args[i + 1]; i += 2
        elif a == "--no-download":
            do_download = False; i += 1
        else:
            rest.append(a); i += 1

    if len(rest) != 1:
        print(__doc__)
        sys.exit(1)

    ticker = rest[0].upper()
    since = (datetime.strptime(since_str, "%Y-%m-%d") if since_str
             else datetime.now().replace(year=datetime.now().year - years))

    print(f"\n{'='*60}\nDiscovering {ticker} filings since {since:%Y-%m-%d}\n{'='*60}")

    (REPO_ROOT / ticker).mkdir(exist_ok=True)
    sources = load_sources(ticker)

    cik = get_or_resolve_cik(ticker, sources)
    if not cik:
        sys.exit(f"Could not resolve CIK for {ticker}")

    filings = enumerate_filings(cik, since)
    if not filings:
        sys.exit("No quarterly filings found in window.")

    print(f"\n  Found {len(filings)} filing(s):")
    for f in filings:
        print(f"    {f['filed']}  {f['form']:5s}  {f['quarter']:12s}  period {f['period_end']}")

    # Merge into sources.json (don't clobber existing hand-entered keys).
    for f in filings:
        date = f["filed"]
        old = sources.get(date, {})
        accn_nodash = f["accession"].replace("-", "")
        sources[date] = {
            "quarter": old.get("quarter") or f["quarter"],
            "period_end": old.get("period_end") or f["period_end"],
            "report": old.get("report") or f"{EDGAR_ARCHIVE}/{int(cik)}/{accn_nodash}/",
            **{k: v for k, v in old.items() if k not in ("quarter", "period_end", "report")},
        }
    save_sources(ticker, sources)
    print(f"\n  Wrote {ticker}/sources.json")

    if not do_download:
        print("  --no-download: skipping report downloads.")
        return

    ok = 0
    for f in filings:
        date, quarter = f["filed"], sources[f["filed"]]["quarter"]
        report_path = REPO_ROOT / ticker / "quarters" / f"{date}_{quarter}-report.md"
        if report_path.exists():
            print(f"\n  {report_path.name} already exists — skipping.")
            ok += 1
            continue
        accn_nodash = f["accession"].replace("-", "")
        url = f"{EDGAR_ARCHIVE}/{int(cik)}/{accn_nodash}/"
        ok += download(ticker, date, quarter, report_path, explicit_url=url)

    print(f"\nDone: {ok}/{len(filings)} report(s) available.")


if __name__ == "__main__":
    main()
