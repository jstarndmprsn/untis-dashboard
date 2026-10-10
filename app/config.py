"""
Einstellungen aus config.env (im Projektordner, wird von configure.py geschrieben).

Format: KEY=VALUE pro Zeile, # für Kommentare. Werte dürfen in "..." stehen
(nötig bei Leerzeichen oder Sonderzeichen; \\ und \" werden dann entschlüsselt).
"""
import os

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
    "SCREEN_ON_FROM": "0730",    # Mo–Fr in dieser Zeit bleibt der Bildschirm an
    "SCREEN_ON_UNTIL": "1600",
    "SCREEN_IDLE_OFF": "600",    # sonst nach so vielen Sekunden aus
    "BUTTON_TAB_GPIO": "17",
    "BUTTON_POWER_GPIO": "27",
    "PORT": "5000",
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


def demo(cfg):
    return str(cfg.get("DEMO", "0")).lower() in ("1", "true", "yes", "ja")
