---
description: 
Review a single ticker: valuation multiples, price/volume technicals, and a fresh news/catalyst check.
---

## Arguments

`$ARGUMENTS` — the ticker symbol to review (e.g. `ODD`, `BARK`, `GTBIF`).

Call this skill as: `/review-ticker <TICKER>`

---

## Pipeline

Work through the steps below in order. Each step writes to a temp directory; the final step assembles everything into the finished report using [template.md](template.md).

### Step 0 — Skip if already reviewed this week

Before doing any work, check whether a review already exists for the current week (weeks start Monday). Reviews are named `YYYY-MM-DD-review.md`, and ISO dates sort lexically, so compare each existing review's date against this Monday:

```bash
week_start=$(date -v-mon +%F)
ls $ARGUMENTS/reviews/*-review.md 2>/dev/null \
  | sed 's#.*/##; s/-review\.md$//' \
  | awk -v w="$week_start" '$0 >= w { print }'
```

If that prints any date, a review already exists for this week. **Stop immediately** — do not run any scripts, create temp directories, or search the web. Report back to the user that a review already exists for `$ARGUMENTS` this week and give its path (`$ARGUMENTS/reviews/<date>-review.md`), then end the run. Only continue to Setup if nothing was printed.

### Setup

Create a working directory for this run:

```bash
mkdir -p /tmp/review-ticker-$ARGUMENTS
```

All intermediate files go here. Today's date is in YYYY-MM-DD format.

---

### Step 1 — Run data scripts

First run `fetch-prices` so any missing price data is downloaded up front. Then run both data commands, redirecting full output to files. Use `COLUMNS=300` so Rich renders wide tables without truncation.

```bash
uv run fetch-prices $ARGUMENTS
COLUMNS=300 uv run check-valuation $ARGUMENTS > /tmp/review-ticker-$ARGUMENTS/valuation-raw.txt 2>&1
COLUMNS=300 uv run check-price $ARGUMENTS > /tmp/review-ticker-$ARGUMENTS/prices-raw.txt 2>&1
```

Read both files back in full before writing anything else.

---

### Step 2 — Write section files

Write each qualitative section as its own file in `/tmp/review-ticker-$ARGUMENTS/`. Do not echo these to the user — they are intermediate artifacts only.

**Rating scale (used by every `RATING:` line below and at assembly).** Scale: −2.5 to +2.5 (1 decimal place). Use the full range. Bands:

| Range | Word |
|---|---|
| −2.5 to −1.5 | STRONG SELL |
| −1.5 to −0.5 | SELL |
| −0.5 to +0.5 | HOLD |
| +0.5 to +1.5 | BUY |
| +1.5 to +2.5 | STRONG BUY |

Each section ends with a sentiment score that reflects what its summary actually says — not a standalone judgment, but a numerical representation of the sentiment and conclusions just written — emitted as a `RATING: <score>` line on its own.

#### `valuation-summary.md`

~300-word qualitative summary covering:

- Revenue trend (Y/Y growth or decline, acceleration/deceleration)
- FCF profile: FCF-positive? Trend? Material gap between company FCF and strict ex-SBC?
- Current valuation: TTM P/S, P/FCF, forward P/S vs. guidance or estimates — cheap, fair, or stretched relative to growth and FCF quality?
- Guidance trajectory: raise or cut track record across columns
- Notable balance-sheet items (net cash/debt, SBC burden relative to FCF)
- One-sentence verdict

```
RATING: <score>
```

#### `prices-summary.md`

~300-word qualitative summary covering:

- Recent price performance: WTD and MTD returns vs. benchmarks in the table
- SMA signal: above/below 20/50/200-day SMAs? Uptrend, downtrend, or mixed? Compare to benchmark SMAs
- Relative volume (rvol): elevated or subdued vs. 20/50/200-day averages? Conviction signal?
- Implied volatility: expected weekly and monthly move; elevated or calm vs. benchmark?
- Volume profile (POC): read the POC levels the table reports relative to the current price — support or resistance?
- One-sentence directional read

```
RATING: <score>
```

#### `news.md`

Search the web for material news and upcoming catalysts for **$ARGUMENTS**. ~400-word section covering:

- Fresh material news (last 2–4 weeks): earnings, guidance changes, analyst actions, management changes, M&A, regulatory filings
- Unusual price moves since the last earnings report and what drove them
- Upcoming catalysts: next earnings date, investor day, product launches, regulatory decisions, lock-up expirations
- Market sentiment: retail narrative and institutional posture (13F changes, short interest if notable)
- Key risks: macro, competitive, balance-sheet, or execution risks specific to this ticker

```
RATING: <score>
```

#### `verdict.md`

One short paragraph (3–5 sentences): key drivers, main risk, what would change the rating. Do not include the numeric score here — it is assembled separately as `{{TOTAL_RATING}}`.

---

### Step 3 — Assemble the report

Read [template.md](template.md). Substitute every placeholder with the corresponding content:

| Placeholder | Source |
|---|---|
| `{{TICKER}}` | `$ARGUMENTS` |
| `{{DATE}}` | today's date |
| `{{PRICE}}` | ref price from the TTM column of `valuation-raw.txt` |
| `{{VALUATION_RAW}}` | full contents of `valuation-raw.txt` |
| `{{VALUATION_SUMMARY}}` | body of `valuation-summary.md` (everything before the `RATING:` line) |
| `{{VALUATION_RATING}}` | score from the `RATING:` line in `valuation-summary.md` |
| `{{VALUATION_RATING_WORD}}` | band word for `{{VALUATION_RATING}}` (rating scale, Step 2) |
| `{{PRICES_RAW}}` | full contents of `prices-raw.txt` |
| `{{PRICES_SUMMARY}}` | body of `prices-summary.md` (everything before the `RATING:` line) |
| `{{PRICES_RATING}}` | score from the `RATING:` line in `prices-summary.md` |
| `{{PRICES_RATING_WORD}}` | band word for `{{PRICES_RATING}}` (rating scale, Step 2) |
| `{{NEWS}}` | body of `news.md` (everything before the `RATING:` line) |
| `{{NEWS_RATING}}` | score from the `RATING:` line in `news.md` |
| `{{NEWS_RATING_WORD}}` | band word for `{{NEWS_RATING}}` (rating scale, Step 2) |
| `{{VERDICT}}` | full contents of `verdict.md` |
| `{{TOTAL_RATING}}` | average of the three section scores, rounded to 1 decimal place |
| `{{TOTAL_RATING_WORD}}` | band word for `{{TOTAL_RATING}}` (rating scale, Step 2) |

> **Formatting rule:** In all written sections, escape every `$` that precedes a number or unit (e.g. `\$952M`, `\$16.81`). Bare `$` signs in markdown trigger math rendering. Exception: inline code spans (backtick-wrapped), where no escaping is needed.

---

### Step 4 — Save

Create the output directory if needed and write the assembled report:

```
$ARGUMENTS/reviews/YYYY-MM-DD-review.md
```

### Step 5 — Cleanup

Remove the temporary working directory:

```bash
rm -rf /tmp/review-ticker-$ARGUMENTS
```

Then tell the user the report has been saved and give the file path. Do not echo the report contents to the chat.
