#!/bin/sh
# Startet Chromium im Kiosk-Modus (Autostart der Desktop-Sitzung), sobald der Webserver antwortet.
#   Tab 1: /heute (Tagesansicht)   Tab 2: / (Wochenplan)   – Tab-Taste wechselt
# Vorher: Bildschirm einstellen (display.sh: Auflösung, Drehung aus config.env).
# BROWSER_FLAGS in config.env: zusätzliche Chromium-Optionen, z. B. --disable-gpu
DIR="$(dirname "$(readlink -f "$0")")/.."
setting() {
  grep -E "^$1=" "$DIR/config.env" 2>/dev/null | tail -1 | cut -d= -f2- | sed -e "s/^[\"']//" -e "s/[\"']$//"
}
PORT=$(setting PORT | tr -d ' ')
PORT=${PORT:-5000}
FLAGS=$(setting BROWSER_FLAGS)

"$DIR/device/display.sh" || true

i=0
while [ "$i" -lt 60 ] && ! curl -sf -o /dev/null "http://127.0.0.1:$PORT/content"; do
  sleep 1; i=$((i + 1))
done

BROWSER=$(command -v chromium || command -v chromium-browser)
# $FLAGS absichtlich ohne Anführungszeichen: mehrere Optionen, durch Leerzeichen getrennt
# shellcheck disable=SC2086
exec "$BROWSER" --kiosk --noerrdialogs --disable-session-crashed-bubble \
  --user-data-dir="$HOME/.config/chromium-kiosk" $FLAGS \
  "http://127.0.0.1:$PORT/heute" "http://127.0.0.1:$PORT/"
