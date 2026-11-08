"""Product & Technology: the workflow and the three proprietary elements, each with a live demo."""

import numpy as np
import streamlit as st

from engine import market, plan, routing
from services import workspace
from ui import charts
from ui import components as ui
from views import PAGES

ELEMENTS = ["Cash-constrained protection plan", "Delivery-weighted routing", "Executable Rate Index"]


def render() -> None:
    ui.header("Product & Technology", "From invoice to supplier paid, within the SME's cash",
              "TradeAhead decides how much to convert, when, and through whom. Licensed banks and payment "
              "partners hold and move the money.")

    ui.html("""<div class="ta-flow">
      <div class="ta-card"><span class="n">1</span><h3>Upload the invoice</h3><p>Amount, currency, terms and shipment dates are read from the PDF or CSV.</p></div>
      <div class="ta-card"><span class="n">2</span><h3>Get a protection plan</h3><p>Daily conversion amounts that stay within the cash buffer.</p></div>
      <div class="ta-card"><span class="n">3</span><h3>Route each conversion</h3><p>Best all-in rate with the highest odds of on-time delivery.</p></div>
      <div class="ta-card"><span class="n">4</span><h3>Pay the supplier</h3><p>Held at a regulated partner, released on the bill of lading.</p></div>
    </div>""")
    st.write("")
    ui.grid([
        ui.card("Our layer", "Platform, algorithms and decisions: planning, routing, the rate index, payment "
                "instructions.", "TradeAhead", ink=True),
        ui.card("Licensed partners", "Banks and licensed FX and payment providers hold the naira and dollars, "
                "execute conversions and pay suppliers.", "Execution"),
    ], min_width=320)

    ui.section("Technology", "Three elements that are hard to copy",
               "IP.com mapped 30+ related patents and papers and rated the refined design medium novelty; "
               "none of the documents found covers currency protection for SMEs.")
    choice = st.segmented_control("Element", ELEMENTS, default=ELEMENTS[0], key="tech_el",
                                  label_visibility="collapsed")
    ref = workspace.reference()
    wide, _ = workspace.fx_history()

    if choice == ELEMENTS[0]:
        _plan_demo(wide)
    elif choice == ELEMENTS[1]:
        _routing_demo(ref, wide)
    else:
        _eri_demo(ref)

    st.write("")
    if st.button("Try it with your own invoice →", type="primary"):
        st.switch_page(PAGES["dashboard"])


def _plan_demo(wide) -> None:
    ui.html(f"""<p>{ui.pill('Trade secret', 'info')}</p>
    <p style="max-width:760px">Sets a daily conversion amount for each trading day before the due date. Buying
    early removes exchange-rate risk but gives up interest on naira deposits; buying everything in one day
    gets a worse rate. The plan balances the three, and <b>never lets cash fall below the buffer</b>, even if
    the naira weakens sharply while it runs.</p>""")
    a, b, c = st.columns(3)
    buffer = a.slider("Cash buffer (₦m)", 5, 40, 18, key="pd_buf")
    risk = b.select_slider("Risk appetite", ["Accept risk", "Balanced", "Protect early"], value="Balanced",
                           key="pd_risk")
    cover = c.slider("Cover", 25, 100, 100, step=25, format="%d%%", key="pd_cover")
    ra = {"Accept risk": 0.3, "Balanced": 3.0, "Protect early": 15.0}[risk]
    sc = market.scenario_from_history(wide["USD/NGN"], 15, rate_gap=0.16)
    res = plan.build_plan(plan.PlanInputs(
        amount_fx=40_000, cover_ratio=cover / 100, scenario=sc, opening_cash=60e6, cash_buffer=buffer * 1e6,
        daily_inflow=2.5e6, daily_outflow=1.2e6, exec_cost=0.02, risk_aversion=ra, max_daily_fx=60_000))
    ui.kpis([
        {"label": "Status", "value": ui.status_pill(res.status), "sub": "Illustration: $40,000 due in 15 trading days"},
        {"label": "Protected", "value": ui.money(res.planned_fx, "USD"), "sub": f"target {ui.money(res.target_fx, 'USD')}"},
        {"label": "Risk removed", "value": ui.pct(res.risk_reduction, decimals=0), "sub": "of the cost uncertainty"},
        {"label": "Interest given up", "value": ui.money(res.carry_cost_local, "NGN", short=True), "sub": "the price of buying early"},
    ])
    l, r = st.columns(2)
    l.plotly_chart(charts.accumulation(res.schedule, res.target_fx, 40_000, "USD", height=290),
                   config=charts.CONFIG, theme=None, width="stretch")
    r.plotly_chart(charts.cash_vs_buffer(res.schedule, buffer * 1e6, "NGN", height=290),
                   config=charts.CONFIG, theme=None, width="stretch")


def _routing_demo(ref, wide) -> None:
    ui.html(f"""<p>{ui.pill('Patent candidate', 'brand')}</p>
    <p style="max-width:760px">Ranks dealers by what a dollar really costs once delivery risk is priced in: the
    executed rate, our margin, and the expected cost of a late or partial fill. The closer the payment, the
    more a slow dealer costs, so <b>the cheapest quote is often not the best route</b>.</p>""")
    a, b = st.columns(2)
    slack = a.slider("Trading days until the supplier must be paid", 0, 15, 3, key="rd_slack")
    ticket = b.select_slider("Ticket size (USD)", [2_000, 5_000, 15_000, 30_000, 45_000], value=15_000,
                             key="rd_ticket")
    sc = market.scenario_from_history(wide["USD/NGN"], 15, rate_gap=0.16)
    ctx = routing.RouteContext(mid=sc.spot, slack_days=slack, daily_vol=float(np.sqrt(sc.total_var)),
                               margin=0.008, late_cost_per_day=ref["markets"]["NG"]["late_cost_per_day"])
    ranked = routing.score_dealers(ticket, ctx, ref["eri"], ref["dealers"], "NG", ref["buckets"])
    st.plotly_chart(charts.rank_slope(ranked), config=charts.CONFIG, theme=None, width="stretch")


def _eri_demo(ref) -> None:
    ui.html(f"""<p>{ui.pill('Trade secret', 'info')}</p>
    <p style="max-width:760px">Screen rates are not what an SME gets. The index learns from every real fill, by
    dealer and ticket size: the rate actually executed, and how often the full amount arrived on time. Recent
    fills count more, and dealers with few fills stay close to a cautious starting estimate.</p>""")
    names = {d["id"]: d["name"] for d in ref["dealers"]}
    eri = ref["eri"][ref["eri"]["dealer_id"].str.startswith("NG")]
    order = [b[2] for b in ref["buckets"]]
    st.plotly_chart(charts.eri_chart(eri, names, order), config=charts.CONFIG, theme=None, width="stretch")
    e = eri.assign(o=eri["bucket"].map({b: i for i, b in enumerate(order)})).sort_values(["dealer_id", "o"])
    ui.table([("Dealer", ""), ("Ticket", ""), ("Quoted", "r"), ("Executed", "r"), ("Slippage", "r"),
              ("On time", ""), ("Worst case (5%)", "r"), ("Fills", "r")],
             [[names[r.dealer_id], r.bucket, f"{r.quoted_bps:.0f} bps", f"<b>{r.exec_bps:.0f} bps</b>",
               f"+{r.slippage_bps:.0f} bps", f"{ui.bar(r.on_time_p)}{r.on_time_p:.0%}", f"{r.on_time_p05:.0%}",
               f"{r.n_fills}"] for r in e.itertuples()])
    ui.html('<p class="ta-muted">Dealer names and fills are simulated for the prototype.</p>')
