"""Cached data loading and the end-to-end run behind the Dashboard."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from engine import market, plan, routing, savings
from services import fx

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
N_PATHS = 20_000


# ---------------------------------------------------------------- data

@st.cache_data(ttl=3600, show_spinner="Fetching live exchange rates…")
def fx_history() -> tuple[pd.DataFrame, str]:
    return fx.load_history(prefer_live=True)


@st.cache_data(show_spinner=False)
def reference() -> dict:
    dealers, fills, mk = routing.load_reference()
    return {
        "dealers": dealers, "fills": fills, "eri": routing.build_rate_index(fills),
        "markets": mk["markets"], "tiers": mk["tiers"], "buckets": mk["buckets"],
        "events": json.loads((DATA / "fx_events.json").read_text()),
    }


def latest(pair: str) -> tuple[float, float, pd.Timestamp]:
    """Last rate, one-day change, date."""
    s = fx_history()[0][pair].dropna()
    return float(s.iloc[-1]), float(s.iloc[-1] / s.iloc[-2] - 1), s.index[-1]


def plan_start(today: date | None = None) -> date:
    return pd.offsets.BDay().rollforward(pd.Timestamp(today or date.today())).date()


def trading_days_until(due: date, start: date | None = None) -> int:
    return len(pd.bdate_range(start or plan_start(), due)) - 1


# ---------------------------------------------------------------- run

@dataclass(frozen=True)
class Sim:
    cover: float = 1.0
    risk_aversion: float = 3.0
    vol_mult: float = 1.0
    jump_mult: float = 1.0
    drift_mode: str = "implied"
    tier: str = "Growth"
    shock: float = 0.30
    shock_day: int = 5
    max_share: float = 0.6


@st.cache_data(show_spinner="Building your protection plan…", max_entries=64)
def run(market_code: str, ccy: str, balance: float, due: date, cash: float, buffer: float,
        inflow: float, outflow: float, sim: Sim) -> dict:
    ref = reference()
    m = ref["markets"][market_code]
    local = m["local"]
    pair, usd_pair = f"{ccy}/{local}", f"USD/{local}"
    wide, _ = fx_history()
    start = plan_start()
    n = trading_days_until(due, start)
    if n < 2:
        return {"error": "The balance is due in less than two trading days, too soon to build a plan."}

    gap = m["local_deposit_rate"] - m["usd_holding_rate"]
    base = market.scenario_from_history(wide[pair], n, drift=sim.drift_mode, rate_gap=gap)
    sc = base.stressed(vol_mult=sim.vol_mult, jump_mult=sim.jump_mult)
    margin = ref["tiers"][sim.tier]["fx_margin"]

    # USD value of one unit of the invoice currency: routing and dealer limits work in USD.
    usd_per_unit = float(wide[pair].dropna().iloc[-1] / wide[usd_pair].dropna().iloc[-1])

    # First estimate of the all-in cost: best route for a typical ticket today.
    ctx0 = routing.RouteContext(mid=sc.spot / usd_per_unit, slack_days=n, daily_vol=np.sqrt(sc.total_var),
                                margin=margin, late_cost_per_day=m["late_cost_per_day"])
    probe = routing.score_dealers(min(max(balance * usd_per_unit / 3, 1_000), 40_000), ctx0, ref["eri"],
                                  ref["dealers"], market_code, ref["buckets"])
    exec_est = float(probe["all_in_rate"].iloc[0] / ctx0.mid - 1) if not probe.empty else 0.02

    p = plan.PlanInputs(
        amount_fx=balance, cover_ratio=sim.cover, scenario=sc, opening_cash=cash, cash_buffer=buffer,
        daily_inflow=inflow, daily_outflow=outflow, local_rate=m["local_deposit_rate"],
        usd_rate=m["usd_holding_rate"], exec_cost=exec_est, risk_aversion=sim.risk_aversion,
        max_daily_fx=150_000 / usd_per_unit, min_ticket=1_000 / usd_per_unit,
        daily_depth_fx=50_000 / usd_per_unit, start=start, settlement_days=m["settlement_days"])
    result = plan.build_plan(p)

    # Route in USD terms (same local cost), then report back.
    sched_usd = result.schedule.copy()
    sched_usd["buy_fx"] = sched_usd["buy_fx"] * usd_per_unit
    sched_usd["expected_rate"] = sched_usd["expected_rate"] / usd_per_unit
    legs = routing.route_plan(sched_usd, n, sc, ref["eri"], ref["dealers"], m, market_code, ref["buckets"],
                              margin=margin, max_share=sim.max_share)
    wf = routing.cost_waterfall(legs)
    all_in = wf["All-in cost"] / (legs["amount_fx"] * legs["mid"]).sum() - 1 if wf else exec_est

    cm = savings.CostModel(exec_cost=all_in, baseline_exec_cost=m["baseline_bank_cost"],
                           parallel_premium=m["parallel_premium"], local_rate=m["local_deposit_rate"],
                           usd_rate=m["usd_holding_rate"], impact_per_fx=plan.impact_coefficient(p))
    paths = market.simulate_paths(sc, N_PATHS, seed=7)
    buys = result.schedule["buy_fx"].to_numpy()
    comp = savings.compare(buys, balance, paths, cm, others=plan.naive_schedules(p))
    shock = savings.shock_savings(buys, balance, market.shock_path(sc, sim.shock, sim.shock_day), cm)

    # Ranking for the first purchase (the one the user is about to approve).
    first = sched_usd[sched_usd["buy_fx"] > 0].head(1)
    ranking = pd.DataFrame()
    if not first.empty:
        r0 = first.iloc[0]
        ctx = routing.RouteContext(mid=float(r0["expected_rate"]), slack_days=int(n - r0["day"] - 1),
                                   daily_vol=np.sqrt(sc.total_var), daily_drift=sc.total_drift, margin=margin,
                                   late_cost_per_day=m["late_cost_per_day"])
        ranking = routing.score_dealers(min(float(r0["buy_fx"]), 60_000), ctx, ref["eri"], ref["dealers"],
                                        market_code, ref["buckets"])

    return {
        "pair": pair, "local": local, "ccy": ccy, "n": n, "start": start, "scenario": sc,
        "usd_per_unit": usd_per_unit, "plan": result, "legs": legs, "waterfall": wf, "all_in": all_in,
        "exec_est": exec_est, "compare": comp, "shock": shock, "ranking": ranking, "margin": margin,
        "end_rates": paths[:, -1],
    }
