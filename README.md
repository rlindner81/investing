# investing

## reviews

- [ABNB](ABNB/reviews)
- [BARK](BARK/reviews)
- [GTBIF](GTBIF/reviews)
- [JD](JD/reviews)
- [ODD](ODD/reviews)
- [SNAP](SNAP/reviews)

## usage check-price

```
usage: check-price [-h] [--as-of DATE] [--weeks | --months] tickers [tickers ...]

Analyse prices vs benchmarks.

positional arguments:
  tickers       Stocks to analyse

options:
  -h, --help    show this help message and exit
  --as-of DATE  Simulate analysis as of this date (YYYY-MM-DD)
  --weeks       Weekly view only
  --months      Monthly view only
```

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
