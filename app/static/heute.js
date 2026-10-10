// Tagesansicht "Heute": holt alle 30 s /api/heute und rendert.
// Nur neu zeichnen, wenn sich Daten ändern (bzw. jede Minute für "jetzt/vorbei").

const $ = (id) => document.getElementById(id);
const WD = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
let data = null;
let lastJson = "";
let lastFetchOk = Date.now();
let simOffset = 0;   // nur bei Vorschau (?tag=...) ≠ 0
const nowDate = () => new Date(Date.now() + simOffset);

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");

// ───── Icons (Linien-Stil, 24er Raster) ─────
const P = {
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  cloud: '<path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/>',
  cloudSun: '<path d="M12 2v2M4.93 4.93l1.41 1.41M20 12h2M19.07 4.93l-1.41 1.41M15.95 12.65a4 4 0 0 0-5.93-4.13"/><path d="M13 22H7a5 5 0 1 1 4.9-6H13a3 3 0 0 1 0 6Z"/>',
  cloudMoon: '<path d="M13 16a3 3 0 0 1 0 6H7a5 5 0 1 1 4.9-6Z"/><path d="M10.1 9A6 6 0 0 1 16 4a4.24 4.24 0 0 0 6 6 6 6 0 0 1-3 5.2"/>',
  fog: '<path d="M4 14.9A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.24"/><path d="M16 17H7M17 21H9"/>',
  drizzle: '<path d="M4 14.9A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.24"/><path d="M8 19v1M8 14v1M16 19v1M16 14v1M12 21v1M12 16v1"/>',
  rain: '<path d="M4 14.9A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.24"/><path d="M16 14v6M8 14v6M12 16v6"/>',
  snow: '<path d="M4 14.9A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.24"/><path d="M8 15h.01M8 19h.01M12 17h.01M12 21h.01M16 15h.01M16 19h.01"/>',
  storm: '<path d="M6 16.33A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 .5 8.97"/><path d="m13 12-3 5h4l-3 5"/>',
  broom: '<path d="M21.5 2.5 12 12"/><path d="M10.6 10.6 13.4 13.4 9.2 20.5 3.5 14.8Z"/><path d="M9.3 12.1 12.1 14.9M10.2 13 5.4 16.7M11.2 14 7.3 18.6"/>',
};
const svg = (name) => `<svg viewBox="0 0 24 24">${P[name]}</svg>`;

function weatherIcon(code, day) {
  if (code === 0) return day ? "sun" : "moon";
  if (code <= 2) return day ? "cloudSun" : "cloudMoon";
  if (code === 3) return "cloud";
  if (code === 45 || code === 48) return "fog";
  if (code >= 51 && code <= 57) return "drizzle";
  if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) return "rain";
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return "snow";
  if (code >= 95) return "storm";
  return "cloud";
}

// ───── Uhr ─────
function tickClock() {
  const d = nowDate();
  $("clock").textContent = `${WD[d.getDay()]} ${d.getHours()}:${pad(d.getMinutes())}`;
}

// ───── Rendering ─────
function people(list) {
  return (list || []).map((p) => `<span class="${p.k}">${esc(p.t)}</span>`).join(" ");
}

function renderLessons() {
  const el = $("lessons");
  const st = data.status.untis;
  const day = data.day;
  $("untis-stale").textContent = st.loaded && !st.ok ? `Stand ${st.since}` : "";

  if (!day) {
    $("day-label").textContent = "Stunden";
    el.innerHTML = `<div class="empty-state">${
      st.loaded ? "Keine Stunden in den nächsten Tagen" :
      st.error ? "Untis gerade nicht erreichbar" : "Stundenplan wird geladen …"}</div>`;
    return;
  }
  $("day-label").textContent = day.label === "Heute" ? "Heute" :
    day.label === "Morgen" ? "Morgen" : day.label;

  const now = nowDate();
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const isToday = day.label === "Heute";

  el.classList.toggle("compact", day.slots.length > 6);
  el.innerHTML = day.slots.map((s) => {
    const past = isToday && s.end_min <= nowMin;
    const current = isToday && s.start_min <= nowMin && nowMin < s.end_min;
    const cs = s.courses;
    const allCancelled = cs.every((c) => c.status === "cancelled");
    const anyChange = cs.some((c) => c.status !== "normal");
    const cardCls = allCancelled ? "cancelled" :
      cs.every((c) => c.status === "event") ? "event" : anyChange ? "changed" : "";
    const courses = cs.map((c) => {
      const subj = c.subject_old && c.subject_old !== c.subject
        ? `<span class="old">${esc(c.subject_old)}</span> <span class="new">${esc(c.subject)}</span>`
        : esc(c.subject);
      return `<div class="course ${c.status}">
          <div class="subj">${subj}</div>
          <div class="who">${people(c.teachers)}</div>
          <div class="where">${people(c.rooms)}</div>
        </div>`;
    }).join("");
    return `<div class="slot ${past ? "past" : ""} ${current ? "now" : ""}">
        <div class="slot-time"><span class="t-start">${s.start}</span><span>${s.end}</span></div>
        <div class="card ${cardCls} ${cs.length > 1 ? "multi" : ""}" style="--c1:${cs[0].color}">${courses}</div>
      </div>`;
  }).join("");
}

function renderTasks() {
  const el = $("tasks");
  const st = data.status.untis;
  const tasks = (data.day && data.day.tasks) || [];
  const hw = data.homework || [];
  let html = "";

  if (tasks.length) {
    html += `<div class="group-label">Aufträge ${esc(data.day.label.toLowerCase() === "heute" ? "heute" : data.day.label)}</div>`;
    html += tasks.map((t) => `<div class="task" style="--c1:${t.color}">
        <div class="task-head"><span class="dot"></span>${esc(t.subject)}<span class="meta">${esc(t.label)}</span></div>
        <div class="task-text">${esc(t.text)}</div>
      </div>`).join("");
  }
  if (hw.length) {
    html += `<div class="group-label">Hausaufgaben</div>`;
    html += hw.map((h) => `<div class="task" style="--c1:${h.color}">
        <div class="task-head"><span class="dot"></span>${esc(h.subject)}<span class="meta">bis ${esc(h.due_label)}</span></div>
        <div class="task-text">${esc(h.text)}</div>
      </div>`).join("");
  }
  if (!html) {
    html = `<div class="events-empty">${
      st.loaded ? "Keine Hausaufgaben oder Aufträge" :
      st.error ? "Untis gerade nicht erreichbar" : "Wird geladen …"}</div>`;
  }
  el.innerHTML = html;
  el.classList.toggle("overflow", el.scrollHeight > el.clientHeight + 4);
}

function renderEvents() {
  const el = $("events");
  const st = data.status.calendar;
  if (!el || !st) return;   // kein Kalender eingerichtet -> Spalte gibt es nicht
  $("cal-stale").textContent = st.loaded && !st.ok ? `Stand ${st.since}` : "";
  const evs = data.events || [];
  if (!evs.length) {
    el.innerHTML = `<div class="events-empty">${
      st.loaded ? "Keine Termine in den nächsten Wochen" :
      st.error ? "Kalender gerade nicht erreichbar" : "Wird geladen …"}</div>`;
    return;
  }
  let html = "", group = null;
  for (const e of evs) {
    if (e.group !== group) {
      group = e.group;
      html += `<div class="ev-date">${esc(group)}</div>`;
    }
    const meta = [e.time, e.until].filter(Boolean).join(" · ");
    html += `<div class="glass" style="--c1:${e.color}">
        <div class="ev-title">${esc(e.title)}</div>${meta ? `<div class="ev-meta">${esc(meta)}</div>` : ""}
      </div>`;
  }
  el.innerHTML = html;
  // Termine, die nicht mehr ganz passen, ausblenden statt abzuschneiden
  const limit = el.getBoundingClientRect().bottom;
  for (const child of [...el.children]) {
    if (child.getBoundingClientRect().bottom > limit) child.remove();
  }
  // Datumsüberschriften ohne sichtbaren Termin darunter entfernen
  for (const d of [...el.querySelectorAll(".ev-date")]) {
    const next = d.nextElementSibling;
    if (!next || !next.classList.contains("glass")) d.remove();
  }
}

function renderHead() {
  const w = data.weather;
  $("date").textContent = data.date_long;
  if (w) {
    $("temp").textContent = `${w.temp}°`;
    $("wicon").innerHTML = svg(weatherIcon(w.code, w.is_day));
    $("wmeta").textContent = `${w.max}° / ${w.min}°` + (w.rain >= 20 ? ` · Regen ${w.rain} %` : "");
  }
  const d = data.duty;
  $("duty").innerHTML = d ? `${svg("broom")}<div><div class="duty-label">Ordnungsdienst</div><div class="duty-names">${esc(d)}</div></div>` : "";
}

function renderStatus() {
  const msgs = [];
  const names = { untis: "Untis", calendar: "Kalender", weather: "Wetter" };
  for (const [k, s] of Object.entries(data.status)) {
    if (s.error && s.loaded) msgs.push(`${names[k]} nicht erreichbar · Stand ${s.since}`);
    else if (s.error) msgs.push(`${names[k]} nicht erreichbar`);
  }
  if (Date.now() - lastFetchOk > 90000) msgs.unshift("Keine Verbindung zum Server");
  $("status").innerHTML = msgs.map((m) => `<span>${esc(m)}</span>`).join("");
}

function render() {
  if (!data) return;
  renderHead();
  renderLessons();
  renderTasks();
  renderEvents();
  renderStatus();
}

async function refresh() {
  try {
    const r = await fetch("/api/heute" + location.search, { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    const txt = await r.text();
    lastFetchOk = Date.now();
    if (txt !== lastJson) {
      lastJson = txt;
      data = JSON.parse(txt);
      simOffset = data.preview ? Date.parse(data.now) - Date.now() : 0;
      tickClock();
      render();
      markReady();
    } else {
      renderStatus();
    }
  } catch (e) {
    if (data) renderStatus();
  }
}

// Fertig-Signal für die Boot-Animation (bootsplash_x.py wartet auf den Fenstertitel
// "Heute - Chromium"): erst nach dem ersten Zeichnen, spätestens nach 20 s.
function markReady() { if (document.title !== "Heute") document.title = "Heute"; }
setTimeout(markReady, 20000);

tickClock();
refresh();
setInterval(tickClock, 1000);
setInterval(refresh, 30000);
// jede Minute "jetzt"/"vorbei" der Stunden aktualisieren
setInterval(() => { if (data) renderLessons(); }, 60000);
