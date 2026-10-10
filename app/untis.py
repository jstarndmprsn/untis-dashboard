"""
WebUntis-Zugriff für beide Ansichten (Wochenplan "/" und Tagesansicht "/heute").

  UntisClient   Login + JSON-RPC + REST (eine Sitzung pro Abruf)
  MasterData    Lehrer, Räume, Fächer, Klassen-ID und Zeitraster – wird nur alle
                paar Stunden neu geladen und von beiden Ansichten geteilt
  search_schools  öffentliche Schulsuche (dieselbe wie in der Untis-App)

Im Demo-Modus (DEMO=1) liefert demo.FakeUntisClient erfundene Daten.
"""
import base64
import threading
import time

import requests

import config

MASTERDATA_MAX_AGE = 6 * 3600
SCHOOL_SEARCH_URL = "https://mobile.webuntis.com/ms/schoolquery2"


class UntisError(RuntimeError):
    pass


def clean_server(server):
    """'https://xyz.webuntis.com/WebUntis/?school=…' -> 'xyz.webuntis.com'"""
    s = (server or "").strip()
    for p in ("https://", "http://"):
        if s.startswith(p):
            s = s[len(p):]
    return s.split("/")[0].split("?")[0]


class UntisClient:
    def __init__(self, server, school, user, password):
        self.base = f"https://{clean_server(server)}/WebUntis"
        self.school, self.user, self.password = school, user, password
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "raspy-dashboard"
        self._token = None
        self.person_id = self.person_type = None
        self.auth = {}

    def __enter__(self):
        r = self.rpc("authenticate", {"user": self.user, "password": self.password, "client": "raspy"})
        self.auth = r or {}   # bei Schülern u. a. klasseId der eigenen Klasse
        self.person_id, self.person_type = self.auth.get("personId"), self.auth.get("personType")
        school_b64 = base64.b64encode(self.school.encode()).decode()
        self.s.cookies.set("schoolname", f'"_{school_b64}"')
        return self

    def __exit__(self, *a):
        try:
            self.rpc("logout", {})
        except Exception:
            pass

    def rpc(self, method, params):
        r = self.s.post(f"{self.base}/jsonrpc.do", params={"school": self.school}, timeout=20,
                        json={"id": "1", "jsonrpc": "2.0", "method": method, "params": params})
        try:
            j = r.json()
        except ValueError:
            raise UntisError(f"Unerwartete Antwort von {self.base} (HTTP {r.status_code}) – "
                             f"Server/Schule prüfen") from None
        if "error" in j:
            raise UntisError(j["error"].get("message", str(j["error"])))
        return j.get("result")

    def rest(self, path, **params):
        if self._token is None:
            self._token = self.s.get(f"{self.base}/api/token/new", timeout=15).text
        r = self.s.get(f"{self.base}{path}", params=params, timeout=20,
                       headers={"Authorization": f"Bearer {self._token}"})
        r.raise_for_status()
        return r.json().get("data", {})


def connect(cfg):
    """Neue (noch nicht angemeldete) Sitzung – mit `with connect(cfg) as u:` benutzen."""
    if config.demo(cfg):
        import demo
        return demo.FakeUntisClient()
    if not cfg["UNTIS_USER"] or not cfg["UNTIS_PASSWORD"]:
        raise UntisError("UNTIS_USER/UNTIS_PASSWORD fehlen in config.env")
    if not cfg["UNTIS_SERVER"] or not cfg["UNTIS_SCHOOL"]:
        raise UntisError("UNTIS_SERVER/UNTIS_SCHOOL fehlen in config.env")
    return UntisClient(cfg["UNTIS_SERVER"], cfg["UNTIS_SCHOOL"], cfg["UNTIS_USER"], cfg["UNTIS_PASSWORD"])


def find_class(classes, name):
    """Klasse per Name (oder Langname) suchen, Groß-/Kleinschreibung egal."""
    want = (name or "").strip().lower().replace(" ", "")
    for k in classes:
        if want in ((k.get("name") or "").lower().replace(" ", ""),
                    (k.get("longName") or "").lower().replace(" ", "")):
            return k
    return None


def timegrid(units):
    """getTimegridUnits -> [{name, start, end}] (Tag mit den meisten Stunden)."""
    best = max(units or [], key=lambda d: len(d.get("timeUnits") or []), default=None)
    if not best:
        return None
    out = [{"name": str(u.get("name") or i + 1), "start": u["startTime"], "end": u["endTime"]}
           for i, u in enumerate(sorted(best["timeUnits"], key=lambda u: u["startTime"]))]
    return out or None


class MasterData:
    """Stammdaten, geteilt von Wochen- und Tagesansicht."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.loaded_at = 0
        self.teachers, self.rooms, self.subjects = {}, {}, {}
        self.class_id, self.class_name, self.periods = None, None, None

    def invalidate(self):
        self.loaded_at = 0

    def ensure(self, u):
        with self.lock:
            if time.time() - self.loaded_at < MASTERDATA_MAX_AGE:
                return self
            self.teachers = self._names(u, "getTeachers")
            self.rooms = self._names(u, "getRooms")
            self.subjects = self._names(u, "getSubjects")
            self.class_id, self.class_name = self._class(u)
            try:
                self.periods = timegrid(u.rpc("getTimegridUnits", {}))
            except Exception as e:
                print(f"[untis] Zeitraster nicht verfügbar ({e}) – wird aus den Stunden abgeleitet", flush=True)
                self.periods = None
            self.loaded_at = time.time()
            return self

    @staticmethod
    def _names(u, method):
        try:
            return {x["id"]: x["name"] for x in u.rpc(method, {})}
        except Exception as e:
            print(f"[untis] {method} nicht erlaubt/fehlgeschlagen: {e}", flush=True)
            return {}

    def _class(self, u):
        # Klassen-IDs ändern sich jedes Schuljahr -> per Name suchen statt fest eintragen
        if str(self.cfg.get("UNTIS_CLASS_ID") or "").strip():
            return int(self.cfg["UNTIS_CLASS_ID"]), self.cfg.get("UNTIS_CLASS") or ""
        name = self.cfg.get("UNTIS_CLASS")
        if not name:
            raise UntisError("UNTIS_CLASS fehlt in config.env")
        classes = u.rpc("getKlassen", {})
        k = find_class(classes, name)
        if not k:
            some = ", ".join(sorted(c.get("name", "") for c in classes)[:30])
            raise UntisError(f"Klasse „{name}“ nicht gefunden. Vorhanden: {some}")
        return k["id"], k["name"]


def search_schools(query):
    """Öffentliche Untis-Schulsuche -> [{displayName, address, server, loginName}]"""
    r = requests.post(SCHOOL_SEARCH_URL, timeout=15, json={
        "id": "raspy", "jsonrpc": "2.0", "method": "searchSchool", "params": [{"search": query}]})
    j = r.json()
    if "error" in j:
        msg = j["error"].get("message", "")
        if "too many" in msg.lower():
            raise UntisError("Zu viele Treffer – bitte genauer suchen (z. B. mit Ort)")
        raise UntisError(msg or str(j["error"]))
    return (j.get("result") or {}).get("schools") or []
