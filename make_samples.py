"""Generate the demo invoice PDFs in samples/. Run once: python make_samples.py"""
import json
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent / "samples"
W, H = A4


def invoice(path, title, header, lines, totals, note=None):
    c = canvas.Canvas(str(OUT / path), pagesize=A4)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, H - 60, title)
    c.setFont("Helvetica", 10)
    y = H - 100
    for row in header:
        c.drawString(50, y, row)
        y -= 16
    y -= 14
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Description")
    c.drawRightString(W - 50, y, "Amount (INR)")
    c.setFont("Helvetica", 10)
    y -= 18
    for desc, amt in lines:
        c.drawString(50, y, desc)
        c.drawRightString(W - 50, y, amt)
        y -= 16
    y -= 10
    for row in totals:
        c.drawString(W - 250, y, row)
        y -= 16
    if note:
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(50, 60, note)
    c.save()


def scanned(path):
    """A page that is only a picture of an invoice: no text layer."""
    img = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(img)
    d.text((80, 80), "TAX INVOICE  -  Kestrel Logistics", fill="black")
    d.text((80, 140), "Invoice No: KL-7781     Date: 12-11-2026", fill="black")
    d.text((80, 180), "PO Number: PO-630", fill="black")
    d.text((80, 260), "Freight charges ............ 18,000.00", fill="black")
    d.text((80, 320), "Grand Total: 18,000.00", fill="black")
    c = canvas.Canvas(str(OUT / path), pagesize=A4)
    c.drawImage(ImageReader(img), 0, 0, W, H)
    c.save()


def main():
    OUT.mkdir(exist_ok=True)
    nw = ["Vendor: Northwind Traders", "Bill To: Acme Manufacturing Pvt Ltd"]

    invoice("01_exceeds_balance.pdf", "TAX INVOICE",
            nw + ["Invoice No: NW-2026-118", "Invoice Date: 14-11-2026", "PO Number: PO-410"],
            [("Industrial fasteners, lot 7", "29,661.02")],
            ["Subtotal: 29,661.02", "GST 18%: 5,338.98", "Grand Total: 35,000.00"])

    clean = (["Invoice No: NW-2026-121", "Invoice Date: 17-11-2026", "PO Number: PO-410"],
             [("Industrial fasteners, lot 8", "21,186.44")],
             ["Subtotal: 21,186.44", "GST 18%: 3,813.56", "Grand Total: 25,000.00"])
    invoice("02_clean_partial.pdf", "TAX INVOICE", nw + clean[0], clean[1], clean[2])
    invoice("03_resent_copy_FINAL_v2.pdf", "TAX INVOICE", nw + clean[0], clean[1], clean[2],
            note="Resent copy - please process")

    invoice("04_alternate_layout.pdf", "BILL OF SUPPLY",
            ["Supplier: Northwind Traders", "Bill No: NW-2026-130", "Bill Date: 2026-11-19",
             "Order Ref: PO-410"],
            [("Packing and handling", "4,500.00")],
            ["Amount Due: Rs. 4,500.00"])

    invoice("05_no_po_reference.pdf", "TAX INVOICE",
            ["Vendor: Aster Supplies", "Invoice No: AS-5502", "Invoice Date: 15-11-2026"],
            [("Lab consumables", "10,169.49")],
            ["Subtotal: 10,169.49", "GST 18%: 1,830.51", "Grand Total: 12,000.00"])

    scanned("06_scanned_image.pdf")

    invoice("07_conflicting_totals.pdf", "TAX INVOICE",
            ["Vendor: Kestrel Logistics", "Invoice No: KL-7790", "Invoice Date: 16-11-2026",
             "PO Number: PO-630"],
            [("Freight, Kanpur to Pune", "9,000.00")],
            ["Grand Total: 9,000.00", "Amount Due: 19,000.00"])

    examples = [
        {"id": "exceeds", "file": "01_exceeds_balance.pdf", "title": "Exceeds remaining balance",
         "expect": "Hold", "note": "₹35,000 against ₹30,000 left on PO-410"},
        {"id": "clean", "file": "02_clean_partial.pdf", "title": "Clean partial invoice",
         "expect": "Approved", "note": "₹25,000 instalment on PO-410"},
        {"id": "resent", "file": "03_resent_copy_FINAL_v2.pdf", "title": "Renamed copy of clean invoice",
         "expect": "Duplicate blocked", "note": "Same invoice number, new file name"},
        {"id": "alternate", "file": "04_alternate_layout.pdf", "title": "Alternate layout",
         "expect": "Approved", "note": "Supplier / Bill No / Amount Due labels, ₹4,500"},
        {"id": "nopo", "file": "05_no_po_reference.pdf", "title": "No PO reference",
         "expect": "Hold", "note": "Vendor has two open POs"},
        {"id": "scan", "file": "06_scanned_image.pdf", "title": "Scanned image",
         "expect": "Hold", "note": "No text layer"},
        {"id": "conflict", "file": "07_conflicting_totals.pdf", "title": "Conflicting totals",
         "expect": "Hold", "note": "Grand Total and Amount Due disagree"},
    ]
    (OUT / "examples.json").write_text(json.dumps(examples, indent=2, ensure_ascii=False))
    print(f"Wrote {len(examples)} sample invoices to {OUT}")


if __name__ == "__main__":
    main()
