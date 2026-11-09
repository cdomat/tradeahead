"""Small HTML building blocks styled by assets/styles.css."""

from __future__ import annotations

from html import escape
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]

FX_SYMBOLS = {"USD": "$", "CNY": "CN¥", "EUR": "€", "GBP": "£", "CAD": "C$", "NGN": "₦"}


# ---------------------------------------------------------------- setup

def inject_css() -> None:
    css = (ROOT / "assets" / "styles.css").read_text()
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def html(markup: str) -> None:
    """Render HTML. Lines are stripped so Markdown never turns indentation into code blocks."""
    st.markdown(" ".join(line.strip() for line in markup.splitlines() if line.strip()), unsafe_allow_html=True)


# ---------------------------------------------------------------- formatting

def sym(ccy: str) -> str:
    return FX_SYMBOLS.get(ccy, ccy + " ")


def money(v: float, ccy: str, short: bool = False, decimals: int = 0) -> str:
    """₦47.9m in short form, $34,906 in long form."""
    if v is None:
        return "—"
    s, sign, a = sym(ccy), "-" if v < 0 else "", abs(v)
    if short:
        for div, unit in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
            if a >= div:
                return f"{sign}{s}{a / div:,.1f}{unit}"
    return f"{sign}{s}{a:,.{decimals}f}"


def pct(v: float, signed: bool = False, decimals: int = 1) -> str:
    return f"{v:+.{decimals}%}" if signed else f"{v:.{decimals}%}"


def rate(v: float) -> str:
    return f"{v:,.2f}" if v >= 10 else f"{v:,.4f}"


# ---------------------------------------------------------------- blocks

def pill(text: str, tone: str = "neutral") -> str:
    return f'<span class="ta-pill ta-pill--{tone}">{escape(text)}</span>'


STATUS_TONES = {
    "Fully protected": "ok", "Partly protected": "info", "Coverage gap": "warn", "Not protected": "risk",
    "Convert": "brand", "Hold": "neutral", "Settlement": "info", "Scheduled": "neutral",
    "Held at partner": "ok", "Paid": "ok", "Awaiting B/L": "warn",
}


def status_pill(text: str) -> str:
    return pill(text, STATUS_TONES.get(text, "neutral"))


def header(title: str, subtitle: str = "", right: str = "") -> None:
    html(f"""
    <div class="ta-header"><div>
      <h1>{escape(title)}</h1>
      {f'<p>{escape(subtitle)}</p>' if subtitle else ''}
    </div><div>{right}</div></div>""")


def section(title: str, body: str = "") -> None:
    html(f"""<div class="ta-section"><h2>{escape(title)}</h2>{f'<p>{body}</p>' if body else ''}</div>""")


def subhead(title: str, note: str = "") -> None:
    """A small heading inside a page or tab, with breathing room above it."""
    html(f'<div class="ta-subhead">{escape(title)}{f" <span>· {escape(note)}</span>" if note else ""}</div>')


def ticker(pair: str, value: float, change: float, source: str, as_of) -> str:
    cls = "up" if change > 0 else "down"
    arrow = "▲" if change > 0 else "▼"
    return (f'<span class="ta-ticker"><span>{escape(pair)}</span><b>{rate(value)}</b>'
            f'<span class="{cls}">{arrow} {abs(change):.2%}</span>'
            f'<span class="ta-muted">· {escape(source)}, {as_of:%d %b}</span></span>')


def kpis(items: list[dict]) -> None:
    """items: {label, value, sub?, tone? ('pos'/'neg'), highlight? (bool, the headline figure)}"""
    cells = []
    for k in items:
        sub = k.get("sub", "")
        tone = k.get("tone", "")
        cells.append(f'<div class="ta-kpi{" ta-kpi--highlight" if k.get("highlight") else ""}">'
                     f'<div class="ta-kpi__label">{escape(k["label"])}</div>'
                     f'<div class="ta-kpi__value">{k["value"]}</div>'
                     f'<div class="ta-kpi__sub {tone}">{sub}</div></div>')
    html(f'<div class="ta-kpis">{"".join(cells)}</div>')


def table(headers: list[tuple[str, str]], rows: list[list[str]], highlight: set[int] | None = None) -> None:
    """headers: [(label, align)] with align '', 'r' or 'c'. Cells are trusted HTML."""
    hl = highlight or set()
    th = "".join(f'<th class="{a}">{escape(h)}</th>' for h, a in headers)
    body = "".join(
        f'<tr class="{"hl" if i in hl else ""}">' +
        "".join(f'<td class="{headers[j][1]}">{c}</td>' for j, c in enumerate(r)) + "</tr>"
        for i, r in enumerate(rows))
    html(f'<div class="ta-table-wrap"><table class="ta-table"><thead><tr>{th}</tr></thead>'
         f'<tbody>{body}</tbody></table></div>')


def bar(share: float) -> str:
    return f'<span class="ta-bar"><span style="width:{max(min(share, 1), 0) * 100:.0f}%"></span></span>'


def steps(items: list[tuple[str, str, str]]) -> None:
    """items: (title, subtitle, state) with state 'done', 'now' or ''."""
    cells = "".join(f'<div class="ta-step ta-step--{s}"><b>{escape(t)}</b>{escape(sub)}</div>'
                    for t, sub, s in items)
    html(f'<div class="ta-steps">{cells}</div>')


def empty(title: str, body: str) -> None:
    html(f'<div class="ta-empty"><h3>{escape(title)}</h3><p>{body}</p></div>')


def alert(body: str, tone: str = "info") -> None:
    html(f'<div class="ta-alert ta-alert--{tone}">{body}</div>')


def card(title: str, body: str, extra: str = "") -> str:
    return f'<div class="ta-card"><h3>{escape(title)}</h3><p>{body}</p>{extra}</div>'


def cols(items: list[tuple[str, str, str]]) -> None:
    """Plain text columns under an orange rule. items: (kicker, title, body); kicker may be ''."""
    cells = "".join(f'<div class="ta-col">{f"<div class=ta-kicker>{escape(k)}</div>" if k else ""}'
                    f'<h3>{escape(t)}</h3><p>{body}</p></div>' for k, t, body in items)
    html(f'<div class="ta-cols">{cells}</div>')


def grid(cards: list[str], min_width: int = 240) -> None:
    html(f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax({min_width}px,1fr));gap:.9rem">'
         f'{"".join(cards)}</div>')


def footer() -> None:
    html("""<div class="ta-footer"><span><b>TradeAhead</b> · Keeping importers ahead of their supplier bills</span>
    <span>Funds are held and moved by licensed partners. TradeAhead plans and routes.
    Dealer data is simulated; FX rates from Yahoo Finance.</span></div>""")
