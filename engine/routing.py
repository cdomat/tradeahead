"""Executable Rate Index and delivery-weighted routing.

Executable Rate Index (ERI)
    For each dealer and ticket size, learns from past fills what it really costs to
    buy dollars (executed rate vs. mid, not the screen quote) and how likely the full
    amount is to arrive on time. Recent fills count more (exponential decay), and the
    on-time probability is a Beta posterior, so a dealer with few fills stays close
    to a cautious prior instead of looking perfect.

Delivery-weighted routing
    Ranks dealers by expected all-in cost per dollar, including what a failed or
    late delivery costs the importer:

      all_in = mid * (1 + markup + margin)
             + (1 - p_on_time) * [ unfilled share * mid * (fallback markup + rate move over the delay)
                                   + days late beyond the slack * late cost per day * mid ]

    The cheapest quote is often not the best route when the payment is close.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import beta as beta_dist

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

PRIOR_ON_TIME = 0.70   # cautious starting belief for any dealer
PRIOR_STRENGTH = 6.0   # worth about six fills


def load_reference() -> tuple[list[dict], pd.DataFrame, dict]:
    dealers = json.loads((DATA / "dealers.json").read_text())
    fills = pd.read_csv(DATA / "dealer_fills.csv", parse_dates=["date"])
    markets = json.loads((DATA / "markets.json").read_text())
    return dealers, fills, markets


def bucket_of(ticket: float, buckets: list) -> str:
    return next(name for lo, hi, name in buckets if lo <= ticket < hi)


def build_rate_index(fills: pd.DataFrame, as_of=None, half_life_days: float = 90.0) -> pd.DataFrame:
    """One row per dealer and ticket bucket."""
    f = fills.copy()
    as_of = pd.Timestamp(as_of) if as_of is not None else f["date"].max()
    f = f[f["date"] <= as_of]
    age = (as_of - f["date"]).dt.days.clip(lower=0)
    f["w"] = 0.5 ** (age / half_life_days)
    f["markup"] = f["executed_rate"] / f["mid_rate"] - 1
    f["quoted_markup"] = f["quoted_rate"] / f["mid_rate"] - 1

    rows = []
    for (dealer, bucket), g in f.groupby(["dealer_id", "bucket"]):
        w = g["w"].to_numpy()
        on = g["on_time"].astype(float).to_numpy()
        a = PRIOR_ON_TIME * PRIOR_STRENGTH + (w * on).sum()
        b = (1 - PRIOR_ON_TIME) * PRIOR_STRENGTH + (w * (1 - on)).sum()
        late = g[~g["on_time"].astype(bool)]
        wl = late["w"].to_numpy()
        rows.append({
            "dealer_id": dealer, "bucket": bucket, "n_fills": len(g), "weight": w.sum(),
            "quoted_bps": 1e4 * np.average(g["quoted_markup"], weights=w),
            "exec_bps": 1e4 * np.average(g["markup"], weights=w),
            "on_time_p": a / (a + b),
            "on_time_p05": float(beta_dist.ppf(0.05, a, b)),
            "late_delay_days": float(np.average(late["delay_days"], weights=wl)) if len(late) else 3.0,
            "late_filled_share": float(np.average(late["filled_share"], weights=wl)) if len(late) else 0.5,
        })
    eri = pd.DataFrame(rows)
    eri["slippage_bps"] = eri["exec_bps"] - eri["quoted_bps"]
    return eri


@dataclass
class RouteContext:
    mid: float                   # mid rate now (local per 1 USD)
    slack_days: int              # days between expected settlement and the latest safe payment date
    daily_vol: float             # for the cost of re-buying after a failed delivery
    daily_drift: float = 0.0
    margin: float = 0.008        # TradeAhead FX margin for the customer's tier
    late_cost_per_day: float = 0.003
    fallback_bps: float = 150.0  # premium paid to cover a failed fill at short notice


def score_dealers(ticket: float, ctx: RouteContext, eri: pd.DataFrame, dealers: list[dict],
                  market: str, buckets: list) -> pd.DataFrame:
    """Every eligible dealer for one ticket, with screen-price rank and delivery-weighted rank."""
    b = bucket_of(ticket, buckets)
    rows = []
    for d in dealers:
        if d["market"] != market or not (d["min_ticket"] <= ticket <= d["max_ticket"]):
            continue
        r = eri[(eri["dealer_id"] == d["id"]) & (eri["bucket"] == b)]
        if r.empty:
            continue
        r = r.iloc[0]
        p = float(r["on_time_p"])
        delay = float(r["late_delay_days"])
        unfilled = 1 - float(r["late_filled_share"])
        move = ctx.daily_vol * np.sqrt(delay) * 0.8 + ctx.daily_drift * delay   # 0.8 ≈ E|Z|
        days_late = max(delay - ctx.slack_days, 0.0)
        fail_cost = unfilled * (ctx.fallback_bps / 1e4 + move) + days_late * ctx.late_cost_per_day

        screen = ctx.mid * (1 + r["quoted_bps"] / 1e4)
        execd = ctx.mid * (1 + r["exec_bps"] / 1e4)
        margin = ctx.mid * ctx.margin
        risk = ctx.mid * (1 - p) * fail_cost
        rows.append({
            "dealer_id": d["id"], "dealer": d["name"], "type": d["type"], "bucket": b,
            "mid": ctx.mid, "screen_rate": screen, "executable_rate": execd, "margin": margin, "delivery_cost": risk,
            "all_in_rate": execd + margin + risk, "on_time_p": p, "on_time_p05": r["on_time_p05"],
            "late_delay_days": delay, "max_ticket": d["max_ticket"], "daily_capacity": d["daily_capacity"],
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["screen_rank"] = out["screen_rate"].rank(method="first").astype(int)
    out["route_rank"] = out["all_in_rate"].rank(method="first").astype(int)
    return out.sort_values("route_rank").reset_index(drop=True)


def route_ticket(ticket: float, ctx: RouteContext, eri: pd.DataFrame, dealers: list[dict], market: str,
                 buckets: list, used_capacity: dict | None = None, max_share: float = 1.0) -> pd.DataFrame:
    """Split one day's purchase across dealers, best delivery-weighted rate first.

    Respects each dealer's ticket limit and remaining daily capacity. `max_share`
    below 1 spreads large tickets so one dealer's failure cannot sink the whole payment.
    """
    used = used_capacity if used_capacity is not None else {}
    remaining, legs = float(ticket), []
    for _ in range(10):
        if remaining < 1:
            break
        ranked = score_dealers(remaining, ctx, eri, dealers, market, buckets)
        if ranked.empty:  # remaining amount too small or too large for everyone: try a smaller slice
            ranked = score_dealers(min(remaining, 50_000), ctx, eri, dealers, market, buckets)
            if ranked.empty:
                break
        placed = False
        for _, r in ranked.iterrows():
            room = r["daily_capacity"] - used.get(r["dealer_id"], 0.0)
            size = min(remaining, r["max_ticket"], room, max(ticket * max_share, 1.0))
            if size < 1:
                continue
            leg = score_dealers(size, ctx, eri, dealers, market, buckets)
            leg = leg[leg["dealer_id"] == r["dealer_id"]]
            if leg.empty:
                continue
            legs.append(leg.iloc[0].to_dict() | {"amount_fx": size})
            used[r["dealer_id"]] = used.get(r["dealer_id"], 0.0) + size
            remaining -= size
            placed = True
            break
        if not placed:
            break
    out = pd.DataFrame(legs)
    if not out.empty:
        out["local_cost"] = out["amount_fx"] * out["all_in_rate"]
    out.attrs["unrouted_fx"] = max(remaining, 0.0)
    return out


def route_plan(schedule: pd.DataFrame, due_day: int, scenario, eri: pd.DataFrame, dealers: list[dict],
               market_cfg: dict, market: str, buckets: list, margin: float, max_share: float = 0.6,
               split_above: float = 20_000) -> pd.DataFrame:
    """Route every purchase in a protection plan. Slack shrinks as the due date gets closer.

    Tickets above `split_above` are spread so no dealer takes more than `max_share`.
    """
    legs = []
    for _, row in schedule[schedule["buy_fx"] > 0].iterrows():
        ctx = RouteContext(
            mid=float(row["expected_rate"]),
            slack_days=int(due_day - row["day"] - market_cfg.get("settlement_days", 1)),
            daily_vol=float(np.sqrt(scenario.total_var)), daily_drift=scenario.total_drift,
            margin=margin, late_cost_per_day=market_cfg.get("late_cost_per_day", 0.003),
        )
        ticket = float(row["buy_fx"])
        r = route_ticket(ticket, ctx, eri, dealers, market, buckets, used_capacity={},
                         max_share=max_share if ticket > split_above else 1.0)
        if not r.empty:
            r.insert(0, "date", row["date"])
            r.insert(0, "day", row["day"])
            legs.append(r)
    return pd.concat(legs, ignore_index=True) if legs else pd.DataFrame()


def cost_waterfall(legs: pd.DataFrame) -> dict[str, float]:
    """Local-currency cost split for the Dealer Liquidity Waterfall chart. Parts add up to the all-in cost."""
    if legs.empty:
        return {}
    a = legs["amount_fx"]
    parts = {
        "Mid-market cost": (a * legs["mid"]).sum(),
        "Dealer spread": (a * (legs["screen_rate"] - legs["mid"])).sum(),
        "Execution slippage": (a * (legs["executable_rate"] - legs["screen_rate"])).sum(),
        "TradeAhead margin": (a * legs["margin"]).sum(),
        "Expected delivery cost": (a * legs["delivery_cost"]).sum(),
    }
    parts["All-in cost"] = (a * legs["all_in_rate"]).sum()
    return {k: float(v) for k, v in parts.items()}
