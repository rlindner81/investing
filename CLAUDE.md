# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

This is an investment research repository. Each ticker gets its own top-level directory containing structured markdown files that document the company's business, management, and quarterly earnings results.

## Directory Structure Per Ticker

```
<TICKER>/
  business-plan.md       — company overview, revenue model, product portfolio, strategy, risks, financial snapshot
  management-team.md     — executive bios, backgrounds, governance notes
  quarters/
    YYYY-MM-DD_<label>-results.md   — one file per reported earnings, date = announcement date
```

## File Conventions

**business-plan.md** covers: company overview (founding, IPO, HQ, fiscal year, latest annual revenue), revenue model, product/brand portfolio, technology or competitive moat, markets and channel mix, growth strategy, key risks, and a financial snapshot table of the most recent fiscal year.

**management-team.md** covers: one section per named executive with background, career prior to the company, role, and notable facts. Ends with corporate governance notes.

**Quarterly results files** are named `YYYY-MM-DD_q#-<period>-results.md` where the date is the announcement date. Each file covers: reported financial metrics (income statement, balance sheet, consensus vs. actual), key operating KPIs, forward guidance, stock reaction, and conference call highlights (CEO statements, CFO statements, key analyst Q&A themes).

## Quarterly File Naming

- Use the announcement date as the filename prefix (ISO 8601: `YYYY-MM-DD`)
- For companies whose fiscal year aligns with the calendar year: `q3-2025-results`, `q4-2025-results`
- For companies with non-calendar fiscal years: include `fy` to avoid ambiguity — e.g., `q4-fy2025-results`, `q3-fy2026-results`
- Always note the fiscal year convention and the calendar period covered at the top of the file

## Earnings Call Transcripts

Transcripts are saved as `YYYY-MM-DD_<quarter>-transcript.md` alongside each results file.

Use `scripts/fetch_transcript.py` to download them:

```bash
# Download all missing transcripts in the repo
python3 scripts/fetch_transcript.py

# Download a specific quarter (auto-discovers URL)
python3 scripts/fetch_transcript.py BARK 2025-06-04

# Supply a URL when auto-discovery fails
python3 scripts/fetch_transcript.py ODD 2026-02-25 --url https://www.insidermonkey.com/blog/...
```

Sources tried in order: The Motley Fool (URL guessed from ticker/date), then Insider Monkey. Motley Fool auto-discovery works for most quarters. When it fails, find the transcript URL manually on insidermonkey.com or seekingalpha.com and pass it with `--url`.

## Adding a New Ticker

1. Create `<TICKER>/business-plan.md`, `<TICKER>/management-team.md`, and `<TICKER>/quarters/` to match the structure above
2. Research using SEC filings (8-K press releases are the primary source for earnings), investor relations pages, and earnings call transcripts
3. For quarterly files, include the two most recent reported earnings — match the depth of the existing ODD and BARK files
4. Note fiscal year end date prominently if it differs from December 31
