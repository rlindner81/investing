---
description:
Onboard a brand-new ticker end to end: create its directory, discover SEC filings, build SOURCES.yml, transcribe FINANCIALS.yml (with revenue guidance) back to fiscal q1-2023, write the business/management docs, and register it in TICKERS.yml with tags and benchmark ETFs.
---

## Arguments

`$ARGUMENTS` — the ticker symbol to onboard (e.g. `RDDT`, `CHWY`, `DUOL`). Optionally
followed by the company's full name if the symbol is ambiguous.

Call this skill as: `/research-ticker <TICKER>`

Reference implementations to match for depth and conventions: **SNAP** (calendar
fiscal year, next-quarter-only guidance) and **BARK** (March fiscal-year end,
full-year guidance, reverse split). Read both tickers' `SOURCES.yml`,
`FINANCIALS.yml`, `business-plan.md`, and `management-team.md` before starting so the
new files match their structure exactly. Also re-read the **Valuation Multiples** and
**Adding a New Ticker** sections of `CLAUDE.md` — the `FINANCIALS.yml` schema there is
authoritative.

---

## Pipeline

Work through the steps in order. The core deliverable is a complete, validated
`FINANCIALS.yml` **including revenue guidance**, covering every quarter back to fiscal
**q1-2023**. Everything else supports that.

### Step 0 — Guard: skip if already onboarded

If `$ARGUMENTS/FINANCIALS.yml` already exists, this ticker is already onboarded.
**Stop immediately** and tell the user, pointing at the existing directory. Do not
overwrite. Only continue if the directory is absent or bare.

---

### Step 1 — Discover filings and bootstrap SOURCES + reports

`discover-filings` resolves the CIK from the symbol, enumerates every 10-Q / 10-K
(or 20-F) since the cutoff, derives each fiscal-quarter label from the period-end and
the company's fiscal-year end, writes `SOURCES.yml`, and downloads each report.

```bash
uv run discover-filings $ARGUMENTS --since 2023-01-01
```

The `--since 2023-01-01` window captures the FY2023 10-K and every 2023–present
10-Q. That is what you need: to transcribe standalone q1-2023 revenue and to give the
first displayed quarter a valid trailing-twelve-month base (see Step 3).

If the symbol doesn't resolve to a CIK (ADRs, recent IPOs, ticker changes), find the
CIK manually on EDGAR full-text search, add it as `_meta.cik` in
`$ARGUMENTS/SOURCES.yml`, and re-run. For a foreign filer that reports on 20-F/6-K
instead of 10-K/10-Q, note it — `discover-filings` picks up 20-F automatically, but
letters/press releases will be 6-K.

After this step, `$ARGUMENTS/SOURCES.yml` has one entry per quarter (`quarter`,
`period_end`, `report`, `report_type`) and `$ARGUMENTS/quarters/*-report.md` holds the
downloaded statements.

---

### Step 2 — Add letters (guidance source) and transcripts, then fetch them

`discover-filings` only fills the `report` key. **Revenue guidance lives in the 8-K
earnings press release / investor letter, not the 10-Q**, so you must add those by
hand. For each quarter entry in `SOURCES.yml`:

1. On EDGAR, open the company's filing list and find the **8-K** filed on (or the day
   before) that quarter's announcement date. Its EX-99.1 (press release) or EX-99.2
   (investor letter) exhibit is the guidance source.
2. Add `letter:` (the exhibit `.htm` URL) and `letter_type: 8-K` to that entry.
   Foreign filers: the exhibit hangs off a **6-K**, so use `letter_type: 6-K`.
3. Optionally add a `transcript:` URL from
   `stockanalysis.com/stocks/<ticker>/transcripts/` for the most recent quarters —
   useful for qualitative guidance hints when a number isn't printed.

Then download everything that's now referenced:

```bash
uv run fetch-sources $ARGUMENTS
uv run fetch-transcript $ARGUMENTS    # only if you added transcript keys
```

Match the `SOURCES.yml` layout of SNAP/BARK: `_meta.cik` at top, then one dated block
per quarter, oldest or newest first as the reference files do.

---

### Step 3 — Transcribe FINANCIALS.yml (the core deliverable)

Create `$ARGUMENTS/FINANCIALS.yml` following the schema in `CLAUDE.md` and the shape
of `SNAP/FINANCIALS.yml`. Read every downloaded `-report.md` and `-letter.md` and
transcribe the numbers — **do not** use Yahoo/derived figures. One entry per quarter,
oldest first, back to **q1-2023**.

Header comment block (copy the SNAP/BARK style): state the fiscal-year convention, the
`unit`, any `currency` other than USD, and ticker-specific quirks (single capex line
vs. multiple, splits, guidance cadence).

Per quarter, fill from the filings:

- **`revenue`** — standalone quarter, from the income statement. Q4 has no standalone
  column in the 10-K: compute it as `FY total − 9-month YTD` and leave a comment
  showing the subtraction (see SNAP q4 lines).
- **`shares_outstanding`** — point-in-time total across all classes at quarter end,
  from the cover page / balance sheet (NOT the weighted average). Net of treasury.
- **`ytd_operating_cf`, `ytd_capex_ppe`**, any other `ytd_capex_*`, **`ytd_sbc`** —
  as reported year-to-date; the tool differences consecutive quarters itself.
- **`cash`, `total_debt`** — balance-sheet snapshot (optional but include when present).
- **Revenue guidance** issued *at that report* (from the Step-2 letter):
  `guidance_nq_revenue_low/high` for next-quarter, `guidance_fy_revenue_low/high` for
  full-year. When a company guides only next-quarter (like SNAP), leave the FY rows
  empty. When guidance was withheld/withdrawn, record the reason with
  `guidance_nq_hint` / `guidance_fy_hint` instead of inventing numbers.

**History-only base for the earliest year.** Follow the SNAP/BARK pattern: the oldest
fiscal year (the 2023 quarters, or FY2024 for a March filer like BARK) exists to give
later quarters a valid TTM base. Those quarters carry fundamentals only — **omit
`report_date` and `shares_outstanding`** so their valuation rows stay blank. Every
quarter from the first fully-covered fiscal year onward gets `report_date` (the
announcement date), `shares_outstanding`, balance-sheet fields, and guidance so
`check-valuation` renders full per-column valuation.

Cross-check reconstructed history against EDGAR's XBRL companyfacts API
(`https://data.sec.gov/api/xbrl/companyfacts/CIK<10-digit>.json`) — that's where SNAP's
2023 diff-base quarters came from. Optionally record the tag mapping in
`$ARGUMENTS/xbrl-to-financials.yml` like SNAP does.

---

### Step 4 — Business and management docs

Write `$ARGUMENTS/business-plan.md` and `$ARGUMENTS/management-team.md` matching the
sections and depth of the SNAP/BARK files (see `CLAUDE.md` → File Conventions). Source
from the 10-K business section, investor relations, and filings. Per the CLAUDE.md
checklist you may also add the two most recent quarterly summary files under
`quarters/`, but FINANCIALS is the priority — add those only if the user wants them.

---

### Step 5 — Register in TICKERS.yml

Add the ticker under `stocks:` in the root `TICKERS.yml`:

```yaml
  - symbol: $ARGUMENTS
    tags: [<cap>, <sector>, <business-model>, ...]
    benchmarks: [<BROAD>, <SECTOR>]
```

- **Tags** — pick from the vocabulary already in the file (market cap band such as
  `mega_cap`/`large_cap`/`mid_cap`/`small_cap`, then sector/theme/business-model tags).
  Mirror how comparable existing tickers are tagged.
- **Two benchmark ETFs — one broad, one sector-specific.** The broad one is almost
  always `SPY` (or `MCHI`/`KWEB` for a China name, matching JD). The sector ETF must
  fit the business (e.g. `SOCL` for social, `PAWZ` for pets, `IGV` for software,
  `XLY` for consumer discretionary).
- If the chosen sector ETF is **not already** under `benchmarks.etfs:`, add it there
  too, with `symbol`, `issuer`, and a one-line `description` in the existing style.
  Reuse an ETF that's already listed when one fits rather than adding a duplicate.

---

### Step 6 — Validate

Confirm the pipeline produces a clean valuation table:

```bash
uv run fetch-prices $ARGUMENTS
COLUMNS=300 uv run check-valuation $ARGUMENTS
```

Check that:

- Every displayed quarter shows a market cap, P/S, and P/FCF (no unexpected blanks).
- Guidance and forward-P/S rows populate for the quarters where you entered guidance.
- **FCF reconciles**: for each full fiscal year, `ytd_operating_cf − ytd_capex_ppe`
  reproduces the company's own reported free cash flow (per the CLAUDE.md sanity check).
- Currency, split, and share-count assumptions look right versus the live price.

Fix any transcription errors and re-run until the table is coherent. Then report to the
user: the directory created, the quarters covered, the TICKERS.yml entry (tags +
benchmarks, and any new ETF added), and paste the final `check-valuation` table.
