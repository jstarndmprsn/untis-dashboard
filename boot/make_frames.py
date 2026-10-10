#!/usr/bin/env python3
"""
Erzeugt die Boot-Animation aus dem Logo (logo.svg: 3 Punkte + 2 Säulen).

Ausgabe (Ordner raspy/ = Plymouth-Theme):
  f0.png … fN.png   Intro (Aufbau) + Loop (Welle)   -> Plymouth + X-Splash
  o0.png … oM.png   Outro (Ausblenden)              -> nur X-Splash
  frames.json       Metadaten (fps, Anzahl, Versatz jedes Bildes)
  raspy.script      Plymouth-Skript (mit eingetragenen Versätzen)
  raspy.plymouth    Theme-Beschreibung

Alle Bilder werden auf ihren Inhalt zugeschnitten (weniger Speicher in Plymouth);
der Versatz relativ zur Leinwand steht in frames.json bzw. im Skript.
"""
import json
import math
import os
import sys

from PIL import Image, ImageChops, ImageDraw

OUT = sys.argv[1] if len(sys.argv) > 1 else "raspy"

FPS = 50
START_DELAY = 2.0       # Sekunden Schwarz beim Booten, bis der Monitor aufgewacht ist
INTRO_S, LOOP_S, OUTRO_S = 1.6, 2.0, 0.56
SCALE = 0.95            # Logo-Einheiten (SVG 294×146) -> Pixel
SS = 4                  # Supersampling
CW, CH = 380, 270       # Leinwand (Logo zentriert, Platz für Bewegung)
LX = (CW - 294 * SCALE) / 2
LY = (CH - 146 * SCALE) / 2
LOGO_C = (147, 73)      # Mittelpunkt des Logos (für Outro-Skalierung)


# ── Easing ──
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_out_back(x, c1=1.70158):
    c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def ease_out_cubic(x):
    return 1 - (1 - x) ** 3


def ease_in_out_cubic(x):
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def spring(u, damp=7.0, freq=9.0):
    """Gedämpfte Feder 0 -> 1, endet exakt bei 1."""
    p = 1 - math.exp(-damp * u) * math.cos(freq * u)
    p_end = 1 - math.exp(-damp) * math.cos(freq)
    return p + (1 - p_end) * u


def seg(t, start, dur):
    return clamp((t - start) / dur)


# ── Elemente in Ruhelage (Logo-Einheiten) ──
# ("dot", cx, cy, r) / ("pill", cx, bottom, w, h)
def rest():
    return [
        {"k": "dot", "cx": 25, "cy": 121, "r": 25},
        {"k": "pill", "cx": 81, "bottom": 146, "w": 44, "h": 146},
        {"k": "dot", "cx": 147, "cy": 48, "r": 25},
        {"k": "pill", "cx": 213, "bottom": 146, "w": 44, "h": 146},
        {"k": "dot", "cx": 269, "cy": 121, "r": 25},
    ]


def intro_state(t):
    e = rest()
    for el in e:
        el["o"] = 1.0
        el["dy"] = 0.0

    # Säulen: erst als Punkt auftauchen, dann nach oben wachsen (Feder)
    for idx, delay in ((1, 0.0), (3, 0.14)):
        p = e[idx]
        a = seg(t, delay, 0.30)
        s = ease_out_back(a, 1.3) if a > 0 else 0.0
        if t < delay + 0.30:
            p["w"] = 44 * s
            p["h"] = 44 * s
            p["bottom"] = 124 + 22 * s
        else:
            b = seg(t, delay + 0.30, 0.60)
            p["h"] = 44 + (146 - 44) * ease_out_back(b, 0.9)
        p["o"] = clamp((t - delay) / 0.12)

    # Punkte links/rechts: aufploppen
    for idx, delay in ((0, 0.50), (4, 0.62)):
        d = e[idx]
        a = seg(t, delay, 0.40)
        d["r"] = 25 * (ease_out_back(a, 1.5) if a > 0 else 0.0)
        d["o"] = clamp((t - delay) / 0.08)

    # Mittlerer Punkt: fällt von oben mit leichtem Nachfedern
    u = seg(t, 0.75, 0.70)
    e[2]["dy"] = -55 * (1 - spring(u)) if u > 0 else -55
    e[2]["o"] = clamp(u / 0.25)
    return e


def loop_state(t):
    """Sanfte Welle von links nach rechts, danach Ruhe (Anfang = Ende = Ruhelage)."""
    e = rest()
    for k, el in enumerate(e):
        c, half = 0.35 + k * 0.16, 0.32
        x = (t - c) / half
        b = math.cos(x * math.pi / 2) ** 2 if abs(x) < 1 else 0.0
        el["o"] = 1.0
        if el["k"] == "dot":
            el["dy"] = -12 * b
            el["r"] = 25 * (1 + 0.05 * b)
        else:
            el["dy"] = -7 * b
    return e


def outro_state(t):
    e = rest()
    x = clamp(t / OUTRO_S)
    g = 1 - 0.08 * ease_in_out_cubic(x)          # leicht kleiner werden
    o = 1 - x ** 2                               # ausblenden
    for el in e:
        el["o"] = o
        el["dy"] = 0.0
        el["g"] = g
    return e


# ── Zeichnen ──
def to_px(x, y):
    return ((LX + x * SCALE) * SS, (LY + y * SCALE) * SS)


def draw(elements):
    alpha = Image.new("L", (CW * SS, CH * SS), 0)
    for el in elements:
        if el["o"] <= 0:
            continue
        g = el.get("g", 1.0)

        def tr(x, y):   # um die Logomitte skalieren
            return (LOGO_C[0] + (x - LOGO_C[0]) * g, LOGO_C[1] + (y - LOGO_C[1]) * g)

        layer = Image.new("L", alpha.size, 0)
        dr = ImageDraw.Draw(layer)
        fill = int(round(255 * clamp(el["o"])))
        if el["k"] == "dot":
            r = el["r"] * g
            if r <= 0.05:
                continue
            cx, cy = tr(el["cx"], el["cy"] + el["dy"])
            x0, y0 = to_px(cx - r, cy - r)
            x1, y1 = to_px(cx + r, cy + r)
            dr.ellipse((x0, y0, x1, y1), fill=fill)
        else:
            w, h = el["w"] * g, el["h"] * g
            if w <= 0.05 or h <= 0.05:
                continue
            cx, bottom = tr(el["cx"], el["bottom"] + el["dy"])
            x0, y0 = to_px(cx - w / 2, bottom - h)
            x1, y1 = to_px(cx + w / 2, bottom)
            rad = min(x1 - x0, y1 - y0) / 2
            dr.rounded_rectangle((x0, y0, x1, y1), radius=rad, fill=fill)
        alpha = ImageChops.lighter(alpha, layer)
    alpha = alpha.resize((CW, CH), Image.LANCZOS)
    img = Image.new("RGBA", (CW, CH), (255, 255, 255, 0))
    img.putalpha(alpha)
    return img


def save_cropped(img, path):
    bbox = img.getchannel("A").getbbox()
    if not bbox:
        img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        bbox = (0, 0, 1, 1)
    else:
        img = img.crop(bbox)
    img.save(path, optimize=True)
    return bbox[0], bbox[1]


def main():
    os.makedirs(OUT, exist_ok=True)
    intro_n, loop_n, outro_n = round(INTRO_S * FPS), round(LOOP_S * FPS), round(OUTRO_S * FPS)
    offsets, outro_offsets = [], []
    for i in range(intro_n):
        offsets.append(save_cropped(draw(intro_state(i / FPS)), f"{OUT}/f{i}.png"))
    for i in range(loop_n):
        offsets.append(save_cropped(draw(loop_state(i / FPS)), f"{OUT}/f{intro_n + i}.png"))
    for i in range(outro_n):
        outro_offsets.append(save_cropped(draw(outro_state(i / FPS)), f"{OUT}/o{i}.png"))

    meta = {"fps": FPS, "intro_n": intro_n, "loop_n": loop_n, "outro_n": outro_n,
            "canvas": [CW, CH], "offsets": offsets, "outro_offsets": outro_offsets,
            "start_delay": START_DELAY,
            "loop_rest_from": math.ceil((0.35 + 4 * 0.16 + 0.32) * FPS)}
    json.dump(meta, open(f"{OUT}/frames.json", "w"))

    with open(f"{OUT}/raspy.plymouth", "w") as f:
        f.write("""[Plymouth Theme]
Name=raspy
Description=Logo-Animation fuer das Klassen-Display
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/raspy
ScriptFile=/usr/share/plymouth/themes/raspy/raspy.script
""")

    off_lines = "\n".join(f"ox[{i}] = {x}; oy[{i}] = {y};" for i, (x, y) in enumerate(offsets))
    with open(f"{OUT}/raspy.script", "w") as f:
        f.write(f"""// Boot-Animation "raspy" (erzeugt von make_frames.py – nicht von Hand bearbeiten)
// Intro (Logo baut sich auf) und danach eine ruhige Welle als Endlosschleife.
// Die gleichen Bilder nutzt bootsplash_x.py, um die Animation im X nahtlos fortzusetzen.

Window.SetBackgroundTopColor(0, 0, 0);
Window.SetBackgroundBottomColor(0, 0, 0);

FPS = {FPS};
// Beim Booten erst Schwarz, bis der Monitor aufgewacht ist (beim Herunterfahren sofort)
if (Plymouth.GetMode() == "boot")
    START_DELAY = {START_DELAY};
else
    START_DELAY = 0;
INTRO_N = {intro_n};
LOOP_N = {loop_n};
CANVAS_W = {CW};
CANVAS_H = {CH};

{off_lines}

for (i = 0; i < INTRO_N + LOOP_N; i++)
    frames[i] = Image("f" + i + ".png");

base_x = Math.Int((Window.GetWidth() - CANVAS_W) / 2);
base_y = Math.Int((Window.GetHeight() - CANVAS_H) / 2);

logo = Sprite();
logo.SetZ(10);

global.t = 0;
global.last = -1;

fun frame_for(n)
{{
    if (n < INTRO_N)
        return n;
    m = n - INTRO_N;
    return INTRO_N + (m - Math.Int(m / LOOP_N) * LOOP_N);
}}

fun refresh_callback()
{{
    global.t = global.t + 1 / FPS;
    if (global.t >= START_DELAY)
    {{
        idx = frame_for(Math.Int((global.t - START_DELAY) * FPS));
        if (idx != global.last)
        {{
            logo.SetImage(frames[idx]);
            logo.SetPosition(base_x + ox[idx], base_y + oy[idx], 10);
            global.last = idx;
        }}
    }}
}}
Plymouth.SetRefreshFunction(refresh_callback);

// Zeitbasis mit der echten Laufzeit abgleichen (Refresh kann Frames auslassen)
fun progress_callback(duration, progress)
{{
    if (Math.Abs(duration - global.t) > 0.1)
        global.t = duration;
}}
Plymouth.SetBootProgressFunction(progress_callback);

// Wichtige Meldungen (z. B. Dateisystemprüfung) dezent unten anzeigen
message = Sprite();
message.SetZ(20);
fun message_callback(text)
{{
    img = Image.Text(text, 0.55, 0.55, 0.58);
    message.SetImage(img);
    message.SetPosition(Math.Int((Window.GetWidth() - img.GetWidth()) / 2), Window.GetHeight() - 80, 20);
}}
Plymouth.SetDisplayMessageFunction(message_callback);
""")
    total = sum(os.path.getsize(os.path.join(OUT, n)) for n in os.listdir(OUT))
    print(f"intro {intro_n}, loop {loop_n}, outro {outro_n} Bilder, {total / 1024:.0f} KB")


if __name__ == "__main__":
    main()
