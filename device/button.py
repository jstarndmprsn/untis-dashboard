"""
Zwei Tasten an GPIO (interner Pull-up, gedrückt = GND), läuft als raspy-buttons.service.
Pins in config.env: BUTTON_TAB_GPIO (Standard 17 = Pin 11), BUTTON_POWER_GPIO (27 = Pin 13).

  Tab-Taste kurz           Tab wechseln (Heute <-> Wochenplan)
  Tab-Taste 3 s            Chromium beenden (Desktop wird sichtbar)
  Aus-Taste halten         Countdown, dann herunterfahren (laufende Updates werden sicher beendet)
  Beide 2 s halten         Thermal-Dashboard; Tab-Taste schließt es
  Bildschirm aus           jede Taste weckt nur
"""
import os
os.environ['GPIOZERO_PIN_FACTORY'] = 'lgpio'

from gpiozero import Button
import shutil
import subprocess
import time
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'app'))
import config  # noqa: E402

CFG = config.load()
sys.stdout.reconfigure(line_buffering=True)

time.sleep(15)

button1 = Button(int(CFG['BUTTON_TAB_GPIO']), pull_up=True, bounce_time=0.1)
button2 = Button(int(CFG['BUTTON_POWER_GPIO']), pull_up=True)

time.sleep(2)

DISPLAY_ENV  = {**os.environ, 'DISPLAY': ':0', 'XAUTHORITY': os.path.expanduser('~/.Xauthority')}
CHROMIUM     = shutil.which('chromium') or shutil.which('chromium-browser') or 'chromium'

NOISE_TIME     = 0.05   # kürzer = Rauschen

# ── Button 1 (Tab-Taste) ──
KILL_TIME      = 3.0    # so lange halten -> pkill chromium

# ── Button 2 (Aus-Taste) ──
OVERLAY_SCRIPT = os.path.join(HERE, 'shutdown_overlay.py')
PREPARE_TIME   = 0.3    # ab hier Overlay-Prozess vorbereiten (filtert Rauschen, Tk braucht Startzeit)
HOLD_TIME      = 1.0    # ab hier ist das Overlay sichtbar
COUNTDOWN      = 3.0    # danach (also nach 4 s Halten) wird ausgeschaltet

# ── Laufende System-Updates (raspy-update.service, dienstags 09:45) ──
UPDATE_STATE     = '/run/raspy-update/state'   # existiert, solange Updates laufen
UPDATE_COUNTDOWN = 5.0  # so lange (nach dem Einblenden) halten, um trotzdem auszuschalten

# ── Beide Tasten -> Thermal-Dashboard ──
COMBO_TIME     = 2.0    # beide so lange halten
THERMAL_URL    = f"http://127.0.0.1:{CFG['PORT']}/thermal"
THERMAL_PROFILE = os.path.expanduser('~/.config/chromium-thermal')   # eigenes Profil = eigenes Fenster
THERMAL_MAX_OPEN = 600  # nach 10 min automatisch schließen

def is_screen_off():
    # Monitor per DPMS aus (screen_schedule.sh bzw. Leerlauf)
    try:
        out = subprocess.run(['xset', 'q'], capture_output=True, text=True,
                             env=DISPLAY_ENV, timeout=2).stdout
        return 'Monitor is Off' in out
    except Exception:
        return False

def screen_on():
    print("Screen wird eingeschaltet", flush=True)
    subprocess.run(['xset', 'dpms', 'force', 'on'], capture_output=True, env=DISPLAY_ENV)
    subprocess.run(['xset', 's', 'reset'],           capture_output=True, env=DISPLAY_ENV)

def updates_running():
    return os.path.exists(UPDATE_STATE)

def start_overlay(press_time, update=False):
    countdown = UPDATE_COUNTDOWN if update else COUNTDOWN
    args = ['python3', OVERLAY_SCRIPT, str(press_time + HOLD_TIME), str(press_time + HOLD_TIME + countdown)]
    if update:
        args.append('update')
    try:
        return subprocess.Popen(args, env=DISPLAY_ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"Overlay konnte nicht gestartet werden: {e}", flush=True)
        return None

def stop_overlay(proc):
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=1)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

def stop_updates_and_shutdown():
    # sicherer Abbruch: systemd ruft ExecStop (raspy-update --stop) auf und wartet,
    # bis das gerade laufende Paket fertig ist (max. 120 s) – erst dann ausschalten.
    # Das Overlay zeigt so lange "Updates werden sicher beendet …".
    def run():
        print("Updates werden sicher beendet …", flush=True)
        try:
            subprocess.run(['sudo', 'systemctl', 'stop', 'raspy-update.service'], timeout=150)
        except Exception as e:
            print(f"Stoppen der Updates: {e}", flush=True)
        print("Updates beendet → shutdown", flush=True)
        subprocess.Popen(['sudo', 'shutdown', 'now'])
    threading.Thread(target=run, daemon=True).start()

def thermal_open():
    print("Thermal-Dashboard öffnen", flush=True)
    subprocess.Popen(
        [CHROMIUM, '--kiosk', f'--app={THERMAL_URL}', f'--user-data-dir={THERMAL_PROFILE}',
         '--no-first-run', '--noerrdialogs', '--disable-session-crashed-bubble',
         '--disable-features=Translate'],
        env=DISPLAY_ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def thermal_close():
    print("Thermal-Dashboard schließen", flush=True)
    subprocess.run(['pkill', '-f', THERMAL_PROFILE])
    # Fokus zurück auf das Kiosk-Chromium, sonst landet ctrl+Tab auf dem Desktop
    time.sleep(0.5)
    subprocess.run(['sh', '-c', 'w=$(xdotool search --onlyvisible --class chromium | head -1); '
                                '[ -n "$w" ] && xdotool windowactivate "$w"'],
                   env=DISPLAY_ENV, capture_output=True, timeout=5)

def tab_switch():
    print("Button 1 → Tab wechseln", flush=True)
    subprocess.Popen(['xdotool', 'key', '--clearmodifiers', 'ctrl+Tab'], env=DISPLAY_ENV)

def button_loop():
    # Beide Tasten per Polling (keine gpiozero-Callbacks: Rauschen auf GPIO27,
    # und die Tasten-Kombination braucht beide Zustände in einer Schleife).
    prev1 = prev2 = False
    t1 = t2 = None          # Druckbeginn
    mode1 = None            # None | 'done'
    mode2 = None            # None | 'wake' | 'hold' | 'done'
    hold_update = False     # beim Halten liefen Updates -> Update-Hinweis, längerer Countdown
    overlay = None
    combo = False           # beide gedrückt -> Einzelaktionen gesperrt, bis beide losgelassen
    combo_since = None
    combo_done = False
    thermal_since = None    # Zeitpunkt, seit dem das Thermal-Dashboard offen ist

    while True:
        p1, p2 = button1.is_pressed, button2.is_pressed
        now = time.time()

        # ── Flanken ──
        if p1 and not prev1:
            t1, mode1 = now, None
            print("Button 1 gedrückt!", flush=True)
            if thermal_since is not None and not p2:
                # Thermal-Dashboard offen -> Tab-Taste schließt es
                thermal_close()
                thermal_since = None
                mode1 = 'done'
        if p2 and not prev2:
            t2, mode2 = now, None
            print("Button 2 gedrückt!", flush=True)

        # ── Kombination: beide gedrückt ──
        if p1 and p2:
            if not combo:
                combo, combo_since, combo_done = True, now, False
                if mode2 == 'hold':
                    print("Beide Tasten → Ausschalten abgebrochen", flush=True)
                    stop_overlay(overlay)
                    overlay = None
                mode1 = mode2 = 'done'
            elif not combo_done and now - combo_since >= COMBO_TIME:
                combo_done = True
                if thermal_since is None:
                    thermal_open()
                    thermal_since = now

        # ── Button 1 allein ──
        if p1 and not combo and mode1 is None and t1 and now - t1 >= KILL_TIME:
            print("Button 1 3s gehalten → pkill chromium", flush=True)
            subprocess.Popen(['pkill', 'chromium'])
            thermal_since = None
            mode1 = 'done'
        if not p1 and prev1 and t1 is not None:
            duration = now - t1
            t1 = None
            if not combo and mode1 is None:
                if duration < NOISE_TIME:
                    print("Button 1 ignoriert (Noise)", flush=True)
                else:
                    tab_switch()
            mode1 = None

        # ── Button 2 allein ──
        if p2 and not combo and t2 is not None:
            held = now - t2
            if mode2 is None and held >= PREPARE_TIME:
                if is_screen_off():
                    # ── Screen ist aus → nur aufwecken, nichts anderes ──
                    screen_on()
                    mode2 = 'wake'
                else:
                    mode2 = 'hold'
                    hold_update = updates_running()
                    overlay = start_overlay(t2, update=hold_update)
            elif mode2 == 'hold' and held >= HOLD_TIME + (UPDATE_COUNTDOWN if hold_update else COUNTDOWN):
                mode2 = 'done'
                if hold_update:
                    print("Button 2 gehalten während Updates → sicher abbrechen, dann shutdown", flush=True)
                    stop_updates_and_shutdown()
                else:
                    print("Button 2 gehalten → shutdown", flush=True)
                    subprocess.Popen(['sudo', 'shutdown', 'now'])
        if not p2 and prev2 and t2 is not None:
            duration = now - t2
            t2 = None
            print(f"Button 2 losgelassen nach {duration:.2f}s", flush=True)
            if not combo:
                if duration < NOISE_TIME:
                    print("Ignoriert (Noise)", flush=True)
                elif mode2 is None:
                    # Kurzer Druck: nur Screen wecken, sonst nichts (Ausschalten nur per Halten)
                    if is_screen_off():
                        screen_on()
                    else:
                        print("Kurz gedrückt → keine Aktion (zum Ausschalten halten)", flush=True)
                elif mode2 == 'hold':
                    print("Ausschalten abgebrochen", flush=True)
                    stop_overlay(overlay)
                    overlay = None
            if mode2 != 'done' or combo:
                mode2 = None

        # Kombination endet erst, wenn beide losgelassen sind
        if combo and not p1 and not p2:
            combo = False
            mode1 = mode2 = None

        # Thermal-Dashboard nicht ewig offen lassen
        if thermal_since is not None and now - thermal_since > THERMAL_MAX_OPEN:
            thermal_close()
            thermal_since = None

        prev1, prev2 = p1, p2
        time.sleep(0.01)

if __name__ == "__main__":
    print("Buttons aktiv...", flush=True)
    # Schleife im Hauptprogramm: stürzt sie ab, endet der Prozess mit Fehler und systemd startet
    # den Dienst neu (in einem Hintergrund-Thread wäre die Aus-Taste sonst still tot).
    try:
        button_loop()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Tastenschleife abgestürzt: {e} – Neustart durch systemd", flush=True)
        os._exit(1)
