"""Company: landing page for importers.

Hero with a live sample plan, the problem in three numbers, what TradeAhead gives you,
a devaluation simulator and the next step. Every number comes from live rates or the
same engine the Dashboard uses.
"""

from datetime import date

import pandas as pd
import streamlit as st

from services import fx, workspace
from services.workspace import Sim
from ui import charts
from ui import components as ui
from views import PAGES

SAMPLE = {"amount": 40_000.0, "days": 15, "cash": 60e6, "buffer": 18e6, "inflow": 2.5e6, "outflow": 1.2e6}


# ---------------------------------------------------------------- data

@st.cache_data(ttl=3600, show_spinner=False)
def _sample(day: date) -> dict:
    """The sample invoice run through the real engine at today's rate."""
    start = workspace.plan_start(day)
    due = (pd.Timestamp(start) + pd.offsets.BDay(SAMPLE["days"])).date()
    res = workspace.run("NG", "USD", SAMPLE["amount"], due, SAMPLE["cash"], SAMPLE["buffer"],
                        SAMPLE["inflow"], SAMPLE["outflow"], Sim())
    p, sched = res["plan"], res["plan"].schedule
    return {
        "due": due, "protected": p.planned_fx / SAMPLE["amount"], "risk_cut": float(p.risk_reduction),
        "saved": float(res["compare"]["savings"]["Unprotected (parallel market)"]["mean"]),
        "coverage": sched["coverage"].tolist(), "buys": sched.loc[sched["buy_fx"] > 0, ["date", "buy_fx"]],
        "lowest_cash": float(sched["cash_after"].min()), "ranking": res["ranking"],
    }


def _mini_bars(values: list[float], cls: str) -> str:
    top = max(values) if values else 1
    return f'<div class="{cls}">' + "".join(
        f'<i style="height:{max(v / top, 0.04) * 100:.0f}%"></i>' for v in values) + "</div>"


# ---------------------------------------------------------------- page

def render() -> None:
    v, chg, as_of = workspace.latest("USD/NGN")
    wide, source = workspace.fx_history()
    ref = workspace.reference()
    sample = _sample(date.today())

    # ---------------------------------------------------------------- hero
    left, right = st.columns([1.1, 1], gap="large", vertical_alignment="center")
    with left:
        ui.html("""<div class="ta-hero">
          <div class="ta-kicker">Now piloting with importers in Nigeria</div>
          <h1>Pay foreign suppliers on time, at a cost you can plan.</h1>
          <p>We buy your currency day by day within your cash buffer, and pay your supplier when the goods ship.</p>
        </div>""")
        st.write("")
        c1, c2, _ = st.columns([1.1, 1, 0.6])
        if c1.button("Open the dashboard", type="primary", width="stretch"):
            st.switch_page(PAGES["dashboard"])
        if c2.button("How it works", width="stretch"):
            st.switch_page(PAGES["product"])
    with right:
        ui.html(_live_card(sample, v, chg, as_of, source))

    # ---------------------------------------------------------------- problem, in three numbers
    s = wide["USD/NGN"].dropna()
    worst = fx.worst_moves(s, 30, 1)
    jump, when = (float(worst["move"].iloc[0]), f"{worst['start'].iloc[0]:%b %Y}") if not worst.empty else (0.0, "")
    fills = ref["fills"][ref["fills"]["dealer_id"].str.startswith("NG")]
    late = 1 - fills["on_time"].astype(float).mean()
    ui.section("Your supplier price is fixed. Your cost isn't.",
               "Your revenue arrives weeks later, in naira.")
    tiles = [("Timing", f"+{jump:.0%}", "Devaluations arrive overnight", f"Biggest 30-day jump in the dollar, {when}"),
             ("Cash", ui.money(SAMPLE["amount"] * v, "NGN", short=True), "Converting early drains cash",
              f"To pre-buy a {ui.money(SAMPLE['amount'], 'USD')} invoice today"),
             ("Delivery", f"{late:.0%}", "Cheap quotes don't always arrive", "Of dealer purchases late or partly filled")]
    ui.html('<div class="ta-stats">' + "".join(
        f'<div><span class="k">{k}</span><b>{num}</b><h3>{t}</h3><p>{note}</p></div>'
        for k, num, t, note in tiles) + "</div>")

    # ---------------------------------------------------------------- what you get
    ui.section("What you get with TradeAhead")
    ui.html(_benefits(sample))

    # ---------------------------------------------------------------- volatility simulator
    ui.section("See what a devaluation does to one invoice",
               "Pick a currency, the day you ordered and the day you paid.")
    with st.container(key="card_fxsim"):
        a, b, c, d = st.columns([1.2, 1, 1, 1])
        pair = a.selectbox("Currency pair", fx.NGN_PAIRS, index=0, key="hist_pair")
        base, quote = pair.split("/")
        s = wide[pair].dropna()
        amount = b.number_input(f"Invoice amount ({base})", min_value=1_000, value=50_000, step=5_000,
                                key="hist_amt")
        order = c.date_input("Ordered on", value=date(2024, 1, 2), min_value=s.index.min().date(),
                             max_value=s.index.max().date(), key="hist_order")
        pay = d.date_input("Balance paid on", value=date(2024, 3, 1), min_value=s.index.min().date(),
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
        moves = fx.worst_moves(s, 30, 3)
        if not moves.empty:
            items = " · ".join(f"{r.start:%b %Y}: <b>{r.move:+.0%}</b>" for r in moves.itertuples())
            ui.html(f'<p class="ta-muted">Largest 30-day moves in {pair}: {items}. Source: {source}.</p>')

    # ---------------------------------------------------------------- call to action
    st.write("")
    with st.container(key="cta"):
        l, r = st.columns([2.2, 1], vertical_alignment="center")
        l.html("""<div><div class="ta-kicker">Nigeria pilot</div>
            <h2 style="font-size:24px;margin:0 0 .35rem">Try it on your next invoice</h2>
            <p class="ta-muted" style="font-size:16px;margin:0">Dollars, yuan, euros, pounds or Canadian dollars.
            Upload an invoice or use our sample.</p></div>""")
        if r.button("Open the dashboard", type="primary", width="stretch", key="open_dash_bottom"):
            st.switch_page(PAGES["dashboard"])


# ---------------------------------------------------------------- pieces

def _live_card(sample, v, chg, as_of, source) -> str:
    buys = sample["buys"]
    first = buys.iloc[0] if not buys.empty else None
    first_txt = f", first {ui.money(first.buy_fx, 'USD')} on {pd.Timestamp(first.date):%a %d %b}" if first is not None else ""
    safe = sample["lowest_cash"] >= SAMPLE["buffer"]
    return f"""<div class="ta-live">
      <div class="ta-live__top"><span class="ta-live__label">Sample invoice · Shenzhen supplier</span>
        {ui.pill("Buffer safe", "ok") if safe else ui.pill("Below buffer", "warn")}</div>
      <div class="ta-live__amt">{ui.money(SAMPLE['amount'], 'USD')}<span>due {sample['due']:%d %b}</span></div>
      <div class="ta-live__row"><span>Protected</span><b>{sample['protected']:.0%}</b></div>
      <div class="ta-live__track"><i style="width:{sample['protected'] * 100:.0f}%"></i></div>
      {_mini_bars(buys['buy_fx'].tolist(), 'ta-live__bars')}
      <div class="ta-live__caption">{len(buys)} daily purchases{first_txt}</div>
      <div class="ta-live__stats">
        <div><span>Saved vs. parallel market</span><b class="pos">{ui.money(sample['saved'], 'NGN', short=True)}</b></div>
        <div><span>Exchange-rate risk removed</span><b>{sample['risk_cut']:.0%}</b></div>
      </div>
      <div class="ta-live__foot">{ui.ticker('USD/NGN', v, chg, 'live' if 'live' in source else source.lower(), as_of)}
        <span>Sample plan at today's rate</span></div>
    </div>"""


def _benefits(sample) -> str:
    cov = sample["coverage"]
    lo, buf, top = sample["lowest_cash"], SAMPLE["buffer"], SAMPLE["cash"]
    rank = sample["ranking"].head(3) if not sample["ranking"].empty else pd.DataFrame()
    dealers = "".join(
        f'<div class="ta-b__dealer{" best" if r.route_rank == 1 else ""}"><b>{r.dealer}</b>'
        f'<span>Quote #{r.screen_rank}</span><span>{r.on_time_p:.0%} on time</span><em>#{r.route_rank}</em></div>'
        for r in rank.itertuples())
    return f"""<div class="ta-bento">
      <div class="ta-b ta-b--wide ta-b--ink">
        {ui.icon('shield')}
        <h3>Predictable payments</h3>
        <p>Small purchases every trading day, so you know the cost before the due date.</p>
        {_mini_bars([max(c, 0.02) for c in cov], 'ta-b__steps')}
        <div class="ta-b__axis"><span>Today</span><span>{cov[-1]:.0%} protected by the due date</span></div>
      </div>
      <div class="ta-b">
        {ui.icon('spot')}
        <div class="ta-b__big">0</div>
        <h3>Derivatives</h3>
        <p>Only buy what you owe. No forwards, options or margin calls.</p>
      </div>
      <div class="ta-b">
        {ui.icon('buffer')}
        <h3>Protected cash flow</h3>
        <p>The buffer for rent, payroll and stock is never touched.</p>
        <div class="ta-b__gauge"><span class="zone" style="width:{buf / top * 100:.0f}%"></span>
          <span class="mark" style="left:{min(lo / top, 1) * 100:.0f}%"></span></div>
        <div class="ta-b__axis"><span>Buffer {ui.money(buf, 'NGN', short=True)}</span>
          <span>Lowest cash {ui.money(lo, 'NGN', short=True)}</span></div>
      </div>
      <div class="ta-b ta-b--wide">
        {ui.icon('route')}
        <h3>On-time delivery</h3>
        <p>Dealers ranked by what arrives on time, not by the screen rate.</p>
        <div class="ta-b__dealers">{dealers}</div>
      </div>
    </div>"""
