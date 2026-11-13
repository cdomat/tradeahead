"""TradeAhead: FX protection and payment planning for importing SMEs.

    streamlit run app.py
"""

import streamlit as st

from ui import components as ui
from views import PAGES, company, dashboard, pricing, product

st.set_page_config(page_title="TradeAhead · FX protection for importers", page_icon="assets/mark.svg",
                   layout="wide", initial_sidebar_state="expanded")
ui.inject_css()
st.logo("assets/logo.svg", size="large")

PAGES.update({
    "company": st.Page(company.render, title="Company", url_path="company", default=True),
    "product": st.Page(product.render, title="Product & Technology", url_path="product"),
    "dashboard": st.Page(dashboard.render, title="Dashboard", url_path="dashboard"),
    "pricing": st.Page(pricing.render, title="Pricing & Contact", url_path="pricing"),
})
st.navigation(list(PAGES.values()), position="top").run()
ui.footer()
