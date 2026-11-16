"""
Design-Themes: eigene Farben, Schriften, Hintergründe und Deko für alle Seiten.

Ein Theme ist ein Ordner mit mindestens einer theme.css (Anleitung: themes/README.md):

  themes/<id>/theme.json   Name, Beschreibung, optional Zeitraum ("season") und Seiten ("pages")
  themes/<id>/theme.css    überschreibt die CSS-Variablen der Seiten (--bg, --text, --surface …)
  themes/<id>/theme.js     optional: Effekte (z. B. Schneefall) – sparsam, der Pi soll kühl bleiben
  themes/<id>/…            Bilder, Schriften – in theme.css relativ einbinden: url("schnee.png")

Gesucht wird in themes/ im Projekt (kommt mit Updates) und in ~/.config/raspy/themes/
(eigene Themes, bleiben bei Updates erhalten; gleicher Name -> das eigene gewinnt).
Ordner, die mit "_" beginnen (z. B. themes/_vorlage), sind Vorlagen und werden nicht angeboten.

THEME in config.env:
  leer/"standard"  das eingebaute Design
  "auto"           ein Theme, dessen Zeitraum heute gilt (z. B. Weihnachten im Dezember), sonst Standard
  "<id>"           immer dieses Theme

Vorschau ohne Einstellung zu ändern: /heute?theme=<id>  (auch / und /thermal; ?theme=standard = ohne)

Die Seiten laden sich selbst neu, wenn sich das aktive Theme oder seine Dateien ändern
(Einstellung geändert, Zeitraum beginnt/endet, Theme bearbeitet).
"""
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date

import config

THEME_DIRS = [os.path.join(config.ROOT, "themes"), os.path.expanduser("~/.config/raspy/themes")]
PAGES = ("heute", "woche", "thermal")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SEASON_RE = re.compile(r"^\s*(\d{1,2})-(\d{1,2})\s*\.\.\s*(\d{1,2})-(\d{1,2})\s*$")
# Was aus einem Theme-Ordner ausgeliefert wird (nichts anderes – keine .py, .env, …)
SERVE_TYPES = {".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif",
               ".woff", ".woff2", ".ttf", ".otf"}
CACHE_SECONDS = 30
STANDARD = ("", "standard", "default", "none")


@dataclass
class Theme:
    id: str
    path: str
    name: str
    description: str = ""
    season: tuple = None                 # ((Monat, Tag), (Monat, Tag)) oder None
    pages: tuple = PAGES
    has_js: bool = False
    version: str = ""
    problems: list = field(default_factory=list)

    def key(self):
        """Ändert sich mit jeder Datei im Theme -> offene Seiten laden neu."""
        return f"{self.id}@{self.version}"


def parse_season(text):
    """"12-01..12-26" -> ((12, 1), (12, 26)); über den Jahreswechsel geht auch ("12-20..01-06")."""
    m = SEASON_RE.match(text or "")
    if not m:
        raise ValueError(f"Zeitraum '{text}' – Format: MM-TT..MM-TT, z. B. 12-01..12-26")
    a, b = (int(m.group(1)), int(m.group(2))), (int(m.group(3)), int(m.group(4)))
    for mon, day in (a, b):
        try:
            date(2024, mon, day)             # Schaltjahr: 02-29 ist erlaubt
        except ValueError:
            raise ValueError(f"Zeitraum '{text}': {day:02d}.{mon:02d}. gibt es nicht") from None
    return a, b


def in_season(season, d):
    if not season:
        return False
    a, b, x = season[0], season[1], (d.month, d.day)
    return a <= x <= b if a <= b else (x >= a or x <= b)


def _version(path):
    h = hashlib.sha1()
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for f in sorted(files):
            try:
                st = os.stat(os.path.join(root, f))
            except OSError:
                continue
            h.update(f"{os.path.relpath(os.path.join(root, f), path)}:{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()[:8]


def load_theme(path, tid=None):
    """Ein Theme-Ordner -> Theme, oder None (keine theme.css). Fehler in theme.json werden
    vermerkt (problems), das Theme bleibt nutzbar."""
    tid = tid or os.path.basename(os.path.normpath(path))
    if not os.path.isfile(os.path.join(path, "theme.css")):
        return None
    meta, problems = {}, []
    try:
        with open(os.path.join(path, "theme.json"), encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, dict):
            meta, problems = {}, ["theme.json ist kein Objekt {…}"]
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        problems.append(f"theme.json unlesbar: {e}")
    t = Theme(id=tid, path=path, name=str(meta.get("name") or tid), description=str(meta.get("description") or ""),
              has_js=os.path.isfile(os.path.join(path, "theme.js")), version=_version(path), problems=problems)
    if meta.get("season"):
        try:
            t.season = parse_season(str(meta["season"]))
        except ValueError as e:
            problems.append(str(e))
    if meta.get("pages") is not None:
        pages = meta["pages"] if isinstance(meta["pages"], list) else []
        t.pages = tuple(p for p in pages if p in PAGES)
        if len(t.pages) != len(pages):
            problems.append(f"pages: nur {', '.join(PAGES)} möglich")
    return t


_cache = {"at": 0, "themes": {}}


def available(fresh=False):
    """Alle Themes {id: Theme} – kurz zwischengespeichert, neue Ordner erscheinen nach ≤ 30 s."""
    if not fresh and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["themes"]
    found = {}
    for d in THEME_DIRS:                       # spätere Ordner (eigene Themes) gewinnen
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            if name.startswith("_") or not ID_RE.match(name):
                continue
            t = load_theme(os.path.join(d, name), name)
            if t:
                found[name] = t
    _cache.update(at=time.time(), themes=found)
    return found


def resolve(setting, today=None, preview=None):
    """Aktives Theme (oder None = Standard). preview (?theme=…) hat Vorrang."""
    themes = available()
    if preview is not None:
        p = preview.strip().lower()
        if p in STANDARD:
            return None
        if p in themes:
            return themes[p]
    s = (setting or "").strip().lower()
    if s in STANDARD:
        return None
    if s == "auto":
        today = today or date.today()
        for tid in sorted(themes):
            if in_season(themes[tid].season, today):
                return themes[tid]
        return None
    return themes.get(s)


def describe(setting):
    """Kurzbeschreibung der Einstellung für das Menü."""
    s = (setting or "").strip().lower()
    if s in STANDARD:
        return "standard"
    if s == "auto":
        t = resolve("auto")
        return "automatic (seasonal)" + (f" – now: {t.name}" if t else " – now: standard")
    t = available().get(s)
    return t.name if t else f"{s} (not found – using standard)"


# ───────────────────────── Flask ─────────────────────────
_cfg = {"THEME": ""}


def active(page):
    """Aktives Theme für eine Seite in der laufenden Anfrage (berücksichtigt ?theme=…)."""
    from flask import request
    t = resolve(_cfg.get("THEME", ""), preview=request.args.get("theme"))
    return t if t and page in t.pages else None


def key_for(page):
    """Kennung des aktiven Themes ("" = Standard) – die Seiten laden neu, wenn sie sich ändert."""
    t = active(page)
    return t.key() if t else ""


def theme_for(page):
    """Für die Templates: was eingebunden werden muss, oder None."""
    t = active(page)
    if not t:
        return None
    base = f"/themes/{t.id}"
    return {"id": t.id, "key": t.key(), "css": f"{base}/theme.css?v={t.version}",
            "js": f"{base}/theme.js?v={t.version}" if t.has_js else None}


def init(app, cfg):
    from flask import abort, send_from_directory
    _cfg.clear()
    _cfg.update(cfg)
    for t in available(fresh=True).values():
        for p in t.problems:
            print(f"[theme] {t.id}: {p}", flush=True)
    if (cfg.get("THEME") or "").strip().lower() not in STANDARD + ("auto",) and not resolve(cfg.get("THEME")):
        print(f"[theme] THEME={cfg.get('THEME')} nicht gefunden – Standard-Design", flush=True)

    @app.context_processor
    def inject():
        return {"theme_for": theme_for}

    @app.route("/themes/<tid>/<path:fname>")
    def theme_file(tid, fname):
        t = available().get(tid)
        if not t or os.path.splitext(fname)[1].lower() not in SERVE_TYPES:
            abort(404)
        # send_from_directory lässt nichts außerhalb des Theme-Ordners heraus (../ usw.)
        return send_from_directory(t.path, fname, max_age=3600)
