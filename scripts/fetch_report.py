#!/usr/bin/env python3
"""
fetch_report.py — Download quarterly financial statements from SEC EDGAR.

Fetches the income statement, balance sheet, and cash flow statement from the
10-Q (or 10-K) filing on SEC EDGAR and saves them as a markdown file alongside
the transcript.

sources.json format:
  {
    "_meta": {"cik": "0001819574"},
    "2025-08-07": {
      "quarter": "q1-2026",
      "transcript": "...",
      "report": "https://..."   # saved on first successful fetch
    }
  }

Usage:
  # Download all missing financial reports across the whole repo
  python3 scripts/fetch_report.py

  # Download a specific quarter by ticker and announcement date
  python3 scripts/fetch_report.py BARK 2025-08-07

  # Supply a URL directly (EDGAR filing index, bypasses auto-discovery)
  python3 scripts/fetch_report.py BARK 2025-08-07 --url https://www.sec.gov/Archives/edgar/data/.../

Output: <TICKER>/quarters/<date>_<quarter>-financials.md
"""

import re
import sys
import gzip
import json
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

HEADERS = {
    "User-Agent": "investment-research-bot research@example.com",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

EDGAR_DATA = "https://data.sec.gov"
EDGAR_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"
REQUEST_DELAY = 0.6  # SEC asks for max 10 req/sec


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            enc = resp.info().get("Content-Encoding", "")
        return gzip.decompress(raw) if enc == "gzip" else raw
    except Exception as e:
        print(f"  HTTP error: {e}")
        return None


def fetch_json(url: str) -> dict | None:
    raw = fetch(url)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}")
        return None


def fetch_text(url: str) -> str | None:
    raw = fetch(url)
    return raw.decode("utf-8", errors="replace") if raw else None


# ---------------------------------------------------------------------------
# EDGAR: CIK resolution
# ---------------------------------------------------------------------------

def resolve_cik(ticker: str) -> str | None:
    """Return 10-digit zero-padded CIK for a ticker symbol."""
    print(f"  Looking up CIK for {ticker}...")
    time.sleep(REQUEST_DELAY)
    data = fetch_json("https://www.sec.gov/files/company_tickers.json")
    if not data:
        return None
    ticker_upper = ticker.upper()
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            cik = str(entry["cik_str"]).zfill(10)
            print(f"  CIK = {cik}")
            return cik
    print(f"  CIK not found for {ticker}")
    return None


# ---------------------------------------------------------------------------
# EDGAR: Filing discovery
# ---------------------------------------------------------------------------

def find_filing(cik: str, announcement_date: str) -> tuple[str, str] | None:
    """Find the 10-Q/10-K filed closest to announcement_date.

    Returns (accession_number, period_end_date) or None.
    Searches the most recent 100 filings; falls back to older filings if needed.
    """
    url = f"{EDGAR_DATA}/submissions/CIK{cik}.json"
    time.sleep(REQUEST_DELAY)
    data = fetch_json(url)
    if not data:
        return None

    ann_dt = datetime.strptime(announcement_date, "%Y-%m-%d")

    def search_filings(filings_block: dict) -> tuple[str, str] | None:
        forms = filings_block.get("form", [])
        accessions = filings_block.get("accessionNumber", [])
        filing_dates = filings_block.get("filingDate", [])
        report_dates = filings_block.get("reportDate", [])
        best, best_diff = None, timedelta(days=30)
        for form, accn, filed, period in zip(forms, accessions, filing_dates, report_dates):
            if form not in ("10-Q", "10-K"):
                continue
            try:
                filed_dt = datetime.strptime(filed, "%Y-%m-%d")
            except ValueError:
                continue
            diff = abs(filed_dt - ann_dt)
            if diff < best_diff:
                best_diff = diff
                best = (accn, period)
        return best

    result = search_filings(data.get("filings", {}).get("recent", {}))
    if not result:
        # Check older filings pages if any
        for older in data.get("filings", {}).get("files", []):
            time.sleep(REQUEST_DELAY)
            older_data = fetch_json(f"{EDGAR_DATA}/submissions/{older['name']}")
            if older_data:
                result = search_filings(older_data)
                if result:
                    break

    if result:
        print(f"  Found filing: {result[0]}  period: {result[1]}")
    else:
        print(f"  No 10-Q/10-K found within 30 days of {announcement_date}")
    return result


# ---------------------------------------------------------------------------
# EDGAR: FilingSummary → statement R-files
# ---------------------------------------------------------------------------

# Keywords in LongName that identify each statement type.
STATEMENT_KEYWORDS = {
    "income":    ["operations", "earnings", "comprehensive loss", "comprehensive income"],
    "balance":   ["balance sheet", "financial position"],
    "cashflow":  ["cash flow"],
}

# Skip parenthetical companion sheets.
SKIP_KEYWORDS = ["parenthetical", "parenthetical"]


def get_statement_files(cik: str, accession: str) -> list[tuple[str, str, str]]:
    """Parse FilingSummary.xml and return [(stmt_type, short_name, r_filename)]."""
    accn_nodash = accession.replace("-", "")
    cik_int = int(cik)
    url = f"{EDGAR_ARCHIVE}/{cik_int}/{accn_nodash}/FilingSummary.xml"
    print(f"  Fetching filing index...")
    time.sleep(REQUEST_DELAY)
    xml_text = fetch_text(url)
    if not xml_text:
        print("  FilingSummary.xml not found")
        return []

    # Strip namespace so findall works without prefix
    xml_text = re.sub(r' xmlns="[^"]+"', "", xml_text, count=1)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  XML parse error: {e}")
        return []

    results = []
    seen_types: set[str] = set()
    for report in root.findall(".//Report"):
        long_name = (report.findtext("LongName") or "").lower()
        short_name = (report.findtext("ShortName") or "").strip()
        html_file = report.findtext("HtmlFileName") or ""
        if not html_file:
            continue
        if any(k in long_name for k in SKIP_KEYWORDS):
            continue
        for stmt_type, keywords in STATEMENT_KEYWORDS.items():
            if stmt_type in seen_types:
                continue
            if any(k in long_name for k in keywords):
                results.append((stmt_type, short_name, html_file))
                seen_types.add(stmt_type)
                break

    return results


# ---------------------------------------------------------------------------
# R-file HTML → markdown table
# ---------------------------------------------------------------------------

def _cell_text(cell_html: str) -> str:
    """Extract clean text from an R-file table cell."""
    # Remove <span></span> (empty XBRL anchors)
    cell_html = re.sub(r"<span[^>]*></span>", "", cell_html)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", cell_html)
    # HTML entities
    text = (text
            .replace("&#160;", "")
            .replace("&nbsp;", "")
            .replace("&#8212;", "—")
            .replace("&#8211;", "–")
            .replace("&#8203;", "")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">"))
    return " ".join(text.split())


def r_file_to_markdown(html: str, short_name: str) -> str:
    """Convert an EDGAR R-file HTML table to markdown."""
    # Scope to the main report table only — the rest of the document is XBRL
    # metadata in hidden divs that would pollute the output.
    table_m = re.search(r'<table[^>]*class="report"[^>]*>(.*?)</table>', html, re.DOTALL | re.IGNORECASE)
    if table_m:
        html = table_m.group(1)

    rows_md: list[list[str]] = []
    for tr_m in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE):
        tr_html = tr_m.group(1)
        cells: list[str] = []

        for cell_m in re.finditer(r"<t([dh])([^>]*)>(.*?)</t\1>", tr_html, re.DOTALL | re.IGNORECASE):
            tag = cell_m.group(1)   # d or h
            attrs = cell_m.group(2)
            inner = cell_m.group(3)
            text = _cell_text(inner)

            # colspan — repeat cell text across columns
            colspan_m = re.search(r'colspan="(\d+)"', attrs)
            colspan = int(colspan_m.group(1)) if colspan_m else 1
            cells.extend([text] * colspan)

        if not any(c.strip() for c in cells):
            continue

        rows_md.append(cells)

    if not rows_md:
        return ""

    # Normalize column count
    max_cols = max(len(r) for r in rows_md)
    rows_md = [r + [""] * (max_cols - len(r)) for r in rows_md]

    lines: list[str] = []
    for i, row in enumerate(rows_md):
        line = "| " + " | ".join(row) + " |"
        lines.append(line)
        # Insert separator after first row (header)
        if i == 0:
            lines.append("|" + "|".join(["---"] * max_cols) + "|")

    return "\n".join(lines)


def fetch_statement(cik: str, accession: str, short_name: str, r_filename: str) -> str | None:
    """Fetch one R-file and return as a markdown table string."""
    accn_nodash = accession.replace("-", "")
    cik_int = int(cik)
    url = f"{EDGAR_ARCHIVE}/{cik_int}/{accn_nodash}/{r_filename}"
    time.sleep(REQUEST_DELAY)
    html = fetch_text(url)
    if not html:
        print(f"  Could not fetch {r_filename}")
        return None
    md = r_file_to_markdown(html, short_name)
    return md if md else None


# ---------------------------------------------------------------------------
# sources.json helpers
# ---------------------------------------------------------------------------

def load_sources(ticker: str) -> dict:
    path = REPO_ROOT / ticker / "sources.json"
    if path.exists():
        with path.open() as f:
            return json.load(f)
    return {}


def save_sources(ticker: str, sources: dict):
    path = REPO_ROOT / ticker / "sources.json"
    with path.open("w") as f:
        json.dump(sources, f, indent=2)
        f.write("\n")


def get_or_resolve_cik(ticker: str, sources: dict) -> str | None:
    """Return CIK from sources._meta, auto-discovering and saving if missing."""
    cik = sources.get("_meta", {}).get("cik")
    if cik:
        return cik
    cik = resolve_cik(ticker)
    if cik:
        sources.setdefault("_meta", {})["cik"] = cik
        save_sources(ticker, sources)
    return cik


# ---------------------------------------------------------------------------
# Repo discovery
# ---------------------------------------------------------------------------

def find_jobs(ticker_filter: str | None = None, date_filter: str | None = None) -> list[tuple]:
    """Return [(ticker, date, quarter, report_path)] for all quarters in sources.json."""
    jobs = []
    pattern = f"{ticker_filter}/sources.json" if ticker_filter else "*/sources.json"
    for sources_path in sorted(REPO_ROOT.glob(pattern)):
        ticker = sources_path.parent.name
        quarters_dir = sources_path.parent / "quarters"
        with sources_path.open() as f:
            sources = json.load(f)
        for date, entry in sources.items():
            if date.startswith("_") or not isinstance(entry, dict):
                continue
            if date_filter and date != date_filter:
                continue
            quarter = entry.get("quarter", "")
            if not quarter:
                continue
            report_path = quarters_dir / f"{date}_{quarter}-financials.md"
            jobs.append((ticker, date, quarter, report_path))
    return jobs


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_report(path: Path, ticker: str, date: str, quarter: str,
                period_end: str, statements: dict[str, str], source_url: str):
    path.parent.mkdir(exist_ok=True)
    header = (
        f"# {ticker} — {quarter.upper().replace('-', ' ')} Financial Statements\n\n"
        f"**Announcement date:** {date}  \n"
        f"**Period ended:** {period_end}  \n"
        f"**Source:** {source_url}\n\n"
        "---\n\n"
    )
    sections: list[str] = []
    for stmt_type, section_header in [
        ("income",   "## Statements of Operations"),
        ("balance",  "## Balance Sheet"),
        ("cashflow", "## Statements of Cash Flows"),
    ]:
        if stmt_type in statements:
            sections.append(f"{section_header}\n\n{statements[stmt_type]}")

    path.write_text(header + "\n\n".join(sections) + "\n")
    print(f"  Saved → {path.relative_to(REPO_ROOT)}")


# ---------------------------------------------------------------------------
# Main download logic
# ---------------------------------------------------------------------------

def download(ticker: str, date: str, quarter: str, report_path: Path,
             explicit_url: str | None = None) -> bool:
    print(f"\n{'='*60}")
    print(f"{ticker}  {quarter}  ({date})")
    print(f"{'='*60}")

    sources = load_sources(ticker)

    # --- Resolve CIK and accession number ---
    if explicit_url:
        m = re.search(r"/edgar/data/(\d+)/(\d+)/", explicit_url)
        if not m:
            print("  FAILED: URL must contain /edgar/data/<cik>/<accession>/")
            return False
        cik = m.group(1).zfill(10)
        accn_nodash = m.group(2)
        accession = f"{accn_nodash[:10]}-{accn_nodash[10:12]}-{accn_nodash[12:]}"
        period_end = sources.get(date, {}).get("period_end", "")
        source_url = explicit_url
    else:
        cik = get_or_resolve_cik(ticker, sources)
        if not cik:
            print(f"  FAILED: could not resolve CIK for {ticker}")
            return False

        filing = find_filing(cik, date)
        if not filing:
            return False
        accession, period_end = filing
        accn_nodash = accession.replace("-", "")
        source_url = f"{EDGAR_ARCHIVE}/{int(cik)}/{accn_nodash}/"

        # Persist discovered URL and period_end into sources.json,
        # keeping key order: quarter, period_end, report, transcript, ...
        old = sources.get(date, {})
        sources[date] = {
            "quarter":    old.get("quarter", ""),
            "period_end": period_end,
            "report":     source_url,
            **{k: v for k, v in old.items() if k not in ("quarter", "period_end", "report")},
        }
        save_sources(ticker, sources)

    # --- Get list of statement R-files from FilingSummary ---
    stmt_files = get_statement_files(cik, accession)
    if not stmt_files:
        print("  FAILED: no financial statement files found")
        return False

    print(f"  Statements: {[(t, f) for t, _, f in stmt_files]}")

    # --- Fetch each statement ---
    statements: dict[str, str] = {}
    for stmt_type, short_name, r_filename in stmt_files:
        print(f"  Fetching {stmt_type} ({r_filename})...")
        md = fetch_statement(cik, accession, short_name, r_filename)
        if md:
            statements[stmt_type] = md
        else:
            print(f"  Warning: could not parse {r_filename}")

    if not statements:
        print("  FAILED: no statements could be fetched")
        return False

    save_report(report_path, ticker, date, quarter, period_end, statements, source_url)
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    explicit_url = None

    if "--url" in args:
        idx = args.index("--url")
        if idx + 1 >= len(args):
            sys.exit("--url requires a value")
        explicit_url = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if len(args) == 2:
        ticker, date = args[0].upper(), args[1]
        jobs = find_jobs(ticker, date)
        if not jobs:
            # Build a job from sources.json even if the file already exists
            sources = load_sources(ticker)
            entry = sources.get(date, {})
            quarter = entry.get("quarter", "")
            if not quarter:
                sys.exit(f"No entry found for {ticker} {date} in sources.json")
            report_path = REPO_ROOT / ticker / "quarters" / f"{date}_{quarter}-financials.md"
            jobs = [(ticker, date, quarter, report_path)]

    elif len(args) == 0:
        jobs = [j for j in find_jobs() if not j[3].exists()]
        if not jobs:
            print("All financial reports already downloaded.")
            return
        print(f"Found {len(jobs)} missing report(s)")

    else:
        print(__doc__)
        sys.exit(1)

    ok = sum(
        download(t, d, q, rp, explicit_url if len(jobs) == 1 else None)
        for t, d, q, rp in jobs
    )
    print(f"\nDone: {ok}/{len(jobs)} report(s) downloaded.")


if __name__ == "__main__":
    main()
