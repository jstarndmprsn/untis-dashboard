"""Design-Themes: Ordner finden, Zeiträume, Auswahl, Einbindung in die Seiten, sichere Auslieferung."""
import argparse
import json
import os
import re
from datetime import date

import pytest

import themes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_theme(base, tid, meta=None, css=":root { --bg: #123456; }", js=None, files=None):
    d = base / tid
    d.mkdir(parents=True)
    if css is not None:
        (d / "theme.css").write_text(css)
    if meta is not None:
        (d / "theme.json").write_text(meta if isinstance(meta, str) else json.dumps(meta))
    if js:
        (d / "theme.js").write_text(js)
    for name, data in (files or {}).items():
        (d / name).write_bytes(data)
    return d


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    shipped, local = tmp_path / "themes", tmp_path / "local"
    shipped.mkdir()
    local.mkdir()
    monkeypatch.setattr(themes, "THEME_DIRS", [str(shipped), str(local)])
    monkeypatch.setitem(themes._cache, "at", 0)
    return shipped, local


# ───────────────────────── Zeiträume ─────────────────────────
def test_season_parse_and_wraparound():
    xmas = themes.parse_season("12-01..12-26")
    assert themes.in_season(xmas, date(2026, 12, 24)) and not themes.in_season(xmas, date(2026, 12, 27))
    ny = themes.parse_season("12-20 .. 01-06")
    assert themes.in_season(ny, date(2026, 12, 31)) and themes.in_season(ny, date(2027, 1, 6))
    assert not themes.in_season(ny, date(2027, 1, 7))
    assert themes.parse_season("02-29..03-01")                        # Schaltjahr erlaubt
    for bad in ("12-01-12-26", "13-01..12-26", "02-30..03-01", "", "Dezember"):
        with pytest.raises(ValueError):
            themes.parse_season(bad)
    assert not themes.in_season(None, date(2026, 12, 24))


# ───────────────────────── Finden ─────────────────────────
def test_available_finds_valid_themes_only(dirs):
    shipped, local = dirs
    make_theme(shipped, "winter", {"name": "Winter", "season": "12-01..02-28"})
    make_theme(shipped, "_template")                                  # Vorlage: nicht anbieten
    make_theme(shipped, "Gross")                                      # ungültige id
    make_theme(shipped, "ohne-css", css=None)
    make_theme(shipped, "kaputt", meta="{nicht json")
    found = themes.available(fresh=True)
    assert sorted(found) == ["kaputt", "winter"]
    assert found["winter"].name == "Winter" and found["winter"].season == ((12, 1), (2, 28))
    assert found["kaputt"].name == "kaputt" and found["kaputt"].problems     # nutzbar, Fehler vermerkt


def test_local_theme_wins_over_shipped(dirs):
    shipped, local = dirs
    make_theme(shipped, "schule", {"name": "Mitgeliefert"})
    make_theme(local, "schule", {"name": "Eigenes"})
    assert themes.available(fresh=True)["schule"].name == "Eigenes"


def test_bad_meta_is_reported_not_fatal(dirs):
    shipped, _ = dirs
    make_theme(shipped, "x", {"season": "31-12..01-01", "pages": ["heute", "kueche"]})
    t = themes.available(fresh=True)["x"]
    assert t.season is None and t.pages == ("heute",) and len(t.problems) == 2


def test_version_changes_with_files(dirs):
    shipped, _ = dirs
    d = make_theme(shipped, "v")
    v1 = themes.load_theme(str(d)).version
    (d / "theme.css").write_text(":root { --bg: #000; } /* länger */")
    assert themes.load_theme(str(d)).version != v1


# ───────────────────────── Auswahl ─────────────────────────
def test_resolve(dirs):
    shipped, _ = dirs
    make_theme(shipped, "weihnachten", {"season": "12-01..12-26"})
    make_theme(shipped, "schule")
    themes.available(fresh=True)
    r = lambda s, d=date(2026, 12, 10), p=None: (lambda t: t.id if t else None)(themes.resolve(s, d, p))  # noqa: E731
    assert r("") is None and r("standard") is None
    assert r("schule") == "schule" and r("gibt-es-nicht") is None
    assert r("auto") == "weihnachten" and r("auto", date(2026, 7, 1)) is None
    assert r("schule", p="weihnachten") == "weihnachten"              # Vorschau hat Vorrang
    assert r("schule", p="standard") is None
    assert r("schule", p="unbekannt") == "schule"                     # unbekannte Vorschau ignorieren


# ───────────────────────── Seiten ─────────────────────────
@pytest.fixture
def client(dirs, monkeypatch):
    import server
    shipped, _ = dirs
    make_theme(shipped, "probe", {"name": "Probe"}, js="document.body.dataset.ok = 1;",
               files={"bild.png": b"\x89PNG\r\n", "notiz.txt": b"geheim", "skript.py": b"print(1)"})
    make_theme(shipped, "nur-heute", {"pages": ["heute"]})
    themes.available(fresh=True)

    def make(theme):
        cfg = server.load_config(argparse.Namespace(demo=True, no_calendar=False, port=None, theme=theme))
        return server.create_app(cfg).test_client()
    return make


def test_no_theme_means_no_extra_markup(client):
    c = client("")
    for url in ("/heute", "/", "/thermal"):
        html = c.get(url).get_data(as_text=True)
        assert "/themes/" not in html and "theme-back" not in html and "theme-front" not in html
    assert c.get("/api/heute").get_json()["theme"] == ""


def test_theme_is_included_everywhere(client):
    c = client("probe")
    for url, page in (("/heute", "heute"), ("/", "woche"), ("/thermal", "thermal")):
        html = c.get(url).get_data(as_text=True)
        assert re.search(r'href="/themes/probe/theme\.css\?v=\w+"', html), url
        assert '/themes/probe/theme.js' in html and 'class="theme-front"' in html
        assert f"page-{page}" in html and "theme-probe" in html
    key = c.get("/api/heute").get_json()["theme"]
    assert key.startswith("probe@") and f'data-theme="{key}"' in c.get("/heute").get_data(as_text=True)


def test_week_view_does_not_reload_forever(client):
    """data-version der Seite = X-Version von /content, sonst lädt die Wochenansicht ständig neu."""
    for theme in ("", "probe"):
        c = client(theme)
        page = c.get("/").get_data(as_text=True)
        dv = re.search(r'data-version="([^"]+)"', page).group(1)
        assert c.get("/content").headers["X-Version"] == dv
        assert c.get("/content?theme=standard").headers["X-Version"] == dv.split("-probe@")[0]


def test_theme_limited_to_pages(client):
    c = client("nur-heute")
    assert "/themes/nur-heute/" in c.get("/heute").get_data(as_text=True)
    assert "/themes/" not in c.get("/").get_data(as_text=True)


def test_preview_parameter(client):
    c = client("")
    assert "/themes/probe/theme.css" in c.get("/heute?theme=probe").get_data(as_text=True)
    assert c.get("/api/heute?theme=probe").get_json()["theme"].startswith("probe@")
    assert "/themes/" not in client("probe").get("/heute?theme=standard").get_data(as_text=True)


def test_theme_files_served_safely(client):
    c = client("probe")
    assert c.get("/themes/probe/theme.css").status_code == 200
    assert c.get("/themes/probe/bild.png").status_code == 200
    for bad in ("/themes/probe/notiz.txt", "/themes/probe/skript.py", "/themes/probe/theme.json",
                "/themes/probe/../../../app/config.py", "/themes/probe/..%2f..%2fconfig.env",
                "/themes/gibt-es-nicht/theme.css", "/themes/_template/theme.css"):
        assert c.get(bad).status_code == 404, bad


# ───────────────────────── Themebarkeit ─────────────────────────
@pytest.mark.parametrize("name", ["heute.css", "woche.css", "thermal.css"])
def test_page_colours_are_variables(name):
    """Farben stehen nur im :root-Block oben (als Variablen) – sonst kann ein Theme sie nicht ändern.
    Erlaubt: schwarz in Masken (#000) und weiße/schwarze Licht-/Schatteneffekte."""
    css = open(os.path.join(ROOT, "app", "static", name), encoding="utf-8").read()
    root_end = css.index("}", css.index(":root"))
    rest = re.sub(r"/\*.*?\*/", "", css[root_end:], flags=re.S)
    hexes = [h for h in re.findall(r"#[0-9a-fA-F]{3,8}\b", rest) if h.lower() != "#000"]
    assert not hexes, hexes
    rgbs = [m for m in re.findall(r"rgba?\(([^)]*)\)", rest)
            if not re.match(r"\s*(255,\s*255,\s*255|0,\s*0,\s*0)\s*,", m)]
    assert not rgbs, rgbs


def test_template_is_complete_and_valid():
    t = themes.load_theme(os.path.join(ROOT, "themes", "_template"))
    assert t and not t.problems and t.season and t.has_js
    css = open(os.path.join(ROOT, "themes", "_template", "theme.css"), encoding="utf-8").read()
    # jede Variable aus den Seiten steht (auskommentiert) in der Vorlage
    for name in ("heute.css", "woche.css", "thermal.css"):
        page = open(os.path.join(ROOT, "app", "static", name), encoding="utf-8").read()
        root = page[page.index(":root"):page.index("}", page.index(":root"))]
        for var in re.findall(r"(--[a-z-]+):", root):
            assert var in css, f"{var} ({name}) fehlt in themes/_template/theme.css"


def test_shipped_themes_are_valid():
    """Jedes mitgelieferte Theme lässt sich laden und hat keine Fehler in theme.json."""
    base = os.path.join(ROOT, "themes")
    for name in sorted(os.listdir(base)):
        if os.path.isdir(os.path.join(base, name)) and not name.startswith("_"):
            assert themes.ID_RE.match(name), name
            t = themes.load_theme(os.path.join(base, name))
            assert t and not t.problems, (name, t and t.problems)
