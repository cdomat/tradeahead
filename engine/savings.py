"""Savings: what the plan costs versus what the importer would pay without it.

Two baselines, both paying the full balance on the due date:
  * Unprotected, authorized dealer: buy everything at the due-date rate.
  * Unprotected, parallel market: the same, plus the parallel-market premium
    (where many SMEs end up when official dollars are not available).

The plan's cost includes the naira interest it gives up by converting early, so
"savings" are net of what protection costs. Results are reported across simulated
paths, because protection is worth the most in the bad cases, and can cost a
little in calm ones.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

TRADING_DAYS = 252


@dataclass
class CostModel:
    exec_cost: float          # all-in conversion cost through TradeAhead (spread + margin + slippage)
    baseline_exec_cost: float  # what the importer pays an authorized dealer today without TradeAhead
    parallel_premium: float   # extra cost on the parallel market
    local_rate: float
    usd_rate: float
    impact_per_fx: float = 0.0  # extra cost per dollar bought in one day (plan.impact_coefficient)


def path_costs(buys: np.ndarray, amount_fx: float, paths: np.ndarray, cm: CostModel) -> np.ndarray:
    """Local cost of a purchase schedule on each simulated path (interest given up included).

    buys: dollars bought on days 0..N-1. Whatever is left is bought on day N.
    paths: shape (n_paths, N + 1).
    """
    n = paths.shape[1] - 1
    buys = np.asarray(buys, dtype=float)[:n]
    residual = amount_fx - buys.sum()
    carry = (cm.local_rate - cm.usd_rate) * (n - np.arange(n)) / TRADING_DAYS
    conv = paths[:, :n] @ (buys * (1 + cm.exec_cost + cm.impact_per_fx * buys + carry))
    return conv + residual * paths[:, n] * (1 + cm.exec_cost + cm.impact_per_fx * residual)


def baselines(amount_fx: float, paths: np.ndarray, cm: CostModel) -> dict[str, np.ndarray]:
    end = paths[:, -1] * amount_fx
    return {
        "Unprotected (bank)": end * (1 + cm.baseline_exec_cost),
        "Unprotected (parallel market)": end * (1 + cm.baseline_exec_cost + cm.parallel_premium),
    }


def _stats(x: np.ndarray) -> dict:
    return {"mean": float(x.mean()), "median": float(np.median(x)), "p05": float(np.percentile(x, 5)),
            "p95": float(np.percentile(x, 95)), "worst": float(x.max())}


def compare(buys: np.ndarray, amount_fx: float, paths: np.ndarray, cm: CostModel,
            others: dict[str, np.ndarray] | None = None) -> dict:
    """Cost statistics for the plan, each baseline and any other strategies, plus savings.

    Savings > 0 means the plan is cheaper. `tail_savings` compares costs in the worst 5%
    of rate outcomes, which is where protection matters.
    """
    plan = path_costs(buys, amount_fx, paths, cm)
    base = baselines(amount_fx, paths, cm)
    strategies = {"TradeAhead plan": plan} | base
    for name, b in (others or {}).items():
        strategies[name] = path_costs(b, amount_fx, paths, cm)

    bad = paths[:, -1] >= np.percentile(paths[:, -1], 95)
    out = {"costs": {k: _stats(v) for k, v in strategies.items()}, "savings": {}, "distributions": strategies}
    for name, b in base.items():
        s = b - plan
        out["savings"][name] = {
            **_stats(s),
            "mean_pct": float((s / b).mean()),
            "prob_cheaper": float((s > 0).mean()),
            "tail_savings": float(s[bad].mean()),
            "tail_savings_pct": float((s[bad] / b[bad]).mean()),
        }
    return out


def shock_savings(buys: np.ndarray, amount_fx: float, path: np.ndarray, cm: CostModel) -> dict:
    """Savings on one deterministic path (e.g. 'overnight +30% devaluation on day 10')."""
    p = path[None, :]
    plan = float(path_costs(buys, amount_fx, p, cm)[0])
    base = {k: float(v[0]) for k, v in baselines(amount_fx, p, cm).items()}
    return {"plan_cost": plan, "baselines": base, "savings": {k: v - plan for k, v in base.items()},
            "savings_pct": {k: (v - plan) / v for k, v in base.items()}}
