#!/usr/bin/env python3
"""
fetch_transcript.py — Download earnings call transcripts from the URLs in each
ticker's sources.json file.

sources.json format (e.g. BARK/sources.json):
  {
    "2025-06-04": {
      "quarter": "q4-fy2025",
      "transcript": "https://www.insidermonkey.com/blog/..."
    },
    ...
  }

The date is the announcement date and is the root key. The script downloads any
transcript URL that does not yet have a corresponding file in quarters/.

Usage:
  # Download all missing transcripts across the whole repo
  python3 scripts/fetch_transcript.py

  # Download a specific quarter by ticker and announcement date
  python3 scripts/fetch_transcript.py BARK 2025-06-04

  # Supply a URL directly (bypasses sources.json lookup)
  python3 scripts/fetch_transcript.py BARK 2025-06-04 --url https://...

Output files: <TICKER>/quarters/<date>_<quarter>-transcript.md
"""

import re
import sys
import gzip
import json
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY = 1.5


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            enc = resp.info().get("Content-Encoding", "")
        return gzip.decompress(raw).decode("utf-8", errors="replace") if enc == "gzip" else raw.decode("utf-8", errors="replace")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# HTML cleaning
# ---------------------------------------------------------------------------

ENTITY_MAP = [
    ("&#x2019;", "'"), ("&#x2018;", "'"), ("&#8217;", "'"), ("&#x27;", "'"),
    ("&#x201C;", '"'), ("&#x201D;", '"'), ("&#8220;", '"'), ("&#8221;", '"'),
    ("&#x2014;", "—"), ("&#x2013;", "–"), ("&#x2026;", "…"),
    ("&amp;", "&"), ("&nbsp;", " "), ("&quot;", '"'), ("&lt;", "<"), ("&gt;", ">"),
]


def clean_html(html: str) -> str:
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
    html = re.sub(r"</p>", "\n\n", html)
    html = re.sub(r"<br\s*/?>", "\n", html)
    html = re.sub(r"<strong[^>]*>(.*?)</strong>", r"\1", html)
    html = re.sub(r"<[^>]+>", "", html)
    for old, new in ENTITY_MAP:
        html = html.replace(old, new)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


# ---------------------------------------------------------------------------
# Extractors (keyed by URL domain)
# ---------------------------------------------------------------------------

def extract_insidermonkey(content: str) -> str | None:
    art = re.search(r"<article[^>]*>(.*?)</article>", content, re.DOTALL)
    if not art:
        return None
    text = clean_html(art.group(1))
    for noise in ["Page 1 of", "John Paulson", "David Tepper", "Paul Tudor"]:
        cut = text.find(noise)
        if cut > 0:
            text = text[:cut]
    text = text.strip()
    return text if len(text) > 2000 else None


def extract(url: str, content: str) -> str | None:
    if "insidermonkey.com" in url:
        return extract_insidermonkey(content)
    return None


# ---------------------------------------------------------------------------
# sources.json
# ---------------------------------------------------------------------------

def load_sources(ticker: str) -> dict:
    path = REPO_ROOT / ticker / "sources.json"
    if path.exists():
        with path.open() as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------------------
# Repo discovery — driven by sources.json
# ---------------------------------------------------------------------------

def find_jobs(ticker_filter: str | None = None, date_filter: str | None = None) -> list[tuple]:
    """Return [(ticker, date, quarter, transcript_path)] for all missing transcripts."""
    jobs = []
    pattern = f"{ticker_filter}/sources.json" if ticker_filter else "*/sources.json"
    for sources_path in sorted(REPO_ROOT.glob(pattern)):
        ticker = sources_path.parent.name
        quarters_dir = sources_path.parent / "quarters"
        with sources_path.open() as f:
            sources = json.load(f)
        for date, entry in sources.items():
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

def trim_transcript(text: str) -> str:
    lines = text.split("\n")
    start = 0
    for i, line in enumerate(lines):
        if re.match(r"^(Operator|[A-Z][a-z]+ [A-Z][a-z]+):", line.strip()):
            start = max(0, i - 1)
            break
    lines = lines[start:]
    noise_patterns = [
        r"^Related Insider Monkey Articles", r"Subscribe with Google",
        r"Insider Monkey Quarterly Strategy", r"Hedge Fund Resource Center",
        r"or\s+Subscribe with", r"We may use your email",
    ]
    end = len(lines)
    for i, line in enumerate(lines):
        if any(re.search(p, line) for p in noise_patterns):
            end = i
            break
    return "\n".join(lines[:end]).strip()


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
            print(f"  SKIP: no transcript URL for {date} in {ticker}/sources.json")
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

    text = trim_transcript(text)
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
            sys.exit(f"No entry found for {ticker} {date} in sources.json")

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
