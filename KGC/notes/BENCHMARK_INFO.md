# KGC — Benchmark Reference (GLD + GDX)

KGC's benchmarks are `[SPY, GDX, GLD]` (in the repo-root `TICKERS.yml`). GDX and
GLD are **not rival benchmarks** — they are a decomposition of the same thing:

- **GLD** — spot physical gold bullion. The commodity itself.
- **GDX** — gold miners. The commodity *plus* the mining industry's operational,
  cost, leverage, and financing noise.

The gap between them (**GDX − GLD**) is therefore "how the miner group is being
priced *as a business* this week," stripped of the metal.

## The thesis being tested

KGC is a low-cost, financially clean, operationally stable mine (see
[AISC.md](AISC.md) for the cost margin). The claim is that such a miner should
track **GLD more closely than a generic miner does** — more of its move is just
the gold price, less is company/sector equity noise. Put differently: KGC should
hug GLD more tightly than GDX does.

This is a **hypothesis to observe over many quarters, not an asserted fact.** A
single week's KGC-vs-GLD gap proves nothing — do **not** read one week where KGC
outruns GLD as "KGC amplifies gold." The prior here is the opposite: KGC ≈ gold.

## How to read the two benchmarks

They answer different questions, so keep both:

- **GLD** tells you what gold did.
- **GDX** tells you what the miner group's *equity* risk did.

When KGC diverges from GLD, check GDX to see whether the divergence is
KGC-specific or sector-wide. If KGC moves with GDX but away from GLD, that's the
miner complex being re-rated as a business; if KGC moves with GLD while GDX
diverges, that's KGC behaving like the metal — which is what the thesis predicts.
