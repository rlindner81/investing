# investing

## tools

Each runs as `uv run <tool> <TICKER>`, e.g. `uv run check-price ODD`.

- **`check-valuation`** — P/S and P/FCF per quarter and fiscal year, plus
  forward multiples, from `FINANCIALS.yml`.
- **`check-price`** — weekly and monthly price vs. benchmarks, with SMAs, 
  from `TICKERS.yml`.
- **`check-flow`** — volume-first grids: hourly day-over-day, daily
  week-over-week.
- **`check-reaction`** — price, relative move and volume around each past
  earnings announcement.
- **`check-shape`** — historical chart windows matching a ticker's recent
  price+volume shape, and what happened next.

## re-seed scweet

```
 uv run python -c "from Scweet import Scweet; \
      Scweet(db_path='tweets/scweet_state.db', auth_token='<AUTH_TOKEN>')"
```
