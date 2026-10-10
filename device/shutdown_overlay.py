#!/usr/bin/env python3
"""
Vollbild-Overlay beim Gedrückthalten der Aus-Taste (Button 2).

Wird von button.py gestartet:
    python3 shutdown_overlay.py <show_at> <end_at> [update]
(beides Unix-Zeitstempel). Das Fenster bleibt unsichtbar bis show_at und zählt
dann bis end_at herunter (3 … 1, im Update-Modus 5 … 1).
Modus "update": laufen gerade System-Updates, wird darauf hingewiesen; wer
trotzdem bis zum Ende hält, bricht die Updates sicher ab und schaltet aus. button.py beendet diesen Prozess, sobald
die Taste vorher losgelassen wird, und löst selbst den Shutdown aus – dieses
Skript zeigt nur an.

Transparenz: xcompmgr läuft mit "-a" (serverseitig) und kann keine
Fenster-Transparenz. Deshalb wird beim Start ein Screenshot gemacht,
weichgezeichnet und abgedunkelt als Hintergrund verwendet (Milchglas-Effekt).
"""
import io
import math
import sys
import time
import tkinter as tk

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageGrab

SHOW_AT = float(sys.argv[1]) if len(sys.argv) > 1 else time.time()
END_AT = float(sys.argv[2]) if len(sys.argv) > 2 else SHOW_AT + 3.0
COUNTDOWN = END_AT - SHOW_AT
UPDATE_MODE = len(sys.argv) > 3 and sys.argv[3] == "update"

# ── Aussehen ──
DIM_COLOR = (10, 10, 12)      # Abdunkel-Farbe für den Hintergrund
DIM = 0.58                    # 0 = nur Blur, 1 = komplett dunkel
BLUR = 6                      # Weichzeichner (auf 1/4-Auflösung)
TEXT = "#f4f4f5"
SUBTEXT = "#a1a1a6"
RED = (214, 59, 50, 255)
TRACK_FILL = (255, 255, 255, 26)
TRACK_LINE = (255, 255, 255, 80)
TRAIL = (214, 59, 50, 90)     # halbtransparentes Rot hinter dem Knopf
if UPDATE_MODE:               # Bernstein statt Rot: "Achtung, lieber warten"
    RED = (232, 160, 48, 255)
    TRAIL = (232, 160, 48, 90)

if UPDATE_MODE:
    TITLE = "Updates werden gerade installiert"
    HINT = "Bitte warten – zum sicheren Abbrechen und Ausschalten gedrückt halten"
    DONE_TITLE = "Updates werden sicher beendet …"
    DONE_HINT = "Danach schaltet sich der Pi aus"
else:
    TITLE = "Zum Ausschalten gedrückt halten"
    HINT = "Loslassen zum Abbrechen"
    DONE_TITLE = "Wird ausgeschaltet …"
    DONE_HINT = ""

FONT_REG = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"
FONT_FAMILY = "Noto Sans"

TRACK_W, TRACK_H = 760, 150   # Größe des Sliders in Bildschirm-Pixeln
KNOB_W = 250
PAD = 9
SS = 3                        # Supersampling für glatte Kanten
FPS = 40

_number_font = ImageFont.truetype(FONT_REG, int((TRACK_H - 2 * PAD) * 0.62 * SS))


def make_background(w, h):
    """Screenshot -> weichgezeichnet + abgedunkelt. Fallback: einfarbig dunkel."""
    try:
        shot = ImageGrab.grab(xdisplay=":0").convert("RGB")
        small = shot.resize((shot.width // 4, shot.height // 4)).filter(ImageFilter.GaussianBlur(BLUR))
        bg = small.resize((w, h), Image.BILINEAR)
        bg = Image.blend(bg, Image.new("RGB", (w, h), DIM_COLOR), DIM)
    except Exception:
        bg = Image.new("RGB", (w, h), DIM_COLOR)
    buf = io.BytesIO()
    bg.save(buf, format="PPM")
    return buf.getvalue()


def render_slider(progress, number):
    """Zeichnet den Slider (RGBA, geglättet) und gibt PNG-Daten für Tk zurück."""
    w, h = TRACK_W * SS, TRACK_H * SS
    pad, kw = PAD * SS, KNOB_W * SS
    r = h // 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Schiene
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=TRACK_FILL,
                        outline=TRACK_LINE, width=2 * SS)

    # Knopf-Position (leicht ease-out, damit es sich weich anfühlt)
    p = max(0.0, min(1.0, progress))
    eased = 1 - (1 - p) ** 1.6
    x0 = pad + eased * (w - 2 * pad - kw)
    kr = (h - 2 * pad) // 2

    # Spur hinter dem Knopf
    if x0 > pad:
        d.rounded_rectangle((pad, pad, x0 + kw, h - pad), radius=kr, fill=TRAIL)

    # Knopf
    d.rounded_rectangle((x0, pad, x0 + kw, h - pad), radius=kr, fill=RED)

    # Zahl
    if number is not None:
        d.text((x0 + kw / 2, h / 2), str(number), font=_number_font,
               fill=(255, 255, 255, 255), anchor="mm")

    img = img.resize((TRACK_W, TRACK_H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def main():
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{sw}x{sh}+0+0")
    root.configure(cursor="none")

    canvas = tk.Canvas(root, width=sw, height=sh, highlightthickness=0, bd=0, bg="#0a0a0c")
    canvas.pack()

    # Hintergrund erst kurz vor dem Einblenden aufnehmen (aktueller Bildschirminhalt)
    state = {"shown": False, "done": False, "last": None, "bg": None, "slider": None}

    cx, cy = sw // 2, sh // 2
    title = canvas.create_text(cx, cy - 170, text=TITLE,
                               font=(FONT_FAMILY, 46), fill=TEXT)
    slider_item = canvas.create_image(cx, cy, anchor="center")
    hint = canvas.create_text(cx, cy + 150, text=HINT,
                              font=(FONT_FAMILY, 22), fill=SUBTEXT)

    def set_slider(progress, number):
        img = tk.PhotoImage(data=render_slider(progress, number))
        canvas.itemconfigure(slider_item, image=img)
        state["slider"] = img  # Referenz halten

    def tick():
        now = time.time()
        if now < SHOW_AT:
            # ~0,25 s vorher Hintergrund + ersten Frame vorbereiten
            if state["bg"] is None and SHOW_AT - now < 0.25:
                state["bg"] = tk.PhotoImage(data=make_background(sw, sh))
                bg_item = canvas.create_image(0, 0, anchor="nw", image=state["bg"])
                canvas.tag_lower(bg_item)
                set_slider(0.0, round(COUNTDOWN))
            root.after(15, tick)
            return

        if not state["shown"]:
            if state["bg"] is None:
                state["bg"] = tk.PhotoImage(data=make_background(sw, sh))
                bg_item = canvas.create_image(0, 0, anchor="nw", image=state["bg"])
                canvas.tag_lower(bg_item)
            root.deiconify()
            root.lift()
            root.attributes("-topmost", True)
            state["shown"] = True

        progress = (now - SHOW_AT) / COUNTDOWN if COUNTDOWN > 0 else 1.0
        if progress >= 1.0:
            if not state["done"]:
                state["done"] = True
                set_slider(1.0, None)
                canvas.itemconfigure(title, text=DONE_TITLE)
                canvas.itemconfigure(hint, text=DONE_HINT)
            root.after(200, tick)
            return

        number = max(1, math.ceil(round(COUNTDOWN) * (1 - progress)))
        key = (round(progress * 400), number)
        if key != state["last"]:
            state["last"] = key
            set_slider(progress, number)
        root.after(int(1000 / FPS), tick)

    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
