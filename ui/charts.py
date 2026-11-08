"""Plotly charts in the TradeAhead style. One y-axis per chart; colour never carries meaning alone."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

INK, TEAL, TEAL_L, EMBER, AMBER = "#0F2A2E", "#3F6B70", "#BFD0CC", "#B8501E", "#E39A4F"
MUTED, GRID, PAPER, LINE, NEG, POS = "#5E696B", "#E9E5DA", "#FDFCF8", "#DDD8CC", "#A8382A", "#2E7D5B"
FONT = "DM Sans, system-ui, sans-serif"

pio.templates["tradeahead"] = go.layout.Template(layout=dict(
    font=dict(family=FONT, size=12.5, color=INK),
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    colorway=[EMBER, INK, TEAL, AMBER],
    margin=dict(l=10, r=10, t=40, b=10),
    xaxis=dict(automargin=True, showgrid=False, linecolor=LINE, ticks="outside", tickcolor=LINE, tickfont=dict(color=MUTED),
               zeroline=False),
    yaxis=dict(automargin=True, gridcolor=GRID, zeroline=False, linecolor="rgba(0,0,0,0)", tickfont=dict(color=MUTED)),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, bgcolor="rgba(0,0,0,0)",
                font=dict(color=INK)),
    hoverlabel=dict(bgcolor=PAPER, bordercolor=LINE, font=dict(family=FONT, color=INK, size=12.5)),
    hovermode="x unified",
))
pio.templates.default = "tradeahead"

CONFIG = {"displayModeBar": False, "responsive": True}


def _short(v: float) -> str:
    a = abs(v)
    for d, u in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if a >= d:
            return f"{v / d:,.1f}{u}"
    return f"{v:,.0f}"


# ---------------------------------------------------------------- Company page

def fx_history(series: pd.Series, pair: str, events: list[dict], height: int = 380) -> go.Figure:
    s = series.dropna()
    fig = go.Figure(go.Scatter(x=s.index, y=s.values, mode="lines", line=dict(color=INK, width=2),
                               name=pair, hovertemplate="%{y:,.2f}<extra></extra>"))
    for e in events:
        d = pd.Timestamp(e["date"])
        if e["pair"] != pair or d < s.index.min() or d > s.index.max():
            continue
        y = float(s.asof(d))
        fig.add_trace(go.Scatter(x=[d], y=[y], mode="markers", showlegend=False,
                                 marker=dict(size=11, color=EMBER, line=dict(color=PAPER, width=2)),
                                 hovertemplate=f"<b>{e['title']}</b><br>{e['detail']}<extra></extra>"))
        fig.add_annotation(x=d, y=y, text=e["title"], showarrow=True, arrowhead=0, arrowcolor=LINE,
                           ax=-10, ay=-38, font=dict(size=11, color=INK), bgcolor=PAPER, bordercolor=LINE,
                           borderpad=3)
    fig.update_layout(height=height, showlegend=False, hovermode="closest",
                      yaxis_title=f"{pair.split('/')[1]} per {pair.split('/')[0]}")
    return fig


# ---------------------------------------------------------------- Protection plan

def accumulation(schedule: pd.DataFrame, target: float, amount: float, ccy: str, height: int = 330) -> go.Figure:
    """Daily purchases (bars) and protected total (line) against the target."""
    d = pd.to_datetime(schedule["date"])
    fig = go.Figure()
    fig.add_trace(go.Bar(x=d, y=schedule["buy_fx"], name=f"Bought that day ({ccy})", marker_color=TEAL_L,
                         marker_line_width=0, hovertemplate="%{y:,.0f}<extra>Bought</extra>"))
    fig.add_trace(go.Scatter(x=d, y=schedule["cum_fx"], name="Protected so far", mode="lines+markers",
                             line=dict(color=EMBER, width=2.5), marker=dict(size=7),
                             hovertemplate="%{y:,.0f}<extra>Protected</extra>"))
    fig.add_hline(y=target, line=dict(color=INK, width=1.5, dash="dash"),
                  annotation_text=f"Target {target:,.0f}", annotation_position="top left",
                  annotation_font=dict(color=INK, size=11))
    if amount > target:
        fig.add_hline(y=amount, line=dict(color=MUTED, width=1, dash="dot"),
                      annotation_text=f"Invoice balance {amount:,.0f}", annotation_position="top left",
                      annotation_font=dict(color=MUTED, size=11))
    fig.update_layout(height=height, bargap=0.35, yaxis_title=ccy, yaxis_rangemode="tozero")
    return fig


def cash_vs_buffer(schedule: pd.DataFrame, buffer: float, local: str, height: int = 300) -> go.Figure:
    """Expected cash after each day's purchase, against the buffer that must never be touched."""
    d = pd.to_datetime(schedule["date"])
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=d, y=schedule["cash_after"], name="Cash after purchases", mode="lines",
                             line=dict(color=TEAL, width=2.5, shape="hv"), fill="tozeroy",
                             fillcolor="rgba(191,208,204,.35)", hovertemplate="%{y:,.0f}<extra>Cash</extra>"))
    fig.add_hline(y=buffer, line=dict(color=EMBER, width=2, dash="dash"),
                  annotation_text=f"Cash buffer {_short(buffer)}", annotation_position="bottom left",
                  annotation_font=dict(color=EMBER, size=11))
    lo = min(schedule["cash_after"].min(), buffer)
    fig.update_layout(height=height, yaxis_title=local, showlegend=False,
                      yaxis_range=[lo * 0.85, schedule["cash_after"].max() * 1.05])
    return fig


# ---------------------------------------------------------------- Routing

def rank_slope(ranked: pd.DataFrame, height: int = 340) -> go.Figure:
    """Quoted-price rank vs delivery-weighted rank. Rank 1 at the top."""
    fig = go.Figure()
    n = len(ranked)
    for _, r in ranked.iterrows():
        best = r["route_rank"] == 1
        color = EMBER if best else (TEAL if r["route_rank"] < r["screen_rank"] else MUTED)
        fig.add_trace(go.Scatter(
            x=[0, 1], y=[r["screen_rank"], r["route_rank"]], mode="lines+markers",
            line=dict(color=color, width=3 if best else 2), marker=dict(size=11, line=dict(color=PAPER, width=2)),
            showlegend=False, hovertemplate=(f"<b>{r['dealer']}</b><br>Quoted rank {r['screen_rank']} → "
                                             f"delivery-weighted rank {r['route_rank']}<extra></extra>")))
        fig.add_annotation(x=0, y=r["screen_rank"], text=f"{r['dealer']}  ", xanchor="right", showarrow=False,
                           font=dict(size=12, color=INK))
        fig.add_annotation(x=1, y=r["route_rank"], text=f"  {r['dealer']} · {r['on_time_p']:.0%} on time",
                           xanchor="left", showarrow=False,
                           font=dict(size=12, color=INK, weight=700 if best else 400))
    fig.update_layout(
        height=height, hovermode="closest", margin=dict(l=170, r=220, t=40, b=10),
        xaxis=dict(range=[-0.05, 1.05], tickvals=[0, 1], ticktext=["<b>Quoted price</b>", "<b>Delivery-weighted</b>"],
                   side="top", linecolor="rgba(0,0,0,0)", ticks=""),
        yaxis=dict(autorange="reversed", showgrid=False, showticklabels=False, range=[n + 0.5, 0.5]),
    )
    return fig


def waterfall(parts: dict[str, float], local: str, height: int = 360) -> go.Figure:
    """Mid-market cost, then each cost layer stacked on top, then the all-in total."""
    labels = [k for k in parts if k != "All-in cost"]
    total = parts["All-in cost"]
    first = parts[labels[0]]
    lo = first * 0.985  # zoom in: the layers are small next to the base
    bases, heights, colors, text = [lo], [first - lo], [TEAL], [_short(first)]
    running = first
    for k in labels[1:]:
        bases.append(running)
        heights.append(parts[k])
        colors.append(EMBER)
        text.append(f"+{_short(parts[k])}")
        running += parts[k]
    bases.append(lo)
    heights.append(total - lo)
    colors.append(INK)
    text.append(_short(total))
    short = {"Mid-market cost": "Mid-market", "Dealer spread": "Dealer spread", "Execution slippage": "Slippage",
             "TradeAhead margin": "TradeAhead margin", "Expected delivery cost": "Delivery risk"}
    x = [short.get(k, k) for k in labels] + ["All-in cost"]
    real = [first] + [parts[k] for k in labels[1:]] + [total]
    fig = go.Figure(go.Bar(x=x, y=heights, base=bases, marker_color=colors, marker_line_width=0,
                           text=text, textposition="outside", textfont=dict(color=INK, size=12),
                           customdata=real, hovertemplate="%{x}: %{customdata:,.0f}<extra></extra>"))
    fig.update_layout(height=height, showlegend=False, hovermode="closest", yaxis_title=local, bargap=0.35,
                      yaxis_range=[lo, total + (total - lo) * 0.08])
    return fig


# ---------------------------------------------------------------- Payments

def held_vs_trigger(schedule: pd.DataFrame, balance: float, bl_date, due_date, ccy: str,
                    height: int = 320) -> go.Figure:
    """Dollars held at the partner over time, against the B/L trigger and the due date."""
    d = list(pd.to_datetime(schedule["date"]) + pd.offsets.BDay(1))  # funds land T+1
    y = list(schedule["cum_fx"])
    if y:  # funds stay held until the due date
        d.append(pd.Timestamp(due_date))
        y.append(y[-1])
    fig = go.Figure(go.Scatter(x=d, y=y, mode="lines", line=dict(color=EMBER, width=2.5, shape="hv"),
                               fill="tozeroy", fillcolor="rgba(184,80,30,.10)", name="Held at partner",
                               hovertemplate="%{y:,.0f}<extra>Held</extra>"))
    fig.add_hline(y=balance, line=dict(color=INK, width=1.5, dash="dash"),
                  annotation_text=f"Balance due {balance:,.0f}", annotation_position="top left",
                  annotation_font=dict(color=INK, size=11))
    for x, label, color, side in ((bl_date, "B/L copy ", TEAL, "right"), (due_date, " Due date", EMBER, "left")):
        x = pd.Timestamp(x)
        fig.add_vline(x=x, line=dict(color=color, width=1.5, dash="dot"))
        fig.add_annotation(x=x, y=1, yref="paper", text=label, showarrow=False, yanchor="bottom", xanchor=side,
                           font=dict(size=11, color=color))
    fig.update_layout(height=height, showlegend=False, yaxis_title=ccy, yaxis_rangemode="tozero")
    return fig


# ---------------------------------------------------------------- Savings

def cost_ranges(dists: dict[str, np.ndarray], local: str, height: int = 340) -> go.Figure:
    """Range of final cost per strategy: thin bar 5–95%, thick bar 25–75%, dot at the median."""
    names = list(dists)
    fig = go.Figure()
    for name in names:
        q5, q25, q50, q75, q95 = np.percentile(dists[name], [5, 25, 50, 75, 95])
        color = EMBER if name == "TradeAhead plan" else (INK if "bank" in name else MUTED)
        label = name.replace("Unprotected (", "Unprotected, ").replace(")", "")
        tip = (f"<b>{label}</b><br>Median {_short(q50)}<br>Middle half {_short(q25)}–{_short(q75)}"
               f"<br>5–95% {_short(q5)}–{_short(q95)}<extra></extra>")
        fig.add_trace(go.Scatter(x=[q5, q95], y=[label] * 2, mode="lines", line=dict(color=color, width=2),
                                 showlegend=False, hovertemplate=tip))
        fig.add_trace(go.Scatter(x=[q25, q75], y=[label] * 2, mode="lines", line=dict(color=color, width=10),
                                 opacity=0.35, showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=[q50], y=[label], mode="markers+text", text=[_short(q50)],
                                 textposition="top center", textfont=dict(size=11, color=INK),
                                 marker=dict(size=11, color=color, line=dict(color=PAPER, width=2)),
                                 showlegend=False, hovertemplate=tip))
    fig.update_layout(height=height, hovermode="closest", xaxis_title=f"Final cost ({local})",
                      xaxis=dict(showgrid=True, gridcolor=GRID),
                      yaxis=dict(autorange="reversed", showgrid=False, categoryorder="array",
                                 categoryarray=[n.replace("Unprotected (", "Unprotected, ").replace(")", "") for n in names]))
    return fig


def shock_bars(costs: dict[str, float], local: str, height: int = 300) -> go.Figure:
    names = [n.replace("Unprotected (", "Unprotected, ").replace(")", "") for n in costs]
    colors = [EMBER if n == "TradeAhead plan" else (INK if "bank" in n else MUTED) for n in names]
    fig = go.Figure(go.Bar(x=list(costs.values()), y=names, orientation="h", marker_color=colors,
                           marker_line_width=0, text=[_short(v) for v in costs.values()], textposition="outside",
                           textfont=dict(color=INK), cliponaxis=False,
                           hovertemplate="%{y}: %{x:,.0f}<extra></extra>"))
    fig.update_layout(height=height, showlegend=False, hovermode="closest", xaxis_title=local,
                      xaxis=dict(range=[0, max(costs.values()) * 1.15], showgrid=True, gridcolor=GRID),
                      yaxis=dict(autorange="reversed", showgrid=False), bargap=0.45)
    return fig


# ---------------------------------------------------------------- Product page

def eri_chart(eri: pd.DataFrame, names: dict[str, str], bucket_order: list[str], height: int = 360) -> go.Figure:
    """Quoted vs executed markup by ticket size, per dealer (small multiples would be overkill here)."""
    fig = go.Figure()
    e = eri.copy()
    e["dealer"] = e["dealer_id"].map(names)
    e["order"] = e["bucket"].map({b: i for i, b in enumerate(bucket_order)})
    e = e.sort_values(["dealer", "order"])
    palette = [EMBER, INK, TEAL, AMBER, MUTED]
    dashes = ["solid", "solid", "dash", "dot", "dashdot"]
    for i, (dealer, g) in enumerate(e.groupby("dealer", sort=False)):
        fig.add_trace(go.Scatter(
            x=g["bucket"], y=g["exec_bps"], mode="lines+markers", name=dealer,
            line=dict(color=palette[i % 5], width=2, dash=dashes[i % 5]), marker=dict(size=8),
            customdata=np.c_[g["quoted_bps"], g["on_time_p"] * 100, g["n_fills"]],
            hovertemplate=(dealer + "<br>Executed %{y:.0f} bps (quoted %{customdata[0]:.0f})"
                           "<br>On time %{customdata[1]:.0f}% · %{customdata[2]} fills<extra></extra>")))
    fig.update_layout(height=height, hovermode="closest", yaxis_title="Cost over mid-market (bps)",
                      xaxis=dict(categoryorder="array", categoryarray=bucket_order, title="Ticket size (USD)"))
    return fig
