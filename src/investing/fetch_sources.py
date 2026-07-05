#!/usr/bin/env python3
"""
fetch_sources.py — Download per-quarter source files from SEC EDGAR.

Reads each ticker's SOURCES.yml and downloads whatever files are missing:

  report  → YYYY-MM-DD_<quarter>-report.md   (income / balance / cash-flow)
  letter  → YYYY-MM-DD_<quarter>-letter.md   (shareholder / earnings letter)

Both keys are optional; only the ones present in SOURCES.yml are fetched.
Files that already exist on disk are always skipped.

SOURCES.yml format:
  _meta:
    cik: "0001819574"
  "2025-08-07":
    quarter: q1-2026
    period_end: "2025-06-30"
    report: https://www.sec.gov/Archives/edgar/data/.../
    report_type: 10-Q
    letter: https://www.sec.gov/Archives/edgar/data/.../dXXXXex991.htm
    letter_type: 8-K
    transcript: https://stockanalysis.com/...

Usage:
  # Download all missing reports + letters across the whole repo
  uv run fetch-sources

  # One ticker only
  uv run fetch-sources BARK

  # One quarter
  uv run fetch-sources BARK 2025-08-07

  # Supply a report URL directly (bypasses auto-discovery)
  uv run fetch-sources BARK 2025-08-07 --url https://www.sec.gov/Archives/edgar/data/.../

  # Only one file type
  uv run fetch-sources --report-only
  uv run fetch-sources --letter-only
"""

import re
import sys
import gzip
import json
import time
import urllib.request
import xml.etree.ElementTree as ET
import yaml
from datetime import datetime, timedelta
from pathlib import Path

from investing.lib import REPO_ROOT, load_sources

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

FORM_PRIORITY = {"10-Q": 0, "10-K": 1, "20-F": 2, "6-K": 3}


def find_filing(cik: str, announcement_date: str) -> tuple[str, str, str] | None:
    url = f"{EDGAR_DATA}/submissions/CIK{cik}.json"
    time.sleep(REQUEST_DELAY)
    data = fetch_json(url)
    if not data:
        return None

    ann_dt = datetime.strptime(announcement_date, "%Y-%m-%d")

    def search_filings(filings_block: dict) -> tuple[str, str, str] | None:
        forms = filings_block.get("form", [])
        accessions = filings_block.get("accessionNumber", [])
        filing_dates = filings_block.get("filingDate", [])
        report_dates = filings_block.get("reportDate", [])
        best, best_diff, best_priority = None, timedelta(days=30), 99
        for form, accn, filed, period in zip(forms, accessions, filing_dates, report_dates):
            if form not in FORM_PRIORITY:
                continue
            try:
                filed_dt = datetime.strptime(filed, "%Y-%m-%d")
            except ValueError:
                continue
            diff = abs(filed_dt - ann_dt)
            priority = FORM_PRIORITY[form]
            if diff < best_diff or (diff == best_diff and priority < best_priority):
                best_diff = diff
                best_priority = priority
                best = (accn, period, form)
        return best

    result = search_filings(data.get("filings", {}).get("recent", {}))
    if not result:
        for older in data.get("filings", {}).get("files", []):
            time.sleep(REQUEST_DELAY)
            older_data = fetch_json(f"{EDGAR_DATA}/submissions/{older['name']}")
            if older_data:
                result = search_filings(older_data)
                if result:
                    break

    if result:
        print(f"  Found filing: {result[0]}  form: {result[2]}  period: {result[1]}")
    else:
        print(f"  No filing found within 30 days of {announcement_date}")
    return result


# ---------------------------------------------------------------------------
# XBRL path: FilingSummary.xml → R-files
# ---------------------------------------------------------------------------

STATEMENT_KEYWORDS = {
    "income":   ["statements of operations", "statements of income", "earnings"],
    "balance":  ["balance sheet", "financial position"],
    "cashflow": ["cash flow"],
}

SKIP_KEYWORDS = ["parenthetical"]


def get_statement_files(cik: str, accession: str) -> list[tuple[str, str, str]]:
    accn_nodash = accession.replace("-", "")
    cik_int = int(cik)
    url = f"{EDGAR_ARCHIVE}/{cik_int}/{accn_nodash}/FilingSummary.xml"
    print(f"  Fetching filing index (XBRL)...")
    time.sleep(REQUEST_DELAY)
    xml_text = fetch_text(url)
    if not xml_text:
        return []

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


def _cell_text(cell_html: str) -> str:
    cell_html = re.sub(r"<span[^>]*></span>", "", cell_html)
    text = re.sub(r"<[^>]+>", "", cell_html)
    text = (text
            .replace("&#160;", "").replace("&nbsp;", "")
            .replace("&#8212;", "—").replace("&#8211;", "–")
            .replace("&#8203;", "").replace("&#8217;", "'")
            .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&middot;", "·"))
    return " ".join(text.split())


def r_file_to_markdown(html: str) -> str:
    table_m = re.search(r'<table[^>]*class="report"[^>]*>(.*?)</table>', html, re.DOTALL | re.IGNORECASE)
    if table_m:
        html = table_m.group(1)

    rows_md: list[list[str]] = []
    for tr_m in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE):
        cells: list[str] = []
        for cell_m in re.finditer(r"<t([dh])([^>]*)>(.*?)</t\1>", tr_m.group(1), re.DOTALL | re.IGNORECASE):
            attrs, inner = cell_m.group(2), cell_m.group(3)
            text = _cell_text(inner)
            colspan = int(m.group(1)) if (m := re.search(r'colspan="(\d+)"', attrs)) else 1
            cells.extend([text] * colspan)
        if any(c.strip() for c in cells):
            rows_md.append(cells)

    return _rows_to_markdown(rows_md)


def fetch_r_statement(cik: str, accession: str, r_filename: str) -> str | None:
    accn_nodash = accession.replace("-", "")
    url = f"{EDGAR_ARCHIVE}/{int(cik)}/{accn_nodash}/{r_filename}"
    time.sleep(REQUEST_DELAY)
    html = fetch_text(url)
    if not html:
        print(f"  Could not fetch {r_filename}")
        return None
    md = r_file_to_markdown(html)
    return md if md else None


# ---------------------------------------------------------------------------
# 6-K path: press release exhibit HTML
# ---------------------------------------------------------------------------

PRESS_KEYWORDS = {
    "income":   ["statements of income", "statement of income", "statements of operations", "statement of operations", "statements of earnings", "statement of earnings"],
    "balance":  ["balance sheet", "financial position"],
    "cashflow": ["statements of cash flow", "statement of cash flow", "cash flows from"],
}


def find_6k_exhibit(cik: str, accession: str) -> str | None:
    accn_nodash = accession.replace("-", "")
    cik_int = int(cik)
    index_url = f"{EDGAR_ARCHIVE}/{cik_int}/{accn_nodash}/"
    time.sleep(REQUEST_DELAY)
    html = fetch_text(index_url)
    if not html:
        return None
    m = re.search(
        r'href="(/Archives/edgar/data/\d+/\d+/[^"]*(?:ex[-_]?99|exhibit[-_]?99)[^"]*\.htm[^"]*)"',
        html, re.IGNORECASE,
    )
    if m:
        return "https://www.sec.gov" + m.group(1)
    return None


def table_html_to_markdown(table_html: str) -> str:
    rows_md: list[list[str]] = []
    for tr_m in re.finditer(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL | re.IGNORECASE):
        cells: list[str] = []
        for cell_m in re.finditer(r"<t([dh])([^>]*)>(.*?)</t\1>", tr_m.group(1), re.DOTALL | re.IGNORECASE):
            attrs, inner = cell_m.group(2), cell_m.group(3)
            text = _cell_text(inner)
            colspan = int(m.group(1)) if (m := re.search(r'colspan="(\d+)"', attrs)) else 1
            cells.extend([text] * colspan)
        if any(c.strip() for c in cells):
            rows_md.append(cells)
    return _rows_to_markdown(rows_md)


def press_release_to_statements(html: str) -> dict[str, str]:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)

    table_spans = list(re.finditer(r"<table[^>]*>.*?</table>", html, re.DOTALL | re.IGNORECASE))

    results: dict[str, str] = {}
    prev_end = 0
    last_type: str | None = None

    for tm in table_spans:
        between = html[prev_end:tm.start()]
        between_text = re.sub(r"<[^>]+>", " ", between)
        between_text = re.sub(r"\s+", " ", between_text).lower()[-500:]

        prev_end = tm.end()

        matched = None
        for stmt_type, keywords in PRESS_KEYWORDS.items():
            if any(k in between_text for k in keywords):
                matched = stmt_type
                break

        if not matched:
            last_type = None
            continue

        md = table_html_to_markdown(tm.group(0))
        if not md:
            continue

        if matched == last_type and matched in results:
            results[matched] += "\n\n" + md
        else:
            results[matched] = md

        last_type = matched

    return results


# ---------------------------------------------------------------------------
# Letter path: 8-K exhibit HTML → markdown
# ---------------------------------------------------------------------------

def _html_entities(text: str) -> str:
    return (text
            .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&nbsp;", " ").replace("&#160;", " ")
            .replace("&#8212;", "—").replace("&#8211;", "–")
            .replace("&#8217;", "'").replace("&#8216;", "'")
            .replace("&#8220;", '"').replace("&#8221;", '"')
            .replace("&#8203;", "").replace("&#38;", "&"))


def exhibit_to_markdown(html: str) -> str:
    """Convert an SEC 8-K exhibit (shareholder letter HTML) to clean markdown.

    Preserves headings, paragraphs, bullet lists, and financial tables.
    Strips page-layout cruft (font/color/style attributes, empty spans, etc.).
    """
    # Drop scripts, styles, and inline CSS blocks
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)

    blocks: list[str] = []

    # Walk the document by splitting on block-level tags.
    # We process tables specially; everything else is text.
    cursor = 0
    for table_m in re.finditer(r"<table[^>]*>.*?</table>", html, re.DOTALL | re.IGNORECASE):
        # Process text between last position and this table
        segment = html[cursor:table_m.start()]
        blocks.extend(_segment_to_blocks(segment))
        # Convert table
        md_table = table_html_to_markdown(table_m.group(0))
        if md_table:
            blocks.append(md_table)
        cursor = table_m.end()

    # Remaining text after last table
    blocks.extend(_segment_to_blocks(html[cursor:]))

    # Collapse and return
    result = "\n\n".join(b for b in blocks if b.strip())
    return result


def _segment_to_blocks(html: str) -> list[str]:
    """Convert a non-table HTML segment into a list of markdown block strings."""
    # Replace block-level tags with newline sentinels
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</(p|div|li|tr|h[1-6])>", "\n", html, flags=re.IGNORECASE)

    # Map heading tags to markdown prefixes
    def heading(m: re.Match) -> str:
        level = int(m.group(1))
        prefix = "#" * min(level + 1, 4)  # h1→##, h2→###, h3→####
        inner = re.sub(r"<[^>]+>", "", m.group(2))
        return f"\n{prefix} {inner.strip()}\n"

    html = re.sub(r"<h([1-6])[^>]*>(.*?)</h\1>", heading, html, flags=re.DOTALL | re.IGNORECASE)

    # Strip all remaining tags
    text = re.sub(r"<[^>]+>", "", html)
    text = _html_entities(text)

    blocks: list[str] = []
    for line in text.split("\n"):
        line = " ".join(line.split())  # normalise whitespace
        if line:
            blocks.append(line)

    return blocks


# ---------------------------------------------------------------------------
# Shared markdown helper
# ---------------------------------------------------------------------------

def _rows_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    max_cols = max(len(r) for r in rows)
    rows = [r + [""] * (max_cols - len(r)) for r in rows]
    lines = []
    for i, row in enumerate(rows):
        lines.append("| " + " | ".join(row) + " |")
        if i == 0:
            lines.append("|" + "|".join(["---"] * max_cols) + "|")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SOURCES.yml helpers
# ---------------------------------------------------------------------------

def save_sources(ticker: str, sources: dict):
    path = REPO_ROOT / ticker / "SOURCES.yml"
    with path.open("w") as f:
        yaml.dump(sources, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def get_or_resolve_cik(ticker: str, sources: dict) -> str | None:
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

def find_jobs(
    ticker_filter: str | None = None,
    date_filter: str | None = None,
    want_reports: bool = True,
    want_letters: bool = True,
) -> list[tuple]:
    """Return [(ticker, date, quarter, path, kind)] for all quarters in SOURCES.yml.

    kind is 'report' or 'letter'.
    """
    jobs = []
    pattern = f"{ticker_filter}/SOURCES.yml" if ticker_filter else "*/SOURCES.yml"
    for sources_path in sorted(REPO_ROOT.glob(pattern)):
        ticker = sources_path.parent.name
        quarters_dir = sources_path.parent / "quarters"
        with sources_path.open() as f:
            sources = yaml.safe_load(f) or {}
        for date, entry in sources.items():
            if not isinstance(date, str) or date.startswith("_") or not isinstance(entry, dict):
                continue
            if date_filter and date != date_filter:
                continue
            quarter = entry.get("quarter", "")
            if not quarter:
                continue
            if want_reports and entry.get("report"):
                jobs.append((ticker, date, quarter,
                             quarters_dir / f"{date}_{quarter}-report.md", "report"))
            if want_letters and entry.get("letter"):
                jobs.append((ticker, date, quarter,
                             quarters_dir / f"{date}_{quarter}-letter.md", "letter"))
    return jobs


# ---------------------------------------------------------------------------
# Save helpers
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


def save_letter(path: Path, ticker: str, date: str, quarter: str,
                body: str, source_url: str):
    path.parent.mkdir(exist_ok=True)
    header = (
        f"# {ticker} — {quarter.upper().replace('-', ' ')} Shareholder Letter\n\n"
        f"**Announcement date:** {date}  \n"
        f"**Source:** {source_url}\n\n"
        "---\n\n"
    )
    path.write_text(header + body + "\n")
    print(f"  Saved → {path.relative_to(REPO_ROOT)}")


# ---------------------------------------------------------------------------
# Download: report
# ---------------------------------------------------------------------------

def download_report(ticker: str, date: str, quarter: str, report_path: Path,
                    explicit_url: str | None = None) -> bool:
    sources = load_sources(ticker)

    if explicit_url:
        m = re.search(r"/edgar/data/(\d+)/(\d+)/", explicit_url)
        if not m:
            print("  FAILED: URL must contain /edgar/data/<cik>/<accession>/")
            return False
        cik = m.group(1).zfill(10)
        accn_nodash = m.group(2)
        accession = f"{accn_nodash[:10]}-{accn_nodash[10:12]}-{accn_nodash[12:]}"
        period_end = sources.get(date, {}).get("period_end", "")
        form_type = None
        source_url = explicit_url
    else:
        cik = get_or_resolve_cik(ticker, sources)
        if not cik:
            print(f"  FAILED: could not resolve CIK for {ticker}")
            return False

        filing = find_filing(cik, date)
        if not filing:
            return False
        accession, period_end, form_type = filing
        accn_nodash = accession.replace("-", "")
        source_url = f"{EDGAR_ARCHIVE}/{int(cik)}/{accn_nodash}/"

        old = sources.get(date, {})
        if form_type == "6-K" and old.get("period_end"):
            period_end = old["period_end"]
        sources[date] = {
            "quarter":    old.get("quarter", ""),
            "period_end": period_end,
            "report":     source_url,
            **{k: v for k, v in old.items() if k not in ("quarter", "period_end", "report")},
        }
        save_sources(ticker, sources)

    statements: dict[str, str] = {}

    if form_type == "6-K":
        print(f"  Locating press release exhibit...")
        exhibit_url = find_6k_exhibit(cik, accession)
        if not exhibit_url:
            print("  FAILED: no ex99 exhibit found in 6-K")
            return False
        print(f"  Fetching exhibit: {exhibit_url}")
        time.sleep(REQUEST_DELAY)
        html = fetch_text(exhibit_url)
        if not html:
            print("  FAILED: could not fetch exhibit")
            return False
        statements = press_release_to_statements(html)
        if not statements:
            print("  FAILED: no financial tables found in press release")
            return False
        print(f"  Extracted: {list(statements)}")
    else:
        stmt_files = get_statement_files(cik, accession)
        if not stmt_files:
            print("  FAILED: no financial statement files found")
            return False
        print(f"  Statements: {[(t, f) for t, _, f in stmt_files]}")
        for stmt_type, short_name, r_filename in stmt_files:
            print(f"  Fetching {stmt_type} ({r_filename})...")
            md = fetch_r_statement(cik, accession, r_filename)
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
# Download: letter
# ---------------------------------------------------------------------------

def download_letter(ticker: str, date: str, quarter: str, letter_path: Path) -> bool:
    sources = load_sources(ticker)
    url = sources.get(date, {}).get("letter")
    if not url:
        print(f"  SKIP: no letter URL for {date} in {ticker}/SOURCES.yml")
        return False

    print(f"  Fetching letter: {url}")
    time.sleep(REQUEST_DELAY)
    html = fetch_text(url)
    if not html:
        print("  FAILED: could not fetch letter")
        return False

    # Keep original HTML alongside the markdown for comparison
    htm_path = letter_path.with_suffix(".htm")
    htm_path.parent.mkdir(exist_ok=True)
    htm_path.write_text(html)
    print(f"  Saved → {htm_path.relative_to(REPO_ROOT)}")

    body = exhibit_to_markdown(html)
    if not body or len(body) < 500:
        print("  FAILED: extracted letter is too short to be valid")
        return False

    # Drop SEC filing header boilerplate (index rows, EX-99.1 labels, page
    # numbers, logo placeholders) that precede the actual letter content.
    # The first substantive paragraph matches a quarter/year pattern.
    body = re.sub(r"^.*?(?=Q\d\s+20\d\d\b)", "", body, flags=re.DOTALL, count=1) or body
    # Fallback: strip leading lines that are short metadata / logo artifacts
    lines = body.splitlines()
    while lines and (len(lines[0].replace(" ", "")) < 20 or "&#167;" in lines[0]):
        lines.pop(0)
    body = "\n\n".join(lines).strip()

    save_letter(letter_path, ticker, date, quarter, body, url)
    return True


# ---------------------------------------------------------------------------
# Unified download dispatcher
# ---------------------------------------------------------------------------

def download(ticker: str, date: str, quarter: str, path: Path, kind: str,
             explicit_url: str | None = None) -> bool:
    print(f"\n{'='*60}")
    print(f"{ticker}  {quarter}  ({date})  [{kind}]")
    print(f"{'='*60}")
    if kind == "report":
        return download_report(ticker, date, quarter, path, explicit_url)
    elif kind == "letter":
        return download_letter(ticker, date, quarter, path)
    return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    explicit_url = None
    want_reports, want_letters = True, True

    flags = {"--report-only", "--letter-only", "--url"}
    i = 0
    rest = []
    while i < len(args):
        a = args[i]
        if a == "--url":
            explicit_url = args[i + 1]; i += 2
        elif a == "--report-only":
            want_letters = False; i += 1
        elif a == "--letter-only":
            want_reports = False; i += 1
        else:
            rest.append(a); i += 1

    ticker_filter = rest[0].upper() if len(rest) >= 1 else None
    date_filter   = rest[1]         if len(rest) >= 2 else None

    if len(rest) > 2:
        print(__doc__)
        sys.exit(1)

    if date_filter:
        # Single quarter: build jobs directly (entry may not have report URL yet)
        jobs = find_jobs(ticker_filter, date_filter, want_reports, want_letters)
        if not jobs and want_reports:
            sources = load_sources(ticker_filter)
            entry = sources.get(date_filter, {})
            quarter = entry.get("quarter", "")
            if not quarter:
                sys.exit(f"No entry found for {ticker_filter} {date_filter} in SOURCES.yml")
            report_path = REPO_ROOT / ticker_filter / "quarters" / f"{date_filter}_{quarter}-report.md"
            jobs = [(ticker_filter, date_filter, quarter, report_path, "report")]
    else:
        jobs = [j for j in find_jobs(ticker_filter, None, want_reports, want_letters)
                if not j[3].exists()]
        if not jobs:
            print("All source files already downloaded.")
            return
        label = "report" if not want_letters else ("letter" if not want_reports else "source file")
        print(f"Found {len(jobs)} missing {label}(s)")

    ok = sum(
        download(t, d, q, p, k, explicit_url if len(jobs) == 1 and k == "report" else None)
        for t, d, q, p, k in jobs
    )
    print(f"\nDone: {ok}/{len(jobs)} file(s) downloaded.")


if __name__ == "__main__":
    main()
