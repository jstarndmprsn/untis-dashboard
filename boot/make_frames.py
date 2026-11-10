#!/usr/bin/env python3
"""
Erzeugt die Boot-Animation aus dem Logo (logo.svg: 3 Punkte + 2 Säulen).

Mit --watchdog (Software-Wächter installiert) läuft vorher die Wächter-Animation:
zwei rote Augen, darüber „SOFTWARE WATCHDOG“; sie schauen nach links, rechts, in die Mitte,
kneifen sich zusammen, darunter erscheint „ACTIVE“. Danach baut sich das Logo auf.

  python3 make_frames.py raspy [--watchdog]

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

from PIL import Image, ImageChops, ImageDraw, ImageFont

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = ARGS[0] if ARGS else "raspy"
WATCHDOG = "--watchdog" in sys.argv

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


# ── Wächter-Animation (vor dem Logo, nur mit --watchdog) ──
WD_S = 3.4                      # Dauer in Sekunden
WD_RED = (232, 36, 36)
WCW, WCH = 600, 330             # eigene, größere Leinwand mit derselben Mitte wie die Logo-Leinwand
EYE_W, EYE_H, EYE_R = 62, 86, 27
EYE_DX, EYE_Y = 64, 168         # Abstand der Augen von der Mitte, Höhe
LOOK = 28                       # so weit schauen die Augen zur Seite
FONTS = ["/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/Library/Fonts/Arial Bold.ttf"]


def font(size):
    for f in FONTS:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size)


def spaced_text(dr, text, cx, cy, size, spacing, fill):
    """Text mit Sperrung, mittig um (cx, cy) – alles in Supersampling-Pixeln."""
    f = font(size)
    widths = [dr.textlength(ch, font=f) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = cx - total / 2
    asc, desc = f.getmetrics()
    for ch, w in zip(text, widths):
        dr.text((x, cy - (asc + desc) / 2), ch, font=f, fill=fill)
        x += w + spacing


def keyframes(t, keys):
    """Lineare Folge von (Zeit, Wert)-Punkten mit weichem Übergang (ease-in-out) dazwischen."""
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * ease_in_out_cubic((t - t0) / (t1 - t0))
    return keys[-1][1]


def watchdog_state(t):
    look = keyframes(t, [(0.70, 0), (1.00, -LOOK), (1.15, -LOOK), (1.60, LOOK), (1.75, LOOK), (2.05, 0)])
    opening = seg(t, 0.15, 0.40)
    squint = ease_out_cubic(seg(t, 2.10, 0.25))
    fade = 1 - seg(t, 3.05, 0.35) ** 2
    return {
        "title": ease_out_cubic(seg(t, 0.0, 0.45)) * fade,
        "eye_o": clamp(opening * 3) * fade,
        "eye_h": 6 + (EYE_H - 6) * (ease_out_back(opening, 1.2) if opening > 0 else 0.0),
        "look": look,
        "squint": squint,
        "active": ease_out_cubic(seg(t, 2.22, 0.32)) * fade,
        "active_dy": 10 * (1 - ease_out_cubic(seg(t, 2.22, 0.32))),
    }


def draw_watchdog(st):
    S = SS
    alpha = Image.new("L", (WCW * S, WCH * S), 0)
    dr = ImageDraw.Draw(alpha)
    cx = WCW / 2
    if st["title"] > 0:
        spaced_text(dr, "SOFTWARE WATCHDOG", cx * S, 58 * S, 30 * S, 6 * S, int(255 * st["title"]))
    if st["eye_o"] > 0:
        k = st["look"] / LOOK                                   # -1 = links, 1 = rechts
        h_open = st["eye_h"] * (1 - 0.64 * st["squint"])        # zusammenkneifen
        for side in (-1, 1):
            grow = 1 + 0.07 * k * -side                         # Auge in Blickrichtung etwas größer
            w, h = EYE_W * grow, h_open * grow
            ex = cx + side * EYE_DX + st["look"]
            ey = EYE_Y + (EYE_H - h_open) * 0.18 * st["squint"]  # beim Kneifen leicht nach unten
            layer = Image.new("L", alpha.size, 0)
            ld = ImageDraw.Draw(layer)
            box = ((ex - w / 2) * S, (ey - h / 2) * S, (ex + w / 2) * S, (ey + h / 2) * S)
            ld.rounded_rectangle(box, radius=min(EYE_R * grow, h / 2) * S, fill=int(255 * st["eye_o"]))
            if st["squint"] > 0:                                # schräges Lid: innen tiefer (entschlossener Blick)
                drop = h * 0.42 * st["squint"]
                x0, y0, x1 = box[0] - 4 * S, box[1] - 4 * S, box[2] + 4 * S
                inner_x, outer_x = (x1, x0) if side < 0 else (x0, x1)
                ld.polygon([(outer_x, y0), (inner_x, y0), (inner_x, box[1] + drop * S), (outer_x, box[1])], fill=0)
            alpha = ImageChops.lighter(alpha, layer)
            dr = ImageDraw.Draw(alpha)
    if st["active"] > 0:
        spaced_text(dr, "ACTIVE", cx * S, (282 + st["active_dy"]) * S, 34 * S, 11 * S, int(255 * st["active"]))
    alpha = alpha.resize((WCW, WCH), Image.LANCZOS)
    img = Image.new("RGBA", (WCW, WCH), WD_RED + (0,))
    img.putalpha(alpha)
    return img


def save_watchdog(img, path):
    """Wie save_cropped, aber der Versatz gilt relativ zur (kleineren) Logo-Leinwand."""
    x, y = save_cropped(img, path)
    return x - (WCW - CW) // 2, y - (WCH - CH) // 2


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
    wd_n = round(WD_S * FPS) if WATCHDOG else 0
    offsets, outro_offsets = [], []
    for i in range(wd_n):          # Wächter-Animation gehört zum Intro (danach baut sich das Logo auf)
        offsets.append(save_watchdog(draw_watchdog(watchdog_state(i / FPS)), f"{OUT}/f{i}.png"))
    for i in range(intro_n):
        offsets.append(save_cropped(draw(intro_state(i / FPS)), f"{OUT}/f{wd_n + i}.png"))
    intro_n += wd_n
    for i in range(loop_n):
        offsets.append(save_cropped(draw(loop_state(i / FPS)), f"{OUT}/f{intro_n + i}.png"))
    for i in range(outro_n):
        outro_offsets.append(save_cropped(draw(outro_state(i / FPS)), f"{OUT}/o{i}.png"))

    meta = {"fps": FPS, "intro_n": intro_n, "loop_n": loop_n, "outro_n": outro_n, "watchdog_n": wd_n,
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
// Wächter-Animation (rote Augen) am Anfang des Intros – beim Herunterfahren überspringen
if (Plymouth.GetMode() == "boot")
    SKIP = 0;
else
    SKIP = {wd_n};
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
        idx = frame_for(Math.Int((global.t - START_DELAY) * FPS) + SKIP);
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
    print(f"intro {intro_n} (davon Wächter {wd_n}), loop {loop_n}, outro {outro_n} Bilder, {total / 1024:.0f} KB")


if __name__ == "__main__":
    main()
