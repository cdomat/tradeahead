# TradeAhead

🚀 **Live App:** [tradeahead.streamlit.app](https://tradeahead.streamlit.app)

## Overview

TradeAhead helps importing SMEs in emerging markets pay foreign suppliers at a cost they can plan, starting with a pilot in Nigeria.
It turns a supplier invoice into a daily currency-buying plan the business can afford, sends each purchase to the dealer most likely to deliver on time, and pays the supplier when the goods ship.

The Dashboard follows one invoice through four steps: **Invoice & cash → Your plan → Best route → Supplier payment**.

## Data Pipeline & FX Engine

**Live rates.** `yfinance` downloads daily closing rates from Yahoo Finance for the US dollar against the naira, yuan, euro, pound sterling and Canadian dollar, from 2021 to today.

**Naira pairs.** Yahoo has no direct naira crosses, so each pair is built from its two dollar rates: CNY/NGN = naira per dollar ÷ yuan per dollar. This gives USD, CNY, EUR, GBP and CAD → NGN.

**Spike cleaning.** Each day's rate is compared with the median of the five days around it. A rate more than 7% away is treated as a data glitch and replaced with that median; a real devaluation stays, because the median moves with it.

**Fallback.** If the live feed fails, the app uses the saved snapshot in `data/fx_history.csv`, and if that is missing, a simulated series that follows the historical path. The app always labels which source is in use.

**Volatility and devaluations.** History is split into two behaviours:
- **Normal daily movement:** the spread of calm days over the last year.
- **Sudden devaluations:** days where the rate jumped more than 5%, which give how often they happen and how big they are (USD/NGN since 2021: 14 such days, about 12% on average).

The forecast assumes the rate drifts in line with the interest-rate gap between the two currencies, as a fair forward price would, so the plan never bets on the direction of the rate.

## Core Methodology

### 1. Cash-Constrained Protection Plan (`engine/plan.py`)

An importer owes, say, $35,000 in 20 trading days but holds naira and cannot convert it all today. The plan sets how many dollars to buy each day, balancing:

- **Exchange-rate risk:** every dollar still unbought is exposed to a devaluation, which favours buying early.
- **Interest given up:** naira converted early stops earning deposit interest, which favours buying late.
- **Conversion cost:** dealer spread plus TradeAhead's margin.
- **Market impact:** very large single-day purchases get worse rates, which favours spreading them out.

**The cash buffer is a hard rule.** No purchase may take cash below the minimum the business sets. Purchases are costed at a pessimistic rate (a 1-in-20 bad outcome), so the buffer holds even if the naira weakens during the plan. Expected monthly receipts and payments are included.

**If cash is not enough** to cover the whole invoice, the plan covers as much as it safely can and reports the rest as a **coverage gap**. That gap is the opening for a future financing product.

**Practical rules.** Purchases are rounded to $100, kept above a $1,000 minimum, and never placed in the final settlement days. The user chooses how much of the balance to protect and **when to buy** (Flexible, Balanced or Lock in early), which sets how strongly the plan front-loads purchases. Nothing is bought until the user approves the plan.

### 2. Delivery-Weighted Routing (`engine/routing.py`)

In volatile markets the cheapest quote is often not the cheapest outcome: a dealer that delivers late or only partly fills the order forces a rushed re-purchase at a worse rate, and can make the supplier payment late.

**Executable Rate Index.** For every dealer and ticket size (under $10k, $10–50k, $50–250k, over $250k), past trades give:
- the rate actually executed versus the rate quoted;
- how often delivery was on time, and how late and complete late deliveries were.

Recent trades count more (90-day half-life). Dealers with little history start from a cautious 70% on-time assumption.

**Ranking.** Each dealer's true cost is the executed rate plus TradeAhead's margin **plus the expected cost of a failed delivery**: re-buying the missing amount at short notice, plus 0.3% for every day the supplier payment runs late. The closer the due date, the more unreliable dealers are penalised. On the sample invoice, Kestrel FX ranks 4th on quoted price but 1st once delivery is counted.

**Splitting.** Purchases over $20,000 are split so that no single dealer takes more than 60%. The Dashboard shows where the conversion cost comes from: dealer spread, TradeAhead fee and the allowance for late deliveries.

### 3. Savings Simulation (`engine/market.py`, `engine/savings.py`)

We simulate **20,000 paths** for the exchange rate up to the due date, including devaluations at their historical frequency and size, and cost each way of buying on every path:

| Way of buying | What it means |
|---|---|
| **TradeAhead plan** | The planned daily purchases, at the routed all-in cost |
| **Unprotected (bank)** | Buy everything on the due date at bank spot, plus a 2% bank cost |
| **Unprotected (parallel market)** | Same, at the parallel-market rate (about 3.7% above official) |
| Wait / buy everything today / equal daily amounts | Simple alternatives, for comparison |

The Dashboard reports the typical saving against the parallel market, the saving in a bad month (worst 1 in 20), and the saving if the naira falls overnight by a chosen amount. Protection works like insurance: against a bank that delivers on time it costs a little on average (the naira interest given up), and it saves heavily when a devaluation hits. Against the parallel market, where most SMEs actually buy, it is cheaper in the large majority of outcomes.

## Project Architecture

```
TradeAhead/
├── app.py                 Entry point: page setup, styling, top navigation
├── views/                 One file per page
│   ├── company.py         Landing page and devaluation simulator
│   ├── product.py         The four steps, with live demos
│   ├── dashboard.py       Invoice & cash, Your plan, Best route, Supplier payment
│   └── pricing.py         Plans, cost calculator and contact form
├── engine/                Financial logic (no UI code, fully tested)
│   ├── market.py          Volatility, devaluation model, rate simulation
│   ├── plan.py            Cash-constrained protection plan
│   ├── routing.py         Executable Rate Index and dealer ranking
│   └── savings.py         Comparison against unprotected buying
├── services/              Data and glue between engine and pages
│   ├── fx.py              Live FX fetch, cleaning, fallback
│   ├── invoice.py         Sample invoice builder and upload reader
│   └── workspace.py       Runs a full Dashboard case and caches it
├── ui/                    Reusable visual pieces
│   ├── components.py      Cards, KPIs, tables, alerts, icons
│   └── charts.py          Plotly charts in the TradeAhead style
├── data/                  FX snapshot, market assumptions, simulated dealers
│   └── generate.py        Rebuilds every data file
├── assets/                Stylesheet, logo, sample invoices
└── tests/                 Engine and page tests
```

Dependencies point one way: pages → services and UI → engine, so the engine can later sit behind an API unchanged.

---
Dealer names and trade history are simulated for demonstration. FX rates are live. Market assumptions are set in `data/generate.py`.
