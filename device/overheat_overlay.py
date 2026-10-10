#!/usr/bin/env python3
"""
Vollbild-Warnung bei Überhitzung (gestartet von thermal_guard.py).

    python3 overheat_overlay.py <end_at> [fake_temp_file]

Zeigt einen Countdown bis end_at (Unix-Zeit) und die aktuelle Temperatur.
thermal_guard.py beendet diesen Prozess, wenn der Pi rechtzeitig abkühlt,
und fährt sonst selbst herunter – dieses Skript zeigt nur an.
Hintergrund wie beim Aus-Overlay: weichgezeichneter Screenshot (xcompmgr -a
kann keine echte Transparenz).
"""
import io
import math
import sys
import time
import tkinter as tk

from PIL import Image, ImageFilter, ImageGrab

END_AT = float(sys.argv[1]) if len(sys.argv) > 1 else time.time() + 20
FAKE = sys.argv[2] if len(sys.argv) > 2 else None

TINT = (70, 8, 6)          # dunkles Rot
TINT_STRENGTH = 0.72
TEXT = "#ffffff"
SUB = "#ffc9c4"
RED = "#ff5a52"
FONT = "Inter"


def read_temp():
    try:
        with open(FAKE or "/sys/class/thermal/thermal_zone0/temp") as f:
            v = float(f.read().strip())
        return v if FAKE else v / 1000
    except (OSError, ValueError):
        return None


def background(w, h):
    try:
        shot = ImageGrab.grab(xdisplay=":0").convert("RGB")
        small = shot.resize((shot.width // 4, shot.height // 4)).filter(ImageFilter.GaussianBlur(6))
        bg = small.resize((w, h), Image.BILINEAR)
        bg = Image.blend(bg, Image.new("RGB", (w, h), TINT), TINT_STRENGTH)
    except Exception:
        bg = Image.new("RGB", (w, h), TINT)
    buf = io.BytesIO()
    bg.save(buf, format="PPM")
    return buf.getvalue()


def main():
    root = tk.Tk()
    root.overrideredirect(True)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{sw}x{sh}+0+0")
    root.configure(cursor="none")
    c = tk.Canvas(root, width=sw, height=sh, highlightthickness=0, bd=0, bg="#2a0503")
    c.pack()
    bg = tk.PhotoImage(data=background(sw, sh))
    c.create_image(0, 0, anchor="nw", image=bg)

    cx, cy = sw // 2, sh // 2
    # Warnsymbol (Dreieck mit Ausrufezeichen)
    s = 70
    c.create_polygon(cx, cy - 330 - s, cx - s * 1.15, cy - 330 + s * 0.85, cx + s * 1.15, cy - 330 + s * 0.85,
                     outline=RED, width=7, fill="", joinstyle="round")
    c.create_line(cx, cy - 330 - s * 0.35, cx, cy - 330 + s * 0.3, fill=RED, width=9, capstyle="round")
    c.create_oval(cx - 6, cy - 330 + s * 0.52, cx + 6, cy - 330 + s * 0.64, fill=RED, outline=RED)

    c.create_text(cx, cy - 170, text="Überhitzung", font=(FONT, 64, "bold"), fill=TEXT)
    c.create_text(cx, cy - 90, text="Der Raspberry Pi wird zum Schutz heruntergefahren",
                  font=(FONT, 30), fill=SUB)
    num = c.create_text(cx, cy + 60, text="", font=(FONT, 150, "bold"), fill=TEXT)
    temp = c.create_text(cx, cy + 190, text="", font=(FONT, 30), fill=SUB)
    c.create_text(cx, cy + 260, text="Wird automatisch abgebrochen, wenn er rechtzeitig abkühlt.",
                  font=(FONT, 22), fill=SUB)

    root.lift()
    root.attributes("-topmost", True)

    def tick():
        left = END_AT - time.time()
        c.itemconfigure(num, text=str(max(0, math.ceil(left))) if left > 0 else "…")
        t = read_temp()
        if t is not None:
            c.itemconfigure(temp, text=f"CPU-Temperatur: {t:.1f} °C")
        root.after(250, tick)

    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
