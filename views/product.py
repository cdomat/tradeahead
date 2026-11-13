"""Product & Technology: the four steps, and a live demo of each part on today's rates."""

import numpy as np
import streamlit as st

from engine import market, plan, routing
from services import workspace
from ui import charts
from ui import components as ui
from views import PAGES
from views.dashboard import WHEN_TO_BUY

FEATURES = ["Protection plan", "Best route", "Real-rate index"]


def render() -> None:
    ui.header("Product & Technology",
              "Four steps from supplier invoice to supplier payment. We plan and route; licensed partners hold "
              "and move the money.")

    steps = [("invoice", "Invoice & cash", "We read amount, currency and dates from your invoice."),
             ("shield", "Your plan", "Daily purchases above your cash buffer. You approve first."),
             ("route", "Best route", "Each purchase goes to the dealer most likely to deliver on time."),
             ("send", "Supplier payment", "Held at a licensed partner, paid when the goods ship.")]
    ui.html('<div class="ta-flow">' + "".join(
        f'<div class="ta-flow__step"><div class="ta-flow__top">{ui.icon(i)}<span>Step {n}</span></div>'
        f'<h3>{t}</h3><p>{body}</p></div>' for n, (i, t, body) in enumerate(steps, 1)) + "</div>")

    ui.section("How each part works", "Choose one to see a live example on today's rates.")
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

    with st.container(key="band_tint_roles"):
        ui.section("Who does what")
        ui.cols([
            ("TradeAhead", "Plans and routes", "Builds your plan, picks the dealer for each purchase and sends "
             "the payment instructions."),
            ("Licensed partners", "Hold and move the money", "Banks and licensed providers hold your funds, buy the "
             "currency and pay your supplier."),
            ("You", "Stay in control", "Set your buffer, approve the plan and see every payment before it goes out."),
        ])

    st.write("")
    with st.container(key="cta"):
        l, r = st.columns([2.2, 1], vertical_alignment="center")
        l.html("""<div><h2 style="font-size:24px;margin:0 0 .35rem">Try it on your next invoice</h2>
            <p class="ta-muted" style="font-size:16px;margin:0">Upload a supplier invoice, or use our sample,
            and see your plan in a minute.</p></div>""")
        if r.button("Open the dashboard", type="primary", width="stretch", key="open_dash_product"):
            st.switch_page(PAGES["dashboard"])


def _intro(what: str, benefits: list[str]) -> None:
    items = "".join(f"<li>{b}</li>" for b in benefits)
    ui.html(f"""<div class="ta-intro"><p>{what}</p>
      <div><div class="ta-kicker">What you get</div><ul>{items}</ul></div></div>""")


def _plan_demo(wide) -> None:
    _intro("Sets how many dollars to buy each trading day before the due date. It weighs exchange-rate risk "
           "against the interest your naira earns, and <b>never lets your cash fall below your buffer</b>.",
           ["A day-by-day purchase list you approve before anything is bought",
            "Most of the exchange-rate risk removed before the due date",
            "A clear warning when your cash can't cover the whole invoice"])
    a, b, c = st.columns(3)
    buffer = a.slider("Cash buffer to keep (₦m)", 5, 40, 18, key="pd_buf")
    cover = b.slider("Share of the balance to protect", 25, 100, 100, step=25, format="%d%%", key="pd_cover")
    when = c.select_slider("When to buy", list(WHEN_TO_BUY), value="Balanced", key="pd_when")
    ra = WHEN_TO_BUY[when]
    sc = market.scenario_from_history(wide["USD/NGN"], 15, rate_gap=0.16)
    res = plan.build_plan(plan.PlanInputs(
        amount_fx=40_000, cover_ratio=cover / 100, scenario=sc, opening_cash=60e6, cash_buffer=buffer * 1e6,
        daily_inflow=2.5e6, daily_outflow=1.2e6, exec_cost=0.02, risk_aversion=ra, max_daily_fx=60_000))
    ui.kpis([
        {"label": "Plan status", "value": ui.status_pill(res.status), "sub": "Example: $40,000 due in 15 trading days"},
        {"label": "Protected before the due date", "value": ui.money(res.planned_fx, "USD"),
         "sub": f"of {ui.money(res.target_fx, 'USD')} you chose to protect"},
        {"label": "Exchange-rate risk removed", "value": ui.pct(res.risk_reduction, decimals=0),
         "sub": "compared with buying everything on the due date", "highlight": True},
        {"label": "Cost of buying early", "value": ui.money(res.carry_cost_local, "NGN", short=True),
         "sub": "interest your naira would have earned"},
    ])
    l, r = st.columns(2)
    l.plotly_chart(charts.accumulation(res.schedule, res.target_fx, 40_000, "USD", height=290),
                   config=charts.CONFIG, theme=None, width="stretch")
    r.plotly_chart(charts.cash_vs_buffer(res.schedule, buffer * 1e6, "NGN", height=290),
                   config=charts.CONFIG, theme=None, width="stretch")


def _routing_demo(ref, wide) -> None:
    _intro("Ranks dealers by what a dollar really costs once late or partial deliveries are counted. Close to "
           "the due date, <b>the cheapest quote is often not the best route</b>.",
           ["Purchases placed with dealers that deliver on time",
            "Large purchases split so no single dealer carries more than 60%",
            "Every part of the conversion cost shown"])
    a, b = st.columns(2)
    slack = a.slider("Trading days until the supplier must be paid", 0, 15, 3, key="rd_slack")
    ticket = b.select_slider("Purchase size (USD)", [2_000, 5_000, 15_000, 30_000, 45_000], value=15_000,
                             key="rd_ticket")
    sc = market.scenario_from_history(wide["USD/NGN"], 15, rate_gap=0.16)
    ctx = routing.RouteContext(mid=sc.spot, slack_days=slack, daily_vol=float(np.sqrt(sc.total_var)),
                               margin=0.008, late_cost_per_day=ref["markets"]["NG"]["late_cost_per_day"])
    ranked = routing.score_dealers(ticket, ctx, ref["eri"], ref["dealers"], "NG", ref["buckets"])
    ui.subhead("Rank by quoted price → rank once delivery is counted")
    st.plotly_chart(charts.rank_slope(ranked), config=charts.CONFIG, theme=None, width="stretch")


def _eri_demo(ref) -> None:
    _intro("Learns from every completed trade what each dealer really charges and how often they deliver on "
           "time, by purchase size. Recent trades count more.",
           ["Rates you can actually get, not just quoted rates",
            "Each dealer's on-time record by purchase size",
            "Plans and routes based on real trades, not promises"])
    names = {d["id"]: d["name"] for d in ref["dealers"]}
    eri = ref["eri"][ref["eri"]["dealer_id"].str.startswith("NG")]
    order = [b[2] for b in ref["buckets"]]
    st.plotly_chart(charts.eri_chart(eri, names, order), config=charts.CONFIG, theme=None, width="stretch")
    e = eri.assign(o=eri["bucket"].map({b: i for i, b in enumerate(order)})).sort_values(["dealer_id", "o"])
    with st.expander("All dealers by purchase size"):
        _eri_table(e, names)
    ui.html('<p class="ta-muted">Dealer names and trade history are simulated for this demo.</p>')


def _eri_table(e, names) -> None:
    ui.table([("Dealer", ""), ("Purchase size (USD)", ""), ("Quoted above mid", "r"), ("Actually paid", "r"),
              ("On time", ""), ("Trades", "r")],
             [[names[r.dealer_id], r.bucket, f"{r.quoted_bps / 100:.2f}%", f"<b>{r.exec_bps / 100:.2f}%</b>",
               f"{ui.bar(r.on_time_p)}{r.on_time_p:.0%}", f"{r.n_fills}"] for r in e.itertuples()])
