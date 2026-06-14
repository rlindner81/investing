# Price Analysis Heuristics

Heuristics derived from backtesting `analyze-prices` signals across multiple stocks
and market regimes. Each fires as an inline hint after the relevant stock's benchmark
table when the condition is met.

---

## Overextension vs SPY

**Condition:** MTD return outperforms SPY by more than 10 percentage points.

When a stock runs more than 10pp ahead of the broad market within a single month,
near-term mean reversion has been historically reliable regardless of the underlying
SMA trend. The signal holds across market regimes and cap sizes. It does not predict
the direction of the longer-term trend — only that the short-term gap tends to close.

**Anchor example:** February 2021. WDAY was up +16.1% MTD vs SPY +4.3% (+11.8pp).
GTBIF the same day was up +22.2% vs SPY (+17.9pp). Both reversed within a month:
WDAY -6.5%, GTBIF -5.3%. The heuristic also fired on ABNB in November 2021
(+12.8pp ahead of SPY) which then fell -6.9%.

```
uv run analyze-prices --as-of 2021-02-16 WDAY GTBIF
```

---

## Small-Cap Selling Exhaustion

**Condition:** Stock is tagged `small_cap`, MTD return is negative, and rvol is
ascending (↑).

On small and micro-cap stocks, a declining price accompanied by rising volume
(rvol ↑ means the 10-day average volume is above the 20-day, which is above the
50-day) suggests active, high-participation selling. Counterintuitively, this is
often a sign the move is exhausting rather than accelerating — sellers who wanted
out are getting out loudly.

The contrast case is equally instructive: a stock down sharply with *neutral* rvol
indicates quiet distribution with no panic, which tends to precede trend continuation
rather than a bounce. Volume character distinguishes exhaustion from continuation.

**Anchor example:** July 2021. JD was down -14.7% MTD with rvol ↑ — elevated volume
into the decline. The following month it bounced +16.6%. Contrast with February 2023,
where JD was also down -13.3% but with neutral rvol, and continued falling -23.5%.
Same magnitude drop, opposite volume signal, opposite outcome.

```
uv run analyze-prices --as-of 2021-07-26 JD
uv run analyze-prices --as-of 2023-02-13 JD
```

---

## Quiet Downtrend Continuation

**Condition:** Stock SMA is descending (↓) and rvol is neutral (no arrow).

A bearish SMA stack alongside unremarkable volume means the stock is declining
without panic — no capitulation, no elevated selling pressure, just steady
deterioration. This combination predicts trend continuation rather than reversal,
because there is no volume-driven exhaustion event to reverse off of.

**Anchor example:** February 2023. JD showed SMA ↓ with neutral rvol after a -13.3%
MTD decline. No hint of exhaustion in the volume. The following month it fell a
further -23.5%. The absence of an rvol signal was itself the signal.

```
uv run analyze-prices --as-of 2023-02-13 JD
```
