import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import market, plan, routing, savings  # noqa: E402
from services import fx, invoice  # noqa: E402

START = date(2026, 11, 9)


@pytest.fixture(scope="module")
def ref():
    wide, _ = fx.load_history(prefer_live=False)
    dealers, fills, mk = routing.load_reference()
    return {"wide": wide, "dealers": dealers, "eri": routing.build_rate_index(fills), "mk": mk,
            "ng": mk["markets"]["NG"]}


def inputs(ref, cash=80e6, ra=3.0, cover=1.0, days=16, amount=34_905.5, **kw):
    ng = ref["ng"]
    sc = market.scenario_from_history(ref["wide"]["USD/NGN"], days,
                                      rate_gap=ng["local_deposit_rate"] - ng["usd_holding_rate"])
    return plan.PlanInputs(amount_fx=amount, cover_ratio=cover, scenario=sc, opening_cash=cash,
                           cash_buffer=18e6, daily_inflow=2.5e6, daily_outflow=1.2e6,
                           local_rate=ng["local_deposit_rate"], usd_rate=ng["usd_holding_rate"],
                           exec_cost=0.019, max_daily_fx=60_000, risk_aversion=ra, start=START, **kw)


def test_buffer_never_touched(ref):
    for cash in (25e6, 40e6, 80e6):
        r = plan.build_plan(inputs(ref, cash=cash))
        assert (r.schedule["cash_after"] >= r.schedule["buffer"] - 1).all()


def test_full_cover_when_cash_allows(ref):
    r = plan.build_plan(inputs(ref, cash=80e6))
    assert r.status == "Fully protected"
    assert r.planned_fx == pytest.approx(34_905.5, abs=1)


def test_coverage_gap_when_cash_short(ref):
    r = plan.build_plan(inputs(ref, cash=30e6))
    assert r.status == "Coverage gap"
    assert r.coverage_gap_fx > 0
    assert r.planned_fx + r.coverage_gap_fx == pytest.approx(r.target_fx, abs=200)


def test_tickets_respect_minimum(ref):
    s = plan.build_plan(inputs(ref, cash=30e6)).schedule
    buys = s.loc[s["buy_fx"] > 0, "buy_fx"].iloc[:-1]
    assert (buys >= 1_000).all()


def test_risk_aversion_buys_earlier(ref):
    def avg_day(ra):
        s = plan.build_plan(inputs(ref, ra=ra)).schedule
        return np.average(s["day"], weights=s["buy_fx"])
    assert avg_day(20) < avg_day(3) < avg_day(0.2)


def test_no_purchase_after_settlement_cutoff(ref):
    s = plan.build_plan(inputs(ref)).schedule
    assert s.loc[s["status"] == "Settlement", "buy_fx"].sum() == 0


def test_partial_cover_leaves_residual(ref):
    r = plan.build_plan(inputs(ref, cover=0.5))
    assert r.residual_fx == pytest.approx(34_905.5 / 2, abs=1)


def test_rate_index_learns_reliability(ref):
    eri = ref["eri"]
    fast = eri[(eri.dealer_id == "NG-F1") & (eri.bucket == "<10k")].on_time_p.item()
    slow = eri[(eri.dealer_id == "NG-B1") & (eri.bucket == "<10k")].on_time_p.item()
    assert fast > slow
    assert (eri["exec_bps"] >= eri["quoted_bps"]).all()  # executed is never better than quoted here


def test_tight_due_date_penalises_slow_dealers(ref):
    def cost(slack, dealer):
        ctx = routing.RouteContext(mid=1345, slack_days=slack, daily_vol=0.01, margin=0.008)
        s = routing.score_dealers(20_000, ctx, ref["eri"], ref["dealers"], "NG", ref["mk"]["buckets"])
        return s.loc[s.dealer_id == dealer, "all_in_rate"].item()
    gap_relaxed = cost(15, "NG-B3") - cost(15, "NG-F1")
    gap_tight = cost(0, "NG-B3") - cost(0, "NG-F1")
    assert gap_tight > gap_relaxed


def test_waterfall_adds_up(ref):
    p = inputs(ref)
    r = plan.build_plan(p)
    legs = routing.route_plan(r.schedule, p.scenario.days, p.scenario, ref["eri"], ref["dealers"], ref["ng"],
                              "NG", ref["mk"]["buckets"], margin=0.008)
    wf = routing.cost_waterfall(legs)
    parts = sum(v for k, v in wf.items() if k != "All-in cost")
    assert parts == pytest.approx(wf["All-in cost"], rel=1e-9)
    assert legs["amount_fx"].sum() == pytest.approx(r.planned_fx, abs=1)
    day_total = legs.groupby("day")["amount_fx"].sum()
    per_dealer = legs.groupby(["day", "dealer_id"])["amount_fx"].sum()
    share = per_dealer / day_total.reindex(per_dealer.index.get_level_values("day")).to_numpy()
    big = day_total.reindex(per_dealer.index.get_level_values("day")).to_numpy() > 20_000
    assert (share[big] <= 0.6 + 1e-9).all()


def test_large_ticket_spread_across_dealers(ref):
    ctx = routing.RouteContext(mid=1345, slack_days=10, daily_vol=0.01, margin=0.008)
    legs = routing.route_ticket(30_500, ctx, ref["eri"], ref["dealers"], "NG", ref["mk"]["buckets"], max_share=0.6)
    assert legs["amount_fx"].sum() == pytest.approx(30_500, abs=1)
    assert legs.groupby("dealer_id")["amount_fx"].sum().max() <= 0.6 * 30_500 + 1e-6
    assert legs["dealer_id"].nunique() >= 2


def test_protection_pays_in_a_shock(ref):
    p = inputs(ref)
    r = plan.build_plan(p)
    cm = savings.CostModel(exec_cost=0.019, baseline_exec_cost=0.02, parallel_premium=0.037,
                           local_rate=0.18, usd_rate=0.02, impact_per_fx=plan.impact_coefficient(p))
    out = savings.shock_savings(r.schedule["buy_fx"].to_numpy(), p.amount_fx,
                                market.shock_path(p.scenario, 0.30, 12), cm)
    assert out["savings_pct"]["Unprotected (bank)"] > 0.15


def test_implied_drift_matches_rate_gap(ref):
    sc = market.scenario_from_history(ref["wide"]["USD/NGN"], 252, rate_gap=0.16)
    growth = market.expected_path(sc)[-1] / sc.spot - 1
    assert growth == pytest.approx(np.exp(0.16) - 1, rel=1e-6)
    sims = market.simulate_paths(sc, 40_000, seed=3)[:, -1].mean() / sc.spot - 1
    assert sims == pytest.approx(growth, abs=0.02)


def test_sample_invoice_round_trip():
    inv = invoice.sample_invoice(date(2026, 11, 7))
    pdf = invoice.parse_invoice(invoice.build_pdf(inv), "x.pdf")
    csv = invoice.parse_invoice(invoice.build_csv(inv), "x.csv")
    for parsed in (pdf, csv):
        assert parsed["balance"][0] == pytest.approx(inv.balance)
        assert parsed["balance_due"][0] == inv.balance_due
        assert parsed["currency"][0] == "USD"


def test_fx_crosses_consistent(ref):
    w = ref["wide"].dropna()
    implied = w["USD/NGN"] / w["USD/CNY"] if "USD/CNY" in w else None
    assert implied is None or np.allclose(implied, w["CNY/NGN"])
    assert set(fx.NGN_PAIRS) <= set(w.columns)
    assert (w["USD/NGN"].pct_change().dropna().abs() < 0.5).all()
