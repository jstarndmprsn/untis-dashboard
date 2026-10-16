"""
Webserver für die Klassen-Anzeige (Port 5000, PORT in config.env).

  /         Wochen-Stundenplan mit Vertretungen (Tabelle: week.py, Daten: dashboard.py)
  /heute    Tagesansicht (dashboard.py)
  /thermal  System-/Temperatur-Dashboard (thermal.py)

Standardmäßig nur vom Pi selbst erreichbar (127.0.0.1) – der Kiosk braucht nicht mehr.
Von anderen Geräten im Netz nur mit LAN_ACCESS=1 in config.env.

Einstellungen: config.env im Projektordner (anlegen mit `python3 app/configure.py`).
Ausprobieren ohne Untis-Konto:  python3 app/server.py --demo   [--no-calendar] [--port 5077]
"""
import argparse
import hashlib
import os
from datetime import date
from html import escape

from flask import Flask, render_template

import config
import dashboard
import thermal
import untis
import week

APP_DIR = os.path.dirname(os.path.abspath(__file__))

LOADING = """
                <div class='error' style='color: #888;'>
                    <div style='text-align: center; font-size: 0.8em; font-weight: normal;'>{}</div>
                </div>"""
FAILED = """
                <div class='error'>
                    <div style='text-align: center;'>
                        <div style='font-size: 3em; margin-bottom: 20px;'>⚠️</div>
                        <div style='font-size: 1.5em; margin-bottom: 10px;'>{}</div>
                        <div style='font-size: 0.8em; color: #aaa;'>{}</div>
                        <div style='font-size: 0.6em; color: #666; margin-top: 20px;'>Wird automatisch aktualisiert...</div>
                    </div>
                </div>"""


def asset_version():
    """Kennung des ausgelieferten Stands: ändert sich mit jedem Update der Wochenansicht,
    dann lädt der Browser die Seite einmal komplett neu (statt VERSION von Hand zu erhöhen)."""
    h = hashlib.sha1()
    for rel in ("week.py", "server.py", "templates/woche.html", "static/woche.css", "static/woche.js"):
        try:
            with open(os.path.join(APP_DIR, rel), "rb") as f:
                h.update(f.read())
        except OSError:
            pass
    return h.hexdigest()[:8]


def render_content(master):
    """Nur der wechselnde Teil der Seite (Tabelle bzw. Meldung).
    Enthält bewusst keine Uhrzeit -> ändert sich nur, wenn sich Daten ändern."""
    data, error, fetched_at = dashboard.untis_snapshot()
    status = dashboard.untis_status()

    if data is None:   # noch nie erfolgreich geladen
        if status["auth"]:
            return FAILED.format("Untis-Login abgelehnt",
                                 "Benutzername/Passwort prüfen: ./install.sh --configure "
                                 "(neuer Versuch alle 30 Minuten)")
        if not status["error"]:      # Startphase oder noch kein Fehler
            return LOADING.format("Stundenplan wird geladen …")
        return FAILED.format("Stundenplan konnte nicht geladen werden", escape(error))

    stale = None
    if status["auth"]:
        stale = f"Untis-Login abgelehnt – Passwort prüfen · Stand {fetched_at.strftime('%H:%M')}"
    elif error and fetched_at:
        stale = f"Untis nicht erreichbar – Stand {fetched_at.strftime('%H:%M')}"
    today = date.today()
    html = week.render_table(data["lessons"], master.periods, dashboard.week_monday(today), today, stale)
    return html if html is not None else LOADING.format("Keine Stunden in dieser Woche")


def create_app(cfg):
    app = Flask(__name__)
    # Stammdaten (Lehrer, Räume, Fächer, Farben, Klassen-ID, Zeitraster) und die Stunden selbst
    # holt dashboard.py – beide Ansichten zeigen dieselben Daten
    master = untis.MasterData(cfg)
    dashboard.init(app, cfg, master)
    thermal.init(app)                   # Thermal-Dashboard /thermal (beide Tasten 2 s halten)
    version = asset_version()

    @app.route("/content")
    def content():
        return render_content(master), 200, {"X-Version": version, "Cache-Control": "no-store"}

    @app.route("/")
    def index():
        return render_template("woche.html", content=render_content(master), version=version)

    return app


def load_config(args):
    cfg = config.load()
    if args.demo:
        cfg["DEMO"] = "1"
    if config.demo(cfg):
        cfg["UNTIS_CLASS"] = cfg["UNTIS_CLASS"] or "9a"
        if not args.no_calendar and not cfg["CALENDAR_ICS_URL"]:
            cfg["CALENDAR_ICS_URL"] = "demo"
        if not cfg["WEATHER_LAT"]:
            cfg["WEATHER_LAT"] = cfg["WEATHER_LON"] = "demo"
    elif not cfg["_exists"]:
        print(f"WARNUNG: {config.CONFIG_FILE} nicht gefunden – zuerst `python3 app/configure.py` "
              f"ausführen (oder mit --demo starten)", flush=True)
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="erfundene Beispieldaten statt Untis")
    ap.add_argument("--no-calendar", action="store_true", help="Demo ohne Kalender-Spalte")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()

    cfg = load_config(args)
    app = create_app(cfg)
    if config.demo(cfg):
        print("DEMO-Modus: erfundene Beispieldaten (kein Untis-Login)", flush=True)
    dashboard.start()
    thermal.start()

    host = "0.0.0.0" if config.enabled(cfg, "LAN_ACCESS") else "127.0.0.1"
    port = args.port or int(cfg["PORT"] or 5000)
    print(f"Webserver auf http://{host}:{port}"
          + ("" if host == "0.0.0.0" else " (nur lokal – LAN_ACCESS=1 für andere Geräte)"), flush=True)
    try:
        from waitress import serve
    except ImportError:   # ältere Installation ohne waitress -> Flask-Entwicklungsserver
        app.run(host=host, port=port, debug=False)
    else:
        serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    main()
