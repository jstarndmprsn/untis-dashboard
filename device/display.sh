#!/bin/sh
# Bildschirm einstellen (X11/xrandr) – läuft beim Start des Kiosks (kiosk.sh) und nach
# ./install.sh --configure. Alles in config.env (siehe docs/hardware.md):
#   DISPLAY_OUTPUT       Ausgang, z. B. HDMI-1 (leer = erster angeschlossener)
#   DISPLAY_RESOLUTION   z. B. 1920x1080 oder 1280x720@60 (leer = nichts ändern)
#   DISPLAY_ROTATE       0, 90, 180, 270 Grad im Uhrzeigersinn (leer = nichts ändern)
# Leere Werte lassen die Einstellung des Systems (Raspberry Pi: Bildschirm-Konfiguration) unverändert.
#   display.sh          Einstellungen anwenden
#   display.sh --list   Ausgänge und Auflösungen anzeigen
CONFIG="${RASPY_CONFIG:-$(dirname "$(readlink -f "$0")")/../config.env}"

# nur die Bildschirm-Werte lesen (config.env wird bewusst nicht per "." geladen – sie enthält das Passwort)
setting() {
  v=$(grep -E "^$1=" "$CONFIG" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"' ")
  echo "${v:-$2}"
}

export DISPLAY="${DISPLAY:-:0}"
[ -n "$XAUTHORITY" ] || export XAUTHORITY="$HOME/.Xauthority"
command -v xrandr >/dev/null || { echo "display.sh: xrandr fehlt (Paket x11-xserver-utils)"; exit 0; }
xrandr --query >/dev/null 2>&1 || { echo "display.sh: kein X-Bildschirm auf $DISPLAY"; exit 0; }

if [ "$1" = "--list" ]; then
  xrandr --query | awk '/ connected/ {print "Output " $1 ":"; out=1; next} /^[^ ]/ {out=0} out && /^ +[0-9]+x[0-9]+/ {print "  " $0}'
  exit 0
fi

OUT=$(setting DISPLAY_OUTPUT)
RES=$(setting DISPLAY_RESOLUTION)
ROT=$(setting DISPLAY_ROTATE)
[ -n "$RES$ROT" ] || exit 0   # nichts eingestellt -> System entscheidet

if [ -z "$OUT" ]; then
  OUT=$(xrandr --query | awk '/ connected/ {print $1; exit}')
  [ -n "$OUT" ] || { echo "display.sh: kein angeschlossener Bildschirm gefunden"; exit 0; }
fi

set -- --output "$OUT"
case "$ROT" in
  "")  ;;
  0)   set -- "$@" --rotate normal ;;
  90)  set -- "$@" --rotate right ;;
  180) set -- "$@" --rotate inverted ;;
  270) set -- "$@" --rotate left ;;
  *)   echo "display.sh: DISPLAY_ROTATE=$ROT ungültig (0, 90, 180, 270) – ignoriert" ;;
esac
case "$RES" in
  "") ;;
  *x*@*) set -- "$@" --mode "${RES%@*}" --rate "${RES#*@}" ;;
  *x*)   set -- "$@" --mode "$RES" ;;
  *)     echo "display.sh: DISPLAY_RESOLUTION=$RES ungültig (z. B. 1920x1080) – ignoriert" ;;
esac

if xrandr "$@"; then
  echo "display.sh: xrandr $*"
else
  echo "display.sh: xrandr $* fehlgeschlagen – verfügbare Auflösungen: display.sh --list"
fi
