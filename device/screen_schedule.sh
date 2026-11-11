#!/bin/sh
# Bildschirm-Zeitplan (läuft per cron alle 5 min + beim Start):
#   SCREEN_SCHEDULE=0: Bildschirm immer an
#   Schulzeit (SCREEN_ON_DAYS, Standard Mo–Fr, SCREEN_ON_FROM–SCREEN_ON_UNTIL): Bildschirm immer an
#   sonst: Abschaltung nach SCREEN_IDLE_OFF Sekunden ohne Tastendruck (Aufwecken mit einer Taste)
# Zeiten stehen in config.env. Manuell erzwingen: screen_schedule.sh on | auto
CONFIG="${RASPY_CONFIG:-$(dirname "$(readlink -f "$0")")/../config.env}"

# nur die Bildschirm-Werte lesen (config.env wird bewusst nicht per "." geladen – sie enthält das Passwort)
setting() {
  v=$(grep -E "^$1=" "$CONFIG" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"' ")
  echo "${v:-$2}"
}
SCHOOL_START=$(setting SCREEN_ON_FROM 0730)
SCHOOL_END=$(setting SCREEN_ON_UNTIL 1600)
IDLE_OFF=$(setting SCREEN_IDLE_OFF 600)
SCHEDULE=$(setting SCREEN_SCHEDULE 1)
DAYS=$(setting SCREEN_ON_DAYS 12345)   # Wochentage als Ziffern, 1 = Montag … 7 = Sonntag

export DISPLAY=:0 XAUTHORITY="$HOME/.Xauthority"
xset q >/dev/null 2>&1 || exit 0    # X läuft (noch) nicht

MODE=$1
case "$SCHEDULE" in 0|no|false|off) MODE=${MODE:-on} ;; esac
if [ -z "$MODE" ]; then
  NOW=$(date +%H%M); DOW=$(date +%u)
  case "$DAYS" in *"$DOW"*) TODAY=y ;; *) TODAY=n ;; esac
  if [ "$TODAY" = y ] && [ "$NOW" -ge "$SCHOOL_START" ] && [ "$NOW" -lt "$SCHOOL_END" ]; then
    MODE=on
  else
    MODE=auto
  fi
fi

if [ "$MODE" = on ]; then
  xset +dpms
  xset dpms force on
  xset s off
  xset dpms 0 0 0
else
  xset s off
  xset dpms "$IDLE_OFF" "$IDLE_OFF" "$IDLE_OFF"
fi
