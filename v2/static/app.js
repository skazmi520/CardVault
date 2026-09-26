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
  if (!r.ok || data.ok === false) {
    // status and body ride along, so a caller can act on e.g. a 409's details
    const e = new Error(data.error || r.statusText);
    e.status = r.status; e.data = data;
    throw e;
  }
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

function lineChart(el, points) {
  // Portfolio value over time. Axes and gridlines are always drawn — a line with
  // no scale is decoration, not data. Colours come from the theme tokens.
  if (!points || points.length < 2) {
    el.innerHTML = '<div class="muted" style="padding:30px 0;text-align:center;font-size:12.5px">Not enough history yet — a snapshot is recorded each day the dashboard is opened.</div>';
    return;
  }
  const draw = () => lineChartDraw(el, points);
  if (!el._cvResize) { el._cvResize = true; window.addEventListener("resize", () => { clearTimeout(el._cvT); el._cvT = setTimeout(draw, 120); }); }
  draw();
}

function lineChartDraw(el, points) {
  const W = Math.max(320, el.clientWidth || 1000), H = Math.max(120, el.clientHeight || 200);
  const PL = 46, PR = 12, PT = 10, PB = 22;
  const vals = points.map(p => p.value);
  let min = Math.min(...vals), max = Math.max(...vals);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.06;
  min -= pad; max += pad;
  const x = i => PL + i * (W - PL - PR) / (points.length - 1);
  const y = v => PT + (max - v) * (H - PT - PB) / (max - min);

  let grid = "", labels = "";
  const TICKS = 3;
  for (let t = 0; t <= TICKS; t++) {
    const v = min + (max - min) * t / TICKS, yy = y(v).toFixed(1);
    grid += `<line x1="${PL}" y1="${yy}" x2="${W - PR}" y2="${yy}" style="stroke:var(--border)" stroke-width="1"/>`;
    labels += `<text x="${PL - 8}" y="${(+yy + 4).toFixed(1)}" style="fill:var(--muted);font-family:var(--font-mono)" font-size="11" text-anchor="end">${fmtShort(v)}</text>`;
  }
  // one label per month change, so the axis reads as dates rather than samples
  const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  let lastM = -1, lastX = -1e9;
  points.forEach((p, i) => {
    const m = +p.date.slice(5, 7) - 1;
    if (m !== lastM) {
      lastM = m;
      // sparse early snapshots would stack three month names on top of each
      // other; a label only lands if it has 34px of clear room
      if (x(i) - lastX >= 34) {
        labels += `<text x="${x(i).toFixed(1)}" y="${H - 6}" style="fill:var(--muted)" font-size="11" text-anchor="${i === 0 ? "start" : "middle"}">${MON[m]}</text>`;
        lastX = x(i);
      }
    }
  });

  const d = points.map((p, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(p.value).toFixed(1)).join(" ");
  const last = points[points.length - 1];
  const gid = "cv-fill-" + Math.random().toString(36).slice(2, 7);
  if (getComputedStyle(el).position === "static") el.style.position = "relative";   // never undo an absolute placement
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">
    <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" style="stop-color:var(--accent)" stop-opacity=".16"/>
      <stop offset="1" style="stop-color:var(--accent)" stop-opacity="0"/>
    </linearGradient></defs>
    ${grid}
    <line x1="${PL}" y1="${H - PB}" x2="${W - PR}" y2="${H - PB}" style="stroke:var(--border-hi)"/>
    <path d="${d} L ${x(points.length - 1).toFixed(1)} ${H - PB} L ${PL} ${H - PB} Z" fill="url(#${gid})"/>
    <path d="${d}" fill="none" style="stroke:var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
    <g class="hover" style="display:none">
      <line class="hx" x1="0" y1="${PT}" x2="0" y2="${H - PB}" style="stroke:var(--border-hi)" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>
      <circle class="hc" cx="0" cy="0" r="4" style="fill:var(--accent);stroke:var(--bg)" stroke-width="2" vector-effect="non-scaling-stroke"/>
    </g>
    <circle cx="${x(points.length - 1).toFixed(1)}" cy="${y(last.value).toFixed(1)}" r="4" style="fill:var(--accent);stroke:var(--bg)" stroke-width="2" vector-effect="non-scaling-stroke"/>
    ${labels}
  </svg><div class="chart-tip"></div>`;

  // hover: nearest point, crosshair and a tooltip with the date and value
  const svg = el.querySelector("svg"), hov = svg.querySelector(".hover"), tip = el.querySelector(".chart-tip");
  const hx = svg.querySelector(".hx"), hc = svg.querySelector(".hc");
  el.addEventListener("mousemove", ev => {
    const r = svg.getBoundingClientRect();
    const px = (ev.clientX - r.left) / r.width * W;
    let i = Math.round((px - PL) / ((W - PL - PR) / (points.length - 1)));
    i = Math.max(0, Math.min(points.length - 1, i));
    const p = points[i], cx = x(i), cy = y(p.value);
    hx.setAttribute("x1", cx); hx.setAttribute("x2", cx);
    hc.setAttribute("cx", cx); hc.setAttribute("cy", cy);
    hov.style.display = "";
    tip.innerHTML = `<div class="muted">${esc(p.date)}</div><div class="v">${fmt(p.value)}</div>`;
    tip.style.display = "block";
    const left = cx / W * r.width, top = cy / H * r.height;
    tip.style.left = Math.min(left + 12, r.width - tip.offsetWidth - 4) + "px";
    tip.style.top = Math.max(0, top - tip.offsetHeight - 12) + "px";
  });
  el.addEventListener("mouseleave", () => { hov.style.display = "none"; tip.style.display = "none"; });
}

/* theme: dark is the default; a saved "light" wins */
document.addEventListener("DOMContentLoaded", () => {
  const b = document.getElementById("theme-btn");
  if (!b) return;
  b.addEventListener("click", () => {
    const light = document.documentElement.getAttribute("data-theme") !== "light";
    if (light) document.documentElement.setAttribute("data-theme", "light");
    else document.documentElement.removeAttribute("data-theme");
    try { localStorage.setItem("cardvault_theme", light ? "light" : "dark"); } catch (e) {}
  });
});
