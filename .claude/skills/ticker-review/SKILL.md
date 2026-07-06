---
description: 
Review a single ticker: valuation multiples, price/volume technicals, and a fresh news/catalyst check.
---

## Arguments

`$ARGUMENTS` — the ticker symbol to review (e.g. `ODD`, `BARK`, `GTBIF`).

Call this skill as: `/ticker-review <TICKER>`

---

## Pipeline

Work through the steps below in order. Each step writes to a temp directory; the final step assembles everything into the finished report using [template.md](template.md).

### Setup

Create a working directory for this run:

```bash
mkdir -p /tmp/ticker-review-$ARGUMENTS
```

All intermediate files go here. Today's date is in YYYY-MM-DD format.

---

### Step 1 — Run data scripts

Run both commands, redirecting full output to files. Use `COLUMNS=300` so Rich renders wide tables without truncation.

```bash
COLUMNS=300 uv run check-valuation $ARGUMENTS > /tmp/ticker-review-$ARGUMENTS/valuation-raw.txt 2>&1
COLUMNS=300 uv run check-prices $ARGUMENTS --iv --vp > /tmp/ticker-review-$ARGUMENTS/prices-raw.txt 2>&1
```

Read both files back in full before writing anything else.

---

### Step 2 — Write section files

Write each qualitative section as its own file in `/tmp/ticker-review-$ARGUMENTS/`. Do not echo these to the user — they are intermediate artifacts only.

#### `valuation-summary.md`

~300-word qualitative summary covering:

- Revenue trend (Y/Y growth or decline, acceleration/deceleration)
- FCF profile: FCF-positive? Trend? Material gap between company FCF and strict ex-SBC?
- Current valuation: TTM P/S, P/FCF, forward P/S vs. guidance or estimates — cheap, fair, or stretched relative to growth and FCF quality?
- Guidance trajectory: raise or cut track record across columns
- Notable balance-sheet items (net cash/debt, SBC burden relative to FCF)
- One-sentence verdict

#### `prices-summary.md`

~300-word qualitative summary covering:

- Recent price performance: WTD and MTD returns vs. benchmarks in the table
- SMA signal: above/below 20/50/200-day SMAs? Uptrend, downtrend, or mixed? Compare to benchmark SMAs
- Relative volume (rvol): elevated or subdued vs. 20/50/200-day averages? Conviction signal?
- Implied volatility: expected weekly and monthly move; elevated or calm vs. benchmark?
- Volume profile (POC): 2-year POC location — support or resistance?
- One-sentence directional read

#### `news.md`

Search the web for material news and upcoming catalysts for **$ARGUMENTS**. ~400-word section covering:

- Fresh material news (last 2–4 weeks): earnings, guidance changes, analyst actions, management changes, M&A, regulatory filings
- Unusual price moves since the last earnings report and what drove them
- Upcoming catalysts: next earnings date, investor day, product launches, regulatory decisions, lock-up expirations
- Market sentiment: retail narrative and institutional posture (13F changes, short interest if notable)
- Key risks: macro, competitive, balance-sheet, or execution risks specific to this ticker

#### `rating.md`

Single rating on its own line: **STRONG BUY / BUY / NEUTRAL / SELL / STRONG SELL**

One short paragraph (3–5 sentences): key drivers, main risk, what would change the rating.

End with the exact disclaimer: *This is not financial advice; this rating reflects a sentiment aggregation of the current review only.*

---

### Step 3 — Assemble the report

Read [template.md](template.md). Substitute every placeholder with the corresponding content:

| Placeholder | Source |
|---|---|
| `{{TICKER}}` | `$ARGUMENTS` |
| `{{DATE}}` | today's date |
| `{{PRICE}}` | ref price from the TTM column of `valuation-raw.txt` |
| `{{VALUATION_RAW}}` | full contents of `valuation-raw.txt` |
| `{{VALUATION_SUMMARY}}` | full contents of `valuation-summary.md` |
| `{{PRICES_RAW}}` | full contents of `prices-raw.txt` |
| `{{PRICES_SUMMARY}}` | full contents of `prices-summary.md` |
| `{{NEWS}}` | full contents of `news.md` |
| `{{RATING}}` | full contents of `rating.md` |

> **Formatting rule:** In all written sections, escape every `$` that precedes a number or unit (e.g. `\$952M`, `\$16.81`). Bare `$` signs in markdown trigger math rendering. Exception: inline code spans (backtick-wrapped), where no escaping is needed.

---

### Step 4 — Save

Create the output directory if needed and write the assembled report:

```
$ARGUMENTS/reviews/YYYY-MM-DD-review.md
```

Then tell the user the report has been saved and give the file path. Do not echo the report contents to the chat.
