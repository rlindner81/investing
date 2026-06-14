Find new price analysis heuristics by running a backtesting experiment, then update HEURISTICS.md with findings.

## Steps

### 1. Select stocks and dates

From TICKERS.yml, randomly pick 4 stocks (any mix of tracked and watchlist).

Pick 16 dates between 2010 and 2025 with roughly this distribution:
- 2 dates in 2010–2014
- 3 dates in 2015–2018
- 4 dates in 2019–2021
- 7 dates in 2022–2025

Avoid known macro event dates: COVID crash (Feb–Apr 2020), Ukraine invasion (Feb–Mar 2022), SVB collapse (Mar 2023), yen carry unwind (Aug 2024), tariff shock (Apr 2025), US elections, Fed meeting days with major surprises.

### 2. Fetch data if needed

Ensure daily prices exist for the selected stocks and their benchmarks:

```
uv run fetch-prices --daily <TICKER> [...]
```

### 3. Run the compact backtest

For each (stock, date) pair, compute and display in a table:
- MTD return
- MTD return vs SPY (relative)
- SMA signal (↑ / ↓ / neutral)
- rvol signal (↑ / ↓ / neutral)
- 1-month forward return (what actually happened)

Use the helper functions in `src/investing/analyze_prices.py` directly.

### 4. Read the full table for patterns

Look for signals that consistently predict the forward return across multiple stocks and dates. Be critical — a pattern needs to appear in at least 4–5 cases without too many contradictions to be worth adding. Noise is the default; signal is the exception.

Cross-reference each case against existing heuristics in HEURISTICS.md:
- Did the heuristic fire? Was it correct?
- Did the heuristic fire? Was it wrong (counterexample)?

### 5. Update HEURISTICS.md

**New heuristic:** Only add if the signal is strong and not already covered. Use this format:

```markdown
## <Name>

**Condition:** <precise condition that triggers it>

<Explanation of why this works, including the contrast case if one exists.>

**Anchor example:** <date, stocks, what the signal showed, what happened next>

```
uv run analyze-prices --as-of <DATE> <TICKER>
```
```

**Existing heuristic — correct:** Add to the anchor examples list:

```markdown
**Anchor example:** <date, stocks, signal, outcome>

```
uv run analyze-prices --as-of <DATE> <TICKER>
```
```

**Existing heuristic — wrong:** Add to a counterexamples section at the bottom of that heuristic:

```markdown
**Counterexample:** <date, stocks, signal, what actually happened instead>

```
uv run analyze-prices --as-of <DATE> <TICKER>
```
```

### 6. Judgment call on heuristic health

After updating, count examples vs counterexamples for each heuristic. If counterexamples equal or outnumber examples, flag the heuristic for review with a warning comment in HEURISTICS.md.
