"""
Demo-Modus (DEMO=1 bzw. `python server.py --demo`): erfundene Klasse "9a" mit
Vertretung, Ausfall, Raumänderung, Veranstaltung, parallelen Kursen,
Hausaufgaben, Terminen und Wetter. Zum Ausprobieren ohne Pi und ohne Untis-Konto.

FakeUntisClient beantwortet dieselben Aufrufe wie untis.UntisClient.
"""
from datetime import date, datetime, timedelta

TEACHERS = {1: "Mül", 2: "Sch", 3: "Wag", 4: "Bec", 5: "Hof", 6: "Kle", 7: "Wol", 8: "Neu", 9: "Fis", 10: "Kra"}
ROOMS = {1: "R204", 2: "Phy1", 3: "Bio1", 4: "Sp1", 5: "Inf1", 6: "Ku1", 7: "Mu1", 8: "Ch1", 9: "R112", 10: "Aula"}
SUBJECTS = {1: "Mat", 2: "Deu", 3: "Eng", 4: "Bio", 5: "Phy", 6: "Ges", 7: "Spo", 8: "Inf",
            9: "BKu", 10: "Mus", 11: "Che", 12: "Geo", 13: "Lat"}
# Farben, wie sie eine Schule in Untis einträgt (nicht jedes Fach hat eine -> Ersatzfarbe)
COLORS = {1: "c00000", 2: "ffff00", 3: "ff8000", 4: "008027", 5: "ffc0a0", 6: "ffffff", 7: "57c0ff",
          8: "a0a0ff", 9: "c000c0", 12: "803b00", 13: "0024c0"}
CLASSES = [{"id": 42, "name": "9a", "longName": "Klasse 9a"}, {"id": 43, "name": "9b", "longName": "Klasse 9b"}]
PERIODS = [(800, 845), (850, 935), (955, 1040), (1045, 1130), (1150, 1235), (1240, 1325), (1330, 1415), (1420, 1505)]

# Wochentag -> [(Stunden, Fach, Lehrer, Raum, Extras)]
WEEK = {
    0: [((0, 1), 1, 1, 1, {}),
        ((2, 3), 2, 2, 1, {"info": "Lektüre „Tschick“ bis Kapitel 12 lesen und mitbringen."}),
        ((4,), 3, 3, 1, {}),
        ((5,), 4, 4, 3, {})],
    1: [((0, 1), 3, 3, 1, {}),
        ((2, 3), 5, 5, 2, {"te_org": 5, "te": 9, "substText": "Vertretung",
                           "info": "Bitte Kapitel 4.2 lesen und Aufgaben 1–3 auf S. 87 bearbeiten."}),
        ((4, 5), 6, 6, 1, {}),
        ((6, 7), 7, 7, 4, {})],
    2: [((0, 1), 2, 2, 1, {}),
        ((2,), 1, 1, 1, {"code": "cancelled", "substText": "Entfall"}),
        ((3,), 12, 8, 1, {}),
        ((4, 5), 8, 10, 5, {"sg": "Inf_9"}),
        ((4, 5), 9, 9, 6, {"sg": "BKu_9"})],
    3: [((0, 1, 2, 3), None, None, 10, {"code": "irregular", "lstext": "Theaterbesuch"}),
        ((4, 5), 1, 1, 1, {})],
    4: [((0, 1), 4, 4, 3, {}),
        ((2,), 3, 3, 1, {}),
        ((3,), 10, 8, 7, {}),
        ((4, 5), 11, 5, 8, {"ro_org": 8, "ro": 9, "substText": "Raumänderung"}),
        ((6,), 13, 9, 1, {"info": "Vokabeln Lektion 5 wiederholen."})],
}


def _int(d):
    return int(d.strftime("%Y%m%d"))


def _monday(d):
    return d - timedelta(days=d.weekday())


def timetable(start, end):
    rows, n = [], 0
    mon = _monday(start)
    while mon <= end:
        for wd, entries in WEEK.items():
            d = mon + timedelta(days=wd)
            if not start <= d <= end:
                continue
            for periods, su, te, ro, ex in entries:
                for p in periods:
                    n += 1
                    row = {"id": n, "date": _int(d), "startTime": PERIODS[p][0], "endTime": PERIODS[p][1],
                           "kl": [{"id": 42}], "su": [{"id": su}] if su else [],
                           "te": [{"id": ex.get("te", te), **({"orgid": ex["te_org"]} if "te_org" in ex else {})}]
                           if te else [],
                           "ro": [{"id": ex.get("ro", ro), **({"orgid": ex["ro_org"]} if "ro_org" in ex else {})}]}
                    for k in ("code", "info", "substText", "lstext", "sg"):
                        if k in ex:
                            row[k] = ex[k]
                    rows.append(row)
        mon += timedelta(days=7)
    return rows


class FakeUntisClient:
    person_id, person_type = 1, 5

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def rpc(self, method, params):
        if method == "getTeachers":
            return [{"id": k, "name": v} for k, v in TEACHERS.items()]
        if method == "getRooms":
            return [{"id": k, "name": v} for k, v in ROOMS.items()]
        if method == "getSubjects":
            return [{"id": k, "name": v, **({"backColor": COLORS[k]} if k in COLORS else {})}
                    for k, v in SUBJECTS.items()]
        if method == "getKlassen":
            return CLASSES
        if method == "getTimegridUnits":
            return [{"day": d, "timeUnits": [{"name": str(i + 1), "startTime": s, "endTime": e}
                                             for i, (s, e) in enumerate(PERIODS)]} for d in range(2, 7)]
        if method == "getTimetable":
            p = params.get("options", params)
            s, e = (datetime.strptime(str(p[k]), "%Y%m%d").date() for k in ("startDate", "endDate"))
            return timetable(s, e)
        return {}

    def rest(self, path, **params):
        today = date.today()
        if path == "/api/rest/view/v1/timetable/entries":
            # Markierungen: Hausaufgabe fällig in der ersten Stunde Mathe/Englisch/Bio jeder Woche,
            # Notiz bei allen Stunden mit Lehrer-Infotext
            s, e = (date.fromisoformat(params[k]) for k in ("start", "end"))
            entries, seen = [], set()
            for r in timetable(s, e):
                icons, texts = [], []
                first = (r["date"], tuple(x["id"] for x in r["su"])) not in seen
                seen.add((r["date"], tuple(x["id"] for x in r["su"])))
                if first and r["su"] and r["su"][0]["id"] in (1, 3, 4) and datetime.strptime(
                        str(r["date"]), "%Y%m%d").weekday() in (0, 4):
                    icons.append("HOMEWORK")
                if r.get("info"):
                    icons.append("NOTES")
                    texts.append({"type": "NOTES_FOR_ALL", "text": r["info"]})
                entries.append({"ids": [r["id"]], "icons": icons, "texts": texts})
            return {"days": [{"gridEntries": entries}]}
        if path == "/api/homeworks/lessons":
            due = lambda n: _int(today + timedelta(days=n))
            return {
                "lessons": [{"id": 101, "subject": "Mat"}, {"id": 102, "subject": "Eng"},
                            {"id": 103, "subject": "Bio"}],
                "homeworks": [
                    {"lessonId": 101, "dueDate": due(1), "text": "Arbeitsheft S. 34, Nr. 2–5", "completed": False},
                    {"lessonId": 102, "dueDate": due(2), "text": "Workbook p. 41, ex. 3 + 4",
                     "remark": "Neue Vokabeln lernen", "completed": False},
                    {"lessonId": 103, "dueDate": due(5), "text": "Plakat zur Zellteilung fertigstellen",
                     "completed": False},
                ]}
        if path == "/api/classreg/classservices":
            mon = _monday(today)
            roles = []
            for w, names in enumerate((("Lena", "Tim"), ("Mia", "Jonas"))):
                for name in names:
                    roles.append({"foreName": name, "duty": {"label": "Klassenordner"},
                                  "startDate": _int(mon + timedelta(days=7 * w)),
                                  "endDate": _int(mon + timedelta(days=7 * w + 6))})
            return {"classRoles": roles}
        return {}


def calendar_events():
    """Gleiches Format wie CalendarFetcher (ohne Farbe)."""
    t = date.today()
    d = lambda n: (t + timedelta(days=n)).isoformat()
    return [
        {"title": "Vokabeltest Englisch", "start": d(1), "end": d(1), "time": "09:55"},
        {"title": "Mathearbeit", "start": d(3), "end": d(3), "time": "08:00"},
        {"title": "Geburtstag Lena", "start": d(3), "end": d(3), "time": None},
        {"title": "Schulkonzert in der Aula", "start": d(6), "end": d(6), "time": "19:00"},
        {"title": "Herbstferien", "start": d(10), "end": d(23), "time": None},
    ]


def weather():
    return {"temp": 14, "code": 2, "is_day": True, "max": 17, "min": 9, "rain": 30}
