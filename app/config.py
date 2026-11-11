"""
Einstellungen aus config.env (im Projektordner, wird von configure.py geschrieben).

Format: KEY=VALUE pro Zeile, # für Kommentare. Werte dürfen in "..." stehen
(nötig bei Leerzeichen oder Sonderzeichen; \\ und \" werden dann entschlüsselt).
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.environ.get("RASPY_CONFIG", os.path.join(ROOT, "config.env"))

DEFAULTS = {
    "UNTIS_SERVER": "",          # z. B. xyz.webuntis.com
    "UNTIS_SCHOOL": "",          # Login-Name der Schule, z. B. gymnasium-musterstadt
    "UNTIS_USER": "",
    "UNTIS_PASSWORD": "",
    "UNTIS_CLASS": "",           # Klassenname, z. B. 7a (ID wird automatisch gesucht)
    "UNTIS_CLASS_ID": "",        # optional: feste Klassen-ID statt Name
    "DUTY_LABEL": "Klassenordner",
    "CALENDAR_ICS_URL": "",      # optional: geheime iCal-Adresse (Google, iCloud, Outlook …)
    "WEATHER_LAT": "",           # optional: leer = kein Wetter
    "WEATHER_LON": "",
    "WEATHER_PLACE": "",
    "SCREEN_SCHEDULE": "1",      # 0 = Bildschirm immer an (kein Zeitplan)
    "SCREEN_ON_DAYS": "12345",   # Wochentage (1 = Mo … 7 = So), an denen der Bildschirm an bleibt
    "SCREEN_ON_FROM": "0730",    # in dieser Zeit bleibt der Bildschirm an
    "SCREEN_ON_UNTIL": "1600",
    "SCREEN_IDLE_OFF": "600",    # sonst nach so vielen Sekunden aus
    # ── Bildschirm (device/display.sh, static/fit.js) ──
    "DISPLAY_OUTPUT": "",        # xrandr-Ausgang, z. B. HDMI-1 (leer = erster angeschlossener)
    "DISPLAY_RESOLUTION": "",    # z. B. 1920x1080 oder 1280x720@60 (leer = wie vom System eingestellt)
    "DISPLAY_ROTATE": "",        # 0/90/180/270 Grad im Uhrzeigersinn (leer = wie vom System eingestellt)
    "DISPLAY_MARGIN": "0",       # Rand in % (gegen abgeschnittene Ränder bei Fernsehern/Overscan)
    "BROWSER_FLAGS": "",         # zusätzliche Chromium-Optionen, z. B. --disable-gpu
    # ── Tasten (device/button.py) ──
    "BUTTON_TAB_GPIO": "17",     # leer = keine Tab-Taste
    "BUTTON_POWER_GPIO": "27",   # leer = keine Aus-Taste
    "BUTTON_WIRING": "gnd",      # gnd | 3v3 | active-low | active-high (siehe docs/hardware.md)
    "BUTTON_TAB_HOLD": "3",      # Tab-Taste so lange halten -> Chromium beenden
    "BUTTON_POWER_HOLD": "4",    # Aus-Taste so lange halten -> herunterfahren
    "BUTTON_COMBO_HOLD": "2",    # beide so lange halten -> System-Dashboard
    "BUTTON_MIN_PRESS": "0.05",  # kürzere Drücke = Störung (lange Kabel: höher stellen)
    # ── Überhitzungsschutz + Lüfter (device/thermal_guard.py, /thermal) ──
    "THERMAL_SENSOR": "/sys/class/thermal/thermal_zone0/temp",
    "THERMAL_WARN": "75",        # ab hier "warm" im System-Dashboard
    "THERMAL_CRIT": "85",        # so heiß für THERMAL_CRIT_SECONDS -> Warnung + Countdown
    "THERMAL_CRIT_SECONDS": "10",
    "THERMAL_PANIC": "90",       # ab hier sofort Countdown
    "THERMAL_ABORT": "82",       # darunter wird der Countdown abgebrochen
    "THERMAL_COUNTDOWN": "20",   # Sekunden Warnung vor dem Herunterfahren
    "FAN_CURVE": "45:0 50:55 60:70 68:100 73:120 77:150 80:200 83:255",   # °C:PWM (0–255)
    "FAN_HWMON": "pwmfan",       # Name des Lüfters unter /sys/class/hwmon (Pi 5 Active Cooler: pwmfan)
    "FAN_MAX_RPM": "9200",       # Skala der Drehzahl-Kurve im System-Dashboard
    "PORT": "5000",
    "LAN_ACCESS": "0",           # 1 = Webserver auch von anderen Geräten im Netz erreichbar
    "STATUS_FILE": "",           # optional: Hinweis eines anderen Programms in der Statuszeile von /heute
    "GUARD_WINDOWS": "",         # Software-Wächter: Zeitfenster für Updates, z. B. "Di,Do 11:35-11:50; Mo-Fr 16:15-18:00"
    "GUARD_APT": "0",            # 1 = auch die System-Updates (apt) in diese Zeitfenster legen
    "DEMO": "0",                 # 1 = erfundene Beispieldaten statt Untis (zum Ausprobieren)
}


def _unquote(v):
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        q, v = v[0], v[1:-1]
        if q == '"':
            v = v.replace('\\"', '"').replace("\\\\", "\\")
    return v


def load_env(path=CONFIG_FILE):
    """Liest KEY=VALUE-Zeilen (ohne Zusatz-Library)."""
    env = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = _unquote(v.strip())
    except FileNotFoundError:
        pass
    return env


def quote(v):
    v = str(v)
    if v and all(c.isalnum() or c in "._-/:+@" for c in v):
        return v
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def load(path=CONFIG_FILE):
    """Alle Einstellungen mit Standardwerten. Umgebungsvariablen haben Vorrang."""
    env = load_env(path)
    # ältere Installationen: GCAL_ICS_URL statt CALENDAR_ICS_URL
    if not env.get("CALENDAR_ICS_URL") and env.get("GCAL_ICS_URL"):
        env["CALENDAR_ICS_URL"] = env["GCAL_ICS_URL"]
    cfg = {k: os.environ.get(k, env.get(k, d)) for k, d in DEFAULTS.items()}
    cfg["_exists"] = os.path.exists(path)
    return cfg


def run_dir():
    """Ordner für Zustandsdateien zwischen den Diensten (/run/raspy, angelegt per tmpfiles.d
    von install.sh). Fehlt er (z. B. beim Ausprobieren am Laptop): Temp-Ordner."""
    d = os.environ.get("RASPY_RUN_DIR", "/run/raspy")
    if not os.access(d, os.W_OK):
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "raspy")
        os.makedirs(d, exist_ok=True)
    return d


def enabled(cfg, key):
    """Ja/Nein-Einstellung (1/true/yes/ja = an)."""
    return str(cfg.get(key, "0")).strip().lower() in ("1", "true", "yes", "ja")


def demo(cfg):
    return enabled(cfg, "DEMO")


# ───────────────────────── Hardware-Einstellungen prüfen ─────────────────────────
# Ein Tippfehler in config.env soll nie einen Dienst lahmlegen: ungültige Werte werden mit einer
# Warnung im Log durch den Standardwert ersetzt. problems() listet sie für ./install.sh --configure.

BUTTON_WIRINGS = ("gnd", "3v3", "active-low", "active-high")
ROTATIONS = ("0", "90", "180", "270")
# Bereiche: (kleinster, größter Wert)
LIMITS = {
    "DISPLAY_MARGIN": (0, 20),
    "BUTTON_TAB_HOLD": (0.5, 30), "BUTTON_POWER_HOLD": (1.5, 30), "BUTTON_COMBO_HOLD": (0.5, 30),
    "BUTTON_MIN_PRESS": (0, 1),
    "THERMAL_WARN": (30, 110), "THERMAL_CRIT": (40, 110), "THERMAL_PANIC": (40, 120),
    "THERMAL_ABORT": (30, 110), "THERMAL_CRIT_SECONDS": (0, 600), "THERMAL_COUNTDOWN": (5, 600),
    "FAN_MAX_RPM": (500, 50000),
}


def _warn(msg):
    print(f"WARNUNG config.env: {msg}", flush=True)


def _num(v):
    try:
        f = float(str(v).strip().replace(",", "."))
    except ValueError:
        return None
    return f if f == f and abs(f) != float("inf") else None


def number(cfg, key, warn=True):
    """Zahl aus der Einstellung; ungültig oder außerhalb von LIMITS -> Standardwert."""
    v = _num(cfg.get(key, ""))
    lo, hi = LIMITS.get(key, (float("-inf"), float("inf")))
    if v is None or not lo <= v <= hi:
        if warn:
            _warn(f"{key}={cfg.get(key)!r} ungültig (erlaubt {lo}–{hi}) – nehme {DEFAULTS[key]}")
        v = float(DEFAULTS[key])
    return v


def gpio(cfg, key):
    """BCM-Nummer der Taste oder None (leer = Taste nicht vorhanden)."""
    v = str(cfg.get(key, "")).strip()
    if v in ("", "-", "none", "off"):
        return None
    if v.isdigit() and 0 <= int(v) <= 27:
        return int(v)
    _warn(f"{key}={v!r} ist keine GPIO-Nummer (0–27) – nehme {DEFAULTS[key]}")
    return int(DEFAULTS[key])


def button_wiring(cfg):
    v = str(cfg.get("BUTTON_WIRING", "")).strip().lower()
    if v in BUTTON_WIRINGS:
        return v
    _warn(f"BUTTON_WIRING={v!r} unbekannt ({', '.join(BUTTON_WIRINGS)}) – nehme gnd")
    return "gnd"


def parse_fan_curve(s):
    """'45:0 50:55 …' -> [(45.0, 0), (50.0, 55), …]; ValueError, wenn etwas nicht passt."""
    pts = []
    for part in str(s).replace(",", " ").split():
        t, sep, p = part.partition(":")
        t, p = _num(t), _num(p)
        if not sep or t is None or p is None or not 0 <= p <= 255:
            raise ValueError(f"\"{part}\" – expected temperature:PWM with PWM 0–255, e.g. 60:70")
        pts.append((t, int(round(p))))
    if len(pts) < 2:
        raise ValueError("needs at least two points, e.g. 50:0 80:255")
    if any(b[0] <= a[0] for a, b in zip(pts, pts[1:])):
        raise ValueError("temperatures must go up from point to point")
    return pts


def fan_curve(cfg, warn=True):
    try:
        return parse_fan_curve(cfg.get("FAN_CURVE", ""))
    except ValueError as e:
        if warn:
            _warn(f"FAN_CURVE: {e} – nehme die Standardkurve")
        return parse_fan_curve(DEFAULTS["FAN_CURVE"])


def thermal_limits(cfg, warn=True):
    """Schwellen des Überhitzungsschutzes; passen sie nicht zusammen -> alle Standardwerte."""
    keys = ("THERMAL_WARN", "THERMAL_CRIT", "THERMAL_CRIT_SECONDS", "THERMAL_PANIC", "THERMAL_ABORT",
            "THERMAL_COUNTDOWN")
    t = {k: number(cfg, k, warn) for k in keys}
    if not t["THERMAL_ABORT"] < t["THERMAL_CRIT"] <= t["THERMAL_PANIC"]:
        if warn:
            _warn("THERMAL_ABORT < THERMAL_CRIT <= THERMAL_PANIC gilt nicht – nehme die Standardwerte")
        t = {k: float(DEFAULTS[k]) for k in keys}
    return t


def display_margin(cfg):
    return number(cfg, "DISPLAY_MARGIN", warn=False)


def screen_days(cfg):
    """SCREEN_ON_DAYS als Ziffern 1–7 (1 = Montag); ungültig -> Mo–Fr."""
    v = str(cfg.get("SCREEN_ON_DAYS", "")).strip()
    if v and all(c in "1234567" for c in v):
        return "".join(sorted(set(v)))
    return DEFAULTS["SCREEN_ON_DAYS"]


def problems(cfg):
    """Ungültige Hardware-Einstellungen als lesbare Liste (leer = alles gut)."""
    out = []
    for key in LIMITS:
        v = _num(cfg.get(key, ""))
        lo, hi = LIMITS[key]
        if v is None or not lo <= v <= hi:
            out.append(f"{key}={cfg.get(key)!r}: needs a number from {lo:g} to {hi:g}")
    for key in ("BUTTON_TAB_GPIO", "BUTTON_POWER_GPIO"):
        v = str(cfg.get(key, "")).strip()
        if v not in ("", "-") and not (v.isdigit() and 0 <= int(v) <= 27):
            out.append(f"{key}={v!r}: needs a BCM GPIO number 0–27 (or empty for no button)")
    tab, power = str(cfg.get("BUTTON_TAB_GPIO", "")).strip(), str(cfg.get("BUTTON_POWER_GPIO", "")).strip()
    if tab and tab == power:
        out.append(f"BUTTON_TAB_GPIO and BUTTON_POWER_GPIO are both {tab}")
    if str(cfg.get("BUTTON_WIRING", "")).strip().lower() not in BUTTON_WIRINGS:
        out.append(f"BUTTON_WIRING={cfg.get('BUTTON_WIRING')!r}: one of {', '.join(BUTTON_WIRINGS)}")
    try:
        parse_fan_curve(cfg.get("FAN_CURVE", ""))
    except ValueError as e:
        out.append(f"FAN_CURVE: {e}")
    t = {k: _num(cfg.get(k, "")) for k in ("THERMAL_ABORT", "THERMAL_CRIT", "THERMAL_PANIC")}
    if None not in t.values() and not t["THERMAL_ABORT"] < t["THERMAL_CRIT"] <= t["THERMAL_PANIC"]:
        out.append("THERMAL_ABORT < THERMAL_CRIT <= THERMAL_PANIC must hold")
    rot = str(cfg.get("DISPLAY_ROTATE", "")).strip()
    if rot and rot not in ROTATIONS:
        out.append(f"DISPLAY_ROTATE={rot!r}: 0, 90, 180 or 270 (or empty)")
    res = str(cfg.get("DISPLAY_RESOLUTION", "")).strip()
    if res and not valid_resolution(res):
        out.append(f"DISPLAY_RESOLUTION={res!r}: like 1920x1080 or 1280x720@60 (or empty)")
    days = str(cfg.get("SCREEN_ON_DAYS", "")).strip()
    if days and not all(c in "1234567" for c in days):
        out.append(f"SCREEN_ON_DAYS={days!r}: weekday digits, 1 = Monday … 7 = Sunday (e.g. 12345)")
    return out


def valid_resolution(v):
    return re.fullmatch(r"\d{3,5}x\d{3,5}(@\d{2,3}(\.\d+)?)?", str(v).strip()) is not None
