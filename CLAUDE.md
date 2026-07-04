# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

This is an investment research repository. Each ticker gets its own top-level directory containing structured markdown files that document the company's business, management, and quarterly earnings results.

## Directory Structure Per Ticker

```
<TICKER>/
  business-plan.md       — company overview, revenue model, product portfolio, strategy, risks, financial snapshot
  management-team.md     — executive bios, backgrounds, governance notes
  FINANCIALS.yml         — structured figures for valuation multiples (see "Valuation Multiples")
  quarters/
    YYYY-MM-DD_<label>-summary.md   — one file per reported earnings, date = announcement date
```

## File Conventions

**business-plan.md** covers: company overview (founding, IPO, HQ, fiscal year, latest annual revenue), revenue model, product/brand portfolio, technology or competitive moat, markets and channel mix, growth strategy, key risks, and a financial snapshot table of the most recent fiscal year.

**management-team.md** covers: one section per named executive with background, career prior to the company, role, and notable facts. Ends with corporate governance notes.

**Quarterly summary files** are named `YYYY-MM-DD_q#-<period>-summary.md` where the date is the announcement date. Each file covers: reported financial metrics (income statement, balance sheet, consensus vs. actual), key operating KPIs, forward guidance, stock reaction, and conference call highlights (CEO statements, CFO statements, key analyst Q&A themes).

## Quarterly File Naming

- Use the announcement date as the filename prefix (ISO 8601: `YYYY-MM-DD`)
- For companies whose fiscal year aligns with the calendar year: `q3-2025-summary`, `q4-2025-summary`
- For companies with non-calendar fiscal years: include `fy` to avoid ambiguity — e.g., `q4-fy2025-summary`, `q3-fy2026-summary`
- Always note the fiscal year convention and the calendar period covered at the top of the file

## Downloading Reports and Transcripts

There are two separate scripts for fetching quarterly data. Both read from `sources.json` and skip files that already exist.

### SEC Financial Reports

Reports are saved as `YYYY-MM-DD_<quarter>-report.md` and contain the income statement, balance sheet, and cash flow from the SEC filing.

Use `fetch-report` to download them:

```bash
# Download all missing reports in the repo
uv run fetch-report

# Download a specific quarter
uv run fetch-report ODD 2026-06-02
```

Source: SEC EDGAR. Add the filing index URL as the `report` key in `sources.json` before running the script.

### Earnings Call Transcripts

Transcripts are saved as `YYYY-MM-DD_<quarter>-transcript.md` alongside each summary file.

Use `fetch-transcript` to download them:

```bash
# Download all missing transcripts in the repo
uv run fetch-transcript

# Download a specific quarter
uv run fetch-transcript BARK 2025-06-04

# Supply a URL directly
uv run fetch-transcript ODD 2026-02-25 --url https://stockanalysis.com/stocks/odd/transcripts/402471-q4-2025/
```

Source: stockanalysis.com. Find the transcript URL by navigating to `stockanalysis.com/stocks/<ticker>/transcripts/` and copying the link for the relevant quarter. Add it as the `transcript` key in `sources.json` before running the script.

## Valuation Multiples

Price-to-Sales and Price-to-FCF multiples are computed from the official SEC
figures — **not** from Yahoo's derived numbers — via a per-ticker `FINANCIALS.yml`.
Only the live share price comes from yfinance; every fundamental is transcribed
from the reports under `<TICKER>/quarters/`.

```bash
uv run show-multiples              # all stocks in TICKERS.yml
uv run show-multiples ODD BARK     # specific tickers
uv run show-multiples NVDA         # ad-hoc ticker → yfinance fallback (flagged)
```

A ticker without a `FINANCIALS.yml` falls back to yfinance's own P/S and P/FCF,
clearly marked as `yahoo` in the output.

### `FINANCIALS.yml` schema

One entry per reported quarter. All monetary values share a single `unit`
(`thousands` / `millions` / `units`). Keep the flat key names exactly — the tool
reads them directly.

```yaml
unit: thousands
quarters:
  - id: q1-2026                 # q<n>-<fiscal-year-label>; the n and year drive fiscal logic
    end_date: 2026-03-31
    revenue: 197940             # standalone quarter, from the income statement
    shares_outstanding: 56657   # point-in-time total (all classes) at quarter end, from
                                # the balance sheet / cover page — NOT the weighted average
    ytd_operating_cf: -20234    # net cash from operations, AS REPORTED (year-to-date)
    ytd_capex_ppe: 858          # purchase of PP&E, year-to-date
    ytd_capex_software: 4197    # capitalization of software dev costs, year-to-date (0/omit if none)
    # --- all of the following are OPTIONAL, entered as low/high ranges ---
    guidance_nq_revenue_low: 168798    # company guidance for the NEXT quarter
    guidance_nq_revenue_high: 180855
    guidance_fy_revenue_low:           # company guidance for the FULL fiscal year
    guidance_fy_revenue_high:
    est_nq_revenue_low:                # my own estimate for the next quarter
    est_nq_revenue_high:
    est_fy_revenue_low: 660000         # my own estimate for the full fiscal year
    est_fy_revenue_high: 710000
```

Conventions and derivations the tool relies on:

- **Standalone vs. YTD.** Revenue is entered standalone (the income statement
  always has a quarterly column). Cash-flow lines are entered exactly as the
  filing reports them — year-to-date — and the tool differences consecutive
  quarters within a fiscal year to recover standalone values, resetting at Q1.
  So every fiscal quarter (Q1–Q4) must be present to build a clean TTM.
- **TTM** = the last four standalone quarters.
- **Two FCF flavours** are shown: `company` = OCF − PP&E capex (matches the FCF
  companies usually print), and `strict` = OCF − PP&E − capitalized software.
- **Negative TTM FCF** prints `n/m` rather than a misleading negative multiple.
- **Forward** multiples use only the most recent quarter's revenue ranges:
  `Fwd P/S (FY)` from the full-year range, and `Fwd-TTM P/S` from the last three
  actual quarters plus the next-quarter range. `guidance_*` (company) and `est_*`
  (your own research) are shown as separate rows — fill in whichever exist.

After entering a new quarter, sanity-check that `ytd_operating_cf − ytd_capex_ppe`
for the full year reproduces the company's own reported FCF figure.

## Adding a New Ticker

1. Create `<TICKER>/business-plan.md`, `<TICKER>/management-team.md`, `<TICKER>/FINANCIALS.yml`, and `<TICKER>/quarters/` to match the structure above
2. Research using SEC filings (8-K press releases are the primary source for earnings), investor relations pages, and earnings call transcripts
3. For quarterly files, include the two most recent reported earnings — match the depth of the existing ODD and BARK files
4. Note fiscal year end date prominently if it differs from December 31
