"""
diagnostics.py — Underwriting Cycle Diagnostics for the Insurance Market Simulation
Produces visualizations and quantified cycle statistics, plus documented parameter sweeps.
"""

# ── Section 0: Imports and plot style ────────────────────────────────────────

import math
import statistics
import time
from collections import defaultdict
from typing import NamedTuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from simulation import (
    Market, Syndicate, Broker, EventKind, build_simulation
)

plt.rcParams.update({
    "figure.figsize": (12, 8),
    "axes.grid": True,
    "grid.alpha": 0.3,
    "font.size": 10,
})
OUTPUT_DIR = "."   # PNGs saved to working directory


# ── Section 1: DiagnosticMarket subclass ─────────────────────────────────────

class DiagnosticMarket(Market):
    """Market subclass that snapshots per-syndicate internal state before markup updates."""

    def __init__(self, syndicates: list[Syndicate], brokers: list[Broker],
                 enable_lead_follow: bool = True) -> None:
        super().__init__(syndicates, brokers, enable_lead_follow=enable_lead_follow)
        self.syndicate_yearly_markup:    defaultdict[int, dict[int, float]] = defaultdict(dict)
        self.syndicate_yearly_wac:       defaultdict[int, dict[int, float]] = defaultdict(dict)
        self.syndicate_yearly_actuarial: defaultdict[int, dict[int, float]] = defaultdict(dict)

    def _handle_annual_review(self, t: float, p: dict) -> None:
        """Snapshot per-syndicate state BEFORE parent calls update_markup."""
        year = p["year"]
        for sid, s in self.syndicates.items():
            if s.is_solvent:
                self.syndicate_yearly_markup[sid][year] = s.markup
                self.syndicate_yearly_wac[sid][year] = s.weighted_avg_claim
                pure = (s.z * s.weighted_avg_claim
                        + (1 - s.z) * self.industry_avg_claim)
                self.syndicate_yearly_actuarial[sid][year] = (
                    pure + s.alpha * s.claims_std_dev
                )
        super()._handle_annual_review(t, p)


def build_diagnostic_simulation(**kwargs) -> DiagnosticMarket:
    """Build a DiagnosticMarket using the same setup logic as build_simulation."""
    plain = build_simulation(**kwargs)
    dm = DiagnosticMarket(
        list(plain.syndicates.values()),
        list(plain.brokers.values()),
        enable_lead_follow=plain.enable_lead_follow,
    )
    dm.queue = plain.queue
    dm.industry_avg_claim = plain.industry_avg_claim
    return dm


# ── Section 2: Data extraction helpers ───────────────────────────────────────

def extract_yearly_loss_ratios(market: Market, horizon: int) -> dict[int, float]:
    """Return {year: loss_ratio} from market yearly_claims / yearly_premiums."""
    result = {}
    for y in range(horizon):
        prem = market.yearly_premiums.get(y, 0.0)
        clms = market.yearly_claims.get(y, 0.0)
        if prem > 0:
            result[y] = clms / prem
    return result


def extract_industry_stats_series(market: Market) -> dict[int, dict]:
    """Return {year: stats_dict} parsed from INDUSTRY_STATS events in event_store."""
    result = {}
    for ev in market.event_store:
        if ev.kind == EventKind.INDUSTRY_STATS:
            year = ev.payload.get("year")
            if year is not None:
                result[year] = dict(ev.payload)
    return result


def extract_market_capital_series(market: Market, horizon: int) -> dict[int, float]:
    """Return {year: total_capital} summed across all syndicates."""
    result = {}
    for y in range(horizon):
        total = 0.0
        for sid in market.syndicate_yearly_capital:
            total += market.syndicate_yearly_capital[sid].get(y, 0.0)
        result[y] = total
    return result


def extract_syndicate_series(market: Market, horizon: int) -> dict[int, dict[int, dict]]:
    """Return {sid: {year: {premium, claims, capital, markup?, wac?}}}."""
    result = {}
    is_diag = isinstance(market, DiagnosticMarket)
    for sid in market.syndicates:
        sid_data = {}
        for y in range(horizon):
            entry: dict = {
                "premium": market.syndicate_yearly_premiums[sid].get(y, 0.0),
                "claims":  market.syndicate_yearly_claims[sid].get(y, 0.0),
                "capital": market.syndicate_yearly_capital[sid].get(y, 0.0),
            }
            if is_diag:
                dm = market  # type: DiagnosticMarket
                if y in dm.syndicate_yearly_markup[sid]:
                    entry["markup"] = dm.syndicate_yearly_markup[sid][y]
                if y in dm.syndicate_yearly_wac[sid]:
                    entry["wac"] = dm.syndicate_yearly_wac[sid][y]
            sid_data[y] = entry
        result[sid] = sid_data
    return result


def extract_solvent_count(market: Market, horizon: int) -> dict[int, int]:
    """Return {year: n_solvent} where solvent = capital > 0 at year-end."""
    result = {}
    for y in range(horizon):
        count = sum(
            1 for sid in market.syndicate_yearly_capital
            if market.syndicate_yearly_capital[sid].get(y, 0.0) > 0
        )
        result[y] = count
    return result


def decompose_price_forces(dm: DiagnosticMarket, horizon: int) -> dict[int, dict]:
    """Return {year: {actuarial_avg, markup_factor_avg, implied_price, industry_premium}}."""
    result = {}
    industry_stats = extract_industry_stats_series(dm)
    for y in range(horizon):
        actuarial_vals = []
        markup_factor_vals = []
        for sid in dm.syndicates:
            if y in dm.syndicate_yearly_actuarial[sid]:
                actuarial_vals.append(dm.syndicate_yearly_actuarial[sid][y])
            if y in dm.syndicate_yearly_markup[sid]:
                markup_factor_vals.append(math.exp(dm.syndicate_yearly_markup[sid][y]))
        if not actuarial_vals:
            continue
        act_avg = sum(actuarial_vals) / len(actuarial_vals)
        mf_avg = (sum(markup_factor_vals) / len(markup_factor_vals)
                  if markup_factor_vals else 1.0)
        ind_prem = industry_stats.get(y, {}).get("avg_premium", act_avg * mf_avg)
        result[y] = {
            "actuarial_avg":    act_avg,
            "markup_factor_avg": mf_avg,
            "implied_price":    act_avg * mf_avg,
            "industry_premium": ind_prem,
        }
    return result


def extract_per_region_lr(
    market: Market,
    horizon: int,
    n_regions: int = 5,
) -> dict[int, dict[int, float]]:
    """Return {region: {year: loss_ratio}} from risk_registry + event_store.

    Premium per (region, year) is derived from bound policy data in
    risk_registry. Claims per (region, year) are summed from CLAIM_PAID
    events, resolved to region via risk_registry.
    """
    region_premiums: dict[int, dict[int, float]] = {r: {} for r in range(n_regions)}
    for policy in market.risk_registry.values():
        region = policy["region"]
        year = int(policy["bound_at"] / 365)
        if year >= horizon:
            continue
        total_prem = sum(policy["lead_price"] * line for _, line in policy["shares"])
        region_premiums[region][year] = region_premiums[region].get(year, 0.0) + total_prem

    region_claims: dict[int, dict[int, float]] = {r: {} for r in range(n_regions)}
    for ev in market.event_store:
        if ev.kind != EventKind.CLAIM_PAID:
            continue
        year = int(ev.sim_time / 365)
        if year >= horizon:
            continue
        rid = ev.payload.get("risk_id")
        if rid is None:
            continue
        pol = market.risk_registry.get(rid)
        if pol is None:
            continue
        r = pol["region"]
        region_claims[r][year] = region_claims[r].get(year, 0.0) + ev.payload.get("amount", 0.0)

    result: dict[int, dict[int, float]] = {}
    for r in range(n_regions):
        lr_by_year: dict[int, float] = {}
        for y in range(horizon):
            prem = region_premiums[r].get(y, 0.0)
            if prem > 0:
                lr_by_year[y] = region_claims[r].get(y, 0.0) / prem
        result[r] = lr_by_year
    return result


# ── Section 3: Cycle detection ────────────────────────────────────────────────

class CycleStats(NamedTuple):
    period_years:  float | None   # dominant period
    amplitude:     float          # mean(peaks) − mean(troughs)
    cv:            float          # coefficient of variation
    acf_at_period: float          # ACF value at estimated period lag
    n_peaks:       int
    mean_lr:       float
    std_lr:        float


def find_local_maxima(series: list[float], min_distance: int = 3) -> list[int]:
    """Return indices of local maxima with minimum separation."""
    n = len(series)
    candidates = []
    for i in range(1, n - 1):
        if series[i] > series[i - 1] and series[i] > series[i + 1]:
            candidates.append(i)
    # Enforce min_distance by keeping highest within each window
    if not candidates:
        return []
    filtered = [candidates[0]]
    for idx in candidates[1:]:
        if idx - filtered[-1] >= min_distance:
            filtered.append(idx)
        elif series[idx] > series[filtered[-1]]:
            filtered[-1] = idx
    return filtered


def find_local_minima(series: list[float], min_distance: int = 3) -> list[int]:
    """Return indices of local minima with minimum separation."""
    neg = [-x for x in series]
    return find_local_maxima(neg, min_distance)


def _centered_moving_average(series: list[float], window: int = 5) -> list[float]:
    """Compute centered moving average, padding edges with edge values."""
    n = len(series)
    half = window // 2
    result = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        result.append(sum(series[lo:hi]) / (hi - lo))
    return result


def detect_cycle(loss_ratios: dict[int, float], max_lag: int = 25) -> CycleStats:
    """Detect cycle period and amplitude in a loss ratio series."""
    # Skip year 0 as burn-in; sort by year
    sorted_years = sorted(y for y in loss_ratios if y > 0)
    if len(sorted_years) < 6:
        lr_vals = [loss_ratios[y] for y in sorted_years]
        mean_lr = statistics.mean(lr_vals) if lr_vals else 0.0
        std_lr = statistics.stdev(lr_vals) if len(lr_vals) > 1 else 0.0
        return CycleStats(None, 0.0, 0.0, 0.0, 0, mean_lr, std_lr)

    raw = [loss_ratios[y] for y in sorted_years]
    n = len(raw)
    mean_lr = statistics.mean(raw)
    std_lr = statistics.stdev(raw) if n > 1 else 0.0
    cv = std_lr / mean_lr if mean_lr > 0 else 0.0

    # Detrend: subtract centered 5-year moving average
    trend = _centered_moving_average(raw, window=5)
    detrended = [raw[i] - trend[i] for i in range(n)]

    # Compute ACF at lags 1..min(max_lag, n//3)
    max_usable_lag = min(max_lag, n // 3)
    if max_usable_lag < 2:
        return CycleStats(None, 0.0, cv, 0.0, 0, mean_lr, std_lr)

    var = sum(x**2 for x in detrended) / n
    acf_vals = []
    for lag in range(1, max_usable_lag + 1):
        cov = sum(detrended[i] * detrended[i + lag]
                  for i in range(n - lag)) / n
        acf_vals.append(cov / var if var > 1e-12 else 0.0)

    # ACF primary: first local max beyond lag 2
    acf_period = None
    acf_at_period = 0.0
    for i in range(2, len(acf_vals) - 1):
        if acf_vals[i] > acf_vals[i - 1] and acf_vals[i] > acf_vals[i + 1]:
            acf_period = i + 1  # lag is 1-indexed
            acf_at_period = acf_vals[i]
            break

    # Peak-detection secondary estimate
    peak_idxs = find_local_maxima(raw, min_distance=3)
    trough_idxs = find_local_minima(raw, min_distance=3)

    inter_peak_dists = []
    for j in range(1, len(peak_idxs)):
        inter_peak_dists.append(peak_idxs[j] - peak_idxs[j - 1])
    peak_period = (statistics.mean(inter_peak_dists)
                   if inter_peak_dists else None)

    peak_vals = [raw[i] for i in peak_idxs]
    trough_vals = [raw[i] for i in trough_idxs]
    amplitude = ((statistics.mean(peak_vals) - statistics.mean(trough_vals))
                 if peak_vals and trough_vals else 0.0)

    # Choose primary estimate; warn if they differ by > 2 years
    period = acf_period if acf_period is not None else peak_period
    if acf_period is not None and peak_period is not None:
        if abs(acf_period - peak_period) > 2:
            print(f"  [cycle] Warning: ACF period ({acf_period:.1f} yr) and "
                  f"peak-detection period ({peak_period:.1f} yr) differ by "
                  f">{abs(acf_period - peak_period):.1f} yr")

    return CycleStats(
        period_years=period,
        amplitude=amplitude,
        cv=cv,
        acf_at_period=acf_at_period,
        n_peaks=len(peak_idxs),
        mean_lr=mean_lr,
        std_lr=std_lr,
    )


# ── Section 4: Plot functions ─────────────────────────────────────────────────

def plot_01_loss_ratio_cycle(loss_ratios_per_seed: dict[int, dict[int, float]],
                             stats: CycleStats,
                             filename: str) -> str:
    """Two-panel: ensemble LR with band + ACF bars."""
    # Collect all years present
    all_years_set: set[int] = set()
    for lr_dict in loss_ratios_per_seed.values():
        all_years_set.update(lr_dict.keys())
    all_years = sorted(all_years_set)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

    # Compute ensemble mean and std per year
    means, stds = [], []
    for y in all_years:
        vals = [lr_dict[y] for lr_dict in loss_ratios_per_seed.values()
                if y in lr_dict]
        means.append(statistics.mean(vals) if vals else float("nan"))
        stds.append(statistics.stdev(vals) if len(vals) > 1 else 0.0)

    mean_arr = np.array(means)
    std_arr = np.array(stds)
    yrs_arr = np.array(all_years)

    # Top panel
    for lr_dict in loss_ratios_per_seed.values():
        ys = [y for y in all_years if y in lr_dict]
        vs = [lr_dict[y] for y in ys]
        ax1.plot(ys, vs, color="grey", alpha=0.25, linewidth=0.8)

    valid = ~np.isnan(mean_arr)
    ax1.plot(yrs_arr[valid], mean_arr[valid], color="blue", linewidth=2,
             label="Ensemble mean")
    ax1.fill_between(yrs_arr[valid],
                     mean_arr[valid] - std_arr[valid],
                     mean_arr[valid] + std_arr[valid],
                     alpha=0.2, color="blue", label="±1σ")
    ax1.axhline(1.0, color="red", linestyle="--", linewidth=1, label="LR = 1.0")
    ax1.axhline(0.6, color="green", linestyle="--", linewidth=1, label="LR = 0.6")
    ax1.set_ylabel("Loss Ratio")
    ax1.set_title("Underwriting Cycle — Loss Ratio")
    ax1.legend(fontsize=8)

    # Bottom panel: ACF
    raw = [m for y, m in zip(all_years, means) if not math.isnan(m) and y > 0]
    n = len(raw)
    max_lag = min(25, n // 3)
    if max_lag >= 2 and n > 0:
        trend = _centered_moving_average(raw, 5)
        detrended = [raw[i] - trend[i] for i in range(n)]
        dvar = sum(x**2 for x in detrended) / n
        acf_lags, acf_bars = [], []
        for lag in range(1, max_lag + 1):
            cov = sum(detrended[i] * detrended[i + lag]
                      for i in range(n - lag)) / n
            acf_lags.append(lag)
            acf_bars.append(cov / dvar if dvar > 1e-12 else 0.0)
        ax2.bar(acf_lags, acf_bars, color="steelblue", alpha=0.7)
        if stats.period_years is not None:
            ax2.axvline(stats.period_years, color="red", linewidth=2,
                        label=f"Period ≈ {stats.period_years:.1f} yr")
            ax2.legend(fontsize=8)
    ax2.set_xlabel("Lag (years)")
    ax2.set_ylabel("ACF")
    ax2.set_title("Autocorrelation of Detrended Loss Ratio")

    fig.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_02_force_decomposition(decomp: dict[int, dict],
                                loss_ratios: dict[int, float],
                                filename: str) -> str:
    """Three-panel: actuarial price + markup factor / loss ratio / log-markup."""
    years = sorted(decomp.keys())
    if not years:
        return ""

    act = [decomp[y]["actuarial_avg"] for y in years]
    mf  = [decomp[y]["markup_factor_avg"] for y in years]
    lr  = [loss_ratios.get(y, float("nan")) for y in years]

    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

    # Top: actuarial price (left Y) + markup factor (right Y)
    ax1 = axes[0]
    ax1r = ax1.twinx()
    ax1.plot(years, act, color="blue", linewidth=2, label="Actuarial price")
    ax1r.plot(years, mf, color="orange", linewidth=2, label="Markup factor exp(m)")
    # Shade where markup factor < 1.0
    mf_arr = np.array(mf)
    yr_arr = np.array(years)
    ax1r.fill_between(yr_arr, mf_arr, 1.0,
                      where=mf_arr < 1.0, alpha=0.15, color="red",
                      label="Markup < 1 (soft)")
    ax1.set_ylabel("Actuarial Price", color="blue")
    ax1r.set_ylabel("Markup Factor", color="orange")
    ax1.set_title("Price Force Decomposition")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax1r.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, fontsize=8, loc="upper left")

    # Middle: loss ratio with green/red background
    ax2 = axes[1]
    lr_arr = np.array(lr)
    ax2.plot(years, lr_arr, color="black", linewidth=2, label="Loss Ratio")
    ax2.fill_between(yr_arr, 0, 1,
                     where=lr_arr < 0.7, alpha=0.15, color="green",
                     label="Profitable (LR<0.7)")
    ax2.fill_between(yr_arr, 0, 1,
                     where=lr_arr > 0.9, alpha=0.15, color="red",
                     label="Unprofitable (LR>0.9)")
    ax2.axhline(1.0, color="red", linestyle="--", linewidth=0.8)
    ax2.axhline(0.6, color="green", linestyle="--", linewidth=0.8)
    ax2.set_ylabel("Loss Ratio")
    ax2.legend(fontsize=8)

    # Bottom: raw log-markup with orange/blue shading
    ax3 = axes[2]
    # Compute median log-markup per year from DiagnosticMarket (not available here;
    # derive from mf via log)
    log_mf = [math.log(max(m, 1e-6)) for m in mf]
    log_arr = np.array(log_mf)
    ax3.plot(years, log_arr, color="black", linewidth=1.5)
    ax3.fill_between(yr_arr, log_arr, 0,
                     where=log_arr > 0, alpha=0.25, color="orange",
                     label="Positive markup (hard)")
    ax3.fill_between(yr_arr, log_arr, 0,
                     where=log_arr < 0, alpha=0.25, color="steelblue",
                     label="Negative markup (soft)")
    ax3.axhline(0, color="black", linewidth=0.8)
    ax3.set_xlabel("Year")
    ax3.set_ylabel("Log Markup")
    ax3.legend(fontsize=8)

    fig.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_03_capital_insolvencies(capital_by_sid: dict[int, dict[int, float]],
                                 market_capital: dict[int, float],
                                 insolvency_counts: dict[int, int],
                                 solvent_counts: dict[int, int],
                                 filename: str) -> str:
    """Two-panel: per-syndicate capital lines + insolvency bars."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

    years_cap = sorted(market_capital.keys())
    colors = plt.cm.tab20.colors  # type: ignore[attr-defined]

    for i, (_, cap_dict) in enumerate(capital_by_sid.items()):
        ys = sorted(cap_dict.keys())
        vs = [cap_dict[y] for y in ys]
        ax1.plot(ys, vs, color=colors[i % len(colors)],
                 alpha=0.5, linewidth=0.9)

    total_cap = [market_capital.get(y, 0.0) for y in years_cap]
    ax1.plot(years_cap, total_cap, color="black", linewidth=2.5,
             label="Total market capital")
    ax1.set_ylabel("Capital")
    ax1.set_title("Syndicate Capital & Insolvencies")
    ax1.legend(fontsize=8)

    # Bottom panel
    all_years = sorted(set(insolvency_counts) | set(solvent_counts))
    ins_vals = [insolvency_counts.get(y, 0) for y in all_years]
    sol_vals = [solvent_counts.get(y, 0) for y in all_years]

    # Cumulative insolvencies
    cumulative_ins = []
    running = 0
    for v in ins_vals:
        running += v
        cumulative_ins.append(running)

    ax2.bar(all_years, cumulative_ins, color="crimson", alpha=0.6,
            label="Cumulative insolvencies")
    ax2b = ax2.twinx()
    ax2b.step(all_years, sol_vals, where="post", color="navy",
              linewidth=2, label="Solvent syndicates")
    ax2.set_xlabel("Year")
    ax2.set_ylabel("Cumulative Insolvencies", color="crimson")
    ax2b.set_ylabel("Solvent Count", color="navy")
    lines1, labels1 = ax2.get_legend_handles_labels()
    lines2, labels2 = ax2b.get_legend_handles_labels()
    ax2.legend(lines1 + lines2, labels1 + labels2, fontsize=8)

    fig.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_04_sensitivity(sweep_results: list,
                        param_name: str,
                        filename: str) -> str:
    """Two-panel: ensemble LR per param value + cycle period vs param value."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

    param_values = [r.param_value for r in sweep_results]
    cmap = plt.cm.viridis  # type: ignore[attr-defined]
    colors = [cmap(i / max(len(sweep_results) - 1, 1))
              for i in range(len(sweep_results))]

    for r, c in zip(sweep_results, colors):
        if r.lr_by_seed:
            all_yrs_set: set[int] = set()
            for lr_d in r.lr_by_seed.values():
                all_yrs_set.update(lr_d.keys())
            all_yrs = sorted(all_yrs_set)
            means = []
            for y in all_yrs:
                vals = [r.lr_by_seed[s][y] for s in r.lr_by_seed if y in r.lr_by_seed[s]]
                means.append(statistics.mean(vals) if vals else float("nan"))
            ax1.plot(all_yrs, means, color=c,
                     label=f"{param_name}={r.param_value:.2f}", linewidth=1.5)

    ax1.axhline(1.0, color="red", linestyle="--", linewidth=0.8)
    ax1.axhline(0.6, color="green", linestyle="--", linewidth=0.8)
    ax1.set_ylabel("Mean Loss Ratio")
    ax1.set_title(f"Sensitivity: {param_name}")
    ax1.legend(fontsize=7, ncol=2)

    periods = [r.mean_period for r in sweep_results]
    period_stds = [r.std_period for r in sweep_results]
    ax2.errorbar(param_values, periods, yerr=period_stds,
                 fmt="o-", color="steelblue", capsize=4, linewidth=2)
    ax2.set_xlabel(param_name)
    ax2.set_ylabel("Cycle Period (years)")
    ax2.set_title(f"Cycle Period vs {param_name}")
    if hasattr(sweep_results[0], "hypothesis") and sweep_results[0].hypothesis:
        fig.text(0.5, 0.01, sweep_results[0].hypothesis,
                 ha="center", fontsize=9, style="italic",
                 wrap=True)

    fig.tight_layout(rect=(0, 0.05, 1, 1))
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_05_phase_portrait(decomp: dict[int, dict],
                           filename: str) -> str:
    """Phase portrait: actuarial (normalized) vs markup factor, coloured by year."""
    years = sorted(decomp.keys())
    if len(years) < 3:
        return ""

    # Normalize actuarial price to year-2 value
    ref_act = decomp.get(2, decomp[years[0]])["actuarial_avg"]
    if ref_act == 0:
        ref_act = 1.0

    act_norm = [decomp[y]["actuarial_avg"] / ref_act for y in years]
    mf       = [decomp[y]["markup_factor_avg"] for y in years]

    fig, ax = plt.subplots(figsize=(8, 7))
    sc = ax.scatter(act_norm, mf, c=years, cmap="viridis", zorder=3, s=40)
    plt.colorbar(sc, ax=ax, label="Year")

    # Arrow overlays every 5 years
    for j in range(0, len(years) - 1, 5):
        ax.annotate("", xy=(act_norm[j + 1], mf[j + 1]),
                    xytext=(act_norm[j], mf[j]),
                    arrowprops=dict(arrowstyle="->", color="black", lw=1.2))

    ax.axhline(1.0, color="grey", linestyle="--", linewidth=0.8)
    ax.set_xlabel("Actuarial Price (normalized to year 2)")
    ax.set_ylabel("Markup Factor exp(m)")
    ax.set_title("Phase Portrait: Price Forces")
    fig.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_06_peak_detection(loss_ratios: dict[int, float],
                           stats: CycleStats,
                           filename: str) -> str:
    """Single panel: detrended LR with peak/trough markers."""
    sorted_years = sorted(y for y in loss_ratios if y > 0)
    if not sorted_years:
        return ""

    raw = [loss_ratios[y] for y in sorted_years]
    trend = _centered_moving_average(raw, window=5)
    detrended = [raw[i] - trend[i] for i in range(len(raw))]

    peak_idxs   = find_local_maxima(detrended, min_distance=3)
    trough_idxs = find_local_minima(detrended, min_distance=3)

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(sorted_years, detrended, color="steelblue", linewidth=1.5,
            label="Detrended LR")
    ax.axhline(0, color="black", linewidth=0.8)

    for i, idx in enumerate(peak_idxs):
        ax.annotate("▲", xy=(sorted_years[idx], detrended[idx]),
                    ha="center", color="red", fontsize=10)
        if i > 0:
            dist = sorted_years[idx] - sorted_years[peak_idxs[i - 1]]
            mid_x = (sorted_years[idx] + sorted_years[peak_idxs[i - 1]]) / 2
            mid_y = max(detrended[idx], detrended[peak_idxs[i - 1]]) + 0.02
            ax.annotate(f"{dist}yr", xy=(mid_x, mid_y),
                        ha="center", fontsize=7, color="darkred")

    for idx in trough_idxs:
        ax.annotate("▼", xy=(sorted_years[idx], detrended[idx]),
                    ha="center", color="blue", fontsize=10)

    period_str = (f"{stats.period_years:.1f} yr"
                  if stats.period_years is not None else "N/A")
    ax.set_title(f"Peak Detection — Estimated Period: {period_str}")
    ax.set_xlabel("Year")
    ax.set_ylabel("Detrended Loss Ratio")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


# ── Section 5: Additional phenomena plot functions ────────────────────────────

def plot_07_lead_follow(with_lf_lrs: dict[int, dict[int, float]],
                        without_lf_lrs: dict[int, dict[int, float]]) -> str:
    """Two-panel: Phenomenon 4 — Lead-Follow Stabilisation."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

    def ensemble_stats(lr_by_seed: dict[int, dict[int, float]]):
        all_yrs = sorted(set(y for d in lr_by_seed.values() for y in d))
        means, stds = [], []
        for y in all_yrs:
            vals = [d[y] for d in lr_by_seed.values() if y in d]
            means.append(statistics.mean(vals) if vals else float("nan"))
            stds.append(statistics.stdev(vals) if len(vals) > 1 else 0.0)
        return np.array(all_yrs), np.array(means), np.array(stds)

    yrs_w, m_w, s_w = ensemble_stats(with_lf_lrs)
    yrs_n, m_n, s_n = ensemble_stats(without_lf_lrs)

    valid_w = ~np.isnan(m_w)
    valid_n = ~np.isnan(m_n)

    ax1.plot(yrs_w[valid_w], m_w[valid_w], color="steelblue", linewidth=2,
             label="With lead-follow")
    ax1.fill_between(yrs_w[valid_w], m_w[valid_w] - s_w[valid_w],
                     m_w[valid_w] + s_w[valid_w], alpha=0.2, color="steelblue")
    ax1.plot(yrs_n[valid_n], m_n[valid_n], color="crimson", linewidth=2,
             label="Without lead-follow")
    ax1.fill_between(yrs_n[valid_n], m_n[valid_n] - s_n[valid_n],
                     m_n[valid_n] + s_n[valid_n], alpha=0.2, color="crimson")
    ax1.axhline(1.0, color="black", linestyle="--", linewidth=0.8)
    ax1.set_ylabel("Loss Ratio")
    ax1.set_title("Phenomenon 4: Lead-Follow Stabilisation — Ensemble Mean ±1σ LR")
    ax1.legend(fontsize=9)

    # Summary bar chart: amplitude, CV, mean LR
    def cycle_summary(lr_by_seed):
        all_yrs = sorted(set(y for d in lr_by_seed.values() for y in d))
        means = []
        for y in all_yrs:
            vals = [d[y] for d in lr_by_seed.values() if y in d]
            means.append(statistics.mean(vals) if vals else float("nan"))
        valid = [v for v in means if not math.isnan(v)]
        if not valid:
            return 0.0, 0.0, 0.0
        peak_idxs = find_local_maxima(valid, min_distance=3)
        trough_idxs = find_local_minima(valid, min_distance=3)
        peak_vals = [valid[i] for i in peak_idxs]
        trough_vals = [valid[i] for i in trough_idxs]
        amplitude = ((statistics.mean(peak_vals) - statistics.mean(trough_vals))
                     if peak_vals and trough_vals else 0.0)
        mean_lr = statistics.mean(valid)
        cv = statistics.stdev(valid) / mean_lr if mean_lr > 0 and len(valid) > 1 else 0.0
        return amplitude, cv, mean_lr

    amp_w, cv_w, mlr_w = cycle_summary(with_lf_lrs)
    amp_n, cv_n, mlr_n = cycle_summary(without_lf_lrs)

    metrics = ["Amplitude", "CV", "Mean LR"]
    vals_w = [amp_w, cv_w, mlr_w]
    vals_n = [amp_n, cv_n, mlr_n]
    x = np.arange(len(metrics))
    width = 0.35
    ax2.bar(x - width / 2, vals_w, width, label="With lead-follow", color="steelblue", alpha=0.8)
    ax2.bar(x + width / 2, vals_n, width, label="Without lead-follow", color="crimson", alpha=0.8)
    ax2.set_xticks(x)
    ax2.set_xticklabels(metrics)
    ax2.set_ylabel("Value")
    ax2.set_title("Summary: Cycle Statistics by Regime")
    ax2.legend(fontsize=9)

    fig.tight_layout()
    path = f"{OUTPUT_DIR}/diag_07_lead_follow.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_08_path_dependence(capital_by_seed: dict[int, dict[int, float]]) -> str:
    """Phenomenon 6: Market Memory & Path Dependence — capital fan chart."""
    all_years = sorted(set(y for cap in capital_by_seed.values() for y in cap))
    if not all_years:
        return ""

    # Build matrix: seeds × years
    paths = []
    for seed in sorted(capital_by_seed):
        row = [capital_by_seed[seed].get(y, 0.0) for y in all_years]
        paths.append(row)
    mat = np.array(paths, dtype=float)  # shape: (n_seeds, n_years)

    median_path = np.median(mat, axis=0)
    min_path = mat.min(axis=0)
    max_path = mat.max(axis=0)

    # Divergence score
    std_final = float(np.std(mat[:, -1]))
    std_early_idx = min(5, mat.shape[1] - 1)
    std_early = float(np.std(mat[:, std_early_idx]))
    divergence = std_final / std_early if std_early > 1e-6 else float("nan")

    fig, ax = plt.subplots(figsize=(12, 6))
    for row in mat:
        ax.plot(all_years, row, color="grey", alpha=0.3, linewidth=0.8)
    ax.fill_between(all_years, min_path, max_path,
                    alpha=0.12, color="steelblue", label="Min/max envelope")
    ax.plot(all_years, median_path, color="steelblue", linewidth=2.5,
            label="Median path")
    ax.set_xlabel("Year")
    ax.set_ylabel("Total Market Capital")
    ax.set_title("Phenomenon 6: Market Memory & Path Dependence\n"
                 f"20 seeds, cats enabled (cat_freq=0.06) — "
                 f"Divergence score: {divergence:.2f}×")
    ax.legend(fontsize=9)
    fig.tight_layout()
    path = f"{OUTPUT_DIR}/diag_08_path_dependence.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_09_market_concentration(syndicate_series: dict[int, dict[int, dict]]) -> str:
    """Phenomenon 3: Winner's Curse & Price Wars — premium share + HHI."""
    sids = sorted(syndicate_series.keys())
    all_years = sorted(set(y for sid in sids
                           for y in syndicate_series[sid]))

    # Per-syndicate premium share by year
    shares: dict[int, list[float]] = {sid: [] for sid in sids}
    hhi_vals: list[float] = []
    plot_years: list[int] = []

    for y in all_years:
        total = sum(syndicate_series[sid][y]["premium"]
                    for sid in sids if y in syndicate_series[sid])
        if total <= 0:
            continue
        plot_years.append(y)
        sq_sum = 0.0
        for sid in sids:
            prem = syndicate_series[sid].get(y, {}).get("premium", 0.0)
            share = prem / total * 100.0
            shares[sid].append(share)
            sq_sum += (share / 100.0) ** 2
        hhi_vals.append(sq_sum)

    fig, ax1 = plt.subplots(figsize=(12, 6))
    colors = plt.cm.tab20.colors  # type: ignore[attr-defined]
    for i, sid in enumerate(sids):
        ax1.plot(plot_years, shares[sid], color=colors[i % len(colors)],
                 linewidth=1.2, alpha=0.75, label=f"Syn {sid}")

    ax1.set_xlabel("Year")
    ax1.set_ylabel("Premium Share (%)")
    ax1.set_title("Phenomenon 3: Winner's Curse — Premium Share & Market Concentration (HHI)")
    ax1.legend(fontsize=7, ncol=2, loc="upper left")

    ax2 = ax1.twinx()
    ax2.plot(plot_years, hhi_vals, color="black", linewidth=2.5,
             linestyle="--", label="HHI")
    ax2.set_ylabel("Herfindahl-Hirschman Index", color="black")
    ax2.legend(fontsize=9, loc="upper right")

    fig.tight_layout()
    path = f"{OUTPUT_DIR}/diag_09_market_concentration.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_10_network_evolution(network_snapshots: dict[int, dict[tuple[int, int], float]],
                              syndicate_yearly_premiums: dict) -> str:  # noqa: ARG001
    """Phenomenon 5: Herding Through Network Evolution — broker-syndicate heatmaps."""
    snapshot_years = sorted(network_snapshots.keys())
    if not snapshot_years:
        return ""

    # Determine broker and syndicate IDs from snapshots
    all_bids: set[int] = set()
    all_sids: set[int] = set()
    for snap in network_snapshots.values():
        for (bid, sid) in snap:
            all_bids.add(bid)
            all_sids.add(sid)
    broker_ids = sorted(all_bids)
    syndicate_ids = sorted(all_sids)

    # Pick 4 representative years
    target_years = [5, 20, 35, 50]
    display_years = []
    for t in target_years:
        closest = min(snapshot_years, key=lambda y: abs(y - t))
        if closest not in display_years:
            display_years.append(closest)
    display_years = sorted(display_years)[:4]

    n_panels = len(display_years)
    fig, axes = plt.subplots(1, n_panels, figsize=(4 * n_panels, 5),
                             sharey=True)
    if n_panels == 1:
        axes = [axes]

    vmin, vmax = 0.0, 1.0
    im = None
    for ax, yr in zip(axes, display_years):
        snap = network_snapshots.get(yr, {})
        mat = np.zeros((len(broker_ids), len(syndicate_ids)))
        for ri, bid in enumerate(broker_ids):
            for ci, sid in enumerate(syndicate_ids):
                mat[ri, ci] = snap.get((bid, sid), 0.0)
        im = ax.imshow(mat, aspect="auto", vmin=vmin, vmax=vmax,
                       cmap="YlOrRd", interpolation="nearest")
        ax.set_title(f"Year {yr}")
        ax.set_xlabel("Syndicate")
        ax.set_xticks(range(len(syndicate_ids)))
        ax.set_xticklabels(syndicate_ids, fontsize=7)
        if ax is axes[0]:
            ax.set_ylabel("Broker")
            ax.set_yticks(range(len(broker_ids)))
            ax.set_yticklabels(broker_ids, fontsize=8)

    if im is not None:
        fig.colorbar(im, ax=axes[-1], label="Relationship Strength")
    fig.suptitle("Phenomenon 5: Herding — Broker-Syndicate Relationship Strength",
                 fontsize=12)
    fig.tight_layout()
    path = f"{OUTPUT_DIR}/diag_10_network_evolution.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


# ── Section 6: Parameter sweep runner ────────────────────────────────────────

SYNDICATE_LEVEL_PARAMS = {"beta", "w", "z", "alpha", "gamma_div", "expense_rate"}
MARKET_LEVEL_PARAMS    = {"initial_capital", "cat_freq", "risks_per_year"}


class SweepResult(NamedTuple):
    param_name:    str
    param_value:   float
    hypothesis:    str
    mean_period:   float | None
    std_period:    float
    mean_amplitude: float
    lr_by_seed:    dict[int, dict[int, float]]
    mean_cv:           float  # mean CV across seeds
    mean_insolvencies: float  # mean total insolvencies across seeds


def run_parameter_sweep(param_name: str,
                        param_values: list[float],
                        hypothesis: str,
                        base_kwargs: dict,
                        n_seeds: int = 10,
                        horizon: int = 60) -> list[SweepResult]:
    """Run n_seeds simulations for each value, detect cycles, return SweepResults."""
    print(f"\n  Hypothesis: {hypothesis}")
    results = []
    for val in param_values:
        print(f"    {param_name} = {val:.3f} ...", end=" ", flush=True)
        kwargs = dict(base_kwargs)
        kwargs["horizon_years"] = horizon

        if param_name in SYNDICATE_LEVEL_PARAMS:
            kwargs["syndicate_params"] = {param_name: val}
        elif param_name == "cat_freq":
            kwargs["cat_freq"] = val
            kwargs["enable_cats"] = val > 0
        elif param_name == "initial_capital":
            kwargs["initial_capital"] = val
        elif param_name == "risks_per_year":
            kwargs["risks_per_year"] = int(val)

        periods, amplitudes, cvs, insolvency_totals = [], [], [], []
        lr_by_seed: dict[int, dict[int, float]] = {}
        for seed in range(n_seeds):
            kwargs["seed"] = seed
            m = build_simulation(**kwargs)
            m.run()
            lr = extract_yearly_loss_ratios(m, horizon)
            lr_by_seed[seed] = lr
            cs = detect_cycle(lr)
            if cs.period_years is not None:
                periods.append(cs.period_years)
            amplitudes.append(cs.amplitude)
            cvs.append(cs.cv)
            insolvency_totals.append(sum(m.yearly_insolvencies.values()))

        mean_period = statistics.mean(periods) if periods else None
        std_period  = statistics.stdev(periods) if len(periods) > 1 else 0.0
        mean_amplitude = statistics.mean(amplitudes) if amplitudes else 0.0
        mean_cv = statistics.mean(cvs) if cvs else 0.0
        mean_insolvencies = statistics.mean(insolvency_totals) if insolvency_totals else 0.0
        print(f"period={mean_period}")
        results.append(SweepResult(
            param_name=param_name,
            param_value=val,
            hypothesis=hypothesis,
            mean_period=mean_period,
            std_period=std_period,
            mean_amplitude=mean_amplitude,
            lr_by_seed=lr_by_seed,
            mean_cv=mean_cv,
            mean_insolvencies=mean_insolvencies,
        ))
    return results


# ── Section 7: 2D sweep runner and heatmap ────────────────────────────────────

def run_2d_sweep(param1: str,
                 values1: list[float],
                 param2: str,
                 values2: list[float],
                 base_kwargs: dict,
                 n_seeds: int = 10,
                 horizon: int = 60) -> list[list[SweepResult]]:
    """Run a 2D parameter sweep; results_2d[i][j] = SweepResult for (values1[i], values2[j])."""
    print(f"\n  2D sweep: {param1} × {param2}")
    results_2d: list[list[SweepResult]] = []
    for v1 in values1:
        row: list[SweepResult] = []
        for v2 in values2:
            print(f"    {param1}={v1:.3f}, {param2}={v2:.3f} ...", end=" ", flush=True)
            kwargs = dict(base_kwargs)
            kwargs["horizon_years"] = horizon
            sp: dict = {}

            # Resolve param1
            if param1 in SYNDICATE_LEVEL_PARAMS:
                sp[param1] = v1
            elif param1 == "initial_capital":
                kwargs["initial_capital"] = v1
            elif param1 == "cat_freq":
                kwargs["cat_freq"] = v1
                kwargs["enable_cats"] = v1 > 0
            elif param1 == "risks_per_year":
                kwargs["risks_per_year"] = int(v1)

            # Resolve param2
            if param2 in SYNDICATE_LEVEL_PARAMS:
                sp[param2] = v2
            elif param2 == "initial_capital":
                kwargs["initial_capital"] = v2
            elif param2 == "cat_freq":
                kwargs["cat_freq"] = v2
                kwargs["enable_cats"] = v2 > 0
            elif param2 == "risks_per_year":
                kwargs["risks_per_year"] = int(v2)

            if sp:
                kwargs["syndicate_params"] = sp

            periods, amplitudes, cvs, insolvency_totals = [], [], [], []
            lr_by_seed: dict[int, dict[int, float]] = {}
            for seed in range(n_seeds):
                kwargs["seed"] = seed
                m = build_simulation(**kwargs)
                m.run()
                lr = extract_yearly_loss_ratios(m, horizon)
                lr_by_seed[seed] = lr
                cs = detect_cycle(lr)
                if cs.period_years is not None:
                    periods.append(cs.period_years)
                amplitudes.append(cs.amplitude)
                cvs.append(cs.cv)
                insolvency_totals.append(sum(m.yearly_insolvencies.values()))

            mean_period = statistics.mean(periods) if periods else None
            std_period = statistics.stdev(periods) if len(periods) > 1 else 0.0
            mean_amplitude = statistics.mean(amplitudes) if amplitudes else 0.0
            mean_cv = statistics.mean(cvs) if cvs else 0.0
            mean_insolvencies = statistics.mean(insolvency_totals) if insolvency_totals else 0.0
            print(f"period={mean_period}")
            row.append(SweepResult(
                param_name=f"{param1}×{param2}",
                param_value=v1,
                hypothesis="",
                mean_period=mean_period,
                std_period=std_period,
                mean_amplitude=mean_amplitude,
                lr_by_seed=lr_by_seed,
                mean_cv=mean_cv,
                mean_insolvencies=mean_insolvencies,
            ))
        results_2d.append(row)
    return results_2d


def _sweep_mean_lr(r: SweepResult) -> float:
    """Compute mean LR across all seeds and years for a SweepResult."""
    all_vals = [v for lr_d in r.lr_by_seed.values() for v in lr_d.values()]
    return statistics.mean(all_vals) if all_vals else float("nan")


def plot_2d_heatmap(results_2d: list[list[SweepResult]],
                    param1: str, values1: list[float],
                    param2: str, values2: list[float],
                    filename: str) -> str:
    """Plot 5 metric heatmaps (period, amplitude, mean_lr, cv, insolvencies) for a 2D sweep."""
    n1, n2 = len(values1), len(values2)
    metric_keys = ["period", "amplitude", "mean_lr", "cv", "insolvencies"]
    metric_titles = ["Cycle Period (yr)", "Amplitude", "Mean LR", "CV", "Mean Insolvencies"]

    # Build data matrices
    data: dict[str, np.ndarray] = {}
    for metric in metric_keys:
        mat = np.zeros((n1, n2))
        for i in range(n1):
            for j in range(n2):
                r = results_2d[i][j]
                if metric == "period":
                    mat[i, j] = r.mean_period if r.mean_period is not None else float("nan")
                elif metric == "amplitude":
                    mat[i, j] = r.mean_amplitude
                elif metric == "mean_lr":
                    mat[i, j] = _sweep_mean_lr(r)
                elif metric == "cv":
                    mat[i, j] = r.mean_cv
                elif metric == "insolvencies":
                    mat[i, j] = r.mean_insolvencies
        data[metric] = mat

    fig, axes = plt.subplots(2, 3, figsize=(14, 9))
    axes_flat = axes.flatten()
    axes_flat[-1].set_visible(False)  # blank last cell

    xtick_labels = [f"{v:.2f}" for v in values2]
    ytick_labels = [f"{v:.2f}" for v in values1]

    for idx, (metric, title) in enumerate(zip(metric_keys, metric_titles)):
        ax = axes_flat[idx]
        mat = data[metric]
        im = ax.imshow(mat, aspect="auto", cmap="viridis", origin="upper")
        plt.colorbar(im, ax=ax)

        # Annotate cells
        mat_min, mat_max = float(np.nanmin(mat)), float(np.nanmax(mat))
        threshold = mat_min + 0.6 * (mat_max - mat_min) if mat_max > mat_min else mat_min
        for i in range(n1):
            for j in range(n2):
                val = mat[i, j]
                text_color = "white" if val < threshold else "black"
                ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                        fontsize=8, color=text_color)

        ax.set_xticks(range(n2))
        ax.set_xticklabels(xtick_labels, fontsize=8)
        ax.set_yticks(range(n1))
        ax.set_yticklabels(ytick_labels, fontsize=8)
        ax.set_xlabel(param2, fontsize=9)
        ax.set_ylabel(param1, fontsize=9)
        ax.set_title(title, fontsize=10)

    fig.suptitle(f"2D Sensitivity: {param1} × {param2}", fontsize=12)
    fig.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


# ── Section 8: OAT Tornado chart ──────────────────────────────────────────────

class TornadoEntry(NamedTuple):
    param_name: str
    metric:     str
    effect:     float   # (metric_at_max_param − metric_at_min_param) / baseline_metric
    val_at_min: float   # metric value when param is at its minimum
    val_at_max: float   # metric value when param is at its maximum
    baseline:   float   # baseline metric value


def compute_tornado_table(all_sweeps_dict: dict[str, list[SweepResult]],
                          baseline_stats: CycleStats) -> list[TornadoEntry]:
    """Compute signed OAT effect for each (param, metric) pair."""
    baseline_map: dict[str, float] = {
        "period":    baseline_stats.period_years or 1.0,
        "amplitude": baseline_stats.amplitude or 1.0,
        "mean_lr":   baseline_stats.mean_lr or 1.0,
        "cv":        baseline_stats.cv or 1.0,
    }

    def get_sweep_metric(r: SweepResult, metric: str, fallback: float) -> float:
        if metric == "period":
            return r.mean_period if r.mean_period is not None else fallback
        if metric == "amplitude":
            return r.mean_amplitude
        if metric == "mean_lr":
            return _sweep_mean_lr(r)
        if metric == "cv":
            return r.mean_cv
        return float("nan")

    entries: list[TornadoEntry] = []
    for param_name, sweep_results in all_sweeps_dict.items():
        if len(sweep_results) < 2:
            continue
        r_min = sweep_results[0]
        r_max = sweep_results[-1]
        for metric, baseline_val in baseline_map.items():
            v_min = get_sweep_metric(r_min, metric, baseline_val)
            v_max = get_sweep_metric(r_max, metric, baseline_val)
            if math.isnan(v_min) or math.isnan(v_max) or baseline_val == 0:
                continue
            effect = (v_max - v_min) / baseline_val
            entries.append(TornadoEntry(
                param_name=param_name,
                metric=metric,
                effect=effect,
                val_at_min=v_min,
                val_at_max=v_max,
                baseline=baseline_val,
            ))
    return entries


def plot_tornado(entries: list[TornadoEntry],
                 filename: str = "diag_13_tornado.png") -> str:
    """2×2 tornado chart, one panel per metric, bars sorted by |effect|."""
    metrics = ["period", "amplitude", "mean_lr", "cv"]
    metric_titles = {
        "period":    "Cycle Period",
        "amplitude": "Amplitude",
        "mean_lr":   "Mean Loss Ratio",
        "cv":        "Coefficient of Variation",
    }

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes_flat = axes.flatten()

    for ax, metric in zip(axes_flat, metrics):
        metric_entries = [e for e in entries if e.metric == metric]
        if not metric_entries:
            ax.set_visible(False)
            continue
        # Sort ascending by |effect| so largest is at top
        metric_entries.sort(key=lambda e: abs(e.effect))

        params = [e.param_name for e in metric_entries]
        effects = [e.effect for e in metric_entries]
        bar_colors = ["green" if eff >= 0 else "crimson" for eff in effects]

        y_pos = list(range(len(params)))
        bars = ax.barh(y_pos, effects, color=bar_colors, alpha=0.75,
                       edgecolor="black", linewidth=0.4)
        ax.axvline(0, color="black", linewidth=1.0)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(params, fontsize=8)
        ax.set_xlabel("Effect (relative to baseline)", fontsize=9)
        ax.set_title(f"Tornado: {metric_titles[metric]}", fontsize=10)

        # Annotate each bar with its effect value
        for bar, eff in zip(bars, effects):
            w = bar.get_width()
            offset = 0.005 * (1 if w >= 0 else -1)
            ax.text(w + offset, bar.get_y() + bar.get_height() / 2,
                    f"{eff:+.2f}", va="center",
                    ha="left" if w >= 0 else "right", fontsize=7)

    fig.suptitle("OAT Tornado Chart — Sensitivity of Cycle Metrics to Parameters",
                 fontsize=12)
    fig.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


# ── Section 9: Agent-count scaling sweep (Experiment 0a) ─────────────────────

class AgentScaleResult(NamedTuple):
    n_syndicates:   int
    n_brokers:      int
    mean_period:    float | None
    std_period:     float
    mean_amplitude: float
    mean_lr:        float
    mean_hhi:       float   # time-averaged HHI across seeds and years
    lr_by_seed:     dict[int, dict[int, float]]


def _compute_time_avg_hhi(market: Market, horizon: int) -> float:
    """Compute time-averaged HHI (Herfindahl-Hirschman Index) from syndicate premium shares."""
    hhi_vals = []
    for y in range(horizon):
        total = sum(market.syndicate_yearly_premiums[sid].get(y, 0.0)
                    for sid in market.syndicates)
        if total <= 0:
            continue
        sq_sum = sum(
            (market.syndicate_yearly_premiums[sid].get(y, 0.0) / total) ** 2
            for sid in market.syndicates
        )
        hhi_vals.append(sq_sum)
    return statistics.mean(hhi_vals) if hhi_vals else float("nan")


def run_agent_scale_sweep(
    configs: list[tuple[int, int]],
    base_kwargs: dict,
    n_seeds: int = 10,
    horizon: int = 60,
) -> list[AgentScaleResult]:
    """Sweep over (n_syndicates, n_brokers) pairs, run n_seeds × 60yr, return AgentScaleResults."""
    print(f"\n  Agent-count scaling sweep: {len(configs)} configs × {n_seeds} seeds")
    results = []
    for n_syn, n_brok in configs:
        print(f"    n_syndicates={n_syn:>3}, n_brokers={n_brok:>3} ...",
              end=" ", flush=True)
        kwargs = dict(base_kwargs)
        kwargs["horizon_years"] = horizon
        kwargs["n_syndicates"] = n_syn
        kwargs["n_brokers"] = n_brok

        periods, amplitudes, hhi_vals = [], [], []
        lr_by_seed: dict[int, dict[int, float]] = {}
        for seed in range(n_seeds):
            kwargs["seed"] = seed
            m = build_simulation(**kwargs)
            m.run()
            lr = extract_yearly_loss_ratios(m, horizon)
            lr_by_seed[seed] = lr
            cs = detect_cycle(lr)
            if cs.period_years is not None:
                periods.append(cs.period_years)
            amplitudes.append(cs.amplitude)
            hhi_vals.append(_compute_time_avg_hhi(m, horizon))

        mean_period = statistics.mean(periods) if periods else None
        std_period = statistics.stdev(periods) if len(periods) > 1 else 0.0
        mean_amplitude = statistics.mean(amplitudes) if amplitudes else 0.0
        all_lr_vals = [v for lr_d in lr_by_seed.values() for v in lr_d.values()]
        mean_lr = statistics.mean(all_lr_vals) if all_lr_vals else float("nan")
        valid_hhi = [h for h in hhi_vals if not math.isnan(h)]
        mean_hhi = statistics.mean(valid_hhi) if valid_hhi else float("nan")
        period_str = f"{mean_period:.1f}" if mean_period is not None else "N/A"
        print(f"period={period_str}, HHI={mean_hhi:.3f}")
        results.append(AgentScaleResult(
            n_syndicates=n_syn,
            n_brokers=n_brok,
            mean_period=mean_period,
            std_period=std_period,
            mean_amplitude=mean_amplitude,
            mean_lr=mean_lr,
            mean_hhi=mean_hhi,
            lr_by_seed=lr_by_seed,
        ))
    return results


def plot_14_agent_scaling(results: list[AgentScaleResult], filename: str) -> str:
    """Four-panel: LR time series / period / amplitude / HHI vs n_syndicates."""
    if not results:
        return ""

    n_configs = len(results)
    cmap = plt.cm.plasma  # type: ignore[attr-defined]
    colors = [cmap(i / max(n_configs - 1, 1)) for i in range(n_configs)]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    ax_lr, ax_period, ax_amp, ax_hhi = (
        axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]
    )

    # ── Panel 1: ensemble mean LR time series ─────────────────────────────────
    for res, color in zip(results, colors):
        all_yrs = sorted(set(y for lr_d in res.lr_by_seed.values() for y in lr_d))
        means, stds = [], []
        for y in all_yrs:
            vals = [res.lr_by_seed[s][y] for s in res.lr_by_seed if y in res.lr_by_seed[s]]
            means.append(statistics.mean(vals) if vals else float("nan"))
            stds.append(statistics.stdev(vals) if len(vals) > 1 else 0.0)
        mean_arr = np.array(means)
        std_arr = np.array(stds)
        valid = ~np.isnan(mean_arr)
        label = f"{res.n_syndicates} syn / {res.n_brokers} brok"
        ax_lr.plot(np.array(all_yrs)[valid], mean_arr[valid],
                   color=color, linewidth=1.8, label=label)
        ax_lr.fill_between(np.array(all_yrs)[valid],
                           mean_arr[valid] - std_arr[valid],
                           mean_arr[valid] + std_arr[valid],
                           alpha=0.10, color=color)

    ax_lr.axhline(1.0, color="red", linestyle="--", linewidth=0.8, label="LR=1.0")
    ax_lr.axhline(0.6, color="green", linestyle="--", linewidth=0.8, label="LR=0.6")
    ax_lr.set_xlabel("Year")
    ax_lr.set_ylabel("Loss Ratio")
    ax_lr.set_title("Ensemble Mean LR by Agent Count")
    ax_lr.legend(fontsize=7)

    # ── Panel 2: cycle period vs n_syndicates ─────────────────────────────────
    x_vals = [r.n_syndicates for r in results]
    periods = [r.mean_period if r.mean_period is not None else float("nan")
               for r in results]
    period_stds = [r.std_period for r in results]

    ax_period.errorbar(x_vals, periods, yerr=period_stds,
                       fmt="o-", color="steelblue", capsize=5, linewidth=2,
                       markersize=7, label="Simulated period")
    ax_period.axhline(6.0, color="darkgreen", linestyle="--", linewidth=1.5,
                      label="Owadally 2018 target (6 yr)")
    ax_period.set_xlabel("Number of Syndicates")
    ax_period.set_ylabel("Cycle Period (years)")
    ax_period.set_title("Cycle Period vs Agent Count")
    ax_period.legend(fontsize=8)
    ax_period.set_xticks(x_vals)

    # ── Panel 3: amplitude vs n_syndicates ────────────────────────────────────
    amplitudes = [r.mean_amplitude for r in results]
    ax_amp.plot(x_vals, amplitudes, "s-", color="crimson", linewidth=2,
                markersize=7)
    ax_amp.set_xlabel("Number of Syndicates")
    ax_amp.set_ylabel("Cycle Amplitude")
    ax_amp.set_title("Cycle Amplitude vs Agent Count")
    ax_amp.set_xticks(x_vals)

    # ── Panel 4: mean HHI vs n_syndicates ────────────────────────────────────
    hhis = [r.mean_hhi for r in results]
    ax_hhi.plot(x_vals, hhis, "^-", color="darkorange", linewidth=2, markersize=7)
    ax_hhi.axhspan(0.06, 0.10, alpha=0.15, color="green",
                   label="Lloyd's reference (0.06–0.10)")
    ax_hhi.set_xlabel("Number of Syndicates")
    ax_hhi.set_ylabel("Mean HHI")
    ax_hhi.set_title("Market Concentration (HHI) vs Agent Count")
    ax_hhi.legend(fontsize=8)
    ax_hhi.set_xticks(x_vals)

    fig.suptitle(
        "Experiment 0a: Agent-Count Scaling Sweep\n"
        "10 seeds × 60yr per config | no cats | n_brokers scales with n_syndicates",
        fontsize=11,
    )
    fig.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


# ── Section 9b: Risk-volume / compute trade-off sweep (Experiment 0b) ─────────

class RiskVolumeResult(NamedTuple):
    label:           str     # short config label
    n_syndicates:    int
    n_brokers:       int
    risks_per_year:  int
    mean_period:     float | None
    std_period:      float
    mean_amplitude:  float
    mean_lr:         float
    mean_hhi:        float
    runtime_seconds: float   # wall-clock seconds for this config (all seeds)
    lr_by_seed:      dict[int, dict[int, float]]


def run_risk_volume_sweep(
    configs: list[tuple[str, int, int, int]],   # (label, n_syn, n_brok, risks/yr)
    base_kwargs: dict,
    n_seeds: int = 10,
    horizon: int = 60,
) -> list[RiskVolumeResult]:
    """Sweep over (n_syndicates, n_brokers, risks_per_year) combos."""
    print(f"\n  Risk-volume sweep: {len(configs)} configs × {n_seeds} seeds")
    results = []
    for label, n_syn, n_brok, rpy in configs:
        print(f"    [{label}] n_syn={n_syn:>3}, n_brok={n_brok:>3}, "
              f"risks/yr={rpy:>4} ...", end=" ", flush=True)
        kwargs = dict(base_kwargs)
        kwargs["horizon_years"] = horizon
        kwargs["n_syndicates"] = n_syn
        kwargs["n_brokers"] = n_brok
        kwargs["risks_per_year"] = rpy

        t0 = time.time()
        periods, amplitudes, hhi_vals = [], [], []
        lr_by_seed: dict[int, dict[int, float]] = {}
        for seed in range(n_seeds):
            kwargs["seed"] = seed
            m = build_simulation(**kwargs)
            m.run()
            lr = extract_yearly_loss_ratios(m, horizon)
            lr_by_seed[seed] = lr
            cs = detect_cycle(lr)
            if cs.period_years is not None:
                periods.append(cs.period_years)
            amplitudes.append(cs.amplitude)
            hhi_vals.append(_compute_time_avg_hhi(m, horizon))
        runtime = time.time() - t0

        mean_period = statistics.mean(periods) if periods else None
        std_period = statistics.stdev(periods) if len(periods) > 1 else 0.0
        mean_amplitude = statistics.mean(amplitudes) if amplitudes else 0.0
        all_lr_vals = [v for lr_d in lr_by_seed.values() for v in lr_d.values()]
        mean_lr = statistics.mean(all_lr_vals) if all_lr_vals else float("nan")
        valid_hhi = [h for h in hhi_vals if not math.isnan(h)]
        mean_hhi = statistics.mean(valid_hhi) if valid_hhi else float("nan")
        period_str = f"{mean_period:.1f}" if mean_period is not None else "N/A"
        print(f"period={period_str}, LR={mean_lr:.3f}, "
              f"HHI={mean_hhi:.3f}, t={runtime:.0f}s")
        results.append(RiskVolumeResult(
            label=label,
            n_syndicates=n_syn,
            n_brokers=n_brok,
            risks_per_year=rpy,
            mean_period=mean_period,
            std_period=std_period,
            mean_amplitude=mean_amplitude,
            mean_lr=mean_lr,
            mean_hhi=mean_hhi,
            runtime_seconds=runtime,
            lr_by_seed=lr_by_seed,
        ))
    return results


def plot_15_risk_volume(results: list[RiskVolumeResult], filename: str) -> str:
    """Four-panel: LR series / mean LR bar / amplitude bar / runtime bar."""
    if not results:
        return ""

    n = len(results)
    cmap = plt.cm.viridis  # type: ignore[attr-defined]
    colors = [cmap(i / max(n - 1, 1)) for i in range(n)]
    labels = [r.label for r in results]
    x = np.arange(n)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    ax_lr_ts, ax_lr_bar, ax_amp, ax_rt = (
        axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]
    )

    # ── Panel 1: ensemble mean LR time series ─────────────────────────────────
    for res, color in zip(results, colors):
        all_yrs = sorted(set(y for lr_d in res.lr_by_seed.values() for y in lr_d))
        means = []
        for y in all_yrs:
            vals = [res.lr_by_seed[s][y] for s in res.lr_by_seed
                    if y in res.lr_by_seed[s]]
            means.append(statistics.mean(vals) if vals else float("nan"))
        valid = [not math.isnan(m) for m in means]
        yrs_v = [all_yrs[i] for i, v in enumerate(valid) if v]
        mean_v = [means[i] for i, v in enumerate(valid) if v]
        ax_lr_ts.plot(yrs_v, mean_v, color=color, linewidth=1.8,
                      label=res.label)

    ax_lr_ts.axhline(1.0, color="red", linestyle="--", linewidth=0.8)
    ax_lr_ts.axhline(0.49, color="green", linestyle="--", linewidth=0.8,
                     label="Baseline LR ~0.49")
    ax_lr_ts.set_xlabel("Year")
    ax_lr_ts.set_ylabel("Loss Ratio")
    ax_lr_ts.set_title("Ensemble Mean LR by Config")
    ax_lr_ts.legend(fontsize=8)

    # ── Panel 2: mean LR bar chart ────────────────────────────────────────────
    mean_lrs = [r.mean_lr for r in results]
    bars = ax_lr_bar.bar(x, mean_lrs, color=colors, alpha=0.85, edgecolor="black",
                         linewidth=0.5)
    ax_lr_bar.axhline(0.49, color="green", linestyle="--", linewidth=1.2,
                      label="Baseline (10 syn, 25 rpy)")
    for bar, val in zip(bars, mean_lrs):
        ax_lr_bar.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.005,
                       f"{val:.3f}", ha="center", va="bottom", fontsize=9)
    ax_lr_bar.set_xticks(x)
    ax_lr_bar.set_xticklabels(labels)
    ax_lr_bar.set_ylabel("Mean Loss Ratio")
    ax_lr_bar.set_title("Mean LR: Does Proportional Scaling Correct LR Inflation?")
    ax_lr_bar.legend(fontsize=8)

    # ── Panel 3: amplitude bar chart ──────────────────────────────────────────
    amplitudes = [r.mean_amplitude for r in results]
    bars2 = ax_amp.bar(x, amplitudes, color=colors, alpha=0.85, edgecolor="black",
                       linewidth=0.5)
    ax_amp.axhline(0.383, color="green", linestyle="--", linewidth=1.2,
                   label="Baseline amplitude (0.383)")
    for bar, val in zip(bars2, amplitudes):
        ax_amp.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.002,
                    f"{val:.3f}", ha="center", va="bottom", fontsize=9)
    ax_amp.set_xticks(x)
    ax_amp.set_xticklabels(labels)
    ax_amp.set_ylabel("Cycle Amplitude")
    ax_amp.set_title("Amplitude vs Config")
    ax_amp.legend(fontsize=8)

    # ── Panel 4: wall-clock runtime ───────────────────────────────────────────
    runtimes = [r.runtime_seconds for r in results]
    bars3 = ax_rt.bar(x, runtimes, color=colors, alpha=0.85, edgecolor="black",
                      linewidth=0.5)
    for bar, val in zip(bars3, runtimes):
        ax_rt.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                   f"{val:.0f}s", ha="center", va="bottom", fontsize=9)
    ax_rt.set_xticks(x)
    ax_rt.set_xticklabels(labels)
    n_seeds_used = len(next(iter(results)).lr_by_seed)
    ax_rt.set_ylabel("Wall-clock Time (seconds)")
    ax_rt.set_title(f"Runtime ({n_seeds_used} seeds each)")

    fig.suptitle(
        "Experiment 0b: Risk-Volume / Compute Trade-off\n"
        "10 seeds × 60yr per config | no cats",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


# ── Section 9d: LOB decomposition (Experiment 1b) ────────────────────────────

# Per-LOB proxy configs. ATTRITIONAL_CLAIM_LAMBDA is hardcoded in simulation.py
# so we proxy LOB characteristics via risks_per_year (frequency), initial_capital
# (size), cat_freq/enable_cats (cat exposure), and initial_claim_est (actuarial
# belief about per-risk severity).
LOB_CONFIGS: dict[str, dict] = {
    "Property":  {"risks_per_year": 10, "initial_capital": 3000, "enable_cats": True,
                  "cat_freq": 0.12, "syndicate_params": {"initial_claim_est": 150.0}},
    "Marine":    {"risks_per_year":  6, "initial_capital": 3500, "enable_cats": True,
                  "cat_freq": 0.08, "syndicate_params": {"initial_claim_est": 200.0}},
    "Liability": {"risks_per_year": 20, "initial_capital": 2000, "enable_cats": False,
                  "cat_freq": 0.00, "syndicate_params": {"initial_claim_est":  40.0}},
    "Aviation":  {"risks_per_year":  3, "initial_capital": 5000, "enable_cats": True,
                  "cat_freq": 0.05, "syndicate_params": {"initial_claim_est": 300.0}},
    "Motor":     {"risks_per_year": 25, "initial_capital": 1500, "enable_cats": False,
                  "cat_freq": 0.00, "syndicate_params": {"initial_claim_est":  25.0}},
}


# ── Section: Experiment 2b — Per-LOB risk heterogeneity ──────────────────────

# Default LOB params: region index → {lambda, sev_mu, limit (lo, hi)}
LOB_PARAMS_DEFAULT: dict[int, dict] = {
    0: {"lambda": 0.30, "sev_mu": 200,  "limit": (1000,  5000)},  # Property
    1: {"lambda": 0.15, "sev_mu": 400,  "limit": (2000, 10000)},  # Marine
    2: {"lambda": 1.50, "sev_mu":  50,  "limit":  (500,  2000)},  # Liability
    3: {"lambda": 0.05, "sev_mu": 800,  "limit": (5000, 20000)},  # Aviation
    4: {"lambda": 2.00, "sev_mu":  30,  "limit":  (200,   800)},  # Motor
}

LOB_NAMES = {0: "Property", 1: "Marine", 2: "Liability", 3: "Aviation", 4: "Motor"}


class LobHeteroResult(NamedTuple):
    label:          str
    lob_params:     dict[int, dict] | None
    mean_lr:        float
    mean_amplitude: float
    mean_period:    float | None
    mean_hhi:       float
    lr_by_seed:     dict[int, dict[int, float]]          # seed → {yr: lr} (market-wide)
    region_lr:      dict[int, dict[int, dict[int, float]]]  # seed → region → {yr: lr}


def run_lob_hetero_sweep(
    configs: list[tuple[str, "dict[int, dict] | None"]],
    n_syndicates: int = 10,
    n_brokers:    int = 4,
    n_regions:    int = 5,
    n_seeds:      int = 10,
    horizon:      int = 60,
) -> list[LobHeteroResult]:
    """Baseline vs heterogeneous lob_params comparison."""
    print(f"\n  LOB heterogeneity sweep: {len(configs)} configs × {n_seeds} seeds")
    results = []
    for label, lp in configs:
        tag = "homogeneous" if lp is None else "heterogeneous"
        print(f"    [{label:<16}] ({tag}) ...", end=" ", flush=True)
        periods, amplitudes, hhi_vals = [], [], []
        lr_by_seed: dict[int, dict[int, float]] = {}
        region_lr:  dict[int, dict[int, dict[int, float]]] = {}

        for seed in range(n_seeds):
            m = build_simulation(
                n_syndicates=n_syndicates,
                n_brokers=n_brokers,
                n_regions=n_regions,
                horizon_years=horizon,
                enable_cats=False,
                seed=seed,
                lob_params=lp,
            )
            m.run()
            lr = extract_yearly_loss_ratios(m, horizon)
            lr_by_seed[seed] = lr
            region_lr[seed] = extract_per_region_lr(m, horizon, n_regions)
            cs = detect_cycle(lr)
            if cs.period_years is not None:
                periods.append(cs.period_years)
            amplitudes.append(cs.amplitude)
            hhi_vals.append(_compute_time_avg_hhi(m, horizon))

        mean_period    = statistics.mean(periods)    if periods    else None
        mean_amplitude = statistics.mean(amplitudes) if amplitudes else 0.0
        valid_hhi      = [h for h in hhi_vals if not math.isnan(h)]
        mean_hhi       = statistics.mean(valid_hhi)  if valid_hhi  else float("nan")
        all_lr         = [v for d in lr_by_seed.values() for v in d.values()]
        mean_lr        = statistics.mean(all_lr) if all_lr else float("nan")
        ps = f"{mean_period:.1f}" if mean_period is not None else "N/A"
        print(f"period={ps}, LR={mean_lr:.3f}, HHI={mean_hhi:.3f}")
        results.append(LobHeteroResult(
            label=label,
            lob_params=lp,
            mean_lr=mean_lr,
            mean_amplitude=mean_amplitude,
            mean_period=mean_period,
            mean_hhi=mean_hhi,
            lr_by_seed=lr_by_seed,
            region_lr=region_lr,
        ))
    return results


def plot_19_lob_hetero(
    results: list[LobHeteroResult],
    n_regions: int = 5,
    filename: str = "diag_19_lob_hetero.png",
) -> str:
    """Four-panel: per-region LR series / market LR comparison / per-region mean LR / cycle stats."""
    if not results:
        return ""

    hetero = next((r for r in results if r.lob_params is not None), results[-1])
    baseline = next((r for r in results if r.lob_params is None), results[0])

    cmap_region = plt.cm.tab10  # type: ignore[attr-defined]
    region_colors = [cmap_region(i / 10) for i in range(n_regions)]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    ax_reg, ax_mkt, ax_bar, ax_cycle = (
        axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]
    )

    # ── Panel 1: per-region LR time series (heterogeneous config) ────────────
    for r in range(n_regions):
        all_yrs = sorted(set(
            y for sd in hetero.region_lr.values()
            for y in sd.get(r, {})
        ))
        means = []
        for y in all_yrs:
            vals = [hetero.region_lr[s][r][y]
                    for s in hetero.region_lr if y in hetero.region_lr[s].get(r, {})]
            means.append(statistics.mean(vals) if vals else float("nan"))
        valid = [(y, m) for y, m in zip(all_yrs, means) if not math.isnan(m)]
        if valid:
            yrs_v, means_v = zip(*valid)
            ax_reg.plot(yrs_v, means_v, color=region_colors[r], linewidth=1.5,
                        label=LOB_NAMES.get(r, f"R{r}"))
    ax_reg.axhline(1.0, color="red", linestyle="--", linewidth=0.8, label="LR=1")
    ax_reg.axhline(0.6, color="green", linestyle=":", linewidth=0.8, label="LR=0.6")
    ax_reg.set_title("Per-Region LR Time Series (Heterogeneous)")
    ax_reg.set_xlabel("Year"); ax_reg.set_ylabel("Loss Ratio")
    ax_reg.legend(fontsize=8)

    # ── Panel 2: market-wide LR time series — baseline vs heterogeneous ───────
    for res, color, ls in [(baseline, "steelblue", "-"), (hetero, "darkorange", "-")]:
        all_yrs = sorted(set(y for lr_d in res.lr_by_seed.values() for y in lr_d))
        means = []
        for y in all_yrs:
            vals = [res.lr_by_seed[s][y] for s in res.lr_by_seed if y in res.lr_by_seed[s]]
            means.append(statistics.mean(vals) if vals else float("nan"))
        valid = [(y, m) for y, m in zip(all_yrs, means) if not math.isnan(m)]
        if valid:
            yrs_v, means_v = zip(*valid)
            ax_mkt.plot(yrs_v, means_v, color=color, linestyle=ls,
                        linewidth=1.5, label=res.label)
    ax_mkt.axhline(1.0, color="red", linestyle="--", linewidth=0.8)
    ax_mkt.set_title("Market LR: Baseline vs LOB Heterogeneous")
    ax_mkt.set_xlabel("Year"); ax_mkt.set_ylabel("Loss Ratio")
    ax_mkt.legend(fontsize=8)

    # ── Panel 3: per-region mean LR bar chart (heterogeneous) ────────────────
    region_means, region_stds = [], []
    for r in range(n_regions):
        vals = [
            hetero.region_lr[s][r][y]
            for s in hetero.region_lr
            for y in hetero.region_lr[s].get(r, {})
        ]
        region_means.append(statistics.mean(vals) if vals else float("nan"))
        region_stds.append(statistics.stdev(vals) if len(vals) > 1 else 0.0)
    x = np.arange(n_regions)
    ax_bar.bar(x, region_means, yerr=region_stds, color=region_colors,
               capsize=4, alpha=0.85)
    ax_bar.axhline(baseline.mean_lr, color="steelblue", linestyle="--",
                   linewidth=1.2, label=f"Baseline market LR ({baseline.mean_lr:.2f})")
    ax_bar.axhline(1.0, color="red", linestyle=":", linewidth=0.8, label="LR=1")
    ax_bar.set_xticks(x)
    ax_bar.set_xticklabels([LOB_NAMES.get(r, f"R{r}") for r in range(n_regions)])
    ax_bar.set_title("Per-Region Mean LR — Heterogeneous Config")
    ax_bar.set_ylabel("Mean Loss Ratio"); ax_bar.legend(fontsize=8)

    # ── Panel 4: cycle stats (period + amplitude) grouped bar ─────────────────
    n = len(results)
    x4 = np.arange(n)
    width = 0.35
    periods   = [r.mean_period    if r.mean_period is not None else 0.0 for r in results]
    amplitudes = [r.mean_amplitude for r in results]
    ax4b = ax_cycle.twinx()
    ax_cycle.bar(x4 - width / 2, periods,    width, color="steelblue", alpha=0.8, label="Period (yr)")
    ax4b.bar(    x4 + width / 2, amplitudes, width, color="darkorange", alpha=0.8, label="Amplitude")
    ax_cycle.axhline(6.0, color="steelblue", linestyle="--", linewidth=0.8,
                     label="Lloyd's target period (6yr)")
    ax_cycle.set_xticks(x4)
    ax_cycle.set_xticklabels([r.label for r in results], rotation=15, ha="right")
    ax_cycle.set_ylabel("Period (yr)", color="steelblue")
    ax4b.set_ylabel("Amplitude",       color="darkorange")
    ax_cycle.set_title("Cycle Period & Amplitude")
    lines1, labs1 = ax_cycle.get_legend_handles_labels()
    lines2, labs2 = ax4b.get_legend_handles_labels()
    ax_cycle.legend(lines1 + lines2, labs1 + labs2, fontsize=8)

    fig.suptitle(
        "Experiment 2b — Per-LOB Risk Heterogeneity\n"
        "Baseline vs heterogeneous claim frequency/severity/limits per region "
        "(10 syn, 4 brok, 5 regions, 25 rpy, no cats, 10 seeds × 60yr)",
        fontsize=11,
    )
    plt.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ── Section: Experiment 2d — Syndicate market entry ──────────────────────────

def extract_entry_history(
    market: Market,
    horizon: int,
) -> dict[int, dict]:
    """Return {year: {entries, insolvencies, n_solvent, avg_markup}} from market state."""
    result: dict[int, dict] = {}
    for y in range(horizon):
        # Count active (solvent) syndicates at end of year y: use capital snapshots
        n_solvent = sum(
            1 for sid in market.syndicates
            if market.syndicate_yearly_capital[sid].get(y, 0) > 0
        )
        result[y] = {
            "entries":      market.yearly_entries.get(y, 0),
            "insolvencies": market.yearly_insolvencies.get(y, 0),
            "n_solvent":    n_solvent,
        }
    return result


class MarketEntryResult(NamedTuple):
    label:         str
    allow_entry:   bool
    cat_freq:      float
    mean_period:   float | None
    mean_amplitude: float
    mean_lr:       float
    mean_hhi:      float
    total_entries: float          # mean total entries over 60yr per seed
    entry_history: dict[int, dict[int, dict]]   # seed → {yr: {entries, insolvencies, n_solvent}}
    lr_by_seed:    dict[int, dict[int, float]]


def run_market_entry_sweep(
    configs: list[tuple[str, bool, float]],   # (label, allow_entry, cat_freq)
    n_syndicates:    int = 10,
    n_brokers:       int = 4,
    n_syndicates_max: int = 20,
    n_seeds:         int = 10,
    horizon:         int = 60,
) -> list[MarketEntryResult]:
    """Compare no-entry vs entry-enabled market under cats."""
    print(f"\n  Market entry sweep: {len(configs)} configs × {n_seeds} seeds")
    results = []
    for label, allow_entry, cat_freq in configs:
        tag = f"entry={'Y' if allow_entry else 'N'}, cf={cat_freq:.2f}"
        print(f"    [{label:<20}] ({tag}) ...", end=" ", flush=True)
        periods, amplitudes, hhi_vals = [], [], []
        lr_by_seed:     dict[int, dict[int, float]] = {}
        entry_history:  dict[int, dict[int, dict]] = {}
        total_entry_list: list[float] = []

        for seed in range(n_seeds):
            m = build_simulation(
                n_syndicates=n_syndicates,
                n_brokers=n_brokers,
                horizon_years=horizon,
                enable_cats=True,
                cat_freq=cat_freq,
                allow_entry=allow_entry,
                n_syndicates_max=n_syndicates_max,
                seed=seed,
            )
            m.run()
            lr = extract_yearly_loss_ratios(m, horizon)
            lr_by_seed[seed] = lr
            cs = detect_cycle(lr)
            if cs.period_years is not None:
                periods.append(cs.period_years)
            amplitudes.append(cs.amplitude)
            hhi_vals.append(_compute_time_avg_hhi(m, horizon))
            eh = extract_entry_history(m, horizon)
            entry_history[seed] = eh
            total_entry_list.append(sum(v["entries"] for v in eh.values()))

        mean_period    = statistics.mean(periods)    if periods    else None
        mean_amplitude = statistics.mean(amplitudes) if amplitudes else 0.0
        valid_hhi      = [h for h in hhi_vals if not math.isnan(h)]
        mean_hhi       = statistics.mean(valid_hhi)  if valid_hhi  else float("nan")
        all_lr         = [v for d in lr_by_seed.values() for v in d.values()]
        mean_lr        = statistics.mean(all_lr) if all_lr else float("nan")
        total_entries  = statistics.mean(total_entry_list) if total_entry_list else 0.0
        ps = f"{mean_period:.1f}" if mean_period is not None else "N/A"
        print(f"period={ps}, LR={mean_lr:.3f}, HHI={mean_hhi:.3f}, "
              f"entries={total_entries:.1f}")
        results.append(MarketEntryResult(
            label=label,
            allow_entry=allow_entry,
            cat_freq=cat_freq,
            mean_period=mean_period,
            mean_amplitude=mean_amplitude,
            mean_lr=mean_lr,
            mean_hhi=mean_hhi,
            total_entries=total_entries,
            entry_history=entry_history,
            lr_by_seed=lr_by_seed,
        ))
    return results


def plot_21_market_entry(results: list[MarketEntryResult], filename: str) -> str:
    """Four-panel: LR time series / active syndicate count / entry+insolvency / HHI."""
    if not results:
        return ""

    n = len(results)
    cmap = plt.cm.tab10  # type: ignore[attr-defined]
    colors = [cmap(i / 10) for i in range(n)]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    ax_lr, ax_nsyn, ax_events, ax_hhi_ts = (
        axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]
    )

    # ── Panel 1: ensemble mean LR time series ─────────────────────────────────
    for res, color in zip(results, colors):
        all_yrs = sorted(set(y for lr_d in res.lr_by_seed.values() for y in lr_d))
        means = []
        for y in all_yrs:
            vals = [res.lr_by_seed[s][y] for s in res.lr_by_seed
                    if y in res.lr_by_seed[s]]
            means.append(statistics.mean(vals) if vals else float("nan"))
        valid = [(y, m) for y, m in zip(all_yrs, means) if not math.isnan(m)]
        if valid:
            yrs_v, means_v = zip(*valid)
            ax_lr.plot(yrs_v, means_v, color=color, linewidth=1.5, label=res.label)
    ax_lr.axhline(1.0, color="red", linestyle="--", linewidth=0.8)
    ax_lr.set_title("Ensemble Mean Loss Ratio")
    ax_lr.set_xlabel("Year"); ax_lr.set_ylabel("Loss Ratio")
    ax_lr.legend(fontsize=8)

    # ── Panel 2: mean active syndicate count over time ────────────────────────
    for res, color in zip(results, colors):
        all_yrs = sorted(set(
            y for sd in res.entry_history.values() for y in sd
        ))
        means = []
        for y in all_yrs:
            vals = [res.entry_history[s][y]["n_solvent"]
                    for s in res.entry_history if y in res.entry_history[s]]
            means.append(statistics.mean(vals) if vals else float("nan"))
        valid = [(y, m) for y, m in zip(all_yrs, means) if not math.isnan(m)]
        if valid:
            yrs_v, means_v = zip(*valid)
            ax_nsyn.plot(yrs_v, means_v, color=color, linewidth=1.5, label=res.label)
    ax_nsyn.set_title("Mean Active Syndicate Count")
    ax_nsyn.set_xlabel("Year"); ax_nsyn.set_ylabel("Solvent syndicates")
    ax_nsyn.legend(fontsize=8)

    # ── Panel 3: mean entries + insolvencies per year ─────────────────────────
    entry_res = next((r for r in results if r.allow_entry), results[-1])
    all_yrs = sorted(set(y for sd in entry_res.entry_history.values() for y in sd))
    mean_entries = []
    mean_insol   = []
    for y in all_yrs:
        ev = [entry_res.entry_history[s][y]["entries"]
              for s in entry_res.entry_history if y in entry_res.entry_history[s]]
        iv = [entry_res.entry_history[s][y]["insolvencies"]
              for s in entry_res.entry_history if y in entry_res.entry_history[s]]
        mean_entries.append(statistics.mean(ev) if ev else 0.0)
        mean_insol.append(statistics.mean(iv) if iv else 0.0)
    ax_events.bar(all_yrs, mean_entries, color="green",  alpha=0.7, label="Entries")
    ax_events.bar(all_yrs, [-v for v in mean_insol], color="red", alpha=0.7, label="Insolvencies")
    ax_events.axhline(0, color="black", linewidth=0.5)
    ax_events.set_title(f"Entries & Insolvencies ({entry_res.label})")
    ax_events.set_xlabel("Year"); ax_events.set_ylabel("Count (entries +, insolvencies −)")
    ax_events.legend(fontsize=8)

    # ── Panel 4: HHI bar per config ───────────────────────────────────────────
    x4 = np.arange(n)
    hhi_vals = [r.mean_hhi for r in results]
    bars = ax_hhi_ts.bar(x4, hhi_vals, color=colors)
    ax_hhi_ts.axhspan(0.06, 0.10, color="green", alpha=0.15,
                      label="Lloyd's HHI band (0.06–0.10)")
    for bar, v in zip(bars, hhi_vals):
        ax_hhi_ts.text(bar.get_x() + bar.get_width() / 2, v + 0.003,
                       f"{v:.3f}", ha="center", va="bottom", fontsize=8)
    ax_hhi_ts.set_xticks(x4)
    ax_hhi_ts.set_xticklabels([r.label for r in results], rotation=15, ha="right")
    ax_hhi_ts.set_title("Time-Averaged Syndicate HHI")
    ax_hhi_ts.set_ylabel("HHI"); ax_hhi_ts.legend(fontsize=8)

    fig.suptitle(
        "Experiment 2d — Syndicate Market Entry\n"
        "Hard-market entry trigger: industry loss ratio > 100% in a year "
        "(10 seeds × 60yr, cats enabled)",
        fontsize=11,
    )
    plt.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ── Section: Experiment 3a — Managed Runoff (Central Fund) ───────────────────

def extract_central_fund_history(market: Market, horizon: int) -> dict[int, float]:
    """Return {year: central_fund_payout} for each simulation year."""
    return {y: market.yearly_central_fund.get(y, 0.0) for y in range(horizon)}


class RunoffResult(NamedTuple):
    label:            str
    allow_runoff:     bool
    allow_entry:      bool
    cat_freq:         float
    mean_period:      float | None
    std_period:       float
    mean_amplitude:   float
    mean_lr:          float          # effective LR incl. Central Fund payouts
    mean_hhi:         float
    mean_cf_total:    float          # mean total CF payout per seed over 60yr
    cf_pct_of_claims: float          # CF payouts / total claims (%)
    lr_by_seed:       dict[int, dict[int, float]]
    cf_by_seed:       dict[int, dict[int, float]]
    hhi_by_seed:      dict[int, dict[int, float]]


def run_runoff_sweep(
    configs: list[tuple[str, bool, bool, float]],  # (label, allow_runoff, allow_entry, cat_freq)
    n_syndicates:     int = 10,
    n_brokers:        int = 4,
    n_syndicates_max: int = 20,
    n_seeds:          int = 10,
    horizon:          int = 60,
) -> list[RunoffResult]:
    """Compare no-runoff vs managed-runoff market under cats."""
    print(f"\n  Runoff sweep: {len(configs)} configs × {n_seeds} seeds")
    results = []
    for label, allow_runoff, allow_entry, cat_freq in configs:
        tag = f"runoff={'Y' if allow_runoff else 'N'}, entry={'Y' if allow_entry else 'N'}, cf={cat_freq:.2f}"
        print(f"    [{label:<25}] ({tag}) ...", end=" ", flush=True)
        periods, amplitudes, hhi_vals = [], [], []
        lr_by_seed:  dict[int, dict[int, float]] = {}
        cf_by_seed:  dict[int, dict[int, float]] = {}
        hhi_by_seed: dict[int, dict[int, float]] = {}
        cf_totals:   list[float] = []
        claim_totals: list[float] = []

        for seed in range(n_seeds):
            m = build_simulation(
                n_syndicates=n_syndicates,
                n_brokers=n_brokers,
                horizon_years=horizon,
                enable_cats=True,
                cat_freq=cat_freq,
                allow_runoff=allow_runoff,
                allow_entry=allow_entry,
                n_syndicates_max=n_syndicates_max,
                seed=seed,
            )
            m.run()
            lr = extract_yearly_loss_ratios(m, horizon)
            lr_by_seed[seed] = lr
            cs = detect_cycle(lr)
            if cs.period_years is not None:
                periods.append(cs.period_years)
            amplitudes.append(cs.amplitude)

            # Per-year HHI from syndicate premiums
            hhi_yr: dict[int, float] = {}
            for y in range(horizon):
                synd_prems = [m.syndicate_yearly_premiums[sid][y]
                              for sid in m.syndicates]
                total = sum(synd_prems)
                if total > 0:
                    hhi_yr[y] = sum((p / total) ** 2 for p in synd_prems)
                else:
                    hhi_yr[y] = float("nan")
            hhi_by_seed[seed] = hhi_yr
            hhi_vals.append(_compute_time_avg_hhi(m, horizon))

            cf_hist = extract_central_fund_history(m, horizon)
            cf_by_seed[seed] = cf_hist
            cf_totals.append(sum(cf_hist.values()))
            claim_totals.append(sum(m.yearly_claims.values()))

        mean_period    = statistics.mean(periods)    if periods    else None
        std_period     = statistics.stdev(periods)   if len(periods) > 1 else 0.0
        mean_amplitude = statistics.mean(amplitudes) if amplitudes else 0.0
        valid_hhi      = [h for h in hhi_vals if not math.isnan(h)]
        mean_hhi       = statistics.mean(valid_hhi)  if valid_hhi  else float("nan")
        all_lr         = [v for d in lr_by_seed.values() for v in d.values()]
        mean_lr        = statistics.mean(all_lr) if all_lr else float("nan")
        mean_cf_total  = statistics.mean(cf_totals) if cf_totals else 0.0
        mean_claims    = statistics.mean(claim_totals) if claim_totals else 0.0
        cf_pct         = (mean_cf_total / mean_claims * 100) if mean_claims > 0 else 0.0

        ps = f"{mean_period:.1f}" if mean_period is not None else "N/A"
        print(f"period={ps}, LR={mean_lr:.3f}, HHI={mean_hhi:.3f}, "
              f"CF={mean_cf_total:.0f} ({cf_pct:.1f}%)")
        results.append(RunoffResult(
            label=label,
            allow_runoff=allow_runoff,
            allow_entry=allow_entry,
            cat_freq=cat_freq,
            mean_period=mean_period,
            std_period=std_period,
            mean_amplitude=mean_amplitude,
            mean_lr=mean_lr,
            mean_hhi=mean_hhi,
            mean_cf_total=mean_cf_total,
            cf_pct_of_claims=cf_pct,
            lr_by_seed=lr_by_seed,
            cf_by_seed=cf_by_seed,
            hhi_by_seed=hhi_by_seed,
        ))
    return results


def plot_22_runoff(results: list[RunoffResult], filename: str) -> str:
    """2×2 panels: LR time series / CF payouts / HHI over time / summary bars."""
    if not results:
        return ""

    n = len(results)
    cmap = plt.cm.tab10  # type: ignore[attr-defined]
    colors = [cmap(i / 10) for i in range(n)]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    ax_lr, ax_cf, ax_hhi, ax_summary = (
        axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]
    )

    horizon = max(
        (max(lr_d.keys()) + 1 for r in results for lr_d in r.lr_by_seed.values()
         if lr_d),
        default=60,
    )

    # ── Panel 1: ensemble mean LR time series ─────────────────────────────────
    for res, color in zip(results, colors):
        all_yrs = sorted(set(y for lr_d in res.lr_by_seed.values() for y in lr_d))
        means, stds = [], []
        for y in all_yrs:
            vals = [res.lr_by_seed[s][y] for s in res.lr_by_seed
                    if y in res.lr_by_seed[s]]
            means.append(statistics.mean(vals) if vals else float("nan"))
            stds.append(statistics.stdev(vals) if len(vals) > 1 else 0.0)
        valid = [(y, m, sd) for y, m, sd in zip(all_yrs, means, stds)
                 if not math.isnan(m)]
        if valid:
            yrs_v, means_v, stds_v = zip(*valid)
            yrs_a = np.array(yrs_v)
            means_a = np.array(means_v)
            stds_a  = np.array(stds_v)
            ax_lr.plot(yrs_a, means_a, color=color, linewidth=1.5, label=res.label)
            ax_lr.fill_between(yrs_a, means_a - stds_a, means_a + stds_a,
                               color=color, alpha=0.15)
    ax_lr.axhline(1.0, color="red",    linestyle="--", linewidth=0.8, label="LR=1.0")
    ax_lr.axhline(0.6, color="orange", linestyle=":",  linewidth=0.8, label="LR=0.6")
    ax_lr.set_title("Ensemble Mean Loss Ratio (±1σ)")
    ax_lr.set_xlabel("Year"); ax_lr.set_ylabel("Loss Ratio")
    ax_lr.legend(fontsize=8)

    # ── Panel 2: Central Fund annual payouts ──────────────────────────────────
    runoff_results = [r for r in results if r.allow_runoff]
    for res, color in zip(runoff_results,
                          [colors[i] for i, r in enumerate(results) if r.allow_runoff]):
        all_yrs = list(range(horizon))
        means = []
        for y in all_yrs:
            vals = [res.cf_by_seed[s].get(y, 0.0) for s in res.cf_by_seed]
            means.append(statistics.mean(vals) if vals else 0.0)
        ax_cf.fill_between(all_yrs, means, alpha=0.4, color=color,
                           label=f"{res.label} (CF%={res.cf_pct_of_claims:.1f}%)")
        ax_cf.plot(all_yrs, means, color=color, linewidth=1.0)
    if not runoff_results:
        ax_cf.text(0.5, 0.5, "No runoff configs", transform=ax_cf.transAxes,
                   ha="center", va="center")
    ax_cf.set_title("Central Fund Annual Payouts (runoff configs only)")
    ax_cf.set_xlabel("Year"); ax_cf.set_ylabel("CF Payout")
    ax_cf.legend(fontsize=8)

    # ── Panel 3: HHI over time ────────────────────────────────────────────────
    for res, color in zip(results, colors):
        all_yrs = list(range(horizon))
        means = []
        for y in all_yrs:
            vals = [res.hhi_by_seed[s].get(y, float("nan")) for s in res.hhi_by_seed]
            vals = [v for v in vals if not math.isnan(v)]
            means.append(statistics.mean(vals) if vals else float("nan"))
        valid = [(y, m) for y, m in zip(all_yrs, means) if not math.isnan(m)]
        if valid:
            yrs_v, means_v = zip(*valid)
            ax_hhi.plot(yrs_v, means_v, color=color, linewidth=1.5, label=res.label)
    ax_hhi.axhspan(0.06, 0.10, color="green", alpha=0.15, label="Lloyd's HHI band")
    ax_hhi.set_title("HHI Over Time")
    ax_hhi.set_xlabel("Year"); ax_hhi.set_ylabel("HHI")
    ax_hhi.legend(fontsize=8)

    # ── Panel 4: summary grouped bars ────────────────────────────────────────
    x = np.arange(n)
    width = 0.25
    ax_summary.bar(x - width, [r.mean_amplitude for r in results],
                   width, label="Amplitude", color="steelblue", alpha=0.8)
    ax_summary.bar(x,         [r.mean_lr        for r in results],
                   width, label="Mean LR",  color="darkorange", alpha=0.8)
    ax_summary.bar(x + width, [r.mean_hhi       for r in results],
                   width, label="HHI",      color="green", alpha=0.8)

    ax2 = ax_summary.twinx()
    cf_pcts = [r.cf_pct_of_claims for r in results]
    ax2.plot(x, cf_pcts, "D--", color="purple", linewidth=1.5,
             markersize=6, label="CF% of claims")
    ax2.set_ylabel("CF payout (% of claims)", color="purple")
    ax2.tick_params(axis="y", labelcolor="purple")

    ax_summary.set_xticks(x)
    ax_summary.set_xticklabels([r.label for r in results], rotation=15, ha="right",
                                fontsize=8)
    ax_summary.set_title("Summary: Amplitude / LR / HHI + CF%")
    ax_summary.set_ylabel("Value")
    lines1, labels1 = ax_summary.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax_summary.legend(lines1 + lines2, labels1 + labels2, fontsize=8, loc="upper left")

    fig.suptitle(
        "Experiment 3a — Managed Runoff (Central Fund Guarantee)\n"
        "Insolvent syndicates' outstanding claims paid by Central Fund analogue "
        "(10 seeds × 60yr, cats enabled)",
        fontsize=11,
    )
    plt.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ── Section: Experiment 2c — Asymmetric broker market power ──────────────────

def extract_broker_gwp_shares(market: Market) -> dict[int, float]:
    """Return {broker_id: total_GWP} summed over all bound policies."""
    gwp: dict[int, float] = {}
    for policy in market.risk_registry.values():
        bid = policy["broker_id"]
        total_prem = sum(policy["lead_price"] * line for _, line in policy["shares"])
        gwp[bid] = gwp.get(bid, 0.0) + total_prem
    return gwp


class BrokerPowerResult(NamedTuple):
    label:            str
    broker_power:     float
    n_brokers:        int
    mean_period:      float | None
    mean_amplitude:   float
    mean_lr:          float
    mean_hhi:         float
    broker_shares:    dict[int, list[float]]  # broker_id → [share_per_seed]
    lr_by_seed:       dict[int, dict[int, float]]


def run_broker_power_sweep(
    configs: list[tuple[str, float, int, int]],  # (label, broker_power, n_syn, n_brok)
    n_seeds:  int = 10,
    horizon:  int = 60,
) -> list[BrokerPowerResult]:
    """Sweep over broker_power values and agent counts."""
    print(f"\n  Broker power sweep: {len(configs)} configs × {n_seeds} seeds")
    results = []
    for label, bp, n_syn, n_brok in configs:
        tag = f"Zipf s={bp:.1f}" if bp > 0 else "uniform"
        print(f"    [{label:<18}] ({tag}, {n_syn} syn, {n_brok} brok) ...",
              end=" ", flush=True)
        periods, amplitudes, hhi_vals = [], [], []
        lr_by_seed: dict[int, dict[int, float]] = {}
        broker_shares: dict[int, list[float]] = {}

        for seed in range(n_seeds):
            m = build_simulation(
                n_syndicates=n_syn,
                n_brokers=n_brok,
                horizon_years=horizon,
                enable_cats=False,
                broker_power=bp,
                seed=seed,
            )
            m.run()
            lr = extract_yearly_loss_ratios(m, horizon)
            lr_by_seed[seed] = lr
            cs = detect_cycle(lr)
            if cs.period_years is not None:
                periods.append(cs.period_years)
            amplitudes.append(cs.amplitude)
            hhi_vals.append(_compute_time_avg_hhi(m, horizon))

            gwp = extract_broker_gwp_shares(m)
            total = sum(gwp.values()) or 1.0
            for bid, v in gwp.items():
                if bid not in broker_shares:
                    broker_shares[bid] = []
                broker_shares[bid].append(v / total)

        mean_period    = statistics.mean(periods)    if periods    else None
        mean_amplitude = statistics.mean(amplitudes) if amplitudes else 0.0
        valid_hhi      = [h for h in hhi_vals if not math.isnan(h)]
        mean_hhi       = statistics.mean(valid_hhi)  if valid_hhi  else float("nan")
        all_lr         = [v for d in lr_by_seed.values() for v in d.values()]
        mean_lr        = statistics.mean(all_lr) if all_lr else float("nan")
        ps = f"{mean_period:.1f}" if mean_period is not None else "N/A"
        print(f"period={ps}, LR={mean_lr:.3f}, HHI={mean_hhi:.3f}")
        results.append(BrokerPowerResult(
            label=label,
            broker_power=bp,
            n_brokers=n_brok,
            mean_period=mean_period,
            mean_amplitude=mean_amplitude,
            mean_lr=mean_lr,
            mean_hhi=mean_hhi,
            broker_shares=broker_shares,
            lr_by_seed=lr_by_seed,
        ))
    return results


def plot_20_broker_power(results: list[BrokerPowerResult], filename: str) -> str:
    """Four-panel: broker GWP shares / cumulative share / LR time series / cycle stats."""
    if not results:
        return ""

    n = len(results)
    cmap = plt.cm.tab10  # type: ignore[attr-defined]
    config_colors = [cmap(i / 10) for i in range(n)]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    ax_shares, ax_cumul, ax_lr, ax_cycle = (
        axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]
    )

    # ── Panel 1: broker GWP share by rank (mean ± std across seeds) ──────────
    for res, color in zip(results, config_colors):
        bids = sorted(res.broker_shares.keys())
        means = [statistics.mean(res.broker_shares[b]) for b in bids]
        stds  = [statistics.stdev(res.broker_shares[b])
                 if len(res.broker_shares[b]) > 1 else 0.0 for b in bids]
        x = np.arange(len(bids))
        width = 0.8 / n
        offset = (list(results).index(res) - n / 2 + 0.5) * width
        ax_shares.bar(x + offset, means, width, yerr=stds,
                      color=color, alpha=0.8, capsize=3, label=res.label)
    if results:
        n_brok_ref = results[0].n_brokers
        ax_shares.axhline(1.0 / n_brok_ref, color="grey", linestyle="--",
                          linewidth=0.8, label=f"Uniform 1/{n_brok_ref}")
    ax_shares.set_title("Broker GWP Share by Rank")
    ax_shares.set_xlabel("Broker ID"); ax_shares.set_ylabel("Share of total GWP")
    ax_shares.legend(fontsize=8)

    # ── Panel 2: cumulative GWP share (sorted desc) + Lloyd's top-5=60% ref ──
    for res, color in zip(results, config_colors):
        bids = sorted(res.broker_shares.keys())
        means = sorted(
            [statistics.mean(res.broker_shares[b]) for b in bids],
            reverse=True,
        )
        cumul = np.cumsum(means)
        ax_cumul.plot(range(1, len(cumul) + 1), cumul,
                      color=color, marker="o", markersize=5, label=res.label)
    if any(r.n_brokers >= 5 for r in results):
        ax_cumul.axhline(0.60, color="red", linestyle="--", linewidth=0.8,
                         label="Lloyd's top-5 = 60%")
    ax_cumul.set_title("Cumulative Broker GWP Share")
    ax_cumul.set_xlabel("No. of top brokers"); ax_cumul.set_ylabel("Cumulative share")
    ax_cumul.set_ylim(0, 1.05); ax_cumul.legend(fontsize=8)

    # ── Panel 3: ensemble mean LR time series ─────────────────────────────────
    for res, color in zip(results, config_colors):
        all_yrs = sorted(set(y for lr_d in res.lr_by_seed.values() for y in lr_d))
        means = []
        for y in all_yrs:
            vals = [res.lr_by_seed[s][y] for s in res.lr_by_seed
                    if y in res.lr_by_seed[s]]
            means.append(statistics.mean(vals) if vals else float("nan"))
        valid = [(y, m) for y, m in zip(all_yrs, means) if not math.isnan(m)]
        if valid:
            yrs_v, means_v = zip(*valid)
            ax_lr.plot(yrs_v, means_v, color=color, linewidth=1.5, label=res.label)
    ax_lr.axhline(1.0, color="red", linestyle="--", linewidth=0.8)
    ax_lr.set_title("Ensemble Mean Loss Ratio")
    ax_lr.set_xlabel("Year"); ax_lr.set_ylabel("Loss Ratio")
    ax_lr.legend(fontsize=8)

    # ── Panel 4: period / amplitude / HHI grouped bar ─────────────────────────
    x4 = np.arange(n)
    width = 0.25
    periods    = [r.mean_period    if r.mean_period is not None else 0.0 for r in results]
    amplitudes = [r.mean_amplitude for r in results]
    hhis       = [r.mean_hhi       for r in results]
    ax4b = ax_cycle.twinx()
    ax_cycle.bar(x4 - width, periods,    width, color="steelblue",  alpha=0.8, label="Period (yr)")
    ax_cycle.bar(x4,         amplitudes, width, color="darkorange", alpha=0.8, label="Amplitude")
    ax4b.bar(    x4 + width, hhis,       width, color="green",      alpha=0.6, label="Syn. HHI")
    ax_cycle.axhline(6.0, color="steelblue", linestyle="--", linewidth=0.8,
                     label="Lloyd's target 6yr")
    ax_cycle.set_xticks(x4)
    ax_cycle.set_xticklabels([r.label for r in results], rotation=15, ha="right")
    ax_cycle.set_ylabel("Period (yr) / Amplitude", color="steelblue")
    ax4b.set_ylabel("Syndicate HHI", color="green")
    ax_cycle.set_title("Cycle Stats & Syndicate HHI")
    lines1, labs1 = ax_cycle.get_legend_handles_labels()
    lines2, labs2 = ax4b.get_legend_handles_labels()
    ax_cycle.legend(lines1 + lines2, labs1 + labs2, fontsize=8, loc="upper right")

    fig.suptitle(
        "Experiment 2c — Asymmetric Broker Market Power\n"
        "Uniform vs Zipf-distributed broker weights (no cats, 10 seeds × 60yr)",
        fontsize=11,
    )
    plt.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ── LobResult (Experiment 1b) ─────────────────────────────────────────────────

class LobResult(NamedTuple):
    lob:            str
    mean_period:    float | None
    std_period:     float
    mean_amplitude: float
    mean_lr:        float
    mean_hhi:       float
    mean_cr:        float
    lr_by_seed:     dict[int, dict[int, float]]
    cr_by_seed:     dict[int, dict[int, float]]


def run_lob_sweep(
    lob_configs: dict[str, dict],
    n_syndicates: int = 10,
    n_brokers:    int = 4,
    n_regions:    int = 5,
    n_seeds:      int = 10,
    horizon:      int = 60,
) -> list[LobResult]:
    """Run one full-market simulation per LOB config and return LobResults."""
    print(f"\n  LOB sweep: {len(lob_configs)} LOBs × {n_seeds} seeds")
    results = []
    for lob, cfg in lob_configs.items():
        print(f"    [{lob:<10}] rpy={cfg['risks_per_year']:>3}, "
              f"cap={cfg['initial_capital']:>5.0f}, "
              f"cats={'Y' if cfg['enable_cats'] else 'N'} "
              f"cf={cfg['cat_freq']:.2f} ...", end=" ", flush=True)
        periods, amplitudes, hhi_vals = [], [], []
        lr_by_seed: dict[int, dict[int, float]] = {}
        cr_by_seed: dict[int, dict[int, float]] = {}
        for seed in range(n_seeds):
            m = build_simulation(
                n_syndicates=n_syndicates,
                n_brokers=n_brokers,
                n_regions=n_regions,
                horizon_years=horizon,
                seed=seed,
                **{k: v for k, v in cfg.items()},
            )
            m.run()
            lr = extract_yearly_loss_ratios(m, horizon)
            cr = compute_combined_ratio_series(m, horizon)
            lr_by_seed[seed] = lr
            cr_by_seed[seed] = cr
            cs = detect_cycle(lr)
            if cs.period_years is not None:
                periods.append(cs.period_years)
            amplitudes.append(cs.amplitude)
            hhi_vals.append(_compute_time_avg_hhi(m, horizon))

        mean_period    = statistics.mean(periods)    if periods    else None
        std_period     = statistics.stdev(periods)   if len(periods) > 1 else 0.0
        mean_amplitude = statistics.mean(amplitudes) if amplitudes else 0.0
        valid_hhi      = [h for h in hhi_vals if not math.isnan(h)]
        mean_hhi       = statistics.mean(valid_hhi)  if valid_hhi  else float("nan")
        all_lr = [v for d in lr_by_seed.values() for v in d.values()]
        all_cr = [v for d in cr_by_seed.values() for v in d.values()]
        mean_lr = statistics.mean(all_lr) if all_lr else float("nan")
        mean_cr = statistics.mean(all_cr) if all_cr else float("nan")
        ps = f"{mean_period:.1f}" if mean_period is not None else "N/A"
        print(f"period={ps}, LR={mean_lr:.3f}, CR={mean_cr:.3f}")
        results.append(LobResult(
            lob=lob,
            mean_period=mean_period, std_period=std_period,
            mean_amplitude=mean_amplitude,
            mean_lr=mean_lr, mean_hhi=mean_hhi, mean_cr=mean_cr,
            lr_by_seed=lr_by_seed, cr_by_seed=cr_by_seed,
        ))
    return results


def plot_17_lob_decomp(results: list[LobResult], filename: str) -> str:
    """Four-panel: LR time series / cycle stats / CR vs target / HHI."""
    if not results:
        return ""

    n = len(results)
    cmap = plt.cm.tab10  # type: ignore[attr-defined]
    colors = [cmap(i / 10) for i in range(n)]
    lob_labels = [r.lob for r in results]
    x = np.arange(n)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    ax_lr, ax_stats, ax_cr, ax_hhi = (
        axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]
    )

    # ── Panel 1: LR time series ───────────────────────────────────────────────
    all_years = sorted(set(y for r in results
                           for d in r.lr_by_seed.values() for y in d))
    for res, color in zip(results, colors):
        means = _ensemble_mean(res.lr_by_seed, all_years)
        valid_yrs = [y for y, m in zip(all_years, means) if not math.isnan(m)]
        valid_m   = [m for m in means if not math.isnan(m)]
        ax_lr.plot(valid_yrs, valid_m, color=color, linewidth=1.8, label=res.lob)

    ax_lr.axhline(1.0, color="red",   linestyle="--", linewidth=0.8)
    ax_lr.axhline(0.6, color="green", linestyle="--", linewidth=0.8)
    ax_lr.set_xlabel("Year")
    ax_lr.set_ylabel("Loss Ratio")
    ax_lr.set_title("Ensemble Mean LR by LOB Proxy")
    ax_lr.legend(fontsize=8)

    # ── Panel 2: Period and amplitude grouped bar ─────────────────────────────
    width = 0.35
    periods = [r.mean_period if r.mean_period is not None else 0.0 for r in results]
    period_stds = [r.std_period for r in results]
    amplitudes = [r.mean_amplitude for r in results]

    ax_stats.bar(x - width / 2, periods, width, yerr=period_stds,
                 color=[c for c in colors], alpha=0.85, edgecolor="black",
                 linewidth=0.5, capsize=3, label="Period (yr)")
    ax_stats2 = ax_stats.twinx()
    ax_stats2.bar(x + width / 2, amplitudes, width,
                  color=[c for c in colors], alpha=0.45, edgecolor="black",
                  linewidth=0.5, hatch="//", label="Amplitude")
    ax_stats.axhline(6.0, color="darkgreen", linestyle="--", linewidth=1.2,
                     label="Lloyd's target period (6 yr)")
    ax_stats.set_xticks(x)
    ax_stats.set_xticklabels(lob_labels, fontsize=9)
    ax_stats.set_ylabel("Cycle Period (years)")
    ax_stats2.set_ylabel("Amplitude", color="grey")
    ax_stats.set_title("Cycle Period & Amplitude by LOB")
    lines1, labels1 = ax_stats.get_legend_handles_labels()
    lines2, labels2 = ax_stats2.get_legend_handles_labels()
    ax_stats.legend(lines1 + lines2, labels1 + labels2, fontsize=7)

    # ── Panel 3: Combined ratio time series ───────────────────────────────────
    cr_years = sorted(set(y for r in results
                          for d in r.cr_by_seed.values() for y in d))
    for res, color in zip(results, colors):
        means = _ensemble_mean(res.cr_by_seed, cr_years)
        valid_yrs = [y for y, m in zip(cr_years, means) if not math.isnan(m)]
        valid_m   = [m for m in means if not math.isnan(m)]
        ax_cr.plot(valid_yrs, valid_m, color=color, linewidth=1.8, label=res.lob)

    ax_cr.axhspan(0.90, 1.10, alpha=0.12, color="green",
                  label="Lloyd's target (90–110%)")
    ax_cr.axhline(1.0, color="grey", linewidth=0.7, linestyle="--")
    ax_cr.set_xlabel("Year")
    ax_cr.set_ylabel("Combined Ratio")
    ax_cr.set_title("Combined Ratio by LOB")
    ax_cr.legend(fontsize=8)

    # ── Panel 4: Summary bar: mean LR and HHI ────────────────────────────────
    mean_lrs = [r.mean_lr  for r in results]
    mean_hhis = [r.mean_hhi for r in results]

    ax_hhi.bar(x - width / 2, mean_lrs, width,
               color=colors, alpha=0.85, edgecolor="black",
               linewidth=0.5, label="Mean LR")
    ax_hhi2 = ax_hhi.twinx()
    ax_hhi2.bar(x + width / 2, mean_hhis, width,
                color=colors, alpha=0.45, edgecolor="black",
                linewidth=0.5, hatch="//", label="Mean HHI")
    ax_hhi.axhline(0.49, color="steelblue", linestyle="--", linewidth=1.0,
                   label="Baseline LR (0.49)")
    ax_hhi2.axhspan(0.06, 0.10, alpha=0.15, color="green")
    ax_hhi.set_xticks(x)
    ax_hhi.set_xticklabels(lob_labels, fontsize=9)
    ax_hhi.set_ylabel("Mean Loss Ratio")
    ax_hhi2.set_ylabel("Mean HHI", color="grey")
    ax_hhi.set_title("Mean LR & HHI by LOB")
    lines1, labels1 = ax_hhi.get_legend_handles_labels()
    lines2, labels2 = ax_hhi2.get_legend_handles_labels()
    ax_hhi.legend(lines1 + lines2, labels1 + labels2, fontsize=7)

    fig.suptitle(
        "Experiment 1b: LOB Decomposition — Per-LOB Proxy Simulations\n"
        "10 seeds × 60yr | LOBs proxied via risks_per_year, capital, cat_freq, claim_est",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


# ── Section 9c: Calibration dashboard (Experiment 1a) ────────────────────────

# Lloyd's of London reference ranges (sources: Annual Report 2023, Owadally 2018)
LLOYDS_BENCHMARKS: dict[str, tuple[float, float]] = {
    "period":         (5.0,  7.0),   # yr — Owadally 2018 UK property
    "hhi":            (0.06, 0.10),  # syndicate GWP share — Lloyd's AR 2023
    "combined_ratio": (0.90, 1.10),  # all years — Lloyd's AR 2023
    "roc":            (0.05, 0.15),  # 5% hard market, 15% soft market
    "cat_lr_delta":   (0.30, 0.50),  # +30–50 pp LR increase in cat years
}


def compute_combined_ratio_series(market: Market, horizon: int) -> dict[int, float]:
    """Return {year: combined_ratio} = (claims + expenses) / premium."""
    result = {}
    for y in range(horizon):
        total_prem = sum(market.syndicate_yearly_premiums[sid].get(y, 0.0)
                         for sid in market.syndicates)
        total_claims = sum(market.syndicate_yearly_claims[sid].get(y, 0.0)
                           for sid in market.syndicates)
        total_expenses = sum(
            market.syndicate_yearly_premiums[sid].get(y, 0.0)
            * market.syndicates[sid].expense_rate
            for sid in market.syndicates
        )
        if total_prem > 0:
            result[y] = (total_claims + total_expenses) / total_prem
    return result


def compute_roc_series(market: Market, horizon: int) -> dict[int, float]:
    """Return {year: mean_RoC_across_solvent_syndicates}.
    RoC = (premium − claims − expenses) / end-of-year capital."""
    result = {}
    for y in range(horizon):
        rocs = []
        for sid, syn in market.syndicates.items():
            prem    = market.syndicate_yearly_premiums[sid].get(y, 0.0)
            claims  = market.syndicate_yearly_claims[sid].get(y, 0.0)
            capital = market.syndicate_yearly_capital[sid].get(y, 0.0)
            if capital > 0 and prem > 0:
                profit = prem - claims - prem * syn.expense_rate
                rocs.append(profit / capital)
        if rocs:
            result[y] = statistics.mean(rocs)
    return result


# ── Section: Experiment 2a — Per-region catastrophe rates ────────────────────

class RegionCatResult(NamedTuple):
    label:             str
    cat_freq_dict:     dict[int, float]   # region → annual rate
    mean_period:       float | None
    std_period:        float
    mean_amplitude:    float
    mean_lr:           float
    mean_hhi:          float
    region_cat_counts: dict[int, list[int]]  # region → [count_per_seed]
    lr_by_seed:        dict[int, dict[int, float]]


def run_per_region_cat_sweep(
    configs: list[tuple[str, dict[int, float]]],  # (label, cat_freq_dict)
    n_syndicates: int = 10,
    n_brokers:    int = 4,
    n_regions:    int = 5,
    n_seeds:      int = 10,
    horizon:      int = 60,
) -> list[RegionCatResult]:
    """Run 10 seeds × 60yr per config with per-region cat_freq dicts."""
    print(f"\n  Per-region cat sweep: {len(configs)} configs × {n_seeds} seeds")
    results = []
    for label, freq_dict in configs:
        total_rate = sum(freq_dict.values())
        print(f"    [{label:<16}] total_rate={total_rate:.2f}/yr ...",
              end=" ", flush=True)
        periods, amplitudes, hhi_vals = [], [], []
        lr_by_seed: dict[int, dict[int, float]] = {}
        region_cat_counts: dict[int, list[int]] = {r: [] for r in range(n_regions)}

        for seed in range(n_seeds):
            m = build_simulation(
                n_syndicates=n_syndicates,
                n_brokers=n_brokers,
                n_regions=n_regions,
                horizon_years=horizon,
                enable_cats=True,
                cat_freq=freq_dict,
                seed=seed,
            )
            m.run()

            # Count actual cat events fired per region (from event store)
            for r in range(n_regions):
                cnt = sum(
                    1 for ev in m.event_store
                    if ev.kind == EventKind.CATASTROPHE_OCCURRED
                    and ev.payload.get("region") == r
                )
                region_cat_counts[r].append(cnt)

            lr = extract_yearly_loss_ratios(m, horizon)
            lr_by_seed[seed] = lr
            cs = detect_cycle(lr)
            if cs.period_years is not None:
                periods.append(cs.period_years)
            amplitudes.append(cs.amplitude)
            hhi_vals.append(_compute_time_avg_hhi(m, horizon))

        mean_period    = statistics.mean(periods)    if periods    else None
        std_period     = statistics.stdev(periods)   if len(periods) > 1 else 0.0
        mean_amplitude = statistics.mean(amplitudes) if amplitudes else 0.0
        valid_hhi      = [h for h in hhi_vals if not math.isnan(h)]
        mean_hhi       = statistics.mean(valid_hhi)  if valid_hhi  else float("nan")
        all_lr         = [v for d in lr_by_seed.values() for v in d.values()]
        mean_lr        = statistics.mean(all_lr) if all_lr else float("nan")
        ps = f"{mean_period:.1f}" if mean_period is not None else "N/A"
        print(f"period={ps}, LR={mean_lr:.3f}, HHI={mean_hhi:.3f}")
        results.append(RegionCatResult(
            label=label,
            cat_freq_dict=freq_dict,
            mean_period=mean_period,
            std_period=std_period,
            mean_amplitude=mean_amplitude,
            mean_lr=mean_lr,
            mean_hhi=mean_hhi,
            region_cat_counts=region_cat_counts,
            lr_by_seed=lr_by_seed,
        ))
    return results


def plot_18_per_region_cats(results: list[RegionCatResult], filename: str) -> str:
    """Four-panel: LR time series / amplitude bar / region cat counts / HHI bar."""
    if not results:
        return ""

    n = len(results)
    cmap = plt.cm.tab10  # type: ignore[attr-defined]
    colors = [cmap(i / 10) for i in range(n)]
    labels = [r.label for r in results]
    x = np.arange(n)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    ax_lr, ax_amp, ax_cnt, ax_hhi = (
        axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]
    )

    # ── Panel 1: ensemble mean LR time series ─────────────────────────────────
    for res, color in zip(results, colors):
        all_yrs = sorted(set(y for lr_d in res.lr_by_seed.values() for y in lr_d))
        means = []
        for y in all_yrs:
            vals = [res.lr_by_seed[s][y] for s in res.lr_by_seed
                    if y in res.lr_by_seed[s]]
            means.append(statistics.mean(vals) if vals else float("nan"))
        valid = [(y, m) for y, m in zip(all_yrs, means) if not math.isnan(m)]
        if valid:
            yrs_v, means_v = zip(*valid)
            ax_lr.plot(yrs_v, means_v, color=color, linewidth=1.5, label=res.label)
    ax_lr.axhline(1.0, color="red", linestyle="--", linewidth=0.8, label="LR=1")
    ax_lr.set_title("Ensemble Mean Loss Ratio")
    ax_lr.set_xlabel("Year")
    ax_lr.set_ylabel("Loss Ratio")
    ax_lr.legend(fontsize=8)

    # ── Panel 2: cycle amplitude bar chart ────────────────────────────────────
    ax_amp.bar(x, [r.mean_amplitude for r in results], color=colors)
    ax_amp.axhline(0.5, color="orange", linestyle="--", linewidth=0.8,
                   label="Cat spike ref (0.50)")
    ax_amp.set_xticks(x)
    ax_amp.set_xticklabels(labels, rotation=15, ha="right")
    ax_amp.set_title("Cycle Amplitude by Config")
    ax_amp.set_ylabel("Amplitude")
    ax_amp.legend(fontsize=8)

    # ── Panel 3: per-region cat event count distribution (box plots) ──────────
    n_regions = max(
        (max(res.region_cat_counts.keys()) + 1 for res in results if res.region_cat_counts),
        default=5
    )
    region_labels = [f"R{r}" for r in range(n_regions)]
    width = 0.8 / max(n, 1)
    for i, (res, color) in enumerate(zip(results, colors)):
        positions = [r + (i - n / 2 + 0.5) * width for r in range(n_regions)]
        data = [res.region_cat_counts.get(r, [0]) for r in range(n_regions)]
        ax_cnt.boxplot(
            data, positions=positions, widths=width * 0.85,
            patch_artist=True, manage_ticks=False,
            boxprops=dict(facecolor=color, alpha=0.7),
            medianprops=dict(color="black"),
            whiskerprops=dict(color=color),
            capprops=dict(color=color),
            flierprops=dict(marker=".", markersize=3, color=color),
        )
        # phantom line for legend
        ax_cnt.plot([], [], color=color, linewidth=4,
                    alpha=0.7, label=res.label)
    ax_cnt.set_xticks(range(n_regions))
    ax_cnt.set_xticklabels(region_labels)
    ax_cnt.set_title("Per-Region Cat Event Count Distribution")
    ax_cnt.set_xlabel("Region")
    ax_cnt.set_ylabel("Cat events over 60yr (per seed)")
    ax_cnt.legend(fontsize=8)

    # ── Panel 4: HHI bar with Lloyd's reference band ──────────────────────────
    hhi_vals = [r.mean_hhi for r in results]
    bars = ax_hhi.bar(x, hhi_vals, color=colors)
    ax_hhi.axhspan(0.06, 0.10, color="green", alpha=0.15, label="Lloyd's HHI band (0.06–0.10)")
    for bar, v in zip(bars, hhi_vals):
        ax_hhi.text(bar.get_x() + bar.get_width() / 2, v + 0.002,
                    f"{v:.3f}", ha="center", va="bottom", fontsize=8)
    ax_hhi.set_xticks(x)
    ax_hhi.set_xticklabels(labels, rotation=15, ha="right")
    ax_hhi.set_title("Time-Averaged HHI by Config")
    ax_hhi.set_ylabel("HHI")
    ax_hhi.legend(fontsize=8)

    fig.suptitle(
        "Experiment 2a — Per-Region Catastrophe Rates\n"
        "Three geographic peril distributions (10 syn, 4 brok, 5 regions, 25 rpy, "
        "10 seeds × 60yr)",
        fontsize=11,
    )
    plt.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ── CalibResult ────────────────────────────────────────────────────────────────

class CalibResult(NamedTuple):
    label:            str
    n_syndicates:     int
    risks_per_year:   int
    enable_cats:      bool
    mean_period:      float | None
    mean_hhi:         float
    mean_cr:          float   # mean combined ratio
    mean_roc:         float
    lr_by_seed:       dict[int, dict[int, float]]
    cr_by_seed:       dict[int, dict[int, float]]
    roc_by_seed:      dict[int, dict[int, float]]


def run_calibration_sweep(
    configs: list[tuple[str, int, int, int, bool]],  # (label, n_syn, n_brok, rpy, cats)
    n_seeds: int = 10,
    horizon: int = 60,
) -> list[CalibResult]:
    """Run calibration configs, extract period/HHI/CR/RoC."""
    print(f"\n  Calibration sweep: {len(configs)} configs × {n_seeds} seeds")
    results = []
    for label, n_syn, n_brok, rpy, cats in configs:
        print(f"    [{label}] n_syn={n_syn}, rpy={rpy}, cats={cats} ...",
              end=" ", flush=True)
        periods, hhi_vals = [], []
        lr_by_seed:  dict[int, dict[int, float]] = {}
        cr_by_seed:  dict[int, dict[int, float]] = {}
        roc_by_seed: dict[int, dict[int, float]] = {}

        for seed in range(n_seeds):
            m = build_simulation(
                n_syndicates=n_syn, n_brokers=n_brok,
                risks_per_year=rpy, horizon_years=horizon,
                enable_cats=cats, cat_freq=0.05,
                seed=seed,
            )
            m.run()
            lr  = extract_yearly_loss_ratios(m, horizon)
            cr  = compute_combined_ratio_series(m, horizon)
            roc = compute_roc_series(m, horizon)
            lr_by_seed[seed]  = lr
            cr_by_seed[seed]  = cr
            roc_by_seed[seed] = roc
            cs = detect_cycle(lr)
            if cs.period_years is not None:
                periods.append(cs.period_years)
            hhi_vals.append(_compute_time_avg_hhi(m, horizon))

        mean_period = statistics.mean(periods) if periods else None
        mean_hhi    = statistics.mean(hhi_vals) if hhi_vals else float("nan")
        all_cr  = [v for d in cr_by_seed.values()  for v in d.values()]
        all_roc = [v for d in roc_by_seed.values() for v in d.values()]
        mean_cr  = statistics.mean(all_cr)  if all_cr  else float("nan")
        mean_roc = statistics.mean(all_roc) if all_roc else float("nan")
        period_str = f"{mean_period:.1f}" if mean_period is not None else "N/A"
        print(f"period={period_str}, CR={mean_cr:.3f}, RoC={mean_roc:.3f}, HHI={mean_hhi:.3f}")
        results.append(CalibResult(
            label=label, n_syndicates=n_syn, risks_per_year=rpy,
            enable_cats=cats,
            mean_period=mean_period, mean_hhi=mean_hhi,
            mean_cr=mean_cr, mean_roc=mean_roc,
            lr_by_seed=lr_by_seed, cr_by_seed=cr_by_seed, roc_by_seed=roc_by_seed,
        ))
    return results


def _ensemble_mean(by_seed: dict[int, dict[int, float]], all_years: list[int]) -> list[float]:
    means = []
    for y in all_years:
        vals = [by_seed[s][y] for s in by_seed if y in by_seed[s]]
        means.append(statistics.mean(vals) if vals else float("nan"))
    return means


def plot_16_calibration(results: list[CalibResult],
                        cat_delta: float,
                        filename: str) -> str:
    """Four-panel calibration dashboard vs Lloyd's benchmarks."""
    if not results:
        return ""

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    ax_score, ax_cr, ax_roc, ax_hhi = (
        axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]
    )

    # ── Panel 1: Bullet scorecard ─────────────────────────────────────────────
    metrics = ["period", "hhi", "combined_ratio", "roc", "cat_lr_delta"]
    metric_labels = {
        "period":         "Cycle Period (yr)",
        "hhi":            "HHI",
        "combined_ratio": "Combined Ratio",
        "roc":            "Return on Capital",
        "cat_lr_delta":   "Cat LR Spike (pp)",
    }
    metric_scale = {  # multiplier for display (e.g. percentage points)
        "period": 1, "hhi": 1, "combined_ratio": 1, "roc": 1, "cat_lr_delta": 100,
    }

    def get_metric(r: CalibResult, m: str) -> float:
        if m == "period":
            return r.mean_period if r.mean_period is not None else float("nan")
        if m == "hhi":         return r.mean_hhi
        if m == "combined_ratio": return r.mean_cr
        if m == "roc":         return r.mean_roc
        if m == "cat_lr_delta": return cat_delta * 100
        return float("nan")

    # Only non-cat results for scorecard (cats handled separately)
    no_cat = [r for r in results if not r.enable_cats]

    y_pos = list(range(len(metrics)))
    ax_score.set_yticks(y_pos)
    ax_score.set_yticklabels([metric_labels[m] for m in metrics], fontsize=9)
    ax_score.set_title("Scorecard vs Lloyd's Benchmarks")
    ax_score.axvline(0, color="black", linewidth=0.5)

    colors_nc = plt.cm.tab10.colors  # type: ignore[attr-defined]
    for yi, m in enumerate(metrics):
        lo, hi = LLOYDS_BENCHMARKS[m]
        scale = metric_scale[m]
        lo_s, hi_s = lo * scale, hi * scale
        # Green reference band
        ax_score.barh(yi, hi_s - lo_s, left=lo_s, height=0.5,
                      color="green", alpha=0.20, zorder=1)
        ax_score.plot([lo_s, hi_s], [yi, yi], color="green",
                      linewidth=1.5, zorder=2)
        # Sim values
        for ri, r in enumerate(no_cat):
            val = get_metric(r, m) * scale
            if not math.isnan(val):
                ax_score.scatter(val, yi + 0.15 * ri - 0.1,
                                 color=colors_nc[ri % 10], s=70, zorder=5,
                                 marker="o", label=r.label if yi == 0 else "")
        # Cat value for cat_lr_delta only
        if m == "cat_lr_delta" and cat_delta > 0:
            ax_score.scatter(cat_delta * 100, yi,
                             color="red", s=90, zorder=5, marker="^",
                             label="cat regime" if yi == 0 else "")

    ax_score.set_xlabel("Metric value")
    ax_score.legend(fontsize=8, loc="lower right")

    # ── Panel 2: Combined ratio time series ───────────────────────────────────
    line_styles = ["-", "--", "-.", ":"]
    all_years = sorted(set(y for r in results
                           for d in r.cr_by_seed.values() for y in d))
    for ri, r in enumerate(results):
        means = _ensemble_mean(r.cr_by_seed, all_years)
        valid_yrs = [y for y, m in zip(all_years, means) if not math.isnan(m)]
        valid_m   = [m for m in means if not math.isnan(m)]
        ax_cr.plot(valid_yrs, valid_m,
                   linestyle=line_styles[ri % 4],
                   color=colors_nc[ri % 10], linewidth=1.8, label=r.label)

    ax_cr.axhspan(0.90, 1.10, alpha=0.12, color="green",
                  label="Lloyd's target (90–110%)")
    ax_cr.axhline(1.0, color="grey", linewidth=0.7, linestyle="--")
    ax_cr.set_xlabel("Year")
    ax_cr.set_ylabel("Combined Ratio")
    ax_cr.set_title("Combined Ratio: Simulation vs Lloyd's Target Band")
    ax_cr.legend(fontsize=8)

    # ── Panel 3: Return on Capital time series ────────────────────────────────
    for ri, r in enumerate(no_cat):
        roc_years = sorted(set(y for d in r.roc_by_seed.values() for y in d))
        means = _ensemble_mean(r.roc_by_seed, roc_years)
        valid_yrs = [y for y, m in zip(roc_years, means) if not math.isnan(m)]
        valid_m   = [m for m in means if not math.isnan(m)]
        ax_roc.plot(valid_yrs, valid_m,
                    color=colors_nc[ri % 10], linewidth=1.8, label=r.label)

    ax_roc.axhspan(0.05, 0.15, alpha=0.12, color="green",
                   label="Lloyd's target (5–15%)")
    ax_roc.axhline(0.0, color="red", linewidth=0.8, linestyle="--")
    ax_roc.set_xlabel("Year")
    ax_roc.set_ylabel("Return on Capital")
    ax_roc.set_title("Return on Capital (no-cat configs)")
    ax_roc.legend(fontsize=8)

    # ── Panel 4: HHI time series ──────────────────────────────────────────────
    # Compute yearly HHI per seed for no-cat configs
    for ri, r in enumerate(no_cat):
        # We only have aggregate HHI; plot it as a horizontal marker + label
        ax_hhi.axhline(r.mean_hhi, color=colors_nc[ri % 10], linewidth=2,
                       linestyle="-", label=f"{r.label}: {r.mean_hhi:.3f}")

    ax_hhi.axhspan(0.06, 0.10, alpha=0.15, color="green",
                   label="Lloyd's target (0.06–0.10)")
    ax_hhi.set_ylim(0, max(0.20, max(r.mean_hhi for r in no_cat) + 0.02))
    ax_hhi.set_xlabel("(time-averaged values)")
    ax_hhi.set_ylabel("HHI")
    ax_hhi.set_title("Market Concentration: HHI vs Lloyd's Band")
    ax_hhi.legend(fontsize=8)
    ax_hhi.set_xticks([])

    fig.suptitle(
        "Experiment 1a: Calibration Dashboard — Simulation vs Lloyd's Benchmarks\n"
        "10 seeds × 60yr | green bands = Lloyd's reference range",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


# ── Section: Experiment 3b — High-Volume Lloyd's-Realistic Density ────────────

class HighVolumeResult(NamedTuple):
    label:            str
    risks_per_year:   int
    n_syndicates:     int
    risks_per_syn_yr: float        # rpy / n_syn
    mean_lr:          float
    mean_amplitude:   float
    mean_period:      float | None
    mean_hhi:         float
    mean_cr:          float
    mean_roc:         float
    lr_cv:            float        # std/mean of LR series — primary LLN metric
    runtime_seconds:  float
    lr_by_seed:       dict[int, dict[int, float]]
    cr_by_seed:       dict[int, dict[int, float]]
    roc_by_seed:      dict[int, dict[int, float]]


def run_high_volume_sweep(
    configs: list[tuple[str, int, int, int, int]],  # (label, n_syn, n_brok, rpy, cap)
    n_seeds:  int = 5,
    horizon:  int = 25,
) -> list[HighVolumeResult]:
    """Compare ref (low-rpy) vs high-volume arms; no cats, no lob_params."""
    print(f"\n  High-volume sweep: {len(configs)} configs × {n_seeds} seeds × {horizon}yr")
    results = []
    for label, n_syn, n_brok, rpy, cap in configs:
        rps = rpy / n_syn
        print(f"    [{label:<18}] n_syn={n_syn}, rpy={rpy} ({rps:.1f}/syn/yr) ...",
              end=" ", flush=True)
        t0 = time.time()

        periods, amplitudes, hhi_vals = [], [], []
        lr_by_seed:  dict[int, dict[int, float]] = {}
        cr_by_seed:  dict[int, dict[int, float]] = {}
        roc_by_seed: dict[int, dict[int, float]] = {}

        for seed in range(n_seeds):
            m = build_simulation(
                n_syndicates=n_syn,
                n_brokers=n_brok,
                risks_per_year=rpy,
                initial_capital=cap,
                horizon_years=horizon,
                enable_cats=False,
                lob_params=None,
                seed=seed,
            )
            m.run()

            lr = extract_yearly_loss_ratios(m, horizon)
            lr_by_seed[seed] = lr
            cr = compute_combined_ratio_series(m, horizon)
            cr_by_seed[seed] = cr
            roc = compute_roc_series(m, horizon)
            roc_by_seed[seed] = roc

            cs = detect_cycle(lr)
            if cs.period_years is not None:
                periods.append(cs.period_years)
            amplitudes.append(cs.amplitude)
            hhi_vals.append(_compute_time_avg_hhi(m, horizon))

            # Smoke-check on seed 0
            if seed == 0:
                total_bound   = len(m.risk_registry)
                total_declined = sum(
                    1 for ev in m.event_store
                    if ev.kind == EventKind.RISK_DECLINED
                ) if hasattr(EventKind, "RISK_DECLINED") else 0
                max_pcr = 0.0
                for sid in m.syndicates:
                    annual_prems = [
                        m.syndicate_yearly_premiums[sid].get(y, 0.0)
                        for y in range(horizon)
                    ]
                    max_prem = max(annual_prems) if annual_prems else 0.0
                    cap_val  = m.syndicate_yearly_capital[sid].get(0, float("nan"))
                    if cap_val > 0:
                        max_pcr = max(max_pcr, max_prem / cap_val)
                decline_rate = (
                    total_declined / (total_bound + total_declined) * 100
                    if (total_bound + total_declined) > 0 else 0.0
                )
                print(f"\n      [smoke seed=0] bound={total_bound}, "
                      f"decline_rate={decline_rate:.1f}%, max_PCR={max_pcr:.3f}")
                print(f"      ", end="")

        runtime = time.time() - t0

        mean_period    = statistics.mean(periods)    if periods    else None
        mean_amplitude = statistics.mean(amplitudes) if amplitudes else 0.0
        valid_hhi      = [h for h in hhi_vals if not math.isnan(h)]
        mean_hhi       = statistics.mean(valid_hhi)  if valid_hhi  else float("nan")

        all_lr  = [v for d in lr_by_seed.values()  for v in d.values()]
        all_cr  = [v for d in cr_by_seed.values()  for v in d.values()]
        all_roc = [v for d in roc_by_seed.values() for v in d.values()]

        mean_lr  = statistics.mean(all_lr)  if all_lr  else float("nan")
        mean_cr  = statistics.mean(all_cr)  if all_cr  else float("nan")
        mean_roc = statistics.mean(all_roc) if all_roc else float("nan")

        std_lr = statistics.stdev(all_lr) if len(all_lr) > 1 else 0.0
        lr_cv  = std_lr / mean_lr if mean_lr > 0 else float("nan")

        ps = f"{mean_period:.1f}" if mean_period is not None else "N/A"
        print(f"period={ps}, LR={mean_lr:.3f}, CV={lr_cv:.3f}, "
              f"amp={mean_amplitude:.3f}, HHI={mean_hhi:.3f}, t={runtime:.0f}s")

        results.append(HighVolumeResult(
            label=label,
            risks_per_year=rpy,
            n_syndicates=n_syn,
            risks_per_syn_yr=rps,
            mean_lr=mean_lr,
            mean_amplitude=mean_amplitude,
            mean_period=mean_period,
            mean_hhi=mean_hhi,
            mean_cr=mean_cr,
            mean_roc=mean_roc,
            lr_cv=lr_cv,
            runtime_seconds=runtime,
            lr_by_seed=lr_by_seed,
            cr_by_seed=cr_by_seed,
            roc_by_seed=roc_by_seed,
        ))
    return results


def plot_23_high_volume(results: list[HighVolumeResult], filename: str) -> str:
    """Six-panel: LR ensemble / LR CV / cycle bars / scorecard / RoC / summary table."""
    if not results:
        return ""

    n = len(results)
    cmap   = plt.cm.tab10  # type: ignore[attr-defined]
    colors = [cmap(i / 10) for i in range(n)]

    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    ax_lr, ax_cv, ax_cycle = axes[0, 0], axes[0, 1], axes[0, 2]
    ax_score, ax_roc, ax_table = axes[1, 0], axes[1, 1], axes[1, 2]

    horizon = max(
        (max(lr_d.keys()) + 1
         for r in results
         for lr_d in r.lr_by_seed.values() if lr_d),
        default=25,
    )
    all_years = list(range(horizon))

    # ── Panel 1: Ensemble mean LR ±1σ ────────────────────────────────────────
    for res, color in zip(results, colors):
        means, stds = [], []
        for y in all_years:
            vals = [res.lr_by_seed[s][y] for s in res.lr_by_seed
                    if y in res.lr_by_seed[s]]
            means.append(statistics.mean(vals) if vals else float("nan"))
            stds.append(statistics.stdev(vals) if len(vals) > 1 else 0.0)
        yrs_a   = np.array(all_years)
        means_a = np.array(means)
        stds_a  = np.array(stds)
        valid   = ~np.isnan(means_a)
        ax_lr.plot(yrs_a[valid], means_a[valid], color=color,
                   linewidth=2, label=res.label)
        ax_lr.fill_between(yrs_a[valid],
                           means_a[valid] - stds_a[valid],
                           means_a[valid] + stds_a[valid],
                           color=color, alpha=0.15)
    ax_lr.axhline(1.0, color="red",    linestyle="--", linewidth=0.8, label="LR=1.0")
    ax_lr.axhline(0.6, color="orange", linestyle=":",  linewidth=0.8, label="LR=0.6")
    ax_lr.set_title("Ensemble Mean Loss Ratio (±1σ)")
    ax_lr.set_xlabel("Year"); ax_lr.set_ylabel("Loss Ratio")
    ax_lr.legend(fontsize=8)

    # ── Panel 2: Per-year LR CV (σ/μ across seeds) — LLN test ───────────────
    for res, color in zip(results, colors):
        cvs = []
        for y in all_years:
            vals = [res.lr_by_seed[s][y] for s in res.lr_by_seed
                    if y in res.lr_by_seed[s]]
            if len(vals) > 1:
                mu = statistics.mean(vals)
                sd = statistics.stdev(vals)
                cvs.append(sd / mu if mu > 0 else float("nan"))
            else:
                cvs.append(float("nan"))
        valid_mask = [not math.isnan(c) for c in cvs]
        yrs_v  = [y for y, v in zip(all_years, valid_mask) if v]
        cvs_v  = [c for c, v in zip(cvs, valid_mask) if v]
        ax_cv.plot(yrs_v, cvs_v, color=color, linewidth=1.5, label=res.label)
    ax_cv.set_title("Per-Year LR Coefficient of Variation (σ/μ across seeds)\nLLN test: lower CV = smoother pricing")
    ax_cv.set_xlabel("Year"); ax_cv.set_ylabel("CV (σ/μ)")
    ax_cv.legend(fontsize=8)

    # ── Panel 3: Cycle period and amplitude bar chart ─────────────────────────
    x      = np.arange(n)
    width  = 0.35
    labels = [r.label for r in results]

    periods_v  = [r.mean_period    if r.mean_period is not None else 0.0 for r in results]
    amps_v     = [r.mean_amplitude for r in results]

    ax_cycle_r = ax_cycle.twinx()
    ax_cycle.bar(x - width / 2, periods_v, width, label="Period (yr)",
                 color="steelblue", alpha=0.8)
    ax_cycle_r.bar(x + width / 2, amps_v, width, label="Amplitude",
                   color="darkorange", alpha=0.8)
    ax_cycle.set_xticks(x)
    ax_cycle.set_xticklabels(labels, rotation=10, ha="right", fontsize=9)
    ax_cycle.set_ylabel("Cycle Period (yr)", color="steelblue")
    ax_cycle_r.set_ylabel("Amplitude", color="darkorange")
    ax_cycle.set_title("Cycle Period & Amplitude Comparison")
    ax_cycle.tick_params(axis="y", labelcolor="steelblue")
    ax_cycle_r.tick_params(axis="y", labelcolor="darkorange")
    lines1, labels1 = ax_cycle.get_legend_handles_labels()
    lines2, labels2 = ax_cycle_r.get_legend_handles_labels()
    ax_cycle.legend(lines1 + lines2, labels1 + labels2, fontsize=8, loc="upper left")

    # ── Panel 4: Calibration scorecard vs Lloyd's bands ───────────────────────
    score_metrics = ["hhi", "combined_ratio", "roc"]
    score_labels  = {"hhi": "HHI", "combined_ratio": "Combined Ratio", "roc": "Return on Capital"}

    def get_score_val(res: HighVolumeResult, m: str) -> float:
        if m == "hhi":            return res.mean_hhi
        if m == "combined_ratio": return res.mean_cr
        if m == "roc":            return res.mean_roc
        return float("nan")

    y_pos = list(range(len(score_metrics)))
    ax_score.set_yticks(y_pos)
    ax_score.set_yticklabels([score_labels[m] for m in score_metrics], fontsize=9)
    ax_score.set_title("Scorecard vs Lloyd's Benchmarks")

    for yi, m in enumerate(score_metrics):
        lo, hi = LLOYDS_BENCHMARKS[m]
        ax_score.barh(yi, hi - lo, left=lo, height=0.4,
                      color="green", alpha=0.20, zorder=1)
        ax_score.plot([lo, hi], [yi, yi], color="green", linewidth=1.5, zorder=2)
        for ri, (res, col) in enumerate(zip(results, colors)):
            val = get_score_val(res, m)
            if not math.isnan(val):
                ax_score.scatter(val, yi + 0.15 * ri - 0.075 * (n - 1),
                                 color=col, s=70, zorder=5,
                                 label=res.label if yi == 0 else "")
    ax_score.set_xlabel("Metric value")
    ax_score.legend(fontsize=8, loc="lower right")

    # ── Panel 5: Return on Capital time series ────────────────────────────────
    roc_lo, roc_hi = LLOYDS_BENCHMARKS["roc"]
    ax_roc.axhspan(roc_lo, roc_hi, color="green", alpha=0.10,
                   label=f"Lloyd's RoC band ({roc_lo:.0%}–{roc_hi:.0%})")
    ax_roc.axhline(0, color="red", linestyle="--", linewidth=0.8)
    for res, color in zip(results, colors):
        means = []
        for y in all_years:
            vals = [res.roc_by_seed[s][y] for s in res.roc_by_seed
                    if y in res.roc_by_seed[s]]
            means.append(statistics.mean(vals) if vals else float("nan"))
        yrs_a   = np.array(all_years)
        means_a = np.array(means)
        valid   = ~np.isnan(means_a)
        ax_roc.plot(yrs_a[valid], means_a[valid], color=color,
                    linewidth=1.5, label=res.label)
    ax_roc.set_title("Return on Capital (ensemble mean)")
    ax_roc.set_xlabel("Year"); ax_roc.set_ylabel("RoC")
    ax_roc.legend(fontsize=8)

    # ── Panel 6: Summary table ────────────────────────────────────────────────
    ax_table.axis("off")
    col_headers = ["Label", "rpy", "rps/syn", "Period", "Amp", "LR", "CV", "HHI", "t(s)"]
    rows = []
    for res in results:
        ps = f"{res.mean_period:.1f}" if res.mean_period is not None else "N/A"
        rows.append([
            res.label,
            str(res.risks_per_year),
            f"{res.risks_per_syn_yr:.0f}",
            ps,
            f"{res.mean_amplitude:.3f}",
            f"{res.mean_lr:.3f}",
            f"{res.lr_cv:.3f}",
            f"{res.mean_hhi:.3f}",
            f"{res.runtime_seconds:.0f}",
        ])
    tbl = ax_table.table(
        cellText=rows,
        colLabels=col_headers,
        loc="center",
        cellLoc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1.0, 1.5)
    ax_table.set_title("Summary Statistics", pad=12)

    fig.suptitle(
        "Experiment 3b: High-Volume Lloyd's-Realistic Density\n"
        "20 syn / 10 brok / 2000 rpy = 100 risks/syn/yr (~8% of Lloyd's) | "
        "5 seeds × 25yr | no cats | capital=50 000 (peril-exposure headroom)",
        fontsize=11,
    )
    plt.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ── Section: Experiment 3c — Catastrophe Calibration Regime ──────────────────

class CatCalibResult(NamedTuple):
    label:               str
    n_syndicates:        int
    n_brokers:           int
    risks_per_year:      int
    initial_capital:     float
    cat_freq:            float
    # Primary calibration metrics
    mean_cat_lr_delta:   float        # mean(LR_cat[y] − LR_nocat[y]) in cat-active years
    std_cat_lr_delta:    float
    wipeout_fraction:    float        # fraction of cat events where damage ≥ total_exposure
    mean_lr_cat:         float
    mean_lr_nocat:       float
    # Standard market metrics (cat arm)
    mean_period:         float | None
    mean_amplitude:      float
    mean_hhi:            float
    mean_cr:             float
    mean_roc:            float
    runtime_seconds:     float
    # Seed-level detail for plotting
    lr_by_seed_cat:      dict        # {seed: {year: lr}}
    lr_by_seed_nocat:    dict        # {seed: {year: lr}}
    cat_delta_by_seed:   dict        # {seed: mean_delta}
    cr_by_seed:          dict        # {seed: {year: cr}}
    roc_by_seed:         dict        # {seed: {year: roc}}
    cat_events_per_seed: dict        # {seed: [{damage, total_exposure, wipeout, year, region}]}


def _extract_cat_event_stats(
    market: Market,
    horizon: int,
) -> list[dict]:
    """Extract per-event calibration stats for each CATASTROPHE_OCCURRED event.

    Returns list of dicts with keys: damage, total_exposure, wipeout, year, region.
    total_exposure is reconstructed from risk_registry (sum of limits for active
    policies in the affected region at the cat event's sim_time), matching the
    logic in Market._handle_catastrophe.
    """
    events = []
    for ev in market.event_store:
        if ev.kind != EventKind.CATASTROPHE_OCCURRED:
            continue
        t = ev.sim_time
        year = int(t / 365)
        if year >= horizon:
            continue
        region = ev.payload["region"]
        damage = ev.payload["damage"]

        # Reconstruct active regional exposure at time t (mirrors _handle_catastrophe)
        total_exposure = sum(
            pol["limit"]
            for pol in market.risk_registry.values()
            if pol["region"] == region
            and pol["bound_at"] <= t < pol["bound_at"] + 365
        )
        # wipeout: entire regional exposure absorbed, every policy hit at full limit
        wipeout = total_exposure > 0 and damage >= total_exposure
        events.append({
            "damage": damage,
            "total_exposure": total_exposure,
            "wipeout": wipeout,
            "year": year,
            "region": region,
        })
    return events


def run_cat_calib_sweep(
    configs: list[tuple],   # (label, n_syn, n_brok, rpy, capital, cat_freq)
    n_seeds: int = 5,
    horizon: int = 50,
) -> list[CatCalibResult]:
    """Run 2-arm (cat / no-cat) sweep for each config; compute calibration metrics.

    Identical seed → identical attritional path → clean counterfactual.
    Cat LR delta computed only over years where ≥1 cat event occurred.
    """
    print(f"\n  Cat calibration sweep: {len(configs)} configs × {n_seeds} seeds "
          f"× {horizon}yr (2-arm cat/no-cat)")
    results = []

    for label, n_syn, n_brok, rpy, capital, cat_freq in configs:
        print(f"    [{label:<28}] n_syn={n_syn}, rpy={rpy}, "
              f"cap={capital:.0f}, cat_freq={cat_freq:.2f} ...",
              end=" ", flush=True)
        t0 = time.time()

        periods, amplitudes, hhi_vals = [], [], []
        lr_by_seed_cat:    dict[int, dict[int, float]] = {}
        lr_by_seed_nocat:  dict[int, dict[int, float]] = {}
        cat_delta_by_seed: dict[int, float]            = {}
        cr_by_seed:        dict[int, dict[int, float]] = {}
        roc_by_seed:       dict[int, dict[int, float]] = {}
        cat_events_per_seed: dict[int, list[dict]]     = {}
        all_cat_events: list[dict] = []

        for seed in range(n_seeds):
            # ── Cat arm ──────────────────────────────────────────────────────
            m_cat = build_simulation(
                n_syndicates=n_syn,
                n_brokers=n_brok,
                risks_per_year=rpy,
                initial_capital=capital,
                horizon_years=horizon,
                enable_cats=True,
                cat_freq=cat_freq,
                seed=seed,
            )
            m_cat.run()

            lr_cat = extract_yearly_loss_ratios(m_cat, horizon)
            lr_by_seed_cat[seed] = lr_cat

            cr = compute_combined_ratio_series(m_cat, horizon)
            cr_by_seed[seed] = cr
            roc = compute_roc_series(m_cat, horizon)
            roc_by_seed[seed] = roc

            cs = detect_cycle(lr_cat)
            if cs.period_years is not None:
                periods.append(cs.period_years)
            amplitudes.append(cs.amplitude)
            hhi_vals.append(_compute_time_avg_hhi(m_cat, horizon))

            evts = _extract_cat_event_stats(m_cat, horizon)
            cat_events_per_seed[seed] = evts
            all_cat_events.extend(evts)

            # ── No-cat arm (same seed → same attritional path) ────────────
            m_nocat = build_simulation(
                n_syndicates=n_syn,
                n_brokers=n_brok,
                risks_per_year=rpy,
                initial_capital=capital,
                horizon_years=horizon,
                enable_cats=False,
                seed=seed,
            )
            m_nocat.run()

            lr_nocat = extract_yearly_loss_ratios(m_nocat, horizon)
            lr_by_seed_nocat[seed] = lr_nocat

            # LR delta in cat-active years only
            cat_years = {ev["year"] for ev in evts}
            deltas = [
                lr_cat.get(y, 0.0) - lr_nocat.get(y, 0.0)
                for y in cat_years
                if y in lr_cat and y in lr_nocat
            ]
            cat_delta_by_seed[seed] = statistics.mean(deltas) if deltas else 0.0

        runtime = time.time() - t0

        mean_period    = statistics.mean(periods)    if periods    else None
        mean_amplitude = statistics.mean(amplitudes) if amplitudes else 0.0
        valid_hhi      = [h for h in hhi_vals if not math.isnan(h)]
        mean_hhi       = statistics.mean(valid_hhi)  if valid_hhi  else float("nan")

        all_lr_cat   = [v for d in lr_by_seed_cat.values()   for v in d.values()]
        all_lr_nocat = [v for d in lr_by_seed_nocat.values() for v in d.values()]
        all_cr       = [v for d in cr_by_seed.values()        for v in d.values()]
        all_roc      = [v for d in roc_by_seed.values()       for v in d.values()]

        mean_lr_cat   = statistics.mean(all_lr_cat)   if all_lr_cat   else float("nan")
        mean_lr_nocat = statistics.mean(all_lr_nocat) if all_lr_nocat else float("nan")
        mean_cr       = statistics.mean(all_cr)        if all_cr       else float("nan")
        mean_roc      = statistics.mean(all_roc)       if all_roc      else float("nan")

        delta_vals        = list(cat_delta_by_seed.values())
        mean_cat_lr_delta = statistics.mean(delta_vals)  if delta_vals          else 0.0
        std_cat_lr_delta  = statistics.stdev(delta_vals) if len(delta_vals) > 1 else 0.0

        n_wipeout        = sum(1 for ev in all_cat_events if ev["wipeout"])
        wipeout_fraction = (n_wipeout / len(all_cat_events)
                            if all_cat_events else float("nan"))

        ps = f"{mean_period:.1f}" if mean_period is not None else "N/A"
        wf = f"{wipeout_fraction*100:.0f}%" if not math.isnan(wipeout_fraction) else "N/A"
        print(f"period={ps}, cat_delta={mean_cat_lr_delta*100:.1f}pp, "
              f"wipeout={wf}, HHI={mean_hhi:.3f}, t={runtime:.0f}s")

        results.append(CatCalibResult(
            label=label,
            n_syndicates=n_syn,
            n_brokers=n_brok,
            risks_per_year=rpy,
            initial_capital=capital,
            cat_freq=cat_freq,
            mean_cat_lr_delta=mean_cat_lr_delta,
            std_cat_lr_delta=std_cat_lr_delta,
            wipeout_fraction=wipeout_fraction,
            mean_lr_cat=mean_lr_cat,
            mean_lr_nocat=mean_lr_nocat,
            mean_period=mean_period,
            mean_amplitude=mean_amplitude,
            mean_hhi=mean_hhi,
            mean_cr=mean_cr,
            mean_roc=mean_roc,
            runtime_seconds=runtime,
            lr_by_seed_cat=lr_by_seed_cat,
            lr_by_seed_nocat=lr_by_seed_nocat,
            cat_delta_by_seed=cat_delta_by_seed,
            cr_by_seed=cr_by_seed,
            roc_by_seed=roc_by_seed,
            cat_events_per_seed=cat_events_per_seed,
        ))

    return results


def plot_24_cat_calibration(results: list[CatCalibResult], filename: str) -> str:
    """Six-panel: delta bars / regime / LR time series / scorecard / phase / table."""
    if not results:
        return ""

    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    ax_delta, ax_regime, ax_ts     = axes[0, 0], axes[0, 1], axes[0, 2]
    ax_score, ax_phase, ax_table   = axes[1, 0], axes[1, 1], axes[1, 2]

    n      = len(results)
    labels = [r.label for r in results]
    x      = np.arange(n)

    # ── Panel 1: Cat LR delta bars ±1σ (colour by wipeout_fraction) ──────────
    def _wipeout_colour(wf: float) -> str:
        if math.isnan(wf) or wf > 0.80:
            return "firebrick"
        if wf > 0.30:
            return "darkorange"
        return "forestgreen"

    bar_colours = [_wipeout_colour(r.wipeout_fraction) for r in results]
    deltas_pp   = [r.mean_cat_lr_delta * 100 for r in results]
    std_pp      = [r.std_cat_lr_delta  * 100 for r in results]

    ax_delta.barh(x, deltas_pp, xerr=std_pp, color=bar_colours, alpha=0.8,
                  height=0.6, capsize=4)
    ax_delta.axvspan(30, 50, color="green", alpha=0.15,
                     label="Lloyd's target [30–50 pp]")
    ax_delta.set_yticks(x)
    ax_delta.set_yticklabels(labels, fontsize=9)
    ax_delta.set_xlabel("Cat LR delta (percentage points)")
    ax_delta.set_title("Cat LR Delta (bars: red=wipeout, amber=mixed, green=proportional)")
    ax_delta.legend(fontsize=8)

    # ── Panel 2: Regime indicator (stacked horizontal bars) ──────────────────
    wipeout_pcts = [
        r.wipeout_fraction * 100 if not math.isnan(r.wipeout_fraction) else 0.0
        for r in results
    ]
    partial_pcts = [100.0 - w for w in wipeout_pcts]

    ax_regime.barh(x, wipeout_pcts,
                   height=0.5, color="firebrick",  alpha=0.8, label="Wipeout events")
    ax_regime.barh(x, partial_pcts, left=wipeout_pcts,
                   height=0.5, color="steelblue",  alpha=0.8, label="Partial-loss events")
    ax_regime.axvline(20, color="black", linestyle="--", linewidth=1,  label="20% threshold")
    ax_regime.axvline(80, color="black", linestyle=":",  linewidth=1,  label="80% threshold")
    ax_regime.set_yticks(x)
    ax_regime.set_yticklabels(labels, fontsize=9)
    ax_regime.set_xlabel("% of cat events")
    ax_regime.set_title("Cat Event Regime: Wipeout vs Partial Loss")
    ax_regime.legend(fontsize=8)

    # ── Panel 3: LR time series — calibrated config (cat vs no-cat) ──────────
    calib_res = next(
        (r for r in results if "calibrated-rpy" in r.label.lower()),
        results[-1],
    )
    horizon = max(
        (max(d.keys()) + 1 for d in calib_res.lr_by_seed_cat.values() if d),
        default=50,
    )
    all_years = list(range(horizon))

    def _band(
        lr_dict_by_seed: dict,
        years: list,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        means, stds = [], []
        for y in years:
            vals = [lr_dict_by_seed[s][y]
                    for s in lr_dict_by_seed if y in lr_dict_by_seed[s]]
            means.append(statistics.mean(vals) if vals else float("nan"))
            stds.append(statistics.stdev(vals) if len(vals) > 1 else 0.0)
        return np.array(years), np.array(means), np.array(stds)

    yrs_a, cat_means,   cat_stds   = _band(calib_res.lr_by_seed_cat,   all_years)
    _,     nocat_means, nocat_stds = _band(calib_res.lr_by_seed_nocat, all_years)

    valid_c  = ~np.isnan(cat_means)
    valid_nc = ~np.isnan(nocat_means)

    ax_ts.plot(yrs_a[valid_c],  cat_means[valid_c],
               color="steelblue", linewidth=2, label="Cat arm (mean)")
    ax_ts.fill_between(yrs_a[valid_c],
                       cat_means[valid_c]   - cat_stds[valid_c],
                       cat_means[valid_c]   + cat_stds[valid_c],
                       color="steelblue", alpha=0.15)
    ax_ts.plot(yrs_a[valid_nc], nocat_means[valid_nc],
               color="darkorange", linestyle="--", linewidth=2, label="No-cat arm (mean)")
    ax_ts.fill_between(yrs_a[valid_nc],
                       nocat_means[valid_nc] - nocat_stds[valid_nc],
                       nocat_means[valid_nc] + nocat_stds[valid_nc],
                       color="darkorange", alpha=0.10)

    # Shade years where any seed had a cat event
    cat_year_set: set[int] = set()
    for evts in calib_res.cat_events_per_seed.values():
        for ev in evts:
            cat_year_set.add(ev["year"])
    for y in sorted(cat_year_set):
        if y < horizon:
            ax_ts.axvspan(y - 0.5, y + 0.5, color="red", alpha=0.07)

    ax_ts.axhline(1.0, color="red",   linestyle="--", linewidth=0.8)
    ax_ts.set_title(f"LR: cat vs no-cat ({calib_res.label})\n(red shading = cat-active year)")
    ax_ts.set_xlabel("Year")
    ax_ts.set_ylabel("Loss Ratio")
    ax_ts.legend(fontsize=8)

    # ── Panel 4: Calibration scorecard vs Lloyd's benchmarks ─────────────────
    score_metrics = ["period", "hhi", "combined_ratio", "roc", "cat_lr_delta"]
    score_labels  = {
        "period":         "Cycle Period (yr)",
        "hhi":            "HHI",
        "combined_ratio": "Combined Ratio",
        "roc":            "Return on Capital",
        "cat_lr_delta":   "Cat LR Delta",
    }

    def _get_metric(r: CatCalibResult, m: str) -> float:
        if m == "period":         return r.mean_period if r.mean_period is not None else float("nan")
        if m == "hhi":            return r.mean_hhi
        if m == "combined_ratio": return r.mean_cr
        if m == "roc":            return r.mean_roc
        if m == "cat_lr_delta":   return r.mean_cat_lr_delta
        return float("nan")

    y_pos = list(range(len(score_metrics)))
    ax_score.set_yticks(y_pos)
    ax_score.set_yticklabels([score_labels[m] for m in score_metrics], fontsize=9)
    ax_score.set_title("Scorecard vs Lloyd's Benchmarks\n(calibrated config, ◆ = in-band)")

    for yi, m in enumerate(score_metrics):
        lo, hi = LLOYDS_BENCHMARKS[m]
        ax_score.barh(yi, hi - lo, left=lo, height=0.4, color="green", alpha=0.20, zorder=1)
        ax_score.plot([lo, hi], [yi, yi], color="green", linewidth=1.5, zorder=2)
        val = _get_metric(calib_res, m)
        if not math.isnan(val):
            in_band = lo <= val <= hi
            ax_score.scatter(val, yi,
                             color="steelblue" if in_band else "firebrick",
                             s=120, zorder=5, marker="D")

    ax_score.set_xlabel("Metric value")

    # ── Panel 5: Phase diagram (rpy × capital, coloured by wipeout_fraction) ─
    rpy_vals = [r.risks_per_year  for r in results]
    cap_vals = [r.initial_capital for r in results]
    wf_vals  = [
        r.wipeout_fraction if not math.isnan(r.wipeout_fraction) else 1.0
        for r in results
    ]

    sc = ax_phase.scatter(
        rpy_vals, cap_vals,
        c=wf_vals, cmap="RdYlGn_r", vmin=0, vmax=1,
        s=200, zorder=5, edgecolors="k", linewidths=0.8,
    )
    plt.colorbar(sc, ax=ax_phase, label="Wipeout fraction", fraction=0.046, pad=0.04)

    for r in results:
        ax_phase.annotate(
            r.label,
            (r.risks_per_year, r.initial_capital),
            textcoords="offset points", xytext=(6, 4), fontsize=7,
        )

    ax_phase.axvline(40,  color="grey",  linestyle="--", linewidth=1,   alpha=0.7,
                     label="rpy=40 (LLN onset)")
    ax_phase.axvline(400, color="black", linestyle="--", linewidth=1.2,
                     label="rpy=400 (proportional regime)")

    # Peril exposure constraint line for n_syn=20, n_regions=5, avg_limit=1250
    rpy_range      = np.linspace(10, max(rpy_vals) * 1.2, 300)
    cap_constraint = (rpy_range / 20 / 5) * 1250 / 0.7
    ax_phase.plot(rpy_range, cap_constraint,
                  color="steelblue", linestyle="-.", linewidth=1.5,
                  label="Peril exposure limit (n_syn=20)")

    ax_phase.set_xscale("log")
    ax_phase.set_yscale("log")
    ax_phase.set_xlabel("Risks per year (log scale)")
    ax_phase.set_ylabel("Initial capital (log scale)")
    ax_phase.set_title("Phase Diagram: rpy × Capital\n(colour = wipeout fraction)")
    ax_phase.legend(fontsize=7, loc="upper left")

    # ── Panel 6: Summary table ────────────────────────────────────────────────
    ax_table.axis("off")
    col_headers = ["Label", "rpy", "Capital", "cat_freq",
                   "Cat Δ (pp)", "Wipeout%", "Period", "HHI", "t(s)"]
    rows = []
    for r in results:
        ps    = f"{r.mean_period:.1f}" if r.mean_period is not None else "N/A"
        wf_s  = (f"{r.wipeout_fraction*100:.0f}%"
                 if not math.isnan(r.wipeout_fraction) else "N/A")
        rows.append([
            r.label,
            str(r.risks_per_year),
            f"{r.initial_capital:.0f}",
            f"{r.cat_freq:.2f}",
            f"{r.mean_cat_lr_delta*100:.1f}",
            wf_s,
            ps,
            f"{r.mean_hhi:.3f}",
            f"{r.runtime_seconds:.0f}",
        ])

    tbl = ax_table.table(
        cellText=rows,
        colLabels=col_headers,
        loc="center",
        cellLoc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    tbl.scale(1.0, 1.5)
    ax_table.set_title("Summary Statistics", pad=12)

    # Colour cat-delta cells: green if in Lloyd's [30, 50] pp band
    for row_idx, r in enumerate(results, start=1):
        delta_pp   = r.mean_cat_lr_delta * 100
        cell_color = "lightgreen" if 30 <= delta_pp <= 50 else "lightyellow"
        tbl[(row_idx, 4)].set_facecolor(cell_color)

    fig.suptitle(
        "Experiment 3c: Catastrophe Calibration — Wipeout vs Proportional Regime\n"
        "Threshold: rpy≥400 ensures proportional cat losses | "
        "Target: cat LR delta 30–50 pp, wipeout < 30%",
        fontsize=11,
    )
    plt.tight_layout()
    path = f"{OUTPUT_DIR}/{filename}"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ── Section 10: main() ────────────────────────────────────────────────────────

def main() -> None:
    """Run all diagnostic phases."""
    HORIZON = 60
    BASE_KWARGS: dict = {"enable_cats": False}

    # ── Phase 1: Single detailed run ──────────────────────────────────────────
    print("=" * 60)
    print("Phase 1: Single detailed run (seed=42, 60yr, no cats)")
    print("=" * 60)

    dm = build_diagnostic_simulation(horizon_years=HORIZON, seed=42,
                                     enable_cats=False)
    dm.run()

    lr_single = extract_yearly_loss_ratios(dm, HORIZON)
    decomp = decompose_price_forces(dm, HORIZON)
    market_cap = extract_market_capital_series(dm, HORIZON)
    solvent = extract_solvent_count(dm, HORIZON)
    insolvencies = dict(dm.yearly_insolvencies)
    stats_single = detect_cycle(lr_single)

    print(f"\nPer-year table (seed=42):")
    print(f"{'Year':>5} {'LR':>8} {'MktCap':>12} {'Solvent':>8}")
    for y in sorted(lr_single.keys()):
        print(f"{y:>5} {lr_single[y]:>8.3f} "
              f"{market_cap.get(y, 0):>12,.0f} "
              f"{solvent.get(y, 0):>8}")

    print(f"\nCycleStats (seed=42):")
    print(f"  Period:       {stats_single.period_years}")
    print(f"  Amplitude:    {stats_single.amplitude:.4f}")
    print(f"  CV:           {stats_single.cv:.4f}")
    print(f"  ACF@period:   {stats_single.acf_at_period:.4f}")
    print(f"  N peaks:      {stats_single.n_peaks}")
    print(f"  Mean LR:      {stats_single.mean_lr:.4f}")
    print(f"  Std LR:       {stats_single.std_lr:.4f}")

    cap_by_sid = {sid: dict(dm.syndicate_yearly_capital[sid])
                  for sid in dm.syndicates}
    syndicate_series = extract_syndicate_series(dm, HORIZON)

    p = plot_01_loss_ratio_cycle({42: lr_single}, stats_single,
                                 "diag_01_lr_single.png")
    print(f"  Saved: {p}")
    p = plot_02_force_decomposition(decomp, lr_single, "diag_02_force_decomp.png")
    print(f"  Saved: {p}")
    p = plot_03_capital_insolvencies(cap_by_sid, market_cap, insolvencies,
                                     solvent, "diag_03_capital.png")
    print(f"  Saved: {p}")
    p = plot_05_phase_portrait(decomp, "diag_05_phase_portrait.png")
    print(f"  Saved: {p}")
    p = plot_06_peak_detection(lr_single, stats_single, "diag_06_peaks.png")
    print(f"  Saved: {p}")

    # ── Phase 2: Multi-seed ensemble ─────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Phase 2: Multi-seed ensemble (seeds 0-9, 60yr, no cats)")
    print("=" * 60)

    lr_by_seed_ens: dict[int, dict[int, float]] = {}
    for seed in range(10):
        m = build_simulation(horizon_years=HORIZON, seed=seed, enable_cats=False)
        m.run()
        lr_by_seed_ens[seed] = extract_yearly_loss_ratios(m, HORIZON)

    # Compute ensemble mean LR
    all_ens_years = sorted(set(y for lr_d in lr_by_seed_ens.values()
                               for y in lr_d))
    ens_mean_lr: dict[int, float] = {}
    for y in all_ens_years:
        vals = [lr_by_seed_ens[s][y] for s in lr_by_seed_ens
                if y in lr_by_seed_ens[s]]
        if vals:
            ens_mean_lr[y] = statistics.mean(vals)

    stats_ens = detect_cycle(ens_mean_lr)
    print(f"\nEnsemble CycleStats:")
    print(f"  Period:     {stats_ens.period_years}")
    print(f"  Amplitude:  {stats_ens.amplitude:.4f}")
    print(f"  Mean LR:    {stats_ens.mean_lr:.4f}")
    print(f"  Std LR:     {stats_ens.std_lr:.4f}")

    p = plot_01_loss_ratio_cycle(lr_by_seed_ens, stats_ens,
                                 "diag_01_lr_ensemble.png")
    print(f"  Saved: {p}")

    # ── Phase 3: Parameter sweeps ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Phase 3: Parameter sweeps (10 seeds, 60yr each)")
    print("=" * 60)

    sweeps = [
        ("beta", [0.15, 0.25, 0.35, 0.45, 0.55],
         "Higher beta → faster markup response → shorter cycle period, larger amplitude"),
        ("w",    [0.10, 0.15, 0.20, 0.25, 0.30],
         "Higher w → faster actuarial learning → shorter cycle (actuary catches up sooner)"),
        ("z",    [0.15, 0.25, 0.35, 0.45],
         "Higher z → syndicates rely more on own experience, reducing coupling via "
         "industry_avg_claim → dampened synchronisation, potentially flatter cycles"),
        ("initial_capital", [800, 1200, 2000, 3000, 5000],
         "Lower capital → tighter capacity constraints → more insolvencies → sharper "
         "hard markets; higher capital → gentle cycles, near-zero insolvencies"),
        ("cat_freq", [0.00, 0.03, 0.05, 0.08, 0.12],
         "Higher cat_freq → correlated capital depletion → amplified cycles and more "
         "insolvencies; at high frequency, exogenous cat shocks dominate over "
         "endogenous cycle"),
    ]

    all_sweep_results: list[SweepResult] = []
    for i, (param_name, param_values, hypothesis) in enumerate(sweeps, start=1):
        print(f"\n  Sweep {i}/5: {param_name}")
        sweep_base = dict(BASE_KWARGS)
        if param_name == "cat_freq":
            sweep_base.pop("enable_cats", None)
        results = run_parameter_sweep(
            param_name, param_values, hypothesis,
            base_kwargs=sweep_base, n_seeds=10, horizon=HORIZON,
        )
        all_sweep_results.extend(results)
        p = plot_04_sensitivity(results, param_name,
                                f"diag_04_sweep_{param_name}.png")
        print(f"    Saved: {p}")

    # ── Phase 4: Summary table ────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Phase 4: Summary table")
    print("=" * 60)
    print(f"\n{'Param':<20} {'Value':>8} {'Period±Std':>16} "
          f"{'Amplitude':>12} {'MeanLR':>8}")
    print("-" * 68)
    for r in all_sweep_results:
        period_str = (f"{r.mean_period:.1f}" if r.mean_period is not None
                      else "N/A")
        std_str = f"±{r.std_period:.1f}"
        print(f"{r.param_name:<20} {r.param_value:>8.3f} "
              f"{period_str + std_str:>16} "
              f"{r.mean_amplitude:>12.4f} "
              f"{statistics.mean([statistics.mean(list(lr_d.values())) for lr_d in r.lr_by_seed.values() if lr_d]):>8.4f}")

    # ── Phase 5: Additional Phenomena ────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Phase 5: Additional Phenomena (plots 07-10)")
    print("=" * 60)

    from simulation import (
        experiment_lead_follow,
        experiment_network_evolution,
        experiment_path_dependence,
    )

    # Phenomenon 3: Winner's curse — premium concentration from Phase 1 run
    print("\n  Plot 09: Market concentration (HHI)...")
    p = plot_09_market_concentration(syndicate_series)
    print(f"  Saved: {p}")

    # Phenomenon 4: Lead-follow stabilisation
    print("\n  Running experiment_lead_follow (seeds 0-14, 60yr)...")
    lf_results = experiment_lead_follow()
    p = plot_07_lead_follow(lf_results["with_lf"], lf_results["without_lf"])
    print(f"  Saved: {p}")

    # Phenomenon 5: Network evolution / herding
    print("\n  Running experiment_network_evolution (seed=42, 50yr)...")
    net_snapshots, syn_premiums = experiment_network_evolution()
    p = plot_10_network_evolution(net_snapshots, syn_premiums)
    print(f"  Saved: {p}")

    # Phenomenon 6: Path dependence
    print("\n  Running experiment_path_dependence (20 seeds, 60yr, cats)...")
    cap_paths = experiment_path_dependence()
    p = plot_08_path_dependence(cap_paths)
    print(f"  Saved: {p}")

    # ── Phase 6: Tier 1 — New univariate sweeps ───────────────────────────────
    print("\n" + "=" * 60)
    print("Phase 6: Tier 1 — New univariate sweeps")
    print("         (alpha, gamma_div, expense_rate, risks_per_year)")
    print("=" * 60)

    tier1_sweeps = [
        ("alpha", [0.0005, 0.001, 0.002, 0.005, 0.01],
         "Higher alpha → higher risk loading → higher floor price → lower mean LR; "
         "may dampen amplitude"),
        ("gamma_div", [0.1, 0.2, 0.3, 0.4, 0.5],
         "Higher dividend payout → capital eroded faster → tighter capacity → "
         "shorter period, higher amplitude"),
        ("expense_rate", [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50],
         "Higher expense → lower net margin → mirrors effect of lower initial_capital"),
        ("risks_per_year", [10, 15, 25, 35, 50],
         "More risks → greater diversification → smoother LR; fewer → "
         "more idiosyncratic variance → higher amplitude"),
    ]

    # Assemble all_sweeps_dict from Phase 3 results + Tier 1
    all_sweeps_dict: dict[str, list[SweepResult]] = {}
    for pname in ["beta", "w", "z", "initial_capital", "cat_freq"]:
        all_sweeps_dict[pname] = [r for r in all_sweep_results if r.param_name == pname]

    for i, (param_name, param_values, hypothesis) in enumerate(tier1_sweeps, start=1):
        print(f"\n  Sweep {i}/4: {param_name}")
        sweep_base = dict(BASE_KWARGS)
        results = run_parameter_sweep(
            param_name, param_values, hypothesis,
            base_kwargs=sweep_base, n_seeds=10, horizon=HORIZON,
        )
        all_sweeps_dict[param_name] = results
        p = plot_04_sensitivity(results, param_name,
                                f"diag_04_sweep_{param_name}.png")
        print(f"    Saved: {p}")

    # ── Phase 7: Tier 2 — 2D interaction sweeps ───────────────────────────────
    print("\n" + "=" * 60)
    print("Phase 7: Tier 2 — 2D interaction sweeps")
    print("=" * 60)

    print("\n  2D Sweep 1/2: beta × w")
    results_beta_w = run_2d_sweep(
        "beta", [0.25, 0.35, 0.45],
        "w",    [0.10, 0.20, 0.30],
        base_kwargs=dict(BASE_KWARGS),
        n_seeds=10, horizon=HORIZON,
    )
    p = plot_2d_heatmap(results_beta_w, "beta", [0.25, 0.35, 0.45],
                        "w", [0.10, 0.20, 0.30],
                        "diag_11_2d_beta_w.png")
    print(f"  Saved: {p}")

    print("\n  2D Sweep 2/2: initial_capital × cat_freq")
    results_cap_cf = run_2d_sweep(
        "initial_capital", [800.0, 2000.0, 5000.0],
        "cat_freq",        [0.00, 0.05, 0.10],
        base_kwargs={},  # cat_freq branch sets enable_cats automatically
        n_seeds=10, horizon=HORIZON,
    )
    p = plot_2d_heatmap(results_cap_cf, "initial_capital", [800.0, 2000.0, 5000.0],
                        "cat_freq", [0.00, 0.05, 0.10],
                        "diag_12_2d_capital_catfreq.png")
    print(f"  Saved: {p}")

    # ── Phase 8: Tier 3 — OAT Tornado chart ──────────────────────────────────
    print("\n" + "=" * 60)
    print("Phase 8: Tier 3 — OAT Tornado chart")
    print("=" * 60)

    tornado_entries = compute_tornado_table(all_sweeps_dict, stats_ens)
    p = plot_tornado(tornado_entries, "diag_13_tornado.png")
    print(f"  Saved: {p}")

    # ── Phase 9: Experiment 0a — Agent-count scaling sweep ───────────────────
    print("\n" + "=" * 60)
    print("Phase 9: Experiment 0a — Agent-count scaling sweep")
    print("         Tests whether more agents lengthen the cycle period")
    print("         towards the Lloyd's target of ~6 years")
    print("=" * 60)

    scale_configs: list[tuple[int, int]] = [
        (10, 4),    # baseline
        (20, 10),   # 2× syndicates
        (40, 30),   # 4×
        (80, 80),   # Lloyd's-scale agent count
    ]
    scale_results = run_agent_scale_sweep(
        scale_configs,
        base_kwargs={"enable_cats": False},
        n_seeds=10,
        horizon=HORIZON,
    )

    print(f"\n{'n_syn':>6} {'n_brok':>7} {'Period':>8} {'Amplitude':>10} "
          f"{'MeanLR':>8} {'HHI':>8}")
    print("-" * 55)
    for r in scale_results:
        period_str = f"{r.mean_period:.1f}" if r.mean_period is not None else "N/A"
        print(f"{r.n_syndicates:>6} {r.n_brokers:>7} {period_str:>8} "
              f"{r.mean_amplitude:>10.4f} {r.mean_lr:>8.4f} {r.mean_hhi:>8.4f}")

    p = plot_14_agent_scaling(scale_results, "diag_14_agent_scaling.png")
    print(f"  Saved: {p}")

    # ── Phase 10: Experiment 0b — Risk-volume / compute trade-off ────────────
    print("\n" + "=" * 60)
    print("Phase 10: Experiment 0b — Risk-volume / compute trade-off")
    print("         Tests proportional scaling (risks ∝ syndicates)")
    print("         and pure diversification (fixed syndicates, more risks)")
    print("=" * 60)

    rv_configs: list[tuple[str, int, int, int]] = [
        # (label,  n_syn, n_brok, risks_per_year)
        ("A-baseline",  10,  4,  25),   # 2.5 risks/syn/yr
        ("B-prop×2",    20, 10,  50),   # 2.5 risks/syn/yr (proportional)
        ("C-prop×4",    40, 30, 100),   # 2.5 risks/syn/yr (proportional)
        ("D-divers",    10,  4, 100),   # 10  risks/syn/yr (diversification only)
    ]
    rv_results = run_risk_volume_sweep(
        rv_configs,
        base_kwargs={"enable_cats": False},
        n_seeds=10,
        horizon=HORIZON,
    )

    print(f"\n{'Config':<14} {'n_syn':>5} {'rpy':>5} {'Period':>8} "
          f"{'Amplitude':>10} {'MeanLR':>8} {'HHI':>8} {'Time(s)':>8}")
    print("-" * 72)
    for r in rv_results:
        period_str = f"{r.mean_period:.1f}" if r.mean_period is not None else "N/A"
        print(f"{r.label:<14} {r.n_syndicates:>5} {r.risks_per_year:>5} "
              f"{period_str:>8} {r.mean_amplitude:>10.4f} {r.mean_lr:>8.4f} "
              f"{r.mean_hhi:>8.4f} {r.runtime_seconds:>8.0f}")

    p = plot_15_risk_volume(rv_results, "diag_15_risk_volume.png")
    print(f"  Saved: {p}")

    # ── Phase 12: Experiment 1b — LOB decomposition ──────────────────────────
    print("\n" + "=" * 60)
    print("Phase 12: Experiment 1b — LOB decomposition")
    print("         5 LOB proxy simulations (10 syn, 10 seeds, 60yr)")
    print("         Varying: risks_per_year, capital, cat_freq, claim_est")
    print("=" * 60)

    lob_results = run_lob_sweep(
        LOB_CONFIGS, n_syndicates=10, n_brokers=4, n_regions=5,
        n_seeds=10, horizon=HORIZON,
    )

    print(f"\n{'LOB':<12} {'Period':>8} {'Amplitude':>10} "
          f"{'MeanLR':>8} {'CR':>8} {'HHI':>8}")
    print("-" * 60)
    for r in lob_results:
        ps = f"{r.mean_period:.1f}" if r.mean_period is not None else "N/A"
        print(f"{r.lob:<12} {ps:>8} {r.mean_amplitude:>10.4f} "
              f"{r.mean_lr:>8.4f} {r.mean_cr:>8.4f} {r.mean_hhi:>8.4f}")

    p = plot_17_lob_decomp(lob_results, "diag_17_lob_decomp.png")
    print(f"  Saved: {p}")

    # ── Phase 11: Experiment 1a — Calibration dashboard ──────────────────────
    print("\n" + "=" * 60)
    print("Phase 11: Experiment 1a — Calibration dashboard")
    print("         Quantifies how close each config is to Lloyd's")
    print("         benchmarks: period, HHI, combined ratio, RoC")
    print("=" * 60)

    calib_configs: list[tuple[str, int, int, int, bool]] = [
        # (label,          n_syn, n_brok, rpy,  cats)
        ("baseline",        10,    4,     25,   False),
        ("baseline+cats",   10,    4,     25,   True),
        ("recommended",     20,   10,     50,   False),
        ("recmd+cats",      20,   10,     50,   True),
    ]
    calib_results = run_calibration_sweep(
        calib_configs, n_seeds=10, horizon=HORIZON,
    )

    # Compute cat LR delta: mean_LR(recommended+cats) − mean_LR(recommended)
    recmd_lr   = [v for d in next(r for r in calib_results
                  if r.label == "recommended").lr_by_seed.values()
                  for v in d.values()]
    recmd_cats_lr = [v for d in next(r for r in calib_results
                     if r.label == "recmd+cats").lr_by_seed.values()
                     for v in d.values()]
    cat_delta = (statistics.mean(recmd_cats_lr) - statistics.mean(recmd_lr)
                 if recmd_lr and recmd_cats_lr else 0.0)

    print(f"\n{'Config':<16} {'Period':>8} {'CR':>8} {'RoC':>8} {'HHI':>8}")
    print("-" * 55)
    for r in calib_results:
        ps = f"{r.mean_period:.1f}" if r.mean_period else "N/A"
        print(f"{r.label:<16} {ps:>8} {r.mean_cr:>8.3f} "
              f"{r.mean_roc:>8.3f} {r.mean_hhi:>8.4f}")
    print(f"\n  Cat LR delta (recmd+cats − recmd): {cat_delta:+.3f} "
          f"({cat_delta*100:+.1f} pp) — Lloyd's target: +30 to +50 pp")

    p = plot_16_calibration(calib_results, cat_delta, "diag_16_calibration.png")
    print(f"  Saved: {p}")

    # ── Phase 13: Experiment 2a — Per-region catastrophe rates ───────────────
    print("\n" + "=" * 60)
    print("Phase 13: Experiment 2a — Per-region catastrophe rates")
    print("         Three geographic peril distributions")
    print("         (10 syn, 4 brok, 5 regions, 25 rpy, 10 seeds × 60yr)")
    print("=" * 60)

    # Three configs: uniform / geographic-peril gradient / single dominant zone
    per_region_configs: list[tuple[str, dict[int, float]]] = [
        # (label, {region: annual_cat_freq})
        ("A-uniform",      {0: 0.05, 1: 0.05, 2: 0.05, 3: 0.05, 4: 0.05}),
        ("B-geo-peril",    {0: 0.15, 1: 0.10, 2: 0.08, 3: 0.02, 4: 0.00}),
        ("C-concentrated", {0: 0.30, 1: 0.00, 2: 0.00, 3: 0.00, 4: 0.00}),
    ]

    rc_results = run_per_region_cat_sweep(
        per_region_configs,
        n_syndicates=10,
        n_brokers=4,
        n_regions=5,
        n_seeds=10,
        horizon=HORIZON,
    )

    print(f"\n{'Config':<18} {'Period':>8} {'Amplitude':>10} "
          f"{'MeanLR':>8} {'HHI':>8}")
    print("-" * 58)
    for r in rc_results:
        ps = f"{r.mean_period:.1f}" if r.mean_period is not None else "N/A"
        print(f"{r.label:<18} {ps:>8} {r.mean_amplitude:>10.4f} "
              f"{r.mean_lr:>8.4f} {r.mean_hhi:>8.4f}")

    p = plot_18_per_region_cats(rc_results, "diag_18_per_region_cats.png")
    print(f"  Saved: {p}")

    # ── Phase 14: Experiment 2b — Per-LOB risk heterogeneity ─────────────────
    print("\n" + "=" * 60)
    print("Phase 14: Experiment 2b — Per-LOB risk heterogeneity")
    print("         Baseline (homogeneous) vs heterogeneous lob_params")
    print("         (10 syn, 4 brok, 5 regions, 25 rpy, no cats, 10 seeds × 60yr)")
    print("=" * 60)

    lob_hetero_configs: list[tuple[str, "dict[int, dict] | None"]] = [
        ("baseline",      None),
        ("heterogeneous", LOB_PARAMS_DEFAULT),
    ]
    lob_hetero_results = run_lob_hetero_sweep(
        lob_hetero_configs,
        n_syndicates=10,
        n_brokers=4,
        n_regions=5,
        n_seeds=10,
        horizon=HORIZON,
    )

    print(f"\n{'Config':<16} {'Period':>8} {'Amplitude':>10} {'MeanLR':>8} {'HHI':>8}")
    print("-" * 55)
    for r in lob_hetero_results:
        ps = f"{r.mean_period:.1f}" if r.mean_period is not None else "N/A"
        print(f"{r.label:<16} {ps:>8} {r.mean_amplitude:>10.4f} "
              f"{r.mean_lr:>8.4f} {r.mean_hhi:>8.4f}")

    p = plot_19_lob_hetero(lob_hetero_results, n_regions=5,
                           filename="diag_19_lob_hetero.png")
    print(f"  Saved: {p}")

    # ── Phase 15: Experiment 2c — Asymmetric broker market power ─────────────
    print("\n" + "=" * 60)
    print("Phase 15: Experiment 2c — Asymmetric broker market power")
    print("         Uniform vs Zipf(s=1) with 4 and 10 brokers")
    print("         (no cats, 10 seeds × 60yr)")
    print("=" * 60)

    broker_power_configs: list[tuple[str, float, int, int]] = [
        # (label,               broker_power, n_syn, n_brok)
        ("4-brok uniform",      0.0,          10,    4),
        ("4-brok Zipf s=1",     1.0,          10,    4),
        ("10-brok Zipf s=1",    1.0,          20,   10),
    ]
    bp_results = run_broker_power_sweep(
        broker_power_configs,
        n_seeds=10,
        horizon=HORIZON,
    )

    print(f"\n{'Config':<20} {'Period':>8} {'Amplitude':>10} {'MeanLR':>8} {'HHI':>8}")
    print("-" * 60)
    for r in bp_results:
        ps = f"{r.mean_period:.1f}" if r.mean_period is not None else "N/A"
        print(f"{r.label:<20} {ps:>8} {r.mean_amplitude:>10.4f} "
              f"{r.mean_lr:>8.4f} {r.mean_hhi:>8.4f}")

    # Top-broker share summary
    print("\n  Broker GWP share (mean across seeds, sorted descending):")
    for r in bp_results:
        bids = sorted(r.broker_shares.keys())
        means = sorted(
            [statistics.mean(r.broker_shares[b]) for b in bids],
            reverse=True,
        )
        cumul5 = sum(means[:5])
        share_str = "  ".join(f"{v:.1%}" for v in means[:5])
        print(f"    {r.label:<20}: {share_str}  (top-5 cumul: {cumul5:.1%})")

    p = plot_20_broker_power(bp_results, "diag_20_broker_power.png")
    print(f"  Saved: {p}")

    # ── Phase 16: Experiment 2d — Syndicate market entry ─────────────────────
    print("\n" + "=" * 60)
    print("Phase 16: Experiment 2d — Syndicate market entry")
    print("         No-entry vs entry-enabled (max=20) under cats")
    print("         Trigger: industry loss ratio > 100% in a year")
    print("         (10 syn, 4 brok, cat_freq=0.06, 10 seeds × 60yr)")
    print("=" * 60)

    market_entry_configs: list[tuple[str, bool, float]] = [
        # (label,             allow_entry, cat_freq)
        ("no-entry  cf=0.06", False,       0.06),
        ("entry     cf=0.06", True,        0.06),
        ("entry     cf=0.03", True,        0.03),
    ]
    me_results = run_market_entry_sweep(
        market_entry_configs,
        n_syndicates=10,
        n_brokers=4,
        n_syndicates_max=20,
        n_seeds=10,
        horizon=HORIZON,
    )

    print(f"\n{'Config':<22} {'Period':>8} {'Amplitude':>10} {'MeanLR':>8} "
          f"{'HHI':>8} {'Entries':>8}")
    print("-" * 70)
    for r in me_results:
        ps = f"{r.mean_period:.1f}" if r.mean_period is not None else "N/A"
        print(f"{r.label:<22} {ps:>8} {r.mean_amplitude:>10.4f} {r.mean_lr:>8.4f} "
              f"{r.mean_hhi:>8.4f} {r.total_entries:>8.1f}")

    p = plot_21_market_entry(me_results, "diag_21_market_entry.png")
    print(f"  Saved: {p}")

    # ── Phase 17: Experiment 3a — Managed Runoff ──────────────────────────────
    print("\n" + "=" * 60)
    print("Phase 17: Experiment 3a — Managed Runoff (Central Fund)")
    print("         No-runoff vs runoff vs runoff+entry under cats")
    print("         (10 syn, 4 brok, cat_freq=0.06, 10 seeds × 60yr)")
    print("=" * 60)

    runoff_configs: list[tuple[str, bool, bool, float]] = [
        # (label,                  allow_runoff, allow_entry, cat_freq)
        ("A-no-runoff  cf=0.06",   False,        False,       0.06),
        ("B-runoff     cf=0.06",   True,         False,       0.06),
        ("C-runoff+entry cf=0.06", True,         True,        0.06),
    ]
    ro_results = run_runoff_sweep(
        runoff_configs,
        n_syndicates=10,
        n_brokers=4,
        n_syndicates_max=20,
        n_seeds=10,
        horizon=HORIZON,
    )

    print(f"\n{'Config':<25} {'Period':>8} {'Amplitude':>10} {'MeanLR':>8} "
          f"{'HHI':>8} {'CF Total':>10} {'CF%':>6}")
    print("-" * 80)
    for r in ro_results:
        ps = f"{r.mean_period:.1f}" if r.mean_period is not None else "N/A"
        print(f"{r.label:<25} {ps:>8} {r.mean_amplitude:>10.4f} {r.mean_lr:>8.4f} "
              f"{r.mean_hhi:>8.4f} {r.mean_cf_total:>10.1f} {r.cf_pct_of_claims:>5.1f}%")

    p = plot_22_runoff(ro_results, "diag_22_runoff.png")
    print(f"  Saved: {p}")

    # ── Phase 18: Experiment 3b — High-Volume Lloyd's-Realistic Density ───────
    print("\n" + "=" * 60)
    print("Phase 18: Experiment 3b — High-Volume Lloyd's-Realistic Density")
    print("         20 syn / 10 brok / 2000 rpy = 100 risks/syn/yr (~8% Lloyd's)")
    print("         Reference arm: same agents, 50 rpy (2.5 risks/syn/yr)")
    print("         Primary question: does Law of Large Numbers suppress cycles?")
    print("         (5 seeds × 25yr, no cats, capital=50000 for exposure headroom)")
    print("=" * 60)

    hv_configs: list[tuple[str, int, int, int, int]] = [
        # (label,              n_syn, n_brok, rpy,    cap)
        # capital=50000 needed so peril-exposure cap (70%×cap per region) allows
        # 2000 rpy with default limit range (500–2000); both arms use same cap.
        ("50-rpy (ref)",       20,    10,      50,  50000),
        ("2000-rpy (HV)",      20,    10,    2000,  50000),
    ]
    hv_results = run_high_volume_sweep(
        hv_configs,
        n_seeds=5,
        horizon=25,
    )

    print(f"\n{'Config':<20} {'rpy':>6} {'rps/syn':>8} {'Period':>8} "
          f"{'Amplitude':>10} {'MeanLR':>8} {'LR_CV':>7} {'HHI':>8} {'t(s)':>6}")
    print("-" * 85)
    for r in hv_results:
        ps = f"{r.mean_period:.1f}" if r.mean_period is not None else "N/A"
        print(f"{r.label:<20} {r.risks_per_year:>6} {r.risks_per_syn_yr:>8.1f} "
              f"{ps:>8} {r.mean_amplitude:>10.4f} {r.mean_lr:>8.4f} "
              f"{r.lr_cv:>7.4f} {r.mean_hhi:>8.4f} {r.runtime_seconds:>6.0f}")

    if len(hv_results) == 2:
        ref_r, hv_r = hv_results
        amp_change = (hv_r.mean_amplitude - ref_r.mean_amplitude) / ref_r.mean_amplitude * 100
        cv_change  = (hv_r.lr_cv          - ref_r.lr_cv)          / ref_r.lr_cv          * 100
        print(f"\n  LLN effect: amplitude {amp_change:+.1f}%, LR CV {cv_change:+.1f}%")
        print(f"  {'LLN suppresses amplitude ✓' if amp_change < -10 else 'Cycle amplitude largely unchanged (behavioural mechanism dominant)'}")

    p = plot_23_high_volume(hv_results, "diag_23_high_volume.png")
    print(f"  Saved: {p}")

    # ── Phase 19: Experiment 3c — Catastrophe Calibration Regime ─────────────
    print("\n" + "=" * 60)
    print("Phase 19: Experiment 3c — Cat Calibration (wipeout vs proportional)")
    print("         Derived threshold: rpy≥400 for proportional regime")
    print("         Calibrated config: n_syn=20, rpy=500, cap=15000, cat_freq=0.05")
    print("         2-arm (cat / no-cat), 5 seeds × 50yr per config")
    print("=" * 60)

    cat_calib_configs: list[tuple] = [
        # (label,                   n_syn, n_brok, rpy,   capital,   cat_freq)
        ("default-uncalibrated",    10,    4,        25,   2_000.0,   0.05),
        ("rpy=100 (transition)",    20,   10,       100,   5_000.0,   0.05),
        ("calibrated-rpy=500",      20,   10,       500,  15_000.0,   0.05),
        ("calibrated-highcat",      20,   10,       500,  15_000.0,   0.10),
    ]

    cc_results = run_cat_calib_sweep(cat_calib_configs, n_seeds=5, horizon=50)

    print(f"\n{'Config':<28} {'rpy':>5} {'cap':>7} {'cat_δ(pp)':>10} "
          f"{'wipeout%':>9} {'period':>7} {'HHI':>7} {'t(s)':>6}")
    print("-" * 80)
    for r in cc_results:
        ps   = f"{r.mean_period:.1f}" if r.mean_period is not None else "N/A"
        wf_s = (f"{r.wipeout_fraction*100:.0f}%"
                if not math.isnan(r.wipeout_fraction) else "N/A")
        print(f"{r.label:<28} {r.risks_per_year:>5} {r.initial_capital:>7.0f} "
              f"{r.mean_cat_lr_delta*100:>10.1f} {wf_s:>9} {ps:>7} "
              f"{r.mean_hhi:>7.3f} {r.runtime_seconds:>6.0f}")

    p = plot_24_cat_calibration(cc_results, "diag_24_cat_calibration.png")
    print(f"  Saved: {p}")

    print("\nDone. All PNGs saved to working directory.")


if __name__ == "__main__":
    main()
