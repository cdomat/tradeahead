# TradeAhead

🚀 **Live App:** [Launch TradeAhead Streamlit Dashboard](YOUR_STREAMLIT_URL_HERE)

## Overview

TradeAhead is an FX planning and protection platform for importing SMEs in emerging markets, starting with Nigeria and then Egypt.
It turns a supplier invoice into a daily currency-buying plan the business can afford, sends each purchase to the dealer most likely to deliver on time, and pays the supplier when the goods ship.

## Quick Start

```bash
git clone https://github.com/cdomat/TradeAhead.git && cd TradeAhead
pip install -r requirements.txt
streamlit run app.py
```

Optional: `pytest -q` runs the test suite, and `python data/generate.py` refreshes the saved FX snapshot and reference data.

## Data Pipeline & FX Engine

**Live rates.** `yfinance` downloads daily closing rates from Yahoo Finance for the US dollar against the naira, Egyptian pound, yuan, euro, pound sterling and Canadian dollar, from 2021 to today.

**Pairs the app shows.** Yahoo has no direct naira or pound crosses (for example, there is no CNY/NGN). We build each one from its two dollar rates: CNY/NGN = naira per dollar ÷ yuan per dollar.
- Naira: USD, CNY, EUR, GBP, CAD → NGN.
- Egyptian pound: USD, CNY, EUR → EGP.

Adding a currency takes one line in `services/fx.py`.

**Spike cleaning.** Free data feeds sometimes show one-day glitches. Each day's rate is compared with the median of the five days around it:
- If it sits more than 7% away, it is treated as a glitch and replaced with that median.
- A real devaluation stays at its new level, so the median moves with it and the move is kept.

**Fallback chain.** If the live feed fails, the app uses the saved snapshot in `data/fx_history.csv`. If that is missing too, it uses a simulated series that follows the real historical path. The app always labels which source is in use.

**Volatility and shocks.** History is split into two behaviours:
- **Normal daily movement:** estimated from the last year, with recent days weighted more.
- **Sudden devaluations:** days where the rate jumped more than 5%. These give how often they happen and how big they tend to be (USD/NGN since 2021: 14 such days, about 12% on average).

By default, the forecast assumes the rate drifts in line with the interest-rate gap between the two currencies. This is the same assumption a fair forward price makes, so the plan never bets on which way the rate will move. On the Dashboard, users can raise volatility, make devaluations more frequent, or apply an overnight devaluation of any size on any day.

## Core Methodology

### 1. Cash-Constrained Protection Plan (`engine/plan.py`)

An importer owes, say, $35,000 in 20 trading days, but holds naira and cannot convert it all today. The plan decides how many dollars to buy on each day, balancing four costs:

- **FX risk:** every dollar still unbought is exposed to a devaluation, which favours buying early.
- **Interest given up:** naira converted early stops earning local deposit interest, which favours buying late.
- **Conversion cost:** dealer spread plus TradeAhead's margin on each purchase.
- **Market impact:** very large single-day purchases get worse rates, which favours spreading purchases out.

**The cash buffer is a hard rule.** The business sets a minimum operating balance, and no purchase may take cash below it. Planned purchases are costed at a pessimistic rate (a 1-in-20 bad outcome), so the buffer still holds if the naira weakens during the plan. Expected customer receipts and expenses are included day by day.

**If cash is not enough** to cover the whole invoice, the plan covers as much as it safely can and reports the rest as a **coverage gap**. That gap is the opening for a future financing product.

**Practical tidying.** Purchases are rounded to $100, kept above a $1,000 minimum ticket, and never placed in the final settlement days.

**Risk appetite** ("Accept risk" → "Maximum protection" on the Dashboard) controls how strongly the plan front-loads its purchases versus spreading them evenly.

### 2. Delivery-Weighted Routing (`engine/routing.py`)

In volatile markets, the cheapest quote is often not the cheapest outcome. A dealer that delivers late or only partly fills the order forces a rushed re-purchase at a worse rate, and can make the supplier payment late.

**Executable Rate Index.** For every dealer and ticket size (under $10k, $10–50k, $50–250k, over $250k), we track from past trades:
- the rate actually executed versus the rate quoted;
- how often delivery was on time;
- how late and how complete the late deliveries were.

Recent trades count more (a 90-day half-life). Dealers with little history start from a cautious 70% on-time assumption.

**Ranking.** Each dealer's true cost is calculated as:
- the executed rate, plus TradeAhead's margin, **plus the expected cost of a failed delivery**;
- that last item covers re-buying the missing amount at short notice, plus a 0.3% cost for every day the supplier payment runs late.

The less time left before the payment is due, the more heavily unreliable dealers are penalised. On the sample invoice, Kestrel FX ranks 4th on quoted price but 1st once delivery is counted.

**Splitting.** Orders over $20,000 are split so that no single dealer takes more than 60%, which limits dependence on any one counterparty. A cost waterfall shows where every naira goes: mid-market rate, dealer spread, slippage, TradeAhead margin and expected delivery cost.

### 3. Savings & Risk Simulation (`engine/market.py`, `engine/savings.py`)

We simulate **20,000 possible paths** for the exchange rate up to the payment date, including random devaluations at their historical frequency and size. On every path we cost:

| Strategy | What it means |
|---|---|
| **TradeAhead plan** | The optimised daily purchases, at the routed all-in cost |
| **Unprotected (bank)** | Buy the full amount on the due date at bank spot, plus a 2% bank cost |
| **Unprotected (parallel market)** | Same, at the parallel-market rate (about 3.7% above official) |
| Wait / buy all today / equal daily amounts | Simple alternatives, for comparison |

**Reported results:**
- average and typical cost, with best and worst cases;
- the share of paths where TradeAhead is cheaper;
- **tail savings**, the average saving in the worst 5% of currency outcomes.

The results show protection as insurance:
- **Against a bank that delivers:** it costs slightly more on average, because naira converted early stops earning interest. It saves heavily when a devaluation hits.
- **Against the parallel market,** where most SMEs actually buy: it is cheaper in the large majority of outcomes.

## Project Architecture

```
TradeAhead/
├── app.py                 Entry point: page setup, styling, top navigation
├── views/                 One file per page
│   ├── company.py         Landing page and FX volatility simulator
│   ├── product.py         Interactive demos of the three core elements
│   ├── dashboard.py       Invoice Workbench, Plan, Routing, Payments, Savings
│   └── pricing.py         Pricing tiers and contact form
├── engine/                Financial logic (no UI code, fully tested)
│   ├── market.py          Volatility, devaluation model, rate simulation
│   ├── plan.py            Cash-constrained protection plan
│   ├── routing.py         Executable Rate Index and dealer ranking
│   └── savings.py         Monte Carlo comparison against baselines
├── services/              Data and glue between engine and pages
│   ├── fx.py              Live FX fetch, cleaning, fallback
│   ├── invoice.py         Sample invoice builder and upload reader
│   └── workspace.py       Runs a full dashboard case and caches it
├── ui/                    Reusable visual pieces
│   ├── components.py      Cards, KPIs, tables, alerts
│   └── charts.py          Branded Plotly charts
├── data/                  FX snapshot, market assumptions, simulated dealers
│   └── generate.py        Rebuilds every data file
├── assets/                Stylesheet, logo, sample invoices
└── tests/                 Engine and page tests
```

Dependencies point one way: pages → services and UI → engine. The engine can therefore be reused behind an API later without changes.

---
Dealer names and trade history are simulated for demonstration. FX rates are live. Market assumptions can be edited in `data/generate.py`.
