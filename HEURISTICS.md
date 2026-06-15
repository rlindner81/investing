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

February 2021. GTBIF was up +20.9% MTD, outperforming SPY by +16.5pp. Reversed
-3.4% the following month.

```
uv run analyze-prices --as-of 2021-02-15 GTBIF
```

July 2023. SNAP was up +12.1% MTD, outperforming SPY by +10.5pp. Fell -32.9% the
following month (Q2 earnings disaster).

```
uv run analyze-prices --as-of 2023-07-17 SNAP
```

January 2023. BARK was up +24.0% MTD, outperforming SPY by +19.6pp. Reversed
-18.8% the following month.

```
uv run analyze-prices --as-of 2023-01-17 BARK
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

June 2019. SNAP was up +15.9% MTD, outperforming SPY by +10.5pp, with SMA ↑ and
rvol ↑. Continued +10.2% the following month — strong momentum carried through.

```
uv run analyze-prices --as-of 2019-06-17 SNAP
```

February 2024. BARK was up +19.6% MTD, outperforming SPY by +17.2pp. Continued
+12.9% the following month — underlying business momentum was too strong.

```
uv run analyze-prices --as-of 2024-02-12 BARK
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

**Counterexamples:**

December 2022. GTBIF was down -29.1% MTD with rvol ↑ — exhaustion heuristic fired.
Continued falling -19.9% the following month. Cannabis sector headwinds overrode the
volume signal.

```
uv run analyze-prices --as-of 2022-12-12 GTBIF
```

February 2024. GTBIF was down -6.9% MTD with rvol ↑. Continued falling -13.5%.
Two consecutive counterexamples on the same stock suggest cannabis OTC names may
behave differently from the small-cap pattern — liquidity is too thin for volume to
be a reliable exhaustion signal.

```
uv run analyze-prices --as-of 2024-02-12 GTBIF
```

---

## Quiet Downtrend Continuation

> ⚠️ **Under review.** Anchor and counterexample counts are now equal (5:5).
> This heuristic may need to be narrowed or retired.

**Condition:** Stock SMA is descending (↓) and rvol is neutral (no arrow).

A bearish SMA stack alongside unremarkable volume means the stock is declining
without panic — no capitulation, no elevated selling pressure, just steady
deterioration. This combination was expected to predict trend continuation rather
than reversal. However accumulating counterexamples suggest the signal is weaker
than initially believed, particularly for macro-driven stocks and broad recoveries.

**Anchor examples:**

February 2023. JD showed SMA ↓ with neutral rvol after a -13.3% MTD decline.
The following month it fell a further -23.5%.

```
uv run analyze-prices --as-of 2023-02-13 JD
```

April 2021. Both JD (-6.7% MTD, SMA ↓, rvol neutral → -9.4%) and SNAP (+14.6% MTD
but SMA ↓, rvol neutral → -17.4%) confirmed the pattern on the same date.

```
uv run analyze-prices --as-of 2021-04-12 JD SNAP
```

October 2021. SNAP (MTD -2.3%, SMA ↓, rvol neutral → -27.5%) and GTBIF (MTD -8.1%,
SMA ↓, rvol neutral → -0.2%) both confirmed.

```
uv run analyze-prices --as-of 2021-10-11 SNAP GTBIF
```

**Counterexamples:**

May 2022. JD showed SMA ↓ with neutral rvol — heuristic fired. Bounced +23.4% as
China eased Shanghai lockdowns. Macro catalyst overrode entirely.

```
uv run analyze-prices --as-of 2022-05-16 JD
```

June 2019. GOOGL had SMA ↓ with neutral rvol, MTD -0.2%. Bounced +4.8% the
following month as the Fed pivoted dovish.

```
uv run analyze-prices --as-of 2019-06-17 GOOGL
```

July 2022. GTBIF had SMA ↓ with neutral rvol, MTD +12.3%. Rose +20.9% the
following month — bear market bounce overrode the bearish stack.

```
uv run analyze-prices --as-of 2022-07-11 GTBIF
```

January 2023. Both GOOGL (SMA ↓, rvol neutral → +3.4%) and GTBIF (SMA ↓, rvol
neutral → +6.5%) bounced despite the bearish signal — broad market recovery in
January 2023 lifted everything.

```
uv run analyze-prices --as-of 2023-01-17 GOOGL GTBIF
```
