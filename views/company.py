"""Company: mission, the problem, live FX history with past shocks, value proposition, market."""

from datetime import date

import pandas as pd
import streamlit as st

from services import fx, workspace
from ui import charts
from ui import components as ui
from views import PAGES


def render() -> None:
    ui.html("""
    <div class="ta-hero">
      <div class="ta-eyebrow">FX protection for importing SMEs · Nigeria first</div>
      <h1>Pay foreign suppliers <em>without the devaluation shock.</em></h1>
      <p>TradeAhead plans, protects and pays an importer's dollar and yuan invoices within the cash the
      business actually has. No derivatives, no onboarding fee.</p>
    </div>""")
    st.write("")
    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button("Open the dashboard →", type="primary", width="stretch"):
        st.switch_page(PAGES["dashboard"])
    if c2.button("See how it works", width="stretch"):
        st.switch_page(PAGES["product"])

    # ---------------------------------------------------------------- problem
    ui.section("The problem", "Same supplier price, twice the cost",
               "Importers pay a deposit when they order and the balance before shipping, while their revenue "
               "arrives weeks later in local currency. When the currency falls in between, the invoice "
               "doesn't change, but the bill does.")
    ui.kpis([
        {"label": "$50,000 order, May vs. Dec 2023", "value": "₦23m → ₦45m", "sub": "₦461 → ₦900 per dollar", "ink": True},
        {"label": "Dollar cost in Egyptian pounds", "value": "+19%", "sub": "Feb–Oct 2026"},
        {"label": "Nigerian manufacturers with official FX access", "value": "< 50%", "sub": "Q3 2025"},
        {"label": "West African banks rejecting SMEs more often", "value": "72%", "sub": "for trade finance"},
    ])

    # ---------------------------------------------------------------- volatility simulator
    ui.section("Historical FX volatility", "What a shock does to one invoice",
               "Live rates from Yahoo Finance. Pick a currency pair and the date you ordered, and see what "
               "the same invoice cost when it was paid.")
    wide, source = workspace.fx_history()
    ref = workspace.reference()
    pairs = fx.NGN_PAIRS + [p for p in fx.EGP_PAIRS if p.startswith("USD")]
    with st.container(key="card_fxsim"):
        a, b, c, d = st.columns([1.2, 1, 1, 1])
        pair = a.selectbox("Currency pair", pairs, index=0, key="hist_pair",
                           help="Nigeria pilot pairs, plus USD/EGP for the Egypt expansion.")
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
             "tone": "neg" if cost1 > cost0 else "pos", "ink": True},
        ])
        st.plotly_chart(charts.fx_history(s, pair, ref["events"]), config=charts.CONFIG, theme=None, width="stretch")
        worst = fx.worst_moves(s, 30, 3)
        if not worst.empty:
            items = " · ".join(f"{r.start:%b %Y}: <b>{r.move:+.0%}</b>" for r in worst.itertuples())
            ui.html(f'<p class="ta-muted">Largest 30-day moves in {pair}: {items}. Source: {source}.</p>')

    # ---------------------------------------------------------------- value proposition
    ui.section("Our answer", "Plan, protect and pay within the cash you have")
    ui.grid([
        ui.card("A plan, not a gamble",
                "Upload the supplier invoice. We set out how many dollars to buy each day before the due date, "
                "never touching the cash buffer the business needs.", "Protect"),
        ui.card("Dollars that actually arrive",
                "Each purchase goes to the licensed dealer with the best all-in rate <i>and</i> the highest "
                "chance of delivering the full amount on time.", "Route"),
        ui.card("Paid on the bill of lading",
                "Converted funds wait at a regulated partner and pay the supplier when the shipment "
                "documents arrive.", "Pay"),
        ui.card("Cheaper than the parallel market",
                "Moving from the parallel market to an authorized dealer alone saves up to 3.7% of a "
                "payment, even when the currency is calm.", "Save", ink=True),
    ])

    # ---------------------------------------------------------------- market
    ui.section("Market", "Nigeria to prove it, five markets to scale it",
               "Formal SMEs that import, most of them traders buying from China, which supplied about a third "
               "of Nigeria's imports in 2025.")
    ui.table(
        [("Market", ""), ("Definition", ""), ("Payments a year", "r"), ("Revenue pool", "r")],
        [["<b>Total addressable</b>", "SME import payments in Nigeria, Egypt, Ghana, Kenya, Pakistan", "$30–40bn", "$250–350m"],
         ["<b>Serviceable available</b>", "About 50,000 Nigerian SMEs paying suppliers directly", "$6–10bn", "$60–80m"],
         ["<b>Serviceable obtainable</b>", "3,500 customers in Nigeria and Egypt by year 3", "≈ $0.5bn", "$4.4m revenue"]])
    st.write("")
    ui.steps([("Lagos pilot", "Months 0–6 · first 50 importers", "now"),
              ("Scale Nigeria", "Months 7–24 · Kano, Onitsha, Aba", ""),
              ("Egypt, then Ghana", "Months 25–36 · same engine", ""),
              ("Working capital", "Year 4+ · supplier finance", "")])
    ui.html('<p class="ta-muted">Founded by Carina Domat and Tolulope Oluwabunmi.</p>')
