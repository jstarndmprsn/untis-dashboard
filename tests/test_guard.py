"""Software-Wächter: Zeitfenster, Versionen, Umschalten/Zurückschalten, Reparatur beim Start, Selbstschutz."""
import datetime as dt
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "guard"))
import raspy_guard as rg  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env=dict(os.environ, **GIT_ENV)).stdout.strip()


def commit(repo, files, msg, tag=None):
    for path, text in files.items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    if tag:
        git(repo, "tag", tag)
    return git(repo, "rev-parse", "HEAD")


# ───────────────────────── Zeitfenster ─────────────────────────
def test_windows_parse_and_left():
    spec = "Di,Do 11:35-11:50; Mo-Fr 16:15-18:00"
    tue = dt.datetime(2026, 11, 10, 11, 40)          # Dienstag
    assert 9 * 60 < rg.window_left(spec, tue) <= 10 * 60
    assert rg.window_left(spec, dt.datetime(2026, 11, 11, 11, 40)) is None   # Mittwoch Pause: nein
    assert rg.window_left(spec, dt.datetime(2026, 11, 11, 17, 0)) is not None
    assert rg.window_left(spec, dt.datetime(2026, 11, 14, 17, 0)) is None    # Samstag
    assert rg.window_left("täglich 2:00-4:00", dt.datetime(2026, 11, 14, 3, 0)) is not None
    assert rg.window_left("Sa-Mo 10:00-12:00", dt.datetime(2026, 11, 16, 11, 0)) is not None  # über Sonntag
    assert rg.window_left("tue,thu 11:35-11:50", tue) is not None                            # englisch
    assert rg.next_window(spec, dt.datetime(2026, 11, 10, 12, 0)) == dt.datetime(2026, 11, 10, 16, 15)
    for bad in ("Di 11:50-11:35", "Xy 10:00-11:00", "Di 10-11", "Di 25:00-26:00"):
        with pytest.raises(ValueError):
            rg.parse_windows(bad)
    assert rg.parse_windows("") == []


# ───────────────────────── Aufbau mit Test-Repo ─────────────────────────
@pytest.fixture
def setup(tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    c1 = commit(origin, {"app/server.py": "print(1)\n", "app/requirements.txt": "flask\n",
                         "device/button.py": "x = 1\n", "README.md": "a\n"}, "eins", tag="v1.0.0")
    base = tmp_path / "raspy"
    subprocess.run(["git", "clone", "-q", str(origin), str(base)], check=True)
    (base / "config.env").write_text("GUARD_WINDOWS=\"Di 11:35-11:50\"\nPORT=5999\n")

    for k, v in {"STATE_DIR": tmp_path / "state", "LOG_FILE": tmp_path / "guard.log",
                 "LIB_DIR": tmp_path / "lib", "UPDATE_RUNNING": tmp_path / "apt-running"}.items():
        monkeypatch.setattr(rg, k, str(v))

    calls = []
    monkeypatch.setattr(rg.Guard, "ensure_venv", lambda self, c: str(tmp_path / "venv"))
    monkeypatch.setattr(rg.Guard, "venv_ok", lambda self, name: True)
    monkeypatch.setattr(rg.Guard, "probe", lambda self, name: True)
    monkeypatch.setattr(rg.Guard, "health_snapshot", lambda self: {"untis": True})
    monkeypatch.setattr(rg.Guard, "unit_enabled", lambda self, u: True)
    monkeypatch.setattr(rg.Guard, "systemctl", lambda self, *a, check=False: calls.append(a) or
                        subprocess.CompletedProcess(a, 0, "active\n", ""))
    health = {"ok": True}
    monkeypatch.setattr(rg.Guard, "healthy", lambda self, before, units, timeout=150:
                        (health["ok"], "ok" if health["ok"] else "Seite antwortet nicht"))
    g = rg.Guard(base=str(base))
    g.adopt()
    return {"g": g, "origin": origin, "base": base, "c1": c1, "calls": calls, "health": health, "tmp": tmp_path}


IN_WINDOW = dt.datetime(2026, 11, 10, 11, 36)     # Dienstag in der Pause
OUTSIDE = dt.datetime(2026, 11, 10, 10, 0)


def test_adopt_creates_release_and_links(setup):
    g, base = setup["g"], setup["base"]
    assert g.current_release() == "v1.0.0"
    rel = base / "releases" / "v1.0.0"
    assert os.readlink(base / "current") == os.path.join("releases", "v1.0.0")
    assert os.readlink(rel / "config.env") == str(base / "config.env")
    assert (base / "current" / "app" / "server.py").read_text() == "print(1)\n"
    assert g.adopt() == 0 and g.current_release() == "v1.0.0"        # idempotent


def test_update_waits_for_window_then_switches(setup):
    g, origin, calls = setup["g"], setup["origin"], setup["calls"]
    commit(origin, {"app/server.py": "print(2)\n"}, "zwei", tag="v1.1.0")
    commit(origin, {"app/server.py": "print(3)\n"}, "drei (nur gepusht, nicht freigegeben)")

    g.tick(now=OUTSIDE)
    assert g.state["prepared"] == "v1.1.0" and g.current_release() == "v1.0.0"   # vorbereitet, wartet
    assert calls == []

    g.tick(now=IN_WINDOW)
    assert g.current_release() == "v1.1.0"
    assert (setup["base"] / "current" / "app" / "server.py").read_text() == "print(2)\n"
    assert ("restart", "raspy-web.service") in calls                          # nur was sich geändert hat
    assert ("restart", "raspy-buttons.service") not in calls
    assert g.state["previous"] == "v1.0.0" and g.state["history"][-1]["result"] == "ok"
    log = open(rg.LOG_FILE).read()
    assert "zwei" in log and "app/server.py" in log                            # Änderungen protokolliert

    g.tick(now=IN_WINDOW)                                                      # nichts Neues mehr
    assert g.current_release() == "v1.1.0"


def test_never_switches_while_apt_runs_or_window_almost_over(setup):
    g, origin = setup["g"], setup["origin"]
    commit(origin, {"app/server.py": "print(2)\n"}, "zwei", tag="v1.1.0")
    open(rg.UPDATE_RUNNING, "w").close()
    g.tick(now=IN_WINDOW)
    assert g.current_release() == "v1.0.0"
    os.unlink(rg.UPDATE_RUNNING)
    g.tick(now=dt.datetime(2026, 11, 10, 11, 48))       # nur noch 2 min übrig
    assert g.current_release() == "v1.0.0"
    g.tick(now=IN_WINDOW)
    assert g.current_release() == "v1.1.0"


def test_failed_update_rolls_back_and_is_not_retried(setup):
    g, origin, health = setup["g"], setup["origin"], setup["health"]
    commit(origin, {"app/server.py": "kaputt\n"}, "kaputt", tag="v1.1.0")
    health["ok"] = False
    g.tick(now=IN_WINDOW)
    assert g.current_release() == "v1.0.0"
    assert "v1.1.0" in g.state["failed"]
    assert "zurück" in g.state["history"][-1]["result"]
    health["ok"] = True
    g.tick(now=IN_WINDOW)
    assert g.current_release() == "v1.0.0"              # kaputte Version nicht nochmal
    commit(origin, {"app/server.py": "repariert\n"}, "fix", tag="v1.1.1")
    g.state["last_fetch"] = 0
    g.tick(now=IN_WINDOW)
    assert g.current_release() == "v1.1.1"


def test_no_downgrade_and_semver_order(setup):
    g, origin = setup["g"], setup["origin"]
    commit(origin, {"README.md": "b\n"}, "b", tag="v1.9.0")
    commit(origin, {"README.md": "c\n"}, "c", tag="v1.10.0")
    git(setup["base"], "fetch", "-q", "--tags", "origin")
    assert [n for n, _ in g.tags()][:3] == ["v1.10.0", "v1.9.0", "v1.0.0"]
    assert g.candidate()[0] == "v1.10.0"
    g.tick(now=IN_WINDOW)
    assert g.current_release() == "v1.10.0"
    assert setup["calls"] == []                          # nur README geändert -> nichts neu gestartet


def test_install_files_trigger_apply(setup, monkeypatch):
    g = setup["g"]
    assert g.plan(["install.sh"])["apply"] and g.plan(["system/raspy-web.service"])["apply"]
    p = g.plan(["device/thermal_guard.py", "boot/make_frames.py"])
    assert p["units"] == ["raspy-thermal.service"] and p["next_boot"] and not p["apply"]
    assert set(g.plan(["app/config.py"])["units"]) == set(rg.RESTART_RULES)


def test_boot_repairs_corrupted_files(setup):
    g, base = setup["g"], setup["base"]
    f = base / "releases" / "v1.0.0" / "app" / "server.py"
    f.write_text("kaputt")
    (base / "releases" / "v1.0.0" / "device" / "button.py").unlink()
    assert sorted(g.verify("v1.0.0", setup["c1"])) == ["app/server.py", "device/button.py"]
    assert g.boot() == 0
    assert f.read_text() == "print(1)\n" and not g.verify("v1.0.0", setup["c1"])
    assert "wiederhergestellt" in open(rg.LOG_FILE).read()


def test_boot_undoes_interrupted_switch_and_fixes_missing_link(setup):
    g, origin, base = setup["g"], setup["origin"], setup["base"]
    commit(origin, {"app/server.py": "print(2)\n"}, "zwei", tag="v1.1.0")
    g.tick(now=OUTSIDE)                                   # v1.1.0 vorbereitet
    # Strom weg genau nach dem Umschalten, vor der Bestätigung:
    g.state["pending"] = {"from": "v1.0.0", "to": "v1.1.0", "at": "x"}
    g.save_state()
    rg.atomic_symlink("releases/v1.1.0", str(base / "current"))
    g2 = rg.Guard(base=str(base))
    g2.boot()
    assert g2.current_release() == "v1.0.0" and not g2.state.get("pending")

    os.unlink(base / "current")                           # Link ganz weg
    g3 = rg.Guard(base=str(base))
    g3.boot()
    assert g3.current_release() in ("v1.0.0", "v1.1.0")


def test_corrupt_state_file_is_survived(setup):
    os.makedirs(rg.STATE_DIR, exist_ok=True)
    open(os.path.join(rg.STATE_DIR, "state.json"), "w").write("{kaputt")
    g = rg.Guard(base=str(setup["base"]))
    assert g.state == {} and g.current_release() == "v1.0.0"
    assert g.boot() == 0


def test_rollback_by_hand(setup):
    g, origin = setup["g"], setup["origin"]
    commit(origin, {"app/server.py": "print(2)\n"}, "zwei", tag="v1.1.0")
    g.tick(now=IN_WINDOW)
    assert g.current_release() == "v1.1.0"
    assert g.rollback()
    assert g.current_release() == "v1.0.0" and "v1.1.0" in g.state["failed"]


# ───────────────────────── Selbstschutz ─────────────────────────
def test_install_self_keeps_previous_and_starter_falls_back(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    monkeypatch.setattr(rg, "LIB_DIR", str(lib))
    src = os.path.join(ROOT, "guard", "raspy_guard.py")
    rg.install_self(src)
    assert (lib / "raspy_guard.sha256").read_text().strip() == rg.sha256(src)

    newer = tmp_path / "neu.py"
    newer.write_text(open(src).read() + "\n# neue Version\n")
    rg.install_self(str(newer))
    assert (lib / "raspy_guard.prev.py").read_text() == open(src).read()   # alte aufgehoben

    broken = tmp_path / "kaputt.py"
    broken.write_text("def (:\n")
    with pytest.raises(SyntaxError):
        rg.install_self(str(broken))
    assert "neue Version" in (lib / "raspy_guard.py").read_text()          # nichts überschrieben

    if not shutil.which("sha256sum"):
        pytest.skip("sha256sum fehlt")
    starter = os.path.join(ROOT, "system", "raspy-guard")
    env = dict(os.environ, RASPY_GUARD_LIB=str(lib))
    r = subprocess.run(["sh", starter, "selftest"], env=env, capture_output=True, text=True)
    assert r.returncode == 0
    (lib / "raspy_guard.py").write_text("kaputt durch Stromausfall")          # Hauptdatei beschädigt
    r = subprocess.run(["sh", starter, "selftest"], env=env, capture_output=True, text=True)
    assert r.returncode == 0                                                   # läuft mit .prev weiter
    (lib / "raspy_guard.prev.py").write_text("auch kaputt")
    r = subprocess.run(["sh", starter, "selftest"], env=env, capture_output=True, text=True)
    assert r.returncode == 1


# ───────────────────────── Versionsanzeige beim Start ─────────────────────────
@pytest.mark.parametrize("broken", [False, True])
def test_boot_version_rebuilds_or_restores_initramfs(setup, monkeypatch, broken):
    g, tmp = setup["g"], setup["tmp"]
    theme, fake_bin = tmp / "theme", tmp / "bin"
    theme.mkdir(); fake_bin.mkdir()
    (theme / "frames.json").write_text('{"version_s": 3.0}')
    (theme / "version.png").write_bytes(b"alt")
    img = tmp / "initrd.img-test"
    img.write_bytes(b"x" * 1000)
    # update-initramfs-Attrappe: baut "neu" bzw. zerstört das Abbild (Stromausfall o. Ä.)
    (fake_bin / "update-initramfs").write_text("#!/bin/sh\n" + (f"printf kaputt > {img}\nexit 1\n" if broken else
                                                               f"printf %01000d 0 > {img}\n"))
    (fake_bin / "update-initramfs").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setattr(rg.shutil, "which", lambda c: None if c == "lsinitramfs" else shutil.which(c))
    monkeypatch.setattr(rg, "THEME_DIR", str(theme))
    monkeypatch.setattr(rg, "BACKUP_DIR", str(tmp / "backup"))
    monkeypatch.setattr(rg.Guard, "initramfs_images", lambda self: ("test", [str(img)]))
    monkeypatch.setattr(rg.Guard, "run", lambda self, cmd, **kw: open(cmd[cmd.index("--version-image") + 1], "wb")
                        .write(b"neu-" + cmd[-1].encode()) and subprocess.CompletedProcess(cmd, 0, "", ""))
    g.boot_version("v1.0.0")
    log = open(rg.LOG_FILE).read()
    if broken:
        assert img.read_bytes() == b"x" * 1000                      # altes initramfs zurück
        assert (theme / "version.png").read_bytes() == b"alt"
        assert "zurückgespielt" in log
    else:
        assert (theme / "version.png").read_bytes() == b"neu-v1.0.0"
        assert "neu gebaut und geprüft" in log


def test_boot_version_skips_without_version_screen(setup, monkeypatch):
    monkeypatch.setattr(rg, "THEME_DIR", str(setup["tmp"] / "kein-theme"))
    called = []
    monkeypatch.setattr(rg.Guard, "rebuild_initramfs", lambda self, *a: called.append(a))
    setup["g"].boot_version("v1.0.0")
    assert called == []
