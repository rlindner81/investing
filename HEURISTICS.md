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

Note: the signal is weaker when the outperformance is driven by SPY falling sharply
rather than the stock genuinely surging. Relative resilience in a down market is
different from overextension in a rising market.

**Anchor examples:**

February 2021. WDAY was up +16.1% MTD vs SPY +4.3% (+11.8pp). GTBIF the same day
was up +22.2% vs SPY (+17.9pp). Both reversed: WDAY -6.5%, GTBIF -5.3%. ABNB in
November 2021 (+12.8pp ahead of SPY) then fell -6.9%.

```
uv run analyze-prices --as-of 2021-02-16 WDAY GTBIF
```

April 2015. JD was up +16.7% MTD, outperforming SPY by +15.1pp. Fell -4.0% the
following month.

```
uv run analyze-prices --as-of 2015-04-13 JD
```

April 2021. SNAP was up +14.6% MTD, outperforming SPY by +11.8pp — while also
carrying a bearish SMA (↓). Both heuristics fired simultaneously. Fell -17.4%.

```
uv run analyze-prices --as-of 2021-04-12 SNAP
```

**Counterexamples:**

August 2016. JD was up +15.9% MTD, outperforming SPY by +14.9pp. Continued +3.2%
the following month. Strong underlying bull momentum overrode the mean-reversion pull.

```
uv run analyze-prices --as-of 2016-08-15 JD
```

September 2020. SNAP showed +11.7pp relative to SPY, but SPY itself was down -4%
during a tech correction — SNAP's outperformance came from resilience, not a surge.
Continued +13.2%.

```
uv run analyze-prices --as-of 2020-09-14 SNAP
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

**Anchor examples:**

July 2021. JD was down -14.7% MTD with rvol ↑ — elevated volume into the decline.
The following month it bounced +16.6%. Contrast with February 2023, where JD was also
down -13.3% but with neutral rvol, and continued falling -23.5%. Same magnitude drop,
opposite volume signal, opposite outcome.

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

**Anchor examples:**

February 2023. JD showed SMA ↓ with neutral rvol after a -13.3% MTD decline.
The following month it fell a further -23.5%.

```
uv run analyze-prices --as-of 2023-02-13 JD
```

April 2021. Both JD (-6.7% MTD, SMA ↓, rvol neutral → -9.4%) and SNAP (+14.6% MTD
but SMA ↓, rvol neutral → -17.4%) confirmed the pattern on the same date. SNAP is
particularly notable: despite outperforming SPY by 11.8pp that month, the bearish SMA
with quiet volume correctly predicted the reversal.

```
uv run analyze-prices --as-of 2021-04-12 JD SNAP
```

**Counterexamples:**

May 2022. JD showed SMA ↓ with neutral rvol after an -18.7% MTD decline — the
heuristic fired, predicting continuation. Instead, JD bounced +23.4% the following
month as China began easing Shanghai lockdown restrictions. A macro catalyst overrode
the technical setup entirely.

```
uv run analyze-prices --as-of 2022-05-16 JD
```
