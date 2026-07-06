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
  SOURCES.yml            — SEC filing URLs and filing types per quarter (see "Downloading Source Files")
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

## Downloading Source Files

`fetch-sources` reads each ticker's `SOURCES.yml` and downloads whatever files are missing. It handles two keys per quarter entry:

- `report` → `YYYY-MM-DD_<quarter>-report.md` — income statement, balance sheet, cash flow from the SEC filing
- `report_type` — filing type for the report URL: `10-Q`, `10-K`, `6-K`, `20-F`
- `letter` → `YYYY-MM-DD_<quarter>-letter.md` — shareholder/earnings letter (8-K exhibit HTML, converted to markdown)
- `letter_type` — filing type for the letter URL: typically `8-K` for US companies, `6-K` or `20-F` for foreign filers

```bash
# Download all missing reports + letters across the whole repo
uv run fetch-sources

# One ticker only
uv run fetch-sources ODD

# One quarter
uv run fetch-sources ODD 2026-06-02

# Supply a report URL directly (bypasses auto-discovery)
uv run fetch-sources ODD 2026-06-02 --url https://www.sec.gov/Archives/edgar/data/.../

# Only one file type
uv run fetch-sources --report-only
uv run fetch-sources --letter-only
```

Source: SEC EDGAR. Add the filing index URL as the `report` key (with its `report_type`) and the 8-K exhibit URL as the `letter` key (with its `letter_type`) in `SOURCES.yml` before running.

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

Source: stockanalysis.com. Find the transcript URL by navigating to `stockanalysis.com/stocks/<ticker>/transcripts/` and copying the link for the relevant quarter. Add it as the `transcript` key in `SOURCES.yml` before running the script.

## Valuation Multiples

Price-to-Sales and Price-to-FCF multiples are computed from the official SEC
figures — **not** from Yahoo's derived numbers — via a per-ticker `FINANCIALS.yml`.
Every fundamental is transcribed from the reports under `<TICKER>/quarters/`; only
prices come from market data — the live price from yfinance for the current
snapshot, and historical closes from `prices/daily/<TICKER>.csv` (see
`fetch-prices`) for each report-date column.

```bash
uv run check-valuation              # all stocks in TICKERS.yml
uv run check-valuation ODD BARK     # specific tickers
uv run check-valuation NVDA         # ad-hoc ticker → yfinance fallback (flagged)
```

A ticker without a `FINANCIALS.yml` falls back to yfinance's own P/S and P/FCF,
clearly marked as `yahoo` in the output.

### Output layout

One vertical table per ticker. Metrics are rows; columns run newest→oldest:
the current (partial) fiscal year's quarters, then each **complete fiscal year**
as an aggregate column (highlighted) followed by its four quarters. A **TTM**
column on the left holds the current live snapshot. Example column order:

```
TTM | q1-2026 | FY-2025 | q4-2025 | q3-2025 | q2-2025 | q1-2025 | FY-2024 | q4-2024 | ...
```

Rows are grouped: fundamentals (revenue, operating CF, capex, FCF, shares),
then the guidance/estimate revenue rows, then per-column valuation
(ref date, ref price, market cap, P/S, P/FCF), then the forward P/S rows.

### `FINANCIALS.yml` schema

One entry per reported quarter, oldest first. All monetary values share a single
`unit` (`thousands` / `millions` / `units`). Keep the flat key names exactly.

An optional `currency` field names the ISO code of the filing currency (default:
`USD`). When set to a non-USD code (e.g. `CNY` for Chinese Yuan, `HKD` for Hong
Kong Dollar), `check-valuation` fetches the live `USD{currency}=X` rate from
yfinance and converts all fundamentals to USD at that rate. Prices are always
sourced from NASDAQ/NYSE in USD, so only the fundamental values need conversion.
The alias `RMB` is accepted as a synonym for `CNY`.

```yaml
unit: thousands
currency: USD           # optional; omit for USD-denominated tickers
quarters:
  - id: q1-2026                 # q<n>-<fiscal-year-label>; n and year drive the fiscal logic
    end_date: 2026-03-31
    report_date: 2026-06-02     # announcement date — the close that day is the price reference
    revenue: 197940             # standalone quarter, from the income statement
    shares_outstanding: 56657   # point-in-time total (all classes) at quarter end, from the
                                # balance sheet / cover page — NOT the weighted average
    ytd_operating_cf: -20234    # net cash from operations, AS REPORTED (year-to-date)
    ytd_capex_ppe: 858          # PP&E purchases, YTD — the ONLY capex line in `company` FCF
    ytd_capex_software: 4197    # any other capitalized spend, YTD; add more ytd_capex_* as needed
    ytd_sbc: 8101               # stock-based comp, YTD (cash flow add-back). Optional; when present,
                                # check-valuation adds SBC rows, FCF-after-SBC, and P/FCF-after-SBC.
    # --- balance sheet snapshot at quarter end (optional) ---
    cash: 312000                  # cash and cash equivalents
    total_debt: 0                 # total financial debt (short-term + long-term)
    # --- guidance ISSUED AT THIS report (all optional, low/high ranges) ---
    guidance_nq_revenue_low: 168798       # next-quarter revenue
    guidance_nq_revenue_high: 180855
    guidance_fy_revenue_low: 806000       # full-year revenue
    guidance_fy_revenue_high: 809000
    guidance_fy_hint: withdrawn           # ...OR a short hint (why none: "withdrawn"/"withheld")
    guidance_nq_hint: withheld            #    same idea for the next-quarter row
    # --- my own full-year estimate (optional, low/high range) ---
    est_fy_revenue_low: 660000
    est_fy_revenue_high: 710000
```

Conventions and derivations the tool relies on:

- **Standalone vs. YTD.** Revenue is entered standalone (the income statement
  always has a quarterly column). Cash-flow lines are entered exactly as the
  filing reports them — year-to-date — and the tool differences consecutive
  quarters within a fiscal year to recover standalone values, resetting at Q1.
- **Per-column valuation.** Each dated quarter column is a point-in-time
  snapshot: market cap = the close on `report_date` × that quarter's
  `shares_outstanding`; P/S and P/FCF use the trailing-twelve-months ending that
  quarter. The **TTM** column uses the live price instead. FY-aggregate columns
  blank the valuation rows (they'd just duplicate Q4).
- **FCF flavours**: `company` = OCF − `ytd_capex_ppe` (matches the FCF companies
  usually print); `strict ex SBC` = OCF − *every* `ytd_capex_*` line − SBC
  (the most conservative view: removes all capitalized spend plus the dilutive
  non-cash comp). `company` FCF is never touched by SBC — SBC only folds into
  the strict variant. When neither extra capex lines nor SBC data are present,
  the two rows collapse to a single `FCF` / `P / FCF`.
- **Guidance rows** show what was issued at each report: `NQ Rev guidance` (next
  quarter) and `FY Rev guidance` (full year, with the raise trajectory), plus
  `FY Rev estimate` (your own). When guidance wasn't given, a `*_hint` prints in
  its place (e.g. `withdrawn`, `withheld`). Read guidance-vs-actual across
  adjacent columns — a quarter's `NQ Rev guidance` sits one column left of the
  actual it predicted.
- **Forward P/S rows** divide each column's market cap by a forward sales base:
  `FW P/S NQ guidance` (last 3 actual quarters + the guided next quarter →
  forward TTM), `FW P/S FY guidance` and `FW P/S FY estimate` (full-year
  revenue). The TTM column uses today's price against the latest of each.
- **FY guidance is per-quarter.** `guidance_fy_revenue_*` is the full-year
  guidance as it stood at *that* report, so the row shows the raise trajectory;
  the FY-aggregate column takes the last interim guide. `FW P/S guidance` /
  `FW P/S estimate` divide each column's market cap by its own guidance/estimate
  revenue (and the TTM column by the latest, at today's price).
- **Net cash** = `cash − total_debt` (negative = net debt, shown in red). Both
  fields are optional point-in-time balance sheet values. The row appears between
  Market cap and P/S in the valuation section; it is blank for FY-aggregate columns.
- **History-only quarters.** Quarters kept purely to give later quarters a valid
  TTM base (e.g. prior-year quarters reconstructed from comparatives) can omit
  `report_date` and `shares_outstanding`; they still show fundamentals but their
  valuation rows stay blank.

After entering a new quarter, sanity-check that `ytd_operating_cf − ytd_capex_ppe`
for the full year reproduces the company's own reported FCF figure.

## Adding a New Ticker

1. Create `<TICKER>/business-plan.md`, `<TICKER>/management-team.md`, `<TICKER>/FINANCIALS.yml`, `<TICKER>/SOURCES.yml`, and `<TICKER>/quarters/` to match the structure above
2. Research using SEC filings (8-K press releases are the primary source for earnings), investor relations pages, and earnings call transcripts
3. For quarterly files, include the two most recent reported earnings — match the depth of the existing ODD and BARK files
4. Note fiscal year end date prominently if it differs from December 31
