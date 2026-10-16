#!/usr/bin/env python3
"""
Überhitzungsschutz + stufenlose Lüftersteuerung (läuft als raspy-thermal.service).

Lüfter:
  Statt der 4 harten Kernel-Stufen wird die Drehzahl gleitend nach FAN_CURVE
  gesetzt und nur langsam verändert (kein hörbares Hochspringen).
  Die Kernel-Kurve in /boot/firmware/config.txt liegt bewusst höher (ab 82 °C)
  und greift nur noch als Notfall-Sicherung, falls dieser Dienst ausfällt.

Notabschaltung:
  1. CPU >= CRIT_TEMP für CRIT_SECONDS Sekunden (oder sofort ab PANIC_TEMP)
  2. -> Vollbild-Warnung (overheat_overlay.py) mit Countdown COUNTDOWN Sekunden
  3. Fällt die Temperatur währenddessen unter ABORT_TEMP -> Abbruch
  4. Sonst: sudo shutdown now

Zustand wird nach /run/raspy/thermal_guard.json geschrieben (Anzeige im Thermal-Dashboard).

Beim Beenden wird der Lüfter wieder dem Kernel übergeben (ursprünglicher pwm1_enable-Wert),
damit die Kernel-Kurve als Sicherung greift. Nach einem Absturz erledigt das ExecStopPost
in raspy-thermal.service mit der Datei /run/raspy/fan-restore.

Test ohne echte Hitze und ohne echten Shutdown:
  python3 thermal_guard.py --dry-run --fake-temp-file /tmp/faketemp
  (Temperatur in °C in die Datei schreiben, z. B. echo 87 > /tmp/faketemp)
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import config  # noqa: E402

CRIT_TEMP = 85.0       # ab hier kritisch (Pi 5 drosselt selbst ab ~85 °C)
CRIT_SECONDS = 10      # so lange muss es kritisch bleiben, bevor gewarnt wird
PANIC_TEMP = 90.0      # ab hier sofort Countdown
ABORT_TEMP = 82.0      # darunter wird der Countdown abgebrochen
COUNTDOWN = 20         # Sekunden Warnung vor dem Herunterfahren

# (°C, PWM 0–255) – dazwischen linear. Gemessen: PWM 60≈1.600, 100≈3.100,
# 125≈3.900, 150≈4.750, 200≈6.300, 255≈8.100 U/min
FAN_CURVE = [(45, 0), (50, 55), (60, 70), (68, 100), (73, 120), (77, 150), (80, 200), (83, 255)]
FAN_STEP_UP = 3        # max. PWM-Änderung pro Sekunde nach oben (sanft)
FAN_STEP_DOWN = 1      # nach unten noch langsamer (kein Pendeln)
FAN_SMOOTH = 0.15      # Glättung der Temperatur (EMA)

RUN_DIR = config.run_dir()
STATE_FILE = os.path.join(RUN_DIR, "thermal_guard.json")
RESTORE_FILE = os.path.join(RUN_DIR, "fan-restore")   # "<pfad zu pwm1_enable> <wert>" für ExecStopPost
OVERLAY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overheat_overlay.py")
DISPLAY_ENV = {**os.environ, "DISPLAY": ":0", "XAUTHORITY": os.path.expanduser("~/.Xauthority")}

sys.stdout.reconfigure(line_buffering=True)


def read_temp(fake_file):
    path = fake_file or "/sys/class/thermal/thermal_zone0/temp"
    try:
        with open(path) as f:
            v = float(f.read().strip())
        return v if fake_file else v / 1000
    except (OSError, ValueError):
        return None


def fan_target(temp):
    if temp <= FAN_CURVE[0][0]:
        return FAN_CURVE[0][1]
    for (t0, p0), (t1, p1) in zip(FAN_CURVE, FAN_CURVE[1:]):
        if temp <= t1:
            return round(p0 + (p1 - p0) * (temp - t0) / (t1 - t0))
    return FAN_CURVE[-1][1]


class Fan:
    """Schreibt PWM direkt in sysfs (Rechte per udev-Regel 99-fan-control.rules)."""

    def __init__(self):
        import glob
        self.dir = None
        for d in glob.glob("/sys/class/hwmon/hwmon*"):   # Nummer nicht stabil -> per Name
            try:
                if open(os.path.join(d, "name")).read().strip() == "pwmfan":
                    self.dir = d
            except OSError:
                pass
        self.pwm = None
        self.smooth_temp = None
        self.orig_enable = None
        if self.dir:
            try:
                v = open(os.path.join(self.dir, "pwm1_enable")).read().strip()
                # "1" = manuell, z. B. von einem abgestürzten Lauf übrig -> dann Automatik (2)
                self.orig_enable = v if v and v != "1" else "2"
            except OSError:
                self.orig_enable = "2"
            try:
                with open(RESTORE_FILE, "w") as f:
                    f.write(f"{os.path.join(self.dir, 'pwm1_enable')} {self.orig_enable}\n")
            except OSError as e:
                print(f"Wiederherstellungs-Datei nicht geschrieben: {e}")

    def restore(self):
        """Lüfter wieder dem Kernel übergeben (dessen Kurve ist die Notfall-Sicherung)."""
        if self.dir is None or self.orig_enable is None:
            return
        try:
            with open(os.path.join(self.dir, "pwm1_enable"), "w") as f:
                f.write(self.orig_enable)
            print(f"Lüfter an Kernel zurückgegeben (pwm1_enable={self.orig_enable})")
        except OSError as e:
            print(f"Lüfter konnte nicht zurückgegeben werden: {e}")

    def update(self, temp):
        if self.dir is None or temp is None:
            return
        self.smooth_temp = temp if self.smooth_temp is None else \
            self.smooth_temp + FAN_SMOOTH * (temp - self.smooth_temp)
        target = fan_target(self.smooth_temp)
        if temp >= FAN_CURVE[-1][0]:
            target = 255           # Notfall: sofort voll, ohne Rampe
        if self.pwm is None:
            try:
                self.pwm = int(open(os.path.join(self.dir, "pwm1")).read())
            except (OSError, ValueError):
                self.pwm = target
        if target > self.pwm:
            new = target if target == 255 else min(target, self.pwm + FAN_STEP_UP)
        else:
            new = max(target, self.pwm - FAN_STEP_DOWN)
        try:
            with open(os.path.join(self.dir, "pwm1_enable"), "w") as f:
                f.write("1")
            with open(os.path.join(self.dir, "pwm1"), "w") as f:
                f.write(str(new))
            self.pwm = new
        except OSError as e:
            print(f"Lüfter konnte nicht gesetzt werden: {e}")

    def rpm(self):
        try:
            return int(open(os.path.join(self.dir, "fan1_input")).read())
        except (OSError, ValueError, TypeError):
            return None


def write_state(state, temp, countdown_end=None, fan=None):
    data = {
        "state": state, "temp": temp, "updated": time.time(),
        "crit_temp": CRIT_TEMP, "crit_seconds": CRIT_SECONDS,
        "abort_temp": ABORT_TEMP, "countdown": COUNTDOWN, "countdown_end": countdown_end,
        "fan_mode": "software" if fan else "kernel",
        "fan_pwm": fan.pwm if fan else None,
        "fan_target": fan_target(fan.smooth_temp) if fan and fan.smooth_temp is not None else None,
        "fan_curve": FAN_CURVE,
    }
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, STATE_FILE)


def start_overlay(end_at, fake_file):
    args = ["python3", OVERLAY, str(end_at)]
    if fake_file:
        args.append(fake_file)
    try:
        return subprocess.Popen(args, env=DISPLAY_ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"Overlay konnte nicht gestartet werden: {e}")
        return None


def stop_overlay(proc):
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="nicht wirklich herunterfahren")
    ap.add_argument("--fake-temp-file", help="Temperatur (°C) aus Datei statt Sensor")
    ap.add_argument("--no-fan", action="store_true", help="Lüfter nicht steuern")
    a = ap.parse_args()
    # Test-Instanzen (dry-run/fake) steuern nie den echten Lüfter
    fan = None if (a.no_fan or a.dry_run or a.fake_temp_file) else Fan()

    # systemctl stop / Herunterfahren schickt SIGTERM -> sauber beenden (finally gibt den Lüfter zurück)
    def stop(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    try:
        run(a, fan)
    finally:
        if fan:
            fan.restore()


def run(a, fan):
    state, crit_since, end_at, overlay = "ok", None, None, None
    print(f"Überhitzungsschutz aktiv: kritisch ab {CRIT_TEMP} °C ({CRIT_SECONDS} s), "
          f"Countdown {COUNTDOWN} s, Abbruch unter {ABORT_TEMP} °C" + (" [DRY-RUN]" if a.dry_run else ""))

    while True:
        temp = read_temp(a.fake_temp_file)
        now = time.time()
        if fan:
            fan.update(temp)

        if temp is not None:
            if state == "ok" and temp >= CRIT_TEMP:
                state, crit_since = "critical", now
                print(f"Kritisch: {temp:.1f} °C")

            elif state == "critical":
                if temp < CRIT_TEMP:
                    state = "ok"
                    print(f"Wieder normal: {temp:.1f} °C")
                elif now - crit_since >= CRIT_SECONDS or temp >= PANIC_TEMP:
                    state, end_at = "countdown", now + COUNTDOWN
                    print(f"Überhitzung {temp:.1f} °C → Warnung, Shutdown in {COUNTDOWN} s")
                    overlay = start_overlay(end_at, a.fake_temp_file)

            elif state == "countdown":
                if temp < ABORT_TEMP:
                    print(f"Abgekühlt auf {temp:.1f} °C → Shutdown abgebrochen")
                    stop_overlay(overlay)
                    state, overlay, end_at = "ok", None, None
                elif now >= end_at:
                    print(f"Überhitzung {temp:.1f} °C → Shutdown")
                    state = "shutdown"
                    if not a.dry_run:
                        write_state(state, temp, None, fan)
                    if a.dry_run:
                        print("[DRY-RUN] würde jetzt 'sudo shutdown now' ausführen")
                    else:
                        subprocess.run(["sudo", "shutdown", "now"])

        if not (a.dry_run or a.fake_temp_file):   # Test-Instanzen überschreiben den echten Zustand nicht
            write_state(state, temp, end_at, fan)
        if state == "shutdown" and a.dry_run:
            time.sleep(3)
            stop_overlay(overlay)
            state, overlay, end_at = "ok", None, None
        time.sleep(1)


if __name__ == "__main__":
    main()
