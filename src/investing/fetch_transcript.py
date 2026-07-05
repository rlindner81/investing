#!/usr/bin/env python3
"""
fetch_transcript.py — Download earnings call transcripts from the URLs in each
ticker's SOURCES.yml file.

SOURCES.yml format (e.g. BARK/SOURCES.yml):
  "2025-06-04":
    quarter: q4-2025
    transcript: https://stockanalysis.com/stocks/bark/transcripts/305774-q4-2025/

The date is the announcement date and is the root key. The script downloads any
transcript URL that does not yet have a corresponding file in quarters/.

Usage:
  # Download all missing transcripts across the whole repo
  uv run src/fetch_transcript.py

  # Download a specific quarter by ticker and announcement date
  uv run src/fetch_transcript.py BARK 2025-06-04

  # Supply a URL directly (bypasses SOURCES.yml lookup)
  uv run src/fetch_transcript.py BARK 2025-06-04 --url https://...

Output files: <TICKER>/quarters/<date>_<quarter>-transcript.md
"""

import re
import sys
import gzip
import time
import urllib.request
import yaml
from pathlib import Path

from investing.lib import REPO_ROOT, load_sources

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY = 1.5


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch(url: str) -> str | None:
    headers = dict(HEADERS)
    if "stockanalysis.com" in url:
        headers["Referer"] = "https://stockanalysis.com/"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            enc = resp.info().get("Content-Encoding", "")
        return gzip.decompress(raw).decode("utf-8", errors="replace") if enc == "gzip" else raw.decode("utf-8", errors="replace")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Extractors (keyed by URL domain)
# ---------------------------------------------------------------------------

def extract_stockanalysis(content: str) -> str | None:
    m = re.search(r'aria-label="Full transcript">(.*)', content, re.DOTALL)
    if not m:
        return None
    article = m.group(1)

    blocks = re.split(r'<div class="border-t border-sharp pt-5[^"]*">', article)
    paragraphs = []
    for block in blocks:
        name_m = re.search(r'<div[^>]*text-lg font-bold[^>]*>([^<]+)</div>', block)
        if not name_m:
            continue
        speaker = name_m.group(1).strip()
        sents = re.findall(r'<span[^>]*transcript-sentence[^>]*>([^<]+)</span>', block)
        if not sents:
            continue
        text = ' '.join(sents).replace('$', r'\$')
        paragraphs.append(f'**{speaker}:** {text}')

    result = '\n\n'.join(paragraphs)
    return result if len(result) > 2000 else None


def extract(url: str, content: str) -> str | None:
    if "stockanalysis.com" in url:
        return extract_stockanalysis(content)
    return None


# ---------------------------------------------------------------------------
# Repo discovery — driven by SOURCES.yml
# ---------------------------------------------------------------------------

def find_jobs(ticker_filter: str | None = None, date_filter: str | None = None) -> list[tuple]:
    """Return [(ticker, date, quarter, transcript_path)] for all missing transcripts."""
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
            if not entry.get("transcript"):
                continue
            transcript_path = quarters_dir / f"{date}_{quarter}-transcript.md"
            jobs.append((ticker, date, quarter, transcript_path))
    return jobs


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_transcript(path: Path, ticker: str, date: str, quarter: str, body: str, source_url: str):
    path.parent.mkdir(exist_ok=True)
    header = (
        f"# {ticker} {quarter.upper().replace('-', ' ')} Earnings Call Transcript\n\n"
        f"**Announcement date:** {date}  \n"
        f"**Source:** {source_url}\n\n"
        "---\n\n"
    )
    path.write_text(header + body + "\n")
    print(f"  Saved → {path.relative_to(REPO_ROOT)}")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download(ticker: str, date: str, quarter: str, transcript_path: Path, explicit_url: str | None = None) -> bool:
    print(f"\n{'='*60}")
    print(f"{ticker}  {quarter}  ({date})")
    print(f"{'='*60}")

    if explicit_url:
        url = explicit_url
    else:
        sources = load_sources(ticker)
        entry = sources.get(date, {})
        url = entry.get("transcript")
        if not url:
            print(f"  SKIP: no transcript URL for {date} in {ticker}/SOURCES.yml")
            return False

    print(f"  Fetching {url}")
    content = fetch(url)
    time.sleep(REQUEST_DELAY)
    if not content:
        print("  FAILED: could not fetch page")
        return False

    text = extract(url, content)
    if not text:
        print("  FAILED: could not extract transcript")
        return False

    save_transcript(transcript_path, ticker, date, quarter, text, url)
    return True


# ---------------------------------------------------------------------------
# Main
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
            sys.exit(f"No entry found for {ticker} {date} in SOURCES.yml")

    elif len(args) == 0:
        jobs = [j for j in find_jobs() if not j[3].exists()]
        if not jobs:
            print("All transcripts already downloaded.")
            return
        print(f"Found {len(jobs)} missing transcript(s)")

    else:
        print(__doc__)
        sys.exit(1)

    ok = sum(download(t, d, q, tp, explicit_url if len(jobs) == 1 else None) for t, d, q, tp in jobs)
    print(f"\nDone: {ok}/{len(jobs)} transcript(s) downloaded.")


if __name__ == "__main__":
    main()
