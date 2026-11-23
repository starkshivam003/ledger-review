"""Run: python -m unittest -v"""
import tempfile
import threading
import unittest
from pathlib import Path

import app
from workflow import APPROVED, DUPLICATE, HOLD, prepare

SAMPLES = Path(__file__).parent / "samples"
app.STAGE_PAUSE = 0


def pdf(name):
    return (SAMPLES / name).read_bytes()


def run_once(db, name):
    conn = app.init_db(db)
    try:
        events = list(app.run(conn, pdf(name), name))
    finally:
        conn.close()
    return events[-1]


def balance(db, po):
    conn = app.init_db(db)
    s = app.state(conn)
    conn.close()
    return next(o["available"] for o in s["orders"] if o["po"] == po)


class LedgerReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "ws.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_demo_sequence(self):
        self.assertEqual(balance(self.db, "PO-410"), "₹30,000")
        r = run_once(self.db, "01_exceeds_balance.pdf")
        self.assertEqual(r["status"], HOLD)
        self.assertEqual(r["evidence"]["excess"], "₹5,000")
        self.assertEqual(run_once(self.db, "02_clean_partial.pdf")["status"], APPROVED)
        self.assertEqual(balance(self.db, "PO-410"), "₹5,000")
        dup = run_once(self.db, "03_resent_copy_FINAL_v2.pdf")
        self.assertEqual(dup["status"], DUPLICATE)
        self.assertTrue(dup["evidence"]["earlier_reference"].startswith("APR-"))
        self.assertEqual(run_once(self.db, "04_alternate_layout.pdf")["status"], APPROVED)
        self.assertEqual(balance(self.db, "PO-410"), "₹500")

    def test_ambiguous_po(self):
        r = run_once(self.db, "05_no_po_reference.pdf")
        self.assertEqual(r["status"], HOLD)
        self.assertEqual(r["evidence"]["candidate_pos"], ["PO-520", "PO-521"])

    def test_scanned_and_conflicting(self):
        self.assertEqual(run_once(self.db, "06_scanned_image.pdf")["status"], HOLD)
        r = run_once(self.db, "07_conflicting_totals.pdf")
        self.assertEqual(r["status"], HOLD)
        self.assertIn("Conflicting", r["reason"])

    def test_po_of_other_vendor(self):
        fields, problems = prepare("Vendor: Aster Supplies\nInvoice No: AS-1\nInvoice Date: 01-11-2026\n"
                                   "PO Number: PO-410\nGrand Total: 100.00")
        conn = app.init_db(self.db)
        conn.execute("BEGIN IMMEDIATE")
        r = app.decide(conn, fields, problems, lambda *a: None)
        conn.execute("ROLLBACK")
        conn.close()
        self.assertEqual(r["status"], HOLD)
        self.assertIn("Northwind", r["reason"])

    def test_not_a_pdf(self):
        conn = app.init_db(self.db)
        r = list(app.run(conn, b"hello", "notes.txt"))[-1]
        conn.close()
        self.assertEqual(r["status"], HOLD)

    def test_exact_balance_is_approved(self):
        text = ("Vendor: Kestrel Logistics\nInvoice No: KL-1\nInvoice Date: 01-11-2026\n"
                "PO Number: PO-630\nGrand Total: 25,000.00")
        conn = app.init_db(self.db)
        conn.execute("BEGIN IMMEDIATE")
        r = app.decide(conn, *prepare(text), lambda *a: None)
        conn.execute("COMMIT")
        conn.close()
        self.assertEqual(r["status"], APPROVED)
        self.assertEqual(r["evidence"]["remaining_after"], "₹0")

    def _concurrent(self, texts):
        app.init_db(self.db).close()
        results, barrier = [], threading.Barrier(len(texts))

        def worker(text):
            conn = app.connect(self.db)
            barrier.wait()
            conn.execute("BEGIN IMMEDIATE")
            r = app.decide(conn, *prepare(text), lambda *a: None)
            conn.execute("COMMIT")
            conn.close()
            results.append(r["status"])

        threads = [threading.Thread(target=worker, args=(t,)) for t in texts]
        [t.start() for t in threads]
        [t.join() for t in threads]
        return sorted(results)

    def test_concurrent_same_invoice(self):
        t = ("Vendor: Northwind Traders\nInvoice No: NW-9\nInvoice Date: 01-11-2026\n"
             "PO Number: PO-410\nGrand Total: 1,000.00")
        self.assertEqual(self._concurrent([t, t]), sorted([APPROVED, DUPLICATE]))

    def test_concurrent_different_invoices_cannot_overcommit(self):
        mk = lambda n: ("Vendor: Northwind Traders\nInvoice No: " + n + "\nInvoice Date: 01-11-2026\n"
                        "PO Number: PO-410\nGrand Total: 20,000.00")
        self.assertEqual(self._concurrent([mk("NW-A"), mk("NW-B")]), sorted([APPROVED, HOLD]))
        self.assertEqual(balance(self.db, "PO-410"), "₹10,000")


if __name__ == "__main__":
    unittest.main()
