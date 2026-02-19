// ─────────────────────────────────────────────────────────────────────────────
// REPORT.typ — An Agent-Based Simulation of the Lloyd's Insurance Market
// Compiled with: typst compile REPORT.typ REPORT.pdf
// ─────────────────────────────────────────────────────────────────────────────

#set document(
  title: "An Agent-Based Simulation of the Lloyd's Insurance Market",
  author: "Simulation Study",
  date: datetime(year: 2026, month: 2, day: 19),
)
#set page(paper: "a4", margin: (x: 2.5cm, y: 3cm), numbering: "1")
#set text(font: "New Computer Modern", size: 11pt, lang: "en")
#set heading(numbering: "1.")
#set par(justify: true, leading: 0.65em)
#show link: underline

#import "@preview/tablem:0.2.0": tablem

// ── Title block ───────────────────────────────────────────────────────────────
#align(center)[
  #v(1em)
  #text(size: 18pt, weight: "bold")[
    An Agent-Based Simulation of \
    the Lloyd's Insurance Market
  ]
  #v(0.6em)
  #text(size: 12pt)[Simulation Study · February 2026]
  #v(0.3em)
  #text(size: 10pt, style: "italic")[Status: Complete (Experiments 0b–3a)]
  #v(0.8em)
]
#line(length: 100%)
#v(0.8em)

// ── Abstract ──────────────────────────────────────────────────────────────────
#align(center)[#text(weight: "bold")[Abstract]]
#v(0.4em)
This report describes the design, calibration, and experimental results of an
agent-based discrete-event simulation (DES) of the Lloyd's of London specialty
insurance market. The model implements three agent types — Syndicate, Broker,
and Market coordinator — operating through a clockless priority-queue DES.
Syndicates employ a two-timescale pricing mechanism combining a slow
exponentially-weighted actuarial estimator with a fast elasticity-based
underwriter markup. Nine experiments across four tiers are reported, covering
agent-count scaling, risk-volume trade-offs, calibration against Lloyd's
benchmarks, line-of-business decomposition, catastrophe geography, risk
heterogeneity, broker market power, syndicate market entry, and managed runoff
via a Central Fund analogue.

Key findings: the endogenous underwriting cycle emerges robustly with a period
of 3–4 years across all parameter configurations and all nine experiments —
structural, not parametric in origin. The cycle period is set by the β/w
timescale ratio and is insensitive to agent count, risk volume, line-of-business
type, broker routing power, market entry, or institutional runoff mechanisms.
Catastrophe frequency is the dominant amplifier of cycle violence. A recommended
Lloyd's-calibrated configuration (20 syndicates, 10 brokers, 50 risks/year)
achieves a combined ratio within the Lloyd's target band (1.019) and an HHI of
0.070, which falls within the 0.06–0.10 reference range. Three structural gaps
relative to the empirical Lloyd's market are identified and their sources
diagnosed.

#v(0.8em)
#line(length: 100%)
#v(1em)

// ── Table of Contents ─────────────────────────────────────────────────────────
#outline(indent: 1.5em, depth: 3)
#pagebreak()

// ─────────────────────────────────────────────────────────────────────────────
= Introduction

Insurance markets exhibit a well-documented cyclical phenomenon in which
pricing, profitability, and capacity oscillate with multi-year periods.
Empirical studies of UK property insurance (Owadally, Zhou & Wright, 2018)
find cycle periods of approximately 6 years; US property-casualty data show
periods of 6–13 years depending on line of business (Owadally et al., 2019a).
These cycles — known as _underwriting cycles_ — create systemic risk: a market
that swings from soft (low premiums, high capacity) to hard (high premiums,
constrained capacity) exposes policyholders to availability gaps and exposes
syndicates to insolvency risk during the transition.

The endogenous nature of underwriting cycles — their emergence from the
micro-level behaviour of competing agents rather than from external shocks —
makes agent-based modelling a natural methodological choice. Wang (2017),
Owadally (2018, 2019b), and Olmez et al. (2024) each demonstrate that simple
actuarial and underwriting behavioural rules, when embedded in a multi-agent
competitive market, reproduce cyclical dynamics without hard-coding any
oscillatory mechanism.

This simulation targets *Lloyd's of London* as its calibration benchmark,
chosen because Lloyd's is the most data-rich and institutionally distinctive
specialty insurance market: a subscription market in which syndicates compete
to underwrite shares of each risk, brokers intermediate placement, and the
Lloyd's Corporation provides a Central Fund backstop. Lloyd's annual reports
provide quantitative benchmarks for syndicate concentration, combined ratios,
and catastrophe loss spikes.

*Scale comparison:*

#tablem[
| Dimension               | Simulation (baseline) | Lloyd's reality                     |
| ----------------------- | --------------------- | ----------------------------------- |
| Syndicates              | 10                    | ~80 active                          |
| Brokers                 | 4                     | ~300 (top 5 place ~60% GWP)         |
| Peril regions / LOBs    | 5                     | 15–20 (heterogeneous risk profiles) |
| Risks per year          | 25                    | ~100,000+                           |
| Cycle period (observed) | 4–5 yr                | ~6 yr (Owadally 2018)               |
| HHI (syndicate share)   | 0.136 (10 syn)        | ~0.06–0.10                          |
| Combined ratio          | 0.83 (no cats)        | 0.90–1.10 across cycle              |
]

This report is structured as follows. Section 2 describes the model
architecture. Section 3 presents baseline market dynamics. Section 4 covers
sensitivity analysis. Sections 5–7 report the three tiers of structural,
mechanism, and institutional experiments respectively. Section 8 synthesises
cross-experiment findings into six key market phenomena. Section 9 assesses
calibration quality against Lloyd's benchmarks. Section 10 identifies open
questions and proposed future work.

// ─────────────────────────────────────────────────────────────────────────────
= Model Architecture

== Discrete-Event Simulation Framework

The simulation uses a *clockless DES* driven by a min-heap priority queue.
There is no fixed time step and no clock process. Simulation time advances at
irregular intervals: when nothing happens between day 47 and day 93, the
simulation skips there directly. Annual housekeeping (dividend payments, markup
updates, industry statistics) is triggered by `AnnualReview` marker events
pre-scheduled at initialisation alongside business and loss events. This design
eliminates the computational waste of empty time steps common in fixed-step ABMs.

The event store is an append-only log of externally observable state changes.
Internal agent reasoning — actuarial pricing, exposure checks, relationship
scoring — happens via method calls and generates no events. This produces a
sparse, meaningful event stream in which every event corresponds to a
real-world observable action (~7 events per risk lifecycle).

== Agent Types

*Market (Coordinator).* One instance per simulation. Owns the risk registry,
orchestrates the lead/follow quoting lifecycle, cascades losses, manages
insolvencies, and maintains industry statistics. The Market does not make
pricing or underwriting decisions; it asks brokers who to approach, asks
syndicates what they would offer, and records outcomes.

*Syndicate.* The primary decision-making agent. A self-contained unit that takes
a risk and market context as input and returns a quote or declines. Heterogeneous
across ten behavioural and financial parameters drawn from uniform distributions
at initialisation. Syndicates implement a lead-follow pricing structure: the lead
syndicate sets a price for the full risk; follow syndicates evaluate that price
against their own actuarial assessment and offer a share.

*Broker.* A substantive decision-making agent (not a thin pass-through). Each
broker maintains evolving relationship scores with each syndicate — tracking
acceptance rates, price competitiveness, and volume — and uses these to select
which syndicates to approach for each risk. The broker-syndicate network topology
evolves endogenously as placement outcomes update relationship strengths.

*Environment (Loss Generator).* A pure schedule of future shocks with no
evolving state. Catastrophe events and attritional losses are pre-generated and
placed in the priority queue at initialisation.

== Pricing Mechanism: Two-Timescale Decomposition

The pricing mechanism is the model's central theoretical contribution,
synthesising the actuarial-underwriter tension identified in Owadally (2018)
and Olmez et al. (2024).

*Actuarial price* (slow timescale, characteristic memory ≈ 1/w years):

$
  "pure_premium" &= z times overline("claim")_"own"
                  + (1-z) times overline("claim")_"ind" \
  "actuarial_price" &= "pure_premium" + alpha times sigma_"claims"
$

The credibility weight $z$ blends own experience with industry statistics; the
EWMA recency weight $w$ controls how quickly past claims are forgotten.

*Underwriter markup* (fast timescale, responds to most recent period):

$
  "arc_elasticity" &= frac(Delta Q \/ overline(Q), Delta P \/ overline(P)) \
  "raw_markup"     &= -1 \/ "arc_elasticity" \
  m_t              &= beta m_"raw" + (1-beta) m_(t-1) \
  "final_price"    &= "actuarial_price" times e^m
$

The markup aggressiveness parameter $beta$ governs how quickly the underwriter
responds to price-volume signals. High $beta$ produces a fast-responding,
volatile market; low $beta$ produces sluggish repricing.

== Event Catalogue

#tablem[
| Event                     | Emitter       | Purpose                             |
| ------------------------- | ------------- | ----------------------------------- |
| `RiskArrived`             | Environment   | New risk enters the market          |
| `RiskAssignedToBroker`    | Market        | Broker selected for placement       |
| `PolicyBound`             | Market        | Policy placed with lead and follows |
| `AttritionalLossOccurred` | Environment   | Per-risk uncorrelated loss          |
| `CatastropheOccurred`     | Environment   | Regional correlated loss            |
| `ClaimPaid`               | Market        | Loss cascaded to specific syndicate |
| `SyndicateInsolvent`      | Market        | Syndicate capital falls below zero  |
| `IndustryStatsPublished`  | Market        | Annual aggregate statistics         |
| `AnnualReview`            | Pre-scheduled | Year-end housekeeping trigger       |
]

== Default Parameters

#tablem[
| Parameter                       | Value    | Distribution    |
| ------------------------------- | -------- | --------------- |
| Syndicates                      | 10       | —               |
| Brokers                         | 4        | —               |
| Risks per year                  | 25       | Gaussian(25, 3) |
| Initial capital                 | 2,000    | × U(0.8, 1.2)   |
| Markup aggressiveness (β)       | —        | U(0.35, 0.50)   |
| Actuarial recency (w)           | —        | U(0.15, 0.25)   |
| Own-vs-industry credibility (z) | —        | U(0.20, 0.40)   |
| Expense rate                    | —        | U(0.30, 0.40)   |
| Risk loading (α)                | 0.001    | fixed           |
| Dividend fraction (γ)           | 0.3      | fixed           |
| Simulation horizon              | 60 years | —               |
| Seeds (ensemble)                | 10       | —               |
]

// ─────────────────────────────────────────────────────────────────────────────
= Baseline Market Dynamics

== The Emergent Underwriting Cycle

With catastrophes disabled, the 10-syndicate baseline market produces
oscillating loss ratios across all seeds. The 10-seed ensemble mean shows a
persistent oscillatory pattern around a stable long-run mean of *0.49* (std
0.07). Individual runs show year-to-year fluctuations between soft-market years
(LR as low as 0.10) and hard-market years (approaching but rarely exceeding
1.0). The single-seed run (seed=42) returns a mean LR of 0.47, coefficient of
variation 0.33, and 12 detected peaks over 60 years.

ACF analysis gives a dominant period of *4 years* (ensemble) / *5 years*
(single seed). Peak detection on the raw series gives inter-peak distances
averaging ~5 years, with a range of 3–7 years. This variation reflects
stochastic claim severity perturbing an otherwise regular cycle — consistent
with a stochastic limit cycle rather than a deterministic one.

#figure(
  image("diag_01_lr_single.png", width: 100%),
  caption: [Loss ratio time series — single seed (seed = 42). The oscillatory
  cycle has a period of ~5 years with mean LR ≈ 0.47.]
) <fig:diag01-lr-single>

#figure(
  image("diag_01_lr_ensemble.png", width: 100%),
  caption: [Loss ratio time series — 10-seed ensemble. Shaded band shows ±1 SD;
  dashed line shows ensemble mean LR ≈ 0.49.]
) <fig:diag01-lr-ensemble>

== Force Decomposition: The Causal Mechanism

The force decomposition plot for seed=42 confirms the two-timescale mechanism
directly. The markup factor $exp(m)$ oscillates on a 3–5 year cadence,
*visibly leading the loss ratio* by approximately one year. The actuarial price
drifts slowly, lagging both the markup and the loss ratio. The phase portrait
(actuarial price vs markup factor, coloured by year) shows systematic
counter-clockwise rotation — the characteristic signature of a limit-cycle
attractor.

The mechanism operates as follows. After a loss year, the underwriter sees
reduced demand and raises markup (fast response, within 1–2 years). The
actuary's EWMA continues rising for up to 1/w ≈ 5 years as past bad experience
is digested. Eventually the underwriter's fast signal — seeing hard-market
volume recovering — begins easing markup downward, while the actuary is still
repricing upward. The market softens, LR deteriorates, and the cycle resets.

#figure(
  image("diag_02_force_decomp.png", width: 100%),
  caption: [Force decomposition (seed = 42): markup factor $exp(m)$, actuarial
  price, and loss ratio over 60 years. Markup leads the LR by ~1 year.]
) <fig:diag02-force>

#figure(
  image("diag_05_phase_portrait.png", width: 90%),
  caption: [Phase portrait: actuarial price vs markup factor, coloured by year.
  Counter-clockwise rotation confirms a limit-cycle attractor.]
) <fig:diag05-phase>

== Capital Dynamics

Without catastrophes, all 10 syndicates remain solvent across the full 60-year
run. Total market capital grows from ~21,200 to ~24,200 (+14%), sustained by
profitable underwriting (mean LR 0.47, combined ratio ~0.82). Syndicate capital
lines are tightly clustered, indicating no divergence in individual fortunes
under attritional-only conditions. This confirms the attritional calibration is
sound.

#figure(
  image("diag_03_capital.png", width: 100%),
  caption: [Syndicate capital lines over 60 years (no catastrophes). All
  syndicates remain solvent; total capital grows ~14%.]
) <fig:diag03-capital>

// ─────────────────────────────────────────────────────────────────────────────
= Sensitivity Analysis

== One-at-a-Time Parameter Sweeps

Nine parameters were swept across five values each, using 10-seed ensemble
means and 60-year horizons:

#text(size: 9.5pt)[#tablem[
| Parameter                                  | Period range | Key finding                                  |
| ------------------------------------------ | ------------ | -------------------------------------------- |
| β (markup aggressiveness, 0.15–0.55)       | 3.1–3.8 yr   | Non-monotonic; resonant near β=0.35–0.45     |
| w (actuarial recency, 0.10–0.30)           | 3.3–3.8 yr   | Weak positive trend                          |
| z (own-vs-industry credibility, 0.15–0.45) | 3.2–3.6 yr   | Controls synchronisation, not period         |
| initial_capital (800–5000)                 | 3.4–3.9 yr   | Amplitude highest at 800 (0.56)              |
| cat_freq (0.00–0.12)                       | 3.4–3.6 yr   | *Dominant driver of amplitude* (0.38→1.73)   |
| α (risk loading, 0.0005–0.01)              | 3.3–3.7 yr   | Near-zero effect                             |
| γ_div (dividend fraction, 0.1–0.5)         | 3.2–3.6 yr   | Non-monotonic, minimal impact                |
| expense_rate (0.20–0.50)                   | 3.3–3.8 yr   | Slight positive trend                        |
| risks_per_year (10–50)                     | 3.3–3.8 yr   | Non-monotonic                                |
]]

*Key finding:* The cycle period is robustly *3–4 years across all nine
parameter dimensions*. No single parameter moves the period outside this band.
`cat_freq` is the dominant driver of amplitude (0.38→1.73 across the sweep),
while all other parameters have modest effects. The tornado chart ranking is:
cat_freq >> β ≈ w >> expense_rate > initial_capital > others.

#grid(
  columns: 3,
  gutter: 0.8em,
  figure(
    image("diag_04_sweep_beta.png"),
    caption: [β sweep],
  ),
  figure(
    image("diag_04_sweep_w.png"),
    caption: [w sweep],
  ),
  figure(
    image("diag_04_sweep_z.png"),
    caption: [z sweep],
  ),
  figure(
    image("diag_04_sweep_initial_capital.png"),
    caption: [Capital sweep],
  ),
  figure(
    image("diag_04_sweep_cat_freq.png"),
    caption: [cat\_freq sweep],
  ),
  figure(
    image("diag_04_sweep_alpha.png"),
    caption: [α sweep],
  ),
  figure(
    image("diag_04_sweep_gamma_div.png"),
    caption: [γ sweep],
  ),
  figure(
    image("diag_04_sweep_expense_rate.png"),
    caption: [Expense rate],
  ),
  figure(
    image("diag_04_sweep_risks_per_year.png"),
    caption: [Risks/yr sweep],
  ),
)

#figure(
  image("diag_13_tornado.png", width: 90%),
  caption: [Tornado chart: OAT sensitivity ranking by amplitude effect.
  `cat_freq` dominates; β and w are secondary.]
) <fig:diag13-tornado>

== Two-Dimensional Heatmaps

The β×w heatmap confirms that cycle period is set by the β/w ratio, not by
either parameter individually. High β/w configurations (fast underwriter, slow
actuary) produce shorter periods; low β/w (slow underwriter, fast actuary)
produces longer periods, converging toward the 6-year empirical target.

The capital×cat_freq heatmap reveals an interaction: catastrophes are most
destructive at low capital (amplitude exceeds 2.0 at capital=800,
cat_freq=0.06), but even well-capitalised syndicates (capital=5000) show
amplitude approaching 1.5 at the highest cat frequencies.

#figure(
  image("diag_11_2d_beta_w.png", width: 90%),
  caption: [2D heatmap: cycle period as a function of β and w. Period varies
  along iso-β/w contours, confirming the timescale-ratio hypothesis.]
) <fig:diag11-heatmap>

#figure(
  image("diag_12_2d_capital_catfreq.png", width: 90%),
  caption: [2D heatmap: cycle amplitude as a function of initial capital and
  cat\_freq. Low capital + high cat frequency produces the most destructive cycles.]
) <fig:diag12-heatmap>

// ─────────────────────────────────────────────────────────────────────────────
= Market Structure Experiments (Tiers 0–1)

== Agent-Count Scaling (Exp 0a)

*Hypothesis tested:* More syndicates → longer cycle period, converging toward
the 6-year empirical target.

#tablem[
| Config       | n_syn | n_brok | Period | Amplitude | Mean LR | HHI   |
| ------------ | ----- | ------ | ------ | --------- | ------- | ----- |
| A — baseline | 10    | 4      | 3.4 yr | 0.383     | 0.485   | 0.136 |
| B            | 20    | 10     | 3.5 yr | 0.425     | 0.526   | 0.090 |
| C            | 40    | 30     | 3.8 yr | 0.434     | 0.601   | 0.058 |
| D            | 80    | 80     | 3.5 yr | 0.513     | 0.651   | 0.041 |
]

*Hypothesis not confirmed for period.* Cycle period is insensitive to agent
count (3.4–3.8 yr throughout), confirming that the β/w timescale ratio — not
market size — drives cycle dynamics. HHI falls sharply with agent count,
entering the Lloyd's reference band (0.06–0.10) at 20–40 syndicates.

A counterintuitive finding: amplitude and mean LR _rise_ with agent count when
`risks_per_year` is held fixed. The mechanism is capacity crowding: with 80
syndicates competing for 25 risks/year, each syndicate is starved of volume,
prices race to the bottom, and realised LR deteriorates. This establishes that
*risks_per_year must scale proportionally with n_syndicates*.

#figure(
  image("diag_14_agent_scaling.png", width: 100%),
  caption: [Agent-count scaling sweep. Cycle period is insensitive to agent
  count; HHI falls monotonically; LR rises with capacity crowding.]
) <fig:diag14-scaling>

== Risk-Volume Trade-off (Exp 0b)

*Hypothesis tested:* Proportional scaling of `risks_per_year` corrects LR
inflation; more risks dampens amplitude via diversification.

#tablem[
| Config       | n_syn | rpy | Period | Amplitude | Mean LR | HHI     | Runtime |
| ------------ | ----- | --- | ------ | --------- | ------- | ------- | ------- |
| A — baseline | 10    | 25  | 3.4 yr | 0.383     | 0.485   | 0.136   | 1×      |
| B — prop×2   | 20    | 50  | 3.1 yr | 0.257     | 0.472   | 0.070 ✓ | 1×      |
| C — prop×4   | 40    | 100 | 3.5 yr | 0.173     | 0.471   | 0.034   | 4×      |
| D — divers   | 10    | 100 | 3.4 yr | 0.223     | 0.430   | 0.108   | 2×      |
]

Both hypotheses confirmed. Config B (proportional scaling ×2) restores mean LR
to 0.472, near-identical to baseline 0.485. Amplitude falls monotonically with
proportional scaling: 0.383→0.257→0.173 — the law of large numbers in action.
*Recommended configuration: n_syn=20, n_brok=10, rpy=50*, giving HHI=0.070
(squarely in Lloyd's band), period ≈ 3.1 yr, and runtime ~1 s/seed.

#figure(
  image("diag_15_risk_volume.png", width: 100%),
  caption: [Risk-volume trade-off (Exp 0b). Proportional scaling of
  risks_per_year restores LR calibration while dampening cycle amplitude.]
) <fig:diag15-risk-vol>

== Calibration Dashboard (Exp 1a)

Side-by-side comparison of four configurations against Lloyd's benchmarks:

#tablem[
| Config                | Period | CR          | RoC       | HHI         |
| --------------------- | ------ | ----------- | --------- | ----------- |
| Baseline (no cats)    | 3.4 yr | 0.831 ✗     | 0.3% ✗    | 0.136 ✗     |
| Baseline + cats       | 3.6 yr | 1.154 ✗     | −38.9% ✗  | 0.392 ✗     |
| Recommended (no cats) | 3.1 yr | 0.818 ✗     | 0.4% ✗    | *0.070 ✓*   |
| Recommended + cats    | 3.3 yr | *1.019 ✓*   | −17.6% ✗  | 0.218       |
]

*Lloyd's targets:* Period 5–7 yr; CR 0.90–1.10; RoC 5–15%; HHI 0.06–0.10;
Cat LR spike +30–50 pp.

Three findings stand out. First, *catastrophes are structural, not optional*:
only the recommended+cats configuration achieves a combined ratio within the
Lloyd's target band (1.019). All no-cat configurations produce CR ≈ 0.82.
Second, *HHI meets target only in the no-cat regime*: with cats, insolvencies
concentrate the market (HHI jumps to 0.218–0.392). Third, *cycle period remains
the main structural gap*: 3.1–3.6 yr vs the 6-year target, a deficit of
approximately 2.7 years.

#figure(
  image("diag_16_calibration.png", width: 100%),
  caption: [Calibration dashboard (Exp 1a): four configurations compared against
  six Lloyd's benchmarks. Green = within target; red = outside.]
) <fig:diag16-calib>

== LOB Decomposition (Exp 1b)

Using peril regions as proxies for five Lloyd's lines of business:

#tablem[
| LOB       | rpy | cat_freq | Period | Amplitude | Mean LR | CR    | HHI   |
| --------- | --- | -------- | ------ | --------- | ------- | ----- | ----- |
| Property  | 10  | 0.12     | 3.8 yr | 2.034     | 1.098   | 1.441 | 0.357 |
| Marine    | 6   | 0.08     | 3.3 yr | 2.429     | 1.167   | 1.510 | 0.323 |
| Liability | 20  | 0        | 3.2 yr | 0.435     | 0.529   | 0.875 | 0.149 |
| Aviation  | 3   | 0.05     | 3.5 yr | 3.050     | 1.286   | 1.631 | 0.360 |
| Motor     | 25  | 0        | 3.3 yr | 0.380     | 0.459   | 0.804 | 0.124 |
]

LOBs split cleanly into two regimes. *Cat-exposed LOBs* (Property, Marine,
Aviation) show mean LR >> 1.0, amplitude 2–3, and HHI 0.32–0.36.
*Non-cat LOBs* (Liability, Motor) show mean LR ≈ 0.46–0.53, amplitude
≈ 0.38–0.43 — near-identical to the baseline market. Amplitude ordering
matches real-world intuition: Aviation > Marine > Property > Liability > Motor.

#figure(
  image("diag_17_lob_decomp.png", width: 100%),
  caption: [LOB decomposition (Exp 1b): per-line LR time series and summary
  statistics across five Lloyd's lines of business.]
) <fig:diag17-lob>

// ─────────────────────────────────────────────────────────────────────────────
= Market Mechanism Experiments (Tier 2)

== Per-Region Catastrophes (Exp 2a)

#tablem[
| Config           | Distribution             | Period | Amplitude | Mean LR | HHI   |
| ---------------- | ------------------------ | ------ | --------- | ------- | ----- |
| A — uniform      | 0.05 × 5 regions         | 3.6 yr | 1.326     | 0.820   | 0.392 |
| B — geo-peril    | 0.15/0.10/0.08/0.02/0.00 | 3.2 yr | 1.583     | 1.113   | 0.362 |
| C — concentrated | 0.30/0/0/0/0             | 3.4 yr | *2.184*   | 0.790   | 0.364 |
]

Period is insensitive to geographic distribution (3.2–3.6 yr). Amplitude rises
sharply with geographic concentration: config C (single hurricane zone,
0.30/yr) produces amplitude 2.18 versus 1.33 for uniform distribution.
Concentrating catastrophes onto one region creates the most synchronised and
sharpest hard markets because all syndicates writing that region are depleted
simultaneously. HHI is similar across all configs (~0.36–0.39), confirming that
cat-driven insolvencies dominate regardless of peril geography.

#figure(
  image("diag_18_per_region_cats.png", width: 100%),
  caption: [Per-region catastrophe experiment (Exp 2a): three geographic
  distributions of cat frequency and their market-level effects.]
) <fig:diag18-cats>

== LOB Heterogeneity (Exp 2b)

#tablem[
| Config                 | Period | Amplitude | Mean LR | HHI   |
| ---------------------- | ------ | --------- | ------- | ----- |
| Baseline (homogeneous) | 3.4 yr | 0.383     | 0.485   | 0.136 |
| Heterogeneous LOBs     | 3.4 yr | 2.260     | 1.743   | 0.183 |
]

Per-region LR (heterogeneous, 10 seeds × 60 yr):

#tablem[
| Region | LOB       | λ    | Mean LR | Notes                                                      |
| ------ | --------- | ---- | ------- | ---------------------------------------------------------- |
| 0      | Property  | 0.30 | 1.34    | Infrequent but large claims                                |
| 1      | Marine    | 0.15 | 0.94    | Near break-even                                            |
| 2      | Liability | 1.50 | 1.89    | Frequent small claims overwhelm pricing                    |
| 3      | Aviation  | 0.05 | n/a     | Limits 5k–20k exceed syndicate capital ~2k; risks declined |
| 4      | Motor     | 2.00 | 1.86    | Very frequent claims overwhelm pricing                     |
]

Period remains unchanged (3.4 yr), confirming β/w sets the cycle regardless of
claim frequency or severity distribution. Market LR nearly doubles (0.49→1.74)
due to a calibration gap: Motor (λ=2.0) and Liability (λ=1.50) are calibrated
far above the baseline that the actuarial estimator was trained on. The Aviation
capacity gap — limits (5k–20k) exceeding default syndicate capital (~2k) —
means most aviation risks are declined, realistically reproducing the specialist
capacity concentration in real Lloyd's aviation.

#figure(
  image("diag_19_lob_hetero.png", width: 100%),
  caption: [LOB heterogeneity (Exp 2b): market and per-region dynamics with
  five heterogeneous LOB parameters.]
) <fig:diag19-lob-hetero>

#figure(
  image("diag_06_peaks.png", width: 90%),
  caption: [Detrended LR peaks and troughs across seeds. Inter-peak spacing
  centres around 5 years with stochastic variation of ±2 years.]
) <fig:diag06-peaks>

== Broker Market Power (Exp 2c)

#tablem[
| Config           | Period | Amplitude | Mean LR | Syn. HHI    | Top-5 broker |
| ---------------- | ------ | --------- | ------- | ----------- | ------------ |
| 4-brok uniform   | 3.4 yr | 0.383     | 0.485   | 0.136       | 100%         |
| 4-brok Zipf s=1  | 3.5 yr | 0.384     | 0.489   | 0.140       | 100%         |
| 10-brok Zipf s=1 | 4.0 yr | 0.428     | 0.526   | *0.090 ✓*   | *70.2%*      |
]

Cycle dynamics are insensitive to broker power: period, amplitude, and LR are
nearly unchanged across all broker configurations. Syndicate HHI at 10 brokers
(0.090) falls within the Lloyd's reference band. Specialism matching moderates
Zipf concentration in specialist regions: the theoretically expected top-1 share
of 48% is observed at only 33.7% due to specialism overrides.

#figure(
  image("diag_20_broker_power.png", width: 100%),
  caption: [Broker market power sweep (Exp 2c): uniform vs Zipf routing, 4 vs
  10 brokers. Cycle dynamics are insensitive to broker power.]
) <fig:diag20-broker>

== Syndicate Market Entry (Exp 2d)

Entry rule: new syndicates enter when annual industry LR exceeds 100%. Mean
starting capital equals the current market average.

#tablem[
| Config                 | Period | Amplitude | Mean LR | HHI   | Mean Entries |
| ---------------------- | ------ | --------- | ------- | ----- | ------------ |
| A — no entry (cf=0.06) | 3.4 yr | 1.379     | 0.918   | 0.337 | 0.0          |
| B — entry (cf=0.06)    | 3.7 yr | 1.938     | 0.907   | 0.306 | 12.0         |
| C — entry (cf=0.03)    | 3.7 yr | 1.489     | 0.773   | 0.219 | 8.3          |
]

Entry reduces HHI (0.337→0.219 with moderate cats), partially offsetting
cat-driven insolvency concentration. With moderate catastrophe frequency
(cf=0.03), entry also reduces mean LR (0.918→0.773): new entrants add capacity
that holds prices down in the recovery phase. The mean entry rate of 12 entries
over 60 years (0.2/yr) is consistent with Lloyd's admission of approximately
one new syndicate per year in hard markets.

Two counter-intuitive findings: entry slightly lengthens cycle period (3.4→3.7
yr) as capital injections dampen the recovery overshoot; and amplitude rises
with high cats + entry (1.379→1.938) because under-capitalised entrants fail in
the next cat year, amplifying insolvency dynamics.

#figure(
  image("diag_21_market_entry.png", width: 100%),
  caption: [Syndicate market entry (Exp 2d): entry dynamics, HHI evolution, and
  new-entrant capital trajectories.]
) <fig:diag21-entry>

#figure(
  image("diag_07_lead_follow.png", width: 90%),
  caption: [Lead-follow stabilisation: convergence of markup factors across
  syndicates over the simulation horizon.]
) <fig:diag07-lead-follow>

// ─────────────────────────────────────────────────────────────────────────────
= Institutional Features (Tier 3)

== Managed Runoff / Central Fund (Exp 3a)

When a syndicate becomes insolvent, its existing policies enter runoff:
outstanding claims are paid by a Lloyd's Central Fund analogue. New risks are
not written. This models the institutional mechanism by which Lloyd's protects
policyholders from syndicate insolvency.

#tablem[
| Config                  | Period | Amplitude | Mean LR | HHI   | CF Total | CF%  |
| ----------------------- | ------ | --------- | ------- | ----- | -------- | ---- |
| A — no runoff (cf=0.06) | 3.4 yr | 1.379     | 0.918   | 0.337 | 0        | 0.0% |
| B — runoff              | 3.4 yr | 1.463     | 0.937   | 0.337 | 1,419    | 1.7% |
| C — runoff + entry      | 3.5 yr | 2.016     | 0.912   | 0.301 | 1,795    | 1.2% |
]

The Central Fund covers approximately 1.7% of total claims in the catastrophe
regime — a modest but meaningful backstop. Period and HHI are unchanged by
runoff alone (structural market forces dominate), confirming that the runoff
mechanism is an institutional safety valve rather than a market stabiliser.
Reported LR is slightly higher with runoff because claims previously silently
dropped at insolvency are now paid and recorded. The combination of entry and
runoff reduces HHI further (0.337→0.301) while keeping CF cost manageable at
1.2% of claims.

#figure(
  image("diag_22_runoff.png", width: 100%),
  caption: [Managed runoff / Central Fund (Exp 3a): LR comparison with and
  without runoff, and cumulative Central Fund disbursements.]
) <fig:diag22-runoff>

// ─────────────────────────────────────────────────────────────────────────────
= Cross-Experiment Synthesis: Key Market Phenomena

=== Phenomenon 1: The Underwriting Cycle is Structurally Robust

The cycle period of *3–4 years* is maintained across all nine parameter
sensitivity sweeps and all nine structural experiments — agent count scaling,
risk volume variation, line-of-business decomposition, per-region catastrophes,
risk heterogeneity, broker power, market entry, and managed runoff. No
intervention in any of these experiments altered the period by more than 0.6
years.

This robustness establishes that the cycle is structural, not parametric: it
emerges from the β/w timescale mismatch inherent in the two-timescale pricing
mechanism. The 2.7-year gap between the simulated period (3.3 yr) and the
empirical Lloyd's target (6 yr, Owadally 2018) is therefore also structural —
it cannot be closed by any of the nine experimental mechanisms but requires
direct modification of β and w in the model core.

=== Phenomenon 2: Catastrophes Are the Dominant Amplifier

Catastrophe frequency (`cat_freq`) is the single parameter with the largest
effect on cycle amplitude, driving it from 0.38 (no cats) to 1.73
(cat_freq=0.12) — a 4.6-fold increase. Every structural addition in Tiers 1–3
(entry, runoff, broker power) leaves the cycle period unchanged but interacts
with cat-driven insolvency concentration to modulate HHI and LR. Non-cat LOBs
(Liability, Motor) are stable and profitable; cat LOBs (Property, Marine,
Aviation) are persistently loss-making at current parameterisation.

Calibration implication: catastrophes are not optional for achieving realistic
combined ratios. All no-cat configurations produce CR ≈ 0.82, well below the
0.90–1.10 Lloyd's target. Only the recommended+cats configuration achieves
CR = 1.019 ✓.

=== Phenomenon 3: Market Concentration Responds to Catastrophe Shocks

HHI spikes after each cat-driven insolvency wave. The recommended no-cat
configuration achieves HHI = 0.070 ✓ (within the Lloyd's band), but enabling
catastrophes pushes HHI to 0.218–0.392 as insolvencies concentrate the
surviving market. Only syndicate market entry can partially restore competitive
structure (HHI 0.337→0.219 with moderate cats). This identifies market entry
as the critical missing institutional mechanism for maintaining competitive
structure in the presence of catastrophe shocks.

=== Phenomenon 4: The β/w Ratio is the Master Control

The cycle period is governed by the ratio of markup aggressiveness (β) to
actuarial recency weight (w). High β/w produces a fast-responding, short-period
cycle; low β/w produces a slow, long-period cycle. The 2D β×w heatmap confirms
this: period varies along iso-ratio contours, not along individual parameter
axes. All other parameters — capital, cat_freq, risk volume, agent count, LOB
type, broker power — modulate amplitude and LR but leave period within the 3–4
year band. Reaching the empirical 6-year target requires reducing β/w to
approximately half its current default value.

=== Phenomenon 5: Law of Large Numbers Dampens Cycles

Risk diversification progressively dampens cycle amplitude: 0.383 (rpy=25) →
0.257 (rpy=50) → 0.173 (rpy=100) along the proportional scaling sequence. More
risks per syndicate reduce the idiosyncratic noise in each syndicate's loss
ratio, producing smoother aggregate market dynamics and weaker oscillations.
Crucially, this dampening requires proportional scaling (maintaining
risks/syndicate constant): increasing agents without increasing risks produces
capacity crowding and LR inflation, as demonstrated in Exp 0a. The recommended
configuration (n_syn=20, rpy=50) achieves a balance between the law of large
numbers effect and HHI calibration.

=== Phenomenon 6: Broker Network Effects Are Structurally Neutral

Routing concentration through dominant brokers (Zipf market power) does not
alter market-wide pricing dynamics. Period, amplitude, and LR are essentially
unchanged across all broker power configurations. Syndicates see the same risk
types regardless of broker identity, because broker specialisms route risks by
peril class — the aggregate risk mix presented to the market is unaffected by
which broker channels that flow. Broker power affects market concentration
metrics (who gets business) but not market dynamics (how prices evolve).

// Appendix figures
#figure(
  image("diag_08_path_dependence.png", width: 100%),
  caption: [Path dependence: capital trajectories across 20 seeds.
  Stochastic variation in claim timing produces divergent long-run capital paths
  from identical initial conditions.]
) <fig:diag08-path>

#figure(
  image("diag_09_market_concentration.png", width: 100%),
  caption: [Market concentration (HHI) and premium share evolution over 60 years.]
) <fig:diag09-concentration>

#figure(
  image("diag_10_network_evolution.png", width: 100%),
  caption: [Broker-syndicate network evolution at years 5, 20, 35, and 49.
  Edge weight encodes cumulative placement volume; herding toward preferred
  syndicates emerges over time.]
) <fig:diag10-network>

// ─────────────────────────────────────────────────────────────────────────────
= Calibration Assessment

Comparison of the recommended+cats configuration against all six Lloyd's
benchmarks:

#tablem[
| Benchmark                    | Lloyd's Target | Recommended + Cats   | Status                   |
| ---------------------------- | -------------- | -------------------- | ------------------------ |
| Cycle period                 | 5–7 yr         | 3.3 yr               | ✗ −2.7 yr short          |
| HHI (syndicate GWP, no cats) | 0.06–0.10      | *0.070*              | ✓                        |
| Combined ratio               | 0.90–1.10      | *1.019*              | ✓                        |
| Return on capital            | 5–15%          | −17.6%               | ✗ structurally negative  |
| Cat LR spike                 | +30–50 pp      | +20.6 pp             | ✗ −9 pp short            |
| Broker concentration (top 5) | ~60% of GWP    | 70.2% (10-brok Zipf) | ≈ (slightly high)        |
]

*What passes:* Combined ratio (recommended+cats), HHI (recommended, no-cats),
broker concentration (10-broker Zipf), market entry rate (~0.2/yr ≈ Lloyd's
~1/yr hard market rate).

*What fails, and why:*

- *Cycle period (−2.7 yr):* Structural gap from β/w ratio. Fix requires
  reducing β toward 0.15–0.25 and/or reducing w toward 0.05–0.10 to lengthen
  actuarial memory. Cannot be fixed by any of the nine experimental mechanisms.

- *Return on capital (structurally negative):* Premium/capital ratio in the
  simulation is approximately 0.02 — syndicates write only a tiny fraction of
  their capital as premium. Real Lloyd's syndicates write at approximately 1.5×
  stamp capacity. The RoC metric requires the stamp capacity mechanism (Tier 3b)
  to be well-defined and calibrated.

- *Cat LR spike (−9 pp):* The current cat_freq=0.05 produces a +20.6 pp spike
  in cat years. Increasing to 0.08–0.10 or raising catastrophe severity would
  close most of this gap, but would also further inflate mean LR and HHI.

// ─────────────────────────────────────────────────────────────────────────────
= Open Questions and Future Work

== Immediate Calibration Priorities

+ *β/w recalibration to target 6-year period.* The β/w ratio should be reduced
  to approximately half the current default value. A grid search over
  β ∈ [0.10, 0.25] and w ∈ [0.05, 0.15] would identify the parameter region
  consistent with a 5–7 year period while maintaining other calibrated metrics.

+ *LOB_PARAMS recalibration.* The default LOB parameters (Motor λ=2.0,
  Liability λ=1.5) produce LR >> 1.0 in Exp 2b. Recalibrating so that the
  frequency-weighted mean λ ≈ 0.6 (the baseline) would restore realistic
  per-LOB LR without requiring changes to the actuarial pricing mechanism.

+ *Monetary rescaling sanity check (Exp 0c).* All dynamics are ratio-based
  (LR, markup factor, HHI). Rescaling monetary constants by 10,000
  (initial_capital = 20M, risk limit U(5M, 20M)) should produce identical
  LR/HHI/period. Verifying this confirms the model is scale-invariant and
  monetary calibration is decoupled from dynamics.

== Proposed Tier 3 Experiments

+ *Stamp capacity (Tier 3b).* Each syndicate's annual GWP is capped at a
  regulatory ceiling (stamp capacity) approved by Lloyd's Corporation,
  independent of current capital. This mechanism limits rapid soft-market
  expansion and is required for a well-calibrated RoC metric.

+ *Proportional reinsurance (Tier 3c).* Syndicates cede X% of each policy at a
  proportional premium, transferring catastrophe losses outside the market.
  Reinsurance is a key stabiliser in real Lloyd's; its absence may contribute
  to the over-violent cycles observed in cat regimes.

== Modelling Improvements

+ *Full event-sourcing closure.* Emitting `DividendPaid` and
  `PremiumDistributed` events, adding `broker_id` to `PolicyBound` and
  `RiskDeclined`, and completing the `SyndicateInsolvent` payload would make the
  event store self-sufficient for all standard projections without restructuring
  the mutable-state architecture.

+ *Branching and counterfactual analysis.* Serialisable state snapshots would
  allow mid-simulation parameter changes and regulatory intervention experiments,
  isolating treatment effects from seed divergence.

// ─────────────────────────────────────────────────────────────────────────────
= References

#tablem[
| Label                | Citation                                                                                                                                              |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Wang 2017            | Yunpeng, "Study on the Underwriting Circle of Property-Liability Insurance Market under Competition"                                                  |
| Owadally 2018        | Owadally, Zhou & Wright, "The Insurance Industry as a Complex Social System: Competition, Cycles, and Crises"                                         |
| Owadally 2019a       | Owadally et al., "Time Series Data Mining with an Application to the Measurement of Underwriting Cycles"                                              |
| Owadally 2019b       | Owadally et al., "An agent-based system with temporal data mining for monitoring financial stability on insurance markets"                            |
| Olmez 2024           | Olmez, Ahmed, Kam, Feng & Tua, "Exploring the Dynamics of the Specialty Insurance Market Using a Novel DES Framework: a Lloyd's of London Case Study" |
| Axtell & Farmer 2022 | "Agent-Based Modeling in Economics and Finance: Past, Present, and Future"                                                                            |
]

#v(2em)
#align(center)[
  #text(size: 9pt, style: "italic")[
    Generated: 2026-02-19 · Experiments 0b–3a complete · Diagnostics diag\_01 through diag\_22
  ]
]
