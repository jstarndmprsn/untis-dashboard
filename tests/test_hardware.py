"""Hardware-Einstellungen: Prüfung/Standardwerte (config.py), Menü-Hilfen (configure.py),
Thermal-Schwellen, Seiten-Skalierung und die Shell-Skripte mit nachgebauten X-Werkzeugen."""
import argparse
import importlib.util
import os
import stat
import subprocess

import pytest

import config
import configure
import dashboard
import server
import thermal

ROOT = config.ROOT


def cfg(**kw):
    c = dict(config.DEFAULTS)
    c.update(kw)
    return c


# ───────────────────────── config.py ─────────────────────────
def test_defaults_have_no_problems():
    assert config.problems(cfg()) == []


def test_number_falls_back_to_default(capsys):
    assert config.number(cfg(BUTTON_POWER_HOLD="6,5"), "BUTTON_POWER_HOLD") == 6.5
    assert config.number(cfg(BUTTON_POWER_HOLD="abc"), "BUTTON_POWER_HOLD") == 4
    assert config.number(cfg(BUTTON_POWER_HOLD="0.2"), "BUTTON_POWER_HOLD") == 4    # unter dem Minimum
    assert "BUTTON_POWER_HOLD" in capsys.readouterr().out


def test_gpio_empty_means_no_button():
    assert config.gpio(cfg(BUTTON_TAB_GPIO=""), "BUTTON_TAB_GPIO") is None
    assert config.gpio(cfg(BUTTON_TAB_GPIO="22"), "BUTTON_TAB_GPIO") == 22
    assert config.gpio(cfg(BUTTON_TAB_GPIO="99"), "BUTTON_TAB_GPIO") == 17


def test_fan_curve():
    assert config.parse_fan_curve("40:0, 60:128 75:255") == [(40, 0), (60, 128), (75, 255)]
    for bad in ("60:0 50:255", "50:300 60:255", "50", "50:0"):
        with pytest.raises(ValueError):
            config.parse_fan_curve(bad)
    assert config.fan_curve(cfg(FAN_CURVE="kaputt"), warn=False) == config.parse_fan_curve(config.DEFAULTS["FAN_CURVE"])


def test_thermal_limits_must_be_in_order():
    t = config.thermal_limits(cfg(THERMAL_CRIT="80", THERMAL_ABORT="75"), warn=False)
    assert (t["THERMAL_CRIT"], t["THERMAL_ABORT"]) == (80, 75)
    t = config.thermal_limits(cfg(THERMAL_CRIT="80", THERMAL_ABORT="85"), warn=False)   # Abbruch über kritisch
    assert (t["THERMAL_CRIT"], t["THERMAL_ABORT"]) == (85, 82)


def test_problems_lists_each_bad_value():
    bad = cfg(DISPLAY_ROTATE="45", DISPLAY_RESOLUTION="4k", DISPLAY_MARGIN="50", BUTTON_WIRING="pullup",
              BUTTON_TAB_GPIO="27", FAN_CURVE="x", SCREEN_ON_DAYS="Mo-Fr")
    text = "\n".join(config.problems(bad))
    for key in ("DISPLAY_ROTATE", "DISPLAY_RESOLUTION", "DISPLAY_MARGIN", "BUTTON_WIRING",
                "both 27", "FAN_CURVE", "SCREEN_ON_DAYS"):
        assert key in text


def test_resolution_format():
    assert config.valid_resolution("1920x1080") and config.valid_resolution("1280x720@59.94")
    assert not config.valid_resolution("1920*1080") and not config.valid_resolution("hd")


def test_screen_days():
    assert config.screen_days(cfg(SCREEN_ON_DAYS="531")) == "135"
    assert config.screen_days(cfg(SCREEN_ON_DAYS="Mo-Fr")) == "12345"


# ───────────────────────── configure.py ─────────────────────────
@pytest.mark.parametrize("text,digits", [
    ("Mo-Fr", "12345"), ("mon - sat", "123456"), ("Mo,Mi,Fr", "135"), ("Mon Wed Fri", "135"),
    ("täglich", "1234567"), ("Fr-Mo", "1567"), ("67", "67"),
])
def test_parse_days(text, digits):
    assert configure.parse_days(text) == digits


def test_parse_days_rejects_nonsense():
    with pytest.raises(ValueError):
        configure.parse_days("Montag bis Freitag")


def test_days_text():
    assert configure.days_text("12345") == "Mon–Fri"
    assert configure.days_text("135") == "Mon, Wed, Fri"
    assert configure.days_text("1234567") == "every day"


def test_written_config_has_hardware_sections(tmp_path):
    path = str(tmp_path / "config.env")
    c = cfg(DISPLAY_ROTATE="180", BUTTON_WIRING="3v3", FAN_CURVE="40:0 70:255")
    configure.write_config(c, path)
    back = config.load_env(path)
    assert back["DISPLAY_ROTATE"] == "180" and back["BUTTON_WIRING"] == "3v3"
    assert back["FAN_CURVE"] == "40:0 70:255" and back["SCREEN_ON_DAYS"] == "12345"
    assert set(config.DEFAULTS) - {"DEMO"} <= set(back)     # jede Einstellung landet in der Datei


# ───────────────────────── Thermal ─────────────────────────
def test_thermal_display_limits_follow_config():
    lim = thermal.display_limits(cfg(THERMAL_WARN="65", FAN_CURVE="40:0 60:150 70:220 75:255", FAN_MAX_RPM="5000"))
    assert lim == {"warn": 65, "fan_emergency": 70, "fan_max_rpm": 5000}


def test_thermal_guard_reads_config(monkeypatch):
    monkeypatch.setenv("THERMAL_CRIT", "78")
    monkeypatch.setenv("THERMAL_ABORT", "72")
    monkeypatch.setenv("FAN_CURVE", "40:0 60:100 70:200")
    spec = importlib.util.spec_from_file_location("thermal_guard_test", os.path.join(ROOT, "device", "thermal_guard.py"))
    tg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tg)
    assert (tg.CRIT_TEMP, tg.ABORT_TEMP, tg.PANIC_TEMP) == (78, 72, 90)
    assert tg.fan_target(30) == 0 and tg.fan_target(50) == 50 and tg.fan_target(99) == 200


# ───────────────────────── Seiten skalieren ─────────────────────────
def test_pages_load_fit_script_with_margin():
    c = server.load_config(argparse.Namespace(demo=True, no_calendar=False, port=None))
    c["DISPLAY_MARGIN"] = "3"
    app = server.create_app(c)
    for src in dashboard._sources.values():
        src.run()
    client = app.test_client()
    for path in ("/heute", "/", "/thermal"):
        html = client.get(path).get_data(as_text=True)
        assert 'data-margin="3"' in html and "/static/fit.js" in html and "/static/fit.css" in html
    assert client.get("/static/fit.js").status_code == 200


def test_page_css_has_no_fixed_screen_size():
    for name in ("heute.css", "thermal.css", "woche.css"):
        css = open(os.path.join(ROOT, "app", "static", name), encoding="utf-8").read()
        assert "1920px" not in css and "100vw" not in css and "100vh" not in css, name


# ───────────────────────── Shell-Skripte mit nachgebauten Werkzeugen ─────────────────────────
XRANDR = """#!/bin/sh
if [ "$1" = "--query" ]; then
  echo "Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 7680 x 7680"
  echo "HDMI-1 disconnected (normal left inverted right x axis y axis)"
  echo "HDMI-2 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 600mm x 340mm"
  echo "   1920x1080     60.00*+  50.00"
  echo "   1280x720      60.00"
  exit 0
fi
echo "$*" >> "$LOG"
"""


def fake_bin(tmp_path, name, script):
    p = tmp_path / "bin" / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(script)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def run_script(tmp_path, script, settings, *args):
    conf = tmp_path / "config.env"
    conf.write_text("".join(f"{k}={v}\n" for k, v in settings.items()))
    log = tmp_path / "calls.log"
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}", "RASPY_CONFIG": str(conf),
           "LOG": str(log), "DISPLAY": ":99", "HOME": str(tmp_path)}
    out = subprocess.run(["sh", os.path.join(ROOT, "device", script), *args], env=env,
                         capture_output=True, text=True, timeout=10)
    return out, (log.read_text() if log.exists() else "")


def test_display_sh_applies_rotation_and_resolution(tmp_path):
    fake_bin(tmp_path, "xrandr", XRANDR)
    _, calls = run_script(tmp_path, "display.sh", {"DISPLAY_ROTATE": "90", "DISPLAY_RESOLUTION": "1280x720@60"})
    assert calls.strip() == "--output HDMI-2 --rotate right --mode 1280x720 --rate 60"


def test_display_sh_leaves_screen_alone_by_default(tmp_path):
    fake_bin(tmp_path, "xrandr", XRANDR)
    _, calls = run_script(tmp_path, "display.sh", {})
    assert calls == ""


def test_display_sh_ignores_invalid_values(tmp_path):
    fake_bin(tmp_path, "xrandr", XRANDR)
    out, calls = run_script(tmp_path, "display.sh", {"DISPLAY_ROTATE": "45", "DISPLAY_OUTPUT": "HDMI-1"})
    assert calls.strip() == "--output HDMI-1" and "ungültig" in out.stdout


def test_display_sh_lists_modes(tmp_path):
    fake_bin(tmp_path, "xrandr", XRANDR)
    out, _ = run_script(tmp_path, "display.sh", {}, "--list")
    assert out.stdout.startswith("Output HDMI-2:") and "1280x720" in out.stdout and "HDMI-1" not in out.stdout


@pytest.mark.parametrize("days,weekday,expect", [
    ("12345", "6", "dpms 600 600 600"),     # Samstag, Mo–Fr -> Leerlauf-Abschaltung
    ("123456", "6", "dpms 0 0 0"),          # Samstag ist Schultag -> immer an
    ("135", "2", "dpms 600 600 600"),       # Dienstag kein Schultag
])
def test_screen_schedule_days(tmp_path, days, weekday, expect):
    fake_bin(tmp_path, "xset", '#!/bin/sh\n[ "$1" = q ] && exit 0\necho "$*" >> "$LOG"\n')
    fake_bin(tmp_path, "date", f'#!/bin/sh\ncase "$1" in +%H%M) echo 1000 ;; +%u) echo {weekday} ;; esac\n')
    _, calls = run_script(tmp_path, "screen_schedule.sh", {"SCREEN_ON_DAYS": days})
    assert expect in calls


def test_example_config_lists_every_setting():
    example = config.load_env(os.path.join(ROOT, "config.example.env"))
    assert set(config.DEFAULTS) - {"DEMO"} == set(example)
    for key in example:                       # Beispiel = Standardwerte
        assert example[key] == config.DEFAULTS[key], key


def make_client(margin):
    c = server.load_config(argparse.Namespace(demo=True, no_calendar=False, port=None))
    c["DISPLAY_MARGIN"] = margin
    app = server.create_app(c)
    for src in dashboard._sources.values():
        src.run()
    return app.test_client()


def test_pages_reload_when_display_settings_change():
    a, b = make_client("0"), make_client("4")
    for client in (a, b):          # Seite und Daten tragen dieselbe Kennung -> kein Dauer-Neuladen
        v = client.get("/api/heute").headers["X-Version"]
        assert f'data-version="{v}"' in client.get("/heute").get_data(as_text=True)
        assert client.get("/api/thermal").headers["X-Version"] == v
        assert client.get("/content").headers["X-Version"] == v
    # anderer Rand -> andere Kennung -> die offene Seite lädt neu
    assert a.get("/api/heute").headers["X-Version"] != b.get("/api/heute").headers["X-Version"]
