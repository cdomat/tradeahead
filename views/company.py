"""Company: landing page for importers. Problem, benefits, how it works, and a live FX shock simulator."""

from datetime import date

import pandas as pd
import streamlit as st

from engine import market, plan
from services import fx, workspace
from ui import charts
from ui import components as ui
from views import PAGES

# Small line icons (stroke follows the text colour).
_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' \
       'stroke-linecap="round" stroke-linejoin="round">{}</svg>'
ICONS = {
    "calendar": _SVG.format('<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/>'),
    "shield": _SVG.format('<path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>'),
    "wallet": _SVG.format('<rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M16 15h2"/>'),
}


@st.cache_data(ttl=3600, show_spinner=False)
def _preview_plan(spot_key: float) -> tuple[pd.DataFrame, float]:
    """A sample plan at today's USD/NGN rate: $40,000 due in 15 trading days, ₦18m buffer."""
    wide, _ = workspace.fx_history()
    sc = market.scenario_from_history(wide["USD/NGN"], 15, rate_gap=0.16)
    res = plan.build_plan(plan.PlanInputs(
        amount_fx=40_000, cover_ratio=1.0, scenario=sc, opening_cash=60e6, cash_buffer=18e6,
        daily_inflow=2.5e6, daily_outflow=1.2e6, exec_cost=0.02, risk_aversion=3.0, max_daily_fx=60_000))
    buys = res.schedule[res.schedule["buy_fx"] > 0][["date", "buy_fx", "expected_rate"]]
    return buys, float(res.risk_reduction)


def _preview_card() -> str:
    v, _, _ = workspace.latest("USD/NGN")
    buys, risk_cut = _preview_plan(round(v, 0))
    rows = "".join(f'<tr><td>{pd.Timestamp(r.date):%a %d %b}</td><td class="r">{ui.money(r.buy_fx, "USD")}</td>'
                   f'<td class="r">{ui.status_pill("Scheduled")}</td></tr>' for r in buys.head(5).itertuples())
    more = len(buys) - 5
    if more > 0:
        rows += f'<tr><td class="ta-muted" colspan="3">+ {more} more purchases</td></tr>'
    return f"""<div class="ta-card ta-preview">
      <h4>Sample supplier invoice</h4>
      <div class="amt">$40,000 <span class="ta-muted">due in 15 trading days</span></div>
      <table>{rows}</table>
      <div class="foot">{risk_cut:.0%} of the exchange-rate risk removed. ₦18m cash buffer never touched.
      Sample plan at today's rate of ₦{v:,.0f} per dollar.</div></div>"""


def render() -> None:
    # ---------------------------------------------------------------- hero
    left, right = st.columns([1.15, 1], gap="large", vertical_alignment="center")
    with left:
        ui.html("""<div class="ta-hero">
          <h1>Pay foreign suppliers on time, at a cost you can plan.</h1>
          <p>TradeAhead turns each supplier invoice into a daily currency-buying plan your cash can afford,
          buys through the dealers most likely to deliver, and pays your supplier when the goods ship.</p>
        </div>""")
        st.write("")
        c1, c2, _ = st.columns([1.1, 1, 0.6])
        if c1.button("Open the dashboard", type="primary", width="stretch"):
            st.switch_page(PAGES["dashboard"])
        if c2.button("How it works", width="stretch"):
            st.switch_page(PAGES["product"])
        ui.html("""<div class="ta-trust"><span>No derivatives</span><span>Funds held by licensed partners</span>
          <span>Free to start</span></div>""")
    with right:
        ui.html(_preview_card())

    # ---------------------------------------------------------------- problem
    ui.section("Your supplier price is fixed. Your cost isn't.",
               "You pay a deposit when you order and the balance before shipping, while your revenue arrives "
               "weeks later in local currency. If the currency falls in between, the same invoice costs far more. "
               "A $50,000 order placed in May 2023 cost ₦23m; paid that December, it cost ₦45m.")
    ui.grid([
        ui.card("Devaluations arrive overnight",
                "One announcement can move the rate 30% or more, long after the order price was agreed."),
        ui.card("Cash is tied up in the business",
                "Converting the whole balance early protects the price but drains the cash you need to operate."),
        ui.card("Cheap quotes don't always deliver",
                "Dealers that fill late or only partly force a rushed purchase at a worse rate, and a late supplier payment."),
    ], min_width=260)

    # ---------------------------------------------------------------- benefits
    ui.section("What TradeAhead gives you")
    ui.grid([
        ui.card("Predictable payments",
                "Know in advance how many dollars you'll buy each day and what the invoice will cost, "
                "instead of finding out on the due date.", icon=ICONS["calendar"]),
        ui.card("No derivative risk",
                "No forwards, options or margin calls. You only ever buy the currency you owe, through "
                "licensed dealers, for an invoice you already have.", icon=ICONS["shield"]),
        ui.card("Protected cash flow",
                "Every plan respects the minimum cash balance you set, even if the currency weakens "
                "sharply while it runs.", icon=ICONS["wallet"]),
    ], min_width=260)

    # ---------------------------------------------------------------- how it works
    ui.section("How it works", "From supplier invoice to supplier paid, in four steps.")
    ui.html("""<div class="ta-flow">
      <div class="ta-card"><span class="n">1</span><h3>Upload the invoice</h3><p>We read the amount, currency, terms and shipping dates from your PDF or CSV.</p></div>
      <div class="ta-card"><span class="n">2</span><h3>Approve your plan</h3><p>Daily purchase amounts that fit your cash and remove most of the exchange-rate risk.</p></div>
      <div class="ta-card"><span class="n">3</span><h3>We buy the currency</h3><p>Each purchase goes to the dealer with the best rate after delivery reliability.</p></div>
      <div class="ta-card"><span class="n">4</span><h3>Your supplier is paid</h3><p>Funds wait at a licensed partner and are released on the bill of lading.</p></div>
    </div>""")

    # ---------------------------------------------------------------- volatility simulator
    ui.section("See what a devaluation does to one invoice",
               "Pick a currency pair and two dates to see how much the same invoice cost when it was paid. "
               "Rates are live from Yahoo Finance.")
    wide, source = workspace.fx_history()
    ref = workspace.reference()
    pairs = fx.NGN_PAIRS + [p for p in fx.EGP_PAIRS if p.startswith("USD")]
    with st.container(key="card_fxsim"):
        a, b, c, d = st.columns([1.2, 1, 1, 1])
        pair = a.selectbox("Currency pair", pairs, index=0, key="hist_pair")
        base, quote = pair.split("/")
        s = wide[pair].dropna()
        amount = b.number_input(f"Invoice amount ({base})", min_value=1_000, value=50_000, step=5_000,
                                key="hist_amt")
        order = c.date_input("Ordered on", value=date(2023, 5, 15), min_value=s.index.min().date(),
                             max_value=s.index.max().date(), key="hist_order")
        pay = d.date_input("Balance paid on", value=date(2023, 12, 29), min_value=s.index.min().date(),
                           max_value=s.index.max().date(), key="hist_pay")
        r0, r1 = float(s.asof(pd.Timestamp(order))), float(s.asof(pd.Timestamp(pay)))
        cost0, cost1 = amount * r0, amount * r1
        ui.kpis([
            {"label": f"Cost when ordered ({order:%d %b %Y})", "value": ui.money(cost0, quote, short=True),
             "sub": f"at {ui.rate(r0)} {quote} per {base}"},
            {"label": f"Cost when paid ({pay:%d %b %Y})", "value": ui.money(cost1, quote, short=True),
             "sub": f"at {ui.rate(r1)} {quote} per {base}"},
            {"label": "Extra cost of waiting", "value": ui.money(cost1 - cost0, quote, short=True),
             "sub": ui.pct(cost1 / cost0 - 1, signed=True) + " on the same invoice",
             "tone": "neg" if cost1 > cost0 else "pos", "highlight": True},
        ])
        st.plotly_chart(charts.fx_history(s, pair, ref["events"]), config=charts.CONFIG, theme=None, width="stretch")
        worst = fx.worst_moves(s, 30, 3)
        if not worst.empty:
            items = " · ".join(f"{r.start:%b %Y}: <b>{r.move:+.0%}</b>" for r in worst.itertuples())
            ui.html(f'<p class="ta-muted">Largest 30-day moves in {pair}: {items}. Source: {source}.</p>')

    # ---------------------------------------------------------------- coverage + call to action
    ui.section("Built for importers",
               "Available for Nigerian importers paying in US dollars, Chinese yuan, euros, pounds and "
               "Canadian dollars, and for Egyptian importers paying in dollars, yuan and euros.")
    st.write("")
    with st.container(key="cta"):
        l, r = st.columns([2.2, 1], vertical_alignment="center")
        l.html("""<div><h2 style="font-size:24px;margin:0 0 .35rem">Try it on your next invoice</h2>
            <p class="ta-muted" style="font-size:16px;margin:0">Download a sample invoice or upload your own.
            No sign-up needed to see your plan.</p></div>""")
        if r.button("Open the dashboard", type="primary", width="stretch", key="open_dash_bottom"):
            st.switch_page(PAGES["dashboard"])
