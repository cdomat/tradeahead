"""Pricing & Contact: tier comparison, FX margin calculator and lead capture form."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

import streamlit as st

from services import workspace
from ui import components as ui

LEADS = Path(__file__).resolve().parents[1] / "data" / "leads.csv"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

FEATURES = [
    ("Invoice upload and reading", {"Starter", "Growth", "Pro"}),
    ("Exposure dashboard", {"Starter", "Growth", "Pro"}),
    ("Manual conversions", {"Starter", "Growth", "Pro"}),
    ("Cash-constrained protection plan", {"Growth", "Pro"}),
    ("Target-rate rules", {"Growth", "Pro"}),
    ("Direct supplier payment on B/L", {"Growth", "Pro"}),
    ("Delivery-weighted routing", {"Growth", "Pro"}),
    ("Cash-flow forecasting", {"Pro"}),
    ("Routing reports", {"Pro"}),
    ("Multi-user access", {"Pro"}),
]


def render() -> None:
    tiers = workspace.reference()["tiers"]
    ui.header("Pricing",
              "A monthly plan for the planning tools, plus a clear FX margin on each conversion. "
              "No onboarding fee, and you can start for free.")

    cards = []
    for name, t in tiers.items():
        featured = name == "Growth"
        price = "Free" if t["price_month"] == 0 else f"${t['price_month']}<small> / month</small>"
        items = "".join(f"<li>{f}</li>" for f in t["features"])
        badge = f'<span class="badge">{ui.pill("Most popular", "brand")}</span>' if featured else ""
        cards.append(f"""<div class="ta-tier{' ta-tier--featured' if featured else ''}">{badge}
            <h3>{name}</h3><div class="price">{price}</div>
            <div class="ta-muted">+ {t['fx_margin']:.1%} FX margin per conversion</div><ul>{items}</ul></div>""")
    ui.grid(cards, min_width=260)

    ui.section("What each plan includes")
    names = list(tiers)
    ui.table([("Feature", "")] + [(n, "c") for n in names],
             [[f] + ["<span class='ta-check'>✓</span>" if n in inc else "<span class='ta-dash'>—</span>" for n in names]
              for f, inc in FEATURES] +
             [["<b>Monthly price</b>"] + [("Free" if tiers[n]["price_month"] == 0 else f"${tiers[n]['price_month']}")
                                          for n in names],
              ["<b>FX margin</b>"] + [f"{tiers[n]['fx_margin']:.1%}" for n in names]])

    ui.section("What you would pay in a typical month")
    with st.container(key="card_calc"):
        a, b = st.columns([1, 2])
        volume = a.number_input("Supplier payments per month (USD)", min_value=0, value=None, step=10_000,
                                placeholder="e.g. 150,000", key="calc_volume")
        if volume:
            rows = []
            for n, t in tiers.items():
                fee = volume * t["fx_margin"] + t["price_month"]
                rows.append({"label": n, "value": f"${fee:,.0f}", "sub": f"{fee / volume:.2%} of volume"})
            best = min(rows, key=lambda r: float(r["value"][1:].replace(",", "")))
            best["highlight"] = True
            with b:
                ui.kpis(rows)
        else:
            b.markdown("<p class='ta-muted' style='padding-top:2rem'>Enter your monthly volume to compare the "
                       "total cost of each plan.</p>", unsafe_allow_html=True)

    with st.container(key="band_tint_contact"):
        ui.section("Talk to our team",
                   "Tell us about your suppliers and payment cycle, and we'll set up your first protection plan with you.")
        l, _ = st.columns([1.6, 1], gap="large")
        with l, st.form("contact", clear_on_submit=True, border=False):
            a, b = st.columns(2)
            name = a.text_input("Full name *")
            company = b.text_input("Company *")
            email = a.text_input("Work email *")
            phone = b.text_input("Phone (optional)")
            a, b = st.columns(2)
            role = a.selectbox("Your role", ["Owner / director", "Finance", "Procurement", "Clearing agent", "Other"],
                               index=None, placeholder="Choose")
            volume = b.selectbox("Monthly supplier payments", ["Under $25k", "$25k–100k", "$100k–500k", "Over $500k"],
                                 index=None, placeholder="Choose")
            markets = st.text_input("City or state you import into", placeholder="e.g. Lagos")
            message = st.text_area("What would you like to protect?", placeholder="e.g. monthly yuan invoices from Guangzhou")
            consent = st.checkbox("I agree to be contacted by TradeAhead about my enquiry. *")
            sent = st.form_submit_button("Send", type="primary")
        if sent:
            errors = [m for ok, m in [(name.strip(), "your name"), (company.strip(), "your company"),
                                      (EMAIL.match(email.strip() or ""), "a valid email"), (consent, "your consent")]
                      if not ok]
            if errors:
                l.error("Please add " + ", ".join(errors) + ".")
            else:
                _save_lead({"received": datetime.now().isoformat(timespec="seconds"), "name": name.strip(),
                            "company": company.strip(), "email": email.strip(), "phone": phone.strip(),
                            "role": role or "", "volume": volume or "", "markets": markets.strip(),
                            "message": message.strip()})
                l.success(f"Thank you, {name.split()[0]}. We will be in touch shortly.")


def _save_lead(row: dict) -> None:
    LEADS.parent.mkdir(exist_ok=True)
    new = not LEADS.exists()
    with LEADS.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)
