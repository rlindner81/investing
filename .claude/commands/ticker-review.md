Review a single ticker: valuation multiples, price/volume technicals, and a fresh news/catalyst check.

## Arguments

`$ARGUMENTS` — the ticker symbol to review (e.g. `ODD`, `BARK`, `GTBIF`).

Call this skill as: `/ticker-review <TICKER>`

## Steps

### 1. Valuation snapshot

Run:

```
uv run show-valuation $ARGUMENTS
```

Read the full output carefully. Then write a **~300-word qualitative summary** covering:

- Revenue trend (Y/Y growth or decline, acceleration/deceleration)
- FCF profile: is the company FCF-positive? How has it trended? Note any material difference between company FCF and strict ex-SBC FCF
- Current valuation: TTM P/S, P/FCF, and forward P/S vs. guidance or estimates — are they cheap, fair, or stretched relative to the growth rate and FCF quality?
- Guidance trajectory: what has management guided and how has the raise (or cut) track record looked across the columns?
- Any notable balance-sheet items (net cash/debt, SBC burden relative to FCF)
- One-sentence verdict on whether the valuation looks compelling, neutral, or stretched

### 2. Price and technical analysis

Run:

```
uv run analyze-prices $ARGUMENTS --iv --vp
```

Read the full output carefully. Then write a **~300-word qualitative summary** covering:

- Recent price performance: WTD and MTD absolute returns and relative to the relevant benchmarks shown in the table
- SMA signal: is the stock above or below its 20/50/200-day SMAs? Uptrend, downtrend, or mixed? Compare to the benchmark SMAs
- Relative volume (rvol): is volume elevated or subdued vs. the 20/50/200-day averages? What does that signal about conviction?
- Implied volatility: what is the market pricing as the expected weekly and monthly move? Is IV elevated or calm relative to the benchmark?
- Volume profile (POC): where is the 2-year point of control? Is the stock trading above or below that range? Does it represent support or resistance?
- One-sentence directional read on the technical picture

### 3. News and catalyst check

Use web search to check for material news, price-moving events, and upcoming catalysts for **$ARGUMENTS** specifically. Adapt the query to the single ticker.

Write a **~400-word section** covering:

- Any fresh material news (last 2–4 weeks): earnings releases, guidance changes, analyst upgrades/downgrades, management changes, M&A, regulatory filings (8-K, 13D/G, etc.)
- Excessive or unusual price moves since the last earnings report and what drove them
- Upcoming catalysts: next earnings date, investor day, product launches, regulatory decisions, lock-up expirations
- Market sentiment: retail investor narrative (Reddit, social) and institutional posture (recent 13F changes, short interest if notable)
- Key risks: macro, competitive, balance-sheet, or execution risks specific to this ticker right now
- Technical context: price and volume patterns compared to recent averages (cross-reference with the analyze-prices output above)

Do not add a summary or ask questions at the end.

### Rating

After the three sections, add a final **Rating** section. Synthesize the valuation, technical, and news findings into a single rating:

**STRONG BUY / BUY / NEUTRAL / SELL / STRONG SELL**

One short paragraph (3–5 sentences) explaining the key drivers behind the rating — what is working, what is the main risk, and what would change it. End with the disclaimer: *This is not financial advice; this rating reflects a sentiment aggregation of the current review only.*

### 4. Save the review

Once the full review is written, save it to:

```
$ARGUMENTS/reviews/YYYY-MM-DD-review.md
```

where `YYYY-MM-DD` is today's date. Create the `reviews/` directory if it does not exist.

The file should contain the complete review exactly as presented to the user, with a front-matter header:

```markdown
---
ticker: $ARGUMENTS
date: YYYY-MM-DD
price: <ref price from show-valuation TTM column>
---
```

followed by the three sections (Valuation, Price & Technicals, News & Catalysts) in full. Do not truncate or summarise — write the complete text that was shown to the user.
