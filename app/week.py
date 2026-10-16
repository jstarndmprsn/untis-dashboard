"""
Wochenansicht (/): zeichnet die Stundentabelle Mo–Fr.

Die Stunden kommen aufbereitet aus dashboard.py (UntisFetcher) – dieselbe Erkennung von
Ausfall, Vertretung, Raumänderung und Veranstaltung wie in der Tagesansicht, dieselben
(offiziellen Untis-)Fachfarben und Markierungen. Reine Funktionen, ohne Flask/Untis (testbar).
"""
from datetime import timedelta
from html import escape

from dashboard import hhmm, hide_covered_by_events, int_date

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
        d = (int_date(l["date"]) - monday).days
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


def render_table(lessons, master_periods, monday, today, stale_note=None):
    """HTML der Wochentabelle (Woche ab `monday`), None wenn sie keine Stunden hat.
    stale_note: Hinweis unten rechts (z. B. "Untis nicht erreichbar – Stand 10:15")."""
    week = [l for l in lessons if 0 <= (int_date(l["date"]) - monday).days <= 4]
    week = hide_covered_by_events(week)
    periods = build_periods(master_periods, week)
    if not periods:
        return None
    grid = week_grid(week, periods, monday)
    HOURS = list(range(len(periods)))

    # Tage ganz ohne Stunden (Feiertag, Ferien) -> eine große Box
    no_school = {d: all(grid[(d, h)]["class"] == "empty" for h in HOURS) for d in range(5)}

    html = ""
    # Hinweis, wenn der letzte Abruf fehlgeschlagen ist und ein älterer Stand angezeigt wird
    if stale_note:
        html += f"<div class='stale'>⚠ {escape(stale_note)}</div>"
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
