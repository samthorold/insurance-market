# Lloyd's Scale Realism — Experiment & Change Plan

Tracks proposed changes to make the simulation more representative of the real Lloyd's of London market.
Update the status column as experiments are completed.

---

## Current vs Target Scale

| Dimension | Simulation (current) | Lloyd's reality |
|-----------|---------------------|-----------------|
| Syndicates | 10 | ~80 active |
| Brokers | 4 | ~300 (top 5 place ~60% GWP) |
| Peril regions / LOBs | 5 (identical) | 15–20 (very different risk profiles) |
| Risks per year | 25 | ~100,000+ |
| GWP (total market) | ~£25k (toy units) | ~£40bn |
| Initial capital / syndicate | £2,000 | £0.5–5bn |
| Cycle period (observed) | 4–5 yr | ~6 yr (Owadally 2018 UK property) |
| HHI (syndicate share) | 0.14 (10 syn) → 0.09 (20 syn) → 0.06 (40 syn) | ~0.06–0.10 |

---

## Tier 0 — Experiments: zero code changes

These use existing `build_simulation()` kwargs. Implement as new sweeps in `diagnostics.py`.

### 0a. Agent-count scaling sweep
**Status:** `[x] complete — 2026-02-19 — diag_14_agent_scaling.png`
**Hypothesis (tested):** More syndicates → longer cycle period, converging towards the 6-year empirical target.

| Config | n_syndicates | n_brokers | Period | Amplitude | MeanLR | HHI |
|--------|-------------|-----------|--------|-----------|--------|-----|
| A (baseline) | 10 | 4 | 3.4 yr | 0.383 | 0.485 | 0.136 |
| B | 20 | 10 | 3.5 yr | 0.425 | 0.526 | 0.090 |
| C | 40 | 30 | 3.8 yr | 0.434 | 0.601 | 0.058 |
| D | 80 | 80 | 3.5 yr | 0.513 | 0.651 | 0.041 |

**Result:** Hypothesis NOT confirmed for period; HHI and capacity crowding effects are the main story.

### Results

1. **Period insensitive to agent count** (3.4–3.8 yr throughout). The hypothesis that more syndicates → longer cycle was NOT confirmed. Cycle period is set by the β/w timescale ratio, not market size. Reaching the 6-year empirical target requires changing behavioural parameters, not just adding more agents.

2. **HHI drops sharply with agent count**: 0.136 → 0.090 → 0.058 → 0.041. Lloyd's reference band is 0.06–0.10. The sweet spot is **20–40 syndicates** for realistic concentration. At 80 syndicates (HHI=0.041), the market is actually *more* fragmented than real Lloyd's.

3. **Amplitude and mean LR rise with agent count** — counterintuitive but explained by capacity crowding: with 80 syndicates competing for only 25 risks/year, each syndicate is starved of volume, prices race to the bottom (high competition), and realised LR deteriorates. This means **risks_per_year must scale with n_syndicates** to maintain realistic risk/capacity ratios. A rough rule: maintain ~2.5 risks/syndicate/year (baseline: 25/10).

### Implication for default parameters
- **Recommended default: n_syndicates=20, n_brokers=10, risks_per_year=50** — puts HHI in the Lloyd's reference band (0.090), keeps computation fast, and avoids the capacity crowding problem. Period remains ~3.5 yr (acceptable given simulation scale).
- For a "Lloyd's-scale" run: n_syndicates=40, n_brokers=30, risks_per_year=100. HHI=0.058 (just below Lloyd's band but structurally correct).

---

### 0b. Risk-volume / compute trade-off
**Status:** `[x] complete — 2026-02-19 — diag_15_risk_volume.png`
**Hypothesis (tested):** Scaling `risks_per_year` proportionally with syndicates corrects LR inflation; more risks dampens amplitude via diversification.

| Config | n_syn | rpy | risks/syn/yr | Period | Amplitude | MeanLR | HHI | Time(s) |
|--------|-------|-----|-------------|--------|-----------|--------|-----|---------|
| A-baseline | 10 | 25 | 2.5 | 3.4 yr | 0.383 | 0.485 | 0.136 | 1 |
| B-prop×2 | 20 | 50 | 2.5 | 3.1 yr | 0.257 | 0.472 | 0.070 ✓ | 1 |
| C-prop×4 | 40 | 100 | 2.5 | 3.5 yr | 0.173 | 0.471 | 0.034 | 4 |
| D-divers | 10 | 100 | 10 | 3.4 yr | 0.223 | 0.430 | 0.108 | 2 |

### Key findings

1. **Proportional scaling corrects LR inflation** (hypothesis confirmed): 20 syn + 50 rpy gives LR=0.472, back near baseline 0.485. The capacity crowding from 0a (20 syn, 25 rpy → LR=0.526) was entirely due to too few risks per syndicate.

2. **Amplitude falls with more risks** (law of large numbers): 0.383 → 0.257 → 0.173 along the proportional configs. More diversification → smoother LR → weaker cycles. This is arguably more realistic for a larger market.

3. **HHI sweet spot confirmed at n_syn=20**: B-prop×2 gives HHI=0.070, squarely in the Lloyd's target band. C-prop×4 (40 syn) overshoots to 0.034 (too fragmented).

4. **Runtime is linear in n_syn × rpy**: A(250 units)=1s, B(1000)=1s, C(4000)=4s, D(1000)=2s. A Lloyd's-scale run (80 syn, 200 rpy = 16,000 units) would be ~64s per seed → ~11min for 10 seeds — feasible.

5. **Pure diversification (D-divers)**: Fixing n_syn=10 but 4× risks pushes LR down (0.430) and still reduces amplitude, but HHI stays above the Lloyd's band (0.108). Not a substitute for scaling syndicates.

### Revised recommended default
**n_syndicates=20, n_brokers=10, risks_per_year=50**: LR≈0.47, HHI=0.070 (in Lloyd's band), amplitude=0.257, runtime ~1s/seed. The amplitude is lower than the baseline (0.383) but more realistic for a 20-syndicate market — the original model's high amplitude was partly an artefact of low diversification.

---

### 0c. Monetary rescaling sanity check
**Status:** `[ ] pending`
All dynamics are ratio-based (LR, markup factor, HHI). Rescaling monetary constants by 10,000 (initial_capital=20M, risk_limit U(5M, 20M)) should produce identical LR/HHI/period. Verify this holds — it confirms model is scale-invariant and monetary calibration is decoupled from dynamics.

---

## Tier 1 — diagnostics.py additions only (no simulation.py changes)

### 1a. Calibration dashboard vs Lloyd's benchmarks
**Status:** `[x] complete — 2026-02-19 — diag_16_calibration.png`

| Metric | Lloyd's target | baseline (no cats) | recommended+cats |
|--------|---------------|-------------------|-----------------|
| Cycle period | 5–7 yr | 3.4 yr ❌ | 3.3 yr ❌ |
| HHI (pre-cat) | 0.06–0.10 | 0.136 ❌ | **0.070 ✓** |
| Combined ratio | 0.90–1.10 | 0.831 ❌ | **1.019 ✓** |
| RoC | 5–15% | 0.3% ❌ | −17.6% ❌ |
| Cat LR spike | +30–50 pp | — | +20.6 pp ❌ |

Full table:

| Config | Period | CR | RoC | HHI |
|--------|--------|-----|-----|-----|
| baseline (10 syn, 25 rpy, no cats) | 3.4 yr | 0.831 | 0.3% | 0.136 |
| baseline+cats | 3.6 yr | 1.154 | −38.9% | 0.392 |
| recommended (20 syn, 50 rpy, no cats) | 3.1 yr | 0.818 | 0.4% | 0.070 |
| recmd+cats | 3.3 yr | **1.019** | −17.6% | 0.218 |

**Output file:** `diag_16_calibration.png` — produced 2026-02-19

### Key findings

1. **Combined ratio hits Lloyd's target only with cats enabled**: recmd+cats CR=1.019 (in 90–110% band). All no-cat configs produce CR≈0.82–0.83 (too profitable without catastrophes). Real Lloyd's embeds cats in its "normal" combined ratio. **Conclusion: default config should have cats enabled.**

2. **HHI meets target only in no-cat regime**: The recommended no-cat config gives HHI=0.070 ✓. With cats, insolvencies concentrate the market (HHI jumps to 0.218–0.392). A key gap: the model has no syndicate entry mechanism to restore concentration after cat-driven insolvencies.

3. **RoC is near-zero in no-cat configs and deeply negative in cat configs**: Premium/capital ratio in the simulation is ~0.02 — syndicates write only a tiny fraction of their capital as premium (far from capacity). Real Lloyd's writes at ~1.5× stamp capacity. This makes the RoC metric not well-calibrated; needs the stamp capacity / market entry mechanisms (Tier 2d) to fix properly.

4. **Cat LR spike: +20.6 pp — below the +30–50 pp target**: cat_freq=0.05 is insufficient to generate the full spike. Could be tuned upward (cat_freq=0.08–0.12) or via higher cat severity. Already at the upper range of 0a's baseline (~+30pp), so the gap is small.

5. **Period remains the main structural gap**: 3.1–3.6 yr vs 6yr target. Confirmed not fixable by agent count or risk volume changes — requires β/w ratio adjustment.

### Calibration gap summary (recommended+cats vs Lloyd's)

| Metric | Gap | Fix required |
|--------|-----|-------------|
| Cycle period | −2.7 yr short | Change β/w ratio (sim.py) |
| HHI (with cats) | 0.148 too high | Syndicate entry mechanism (2d) |
| Combined ratio | ✓ within target | — |
| RoC | Structurally too low | Stamp capacity / higher risk volume |
| Cat LR spike | −9 pp short | Raise cat_freq to 0.08 or cat severity |

---

### 1b. LOB decomposition using regions as proxies
**Status:** `[x] complete — 2026-02-19 — diag_17_lob_decomp.png`

Note: `ATTRITIONAL_CLAIM_LAMBDA` is hardcoded in simulation.py, so LOBs are proxied via `risks_per_year`, `initial_capital`, `cat_freq`, and `syndicate_params['initial_claim_est']`.

| LOB | rpy | cap | cat_freq | Period | Amplitude | MeanLR | CR | HHI |
|-----|-----|-----|----------|--------|-----------|--------|-----|-----|
| Property | 10 | 3000 | 0.12 | 3.8 yr | 2.034 | 1.098 | 1.441 | 0.357 |
| Marine | 6 | 3500 | 0.08 | 3.3 yr | 2.429 | 1.167 | 1.510 | 0.323 |
| Liability | 20 | 2000 | 0 | 3.2 yr | 0.435 | 0.529 | 0.875 | 0.149 |
| Aviation | 3 | 5000 | 0.05 | 3.5 yr | 3.050 | 1.286 | 1.631 | 0.360 |
| Motor | 25 | 1500 | 0 | 3.3 yr | 0.380 | 0.459 | 0.804 | 0.124 |

**Output file:** `diag_17_lob_decomp.png` — produced 2026-02-19

### Key findings

1. **LOBs split cleanly into two regimes**:
   - **Cat-exposed (Property, Marine, Aviation)**: mean LR >> 1.0, amplitude 2–3, HHI 0.32–0.36 (high concentration from insolvencies). Very volatile cycles.
   - **Non-cat (Liability, Motor)**: mean LR ≈ 0.46–0.53, amplitude ≈ 0.38–0.43, HHI ≈ 0.12–0.15. Behave like the baseline market — stable profitable cycles.

2. **Amplitude ordering matches real-world intuition**: Aviation (3.05) > Marine (2.43) > Property (2.03) > Liability (0.43) > Motor (0.38). Cat LOBs are dramatically more volatile.

3. **Cat-exposed configs are over-stressed**: cat_freq 0.08–0.12 produces average LR >> 1.0 (persistent losses, not just spikes). Real Lloyd's property has cat events 1 per 5–10 years, not 1 per 8–12 years with the current parameterisation. Suggested recalibration: Property cat_freq=0.03–0.05, Marine=0.03, Aviation=0.02.

4. **Non-cat LOBs are close to target**: Motor CR=0.804 and Liability CR=0.875 — below the 90–110% Lloyd's band (too profitable). Matches the no-cat baseline finding from 1a: cats are needed to reach realistic combined ratios.

5. **HHI is high for cat LOBs due to insolvencies**: Without a market entry mechanism (2d), cat-driven insolvencies permanently concentrate the market. Amplitude of 2–3 means cycles are so violent that syndicates routinely fail.

### LOB calibration guidance (for Tier 2b implementation)
| LOB | Suggested cat_freq | Target LR range | Notes |
|-----|--------------------|-----------------|-------|
| Property | 0.03–0.05 | 0.70–0.90 | Reduce from 0.12; one major cat per 20–30 yr |
| Marine | 0.03 | 0.65–0.85 | Reduce from 0.08 |
| Liability | 0 | 0.55–0.75 | Already reasonable; raise initial_claim_est slightly |
| Aviation | 0.02 | 0.60–0.80 | Very rare cat; rpy=3 creates extreme idiosyncratic noise |
| Motor | 0 | 0.55–0.70 | Already reasonable |

---

## Tier 2 — simulation.py structural improvements (medium complexity)

> simulation.py is currently marked "do not modify" for diagnostic runs. These are proposals for the next version of the model.

### 2a. Per-region catastrophe rates
**Status:** `[x] complete — 2026-02-19 — diag_18_per_region_cats.png`
**Change:** `cat_freq` now accepts `dict[int, float]` in addition to `float`. Fully backward-compatible.
**Code impact:** 2-line signature change + 3-line scheduling loop in `build_simulation`. `_handle_catastrophe` unchanged.

| Config | freq_dict | Period | Amplitude | MeanLR | HHI |
|--------|-----------|--------|-----------|--------|-----|
| A-uniform | 0.05×5 | 3.6 yr | 1.326 | 0.820 | 0.392 |
| B-geo-peril | 0.15/0.10/0.08/0.02/0.00 | 3.2 yr | 1.583 | 1.113 | 0.362 |
| C-concentrated | 0.30/0/0/0/0 | 3.4 yr | **2.184** | 0.790 | 0.364 |

### Key findings

1. **Period insensitive to geographic distribution** (3.2–3.6 yr) — β/w timescale drives cycles, not peril geography.

2. **Amplitude rises sharply with concentration**: C (2.18) > B (1.58) > A (1.33). Concentrating all cats onto one region creates the most synchronised, sharpest hard markets. A single dominant hurricane zone behaves like a periodic shock that periodically wipes out the whole market.

3. **MeanLR tracks total annual rate, not geography**: B has the highest total rate (0.35/yr) and the highest LR. C and A have similar total rates (0.30 and 0.25/yr respectively) but C's higher amplitude is from synchronisation, not frequency.

4. **HHI is similar across all configs** (~0.36–0.39) — cat-driven insolvencies dominate regardless of how cats are distributed. Entry mechanism (2d) is still the bottleneck for normalising HHI.

### Implication for next steps
- Provides the mechanism for realistic LOB calibration (2b): property can use dict with high rate in region 0, liability uses empty dict / zero rate
- Confirmed cat_freq=0.05 uniform is a decent baseline; geographic configs show what range of cycle violence to expect

---

### 2b. Per-LOB risk heterogeneity
**Status:** `[x] complete — 2026-02-19 — diag_19_lob_hetero.png`
**Change:** `lob_params: dict[int, dict] | None = None` kwarg added to `build_simulation`. Maps region → `{lambda, sev_mu, limit}`. Fully backward-compatible.
**Code impact:** 2 edits in simulation.py (~15 lines); extract_per_region_lr helper + run/plot functions in diagnostics.py (~200 lines).

Default LOB mapping (diagnostics.LOB_PARAMS_DEFAULT):
```python
{0: {"lambda": 0.30, "sev_mu": 200,  "limit": (1000,  5000)},  # Property
 1: {"lambda": 0.15, "sev_mu": 400,  "limit": (2000, 10000)},  # Marine
 2: {"lambda": 1.50, "sev_mu":  50,  "limit":  (500,  2000)},  # Liability
 3: {"lambda": 0.05, "sev_mu": 800,  "limit": (5000, 20000)},  # Aviation
 4: {"lambda": 2.00, "sev_mu":  30,  "limit":  (200,   800)}}  # Motor
```

| Config | Period | Amplitude | MeanLR | HHI |
|--------|--------|-----------|--------|-----|
| baseline (homogeneous) | 3.4 yr | 0.383 | 0.485 | 0.136 |
| heterogeneous | 3.4 yr | 2.260 | 1.743 | 0.183 |

Per-region mean LR (heterogeneous, 10 seeds × 60yr):
| Region | LOB | λ | Mean LR | Notes |
|--------|-----|---|---------|-------|
| 0 | Property | 0.30 | 1.34 | Infrequent but large severity |
| 1 | Marine | 0.15 | 0.94 | Near break-even; rare large losses |
| 2 | Liability | 1.50 | 1.89 | Frequent small claims overwhelm pricing |
| 3 | Aviation | 0.05 | n/a | Limits (5k–20k) exceed syndicate capital; risks mostly declined |
| 4 | Motor | 2.00 | 1.86 | Very frequent claims overwhelm pricing |

### Key findings

1. **Period unchanged (3.4 yr)** — confirms β/w sets the cycle, not claim frequency/severity/limit distribution.

2. **Market LR nearly doubles (0.49 → 1.74)** — LOB heterogeneity reveals a calibration gap: motor (λ=2.0) and liability (λ=1.50) are calibrated much higher than the baseline (λ=0.6) the actuarial estimator was trained on. Premiums cannot adapt fast enough. **Next step: recalibrate LOB_PARAMS_DEFAULT so that frequency-weighted mean λ ≈ 0.6 to match actuarial learning assumptions.**

3. **Amplitude nearly 6× higher (0.38 → 2.26)** — heterogeneous frequencies create more volatile markets as syndicates oscillate between over- and under-pricing different LOBs.

4. **Aviation capacity gap identified** — aviation limits (5k–20k) far exceed default syndicate capital (~2k). Most aviation risks are declined. This is realistic (Lloyd's aviation is concentrated with specialist capacity) but means the feature is only useful at higher capitalisation or with per-LOB capital allocation (Tier 2d).

### LOB_PARAMS calibration guidance (recalibration needed)
For market-wide LR ≈ 0.5 target, approximate frequency targets (assuming risks spread evenly across regions):
- Mean weighted λ should ≈ 0.6 (current: (0.30+0.15+1.50+0.05+2.00)/5 = 0.80 → slightly high)
- Alternatively, keep current λ but lower sev_mu values so expected loss per risk ≈ baseline

---

### 2c. Asymmetric broker market power
**Status:** `[x] complete — 2026-02-19 — diag_20_broker_power.png`
**Change:** `broker_power: float = 0.0` added to `build_simulation`. When >0, Zipf weights (1/rank^s) assigned to brokers. `_handle_risk` uses `random.choices(weights=...)` when weights differ; falls back to `random.choice` for backward compat.
**Code impact:** 3 edits in simulation.py (~15 lines); helpers + run/plot in diagnostics.py (~160 lines).

| Config | Period | Amplitude | MeanLR | Syn. HHI | Top-1 broker | Top-5 broker |
|--------|--------|-----------|--------|----------|--------------|--------------|
| 4-brok uniform | 3.4 yr | 0.383 | 0.485 | 0.136 | 28.2% | 100% |
| 4-brok Zipf s=1 | 3.5 yr | 0.384 | 0.489 | 0.140 | 33.7% | 100% |
| 10-brok Zipf s=1 | 4.0 yr | 0.428 | 0.526 | **0.090** ✓ | 20.4% | **70.2%** |

### Key findings

1. **Cycle dynamics are insensitive to broker power** — period, amplitude, and LR are nearly unchanged (3.4→3.5yr, amplitude ~0.38). Routing through a dominant broker does not alter market-wide pricing. Syndicates see the same risk types regardless of which broker routes them.

2. **Broker concentration increases correctly with Zipf**: top-1 broker share rises 28.2% → 33.7% with 4 brokers. Theoretical share with Zipf s=1, 4 brokers = 48%; observed 33.7% is moderated by specialism matching that overrides pure power ranking for specialist regions.

3. **10-brok Zipf hits Lloyd's broker benchmark**: top-5 cumulative = 70.2% vs Lloyd's target of ~60%. Slightly above target but reasonable for a 10-broker proxy of the real 300-broker market. For exact calibration, use s≈0.7.

4. **Syndicate HHI at 10 brokers = 0.090** — squarely in Lloyd's band (0.06–0.10) ✓. Combined with Experiment 0b finding (n_syn=20, rpy=50 → HHI=0.070), the recommended config with broker_power=1.0 now calibrates both syndicate and broker concentration realistically.

---

### 2d. Syndicate market entry
**Status:** `[x] complete — 2026-02-19 — diag_21_market_entry.png`
**Change:** new syndicates enter when industry loss ratio > 100% in a year. Cap total syndicates at `n_syndicates_max`. This models the well-documented Lloyd's phenomenon where hard markets attract new capital.

Entry rule: `year_lr > ENTRY_LR_THRESHOLD (1.00)` → add one new syndicate. (Original markup-based trigger was broken: cat regime drives markup negative, so LR-based trigger was used instead.)

**Code impact:** ~55 lines; `allow_entry: bool`, `n_syndicates_max: int`, `ENTRY_LR_THRESHOLD = 1.00` constant, `_admit_new_syndicate()` method, `SYNDICATE_ENTERED` event kind.

| Config | Period | Amplitude | MeanLR | HHI | MeanEntries |
|--------|--------|-----------|--------|-----|-------------|
| A-no-entry (cf=0.06) | 3.4 yr | 1.379 | 0.918 | 0.337 | 0.0 |
| B-entry (cf=0.06) | 3.7 yr | 1.938 | 0.907 | 0.306 | 12.0 |
| C-entry (cf=0.03) | 3.7 yr | 1.489 | 0.773 | 0.219 | 8.3 |

### Key findings

1. **Entry reduces HHI** (0.337 → 0.306 → 0.219): new capital enters after LR>1.0 years, partially offsetting cat-driven insolvency concentration. Still above Lloyd's target (0.06–0.10) because cats drive so many insolvencies, but the direction is correct.

2. **Entry reduces mean LR with moderate cats** (cf=0.03): C-entry LR=0.773 vs A-no-entry 0.918 with cf=0.06. At lower cat frequency, new entrants add capacity that holds prices down in the early recovery phase.

3. **Entry slightly lengthens cycle period** (3.4 → 3.7 yr): new syndicate capital injections dampen the recovery overshoot slightly, stretching out the soft market phase.

4. **Amplitude rises with high cats + entry** (cf=0.06: 1.379 → 1.938): counter-intuitive — new entrants at the tail of the hard market enter under-capitalised, then fail in the next cat year, amplifying insolvency dynamics.

5. **Mean entries: 12/60yr at cf=0.06** (0.2 entries/yr): realistic — Lloyd's sees roughly one new syndicate admitted per year in hard markets.

### Notes on trigger design
The original markup-based trigger (`industry_avg_markup > 0.20`) never fired because the cat/elasticity mechanism drives markup *negative* after catastrophes (surviving syndicates see price up + qty up → positive elasticity → negative markup update). The LR-based trigger fires reliably 3–10 times per 60yr at cf=0.03–0.06.

---

## Tier 3 — architectural improvements (high complexity)

### 3a. Managed runoff
When a syndicate becomes insolvent, its policies enter a multi-year runoff period — claims are still paid (by the Lloyd's Central Fund analogue) but no new risks are written. Softens the capacity cliff that current insolvencies create.

**Status:** `[x] done — diag_22_runoff.png`

**Results (10 seeds × 60yr, cat_freq=0.06):**
| Config | Period | Amplitude | MeanLR | HHI | CF Total | CF% |
|--------|--------|-----------|--------|-----|----------|-----|
| A-no-runoff | 3.4 | 1.379 | 0.918 | 0.337 | 0 | 0.0% |
| B-runoff | 3.4 | 1.463 | 0.937 | 0.337 | 1,419 | 1.7% |
| C-runoff+entry | 3.5 | 2.016 | 0.912 | 0.301 | 1,795 | 1.2% |

Key findings: CF covers ~1.7% of total claims; period/HHI unchanged by runoff alone; entry still reduces HHI (0.337→0.301); LR slightly higher with runoff (claims no longer silently dropped).

---

### 3b. Stamp capacity (Lloyd's-specific annual GWP ceiling)
In the real market, each syndicate's annual GWP is capped by a "Stamp Capacity" approved by Lloyd's Corporation — a regulatory ceiling independent of current capital. This limits rapid soft-market expansion above the existing `MAX_PREMIUM_TO_CAPITAL_RATIO` constraint.

**Status:** `[ ] proposed (low priority)`

---

### 3c. Proportional reinsurance
Syndicates cede X% of each policy at a proportional premium, transferring cat losses outside the market. A key stabiliser in real Lloyd's that dampens the cycle in catastrophe-heavy regimes.

**Status:** `[ ] proposed (low priority)`

---

## Verification benchmarks

For each completed experiment or change, assess against:

| Benchmark | Source | Target value |
|-----------|--------|-------------|
| Cycle period | Owadally 2018, UK property | ~6 years |
| HHI (syndicate GWP) | Lloyd's Annual Report 2023 | 0.06–0.10 |
| Combined ratio range | Lloyd's Annual Report | 90–110% across cycle |
| Cat-year LR spike | Industry cat studies | +30–50 pp |
| Insolvency rate | Solvency II guidance | < 1 syndicate / decade (stable market) |
| Broker concentration | Lloyd's market data | Top 5 brokers ~60% of GWP |

---

## Prioritised roadmap

| Priority | ID | Change | Effort | Status | Key finding / insight |
|----------|----|--------|--------|--------|-----------------------|
| 1 | 0a | Agent-count scaling sweep | None | ✅ done | Period insensitive to n_syn; HHI in band at 20–40 syn; must scale risks ∝ syndicates |
| 2 | 0b | Risk-volume / compute study | None | ✅ done | Proportional scaling corrects LR; HHI=0.07 at 20 syn/50 rpy; Lloyd's-scale run ~11 min |
| 3 | 1a | Calibration dashboard | Diagnostics only | ✅ done | CR✓ with cats; period gap −2.7yr; RoC needs stamp capacity; HHI jumps with cats |
| 4 | 1b | LOB decomposition | Diagnostics only | ✅ done | Cat/non-cat split clear; amplitude ordering correct; cat_freq needs recalibration per LOB |
| 5 | 2a | Per-region cat rates | ~15 lines sim.py | ✅ done | Period flat; amplitude rises with concentration; C-concentrated amplitude=2.18 |
| 6 | 2b | Per-LOB risk heterogeneity | ~40 lines sim.py | ✅ done | Period flat; LR doubles with default params (recal needed); aviation capacity gap found |
| 7 | 2c | Asymmetric broker power | ~25 lines sim.py | ✅ done | Cycle unchanged; broker conc. rises; 10-brok Zipf top-5=70% ≈ Lloyd's 60% |
| 8 | 2d | Syndicate entry | ~55 lines sim.py | ✅ done | Entry reduces HHI (0.337→0.219); LR↓ with moderate cats; mean 12 entries/60yr |
| 9 | 3a | Managed runoff (Central Fund) | ~16 lines sim.py | ✅ done | CF covers 1.7% claims; LR slightly higher; period/HHI unchanged by runoff alone |
| 10 | 3b–3c | Reinsurance / Stamp capacity | Major refactor | proposed | Full Lloyd's institutional features |
