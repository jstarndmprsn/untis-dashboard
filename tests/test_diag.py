"""Diagnose-Protokoll: Zustandswechsel, Neustart-Erkennung, Kernel-Meldungen, Bereinigen, Auswertung."""
import ast
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "diag"))
import raspy_diag as rd  # noqa: E402

BOOT_A = "aaaaaaaa-0000-0000-0000-000000000000"
BOOT_B = "bbbbbbbb-0000-0000-0000-000000000000"


class FakeProbes:
    """Alle Messwerte von Hand setzbar – keine echten /proc-, vcgencmd- oder journalctl-Aufrufe."""

    def __init__(self, boot=BOOT_A, up=1000.0):
        self._boot, self.up = boot, up
        self.net = {"up": True, "iface": "wlan0", "signal": -55}
        self.thr = 0
        self.temp = 50.0
        self.disk_ = {"free_mb": 10000, "free_pct": 60.0}
        self.synced = True
        self.units_ = {"raspy-web.service": {"active": "active", "enabled": True, "restarts": 0},
                       "lightdm.service": {"active": "active", "enabled": True, "restarts": 0}}
        self.kiosk = True
        self.web = None
        self.klines = []
        self.stopping = False

    def boot_id(self): return self._boot
    def uptime(self): return self.up
    def network(self): return dict(self.net)
    def throttled(self): return self.thr
    def temperature(self): return self.temp
    def disk(self): return self.disk_
    def clock_synced(self): return self.synced
    def units(self, names=None): return {k: dict(v) for k, v in self.units_.items() if not names or k in names}
    def kiosk_running(self): return self.kiosk
    def web_sources(self): return self.web
    def kernel(self, since_us): return [(us, m) for us, m in self.klines if us > since_us]
    def system_stopping(self): return self.stopping


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def no_redact(text):
    return text


@pytest.fixture
def env(tmp_path):
    store = rd.Store(str(tmp_path))
    clock = Clock()
    probes = FakeProbes()
    return store, clock, probes


def monitor(store, probes, clock, start=True):
    m = rd.Monitor(store, probes, redact=no_redact, now=clock)
    if start:
        m.start()
    return m


def advance(m, clock, probes, seconds, step=10):
    for _ in range(int(seconds // step)):
        clock.t += step
        probes.up += step
        m.step()


def kinds(store):
    return [(e["k"], e.get("state")) for e in store.events()]


# ───────────────────────── Zustände ─────────────────────────
def test_cond_needs_confirmation_and_reports_duration():
    c = rd.Cond("net", confirm=20, clear=10)
    assert c.update(True, 0, "weg", "da") is None
    assert c.update(False, 5, "weg", "da") is None          # kurzer Aussetzer: kein Eintrag
    assert c.update(True, 10, "weg", "da") is None
    ev = c.update(True, 30, "weg", "da")
    assert ev["state"] == "bad" and ev["t"] == 10            # Beginn = erster Zeitpunkt, nicht Bestätigung
    assert c.update(None, 40) is None                        # unbekannt ändert nichts
    assert c.update(False, 100, "weg", "da") is None
    ev = c.update(False, 110, "weg", "da")
    assert ev["state"] == "good" and ev["dur"] == 90 and "nach 90 s" in ev["msg"]


def test_network_drop_and_return(env):
    store, clock, probes = env
    m = monitor(store, probes, clock)
    advance(m, clock, probes, 60)
    probes.net = {"up": False, "iface": None, "signal": None}
    advance(m, clock, probes, 10)                            # 10 s: noch kein Eintrag
    assert ("net", "bad") not in kinds(store)
    advance(m, clock, probes, 120)
    probes.net = {"up": True, "iface": "wlan0", "signal": -60}
    advance(m, clock, probes, 30)
    evs = [e for e in store.events() if e["k"] == "net"]
    assert [e["state"] for e in evs] == ["bad", "good"]
    assert evs[1]["dur"] >= 120
    assert rd.open_problems(store.events()) == []


def test_open_problem_shown_until_resolved(env):
    store, clock, probes = env
    m = monitor(store, probes, clock)
    probes.temp = 84.0
    advance(m, clock, probes, 60)
    op = rd.open_problems(store.events())
    assert [e["k"] for e in op] == ["temp"]
    probes.temp = 82.0                                       # Hysterese: erst unter 75 °C vorbei
    advance(m, clock, probes, 30)
    assert rd.open_problems(store.events())
    probes.temp = 70.0
    advance(m, clock, probes, 20)
    assert rd.open_problems(store.events()) == []
    end = [e for e in store.events() if e["k"] == "temp"][-1]
    assert "höchstens 84.0 °C" in end["msg"]


def test_undervoltage_now_and_short_sticky(env):
    store, clock, probes = env
    m = monitor(store, probes, clock)
    advance(m, clock, probes, 10)
    probes.thr = 0x10000                                     # nur "seit Start aufgetreten": kurz, schon vorbei
    advance(m, clock, probes, 10)
    evs = [e for e in store.events() if e["k"] == "undervolt"]
    assert len(evs) == 1 and "kurz" in evs[0]["msg"] and evs[0]["lvl"] == "err"
    probes.thr = 0x10001                                     # jetzt dauerhaft
    advance(m, clock, probes, 10)
    probes.thr = 0x10000
    advance(m, clock, probes, 70)
    states = [e.get("state") for e in store.events() if e["k"] == "undervolt"]
    assert states == [None, "bad", "good"]


def test_untis_error_classified_and_auth(env):
    store, clock, probes = env
    m = monitor(store, probes, clock)

    def web(err=None, auth=False, loaded=True):
        probes.web = {"updated": clock.t, "sources": {"untis": {
            "ok": err is None and not auth, "error": err, "auth": auth, "loaded": loaded, "since": "10:00"}}}

    web()
    advance(m, clock, probes, 30)
    web("ConnectionError: HTTPSConnectionPool: Max retries exceeded (NameResolutionError)")
    advance(m, clock, probes, 60)
    assert ("src:untis", "bad") not in kinds(store)          # erst nach 90 s
    advance(m, clock, probes, 60)
    ev = [e for e in store.events() if e["k"] == "src:untis"][-1]
    assert ev["state"] == "bad" and "DNS" in ev["msg"]
    web()
    advance(m, clock, probes, 10)
    assert [e for e in store.events() if e["k"] == "src:untis"][-1]["state"] == "good"
    web("RPC error", auth=True)
    advance(m, clock, probes, 10)
    assert ("auth", "bad") in kinds(store)


def test_web_server_hang_detected(env):
    store, clock, probes = env
    m = monitor(store, probes, clock)
    probes.web = {"updated": clock.t, "sources": {}}
    advance(m, clock, probes, 700)
    assert ("web_stale", "bad") in kinds(store)


def test_service_crash_and_kiosk(env):
    store, clock, probes = env
    m = monitor(store, probes, clock)
    advance(m, clock, probes, 30)
    probes.units_["raspy-web.service"]["restarts"] = 2
    probes.kiosk = False
    advance(m, clock, probes, 120)
    ev = [e for e in store.events() if e["k"] == "svc"]
    assert len(ev) == 1 and "raspy-web.service" in ev[0]["msg"] and "2×" in ev[0]["msg"]
    assert ("kiosk", "bad") in kinds(store)


def test_kiosk_ignored_right_after_boot(env):
    store, clock, probes = env
    probes.up = 20
    probes.kiosk = False
    m = monitor(store, probes, clock)
    advance(m, clock, probes, 120)
    assert ("kiosk", "bad") not in kinds(store)


# ───────────────────────── Start / Strom weg ─────────────────────────
def test_unclean_previous_boot_detected(env):
    store, clock, probes = env
    m = monitor(store, probes, clock)
    advance(m, clock, probes, 400)                           # Lebenszeichen geschrieben
    last = store.read_alive()["t"]
    # Stromausfall: kein stop(); neuer Boot
    clock.t += 3600
    probes2 = FakeProbes(boot=BOOT_B, up=60)
    monitor(store, probes2, clock)
    evs = store.events()
    unclean = [e for e in evs if e["k"] == "unclean"]
    assert len(unclean) == 1 and unclean[0]["last"] == last
    assert evs[-1]["k"] == "boot"


def test_clean_shutdown_is_not_reported(env):
    store, clock, probes = env
    m = monitor(store, probes, clock)
    advance(m, clock, probes, 30)
    probes.stopping = True
    m.stop()
    assert store.read_alive()["clean"] is True
    clock.t += 600
    monitor(store, FakeProbes(boot=BOOT_B, up=40), clock)
    assert "unclean" not in [e["k"] for e in store.events()]
    assert "shutdown" in [e["k"] for e in store.events()]


def test_service_restart_same_boot(env):
    store, clock, probes = env
    monitor(store, probes, clock)
    clock.t += 20
    monitor(store, probes, clock)                            # gleicher Boot, nicht sauber beendet
    assert [e["k"] for e in store.events()] == ["boot", "restart"]


def test_unsynced_clock_marks_events(env):
    store, clock, probes = env
    probes.synced = False
    m = monitor(store, probes, clock)
    probes.net = {"up": False, "iface": None, "signal": None}
    advance(m, clock, probes, 40)
    assert all(e.get("unsynced") for e in store.events())


def test_error_categories():
    assert "DNS" in rd.classify_error("ConnectionError: (NameResolutionError: Failed to resolve)")
    assert "Proxy" in rd.classify_error("ProxyError: Tunnel connection failed: 403 Forbidden")
    assert "Zertifikat" in rd.classify_error("SSLError: CERTIFICATE_VERIFY_FAILED")
    assert rd.classify_error("ReadTimeout: read timed out") == "Zeitüberschreitung"
    assert rd.classify_error("x", auth=True) == "Login abgelehnt"


# ───────────────────────── Kernel ─────────────────────────
def test_kernel_messages_classified():
    assert rd.classify_kernel("mmc0: Timeout waiting for hardware interrupt.")[0] == "sd"
    assert rd.classify_kernel("EXT4-fs error (device mmcblk0p2): ext4_find_entry")[0] == "fs"
    assert rd.classify_kernel("hwmon hwmon1: Undervoltage detected!")[0] == "undervolt"
    assert rd.classify_kernel("Out of memory: Killed process 1234 (chromium)")[0] == "oom"
    assert rd.classify_kernel("brcmf_cfg80211_scan: scan error (-110)")[0] == "wifi_drv"
    assert rd.classify_kernel("usb 1-1: new high-speed USB device") is None


def test_kernel_scan_rate_limited(env):
    store, clock, probes = env
    m = monitor(store, probes, clock)
    base = int(clock.t * 1_000_000)
    probes.klines = [(base + i, "mmc0: error -110 whilst initialising SD card") for i in range(1, 6)]
    advance(m, clock, probes, 60)
    probes.klines += [(int(clock.t * 1_000_000) + 1, "mmc0: error -110 whilst initialising SD card")]
    advance(m, clock, probes, 60)
    sd = [e for e in store.events() if e["k"] == "k:sd"]
    assert len(sd) == 1 and "(5×)" in sd[0]["msg"] and sd[0]["lvl"] == "err"
    clock.t += rd.KERNEL_RATE
    probes.klines += [(int(clock.t * 1_000_000) + 1, "mmc0: error -110 whilst initialising SD card")]
    advance(m, clock, probes, 60)
    sd = [e for e in store.events() if e["k"] == "k:sd"]
    assert len(sd) == 2 and "(2×)" in sd[1]["msg"]          # unterdrückte Meldung mitgezählt


# ───────────────────────── Auswertung ─────────────────────────
def test_summary_finds_typical_hour():
    import datetime as dt
    evs = []
    for day in range(1, 5):
        t = dt.datetime(2026, 11, 9 + day, 7, 50).timestamp()
        evs.append({"t": t, "k": "net", "lvl": "warn", "state": "bad", "msg": "Netz weg"})
        evs.append({"t": t + 300, "k": "net", "lvl": "info", "state": "good", "msg": "Netz da", "dur": 300})
    now = dt.datetime(2026, 11, 14, 12, 0).timestamp()
    (kind, cnt, dur, hour, _), = rd.summarize(evs, now)
    assert (kind, cnt, dur, hour) == ("net", 4, 1200, 7)


def test_status_output(env, capsys):
    store, clock, probes = env
    m = monitor(store, probes, clock)
    probes.thr = 0x1
    advance(m, clock, probes, 10)
    out = []
    rd.cmd_status(store, out=out.append, now=clock.t)
    text = "\n".join(out)
    assert "Gerade offen:" in text and "Unterspannung" in text and "Netzteil" in text


def test_log_rotation(env, monkeypatch):
    store, clock, probes = env
    monkeypatch.setattr(rd, "LOG_MAX", 2000)
    for i in range(100):
        store.append({"t": i, "k": "x", "lvl": "info", "msg": "y" * 50})
    assert os.path.getsize(store.path) <= 2000 + 200
    assert os.path.exists(store.old)
    assert len(store.events()) < 100                          # älteste fallen heraus
    with open(store.path, "a") as f:
        f.write('{"t": 5, "k"')                               # halbe Zeile (Stromausfall)
    store.events()


# ───────────────────────── Datenschutz ─────────────────────────
def test_redactor_removes_personal_data():
    env = {"UNTIS_USER": "anzeige7a", "UNTIS_PASSWORD": "geheim123!", "UNTIS_SCHOOL": "gym-musterstadt",
           "CALENDAR_ICS_URL": "https://calendar.google.com/calendar/ical/x/private-abc/basic.ics",
           "WEATHER_LAT": "48.1371", "WEATHER_LON": "11.5754"}
    r = rd.Redactor(env, user="jakob", home="/home/jakob", host="raspy-klasse7a")
    text = ("login anzeige7a pw geheim123! school gym-musterstadt at https://x.webuntis.com/WebUntis?school=a "
            "/home/jakob/raspy jakob@raspy-klasse7a 48.1371 mail lehrer@schule.de 8.8.8.8 192.168.1.20 "
            "dc:a6:32:00:11:22")
    out = r(text)
    for leak in ("anzeige7a", "geheim123", "musterstadt", "webuntis.com/WebUntis", "jakob", "klasse7a",
                 "48.1371", "lehrer@", "8.8.8.8", "dc:a6"):
        assert leak not in out, leak
    assert "192.168.1.20" in out and "~/raspy" in out


def test_messages_are_redacted_before_storing(env):
    store, clock, probes = env
    r = rd.Redactor({"UNTIS_USER": "anzeige7a"}, user="", home="", host="")
    m = rd.Monitor(store, probes, redact=r, now=clock)
    m.start()
    probes.web = {"updated": clock.t, "sources": {"untis": {
        "ok": False, "error": "HTTPError: 500 for https://x.webuntis.com/?u=anzeige7a", "auth": False,
        "loaded": True}}}
    advance(m, clock, probes, 120)
    raw = open(store.path).read()
    assert "anzeige7a" not in raw and "webuntis.com/?" not in raw


def test_diag_uses_standard_library_only():
    """Die Diagnose soll auch laufen, wenn die Python-Umgebung (venv) kaputt ist."""
    tree = ast.parse(open(os.path.join(ROOT, "diag", "raspy_diag.py"), encoding="utf-8").read())
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)


def test_service_has_no_network():
    unit = open(os.path.join(ROOT, "system", "raspy-diag.service"), encoding="utf-8").read()
    assert "RestrictAddressFamilies=AF_UNIX\n" in unit and "IPAddressDeny=any" in unit
    assert "IPAddressAllow" not in unit


def test_dashboard_writes_source_state(tmp_path, monkeypatch):
    import dashboard
    monkeypatch.setitem(dashboard._state, "path", str(tmp_path / "sources.json"))
    monkeypatch.setattr(dashboard, "_sources", {})
    src = dashboard.Source("Untis", 120, lambda: (_ for _ in ()).throw(RuntimeError("kaputt")))
    dashboard._sources["untis"] = src
    monkeypatch.setattr(dashboard, "_STARTED", time.time() - 1000)
    src.run()
    data = json.loads((tmp_path / "sources.json").read_text())
    assert data["sources"]["untis"]["ok"] is False and "kaputt" in data["sources"]["untis"]["error"]
