"""
Flask-Server für die Klassen-Anzeige (Port 5000, PORT in config.env).

  /         Wochen-Stundenplan mit Vertretungen (dieses Modul)
  /heute    Tagesansicht (dashboard.py)
  /thermal  System-/Temperatur-Dashboard (thermal.py)

Einstellungen: config.env im Projektordner (anlegen mit `python3 app/configure.py`).
Ausprobieren ohne Untis-Konto:  python3 app/server.py --demo   [--no-calendar]
"""
import argparse
import threading
import time
from flask import Flask
from datetime import date, timedelta, datetime
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

# Wie oft Untis abgefragt wird (Sekunden). Die Seite selbst wird aus dem Cache ausgeliefert.
FETCH_INTERVAL = 90

# Nach dem Booten ist das Netz oft noch nicht da -> so lange "wird geladen" statt Fehler zeigen
STARTUP_GRACE = 180
_STARTED = time.time()

DAYS = ["Mo", "Di", "Mi", "Do", "Fr"]

# Fachfarben wie in der Tagesansicht
subject_color = dashboard.subject_color


def hhmm(v):
    return f"{v // 100:02d}:{v % 100:02d}"


def build_periods(master_periods, timetable):
    """Zeilen der Tabelle = Stunden aus dem Untis-Zeitraster. Ohne Zeitraster
    (nicht freigegeben) werden sie aus den Anfangs-/Endzeiten der Woche abgeleitet."""
    if master_periods:
        periods = master_periods
    else:
        times = sorted({(l["startTime"], l["endTime"]) for l in timetable if l.get("startTime")})
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
.empty { background: #111; border: none; }
.error { display: flex; align-items: center; justify-content: center; height: 100vh; font-size: 2.5em; font-weight: bold; color: #ff3333; }

/* Vertretungs-Styles */
td.substitution { border-color: #4caf50 !important; }
td.cancelled { border-color: #ff3333 !important; }
td.event { border-color: #00bcd4 !important; }
.old-teacher { text-decoration: line-through; color: #888; }
.new-teacher, .new-room, .new-subject { color: #4caf50; border-bottom: 2px solid #4caf50; padding-bottom: 1px; }

/* Multi-Course Styles */
.multi-course { display: flex; gap: 3px; height: 100%; }
.course { flex: 1; min-width: 0; padding: 2px; border-left: 1px solid #555; }
.course:first-child { border-left: none; }
td.multi::before { background: var(--stripe-gradient, var(--stripe-color)) !important; }

/* Fullscreen Version Display */
#version-screen { display: none; position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: #111; color: #fff; align-items: center; justify-content: center; font-size: 5em; font-weight: bold; z-index: 9999; }
"""

def _find_texts_in_data(d):
    texts = []
    if isinstance(d, dict):
        for k, v in d.items():
            lk = str(k).lower()
            if isinstance(v, str):
                if any(sub in lk for sub in ("text", "subst", "info", "note", "lstext")) and v.strip():
                    texts.append(v.strip())
            else:
                texts.extend(_find_texts_in_data(v))
    elif isinstance(d, list):
        for it in d:
            texts.extend(_find_texts_in_data(it))
    return texts


class _Lesson:
    """Rohdaten einer Untis-Stunde (getTimetable) – `_data` wie bei python-webuntis."""

    def __init__(self, data):
        self._data = data
        d = str(data.get("date", ""))
        self.day = date(int(d[:4]), int(d[4:6]), int(d[6:])).weekday() if len(d) == 8 else None
        self.start, self.end = data.get("startTime"), data.get("endTime")


def get_timetable():
    try:
        today = date.today()
        start_of_week = today - timedelta(days=today.weekday())

        with untis.connect(CFG) as u:
            m = MASTER.ensure(u)
            try:
                rows = u.rpc("getTimetable", {
                    "id": m.class_id, "type": 1,
                    "startDate": start_of_week.strftime("%Y%m%d"),
                    "endDate": (start_of_week + timedelta(days=4)).strftime("%Y%m%d"),
                })
            except Exception:
                MASTER.invalidate()   # z. B. neues Schuljahr -> Klassen-ID neu suchen
                raise

        timetable = [_Lesson(r) for r in rows]
        periods = build_periods(m.periods, rows)
        HOURS = list(range(len(periods)))

        grid = {(d, h): {"lessons": [], "class": "empty", "style": ""} for d in range(5) for h in HOURS}

        # Namen aus Untis HTML-sicher machen (werden direkt ins HTML geschrieben)
        all_teachers = {k: escape(v) for k, v in m.teachers.items()}
        all_rooms = {k: escape(v) for k, v in m.rooms.items()}
        all_subjects = {k: escape(v) for k, v in m.subjects.items()}

        for lesson in timetable:
            if lesson.start is None or lesson.day is None:
                continue
            day = lesson.day
            if day > 4:
                continue

            # Zugriff auf RAW-Daten statt Properties
            subject = "?"
            teacher = ""
            room = ""
            event_text = None
            is_event = False
            additional_info = ""
            
            # Original-Werte für Vergleich
            original_subject = None
            original_teacher = None
            original_room = None
            
            # Status-Code prüfen
            status_code = None
            try:
                if 'code' in lesson._data:
                    status_code = lesson._data['code']
            except:
                pass
            
            # Fach aus Raw-Data
            try:
                if 'su' in lesson._data and lesson._data['su']:
                    subject = escape(lesson._data['su'][0].get('name', "?"))
                    subject_id = lesson._data['su'][0].get('id')
                    if subject_id and subject_id in all_subjects:
                        subject = all_subjects[subject_id]
                    # Original-Fach (falls vorhanden)
                    if 'substText' in lesson._data['su'][0]:
                        original_subject = lesson._data['su'][0]['substText']
            except:
                pass
            
            # Lehrer aus Raw-Data
            try:
                if 'te' in lesson._data and lesson._data['te']:
                    teacher_ids = [t['id'] for t in lesson._data['te']]
                    teachers = [all_teachers.get(tid, "") for tid in teacher_ids]
                    teacher = ", ".join([t for t in teachers if t])  # Alle Lehrer mit Komma getrennt
                    # Original-Lehrer prüfen
                    if lesson._data['te'][0] and 'orgid' in lesson._data['te'][0]:
                        orig_teacher_id = lesson._data['te'][0]['orgid']
                        original_teacher = all_teachers.get(orig_teacher_id, "")
            except:
                pass
            
            # Raum aus Raw-Data
            try:
                if 'ro' in lesson._data and lesson._data['ro']:
                    room_id = lesson._data['ro'][0]['id']
                    room = all_rooms.get(room_id, "")
                    # Original-Raum prüfen
                    if 'orgid' in lesson._data['ro'][0]:
                        orig_room_id = lesson._data['ro'][0]['orgid']
                        original_room = all_rooms.get(orig_room_id, "")
            except:
                pass

            # Zusätzliche Informationen
            try:
                additional_info = (lesson._data.get('info', '') or
                                   lesson._data.get('lstext', '') or
                                   lesson._data.get('topic', '') or
                                   lesson._data.get('text', '') or
                                   lesson._data.get('subjectText', '') or
                                   lesson._data.get('teacherText', '') or
                                   lesson._data.get('roomText', '')).strip()
                additional_info = escape(additional_info)
            except:
                pass

            # Falls nichts gefunden wurde, rekursiv nach möglichen Text-/Subst-Feldern im Raw-Dict suchen
            try:
                if not additional_info:
                    found = _find_texts_in_data(lesson._data)
                    # Filtere kurze/falsche Treffer
                    found = [f for f in found if len(f) > 1 and f != "?"]
                    if found:
                        # Priorisiere längere/nützlichere Texte
                        additional_info = escape(" | ".join(dict.fromkeys(found)))
            except:
                pass

            # Event/Veranstaltung prüfen: robuster erkennen
            try:
                # Standardfall: expliziter 'irregular' Status ohne Original-Lehrer
                if status_code == 'irregular' and (not original_teacher or original_teacher == teacher):
                    is_event = True
                # Manche APIs setzen Flags/Keys für Events
                elif lesson._data.get('isEvent') or lesson._data.get('event') or lesson._data.get('type') == 'event':
                    is_event = True
                # Wenn kein Lehrer vorhanden, aber ein Fach gesetzt ist und es kein explicit 'cancelled' ist,
                # ist es wahrscheinlich eine Veranstaltung und kein Ausfall.
                # Ausnahme: Untis liefert te=[{'id': 0, 'orgid': X}] -> Lehrer X entfällt ohne Vertretung = Ausfall.
                elif not teacher and not original_teacher and subject != "?" and status_code != 'cancelled':
                    is_event = True

                if is_event:
                    lstext = escape((lesson._data.get('lstext') or '').strip())
                    event_text = subject if subject != "?" else (lstext or "Veranstaltung")
            except:
                pass

            # Prüfe ob Vertretung oder Ausfall. Wichtig: Events ohne Lehrer gelten nicht als Ausfall.
            is_cancelled = (status_code == 'cancelled') or (not teacher and not is_event)
            is_substitution = False

            # HTML für Lehrer und Raum
            teacher_html = ""
            room_html = ""
            subject_html = subject

            # Lehrer-Anzeige
            if is_cancelled:
                # Echter Ausfall - Lehrer durchstreichen
                if teacher:
                    # Lehrer ist bekannt, aber Stunde fällt aus
                    teacher_html = f'<span class="old-teacher">{teacher}</span>'
                elif original_teacher:
                    # Lehrer wurde ausgetragen (te id 0 + orgid)
                    teacher_html = f'<span class="old-teacher">{original_teacher}</span>'
                else:
                    teacher_html = '<span class="old-teacher">---</span>'
                # WICHTIG: Bei Ausfall keinen neuen Lehrer
                teacher = ""
            elif original_teacher and original_teacher != teacher:
                # Vertretungslehrer
                is_substitution = True
                teacher_html = f'<span class="old-teacher">{original_teacher}</span> <span class="new-teacher">{teacher}</span>'
            else:
                teacher_html = teacher

            # Raum-Anzeige
            if original_room and original_room != room:
                # Raumänderung mit Original
                is_substitution = True
                room_html = f'<span class="new-room">{room}</span>'
            else:
                room_html = room
            
            # Fach-Anzeige (falls geändert oder ausgefallen)
            # WICHTIG: Prüfung NACH der Lehrer-Verarbeitung, da dort teacher="" gesetzt wird
            if not teacher:
                # Wenn es ein Event ist, zeige das Event-Label statt Ausfall-Markierung
                if is_event:
                    subject_html = event_text if event_text else ("Veranstaltung" if subject == "?" else subject)
                else:
                    # Kein Lehrer = Ausfall -> Fach durchstreichen
                    subject_html = f'<span class="old-teacher">{subject}</span>'
            elif original_subject and original_subject != subject:
                # Fach wurde geändert
                is_substitution = True
                subject_html = f'<span class="old-teacher">{original_subject}</span> <span class="new-subject">{subject}</span>'
            else:
                subject_html = "Veranstaltung" if subject == "?" else subject
            # Wenn es einen speziellen Vertretungstext gibt und die Original-Lehrkraft weiterhin eingetragen ist,
            # dann als Vertretung markieren und den Text anzeigen.
            note_html = ""
            try:
                show_note = False
                if additional_info and original_teacher and original_teacher == teacher:
                    show_note = True
                if show_note:
                    is_substitution = True
                    note_html = f' <span class="info">{additional_info}</span>'
            except:
                note_html = ""

            # Lektion-Objekt erstellen
            lesson_data = {
                "subject": subject,
                "subject_html": subject_html,
                "teacher": teacher,  # Roher Lehrername
                "teacher_html": teacher_html,
                "room": room,  # Roher Raumname
                "room_html": room_html,
                "note_html": note_html,
                "note": additional_info,
                "color": subject_color(subject),
                "is_cancelled": is_cancelled,
                "is_substitution": is_substitution,
                "is_event": is_event,
                "event_text": event_text
            }
            
            # In alle Stunden des Zeitrasters eintragen, die diese Einheit belegt
            # (Veranstaltungen über mehrere Stunden, Blockstunden)
            for h_val in covered_periods(periods, lesson.start, lesson.end or lesson.start + 1):
                grid[(day, h_val)]["lessons"].append(lesson_data)

        # Grid in HTML-Format konvertieren
        final_grid = {}
        for (day, hour), cell in grid.items():
            lessons = cell["lessons"]
            
            if not lessons:
                # Leere Zelle
                final_grid[(day, hour)] = {"text": "", "class": "empty", "style": "", "is_event": False, "rowspan": 1}
                continue
            
            # Duplikate entfernen basierend auf Fach + Lehrer + Raum
            unique_lessons = []
            seen = set()
            for lesson in lessons:
                key = (lesson["subject"], lesson["teacher_html"], lesson["room_html"])
                if key not in seen:
                    seen.add(key)
                    unique_lessons.append(lesson)
            
            lessons = unique_lessons
            
            # Wenn nur ein Kurs übrig ist
            if len(lessons) == 1:
                lesson = lessons[0]
                
                # Event speziell darstellen
                if lesson.get("is_event"):
                    text = f'<span class="subject">{lesson["subject_html"]}</span><br><span class="info">{lesson["teacher_html"]} {lesson["room_html"]}{lesson.get("note_html", "")}</span>'
                    cls = "event"
                    if lesson["is_cancelled"]:
                        cls += " cancelled"
                    final_grid[(day, hour)] = {
                        "text": text,
                        "class": cls,
                        "style": f"--stripe-color:{lesson['color']};",
                        "is_event": True,
                        "rowspan": 1
                    }
                    continue
                
                text = f'<span class="subject">{lesson["subject_html"]}</span><br><span class="info">{lesson["teacher_html"]} {lesson["room_html"]}{lesson.get("note_html", "")}</span>'
                cls = "normal"
                
                # WICHTIG: Entscheidend ist ob ein Lehrer DA ist, nicht der Status!
                # ROT = Kein Lehrer (egal ob cancelled oder substitution)
                # GRÜN = Lehrer vorhanden UND (cancelled ODER substitution)
                if not lesson["teacher"]:
                    # Kein Lehrer = Ausfall = ROT
                    cls += " cancelled"
                elif lesson["is_cancelled"] or lesson["is_substitution"]:
                    # Lehrer vorhanden + Änderung = Vertretung/Verlegung = GRÜN
                    cls += " substitution"
                    
                final_grid[(day, hour)] = {
                    "text": text,
                    "class": cls,
                    "style": f"--stripe-color:{lesson['color']};",
                    "is_event": False,
                    "rowspan": 1
                }
            else:
                # Prüfe zuerst ob ein Event dabei ist
                event_lesson = None
                other_lessons = []
                for l in lessons:
                    if l.get("is_event"):
                        event_lesson = l
                    else:
                        other_lessons.append(l)
                
                # Wenn Event dabei ist, zeige nur das Event
                if event_lesson:
                    text = f'<span class="subject">{event_lesson["subject_html"]}</span><br><span class="info">{event_lesson["teacher_html"]} {event_lesson["room_html"]}{event_lesson.get("note_html", "")}</span>'
                    cls = "event"
                    if event_lesson["is_cancelled"]:
                        cls += " cancelled"
                    final_grid[(day, hour)] = {
                        "text": text,
                        "class": cls,
                        "style": f"--stripe-color:{event_lesson['color']};",
                        "is_event": True,
                        "rowspan": 1
                    }
                    continue
                
                # Mehrere verschiedene Kurse - aber nur anzeigen wenn WIRKLICH unterschiedliche Fächer
                # Filter: Nur zählen wenn verschiedene Fächer
                subjects_set = set(l["subject"] for l in lessons)
                
                if len(subjects_set) == 1:
                    # Alle gleichen Fächer -> wahrscheinlich Duplikat/Verlegung, zeige nur ersten
                    lesson = lessons[0]
                    text = f'<span class="subject">{lesson["subject_html"]}</span><br><span class="info">{lesson["teacher_html"]} {lesson["room_html"]}{lesson.get("note_html", "")}</span>'
                    cls = "normal"
                    
                    # Gleiche Logik: Kein Lehrer = ROT, sonst GRÜN bei Änderungen
                    if not lesson["teacher"]:
                        cls += " cancelled"
                    elif lesson["is_cancelled"] or lesson["is_substitution"]:
                        cls += " substitution"
                        
                    final_grid[(day, hour)] = {
                        "text": text,
                        "class": cls,
                        "style": f"--stripe-color:{lesson['color']};",
                        "is_event": False,
                        "rowspan": 1
                    }
                else:
                    # Wirklich mehrere verschiedene Fächer
                    # Priorisierung: Ausfälle zuerst, dann Vertretungen
                    lessons_sorted = sorted(lessons, key=lambda x: (not x["is_cancelled"], not x["is_substitution"]))
                    
                    # Prüfe: Gibt es einen echten Ausfall (ohne Lehrer)?
                    # Ausfall = is_cancelled UND kein aktiver Lehrer
                    has_real_cancellation = any(l["is_cancelled"] and not l["teacher"] for l in lessons)
                    all_cancelled = all(l["is_cancelled"] and not l["teacher"] for l in lessons)
                    
                    # Kurse nebeneinander anzeigen mit flex-Gewichtung
                    parts = []
                    colors = []
                    
                    for i, lesson in enumerate(lessons_sorted):
                        # Erste Lektion (mit Problem) bekommt mehr Platz
                        flex = "2" if i == 0 and (lesson["is_cancelled"] or lesson["is_substitution"]) else "1"
                        part = f'<div class="course" style="flex:{flex}"><span class="subject">{lesson["subject_html"]}</span><br><span class="info">{lesson["teacher_html"]} {lesson["room_html"]}{lesson.get("note_html", "")}</span></div>'
                        parts.append(part)
                        colors.append(lesson["color"])
                    
                    text = '<div class="multi-course">' + ''.join(parts) + '</div>'
                    cls = "normal multi"
                    
                    # WICHTIG: 
                    # ROT = ALLE Kurse fallen aus
                    # GRÜN = Mindestens ein Kurs findet statt (Verlegung/Vertretung/teilweiser Ausfall)
                    if all_cancelled:
                        cls += " cancelled"
                    elif has_real_cancellation or any(l["is_substitution"] for l in lessons):
                        cls += " substitution"
                    
                    # Gradient für mehrere Farben
                    if len(colors) > 1:
                        gradient = f"linear-gradient(to right, {', '.join(colors)})"
                        style = f"--stripe-color:{colors[0]}; --stripe-gradient:{gradient};"
                    else:
                        style = f"--stripe-color:{colors[0]};"
                    
                    final_grid[(day, hour)] = {"text": text, "class": cls, "style": style, "is_event": False, "rowspan": 1}
        
        # Berechne rowspan für Events
        for d in range(5):
            for i, h in enumerate(HOURS):
                if final_grid[(d, h)]["rowspan"] == 0:
                    continue
                if final_grid[(d, h)]["is_event"]:
                    rowspan = 1
                    for j in range(i + 1, len(HOURS)):
                        h_next = HOURS[j]
                        if (final_grid[(d, h_next)]["text"] == final_grid[(d, h)]["text"] and
                            final_grid[(d, h_next)]["is_event"]):
                            rowspan += 1
                            final_grid[(d, h_next)]["rowspan"] = 0
                        else:
                            break
                    final_grid[(d, h)]["rowspan"] = rowspan
        
        return final_grid, start_of_week, periods

    except Exception as e:
        error_msg = str(e)
        error_type = type(e).__name__
        
        # Spezifische Fehlermeldungen
        if any(w in error_msg.lower() for w in ("login", "authentication", "bad credentials")):
            return f"Login fehlgeschlagen. Bitte Benutzername und Passwort prüfen.", None
        elif "connection" in error_msg.lower() or "network" in error_msg.lower():
            return f"Netzwerkfehler: WebUntis-Server nicht erreichbar.", None
        elif "timeout" in error_msg.lower():
            return f"Zeitüberschreitung: WebUntis-Server antwortet nicht.", None
        else:
            return f"Fehler beim Laden: {error_type} - {error_msg}", None

# ------------------ CACHE ------------------
# Untis wird nur alle FETCH_INTERVAL Sekunden im Hintergrund abgefragt.
# Schlägt ein Abruf fehl, bleibt der letzte erfolgreiche Plan stehen.
_cache = {"grid": None, "start_of_week": None, "periods": None, "fetched_at": None, "error": None}
_cache_lock = threading.Lock()


def refresh_cache():
    result = get_timetable()
    with _cache_lock:
        if isinstance(result[0], str):
            _cache["error"] = result[0]
            print(f"Untis-Abruf fehlgeschlagen: {result[0]}", flush=True)
        else:
            _cache["grid"], _cache["start_of_week"], _cache["periods"] = result
            _cache["fetched_at"] = datetime.now()
            _cache["error"] = None


def fetch_loop():
    failures = 0
    while True:
        try:
            refresh_cache()
        except Exception as e:
            print(f"Fehler im Abruf-Thread: {e}", flush=True)
        with _cache_lock:
            failed = _cache["error"] is not None
        # nach Fehler schnell erneut versuchen (WLAN beim Booten noch nicht da)
        failures = failures + 1 if failed else 0
        time.sleep(min(FETCH_INTERVAL, [3, 5, 10, 20, 40, 60][min(failures, 6) - 1]) if failed else FETCH_INTERVAL)


# ==================== VERSION STRING - HIER ÄNDERN ====================
VERSION = "V.1.3"
# ======================================================================

PAGE_CSS = CSS + """
td.normal::before { background: var(--stripe-color); }
.no-school { background: #222; color: #666; font-size: 1.5em; text-align: center; padding: 50px; border-radius: 15px; }
.stale { position: fixed; bottom: 6px; right: 12px; background: #5a1a1a; color: #ffb3b3; padding: 4px 10px; border-radius: 6px; font-size: 0.9em; z-index: 100; }
"""


def render_content():
    """Nur der wechselnde Teil der Seite (Tabelle bzw. Fehlermeldung).
    Enthält bewusst keine Uhrzeit -> ändert sich nur, wenn sich Daten ändern."""
    with _cache_lock:
        grid = _cache["grid"]
        start_of_week = _cache["start_of_week"]
        periods = _cache["periods"]
        fetched_at = _cache["fetched_at"]
        cache_error = _cache["error"]

    # Noch nie erfolgreich geladen -> Lade- bzw. Fehleranzeige
    if grid is None:
        if not cache_error or time.time() - _STARTED < STARTUP_GRACE:
            return """
                <div class='error' style='color: #888;'>
                    <div style='text-align: center; font-size: 0.8em; font-weight: normal;'>Stundenplan wird geladen …</div>
                </div>"""
        error_msg = cache_error
        return f"""
                <div class='error'>
                    <div style='text-align: center;'>
                        <div style='font-size: 3em; margin-bottom: 20px;'>⚠️</div>
                        <div style='font-size: 1.5em; margin-bottom: 10px;'>Stundenplan konnte nicht geladen werden</div>
                        <div style='font-size: 0.8em; color: #aaa;'>{error_msg}</div>
                        <div style='font-size: 0.6em; color: #666; margin-top: 20px;'>Wird automatisch aktualisiert...</div>
                    </div>
                </div>"""

    if not periods:
        return """
                <div class='error' style='color: #888;'>
                    <div style='text-align: center; font-size: 0.8em; font-weight: normal;'>Keine Stunden in dieser Woche</div>
                </div>"""

    today_idx = date.today().weekday()
    HOURS = list(range(len(periods)))

    # Prüfe für jeden Tag, ob alle Stunden ausfallen
    day_cancelled = {}
    for d in range(5):
        all_cancelled = True
        for h in HOURS:
            cell = grid[(d, h)]
            # Wenn es Text gibt und nicht leer ist, findet Unterricht statt
            if cell["text"] and cell["class"] != "empty":
                all_cancelled = False
                break
        day_cancelled[d] = all_cancelled

    html = ""
    # Hinweis, wenn der letzte Abruf fehlgeschlagen ist und ein älterer Stand angezeigt wird
    if cache_error and fetched_at:
        html += f"<div class='stale'>⚠ Untis nicht erreichbar – Stand {fetched_at.strftime('%H:%M')}</div>"
    html += "<table><tr><th id='clock'></th>"

    for i, d in enumerate(DAYS):
        day_date = (start_of_week + timedelta(days=i)).strftime("%d.%m")
        cls = "today" if i == today_idx else ""
        html += f'<th class="{cls}">{d}<span class="date">{day_date}</span></th>'
    html += "</tr>"

    for h in HOURS:
        html += f"<tr><th>{periods[h]['label']}<br>{periods[h]['time']}</th>"
        for d in range(5):
            if day_cancelled[d]:
                # Ganzer Tag fällt aus - zeige nur in der ersten Stunde die große Box
                if h == HOURS[0]:
                    html += f'<td class="no-school" rowspan="{len(HOURS)}">Kein Unterricht</td>'
                # Für andere Stunden nichts hinzufügen (rowspan deckt alles ab)
            else:
                # Normaler Unterricht
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
    threading.Thread(target=fetch_loop, daemon=True).start()
    dashboard.start()
    thermal.start()
    app.run(host="0.0.0.0", port=_args.port or int(CFG["PORT"] or 5000), debug=False)
