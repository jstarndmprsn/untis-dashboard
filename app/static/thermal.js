// Thermal-Dashboard: holt alle 2 s /api/thermal und zeichnet Kennzahlen + Verläufe.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");
const NS = "http://www.w3.org/2000/svg";
const WINDOW_S = 15 * 60;

function tickClock() {
  const d = new Date();
  $("clock").textContent = `${d.getHours()}:${pad(d.getMinutes())}`;
}

// Liniendiagramm (eine Reihe, eine Achse) mit optionalen Referenzlinien
function lineChart(svg, ts, vals, { min, max, unit, refs = [], fmt = (v) => Math.round(v) }) {
  const W = svg.clientWidth, H = svg.clientHeight;
  const L = 56, R = 70, T = 8, B = 26;
  const now = ts.length ? ts[ts.length - 1] : Date.now() / 1000;
  const x = (t) => L + (1 - (now - t) / WINDOW_S) * (W - L - R);
  const y = (v) => T + (1 - (v - min) / (max - min)) * (H - T - B);
  let out = "";

  // Raster + Y-Beschriftung (zurückhaltend)
  const steps = 4;
  for (let i = 0; i <= steps; i++) {
    const v = min + (max - min) * i / steps;
    out += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)" stroke-width="1"/>`;
    out += `<text x="${L - 12}" y="${y(v) + 5}" text-anchor="end">${fmt(v)}</text>`;
  }
  // X-Beschriftung
  for (const m of [15, 10, 5, 0]) {
    const t = now - m * 60;
    out += `<text x="${x(t)}" y="${H - 4}" text-anchor="${m === 0 ? "end" : m === 15 ? "start" : "middle"}">${m === 0 ? "jetzt" : "−" + m + " min"}</text>`;
  }
  // Referenzlinien (Schwellen), mit Text statt nur Farbe
  for (const r of refs) {
    if (r.v < min || r.v > max) continue;
    out += `<line x1="${L}" x2="${W - R}" y1="${y(r.v)}" y2="${y(r.v)}" stroke="${r.color}" stroke-width="1.5" stroke-dasharray="6 6" opacity=".8"/>`;
    out += `<text class="ref-label" x="${L + 8}" y="${y(r.v) - 6}" style="fill:${r.color}">${esc(r.label)}</text>`;
  }
  // Linie + dezente Fläche (Lücken bei fehlenden Werten)
  const pts = [];
  ts.forEach((t, i) => { if (vals[i] != null && now - t <= WINDOW_S) pts.push([x(t), y(Math.min(max, Math.max(min, vals[i])))]); });
  if (pts.length > 1) {
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join("");
    out += `<path d="${d}L${pts[pts.length - 1][0]} ${y(min)}L${pts[0][0]} ${y(min)}Z" fill="var(--series)" opacity=".08"/>`;
    out += `<path d="${d}" fill="none" stroke="var(--series)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    const last = pts[pts.length - 1];
    out += `<circle cx="${last[0]}" cy="${last[1]}" r="5" fill="var(--series)" stroke="var(--bg)" stroke-width="2"/>`;
    out += `<text class="v-label" x="${last[0] + 12}" y="${last[1] + 6}">${fmt(vals[vals.length - 1])}${unit}</text>`;
  }
  svg.innerHTML = out;
}

function statusOf(d) {
  const c = d.cur, g = d.guard, lim = d.limits;
  const notes = [];
  let level = "good";
  const bump = (l) => { const o = { good: 0, warn: 1, crit: 2 }; if (o[l] > o[level]) level = l; };

  if (g.alive && (g.state === "countdown" || g.state === "critical")) {
    bump("crit");
    notes.push(["crit", "■", `Überhitzung! Notabschaltung ${g.state === "countdown" ? "läuft" : "droht"}`]);
  } else if (c.temp != null && c.temp >= lim.fan_emergency) {
    bump("warn");
    notes.push(["warn", "▲", `Sehr warm – Lüfter dreht stark auf`]);
  } else if (c.temp != null && c.temp >= lim.warn) {
    bump("warn");
    notes.push(["warn", "▲", `Warm (ab ${lim.warn} °C)`]);
  }
  const th = d.throttled;
  if (th && th.now.length) { bump("warn"); notes.push(["warn", "▲", `Aktuell: ${th.now.join(", ")}`]); }
  if (th && th.since_boot.length) notes.push(["warn", "▲", `Seit dem Start aufgetreten: ${th.since_boot.join(", ")}`]);
  if (!g.alive) { bump("warn"); notes.push(["warn", "▲", "Überhitzungsschutz läuft nicht"]); }
  else notes.push(["good", "●", `Notabschaltung ab ${g.crit_temp} °C (nach ${g.crit_seconds} s), Abbruch unter ${g.abort_temp} °C`]);
  if (g.alive && g.fan_mode === "software") notes.push(["good", "●", "Lüfter: stufenlose Kurve (Kernel-Notfallkurve ab 82 °C)"]);
  else { bump("warn"); notes.push(["warn", "▲", "Lüfter nur über Kernel-Notfallkurve gesteuert"]); }
  if (th && !th.now.length && !th.since_boot.length) notes.push(["good", "●", "Keine Drosselung oder Unterspannung"]);
  return { level, notes };
}

const LEVEL_TEXT = { good: ["●", "Alles in Ordnung"], warn: ["▲", "Achtung"], crit: ["■", "Kritisch"] };

function tempTag(t, lim) {
  if (t == null) return "";
  if (t >= lim.fan_emergency) return `<span class="tag crit">heiß</span>`;
  if (t >= lim.warn) return `<span class="tag warn">warm</span>`;
  return `<span class="tag good">normal</span>`;
}

function render(d) {
  const c = d.cur, lim = d.limits;
  const { level, notes } = statusOf(d);
  const [ic, txt] = LEVEL_TEXT[level];
  const pill = $("status");
  pill.className = "pill " + level;
  pill.innerHTML = `<span class="ic">${ic}</span>${txt}`;

  $("v-temp").innerHTML = c.temp != null ? `${c.temp.toFixed(1)}<small>°C</small>` : "–";
  $("s-temp").innerHTML = tempTag(c.temp, lim);
  $("v-fan").innerHTML = c.rpm != null ? `${c.rpm.toLocaleString("de-DE")}<small>U/min</small>` : "–";
  const soft = d.guard.alive && d.guard.fan_mode === "software";
  $("s-fan").textContent = `${c.pwm_pct ?? "–"} % · ${soft ? "stufenlose Kurve" : "Kernel-Stufe " + c.stage + "/" + c.stage_max}`;
  $("v-cpu").innerHTML = `${Math.round(c.cpu)}<small>%</small>`;
  $("s-cpu").textContent = `${c.freq_ghz ?? "–"} GHz · Last ${c.load.join(" / ")}`;
  $("v-mem").innerHTML = `${c.mem_pct}<small>%</small>`;
  const up = c.uptime_s, h = Math.floor(up / 3600), m = Math.floor((up % 3600) / 60);
  $("s-mem").textContent = `${c.mem_used_gb} von ${c.mem_total_gb} GB · läuft seit ${h} h ${m} min`;

  const H = d.hist;
  lineChart($("c-temp"), H.t, H.temp, { min: 30, max: 90, unit: "°", fmt: (v) => Math.round(v),
    refs: [
      { v: lim.warn, label: "warm", color: "var(--warn)" },
      ...(d.guard.alive ? [{ v: d.guard.crit_temp, label: "Notabschaltung", color: "var(--crit)" }] : []),
    ] });
  lineChart($("c-cpu"), H.t, H.cpu, { min: 0, max: 100, unit: " %" });
  lineChart($("c-rpm"), H.t, H.rpm, { min: 0, max: lim.fan_max_rpm, unit: "",
    fmt: (v) => v >= 1000 ? (v / 1000).toFixed(1).replace(".", ",") + "k" : Math.round(v) });

  $("cores").innerHTML = c.cores.map((v, i) => `<div class="core"><span>Kern ${i + 1}</span>
      <div class="bar"><i style="width:${Math.max(1, v)}%"></i></div><b>${Math.round(v)} %</b></div>`).join("");
  $("procs").innerHTML = d.procs.map((p) => `<div class="proc"><span>${esc(p.name)}</span><span>${p.cpu.toFixed(1)}</span></div>`).join("");
  $("notes").innerHTML = notes.map(([l, i, t]) => `<div class="note ${l}"><span class="ic">${i}</span><span>${esc(t)}</span></div>`).join("");
}

async function refresh() {
  try {
    const r = await fetch("/api/thermal", { cache: "no-store" });
    if (r.ok) render(await r.json());
  } catch (e) { /* Server kurz weg -> alten Stand behalten */ }
}

tickClock();
refresh();
setInterval(tickClock, 1000);
setInterval(refresh, 2000);
