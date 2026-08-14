# ODD — Similarweb Traffic as a CPA/Demand Proxy

**Why this proxy exists:** ODD reports first-order and repeat-order counts only
quarterly, in percentage terms, and self-reported. The entire 2026 thesis turns
on whether the CPA dislocation with the largest advertising partner (Meta, never
named on the calls) is recovering. Similarweb's public tier gives a free,
independent, *monthly* read on the two core brands — enough to test management's
claims between prints.

**What it measures, and what it does not.** Site visits are a demand/traffic
signal, not revenue: they say nothing about conversion rate or AOV. Similarweb's
public figures are modeled panel estimates with real error bars. Use them for
**direction and month-over-month change within a single brand**, never for levels
and never for cross-brand size comparisons (see the caveat below).

## The two brands are web DTC — app data does not apply

IL MAKIAGE and SpoiledChild are website businesses with no meaningful app funnel,
so Sensor Tower / Appfigures download estimates are a dead end for them.

METHODIQ is the exception: it is genuinely app-centric (weekly photo check-ins,
vision models on 1M+ facial images, clinician messaging), so app download
estimates *would* be a valid proxy there. But METHODIQ is guided to only \$25M of
2026 revenue — roughly 3% of the total. High signal quality, negligible weight.

## Bounce rate is the highest-value field

On the Q1-2026 call, Oran Holtzman's stated evidence that the break was
**technical rather than brand** was *spiking bounce rates* — his read being that
the algorithm was serving ODD's ads to low-quality audiences who landed and left.

Similarweb publishes bounce rate publicly and monthly. That makes it a direct,
free, independent proxy for the exact mechanism management described:

- If the algorithm truly recalibrates → bounce rate should **fall back**.
- If traffic recovers while bounce rate **stays elevated** → ODD is buying junk
  traffic, and the recovery is being purchased rather than earned.

This is the single most useful number on the page.

## Monthly capture spec

URLs: `similarweb.com/website/ilmakiage.com/` and
`similarweb.com/website/spoiledchild.com/`

Capture **the same 8 fields for both brands, once a month**. Similarweb refreshes
roughly mid-month for the prior month; take the reading at a consistent point in
the month so the series is comparable. Record the month the page *says* it covers,
not the date of capture.

**Tier 1 — the four that decide the thesis:**

1. **M/M change (%)** — the headline direction. Watch for it crossing **zero**;
   a smaller negative is not recovery (see level caveat below).
2. **Bounce rate (%)** — the mechanism Holtzman himself named. Rising = junk
   traffic; falling back = genuine recalibration.
3. **Top channel + its %** — *which* channel leads, and by how much. This is the
   paid-vs-earned test. Display/Paid Search leading = bought traffic.
4. **Non-market share + the per-country deltas** — the cleanest external read on
   ad-targeting quality. ODD sells **US / CA / UK / AU / IL**; anything else
   (Philippines, India, Brazil…) is traffic the algorithm should not be buying.
   The Top Countries panel carries a **M/M delta per country**, which solves the
   no-history problem for this one field.
5. **Direct share (%)** — the loyal-core proxy. Rising direct share **while total
   visits fall** = burn-off of the paid layer, not repair.

**Tier 2 — context, cheap to grab:**

6. **Pages/visit** and **avg visit duration** — intent quality; corroborates bounce.
7. **All three ranks + their deltas** — within-brand trajectory only.
8. **Total visits (3mo)** — record it, but treat as soft (see basis caveat).

### Two traps at capture time

> **1. Country deltas are changes in *share*, not volume.** Shares sum to 100%, so
> a country can post a negative delta while its absolute traffic is flat. Convert
> before concluding: **Δ volume ≈ Δ share × Δ total visits**. A junk geography has
> to fall *faster than the total* to count as genuine clean-up.

> **2. Do not record the "organic vs paid" percentages as the channel mix.** That
> is the **keyword** split (search-only) and does not sum against
> Direct/Display/Mail. Conflating the two inverts the conclusion — see the Jul
> 2026 read.

### Capture by eye, not by scraper

Automated text extraction of these pages is **unreliable on exactly the fields
that matter**. Two WebFetch passes over the same July page returned country
deltas with **opposite signs**, both times rendering every country as positive —
which cannot be true, since shares must sum to 100%. The arrow *direction* is
carried by an icon the text extractor drops.

Capture screenshots instead: in Chrome, DevTools → **Cmd+Shift+P** → "Capture
full size screenshot", or "Capture node screenshot" for the **Top Countries** and
**Marketing Channels** panels specifically. Store them under
`ODD/proxydata/YYYY-MM-<host>.png`. Read the numbers off the image by eye and
type them into the tables below.

### The Ranking by Traffic chart — the M/M source

Both brand pages carry a **"Ranking by Traffic" line chart**. Hovering each dot
pops a tooltip reading `Total visits – <Month> <Year>` with the M/M change (and
the visit count on the latest dot). **This is where the monthly series comes
from** — the summary panel's headline only gives the current month.

It holds **three dots (a rolling quarter)**, so there is no deeper history to
mine and **no pre-crash baseline is obtainable**. Hover all three each month; the
oldest rolls off.

**Read screenshots at native resolution.** A full-page PNG (~1265×11900) is
downscaled on open and the small delta figures become illegible. Crop the panel
first, e.g.:

```bash
sips -c 620 640 --cropOffset 2500 620 <page>.png --out /tmp/panel.png
```

## Readings

### IL MAKIAGE

| Month | M/M | Bounce | Top channel | Direct | Pages/visit | Duration | US % | US cat rank | Visits |
|---|---|---|---|---|---|---|---|---|---|
| May 2026 | **−6.97%** | — | — | — | — | — | — | — | — |
| Jun 2026 | **−12.88%** | — | — | — | — | — | — | — | — |
| Jul 2026 | **−10.45%** | 48.9% | Direct 29.04% | 29.04% | 3.92 | 3:42 | 77.5% | #44 | 2.4M |

The Ranking-by-Traffic chart carries **only three dots (May/Jun/Jul)** — there is
no April point and no pre-crash history to recover. The series starts here.

Jul 2026 geography (shares **with M/M share deltas**, read off the page image):

| Country | Share | Δ share | ≈ Δ volume |
|---|---|---|---|
| United States | 77.48% | ↓ 11.85% | ~−21% |
| **Philippines** | 6.72% | ↓ 31.34% | ~−39% |
| **India** | 4.80% | ↓ 29.01% | ~−36% |
| United Kingdom | 3.54% | **↑ 37.94%** | ~+23% |
| Canada | 0.90% | ↓ 23.28% | ~−31% |
| Others | 6.55% | — | — |

Ranks: global 21,734 (↓1,624), US 4,893 (↓733), category #44 (↓8). Keyword split
(*not* channel mix): 32.75% organic / 67.25% paid. Audience 83.5% female, primary
age 45–54. Source image: `ODD/proxydata/2026-07-www.similarweb.com_website_ilmakiage.com_.png`

### SpoiledChild

| Month | M/M | Bounce | Top channel | Direct | Pages/visit | Duration | US % | US cat rank | Visits |
|---|---|---|---|---|---|---|---|---|---|
| May 2026 | **+26.4%** | — | — | — | — | — | — | — | — |
| Jun 2026 | **−32.2%** | — | — | — | — | — | — | — | — |
| Jul 2026 | **−26.6%** | 44.3% | **Display 37.65%** | 3rd | 3.04 | 3:24 | 87.3% | #19 | 5.7M |

**SpoiledChild's break is only ~2 months old.** May was still *growing* at +26.4%;
the collapse starts in June. Indexed off May = 100: **Jun 67.8, Jul 49.8 — roughly
−50% in two months**, a steeper per-month rate than IL MAKIAGE is currently
running. Read off the Ranking-by-Traffic chart tooltips (May/Jun/Jul dots).

Note the timing: SpoiledChild broke in **June, the month after** Holtzman flagged
IL MAKIAGE's 28% CPA improvement in May. That is the lagged-contagion pattern his
own remark implied — remediation effort moves to IL MAKIAGE, SpoiledChild breaks
next. The two brands are not independent bets on the same platform.

Jul 2026 geography (shares **with M/M share deltas**, cropped from the page image):

| Country | Share | Δ share | ≈ Δ volume |
|---|---|---|---|
| **United States** | 87.27% | ↓ 30.54% | **~−49%** |
| Canada | 3.29% | ↑ 18.12% | ~−13% |
| **Brazil** | 1.53% | ↓ 68.83% | ~−77% |
| Germany | 0.70% | ↑ 6.57% | ~−22% |
| United Kingdom | 0.63% | ↓ 0.34% | ~−27% |
| Others | 6.58% | — | — |

Ranks: global 10,735 (↓2,096), US 2,096 (↓561), category #19 (↓8).
Channel order: Display 1st, Mail 2nd, Direct 3rd. Keyword split (*not* channel
mix): 75% organic / 25% paid. Audience 79.2% female, primary age 45–54. Source
image: `ODD/proxydata/2026-07-www.similarweb.com_website_spoiledchild.com_.png`

## Jul 2026 read — the channel data inverts the intuitive story

**SpoiledChild is the more ad-dependent brand.** Its #1 channel is **Display at
37.65%** — bought traffic — with Direct only 3rd. IL MAKIAGE's #1 is **Direct at
29.04%**. The tempting read from the keyword split (SpoiledChild 75% organic vs IL
MAKIAGE 32.75%) says the opposite, but that is search keywords only and excludes
Display and Mail entirely. On actual channel mix, SpoiledChild leans harder on
paid.

That is consistent with SpoiledChild being **earlier in the same damage curve**,
not insulated from it — matching Holtzman's statement that SpoiledChild
remediation only begins once IL MAKIAGE is solved. Its −26.6% is still a single
observation, but it is no longer a surprising one.

**The geography deltas point opposite ways for the two brands — this is the key
Jul 2026 finding.** IL MAKIAGE is shedding *junk* fastest; SpoiledChild is
shedding its *core market* fastest:

| | IL MAKIAGE | SpoiledChild |
|---|---|---|
| US volume | ~−21% | **~−49%** |
| Worst geography | Philippines ~−39%, India ~−36% | Brazil ~−77% (only 1.53% of traffic) |
| Any geography growing? | **UK ~+23%** | none in volume terms |

For IL MAKIAGE the junk geographies fall ~3x faster than the US — the signature of
an algorithm re-targeting toward real markets. **That pattern does not replicate
for SpoiledChild**, where the US is collapsing faster than the total and the only
big cleanup (Brazil) is immaterial at 1.53% of traffic. SpoiledChild is losing the
customers it actually sells to.

**But the IL MAKIAGE geography deltas are the one genuinely encouraging signal.**
Converting
share deltas to volume against a total that fell 10.45%: **Philippines ≈ −39% and
India ≈ −36%, versus the US at ≈ −21%** — the junk geographies are shedding
roughly **3x faster than the core market**, and the **UK is the only country
growing (≈ +23% volume)**. That is the signature of an algorithm re-targeting
toward real markets rather than buying cheap impressions wherever available, and
it is the first evidence in this file pointing at *repair* rather than decay.

Two things temper it. The **US itself is still down ~21% in volume** — the mix is
cleaning up while the core market keeps shrinking, so composition improves as the
level falls. And the UK is only 3.54% of traffic, so its growth is small in
absolute terms. One month, not a trend.

**IL MAKIAGE's geography is still a junk-traffic flag on level.** Philippines
6.72% + India 4.80% ≈ **11.5% of visits from countries where the brand barely
sells**.
ODD's markets are US/CA/UK/AU/IL. Traffic from non-markets is close to a direct
external corroboration of Holtzman's "lower-quality audiences being served with
our ads." SpoiledChild, at 87.3% US, is far cleaner on this axis. **Track the
non-market share monthly — if it falls as the algorithm recalibrates, that is
real evidence of repair.**

Note also IL MAKIAGE's Direct share of 29.04% is *below* the ~44.3% seen in a 2024
reading. If the burn-off hypothesis were the whole story, direct share should be
*rising* as paid traffic dies. It is not — which argues the paid layer is still
substantially present, and the decline therefore has further to run.

**Read as of Jul 2026 — the observed monthly path.** Compounding the tooltip
figures off an April base of 100 (April itself unobserved, so this measures
May→Jul only):

| Month | Index | M/M |
|---|---|---|
| Apr 2026 | 100 | *(no data)* |
| May | 93.0 | −6.97% |
| Jun | 81.1 | −12.88% |
| Jul | 72.6 | −10.45% |

**≈27% lost over May–Jul.** How much preceded May is unknown and not
recoverable — the chart holds only three points.

**The shape is neither collapse nor recovery.** −7.0% → −12.9% → −10.5% is a
brand grinding along a persistent high-single to low-teens monthly decline, with
**May the best month and no trend since**. Flat-ish deterioration. The first
derivative has not crossed zero in any month observed, so the base is still
eroding — but at a far more modest rate than a crisis narrative implies.

**Level still matters, but the mechanism is unresolved.** A candidate reading is
that the *paid* layer burned off leaving a loyal direct/repeat core — matching the
reported **first orders −50% / repeat orders −15%** split. Against it: IL
MAKIAGE's direct share is 29.04%, *below* the ~44.3% seen in 2024, which is the
opposite of what pure burn-off predicts. Unresolved on current data.

**The test that separates burn-off from recovery:** Similarweb publishes
traffic-source splits. A 2024 reading had IL MAKIAGE at ~44.3% direct. If **direct
share rises while total visits keep falling**, it is burn-off, not repair. If
total visits stabilize *and* paid/referral share recovers, that is genuine.
Record the source split alongside the headline each month.

Bounce rate ticked up ~1.3pts over the same span — mildly negative, not a spike.

SpoiledChild's −26.6% has **no prior reading to compare against**, so it is a
single point, not a trend. Do not conclude SpoiledChild is deteriorating faster
than IL MAKIAGE on one observation. It is worth watching precisely because
Holtzman said SpoiledChild remediation only begins *after* IL MAKIAGE is solved —
so a lagged decline there is the predicted pattern, but it is not yet evidenced.

## No historical series is recoverable

There is no pre-crash (2025) baseline available and no way to build one
retroactively:

- Similarweb's **public tier exposes only a rolling ~3-month window** — the
  underlying monthly series is behind the paid product.
- Similarweb **blocks the Wayback Machine**. CDX shows captures for
  `similarweb.com/website/ilmakiage.com/` at `20250711191851` and
  `20260226152724`, but both carry **statuscode 202** and return **zero bytes** —
  they are empty capture stubs, not archived content.

A true historical baseline would require a paid Similarweb seat with historical
export, or a third party such as TickerTrends.

**Therefore the value is prospective, not archaeological.** Record a reading each
month from here. Three or four points before the next print are worth more than
anything reconstructable backwards.

## What is and is not comparable across brands

**Visits — basis now clarified.** The Ranking-by-Traffic tooltip labels the Jul
dot "Total visits – Jul 2026 … 5.7M", so **5.7M is a single month**, not the
trailing-3-month total the summary panel implies. On that basis SpoiledChild
genuinely is the larger traffic property, and its #19 category rank vs IL
MAKIAGE's #44 is probably real rather than an artifact.

This contradicts a 2024 Similarweb competitor listing that put spoiledchild.com at
~1M visits against a much larger IL MAKIAGE base. Two readings: either
SpoiledChild grew enormously since 2024 (plausible — it went from launch to ~\$250M
revenue), or the two figures use different bases. **Verify IL MAKIAGE's own
tooltip basis before trusting any cross-brand ratio**; until then treat levels as
soft and ranks as within-brand trajectory.

**Comparable — percentage composition.** Channel mix, direct share, bounce rate,
geography split and pages/visit are internally normalized, so they can be read
across brands. That is why the Display-vs-Direct contrast above is a legitimate
finding while the 5.7M-vs-2.4M contrast is not.

**Why traffic rank and revenue rank legitimately diverge for these two brands**
(so a mismatch is not evidence of bad data):

- **AOV differs** — IL MAKIAGE sells individually cheap cosmetics; SpoiledChild
  sells higher-ticket supplements/treatments in capsule dispensers.
- **Repurchase mechanics differ** — SpoiledChild is replenishment/subscription
  shaped, so a subscriber generates revenue *without* a monthly site visit. IL
  MAKIAGE needs a browse-and-buy session per purchase.
- **Funnel shape differs** — IL MAKIAGE's shade-match quiz was built as a
  top-of-funnel ad landing target, inflating visits with low-intent arrivals that
  never converted. Its higher bounce rate (48.9% vs 44.3%) corroborates this.

Traffic rank measures sessions, not dollars.

## Related

- Other free proxy: **Meta Ad Library** gives creative velocity (new ads per week
  per brand/geo) and creative longevity (a rough ROAS proxy, since ODD kills
  underperformers fast). Free and brand-separable, but labor-intensive, and it
  measures ODD's own behavior rather than consumer response. Note dollar spend
  ranges are disclosed only for political/issue ads, never for commercial ones.
- Paid channel check: **KeyBanc** runs a weekly credit-card panel on ODD and
  flagged "significant improvement" in May 2026 after April weakness. Notes are
  client-only and surface publicly only in fragments. Caveat: US-centric card
  panels under-capture ODD given its ~17.5% international mix.
