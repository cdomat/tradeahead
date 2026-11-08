"""Cash-constrained protection plan.

Decides how many dollars to buy on each trading day before an invoice is due.

For daily purchases x_0 .. x_{N-1} (USD), the plan minimises

    expected local cost of buying            sum_t x_t * F_t * (1 + c)
  + naira interest given up by buying early  sum_t x_t * F_t * (r_local - r_usd) * (N - t) / 252
  + a penalty on the exchange-rate risk left open
                                             k * S0^2 * var_d * sum_k U_k^2 / (A * S0)
  + the extra cost of buying a lot in one day  S0 * eta * sum_t x_t^2

where F_t is the expected rate on day t, c the all-in execution cost, var_d the
daily variance of the rate (devaluation jumps included), U_k the dollars still
unbought during day k, and eta the price impact per dollar of daily volume.
The sum of U_k^2 is the variance of the final cost, so buying earlier lowers risk
but costs interest and, if bunched, a worse rate. This is the classic
optimal-execution trade-off (Almgren-Chriss), with the importer's cash added as a hard limit.

Constraints:
  * the plan buys exactly the target (cover ratio x invoice balance) by the cut-off,
  * no daily purchase exceeds the daily limit,
  * cumulative naira spent never pushes cash below the buffer. Naira needs are
    priced at a stressed rate (95% bad case), so a weaker currency does not break it.

If the cash available cannot fund the target, the plan buys as much as it can
and reports the remainder as a coverage gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .market import Scenario, expected_path

TRADING_DAYS = 252


@dataclass
class PlanInputs:
    amount_fx: float                 # invoice balance still to pay, in foreign currency
    cover_ratio: float               # share of the balance to protect before the due date (0..1)
    scenario: Scenario               # spot, days to due date, volatility, devaluation risk
    opening_cash: float              # local cash on hand today
    cash_buffer: float               # local cash the business must always keep
    daily_inflow: float | np.ndarray = 0.0    # expected local receipts per trading day
    daily_outflow: float | np.ndarray = 0.0   # other committed local payments per trading day
    local_rate: float = 0.18         # annual interest on local deposits
    usd_rate: float = 0.02           # annual interest on foreign currency held at the partner
    exec_cost: float = 0.018         # all-in cost of converting, as a share (spread + margin)
    max_daily_fx: float = 1e12       # most that can be bought in one day
    min_ticket: float = 1_000.0      # smallest purchase worth making
    daily_depth_fx: float = 50_000.0  # size the dealer panel absorbs in a day at normal prices
    impact_bps: float = 25.0         # extra cost (bps) when one day's purchase equals that depth
    risk_aversion: float = 3.0       # 0 = cost only, higher = protect earlier
    settlement_days: int = 1         # last purchase must settle before the due date
    cash_confidence_z: float = 1.65  # stress level for naira needs (1.65 ≈ 95%)
    start: date | None = None        # first trading day of the plan (default today)


@dataclass
class PlanResult:
    schedule: pd.DataFrame
    target_fx: float
    planned_fx: float
    coverage_gap_fx: float
    residual_fx: float               # bought at spot on the due date (not protected)
    expected_cost_local: float       # conversions + residual, at expected rates
    carry_cost_local: float          # interest given up by buying early
    cost_std_local: float            # uncertainty of the final cost
    unprotected_std_local: float     # same, if nothing were protected
    status: str
    notes: list[str] = field(default_factory=list)

    @property
    def risk_reduction(self) -> float:
        return 1 - self.cost_std_local / self.unprotected_std_local if self.unprotected_std_local else 0.0


def _series(v, n) -> np.ndarray:
    a = np.asarray(v, dtype=float)
    return np.full(n, float(a)) if a.ndim == 0 else np.resize(a, n)


def _trading_dates(start: date | None, n: int) -> pd.DatetimeIndex:
    return pd.bdate_range(pd.Timestamp(start or date.today()), periods=n)


def cash_capacity(p: PlanInputs) -> tuple[np.ndarray, np.ndarray]:
    """Per day: stressed rate used for cash checks, and cumulative local cash free to spend."""
    sc, n = p.scenario, p.scenario.days
    t = np.arange(n)
    worst_impact = impact_coefficient(p) * min(p.max_daily_fx, p.amount_fx)
    stressed = (expected_path(sc)[:n] * np.exp(p.cash_confidence_z * np.sqrt(sc.total_var * t))
                * (1 + p.exec_cost + worst_impact))
    net = _series(p.daily_inflow, n) - _series(p.daily_outflow, n)
    free_cum = (p.opening_cash - p.cash_buffer) + np.cumsum(net)
    return stressed, free_cum


def max_coverable(p: PlanInputs, last_day: int) -> np.ndarray:
    """Buy as early as cash and daily limits allow: the most the plan can ever protect."""
    stressed, free_cum = cash_capacity(p)
    x = np.zeros(p.scenario.days)
    spent, remaining = 0.0, p.amount_fx * p.cover_ratio
    for t in range(last_day + 1):
        affordable = max(free_cum[t] - spent, 0.0) / stressed[t]
        x[t] = min(remaining, p.max_daily_fx, affordable)
        spent += x[t] * stressed[t]
        remaining -= x[t]
    return x


def build_plan(p: PlanInputs) -> PlanResult:
    sc = p.scenario
    n = sc.days
    if n < 1:
        raise ValueError("The due date must be at least one trading day away.")
    notes: list[str] = []
    last = max(n - 1 - p.settlement_days, 0)  # last day a purchase can still settle in time
    target = p.amount_fx * float(np.clip(p.cover_ratio, 0, 1))
    stressed, free_cum = cash_capacity(p)

    if free_cum[0] < 0:
        notes.append("Cash today is already below the buffer, so the plan starts once receipts arrive.")

    greedy = max_coverable(p, last)
    feasible = float(greedy.sum())
    gap = max(target - feasible, 0.0)
    goal = min(target, feasible)
    if gap > 1:
        notes.append(f"Cash covers {feasible / target:.0%} of the target. "
                     f"{gap:,.0f} cannot be protected without breaking the buffer.")

    F = expected_path(sc)
    t = np.arange(n)
    carry = F[:n] * (p.local_rate - p.usd_rate) * (n - t) / TRADING_DAYS
    lin = F[:n] * (1 + p.exec_cost) + carry
    # U_k for k = 1..n: dollars still open during day k (the residual stays open to the end).
    L = np.tril(np.ones((n, n)))                       # U = A - L @ x
    kappa = p.risk_aversion * sc.total_var / max(p.amount_fx, 1.0)  # penalty = kappa * S0 * sum(U^2), in local currency
    eta = impact_coefficient(p)                                      # impact = S0 * eta * sum(x^2)
    active = np.zeros(n, dtype=bool)
    active[: last + 1] = True

    def obj(x):
        U = p.amount_fx - L @ x
        return lin @ x + kappa * sc.spot * (U @ U) + sc.spot * eta * (x @ x)

    def grad(x):
        U = p.amount_fx - L @ x
        return lin - 2 * kappa * sc.spot * (L.T @ U) + 2 * sc.spot * eta * x

    cons = [{"type": "eq", "fun": lambda x: x.sum() - goal, "jac": lambda x: np.ones(n)},
            {"type": "ineq", "fun": lambda x: free_cum - np.cumsum(x * stressed),
             "jac": lambda x: -np.tril(np.ones((n, n))) * stressed}]
    bounds = [(0.0, min(p.max_daily_fx, goal) if active[i] else 0.0) for i in range(n)]

    if goal <= 0:
        x = np.zeros(n)
    else:
        res = minimize(obj, greedy * goal / max(feasible, 1e-9), jac=grad, bounds=bounds,
                       constraints=cons, method="SLSQP", options={"maxiter": 500, "ftol": 1e-9})
        x = res.x if res.success else greedy * goal / max(feasible, 1e-9)
        if not res.success:
            notes.append("The optimiser did not converge, so the plan uses the earliest feasible schedule.")
        x = _tidy(x, p.min_ticket, goal, p.max_daily_fx, free_cum, stressed, last)

    residual = p.amount_fx - x.sum()
    U = p.amount_fx - L @ x
    var_d = sc.total_var
    dates = _trading_dates(p.start, n + 1)
    expected_cost = float(x @ (F[:n] * (1 + p.exec_cost + eta * x)) + residual * F[n] * (1 + p.exec_cost))
    carry_cost = float(x @ carry)
    spent = np.cumsum(x * F[:n] * (1 + p.exec_cost + eta * x))

    sched = pd.DataFrame({
        "day": t, "date": dates[:n].date, "buy_fx": x.round(2),
        "expected_rate": F[:n].round(4), "local_needed": (x * F[:n] * (1 + p.exec_cost + eta * x)).round(0),
        "cum_fx": np.cumsum(x).round(2), "coverage": (np.cumsum(x) / p.amount_fx).round(4),
        "open_fx": U.round(2),
        "cash_free_before": (free_cum - np.concatenate([[0], spent[:-1]])).round(0),
        "cash_free_after": (free_cum - spent).round(0),
    })
    sched["buffer"] = p.cash_buffer
    sched["cash_after"] = sched["cash_free_after"] + p.cash_buffer
    sched["status"] = np.where(sched["buy_fx"] > 0, "Convert", np.where(t > last, "Settlement", "Hold"))

    status = ("Coverage gap" if gap > 1 else "Fully protected" if residual <= 1
              else "Partly protected" if x.sum() > 0 else "Not protected")
    return PlanResult(
        schedule=sched, target_fx=target, planned_fx=float(x.sum()), coverage_gap_fx=gap,
        residual_fx=float(residual), expected_cost_local=expected_cost, carry_cost_local=carry_cost,
        cost_std_local=float(sc.spot * np.sqrt(var_d * (U @ U))),
        unprotected_std_local=float(sc.spot * p.amount_fx * np.sqrt(var_d * n)),
        status=status, notes=notes,
    )


def impact_coefficient(p: PlanInputs) -> float:
    """Cost per dollar rises by impact_bps for every `daily_depth_fx` bought in one day."""
    return p.impact_bps / 1e4 / max(p.daily_depth_fx, 1.0)


def _tidy(x, min_ticket, goal, cap, free_cum, stressed, last) -> np.ndarray:
    """Turn the optimiser's output into tickets a dealer would accept.

    Follows the optimiser's cumulative path day by day, in whole hundreds, skips
    purchases below the minimum ticket (they roll into the next day) and puts the
    exact remainder on the last day. Cash is re-checked at every step.
    """
    path = np.cumsum(x)
    out = np.zeros_like(x)
    bought = spent = 0.0
    for t in range(last + 1):
        want = (goal - bought) if t == last else (path[t] - bought)
        afford = max(free_cum[t] - spent, 0.0) / stressed[t]
        amt = max(min(want, afford, cap), 0.0)
        if t < last:
            amt = np.floor(amt / 100) * 100
            if amt < min_ticket:
                amt = 0.0
        out[t] = round(amt, 2)
        bought += out[t]
        spent += out[t] * stressed[t]

    # A last-day remainder below the minimum ticket would find no dealer:
    # move it onto the latest earlier purchase if cash still holds on every day after.
    if 0 < out[last] < min_ticket:
        earlier = np.flatnonzero(out[:last] > 0)
        if earlier.size:
            t0 = earlier[-1]
            trial = out.copy()
            trial[t0] += trial[last]
            trial[last] = 0.0
            if trial[t0] <= cap and (free_cum[: last + 1] - np.cumsum(trial * stressed)[: last + 1] >= -1e-6).all():
                out = trial
    return out


def naive_schedules(p: PlanInputs) -> dict[str, np.ndarray]:
    """Comparison strategies an importer might use without TradeAhead (before cash limits)."""
    n = p.scenario.days
    last = max(n - 1 - p.settlement_days, 0)
    target = p.amount_fx * p.cover_ratio
    even = np.zeros(n)
    even[: last + 1] = target / (last + 1)
    today = np.zeros(n)
    today[0] = target
    return {"Wait for the due date": np.zeros(n), "Buy everything today": today, "Equal daily amounts": even}
