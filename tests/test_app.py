"""Smoke tests: every page renders without errors, the Dashboard across many settings."""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _page(name):
    import importlib
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path.cwd()))
    from ui import components as ui
    ui.inject_css()
    importlib.import_module(f"views.{name}").render()


DUE = (pd.Timestamp(date.today()) + pd.offsets.BDay(16)).date()
BASE = {"supplier": "Test Co", "invoice_no": "PI-1", "ccy": "USD", "balance": 34_905.5,
        "bl_date": DUE - timedelta(days=5), "due_date": DUE, "deposit_paid": True,
        "cash": 60e6, "buffer": 18e6, "inflow": 2.5e6, "outflow": 1.2e6}


def run(name, state=None):
    at = AppTest.from_function(_page, args=(name,), default_timeout=120)
    if state is not None:
        at.session_state["_dash"] = state
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


@pytest.mark.parametrize("name", ["company", "product", "pricing"])
def test_static_pages(name):
    run(name)


def test_dashboard_blank_has_no_plan():
    at = run("dashboard")
    assert "Nothing here yet" in " ".join(m.value for m in at.markdown)


@pytest.mark.parametrize("extra", [
    {},
    {"cash": 30e6},                                   # coverage gap
    {"ccy": "CNY", "balance": 250_000.0},              # yuan invoice
    {"market": "EG"},                                  # Egypt
    {"cover": 0}, {"cover": 50},
    {"tier": "Starter", "risk": "Maximum protection"},
    {"drift": "historical", "vol": 3.0, "jump": 4.0, "shock": 60, "shock_day": 30},
    {"cash": 10e6},                                    # below the buffer today
])
def test_dashboard_settings(extra):
    state = {**BASE, **extra}
    at = run("dashboard", state)
    text = " ".join(m.value for m in at.markdown)
    assert "to continue" not in text
    assert "Plan status" in text


def test_contact_form(tmp_path, monkeypatch):
    import views.pricing as pricing
    monkeypatch.setattr(pricing, "LEADS", tmp_path / "leads.csv")
    at = run("pricing")
    at.button[0].click().run()  # empty submit
    assert at.error and not (tmp_path / "leads.csv").exists()
    for label, value in [("Full name *", "Ada Obi"), ("Company *", "Obi Imports"), ("Work email *", "ada@example.com")]:
        next(t for t in at.text_input if t.label == label).input(value)
    at.checkbox[0].check()
    at.button[0].click().run()
    assert at.success and "Ada Obi" in (tmp_path / "leads.csv").read_text()
