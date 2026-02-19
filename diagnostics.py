"""
diagnostics.py — Underwriting Cycle Diagnostics for the Insurance Market Simulation
Produces visualizations and quantified cycle statistics, plus documented parameter sweeps.
"""

# ── Section 0: Imports and plot style ────────────────────────────────────────

import math
import statistics
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

SYNDICATE_LEVEL_PARAMS = {"beta", "w", "z"}
MARKET_LEVEL_PARAMS    = {"initial_capital", "cat_freq"}


class SweepResult(NamedTuple):
    param_name:    str
    param_value:   float
    hypothesis:    str
    mean_period:   float | None
    std_period:    float
    mean_amplitude: float
    lr_by_seed:    dict[int, dict[int, float]]


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

        periods, amplitudes = [], []
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

        mean_period = statistics.mean(periods) if periods else None
        std_period  = statistics.stdev(periods) if len(periods) > 1 else 0.0
        mean_amplitude = statistics.mean(amplitudes) if amplitudes else 0.0
        print(f"period={mean_period}")
        results.append(SweepResult(
            param_name=param_name,
            param_value=val,
            hypothesis=hypothesis,
            mean_period=mean_period,
            std_period=std_period,
            mean_amplitude=mean_amplitude,
            lr_by_seed=lr_by_seed,
        ))
    return results


# ── Section 6: main() ─────────────────────────────────────────────────────────

def main() -> None:
    """Run all four diagnostic phases."""
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

    print("\nDone. All PNGs saved to working directory.")


if __name__ == "__main__":
    main()
