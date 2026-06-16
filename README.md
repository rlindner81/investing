# investing

## usage

```
usage: analyze-prices [-h] [--as-of DATE] [--weeks | --months] [--iv] [tickers ...]

Analyse prices vs benchmarks.

positional arguments:
  tickers       Stocks to analyse (default: all)

options:
  -h, --help    show this help message and exit
  --as-of DATE  Simulate analysis as of this date (YYYY-MM-DD)
  --weeks       Weekly view only
  --months      Monthly view only
  --iv          Fetch and show implied volatility / expected move
```

## examples

To ad-hoc analyze a stock
```
uv run analyze-prices NIO --iv
```

For a comparative benchmark analysis check [TICKERS.yml](./TICKERS.yml) and run, e.g.
```
uv run analyze-prices ODD
```
