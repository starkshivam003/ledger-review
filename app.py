"""Ledger Review: invoice PDF -> reasoned AP decision.

Run:  pip install pypdf  &&  python app.py   then open http://localhost:8000
"""
import base64
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from documents import DocumentError, read_document
from workflow import decide, prepare, rupees, vendor_key

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SAMPLES = ROOT / "samples"
STAGE_PAUSE = 0.35  # seconds between streamed stages, so the live view is readable

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(
  po_id TEXT PRIMARY KEY, vendor TEXT NOT NULL, vendor_key TEXT NOT NULL, ceiling_paise INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(
  vendor_key TEXT NOT NULL, invoice_key TEXT NOT NULL, vendor TEXT, invoice_number TEXT,
  po_id TEXT NOT NULL REFERENCES orders(po_id), amount_paise INTEGER NOT NULL,
  reference TEXT NOT NULL, approved_at TEXT NOT NULL,
  PRIMARY KEY(vendor_key, invoice_key));
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, at TEXT NOT NULL, record TEXT NOT NULL);
"""


def connect(path):
    conn = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(path):
    fresh = not Path(path).exists()
    conn = connect(path)
    conn.executescript(SCHEMA)
    if fresh:
        seed = json.loads((SAMPLES / "procurement.json").read_text())
        conn.execute("BEGIN")
        for o in seed["orders"]:
            conn.execute("INSERT INTO orders VALUES (?,?,?,?)",
                         (o["po_id"], o["vendor"], vendor_key(o["vendor"]), o["ceiling_paise"]))
        for a in seed["approvals"]:
            conn.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?)",
                         (vendor_key(a["vendor"]), a["invoice_number"].upper(), a["vendor"],
                          a["invoice_number"], a["po_id"], a["amount_paise"], a["reference"], a["approved_at"]))
        conn.execute("COMMIT")
    return conn


def run(conn, pdf: bytes, filename: str):
    """Generator of stage events; the last event is the saved decision record."""
    run_id = "RUN-" + uuid.uuid4().hex[:6].upper()
    started = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    stages = []

    def event(stage, status, detail):
        e = {"type": "stage", "stage": stage, "status": status, "detail": detail}
        stages.append(e)
        return e

    yield event("receive", "pass", f"{filename} · {len(pdf) // 1024 or 1} KB")

    # Extraction happens outside the write lock.
    try:
        text = read_document(pdf)
        yield event("extract", "pass" if text.strip() else "fail",
                    f"{len(text.split())} words read" if text.strip() else "No text layer found")
        fields, problems = prepare(text)
    except DocumentError as exc:
        yield event("extract", "fail", str(exc))
        fields, problems = {}, [str(exc)]

    # Decide and record in one transaction.
    pending = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        decision = decide(conn, fields, problems, lambda s, st, d: pending.append(event(s, st, d)))
        record = {"id": run_id, "at": started, "file": filename, "fields": fields,
                  **decision, "stages": stages}
        conn.execute("INSERT INTO runs VALUES (?,?,?)", (run_id, started, json.dumps(record)))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    for e in pending:
        time.sleep(STAGE_PAUSE)
        yield e
    time.sleep(STAGE_PAUSE)
    yield {"type": "result", **record}


def state(conn):
    orders = conn.execute(
        "SELECT o.po_id, o.vendor, o.ceiling_paise, COALESCE(SUM(a.amount_paise),0) "
        "FROM orders o LEFT JOIN approvals a ON a.po_id=o.po_id GROUP BY o.po_id ORDER BY o.po_id").fetchall()
    runs = [json.loads(r[0]) for r in conn.execute("SELECT record FROM runs ORDER BY at DESC, rowid DESC LIMIT 100")]
    return {"orders": [{"po": p, "vendor": v, "ceiling": rupees(c), "approved": rupees(u),
                        "available": rupees(c - u), "used_pct": round(100 * u / c)} for p, v, c, u in orders],
            "runs": runs}


def examples():
    meta = json.loads((SAMPLES / "examples.json").read_text())
    return [e for e in meta if (SAMPLES / e["file"]).exists()]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def workspace(self):
        m = re.search(r"ws=([0-9a-f]{16})", self.headers.get("Cookie", ""))
        ws = m.group(1) if m else secrets.token_hex(8)
        self._new_ws = None if m else ws
        DATA.mkdir(exist_ok=True)
        return init_db(DATA / f"ws_{ws}.db")

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.cookie()
        self.end_headers()
        self.wfile.write(data)

    def cookie(self):
        if getattr(self, "_new_ws", None):
            self.send_header("Set-Cookie", f"ws={self._new_ws}; Path=/; HttpOnly; SameSite=Lax")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self.send(200, (ROOT / "web/index.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/ui.js":
            return self.send(200, (ROOT / "web/ui.js").read_bytes(), "text/javascript; charset=utf-8")
        if self.path == "/examples":
            return self.send(200, examples())
        if self.path in ("/history", "/state"):
            conn = self.workspace()
            try:
                return self.send(200, state(conn))
            finally:
                conn.close()
        self.send(404, {"error": "Not found"})

    def do_POST(self):
        if self.path == "/reset":
            conn = self.workspace(); conn.close()
            m = re.search(r"ws=([0-9a-f]{16})", self.headers.get("Cookie", ""))
            if m:
                for suffix in ("", "-wal", "-shm"):
                    p = DATA / f"ws_{m.group(1)}.db{suffix}"
                    if p.exists():
                        p.unlink()
            return self.send(200, {"ok": True})
        if self.path != "/run":
            return self.send(404, {"error": "Not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 8 * 1024 * 1024:
                return self.send(413, {"error": "Request too large. Upload a PDF under 5 MB."})
            body = json.loads(self.rfile.read(length) or b"{}")
            if body.get("file_b64"):
                pdf, name = base64.b64decode(body["file_b64"], validate=True), body.get("filename", "upload.pdf")
            elif body.get("example"):
                match = [e for e in examples() if e["id"] == body["example"]]
                if not match:
                    return self.send(400, {"error": "Unknown example."})
                pdf, name = (SAMPLES / match[0]["file"]).read_bytes(), match[0]["file"]
            else:
                return self.send(400, {"error": "Choose an example or upload a PDF."})
        except (ValueError, json.JSONDecodeError):
            return self.send(400, {"error": "The request could not be read. Try uploading the file again."})

        conn = self.workspace()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.cookie()
        self.end_headers()
        try:
            for e in run(conn, pdf, name):
                self.wfile.write((json.dumps(e) + "\n").encode())
                self.wfile.flush()
        except BrokenPipeError:
            pass
        finally:
            conn.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Ledger Review running on http://localhost:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
