"""
Flask-Server für die Klassen-Anzeige (Port 5000, PORT in config.env).

  /         Wochen-Stundenplan mit Vertretungen (dieses Modul)
  /heute    Tagesansicht (dashboard.py)
  /thermal  System-/Temperatur-Dashboard (thermal.py)

Einstellungen: config.env im Projektordner (anlegen mit `python3 app/configure.py`).
Ausprobieren ohne Untis-Konto:  python3 app/server.py --demo   [--no-calendar]
"""
import argparse
import time
from flask import Flask
from datetime import date, timedelta
from html import escape

import config
import dashboard
import thermal
import untis

app = Flask(__name__)

_ap = argparse.ArgumentParser()
_ap.add_argument("--demo", action="store_true", help="erfundene Beispieldaten statt Untis")
_ap.add_argument("--no-calendar", action="store_true", help="Demo ohne Kalender-Spalte")
_ap.add_argument("--port", type=int)
_args, _ = _ap.parse_known_args()

CFG = config.load()
if _args.demo:
    CFG["DEMO"] = "1"
if config.demo(CFG):
    CFG["UNTIS_CLASS"] = CFG["UNTIS_CLASS"] or "9a"
    if not _args.no_calendar and not CFG["CALENDAR_ICS_URL"]:
        CFG["CALENDAR_ICS_URL"] = "demo"
    if not CFG["WEATHER_LAT"]:
        CFG["WEATHER_LAT"] = CFG["WEATHER_LON"] = "demo"
elif not CFG["_exists"]:
    print(f"WARNUNG: {config.CONFIG_FILE} nicht gefunden – zuerst `python3 app/configure.py` "
          f"ausführen (oder mit --demo starten)", flush=True)

# Stammdaten (Lehrer, Räume, Fächer, Klassen-ID, Zeitraster) teilen sich beide Ansichten
MASTER = untis.MasterData(CFG)

# Tagesansicht /heute – eigenes Modul dashboard.py
dashboard.init(app, CFG, MASTER)
# Thermal-Dashboard /thermal (beide Tasten 2 s halten)
thermal.init(app)

# Nach dem Booten ist das Netz oft noch nicht da -> so lange "wird geladen" statt Fehler zeigen
STARTUP_GRACE = 180

DAYS = ["Mo", "Di", "Mi", "Do", "Fr"]

# Reihenfolge mehrerer Kurse in einer Zelle: Änderungen zuerst (bekommen mehr Platz)
STATUS_ORDER = {"cancelled": 0, "changed": 1, "event": 2, "normal": 3}

MARK_ICONS = {
    # Hausaufgabe fällig (Buch) / Notiz bzw. Arbeitsauftrag (Sprechblase), wie auf /heute
    "hw": ("Hausaufgabe fällig", '<path d="M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2z"/>'
                                 '<path d="M22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z"/>'),
    "notes": ("Notiz", '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'
                       '<path d="M8 8.5h8M8 12.5h5"/>'),
}


def hhmm(v):
    return f"{v // 100:02d}:{v % 100:02d}"


def build_periods(master_periods, lessons):
    """Zeilen der Tabelle = Stunden aus dem Untis-Zeitraster, aber nur die, in denen diese
    Woche etwas stattfindet (viele Raster reichen von der Frühstunde bis abends). Ohne
    Zeitraster (nicht freigegeben) werden sie aus den Anfangs-/Endzeiten abgeleitet."""
    if master_periods:
        periods = [p for p in master_periods
                   if any(l["start"] < p["end"] and l["end"] > p["start"] for l in lessons)]
    else:
        times = sorted({(l["start"], l["end"]) for l in lessons})
        periods = [{"name": str(i + 1), "start": s, "end": e} for i, (s, e) in enumerate(times)]
    out = []
    for p in periods:
        label = f"{p['name']}. Std" if p["name"].isdigit() else p["name"]
        out.append({**p, "label": label, "time": f"{hhmm(p['start'])} - {hhmm(p['end'])}"})
    return out


def covered_periods(periods, start, end):
    """Indizes der Stunden, die eine Unterrichtseinheit (start–end, hhmm) belegt."""
    idx = [i for i, p in enumerate(periods) if p["start"] < end and p["end"] > start]
    if idx:
        return idx
    # außerhalb des Rasters (z. B. vor der 1. Stunde) -> nächstliegende Stunde
    before = [i for i, p in enumerate(periods) if p["start"] <= start]
    return [before[-1] if before else 0]

CSS = """
body { margin: 0; font-family: Arial, Helvetica, sans-serif; background: #111; color: #fff; }
table { width: 100vw; height: 100vh; border-collapse: separate; border-spacing: 10px; table-layout: fixed; }
th { background: #222; font-size: 1.5em; color: #fff; border: 1px solid #333; padding: 3px; text-align: center; border-radius: 3px; }
th:first-child { width: 120px; font-size: 1.2em; }
th.today { color: rgb(255, 96, 54); }
th .date { display: block; font-size: 0.9em; color: #aaa; margin-top: 3px; }
td { font-size: 1.1em; padding: 10px 10px 10px 18px; border-radius: 15px; background: #333; color: #fff; position: relative; border: 3px solid transparent; overflow: hidden; word-wrap: break-word; }
td::before { content: ''; position: absolute; left: 0; top: 0; width: 8px; height: 100%; border-radius: 15px 0 0 15px; background: transparent; }
td .subject { font-weight: bold; display: block; }
td .info { font-size: 0.95em; display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
td .note { color: #bbb; }
.empty { background: #111; border: none; }
.error { display: flex; align-items: center; justify-content: center; height: 100vh; font-size: 2.5em; font-weight: bold; color: #ff3333; }

/* Vertretungs-Styles */
td.substitution { border-color: #4caf50 !important; }
td.cancelled { border-color: #ff3333 !important; }
td.event { border-color: #00bcd4 !important; }
.old-teacher { text-decoration: line-through; color: #888; }
.new-teacher, .new-room, .new-subject { color: #4caf50; border-bottom: 2px solid #4caf50; padding-bottom: 1px; }

/* Untis-Markierungen: Hausaufgabe fällig / Notiz */
.marks { white-space: nowrap; margin-left: 0.3em; }
.mark { width: 0.9em; height: 0.9em; vertical-align: -0.1em; margin-left: 0.2em; fill: none; stroke-width: 2;
        stroke-linecap: round; stroke-linejoin: round; }
.mark.hw { stroke: #ffcc4d; }
.mark.notes { stroke: #bbb; }

/* Multi-Course Styles */
.multi-course { display: flex; gap: 3px; height: 100%; }
.course { flex: 1; min-width: 0; padding: 2px; border-left: 1px solid #555; }
.course:first-child { border-left: none; }
td.multi::before { background: var(--stripe-gradient, var(--stripe-color)) !important; }

/* Fullscreen Version Display */
#version-screen { display: none; position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: #111; color: #fff; align-items: center; justify-content: center; font-size: 5em; font-weight: bold; z-index: 9999; }
"""


# ------------------ WOCHENTABELLE ------------------
# Die Stunden kommen aufbereitet aus dashboard.py – dieselbe Erkennung von Ausfall,
# Vertretung, Raumänderung und Veranstaltung wie in der Tagesansicht /heute, dieselben
# (offiziellen Untis-)Fachfarben und Markierungen. Hier wird nur die Tabelle gezeichnet.

def marks_html(marks):
    """Kleine Symbole hinter dem Fach für Untis-Markierungen der Stunde."""
    icons = "".join(f'<svg class="mark {m}" viewBox="0 0 24 24"><title>{MARK_ICONS[m][0]}</title>{MARK_ICONS[m][1]}</svg>'
                    for m in marks or [] if m in MARK_ICONS)
    return f'<span class="marks">{icons}</span>' if icons else ""


def people_html(people, cancelled, new_cls):
    """[{t, k}] aus dashboard -> alt durchgestrichen, neu grün; bei Ausfall alles durchgestrichen."""
    parts = []
    for p in people:
        name = escape(p["t"])
        if cancelled or p["k"] == "old":
            parts.append(f'<span class="old-teacher">{name}</span>')
        elif p["k"] == "new":
            parts.append(f'<span class="{new_cls}">{name}</span>')
        else:
            parts.append(name)
    return " ".join(parts)


def course_html(l, full_name=False):
    cancelled = l["status"] == "cancelled"
    subject = escape(l.get("subject_full") if full_name and l.get("subject_full") else l["subject"])
    if cancelled:
        subject = f'<span class="old-teacher">{subject}</span>'
    elif l["subject_old"] and l["subject_old"] != l["subject"]:
        subject = f'<span class="old-teacher">{escape(l["subject_old"])}</span> <span class="new-subject">{subject}</span>'
    info = " ".join(x for x in (people_html(l["teachers"], cancelled, "new-teacher"),
                                people_html(l["rooms"], False, "new-room")) if x)
    if l["note"] and l["note"] != l["subject"]:   # Vertretungstext, z. B. "Entfall", "AA für zuhause"
        note = escape(l["note"])
        info += f' <span class="note">· {note}</span>' if info else f'<span class="note">{note}</span>'
    return f'<span class="subject">{subject}{marks_html(l["marks"])}</span><br><span class="info">{info or "&nbsp;"}</span>'


def cell(lessons):
    """Alle Kurse einer Stunde -> {text, class, style, is_event}."""
    # Untis liefert manche Stunden mehrfach (z. B. eine pro beteiligter Klasse)
    uniq = {}
    for l in lessons:
        uniq.setdefault((l["group"], l["subject"], l["status"]), l)
    ls = sorted(uniq.values(), key=lambda l: (STATUS_ORDER.get(l["status"], 9), l["group"]))
    status = {l["status"] for l in ls}

    # gleiche Einteilung wie /heute: alle entfallen = rot, nur Veranstaltungen = blau,
    # irgendeine Änderung = grün
    if status == {"cancelled"}:
        cls = "normal cancelled"
    elif status == {"event"}:
        cls = "event"
    elif status != {"normal"}:
        cls = "normal substitution"
    else:
        cls = "normal"
    is_event = cls == "event"

    colors = [l["color"] for l in ls]
    if len(ls) == 1:
        return {"text": course_html(ls[0]), "class": cls, "style": f"--stripe-color:{colors[0]};",
                "is_event": is_event}
    # parallele Kurse mit gleichem Kürzel (z. B. drei Kunstkurse) -> volle Kursnamen ("BKu1", "BKu2")
    names = [l["subject"] for l in ls]
    full = len(set(names)) < len(names)
    parts = []
    for i, l in enumerate(ls):
        # erster Kurs mit Änderung bekommt mehr Platz
        flex = "2" if i == 0 and l["status"] in ("cancelled", "changed") else "1"
        parts.append(f'<div class="course" style="flex:{flex}">{course_html(l, full)}</div>')
    gradient = f"linear-gradient(to right, {', '.join(colors)})"
    return {"text": '<div class="multi-course">' + "".join(parts) + "</div>",
            "class": cls if is_event else cls + " multi",
            "style": f"--stripe-color:{colors[0]}; --stripe-gradient:{gradient};",
            "is_event": is_event}


def week_grid(lessons, periods, monday):
    """Aufbereitete Stunden (dashboard) -> {(Tag, Stunde): Zelle} für Mo–Fr."""
    raw = {(d, h): [] for d in range(5) for h in range(len(periods))}
    for l in lessons:
        d = (dashboard.int_date(l["date"]) - monday).days
        if not 0 <= d <= 4:
            continue
        # in alle Stunden des Zeitrasters eintragen, die die Einheit belegt (Blockstunden, Ausflüge)
        for h in covered_periods(periods, l["start"], l["end"] or l["start"] + 1):
            raw[(d, h)].append(l)

    grid = {}
    for key, ls in raw.items():
        grid[key] = ({**cell(ls), "rowspan": 1} if ls else
                     {"text": "", "class": "empty", "style": "", "is_event": False, "rowspan": 1})

    # Veranstaltungen über mehrere Stunden zu einer hohen Zelle zusammenfassen
    for d in range(5):
        for h in range(len(periods)):
            c = grid[(d, h)]
            if c["rowspan"] == 0 or not c["is_event"]:
                continue
            for h2 in range(h + 1, len(periods)):
                n = grid[(d, h2)]
                if n["is_event"] and n["text"] == c["text"]:
                    c["rowspan"] += 1
                    n["rowspan"] = 0
                else:
                    break
    return grid


# ==================== VERSION STRING - HIER ÄNDERN ====================
VERSION = "V.1.4"
# ======================================================================

PAGE_CSS = CSS + """
td.normal::before { background: var(--stripe-color); }
table.compact td { padding-top: 4px; padding-bottom: 4px; }
table.compact td br { display: none; }
.no-school { background: #222; color: #666; font-size: 1.5em; text-align: center; padding: 50px; border-radius: 15px; }
.stale { position: fixed; bottom: 6px; right: 12px; background: #5a1a1a; color: #ffb3b3; padding: 4px 10px; border-radius: 6px; font-size: 0.9em; z-index: 100; }
"""


def render_content():
    """Nur der wechselnde Teil der Seite (Tabelle bzw. Fehlermeldung).
    Enthält bewusst keine Uhrzeit -> ändert sich nur, wenn sich Daten ändern."""
    data, error, fetched_at = dashboard.untis_snapshot()

    # Noch nie erfolgreich geladen -> Lade- bzw. Fehleranzeige
    if data is None:
        if not error or time.time() - dashboard._STARTED < STARTUP_GRACE:
            return """
                <div class='error' style='color: #888;'>
                    <div style='text-align: center; font-size: 0.8em; font-weight: normal;'>Stundenplan wird geladen …</div>
                </div>"""
        return f"""
                <div class='error'>
                    <div style='text-align: center;'>
                        <div style='font-size: 3em; margin-bottom: 20px;'>⚠️</div>
                        <div style='font-size: 1.5em; margin-bottom: 10px;'>Stundenplan konnte nicht geladen werden</div>
                        <div style='font-size: 0.8em; color: #aaa;'>{escape(error)}</div>
                        <div style='font-size: 0.6em; color: #666; margin-top: 20px;'>Wird automatisch aktualisiert...</div>
                    </div>
                </div>"""

    today = date.today()
    monday = today - timedelta(days=today.weekday())
    week = [l for l in data["lessons"] if 0 <= (dashboard.int_date(l["date"]) - monday).days <= 4]
    week = dashboard.hide_covered_by_events(week)
    periods = build_periods(MASTER.periods, week)
    if not periods:
        return """
                <div class='error' style='color: #888;'>
                    <div style='text-align: center; font-size: 0.8em; font-weight: normal;'>Keine Stunden in dieser Woche</div>
                </div>"""
    grid = week_grid(week, periods, monday)
    HOURS = list(range(len(periods)))

    # Tage ganz ohne Stunden (Feiertag, Ferien) -> eine große Box
    no_school = {d: all(grid[(d, h)]["class"] == "empty" for h in HOURS) for d in range(5)}

    html = ""
    # Hinweis, wenn der letzte Abruf fehlgeschlagen ist und ein älterer Stand angezeigt wird
    if error and fetched_at:
        html += f"<div class='stale'>⚠ Untis nicht erreichbar – Stand {fetched_at.strftime('%H:%M')}</div>"
    # viele Zeilen (z. B. Nachmittagsunterricht bis abends) -> Zellen etwas kompakter
    html += f"<table class='{'compact' if len(periods) > 9 else ''}'><tr><th id='clock'></th>"

    for i, d in enumerate(DAYS):
        day_date = (monday + timedelta(days=i)).strftime("%d.%m")
        cls = "today" if i == today.weekday() else ""
        html += f'<th class="{cls}">{d}<span class="date">{day_date}</span></th>'
    html += "</tr>"

    for h in HOURS:
        html += f"<tr><th>{periods[h]['label']}<br>{periods[h]['time']}</th>"
        for d in range(5):
            if no_school[d]:
                # nur in der ersten Stunde die große Box (rowspan deckt den Rest ab)
                if h == HOURS[0]:
                    html += f'<td class="no-school" rowspan="{len(HOURS)}">Kein Unterricht</td>'
            else:
                c = grid[(d, h)]
                if c["rowspan"] > 0:
                    rowspan_attr = f' rowspan="{c["rowspan"]}"' if c["rowspan"] > 1 else ""
                    html += f'<td class="{c["class"]}" style="{c["style"]}"{rowspan_attr}>{c["text"]}</td>'
        html += "</tr>"

    html += "</table>"
    return html


@app.route("/content")
def content():
    return render_content(), 200, {"X-Version": VERSION, "Cache-Control": "no-store"}


@app.route("/")
def index():
    # Seite wird nur einmal geladen; danach holt JavaScript alle 5 s nur /content
    # und tauscht die Tabelle nur aus, wenn sich wirklich etwas geändert hat
    # (kein kompletter Reload mehr -> kein Flackern, weniger CPU).
    return f"""<html><head><meta charset='UTF-8'><title>Stundenplan</title>
<style>{PAGE_CSS}</style></head><body>
<div id='version-screen'>{VERSION}</div>
<div id='content'>{render_content()}</div>
<script>
const VERSION = {VERSION!r};
const content = document.getElementById('content');
let last = content.innerHTML;

function pad(n) {{ return String(n).padStart(2, '0'); }}
function updateClock() {{
    const el = document.getElementById('clock');
    if (el) {{
        const d = new Date();
        el.textContent = pad(d.getHours()) + ':' + pad(d.getMinutes());
    }}
}}

async function refresh() {{
    try {{
        const r = await fetch('/content', {{cache: 'no-store'}});
        if (!r.ok) return;
        // Neue Server-Version eingespielt -> einmal komplett neu laden (neues CSS/JS)
        if (r.headers.get('X-Version') !== VERSION) {{ location.reload(); return; }}
        const html = await r.text();
        if (html !== last) {{
            last = html;
            content.innerHTML = html;
            updateClock();
        }}
    }} catch (e) {{
        // Server kurz weg (z. B. Neustart) -> alten Stand stehen lassen
    }}
}}

updateClock();
setInterval(updateClock, 1000);
setInterval(refresh, 5000);

document.addEventListener('keydown', function(e) {{
    if (e.key === 'F8') {{
        e.preventDefault();
        const versionScreen = document.getElementById('version-screen');
        if (versionScreen.style.display === 'flex') {{
            versionScreen.style.display = 'none';
            content.style.display = 'block';
        }} else {{
            versionScreen.style.display = 'flex';
            content.style.display = 'none';
        }}
    }}
}});
</script>
</body></html>"""


if __name__ == "__main__":
    if config.demo(CFG):
        print("DEMO-Modus: erfundene Beispieldaten (kein Untis-Login)", flush=True)
    dashboard.start()
    thermal.start()
    app.run(host="0.0.0.0", port=_args.port or int(CFG["PORT"] or 5000), debug=False)
