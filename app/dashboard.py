"""
Tagesansicht "Heute" (/heute).

Datenquellen (jede wird unabhängig im Hintergrund abgefragt und gecacht;
fällt eine aus, bleibt ihr letzter Stand stehen):
  - Untis:    Stunden des Tages inkl. Vertretungen/Ausfälle, Arbeitsaufträge
              (Lehrer-Infotexte), Hausaufgaben, Ordnungsdienst
  - Kalender: optional – beliebige iCal-Adresse (CALENDAR_ICS_URL in config.env,
              z. B. die geheime Adresse eines Google Kalenders). Ohne Kalender
              zeigt die Seite nur zwei Spalten.
  - Wetter:   optional – Open-Meteo (WEATHER_LAT/WEATHER_LON), kein API-Key nötig

Die Seite selbst (templates/heute.html + static/heute.*) holt alle 30 s
/api/heute und rendert im Browser.
"""
import re
import threading
import time
from datetime import date, datetime, timedelta

import icalendar
import recurring_ical_events
import requests
from flask import jsonify, render_template, request

import demo
import untis

# ------------------ KONFIGURATION ------------------
UNTIS_INTERVAL = 120        # Sekunden
CALENDAR_INTERVAL = 600
WEATHER_INTERVAL = 900

CALENDAR_DAYS_AHEAD = 60
CALENDAR_MAX_EVENTS = 8
HOMEWORK_DAYS_AHEAD = 14

# Nach dem Booten ist das Netz oft noch nicht da -> so lange "wird geladen" statt Fehler zeigen
STARTUP_GRACE = 180
_STARTED = time.time()

# Anzeige-Kürzel (Grundfach aus Untis-Namen wie "Mat (8-10)", "Bio B1", "BKu2")
SUBJECT_ALIASES = {"BKu": "Kun"}

SUBJECT_COLORS = {
    "Mat": "#ff3b30", "Deu": "#f0e000", "Eng": "#dfa916", "Fra": "#ff9f0a", "Spa": "#ff6482",
    "Lat": "#3a2cff", "Bio": "#34c759", "Che": "#2e9e16", "Phy": "#c4bf7c", "Inf": "#a259ff",
    "Ges": "#f5f5f5", "Geo": "#9c6b3c", "Pol": "#7be5ec", "Rel": "#9a9aa0", "Phil": "#9a9aa0",
    "Kun": "#bc38f0", "Mus": "#af5bf3", "Spo": "#2d96db",
}

# Google liefert im iCal-Export keine Terminfarben -> Farbe per Stichwort
CALENDAR_COLOR_RULES = [
    (r"geburtstag", "#ff6b6b"),
    (r"arbeit|test|klausur|vokabel|lek(tion)?\b|präsentation|referat", "#6ee07a"),
    (r"ferien|frei\b|feiertag|kein unterricht|unterrichtsfrei", "#5ac8fa"),
    (r"reise|ausflug|fahrt|konzert|aufführung|fest", "#ffd35a"),
]
CALENDAR_DEFAULT_COLOR = "#d8d8e0"

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
WEEKDAYS_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]


# ------------------ HILFSFUNKTIONEN ------------------
def base_subject(name):
    m = re.match(r"[A-Za-zÄÖÜäöü]+", name or "")
    base = m.group(0) if m else (name or "?")
    return SUBJECT_ALIASES.get(base, base)


def subject_color(name):
    base = base_subject(name)
    if base in SUBJECT_COLORS:
        return SUBJECT_COLORS[base]
    # unbekanntes Fach (andere Schule, andere Kürzel): feste Farbe aus dem Namen
    h = sum(ord(c) * 31 ** i for i, c in enumerate(base)) % 360
    return f"hsl({h}, 65%, 62%)"


def hhmm(v):
    return f"{v // 100:02d}:{v % 100:02d}"


def minutes(v):
    return (v // 100) * 60 + v % 100


def int_date(v):
    s = str(v)
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def day_label(d, today):
    if d == today:
        return "Heute"
    if d == today + timedelta(days=1):
        return "Morgen"
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d.day}.{d.month}."


def clean_text(t):
    t = (t or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    t = re.sub(r"[  ]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


# ------------------ HINTERGRUND-QUELLEN ------------------
RETRY_DELAYS = [3, 5, 10, 20, 40, 60]

class Source:
    """Eine Datenquelle mit eigenem Intervall, Cache und Fehlerstatus."""

    def __init__(self, name, interval, fn):
        self.name, self.interval, self.fn = name, interval, fn
        self.data, self.error, self.fetched_at = None, None, None
        self.next_run = 0
        self.failures = 0
        self.lock = threading.Lock()

    def run(self):
        try:
            data = self.fn()
            with self.lock:
                self.data, self.error, self.fetched_at = data, None, datetime.now()
            self.failures = 0
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            print(f"[heute] {self.name} fehlgeschlagen: {msg}", flush=True)
            with self.lock:
                self.error = msg
            self.failures += 1
        # nach Fehler schnell erneut versuchen (beim Booten ist das WLAN erst nach
        # einigen Sekunden da): 3, 5, 10, 20, 40, 60, 60 … s
        if self.error is None:
            delay = self.interval
        else:
            delay = min(60, self.interval, RETRY_DELAYS[min(self.failures, len(RETRY_DELAYS)) - 1])
        self.next_run = time.time() + delay

    def loop(self):
        while True:
            wait = self.next_run - time.time()
            if wait > 0:
                time.sleep(min(wait, 5))
                continue
            self.run()

    def snapshot(self):
        with self.lock:
            return self.data, self.error, self.fetched_at


# ------------------ UNTIS ------------------
class UntisFetcher:
    def __init__(self, cfg, master):
        self.cfg, self.master = cfg, master
        self.duty_label = (cfg.get("DUTY_LABEL") or "").strip().lower()
        self.warned = set()

    def _optional(self, name, fn):
        """Hausaufgaben/Ordnungsdienst sind nicht an jeder Schule für Schüler freigegeben –
        dann einfach leer lassen, statt die ganze Untis-Quelle als Fehler zu melden."""
        try:
            return fn()
        except Exception as e:
            if name not in self.warned:
                print(f"[heute] {name} nicht verfügbar ({e}) – wird ausgeblendet", flush=True)
                self.warned.add(name)
            return {}

    def __call__(self):
        today = date.today()
        with untis.connect(self.cfg) as u:
            m = self.master.ensure(u)
            try:
                rows = u.rpc("getTimetable", {"options": {
                    "element": {"id": m.class_id, "type": 1},
                    "startDate": today.strftime("%Y%m%d"),
                    "endDate": (today + timedelta(days=10)).strftime("%Y%m%d"),
                    "showInfo": True, "showSubstText": True, "showLsText": True, "showStudentgroup": True,
                }})
            except Exception:
                self.master.invalidate()   # z. B. neues Schuljahr -> Klassen-ID neu suchen
                raise
            hw = self._optional("Hausaufgaben", lambda: u.rest(
                "/api/homeworks/lessons",
                startDate=(today - timedelta(days=14)).strftime("%Y%m%d"),
                endDate=(today + timedelta(days=HOMEWORK_DAYS_AHEAD)).strftime("%Y%m%d")))
            services = self._optional("Ordnungsdienst", lambda: u.rest(
                "/api/classreg/classservices",
                startDate=(today - timedelta(days=7)).strftime("%Y%m%d"),
                endDate=(today + timedelta(days=14)).strftime("%Y%m%d"),
                classId=m.class_id)) if self.duty_label else {}

        return {
            "lessons": [self._lesson(l) for l in rows],
            "homework": self._homework(hw, today),
            "duties": self._duties(services),
        }

    # -- Stunden --
    def _people(self, entries, kind):
        """[{id, orgid}] -> Liste {t: Name, k: ''|'old'|'new'}"""
        names = {"te": self.master.teachers, "ro": self.master.rooms, "su": self.master.subjects}[kind]
        out = []
        for e in entries or []:
            cur = names.get(e.get("id"), "") if e.get("id") else ""
            org = names.get(e.get("orgid"), "") if e.get("orgid") else ""
            if org and org != cur:
                out.append({"t": org, "k": "old"})
                if cur:
                    out.append({"t": cur, "k": "new"})
            elif cur:
                out.append({"t": cur, "k": ""})
        return out

    def _lesson(self, l):
        su = self._people(l.get("su"), "su")
        te = self._people(l.get("te"), "te")
        ro = self._people(l.get("ro"), "ro")
        subject_raw = next((x["t"] for x in su if x["k"] != "old"), su[0]["t"] if su else "?")
        code = l.get("code")
        te_list = l.get("te") or []
        teacher_removed = bool(te_list) and all(not e.get("id") for e in te_list)

        if code == "cancelled" or teacher_removed:
            status = "cancelled"
        elif not te_list and not l.get("su"):
            status = "event"
        elif any(x["k"] for x in su + te + ro) or code == "irregular":
            status = "changed"
        else:
            status = "normal"
        if status == "event" and subject_raw == "?":
            # Veranstaltung ohne Fach (Ausflug, Theater …): Untis-Text als Titel
            subject_raw = clean_text(l.get("lstext")) or clean_text(l.get("substText")) or "Veranstaltung"

        return {
            "date": l["date"], "start": l["startTime"], "end": l["endTime"],
            "subject": base_subject(subject_raw) if status != "event" else subject_raw,
            "subject_old": next((base_subject(x["t"]) for x in su if x["k"] == "old"), None),
            "color": subject_color(subject_raw),
            "teachers": te, "rooms": ro, "status": status,
            "group": l.get("sg") or subject_raw,
            "note": clean_text(l.get("substText")),
            "info": clean_text(l.get("info")),
        }

    # -- Hausaufgaben --
    def _homework(self, hw, today):
        subj = {l["id"]: l["subject"] for l in hw.get("lessons", [])}
        out = []
        for h in hw.get("homeworks", []):
            due = int_date(h["dueDate"])
            if h.get("completed") or due < today:
                continue
            text = clean_text(h.get("text"))
            remark = clean_text(h.get("remark"))
            if remark and remark.lower() != text.lower():
                text = f"{text}\n{remark}" if text else remark
            name = subj.get(h.get("lessonId"), "?")
            out.append({"subject": base_subject(name), "color": subject_color(name),
                        "due": h["dueDate"], "text": text})
        out.sort(key=lambda x: x["due"])
        return out

    # -- Klassenordner --
    def _duties(self, services):
        out = []
        for r in services.get("classRoles", []):
            if ((r.get("duty") or {}).get("label") or "").strip().lower() != self.duty_label:
                continue
            first = (r.get("foreName") or "").split(" ")[0]
            out.append({"from": r["startDate"], "to": r["endDate"], "name": first})
        return out


# ------------------ KALENDER ------------------
def calendar_color(title):
    t = title.lower()
    for pattern, color in CALENDAR_COLOR_RULES:
        if re.search(pattern, t):
            return color
    return CALENDAR_DEFAULT_COLOR


class CalendarFetcher:
    def __init__(self, url):
        self.url = url

    def __call__(self):
        if self.url == "demo":
            events = [{**e, "color": calendar_color(e["title"])} for e in demo.calendar_events()]
            return events
        # webcal:// ist dasselbe wie https:// (so liefern iCloud & Co. ihre Links)
        url = "https://" + self.url[len("webcal://"):] if self.url.startswith("webcal://") else self.url
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        cal = icalendar.Calendar.from_ical(r.content)
        today = date.today()
        events = []
        for ev in recurring_ical_events.of(cal).between(today, today + timedelta(days=CALENDAR_DAYS_AHEAD)):
            title = str(ev.get("SUMMARY", "")).strip() or "(ohne Titel)"
            start = ev.decoded("DTSTART")
            end = ev.decoded("DTEND") if ev.get("DTEND") else None
            all_day = not isinstance(start, datetime)
            if all_day:
                first = start
                last = (end - timedelta(days=1)) if end else start
                time_str = None
            else:
                start = start.astimezone() if start.tzinfo else start
                first = start.date()
                last = (end.astimezone() if end and end.tzinfo else end or start).date()
                time_str = start.strftime("%H:%M")
            events.append({
                "title": title,
                "start": first.isoformat(), "end": last.isoformat(),
                "time": time_str, "color": calendar_color(title),
            })
        events.sort(key=lambda e: (e["start"], e["time"] or ""))
        return events


# ------------------ WETTER ------------------
class WeatherFetcher:
    def __init__(self, lat, lon):
        self.lat, self.lon = lat, lon

    def __call__(self):
        if self.lat == "demo":
            return demo.weather()
        return fetch_weather(self.lat, self.lon)


def fetch_weather(lat, lon):
    r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto", "forecast_days": 1,
    })
    r.raise_for_status()
    j = r.json()
    cur, daily = j["current"], j["daily"]
    return {
        "temp": round(cur["temperature_2m"]),
        "code": cur["weather_code"],
        "is_day": bool(cur["is_day"]),
        "max": round(daily["temperature_2m_max"][0]),
        "min": round(daily["temperature_2m_min"][0]),
        "rain": daily["precipitation_probability_max"][0],
    }


# ------------------ AUFBEREITUNG FÜR DIE SEITE ------------------
def build_day(lessons, now):
    """Wählt den anzuzeigenden Tag (heute bis Schulschluss, danach nächster Schultag)
    und fasst Doppelstunden + parallele Kurse zusammen."""
    today = now.date()
    now_min = now.hour * 60 + now.minute
    by_day = {}
    for l in lessons:
        by_day.setdefault(int_date(l["date"]), []).append(l)

    target = None
    for d in sorted(by_day):
        if d < today:
            continue
        if d == today and max(minutes(l["end"]) for l in by_day[d]) <= now_min:
            continue  # heute schon vorbei
        target = d
        break
    if target is None:
        return None

    # Doppelstunden zusammenfassen (gleiche Gruppe/Status/Personen, Lücke <= 20 min)
    def key(l):
        return (l["group"], l["status"], repr(l["teachers"]), repr(l["rooms"]), l["subject"])

    merged = []
    for l in sorted(by_day[target], key=lambda x: (x["start"], x["group"])):
        prev = next((m for m in reversed(merged) if key(m) == key(l)), None)
        if prev and 0 <= minutes(l["start"]) - minutes(prev["end"]) <= 20:
            prev["end"] = max(prev["end"], l["end"])
            if l["info"] and l["info"] not in prev["info"]:
                prev["info"] = (prev["info"] + "\n" + l["info"]).strip()
            continue
        merged.append(dict(l))

    # Parallele Kurse (gleiche Startzeit) in einen Slot
    slots = {}
    for l in merged:
        slots.setdefault(l["start"], []).append(l)
    out = []
    for start in sorted(slots):
        courses = slots[start]
        out.append({
            "start": hhmm(start), "end": hhmm(max(c["end"] for c in courses)),
            "start_min": minutes(start), "end_min": minutes(max(c["end"] for c in courses)),
            "courses": [{k: c[k] for k in ("subject", "subject_old", "color", "teachers", "rooms", "status")}
                        for c in courses],
        })

    # Arbeitsaufträge dieses Tages (Lehrer-Infotexte), doppelte zusammenfassen
    tasks, seen = [], set()
    for l in merged:
        if not l["info"]:
            continue
        k = (l["subject"], l["info"])
        if k in seen:
            continue
        seen.add(k)
        tasks.append({"subject": l["subject"], "color": l["color"], "label": l["note"], "text": l["info"]})

    return {"date": target.isoformat(), "label": day_label(target, today), "slots": out, "tasks": tasks}


def source_status(src):
    data, error, fetched_at = src.snapshot()
    if data is None and time.time() - _STARTED < STARTUP_GRACE:
        error = None  # Startphase: noch als "lädt" behandeln
    return {
        "ok": error is None and data is not None,
        "error": error,
        "loaded": data is not None,
        "since": fetched_at.strftime("%H:%M") if fetched_at else None,
    }


# ------------------ INTEGRATION IN FLASK ------------------
_sources = {}


def init(app, cfg, master):
    _sources["untis"] = Source("Untis", UNTIS_INTERVAL, UntisFetcher(cfg, master))
    # Kalender und Wetter sind optional: ohne Eintrag in config.env gibt es die Quelle nicht
    if cfg["CALENDAR_ICS_URL"].strip():
        _sources["calendar"] = Source("Kalender", CALENDAR_INTERVAL, CalendarFetcher(cfg["CALENDAR_ICS_URL"].strip()))
    if cfg["WEATHER_LAT"].strip() and cfg["WEATHER_LON"].strip():
        _sources["weather"] = Source("Wetter", WEATHER_INTERVAL,
                                     WeatherFetcher(cfg["WEATHER_LAT"].strip(), cfg["WEATHER_LON"].strip()))

    @app.route("/heute")
    def heute():
        return render_template("heute.html", calendar="calendar" in _sources)

    @app.route("/api/heute")
    def api_heute():
        now = datetime.now()
        # Vorschau: /heute?tag=2026-09-28 bzw. ?tag=2026-09-28T09:50 simuliert einen Zeitpunkt
        tag = request.args.get("tag")
        if tag:
            try:
                now = datetime.fromisoformat(tag if "T" in tag else tag + "T07:30")
            except ValueError:
                pass
        today = now.date()
        untis_data, _, _ = _sources["untis"].snapshot()
        cal = _sources["calendar"].snapshot()[0] if "calendar" in _sources else None
        weather = _sources["weather"].snapshot()[0] if "weather" in _sources else None

        day = build_day(untis_data["lessons"], now) if untis_data else None
        ref = date.fromisoformat(day["date"]) if day else today
        ref_int = int(ref.strftime("%Y%m%d"))

        homework = []
        if untis_data:
            for h in untis_data["homework"]:
                d = int_date(h["due"])
                if d < ref:
                    continue  # schon abgegeben (Anzeige bezieht sich auf den gezeigten Schultag)
                homework.append({**h, "due_label": day_label(d, today)})
                if len(homework) >= 12:
                    break

        duty = None
        if untis_data:
            names = [x["name"] for x in untis_data["duties"] if x["from"] <= ref_int <= x["to"]]
            duty = " & ".join(names) if names else None

        events = []
        if cal:
            for e in cal:
                s, en = date.fromisoformat(e["start"]), date.fromisoformat(e["end"])
                if en < today:
                    continue
                shown = max(s, today)
                events.append({**e, "group": day_label(shown, today),
                               "until": f"bis {en.day}.{en.month}." if en > shown else None})
                if len(events) >= CALENDAR_MAX_EVENTS:
                    break

        return jsonify({
            "day": day, "homework": homework, "duty": duty,
            "events": events, "weather": weather,
            "date_long": f"{today.day}. {MONTHS[today.month - 1]}",
            "now": now.isoformat(timespec="seconds"), "preview": bool(tag),
            "features": {"calendar": "calendar" in _sources, "weather": "weather" in _sources},
            "status": {k: source_status(v) for k, v in _sources.items()},
        })


def start():
    # jede Quelle in eigenem Thread – ein langsamer Untis-Login hält Kalender/Wetter nicht auf
    for src in _sources.values():
        threading.Thread(target=src.loop, daemon=True).start()
