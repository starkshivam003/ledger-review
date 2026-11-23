# Ledger Review · PS-1 invoice processing

Takes a vendor invoice PDF, matches it to a purchase order, checks prior approvals and returns
**Approved**, **Hold** or **Duplicate blocked**, with the reason and the figures behind it.
Design details are in `ARCHITECTURE.md`.

## Run it

```
pip install pypdf
python app.py
```
Open http://localhost:8000. Use a private browser window for a fresh ledger, or click **Reset demo ledger**.

Tests: `python -m unittest -v` (8 tests, including two concurrency tests).

To regenerate the sample invoices: `pip install reportlab pillow` then `python make_samples.py`.

## Demo order (PO-410 starts with ₹30,000 available)

| Step | Example | Decision | PO-410 after |
|---|---|---|---:|
| 1 | Exceeds remaining balance · ₹35,000 | Hold, ₹5,000 excess | ₹30,000 |
| 2 | Clean partial invoice · ₹25,000 | Approved | ₹5,000 |
| 3 | Renamed copy of clean invoice | Duplicate blocked | ₹5,000 |
| 4 | Alternate layout · ₹4,500 | Approved | ₹500 |
| 5 | No PO reference (Aster Supplies) | Hold, two candidate POs | — |
| 6 | Scanned image | Hold, needs a text-based copy | — |
| 7 | Conflicting totals | Hold, totals disagree | — |

## Files

| File | Role |
|---|---|
| `app.py` | HTTP server, workspace cookie, transaction, streaming run events, history |
| `documents.py` | PDF text extraction and label matching |
| `workflow.py` | Field validation and the four decision rules |
| `web/index.html`, `web/ui.js` | Input panel, live run view, decision detail, dashboard |
| `samples/` | Seed POs, example metadata and demo PDFs |
| `test_workflow.py` | Unit and concurrency tests |

## Notes

- The live view streams extraction as it happens. The decision checks run inside one short
  transaction and are then streamed with a small pause (`STAGE_PAUSE`) so each stage is readable.
- No API keys or model calls. Zero overage tolerance (`TOLERANCE_PAISE` in `workflow.py`).
- To host it (for the "live link"), any Python host works, e.g. Render or Railway:
  start command `python app.py`, and the app reads the `PORT` environment variable.
