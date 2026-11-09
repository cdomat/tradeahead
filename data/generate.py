"""Build every data file the app reads.

    python data/generate.py            # live FX from Yahoo Finance
    python data/generate.py --offline  # no network: saved snapshot, else simulated FX

Writes to data/:
    fx_history.csv     daily rates for the naira pairs (live, cleaned)
    fx_events.json     historical devaluations shown in the volatility chart
    markets.json       per-market assumptions (deposit rates, parallel premium, fees)
    dealers.json       licensed dealer panel (fictional names) used by routing
    dealer_fills.csv   simulated past fills that the Executable Rate Index learns from
and the sample invoice (PDF + CSV) to assets/samples/.

No importer or invoice data is generated for the Dashboard: users enter their own.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import fx, invoice  # noqa: E402

DATA = ROOT / "data"
SEED = 42

# ---------------------------------------------------------------- Market assumptions
# Editable. Sources or reasoning in "note". Rates are annual unless stated.
MARKETS = {
    "NG": {
        "name": "Nigeria", "local": "NGN", "symbol": "₦", "pairs": fx.NGN_PAIRS, "default_pair": "USD/NGN",
        "local_deposit_rate": 0.18, "usd_holding_rate": 0.02,
        "parallel_premium": 0.037, "baseline_bank_cost": 0.02, "settlement_days": 1, "late_cost_per_day": 0.003,
        "note": "Deposit rate: assumed SME naira deposit/money-market yield. Parallel premium: up to 3.7% "
                "saving from using authorized dealers instead of the parallel market. Bank cost: assumed all-in cost an SME pays its bank today. "
                "Late cost: daily cost of a "
                "delayed supplier payment (held cargo, demurrage), as a share of the payment.",
    },
}

TIERS = {
    "Starter": {"price_month": 0, "fx_margin": 0.010,
                "features": ["Invoice upload", "Exposure dashboard", "Manual conversions"]},
    "Growth": {"price_month": 49, "fx_margin": 0.008,
               "features": ["Protection plan", "Target-rate rules", "Direct supplier payment"]},
    "Pro": {"price_month": 149, "fx_margin": 0.006,
            "features": ["Cash-flow forecasting", "Routing reports", "Multi-user access"]},
}

# ---------------------------------------------------------------- Historical shocks
FX_EVENTS = [
    {"date": "2023-06-14", "pair": "USD/NGN", "title": "Nigeria unifies FX windows",
     "detail": "The central bank merges its exchange-rate windows. The official rate jumps from about "
               "₦461 to ₦750 per dollar within days."},
    {"date": "2023-12-29", "pair": "USD/NGN", "title": "₦900 by year end",
     "detail": "A $50,000 order that cost about ₦23m in May now costs about ₦45m."},
    {"date": "2024-02-01", "pair": "USD/NGN", "title": "Second naira slide",
     "detail": "The naira falls past ₦1,400 per dollar as dollar shortages persist."},
]

# ---------------------------------------------------------------- Dealer panel (fictional)
# spread_bps: quoted margin over mid by ticket bucket. fill_prob: chance the full amount
# arrives on time. delay_days: mean extra days when it does not. Buckets in USD.
BUCKETS = [(0, 10_000, "<10k"), (10_000, 50_000, "10-50k"), (50_000, 250_000, "50-250k"), (250_000, 1e12, "250k+")]

DEALERS = [
    {"id": "NG-B1", "market": "NG", "name": "Atlas Commercial Bank", "type": "Tier-1 bank",
     "min_ticket": 5_000, "max_ticket": 250_000, "daily_capacity": 400_000,
     "spread_bps": {"<10k": 140, "10-50k": 95, "50-250k": 70, "250k+": 60},
     "fill_prob": {"<10k": 0.62, "10-50k": 0.70, "50-250k": 0.80, "250k+": 0.85},
     "delay_days": 6.0, "slippage_bps": 15},
    {"id": "NG-B2", "market": "NG", "name": "Harbour Trust Bank", "type": "Tier-2 bank",
     "min_ticket": 2_000, "max_ticket": 100_000, "daily_capacity": 150_000,
     "spread_bps": {"<10k": 120, "10-50k": 90, "50-250k": 75, "250k+": 75},
     "fill_prob": {"<10k": 0.74, "10-50k": 0.78, "50-250k": 0.72, "250k+": 0.60},
     "delay_days": 4.0, "slippage_bps": 25},
    {"id": "NG-F1", "market": "NG", "name": "Kestrel FX", "type": "Licensed FX provider",
     "min_ticket": 500, "max_ticket": 60_000, "daily_capacity": 120_000,
     "spread_bps": {"<10k": 110, "10-50k": 100, "50-250k": 100, "250k+": 100},
     "fill_prob": {"<10k": 0.95, "10-50k": 0.92, "50-250k": 0.80, "250k+": 0.60},
     "delay_days": 1.5, "slippage_bps": 10},
    {"id": "NG-F2", "market": "NG", "name": "Lagoon Pay", "type": "Licensed payment provider",
     "min_ticket": 1_000, "max_ticket": 40_000, "daily_capacity": 80_000,
     "spread_bps": {"<10k": 90, "10-50k": 85, "50-250k": 85, "250k+": 85},
     "fill_prob": {"<10k": 0.88, "10-50k": 0.83, "50-250k": 0.70, "250k+": 0.50},
     "delay_days": 2.5, "slippage_bps": 30},
    {"id": "NG-B3", "market": "NG", "name": "Delta Merchant Bank", "type": "Merchant bank",
     "min_ticket": 20_000, "max_ticket": 500_000, "daily_capacity": 600_000,
     "spread_bps": {"<10k": 200, "10-50k": 80, "50-250k": 55, "250k+": 45},
     "fill_prob": {"<10k": 0.50, "10-50k": 0.66, "50-250k": 0.78, "250k+": 0.86},
     "delay_days": 8.0, "slippage_bps": 12},
]


def bucket_of(ticket: float) -> str:
    return next(name for lo, hi, name in BUCKETS if lo <= ticket < hi)


def simulate_fills(history: pd.DataFrame, n_per_dealer: int = 260, seed: int = SEED) -> pd.DataFrame:
    """Past fills for each dealer: quoted vs. executed rate and whether the dollars arrived on time.

    Delivery gets worse when the currency is under stress (high recent volatility),
    which is what the Rate Index has to learn from real fills.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for d in DEALERS:
        pair = MARKETS[d["market"]]["default_pair"]
        s = history[pair].dropna()
        s = s[s.index >= s.index.max() - pd.Timedelta(days=540)]
        stress = np.log(s).diff().rolling(20).std().bfill()
        stress_rank = stress.rank(pct=True)
        dates = np.sort(rng.choice(s.index, size=n_per_dealer, replace=True))
        tickets = np.exp(rng.uniform(np.log(max(d["min_ticket"], 500)), np.log(d["max_ticket"]), n_per_dealer))
        for dt, tk in zip(dates, tickets):
            b = bucket_of(tk)
            mid = float(s[dt])
            quoted = mid * (1 + d["spread_bps"][b] / 1e4)
            slip = max(0.0, rng.normal(d["slippage_bps"], d["slippage_bps"] * 0.8)) / 1e4
            p = np.clip(d["fill_prob"][b] - 0.25 * (stress_rank[dt] - 0.5), 0.05, 0.99)
            on_time = rng.random() < p
            filled_share = 1.0 if on_time else float(np.clip(rng.beta(2, 2), 0.1, 0.95))
            delay = 0 if on_time else int(max(1, round(rng.exponential(d["delay_days"]))))
            rows.append({
                "date": pd.Timestamp(dt).date(), "dealer_id": d["id"], "market": d["market"], "pair": pair,
                "ticket_usd": round(tk, -1), "bucket": b, "mid_rate": round(mid, 4),
                "quoted_rate": round(quoted, 4), "executed_rate": round(quoted * (1 + slip), 4),
                "on_time": on_time, "filled_share": round(filled_share, 3), "delay_days": delay,
            })
    return pd.DataFrame(rows)


def main(offline: bool = False) -> None:
    DATA.mkdir(exist_ok=True)

    wide, source = fx.load_history(prefer_live=not offline)
    if source != "Yahoo Finance (live)" and not offline:
        print(f"! Live fetch failed, using: {source}")
    fx.to_long(wide).to_csv(DATA / "fx_history.csv", index=False)

    calib = {p: fx.calibrate(wide[p]) for p in wide.columns}
    (DATA / "fx_calibration.json").write_text(json.dumps(
        {"source": source, "as_of": str(wide.index.max().date()), "pairs": calib}, indent=2))

    (DATA / "fx_events.json").write_text(json.dumps(FX_EVENTS, indent=2, ensure_ascii=False))
    (DATA / "markets.json").write_text(json.dumps({"markets": MARKETS, "tiers": TIERS,
                                                   "buckets": [[lo, hi, n] for lo, hi, n in BUCKETS]},
                                                  indent=2, ensure_ascii=False))
    (DATA / "dealers.json").write_text(json.dumps(DEALERS, indent=2))
    fills = simulate_fills(wide)
    fills.to_csv(DATA / "dealer_fills.csv", index=False)

    invoice.SAMPLES.mkdir(parents=True, exist_ok=True)
    inv = invoice.sample_invoice()
    (invoice.SAMPLES / "sample_proforma_invoice_USD.pdf").write_bytes(invoice.build_pdf(inv))
    (invoice.SAMPLES / "sample_proforma_invoice_USD.csv").write_bytes(invoice.build_csv(inv))

    usd = calib["USD/NGN"]
    print(f"FX source: {source}, {len(wide):,} days to {wide.index.max().date()}")
    print(f"USD/NGN spot {usd['spot']:,.2f} · annual vol {usd['annual_vol']:.1%} · "
          f"1y drift {usd['annual_drift']:+.1%}")
    print(f"Dealers: {len(DEALERS)} · simulated fills: {len(fills):,}")
    print(f"Sample invoice: {inv.currency} {inv.total:,.2f}, balance {inv.balance:,.2f} due {inv.balance_due}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="skip Yahoo Finance and simulate FX history")
    main(ap.parse_args().offline)
