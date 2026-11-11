#!/usr/bin/env python3
"""
Software-Wächter (raspy-guard): spielt freigegebene Versionen automatisch ein – nur in den
Zeitfenstern aus config.env – und repariert beim Start beschädigte Programmdateien.

Aufbau (BASE = der git-Ordner, z. B. ~/raspy):
  BASE/releases/<version>/   je Version ein eigener Ordner (git worktree, nur lesen)
  BASE/current               Link auf die aktive Version – alle Dienste starten über diesen Link
  BASE/venvs/<hash>/         Python-Umgebung je requirements.txt (von mehreren Versionen geteilt)
  BASE/config.env            Einstellungen (in jede Version verlinkt)

Ablauf:
  Im Hintergrund (jederzeit, unsichtbar): neue Git-Tags v* holen, neue Version vorbereiten
  (Ordner, Python-Umgebung) und prüfen (Prüfsummen gegen Git, Kompilieren, Probelauf mit Demo-Daten).
  Im Zeitfenster: Link umschalten (ein einziger atomarer Schritt), nur das neu starten, was sich
  geändert hat, prüfen. Läuft etwas nicht: automatisch zurück auf die alte Version.
  Beim Start (raspy-guard-boot.service, vor den anderen Diensten): unterbrochenes Umschalten
  zurücknehmen, Dateien der aktiven Version prüfen und aus Git wiederherstellen.

Protokoll: /var/log/raspy-guard.log   Zustand: /var/lib/raspy-guard/state.json

  raspy-guard status          Überblick
  raspy-guard tick            ein Durchlauf (raspy-guard.timer, alle 2 min)
  raspy-guard boot            Prüfung/Reparatur beim Start
  raspy-guard update-now      neue Version sofort einspielen (ohne Zeitfenster)
  raspy-guard rollback        zurück auf die vorherige Version
  raspy-guard adopt           den aktuellen Stand als erste Version übernehmen (install.sh)
  raspy-guard install-self F  Wächter-Datei F sicher installieren (install.sh / Selbst-Update)

Nur Python-Standardbibliothek – der Wächter darf nicht von der Version abhängen, die er verwaltet.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

GUARD_VERSION = 1

CONF_FILE = os.environ.get("RASPY_GUARD_CONF", "/etc/raspy-guard.conf")
STATE_DIR = os.environ.get("RASPY_GUARD_STATE", "/var/lib/raspy-guard")
LOG_FILE = os.environ.get("RASPY_GUARD_LOG", "/var/log/raspy-guard.log")
LIB_DIR = os.environ.get("RASPY_GUARD_LIB", "/usr/local/lib/raspy-guard")
LOCK_FILE = os.environ.get("RASPY_GUARD_LOCK", "/run/raspy-guard.lock")
UPDATE_RUNNING = "/run/raspy-update/state"         # raspy-update (apt) läuft gerade
THEME_DIR = os.environ.get("RASPY_GUARD_THEME", "/usr/share/plymouth/themes/raspy")
BACKUP_DIR = os.environ.get("RASPY_GUARD_BACKUP", "/var/backups/raspy-guard")
FIRMWARE = {"-2712": "initramfs_2712", "-v8": "initramfs8", "-v7l": "initramfs7l", "-v7": "initramfs7", "-v6": "initramfs"}

FETCH_EVERY = 3600          # neue Tags höchstens stündlich holen
MIN_LEFT = 4 * 60           # so viel muss vom Zeitfenster noch übrig sein (Umschalten + Prüfen)
KEEP_RELEASES = 3
APT_EVERY_DAYS = 6
LOG_MAX = 1_000_000

# Dateien -> was nach dem Umschalten neu gestartet bzw. neu angewendet werden muss
APPLY_PATTERNS = (r"^install\.sh$", r"^system/", r"^guard/")
RESTART_RULES = {
    "raspy-web.service": (r"^app/",),
    "raspy-buttons.service": (r"^device/button\.py$", r"^device/shutdown_overlay\.py$", r"^app/config\.py$"),
    "raspy-thermal.service": (r"^device/thermal_guard\.py$", r"^device/overheat_overlay\.py$", r"^app/config\.py$"),
}
NEXT_BOOT_PATTERNS = (r"^boot/", r"^device/bootsplash_x\.py$", r"^device/kiosk\.sh$", r"^device/display\.sh$",
                      r"^system/raspy-display-setup$")
VENV_IMPORTS = "import flask, waitress, requests, icalendar, recurring_ical_events, psutil"

DAYS = {"mo": 0, "mon": 0, "di": 1, "tue": 1, "mi": 2, "wed": 2, "do": 3, "thu": 3,
        "fr": 4, "fri": 4, "sa": 5, "sat": 5, "so": 6, "sun": 6}


class GuardError(RuntimeError):
    pass


# ───────────────────────── Protokoll ─────────────────────────
def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def log_block(title, text):
    text = (text or "").rstrip()
    log(title + ("" if text else " (nichts)"))
    for ln in text.splitlines():
        log("    " + ln)


def boot_message(text):
    """Kurzer Hinweis auf dem Startbildschirm (Plymouth), falls er gerade läuft."""
    try:
        if subprocess.run(["plymouth", "--ping"], capture_output=True, timeout=3).returncode == 0:
            subprocess.run(["plymouth", "display-message", f"--text={text}"], capture_output=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        pass


# ───────────────────────── Dateien sicher schreiben ─────────────────────────
def fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def atomic_write(path, data, mode=0o644):
    """Erst komplett in eine Temp-Datei, dann in einem Schritt austauschen."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode())
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        fsync_dir(d)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def atomic_symlink(target, link):
    tmp = f"{link}.tmp-{os.getpid()}"
    if os.path.lexists(tmp):
        os.unlink(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, link)
    fsync_dir(os.path.dirname(link))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ───────────────────────── Einstellungen ─────────────────────────
def read_env(path):
    env = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    q, v = v[0], v[1:-1]
                    if q == '"':
                        v = v.replace('\\"', '"').replace("\\\\", "\\")
                env[k.strip()] = v
    except FileNotFoundError:
        pass
    return env


class Guard:
    def __init__(self, base=None, owner=None):
        conf = read_env(CONF_FILE)
        self.base = os.path.abspath(base or os.environ.get("RASPY_GUARD_BASE") or conf.get("BASE") or "")
        if not self.base or not os.path.isdir(os.path.join(self.base, ".git")):
            raise GuardError(f"Kein raspy-git-Ordner gefunden (BASE={self.base or '?'}, {CONF_FILE})")
        self.owner = owner or conf.get("OWNER") or pwd.getpwuid(os.stat(self.base).st_uid).pw_name
        self.releases = os.path.join(self.base, "releases")
        self.current = os.path.join(self.base, "current")
        self.venvs = os.path.join(self.base, "venvs")
        self.state_file = os.path.join(STATE_DIR, "state.json")
        self.state = self.load_state()

    # ── Einstellungen aus config.env ──
    @property
    def cfg(self):
        return read_env(os.path.join(self.base, "config.env"))

    def port(self):
        return int(self.cfg.get("PORT") or 5000)

    # ── Zustand ──
    def load_state(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                s = json.load(f)
            if isinstance(s, dict):
                return s
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log("Zustandsdatei beschädigt – wird aus dem aktuellen Link neu aufgebaut")
        return {}

    def save_state(self):
        atomic_write(self.state_file, json.dumps(self.state, indent=1, sort_keys=True) + "\n")

    # ── Befehle ──
    def run(self, cmd, owner=True, timeout=600, check=True, env=None, cwd=None):
        """Befehl ausführen – git/pip/install.sh als Besitzer des Ordners, nicht als root."""
        full_env = dict(os.environ, **(env or {}))
        if owner and os.geteuid() == 0 and self.owner != "root":
            home = pwd.getpwnam(self.owner).pw_dir
            cmd = ["runuser", "-u", self.owner, "--", "env", f"HOME={home}"] + \
                  [f"{k}={v}" for k, v in (env or {}).items()] + list(cmd)
            full_env = None
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=full_env, cwd=cwd)
        if check and r.returncode != 0:
            raise GuardError(f"{' '.join(cmd[-6:])} → {r.returncode}: {(r.stderr or r.stdout).strip()[-400:]}")
        return r

    def git(self, *args, cwd=None, **kw):
        return self.run(["git", "-C", cwd or self.base, *args], **kw)

    def systemctl(self, *args, check=False):
        return self.run(["systemctl", *args], owner=False, check=check, timeout=120)

    def unit_enabled(self, unit):
        return self.systemctl("is-enabled", unit).stdout.strip() in ("enabled", "static")

    # ───────────────────────── Versionen ─────────────────────────
    def tags(self):
        """Freigegebene Versionen: Tags v1, v1.2, v1.2.3 … (neueste zuerst)."""
        out = self.git("for-each-ref", "--format=%(refname:short) %(objectname) %(*objectname)", "refs/tags").stdout
        res = []
        for line in out.splitlines():
            parts = line.split()
            if not parts:
                continue
            m = re.fullmatch(r"v(\d+(?:\.\d+)*)", parts[0])
            if m:
                commit = parts[2] if len(parts) > 2 else parts[1]   # annotierte Tags: Ziel-Commit
                res.append((tuple(int(x) for x in m.group(1).split(".")), parts[0], commit))
        return [(name, commit) for _, name, commit in sorted(res, reverse=True)]

    def is_ancestor(self, a, b):
        return self.git("merge-base", "--is-ancestor", a, b, check=False).returncode == 0

    def current_release(self):
        if not os.path.islink(self.current):
            return None
        name = os.path.basename(os.readlink(self.current).rstrip("/"))
        path = os.path.join(self.releases, name)
        return name if os.path.isdir(path) else None

    def release_commit(self, name):
        info = self.state.get("releases", {}).get(name)
        if info:
            return info["commit"]
        r = self.git("rev-parse", "HEAD", cwd=os.path.join(self.releases, name), check=False)
        return r.stdout.strip() if r.returncode == 0 else None

    def candidate(self):
        """Neueste freigegebene Version, die neuer ist als die aktive (keine Rückschritte)."""
        cur = self.current_release()
        cur_commit = self.release_commit(cur) if cur else None
        failed = set(self.state.get("failed", []))
        for name, commit in self.tags():
            if name in failed:
                continue
            if commit == cur_commit:
                return None
            if cur_commit is None or self.is_ancestor(cur_commit, commit):
                return name, commit
        return None

    # ───────────────────────── Prüfen + Reparieren ─────────────────────────
    def tree(self, commit):
        out = self.git("ls-tree", "-r", "-z", "--full-tree", commit).stdout
        files = []
        for entry in out.split("\0"):
            if not entry:
                continue
            meta, path = entry.split("\t", 1)
            mode, typ, sha = meta.split()
            if typ == "blob":
                files.append((mode, sha, path))
        return files

    def verify(self, name, commit):
        """Alle Dateien der Version gegen Git prüfen -> Liste beschädigter/fehlender Dateien."""
        root = os.path.join(self.releases, name)
        bad = []
        for mode, sha, path in self.tree(commit):
            p = os.path.join(root, path)
            try:
                if mode == "120000":
                    data = os.readlink(p).encode()
                else:
                    with open(p, "rb") as f:
                        data = f.read()
            except OSError:
                bad.append(path)
                continue
            if hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() != sha:
                bad.append(path)
        return bad

    def repair(self, name, commit):
        """Version aus den Git-Daten wiederherstellen. True, wenn danach alles stimmt."""
        root = os.path.join(self.releases, name)
        gitdir = self.git("rev-parse", "--git-dir", cwd=root, check=False).stdout.strip()
        if gitdir:
            lock = os.path.join(root, gitdir, "index.lock") if not os.path.isabs(gitdir) else os.path.join(gitdir, "index.lock")
            if os.path.exists(lock):
                os.unlink(lock)          # vom Stromausfall übrig geblieben
        r = self.git("reset", "-q", "--hard", commit, cwd=root, check=False)
        if r.returncode != 0:
            log(f"  Wiederherstellen aus Git fehlgeschlagen: {r.stderr.strip()[-200:]}")
            return False
        return not self.verify(name, commit)

    def venv_ok(self, name):
        py = os.path.join(self.releases, name, "venv", "bin", "python")
        try:
            return subprocess.run([py, "-c", VENV_IMPORTS], capture_output=True, timeout=60).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def release_ok(self, name):
        commit = self.release_commit(name)
        return bool(commit) and os.path.isdir(os.path.join(self.releases, name)) and \
            not self.verify(name, commit) and self.venv_ok(name)

    # ───────────────────────── Vorbereiten ─────────────────────────
    def req_hash(self, commit):
        r = self.git("show", f"{commit}:app/requirements.txt", check=False)
        return hashlib.sha256(r.stdout.encode()).hexdigest()[:12]

    def ensure_venv(self, commit):
        """Python-Umgebung für diese requirements.txt – erst fertig bauen, dann umbenennen."""
        h = self.req_hash(commit)
        final = os.path.join(self.venvs, h)
        if os.path.exists(os.path.join(final, ".ok")):
            return final
        os.makedirs(self.venvs, exist_ok=True)
        self.fix_owner(self.venvs)
        tmp = final + ".building"
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(final, ignore_errors=True)
        req = os.path.join(self.releases, "_req-" + h + ".txt")
        with open(req, "w") as f:
            f.write(self.git("show", f"{commit}:app/requirements.txt").stdout)
        self.fix_owner(req)
        log(f"  Python-Umgebung {h} wird gebaut …")
        self.run(["python3", "-m", "venv", tmp], timeout=300)
        self.run([os.path.join(tmp, "bin", "pip"), "install", "-q", "--upgrade", "pip"], timeout=900, check=False)
        self.run([os.path.join(tmp, "bin", "pip"), "install", "-q", "-r", req], timeout=1800)
        os.unlink(req)
        # venv kennt seinen Pfad (pyvenv.cfg/Skripte) – darum beim Umbenennen nur python -m nutzen
        os.replace(tmp, final)
        atomic_write(os.path.join(final, ".ok"), "ok\n")
        self.fix_owner(final)
        return final

    def fix_owner(self, path):
        if os.geteuid() == 0 and self.owner != "root":
            pw = pwd.getpwnam(self.owner)
            try:
                os.chown(path, pw.pw_uid, pw.pw_gid)
            except OSError:
                pass

    def link_shared(self, name, venv):
        root = os.path.join(self.releases, name)
        for link, target in (("config.env", os.path.join(self.base, "config.env")),
                             (".install-state", os.path.join(self.base, ".install-state")),
                             ("venv", venv)):
            p = os.path.join(root, link)
            if os.path.islink(p) and os.readlink(p) == target:
                continue
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            atomic_symlink(target, p)
            self.fix_owner_link(p)

    def fix_owner_link(self, p):
        if os.geteuid() == 0 and self.owner != "root":
            pw = pwd.getpwnam(self.owner)
            try:
                os.lchown(p, pw.pw_uid, pw.pw_gid)
            except OSError:
                pass

    def make_release(self, name, commit):
        """Versions-Ordner als git worktree anlegen (bei halb fertigem Ordner: neu)."""
        root = os.path.join(self.releases, name)
        os.makedirs(self.releases, exist_ok=True)
        self.fix_owner(self.releases)
        if os.path.isdir(root):
            if self.release_commit(name) == commit and not self.verify(name, commit):
                return root
            self.git("worktree", "remove", "--force", root, check=False)
            shutil.rmtree(root, ignore_errors=True)
        self.git("worktree", "prune", check=False)
        self.git("worktree", "add", "-q", "--detach", "--force", root, commit)
        return root

    def probe(self, name):
        """Probelauf der neuen Version mit Demo-Daten auf einem freien Port (ohne echte Zugangsdaten)."""
        root = os.path.join(self.releases, name)
        py = os.path.join(root, "venv", "bin", "python")
        self.run([py, "-m", "compileall", "-q", "app", "device", "guard"], cwd=root, timeout=300)
        if os.path.exists(os.path.join(root, "install.sh")):
            self.run(["bash", "-n", "install.sh"], cwd=root)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        empty = os.path.join(self.releases, f"_probe-{port}.env")
        with open(empty, "w") as f:
            f.write("DEMO=1\n")
        self.fix_owner(empty)
        cmd = [py, "app/server.py", "--demo", "--no-calendar", "--port", str(port)]
        env = {"RASPY_CONFIG": empty, "PORT": str(port), "LAN_ACCESS": "0"}
        if os.geteuid() == 0 and self.owner != "root":
            home = pwd.getpwnam(self.owner).pw_dir
            cmd = ["runuser", "-u", self.owner, "--", "env", f"HOME={home}"] + [f"{k}={v}" for k, v in env.items()] + cmd
            env = None
        p = subprocess.Popen(cmd, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                             env=None if env is None else dict(os.environ, **env))
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                if p.poll() is not None:
                    raise GuardError(f"Probelauf beendet sich sofort: {p.stderr.read()[-400:]}")
                try:
                    if http_ok(port, "/content") and http_json(port, "/api/heute").get("day"):
                        return True
                except (OSError, ValueError):
                    pass
                time.sleep(1)
            raise GuardError("Probelauf antwortet nicht innerhalb von 60 s")
        finally:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
            os.unlink(empty)

    def prepare(self, name, commit):
        log(f"Neue Version {name} ({commit[:7]}) wird vorbereitet")
        cur = self.current_release()
        if cur:
            old = self.release_commit(cur)
            log_block(f"  Änderungen {cur} → {name}:",
                      self.git("log", "--oneline", "--no-decorate", f"{old}..{commit}", check=False).stdout)
            log_block("  Geänderte Dateien:", self.git("diff", "--stat", old, commit, check=False).stdout)
        self.make_release(name, commit)
        bad = self.verify(name, commit)
        if bad:
            raise GuardError(f"frisch ausgecheckte Dateien stimmen nicht mit Git überein: {bad[:5]}")
        venv = self.ensure_venv(commit)
        self.link_shared(name, venv)
        if not self.venv_ok(name):
            raise GuardError("Python-Umgebung unvollständig")
        self.probe(name)
        self.state.setdefault("releases", {})[name] = {"commit": commit, "prepared": now_iso()}
        self.state["prepared"] = name
        self.save_state()
        log(f"  {name} geprüft und bereit (Prüfsummen ✓, Kompilieren ✓, Probelauf ✓)")

    # ───────────────────────── Umschalten ─────────────────────────
    def changes(self, old_commit, new_commit):
        out = self.git("diff", "--name-only", old_commit, new_commit, check=False).stdout
        return [p for p in out.splitlines() if p]

    def plan(self, files):
        match = lambda pats: any(re.search(p, f) for p in pats for f in files)  # noqa: E731
        units = [u for u, pats in RESTART_RULES.items() if match(pats)]
        return {"apply": match(APPLY_PATTERNS), "units": units, "next_boot": match(NEXT_BOOT_PATTERNS)}

    def health_snapshot(self):
        try:
            return {k: v.get("ok") for k, v in http_json(self.port(), "/api/heute").get("status", {}).items()}
        except (OSError, ValueError):
            return {}

    def healthy(self, before, units, timeout=150):
        """Läuft die neue Version? Seite antwortet, Dienste laufen, Untis wie vorher erreichbar."""
        deadline = time.time() + timeout
        need_untis = before.get("untis") is True
        last = "Seite antwortet nicht"
        while time.time() < deadline:
            down = [u for u in units if self.systemctl("is-active", u).stdout.strip() not in ("active", "activating")]
            if down:
                last = f"Dienst läuft nicht: {', '.join(down)}"
            else:
                try:
                    if http_ok(self.port(), "/content"):
                        st = http_json(self.port(), "/api/heute").get("status", {})
                        if not need_untis or st.get("untis", {}).get("ok"):
                            return True, "ok"
                        last = "Untis-Daten kommen nicht (vorher ging es)"
                except (OSError, ValueError) as e:
                    last = f"Seite antwortet nicht ({type(e).__name__})"
            time.sleep(3)
        return False, last

    def activate(self, name, plan):
        """Link umschalten und das Nötige neu starten/anwenden."""
        atomic_symlink(os.path.join("releases", name), self.current)
        if plan["apply"]:
            log("  install.sh --apply (Systemdateien/Installer geändert)")
            r = self.run([os.path.join(self.current, "install.sh"), "--apply"], cwd=self.current, timeout=1500, check=False)
            log_block("  install.sh:", "\n".join(r.stdout.splitlines()[-15:]))
            if r.returncode != 0:
                raise GuardError(f"install.sh --apply fehlgeschlagen ({r.returncode})")
        else:
            for u in plan["units"]:
                if self.unit_enabled(u):
                    log(f"  Neustart {u}")
                    self.systemctl("restart", u)
        if plan["next_boot"]:
            log("  Hinweis: Änderungen an Boot-Animation/Kiosk werden erst beim nächsten Start aktiv")

    def switch(self, name, reason="Zeitfenster"):
        cur = self.current_release()
        info = self.state["releases"][name]
        old_commit = self.release_commit(cur) if cur else None
        files = self.changes(old_commit, info["commit"]) if old_commit else ["app/", "install.sh"]
        plan = self.plan(files)
        if not plan["apply"] and not plan["units"]:
            plan["units"] = []       # nur Doku o. Ä. geändert -> nichts neu starten
        units = [u for u in (plan["units"] if not plan["apply"] else RESTART_RULES) if self.unit_enabled(u)]
        log(f"Umschalten {cur or '–'} → {name} ({reason}); neu starten: {', '.join(units) or 'nichts'}"
            + (", install.sh --apply" if plan["apply"] else ""))
        before = self.health_snapshot()
        self.state["pending"] = {"from": cur, "to": name, "at": now_iso()}
        self.save_state()
        try:
            self.activate(name, plan)
            ok, why = self.healthy(before, units) if (units or plan["apply"]) else (True, "nichts neu gestartet")
        except Exception as e:      # noqa: BLE001 – egal was schiefgeht: zurück auf die alte Version
            ok, why = False, f"{type(e).__name__}: {e}"
        if ok:
            self.state.update(previous=cur, last_good=name, pending=None, prepared=None)
            self.state.setdefault("history", []).append({"at": now_iso(), "from": cur, "to": name, "result": "ok"})
            self.save_state()
            log(f"  ✓ {name} läuft")
            self.boot_version(name)
            self.self_update(name)
            self.prune()
            return True
        log(f"  ✗ {name} läuft nicht ({why}) – zurück auf {cur}")
        if cur:
            try:
                self.activate(cur, plan)
                ok2, why2 = self.healthy(before, units) if units else (True, "")
                log(f"  {'✓' if ok2 else '✗'} zurück auf {cur}" + ("" if ok2 else f" – {why2}"))
            except Exception as e:  # noqa: BLE001
                log(f"  ✗ Zurückschalten fehlgeschlagen: {e}")
        self.state.setdefault("failed", []).append(name)
        self.state.setdefault("history", []).append({"at": now_iso(), "from": cur, "to": name, "result": f"zurück: {why}"})
        self.state.update(pending=None, prepared=None)
        self.save_state()
        return False

    def rollback(self):
        cur, prev = self.current_release(), self.state.get("previous")
        if not prev or not os.path.isdir(os.path.join(self.releases, prev)):
            raise GuardError("Keine vorherige Version vorhanden")
        if not self.release_ok(prev):
            raise GuardError(f"Vorherige Version {prev} ist nicht in Ordnung")
        self.state.setdefault("releases", {}).setdefault(prev, {"commit": self.release_commit(prev)})
        ok = self.switch(prev, reason="von Hand zurück")
        if ok and cur:
            self.state.setdefault("failed", []).append(cur)   # nicht gleich wieder automatisch einspielen
            self.save_state()
        return ok

    def prune(self):
        keep = {self.current_release(), self.state.get("previous"), self.state.get("prepared")}
        names = sorted((n for n in os.listdir(self.releases) if not n.startswith(("_", "."))),
                       key=lambda n: os.path.getmtime(os.path.join(self.releases, n)), reverse=True)
        for n in names[KEEP_RELEASES:]:
            if n in keep:
                continue
            log(f"  alte Version {n} entfernt")
            self.git("worktree", "remove", "--force", os.path.join(self.releases, n), check=False)
            shutil.rmtree(os.path.join(self.releases, n), ignore_errors=True)
            self.state.get("releases", {}).pop(n, None)
        self.git("worktree", "prune", check=False)
        used = {os.path.basename(os.readlink(os.path.join(self.releases, n, "venv")))
                for n in os.listdir(self.releases) if os.path.islink(os.path.join(self.releases, n, "venv"))}
        for v in os.listdir(self.venvs) if os.path.isdir(self.venvs) else []:
            if v not in used:
                shutil.rmtree(os.path.join(self.venvs, v), ignore_errors=True)
        self.save_state()

    # ───────────────────────── Versionsanzeige beim Start ─────────────────────────
    def boot_version(self, name):
        """Neue Programmversion in der Boot-Animation anzeigen: version.png austauschen und das
        initramfs des laufenden Kernels neu bauen (wie bei jedem Kernel-Update). Vorher sichern,
        danach prüfen – stimmt etwas nicht, kommt das alte initramfs zurück."""
        try:
            meta = json.load(open(os.path.join(THEME_DIR, "frames.json")))
        except (OSError, ValueError):
            return                                   # keine Boot-Animation installiert
        if not meta.get("version_s"):
            return                                   # Theme ohne Versionsanzeige
        root = os.path.join(self.releases, name)
        tmp = tempfile.mkdtemp(prefix="raspy-version-")
        self.fix_owner(tmp)
        new = os.path.join(tmp, "version.png")
        try:
            self.run([os.path.join(root, "venv", "bin", "python"), os.path.join(root, "boot", "make_frames.py"),
                      "--version-image", new, name], cwd=root, timeout=120)
            target = os.path.join(THEME_DIR, "version.png")
            data = open(new, "rb").read()
            if os.path.exists(target) and open(target, "rb").read() == data:
                return
            self.rebuild_initramfs(target, data, name)
        except Exception as e:  # noqa: BLE001 – die Versionsanzeige darf ein Update nie scheitern lassen
            log(f"  Versionsanzeige beim Start nicht aktualisiert: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def initramfs_images(self):
        kernel = os.uname().release
        images = [f"/boot/initrd.img-{kernel}"]
        for suffix, fw in FIRMWARE.items():
            if kernel.endswith(suffix):
                for d in ("/boot/firmware", "/boot"):
                    if os.path.exists(os.path.join(d, fw)):
                        images.append(os.path.join(d, fw))
                        break
                break
        return kernel, [i for i in images if os.path.exists(i)]

    def rebuild_initramfs(self, target, data, name):
        kernel, images = self.initramfs_images()
        if not images:
            raise GuardError(f"kein initramfs für Kernel {kernel} gefunden")
        os.makedirs(BACKUP_DIR, exist_ok=True)
        backups = {}
        for img in images:
            bak = os.path.join(BACKUP_DIR, os.path.basename(img) + ".bak")
            atomic_write(bak, open(img, "rb").read(), 0o600)
            backups[img] = bak
        old_png = open(target, "rb").read() if os.path.exists(target) else None
        atomic_write(target, data)
        log(f"  Versionsanzeige beim Start → {name}: initramfs für {kernel} wird neu gebaut …")
        r = subprocess.run(["update-initramfs", "-u", "-k", kernel], capture_output=True, text=True, timeout=900)
        problems = [] if r.returncode == 0 else [f"update-initramfs → {r.returncode}: {r.stderr.strip()[-200:]}"]
        for img, bak in backups.items():
            if os.path.getsize(img) < 0.8 * os.path.getsize(bak):
                problems.append(f"{img} viel kleiner als vorher")
            elif shutil.which("lsinitramfs"):
                listing = subprocess.run(["lsinitramfs", img], capture_output=True, text=True, timeout=300).stdout
                if "plymouth/themes/raspy/version.png" not in listing:
                    problems.append(f"{img} enthält die Boot-Animation nicht")
        if not problems:
            log("  ✓ initramfs neu gebaut und geprüft")
            return
        log(f"  ✗ initramfs nicht in Ordnung ({'; '.join(problems)}) – alte Fassung wird zurückgespielt")
        for img, bak in backups.items():
            atomic_write(img, open(bak, "rb").read(), os.stat(bak).st_mode & 0o777 or 0o644)
        if old_png is not None:
            atomic_write(target, old_png)
        raise GuardError("initramfs zurückgespielt")

    # ───────────────────────── Selbst-Update ─────────────────────────
    def self_update(self, name):
        src = os.path.join(self.releases, name, "guard", "raspy_guard.py")
        if not os.path.exists(src) or os.geteuid() != 0:
            return
        installed = os.path.join(LIB_DIR, "raspy_guard.py")
        if os.path.exists(installed) and sha256(installed) == sha256(src):
            return
        try:
            install_self(src)
            log("  Wächter selbst aktualisiert")
        except Exception as e:  # noqa: BLE001
            log(f"  Wächter-Update übersprungen: {e}")

    # ───────────────────────── Einstieg: Start (boot) ─────────────────────────
    def boot(self):
        msg = None
        pend = self.state.get("pending")
        if pend:
            back = pend.get("from")
            log(f"Start: Umschalten auf {pend.get('to')} wurde unterbrochen – zurück auf {back}")
            if back and os.path.isdir(os.path.join(self.releases, back)):
                atomic_symlink(os.path.join("releases", back), self.current)
            self.state["pending"] = None
            self.save_state()
        cur = self.current_release()
        if not cur:
            fallback = self.best_valid()
            if not fallback:
                log("Start: keine gültige Version gefunden – nichts zu tun")
                return 1
            log(f"Start: Link 'current' fehlte/kaputt – zeigt jetzt auf {fallback}")
            atomic_symlink(os.path.join("releases", fallback), self.current)
            cur = fallback
        commit = self.release_commit(cur)
        bad = self.verify(cur, commit) if commit else ["(unbekannter Commit)"]
        if bad:
            msg = "Software Watchdog: Programmdateien werden repariert …"
            boot_message(msg)
            log(f"Start: {len(bad)} beschädigte Datei(en) in {cur}: {', '.join(bad[:8])}")
            if commit and self.repair(cur, commit):
                log("  ✓ aus Git wiederhergestellt")
            else:
                fallback = self.best_valid(exclude={cur})
                if fallback:
                    log(f"  ✗ nicht reparierbar – weiter mit {fallback}")
                    atomic_symlink(os.path.join("releases", fallback), self.current)
                    self.state.update(previous=cur)
                    cur = fallback
                else:
                    log("  ✗ nicht reparierbar und keine andere Version vorhanden")
        if not self.venv_ok(cur):
            msg = msg or "Software Watchdog: Programmdateien werden repariert …"
            boot_message(msg)
            fallback = self.best_valid(exclude={cur})
            if fallback:
                log(f"Start: Python-Umgebung von {cur} kaputt – weiter mit {fallback} (wird später neu gebaut)")
                atomic_symlink(os.path.join("releases", fallback), self.current)
                self.state["rebuild_venv"] = cur
            else:
                log(f"Start: Python-Umgebung von {cur} kaputt – wird neu gebaut, sobald das Netz da ist")
                self.state["rebuild_venv"] = cur
        self.save_state()
        if msg:
            boot_message("")
        else:
            log(f"Start: {cur} geprüft, alles in Ordnung")
        return 0

    def best_valid(self, exclude=()):
        names = [self.state.get("last_good"), self.state.get("previous")]
        if os.path.isdir(self.releases):
            names += sorted((n for n in os.listdir(self.releases) if not n.startswith(("_", "."))),
                            key=lambda n: os.path.getmtime(os.path.join(self.releases, n)), reverse=True)
        for n in names:
            if n and n not in exclude and os.path.isdir(os.path.join(self.releases, n)) and self.release_ok(n):
                return n
        return None

    # ───────────────────────── Einstieg: Durchlauf (tick) ─────────────────────────
    def tick(self, now=None, force=False):
        now = now or dt.datetime.now()
        if self.state.get("pending"):
            log("Unterbrochenes Umschalten gefunden – wird zurückgenommen")
            self.boot()
        if self.state.get("rebuild_venv"):
            self.rebuild_venv(self.state["rebuild_venv"])
        due = time.time() - self.state.get("last_fetch", 0) >= FETCH_EVERY
        if force or due:
            r = self.git("fetch", "-q", "--tags", "--force", "--prune", "origin", check=False, timeout=180)
            self.state["last_fetch"] = time.time()
            if r.returncode != 0:
                err = r.stderr.strip()[-200:]
                if self.state.get("fetch_error") != err:
                    log(f"Neue Versionen konnten nicht abgefragt werden: {err}")
                self.state["fetch_error"] = err
            else:
                self.state["fetch_error"] = None
            self.save_state()
        cand = self.candidate()
        if cand and self.state.get("prepared") != cand[0]:
            try:
                self.prepare(*cand)
            except Exception as e:  # noqa: BLE001
                log(f"  ✗ {cand[0]} nicht eingespielt – Vorbereitung fehlgeschlagen: {e}")
                self.state.setdefault("failed", []).append(cand[0])
                self.state.setdefault("history", []).append(
                    {"at": now_iso(), "from": self.current_release(), "to": cand[0], "result": f"Vorbereitung: {e}"})
                self.state["prepared"] = None
                self.save_state()
                return 1
        prepared = self.state.get("prepared")
        if not prepared:
            return self.maybe_apt(now)
        if os.path.exists(UPDATE_RUNNING):
            return 0
        win = window_left(self.cfg.get("GUARD_WINDOWS", ""), now)
        if force or (win is not None and win >= MIN_LEFT):
            return 0 if self.switch(prepared, "sofort" if force else "Zeitfenster") else 1
        return 0

    def rebuild_venv(self, name):
        commit = self.release_commit(name)
        try:
            venv = self.ensure_venv(commit)
            self.link_shared(name, venv)
            if self.venv_ok(name):
                log(f"Python-Umgebung für {name} neu gebaut")
                self.state["rebuild_venv"] = None
                self.save_state()
        except Exception as e:  # noqa: BLE001
            log(f"Python-Umgebung für {name} noch nicht neu gebaut: {e}")

    def maybe_apt(self, now):
        """System-Updates (raspy-update) in die Zeitfenster legen, wenn GUARD_APT=1."""
        if str(self.cfg.get("GUARD_APT", "0")).lower() not in ("1", "yes", "true", "ja"):
            return 0
        if time.time() - self.state.get("last_apt", 0) < APT_EVERY_DAYS * 86400 or os.path.exists(UPDATE_RUNNING):
            return 0
        win = window_left(self.cfg.get("GUARD_WINDOWS", ""), now)
        if win is None or win < MIN_LEFT or not os.path.exists("/etc/systemd/system/raspy-update.service"):
            return 0
        log("System-Updates (raspy-update) im Zeitfenster gestartet")
        self.systemctl("start", "--no-block", "raspy-update.service")
        self.state["last_apt"] = time.time()
        self.save_state()
        return 0

    # ───────────────────────── Einstieg: erste Version übernehmen ─────────────────────────
    def adopt(self):
        """Den ausgecheckten Stand von BASE als erste Version übernehmen (idempotent)."""
        if self.current_release():
            log(f"Aktive Version: {self.current_release()} – nichts zu übernehmen")
            return 0
        commit = self.git("rev-parse", "HEAD").stdout.strip()
        tag = next((n for n, c in self.tags() if c == commit), None)
        name = tag or f"c-{commit[:7]}"
        log(f"Erste Version übernommen: {name} ({commit[:7]})")
        self.make_release(name, commit)
        venv = self.ensure_venv(commit)
        self.link_shared(name, venv)
        atomic_symlink(os.path.join("releases", name), self.current)
        self.state.setdefault("releases", {})[name] = {"commit": commit, "prepared": now_iso()}
        self.state.update(last_good=name, pending=None, prepared=None)
        self.save_state()
        return 0

    # ───────────────────────── Einstieg: Überblick ─────────────────────────
    def status(self):
        cur = self.current_release()
        print(f"Aktive Version:     {cur or '–'}  ({(self.release_commit(cur) or '')[:7]})")
        print(f"Vorherige Version:  {self.state.get('previous') or '–'}")
        cand = self.candidate()
        print(f"Neue Version:       {cand[0] if cand else 'keine'}"
              + (" (vorbereitet, wartet aufs Zeitfenster)" if cand and self.state.get('prepared') == cand[0] else ""))
        wins = self.cfg.get("GUARD_WINDOWS", "")
        print(f"Zeitfenster:        {wins or '– (keine: Updates nur mit update-now)'}")
        nxt = next_window(wins, dt.datetime.now())
        if nxt:
            print(f"Nächstes Fenster:   {nxt:%a %d.%m. %H:%M}")
        if self.state.get("fetch_error"):
            print(f"Letzte Abfrage:     Fehler – {self.state['fetch_error']}")
        if self.state.get("failed"):
            print(f"Nicht eingespielt:  {', '.join(self.state['failed'][-5:])}")
        for h in self.state.get("history", [])[-5:]:
            print(f"  {h['at'][:16]}  {h.get('from') or '–'} → {h['to']}: {h['result']}")
        if cur:
            bad = self.verify(cur, self.release_commit(cur))
            print(f"Dateien:            {'alle in Ordnung' if not bad else f'{len(bad)} beschädigt: ' + ', '.join(bad[:5])}")
        print(f"Protokoll:          {LOG_FILE}")
        return 0


# ───────────────────────── Zeitfenster ─────────────────────────
def parse_windows(spec):
    """'Di,Do 11:35-11:50; Mo-Fr 16:15-18:00' -> [(Wochentage, Start-Minute, End-Minute)]."""
    out = []
    for part in re.split(r"[;\n]", spec or ""):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(.+?)\s+(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})", part)
        if not m:
            raise ValueError(f"Zeitfenster nicht verstanden: „{part}“ (Beispiel: Di,Do 11:35-11:50)")
        days_s, h1, m1, h2, m2 = m.groups()
        start, end = int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)
        if not (0 <= start < end <= 24 * 60) or int(m1) > 59 or int(m2) > 59:
            raise ValueError(f"Zeit ungültig in „{part}“ (Ende muss nach dem Anfang liegen)")
        days = set()
        for d in re.split(r"\s*,\s*", days_s.strip().lower()):
            if d in ("täglich", "taeglich", "daily", "*", "alle"):
                days |= set(range(7))
                continue
            rng = re.fullmatch(r"(\w+)\s*-\s*(\w+)", d)
            if rng:
                a, b = DAYS.get(rng.group(1)[:3]) if rng.group(1)[:3] in DAYS else DAYS.get(rng.group(1)[:2]), \
                       DAYS.get(rng.group(2)[:3]) if rng.group(2)[:3] in DAYS else DAYS.get(rng.group(2)[:2])
                if a is None or b is None:
                    raise ValueError(f"Wochentag nicht verstanden in „{part}“")
                days |= set(range(a, b + 1)) if a <= b else set(range(a, 7)) | set(range(0, b + 1))
            else:
                k = d[:3] if d[:3] in DAYS else d[:2]
                if k not in DAYS:
                    raise ValueError(f"Wochentag „{d}“ nicht verstanden (Mo, Di, Mi, Do, Fr, Sa, So)")
                days.add(DAYS[k])
        out.append((days, start, end))
    return out


def window_left(spec, now):
    """Sekunden, die vom aktuellen Zeitfenster noch übrig sind – None außerhalb."""
    try:
        wins = parse_windows(spec)
    except ValueError as e:
        log(str(e))
        return None
    mins = now.hour * 60 + now.minute + now.second / 60
    for days, start, end in wins:
        if now.weekday() in days and start <= mins < end:
            return (end - mins) * 60
    return None


def next_window(spec, now):
    try:
        wins = parse_windows(spec)
    except ValueError:
        return None
    best = None
    for add in range(8):
        day = (now + dt.timedelta(days=add)).date()
        for days, start, _ in wins:
            t = dt.datetime.combine(day, dt.time(start // 60, start % 60))
            if day.weekday() in days and t >= now and (best is None or t < best):
                best = t
    return best


# ───────────────────────── HTTP ─────────────────────────
def http_ok(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=8) as r:
        return r.status == 200


def http_json(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=8) as r:
        return json.loads(r.read().decode())


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


# ───────────────────────── sich selbst sicher installieren ─────────────────────────
def install_self(src):
    """Wächter-Datei atomar installieren; die alte bleibt als .prev (Rückfall des Starters)."""
    code = open(src, "rb").read()
    compile(code, src, "exec")
    r = subprocess.run([sys.executable, src, "selftest"], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, RASPY_GUARD_CONF="/nonexistent"))
    if r.returncode != 0:
        raise GuardError(f"Selbsttest der neuen Wächter-Datei fehlgeschlagen: {(r.stderr or r.stdout)[-300:]}")
    os.makedirs(LIB_DIR, exist_ok=True)
    main, main_sum = os.path.join(LIB_DIR, "raspy_guard.py"), os.path.join(LIB_DIR, "raspy_guard.sha256")
    prev, prev_sum = os.path.join(LIB_DIR, "raspy_guard.prev.py"), os.path.join(LIB_DIR, "raspy_guard.prev.sha256")
    digest = hashlib.sha256(code).hexdigest()
    if os.path.exists(main) and os.path.exists(main_sum) and open(main_sum).read().strip() == sha256(main):
        if open(main_sum).read().strip() == digest:
            return
        atomic_write(prev, open(main, "rb").read())        # funktionierende alte Version aufheben
        atomic_write(prev_sum, sha256(prev) + "\n")
    atomic_write(main, code)
    atomic_write(main_sum, digest + "\n")


def selftest():
    parse_windows("Di,Do 11:35-11:50; Mo-Fr 16:15-18:00; täglich 2:00-4:00")
    assert window_left("Mo-So 0:00-23:59", dt.datetime(2026, 1, 1, 12)) is not None
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="raspy-guard", description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["status", "tick", "boot", "update-now", "rollback", "adopt",
                                        "install-self", "selftest", "check-windows"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--base")
    a = ap.parse_args(argv)
    if a.command == "selftest":
        return selftest()
    if a.command == "install-self":
        install_self(a.arg)
        print(f"Wächter installiert: {LIB_DIR}/raspy_guard.py")
        return 0
    if a.command == "check-windows":
        wins = parse_windows(a.arg or "")
        nxt = next_window(a.arg or "", dt.datetime.now())
        print(f"{len(wins)} Zeitfenster ok" + (f", nächstes: {nxt:%a %d.%m. %H:%M}" if nxt else ""))
        return 0
    g = Guard(base=a.base)
    if a.command == "status":
        return g.status()
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if a.command == "tick" else 0))
    except BlockingIOError:
        return 0                    # läuft schon
    if a.command == "tick":
        return g.tick()
    if a.command == "boot":
        return g.boot()
    if a.command == "adopt":
        return g.adopt()
    if a.command == "rollback":
        return 0 if g.rollback() else 1
    if a.command == "update-now":
        g.state["last_fetch"] = 0
        return g.tick(force=True)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GuardError as e:
        log(f"Fehler: {e}")
        sys.exit(1)
