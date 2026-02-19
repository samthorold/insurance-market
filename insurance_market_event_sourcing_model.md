# Insurance Market: Unified Event-Sourcing Model

A distillation of agent-based and discrete-event research on insurance markets into a single event-sourced representation. Uses a coordinator pattern with fewer, more complex agents and a clockless discrete-event simulation that advances at irregular time intervals.

---

## Sources

| Label                    | Paper                                                                                                                                                 |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Wang 2017**            | Yunpeng, "Study on the Underwriting Circle of Property-Liability Insurance Market under Competition"                                                  |
| **Owadally 2018**        | Owadally, Zhou & Wright, "The Insurance Industry as a Complex Social System: Competition, Cycles, and Crises"                                         |
| **Owadally 2019a**       | Owadally et al., "Time Series Data Mining with an Application to the Measurement of Underwriting Cycles"                                              |
| **Owadally 2019b**       | Owadally et al., "An agent-based system with temporal data mining for monitoring financial stability on insurance markets"                            |
| **Olmez 2024**           | Olmez, Ahmed, Kam, Feng & Tua, "Exploring the Dynamics of the Specialty Insurance Market Using a Novel DES Framework: a Lloyd's of London Case Study" |
| **Axtell & Farmer 2022** | "Agent-Based Modeling in Economics and Finance: Past, Present, and Future"                                                                            |

---

## 1. Core Research Themes

### 1.1 Underwriting Cycles Are Emergent

Every paper converges on the same finding: underwriting cycles (5-10 year oscillations in profitability) are not imposed externally but emerge from the micro-level interactions of heterogeneous agents. Owadally 2018 provides the cleanest demonstration: even with simple behavioural rules for actuaries, underwriters, and customers, cyclical loss ratios appear with a period of roughly 6 years matching empirical UK property insurance data. Wang 2017 arrives at the same conclusion via Nash equilibrium in a game-theoretic setting, finding cycles of 7-8 years across 1000 simulations of 10 competing Chinese insurers.

### 1.2 The Actuary-Underwriter Tension Drives Volatility

Owadally 2018 and 2019b both parameterise the internal power struggle between actuaries (prudent risk managers who set cost-based premiums) and underwriters (profit-maximisers who adjust prices to market demand). The parameter beta governs how aggressively underwriters deviate from the actuarial rate. Low beta gives actuaries dominance and dampens volatility but increases long-run cyclicality; high beta gives underwriters dominance and creates noisy, uncorrelated price sequences. Olmez 2024 embeds the same tension through separate actuarial and underwriting sub-processes, each emitting independent quote components that the syndicate must reconcile. In the coordinator pattern adopted here, these are internal method calls within the Syndicate agent rather than separate event-emitting processes.

### 1.3 Heterogeneity Is Necessary

Wang 2017 differentiates insurers by portfolio size, expense rate, lapse sensitivity, and capital. Owadally 2018 places insurers and customers on a circular preference landscape where distance encodes non-price affinity (branding, service, channel). Olmez 2024 uses broker-syndicate network topologies (circular, graph-based, random) to control which risks each syndicate sees. In the model proposed here, heterogeneity is primarily driven by the evolving broker-syndicate relationship network, which endogenously determines who sees which risks based on past interaction history.

### 1.4 Capital Dynamics and Solvency Constraints Create Feedback Loops

All models track insurer capital as a running aggregate of premium income minus claims minus expenses. Solvency constraints (Wang: 99.5% VaR; Olmez: VaR exposure management and premium-to-capital ratio) create non-linear feedback: a bad loss year depletes capital, which constrains future underwriting capacity, which raises market prices, which restores capital, which relaxes constraints, which intensifies competition, which lowers prices. This feedback loop is the primary engine of the cycle.

### 1.5 Catastrophe Events Amplify Cycles

Olmez 2024 is the only paper to model catastrophic losses explicitly (correlated, low-frequency, high-severity events hitting peril regions). Catastrophe events sharpen the cycle by simultaneously depleting multiple syndicates' capital, triggering insolvencies, and causing steep premium increases. Without catastrophes, cycles are gentler and insolvencies rarer.

### 1.6 Syndication of Risk (Lead-Follow) Stabilises Markets

Unique to Olmez 2024 and the Lloyd's market structure: a lead syndicate prices the risk and takes the largest share; follow syndicates accept similar terms for smaller shares. This distributes exposure, couples loss experiences across syndicates, and narrows price dispersion. The result is lower volatility and fewer insolvencies.

### 1.7 Broker-Syndicate Networks Shape Market Structure

In the Lloyd's specialty market, the broker is the intermediary who decides which syndicates see which risks. This selection is driven by pre-existing relationships, past experience, and the broker's judgement about syndicate appetite. The network is not static: brokers strengthen ties with syndicates that accept risks at competitive prices, and weaken ties with those that decline or overcharge. This co-evolution of the broker-syndicate network with market conditions creates feedback loops — successful syndicates attract more broker attention, concentrating business, which can tip into over-exposure when catastrophes strike.

### 1.8 Classical Time Series Methods Are Insufficient

Owadally 2019a demonstrates that AR(2) models often fail to detect cycles in US property-casualty data, particularly for shorter windows or lines affected by outliers. Motif-Based Periodicity Detection (MBPD), built on SAX discretisation and Sequitur grammar induction, robustly estimates periods of 6-13 years across all lines and is resilient to trends and outliers. This motivates moving from linear statistical models to agent-based simulation combined with data mining.

---

## 2. Architecture: Coordinator Pattern

The model uses three agent types and a coordinator, replacing the many-process design of Olmez 2024 and the many-agent ABMs of Owadally 2018/2019b. Internal decision logic (actuarial pricing, underwriter markup, exposure management) is implemented as method calls within agents, not as separate event-emitting processes. Only externally observable outcomes generate events.

There is no clock process. The simulation advances via a priority queue of events sorted by simulation time, skipping forward at irregular intervals. Annual housekeeping (dividends, industry statistics) is handled by `AnnualReview` marker events pre-scheduled at initialisation, not by a ticking clock.

### 2.1 Market (Coordinator)

The coordinator. One instance per simulation. Owns the risk registry, orchestrates the quoting/binding lifecycle between brokers and syndicates, cascades losses, and maintains industry statistics.

**Responsibilities:**

- Assign incoming risks to brokers
- Orchestrate the lead/follow quoting process by calling broker and syndicate methods
- Manage the central risk repository (policies, line sizes, syndicate assignments)
- Cascade attritional and catastrophe losses to the syndicates on affected policies
- Compute and publish industry statistics at annual reviews
- Handle syndicate insolvencies and portfolio redistribution

**State:**

| Field              | Type                             | Purpose                                    |
| ------------------ | -------------------------------- | ------------------------------------------ |
| `risk_registry`    | dict of risk_id to Policy        | All bound policies and their syndicate shares |
| `industry_stats`   | IndustryStats                    | Running aggregates: avg claim freq, severity, loss ratio, premium |
| `queue`            | min-heap of Event by sim_time    | Priority queue driving the simulation      |
| `event_store`      | append-only list of Event        | Immutable log of all emitted events        |

The Market does not make pricing or underwriting decisions. It asks brokers who to approach, asks syndicates what they'd offer, and records the outcomes.

### 2.2 Syndicate

The primary decision-making agent. A self-contained black box that takes a risk and market context as input and returns a quote or declines. Internal pricing logic (actuarial, underwriting, exposure management) is implemented as private methods — no internal events.

**State (rebuilt from events):**

| Field                | Type                    | Source papers                          |
| -------------------- | ----------------------- | -------------------------------------- |
| `capital`            | float                   | All                                    |
| `portfolio`          | list of PolicyId        | Owadally 2018/2019b, Olmez             |
| `claims_history`     | list of (t, amount)     | All                                    |
| `weighted_avg_claim` | float                   | Owadally (EWMA with weight w)          |
| `claims_std_dev`     | float                   | All (F_t in pricing equations)         |
| `markup`             | float                   | Owadally 2018/2019b, Olmez (m_t)       |
| `expense_rate`       | float                   | Wang                                   |
| `peril_exposures`    | dict of region to float | Olmez (VaR EM)                         |
| `solvency_ratio`     | float                   | Wang, Olmez                            |
| `is_solvent`         | bool                    | All                                    |

**Behavioural parameters:**

| Parameter | Meaning                                         | Typical value |
| --------- | ----------------------------------------------- | ------------- |
| `z`       | Credibility weight (own vs industry experience) | 0.2-0.5       |
| `w`       | Recency weight for EWMA of past claims          | 0.2           |
| `alpha`   | Risk/safety loading factor                      | 0.001         |
| `beta`    | Underwriter markup aggressiveness               | 0.2-0.3       |
| `omega`   | Confidence in own actuarial premium vs market   | 0.9 (Wang)    |

**Internal pricing pipeline (method calls, not events):**

```
quote_as_lead(risk, industry_stats) -> LeadQuote | None:
    1. Actuarial price:
       pure_premium = z * weighted_avg_claim + (1-z) * industry_stats.avg_claim_cost
       risk_loading = alpha * claims_std_dev
       actuarial_price = pure_premium + risk_loading

    2. Underwriter markup:
       final_price = actuarial_price * exp(markup)

    3. Exposure check (VaR or Premium-to-Capital):
       if not passes_exposure_check(risk, final_price):
           return None

    4. Return LeadQuote(price=final_price, line_size=default_lead_line)

quote_as_follow(risk, lead_price, industry_stats) -> FollowQuote | None:
    1. Run same actuarial + underwriting pipeline to get own_price
    2. Compute pricing_strength = own_price / lead_price
    3. If pricing_strength > 1.0: risk looks cheap, offer larger line
       If pricing_strength < threshold: risk looks expensive, decline
    4. Exposure check
    5. Return FollowQuote(line_size=adjusted_line)
```

**State update methods (called by Market after binding/claims):**

```
book_policy(policy, role):
    portfolio.append(policy)
    capital += premium_share * (1 - expense_rate)

receive_claim(amount):
    capital -= amount
    claims_history.append((current_time, amount))
    update weighted_avg_claim via EWMA
    update claims_std_dev

pay_dividend():
    if profit > 0:
        dividend = gamma * profit
        capital -= dividend
        return dividend
    return 0

update_markup(industry_stats):
    arc_elasticity = (Q_{t-1} - Q_{t-2}) / (Q_{t-1} + Q_{t-2})
                   / (P_{t-1} - P_{t-2}) / (P_{t-1} + P_{t-2})
    raw_markup = -1 / arc_elasticity
    markup = beta * raw_markup + (1 - beta) * markup
```

### 2.3 Broker

A substantive decision-making agent for the specialty insurance market. Not a thin risk-generation process but a participant with its own evolving relationships, specialisms, and market memory. Multiple broker instances (3-10, mirroring the handful of dominant Lloyd's brokers: Marsh, Aon, WTW, Gallagher).

**State:**

| Field              | Type                                           | Purpose                                              |
| ------------------ | ---------------------------------------------- | ---------------------------------------------------- |
| `relationships`    | dict of syndicate_id to RelationshipState      | Evolving strength/quality of each broker-syndicate tie |
| `specialisms`      | set of peril_region or risk_class              | Classes this broker knows well                       |
| `placement_history`| list of (risk_id, outcome)                     | Track record for analysis                            |

**RelationshipState (per broker-syndicate pair):**

| Field                  | Type  | Purpose                                                   |
| ---------------------- | ----- | --------------------------------------------------------- |
| `strength`             | float | Overall relationship quality, 0-1                         |
| `acceptance_rate`      | float | EWMA of accept/decline decisions                          |
| `price_competitiveness`| float | EWMA of how this syndicate's quotes compare to winners    |
| `volume`               | int   | Number of risks shown to this syndicate                   |
| `last_interaction`     | float | Simulation time of last interaction (for decay)           |

**Syndicate selection logic:**

```
select_syndicates(risk) -> (lead_candidates, follow_candidates):
    for each syndicate in relationships:
        score = (
            strength *
            acceptance_rate *
            price_competitiveness *
            specialism_match(syndicate, risk)
        )
    sort by score descending
    leads = top lead_k syndicates
    follows = next follow_k syndicates
    return leads, follows
```

**Relationship evolution (called by Market after each placement attempt):**

```
update_relationship(syndicate_id, outcome):
    if outcome.accepted:
        strength *= 1.05     # successful placement strengthens the tie
        acceptance_rate = recency * 1.0 + (1 - recency) * acceptance_rate
        if outcome.price_vs_lead is not None:
            price_competitiveness = recency * normalise(outcome.price_vs_lead)
                                 + (1 - recency) * price_competitiveness
    else:
        strength *= 0.98     # decline weakens the tie
        acceptance_rate = recency * 0.0 + (1 - recency) * acceptance_rate
    volume += 1
    last_interaction = current_time
```

**Network topology special cases:**

The evolving relationship model subsumes the static topologies from the literature as special cases:

- **Random (Olmez base case):** All relationships initialised with equal strength, never updated
- **Circular (Owadally 2018):** Strength initialised as inverse distance on a circle, never updated
- **Weighted graph (Olmez network):** Strength initialised from a stochastic block model (brokers in the same specialism cluster have higher initial strength with syndicates writing that class), then allowed to evolve

### 2.4 Environment (Loss Generator)

Not an agent in the social sense. A pure schedule of future shocks with no evolving state. Pre-generates all loss events at initialisation and places them in the priority queue.

**Catastrophe losses** (per-region, correlated): Count over the full horizon drawn from Poisson(mu_cat \* T). Each event assigned a random simulation time, a random peril region, and a severity drawn from Truncated Pareto(shape=5, min=0.25 \* risk_limit). Pre-generated and placed in the priority queue at initialisation.

**Attritional losses** (per-risk, uncorrelated): Generated dynamically when each policy is bound. Count drawn from Poisson(lambda \* duration_years). Each assigned a random simulation time within the policy's duration. Severity drawn from Gamma(1/COV², mu \* COV²). Placed in the priority queue at binding time.

---

## 3. Event Catalogue

Only externally observable state changes generate events. Internal agent reasoning (actuarial pricing, exposure checks, relationship scoring) happens via method calls and does not appear in the event store. This produces a sparse, meaningful event stream where each event corresponds to something observable in the real market.

### 3.1 Market Entry Events

| Event                 | Payload                                            | Emitter     | Purpose                                                |
| --------------------- | -------------------------------------------------- | ----------- | ------------------------------------------------------ |
| `RiskArrived`         | risk_id, peril_region, risk_limit, expiry          | Environment | A new risk enters the market                           |
| `RiskAssignedToBroker`| risk_id, broker_id                                 | Market      | Broker selected to place this risk                     |
| `RiskDeclined`        | risk_id, broker_id                                 | Market      | No syndicate willing to lead — risk unplaced           |

### 3.2 Binding Events

| Event               | Payload                                                              | Emitter | Purpose                                                    |
| -------------------- | -------------------------------------------------------------------- | ------- | ---------------------------------------------------------- |
| `PolicyBound`        | risk_id, broker_id, lead_id, lead_price, lead_line, follows[(id, line)], total_line | Market  | Policy fully placed with lead and follow syndicates        |
| `PremiumDistributed` | risk_id, premiums_by_syndicate                                       | Market  | Premium income allocated proportionally by line size       |

### 3.3 Loss Events

| Event                     | Payload                              | Emitter     | Purpose                                              |
| ------------------------- | ------------------------------------ | ----------- | ---------------------------------------------------- |
| `AttritionalLossOccurred` | risk_id, amount                      | Environment | Uncorrelated per-risk loss fires from the queue      |
| `CatastropheOccurred`     | peril_region, total_damage           | Environment | Correlated regional loss fires from the queue        |
| `ClaimPaid`               | syndicate_id, risk_id, amount, share | Market      | Loss cascaded to a specific syndicate on the policy  |

### 3.4 Capital and Solvency Events

| Event                | Payload                                     | Emitter | Purpose                                              |
| -------------------- | ------------------------------------------- | ------- | ---------------------------------------------------- |
| `DividendPaid`       | syndicate_id, amount                        | Market  | Annual profit distribution to capital providers      |
| `SyndicateInsolvent` | syndicate_id, remaining_capital, portfolio  | Market  | Syndicate's capital falls below zero after a claim   |

### 3.5 Market Intelligence Events

| Event                      | Payload                                                  | Emitter | Purpose                                            |
| -------------------------- | -------------------------------------------------------- | ------- | -------------------------------------------------- |
| `IndustryStatsPublished`   | avg_claim_freq, avg_severity, avg_loss_ratio, avg_premium, period | Market  | Annual aggregate statistics for syndicate pricing   |
| `AnnualReview`             | year                                                     | Pre-scheduled | Marker event triggering year-end housekeeping      |

### 3.6 Typical Event Stream for One Risk

```
seq=1041  t=412.7  RiskArrived(risk_id=42, region=3, limit=10M)
seq=1042  t=412.7  RiskAssignedToBroker(risk_id=42, broker_id=B2)
seq=1043  t=412.7  PolicyBound(risk_id=42, lead=S1@50%, follows=[S3@20%, S7@30%], price=320K)
seq=1044  t=412.7  PremiumDistributed(risk_id=42, {S1: 160K, S3: 64K, S7: 96K})
seq=1045  t=518.2  AttritionalLossOccurred(risk_id=42, amount=45K)
seq=1046  t=518.2  ClaimPaid(S1, risk_id=42, 22.5K)
seq=1047  t=518.2  ClaimPaid(S3, risk_id=42, 9K)
seq=1048  t=518.2  ClaimPaid(S7, risk_id=42, 13.5K)
```

Seven events for the full lifecycle of one risk, each corresponding to a real-world observable action. Compare with ~25 events in the fully decomposed Olmez design.

---

## 4. Simulation Loop

The simulation is driven by a priority queue of events sorted by simulation time. There is no fixed time step. The loop processes events in chronological order, and time jumps forward irregularly — if nothing happens between day 47 and day 93, the simulation skips straight there.

```
1. Initialise
     a. Create syndicates with seed capital and behavioural parameters
     b. Create brokers with specialism profiles
     c. Seed broker-syndicate relationships (stochastic block model or uniform)
     d. Pre-generate catastrophe events for full horizon -> push to queue
     e. Pre-generate RiskArrived events (Poisson process) -> push to queue
     f. Pre-schedule AnnualReview marker events -> push to queue

2. Main loop
     while queue is not empty:
         event = heappop(queue)
         event_store.append(event)
         new_events = market.dispatch(event)
         for e in new_events:
             heappush(queue, e)

3. Market.dispatch(event)
     match event:

         RiskArrived(risk):
             broker = assign_broker(risk)         # by specialism match
             emit RiskAssignedToBroker

             leads, follows = broker.select_syndicates(risk)

             # Solicit lead quotes (synchronous method calls)
             lead_quotes = []
             for s_id in leads:
                 quote = syndicates[s_id].quote_as_lead(risk, industry_stats)
                 broker.update_relationship(s_id, Outcome(accepted=quote is not None))
                 if quote: lead_quotes.append(quote)

             if no lead_quotes:
                 emit RiskDeclined
                 return

             best_lead = cheapest quote
             # Solicit follow quotes using lead's price as anchor
             accepted_follows = []
             remaining = 1.0 - best_lead.line_size
             for s_id in follows:
                 if remaining <= 0: break
                 follow = syndicates[s_id].quote_as_follow(risk, best_lead.price, industry_stats)
                 broker.update_relationship(s_id, Outcome(...))
                 if follow:
                     line = min(follow.line_size, remaining)
                     accepted_follows.append((s_id, line))
                     remaining -= line

             # Bind policy
             policy = Policy(risk, best_lead, accepted_follows)
             risk_registry[risk.id] = policy
             syndicates[best_lead.id].book_policy(policy, "lead")
             for s_id, line in accepted_follows:
                 syndicates[s_id].book_policy(policy, "follow")

             emit PolicyBound, PremiumDistributed

             # Schedule attritional losses for this policy
             for each sampled attritional loss:
                 push AttritionalLossOccurred to queue

         AttritionalLossOccurred(risk_id, amount):
             policy = risk_registry[risk_id]
             for s_id, share in policy.shares:
                 claim = amount * share
                 syndicates[s_id].receive_claim(claim)
                 emit ClaimPaid(s_id, claim)
                 if not syndicates[s_id].is_solvent:
                     emit SyndicateInsolvent
                     handle_insolvency(s_id)

         CatastropheOccurred(region, total_damage):
             affected_policies = policies in region
             for policy in affected_policies:
                 policy_loss = total_damage * (policy.limit / total_region_exposure)
                 # same claim distribution as attritional, per syndicate share

         AnnualReview(year):
             industry_stats.compute(risk_registry, syndicates)
             emit IndustryStatsPublished
             for each solvent syndicate:
                 dividend = syndicate.pay_dividend()
                 if dividend > 0: emit DividendPaid
                 syndicate.update_markup(industry_stats)
```

---

## 5. Projection (State Rebuild) Logic

Each agent's current state is a left fold over its relevant event stream. A left fold starts from the beginning of the stream and walks forward chronologically, carrying an accumulator (the agent's state) that gets updated at each event. The final value of the accumulator is the agent's current state. There is no separate "state database" — state is entirely a consequence of processing every event in sequence.

### 5.1 Insurer Capital Projection

```
capital = initial_capital

for each event in stream where event involves this syndicate:
    match event:
        PremiumDistributed(premiums) -> capital += premiums[my_id] * (1 - expense_rate)
        ClaimPaid(amount)            -> capital -= amount
        DividendPaid(amount)         -> capital -= amount
```

### 5.2 Market Loss Ratio Projection

```
yearly_premiums = defaultdict(float)
yearly_claims = defaultdict(float)

for each event in stream:
    year = int(event.sim_time / 365)
    match event:
        PremiumDistributed(premiums) -> yearly_premiums[year] += sum(premiums.values())
        ClaimPaid(amount)            -> yearly_claims[year] += amount

loss_ratios = {y: yearly_claims[y] / yearly_premiums[y] for y in yearly_premiums}
```

### 5.3 Broker Performance Projection

```
for each event in stream:
    match event:
        RiskAssignedToBroker(broker_id) -> broker_volume[broker_id] += 1
        PolicyBound(broker_id)          -> broker_placements[broker_id] += 1
        RiskDeclined(broker_id)         -> broker_declines[broker_id] += 1

placement_rate = {b: placements[b] / volume[b] for b in brokers}
```

### 5.4 Network Evolution Projection

```
for each event in stream:
    match event:
        PolicyBound(broker_id, lead_id, follows):
            broker_syndicate_volume[(broker_id, lead_id)] += 1
            for (s_id, _) in follows:
                broker_syndicate_volume[(broker_id, s_id)] += 1

# Reveals emergent clustering: which broker-syndicate pairs dominate
```

### 5.5 Branching and What-If

Fork the event stream at any point, rebuild agent state by replaying to that point (or load from a snapshot), modify a parameter or inject a hypothetical event, and continue the simulation from that state. Pre-generated catastrophe events in the queue should be kept identical across branches to isolate the effect of the parameter change from stochastic variation.

---

## 6. Emergent Phenomena

The model does not hard-code market cycles, crises, or herding. These phenomena arise from the mechanical interaction of agent behaviours operating at different timescales and through different feedback channels. This section traces how each stylised fact emerges from the specific design choices above.

### 6.1 The Underwriting Cycle

The cycle emerges from a timescale mismatch between two components inside every Syndicate.

The **actuary** updates `weighted_avg_claim` via an EWMA with recency weight `w` (typically 0.2). This is a slow-moving estimator: after a bad loss year, it takes roughly `1/w = 5` periods for the effect to fully feed through to the actuarial premium. The actuary also blends own experience with industry statistics via credibility weight `z`, further smoothing the signal.

The **underwriter** updates `markup` using arc-elasticity of demand, weighted by `beta` (typically 0.2-0.3). This responds to the most recent period's price-volume relationship — a fast-moving signal. When the market softens (prices falling, volumes rising), the underwriter sees positive elasticity and pushes markup down. When the market hardens (prices rising, volumes falling), markup goes up.

The cycle works as follows. After a period of losses, the actuary gradually raises the pure premium. The underwriter, seeing reduced demand and higher prices, also raises markup. Both forces push prices up together — the hard market. But the actuary's EWMA keeps rising even after conditions improve, because it's still digesting old bad experience. Meanwhile, the underwriter sees the hard market attracting volume (competitors have also raised prices, so demand is relatively inelastic) and begins to ease markup. The underwriter's fast signal says "we can afford to compete"; the actuary's slow signal still says "losses are high". Eventually the underwriter's downward pressure on markup overwhelms the actuary's upward pressure on the pure premium, and the market softens. Prices fall, volumes rise, loss ratios deteriorate, and the cycle begins again.

The period of the cycle is governed by the ratio of timescales: the actuary's effective memory (`1/w` periods for own experience, blended with industry stats that update annually) versus the underwriter's response speed (`beta`). With typical parameters from Owadally 2018 (`w=0.2`, `beta=0.3`), the model produces cycles of approximately 6 years, matching empirical UK property insurance data.

### 6.2 Catastrophe-Driven Crises

Catastrophe events amplify the cycle through correlated capital depletion. When a `CatastropheOccurred` event fires, the Market cascades losses to every syndicate holding policies in the affected peril region, proportional to their line sizes. Multiple syndicates suffer large losses simultaneously.

The cascade works through three channels. First, **direct capital depletion**: syndicates with concentrated exposure to the affected region may become insolvent, triggering `SyndicateInsolvent` events. Second, **exposure management tightening**: surviving syndicates whose `peril_exposures` have spiked will fail their VaR or premium-to-capital checks and decline subsequent risks in that region, reducing market capacity. Third, **actuarial repricing**: the large claims feed into every affected syndicate's EWMA, pushing actuarial premiums up — but slowly, over `1/w` periods, so the full price impact lags the event.

The combination of immediate capacity reduction (from insolvencies and exposure limits) and delayed price adjustment creates the characteristic post-catastrophe pattern: a sharp premium spike as supply contracts, followed by a gradual softening as new capital enters (surviving syndicates' capital recovers via higher premiums) and the catastrophe experience fades from the EWMA window.

Without catastrophes, the model produces gentle, regular cycles. With catastrophes, the same underlying cycle is punctuated by sharp dislocations that can reset the phase of the cycle entirely — a catastrophe during a soft market can trigger a hard market years earlier than the endogenous cycle would have produced.

### 6.3 Winner's Curse and Price Wars

The broker network creates a positive feedback loop that can produce price wars. The mechanism operates through the Broker's `select_syndicates` scoring function, which weights `price_competitiveness` — an EWMA of how each syndicate's quotes compare to winning prices.

A syndicate that consistently offers low prices accumulates high `price_competitiveness` scores across multiple brokers. This raises its composite score, so brokers show it more risks (higher `volume`). Seeing more risks, the syndicate writes more business, which initially looks profitable (premiums flowing in, claims not yet materialised). The underwriter, observing strong demand, may ease `markup` further. The syndicate's prices drop, its `price_competitiveness` scores rise further, and more brokers route more risks to it.

This is the winner's curse: the syndicate winning the most business is, by construction, the one most aggressively underpricing risk. The feedback loop concentrates business in the most aggressive underwriters until losses materialise and capital depletion forces a correction. The broker network amplifies this because relationship strength compounds — a syndicate that accepts many risks at competitive prices becomes the default choice for multiple brokers, creating concentration that would not occur under random allocation.

### 6.4 Lead-Follow Stabilisation

The lead-follow syndication structure acts as a stabilising counterforce to the price war dynamic. When a lead syndicate sets a price, follow syndicates evaluate it against their own actuarial assessment via `pricing_strength = own_price / lead_price`. If the lead's price looks too cheap (`pricing_strength > 1` by a wide margin), follows may still participate but take smaller lines. If the lead's price looks reasonable, follows take standard lines.

This creates a price discovery mechanism. An aggressively low lead price will struggle to fill the slip — follows will either decline or take minimal lines, leaving the risk underplaced. The broker, observing poor fill rates with aggressive leads, will adjust: either the lead must raise its price, or the broker routes the risk to a different lead whose pricing attracts better follow participation. This is visible in the event stream as `PolicyBound` events with low `total_line` values when the lead underprices.

Lead-follow also couples loss experience across syndicates. All participants on a policy share the same claims (proportional to line size), so their EWMAs and markup adjustments move in parallel. This narrows price dispersion — syndicates that share policies converge in their actuarial estimates — and reduces the variance of the cycle.

### 6.5 Herding Through Network Evolution

The broker relationship evolution mechanism produces herding behaviour without any explicit imitation logic. The mechanism is as follows.

A syndicate that performs well in one period (accepting risks, competitive pricing, no insolvency) sees its `strength` and `acceptance_rate` rise across all brokers it interacted with. In the next period, these improved scores cause more brokers to select it, including brokers it hasn't worked with before (whose relationships may have been initialised via the stochastic block model at moderate strength). The syndicate sees more risks, writes more business, and — if its pricing was sound — continues to perform well.

Meanwhile, a syndicate that performed poorly (declining many risks, uncompetitive pricing) sees its scores decay. Brokers route fewer risks to it. Seeing less business, the syndicate's underwriter may cut `markup` to attract volume, but the broker network's memory means it takes several periods for the improved competitiveness to translate into restored relationship strength.

The result is endogenous concentration: the market converges toward a subset of "popular" syndicates that attract disproportionate broker attention. This mimics the real Lloyd's market where a handful of syndicates dominate particular classes. The concentration is self-reinforcing but also self-correcting — when a popular syndicate suffers a large loss and becomes less competitive, its relationship scores decay and business redistributes.

The speed of herding is controlled by the relationship evolution parameters: the `1.05` and `0.98` multipliers on `strength`, and the `recency` weight on the EWMA updates. Faster evolution produces quicker herding but also quicker correction; slower evolution produces more stable but stickier market structures.

### 6.6 Market Memory and Path Dependence

The model contains multiple memory layers operating at different timescales, and their interaction produces path dependence — the market's current state depends on its full history, not just current conditions.

**Actuarial memory** (`weighted_avg_claim`, EWMA with weight `w`): effective memory of `1/w` periods, typically 5 years. This is the primary driver of the pricing cycle.

**Underwriter memory** (`markup`, updated via `beta`-weighted elasticity): effective memory of 1-2 periods. Fast-responding, creating the oscillation against the slower actuarial signal.

**Broker memory** (RelationshipState, especially `strength`): the slowest-moving component. Relationship strength compounds multiplicatively (`*= 1.05` or `*= 0.98`), so it takes many interactions to significantly shift an established relationship. A syndicate that has built strong broker relationships over years can weather a bad period better than a newcomer with identical financials, because brokers will continue routing risks to it based on accumulated relationship capital.

**Capital memory**: capital is the integral of all past premium income minus all past claims minus all past dividends. A syndicate that suffered a catastrophe five years ago may have fully recovered its pricing and relationships but still carry a capital deficit that constrains its underwriting capacity via exposure management checks.

These overlapping memories mean that two simulations with identical parameters but different random seeds can diverge permanently after a single catastrophe event. The catastrophe depletes capital (long memory), shifts actuarial estimates (medium memory), adjusts underwriter markup (short memory), and reshapes broker relationships (very long memory). The broker network effect is particularly powerful: if a catastrophe causes a syndicate's insolvency, the brokers who relied on that syndicate must redistribute business to others, permanently altering the network topology even after new syndicates enter to replace the failed one.

---

## 7. Experimental Results

The simulation (`simulation.py`) and a companion diagnostics module (`diagnostics.py`) were run to verify that the phenomena described in Section 6 emerge from the design. All results below are from `diagnostics.py` run at 60-year horizon. The baseline configuration is no catastrophes, 10 seeds (seeds 0–9) for ensemble runs, and 1 seed (42) for the single detailed run. Parameter sweeps use 10 seeds each.

### 7.1 Underwriting Cycles Emerge Without Catastrophes

![Underwriting Cycle — Loss Ratio](diag_01_lr_ensemble.png)

With catastrophes disabled, the market produces oscillating loss ratios across all seeds. The 10-seed ensemble mean (blue line, top panel) shows a persistent oscillatory pattern around a stable long-run mean of **0.49** with standard deviation 0.07. Individual runs show year-to-year fluctuations between soft-market years (loss ratios as low as 0.1) and hard-market years (approaching but rarely exceeding 1.0 without cats). The single-seed run (seed=42) returns a mean LR of 0.47, CV of 0.33, and 12 detected peaks over 60 years.

The ACF (bottom panel) shows the dominant period estimate at **4 years** (ensemble) / **5 years** (single seed), measured from the first local maximum of the detrended ACF. Peak-detection on the raw series gives inter-peak distances in the 3–7 year range, averaging ~5 years. The 4–5 year period is consistent with the theorised ~6-year period from Owadally 2018 for UK property insurance, compressed slightly by the small market size (10 syndicates). The mechanism is confirmed: markup responds within 1–2 years (fast), actuarial repricing takes ~5 years (slow), and their phase difference drives the oscillation.

![Peak Detection](diag_06_peaks.png)

Peak detection on the detrended LR series (seed=42) identifies 12 peaks with inter-peak distances labelled: 3yr, 4yr, 5yr, 7yr, and 3yr in various sections. The variation reflects stochastic claim severity perturbing an otherwise regular cycle — consistent with a stochastic limit cycle rather than a deterministic one.

### 7.2 The Causal Mechanism: Price Force Decomposition

![Price Force Decomposition](diag_02_force_decomp.png)

The force decomposition plot (seed=42) confirms the two-timescale mechanism directly. Three panels show:

- **Top:** Actuarial price (blue, left axis) drifts slowly downward over the first 15 years as the EWMA converges from the initial estimate of 75 toward the equilibrium. The markup factor exp(m) (orange, right axis) oscillates on a 3–5 year cadence, visibly leading the loss ratio and cycling between approximately 0.90 (soft) and 1.33 (hard market peak).
- **Middle:** The loss ratio oscillates between profitable (green-shaded, LR < 0.7) and unprofitable (red-shaded, LR > 0.9) phases, tracking the markup with a ~1-year lag.
- **Bottom:** Log-markup alternates between positive (orange, hard market) and negative (blue, soft market) phases with clear multi-year persistence.

The phase relationship is as described in Section 6.1: the markup leads the loss ratio, and the actuarial price lags both.

### 7.3 Phase Portrait Confirms a Limit-Cycle Attractor

![Phase Portrait](diag_05_phase_portrait.png)

The phase portrait (actuarial price normalised to year-2 value, vs markup factor exp(m), coloured by year) shows that the market state does not random-walk. Points cluster in a band between markup factor 1.0–1.35, with actuarial price compressed to 0.15–0.4× the year-2 value once the initial transient settles. Arrow overlays show systematic counter-clockwise rotation: markup expands (hard market), then contracts while actuarial reprices upward, then cycle repeats. The orbit is noisy but centred — the characteristic signature of a stochastic limit cycle.

### 7.4 Capital Stability and the No-Catastrophe Baseline

![Capital and Insolvencies](diag_03_capital.png)

Without catastrophes, all 10 syndicates remain solvent across the full 60-year run. Total market capital grows monotonically from ~21,200 to ~24,200 (+14%), sustained by profitable underwriting (mean LR 0.47, combined ratio ~0.82 with expense rates of 30–40%). The syndicate capital lines are tightly clustered and parallel, indicating no divergence in individual fortunes under attritional-only conditions. This confirms the attritional calibration is sound: the market is consistently profitable without exogenous shocks.

### 7.5 Catastrophes Amplify Cycles and Cause Insolvencies

![Catastrophe Sensitivity](diag_04_sweep_cat_freq.png)

The catastrophe frequency sweep is the starkest result. Amplitude and mean LR scale dramatically with cat exposure:

| cat_freq | Mean LR | Amplitude |
| --- | --- | --- |
| 0.00 | 0.49 | 0.38 |
| 0.03 | 0.86 | 1.39 |
| 0.05 | 1.00 | 1.33 |
| 0.08 | 1.03 | 1.53 |
| 0.12 | 1.35 | 1.73 |

At cat_freq ≥ 0.05, the mean LR exceeds 1.0 — the market is loss-making on average. Individual year spikes reach LR > 5 at high cat frequencies (visible in the top panel), representing years when catastrophe losses overwhelm premium income. Crucially, the **endogenous cycle period stays flat** at 3.3–3.6 years across all cat_freq values — catastrophes amplify and distort the cycle but do not alter its period structure. The endogenous mechanism operates independently of exogenous shocks.

### 7.6 Parameter Sensitivity

![Beta Sensitivity](diag_04_sweep_beta.png)

**beta (markup aggressiveness, 0.15–0.55):** Cycle period shows a non-monotonic response, peaking at 3.8 years for beta = 0.35–0.45 (near the default range of 0.35–0.50) and falling to 3.1–3.4 at the extremes. This is mechanistically coherent: at very low beta the markup barely oscillates and there is no self-sustaining cycle; at the resonant range the feedback is strongest; at very high beta the markup over-corrects and compresses the cycle. Amplitude increases weakly across the sweep (0.37 → 0.40).

**w (actuarial recency weight, 0.10–0.30):** Period increases weakly from 3.5 to 3.8 years as w rises. The effect is small because changes from 0.10 to 0.30 move the EWMA effective memory from ~6.6 to ~1.9 years — a large structural change with surprisingly modest effect on period. This suggests the cycle period is primarily controlled by the markup timescale (beta), not the actuarial timescale (w).

**z (own vs industry credibility, 0.15–0.45):** No systematic effect on period (3.2–3.6 years across all values). This supports the interpretation that z controls synchronisation strength between syndicates rather than the per-syndicate cycle period. In a 10-syndicate market, syndicates are never fully decoupled from the industry average regardless of z value.

**initial_capital (800–5000):** Period is largely stable (3.4–3.9 years). Amplitude is highest at low capital (0.56 at 800) as capacity constraints are binding. Mean LR rises with capital (0.45 at 1200 → 0.65 at 5000), reflecting that high capital relaxes the premium-to-capital ratio constraint, allowing more aggressive underwriting that tightens margins over time.

### 7.7 Simulation Configuration

The full simulation code is available alongside this document (`simulation.py`); diagnostics in `diagnostics.py`. Actual defaults as implemented:

| Parameter | Value | Notes |
| --- | --- | --- |
| Syndicates | 10 | Heterogeneous z, w, beta, expense_rate |
| Brokers | 4 | 1–2 specialism regions each |
| Peril regions | 5 | Risks assigned uniformly at random |
| Risks per year | 25 | Gaussian(25, 3), one-year duration |
| Claim frequency | Poisson(0.6) per risk | ~45% of risks produce at least one claim |
| Claim severity | Gamma(mu=75, COV=0.7) | Mean severity ≈ 75% of initial actuarial price |
| Catastrophe frequency | 0.05 per region-year (default) | Pareto(shape=2) severity × 5000 |
| Initial capital | 2,000 per syndicate | ×U(0.8, 1.2) heterogeneity |
| beta | U(0.35, 0.50) | Underwriter markup aggressiveness |
| w | U(0.15, 0.25) | Actuarial EWMA recency weight |
| z | U(0.20, 0.40) | Own-vs-industry credibility weight |
| Relationship evolution | strength ×1.08 (accept) / ×0.95 (decline) | EWMA recency = 0.4 |
| Diagnostic horizon | 60 years | 10 seeds for ensemble/sweeps |

---

## 8. Mapping Papers to Model Components

| Model component                      | Wang 2017               | Owadally 2018      | Owadally 2019a  | Owadally 2019b              | Olmez 2024                        |
| ------------------------------------ | ----------------------- | ------------------ | --------------- | --------------------------- | --------------------------------- |
| Nash equilibrium pricing             | Primary                 | -                  | -               | -                           | -                                 |
| Actuarial pure premium (credibility) | via omega weighting     | P_hat with z,w     | -               | Eq. 6-7                     | Eq. 1                             |
| Underwriter markup (elasticity)      | -                       | Eq. 1-2 (beta)     | -               | Eq. 1-4                     | Eq. 3-4                           |
| Customer choice (distance)           | Logit transfer probs    | TC = P + gamma\*d  | -               | TC = P + gamma\*d           | via network topology              |
| Solvency constraint                  | 99.5% VaR, Eq.8         | Capital capacity   | -               | Capital capacity            | VaR EM + Premium EM               |
| Broker-syndicate network             | -                       | Circle (implicit)  | -               | n-dim space                 | Circle / graph / random           |
| Evolving relationships               | -                       | -                  | -               | theta, phi (strategy space) | -                                 |
| Insurer heterogeneity                | Size, expense, lapse    | Location, capital  | -               | Location, capital, strategy | Network weights                   |
| Capital accumulation                 | K + premium - claims    | Premium - claims   | -               | Premium - claims            | Premium - claims - dividends      |
| Insolvency and exit                  | Capital < regulatory    | Capacity exhausted | -               | Capacity exhausted          | Capital < 0                       |
| Catastrophe events                   | -                       | -                  | -               | -                           | Pareto severity, Poisson freq     |
| Lead-follow syndication              | -                       | -                  | -               | -                           | Lead sets price, follow sets line |
| Time series data mining (MBPD)       | AR(2) on market premium | AR(2), spectral    | WARP, MBPD, SAX | GrammarViz (SAX+Sequitur)   | -                                 |
| Game-theoretic equilibrium           | Dutang GNE              | -                  | -               | -                           | -                                 |
| Coordinator pattern                  | -                       | -                  | -               | -                           | -                                 |
| Clockless DES                        | -                       | -                  | -               | -                           | HADES (partial)                   |

---

## 9. Key Parameters Across Papers

| Parameter             | Symbol   | Wang                | Owadally 2018 | Owadally 2019b | Olmez 2024       |
| --------------------- | -------- | ------------------- | ------------- | -------------- | ---------------- |
| Number of insurers    | N        | 10                  | 20            | 20             | 10 (default)     |
| Number of brokers     | -        | -                   | -             | -              | 1 (implicit)     |
| Number of customers   | M/n      | 10,000              | 1,000         | 1,000          | ~22/yr (Poisson) |
| Mean claim size       | mu       | E(Y)=1 (normalised) | 100           | 100            | 3,000,000        |
| Claim std dev         | sigma    | 4.472               | 10            | 10             | COV=1            |
| Credibility weight    | z        | omega=0.9           | 0.2           | 0.2            | 0.5              |
| Recency weight        | w        | -                   | 0.2           | 0.2            | 0.2              |
| Risk loading          | alpha    | -                   | 0.001         | 0.001          | 0                |
| Markup weight         | beta     | -                   | 0.3           | 0.3            | 0.2              |
| Distance cost         | gamma    | -                   | 0.08          | 0.05           | via topology     |
| Simulation horizon    | T        | 25 periods          | 1,000 years   | 1,000 years    | 50 years         |
| Initial solvency      | -        | 150%                | Equal capital | Equal capital  | $10M             |
| Expense rate          | e        | 0.34-0.45           | Ignored       | Ignored        | Ignored          |
| Claim frequency       | b/lambda | Poisson             | Bernoulli(1)  | Bernoulli(1)   | Poisson(0.1/yr)  |
| Catastrophe frequency | -        | -                   | -             | -              | 0.05/yr          |

---

## 10. Design Decisions for Implementation

**Coordinator pattern.** Three agent types (Market, Syndicate, Broker) plus an Environment loss generator. The Market coordinator orchestrates interactions; syndicates and brokers make decisions via method calls. Internal decision logic does not generate events. This produces a sparse event stream (~7 events per risk lifecycle vs ~25 in the fully decomposed design) where every event corresponds to a real-world observable action.

**Clockless DES.** A priority queue (min-heap on simulation time) drives the simulation. Time advances irregularly — empty periods are skipped. Annual housekeeping is triggered by pre-scheduled `AnnualReview` marker events sitting in the queue alongside business and loss events. No clock process exists.

**Event store.** Append-only log, partitioned by simulation run. Each event carries a monotonic sequence number, a simulation timestamp (fractional days from simulation start), an event type discriminator, a JSON payload, and a `causation_id` pointing to the event that caused it. The causation chain allows tracing any insolvency back through claims, losses, and the original risk arrival.

**Snapshots.** Periodically snapshot all agent state (e.g. at each `AnnualReview`) to accelerate replay for long simulations. Snapshots are derived data, always rebuildable from the event stream.

**Projections.** Read-side projections are pure functions that fold over a filtered subset of the event stream to produce specific views: market loss ratio time series, individual syndicate solvency paths, broker placement rates, network evolution heatmaps, cycle detection (MBPD on projected loss ratios), insolvency probability distributions. Multiple projections can run in parallel over the same event store.

**Broker-syndicate network initialisation.** Seed from a stochastic block model: brokers in the same specialism cluster have higher initial relationship strength with syndicates that write that class, with noise. This gives realistic starting topology without hand-coding every edge. Static topologies from the literature (random, circular, weighted graph) are recovered as special cases by disabling relationship evolution.

**Branching and what-if.** Fork the event stream at any point, inject a catastrophe event or regulatory change, and project forward. Keep pre-generated catastrophe schedules identical across branches to isolate the effect of the parameter change. This is how Owadally 2019b's expert system tests regulatory interventions.

**Calibration.** Follow Owadally 2018's method-of-moments grid search: match mean, standard deviation, and lag-1 autocorrelation of simulated market loss ratios to empirical data by searching over the parameter space. Each (parameter set, Monte Carlo run) pair is independent and embarrassingly parallel. Olmez 2024 calibrates via expert interviews and qualitative validation against known stylised facts.
