const STAGES = [
  ["receive", "Receive"], ["extract", "Extract text"], ["validate", "Validate fields"],
  ["duplicate", "Duplicate check"], ["match_po", "Match PO"], ["balance", "PO balance"],
];
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const cls = (status) => (status || "").split(" ")[0];
let busy = false;

function resetStages() {
  $("stages").innerHTML = STAGES.map(([k, label]) =>
    `<li id="st-${k}"><span class="dot"></span><span class="name">${label}</span><span class="detail">waiting</span></li>`).join("");
}

function showStage(e) {
  const li = $("st-" + e.stage);
  if (!li) return;
  li.className = e.status;
  li.querySelector(".detail").textContent = e.detail;
}

function showResult(r) {
  const ev = r.evidence || {};
  const label = {po: "PO", ceiling: "PO ceiling", already_approved: "Already approved", available: "Available before",
    invoice: "This invoice", excess: "Excess", remaining_after: "Remaining after", reference: "Approval ref",
    earlier_reference: "Earlier approval", earlier_amount: "Earlier amount", this_amount: "This amount",
    approved_at: "Approved at", candidate_pos: "Candidate POs", vendor_pos: "Vendor's POs",
    po_reference: "PO on invoice", problems: "Problems", vendor: "Vendor"};
  const rows = Object.entries(ev).map(([k, v]) =>
    `<dt>${label[k] || k}</dt><dd>${esc(Array.isArray(v) ? v.join(" · ") : v)}</dd>`).join("");
  const f = r.fields || {};
  const fieldRows = [["Vendor", f.vendor], ["Invoice no.", f.invoice_number], ["Date", f.invoice_date],
    ["PO reference", f.po_reference || "—"], ["Gross total", f.total]]
    .map(([k, v]) => `<dt>${k}</dt><dd>${esc(v ?? "not found")}</dd>`).join("");
  $("result").innerHTML = `<h2>3 · Decision · ${esc(r.id)}</h2>
    <span class="badge ${cls(r.status)}">${esc(r.status)}</span>
    <p class="reason">${esc(r.reason)}</p>
    <dl>${rows}</dl>
    <div class="sub"><h2>Extracted from ${esc(r.file)}</h2><dl>${fieldRows}</dl></div>`;
}

async function refresh() {
  const s = await (await fetch("/state")).json();
  $("pos").innerHTML = s.orders.map((o) => `<div class="po"><b>${o.po}</b> · ${esc(o.vendor)}
    <div class="bar"><i style="width:${Math.min(100, o.used_pct)}%"></i></div>
    <small>${o.available} available of ${o.ceiling}</small></div>`).join("");
  const n = (st) => s.runs.filter((r) => r.status === st).length;
  $("counts").innerHTML = `<span><b>${s.runs.length}</b>runs</span><span><b>${n("Approved")}</b>approved</span>
    <span><b>${n("Hold")}</b>on hold</span><span><b>${n("Duplicate blocked")}</b>duplicates blocked</span>`;
  $("history").innerHTML = s.runs.length ? s.runs.map((r, i) => `<tr class="row" data-i="${i}">
    <td>${esc(r.id)}</td><td>${new Date(r.at).toLocaleTimeString()}</td><td>${esc(r.file)}</td>
    <td>${esc(r.fields?.vendor || "—")} · ${esc(r.fields?.invoice_number || "—")}</td>
    <td>${esc(r.fields?.total || "—")}</td><td><span class="badge ${cls(r.status)}">${esc(r.status)}</span></td>
    <td>${esc(r.reason)}</td></tr>`).join("")
    : `<tr><td colspan="7" class="empty">No runs yet in this workspace.</td></tr>`;
  document.querySelectorAll("tr.row").forEach((tr) => tr.onclick = () => {
    const r = s.runs[+tr.dataset.i];
    resetStages(); r.stages.forEach(showStage); showResult(r);
    window.scrollTo({top: 0, behavior: "smooth"});
  });
}

async function startRun(payload) {
  if (busy) return;
  busy = true;
  resetStages();
  $("result").innerHTML = `<h2>3 · Decision</h2><p class="empty">Running…</p>`;
  try {
    const res = await fetch("/run", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      $("result").innerHTML = `<h2>3 · Decision</h2><p class="reason">${esc(err.error || "The run could not start.")}</p>`;
      return;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const e = JSON.parse(buf.slice(0, i));
        buf = buf.slice(i + 1);
        if (e.type === "stage") showStage(e); else showResult(e);
      }
    }
    STAGES.forEach(([k]) => { const li = $("st-" + k); if (!li.className) { li.className = "skip"; li.querySelector(".detail").textContent = "not reached"; } });
  } finally {
    busy = false;
    refresh();
  }
}

async function init() {
  resetStages();
  const ex = await (await fetch("/examples")).json();
  $("examples").innerHTML = ex.map((e) => `<button class="ex" data-id="${e.id}">
    <span class="exp">${esc(e.expect)}</span><b>${esc(e.title)}</b><small>${esc(e.note)}</small></button>`).join("");
  document.querySelectorAll(".ex").forEach((b) => b.onclick = () => startRun({example: b.dataset.id}));
  $("file").onchange = (ev) => {
    const file = ev.target.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) { $("result").innerHTML = `<h2>3 · Decision</h2><p class="reason">That file is over 5 MB. Upload a smaller PDF.</p>`; return; }
    const fr = new FileReader();
    fr.onload = () => startRun({file_b64: fr.result.split(",")[1], filename: file.name});
    fr.readAsDataURL(file);
    ev.target.value = "";
  };
  $("reset").onclick = async () => { await fetch("/reset", {method: "POST"}); resetStages();
    $("result").innerHTML = `<h2>3 · Decision</h2><p class="empty">Ledger reset to the opening balance.</p>`; refresh(); };
  refresh();
}
init();
