"""
Insurance Market Event-Sourcing Simulation
Coordinator pattern, clockless DES, broker-syndicate evolving network.
"""
import heapq
import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from collections import defaultdict
from enum import Enum

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PERIL_EXPOSURE_FRACTION    = 0.7
MAX_PREMIUM_TO_CAPITAL_RATIO   = 2.0
RELATIONSHIP_ACCEPT_MULTIPLIER = 1.08
RELATIONSHIP_DECAY_MULTIPLIER  = 0.95
FOLLOW_EXIT_STRENGTH_THRESHOLD = 1.5
FOLLOW_REDUCE_LINE_THRESHOLD   = 1.2
FOLLOW_INCREASE_LINE_THRESHOLD = 0.8
MAX_LOG_MARKUP                 = 0.5
ATTRITIONAL_CLAIM_LAMBDA       = 0.6
FOLLOW_MAX_LINE                = 0.4
CAT_PARETO_SHAPE               = 2
CAT_SEVERITY_SCALE             = 5000.0
ENTRY_LR_THRESHOLD             = 1.00   # industry LR > 100% in a year → new capital enters


# ── Event Kinds ──────────────────────────────────────────────────────────────

class EventKind(str, Enum):
    """Enumeration of all valid simulation event kinds."""
    RISK_ARRIVED         = "RiskArrived"
    ATTRITIONAL_LOSS     = "AttritionalLoss"
    CATASTROPHE_OCCURRED = "CatastropheOccurred"
    ANNUAL_REVIEW        = "AnnualReview"
    RISK_DECLINED           = "RiskDeclined"
    POLICY_BOUND            = "PolicyBound"
    SYNDICATE_INSOLVENT     = "SyndicateInsolvent"
    INDUSTRY_STATS          = "IndustryStatsPublished"
    RISK_ASSIGNED_TO_BROKER = "RiskAssignedToBroker"
    CLAIM_PAID              = "ClaimPaid"
    SYNDICATE_ENTERED       = "SyndicateEntered"


# ── Events ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True, order=True)
class Event:
    sim_time:   float
    heap_order: int             # tie-breaker for heap ordering
    kind:       str  = field(compare=False)
    payload:    dict = field(compare=False, default_factory=dict)
    seq:        int  = field(compare=False, default=0)  # log append order


# ── Syndicate ───────────────────────────────────────────────────────────────

class Syndicate:
    """Models a Lloyd's-style underwriting syndicate with adaptive markup pricing."""

    def __init__(self, sid: int, capital: float, z: float = 0.3, w: float = 0.2,
                 alpha: float = 0.001, beta: float = 0.25,
                 expense_rate: float = 0.35, gamma_div: float = 0.3,
                 initial_claim_est: float = 100.0,
                 initial_std: float = 10.0) -> None:
        """Initialize a syndicate with heterogeneous pricing parameters."""
        self.sid = sid
        self.capital = capital
        self.initial_capital = capital
        self.z = z
        self.w = w
        self.alpha = alpha
        self.beta = beta
        self.expense_rate = expense_rate
        self.gamma_div = gamma_div  # dividend fraction of profit
        self.weighted_avg_claim = initial_claim_est
        self.claims_std_dev = initial_std
        self.markup = 0.0  # log-markup, starts neutral
        self.portfolio: list[int] = []
        self.claims_history: list[tuple[float, float]] = []
        self.premium_income_this_year = 0.0
        self.claims_this_year = 0.0
        self.is_solvent = True
        self.peril_exposures: defaultdict[int, float] = defaultdict(float)
        # For markup update: track last two periods' price and quantity
        self._prev_price = initial_claim_est
        self._prev_prev_price = initial_claim_est
        self._prev_qty = 1
        self._prev_prev_qty = 1
        self._this_year_price_sum = 0.0
        self._this_year_qty = 0

    def quote_as_lead(self, risk_limit: float, peril_region: int,
                      industry_avg_claim: float, max_line: float = 0.5
                      ) -> dict[str, float] | None:
        """Return a lead quote dict with price and line, or None if capacity is unavailable."""
        if not self.is_solvent:
            return None
        pure = self.z * self.weighted_avg_claim + (1 - self.z) * industry_avg_claim
        loading = self.alpha * self.claims_std_dev
        actuarial = pure + loading
        price = actuarial * math.exp(self.markup)
        # Exposure check: don't let any region exceed 30% of capital
        if (self.peril_exposures.get(peril_region, 0) + risk_limit * max_line
                > MAX_PERIL_EXPOSURE_FRACTION * self.capital):
            return None
        # Premium-to-capital ratio check
        if ((self.premium_income_this_year + price * max_line)
                / max(self.capital, 1) > MAX_PREMIUM_TO_CAPITAL_RATIO):
            return None
        return {"price": price, "line": max_line}

    def quote_as_follow(self, risk_limit: float, lead_price: float,
                        peril_region: int, industry_avg_claim: float,
                        default_line: float = 0.25) -> dict[str, float] | None:
        """Return a follow-line quote dict, or None if capacity or price strength is unacceptable."""
        if not self.is_solvent:
            return None
        pure = self.z * self.weighted_avg_claim + (1 - self.z) * industry_avg_claim
        loading = self.alpha * self.claims_std_dev
        own_price = (pure + loading) * math.exp(self.markup)
        strength = own_price / lead_price if lead_price > 0 else 1.0
        if strength > FOLLOW_EXIT_STRENGTH_THRESHOLD:
            return None  # lead price looks far too cheap
        line = default_line
        if strength > FOLLOW_REDUCE_LINE_THRESHOLD:
            line = default_line * 0.5  # take smaller line
        elif strength < FOLLOW_INCREASE_LINE_THRESHOLD:
            line = min(default_line * 1.5, FOLLOW_MAX_LINE)  # take bigger line
        # Premium-to-capital check (mirrors quote_as_lead)
        if ((self.premium_income_this_year + lead_price * line)
                / max(self.capital, 1) > MAX_PREMIUM_TO_CAPITAL_RATIO):
            return None
        # Exposure check
        if (self.peril_exposures.get(peril_region, 0) + risk_limit * line
                > MAX_PERIL_EXPOSURE_FRACTION * self.capital):
            return None
        return {"line": line}

    def book_policy(self, risk_id: int, premium_share: float, risk_limit: float,
                    line: float, peril_region: int) -> None:
        """Record a bound policy and update capital and exposure tracking."""
        self.portfolio.append(risk_id)
        net_premium = premium_share * (1 - self.expense_rate)
        self.capital += net_premium
        self.premium_income_this_year += premium_share
        self.peril_exposures[peril_region] += risk_limit * line
        self._this_year_price_sum += premium_share / max(line, 0.01)
        self._this_year_qty += 1

    def receive_claim(self, amount: float, t: float,
                      full_amount: float | None = None) -> None:
        """Deduct a claim and update EWMA mean and standard deviation.

        amount      -- the syndicate's share of the loss (deducted from capital)
        full_amount -- the total pre-share loss amount, used for EWMA so that
                       actuarial pricing tracks full risk cost, not line-weighted cost
        """
        self.capital -= amount
        self.claims_history.append((t, amount))
        self.claims_this_year += amount
        # EWMA tracks full loss amount so actuarial price reflects whole-risk cost
        ewma_amount = full_amount if full_amount is not None else amount
        # Compute deviation from pre-update mean, then update mean (correct EWMA order)
        dev = ewma_amount - self.weighted_avg_claim
        self.weighted_avg_claim = (self.w * ewma_amount +
                                   (1 - self.w) * self.weighted_avg_claim)
        self.claims_std_dev = math.sqrt(
            self.w * dev**2 + (1 - self.w) * self.claims_std_dev**2
        )
        if self.capital <= 0:
            self.is_solvent = False

    def pay_dividend(self) -> float:
        """Pay dividend from annual profit and return the dividend amount."""
        profit = (self.premium_income_this_year * (1 - self.expense_rate)
                  - self.claims_this_year)
        if profit > 0:
            div = self.gamma_div * profit
            self.capital -= div
            return div
        return 0.0

    def update_markup(self) -> None:
        """Update the log-markup using arc-elasticity of demand from the last two periods."""
        # Arc-elasticity based markup update
        P_t = self._this_year_price_sum / max(self._this_year_qty, 1)
        Q_t = self._this_year_qty

        # Arc-elasticity: use t-1 vs t-2 (one-year lag, as per spec)
        P_tm1 = self._prev_price
        Q_tm1 = max(self._prev_qty, 1)
        P_tm2 = self._prev_prev_price
        Q_tm2 = max(self._prev_prev_qty, 1)

        dQ = Q_tm1 - Q_tm2
        sQ = Q_tm1 + Q_tm2
        dP = P_tm1 - P_tm2
        sP = P_tm1 + P_tm2

        if abs(dP) > 1e-6 and abs(sP) > 1e-6 and abs(sQ) > 1e-6:
            elasticity = (dQ / sQ) / (dP / sP)
            if abs(elasticity) > 1e-6:
                raw_markup = -1.0 / elasticity
                raw_markup = max(-1.0, min(1.0, raw_markup))
                self.markup = self.beta * raw_markup + (1 - self.beta) * self.markup
        self.markup = max(-MAX_LOG_MARKUP, min(MAX_LOG_MARKUP, self.markup))

        # Shift: current becomes t-1, old t-1 becomes t-2
        self._prev_prev_price = self._prev_price
        self._prev_prev_qty   = self._prev_qty
        self._prev_price = P_t
        self._prev_qty   = Q_t
        self._this_year_price_sum = 0.0
        self._this_year_qty = 0
        self.premium_income_this_year = 0.0
        self.claims_this_year = 0.0


# ── Broker ──────────────────────────────────────────────────────────────────

class RelationshipState:
    """Tracks the evolving strength and performance metrics of a broker-syndicate relationship."""

    def __init__(self, strength: float = 0.5, recency: float = 0.3) -> None:
        """Initialize relationship state with given strength and recency weight."""
        self.strength = strength
        self.acceptance_rate = 0.5
        self.price_competitiveness = 0.5
        self.volume = 0
        self.last_interaction = 0.0
        self.recency = recency

    def update(self, accepted: bool, price_ratio: float | None, t: float) -> None:
        """Update relationship metrics after an interaction."""
        if accepted:
            self.strength = min(1.0, self.strength * RELATIONSHIP_ACCEPT_MULTIPLIER)
            self.acceptance_rate = (
                self.recency + (1 - self.recency) * self.acceptance_rate
            )
            if price_ratio is not None:
                comp = max(0, min(1, 1.0 - abs(price_ratio - 1.0)))
                self.price_competitiveness = (self.recency * comp +
                    (1 - self.recency) * self.price_competitiveness)
        else:
            self.strength = max(0.01, self.strength * RELATIONSHIP_DECAY_MULTIPLIER)
            self.acceptance_rate = (1 - self.recency) * self.acceptance_rate
        self.volume += 1
        self.last_interaction = t

    def score(self, specialism_match: float = 1.0) -> float:
        """Return a composite score for syndicate selection."""
        return (self.strength * self.acceptance_rate *
                self.price_competitiveness * specialism_match)


class Broker:
    """Routes risks to syndicates and maintains weighted relationship histories."""

    def __init__(self, bid: int, syndicate_ids: list[int],
                 specialisms: set[int] | None = None,
                 init_strength: float = 0.5, recency: float = 0.3,
                 market_power: float = 1.0) -> None:
        """Initialize broker with relationships to all syndicates."""
        self.bid = bid
        self.market_power = market_power
        self.specialisms: set[int] = specialisms or set()
        self.relationships: dict[int, RelationshipState] = {}
        for sid in syndicate_ids:
            self.relationships[sid] = RelationshipState(
                strength=init_strength + random.uniform(-0.1, 0.1),
                recency=recency
            )

    def select_syndicates(self, peril_region: int, lead_k: int = 3,
                          follow_k: int = 5) -> tuple[list[int], list[int]]:
        """Return ranked lead and follow syndicate IDs for the given peril region."""
        scores = []
        for sid, rel in self.relationships.items():
            sm = 1.2 if peril_region in self.specialisms else 1.0
            scores.append((rel.score(sm), sid))
        scores.sort(reverse=True)
        leads = [sid for _, sid in scores[:lead_k]]
        follows = [sid for _, sid in scores[lead_k:lead_k + follow_k]]
        return leads, follows


# ── Market (Coordinator) ───────────────────────────────────────────────────

class Market:
    """Coordinates the discrete-event simulation of the insurance marketplace."""

    def __init__(self, syndicates: list[Syndicate], brokers: list[Broker],
                 enable_lead_follow: bool = True,
                 allow_entry: bool = False,
                 allow_runoff: bool = False,
                 n_syndicates_max: int = 20,
                 entry_capital: float = 2000.0,
                 entry_syndicate_params: dict | None = None) -> None:
        """Initialize the market with syndicates, brokers, and event infrastructure."""
        self.syndicates: dict[int, Syndicate] = {s.sid: s for s in syndicates}
        self.brokers: dict[int, Broker] = {b.bid: b for b in brokers}
        self.risk_registry: dict[int, dict] = {}
        self._region_to_risks: defaultdict[int, set[int]] = defaultdict(set)
        self.event_store: list[Event] = []
        self.queue: list[Event] = []
        self._heap_seq  = 0
        self._store_seq = 0
        self.industry_avg_claim = 100.0
        self.industry_avg_claim_freq = 0.0
        self.industry_avg_severity = 100.0
        self.industry_avg_loss_ratio = 0.6
        self.industry_avg_premium = 100.0
        self.industry_avg_markup: float = 0.0
        self.enable_lead_follow = enable_lead_follow
        # Market-entry configuration
        self.allow_entry = allow_entry
        self.allow_runoff = allow_runoff
        self.n_syndicates_max = n_syndicates_max
        self._entry_capital = entry_capital
        self._entry_syndicate_params: dict | None = entry_syndicate_params
        self._entry_signal_years = 0
        self._next_sid = len(syndicates)
        # Tracking
        self.yearly_premiums: defaultdict[int, float] = defaultdict(float)
        self.yearly_claims: defaultdict[int, float] = defaultdict(float)
        self.yearly_policies: defaultdict[int, int] = defaultdict(int)
        self.yearly_declines: defaultdict[int, int] = defaultdict(int)
        self.yearly_insolvencies: defaultdict[int, int] = defaultdict(int)
        self.yearly_entries: defaultdict[int, int] = defaultdict(int)
        self.yearly_central_fund: defaultdict[int, float] = defaultdict(float)
        self.syndicate_yearly_premiums: defaultdict[int, defaultdict[int, float]] = (
            defaultdict(lambda: defaultdict(float)))
        self.syndicate_yearly_claims: defaultdict[int, defaultdict[int, float]] = (
            defaultdict(lambda: defaultdict(float)))
        self.syndicate_yearly_capital: defaultdict[int, defaultdict[int, float]] = (
            defaultdict(lambda: defaultdict(float)))
        # Network snapshots
        self.network_snapshots: dict[int, dict[tuple[int, int], float]] = {}
        # Dispatch table
        self._handlers: dict[str, Callable] = {
            EventKind.RISK_ARRIVED:         self._handle_risk,
            EventKind.ATTRITIONAL_LOSS:     self._handle_attritional,
            EventKind.CATASTROPHE_OCCURRED: self._handle_catastrophe,
            EventKind.ANNUAL_REVIEW:        self._handle_annual_review,
        }

    def _emit(self, t: float, kind: str, payload: dict | None = None,
              heap_order: int | None = None) -> Event:
        """Append an event to the audit log and return it."""
        self._store_seq += 1
        ev = Event(sim_time=t, heap_order=heap_order or self._store_seq,
                   kind=kind, payload=payload or {}, seq=self._store_seq)
        self.event_store.append(ev)
        return ev

    def push(self, t: float, kind: str, payload: dict | None = None) -> None:
        """Schedule an event on the priority queue."""
        self._heap_seq += 1
        ev = Event(sim_time=t, heap_order=self._heap_seq, kind=kind,
                   payload=payload or {})
        heapq.heappush(self.queue, ev)

    def run(self) -> None:
        """Process all queued events in chronological order."""
        while self.queue:
            event = heapq.heappop(self.queue)
            self._emit(event.sim_time, event.kind, event.payload,
                       heap_order=event.heap_order)
            self._dispatch(event)

    def _dispatch(self, event: Event) -> None:
        """Route the event to its handler via the dispatch table."""
        handler = self._handlers.get(event.kind)
        if handler is not None:
            handler(event.sim_time, event.payload)

    def _handle_risk(self, t: float, p: dict) -> None:
        """Process a RiskArrived event: solicit quotes, bind policy, schedule losses."""
        risk_id = p["risk_id"]
        region = p["region"]
        limit = p["limit"]
        year = int(t / 365)

        # Assign broker: prefer specialism match, weighted by market_power
        broker_list = list(self.brokers.values())
        specialist_brokers = [b for b in broker_list if region in b.specialisms]
        candidates = specialist_brokers if specialist_brokers else broker_list
        weights = [b.market_power for b in candidates]
        if any(w != 1.0 for w in weights):
            broker = random.choices(candidates, weights=weights, k=1)[0]
        else:
            broker = random.choice(candidates)
        self._emit(t, EventKind.RISK_ASSIGNED_TO_BROKER, {
            "risk_id": risk_id, "broker_id": broker.bid, "region": region
        })

        leads, follows = broker.select_syndicates(region)

        # Solicit lead quotes
        lead_quotes = []
        for sid in leads:
            s = self.syndicates.get(sid)
            if s is None or not s.is_solvent:
                continue
            q = s.quote_as_lead(limit, region, self.industry_avg_claim)
            if q:
                lead_quotes.append((sid, q))
                broker.relationships[sid].update(True, None, t)
            else:
                broker.relationships[sid].update(False, None, t)

        if not lead_quotes:
            self._emit(t, EventKind.RISK_DECLINED, {"risk_id": risk_id})
            self.yearly_declines[year] += 1
            return

        # Best lead = cheapest
        lead_quotes.sort(key=lambda x: x[1]["price"])
        lead_sid, lead_q = lead_quotes[0]
        lead_price = lead_q["price"]
        lead_line = lead_q["line"]

        shares = [(lead_sid, lead_line)]
        remaining = 1.0 - lead_line

        if self.enable_lead_follow:
            # Solicit follow quotes
            for sid in follows:
                if remaining <= 0.01:
                    break
                s = self.syndicates.get(sid)
                if s is None or not s.is_solvent or sid == lead_sid:
                    continue
                fq = s.quote_as_follow(limit, lead_price, region,
                                       self.industry_avg_claim)
                if fq:
                    line = min(fq["line"], remaining)
                    shares.append((sid, line))
                    remaining -= line
                    ratio = (s.weighted_avg_claim * math.exp(s.markup)) / lead_price
                    broker.relationships[sid].update(True, ratio, t)
                else:
                    broker.relationships[sid].update(False, None, t)

        # Bind policy
        policy = {
            "risk_id": risk_id, "region": region, "limit": limit,
            "shares": shares, "lead_price": lead_price,
            "broker_id": broker.bid, "bound_at": t
        }
        self.risk_registry[risk_id] = policy
        self._region_to_risks[region].add(risk_id)

        for sid, line in shares:
            prem = lead_price * line
            self.syndicates[sid].book_policy(risk_id, prem, limit, line, region)
            self.yearly_premiums[year] += prem
            self.syndicate_yearly_premiums[sid][year] += prem

        self._emit(t, EventKind.POLICY_BOUND, {
            "risk_id": risk_id, "lead": lead_sid, "price": lead_price,
            "shares": shares
        })
        self.yearly_policies[year] += 1

        # Schedule attritional losses
        duration = 365  # 1-year policies
        # Target: ~60% loss ratio on average
        # Premium per risk ≈ actuarial_price * total_line ≈ 100 * 0.75
        # Need claims ≈ 0.6 * 75 = 45 per risk on average
        # Use Poisson(0.6) claims per risk, mean severity ≈ 75
        claim_lambda = p.get("claim_lambda", ATTRITIONAL_CLAIM_LAMBDA)
        n_losses = _poisson_sample(claim_lambda)
        for _ in range(n_losses):
            loss_t = t + random.uniform(1, duration)
            cov = 0.7
            sev_mu = p.get("sev_mu")
            mu = sev_mu if sev_mu is not None else self.industry_avg_claim * 0.75
            severity = random.gammavariate(1 / cov**2, mu * cov**2)
            self.push(loss_t, EventKind.ATTRITIONAL_LOSS, {
                "risk_id": risk_id, "amount": severity
            })

    def _handle_attritional(self, t: float, p: dict) -> None:
        """Allocate an attritional loss across all syndicate shares of the policy."""
        risk_id = p["risk_id"]
        amount = p["amount"]
        year = int(t / 365)
        policy = self.risk_registry.get(risk_id)
        if policy is None:
            return
        for sid, share in policy["shares"]:
            claim = amount * share
            s = self.syndicates[sid]
            if s.is_solvent:
                s.receive_claim(claim, t, full_amount=amount)
                self.yearly_claims[year] += claim
                self.syndicate_yearly_claims[sid][year] += claim
                self._emit(t, EventKind.CLAIM_PAID, {
                    "risk_id": risk_id, "sid": sid, "amount": claim,
                    "source": "attritional"
                })
                if not s.is_solvent:
                    self._emit(t, EventKind.SYNDICATE_INSOLVENT, {"sid": sid})
                    self.yearly_insolvencies[year] += 1
            elif self.allow_runoff:
                # Insolvent syndicate in managed runoff: Central Fund pays.
                # Do NOT call s.receive_claim() — EWMA state is frozen.
                self.yearly_claims[year] += claim
                self.yearly_central_fund[year] += claim
                self._emit(t, EventKind.CLAIM_PAID, {
                    "risk_id": risk_id, "sid": sid, "amount": claim,
                    "source": "central_fund"
                })

    def _admit_new_syndicate(self, t: float, year: int) -> None:
        """Instantiate a new syndicate and register it with all brokers."""
        sid = self._next_sid
        self._next_sid += 1
        params: dict = {
            "sid": sid,
            "capital": self._entry_capital * random.uniform(0.8, 1.2),
            "z": random.uniform(0.2, 0.4),
            "w": random.uniform(0.15, 0.25),
            "alpha": 0.001,
            "beta": random.uniform(0.35, 0.5),
            "expense_rate": random.uniform(0.30, 0.40),
            "gamma_div": 0.3,
            "initial_claim_est": 75.0,
            "initial_std": 10.0,
        }
        if self._entry_syndicate_params:
            params.update(self._entry_syndicate_params)
        new_syn = Syndicate(**params)
        self.syndicates[sid] = new_syn
        # Register with all brokers at modest initial strength (newcomer effect)
        for broker in self.brokers.values():
            existing = next(iter(broker.relationships.values()), None)
            recency = existing.recency if existing else 0.4
            broker.relationships[sid] = RelationshipState(strength=0.3, recency=recency)
        self._emit(t, EventKind.SYNDICATE_ENTERED,
                   {"sid": sid, "year": year, "capital": new_syn.capital})
        self.yearly_entries[year] += 1

    def _handle_catastrophe(self, t: float, p: dict) -> None:
        """Allocate catastrophe losses across active policies in the affected region."""
        region = p["region"]
        # Key is "damage" (consistent with push site in build_simulation)
        total_damage = p["damage"]
        year = int(t / 365)

        # Only affect active policies (1-year duration from bound_at)
        candidate_ids = self._region_to_risks.get(region, set())
        affected = [
            (rid, self.risk_registry[rid])
            for rid in candidate_ids
            if self.risk_registry[rid]["bound_at"] + 365 > t
        ]
        if not affected:
            return
        total_exposure = sum(pol["limit"] for _, pol in affected)
        if total_exposure <= 0:
            return

        for rid, pol in affected:
            policy_loss = min(
                total_damage * (pol["limit"] / total_exposure),
                pol["limit"]
            )
            for sid, share in pol["shares"]:
                claim = policy_loss * share
                s = self.syndicates[sid]
                if s.is_solvent:
                    s.receive_claim(claim, t, full_amount=policy_loss)
                    self.yearly_claims[year] += claim
                    self.syndicate_yearly_claims[sid][year] += claim
                    self._emit(t, EventKind.CLAIM_PAID, {
                        "risk_id": rid, "sid": sid, "amount": claim,
                        "source": "catastrophe", "region": region
                    })
                    if not s.is_solvent:
                        self._emit(t, EventKind.SYNDICATE_INSOLVENT, {"sid": sid})
                        self.yearly_insolvencies[year] += 1
                elif self.allow_runoff:
                    # Insolvent syndicate in managed runoff: Central Fund pays.
                    self.yearly_claims[year] += claim
                    self.yearly_central_fund[year] += claim
                    self._emit(t, EventKind.CLAIM_PAID, {
                        "risk_id": rid, "sid": sid, "amount": claim,
                        "source": "central_fund", "region": region
                    })
        # No re-emit: the CatastropheOccurred event was already logged by run()

    def _handle_annual_review(self, t: float, p: dict) -> None:
        """Run end-of-year accounting: network snapshot, industry stats, dividends, markup."""
        year = p["year"]

        # Snapshot network
        snap: dict[tuple[int, int], float] = {}
        for bid, broker in self.brokers.items():
            for sid, rel in broker.relationships.items():
                snap[(bid, sid)] = rel.strength
        self.network_snapshots[year] = snap

        # Snapshot syndicate capital
        for sid, s in self.syndicates.items():
            self.syndicate_yearly_capital[sid][year] = s.capital

        # Compute industry averages
        solvent = [s for s in self.syndicates.values() if s.is_solvent]
        if solvent:
            self.industry_avg_claim  = sum(s.weighted_avg_claim for s in solvent) / len(solvent)
            self.industry_avg_markup = sum(s.markup for s in solvent) / len(solvent)

        # Market entry: one new syndicate if industry LR > 100% in a year
        year_prem = self.yearly_premiums.get(year, 0.0)
        year_lr = (self.yearly_claims.get(year, 0.0) / year_prem if year_prem > 0 else 0.0)
        if self.allow_entry and len(solvent) < self.n_syndicates_max:
            if year_lr > ENTRY_LR_THRESHOLD:
                self._admit_new_syndicate(t, year)

        # Compute additional IndustryStats fields
        total_prem   = self.yearly_premiums.get(year, 0.0)
        total_claims = self.yearly_claims.get(year, 0.0)
        total_pols   = max(self.yearly_policies.get(year, 0), 1)

        self.industry_avg_premium = total_prem / total_pols
        self.industry_avg_loss_ratio = (total_claims / total_prem
                                        if total_prem > 0 else 0.0)
        self.industry_avg_severity = self.industry_avg_claim
        avg_sev = self.industry_avg_claim if self.industry_avg_claim > 0 else 1.0
        self.industry_avg_claim_freq = (total_claims / avg_sev) / total_pols

        self._emit(t, EventKind.INDUSTRY_STATS, {
            "year":           year,
            "avg_claim_freq": self.industry_avg_claim_freq,
            "avg_severity":   self.industry_avg_severity,
            "avg_loss_ratio": self.industry_avg_loss_ratio,
            "avg_premium":    self.industry_avg_premium,
        })

        # Dividends and markup updates
        for s in self.syndicates.values():
            if s.is_solvent:
                s.pay_dividend()
                s.update_markup()

        # Reset peril exposures for new year
        for s in self.syndicates.values():
            s.peril_exposures.clear()

        # Prune expired entries from the region index
        year_start_t = year * 365
        for risk_ids in self._region_to_risks.values():
            risk_ids -= {
                rid for rid in risk_ids
                if self.risk_registry[rid]["bound_at"] + 365 <= year_start_t
            }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _poisson_sample(lam: float) -> int:
    """Sample from Poisson(lam) via inverse-CDF using module random state."""
    n = 0
    u = random.random()
    prob = math.exp(-lam)
    cumulative = prob
    while u > cumulative:
        n += 1
        prob *= lam / n
        cumulative += prob
    return n


# ── Simulation Setup ────────────────────────────────────────────────────────

def build_simulation(
    n_syndicates: int = 10,
    n_brokers: int = 4,
    n_regions: int = 5,
    horizon_years: int = 50,
    risks_per_year: int = 25,
    initial_capital: float = 2000.0,
    cat_freq: float | dict[int, float] = 0.05,
    enable_cats: bool = True,
    enable_lead_follow: bool = True,
    evolve_network: bool = True,
    seed: int = 42,
    syndicate_params: dict | None = None,
    lob_params: dict[int, dict] | None = None,
    broker_power: float = 0.0,
    allow_entry: bool = False,
    allow_runoff: bool = False,
    n_syndicates_max: int = 20,
) -> Market:
    """Build and return a Market populated with syndicates, brokers, and scheduled events."""
    random.seed(seed)

    # Create syndicates with slight heterogeneity
    syndicates = []
    for i in range(n_syndicates):
        params = {
            "sid": i, "capital": initial_capital * random.uniform(0.8, 1.2),
            "z": random.uniform(0.2, 0.4),
            "w": random.uniform(0.15, 0.25),
            "alpha": 0.001,
            "beta": random.uniform(0.35, 0.5),
            "expense_rate": random.uniform(0.30, 0.40),
            "gamma_div": 0.3,
            "initial_claim_est": 75.0,
            "initial_std": 10.0
        }
        if syndicate_params:
            params.update(syndicate_params)
        syndicates.append(Syndicate(**params))

    # Create brokers
    brokers = []
    for i in range(n_brokers):
        specs = set(random.sample(range(n_regions),
                                  k=random.randint(1, max(1, n_regions // 2))))
        init_str = 0.5
        recency = 0.4 if evolve_network else 0.0  # 0 = no evolution
        brokers.append(Broker(i, [s.sid for s in syndicates],
                              specialisms=specs, init_strength=init_str,
                              recency=recency))

    # Assign Zipf market-power weights when requested (rank 1 = most powerful)
    if broker_power > 0.0:
        for rank, b in enumerate(brokers, start=1):
            b.market_power = 1.0 / (rank ** broker_power)

    market = Market(syndicates, brokers,
                    enable_lead_follow=enable_lead_follow,
                    allow_entry=allow_entry,
                    allow_runoff=allow_runoff,
                    n_syndicates_max=n_syndicates_max,
                    entry_capital=initial_capital,
                    entry_syndicate_params=syndicate_params)

    # Schedule risk arrivals (Poisson process)
    risk_id = 0
    for year in range(horizon_years):
        n_risks = max(1, int(random.gauss(risks_per_year, 3)))
        for _ in range(n_risks):
            t_arrival = year * 365 + random.uniform(0, 365)
            region = random.randint(0, n_regions - 1)
            if lob_params is not None:
                lp = lob_params.get(region, {})
                lo, hi = lp.get("limit", (500, 2000))
                limit = random.uniform(lo, hi)
                payload: dict = {
                    "risk_id": risk_id, "region": region, "limit": limit,
                    "claim_lambda": lp.get("lambda", ATTRITIONAL_CLAIM_LAMBDA),
                    "sev_mu": lp.get("sev_mu", None),
                }
            else:
                limit = random.uniform(500, 2000)
                payload = {"risk_id": risk_id, "region": region, "limit": limit}
            market.push(t_arrival, EventKind.RISK_ARRIVED, payload)
            risk_id += 1

    # Schedule catastrophes: Poisson per region, Pareto severity
    if enable_cats:
        _freq = (cat_freq if isinstance(cat_freq, dict)
                 else {r: cat_freq for r in range(n_regions)})
        for region in range(n_regions):
            lam = _freq.get(region, 0.0) * horizon_years
            n_cats = _poisson_sample(lam)
            for _ in range(n_cats):
                t_cat = random.uniform(0, horizon_years * 365)
                damage = random.paretovariate(CAT_PARETO_SHAPE) * CAT_SEVERITY_SCALE
                market.push(t_cat, EventKind.CATASTROPHE_OCCURRED, {
                    "region": region, "damage": damage
                })

    # Schedule annual reviews
    for year in range(horizon_years):
        market.push((year + 1) * 365 - 1, EventKind.ANNUAL_REVIEW, {"year": year})

    return market


# ── Experiments ─────────────────────────────────────────────────────────────

def experiment_underwriting_cycle(seeds=range(15), horizon=80):
    """Experiment 1: Show underwriting cycles emerge."""
    all_lr = {}
    for seed in seeds:
        m = build_simulation(horizon_years=horizon, enable_cats=False,
                             seed=seed)
        m.run()
        lr = {}
        for y in range(horizon):
            p = m.yearly_premiums.get(y, 0)
            c = m.yearly_claims.get(y, 0)
            if p > 0:
                lr[y] = c / p
        all_lr[seed] = lr
    return all_lr


def experiment_catastrophe_impact(seeds=range(15), horizon=80):
    """Experiment 2: Compare with and without catastrophes."""
    results = {"with_cats": {}, "without_cats": {}}
    for seed in seeds:
        for cats, label in [(True, "with_cats"), (False, "without_cats")]:
            m = build_simulation(horizon_years=horizon, enable_cats=cats,
                                 cat_freq=0.08, seed=seed)
            m.run()
            lr = {}
            insolvencies = {}
            for y in range(horizon):
                p = m.yearly_premiums.get(y, 0)
                c = m.yearly_claims.get(y, 0)
                if p > 0:
                    lr[y] = c / p
                insolvencies[y] = m.yearly_insolvencies.get(y, 0)
            results[label][seed] = {"lr": lr, "insolvencies": insolvencies}
    return results


def experiment_network_evolution(horizon=50):
    """Experiment 3: Show network concentration over time."""
    m = build_simulation(horizon_years=horizon, seed=42, evolve_network=True)
    m.run()
    return m.network_snapshots, m.syndicate_yearly_premiums


def experiment_lead_follow(seeds=range(15), horizon=60):
    """Experiment 4: Compare with and without lead-follow."""
    results = {"with_lf": {}, "without_lf": {}}
    for seed in seeds:
        for lf, label in [(True, "with_lf"), (False, "without_lf")]:
            m = build_simulation(horizon_years=horizon, enable_lead_follow=lf,
                                 seed=seed)
            m.run()
            lr = {}
            for y in range(horizon):
                p = m.yearly_premiums.get(y, 0)
                c = m.yearly_claims.get(y, 0)
                if p > 0:
                    lr[y] = c / p
            results[label][seed] = lr
    return results


def experiment_path_dependence(n_seeds=20, horizon=60):
    """Experiment 5: Show divergence across seeds."""
    all_capital = {}
    for seed in range(n_seeds):
        m = build_simulation(horizon_years=horizon, enable_cats=True,
                             cat_freq=0.06, seed=seed)
        m.run()
        # Total market capital by year
        cap = {}
        for y in range(horizon):
            total = 0
            for sid in m.syndicate_yearly_capital:
                total += m.syndicate_yearly_capital[sid].get(y, 0)
            cap[y] = total
        all_capital[seed] = cap
    return all_capital


