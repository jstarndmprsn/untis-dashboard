#!/bin/sh
# Startet Chromium im Kiosk-Modus (Autostart der Desktop-Sitzung), sobald der Webserver antwortet.
#   Tab 1: /heute (Tagesansicht)   Tab 2: / (Wochenplan)   – Tab-Taste wechselt
DIR="$(dirname "$(readlink -f "$0")")/.."
PORT=$(grep -E '^PORT=' "$DIR/config.env" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d "\"' ")
PORT=${PORT:-5000}

i=0
while [ "$i" -lt 60 ] && ! curl -sf -o /dev/null "http://127.0.0.1:$PORT/content"; do
  sleep 1; i=$((i + 1))
done

BROWSER=$(command -v chromium || command -v chromium-browser)
exec "$BROWSER" --kiosk --noerrdialogs --disable-session-crashed-bubble \
  --user-data-dir="$HOME/.config/chromium-kiosk" \
  "http://127.0.0.1:$PORT/heute" "http://127.0.0.1:$PORT/"
