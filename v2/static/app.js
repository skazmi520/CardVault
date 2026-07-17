/* CardVault v2 — shared helpers */

function fmt(n) {
  if (n === null || n === undefined || isNaN(n)) return "—";
  const sign = n < 0 ? "-" : "";
  return sign + "$" + Math.abs(n).toLocaleString("en-US",
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtGain(n) {
  if (n === null || n === undefined || isNaN(n)) return "—";
  return (n >= 0 ? "+" : "") + fmt(n).replace("$-", "-$");
}

function gainClass(n) {
  if (n === null || n === undefined || isNaN(n)) return "muted";
  return n >= 0 ? "pos" : "neg";
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({ ok: false, error: "bad response" }));
  if (!r.ok || data.ok === false) throw new Error(data.error || r.statusText);
  return data;
}

/* ── toasts ────────────────────────────────────────── */
function toast(msg, type = "success", ms = 3500) {
  let stack = document.querySelector(".toast-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.className = "toast-stack";
    document.body.appendChild(stack);
  }
  const t = document.createElement("div");
  t.className = "toast " + type;
  t.textContent = msg;
  stack.appendChild(t);
  requestAnimationFrame(() => t.classList.add("show"));
  setTimeout(() => { t.classList.remove("show"); setTimeout(() => t.remove(), 300); }, ms);
}

/* queue a toast to show after the page reloads / navigates */
function toastAfterReload(msg, type = "success") {
  sessionStorage.setItem("cardvault_toast", JSON.stringify({ msg, type }));
}
document.addEventListener("DOMContentLoaded", () => {
  const raw = sessionStorage.getItem("cardvault_toast");
  if (!raw) return;
  sessionStorage.removeItem("cardvault_toast");
  try { const q = JSON.parse(raw); toast(q.msg, q.type); } catch (e) { /* stale entry */ }
});

/* promise-based replacement for confirm() — resolves true/false */
function confirmDialog({ title = "Are you sure?", body = "", confirmText = "Confirm", danger = false } = {}) {
  return new Promise(resolve => {
    const bd = document.createElement("div");
    bd.className = "modal-backdrop";
    bd.innerHTML = `<div class="modal cdialog">
      <h2></h2><p class="muted cdialog-body"></p>
      <div class="actions">
        <button class="btn ghost cd-cancel">Cancel</button>
        <button class="btn${danger ? " danger" : ""} cd-ok"></button>
      </div></div>`;
    bd.querySelector("h2").textContent = title;
    bd.querySelector(".cdialog-body").textContent = body;
    bd.querySelector(".cd-ok").textContent = confirmText;
    const done = val => {
      document.removeEventListener("keydown", onKey, true);
      bd.remove();
      resolve(val);
    };
    const onKey = e => {
      if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); done(false); }
      else if (e.key === "Enter") { e.stopPropagation(); e.preventDefault(); done(true); }
    };
    document.addEventListener("keydown", onKey, true);
    bd.addEventListener("click", e => { if (e.target === bd) done(false); });
    bd.querySelector(".cd-cancel").addEventListener("click", () => done(false));
    bd.querySelector(".cd-ok").addEventListener("click", () => done(true));
    document.body.appendChild(bd);
    bd.querySelector(".cd-ok").focus();
  });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* line chart with X/Y axes, gridlines and tick labels */
function fmtShort(n) {
  if (Math.abs(n) >= 1000) return "$" + (n / 1000).toFixed(Math.abs(n) >= 100000 ? 0 : 1) + "k";
  return "$" + n.toFixed(0);
}

function lineChart(el, points, { color = "#4f8cff" } = {}) {
  if (!points || points.length < 2) {
    el.innerHTML = '<div class="muted" style="padding:30px 0;text-align:center">Not enough repricing history yet — a snapshot is recorded each day the dashboard is opened.</div>';
    return;
  }
  const W = 900, H = 270, PL = 62, PR = 18, PT = 16, PB = 30;
  const vals = points.map(p => p.value);
  let min = Math.min(...vals), max = Math.max(...vals);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.08;
  min -= pad; max += pad;

  const x = i => PL + i * (W - PL - PR) / (points.length - 1);
  const y = v => PT + (max - v) * (H - PT - PB) / (max - min);

  /* Y gridlines + labels */
  let grid = "", labels = "";
  const TICKS = 4;
  for (let t = 0; t <= TICKS; t++) {
    const v = min + (max - min) * t / TICKS;
    const yy = y(v);
    grid += `<line x1="${PL}" y1="${yy.toFixed(1)}" x2="${W - PR}" y2="${yy.toFixed(1)}"
                   stroke="#262b34" stroke-width="1"/>`;
    labels += `<text x="${PL - 8}" y="${(yy + 4).toFixed(1)}" fill="#8b93a3"
                     font-size="11" text-anchor="end">${fmtShort(v)}</text>`;
  }

  /* X tick labels (up to 6, evenly spaced) */
  const n = points.length, steps = Math.min(6, n);
  for (let s = 0; s < steps; s++) {
    const i = Math.round(s * (n - 1) / (steps - 1));
    const anchor = s === 0 ? "start" : s === steps - 1 ? "end" : "middle";
    labels += `<text x="${x(i).toFixed(1)}" y="${H - 8}" fill="#8b93a3"
                     font-size="11" text-anchor="${anchor}">${esc(points[i].date.slice(5))}</text>`;
  }

  const d = points.map((p, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(p.value).toFixed(1)).join(" ");
  const last = points[points.length - 1];

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}">
    ${grid}
    <line x1="${PL}" y1="${PT}" x2="${PL}" y2="${H - PB}" stroke="#2a2f39"/>
    <line x1="${PL}" y1="${H - PB}" x2="${W - PR}" y2="${H - PB}" stroke="#2a2f39"/>
    <path d="${d} L ${x(points.length - 1).toFixed(1)} ${H - PB} L ${PL} ${H - PB} Z"
          fill="${color}" opacity="0.08"/>
    <path d="${d}" fill="none" stroke="${color}" stroke-width="2"
          stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${x(points.length - 1).toFixed(1)}" cy="${y(last.value).toFixed(1)}" r="3.5" fill="${color}"/>
    <text x="${W - PR}" y="${(y(last.value) - 10).toFixed(1)}" fill="#e8eaf0"
          font-size="12" font-weight="600" text-anchor="end">${fmt(last.value)}</text>
    ${labels}
  </svg>`;
}
