# investing

## tools

Each runs as `uv run <tool> <TICKER>`, e.g. `uv run check-price ODD`.

- **`check-valuation`** — P/S and P/FCF per quarter and fiscal year, plus
  forward multiples, from the hand-entered `FINANCIALS.yml`.
- **`check-price`** — weekly and monthly price vs. benchmarks, with SMAs.
- **`check-flow`** — volume-first grids: hourly day-over-day, daily
  week-over-week.
- **`check-reaction`** — price, relative move and volume around each past
  earnings announcement.
- **`check-shape`** — historical chart windows matching a ticker's recent
  price+volume shape, and what happened next.

## examples

To ad-hoc analyze a stock
```
uv run check-price NIO
```

For a comparative benchmark analysis check [TICKERS.yml](./TICKERS.yml) and run, e.g.
```
uv run check-price ODD
```

## re-seed scweet

```
 uv run python -c "from Scweet import Scweet; \
      Scweet(db_path='tweets/scweet_state.db', auth_token='<AUTH_TOKEN>')"
```
