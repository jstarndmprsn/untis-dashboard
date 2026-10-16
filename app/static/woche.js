// Wochenansicht: die Seite wird nur einmal geladen; danach holt das Skript alle 5 s nur
// /content und tauscht die Tabelle nur aus, wenn sich wirklich etwas geändert hat
// (kein kompletter Reload -> kein Flackern, weniger CPU).
const VERSION = document.body.dataset.version;
const content = document.getElementById("content");
let last = content.innerHTML;

const pad = (n) => String(n).padStart(2, "0");
function updateClock() {
  const el = document.getElementById("clock");
  if (el) {
    const d = new Date();
    el.textContent = pad(d.getHours()) + ":" + pad(d.getMinutes());
  }
}

async function refresh() {
  try {
    const r = await fetch("/content", { cache: "no-store" });
    if (!r.ok) return;
    // neue Server-Version eingespielt -> einmal komplett neu laden (neues CSS/JS)
    if (r.headers.get("X-Version") !== VERSION) { location.reload(); return; }
    const html = await r.text();
    if (html !== last) {
      last = html;
      content.innerHTML = html;
      updateClock();
    }
  } catch (e) {
    // Server kurz weg (z. B. Neustart) -> alten Stand stehen lassen
  }
}

updateClock();
setInterval(updateClock, 1000);
setInterval(refresh, 5000);

// F8: Versionsnummer anzeigen
document.addEventListener("keydown", (e) => {
  if (e.key !== "F8") return;
  e.preventDefault();
  const v = document.getElementById("version-screen");
  const show = v.style.display !== "flex";
  v.style.display = show ? "flex" : "none";
  content.style.display = show ? "none" : "block";
});
