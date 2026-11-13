"""TradeAhead: FX protection and payment planning for importing SMEs.

    streamlit run app.py
"""

import streamlit as st

from ui import components as ui
from views import PAGES, company, dashboard, pricing, product

st.set_page_config(page_title="TradeAhead · FX protection for importers", page_icon="assets/mark.svg",
                   layout="wide", initial_sidebar_state="collapsed")
ui.inject_css()
st.logo("assets/logo.svg", size="large")

PAGES.update({
    "company": st.Page(company.render, title="Company", url_path="company", default=True),
    "product": st.Page(product.render, title="Product & Technology", url_path="product"),
    "dashboard": st.Page(dashboard.render, title="Dashboard", url_path="dashboard"),
    "pricing": st.Page(pricing.render, title="Pricing & Contact", url_path="pricing"),
})
# Our own tab bar: Streamlit's top menu turns into a side menu on narrow screens.
page = st.navigation(list(PAGES.values()), position="hidden")
with st.container(key="topnav", horizontal=True, gap="small"):
    for p in PAGES.values():
        st.page_link(p, label=p.title)
active = [p.url_path for p in PAGES.values()].index(page.url_path) + 1
ui.html(f"<style>.st-key-topnav>div:nth-child({active}) a{{box-shadow:inset 0 -2px 0 var(--ember);}}"
        f".st-key-topnav>div:nth-child({active}) p{{color:var(--ink)!important;font-weight:600!important;}}</style>")
page.run()
ui.footer()
