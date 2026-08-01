#!/usr/bin/env python3
"""
fetch_tweets.py — Pull the latest tweets about a ticker via Scweet, dedupe them
against an append-only ledger, and print either "no new data" or the 5 latest
new tweets.

Each run searches recent tweets for the ticker's cashtag (e.g. ``$RBLX``) —
English only, with at least one reply and one retweet to filter out noise —
appends any tweet_id it hasn't seen before to a per-ticker JSONL ledger, and
reports only what's genuinely new. The ledger is append-only: it's the memory
that keeps successive runs from re-reporting the same tweets.

Credentials
-----------
Scweet keeps its authenticated session in a local SQLite db
(``tweets/scweet_state.db``), so this script never handles a token itself —
it just points Scweet at that db. Seed the session ONCE with your x.com
``auth_token`` cookie (DevTools → Application → Cookies → https://x.com);
merely constructing the client provisions the db — no search needed:

  uv run python -c "from Scweet import Scweet; \
      Scweet(db_path='tweets/scweet_state.db', auth_token='<AUTH_TOKEN>')"

Once the db exists, seeding is done and every ``fetch-tweets`` run reuses the
session. The ``tweets/`` dir (db included) is gitignored. If the session is
missing or expired, a run exits telling you to re-seed.

A cooldown keeps repeated runs from hammering the API: if the ledger was
refreshed within the last hour (``--cooldown`` minutes), the search is skipped
and the latest tweets are served straight from the ledger. Pass ``--cooldown 0``
to force a fresh fetch.

Usage:
  uv run fetch-tweets RBLX
  uv run fetch-tweets RBLX --limit 100 --days 3   # widen/narrow the search
  uv run fetch-tweets RBLX --show 10              # print more than 5
  uv run fetch-tweets RBLX --query '$RBLX OR "Roblox earnings"'  # custom query
  uv run fetch-tweets RBLX --cooldown 0           # bypass cooldown, force fetch

Ledger: tweets/<TICKER>.jsonl  (one JSON tweet record per line, append-only)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from investing.lib import REPO_ROOT

TWEETS_DIR = REPO_ROOT / "tweets"
STATE_DB = TWEETS_DIR / "scweet_state.db"

# Don't re-hit the API if the ledger was refreshed within this window; serve the
# latest tweets from the ledger instead. Overridable per-run via --cooldown.
DEFAULT_COOLDOWN_MIN = 60

# The fields we persist per tweet. Scweet returns more (raw, media, ...); we keep
# a stable, readable subset — tweet_id is the dedup key.
LEDGER_FIELDS = (
    "tweet_id",
    "timestamp",
    "screen_name",
    "name",
    "text",
    "likes",
    "retweets",
    "comments",
    "tweet_url",
)


# ---------------------------------------------------------------------------
# Ledger — append-only JSONL keyed on tweet_id
# ---------------------------------------------------------------------------

def ledger_path(ticker: str) -> Path:
    """Ledger location for a ticker.

    A tracked ticker (its ``<TICKER>/`` dir exists) keeps the ledger alongside
    its research as ``<TICKER>/tweets.jsonl``; an ad-hoc ticker falls back to the
    transient ``tweets/<TICKER>.jsonl``.
    """
    ticker_dir = REPO_ROOT / ticker
    if ticker_dir.is_dir():
        return ticker_dir / "tweets.jsonl"
    return TWEETS_DIR / f"{ticker}.jsonl"


def load_records(path: Path) -> list[dict]:
    """Read the ledger and return all records, in file (append) order."""
    if not path.exists():
        return []
    records: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate a partially-written trailing line
    return records


def load_seen_ids(path: Path) -> set[str]:
    """Return the set of tweet_ids already recorded in the ledger."""
    return {str(r["tweet_id"]) for r in load_records(path) if r.get("tweet_id")}


def seconds_since_update(path: Path) -> float | None:
    """Age of the ledger in seconds (by mtime), or None if it doesn't exist."""
    if not path.exists():
        return None
    return time.time() - path.stat().st_mtime


def append_records(path: Path, records: list[dict]) -> None:
    """Append records to the ledger, creating it (and tweets/) if needed."""
    path.parent.mkdir(exist_ok=True)
    with path.open("a") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Scweet
# ---------------------------------------------------------------------------

def build_client():
    """Construct a Scweet client backed by the persisted session db.

    Scweet stores its authenticated session in STATE_DB; we pass no token here.
    Seeding is a one-time step (see the module docstring). If the db has no
    valid session, the AuthError surfaces at search time.
    """
    from Scweet import Scweet

    TWEETS_DIR.mkdir(exist_ok=True)
    return Scweet(db_path=str(STATE_DB))


def normalize(raw: dict) -> dict:
    """Flatten a Scweet tweet dict into our stable ledger record."""
    user = raw.get("user") or {}
    return {
        "tweet_id": str(raw.get("tweet_id", "")),
        "timestamp": raw.get("timestamp", ""),
        "screen_name": user.get("screen_name", ""),
        "name": user.get("name", ""),
        "text": (raw.get("text") or "").strip(),
        "likes": raw.get("likes", 0),
        "retweets": raw.get("retweets", 0),
        "comments": raw.get("comments", 0),
        "tweet_url": raw.get("tweet_url", ""),
    }


def search_tweets(ticker: str, query: str | None, days: int, limit: int) -> list[dict]:
    from Scweet import AccountPoolExhausted, AuthError

    client = build_client()
    q = query or f"${ticker}"
    since = (date.today() - timedelta(days=days)).isoformat()
    try:
        raw = client.search(
            q,
            since=since,
            limit=limit,
            display_type="Latest",
            lang="en",
            min_replies=1,
            min_retweets=1,
        )
    except (AuthError, AccountPoolExhausted) as exc:
        # Un-seeded db (empty pool) or an expired session both land here.
        sys.exit(
            f"No usable Scweet session in {STATE_DB.relative_to(REPO_ROOT)} "
            f"({type(exc).__name__}: {exc}). Seed/re-seed it — see the module "
            "docstring."
        )
    except Exception as exc:  # Scweet raises typed errors on rate/network too
        sys.exit(f"Scweet search failed: {type(exc).__name__}: {exc}")
    return [normalize(t) for t in (raw or []) if t.get("tweet_id")]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def sort_newest_first(records: list[dict]) -> list[dict]:
    # timestamp is an ISO-8601 string; lexical sort matches chronological order.
    return sorted(records, key=lambda r: r.get("timestamp", ""), reverse=True)


def print_tweets(ticker: str, records: list[dict], show: int, heading: str) -> None:
    print(f"\n{heading}\n")
    for rec in records[:show]:
        handle = f"@{rec['screen_name']}" if rec["screen_name"] else "?"
        who = f"{rec['name']} ({handle})" if rec["name"] else handle
        stamp = rec["timestamp"] or "?"
        text = " ".join(rec["text"].split())  # collapse newlines for terminal
        stats = f"♥ {rec['likes']}  ↻ {rec['retweets']}  💬 {rec['comments']}"
        print(f"  {stamp}  {who}")
        print(f"    {text}")
        print(f"    {stats}   {rec['tweet_url']}")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        prog="fetch-tweets",
        description="Fetch latest tweets for a ticker, dedupe via an append-only ledger.",
    )
    p.add_argument("ticker", help="ticker symbol, e.g. RBLX (cashtag search $TICKER)")
    p.add_argument("--query", help="override the search query (default: $TICKER)")
    p.add_argument("--days", type=int, default=2, help="look back this many days (default: 2)")
    p.add_argument("--limit", type=int, default=50, help="max tweets to pull per run (default: 50)")
    p.add_argument("--show", type=int, default=5, help="how many new tweets to print (default: 5)")
    p.add_argument(
        "--cooldown",
        type=float,
        default=DEFAULT_COOLDOWN_MIN,
        help=f"skip the API and serve from the ledger if it was refreshed within "
        f"this many minutes (default: {DEFAULT_COOLDOWN_MIN}; 0 disables)",
    )
    args = p.parse_args()

    ticker = args.ticker.upper()
    path = ledger_path(ticker)

    # Cooldown: if the ledger was refreshed recently, don't re-hit the API —
    # print the latest tweets straight from the ledger.
    age = seconds_since_update(path)
    if args.cooldown > 0 and age is not None and age < args.cooldown * 60:
        records = sort_newest_first(load_records(path))
        mins = age / 60
        print_tweets(
            ticker,
            records,
            args.show,
            heading=f"Cooldown ({mins:.0f} min since last fetch, < {args.cooldown:.0f} min) — "
            f"latest {min(args.show, len(records))} of {len(records)} from ledger for ${ticker}:",
        )
        print(f"Ledger: {path.relative_to(REPO_ROOT)} (unchanged; re-run with --cooldown 0 to force).")
        return

    seen = load_seen_ids(path)
    fetched = search_tweets(ticker, args.query, args.days, args.limit)

    new = [r for r in fetched if r["tweet_id"] not in seen]
    if not new:
        print(f"No new data for ${ticker} ({len(fetched)} tweet(s) fetched, all already in ledger).")
        return

    new = sort_newest_first(new)
    append_records(path, new)
    print_tweets(
        ticker,
        new,
        args.show,
        heading=f"{len(new)} new tweet(s) for ${ticker} — showing top {min(args.show, len(new))}:",
    )
    print(f"Ledger: {path.relative_to(REPO_ROOT)} (+{len(new)}, {len(seen) + len(new)} total)")


if __name__ == "__main__":
    main()
