"""Read the text layer of a PDF and find values under known field labels."""
import io
import re

from pypdf import PdfReader

MAX_PDF_BYTES = 5 * 1024 * 1024  # 5 MB

# Each field lists the labels vendors actually use. Longer labels first.
LABELS = {
    "vendor": ["Vendor", "Supplier", "Billed by", "From"],
    "invoice_number": ["Invoice Number", "Invoice No", "Invoice #", "Bill No", "Bill Number"],
    "invoice_date": ["Invoice Date", "Bill Date", "Date"],
    "po_reference": ["PO Number", "PO Reference", "Purchase Order", "Order Ref", "PO No", "PO"],
    "total": ["Grand Total", "Total Amount Due", "Amount Due", "Invoice Total"],
}


class DocumentError(Exception):
    """The file cannot be used as an invoice (not a PDF, too big, encrypted)."""


def read_document(data: bytes) -> str:
    if len(data) > MAX_PDF_BYTES:
        raise DocumentError(f"File is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB.")
    if not data.startswith(b"%PDF"):
        raise DocumentError("File is not a PDF.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("PDF is password-protected.")
        pages = [page.extract_text() or "" for page in reader.pages[:10]]
    except DocumentError:
        raise
    except Exception as exc:  # corrupt or unsupported PDF
        raise DocumentError(f"PDF could not be opened ({exc.__class__.__name__}).")
    return "\n".join(pages)


def field(text: str, name: str) -> list[str]:
    """All values found under any label of this field, in document order.

    A label only counts at the start of a line and must be followed by ':' or '-',
    so 'Subtotal: 30,000' is never read as 'Total' and 'PO Number:' is not 'PO:'.
    """
    values = []
    labels = sorted(LABELS[name], key=len, reverse=True)
    pattern = re.compile(
        r"^\s*(?:" + "|".join(re.escape(l) for l in labels) + r")\s*[:\-]\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    for match in pattern.finditer(text):
        values.append(match.group(1))
    return values
