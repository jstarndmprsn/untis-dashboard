#!/usr/bin/env python3
"""
Diagnose (raspy-diag): führt auf dem Pi ein kleines Protokoll über Probleme – nur auf dem Gerät.

Der Dienst (raspy-diag.service, optionales Extra in ./install.sh) schaut alle paar Sekunden nach
und schreibt nur, wenn sich ein Zustand ändert: Netz weg/wieder da, Untis nicht erreichbar,
Unterspannung, Überhitzung, SD-Karten-Fehler, abgestürzte Dienste, Strom weg ohne Herunterfahren …
So bleibt das Protokoll klein und die SD-Karte wird geschont.

Datenschutz:
  - Der Dienst kann keine Netzwerkverbindung öffnen (systemd: RestrictAddressFamilies=AF_UNIX,
    IPAddressDeny=any). Es gibt keine Telemetrie, nichts wird hochgeladen.
  - Gespeichert werden Zeitpunkt, Art des Problems und kurze Fehlertexte (ohne Adressen,
    Passwörter, Namen oder Stundenplan-Inhalte). Ansehen: raspy-diag, löschen: sudo raspy-diag clear.
  - Nur "raspy-diag check" fragt (wenn du es aufrufst) DNS und den WebUntis-Server an –
    denselben, den die Anzeige sowieso alle 2 Minuten abfragt.

  raspy-diag                  Überblick: offene Probleme, Häufungen der letzten 7 Tage, letzte Einträge
  raspy-diag log [--days N]   alle Einträge (Standard: 7 Tage)
  raspy-diag check            jetzt alles prüfen (Netz, Untis, Strom, Temperatur, SD-Karte, Dienste …)
  raspy-diag export [ZIEL]    bereinigten Bericht als Textdatei speichern (z. B. auf einen USB-Stick)
  raspy-diag clear            Protokoll löschen (sudo)
  raspy-diag run              der Dienst selbst (raspy-diag.service)

Protokoll: /var/lib/raspy-diag/events.jsonl (höchstens ~1 MB, ältere Einträge fallen heraus)

Nur Python-Standardbibliothek – die Diagnose soll auch laufen, wenn die Python-Umgebung kaputt ist.
"""
import argparse
import collections
import datetime as dt
import glob
import json
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.environ.get("RASPY_DIAG_DIR", "/var/lib/raspy-diag")
RUN_DIR = os.environ.get("RASPY_RUN_DIR", "/run/raspy")
CONFIG_FILE = os.environ.get("RASPY_CONFIG", os.path.join(ROOT, "config.env"))

EVENTS = "events.jsonl"
ALIVE = "alive.json"
LOG_MAX = 512 * 1024          # dann events.jsonl -> events.1.jsonl (zusammen höchstens ~1 MB)
HEARTBEAT = 300               # Lebenszeichen alle 5 min (für "Strom weg um ca. …")
TICK = 10                     # Sekunden zwischen zwei Messungen
BOOT_GRACE = 180              # nach dem Start ist vieles noch nicht da (WLAN, Kiosk, Zeit)

WEB_SOURCES_FILE = "sources.json"   # schreibt raspy-web (app/dashboard.py) nach jedem Abruf
WEB_STALE = 600

UNITS = ["raspy-web.service", "raspy-buttons.service", "raspy-thermal.service", "lightdm.service"]

TEMP_HOT, TEMP_OK = 80.0, 75.0
WIFI_WEAK, WIFI_OK = -78, -72        # dBm
DISK_LOW_PCT, DISK_LOW_MB, DISK_OK_PCT = 5, 300, 8

THROTTLE = {0: ("undervolt", "Unterspannung"), 1: ("freqcap", "Takt begrenzt"),
            2: ("throttled", "CPU gedrosselt"), 3: ("templimit", "Temperaturlimit erreicht")}

# Kernel-Meldungen, die auf Hardware-/Systemprobleme hindeuten
KERNEL_PATTERNS = [
    ("sd", "SD-Karte/Speicher: Lesefehler",
     r"(?i:mmc\d+: .*(error|timeout|timed out))|mmcblk\d+.*error|Buffer I/O error|I/O error, dev|blk_update_request"),
    ("fs", "Dateisystem-Fehler",
     r"EXT4-fs (error|warning)|[Rr]emounting filesystem read-only|FAT-fs \(.*\): .*error"),
    ("undervolt", "Unterspannung (Kernel)", r"[Uu]nder-?voltage detected"),
    ("oom", "Speicher voll – Programm beendet", r"Out of memory: Killed process|oom-kill:"),
    ("hung", "Prozess hing länger als 2 min", r"blocked for more than \d+ seconds"),
    ("wifi_drv", "WLAN-Treiber-Fehler", r"brcmf_\w+: .*(error|fail|timeout)"),
]
KERNEL_RATE = 600             # je Art höchstens ein Eintrag pro 10 min (der Rest wird mitgezählt)

# Was man tun kann – für Überblick, check und Bericht
ADVICE = {
    "unclean": "Strom weg oder eingefroren. Steckt das Netzteil fest? Hängt der Pi an einer "
               "abschaltbaren Steckdosenleiste? Mehrmals zur selben Uhrzeit: Zeitschaltuhr/Hausmeister?",
    "net": "WLAN-Verbindung prüfen (Abstand zum Access Point, Schul-WLAN trennt nachts/morgens?). "
           "Häuft es sich zur selben Uhrzeit, liegt es meist am Netz, nicht am Pi.",
    "wifi_weak": "Schwaches WLAN-Signal: Pi näher an den Access Point, kein Metallgehäuse, "
                 "oder per LAN-Kabel anschließen.",
    "src:untis": "Untis nicht erreichbar. Läuft die Untis-App im selben WLAN? "
                 "Schul-WLAN mit Anmeldeseite blockiert den Zugriff oft.",
    "auth": "Login abgelehnt: Benutzername/Passwort mit ./install.sh --configure prüfen "
            "(raspy versucht es nur alle 30 min, damit das Konto nicht gesperrt wird).",
    "src:calendar": "Kalender nicht erreichbar: geheime iCal-Adresse noch gültig? (./install.sh --configure)",
    "src:weather": "Wetter nicht erreichbar – meist nur kurz, Open-Meteo hat selten Ausfälle.",
    "undervolt": "Unterspannung: Netzteil zu schwach oder Kabel zu dünn/lang. "
                 "Offizielles Netzteil verwenden (Pi 5: 27 W, Pi 4: 15 W).",
    "throttled": "Gedrosselt: zu warm oder zu wenig Strom – Lüfter, Belüftung und Netzteil prüfen.",
    "templimit": "Temperaturlimit: Lüfter/Kühlkörper prüfen, nicht direkt über der Heizung montieren.",
    "freqcap": "Takt begrenzt: meist Unterspannung oder Hitze, siehe dort.",
    "temp": "Zu heiß: Lüfter läuft? Gehäuse belüftet? Sonne/Heizung?",
    "k:sd": "SD-Karte meldet Fehler: Sicherung machen und die Karte bald ersetzen (Markenkarte, A1/A2).",
    "k:fs": "Dateisystem-Fehler: oft Folge von Stromausfällen oder einer alternden SD-Karte.",
    "k:oom": "Arbeitsspeicher voll: läuft noch etwas anderes auf dem Pi? Chromium mit vielen Tabs?",
    "k:hung": "Ein Prozess hing: oft eine langsame/defekte SD-Karte.",
    "k:undervolt": "Unterspannung: Netzteil prüfen (siehe oben).",
    "k:wifi_drv": "WLAN-Treiber-Fehler: System aktuell halten; hilft das nicht, LAN-Kabel oder USB-WLAN.",
    "svc": "Ein Dienst ist abgestürzt und wurde neu gestartet. Details: journalctl -u <dienst> -b",
    "kiosk": "Der Browser läuft nicht (Tab-Taste 3 s gehalten oder abgestürzt). "
             "Neu starten: sudo systemctl restart lightdm",
    "web_stale": "Der Webserver hängt: sudo systemctl restart raspy-web, Details: journalctl -u raspy-web -b",
    "disk": "Speicher fast voll: sudo apt-get clean; sudo journalctl --vacuum-size=50M",
    "clock": "Uhrzeit nicht synchronisiert: im Schulnetz ist NTP oft gesperrt. Ohne richtige Uhrzeit "
             "schlagen HTTPS-Verbindungen fehl und die Tagesansicht zeigt den falschen Tag.",
}

CONDITION_KINDS = {"net", "wifi_weak", "src:untis", "src:calendar", "src:weather", "auth",
                   "undervolt", "throttled", "templimit", "freqcap", "temp", "kiosk", "web_stale",
                   "disk", "clock", "svc_failed"}


# ───────────────────────── kleine Helfer ─────────────────────────
def read(path, default=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


def run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def read_env(path=CONFIG_FILE):
    env = {}
    for line in (read(path, "") or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        env[k.strip()] = v
    return env


def fmt_dur(sec):
    sec = int(sec)
    if sec < 120:
        return f"{sec} s"
    if sec < 5400:
        return f"{round(sec / 60)} min"
    if sec < 2 * 86400:
        return f"{sec / 3600:.1f} h".replace(".", ",")
    return f"{round(sec / 86400)} Tage"


WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def fmt_time(t):
    d = dt.datetime.fromtimestamp(t)
    return f"{WEEKDAYS[d.weekday()]} {d:%d.%m. %H:%M:%S}"


# ───────────────────────── Bereinigen ─────────────────────────
_URL = re.compile(r"\b(?:https?|webcal|wss?)://\S+")
_MAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_MAC = re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")
_IPV4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")
SECRET_KEYS = ("UNTIS_USER", "UNTIS_PASSWORD", "UNTIS_SCHOOL", "CALENDAR_ICS_URL", "GCAL_ICS_URL",
               "WEATHER_LAT", "WEATHER_LON", "WEATHER_PLACE")


def _private_ip(m):
    a, b = int(m.group(1)), int(m.group(2))
    return a in (10, 127) or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31) or (a == 169 and b == 254)


class Redactor:
    """Entfernt alles, was Personen, Schule oder Zugangsdaten verrät – für Protokoll und Bericht."""

    def __init__(self, env=None, user=None, home=None, host=None):
        env = read_env() if env is None else env
        self.words = []
        for k in SECRET_KEYS:
            v = (env.get(k) or "").strip()
            if len(v) >= 4:
                self.words.append((v, f"[{k}]"))
        self.home = home if home is not None else os.path.expanduser("~")
        self.user = user if user is not None else _owner_name()
        self.host = host if host is not None else socket.gethostname()

    def __call__(self, text):
        text = _URL.sub("[Adresse]", str(text))
        for v, label in self.words:
            text = text.replace(v, label)
        text = _MAIL.sub("[E-Mail]", text)
        text = _MAC.sub("[MAC]", text)
        text = _IPV4.sub(lambda m: m.group(0) if _private_ip(m) else "[IP]", text)
        if self.home and self.home != "/":
            text = text.replace(self.home, "~")
        for name, label in ((self.user, "<benutzer>"), (self.host, "<pi>")):
            if name and len(name) >= 3 and name not in ("root", "localhost", "raspberrypi"):
                text = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", label, text)
        return text


def _owner_name():
    """Der Desktop-Benutzer (auch wenn der Dienst als root läuft): Besitzer des Projektordners."""
    try:
        import pwd
        return pwd.getpwuid(os.stat(ROOT).st_uid).pw_name
    except (OSError, KeyError, ImportError):
        return None


# ───────────────────────── Protokoll-Datei ─────────────────────────
class Store:
    def __init__(self, d=STATE_DIR):
        self.dir = d
        self.path = os.path.join(d, EVENTS)
        self.old = os.path.join(d, "events.1.jsonl")

    def _chgrp(self, path):
        # Dateien gehören root, Gruppe = Gruppe des Ordners (der Desktop-Benutzer darf lesen)
        try:
            os.chown(path, -1, os.stat(self.dir).st_gid)
            os.chmod(path, 0o640)
        except OSError:
            pass

    def append(self, ev):
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > LOG_MAX:
                os.replace(self.path, self.old)
            new = not os.path.exists(self.path)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())      # selten, aber ein Stromausfall soll den Eintrag nicht kosten
            if new:
                self._chgrp(self.path)
        except OSError as e:
            print(f"[diag] Protokoll nicht schreibbar: {e}", flush=True)

    def events(self, since=None):
        out = []
        for p in (self.old, self.path):
            try:
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        try:
                            ev = json.loads(line)
                        except ValueError:
                            continue          # halbe Zeile nach Stromausfall
                        if isinstance(ev, dict) and "t" in ev and (since is None or ev["t"] >= since):
                            out.append(ev)
            except OSError:
                pass
        out.sort(key=lambda e: e["t"])
        return out

    def read_alive(self):
        try:
            return json.loads(read(os.path.join(self.dir, ALIVE), "") or "{}")
        except ValueError:
            return {}

    def write_alive(self, data):
        p = os.path.join(self.dir, ALIVE)
        tmp = p + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
            self._chgrp(p)
        except OSError:
            pass

    def clear(self):
        for p in (self.path, self.old):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass


# ───────────────────────── Zustände mit Bestätigung ─────────────────────────
class Cond:
    """Ein Ja/Nein-Zustand ("Netz weg"). Ein Eintrag entsteht erst, wenn der Zustand `confirm`
    Sekunden anhält – kurze Aussetzer erzeugen keine Einträge. Beim Ende wird die Dauer vermerkt."""

    def __init__(self, kind, level="warn", confirm=0, clear=0):
        self.kind, self.level, self.confirm, self.clear = kind, level, confirm, clear
        self.bad = False          # bestätigter Zustand
        self.since = None         # seit wann der beobachtete Zustand vom bestätigten abweicht
        self.started = None
        self.peak = None

    def update(self, bad, now, msg_bad="", msg_good="", value=None):
        """bad: True/False, None = unbekannt (nichts ändern). Gibt ein Ereignis zurück oder None."""
        if bad is None:
            return None
        if bad and self.bad and value is not None:
            self.peak = value if self.peak is None else max(self.peak, value)
        if bad == self.bad:
            self.since = None
            return None
        if self.since is None:
            self.since = now
        if now - self.since < (self.confirm if bad else self.clear):
            return None
        at = self.since
        self.since = None
        self.bad = bad
        if bad:
            self.started, self.peak = at, value
            return {"t": at, "k": self.kind, "lvl": self.level, "state": "bad", "msg": msg_bad}
        dur = at - self.started if self.started else None
        ev = {"t": at, "k": self.kind, "lvl": "info", "state": "good",
              "msg": msg_good + (f" (nach {fmt_dur(dur)})" if dur is not None else "")}
        if dur is not None:
            ev["dur"] = round(dur)
        self.started = None
        return ev


# ───────────────────────── Messungen (in Tests ersetzbar) ─────────────────────────
class Probes:
    def boot_id(self):
        return read("/proc/sys/kernel/random/boot_id", "")

    def uptime(self):
        try:
            return float((read("/proc/uptime", "0") or "0").split()[0])
        except ValueError:
            return 0.0

    def network(self):
        """{"up": bool, "iface": str|None, "signal": dBm|None}"""
        iface = None
        for line in (read("/proc/net/route", "") or "").splitlines()[1:]:
            f = line.split()
            if len(f) > 3 and f[1] == "00000000" and int(f[3], 16) & 2:
                iface = f[0]
                break
        if iface is None:
            for line in (read("/proc/net/ipv6_route", "") or "").splitlines():
                f = line.split()
                if len(f) == 10 and f[0] == "0" * 32 and f[1] == "00" and f[9] != "lo":
                    iface = f[9]
                    break
        signal_dbm = None
        for line in (read("/proc/net/wireless", "") or "").splitlines()[2:]:
            name, _, rest = line.partition(":")
            f = rest.split()
            if name.strip() == (iface or "wlan0") and len(f) >= 3:
                try:
                    signal_dbm = int(float(f[2]))
                except ValueError:
                    pass
        return {"up": iface is not None, "iface": iface, "signal": signal_dbm}

    def throttled(self):
        out = run(["vcgencmd", "get_throttled"], timeout=5)
        try:
            return int(out.strip().split("=")[1], 16)
        except (AttributeError, IndexError, ValueError):
            return None

    def temperature(self):
        v = read("/sys/class/thermal/thermal_zone0/temp")
        try:
            return round(int(v) / 1000, 1)
        except (TypeError, ValueError):
            return None

    def disk(self):
        try:
            u = shutil.disk_usage("/")
            return {"free_mb": u.free // 2**20, "free_pct": round(u.free * 100 / u.total, 1)}
        except OSError:
            return None

    def clock_synced(self):
        out = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], timeout=5)
        if out and out.strip() in ("yes", "no"):
            return out.strip() == "yes"
        if os.path.exists("/run/systemd/timesync/synchronized"):
            return True
        return None

    def units(self, names=UNITS):
        out = run(["systemctl", "show", *names, "-p", "Id,ActiveState,NRestarts,UnitFileState"], timeout=10)
        res = {}
        for block in (out or "").strip().split("\n\n"):
            kv = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            if kv.get("Id"):
                res[kv["Id"]] = {"active": kv.get("ActiveState"), "enabled": kv.get("UnitFileState") == "enabled",
                                 "restarts": int(kv.get("NRestarts") or 0)}
        return res

    def kiosk_running(self):
        for p in glob.glob("/proc/[0-9]*/cmdline"):
            try:
                with open(p, "rb") as f:
                    cmd = f.read()
            except OSError:
                continue
            if b"chromium" in cmd and b"--kiosk" in cmd:
                return True
        return False

    def web_sources(self):
        try:
            return json.loads(read(os.path.join(RUN_DIR, WEB_SOURCES_FILE), "") or "null")
        except ValueError:
            return None

    def kernel(self, since_us):
        """Kernel-Meldungen seit `since_us` (Mikrosekunden) aus dem Journal: [(us, text)]."""
        out = run(["journalctl", "-k", "-b", "-q", "--no-pager", "-o", "json",
                   "--since", f"@{since_us // 1_000_000}"], timeout=20)
        if out is None:
            return None               # journalctl fehlt oder hing
        res = []
        for line in (out or "").splitlines():
            try:
                e = json.loads(line)
                us = int(e.get("__REALTIME_TIMESTAMP", 0))
            except (ValueError, TypeError):
                continue
            msg = e.get("MESSAGE")
            if isinstance(msg, list):     # Bytes, wenn nicht UTF-8
                msg = bytes(b & 0xFF for b in msg if isinstance(b, int)).decode("utf-8", "replace")
            if us > since_us and isinstance(msg, str):
                res.append((us, msg))
        return res

    def system_stopping(self):
        out = run(["systemctl", "is-system-running"], timeout=5)
        return bool(out) and out.strip() == "stopping"


def classify_kernel(msg):
    for key, label, pattern in KERNEL_PATTERNS:
        if re.search(pattern, msg):
            return key, label
    return None


def classify_error(err, auth=False):
    """Fehlertext von raspy-web -> verständliche Kategorie."""
    if auth:
        return "Login abgelehnt"
    e = err or ""
    if re.search(r"NameResolution|getaddrinfo|Name or service not known|Temporary failure in name|nodename", e):
        return "Name nicht auflösbar (DNS – kein Internet?)"
    if re.search(r"ProxyError|Tunnel connection failed|407 Proxy", e):
        return "Proxy blockiert die Verbindung (Schulnetz?)"
    if re.search(r"SSLError|CERTIFICATE|certificate", e):
        return "HTTPS/Zertifikat-Fehler (Uhrzeit falsch? Schul-Proxy?)"
    if re.search(r"Timeout|timed out", e):
        return "Zeitüberschreitung"
    if re.search(r"Network is unreachable|No route to host|Connection refused|ConnectionError|"
                 r"RemoteDisconnected|Connection reset|Connection aborted", e):
        return "keine Verbindung"
    if re.search(r"HTTPError|\b5\d\d\b", e):
        return "Server-Fehler"
    return "Fehler"


# ───────────────────────── Dienst ─────────────────────────
SOURCE_NAMES = {"untis": "Untis", "calendar": "Kalender", "weather": "Wetter"}


class Monitor:
    def __init__(self, store, probes=None, redact=None, now=time.time):
        self.store, self.p, self.now = store, probes or Probes(), now
        self.redact = redact or Redactor()
        self.boot = self.p.boot_id()
        t = now()
        self.c = {
            "net": Cond("net", "warn", confirm=20, clear=10),
            "wifi_weak": Cond("wifi_weak", "info", confirm=120, clear=120),
            "temp": Cond("temp", "warn", confirm=30),
            "disk": Cond("disk", "warn"),
            "clock": Cond("clock", "warn", confirm=600),
            "kiosk": Cond("kiosk", "info", confirm=60, clear=10),
            "web_stale": Cond("web_stale", "err"),
            "auth": Cond("auth", "err"),
        }
        for bit, (key, _) in THROTTLE.items():
            self.c[key] = Cond(key, "err" if key == "undervolt" else "warn", confirm=0, clear=60)
        for key in SOURCE_NAMES:
            self.c[f"src:{key}"] = Cond(f"src:{key}", "warn", confirm=90, clear=0)
        self.sticky = 0
        self.restarts = None
        self.failed = set()
        self.kernel_since = int(t * 1_000_000)
        self.kernel_last = {}       # Art -> (Zeitpunkt des letzten Eintrags, seitdem unterdrückt)
        self.last_alive = 0
        self.synced = None
        self.n = 0

    # Ereignis speichern (mit Uhrzeit-Hinweis, falls die Uhr noch nicht stimmt)
    def emit(self, ev, extra=None):
        if ev is None:
            return
        ev = dict(ev, b=self.boot[:8])
        if extra:
            ev.update(extra)
        ev["msg"] = self.redact(ev.get("msg", ""))[:300]
        if self.synced is False:
            ev["unsynced"] = True
        self.store.append(ev)
        print(f"[diag] {ev['msg']}", flush=True)

    def event(self, kind, msg, level="info", t=None, **extra):
        self.emit({"t": t or self.now(), "k": kind, "lvl": level, "msg": msg}, extra)

    def start(self):
        """Beim Start: neuer Boot? Endete der vorige Betrieb sauber?"""
        t = self.now()
        alive = self.store.read_alive()
        up = self.p.uptime()
        booted = t - up
        self.synced = self.p.clock_synced()
        if alive.get("boot") == self.boot:
            # nur der Dienst wurde neu gestartet (Update, Absturz): Kernel-Meldungen ab dem letzten Lebenszeichen
            if not alive.get("clean"):
                self.event("restart", "Diagnose-Dienst neu gestartet (war abgestürzt?)")
            self.kernel_since = int(max(alive.get("t", 0), booted) * 1_000_000)
        else:
            self.kernel_since = int(booted * 1_000_000)    # neuer Boot: alles seit dem Start ansehen
            if not alive:
                self.event("boot", f"Diagnose eingeschaltet · Version {version()}")
            else:
                if not alive.get("clean"):
                    last = alive.get("t")
                    self.event("unclean", "Vorheriger Betrieb endete ohne Herunterfahren (Strom weg, Absturz "
                               "oder eingefroren)" + (f" – letztes Lebenszeichen {fmt_time(last)}" if last else ""),
                               "err", t=booted, last=last)
                self.event("boot", f"Pi gestartet · Version {version()}", t=booted)
        self.heartbeat(force=True)

    def heartbeat(self, force=False, clean=False):
        t = self.now()
        if force or clean or t - self.last_alive >= HEARTBEAT:
            self.store.write_alive({"boot": self.boot, "t": t, "clean": clean})
            self.last_alive = t

    def stop(self):
        if self.p.system_stopping():
            self.event("shutdown", "Pi wird heruntergefahren/neu gestartet")
        else:
            self.event("stop", "Diagnose-Dienst beendet")
        self.heartbeat(clean=True)

    def step(self):
        t = self.now()
        up = self.p.uptime()
        settled = up > BOOT_GRACE
        n = self.n
        self.n += 1

        # Netz
        net = self.p.network()
        self.emit(self.c["net"].update(not net["up"], t, "Netz weg (keine Verbindung zum Router)",
                                       f"Netz wieder da ({net['iface']})"))
        sig = net["signal"]
        weak = None if sig is None else (sig < WIFI_WEAK if not self.c["wifi_weak"].bad else sig < WIFI_OK)
        self.emit(self.c["wifi_weak"].update(weak, t, f"WLAN-Signal schwach ({sig} dBm)",
                                             f"WLAN-Signal wieder gut ({sig} dBm)"))

        # Strom / Drosselung (vcgencmd)
        thr = self.p.throttled()
        if thr is not None:
            for bit, (key, label) in THROTTLE.items():
                now_set = bool(thr & (1 << bit))
                ev = self.c[key].update(now_set, t, label, f"{label} vorbei")
                new_sticky = thr & (1 << (bit + 16)) and not self.sticky & (1 << (bit + 16))
                if ev is None and new_sticky and not now_set and not self.c[key].bad:
                    # kurz aufgetreten, zwischen zwei Messungen schon wieder vorbei
                    ev = {"t": t, "k": key, "lvl": self.c[key].level, "msg": f"{label} (kurz, seit letzter Messung)"}
                self.emit(ev)
            self.sticky = thr

        # Temperatur
        temp = self.p.temperature()
        if temp is not None:
            hot = temp >= TEMP_HOT if not self.c["temp"].bad else temp >= TEMP_OK
            ev = self.c["temp"].update(hot, t, f"Zu heiß: {temp} °C", f"Temperatur wieder normal ({temp} °C)", value=temp)
            if ev and ev["state"] == "good" and self.c["temp"].peak:
                ev["msg"] += f", höchstens {self.c['temp'].peak} °C"
            self.emit(ev)

        # Uhrzeit
        if n % 6 == 0:
            self.synced = self.p.clock_synced()
            self.emit(self.c["clock"].update(None if self.synced is None else not self.synced, t,
                                             "Uhrzeit nicht synchronisiert (Zeitserver nicht erreichbar)",
                                             "Uhrzeit synchronisiert"))

        # Daten der Anzeige (raspy-web schreibt den Zustand jeder Quelle)
        web = self.p.web_sources()
        if isinstance(web, dict):
            stale = t - float(web.get("updated") or 0) > WEB_STALE
            for key, name in SOURCE_NAMES.items():
                s = (web.get("sources") or {}).get(key)
                if not isinstance(s, dict) or stale:
                    continue
                if s.get("auth"):
                    self.emit(self.c["auth"].update(True, t, f"{name}-Login abgelehnt (Passwort/Konto prüfen)"))
                    continue
                if key == "untis":
                    self.emit(self.c["auth"].update(False, t, "", f"{name}-Login wieder ok"))
                err = s.get("error")
                bad = bool(err) if s.get("loaded") or err else None
                self.emit(self.c[f"src:{key}"].update(
                    bad, t, f"{name} nicht erreichbar: {classify_error(err)} – {err or ''}",
                    f"{name} wieder erreichbar"))
            units = self.p.units(["raspy-web.service"]) if stale else {}
            web_active = units.get("raspy-web.service", {}).get("active") == "active"
            self.emit(self.c["web_stale"].update(stale and web_active, t,
                                                 "Webserver hängt (keine neuen Daten seit über 10 min)",
                                                 "Webserver liefert wieder Daten"))

        # Dienste (Absturz = Neustart durch systemd) und Kiosk
        if n % 3 == 0:
            units = self.p.units()
            for name, u in units.items():
                if not u["enabled"]:
                    continue
                before = (self.restarts or {}).get(name)
                if before is not None and u["restarts"] > before:
                    self.event("svc", f"{name} abgestürzt und neu gestartet ({u['restarts'] - before}×)", "warn",
                               unit=name)
                if u["active"] == "failed" and name not in self.failed:
                    self.failed.add(name)
                    self.event("svc_failed", f"{name} ausgefallen (startet nicht mehr)", "err", state="bad", unit=name)
                elif u["active"] == "active" and name in self.failed:
                    self.failed.discard(name)
                    self.event("svc_failed", f"{name} läuft wieder", state="good", unit=name)
            self.restarts = {k: v["restarts"] for k, v in units.items()}
            lightdm = units.get("lightdm.service", {})
            if settled and lightdm.get("enabled") and lightdm.get("active") == "active":
                self.emit(self.c["kiosk"].update(not self.p.kiosk_running(), t,
                                                 "Kiosk-Browser läuft nicht (geschlossen oder abgestürzt)",
                                                 "Kiosk-Browser läuft wieder"))

        # Speicherplatz
        if n % 30 == 0:
            d = self.p.disk()
            if d:
                low = (d["free_pct"] < DISK_LOW_PCT or d["free_mb"] < DISK_LOW_MB) if not self.c["disk"].bad \
                    else d["free_pct"] < DISK_OK_PCT
                self.emit(self.c["disk"].update(low, t, f"Speicher fast voll ({d['free_mb']} MB frei)",
                                                f"Wieder genug Speicher ({d['free_mb']} MB frei)"))

        # Kernel-Meldungen (SD-Karte, Dateisystem, Speicher voll, WLAN-Treiber …)
        if n % 6 == 0:
            self.scan_kernel(t)

        self.heartbeat()

    def scan_kernel(self, t):
        lines = self.p.kernel(self.kernel_since) or []
        found = collections.OrderedDict()
        for us, msg in lines:
            self.kernel_since = max(self.kernel_since, us)
            c = classify_kernel(msg)
            if c:
                key = c[0]
                cnt, first_us, sample = found.get(key, (0, us, msg))
                found[key] = (cnt + 1, first_us, sample)
        for key, (cnt, first_us, sample) in found.items():
            label = next(lbl for k, lbl, _ in KERNEL_PATTERNS if k == key)
            last, suppressed = self.kernel_last.get(key, (0, 0))
            if t - last < KERNEL_RATE:
                self.kernel_last[key] = (last, suppressed + cnt)
                continue
            total = cnt + suppressed
            self.kernel_last[key] = (t, 0)
            self.event(f"k:{key}", f"{label}{f' ({total}×)' if total > 1 else ''}: {sample.strip()[:160]}",
                       "err" if key in ("sd", "fs") else "warn", t=first_us / 1_000_000)


def base_dir():
    """Projektordner; mit Software-Wächter liegt das Programm in BASE/releases/<version>."""
    real = os.path.realpath(ROOT)
    return os.path.dirname(os.path.dirname(real)) if os.path.basename(os.path.dirname(real)) == "releases" else real


def version():
    real = os.path.realpath(ROOT)
    if os.path.basename(os.path.dirname(real)) == "releases":
        return os.path.basename(real)
    out = run(["git", "-c", f"safe.directory={ROOT}", "-C", ROOT, "describe", "--tags", "--always"], timeout=5)
    return (out or "").strip() or "?"


def serve(store):
    os.makedirs(store.dir, exist_ok=True)
    mon = Monitor(store)
    running = {"on": True}

    def stop(signum, frame):
        running["on"] = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    mon.start()
    while running["on"]:
        try:
            mon.step()
        except Exception as e:      # eine fehlerhafte Messung darf den Dienst nicht beenden
            print(f"[diag] Messung fehlgeschlagen: {type(e).__name__}: {e}", flush=True)
        for _ in range(TICK):
            if not running["on"]:
                break
            time.sleep(1)
    mon.stop()
    return 0


# ───────────────────────── Auswertung ─────────────────────────
LEVEL_MARK = {"err": "✗", "warn": "!", "info": "·"}


def open_problems(events):
    """Zustände, die im aktuellen Boot begonnen und noch nicht geendet haben."""
    cur = {}
    for ev in events:
        if ev["k"] in ("boot", "unclean"):
            cur = {}
        key = ev["k"] + ("/" + ev["unit"] if ev.get("unit") else "")
        if ev.get("state") == "bad":
            cur[key] = ev
        elif ev.get("state") == "good":
            cur.pop(key, None)
    return sorted(cur.values(), key=lambda e: e["t"])


def problem_kind(ev):
    return "svc" if ev["k"] in ("svc", "svc_failed") else ev["k"]


def summarize(events, now=None, days=7):
    """Häufungen der letzten Tage: [(kind, count, total_dur, typical_hour|None, last_ev)]."""
    now = now or time.time()
    groups = collections.defaultdict(list)
    for ev in events:
        if ev["t"] < now - days * 86400 or ev.get("lvl") == "info" and ev["k"] != "kiosk":
            continue
        if ev.get("state") == "good":
            continue
        groups[problem_kind(ev)].append(ev)
    res = []
    for kind, evs in groups.items():
        hours = collections.Counter(dt.datetime.fromtimestamp(e["t"]).hour for e in evs if not e.get("unsynced"))
        typical = None
        if len(evs) >= 3 and hours:
            h, c = hours.most_common(1)[0]
            if c * 2 >= len(evs):
                typical = h
        dur = sum(e.get("dur", 0) for e in events
                  if problem_kind(e) == kind and e.get("state") == "good" and e["t"] >= now - days * 86400)
        res.append((kind, len(evs), dur, typical, evs[-1]))
    res.sort(key=lambda r: (-r[1], -r[4]["t"]))
    return res


def line(ev):
    mark = LEVEL_MARK.get(ev.get("lvl"), "·")
    note = "  (Uhrzeit evtl. falsch)" if ev.get("unsynced") else ""
    return f"  {mark} {fmt_time(ev['t'])}  {ev.get('msg', '')}{note}"


def cmd_status(store, out=print, now=None):
    events = store.events()
    alive = store.read_alive()
    if not events and not alive:
        out("Noch keine Einträge. Läuft der Dienst?  systemctl status raspy-diag")
        return 0
    now = now or time.time()
    out("Diagnose-Protokoll (nur auf diesem Pi – nichts wird verschickt)")
    if alive.get("t"):
        out(f"Letztes Lebenszeichen:  {fmt_time(alive['t'])}"
            + ("" if now - alive["t"] < HEARTBEAT * 2 else "  ← Dienst läuft nicht?"))
    if events:
        out(f"Protokoll seit:         {fmt_time(events[0]['t'])}  "
            f"({len(events)} {'Eintrag' if len(events) == 1 else 'Einträge'})")

    op = open_problems(events)
    out("\nGerade offen:" if op else "\nGerade offen:           nichts")
    for ev in op:
        out(f"  {LEVEL_MARK.get(ev['lvl'], '·')} seit {fmt_time(ev['t'])}: {ev['msg']}")

    summary = summarize(events, now)
    if summary:
        out("\nLetzte 7 Tage:")
        for kind, cnt, dur, typical, last in summary:
            extra = []
            if dur:
                extra.append(f"zusammen {fmt_dur(dur)}")
            if typical is not None:
                extra.append(f"meist gegen {typical:02d} Uhr")
            out(f"  {cnt:3d}×  {last['msg'][:70]}" + (f"  ({', '.join(extra)})" if extra else ""))
        tips = []
        for kind, *_ in summary:
            tip = ADVICE.get(kind) or ADVICE.get(kind.split(":")[0])
            if tip and tip not in tips:
                tips.append(tip)
        if tips:
            out("\nWas helfen kann:")
            for tip in tips[:4]:
                out(f"  → {tip}")
    else:
        out("\nLetzte 7 Tage:          keine Probleme")

    out("\nLetzte Einträge:")
    for ev in events[-12:]:
        out(line(ev))
    out("\nMehr: raspy-diag log · jetzt prüfen: raspy-diag check · Bericht: raspy-diag export")
    return 0


def cmd_log(store, days=7, out=print):
    since = None if days <= 0 else time.time() - days * 86400
    events = store.events(since)
    if not events:
        out("Keine Einträge" + (f" in den letzten {days} Tagen." if since else "."))
    for ev in events:
        out(line(ev))
    return 0


# ───────────────────────── Live-Prüfung ─────────────────────────
class Check:
    def __init__(self, out=print):
        self.out, self.problems = out, 0

    def ok(self, label, text=""):
        self.out(f"  ✓ {label:<22}{text}")

    def warn(self, label, text, tip=None, bad=False):
        self.problems += 1
        self.out(f"  {'✗' if bad else '!'} {label:<22}{text}")
        if tip:
            self.out(f"    → {tip}")

    def info(self, label, text):
        self.out(f"  · {label:<22}{text}")


def tls_check(host, timeout=8):
    """Nur der HTTPS-Verbindungsaufbau mit Zertifikatsprüfung – es wird nichts gesendet."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=timeout) as s:
            with ctx.wrap_socket(s, server_hostname=host):
                return None
    except ssl.SSLCertVerificationError as e:
        return f"Zertifikat ungültig ({e.verify_message})"
    except (OSError, ssl.SSLError) as e:
        return f"{type(e).__name__}: {e}"


def cmd_check(out=print, probes=None, redact=None):
    p = probes or Probes()
    redact = redact or Redactor()
    env = read_env()
    c = Check(out)
    up = p.uptime()
    out("Live-Prüfung (fragt nur DNS und den WebUntis-Server an)")

    out("\nNetz")
    net = p.network()
    if net["up"]:
        c.ok("Verbindung", f"über {net['iface']}")
    else:
        c.warn("Verbindung", "kein Netz (keine Standard-Route)", ADVICE["net"], bad=True)
    if net["signal"] is not None:
        if net["signal"] < WIFI_WEAK:
            c.warn("WLAN-Signal", f"{net['signal']} dBm (schwach)", ADVICE["wifi_weak"])
        else:
            c.ok("WLAN-Signal", f"{net['signal']} dBm")
    server = env.get("UNTIS_SERVER", "").strip()
    if server and net["up"]:
        try:
            socket.getaddrinfo(server, 443, proto=socket.IPPROTO_TCP)
            c.ok("DNS", f"{server} gefunden")
            err = tls_check(server)
            if err:
                c.warn("HTTPS zu WebUntis", redact(err),
                       "Uhrzeit prüfen; im Schulnetz evtl. ein Proxy, der HTTPS aufbricht.", bad=True)
            else:
                c.ok("HTTPS zu WebUntis", "Verbindung und Zertifikat ok")
        except OSError as e:
            c.warn("DNS", f"{server} nicht auflösbar ({e.strerror or e})",
                   "Kein Internet? Schul-WLAN mit Anmeldeseite? DNS-Server des Routers prüfen.", bad=True)
    elif not server:
        c.info("WebUntis", "kein Server in config.env (./install.sh --configure)")

    out("\nZeit")
    synced = p.clock_synced()
    now_d = dt.datetime.now()
    now_s = f"{WEEKDAYS[now_d.weekday()]} {now_d:%d.%m.%Y %H:%M}"
    if synced is False:
        c.warn("Uhrzeit", f"{now_s} – nicht synchronisiert", ADVICE["clock"])
    else:
        c.ok("Uhrzeit", now_s + (" (synchronisiert)" if synced else ""))
    c.info("Läuft seit", fmt_dur(up))

    out("\nAnzeige")
    web = p.web_sources()
    units = p.units()
    wu = units.get("raspy-web.service", {})
    if wu.get("active") == "active":
        c.ok("raspy-web", "läuft" + (f" ({wu['restarts']}× neu gestartet)" if wu.get("restarts") else ""))
    else:
        c.warn("raspy-web", wu.get("active") or "nicht gefunden",
               "sudo systemctl restart raspy-web · Details: journalctl -u raspy-web -b", bad=True)
    port = env.get("PORT", "5000") or "5000"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/content", timeout=5):
            c.ok("Webseite", f"antwortet (Port {port})")
    except OSError as e:
        c.warn("Webseite", f"antwortet nicht auf Port {port} ({type(e).__name__})", ADVICE["web_stale"], bad=True)
    if isinstance(web, dict):
        for key, name in SOURCE_NAMES.items():
            s = (web.get("sources") or {}).get(key)
            if not isinstance(s, dict):
                continue
            if s.get("ok"):
                c.ok(name, f"Daten von {s.get('since') or '?'}")
            elif s.get("auth"):
                c.warn(name, "Login abgelehnt", ADVICE["auth"], bad=True)
            elif s.get("error"):
                c.warn(name, f"{classify_error(s['error'])} – {redact(s['error'])[:100]}"
                       + (f" · zeigt Stand {s['since']}" if s.get("since") else ""),
                       ADVICE.get(f"src:{key}"), bad=key == "untis")
            else:
                c.info(name, "wird geladen …")
    lightdm = units.get("lightdm.service", {})
    if lightdm.get("active") == "active":
        if p.kiosk_running():
            c.ok("Kiosk-Browser", "läuft")
        else:
            c.warn("Kiosk-Browser", "läuft nicht", ADVICE["kiosk"])
    elif lightdm:
        c.warn("Desktop (lightdm)", lightdm.get("active") or "?", "sudo systemctl restart lightdm")
    for name in ("raspy-buttons.service", "raspy-thermal.service"):
        u = units.get(name, {})
        if u.get("enabled"):
            if u.get("active") == "active":
                c.ok(name.replace(".service", ""), "läuft" + (f" ({u['restarts']}× neu gestartet)"
                                                             if u.get("restarts") else ""))
            else:
                c.warn(name.replace(".service", ""), u.get("active") or "?", f"journalctl -u {name} -b")

    out("\nHardware")
    thr = p.throttled()
    if thr is None:
        c.info("Strom/Drosselung", "vcgencmd nicht verfügbar")
    else:
        now_l = [lbl for b, (_, lbl) in THROTTLE.items() if thr & (1 << b)]
        past_l = [lbl for b, (_, lbl) in THROTTLE.items() if thr & (1 << (b + 16))]
        if now_l:
            c.warn("Strom/Drosselung", "jetzt: " + ", ".join(now_l), ADVICE["undervolt"] if thr & 1 else
                   ADVICE["throttled"], bad=bool(thr & 1))
        elif past_l:
            c.warn("Strom/Drosselung", "seit dem Start aufgetreten: " + ", ".join(past_l),
                   ADVICE["undervolt"] if thr & (1 << 16) else ADVICE["throttled"])
        else:
            c.ok("Strom/Drosselung", "keine Unterspannung, keine Drosselung seit dem Start")
    temp = p.temperature()
    if temp is not None:
        if temp >= TEMP_HOT:
            c.warn("Temperatur", f"{temp} °C", ADVICE["temp"], bad=True)
        else:
            c.ok("Temperatur", f"{temp} °C")
    d = p.disk()
    if d:
        if d["free_pct"] < DISK_LOW_PCT or d["free_mb"] < DISK_LOW_MB:
            c.warn("Speicherplatz", f"{d['free_mb']} MB frei ({d['free_pct']} %)", ADVICE["disk"], bad=True)
        else:
            c.ok("Speicherplatz", f"{d['free_mb'] / 1024:.1f} GB frei ({d['free_pct']:.0f} %)".replace(".", ","))
    try:
        lines = p.kernel(int((time.time() - up) * 1_000_000))
    except Exception:
        lines = None
    if lines or (lines is not None and os.geteuid() == 0):
        hits = collections.Counter()
        for _, msg in lines:
            k = classify_kernel(msg)
            if k:
                hits[k] += 1
        if hits:
            for (key, label), n in hits.items():
                c.warn("Kernel", f"{label}: {n}× seit dem Start", ADVICE.get(f"k:{key}"), bad=key in ("sd", "fs"))
        else:
            c.ok("Kernel", "keine SD-/Dateisystem-/Speicherfehler seit dem Start")
    else:
        c.info("Kernel", "Kernel-Meldungen nicht lesbar – mit sudo raspy-diag check prüfen")

    cfg = os.path.join(ROOT, "config.env")
    if os.path.exists(cfg) and os.stat(cfg).st_mode & 0o077:
        c.warn("config.env", "auch für andere Benutzer lesbar", f"chmod 600 {redact(cfg)}")

    out("")
    out("Alles in Ordnung." if not c.problems else f"{c.problems} Auffälligkeit(en) – siehe oben.")
    return 0 if not c.problems else 1


# ───────────────────────── Bericht ─────────────────────────
def system_info():
    rows = []
    model = (read("/proc/device-tree/model", "") or "").replace("\0", "") or "unbekannt"
    rows.append(("Gerät", model))
    osr = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', read("/etc/os-release", "") or "", re.M))
    rows.append(("System", osr.get("PRETTY_NAME", "?")))
    rows.append(("Kernel", os.uname().release))
    rows.append(("raspy-Version", version()))
    st = read(os.path.join(ROOT, ".install-state")) or read(os.path.join(base_dir(), ".install-state"), "")
    extras = [k[2:].lower() for k, v in re.findall(r"^(F_\w+)=(\w*)$", st or "", re.M) if v == "y"]
    rows.append(("Extras", ", ".join(extras) or "keine"))
    env = read_env()
    feats = [f"Kalender {'an' if env.get('CALENDAR_ICS_URL') else 'aus'}",
             f"Wetter {'an' if env.get('WEATHER_LAT') else 'aus'}",
             f"Zeitplan {'aus' if env.get('SCREEN_SCHEDULE', '1') in ('0', 'no', 'false', 'off') else 'an'}",
             f"LAN_ACCESS={env.get('LAN_ACCESS', '0') or '0'}"]
    rows.append(("Einstellungen", ", ".join(feats) + " (Werte nicht enthalten)"))
    return rows


def tail(path, n):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return collections.deque(f, maxlen=n)
    except OSError:
        return None


def build_report(store):
    redact = Redactor()
    buf = []
    w = buf.append
    w("raspy – Diagnose-Bericht")
    w(f"Erstellt: {dt.datetime.now():%d.%m.%Y %H:%M}")
    w("Bereinigt: ohne Passwort, Benutzername, Schule, Kalender-Adresse, Wetter-Ort, öffentliche IPs,")
    w("Benutzer- und Gerätename. Bitte vor dem Weitergeben trotzdem kurz durchlesen.")
    w("")
    w("== System ==")
    for k, v in system_info():
        w(f"  {k:<15}{v}")
    w("")
    w("== Überblick ==")
    cmd_status(store, out=w)
    w("")
    w("== Live-Prüfung ==")
    cmd_check(out=w, redact=redact)
    w("")
    w("== Alle Einträge ==")
    cmd_log(store, days=0, out=w)
    for title, path in (("Software-Wächter", "/var/log/raspy-guard.log"),
                        ("System-Updates", "/var/log/raspy-update.log")):
        lines = tail(path, 60)
        if lines:
            w("")
            w(f"== {title} (letzte {len(lines)} Zeilen von {path}) ==")
            buf.extend("  " + ln.rstrip("\n") for ln in lines)
    j = run(["journalctl", "-u", "raspy-web", "-b", "-n", "80", "--no-pager", "-q", "-o", "short-iso"], timeout=20)
    if j and j.strip():
        w("")
        w("== Webserver-Meldungen seit dem Start (letzte 80) ==")
        buf.extend("  " + ln for ln in j.strip().splitlines())
    return "\n".join(redact(ln) for ln in buf) + "\n"


def cmd_export(store, target=None):
    name = f"raspy-diagnose-{dt.datetime.now():%Y%m%d-%H%M}.txt"
    if not target:
        sticks = [d for d in glob.glob(f"/media/{_owner_name() or '*'}/*") if os.access(d, os.W_OK)]
        target = sticks[0] if len(sticks) == 1 else os.path.expanduser("~")
    path = os.path.join(target, name) if os.path.isdir(target) else target
    text = build_report(store)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Bericht gespeichert: {path}")
    print("Er bleibt auf dem Gerät/Stick – weitergeben nur, wenn du das möchtest (vorher durchlesen).")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="raspy-diag", description=__doc__.split("\n\n")[0])
    ap.add_argument("command", nargs="?", default="status",
                    choices=["status", "log", "check", "export", "clear", "run"])
    ap.add_argument("target", nargs="?", help="export: Ordner oder Datei (Standard: USB-Stick bzw. ~)")
    ap.add_argument("--days", type=int, default=7, help="log: so viele Tage (0 = alles)")
    a = ap.parse_args(argv)
    store = Store()
    if a.command == "run":
        return serve(store)
    if a.command == "status":
        return cmd_status(store)
    if a.command == "log":
        return cmd_log(store, a.days)
    if a.command == "check":
        return cmd_check()
    if a.command == "export":
        return cmd_export(store, a.target)
    if a.command == "clear":
        if os.geteuid() != 0:
            print("Bitte mit sudo: sudo raspy-diag clear", file=sys.stderr)
            return 1
        store.clear()
        print("Protokoll gelöscht.")
        return 0
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:      # raspy-diag log | head
        sys.exit(0)
