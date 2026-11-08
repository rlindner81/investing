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

The `transcript` URL must be the SPECIFIC per-quarter page
(.../transcripts/<id>-<quarter>/), not the bare index (.../transcripts/) — the
extractor parses a single transcript page and fails on an index.

Foreign filers list under their home exchange, not the US ticker/ADR: the
`stocks/<us-ticker>/` path 404s and transcripts live under
`quote/<exchange>/<home-ticker>/transcripts/<id>-<quarter>/` instead (e.g. KGC →
`quote/tsx/K/`, GTBIF → `quote/cse/GTII/`). Record the home listing in the
SOURCES `_meta` block via `home_exchange` / `home_ticker`.

Usage:
  # Download all missing transcripts for one ticker (required)
  uv run fetch-transcript BARK

  # Download a specific quarter by ticker and announcement date
  uv run fetch-transcript BARK 2025-06-04

  # Supply a URL directly (bypasses SOURCES.yml lookup)
  uv run fetch-transcript BARK 2025-06-04 --url https://...

Output files: <TICKER>/quarters/<date>_<quarter>-transcript.md

Preliminary transcripts: fresh calls are often published before speakers are
attributed. Such a page is saved as <date>_<quarter>-preliminary-transcript.md
(title says "Preliminary"; unnamed paragraphs labeled **Unattributed:**). It does not count as
downloaded: every later run refetches it, and once the source is attributed the
final -transcript.md is written and the preliminary file deleted. A preliminary
never overwrites an existing final file. Generic "Speaker 7" labels are a
permanent source gap, not a preliminary marker.
"""

import re
import sys
import gzip
import time
import urllib.error
import urllib.request
import yaml
from pathlib import Path

from investing.lib import REPO_ROOT, load_sources, get_logger, setup_logging

log = get_logger(__name__)

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
    except urllib.error.HTTPError as e:
        # Server answered with a status — 403/404 are routine here (bot walls,
        # a URL pointing at an index page rather than a transcript).
        log.warning("%s: HTTP %s %s", url, e.code, e.reason)
    except (urllib.error.URLError, TimeoutError) as e:
        # DNS/connection/timeout — offline or the host is refusing us.
        log.warning("%s: request failed (%s)", url, getattr(e, "reason", e))
    except Exception:
        log.error("%s: unexpected error fetching page", url, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# Extractors (keyed by URL domain)
# ---------------------------------------------------------------------------

SENTENCE_RE = re.compile(r'<span[^>]*transcript-sentence[^>]*>([^<]+)</span>')
UNATTRIBUTED = "Unattributed"  # label for paragraphs the source hasn't assigned a speaker yet
PARA_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL)


def _paragraph_texts(html: str) -> list[str]:
    """Sentence text per <p> in `html` (falls back to one blob if no <p>)."""
    paras = [SENTENCE_RE.findall(p) for p in PARA_RE.findall(html)] or [SENTENCE_RE.findall(html)]
    return [' '.join(s).replace('$', r'\$') for s in paras if s]


def extract_stockanalysis(content: str) -> tuple[str, bool] | None:
    """Return (markdown, preliminary) or None.

    Fresh transcripts are often published before stockanalysis.com attributes
    speakers: the blocks carry no name div. Those paragraphs are kept
    under an `**Unattributed:**` label and the result is flagged preliminary, so the caller files it under the
    `-preliminary-transcript.md` name and refetches it later. A page with every
    block named (even generic "Speaker 7" labels — those are permanent source
    gaps) is final.
    """
    m = re.search(r'aria-label="Full transcript">(.*)', content, re.DOTALL)
    if not m:
        return None
    article = m.group(1)

    blocks = re.split(r'<div class="border-t border-sharp pt-5[^"]*">', article)[1:]
    if not blocks:  # no speaker-block layout at all — treat the article as one unnamed block
        blocks = [article]
    paragraphs = []
    unnamed = 0
    for block in blocks:
        name_m = re.search(r'<div[^>]*text-lg font-bold[^>]*>([^<]+)</div>', block)
        if name_m:
            texts = _paragraph_texts(block)
            if texts:
                paragraphs.append(f'**{name_m.group(1).strip()}:** ' + ' '.join(texts))
        else:
            texts = _paragraph_texts(block)
            unnamed += len(texts)
            paragraphs.extend(f'**{UNATTRIBUTED}:** {t}' for t in texts)

    result = '\n\n'.join(paragraphs)
    if len(result) <= 2000:
        return None
    return result, unnamed > 0


def extract(url: str, content: str) -> tuple[str, bool] | None:
    if "stockanalysis.com" in url:
        return extract_stockanalysis(content)
    return None


# ---------------------------------------------------------------------------
# Repo discovery — driven by SOURCES.yml
# ---------------------------------------------------------------------------

def final_path(quarters_dir: Path, date: str, quarter: str) -> Path:
    return quarters_dir / f"{date}_{quarter}-transcript.md"


def preliminary_path(final: Path) -> Path:
    """<date>_<quarter>-preliminary-transcript.md — sibling of the final file."""
    return final.with_name(final.name.replace("-transcript.md", "-preliminary-transcript.md"))


def find_jobs(ticker_filter: str | None = None, date_filter: str | None = None) -> list[tuple]:
    """Return [(ticker, date, quarter, final_transcript_path)] for every SOURCES entry with a transcript URL."""
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
            transcript_path = final_path(quarters_dir, date, quarter)
            jobs.append((ticker, date, quarter, transcript_path))
    return jobs


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_transcript(path: Path, ticker: str, date: str, quarter: str, body: str, source_url: str,
                    preliminary: bool = False):
    path.parent.mkdir(exist_ok=True)
    title = "Preliminary Earnings Call Transcript" if preliminary else "Earnings Call Transcript"
    header = (
        f"# {ticker} {quarter.upper().replace('-', ' ')} {title}\n\n"
        f"**Announcement date:** {date}  \n"
        f"**Source:** {source_url}\n\n"
        "---\n\n"
    )
    path.write_text(header + body + "\n")
    print(f"  Saved → {path.relative_to(REPO_ROOT)}" + ("  (PRELIMINARY — speakers not yet attributed)" if preliminary else ""))


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

    extracted = extract(url, content)
    if not extracted:
        print("  FAILED: could not extract transcript")
        return False
    text, preliminary = extracted

    prelim = preliminary_path(transcript_path)
    if preliminary:
        if transcript_path.exists():
            print(f"  SKIP: source is unattributed but a final {transcript_path.name} already exists")
            return False
        save_transcript(prelim, ticker, date, quarter, text, url, preliminary=True)
    else:
        save_transcript(transcript_path, ticker, date, quarter, text, url)
        if prelim.exists():
            prelim.unlink()
            print(f"  Removed {prelim.name}")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    setup_logging()
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

    elif len(args) == 1:
        ticker = args[0].upper()
        # A preliminary file doesn't count as downloaded — keep retrying until final.
        jobs = [j for j in find_jobs(ticker) if not j[3].exists()]
        if not jobs:
            print(f"All {ticker} transcripts already downloaded.")
            return
        n_prelim = sum(preliminary_path(j[3]).exists() for j in jobs)
        print(f"Found {len(jobs)} missing transcript(s)" + (f", {n_prelim} currently preliminary" if n_prelim else ""))

    else:
        print(__doc__)
        sys.exit(1)

    ok = sum(download(t, d, q, tp, explicit_url if len(jobs) == 1 else None) for t, d, q, tp in jobs)
    still_prelim = [tp for _, _, _, tp in jobs if not tp.exists() and preliminary_path(tp).exists()]
    print(f"\nDone: {ok}/{len(jobs)} transcript(s) downloaded"
          + (f", {len(still_prelim)} preliminary — re-run later for the final version." if still_prelim else "."))


if __name__ == "__main__":
    main()
