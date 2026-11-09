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
              "Enter a supplier invoice and your cash position. The plan, routing, payment and savings views "
              "update as you change the simulator on the left.",
              right=ui.ticker(pair, v, chg, "live" if "live" in source else source.lower(), as_of))

    tabs = st.tabs(["Invoice Workbench", "Protection Plan", "Routing", "Payments", "Savings"])
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
                         "Fill in the Invoice Workbench tab. Upload a supplier invoice or download the sample "
                         "to try it.")
            elif "error" in res:
                ui.alert(res["error"], "warn")
            else:
                fn(res, inv, cash, ref, m, sim)


# ---------------------------------------------------------------- sidebar: scenario simulator

def _sidebar(ref) -> tuple[str, Sim]:
    sb = st.sidebar
    sb.markdown("### Scenario simulator")
    sb.caption("Every change re-runs the plan, routing and 20,000 rate paths.")
    if len(LIVE_MARKETS) > 1:
        market_code = sb.radio("Market", list(LIVE_MARKETS), format_func=LIVE_MARKETS.get,
                               key=_key("market", "NG"), horizontal=True)
    else:
        market_code = next(iter(LIVE_MARKETS))
        sb.caption(f"Market: {LIVE_MARKETS[market_code]}")

    sb.markdown('<div class="ta-side-label">Protection</div>', unsafe_allow_html=True)
    cover = sb.slider("Share of the balance to protect", 0, 100, step=5, format="%d%%", key=_key("cover", 100))
    risk = sb.select_slider("Risk appetite", list(RISK_LEVELS), key=_key("risk", "Balanced"),
                            help="Higher protection buys earlier: less exchange-rate risk, more interest given up.")
    tier = sb.radio("Plan tier", list(ref["tiers"]), key=_key("tier", "Growth"), horizontal=True,
                    help="Sets TradeAhead's FX margin: Starter 1.0%, Growth 0.8%, Pro 0.6%.")

    sb.markdown('<div class="ta-side-label">Market stress</div>', unsafe_allow_html=True)
    drift = sb.selectbox("Rate outlook", list(DRIFT_LABELS), format_func=DRIFT_LABELS.get, key=_key("drift", "implied"))
    vol = sb.slider("Day-to-day volatility", 0.5, 3.0, step=0.25, format="%.2f×", key=_key("vol", 1.0))
    jump = sb.slider("Devaluation risk", 0.0, 4.0, step=0.25, format="%.2f×", key=_key("jump", 1.0),
                     help="Multiplies how often sudden devaluations happen, versus 2021–2026 history.")

    sb.markdown('<div class="ta-side-label">Shock test</div>', unsafe_allow_html=True)
    shock = sb.slider("Overnight devaluation", 0, 60, step=5, format="+%d%%", key=_key("shock", 30))
    shock_day = sb.slider("On trading day", 1, 30, key=_key("shock_day", 5))
    _save("market", "cover", "risk", "tier", "drift", "vol", "jump", "shock", "shock_day")

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
                    help=f"Currencies quoted against the {local} in this market. Edit services/fx.py to add more.")
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
            ui.alert("Your plan is ready. Open <b>Protection Plan</b> above.", "ok")


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
    headroom = (sched["cash_after"] - sched["buffer"]).min()
    ui.kpis([
        {"label": "Plan status", "value": ui.status_pill(p.status),
         "sub": f"{res['n']} trading days to {inv['due_date']:%d %b}", "highlight": True},
        {"label": "Protected before the due date", "value": ui.money(p.planned_fx, ccy),
         "sub": f"{p.planned_fx / float(inv['balance']):.0%} of the balance"},
        {"label": "Exchange-rate risk removed", "value": ui.pct(p.risk_reduction, decimals=0),
         "sub": f"cost uncertainty {ui.money(p.cost_std_local, local, short=True)} vs. {ui.money(p.unprotected_std_local, local, short=True)}"},
        {"label": "Interest given up", "value": ui.money(p.carry_cost_local, local, short=True),
         "sub": f"lowest headroom above buffer {ui.money(headroom, local, short=True)}"},
    ])
    if p.coverage_gap_fx > 1:
        c1, c2 = st.columns([1.6, 1])
        with c1:
            ui.alert(f"<b>Coverage gap: {ui.money(p.coverage_gap_fx, ccy)}.</b> Your cash can fund "
                     f"{p.planned_fx / p.target_fx:.0%} of the target without touching the "
                     f"{ui.money(cash['buffer'], local, short=True)} buffer. The rest is bought at the spot rate "
                     f"on the due date, unprotected.", "warn")
        with c2:
            ui.html(f"""<div class="ta-locked"><span class="ta-muted">Coming soon</span>
            <b>Finance the gap</b><p class="ta-muted" style="margin:.2rem 0 0">A partner lender funds the missing
            {ui.money(p.coverage_gap_fx, ccy)}, priced on your payment history with TradeAhead.</p></div>""")
    for n in p.notes:
        if "Cash covers" not in n:
            ui.alert(n, "info")
    if sim.tier == "Starter":
        ui.alert("Protection plans are part of <b>Growth</b> and <b>Pro</b>. On Starter you would convert "
                 "manually; this preview shows what the plan would do.", "info")

    l, r = st.columns(2, gap="large")
    with l:
        ui.html("<b>Daily dollar accumulation</b>" if ccy == "USD" else f"<b>Daily {ccy} accumulation</b>")
        st.plotly_chart(charts.accumulation(sched, p.target_fx, float(inv["balance"]), ccy),
                        config=charts.CONFIG, theme=None, width="stretch")
    with r:
        ui.html("<b>Cash after purchases vs. buffer</b> <span class='ta-muted'>(at expected rates)</span>")
        st.plotly_chart(charts.cash_vs_buffer(sched, cash["buffer"], local), config=charts.CONFIG, theme=None, width="stretch")

    ui.html("<b>Conversion schedule</b>")
    rows = []
    for r in sched.itertuples():
        rows.append([f"{pd.Timestamp(r.date):%a %d %b}", ui.status_pill(r.status),
                     ui.money(r.buy_fx, ccy) if r.buy_fx else "—", ui.rate(r.expected_rate),
                     ui.money(r.local_needed, local) if r.local_needed else "—",
                     f"{ui.bar(r.coverage)}{r.coverage:.0%}", ui.money(r.cash_after, local, short=True),
                     ui.money(r.cash_after - r.buffer, local, short=True)])
    ui.table([("Date", ""), ("Action", ""), (f"Buy ({ccy})", "r"), ("Expected rate", "r"),
              (f"Cost ({local})", "r"), ("Protected", ""), ("Cash after", "r"), ("Above buffer", "r")], rows,
             highlight={i for i, r in enumerate(sched.itertuples()) if r.buy_fx > 0})
    if p.residual_fx > 1:
        ui.html(f"<p class='ta-muted'>{ui.money(p.residual_fx, ccy)} is left to buy at the spot rate on "
                f"the due date.</p>")


# ---------------------------------------------------------------- tab 3: routing

def _routing_tab(res, inv, cash, ref, m, sim) -> None:
    legs, ranking, wf, local = res["legs"], res["ranking"], res["waterfall"], res["local"]
    if legs.empty:
        ui.empty("No purchases to route", "The plan does not buy anything before the due date with these settings.")
        return
    on_time = (legs["amount_fx"] * legs["on_time_p"]).sum() / legs["amount_fx"].sum()
    cheapest = ranking.loc[ranking["screen_rank"] == 1, "dealer"].item() if not ranking.empty else "—"
    best = ranking.loc[ranking["route_rank"] == 1, "dealer"].item() if not ranking.empty else "—"
    ui.kpis([
        {"label": "All-in cost over mid-market", "value": f"{res['all_in'] * 1e4:,.0f} bps",
         "sub": f"incl. {res['margin']:.1%} TradeAhead margin ({sim.tier})", "highlight": True},
        {"label": "Expected on-time delivery", "value": ui.pct(on_time, decimals=0), "sub": "value-weighted"},
        {"label": "Cheapest quote", "value": cheapest, "sub": "rank 1 on screen price"},
        {"label": "Best delivery-weighted route", "value": best, "sub": "rank 1 after delivery risk"},
    ])
    if ranking.empty:
        return
    first = res["plan"].schedule.query("buy_fx > 0").iloc[0]
    ui.html(f"<b>Dealer ranking for the first purchase</b> <span class='ta-muted'>· "
            f"{pd.Timestamp(first['date']):%a %d %b}, {ui.money(first['buy_fx'] * res['usd_per_unit'], 'USD')} "
            f"· {ranking['bucket'].iloc[0]} ticket</span>")
    st.plotly_chart(charts.rank_slope(ranking), config=charts.CONFIG, theme=None, width="stretch")
    rows = []
    for r in ranking.itertuples():
        arrow = "" if r.route_rank == r.screen_rank else (
            f" <span class='pos'>▲{r.screen_rank - r.route_rank}</span>" if r.route_rank < r.screen_rank
            else f" <span class='neg'>▼{r.route_rank - r.screen_rank}</span>")
        rows.append([f"<b>{r.dealer}</b><br><span class='ta-muted'>{r.type}</span>",
                     f"<span class='ta-rank'>{r.screen_rank}</span> {ui.rate(r.screen_rate)}",
                     ui.rate(r.executable_rate), f"{ui.bar(r.on_time_p)}{r.on_time_p:.0%}",
                     f"{r.late_delay_days:.1f} days", ui.rate(r.delivery_cost),
                     f"<b>{ui.rate(r.all_in_rate)}</b>",
                     f"<span class='ta-rank{' ta-rank--best' if r.route_rank == 1 else ''}'>{r.route_rank}</span>{arrow}"])
    ui.table([("Dealer", ""), ("Quoted (rank)", ""), ("Executable", "r"), ("On time", ""), ("Avg delay if late", "r"),
              ("Delivery cost", "r"), ("All-in", "r"), ("Route rank", "")], rows,
             highlight={i for i, r in enumerate(ranking.itertuples()) if r.route_rank == 1})
    ui.html(f"<p class='ta-muted'>Rates in {local} per USD. Delivery cost = (1 − on-time probability) × "
            f"cost of a failed fill: re-buying at short notice plus each day the supplier payment is late.</p>")

    ui.html("<b>Dealer liquidity waterfall</b> <span class='ta-muted'>· whole plan, from mid-market cost to "
            "all-in cost</span>")
    st.plotly_chart(charts.waterfall(wf, local), config=charts.CONFIG, theme=None, width="stretch")
    with st.expander(f"Route log · {len(legs)} conversions"):
        ui.table([("Date", ""), ("Dealer", ""), ("USD", "r"), ("All-in", "r"), ("On time", "r")],
                 [[f"{pd.Timestamp(x.date):%d %b}", x.dealer, f"{x.amount_fx:,.0f}", ui.rate(x.all_in_rate),
                   f"{x.on_time_p:.0%}"] for x in legs.itertuples()])


# ---------------------------------------------------------------- tab 4: payments

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
        ("Converting", f"until {last_buy:%d %b}" if last_buy is not None else "nothing to convert",
         "done" if converting_done or received else "now"),
        ("B/L copy received", f"expected {inv['bl_date']:%d %b}", "done" if received else ""),
        ("Supplier paid", f"latest {inv['due_date']:%d %b}", "done" if received else ""),
    ])
    held_now = sched.loc[pd.to_datetime(sched["date"]) < pd.Timestamp(today), "buy_fx"].sum()
    ui.kpis([
        {"label": "Payment status", "value": ui.status_pill("Paid" if received else "Awaiting B/L"),
         "sub": "released automatically on the B/L copy", "highlight": True},
        {"label": "Held at partner today", "value": ui.money(held_now, ccy), "sub": "settled conversions"},
        {"label": "Scheduled by due date", "value": ui.money(p.planned_fx, ccy), "sub": "after all conversions settle"},
        {"label": "Bought at spot on the due date", "value": ui.money(p.residual_fx, ccy),
         "sub": "unprotected remainder"},
    ])
    l, r = st.columns([1.5, 1], gap="large")
    with l:
        ui.html("<b>Funds held vs. bill of lading trigger</b>")
        st.plotly_chart(charts.held_vs_trigger(sched[sched["buy_fx"] > 0] if p.planned_fx else sched,
                                               float(inv["balance"]), inv["bl_date"], inv["due_date"], ccy),
                        config=charts.CONFIG, theme=None, width="stretch")
    with r:
        ui.html("<b>Supplier payment</b>")
        if received:
            ref_no = f"TA-{inv['due_date']:%y%m%d}-{abs(hash(inv['invoice_no'] or '')) % 10_000:04d}"
            ui.html(f"""<div class="ta-card"><span class="ta-muted">Payment released</span>
            <h3>{ui.money(float(inv['balance']), ccy, decimals=2)}</h3>
            <p>To <b>{inv['supplier']}</b><br>Invoice {inv['invoice_no'] or '—'}<br>Reference {ref_no}<br>
            Value date {max(inv['bl_date'], today):%d %b %Y}</p>
            <p style="margin-top:.6rem">{ui.pill('Simulated', 'neutral')}</p></div>""")
            if st.button("Undo", key="bl_undo"):
                st.session_state[bl_key] = False
                st.rerun()
        else:
            ui.html(f"""<div class="ta-card"><span class="ta-muted">Payment instruction</span>
            <h3>{ui.money(float(inv['balance']), ccy, decimals=2)}</h3>
            <p>To <b>{inv['supplier']}</b><br>Trigger: copy of bill of lading (expected {inv['bl_date']:%d %b})<br>
            Deadline: {inv['due_date']:%d %b %Y}</p></div>""")
            st.write("")
            if st.button("Simulate B/L copy received", type="primary", width="stretch"):
                st.session_state[bl_key] = True
                st.rerun()

    legs = res["legs"]
    if not legs.empty:
        ui.html("<b>Settlement log</b>")
        rows = []
        for x in legs.itertuples():
            settle = (pd.Timestamp(x.date) + pd.offsets.BDay(m["settlement_days"])).date()
            status = "Held at partner" if settle <= today or received else "Scheduled"
            rows.append([f"{pd.Timestamp(x.date):%d %b}", f"{settle:%d %b}", x.dealer,
                         f"{x.amount_fx / res['usd_per_unit']:,.0f} {ccy}", ui.status_pill(status)])
        ui.table([("Converted", ""), ("Settles", ""), ("Dealer", ""), ("Amount", "r"), ("Status", "")], rows)


# ---------------------------------------------------------------- tab 5: savings

def _savings_tab(res, inv, cash, ref, m, sim) -> None:
    comp, shock, local = res["compare"], res["shock"], res["local"]
    sb, sp = comp["savings"]["Unprotected (bank)"], comp["savings"]["Unprotected (parallel market)"]
    ui.kpis([
        {"label": "Saving in the worst 5% of outcomes", "value": ui.money(sb["tail_savings"], local, short=True),
         "sub": f"{sb['tail_savings_pct']:+.1%} vs. unprotected bank purchase", "highlight": True},
        {"label": f"Saving in a +{sim.shock:.0%} shock on day {sim.shock_day}",
         "value": ui.money(shock["savings"]["Unprotected (bank)"], local, short=True),
         "sub": f"{shock['savings_pct']['Unprotected (bank)']:+.1%} vs. bank",
         "tone": "pos" if shock["savings"]["Unprotected (bank)"] > 0 else "neg"},
        {"label": "Average vs. parallel market", "value": ui.money(sp["mean"], local, short=True),
         "sub": f"{sp['mean_pct']:+.1%} · cheaper in {sp['prob_cheaper']:.0%} of paths",
         "tone": "pos" if sp["mean"] > 0 else "neg"},
        {"label": "Average vs. bank", "value": ui.money(sb["mean"], local, short=True),
         "sub": f"{sb['mean_pct']:+.1%} · the average cost of protection" if sb["mean"] < 0 else f"{sb['mean_pct']:+.1%}",
         "tone": "pos" if sb["mean"] > 0 else "neg"},
    ])
    ui.alert("Protection works like insurance. Against a bank that delivers on time, it costs a little on "
             "average (interest given up, conversion costs) and pays off when the currency falls. Against the "
             "parallel market, where many SMEs buy today, it saves money in most outcomes.", "info")

    ui.html(f"<b>Final cost across {workspace.N_PATHS:,} simulated rate paths</b> "
            "<span class='ta-muted'>· line 5–95% of outcomes, band middle half, dot median</span>")
    st.plotly_chart(charts.cost_ranges(comp["distributions"], local), config=charts.CONFIG, theme=None,
                    width="stretch")
    ui.html(f"<b>Shock test: +{sim.shock:.0%} overnight on trading day {sim.shock_day}</b>")
    costs = {"TradeAhead plan": shock["plan_cost"]} | shock["baselines"]
    st.plotly_chart(charts.shock_bars(costs, local, height=230), config=charts.CONFIG, theme=None, width="stretch")

    ui.html("<b>Strategy comparison</b> <span class='ta-muted'>· final cost including interest given up</span>")
    rows = []
    for name, s in comp["costs"].items():
        rows.append([f"<b>{name}</b>" if name == "TradeAhead plan" else name,
                     ui.money(s["mean"], local, short=True), ui.money(s["median"], local, short=True),
                     ui.money(s["p95"], local, short=True), ui.money(s["worst"], local, short=True),
                     ui.money(s["p95"] - s["p05"], local, short=True)])
    ui.table([("Strategy", ""), ("Average", "r"), ("Median", "r"), ("Bad case (95th pct)", "r"),
              ("Worst path", "r"), ("Range (5–95%)", "r")], rows, highlight={0})

    with st.expander("Assumptions"):
        sc = res["scenario"]
        ui.table([("Assumption", ""), ("Value", "r")], [
            ["TradeAhead all-in conversion cost (routed)", ui.pct(res["all_in"], decimals=2)],
            ["Bank all-in cost without TradeAhead", ui.pct(m["baseline_bank_cost"], decimals=2)],
            ["Parallel-market premium", ui.pct(m["parallel_premium"], decimals=1)],
            [f"{local} deposit rate / USD holding rate",
             f"{m['local_deposit_rate']:.0%} / {m['usd_holding_rate']:.0%}"],
            ["Day-to-day volatility (annualised)", ui.pct(sc.daily_vol * 252 ** 0.5)],
            ["Devaluations per year (>5% in a day)", f"{sc.jump_prob * 252:.1f}, avg {sc.jump_mean:.0%}"],
            ["Expected change over the plan", ui.pct(sc.total_drift * res['n'], signed=True)],
            ["Simulated paths", f"{workspace.N_PATHS:,}"],
        ])
