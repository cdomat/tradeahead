"""Exchange-rate scenarios for the plan and the simulator.

Rates follow a jump-diffusion: small daily moves (volatility) plus rare, sudden
devaluations (jumps). Calm recent data alone would hide the risk TradeAhead
protects against, so jump frequency and size come from the full history.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Scenario:
    spot: float                 # local currency per 1 unit of foreign currency today
    days: int                   # trading days until the payment is due
    daily_vol: float            # standard deviation of daily log moves, excluding jumps
    daily_drift: float = 0.0    # average daily log move, excluding jumps
    jump_prob: float = 0.0      # chance of a devaluation on any given day
    jump_mean: float = 0.0      # average size of a devaluation (log move)
    jump_sd: float = 0.0        # spread of devaluation sizes

    # Totals used by the plan (mean and variance of one day's log move, jumps included).
    @property
    def total_drift(self) -> float:
        return self.daily_drift + self.jump_prob * self.jump_mean

    @property
    def total_var(self) -> float:
        p, m, s = self.jump_prob, self.jump_mean, self.jump_sd
        return self.daily_vol ** 2 + p * (s ** 2 + m ** 2) - (p * m) ** 2

    def stressed(self, vol_mult: float = 1.0, jump_mult: float = 1.0, extra_annual_drift: float = 0.0):
        """The simulator's sliders: scale volatility and devaluation risk, add a trend.

        Extra devaluation risk is added on top of the current drift (not offset),
        so a higher jump setting makes the currency both riskier and weaker on average.
        """
        return replace(self, daily_vol=self.daily_vol * vol_mult,
                       jump_prob=min(self.jump_prob * jump_mult, 0.5),
                       daily_drift=self.daily_drift + extra_annual_drift / 252)


def jumps_from_history(series: pd.Series, threshold: float = 0.05) -> dict:
    """Frequency and size of daily devaluations larger than `threshold` (log move)."""
    r = np.log(series.dropna()).diff().dropna()
    jumps = r[r > threshold]
    normal = r[r.abs() <= threshold]
    return {
        "jump_prob": len(jumps) / max(len(r), 1),
        "jump_mean": float(jumps.mean()) if len(jumps) else 0.0,
        "jump_sd": float(jumps.std(ddof=0)) if len(jumps) > 1 else 0.0,
        "daily_vol": float(normal.std()),
        "n_jumps": int(len(jumps)),
        "n_days": int(len(r)),
    }


DRIFT_MODES = {
    "implied": "Market-implied: the currency weakens on average by the interest-rate gap",
    "historical": "Historical: past devaluations repeat at the same pace (stress view)",
    "recent": "Recent trend: the past year's average move continues",
}


def scenario_from_history(series: pd.Series, days: int, recent_days: int = 250,
                          drift: str = "implied", rate_gap: float = 0.16) -> Scenario:
    """Default scenario: today's spot, recent day-to-day volatility, devaluation risk from all history.

    drift="implied" (default) sets the expected path to grow at the local-minus-dollar
    interest gap, the rate a fair forward would price. Devaluations still happen
    (they drive the risk), but calm days drift the other way, so the plan does not
    bet on direction. "historical" adds the jumps on top with no offset, and
    "recent" uses the past year's average move.
    """
    s = series.dropna()
    r_recent = np.log(s).diff().dropna().tail(recent_days)
    j = jumps_from_history(s)
    vol = float(r_recent[r_recent.abs() <= 0.05].std())
    sc = Scenario(spot=float(s.iloc[-1]), days=int(days), daily_vol=vol,
                  jump_prob=j["jump_prob"], jump_mean=j["jump_mean"], jump_sd=j["jump_sd"])
    if drift == "recent":
        return replace(sc, daily_drift=float(r_recent.mean()))
    if drift == "implied":
        return implied_drift(sc, rate_gap)
    return sc


def implied_drift(sc: Scenario, rate_gap: float) -> Scenario:
    """Set the calm-day drift so the expected rate grows at exactly `rate_gap` a year."""
    jump_factor = 1 + sc.jump_prob * (np.exp(sc.jump_mean + 0.5 * sc.jump_sd ** 2) - 1)
    d = rate_gap / 252 - 0.5 * sc.daily_vol ** 2 - np.log(jump_factor)
    return replace(sc, daily_drift=float(d))


def expected_path(sc: Scenario) -> np.ndarray:
    """Expected rate on each day 0..days."""
    t = np.arange(sc.days + 1)
    one_day = np.exp(sc.daily_drift + 0.5 * sc.daily_vol ** 2) * (
        1 + sc.jump_prob * (np.exp(sc.jump_mean + 0.5 * sc.jump_sd ** 2) - 1))
    return sc.spot * one_day ** t


def simulate_paths(sc: Scenario, n_paths: int = 5000, seed: int = 0) -> np.ndarray:
    """Monte Carlo rate paths, shape (n_paths, days + 1). Column 0 is today's spot."""
    rng = np.random.default_rng(seed)
    n = sc.days
    diffusion = rng.normal(sc.daily_drift, sc.daily_vol, size=(n_paths, n))
    jumps = (rng.random((n_paths, n)) < sc.jump_prob) * rng.normal(sc.jump_mean, sc.jump_sd, size=(n_paths, n))
    log_paths = np.cumsum(diffusion + np.maximum(jumps, 0.0), axis=1)
    return sc.spot * np.exp(np.concatenate([np.zeros((n_paths, 1)), log_paths], axis=1))


def shock_path(sc: Scenario, shock: float, shock_day: int | None = None) -> np.ndarray:
    """A single devaluation of `shock` (e.g. 0.30 = +30%) on `shock_day`, flat otherwise."""
    day = sc.days // 2 if shock_day is None else int(np.clip(shock_day, 1, sc.days))
    path = np.full(sc.days + 1, sc.spot)
    path[day:] *= 1 + shock
    return path
