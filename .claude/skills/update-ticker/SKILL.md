---
description:
Update an already-onboarded ticker with any earnings reported since it was last worked: discover new SEC filings, fetch their reports/letters/transcripts, transcribe the new quarters into FINANCIALS.yml (with revenue guidance and announce fields), and validate. If no new filings exist, there is nothing to do.
---

## Arguments

`$ARGUMENTS` — the ticker symbol to update (e.g. `NFLX`, `ODD`, `JD`).

Call this skill as: `/update-ticker <TICKER>`

This is the incremental counterpart to `/onboard-ticker`. Onboarding builds the whole
history back to q1-2023; **update fills only the gap** between the newest quarter
already in `FINANCIALS.yml` and today, using the exact same conventions. Re-read the
**Valuation Multiples**, **`report_date` vs. `announce_date`**, and **Adding a New
Ticker** sections of `CLAUDE.md`, and skim the ticker's own `FINANCIALS.yml` header
and last few entries, before transcribing — new quarters must match what's there.

---

## Pipeline

### Step 0 — Guard: must already be onboarded

If `$ARGUMENTS/FINANCIALS.yml` does **not** exist, this ticker was never onboarded.
**Stop** and tell the user to run `/onboard-ticker $ARGUMENTS` instead. Only continue
if it exists.

Read `$ARGUMENTS/FINANCIALS.yml` and find the **latest transcribed quarter** — the
entry with the newest `report_date`:

```bash
uv run python -c "
import yaml
qs = yaml.safe_load(open('$ARGUMENTS/FINANCIALS.yml'))['quarters']
dated = [q for q in qs if q.get('report_date')]
last = max(dated, key=lambda q: str(q['report_date']))
print(last['id'], last['report_date'])
"
```

Note that `id` and `report_date` (call it `LAST_ID` / `LAST_DATE`) — everything newer
than `LAST_DATE` is the gap to fill.

---

### Step 1 — Discover new filings

Re-run discovery windowed to just after the last transcribed quarter. `discover-filings`
**merges** into `SOURCES.yml` (it never clobbers hand-entered keys), so this only adds
blocks for genuinely new filings and downloads their reports.

```bash
uv run discover-filings $ARGUMENTS --since <LAST_DATE>
```

Use `LAST_DATE` (the last `report_date`) as `--since`. It's a filing-date window, so the
already-known latest quarter may reappear — that's fine, it's a no-op merge.

**Decide if there is anything to do.** Compare the quarter labels discover-filings
reports against what `FINANCIALS.yml` already contains. If every discovered quarter is
already transcribed (no `quarter` id newer than `LAST_ID`), **stop here** and report to
the user that `$ARGUMENTS` is already up to date through `LAST_ID` — nothing to do.

Otherwise, the **new quarters** are those discovered filings whose `quarter` id is not
yet in `FINANCIALS.yml`. Everything below operates only on those.

---

### Step 2 — Add letters + transcripts for the new quarters, then fetch

`discover-filings` fills only the `report` key. For each **new** quarter's block in
`SOURCES.yml`, add the guidance sources exactly as onboarding does:

1. On EDGAR, open the company's filings and find the **8-K** (foreign filers: **6-K**)
   for that quarter's announcement, and add its EX-99.1 / EX-99.2 exhibit `.htm` URL as
   `letter:` with the right `letter_type`.
2. Optionally add a `transcript:` URL from the ticker's stockanalysis.com transcripts
   page for the new quarter(s). For foreign filers listed under a home exchange, use the
   `_meta.home_exchange` / `home_ticker` path already recorded in `SOURCES.yml`.

Then download only what's missing (these commands skip files already present):

```bash
uv run fetch-sources $ARGUMENTS
uv run fetch-transcript $ARGUMENTS    # only if you added transcript keys
```

---

### Step 3 — Transcribe the new quarter(s) into FINANCIALS.yml

For each new quarter, read its downloaded `-report.md` and `-letter.md` and add one
entry to `$ARGUMENTS/FINANCIALS.yml`, following the schema in `CLAUDE.md` and matching
the fields and style of the entries already in the file (quarters are oldest-first, so
**append** the new ones at the end). Transcribe from the filings — never Yahoo/derived
numbers. Per quarter fill, as onboarding does:

- **`revenue`** — standalone quarter from the income statement (Q4 = FY total − 9-month
  YTD, with the subtraction shown inline).
- **`shares_outstanding`** — point-in-time total across all classes at quarter end (cover
  page / balance sheet, not the weighted average).
- **`ytd_operating_cf`, `ytd_capex_ppe`**, any other `ytd_capex_*`, **`ytd_sbc`** — as
  reported year-to-date.
- **`cash`, `total_debt`** — balance-sheet snapshot when present.
- **`announce_date`, `announce_session`** — required for `check-reactions`, which does
  not guess; insert both right after `report_date` in the new quarter's block. Fill
  them by hand from the authoritative SEC acceptance timestamp of the earnings filing —
  you already have the accession from Step 2's `letter` block (or the `report` 8-K).
  Read the timestamp from the SEC submissions feed:

  ```bash
  uv run python -c "
  import json, urllib.request
  cik = '<10-digit cik from SOURCES.yml _meta.cik>'
  req = urllib.request.Request(
      f'https://data.sec.gov/submissions/CIK{cik}.json',
      headers={'User-Agent': 'research example@example.com'})
  r = json.load(urllib.request.urlopen(req))['filings']['recent']
  for form, acc, adt, items in zip(r['form'], r['accessionNumber'],
                                   r['acceptanceDateTime'], r['items']):
      if adt >= '2026-07-01':           # narrow to the new quarter's window
          print(adt, form, acc, items)
  "
  ```

  Identify the earnings filing in three tiers: the **item-2.02 8-K** nearest
  `report_date` (±6 days), else an **item-7.01/8.01 8-K** on the exact date (some
  issuers furnish earnings there), else the nearest **6-K/20-F** within ±3 days (foreign
  filers carry no item taxonomy). Then `announce_date` = that filing's acceptance
  **date**, and `announce_session` = its acceptance **time in ET** (the feed timestamp
  is UTC): before 09:30 → `pre-open`, 09:30–16:00 → `intraday`, after 16:00 →
  `after-close`. Eyeball any off-date / 6-K / intraday pick against the actual filing
  (see the `report_date` vs. `announce_date` note in `CLAUDE.md`).
- **Revenue guidance** issued at that report (from the letter): `guidance_nq_revenue_*`,
  `guidance_fy_revenue_*`, or a `*_hint` when withheld/withdrawn. Match the ticker's
  established guidance cadence (next-quarter-only vs. full-year).
- **`est_fy_revenue_*`** — carry the user's own estimate forward only if the file already
  keeps one for recent quarters; otherwise leave it out.

Keep the header comment block intact; extend it only if the new quarter introduces a new
quirk (a split, a new capex line, a currency change).

---

### Step 4 — Validate

```bash
uv run fetch-prices $ARGUMENTS
COLUMNS=300 uv run check-valuation $ARGUMENTS
uv run check-reactions $ARGUMENTS --show 3
```

Check that:

- The new quarter(s) appear as fresh columns in `check-valuation` with a market cap,
  P/S, and P/FCF (no unexpected blanks), and their guidance / forward-P/S rows populate.
- **FCF reconciles** for any newly completed fiscal year: `ytd_operating_cf −
  ytd_capex_ppe` reproduces the company's reported free cash flow.
- `check-reactions` runs without error (proves the announce fields are valid) and the
  new quarter's reaction reads sensibly.

Fix transcription errors and re-run until coherent.

---

### Step 5 — Report

Tell the user what changed: which quarter(s) were added (ids + report dates), the
guidance captured, any announce-field picks that were flagged for review, and paste the
updated `check-valuation` table. If nothing was new (Step 1), just say the ticker is
already up to date through `LAST_ID`.

Do not commit — leave git to the user.
