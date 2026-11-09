"""Dashboard: invoice workbench, protection plan, routing, payments, savings.

Starts blank. Nothing is computed until the user enters an invoice and a cash position.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from services import invoice, workspace
from services.workspace import Sim
from ui import charts
from ui import components as ui

# Markets open to customers. Add a market to data/generate.py, then list it here.
LIVE_MARKETS = {"NG": "Nigeria (pilot)"}

RISK_LEVELS = {"Accept risk": 0.3, "Balanced": 3.0, "Protect early": 10.0, "Maximum protection": 30.0}
DRIFT_LABELS = {"implied": "Market-implied (default)", "historical": "Historical devaluations (stress)",
                "recent": "Recent trend"}
INVOICE_KEYS = ["supplier", "invoice_no", "ccy", "balance", "bl_date", "due_date", "deposit_paid"]
CASH_KEYS = ["cash", "buffer", "inflow", "outflow"]


# ---------------------------------------------------------------- state that survives page changes

def _store() -> dict:
    return st.session_state.setdefault("_dash", {})


def _key(k: str, default=None) -> str:
    """Widget key for a stored value. Widget state is dropped when the user leaves the page,
    so values are copied to a plain dict and restored on return. Defaults are seeded here,
    never passed to the widget, so Streamlit never sees two sources for one value."""
    w = f"w_{k}"
    if w not in st.session_state:
        if k in _store():
            st.session_state[w] = _store()[k]
        elif default is not None:
            st.session_state[w] = default
    return w


def _save(*keys: str) -> None:
    for k in keys:
        if f"w_{k}" in st.session_state:
            _store()[k] = st.session_state[f"w_{k}"]


def _get(k: str, default=None):
    return _store().get(k, default)


# ---------------------------------------------------------------- page

def render() -> None:
    ref = workspace.reference()
    market_code, sim = _sidebar(ref)
    m = ref["markets"][market_code]
    local = m["local"]

    ccy = _get("ccy") or "USD"
    pair = f"{ccy}/{local}" if f"{ccy}/{local}" in m["pairs"] else m["default_pair"]
    v, chg, as_of = workspace.latest(pair)
    _, source = workspace.fx_history()
    ui.header("Protection workspace",
              "Enter a supplier invoice and your cash position to get your protection plan. "
              "You can change the plan settings on the left at any time.",
              right=ui.ticker(pair, v, chg, "live" if "live" in source else source.lower(), as_of))

    tabs = st.tabs(["Invoice & cash", "Your plan", "Dealers", "Payment", "Savings"])
    with tabs[0]:
        _workbench(ref, m)

    inv, cash = _invoice(), _cash()
    res = None
    if inv and cash:
        res = workspace.run(market_code, inv["ccy"], float(inv["balance"]), inv["due_date"], cash["cash"],
                            cash["buffer"], cash["inflow"], cash["outflow"], sim)
    missing = ("an invoice" if not inv else "") + (" and " if not inv and not cash else "") + \
              ("your cash position" if not cash else "")
    for tab, fn in zip(tabs[1:], (_plan_tab, _routing_tab, _payments_tab, _savings_tab)):
        with tab:
            if res is None:
                ui.empty("Add " + missing + " to continue",
                         "Fill in the Invoice & cash tab. Upload a supplier invoice or download the sample "
                         "to try it.")
            elif "error" in res:
                ui.alert(res["error"], "warn")
            else:
                fn(res, inv, cash, ref, m, sim)


# ---------------------------------------------------------------- sidebar: scenario simulator

def _sidebar(ref) -> tuple[str, Sim]:
    sb = st.sidebar
    sb.markdown("### Plan settings")
    if len(LIVE_MARKETS) > 1:
        market_code = sb.radio("Market", list(LIVE_MARKETS), format_func=LIVE_MARKETS.get,
                               key=_key("market", "NG"), horizontal=True)
    else:
        market_code = next(iter(LIVE_MARKETS))
        sb.caption(f"Market: {LIVE_MARKETS[market_code]}")

    cover = sb.slider("How much of the balance to protect", 0, 100, step=5, format="%d%%", key=_key("cover", 100))
    risk = sb.select_slider("Risk appetite", list(RISK_LEVELS), key=_key("risk", "Balanced"),
                            help="More protection buys earlier: less exchange-rate risk, more interest given up.")
    tier = sb.radio("Your TradeAhead plan", list(ref["tiers"]), key=_key("tier", "Growth"), horizontal=True,
                    help="Sets the TradeAhead fee per conversion: Starter 1.0%, Growth 0.8%, Pro 0.6%.")

    sb.markdown('<div class="ta-side-label">What if the naira falls overnight?</div>', unsafe_allow_html=True)
    shock = sb.slider("Sudden fall", 0, 60, step=5, format="%d%%", key=_key("shock", 30))
    shock_day = sb.slider("Trading days from now", 1, 30, key=_key("shock_day", 5))

    with sb.expander("Advanced market assumptions"):
        drift = st.selectbox("Rate outlook", list(DRIFT_LABELS), format_func=DRIFT_LABELS.get,
                             key=_key("drift", "implied"))
        vol = st.slider("Day-to-day volatility", 0.5, 3.0, step=0.25, format="%.2f×", key=_key("vol", 1.0))
        jump = st.slider("How often devaluations happen", 0.0, 4.0, step=0.25, format="%.2f×",
                         key=_key("jump", 1.0), help="Compared with naira history since 2021.")
    _save("market", "cover", "risk", "tier", "drift", "vol", "jump", "shock", "shock_day")

    sb.write("")
    if sb.button("Reset workspace", width="stretch"):
        st.session_state.pop("_dash", None)
        for k in list(st.session_state):
            if k.startswith("w_") or k.startswith("_parsed"):
                del st.session_state[k]
        st.rerun()

    return market_code, Sim(cover=cover / 100, risk_aversion=RISK_LEVELS[risk], vol_mult=vol, jump_mult=jump,
                            drift_mode=drift, tier=tier, shock=shock / 100, shock_day=shock_day)


# ---------------------------------------------------------------- tab 1: workbench

def _workbench(ref, m) -> None:
    local = m["local"]
    currencies = [p.split("/")[0] for p in m["pairs"]]
    left, right = st.columns([1.35, 1], gap="large")

    with left, st.container(key="card_invoice"):
        ui.html('<h3 style="margin:0 0 .6rem;font-size:18px">1. Supplier invoice</h3>')
        up = st.file_uploader("Upload a proforma or commercial invoice (PDF or CSV)", type=["pdf", "csv"],
                              key="uploader")
        pdf, csv_bytes = _sample_files(date.today())
        st.caption("No invoice to hand? Download a sample and upload it above.")
        d1, d2 = st.columns(2)
        d1.download_button("Sample PDF", pdf, "sample_proforma_invoice.pdf",
                           "application/pdf", width="stretch", icon=":material/download:")
        d2.download_button("Sample CSV", csv_bytes, "sample_proforma_invoice.csv",
                           "text/csv", width="stretch", icon=":material/download:")

        conf = _read_upload(up, currencies)
        if conf:
            found = sum(1 for c in conf.values() if c == "high")
            ui.alert(f"Read <b>{found} fields</b> from <b>{up.name}</b>. Check the values below, "
                     f"then fill in anything marked.", "ok")

        a, b = st.columns(2)
        a.text_input("Supplier", key=_key("supplier"), placeholder="e.g. Shenzhen supplier Co., Ltd.")
        b.text_input("Invoice number", key=_key("invoice_no"), placeholder="e.g. PI-2026-0417")
        a, b = st.columns(2)
        a.selectbox("Invoice currency", currencies, index=None, key=_key("ccy"), placeholder="Choose currency",
                    help=f"Currencies we can buy with {local}.")
        b.number_input("Balance still to pay", min_value=0.0, value=None, step=1_000.0, format="%.2f",
                       key=_key("balance"), placeholder="e.g. 34,905.50")
        a, b = st.columns(2)
        a.date_input("Bill of lading expected", value=None, min_value=date.today(), key=_key("bl_date"),
                     help="The payment trigger in most supplier terms (e.g. 'balance against copy of B/L').")
        b.date_input("Balance due date", value=None, min_value=date.today() + timedelta(days=2),
                     key=_key("due_date"))
        st.checkbox("Deposit already paid", key=_key("deposit_paid"))
        _save(*INVOICE_KEYS)
        if conf:
            ui.html("<p class='ta-muted'>" + " ".join(
                f"{k.replace('_', ' ')} {ui.pill('read', 'ok') if c == 'high' else ui.pill('check', 'warn') if c == 'check' else ui.pill('missing', 'risk')}"
                for k, c in conf.items()) + "</p>")

    with right, st.container(key="card_cash"):
        ui.html('<h3 style="margin:0 0 .6rem;font-size:18px">2. Cash position</h3>')
        s = ui.sym(local)
        st.number_input(f"Cash on hand today ({s})", min_value=0.0, value=None, step=1e6, format="%.0f",
                        key=_key("cash"), placeholder="e.g. 60,000,000")
        st.number_input(f"Minimum cash buffer ({s})", min_value=0.0, value=None, step=1e6, format="%.0f",
                        key=_key("buffer"), placeholder="e.g. 18,000,000",
                        help="Cash the business must always keep for stock, wages and rent. The plan never uses it.")
        st.number_input(f"Expected receipts per trading day ({s})", min_value=0.0, value=None, step=1e5,
                        format="%.0f", key=_key("inflow"), placeholder="e.g. 2,500,000")
        st.number_input(f"Other payments per trading day ({s})", min_value=0.0, value=None, step=1e5,
                        format="%.0f", key=_key("outflow"), placeholder="e.g. 1,200,000")
        _save(*CASH_KEYS)

        inv, cash = _invoice(), _cash()
        st.write("")
        ui.html("<p>" + (ui.pill("Invoice complete", "ok") if inv else ui.pill("Invoice incomplete", "warn")) + " " +
                (ui.pill("Cash position complete", "ok") if cash else ui.pill("Cash position incomplete", "warn")) +
                "</p>")
        if inv and cash:
            ui.alert("Your plan is ready. Open <b>Your plan</b> above.", "ok")


@st.cache_data(show_spinner=False)
def _sample_files(day: date) -> tuple[bytes, bytes]:
    """Built once a day, so the download links stay stable across reruns."""
    inv = invoice.sample_invoice(day)
    return invoice.build_pdf(inv), invoice.build_csv(inv)


def _read_upload(up, currencies) -> dict:
    """Parse a new upload once and pre-fill the form. Returns field confidences for the latest upload."""
    if up is None:
        return {}
    if st.session_state.get("_parsed_id") != up.file_id:
        parsed = invoice.parse_invoice(up.getvalue(), up.name)
        fields = {"supplier": "supplier", "invoice_no": "invoice_no", "currency": "ccy", "balance": "balance",
                  "bl_date": "bl_date", "balance_due": "due_date", "deposit_paid": "deposit_paid"}
        conf = {}
        for src, dst in fields.items():
            val, c = parsed.get(src, (None, "missing"))
            if dst == "ccy" and val not in currencies:
                val, c = None, "missing" if val is None else "check"
            if dst in ("bl_date", "due_date") and val is not None and val <= date.today():
                val, c = None, "check"
            if val is not None:
                st.session_state[f"w_{dst}"] = val
                _store()[dst] = val
            conf[dst] = c
        if parsed.get("deposit_pct", (None,))[0] and "deposit_paid" not in parsed:
            st.session_state["w_deposit_paid"] = _store()["deposit_paid"] = True
            conf["deposit_paid"] = "check"
        st.session_state["_parsed_id"] = up.file_id
        st.session_state["_parsed_conf"] = conf
    return st.session_state.get("_parsed_conf", {})


def _invoice() -> dict | None:
    v = {k: _get(k) for k in INVOICE_KEYS}
    if not (v["ccy"] and v["balance"] and v["due_date"]) or v["balance"] <= 0:
        return None
    v["bl_date"] = v["bl_date"] or v["due_date"] - timedelta(days=5)
    v["supplier"] = v["supplier"] or "Supplier"
    return v


def _cash() -> dict | None:
    v = {k: _get(k) for k in CASH_KEYS}
    if v["cash"] is None or v["buffer"] is None:
        return None
    v["inflow"] = v["inflow"] or 0.0
    v["outflow"] = v["outflow"] or 0.0
    return v


# ---------------------------------------------------------------- tab 2: plan

def _plan_tab(res, inv, cash, ref, m, sim) -> None:
    p, ccy, local = res["plan"], res["ccy"], res["local"]
    sched = p.schedule
    ui.kpis([
        {"label": "Plan status", "value": ui.status_pill(p.status),
         "sub": f"{res['n']} trading days to {inv['due_date']:%d %b}", "highlight": True},
        {"label": "Protected before the due date", "value": ui.money(p.planned_fx, ccy),
         "sub": f"{p.planned_fx / float(inv['balance']):.0%} of the balance"},
        {"label": "Exchange-rate risk removed", "value": ui.pct(p.risk_reduction, decimals=0),
         "sub": "compared with buying everything on the due date"},
        {"label": "Cost of buying early", "value": ui.money(p.carry_cost_local, local, short=True),
         "sub": f"interest your {local} would have earned"},
    ])
    if p.coverage_gap_fx > 1:
        c1, c2 = st.columns([1.6, 1])
        with c1:
            ui.alert(f"<b>Coverage gap: {ui.money(p.coverage_gap_fx, ccy)}.</b> Your cash can fund "
                     f"{p.planned_fx / p.target_fx:.0%} of the target without touching your "
                     f"{ui.money(cash['buffer'], local, short=True)} buffer. The rest is bought on the due date "
                     f"at that day's rate.", "warn")
        with c2:
            ui.html(f"""<div class="ta-locked"><span class="ta-muted">Coming soon</span>
            <b>Finance the gap</b><p class="ta-muted" style="margin:.2rem 0 0">A partner lender funds the missing
            {ui.money(p.coverage_gap_fx, ccy)}.</p></div>""")
    if sim.tier == "Starter":
        ui.alert("Protection plans are part of <b>Growth</b> and <b>Pro</b>. On Starter you would convert "
                 "manually; this preview shows what the plan would do.", "info")

    l, r = st.columns(2, gap="large")
    with l:
        ui.subhead(f"{'Dollars' if ccy == 'USD' else ccy} bought over time")
        st.plotly_chart(charts.accumulation(sched, p.target_fx, float(inv["balance"]), ccy),
                        config=charts.CONFIG, theme=None, width="stretch")
    with r:
        ui.subhead("Your cash stays above the buffer")
        st.plotly_chart(charts.cash_vs_buffer(sched, cash["buffer"], local), config=charts.CONFIG, theme=None, width="stretch")

    buys = sched[sched["buy_fx"] > 0]
    ui.subhead("Purchase schedule", f"{len(buys)} purchases")
    ui.table([("Date", ""), (f"Buy ({ccy})", "r"), ("Expected rate", "r"), (f"Cost ({local})", "r"),
              ("Protected so far", ""), ("Cash above buffer", "r")],
             [[f"{pd.Timestamp(r.date):%a %d %b}", ui.money(r.buy_fx, ccy), ui.rate(r.expected_rate),
               ui.money(r.local_needed, local, short=True), f"{ui.bar(r.coverage)}{r.coverage:.0%}",
               ui.money(r.cash_after - r.buffer, local, short=True)] for r in buys.itertuples()])
    if p.residual_fx > 1:
        ui.html(f"<p class='ta-muted'>{ui.money(p.residual_fx, ccy)} is left to buy on the due date at that "
                f"day's rate.</p>")


# ---------------------------------------------------------------- tab 3: dealers

def _routing_tab(res, inv, cash, ref, m, sim) -> None:
    legs, ranking, wf, local = res["legs"], res["ranking"], res["waterfall"], res["local"]
    if legs.empty:
        ui.empty("No purchases to place", "The plan does not buy anything before the due date with these settings.")
        return
    on_time = (legs["amount_fx"] * legs["on_time_p"]).sum() / legs["amount_fx"].sum()
    ui.kpis([
        {"label": "Total conversion cost", "value": ui.pct(res["all_in"], decimals=2),
         "sub": f"above the mid-market rate, incl. {res['margin']:.1%} TradeAhead fee", "highlight": True},
        {"label": "Expected on-time delivery", "value": ui.pct(on_time, decimals=0), "sub": "across all purchases"},
        {"label": "Dealers used", "value": f"{legs['dealer'].nunique()}", "sub": f"{len(legs)} purchases in total"},
    ])

    if not ranking.empty:
        cheapest = ranking.loc[ranking["screen_rank"] == 1].iloc[0]
        best = ranking.loc[ranking["route_rank"] == 1].iloc[0]
        if cheapest["dealer"] != best["dealer"]:
            ui.alert(f"<b>{cheapest['dealer']}</b> quotes the lowest rate, but <b>{best['dealer']}</b> delivers on "
                     f"time more often ({best['on_time_p']:.0%} vs. {cheapest['on_time_p']:.0%}). Once the cost of a "
                     f"late delivery is counted, {best['dealer']} is cheaper, so we use it first.", "info")
        ui.subhead("How we chose the dealer for your first purchase")
        ui.table([("Dealer", ""), ("Quoted rate", "r"), ("On-time record", ""), ("Rank by quote", "c"),
                  ("Our rank", "c")],
                 [[f"<b>{r.dealer}</b>", ui.rate(r.screen_rate), f"{ui.bar(r.on_time_p)}{r.on_time_p:.0%}",
                   f"{r.screen_rank}",
                   f"<span class='ta-rank{' ta-rank--best' if r.route_rank == 1 else ''}'>{r.route_rank}</span>"]
                  for r in ranking.itertuples()],
                 highlight={i for i, r in enumerate(ranking.itertuples()) if r.route_rank == 1})
        ui.html(f"<p class='ta-muted'>Rates in {local} per US dollar.</p>")

    split = legs.groupby("dealer", as_index=False).agg(n=("amount_fx", "size"), usd=("amount_fx", "sum"))
    split = split.sort_values("usd", ascending=False)
    ui.subhead("Where your purchases go")
    ui.table([("Dealer", ""), ("Purchases", "r"), ("Amount (USD)", "r"), ("Share", "")],
             [[r.dealer, f"{r.n}", f"{r.usd:,.0f}", f"{ui.bar(r.usd / split['usd'].sum())}{r.usd / split['usd'].sum():.0%}"]
              for r in split.itertuples()])

    with st.expander("Where the conversion cost comes from"):
        mid = wf["Mid-market cost"]
        parts = [("Dealer spread and slippage", wf["Dealer spread"] + wf["Execution slippage"]),
                 ("TradeAhead fee", wf["TradeAhead margin"]),
                 ("Allowance for late deliveries", wf["Expected delivery cost"])]
        ui.table([("Part", ""), (f"Cost ({local})", "r"), ("Share of mid-market value", "r")],
                 [[n, ui.money(v, local, short=True), ui.pct(v / mid, decimals=2)] for n, v in parts] +
                 [["<b>Total above mid-market</b>", f"<b>{ui.money(wf['All-in cost'] - mid, local, short=True)}</b>",
                   f"<b>{ui.pct((wf['All-in cost'] - mid) / mid, decimals=2)}</b>"]])


# ---------------------------------------------------------------- tab 4: payment

def _payments_tab(res, inv, cash, ref, m, sim) -> None:
    p, ccy = res["plan"], res["ccy"]
    sched, today = p.schedule, date.today()
    bl_key = f"bl_{inv['invoice_no']}_{inv['due_date']}"
    received = st.session_state.get(bl_key, False)
    last_buy = sched.loc[sched["buy_fx"] > 0, "date"].max() if p.planned_fx else None
    converting_done = last_buy is not None and last_buy < today
    ui.steps([
        ("Deposit paid", "with the order" if inv.get("deposit_paid") else "not confirmed",
         "done" if inv.get("deposit_paid") else ""),
        ("Plan approved", f"{res['start']:%d %b}", "done"),
        ("Buying currency", f"until {last_buy:%d %b}" if last_buy is not None else "nothing to buy",
         "done" if converting_done or received else "now"),
        ("Bill of lading received", f"expected {inv['bl_date']:%d %b}", "done" if received else ""),
        ("Supplier paid", f"latest {inv['due_date']:%d %b}", "done" if received else ""),
    ])

    l, r = st.columns([1, 1.5], gap="large")
    with l:
        if received:
            ref_no = f"TA-{inv['due_date']:%y%m%d}-{abs(hash(inv['invoice_no'] or '')) % 10_000:04d}"
            ui.html(f"""<div class="ta-card"><span class="ta-muted">Payment released</span>
            <h3>{ui.money(float(inv['balance']), ccy, decimals=2)}</h3>
            <p>To <b>{inv['supplier']}</b><br>Invoice {inv['invoice_no'] or '—'}<br>Reference {ref_no}<br>
            Value date {max(inv['bl_date'], today):%d %b %Y}</p>
            <p style="margin-top:.6rem">{ui.status_pill('Paid')} {ui.pill('Simulated', 'neutral')}</p></div>""")
            if st.button("Undo", key="bl_undo"):
                st.session_state[bl_key] = False
                st.rerun()
        else:
            ui.html(f"""<div class="ta-card"><span class="ta-muted">Payment instruction</span>
            <h3>{ui.money(float(inv['balance']), ccy, decimals=2)}</h3>
            <p>To <b>{inv['supplier']}</b><br>Paid when we receive the bill of lading copy
            (expected {inv['bl_date']:%d %b})<br>Deadline {inv['due_date']:%d %b %Y}</p>
            <p style="margin-top:.6rem">{ui.status_pill('Awaiting B/L')}</p></div>""")
            st.write("")
            if st.button("Simulate bill of lading received", type="primary", width="stretch"):
                st.session_state[bl_key] = True
                st.rerun()
    with r:
        held_now = sched.loc[pd.to_datetime(sched["date"]) < pd.Timestamp(today), "buy_fx"].sum()
        ui.kpis([
            {"label": "Held for you today", "value": ui.money(held_now, ccy), "sub": "at our licensed partner"},
            {"label": "Still to buy", "value": ui.money(max(float(inv["balance"]) - held_now, 0), ccy),
             "sub": "on the plan and the due date"},
        ])
        legs = res["legs"]
        if not legs.empty:
            rows = []
            for x in legs.itertuples():
                settle = (pd.Timestamp(x.date) + pd.offsets.BDay(m["settlement_days"])).date()
                status = "Held at partner" if settle <= today or received else "Scheduled"
                rows.append([f"{pd.Timestamp(x.date):%d %b}", x.dealer,
                             f"{x.amount_fx / res['usd_per_unit']:,.0f} {ccy}", ui.status_pill(status)])
            ui.table([("Bought", ""), ("Dealer", ""), ("Amount", "r"), ("Status", "")], rows)


# ---------------------------------------------------------------- tab 5: savings

def _savings_tab(res, inv, cash, ref, m, sim) -> None:
    comp, shock, local = res["compare"], res["shock"], res["local"]
    sb, sp = comp["savings"]["Unprotected (bank)"], comp["savings"]["Unprotected (parallel market)"]
    shock_saving = shock["savings"]["Unprotected (bank)"]
    ui.kpis([
        {"label": "Typical saving vs. the parallel market", "value": ui.money(sp["mean"], local, short=True),
         "sub": f"cheaper in {sp['prob_cheaper']:.0%} of scenarios",
         "tone": "pos" if sp["mean"] > 0 else "neg", "highlight": True},
        {"label": f"If the naira falls {sim.shock:.0%} in {sim.shock_day} trading days",
         "value": ui.money(shock_saving, local, short=True), "sub": "saved vs. buying at the bank on the due date",
         "tone": "pos" if shock_saving > 0 else "neg"},
        {"label": "In a bad month (worst 1 in 20)", "value": ui.money(sb["tail_savings"], local, short=True),
         "sub": "saved vs. buying at the bank on the due date", "tone": "pos" if sb["tail_savings"] > 0 else "neg"},
    ])
    note = (f"Against a bank that delivers on time, protection costs about "
            f"{ui.money(-sb['mean'], local, short=True)} on average, the interest your naira would have earned. "
            if sb["mean"] < 0 else "")
    ui.alert("Protection works like insurance. " + note +
             "It pays off when the naira falls, and against the parallel market, where many importers buy "
             "today, it saves money in most cases.", "info")

    ui.subhead(f"What the invoice costs if the naira falls {sim.shock:.0%} in {sim.shock_day} trading days")
    costs = {"TradeAhead plan": shock["plan_cost"]} | shock["baselines"]
    st.plotly_chart(charts.shock_bars(costs, local, height=230), config=charts.CONFIG, theme=None, width="stretch")
    ui.html("<p class='ta-muted'>Change the size and timing of the fall on the left.</p>")

    with st.expander("How we calculated this"):
        sc = res["scenario"]
        ui.html(f"<p>We simulated {workspace.N_PATHS:,} possible paths for the exchange rate up to your due "
                "date, including sudden devaluations as often as they have happened since 2021, and costed "
                "each strategy on every path.</p>")
        ui.table([("Strategy", ""), ("Average cost", "r"), ("Bad case (1 in 20)", "r")],
                 [[f"<b>{n}</b>" if n == "TradeAhead plan" else n, ui.money(s["mean"], local, short=True),
                   ui.money(s["p95"], local, short=True)] for n, s in comp["costs"].items()], highlight={0})
        st.write("")
        ui.table([("Assumption", ""), ("Value", "r")], [
            ["TradeAhead conversion cost", ui.pct(res["all_in"], decimals=2)],
            ["Bank conversion cost without TradeAhead", ui.pct(m["baseline_bank_cost"], decimals=2)],
            ["Parallel-market premium", ui.pct(m["parallel_premium"], decimals=1)],
            [f"Interest on {local} deposits", f"{m['local_deposit_rate']:.0%}"],
            ["Sudden devaluations per year", f"{sc.jump_prob * 252:.1f}, average {sc.jump_mean:.0%}"],
        ])
