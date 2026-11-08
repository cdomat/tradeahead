"""Sample proforma invoice (PDF and CSV) for demos, plus the parser the Dashboard uses.

The supplier and buyer are fictional. Dates are set relative to today, so the
sample always has a shipment and a due date in the future.

Run directly to write the files to assets/samples/:
    python -m services.invoice
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "assets" / "samples"


@dataclass
class LineItem:
    description: str
    hs_code: str
    qty: int
    unit_price: float

    @property
    def amount(self) -> float:
        return round(self.qty * self.unit_price, 2)


@dataclass
class Invoice:
    invoice_no: str
    issue_date: date
    supplier: str
    supplier_address: str
    buyer: str
    buyer_address: str
    currency: str
    items: list[LineItem]
    freight: float
    insurance: float
    incoterm: str
    port_of_loading: str
    port_of_discharge: str
    deposit_pct: float
    deposit_paid: bool
    etd: date            # vessel departs
    bl_date: date        # bill of lading issued
    eta: date            # vessel arrives
    balance_due: date    # balance payable (here: 5 days after the B/L copy)
    payment_terms: str = field(default="")

    @property
    def subtotal(self) -> float:
        return round(sum(i.amount for i in self.items), 2)

    @property
    def total(self) -> float:
        return round(self.subtotal + self.freight + self.insurance, 2)

    @property
    def deposit(self) -> float:
        return round(self.total * self.deposit_pct, 2)

    @property
    def balance(self) -> float:
        return round(self.total - self.deposit, 2)

    def summary(self) -> dict:
        """The fields the Dashboard needs, in one flat dict."""
        return {
            "invoice_no": self.invoice_no, "issue_date": self.issue_date, "supplier": self.supplier,
            "buyer": self.buyer, "currency": self.currency, "total": self.total,
            "deposit_pct": self.deposit_pct, "deposit": self.deposit, "deposit_paid": self.deposit_paid,
            "balance": self.balance, "incoterm": self.incoterm, "port_of_loading": self.port_of_loading,
            "port_of_discharge": self.port_of_discharge, "etd": self.etd, "bl_date": self.bl_date,
            "eta": self.eta, "balance_due": self.balance_due, "payment_terms": self.payment_terms,
        }


def sample_invoice(today: date | None = None, currency: str = "USD") -> Invoice:
    today = today or date.today()
    fx = 1.0 if currency == "USD" else 7.10  # CNY version keeps the same order size
    items = [
        LineItem("Android smartphone 6.6in, 128GB, dual SIM", "8517.13", 300, 92.00 * fx),
        LineItem("Power bank 20,000mAh, PD 22.5W", "8507.60", 1200, 8.40 * fx),
        LineItem("USB-C fast charger 33W, UK plug", "8504.40", 1500, 3.10 * fx),
        LineItem("Bluetooth earbuds, ENC, charging case", "8518.30", 800, 6.25 * fx),
    ]
    etd = today + timedelta(days=18)
    bl = etd + timedelta(days=1)
    inv = Invoice(
        invoice_no=f"PI-SZBW-{today:%y%m}-0417",
        issue_date=today - timedelta(days=5),
        supplier="Shenzhen Brightway Electronics Co., Ltd.",
        supplier_address="Bldg 7, Huaqiang North Rd, Futian District, Shenzhen 518031, China",
        buyer="[Your company name] Ltd.",
        buyer_address="Computer Village, Ikeja, Lagos, Nigeria",
        currency=currency,
        items=items,
        freight=round(2350.00 * fx, 2),
        insurance=round(185.00 * fx, 2),
        incoterm="CIF",
        port_of_loading="Shekou, Shenzhen",
        port_of_discharge="Apapa, Lagos",
        deposit_pct=0.30,
        deposit_paid=True,
        etd=etd,
        bl_date=bl,
        eta=etd + timedelta(days=32),
        balance_due=bl + timedelta(days=5),
    )
    inv.payment_terms = (f"30% T/T deposit with order (paid). 70% balance T/T within 5 days of "
                         f"copy of Bill of Lading.")
    return inv


# ---------------------------------------------------------------- PDF

def build_pdf(inv: Invoice) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ink, line, muted = colors.HexColor("#1F2933"), colors.HexColor("#C9CED6"), colors.HexColor("#5B6573")
    base = ParagraphStyle("b", fontName="Helvetica", fontSize=8.5, leading=11.5, textColor=ink)
    small = ParagraphStyle("s", parent=base, fontSize=7.5, leading=10, textColor=muted)
    bold = ParagraphStyle("bd", parent=base, fontName="Helvetica-Bold")
    title = ParagraphStyle("t", parent=base, fontName="Helvetica-Bold", fontSize=15, leading=18)
    c = inv.currency
    money = lambda v: f"{v:,.2f}"  # noqa: E731
    d = lambda v: v.strftime("%d %b %Y")  # noqa: E731

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"Proforma Invoice {inv.invoice_no}", author=inv.supplier)
    W = A4[0] - 32 * mm
    story = []

    head = Table([[Paragraph(f"<b>{inv.supplier}</b><br/>{inv.supplier_address}<br/>"
                             "Tel +86 755 0000 0000 · sales@brightway-example.cn", base),
                   Paragraph("PROFORMA INVOICE", ParagraphStyle("r", parent=title, alignment=2))]],
                 colWidths=[W * 0.62, W * 0.38])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 1.2, ink),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story += [head, Spacer(1, 6)]

    meta = [
        [Paragraph("<b>Bill to</b>", small), Paragraph("<b>Invoice details</b>", small)],
        [Paragraph(f"{inv.buyer}<br/>{inv.buyer_address}", base),
         Paragraph(f"Invoice No: {inv.invoice_no}<br/>Invoice Date: {d(inv.issue_date)}<br/>"
                   f"Currency: {c}<br/>Incoterm: {inv.incoterm} {inv.port_of_discharge}", base)],
    ]
    t = Table(meta, colWidths=[W * 0.5, W * 0.5])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [t, Spacer(1, 8)]

    rows = [["#", "Description", "HS code", "Qty", f"Unit price ({c})", f"Amount ({c})"]]
    for n, it in enumerate(inv.items, 1):
        rows.append([str(n), Paragraph(it.description, base), it.hs_code, f"{it.qty:,}",
                     money(it.unit_price), money(it.amount)])
    rows += [["", "", "", "", "Subtotal", money(inv.subtotal)],
             ["", "", "", "", "Ocean freight", money(inv.freight)],
             ["", "", "", "", "Insurance", money(inv.insurance)],
             ["", "", "", "", f"Total {c}", money(inv.total)]]
    t = Table(rows, colWidths=[W * 0.05, W * 0.41, W * 0.11, W * 0.09, W * 0.17, W * 0.17], repeatRows=1)
    n_items = len(inv.items)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("TEXTCOLOR", (0, 0), (-1, -1), ink), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF1F4")),
        ("LINEBELOW", (0, 0), (-1, n_items), 0.4, line), ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("FONT", (4, -1), (-1, -1), "Helvetica-Bold", 9),
        ("LINEABOVE", (4, -1), (-1, -1), 1, ink), ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [t, Spacer(1, 10)]

    terms = [
        [Paragraph("<b>Payment terms</b>", small), Paragraph("<b>Shipment</b>", small)],
        [Paragraph(f"{inv.payment_terms}<br/>Deposit ({inv.deposit_pct:.0%}): {c} {money(inv.deposit)}"
                   f"{' (received)' if inv.deposit_paid else ''}<br/>"
                   f"Balance ({1 - inv.deposit_pct:.0%}): {c} {money(inv.balance)}<br/>"
                   f"Balance Due Date: {d(inv.balance_due)}", base),
         Paragraph(f"Port of Loading: {inv.port_of_loading}<br/>Port of Discharge: {inv.port_of_discharge}<br/>"
                   f"ETD: {d(inv.etd)}<br/>Bill of Lading (expected): {d(inv.bl_date)}<br/>"
                   f"ETA: {d(inv.eta)}", base)],
    ]
    t = Table(terms, colWidths=[W * 0.5, W * 0.5])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.4, line),
                           ("LINEAFTER", (0, 0), (0, -1), 0.4, line), ("TOPPADDING", (0, 0), (-1, -1), 5),
                           ("LEFTPADDING", (0, 0), (-1, -1), 7)]))
    story += [t, Spacer(1, 8)]

    story += [Paragraph("<b>Beneficiary bank</b>: Example Bank (sample only) · Account: 0000 0000 0000 · "
                        "SWIFT: XXXXXXXX. Please quote the invoice number on your transfer.", small),
              Spacer(1, 14),
              Paragraph("SAMPLE DOCUMENT FOR TESTING TRADEAHEAD. Fictional supplier, buyer and bank details.",
                        ParagraphStyle("f", parent=small, alignment=1))]
    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------- CSV

CSV_FIELDS = ["invoice_no", "issue_date", "supplier", "buyer", "currency", "total", "deposit_pct",
              "deposit_paid", "balance", "incoterm", "port_of_loading", "port_of_discharge",
              "etd", "bl_date", "eta", "balance_due", "payment_terms"]


def build_csv(inv: Invoice) -> bytes:
    """One header row plus one invoice row, the format the CSV upload expects."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
    w.writeheader()
    s = inv.summary()
    w.writerow({k: (v.isoformat() if isinstance(v, date) else v) for k, v in s.items() if k in CSV_FIELDS})
    return buf.getvalue().encode()


# ---------------------------------------------------------------- Parser (used by the Dashboard)

_AMT = r"([\d,]+\.\d{2})"
_DATE = r"(\d{1,2} \w{3} \d{4})"
_PDF_PATTERNS = {
    "invoice_no": r"Invoice No:\s*(\S+)",
    "issue_date": r"Invoice Date:\s*" + _DATE,
    "currency": r"Currency:\s*([A-Z]{3})",
    "incoterm": r"Incoterm:\s*([A-Z]{3})",
    "total": r"Total [A-Z]{3}\s*" + _AMT,
    "deposit_pct": r"Deposit \((\d+)%\)",
    "balance": r"Balance \(\d+%\):\s*[A-Z]{3}\s*" + _AMT,
    "balance_due": r"Balance Due Date:\s*" + _DATE,
    "port_of_loading": r"Port of Loading:\s*(.+)",
    "port_of_discharge": r"Port of Discharge:\s*(.+)",
    "etd": r"ETD:\s*" + _DATE,
    "bl_date": r"Bill of Lading \(expected\):\s*" + _DATE,
    "eta": r"ETA:\s*" + _DATE,
}


def _to_value(key: str, raw: str):
    from datetime import datetime
    raw = raw.strip()
    if key in {"issue_date", "balance_due", "etd", "bl_date", "eta"}:
        for fmt in ("%d %b %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                pass
        return None
    if key in {"total", "balance"}:
        return float(raw.replace(",", ""))
    if key == "deposit_pct":
        v = float(raw)
        return v / 100 if v > 1 else v
    if key == "deposit_paid":
        return raw.lower() in {"true", "yes", "1", "paid"}
    return raw


def parse_invoice(data: bytes, filename: str) -> dict:
    """Read the key fields from a PDF or CSV. Returns {field: (value, confidence)}.

    Confidence is 'high' when the field was found by an exact label, 'check'
    when it was derived, and 'missing' when the user must type it in.
    """
    out: dict[str, tuple] = {}
    if filename.lower().endswith(".csv"):
        row = next(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))), {})
        for k, v in row.items():
            if k in CSV_FIELDS and v not in (None, ""):
                out[k] = (_to_value(k, v), "high")
    else:
        from pypdf import PdfReader
        text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
        for k, pat in _PDF_PATTERNS.items():
            m = re.search(pat, text)
            if m:
                out[k] = (_to_value(k, m.group(1)), "high")
        first = text.strip().splitlines()[0] if text.strip() else ""
        if first and "PROFORMA" not in first.upper():
            out["supplier"] = (first.strip(), "check")
    # Fill in what can be derived.
    if "balance" not in out and "total" in out and "deposit_pct" in out:
        out["balance"] = (round(out["total"][0] * (1 - out["deposit_pct"][0]), 2), "check")
    if "balance_due" not in out and "bl_date" in out:
        out["balance_due"] = (out["bl_date"][0] + timedelta(days=5), "check")
    for k in ("currency", "balance", "balance_due"):
        out.setdefault(k, (None, "missing"))
    return out


if __name__ == "__main__":
    SAMPLES.mkdir(parents=True, exist_ok=True)
    inv = sample_invoice()
    (SAMPLES / "sample_proforma_invoice_USD.pdf").write_bytes(build_pdf(inv))
    (SAMPLES / "sample_proforma_invoice_USD.csv").write_bytes(build_csv(inv))
    print(f"Wrote samples to {SAMPLES}  ·  total {inv.currency} {inv.total:,.2f}, "
          f"balance {inv.balance:,.2f} due {inv.balance_due}")
    print({k: v for k, v in asdict(inv).items() if k != "items"})
