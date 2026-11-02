---
description:
Pre-earnings scenario plan for one or more tickers: bring each ticker up to date, gather the guidance/consensus bar and historical reactions, and write `<TICKER>/earnings/<fy>-q<n>-scenario-plan.md` with bull/base/bear scenarios, likelihoods, triggers, and a blank position-change column for the user.
---

## Arguments

`$ARGUMENTS` — one or more ticker symbols (e.g. `SNAP ABNB`). At least one is required.

Call this skill as: `/plan-earnings <TICKER> [<TICKER> ...]`

---

## Step 0 — Guard

Each ticker must already be onboarded (`<TICKER>/FINANCIALS.yml` exists); otherwise
tell the user to run `/onboard-ticker` first and skip it.

**Skip existing plans.** If `<TICKER>/earnings/<fy>-q<n>-scenario-plan.md` already
exists for the upcoming quarter, report its path and skip that ticker.

## Step 1 — Fiscal label

The upcoming quarter is the one after the newest `id` in `<TICKER>/FINANCIALS.yml`
(`q2-2026` → `q3-2026`; `q4-2026` → `q1-2027`; `q1-fy2027` → `q2-fy2027`). The
filename is **always fiscal year first, then quarter, with no `fy` prefix**:
`2026-q3-scenario-plan.md`, `2027-q2-scenario-plan.md`. This holds for
non-calendar fiscal years too. Same rule for every ticker, no exceptions.

## Step 2 — Update and gather context (one agent per ticker)

With more than one ticker, spawn one `general-purpose` agent per ticker in parallel,
running in the background. With a single ticker, do it inline. Each agent:

1. Runs `/update-ticker <TICKER>` to fill any gap through the latest reported quarter.
   Never commit.
   - `discover-filings` has been seen to rewrite `SOURCES.yml` badly: it adds
     duplicate blocks and drops comments and `_meta` formatting.
   - Check `git diff <TICKER>/SOURCES.yml`. Restore the file and re-add the new
     blocks by hand if needed.
2. Returns a numbers-first brief containing:
   - Confirmed date and session (pre-open / after-close), which sets the reaction bar.
   - The standing guidance for this quarter and the FY, read from `FINANCIALS.yml`
     and the last transcript.
   - Consensus revenue and EPS (web search; name the source and flag single-source
     or stale figures), and the track record of beats against the company's own guide.
   - Key KPIs and themes from the last call, `<TICKER>/notes/`, and the latest
     `<TICKER>/reviews/` ratings.
   - The `uv run check-valuation <TICKER>` TTM multiples and their history.
   - `uv run check-reaction <TICKER> --show 8`: the day-1 and day-5 move per quarter,
     and the pattern by fiscal quarter.
   - Price into the print against the pre-last-earnings close, the post-print
     high/low, and the 52-week range.
   - Any events since the last report: 8-Ks, management changes, litigation,
     regulation, deals.

## Step 3 — Write the plan

Write one file per ticker, kept **small** (about 40 lines), at
`<TICKER>/earnings/<fy>-q<n>-scenario-plan.md`. Follow the repo's markdown rules:
escape `\$` and `\~` everywhere outside code spans.

```markdown
# <TICKER> — Q<n>-<fy> Earnings Scenario Plan

**Report:** <Day YYYY-MM-DD>, <session>; call <time> (reaction bar <Day MM-DD>). <fiscal-year note + calendar months covered>.
**Price into print:** \~\$X (date). Pre-last-earnings close \$Y; <other key level>.
**Last review (date):** fundamentals <rating> / price action <rating>.
**<Optional overlay>:** <macro, regulatory, management-change context>.

## The Bar

| Item | Guide / consensus |
|---|---|
| Revenue | guide …; consensus … |
| <EBITDA / KPI guided> | … |
| EPS | consensus … |
| <FY guide> | … |
| Track record | <beats vs own guide> |

## Scenarios

| Scenario | Likelihood | What it looks like | Historical day-1 analog | Position change (%) |
|---|---|---|---|---|
| **Bull: <name>** | NN% | <concrete numeric triggers> | <range (quarter: move)> | _your call_ |
| **Base: <name>** | NN% | … | … | _your call_ |
| **Bear: <name>** | NN% | … | … | _your call_ |

## Decision Notes

- **Thesis breaks if:** …
- **Thesis confirms if:** …
- <asymmetry / valuation context, drift or fade pattern, key levels, liquidity, same-evening overlap with other holdings>
```

Rules for the scenarios:

- **Three scenarios whose likelihoods sum to 100%.** Ground them in base rates: the
  beat history against the company's own guide, how past reactions split by fiscal
  quarter, and where valuation and positioning sit going in. These are judgment
  estimates; say so in the summary, not in the file.
- **Triggers are concrete numbers** (revenue ≥ \$X, a KPI ≥ Y, a guide midpoint
  against consensus), so the scenario can be called on the night.
- **Day-1 analogs** cite real quarters from `check-reaction`.
- **Position change (%) is always `_your call_`.** Never fill it with a buy/sell or
  trim/add percentage, even if asked: that is personalized investment advice. The
  table supplies the data for the user's own decision.

## Step 4 — Report

List the file paths with each plan's likelihood split in one line. Mention any
repo side-effects (`SOURCES.yml` fixes, new quarters transcribed) and tickers that
still need `/update-ticker` once their results are filed. No further recap.
