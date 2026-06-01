#!/usr/bin/env python3
"""
fetch_transcript.py — Download earnings call transcripts and save them alongside results files.

Sources tried in order:
  1. The Motley Fool  — free, full transcripts including Q&A; works for most S&P/Russell companies
  2. Insider Monkey   — free, full transcripts; used as fallback when Fool doesn't have it

Automatic URL discovery works for Motley Fool. For Insider Monkey, search cannot be automated
reliably (anti-bot measures). When a transcript isn't found automatically, find the URL manually
(search insidermonkey.com or similar) and pass it with --url.

Usage:
  # Download all missing transcripts across the whole repo
  python3 scripts/fetch_transcript.py

  # Download a specific quarter (auto-discover URL)
  python3 scripts/fetch_transcript.py BARK 2025-06-04

  # Supply the URL directly (use when auto-discovery fails)
  python3 scripts/fetch_transcript.py BARK 2026-02-05 --url https://www.insidermonkey.com/blog/bark-inc-nysebark-q3-2026-earnings-call-transcript-1690270/

Output files: <TICKER>/quarters/<date>_<quarter>-transcript.md
"""

import re
import sys
import gzip
import time
import urllib.request
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
}

# Seconds to wait between requests to be polite
REQUEST_DELAY = 1.5


# ---------------------------------------------------------------------------
# HTTP helpers
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
# Source: The Motley Fool
# ---------------------------------------------------------------------------

def _motleyfool_url(ticker: str, date: str, quarter: str) -> list[str]:
    """Generate candidate Motley Fool URLs for a given quarter."""
    t = ticker.lower()
    date_path = date.replace("-", "/")
    variants = [quarter]
    # q4-fy2025 → q4-2025
    if "fy" in quarter:
        variants.append(quarter.replace("fy", "").replace("--", "-"))
    # q3-2026 → q3-fy2026 (less common but try it)
    else:
        year = re.search(r"(\d{4})", quarter)
        if year:
            variants.append(quarter.replace(year.group(1), f"fy{year.group(1)}"))
    seen = []
    for v in variants:
        url = f"https://www.fool.com/earnings/call-transcripts/{date_path}/{t}-{t}-{v}-earnings-call-transcript/"
        if url not in seen:
            seen.append(url)
    return seen


def fetch_motleyfool(ticker: str, date: str, quarter: str) -> str | None:
    for url in _motleyfool_url(ticker, date, quarter):
        content = fetch(url)
        time.sleep(REQUEST_DELAY)
        if not content or "article-body-transcript" not in content:
            continue
        idx = content.find("article-body-transcript")
        chunk = content[idx:]
        for end_pat in ["<div class=\"mx-auto", "<footer", '<div id="related']:
            i = chunk.find(end_pat, 1000)
            if 0 < i < len(chunk):
                chunk = chunk[:i]
                break
        text = clean_html(chunk)
        if len(text) > 2000:
            return text, url
    return None


# ---------------------------------------------------------------------------
# Source: Insider Monkey
# ---------------------------------------------------------------------------

def _insidermonkey_search_url(ticker: str, quarter: str) -> str | None:
    """Search for the Insider Monkey transcript URL.

    Search engines block automated scraping, so discovery is best-effort.
    When this returns None, pass --url manually on the command line.
    """
    # Insider Monkey's own search
    q = urllib.parse.quote(f"{ticker} {quarter} earnings call transcript")
    for search_url in [
        f"https://www.insidermonkey.com/?s={q}",
    ]:
        content = fetch(search_url)
        time.sleep(REQUEST_DELAY)
        if not content:
            continue
        urls = re.findall(
            r"https://www\.insidermonkey\.com/blog/[a-z0-9-]+-transcript-\d+/",
            content,
        )
        if urls:
            return urls[0]
    return None


def fetch_insidermonkey(ticker: str, date: str, quarter: str) -> str | None:
    url = _insidermonkey_search_url(ticker, quarter)
    if not url:
        return None
    time.sleep(REQUEST_DELAY)
    content = fetch(url)
    if not content:
        return None
    art = re.search(r"<article[^>]*>(.*?)</article>", content, re.DOTALL)
    if not art:
        return None
    text = clean_html(art.group(1))
    # Trim trailing noise (hedge fund ads, related posts sidebar)
    for noise in ["Page 1 of", "John Paulson", "David Tepper", "Paul Tudor"]:
        idx = text.find(noise)
        if idx > 0:
            text = text[:idx]
    text = text.strip()
    return (text, url) if len(text) > 2000 else None


# ---------------------------------------------------------------------------
# Repo discovery
# ---------------------------------------------------------------------------

def find_quarters() -> list[tuple]:
    """Return [(ticker, date, quarter_slug, results_path, transcript_path)]."""
    entries = []
    for results_path in sorted(REPO_ROOT.glob("*/quarters/*-results.md")):
        stem = results_path.stem  # e.g. 2025-06-04_q4-fy2025-results
        parts = stem.split("_", 1)
        if len(parts) != 2:
            continue
        date = parts[0]
        quarter = parts[1].replace("-results", "")
        ticker = results_path.parent.parent.name
        transcript_path = results_path.parent / f"{date}_{quarter}-transcript.md"
        entries.append((ticker, date, quarter, results_path, transcript_path))
    return entries


# ---------------------------------------------------------------------------
# Transcript cleanup and save
# ---------------------------------------------------------------------------

def trim_transcript(text: str) -> str:
    """Remove navigation/site chrome, keep only the transcript body."""
    lines = text.split("\n")

    # Find where actual transcript starts (first speaker line)
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^(Operator|[A-Z][a-z]+ [A-Z][a-z]+):", stripped):
            start = max(0, i - 1)
            break

    lines = lines[start:]

    # Trim trailing noise
    noise_patterns = [
        r"^Read Next", r"^Related Articles", r"^\d{4}-\d{2}-\d{2} .By Motley Fool",
        r"Follow .* \(NASDAQ:", r"^\s*\$[\d,]+\s*$",
    ]
    end = len(lines)
    for i, line in enumerate(lines):
        if any(re.search(p, line) for p in noise_patterns):
            end = i
            break

    return "\n".join(lines[:end]).strip()


def save_transcript(path: Path, ticker: str, date: str, quarter: str, body: str, source_url: str):
    header = (
        f"# {ticker} {quarter.upper().replace('-', ' ')} Earnings Call Transcript\n\n"
        f"**Announcement date:** {date}  \n"
        f"**Source:** {source_url}\n\n"
        "---\n\n"
    )
    path.write_text(header + body + "\n")
    print(f"  Saved → {path.relative_to(REPO_ROOT)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SOURCES = [
    ("Motley Fool", fetch_motleyfool),
    ("Insider Monkey", fetch_insidermonkey),
]


def download(ticker: str, date: str, quarter: str, transcript_path: Path) -> bool:
    print(f"\n{'='*60}")
    print(f"{ticker}  {quarter}  ({date})")
    print(f"{'='*60}")
    for name, fn in SOURCES:
        print(f"  Trying {name}...")
        result = fn(ticker, date, quarter)
        if result:
            body, url = result
            body = trim_transcript(body)
            save_transcript(transcript_path, ticker, date, quarter, body, url)
            return True
    print("  FAILED: no transcript found")
    return False


def fetch_from_url(url: str) -> tuple[str, str] | None:
    """Fetch a transcript from an explicit URL, trying each extractor in turn."""
    content = fetch(url)
    if not content:
        return None
    # Motley Fool
    idx = content.find("article-body-transcript")
    if idx >= 0:
        chunk = content[idx:]
        for end_pat in ['<div class="mx-auto', "<footer", '<div id="related']:
            i = chunk.find(end_pat, 1000)
            if 0 < i < len(chunk):
                chunk = chunk[:i]
                break
        text = clean_html(chunk)
        if len(text) > 2000:
            return text, url
    # Insider Monkey / generic article
    art = re.search(r"<article[^>]*>(.*?)</article>", content, re.DOTALL)
    if art:
        text = clean_html(art.group(1))
        for noise in ["Page 1 of", "John Paulson", "David Tepper", "Paul Tudor"]:
            cut = text.find(noise)
            if cut > 0:
                text = text[:cut]
        text = text.strip()
        if len(text) > 2000:
            return text, url
    return None


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
        quarters = find_quarters()
        entry = next((q for q in quarters if q[0] == ticker and q[1] == date), None)
        if not entry:
            sys.exit(f"No results file found for {ticker} {date}")
        jobs = [(entry[0], entry[1], entry[2], entry[4])]

    elif len(args) == 0:
        jobs = [
            (t, d, q, tp)
            for t, d, q, _, tp in find_quarters()
            if not tp.exists()
        ]
        if not jobs:
            print("All transcripts already downloaded.")
            return
        print(f"Found {len(jobs)} missing transcript(s)")

    else:
        print(__doc__)
        sys.exit(1)

    if explicit_url:
        if len(jobs) != 1:
            sys.exit("--url requires a specific TICKER DATE to be given")
        ticker, date, quarter, transcript_path = jobs[0]
        print(f"\n{'='*60}")
        print(f"{ticker}  {quarter}  ({date})")
        print(f"{'='*60}")
        print(f"  Fetching from provided URL...")
        result = fetch_from_url(explicit_url)
        if result:
            body, url = result
            body = trim_transcript(body)
            save_transcript(transcript_path, ticker, date, quarter, body, url)
            print("\nDone: 1/1 transcript(s) downloaded.")
        else:
            sys.exit(f"  FAILED: could not extract transcript from {explicit_url}")
        return

    ok = sum(download(*j) for j in jobs)
    print(f"\nDone: {ok}/{len(jobs)} transcript(s) downloaded.")


if __name__ == "__main__":
    main()
