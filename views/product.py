"""Product & Technology: customer-facing feature guide, with a live demo of each feature."""

import numpy as np
import streamlit as st

from engine import market, plan, routing
from services import workspace
from ui import charts
from ui import components as ui
from views import PAGES

FEATURES = ["Smart FX Planning Engine", "Risk-Weighted Dealer Routing", "Executable FX Rate Index"]


def render() -> None:
    ui.header("Product & Technology",
              "TradeAhead plans how much currency to buy and when, chooses who to buy it from, and pays your "
              "supplier. Licensed banks and payment partners hold and move the money.")

    ui.html("""<div class="ta-flow">
      <div class="ta-card"><span class="n">1</span><h3>Upload the invoice</h3><p>Amount, currency, terms and shipping dates are read from the PDF or CSV.</p></div>
      <div class="ta-card"><span class="n">2</span><h3>Get a protection plan</h3><p>Daily purchase amounts that stay within your cash buffer.</p></div>
      <div class="ta-card"><span class="n">3</span><h3>Route each purchase</h3><p>Best rate after accounting for each dealer's delivery record.</p></div>
      <div class="ta-card"><span class="n">4</span><h3>Pay the supplier</h3><p>Held at a licensed partner, released on the bill of lading.</p></div>
    </div>""")

    ui.section("Features", "Choose a feature to see what it does, with a live example on today's rates.")
    choice = st.segmented_control("Feature", FEATURES, default=FEATURES[0], key="tech_el",
                                  label_visibility="collapsed")
    ref = workspace.reference()
    wide, _ = workspace.fx_history()

    if choice == FEATURES[1]:
        _routing_demo(ref, wide)
    elif choice == FEATURES[2]:
        _eri_demo(ref)
    else:
        _plan_demo(wide)

    ui.section("Who does what")
    ui.grid([
        ui.card("TradeAhead", "Reads your invoices, builds and updates the protection plan, chooses the dealer "
                "for each purchase and sends the payment instructions."),
        ui.card("Licensed partners", "Banks and licensed FX and payment providers hold your local currency and "
                "dollars, carry out each purchase and pay your supplier."),
        ui.card("You", "Set your cash buffer and how much of each invoice to protect, approve the plan, and "
                "stay in control of every payment."),
    ], min_width=260)

    st.write("")
    if st.button("Try it with your own invoice", type="primary"):
        st.switch_page(PAGES["dashboard"])


def _intro(what: str, benefits: list[str]) -> None:
    items = "".join(f"<li>{b}</li>" for b in benefits)
    ui.html(f"""<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:1.4rem;
      margin:.6rem 0 1rem"><p style="margin:0">{what}</p>
      <div><b style="color:var(--ink)">What you get</b><ul style="margin:.3rem 0 0;padding-left:1.1rem">{items}</ul></div>
      </div>""")


def _plan_demo(wide) -> None:
    _intro("Sets how many dollars to buy on each trading day before your payment is due. Buying early removes "
           "exchange-rate risk but gives up interest on your local-currency deposits, and buying everything in "
           "one day gets a worse rate. The plan balances the three, and <b>never lets your cash fall below the "
           "buffer you set</b>, even if the currency weakens sharply while it runs.",
           ["A day-by-day purchase schedule you can approve",
            "Most of the exchange-rate risk removed before the due date",
            "A clear warning when your cash can't cover the whole invoice"])
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
        {"label": "Status", "value": ui.status_pill(res.status), "sub": "Example: $40,000 due in 15 trading days"},
        {"label": "Protected", "value": ui.money(res.planned_fx, "USD"), "sub": f"target {ui.money(res.target_fx, 'USD')}"},
        {"label": "Risk removed", "value": ui.pct(res.risk_reduction, decimals=0), "sub": "of the cost uncertainty",
         "highlight": True},
        {"label": "Interest given up", "value": ui.money(res.carry_cost_local, "NGN", short=True), "sub": "the price of buying early"},
    ])
    l, r = st.columns(2)
    l.plotly_chart(charts.accumulation(res.schedule, res.target_fx, 40_000, "USD", height=290),
                   config=charts.CONFIG, theme=None, width="stretch")
    r.plotly_chart(charts.cash_vs_buffer(res.schedule, buffer * 1e6, "NGN", height=290),
                   config=charts.CONFIG, theme=None, width="stretch")


def _routing_demo(ref, wide) -> None:
    _intro("Ranks dealers by what a dollar really costs you once delivery is taken into account: the rate "
           "actually executed, our margin, and the expected cost if the dealer delivers late or only part of "
           "the amount. The closer your payment date, the more a slow dealer costs, so <b>the cheapest quote "
           "is often not the best route</b>.",
           ["Purchases placed with dealers that deliver on time",
            "Large purchases split so no single dealer carries more than 60%",
            "A breakdown of every cost in each conversion"])
    a, b = st.columns(2)
    slack = a.slider("Trading days until the supplier must be paid", 0, 15, 3, key="rd_slack")
    ticket = b.select_slider("Purchase size (USD)", [2_000, 5_000, 15_000, 30_000, 45_000], value=15_000,
                             key="rd_ticket")
    sc = market.scenario_from_history(wide["USD/NGN"], 15, rate_gap=0.16)
    ctx = routing.RouteContext(mid=sc.spot, slack_days=slack, daily_vol=float(np.sqrt(sc.total_var)),
                               margin=0.008, late_cost_per_day=ref["markets"]["NG"]["late_cost_per_day"])
    ranked = routing.score_dealers(ticket, ctx, ref["eri"], ref["dealers"], "NG", ref["buckets"])
    st.plotly_chart(charts.rank_slope(ranked), config=charts.CONFIG, theme=None, width="stretch")


def _eri_demo(ref) -> None:
    _intro("Screen rates are not what a business actually gets. The index learns from every completed trade, "
           "by dealer and purchase size: the rate actually executed, and how often the full amount arrived on "
           "time. Recent trades count more, and dealers with little history are treated cautiously.",
           ["Rates you can actually execute, not just quoted rates",
            "Each dealer's on-time delivery record by purchase size",
            "A realistic worst case for every dealer"])
    names = {d["id"]: d["name"] for d in ref["dealers"]}
    eri = ref["eri"][ref["eri"]["dealer_id"].str.startswith("NG")]
    order = [b[2] for b in ref["buckets"]]
    st.plotly_chart(charts.eri_chart(eri, names, order), config=charts.CONFIG, theme=None, width="stretch")
    e = eri.assign(o=eri["bucket"].map({b: i for i, b in enumerate(order)})).sort_values(["dealer_id", "o"])
    ui.table([("Dealer", ""), ("Purchase size", ""), ("Quoted", "r"), ("Executed", "r"), ("Slippage", "r"),
              ("On time", ""), ("Worst case (5%)", "r"), ("Trades", "r")],
             [[names[r.dealer_id], r.bucket, f"{r.quoted_bps:.0f} bps", f"<b>{r.exec_bps:.0f} bps</b>",
               f"+{r.slippage_bps:.0f} bps", f"{ui.bar(r.on_time_p)}{r.on_time_p:.0%}", f"{r.on_time_p05:.0%}",
               f"{r.n_fills}"] for r in e.itertuples()])
    ui.html('<p class="ta-muted">Dealer names and trade history are simulated for this demo.</p>')
