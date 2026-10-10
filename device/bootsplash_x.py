#!/usr/bin/env python3
"""
Boot-Animation im X fortsetzen, bis die Heute-Seite in Chromium bereit ist.

Ablauf beim Booten:
  Plymouth-Theme "raspy" (Logo baut sich auf, danach Welle)
  -> LightDM übernimmt Plymouths VT, das letzte Bild bleibt stehen (retain-splash)
  -> dieses Skript (gestartet von /usr/local/sbin/raspy-display-setup) zeigt
     dieselben Bilder im selben Zeittakt weiter, während darunter Desktop und
     Chromium starten
  -> sobald das Fenster "Heute - Chromium" existiert (die Seite setzt den Titel
     erst nach dem ersten Laden), noch die Welle zu Ende, dann ausblenden.

Der Desktop wird dabei ganz normal gestartet – er ist nur verdeckt. Wird Chromium
später beendet, ist er wie gewohnt da.

Test ohne Neustart:  sudo -E python3 bootsplash_x.py --demo 8
"""
import argparse
import json
import os
import subprocess
import time
import tkinter as tk

THEME = "/usr/share/plymouth/themes/raspy"
READY_FLAG = "/tmp/bootsplash_ready"      # Fenster ist sichtbar (für raspy-display-setup)
READY_TITLE = "^Heute - Chromium$"
MAX_WAIT = 60        # spätestens dann ausblenden (z. B. falls Chromium nicht startet)
SETTLE = 0.3         # nach "bereit" kurz warten, bis die Seite fertig gezeichnet ist
CHECK_EVERY = 0.25


def log(msg):
    print(f"[{time.monotonic():8.2f}] {msg}", flush=True)


PLYMOUTH_T0 = 1.38   # CLOCK_MONOTONIC, zu der das Plymouth-Skript startet (gemessen am 25.09.2026)


def plymouth_t0():
    """Startzeit des Plymouth-Skripts – exakt aus dem Debug-Log dieses Boots, sonst Messwert."""
    try:
        log = "/var/log/plymouth-debug.log"
        booted = time.time() - time.monotonic()
        if os.path.getmtime(log) > booted:
            for line in open(log, errors="replace"):
                if "executing script file" in line:
                    h, m, sec = line.split()[0].split(":")
                    return int(h) * 3600 + int(m) * 60 + float(sec)
    except (OSError, ValueError):
        pass
    return PLYMOUTH_T0


def xorg_start():
    """Startzeit von X (CLOCK_MONOTONIC) = der Moment, in dem Plymouth sein letztes Bild zeigte."""
    try:
        pid = subprocess.run(["pidof", "Xorg"], capture_output=True, text=True, timeout=2).stdout.split()[0]
        with open(f"/proc/{pid}/stat") as f:
            ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        return ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def chromium_ready():
    try:
        return subprocess.run(["xdotool", "search", "--onlyvisible", "--name", READY_TITLE],
                              capture_output=True, timeout=2).returncode == 0
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", type=float, help="Test: so viele Sekunden laufen, dann ausblenden")
    a = ap.parse_args()

    m = json.load(open(os.path.join(THEME, "frames.json")))
    fps, intro_n, loop_n, outro_n = m["fps"], m["intro_n"], m["loop_n"], m["outro_n"]
    cw, ch = m["canvas"]
    rest_from = m["loop_rest_from"]

    root = tk.Tk()
    root.withdraw()
    root.configure(bg="black")      # sonst malt X beim Einblenden kurz Tk-Hellgrau (weißer Blitz)
    root.overrideredirect(True)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{sw}x{sh}+0+0")
    c = tk.Canvas(root, width=sw, height=sh, bg="black", highlightthickness=0, bd=0, cursor="none")
    c.pack()
    base_x, base_y = (sw - cw) // 2, (sh - ch) // 2   # wie im Plymouth-Skript
    item = c.create_image(0, 0, anchor="nw")

    cache = {}

    def image(name):
        if name not in cache:
            cache[name] = tk.PhotoImage(file=os.path.join(THEME, name + ".png"))
        return cache[name]

    def show(name, off):
        c.itemconfigure(item, image=image(name))
        c.coords(item, base_x + off[0], base_y + off[1])

    # Nahtlos weitermachen: genau bei dem Bild, bei dem Plymouth stehen geblieben ist
    # (das Bild blieb dank "-background none" bis jetzt auf dem Schirm).
    freeze = None if a.demo else xorg_start()
    if a.demo:
        t_start = 0.0
    elif freeze is not None:
        t_start = max(0.0, freeze - plymouth_t0())
    else:
        t_start = intro_n / fps
    if not a.demo:
        t_start = max(0.0, t_start - m.get("start_delay", 0))   # Schwarz-Vorlauf abziehen
    started = time.monotonic()
    t0 = started - t_start           # virtuelle Animations-Startzeit
    log(f"Start bei Animationszeit {t_start:.2f} s (X-Start {freeze})")

    st = {"phase": "run", "ready_at": None, "next_check": 0.0, "outro_at": None, "last": None}

    def frame_index(now):
        n = int((now - t0) * fps)
        if n < intro_n:
            return n, n >= intro_n - 7                  # Ende des Aufbaus = Ruhelage
        k = (n - intro_n) % loop_n
        return intro_n + k, k >= rest_from or k <= 1    # (Bild, in Ruhelage?)

    def keep_on_top():
        # sehr oft nach oben holen, damit neu erscheinende Fenster (Chromium ist anfangs
        # weiß, Panel, Desktop) nicht kurz davor aufblitzen
        root.lift()
        root.after(5, keep_on_top)

    def tick():
        now = time.monotonic()

        if st["phase"] == "run":
            idx, at_rest = frame_index(now)
            if idx != st["last"]:
                show(f"f{idx}", m["offsets"][idx])
                st["last"] = idx

            if st["ready_at"] is None and now >= st["next_check"]:
                st["next_check"] = now + CHECK_EVERY
                if a.demo:
                    ready = now - started >= a.demo
                else:
                    ready = chromium_ready() or now - started >= MAX_WAIT
                if ready:
                    st["ready_at"] = now
                    log("Chromium bereit" if not a.demo else "Demo-Ende")

            # erst ausblenden, wenn die Welle in Ruhelage ist (kein Sprung im Bild)
            if st["ready_at"] and now - st["ready_at"] >= SETTLE and at_rest:
                st["phase"], st["outro_at"] = "outro", now
                log("Ausblenden")

        if st["phase"] == "outro":
            k = int((now - st["outro_at"]) * fps)
            if k < outro_n:
                show(f"o{k}", m["outro_offsets"][k])
            elif k < outro_n + 5:          # 0,1 s Schwarz, dann Chromium freigeben
                c.itemconfigure(item, image="")
            else:
                log("Ende")
                root.destroy()
                return
        root.after(20, tick)

    # erstes Bild zeichnen, dann sichtbar machen und Bescheid geben
    idx, _ = frame_index(time.monotonic())
    show(f"f{idx}", m["offsets"][idx])
    root.deiconify()
    root.lift()
    root.update()
    try:
        open(READY_FLAG, "w").close()
    except OSError:
        pass
    log("Fenster sichtbar")
    keep_on_top()
    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
