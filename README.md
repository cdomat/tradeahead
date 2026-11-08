# TradeAhead

FX protection and payment planning for importing SMEs in volatile-currency markets, starting with Nigeria.
Importers owe suppliers in USD or CNY but earn in naira; a devaluation between order and payment can double
the bill. TradeAhead converts the invoice balance in daily tranches **within the cash the firm actually has**,
routes each tranche to the licensed dealer most likely to **deliver on time**, and pays the supplier on the
bill of lading. No derivatives; licensed partners hold and move funds.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python data/generate.py      # live FX snapshot + reference data (--offline to skip the network)
streamlit run app.py         # pytest -q runs the 28 engine and app tests
```

## Data sourcing

| Source | Content | Module |
|---|---|---|
| Yahoo Finance (`yfinance`) | Daily closes for the USD legs `NGN=X, EGP=X, CNY=X, EUR=X, GBP=X, CAD=X`, 2021→today | `services/fx.py` |
| Derived | Pairs `USD/NGN, CNY/NGN, EUR/NGN, GBP/NGN, CAD/NGN` and `USD/EGP, CNY/EGP, EUR/EGP` | `services/fx.py` |
| Curated | Devaluation events (2022–2026) annotated on the history chart | `data/fx_events.json` |
| Simulated | 8 fictional licensed dealers and 2,080 historical fills | `data/dealers.json`, `data/dealer_fills.csv` |
| Assumptions | Deposit rates, parallel premium, bank cost, late-payment cost, tier margins | `data/markets.json` |

**Crosses.** Yahoo lists no direct naira crosses, so every pair is built from two USD legs quoted as units per USD:
$\text{XXX/NGN}_t = \text{NGN=X}_t \,/\, \text{XXX=X}_t$. Adding a currency is one entry in `USD_LEGS` and the
market's pair list.

**Cleaning.** Forward-fill gaps, drop weekend rows, then remove reverting spikes: a close $s_t$ is replaced by
its centred 5-day median $\tilde s_t$ when $|s_t/\tilde s_t - 1| > 7\%$. A genuine devaluation persists, so the
median follows it and it survives; a one-day glitch does not.

**Fallback chain.** live fetch (cached 1 h in the app) → `data/fx_history.csv` snapshot → synthetic series
anchored to the historical path (₦461 → ₦750 → ₦900 → ₦1,420 …). The UI labels which source is in use.

**Calibration.** From log returns $r_t = \ln(s_t/s_{t-1})$:

- *Jumps (devaluations):* days with $r_t > 5\%$ over the full history give frequency $p$, mean $\mu_J$ and
  dispersion $\sigma_J$ (USD/NGN 2021–26: 14 jumps, $p \approx 0.009$/day, $\mu_J \approx 12\%$).
- *Diffusion:* $\sigma$ = standard deviation of non-jump returns over the last 250 days.
- *Drift:* default **market-implied**, which sets the expected path to grow at the interest gap
  $g = r_{\text{local}} - r_{\text{USD}}$ (what a fair forward prices), so the plan never bets on direction:

$$\mu = \tfrac{g}{252} - \tfrac12\sigma^2 - \ln\!\big(1 + p\,(e^{\mu_J + \sigma_J^2/2} - 1)\big)$$

  Alternatives: *historical* (jumps add drift with no offset, a stress view) and *recent* (trailing mean).

**Dealer fills.** Each fill records ticket, mid, quoted and executed rate, on-time flag, filled share and delay.
On-time probability falls with currency stress (20-day realised-volatility rank), which the Rate Index must learn.

## Engine

### 1 · Cash-constrained protection plan · `engine/plan.py`

Buy $x_t \ge 0$ units of foreign currency on trading days $t = 0..N-1$ ahead of the due date $N$. With expected
rate $F_t = \mathbb E[S_t]$, all-in conversion cost $c$, open position $U_k = A - \sum_{s \le k} x_s$ and daily
variance $v = \sigma^2 + p(\sigma_J^2 + \mu_J^2) - p^2\mu_J^2$:

$$
\min_x \;\underbrace{\sum_t x_t F_t (1+c)}_{\text{conversion}}
+ \underbrace{\sum_t x_t F_t\,(r_{\text{local}} - r_{\text{USD}})\tfrac{N-t}{252}}_{\text{interest given up}}
+ \underbrace{\lambda\,\tfrac{S_0\,v}{A}\sum_k U_k^2}_{\text{FX risk}}
+ \underbrace{S_0\,\eta \sum_t x_t^2}_{\text{daily-size impact}}
$$

$\sum_k U_k^2$ is the variance of the final cost, so the problem is the Almgren–Chriss execution trade-off with
the importer's cash as a hard constraint:

- **Target:** $\sum_t x_t = \rho A$ (cover ratio $\rho$), no purchases after $N - 1 - \text{settlement}$.
- **Buffer, never breached:** $\sum_{s \le t} x_s \tilde S_s \le C_0 - B + \sum_{s \le t}(\text{in}_s - \text{out}_s)\;\forall t$,
  priced at a stressed rate $\tilde S_t = F_t\,e^{1.65\sqrt{v t}}\,(1 + c + \eta\,x_{\max})$, the 95% bad case.
- **Capacity:** $0 \le x_t \le x_{\max}$; impact $\eta = 25\text{ bps} / \$50\text{k}$ of daily volume.

Solved with SLSQP (analytic gradients). If even buying as early as cash allows (a greedy pass) cannot reach
$\rho A$, the target drops to that maximum and the shortfall is reported as a **coverage gap**. Tranches are then
rounded to $100, sub-minimum tickets ($1k) roll forward, and cash is re-checked at every step. Risk appetite
$\lambda \in \{0.3, 3, 10, 30\}$ moves the schedule from even daily buying to front-loaded.

### 2 · Executable Rate Index and delivery-weighted routing · `engine/routing.py`

**Index (per dealer × ticket bucket <10k / 10–50k / 50–250k / 250k+).** Fill weight $w = 0.5^{\text{age}/90\text{d}}$.

- Executable markup $m = \sum w\,(\text{exec}/\text{mid} - 1) / \sum w$, quoted markup and slippage likewise.
- On-time probability: Beta posterior with a cautious prior (mean 0.70, strength 6):
  $p = \dfrac{4.2 + \sum w\,\mathbb 1_{\text{on time}}}{6 + \sum w}$, plus its 5% quantile as the worst case.
- Given a late fill: weighted mean delay $d$ and filled share $\phi$.

**Score.** For mid $M$, tier margin $\mu_{TA}$ (1.0 / 0.8 / 0.6%), slack $s$ = trading days left before the
payment is late, fallback premium $f = 150$ bps and late cost $\ell = 0.3\%$ per day:

$$
\text{all-in} = M(1 + m + \mu_{TA}) + M(1-p)\Big[(1-\phi)\big(f + 0.8\,\sigma_d\sqrt d + \bar g\,d\big) + \max(d - s, 0)\,\ell\Big]
$$

The second term is the expected cost of a failed fill: re-buying the unfilled share at short notice after the
rate has moved ($0.8 \approx \mathbb E|Z|$), plus every day the supplier payment runs late. Shrinking slack
penalises slow dealers, so the cheapest quote is often not the best route.

**Allocation.** Greedy by all-in rate within each dealer's ticket limits and daily capacity; tickets above $20k
are split so no dealer takes more than 60%. The cost **waterfall** decomposes the plan's local cost into
mid-market + dealer spread + slippage + TradeAhead margin + expected delivery cost = all-in.

### 3 · Savings and Monte Carlo · `engine/market.py`, `engine/savings.py`

**Paths.** 20,000 jump-diffusion paths over the plan horizon, with devaluation-only jumps:

$$\ln S_{t+1} - \ln S_t = \varepsilon_t + B_t\max(J_t, 0),\quad \varepsilon_t \sim \mathcal N(\mu,\sigma^2),\; B_t \sim \text{Bern}(p),\; J_t \sim \mathcal N(\mu_J,\sigma_J^2)$$

Simulator sliders scale $\sigma$ and $p$ (and switch drift mode); the shock test adds a deterministic
overnight devaluation of size $k$ on day $d$.

**Cost per path** (local currency, residual $R = A - \sum x_t$ bought on day $N$):

$$C^{\text{plan}} = \sum_t x_t S_t\Big(1 + c + \eta x_t + (r_{\text{local}} - r_{\text{USD}})\tfrac{N-t}{252}\Big) + R\,S_N(1 + c + \eta R)$$

$c$ is the realised all-in cost of the routed plan.

**Baselines** (full balance at the due-date rate): bank $A S_N(1 + c_{\text{bank}})$ with $c_{\text{bank}} = 2\%$;
parallel market $A S_N(1 + c_{\text{bank}} + 3.7\%)$. Also compared: wait, buy everything today, equal daily amounts.

**Reported:** mean, median, 5/95th percentiles and worst path of cost; savings $=C^{\text{base}} - C^{\text{plan}}$
as mean, share of paths where the plan is cheaper, and **tail savings**, the mean saving on paths where $S_N$ is in
its top 5%. Against a bank that delivers, protection costs a little on average (interest given up) and pays
off in the tail. Against the parallel market it is cheaper in most outcomes.

---
Dealer names and fills are simulated; market assumptions are editable in `data/generate.py`.
