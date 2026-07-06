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

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* minimal line chart: draws into a container as SVG */
function lineChart(el, points, { color = "#4f8cff" } = {}) {
  if (!points || points.length < 2) {
    el.innerHTML = '<div class="muted" style="padding:30px 0;text-align:center">Not enough repricing history yet — a snapshot is recorded each day the dashboard is opened.</div>';
    return;
  }
  const W = 900, H = 220, P = 34;
  const vals = points.map(p => p.value);
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = (max - min) || 1;
  const x = i => P + i * (W - 2 * P) / (points.length - 1);
  const y = v => H - P - (v - min) * (H - 2 * P) / span;
  let d = points.map((p, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(p.value).toFixed(1)).join(" ");
  const last = points[points.length - 1], first = points[0];
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <path d="${d} L ${x(points.length - 1)} ${H - P} L ${x(0)} ${H - P} Z"
          fill="${color}" opacity="0.08"/>
    <path d="${d}" fill="none" stroke="${color}" stroke-width="2"/>
    <circle cx="${x(points.length - 1)}" cy="${y(last.value)}" r="3.5" fill="${color}"/>
    <text x="${P}" y="14" fill="#8b93a3" font-size="11">${esc(first.date)}</text>
    <text x="${W - P}" y="14" fill="#8b93a3" font-size="11" text-anchor="end">${esc(last.date)} · ${fmt(last.value)}</text>
    <text x="${P}" y="${y(max) - 6}" fill="#8b93a3" font-size="11">${fmt(max)}</text>
    <text x="${P}" y="${y(min) + 14}" fill="#8b93a3" font-size="11">${fmt(min)}</text>
  </svg>`;
}
