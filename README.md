# investing

## usage

```
usage: check-prices [-h] [--as-of DATE] [--weeks | --months] [--iv] [tickers ...]

Analyse prices vs benchmarks.

positional arguments:
  tickers       Stocks to analyse (default: all)

options:
  -h, --help    show this help message and exit
  --as-of DATE  Simulate analysis as of this date (YYYY-MM-DD)
  --weeks       Weekly view only
  --months      Monthly view only
  --iv          Fetch and show implied volatility / expected move
  --vp          Show 2yr hourly volume profile point of control
```

## examples

To ad-hoc analyze a stock
```
uv run check-prices NIO --iv --vp
```

For a comparative benchmark analysis check [TICKERS.yml](./TICKERS.yml) and run, e.g.
```
uv run check-prices ODD
```
