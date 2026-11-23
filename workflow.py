"""Business rules: turn extracted text into invoice fields, then decide.

Decision order (each check can stop the run):
  1. fields usable          -> HOLD
  2. already approved       -> DUPLICATE_BLOCKED
  3. exactly one vendor PO  -> HOLD
  4. fits remaining balance -> HOLD
  otherwise                 -> APPROVED (and the balance is consumed)
"""
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from documents import field

APPROVED, HOLD, DUPLICATE = "Approved", "Hold", "Duplicate blocked"
TOLERANCE_PAISE = 0  # zero overage tolerance (deliberate, see README)
DATE_FORMATS = ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"]


# ---------- normalisation ----------

def vendor_key(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().upper()


def invoice_key(number: str) -> str:
    return number.strip().upper()


def po_key(ref: str) -> str:
    return re.sub(r"\s+", "", ref).upper()


def rupees(paise: int) -> str:
    """Indian grouping: 100000.00 -> ₹1,00,000"""
    r, p = divmod(paise, 100)
    s = str(r)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        s = head + "," + tail
    return "₹" + s + (f".{p:02d}" if p else "")


def parse_amount(raw: str):
    cleaned = re.sub(r"(?i)(inr|rs\.?|₹|/-)", "", raw).replace(",", "").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if value <= 0 or value != value.quantize(Decimal("0.01")):
        return None
    return int(value * 100)


def parse_date(raw: str):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


# ---------- step 1: extract and validate ----------

def prepare(text: str):
    """Return (fields, problems). fields holds clean values; problems explain a Hold."""
    fields, problems = {}, []

    if not text.strip():
        return fields, ["The PDF has no text layer (likely a scan). Please send a text-based copy."]

    raw = {name: field(text, name) for name in
           ["vendor", "invoice_number", "invoice_date", "po_reference", "total"]}

    conflicted = set()

    def single(name, key=lambda v: v.strip().upper()):
        values = raw[name]
        if len({key(v) for v in values}) > 1:
            conflicted.add(name)
            problems.append(f"Conflicting values for {name.replace('_', ' ')}: {' vs '.join(values)}.")
            return None
        return values[0].strip() if values else None

    vendor = single("vendor", vendor_key)
    number = single("invoice_number", invoice_key)
    date_raw = single("invoice_date")
    po_raw = single("po_reference", po_key)
    total_raw = single("total", parse_amount)

    for name, value in [("vendor", vendor), ("invoice_number", number),
                        ("invoice_date", date_raw), ("total", total_raw)]:
        if value is None and name not in conflicted:
            problems.append(f"Missing {name.replace('_', ' ')}.")

    if vendor:
        fields["vendor"] = vendor
    if number:
        fields["invoice_number"] = number
    if po_raw:
        fields["po_reference"] = po_key(po_raw)
    if date_raw:
        d = parse_date(date_raw)
        if d is None:
            problems.append(f"Invoice date '{date_raw}' is not a valid date.")
        elif d > date.today():
            problems.append(f"Invoice date {d.isoformat()} is in the future.")
        else:
            fields["invoice_date"] = d.isoformat()
    if total_raw:
        paise = parse_amount(total_raw)
        if paise is None:
            problems.append(f"Total '{total_raw}' is not a valid amount.")
        else:
            fields["total_paise"] = paise
            fields["total"] = rupees(paise)
    return fields, problems


# ---------- steps 2-4: decide against the ledger ----------

def decide(conn, fields, problems, trace):
    """Run inside an open write transaction. Returns the decision dict.
    trace(stage, status, detail) records each check for the live view."""
    if problems:
        trace("validate", "fail", " ".join(problems))
        return {"status": HOLD, "reason": problems[0], "evidence": {"problems": problems}}
    trace("validate", "pass", f"{fields['vendor']} · {fields['invoice_number']} · {fields['total']}")

    vkey, ikey = vendor_key(fields["vendor"]), invoice_key(fields["invoice_number"])

    # 2. duplicate
    prior = conn.execute(
        "SELECT po_id, amount_paise, reference, approved_at FROM approvals "
        "WHERE vendor_key=? AND invoice_key=?", (vkey, ikey)).fetchone()
    if prior:
        po_id, amt, ref, at = prior
        trace("duplicate", "fail", f"Already approved as {ref}")
        return {"status": DUPLICATE,
                "reason": f"Invoice {fields['invoice_number']} from {fields['vendor']} was already approved ({ref}).",
                "evidence": {"earlier_reference": ref, "earlier_amount": rupees(amt),
                             "this_amount": fields["total"], "po": po_id, "approved_at": at}}
    trace("duplicate", "pass", "No earlier approval for this vendor and invoice number")

    # 3. match exactly one PO of this vendor
    orders = conn.execute("SELECT po_id, ceiling_paise FROM orders WHERE vendor_key=?", (vkey,)).fetchall()
    if not orders:
        trace("match_po", "fail", "Vendor not in the procurement register")
        return {"status": HOLD, "reason": f"{fields['vendor']} is not an approved vendor with an open PO.",
                "evidence": {"vendor": fields["vendor"]}}
    ref = fields.get("po_reference")
    if ref:
        match = [o for o in orders if o[0] == ref]
        if not match:
            owner = conn.execute("SELECT vendor FROM orders WHERE po_id=?", (ref,)).fetchone()
            why = f"belongs to {owner[0]}" if owner else "does not exist"
            trace("match_po", "fail", f"{ref} {why}")
            return {"status": HOLD, "reason": f"PO {ref} {why}, not {fields['vendor']}.",
                    "evidence": {"po_reference": ref, "vendor_pos": [o[0] for o in orders]}}
        po_id, ceiling = match[0]
    elif len(orders) == 1:
        po_id, ceiling = orders[0]
    else:
        ids = [o[0] for o in orders]
        trace("match_po", "fail", f"No PO reference; candidates {', '.join(ids)}")
        return {"status": HOLD, "reason": "No PO reference and the vendor has several open POs. Procurement must confirm which one.",
                "evidence": {"candidate_pos": ids}}
    trace("match_po", "pass", f"Matched {po_id}" + ("" if ref else " (vendor's only PO)"))

    # 4. balance
    used = conn.execute("SELECT COALESCE(SUM(amount_paise),0) FROM approvals WHERE po_id=?",
                        (po_id,)).fetchone()[0]
    available = ceiling - used
    calc = {"po": po_id, "ceiling": rupees(ceiling), "already_approved": rupees(used),
            "available": rupees(available), "invoice": fields["total"]}
    amount = fields["total_paise"]
    if amount > available + TOLERANCE_PAISE:
        calc["excess"] = rupees(amount - available)
        trace("balance", "fail", f"{fields['total']} > {rupees(available)} available")
        return {"status": HOLD,
                "reason": f"Invoice exceeds the remaining balance of {po_id} by {calc['excess']}.",
                "evidence": calc}
    trace("balance", "pass", f"{fields['total']} ≤ {rupees(available)} available")

    reference = "APR-" + uuid.uuid4().hex[:8].upper()
    conn.execute(
        "INSERT INTO approvals(vendor_key, invoice_key, vendor, invoice_number, po_id, amount_paise, reference, approved_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (vkey, ikey, fields["vendor"], fields["invoice_number"], po_id, amount, reference,
         datetime.utcnow().isoformat(timespec="seconds") + "Z"))
    calc["remaining_after"] = rupees(available - amount)
    calc["reference"] = reference
    return {"status": APPROVED,
            "reason": f"Approved against {po_id}. {calc['remaining_after']} remains.",
            "evidence": calc}
