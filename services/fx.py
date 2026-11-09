"""Live FX data from Yahoo Finance (yfinance), with cleaning and an offline fallback.

Yahoo quotes most currencies against the US dollar ("NGN=X" = naira per dollar).
Direct naira crosses such as CNY/NGN are not listed, so every pair is built from
its two dollar legs:  XXX/NGN = (NGN per USD) / (XXX per USD).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "fx_history.csv"

# Units of each currency per 1 USD, as Yahoo lists them.
USD_LEGS = {"NGN": "NGN=X", "CNY": "CNY=X", "EUR": "EUR=X", "GBP": "GBP=X", "CAD": "CAD=X"}

# Nigeria: five foreign currencies Nigerian importers pay in, quoted in naira.
NGN_PAIRS = ["USD/NGN", "CNY/NGN", "EUR/NGN", "GBP/NGN", "CAD/NGN"]
ALL_PAIRS = NGN_PAIRS


def _clean(series: pd.Series, jump: float = 0.07) -> pd.Series:
    """Remove one-day spikes that reverse, keep real devaluations.

    A real devaluation stays at the new level, so the centred 5-day median
    follows it. A data glitch sits far from that median for a day or two and
    is replaced by it.
    """
    s = series.astype(float).ffill()
    med = s.rolling(5, center=True, min_periods=1).median()
    spike = (s / med - 1).abs() > jump
    return s.mask(spike, med)


def _crosses(legs: pd.DataFrame) -> pd.DataFrame:
    """Turn dollar legs (units per USD) into the pairs the app shows."""
    out = {}
    for pair in ALL_PAIRS:
        base, quote = pair.split("/")
        q = legs[quote]
        out[pair] = q if base == "USD" else q / legs[base]
    return pd.DataFrame(out)


def fetch_live(start: str = "2021-01-01") -> pd.DataFrame:
    """Download dollar legs from Yahoo and return a wide frame: date index, one column per pair."""
    import yfinance as yf  # imported here so the rest of the app runs without it

    raw = yf.download(list(USD_LEGS.values()), start=start, progress=False, auto_adjust=False)["Close"]
    if raw.empty:
        raise RuntimeError("Yahoo Finance returned no data")
    legs = raw.rename(columns={v: k for k, v in USD_LEGS.items()})
    legs = legs.sort_index().ffill().dropna(how="any")
    legs = legs.apply(_clean)
    legs.index = pd.to_datetime(legs.index).tz_localize(None).normalize()
    legs = legs[legs.index.day_of_week < 5]  # Yahoo sometimes adds a weekend row
    return _crosses(legs).round(4)


def to_long(wide: pd.DataFrame) -> pd.DataFrame:
    df = wide.rename_axis("date").reset_index().melt(id_vars="date", var_name="pair", value_name="rate")
    return df.dropna().sort_values(["pair", "date"]).reset_index(drop=True)


def load_history(prefer_live: bool = True, start: str = "2021-01-01") -> tuple[pd.DataFrame, str]:
    """Return (wide history, source label). Tries live data, then the saved snapshot, then synthetic."""
    if prefer_live:
        try:
            return fetch_live(start), "Yahoo Finance (live)"
        except Exception:
            pass
    if SNAPSHOT.exists():
        long = pd.read_csv(SNAPSHOT, parse_dates=["date"])
        wide = long.pivot(index="date", columns="pair", values="rate")
        return wide[[p for p in ALL_PAIRS if p in wide.columns]], "Saved snapshot"
    return synthetic_history(), "Simulated"


def synthetic_history(seed: int = 7) -> pd.DataFrame:
    """Offline stand-in that follows the real naira path at anchor points."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-01-01", pd.Timestamp.today().normalize())
    anchors = {
        "NGN": [("2021-01-01", 380), ("2023-06-13", 461), ("2023-06-16", 750), ("2023-12-29", 897),
                ("2024-02-01", 1420), ("2024-09-30", 1667), ("2025-06-30", 1542), ("2026-11-06", 1345)],
    }
    legs = {}
    for ccy, pts in anchors.items():
        idx = pd.to_datetime([d for d, _ in pts])
        base = pd.Series([v for _, v in pts], index=idx).reindex(dates.union(idx)).interpolate("time").reindex(dates)
        noise = np.exp(np.cumsum(rng.normal(0, 0.003, len(dates))))
        noise /= pd.Series(noise).rolling(20, min_periods=1).mean().to_numpy()
        legs[ccy] = base.to_numpy() * noise
    legs["CNY"] = 6.9 + 0.2 * np.sin(np.linspace(0, 6, len(dates)))
    legs["EUR"] = 0.90 + 0.04 * np.sin(np.linspace(1, 7, len(dates)))
    legs["GBP"] = 0.77 + 0.03 * np.sin(np.linspace(2, 8, len(dates)))
    legs["CAD"] = 1.33 + 0.04 * np.sin(np.linspace(0, 5, len(dates)))
    return _crosses(pd.DataFrame(legs, index=dates)).round(4)


def calibrate(series: pd.Series, lookback_days: int = 250, ewma_lambda: float = 0.94) -> dict:
    """Daily volatility and drift for the simulator, from recent history.

    Volatility uses an EWMA of squared log returns (RiskMetrics style), so recent
    turbulence counts more. Drift is the average daily log change over the lookback.
    """
    r = np.log(series.dropna()).diff().dropna().tail(lookback_days)
    w = ewma_lambda ** np.arange(len(r))[::-1]
    vol = float(np.sqrt(np.sum(w * r.to_numpy() ** 2) / w.sum()))
    return {
        "spot": float(series.dropna().iloc[-1]),
        "daily_vol": vol,
        "annual_vol": vol * np.sqrt(252),
        "daily_drift": float(r.mean()),
        "annual_drift": float(r.mean() * 252),
        "max_1d_move": float(r.max()),
    }


def worst_moves(series: pd.Series, horizon_days: int = 30, top: int = 5) -> pd.DataFrame:
    """Largest rises in the rate over any `horizon_days` window: the shocks a plan protects against."""
    s = series.dropna()
    fwd = s.shift(-horizon_days) / s - 1
    rows, used = [], set()
    for d, move in fwd.sort_values(ascending=False).items():
        if len(rows) == top or pd.isna(move):
            break
        if any(abs((d - u).days) < horizon_days * 1.5 for u in used):
            continue
        used.add(d)
        rows.append({"start": d.date(), "rate_start": s[d], "rate_end": s.shift(-horizon_days)[d], "move": move})
    return pd.DataFrame(rows)
