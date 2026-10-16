"""
Thermal-Dashboard (/thermal): Temperatur, Lüfter, CPU-Last, Prozesse.

Ein Hintergrund-Thread misst alle 2 s und hält die letzten 15 min im Speicher.
Die Seite wird von button.py als eigenes Chromium-Fenster geöffnet
(beide Tasten 2 s halten) und mit der Tab-Taste wieder geschlossen.

Der Überhitzungsschutz selbst läuft unabhängig davon als raspy-thermal.service
(device/thermal_guard.py) und schreibt seinen Zustand nach /run/raspy/thermal_guard.json.
"""
import glob
import json
import os
import subprocess
import threading
import time
from collections import deque

import psutil
from flask import jsonify, render_template

import config

SAMPLE_INTERVAL = 2          # Sekunden
HISTORY_SECONDS = 15 * 60
GUARD_STATE_FILE = os.path.join(config.run_dir(), "thermal_guard.json")

# Anzeige-Schwellen (die Notabschaltung selbst kommt aus thermal_guard.py)
TEMP_WARN = 75
TEMP_FAN_EMERGENCY = 80       # ab hier dreht der Lüfter stark auf (Software-Kurve in thermal_guard.py)
FAN_MAX_RPM = 9200            # PWM 255 gemessen (warm: bis ~9.100)


def _read(path, cast=str):
    try:
        with open(path) as f:
            return cast(f.read().strip())
    except (OSError, ValueError):
        return None


def _find_fan_hwmon():
    # hwmon-Nummer ist nicht stabil -> per Name suchen
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        if _read(os.path.join(d, "name")) == "pwmfan":
            return d
    return None


def _cooling_device():
    for d in glob.glob("/sys/class/thermal/cooling_device*"):
        if _read(os.path.join(d, "type")) == "pwm-fan":
            return d
    return None


THROTTLE_BITS = {
    0: "Unterspannung", 1: "Takt begrenzt", 2: "Gedrosselt", 3: "Temperaturlimit",
}


def read_throttled():
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3).stdout
        val = int(out.strip().split("=")[1], 16)
    except Exception:
        return None
    now = [label for bit, label in THROTTLE_BITS.items() if val & (1 << bit)]
    past = [label for bit, label in THROTTLE_BITS.items() if val & (1 << (bit + 16))]
    return {"now": now, "since_boot": past}


class Sampler:
    def __init__(self):
        n = HISTORY_SECONDS // SAMPLE_INTERVAL
        self.hist = {k: deque(maxlen=n) for k in ("t", "temp", "cpu", "rpm")}
        self.cur = {}
        self.throttled = None
        self.fan = _find_fan_hwmon()
        self.cool = _cooling_device()
        self.lock = threading.Lock()
        psutil.cpu_percent(percpu=True)  # Startwert

    def sample(self):
        temp = _read("/sys/class/thermal/thermal_zone0/temp", int)
        temp = round(temp / 1000, 1) if temp is not None else None
        per_core = psutil.cpu_percent(percpu=True)
        cpu = round(sum(per_core) / len(per_core), 1) if per_core else 0
        rpm = _read(os.path.join(self.fan, "fan1_input"), int) if self.fan else None
        pwm = _read(os.path.join(self.fan, "pwm1"), int) if self.fan else None
        stage = _read(os.path.join(self.cool, "cur_state"), int) if self.cool else None
        stage_max = _read(os.path.join(self.cool, "max_state"), int) if self.cool else None
        freq = psutil.cpu_freq()
        mem = psutil.virtual_memory()
        now = time.time()
        with self.lock:
            self.cur = {
                "temp": temp, "cpu": cpu, "cores": per_core,
                "rpm": rpm, "pwm_pct": round(pwm / 255 * 100) if pwm is not None else None,
                "stage": stage, "stage_max": stage_max,
                "freq_ghz": round(freq.current / 1000, 2) if freq else None,
                "mem_pct": round(mem.percent), "mem_used_gb": round(mem.used / 1e9, 1),
                "mem_total_gb": round(mem.total / 1e9, 1),
                "load": [round(x, 2) for x in os.getloadavg()],
                "uptime_s": int(now - psutil.boot_time()),
            }
            for k, v in (("t", int(now)), ("temp", temp), ("cpu", cpu), ("rpm", rpm)):
                self.hist[k].append(v)

    def loop(self):
        n = 0
        while True:
            try:
                self.sample()
                if n % 5 == 0:
                    self.throttled = read_throttled()
            except Exception as e:
                print(f"[thermal] Messung fehlgeschlagen: {e}", flush=True)
            n += 1
            time.sleep(SAMPLE_INTERVAL)

    def snapshot(self):
        with self.lock:
            return dict(self.cur), {k: list(v) for k, v in self.hist.items()}, self.throttled


def top_processes(n=6):
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        info = p.info
        if info["cpu_percent"] is None:
            continue
        procs.append(info)
    procs.sort(key=lambda x: x["cpu_percent"], reverse=True)
    return [{"name": p["name"], "cpu": round(p["cpu_percent"], 1),
             "mem": round(p["memory_percent"] or 0, 1)} for p in procs[:n]]


def guard_state():
    try:
        with open(GUARD_STATE_FILE) as f:
            g = json.load(f)
        g["alive"] = time.time() - g.get("updated", 0) < 15
        return g
    except (OSError, ValueError):
        return {"alive": False}


_sampler = None


def init(app):
    global _sampler
    _sampler = Sampler()

    @app.route("/thermal")
    def thermal_page():
        return render_template("thermal.html")

    @app.route("/api/thermal")
    def api_thermal():
        cur, hist, throttled = _sampler.snapshot()
        return jsonify({
            "cur": cur, "hist": hist, "throttled": throttled,
            "procs": top_processes(), "guard": guard_state(),
            "limits": {"warn": TEMP_WARN, "fan_emergency": TEMP_FAN_EMERGENCY, "fan_max_rpm": FAN_MAX_RPM},
        })


def start():
    threading.Thread(target=_sampler.loop, daemon=True).start()
