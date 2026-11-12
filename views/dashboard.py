"""Dashboard: the importer's workspace.

Follows one invoice through the four steps: invoice and cash, protection plan (with what it
saves), best route, supplier payment. Starts blank; nothing is computed until an invoice and a cash
position are entered.
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

# How early the plan buys. Higher values buy sooner: less exchange-rate risk, more interest given up.
WHEN_TO_BUY = {"Flexible": 0.3, "Balanced": 3.0, "Lock in early": 15.0}
SHOCK_WHEN = {"Tomorrow": 1, "Next week": 5, "In two weeks": 10}
TRADING_DAYS_PER_MONTH = 21
TIER = "Growth"  # pricing tier used for the fee until accounts exist

INVOICE_KEYS = ["supplier", "invoice_no", "ccy", "balance", "bl_date", "due_date", "deposit_paid"]
CASH_KEYS = ["cash", "buffer", "inflow", "outflow"]
FIELD_NAMES = {"supplier": "supplier", "invoice_no": "invoice number", "ccy": "currency", "balance": "balance",
               "bl_date": "bill of lading date", "due_date": "due date", "deposit_paid": "deposit"}


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
    market_code = next(iter(LIVE_MARKETS))
    m = ref["markets"][market_code]
    local = m["local"]

    ccy = _get("ccy") or "USD"
    pair = f"{ccy}/{local}" if f"{ccy}/{local}" in m["pairs"] else m["default_pair"]
    v, chg, as_of = workspace.latest(pair)
    _, source = workspace.fx_history()
    ui.header("Your workspace",
              "Add a supplier invoice and your cash position, approve the plan, then follow each purchase "
              "through to the supplier payment.",
              right=ui.ticker(pair, v, chg, "live" if "live" in source else source.lower(), as_of))
    overview = st.container(key="overview")  # filled once the plan has run

    tabs = st.tabs(["1 · Invoice & cash", "2 · Your plan", "3 · Best route", "4 · Supplier payment"])
    with tabs[0]:
        cover, when = _workbench(m)

    # The fall-in-the-naira control sits in the plan tab, but the run needs its value first.
    shock = st.session_state[_key("shock", 30)]
    shock_when = st.session_state[_key("shock_when", "Next week")]
    sim = Sim(cover=cover / 100, risk_aversion=WHEN_TO_BUY.get(when, 3.0), tier=TIER,
              shock=shock / 100, shock_day=SHOCK_WHEN.get(shock_when, 5))

    inv, cash = _invoice(), _cash()
    res = None
    if inv and cash:
        res = workspace.run(market_code, inv["ccy"], float(inv["balance"]), inv["due_date"], cash["cash"],
                            cash["buffer"], cash["inflow"], cash["outflow"], sim)
    missing = ("an invoice" if not inv else "") + (" and " if not inv and not cash else "") + \
              ("your cash position" if not cash else "")
    approval = f"approved_{inv['invoice_no']}_{inv['due_date']}_{cover}_{when}" if inv else ""
    for tab, fn in zip(tabs[1:], (_plan_tab, _route_tab, _payment_tab)):
        with tab:
            if res is None:
                ui.empty("Add " + missing + " to continue",
                         "Fill in step 1. Upload a supplier invoice, or download the sample to try it.")
            elif "error" in res:
                ui.alert(res["error"], "warn")
            else:
                fn(res, inv, cash, m, sim, approval)
    with overview:
        _overview(inv, res, approval)


# ---------------------------------------------------------------- overview under the header

def _overview(inv, res, approval) -> None:
    """One line per invoice in the workspace, visible from every tab."""
    if not inv:
        return
    ccy = inv["ccy"]
    protected = "—"
    if res is None:
        status = "Details missing"
    elif "error" in res:
        status = "Details missing"
    else:
        p = res["plan"]
        protected = f"{ui.bar(p.planned_fx / float(inv['balance']))}{p.planned_fx / float(inv['balance']):.0%}"
        if st.session_state.get(f"bl_{inv['invoice_no']}_{inv['due_date']}"):
            status = "Paid"
        elif not st.session_state.get(approval):
            status = "Waiting for approval"
        else:
            last = p.schedule.loc[p.schedule["buy_fx"] > 0, "date"].max() if p.planned_fx else None
            status = "Ready to pay" if last is None or last < date.today() else "Buying currency"
    ui.table([("Supplier", ""), ("Invoice", ""), ("Balance", "r"), ("Due", ""), ("Protected", ""), ("Status", "")],
             [[f"<b>{inv['supplier']}</b>", inv["invoice_no"] or "—", ui.money(float(inv["balance"]), ccy),
               f"{inv['due_date']:%d %b %Y}", protected, ui.status_pill(status)]])


def _start_over() -> None:
    st.session_state.pop("_dash", None)
    for k in list(st.session_state):
        if k.startswith(("w_", "_parsed", "approved_", "bl_")):
            del st.session_state[k]


# ---------------------------------------------------------------- tab 1: invoice and cash

def _workbench(m) -> tuple[int, str]:
    local = m["local"]
    currencies = [p.split("/")[0] for p in m["pairs"]]
    left, right = st.columns([1.35, 1], gap="large")

    with left, st.container(key="card_invoice"):
        ui.html('<h3 style="margin:0 0 .6rem;font-size:18px">Supplier invoice</h3>')
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
            unsure = [FIELD_NAMES[k] for k, c in conf.items() if c != "high"]
            msg = f"Read <b>{up.name}</b>. " + (
                f"Please check: {', '.join(unsure)}." if unsure else "Check the values below.")
            ui.alert(msg, "warn" if unsure else "ok")

        a, b = st.columns(2)
        a.text_input("Supplier", key=_key("supplier"), placeholder="e.g. Shenzhen supplier Co., Ltd.")
        b.text_input("Invoice number", key=_key("invoice_no"), placeholder="e.g. PI-2026-0417")
        a, b = st.columns(2)
        a.selectbox("Invoice currency", currencies, index=None, key=_key("ccy"), placeholder="Choose currency")
        b.number_input("Balance still to pay", min_value=0.0, value=None, step=1_000.0, format="%.2f",
                       key=_key("balance"), placeholder="e.g. 34,905.50")
        a, b = st.columns(2)
        a.date_input("Goods ship (bill of lading)", value=None, min_value=date.today(), key=_key("bl_date"),
                     help="Most suppliers ask for the balance against a copy of the bill of lading.")
        b.date_input("Balance due date", value=None, min_value=date.today() + timedelta(days=2),
                     key=_key("due_date"))
        st.checkbox("Deposit already paid", key=_key("deposit_paid"))
        _save(*INVOICE_KEYS)

    with right, st.container(key="card_cash"):
        ui.html('<h3 style="margin:0 0 .6rem;font-size:18px">Your cash</h3>')
        s = ui.sym(local)
        st.number_input(f"Cash on hand today ({s})", min_value=0.0, value=None, step=1e6, format="%.0f",
                        key=_key("cash"), placeholder="e.g. 60,000,000")
        st.number_input(f"Cash buffer to keep ({s})", min_value=0.0, value=None, step=1e6, format="%.0f",
                        key=_key("buffer"), placeholder="e.g. 18,000,000",
                        help="What the business must always keep for stock, wages and rent. "
                             "The plan never uses it.")
        st.number_input(f"Money coming in per month ({s})", min_value=0.0, value=None, step=1e6,
                        format="%.0f", key=_key("inflow"), placeholder="Optional, e.g. 52,000,000")
        st.number_input(f"Other payments per month ({s})", min_value=0.0, value=None, step=1e6,
                        format="%.0f", key=_key("outflow"), placeholder="Optional, e.g. 25,000,000")
        _save(*CASH_KEYS)

    with right, st.container(key="card_protection"):
        ui.html('<h3 style="margin:0 0 .6rem;font-size:18px">Your protection</h3>')
        cover = st.slider("Share of the balance to protect", 0, 100, step=5, format="%d%%", key=_key("cover", 100),
                          help="The rest is bought on the due date at that day's rate.")
        when = st.select_slider("When to buy", list(WHEN_TO_BUY), key=_key("when", "Balanced"),
                                help="Lock in early buys sooner: less exchange-rate risk, but your naira earns "
                                     "less interest. Flexible waits longer.")
        _save("cover", "when")
        st.caption("Your cash buffer is never used, whatever you choose here.")

    inv, cash = _invoice(), _cash()
    l, r = st.columns([1.35, 1], gap="large", vertical_alignment="center")  # same grid as the cards
    with l:
        if inv and cash:
            ui.alert("Your plan is ready. Open <b>2 · Your plan</b> above.", "ok")
        else:
            ui.html("<p>" + (ui.pill("Invoice complete", "ok") if inv else ui.pill("Invoice incomplete", "warn"))
                    + " " + (ui.pill("Cash complete", "ok") if cash else ui.pill("Cash incomplete", "warn")) + "</p>")
    r.button("Start over", type="tertiary", icon=":material/restart_alt:", width="stretch", on_click=_start_over)
    return cover, when


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
    """Cash position for the plan. Monthly flows are entered; the plan works per trading day."""
    v = {k: _get(k) for k in CASH_KEYS}
    if v["cash"] is None or v["buffer"] is None:
        return None
    v["inflow"] = (v["inflow"] or 0.0) / TRADING_DAYS_PER_MONTH
    v["outflow"] = (v["outflow"] or 0.0) / TRADING_DAYS_PER_MONTH
    return v


# ---------------------------------------------------------------- tab 2: plan

def _plan_tab(res, inv, cash, m, sim, approval) -> None:
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
         "sub": "interest your naira would have earned"},
    ])
    if p.coverage_gap_fx > 1:
        c1, c2 = st.columns([1.6, 1])
        with c1:
            ui.alert(f"<b>Your cash covers {p.planned_fx / p.target_fx:.0%} of the target.</b> Buying the other "
                     f"{ui.money(p.coverage_gap_fx, ccy)} early would take you below your "
                     f"{ui.money(cash['buffer'], local, short=True)} buffer, so it is bought on the due date "
                     f"at that day's rate.", "warn")
        with c2:
            ui.html(f"""<div class="ta-locked"><span class="ta-muted">Coming soon</span>
            <b>Finance the gap</b><p class="ta-muted" style="margin:.2rem 0 0">A partner lender funds the missing
            {ui.money(p.coverage_gap_fx, ccy)}.</p></div>""")

    l, r = st.columns(2, gap="large")
    with l:
        ui.subhead(f"{'Dollars' if ccy == 'USD' else ccy} bought over time")
        st.plotly_chart(charts.accumulation(sched, p.target_fx, float(inv["balance"]), ccy),
                        config=charts.CONFIG, theme=None, width="stretch")
    with r:
        ui.subhead("Your cash stays above the buffer")
        st.plotly_chart(charts.cash_vs_buffer(sched, cash["buffer"], local), config=charts.CONFIG, theme=None,
                        width="stretch")

    buys = sched[sched["buy_fx"] > 0]
    ui.subhead("What we will buy, and when", f"{len(buys)} purchases")
    ui.table([("Date", ""), (f"Buy ({ccy})", "r"), (f"Approx. cost ({local})", "r"), ("Protected so far", "")],
             [[f"{pd.Timestamp(r.date):%a %d %b}", ui.money(r.buy_fx, ccy),
               ui.money(r.local_needed, local, short=True), f"{ui.bar(r.coverage)}{r.coverage:.0%}"]
              for r in buys.itertuples()])
    if p.residual_fx > 1:
        ui.html(f"<p class='ta-muted'>{ui.money(p.residual_fx, ccy)} is left to buy on the due date at that "
                f"day's rate.</p>")

    _savings(res, sim, m)

    st.write("")
    if buys.empty:
        return
    if st.session_state.get(approval):
        c1, c2 = st.columns([3, 1], vertical_alignment="center")
        with c1:
            ui.alert(f"<b>Plan approved.</b> The first purchase is on {pd.Timestamp(buys['date'].iloc[0]):%d %b}. "
                     "If you change your protection settings in step 1, you approve again.", "ok")
        if c2.button("Withdraw approval", width="stretch"):
            st.session_state[approval] = False
            st.rerun()
    elif st.button("Approve plan", type="primary"):
        st.session_state[approval] = True
        st.rerun()


# ---------------------------------------------------------------- tab 3: best route

def _route_tab(res, inv, cash, m, sim, approval) -> None:
    legs, ranking, wf, local = res["legs"], res["ranking"], res["waterfall"], res["local"]
    if legs.empty:
        ui.empty("No purchases to place", "The plan does not buy anything before the due date with these settings.")
        return
    on_time = (legs["amount_fx"] * legs["on_time_p"]).sum() / legs["amount_fx"].sum()
    ui.kpis([
        {"label": "Total conversion cost", "value": ui.pct(res["all_in"], decimals=2),
         "sub": f"above the mid-market rate, incl. our {res['margin']:.1%} fee", "highlight": True},
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
        share = legs.groupby("dealer")["amount_fx"].sum() / legs["amount_fx"].sum()
        ui.subhead("Dealers for your purchases", "best route first")
        ui.table([("", "c"), ("Dealer", ""), ("Quoted rate", "r"), ("On-time record", ""),
                  ("Share of your purchases", "")],
                 [[f"<span class='ta-rank{' ta-rank--best' if r.route_rank == 1 else ''}'>{r.route_rank}</span>",
                   f"<b>{r.dealer}</b>", ui.rate(r.screen_rate), f"{ui.bar(r.on_time_p)}{r.on_time_p:.0%}",
                   f"{ui.bar(share[r.dealer])}{share[r.dealer]:.0%}" if r.dealer in share else
                   "<span class='ta-muted'>not used</span>"]
                  for r in ranking.itertuples()],
                 highlight={i for i, r in enumerate(ranking.itertuples()) if r.route_rank == 1})
        ui.html(f"<p class='ta-muted'>Rates in {local} per US dollar. No dealer takes more than 60% of a "
                "large purchase, so one late delivery can't hold up the whole payment.</p>")

    with st.expander("Where the conversion cost comes from"):
        mid = wf["Mid-market cost"]
        parts = [("Dealer spread", wf["Dealer spread"] + wf["Execution slippage"]),
                 ("TradeAhead fee", wf["TradeAhead margin"]),
                 ("Allowance for late deliveries", wf["Expected delivery cost"])]
        ui.table([("Part", ""), (f"Cost ({local})", "r"), ("Share of the invoice value", "r")],
                 [[n, ui.money(v, local, short=True), ui.pct(v / mid, decimals=2)] for n, v in parts] +
                 [["<b>Total</b>", f"<b>{ui.money(wf['All-in cost'] - mid, local, short=True)}</b>",
                   f"<b>{ui.pct((wf['All-in cost'] - mid) / mid, decimals=2)}</b>"]])


# ---------------------------------------------------------------- tab 4: supplier payment

def _payment_tab(res, inv, cash, m, sim, approval) -> None:
    p, ccy = res["plan"], res["ccy"]
    sched, today = p.schedule, date.today()
    approved = bool(st.session_state.get(approval))
    bl_key = f"bl_{inv['invoice_no']}_{inv['due_date']}"
    received = st.session_state.get(bl_key, False)
    last_buy = sched.loc[sched["buy_fx"] > 0, "date"].max() if p.planned_fx else None
    converting_done = last_buy is not None and last_buy < today
    ui.steps([
        ("Deposit paid", "with the order" if inv.get("deposit_paid") else "not confirmed",
         "done" if inv.get("deposit_paid") else ""),
        ("Plan approved", f"{res['start']:%d %b}" if approved else "waiting for you", "done" if approved else "now"),
        ("Buying currency", f"until {last_buy:%d %b}" if last_buy is not None else "nothing to buy",
         "done" if converting_done or received else "now" if approved else ""),
        ("Goods shipped", f"expected {inv['bl_date']:%d %b}", "done" if received else ""),
        ("Supplier paid", f"latest {inv['due_date']:%d %b}", "done" if received else ""),
    ])
    if not approved:
        ui.alert("Approve the plan in <b>2 · Your plan</b> to start buying.", "info")

    l, r = st.columns([1, 1.5], gap="large")
    with l:
        if received:
            cost = res["compare"]["costs"]["TradeAhead plan"]["mean"]
            saved = res["compare"]["savings"]["Unprotected (parallel market)"]["mean"]
            ref_no = f"TA-{inv['due_date']:%y%m%d}-{abs(hash(inv['invoice_no'] or '')) % 10_000:04d}"
            ui.html(f"""<div class="ta-card"><span class="ta-muted">Payment sent</span>
            <h3>{ui.money(float(inv['balance']), ccy, decimals=2)}</h3>
            <p>To <b>{inv['supplier']}</b><br>Invoice {inv['invoice_no'] or '—'}<br>Reference {ref_no}<br>
            Value date {max(inv['bl_date'], today):%d %b %Y}</p>
            <p>Expected cost {ui.money(cost, res['local'], short=True)}<br>
            <b>{ui.money(saved, res['local'], short=True)}</b> less than buying on the parallel market</p>
            <p style="margin-top:.6rem">{ui.status_pill('Paid')} {ui.pill('Demo', 'neutral')}</p></div>""")
            if st.button("Undo", key="bl_undo"):
                st.session_state[bl_key] = False
                st.rerun()
        else:
            ui.html(f"""<div class="ta-card"><span class="ta-muted">Payment to your supplier</span>
            <h3>{ui.money(float(inv['balance']), ccy, decimals=2)}</h3>
            <p>To <b>{inv['supplier']}</b><br>Sent when you upload the bill of lading copy
            (expected {inv['bl_date']:%d %b})<br>Deadline {inv['due_date']:%d %b %Y}</p>
            <p style="margin-top:.6rem">{ui.status_pill('Awaiting B/L')}</p></div>""")
            st.write("")
            if st.button("Demo: goods have shipped", type="primary", width="stretch", disabled=not approved):
                st.session_state[bl_key] = True
                st.rerun()
    with r:
        held_now = sched.loc[pd.to_datetime(sched["date"]) < pd.Timestamp(today), "buy_fx"].sum() if approved else 0
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
                status = ("Held at partner" if settle <= today or received else "Scheduled") if approved \
                    else "Not approved"
                rows.append([f"{pd.Timestamp(x.date):%d %b}", x.dealer,
                             f"{x.amount_fx / res['usd_per_unit']:,.0f} {ccy}", ui.status_pill(status)])
            ui.table([("Date", ""), ("Dealer", ""), ("Amount", "r"), ("Status", "")], rows)


# ---------------------------------------------------------------- what the plan saves (inside the plan tab)

def _when_label(day: int) -> str:
    return next((k for k, v in SHOCK_WHEN.items() if v == day), f"in {day} trading days")


def _savings(res, sim, m) -> None:
    comp, shock, local = res["compare"], res["shock"], res["local"]
    sb, sp = comp["savings"]["Unprotected (bank)"], comp["savings"]["Unprotected (parallel market)"]
    shock_saving = shock["savings"]["Unprotected (bank)"]
    when = _when_label(sim.shock_day).lower()
    ui.subhead("What this plan saves you")
    ui.kpis([
        {"label": "Typical saving vs. the parallel market", "value": ui.money(sp["mean"], local, short=True),
         "sub": f"cheaper in {sp['prob_cheaper']:.0%} of cases",
         "tone": "pos" if sp["mean"] > 0 else "neg", "highlight": True},
        {"label": "In a bad month (1 in 20)", "value": ui.money(sb["tail_savings"], local, short=True),
         "sub": "saved vs. buying at the bank on the due date", "tone": "pos" if sb["tail_savings"] > 0 else "neg"},
        {"label": f"If the naira falls {sim.shock:.0%} {when}", "value": ui.money(shock_saving, local, short=True),
         "sub": "saved vs. buying at the bank on the due date", "tone": "pos" if shock_saving > 0 else "neg"},
    ])
    note = (f" Against a bank that delivers on time it costs about {ui.money(-sb['mean'], local, short=True)} "
            "on average, the interest your naira would have earned." if sb["mean"] < 0 else "")
    ui.html("<p class='ta-muted'>Protection works like insurance: it pays off when the naira falls." + note + "</p>")

    with st.expander("What if the naira falls overnight?"):
        a, b, _ = st.columns([1.2, 1, 1])
        a.slider("Size of the fall", 0, 60, step=5, format="%d%%", key=_key("shock", 30))
        b.select_slider("When", list(SHOCK_WHEN), key=_key("shock_when", "Next week"))
        _save("shock", "shock_when")
        costs = {"TradeAhead plan": shock["plan_cost"]} | shock["baselines"]
        st.plotly_chart(charts.shock_bars(costs, local, height=230), config=charts.CONFIG, theme=None,
                        width="stretch")

    with st.expander("How we calculated this"):
        sc = res["scenario"]
        ui.html(f"<p>We simulated {workspace.N_PATHS:,} possible paths for the exchange rate up to your due "
                "date, including sudden devaluations as often as they have happened since 2021, and costed "
                "each way of buying on every path.</p>")
        ui.table([("Way of buying", ""), ("Average cost", "r"), ("Bad case (1 in 20)", "r")],
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
