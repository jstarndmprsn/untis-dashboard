#!/usr/bin/env bash
# raspy installer – run on the Raspberry Pi as your normal desktop user (not with sudo):
#
#   ./install.sh               install, or update after `git pull` (keeps your config.env)
#   ./install.sh --configure   change settings in a menu (school, login, class, calendar, weather, screen …)
#   ./install.sh --uninstall   remove the services and system changes again
#
# Needs Raspberry Pi OS with desktop (Debian 12/13). Asks for sudo when it needs it.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ME="$(id -un)"
STATE="$DIR/.install-state"
SYSEDIT="$DIR/system/sysedit.py"
LIGHTDM=${LIGHTDM:-/etc/lightdm/lightdm.conf}
BOOT=${BOOT:-/boot/firmware}; [ -d "$BOOT" ] || BOOT=/boot
ORIG_SETUP=""

# ───────────────────────── helpers ─────────────────────────
say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

ask_yn() {   # ask_yn "Question" y|n  -> exit code 0 = yes
  local a hint="y/N"; [ "$2" = y ] && hint="Y/n"
  read -r -p "  $1 [$hint]: " a </dev/tty || a=""
  a=${a:-$2}; [[ $a =~ ^[YyJj] ]]
}

render() {   # Platzhalter in Vorlagen ersetzen
  sed -e "s|@DIR@|$DIR|g" -e "s|@USER@|$ME|g" -e "s|@HOME@|$HOME|g" -e "s|@ORIG_SETUP@|$ORIG_SETUP|g" "$1"
}

put() {      # put SRC DEST MODE   (Vorlage rendern und mit sudo installieren)
  render "$1" | sudo tee "$2" >/dev/null
  sudo chmod "$3" "$2"
}

sysedit() { sudo python3 "$SYSEDIT" "$@"; }   # Sicherungen: /var/backups/raspy

setting() {  # Wert aus config.env (ohne Anführungszeichen)
  grep -E "^$1=" "$DIR/config.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"' " || true
}

# shellcheck source=/dev/null
load_state() { [ -f "$STATE" ] && . "$STATE" || true; }
save_state() {
  printf 'F_BUTTONS=%s\nF_FAN=%s\nF_UPDATES=%s\nF_BOOT=%s\n' \
    "$F_BUTTONS" "$F_FAN" "$F_UPDATES" "$F_BOOT" > "$STATE"
}

unit_on()  { sudo systemctl daemon-reload; sudo systemctl enable "$@" >/dev/null 2>&1; }
unit_off() { for u in "$@"; do sudo systemctl disable --now "$u" >/dev/null 2>&1 || true
                               sudo rm -f "/etc/systemd/system/$u"; done; sudo systemctl daemon-reload; }

# ───────────────────────── checks ─────────────────────────
[ "$(id -u)" -ne 0 ] || die "Please run as your normal user (./install.sh), not with sudo."
[[ "$DIR" != *" "* ]] || die "The folder path must not contain spaces: $DIR"
command -v apt-get >/dev/null || die "This installer needs Raspberry Pi OS / Debian (apt-get)."
MODEL="$( { tr -d '\0' < /proc/device-tree/model; } 2>/dev/null || echo "unknown device")"
IS_PI=n;  [[ $MODEL == Raspberry\ Pi* ]] && IS_PI=y
IS_PI5=n; [[ $MODEL == Raspberry\ Pi\ 5* ]] && IS_PI5=y
F_BUTTONS="" F_FAN="" F_SCREEN="" F_UPDATES="" F_BOOT=""
load_state

# ───────────────────────── features: install / remove ─────────────────────────
install_web() {
  say "Web server"
  put "$DIR/system/raspy-web.service" /etc/systemd/system/raspy-web.service 644
  unit_on raspy-web.service
  sudo systemctl restart raspy-web.service
  local port; port=$(setting PORT); port=${port:-5000}
  for _ in $(seq 1 20); do curl -sf -o /dev/null "http://127.0.0.1:$port/content" && break; sleep 0.5; done
  if curl -sf -o /dev/null "http://127.0.0.1:$port/content"; then ok "raspy-web.service running on port $port"
  else warn "Web server does not answer yet – check: journalctl -u raspy-web -n 50"; fi
}

install_kiosk() {
  say "Kiosk (Chromium in full screen after login)"
  mkdir -p "$HOME/.config/autostart"
  render "$DIR/system/raspy-kiosk.desktop" > "$HOME/.config/autostart/raspy-kiosk.desktop"
  ok "Autostart: ~/.config/autostart/raspy-kiosk.desktop"
  # Bildschirmleser aus (spart CPU) – nur, wenn er installiert ist
  if command -v orca >/dev/null; then
    printf '[Desktop Entry]\nType=Application\nName=Orca Screen Reader\nExec=orca\nHidden=true\n' \
      > "$HOME/.config/autostart/orca-autostart.desktop"
  fi
  if [ -d /etc/chromium.d ]; then put "$DIR/system/99-kiosk-no-a11y" /etc/chromium.d/99-kiosk-no-a11y 644; fi
  # Mauszeiger nach 3 s ausblenden
  if [ -f /etc/default/unclutter ]; then
    sudo sed -i -e 's/^START_UNCLUTTER=.*/START_UNCLUTTER="true"/' -e 's/^EXTRA_OPTS=.*/EXTRA_OPTS="-idle 3 -root"/' /etc/default/unclutter
  fi

  if [ ! -f "$LIGHTDM" ]; then
    warn "LightDM not found – the kiosk needs Raspberry Pi OS *with desktop*."
    return
  fi
  # X11 statt Wayland (xdotool für die Tab-Taste und die Overlays brauchen X11)
  local session; session=$(sysedit ini-get "$LIGHTDM" 'Seat:*' autologin-session)
  [ -n "$session" ] || session=$(sysedit ini-get "$LIGHTDM" 'Seat:*' user-session)
  if [[ $session != *-x ]]; then   # X11-Sitzung: rpd-x (Debian 13) bzw. LXDE-pi-x (Debian 12)
    warn "Your desktop runs on Wayland (session: ${session:-unknown}); raspy needs X11."
    if ask_yn "Switch to X11 now (raspi-config)?" y; then
      sudo raspi-config nonint do_wayland W1 && ok "Switched to X11 (active after reboot)" \
        || warn "Could not switch – use: sudo raspi-config → Advanced Options → Wayland → X11"
    fi
  else
    ok "Desktop session: X11"
  fi
  local autologin; autologin=$(sysedit ini-get "$LIGHTDM" 'Seat:*' autologin-user)
  if [ "$autologin" != "$ME" ]; then
    if ask_yn "Log in automatically as $ME after boot (needed for the kiosk)?" y; then
      sudo raspi-config nonint do_boot_behaviour B4 && ok "Desktop autologin for $ME" \
        || warn "Could not enable – use: sudo raspi-config → System Options → Boot / Auto Login"
    fi
  else
    ok "Autologin as $ME"
  fi
}

remove_kiosk() {
  rm -f "$HOME/.config/autostart/raspy-kiosk.desktop" "$HOME/.config/autostart/orca-autostart.desktop"
  sudo rm -f /etc/chromium.d/99-kiosk-no-a11y
}

install_sudoers() {   # Tasten + Überhitzungsschutz dürfen ausschalten
  local tmp; tmp=$(mktemp)
  render "$DIR/system/sudoers-raspy" > "$tmp"
  if sudo visudo -cf "$tmp" >/dev/null; then
    sudo install -m 440 -o root -g root "$tmp" /etc/sudoers.d/raspy
  else
    warn "sudoers rule invalid – skipped (shutdown from the buttons may ask for a password)"
  fi
  rm -f "$tmp"
}

install_buttons() {
  say "Buttons"
  put "$DIR/system/raspy-buttons.service" /etc/systemd/system/raspy-buttons.service 644
  install_sudoers
  unit_on raspy-buttons.service
  sudo systemctl restart raspy-buttons.service
  ok "raspy-buttons.service (GPIO $(setting BUTTON_TAB_GPIO) = tab, GPIO $(setting BUTTON_POWER_GPIO) = power)"
}
remove_buttons() { unit_off raspy-buttons.service; }

install_fan() {
  say "Fan control + overheat protection"
  put "$DIR/system/99-fan-control.rules" /etc/udev/rules.d/99-fan-control.rules 644
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=hwmon --action=add || true
  # Kernel-Lüfterkurve bleibt als Notfall-Sicherung (greift erst ab 82 °C, falls der Dienst ausfällt)
  sysedit block "$BOOT/config.txt" "raspy fan" <<'EOF'
[all]
# Kernel-Lüfterkurve nur als NOTFALL-Sicherung – Normalbetrieb: device/thermal_guard.py (raspy-thermal.service)
dtparam=fan_temp0=82000,fan_temp0_hyst=3000,fan_temp0_speed=200
dtparam=fan_temp1=84000,fan_temp1_hyst=3000,fan_temp1_speed=255
dtparam=fan_temp2=86000,fan_temp2_hyst=3000,fan_temp2_speed=255
dtparam=fan_temp3=88000,fan_temp3_hyst=3000,fan_temp3_speed=255
EOF
  put "$DIR/system/raspy-thermal.service" /etc/systemd/system/raspy-thermal.service 644
  install_sudoers
  unit_on raspy-thermal.service
  sudo systemctl restart raspy-thermal.service
  ok "raspy-thermal.service (emergency shutdown at 85 °C, see device/thermal_guard.py)"
}
remove_fan() {
  unit_off raspy-thermal.service
  sudo rm -f /etc/udev/rules.d/99-fan-control.rules
  if [ -f "$BOOT/config.txt" ]; then sysedit unblock "$BOOT/config.txt" "raspy fan"; fi
}

install_screen() {   # immer installiert; an/aus und Zeiten stehen in config.env (SCREEN_SCHEDULE …)
  say "Screen schedule"
  { crontab -l 2>/dev/null | grep -v 'raspy:screen' || true
    echo "*/5 * * * * $DIR/device/screen_schedule.sh # raspy:screen"
    echo "@reboot sleep 45 && $DIR/device/screen_schedule.sh # raspy:screen"
  } | crontab -
  "$DIR/device/screen_schedule.sh" || true   # sofort anwenden (tut nichts, solange kein Desktop läuft)
  local idle; idle=$(setting SCREEN_IDLE_OFF)
  case "$(setting SCREEN_SCHEDULE)" in
    0|no|false|off) ok "Screen always on (change with ./install.sh --configure)" ;;
    *) ok "Mon–Fri $(setting SCREEN_ON_FROM)–$(setting SCREEN_ON_UNTIL) always on, otherwise off after $(( ${idle:-600} / 60 )) min idle" ;;
  esac
}
remove_screen() {
  { crontab -l 2>/dev/null | grep -v 'raspy:screen' || true; } | crontab -
  DISPLAY=:0 XAUTHORITY="$HOME/.Xauthority" xset dpms 600 600 600 2>/dev/null || true
}

install_updates() {
  say "Automatic updates"
  put "$DIR/system/raspy-update" /usr/local/sbin/raspy-update 755
  for u in raspy-update.service raspy-update.timer raspy-update-recover.service; do
    put "$DIR/system/$u" "/etc/systemd/system/$u" 644
  done
  install_sudoers
  unit_on raspy-update.timer raspy-update-recover.service
  sudo systemctl start raspy-update.timer
  ok "Every Tuesday 09:45 in the background (log: /var/log/raspy-update.log)"
}
remove_updates() {
  unit_off raspy-update.timer raspy-update-recover.service raspy-update.service
  sudo rm -f /usr/local/sbin/raspy-update
}

install_boot() {
  say "Boot animation"
  local tmp; tmp=$(mktemp -d)
  "$DIR/venv/bin/python" "$DIR/boot/make_frames.py" "$tmp/raspy" >/dev/null
  sudo rm -rf /usr/share/plymouth/themes/raspy
  sudo cp -r "$tmp/raspy" /usr/share/plymouth/themes/raspy
  rm -rf "$tmp"
  sudo mkdir -p /usr/local/share/raspy
  sudo install -m 644 "$DIR/system/blank.xbm" /usr/local/share/raspy/blank.xbm

  # ursprüngliches display-setup-script merken, raspy-display-setup ruft es danach auf
  local cur; cur=$(sysedit ini-get "$LIGHTDM" 'Seat:*' display-setup-script)
  if [ "$cur" != "/usr/local/sbin/raspy-display-setup" ]; then
    echo "$cur" | sudo tee /usr/local/share/raspy/orig-display-setup >/dev/null
  fi
  ORIG_SETUP=$(cat /usr/local/share/raspy/orig-display-setup 2>/dev/null || true)
  put "$DIR/system/raspy-display-setup" /usr/local/sbin/raspy-display-setup 755
  ORIG_SETUP=""

  # LightDM übernimmt Plymouths VT 1, damit das letzte Bild stehen bleibt
  sysedit ini-set "$LIGHTDM" LightDM minimum-vt 1
  sysedit ini-set "$LIGHTDM" 'Seat:*' xserver-command "X -background none"
  sysedit ini-set "$LIGHTDM" 'Seat:*' display-setup-script /usr/local/sbin/raspy-display-setup
  sudo mkdir -p /etc/systemd/system/lightdm.service.d
  put "$DIR/system/lightdm-raspy-vt.conf" /etc/systemd/system/lightdm.service.d/raspy-vt.conf 644
  sudo systemctl disable getty@tty1.service >/dev/null 2>&1 || true
  sudo systemctl daemon-reload

  sysedit cmdline "$BOOT/cmdline.txt"
  sysedit block "$BOOT/config.txt" "raspy boot animation" <<'EOF'
[all]
# kein Firmware-Regenbogen beim Start (Boot-Animation)
disable_splash=1
EOF
  sudo plymouth-set-default-theme raspy
  echo "  Updating initramfs (takes a minute) …"
  sudo update-initramfs -u >/dev/null
  ok "Boot animation installed (originals backed up in /var/backups/raspy)"
}
remove_boot() {
  [ -f "$LIGHTDM" ] || return 0
  [ "$(sysedit ini-get "$LIGHTDM" 'Seat:*' display-setup-script)" = /usr/local/sbin/raspy-display-setup ] || return 0
  local orig; orig=$(cat /usr/local/share/raspy/orig-display-setup 2>/dev/null || true)
  [ -n "$orig" ] || [ ! -x /usr/share/dispsetup.sh ] || orig=/usr/share/dispsetup.sh
  sysedit ini-set "$LIGHTDM" 'Seat:*' display-setup-script "$orig"
  sysedit ini-set "$LIGHTDM" 'Seat:*' xserver-command X
  sysedit ini-set "$LIGHTDM" LightDM minimum-vt 7
  sudo rm -f /etc/systemd/system/lightdm.service.d/raspy-vt.conf /usr/local/sbin/raspy-display-setup
  sudo systemctl enable getty@tty1.service >/dev/null 2>&1 || true
  sudo systemctl daemon-reload
  sysedit uncmdline "$BOOT/cmdline.txt"
  sysedit unblock "$BOOT/config.txt" "raspy boot animation"
  if [ -d /usr/share/plymouth/themes/pix ]; then sudo plymouth-set-default-theme pix; fi
  sudo rm -rf /usr/share/plymouth/themes/raspy /usr/local/share/raspy
  sudo update-initramfs -u >/dev/null
}

configure() {   # Menü, wenn config.env schon existiert, sonst Schritt für Schritt
  local flags=()
  [ "${F_BUTTONS:-n}" = y ] && flags+=(--buttons)
  "$DIR/venv/bin/python" "$DIR/app/configure.py" "${flags[@]}" "$@" </dev/tty
}

# Früher war der Bildschirm-Zeitplan ein Extra (F_SCREEN in .install-state), jetzt ist es SCREEN_SCHEDULE
# in config.env. Wer ihn damals abgewählt hat, bekommt SCREEN_SCHEDULE=0 (Bildschirm immer an).
migrate_screen() {
  if [ "${F_SCREEN:-}" = n ] && [ -f "$DIR/config.env" ] && ! grep -q '^SCREEN_SCHEDULE=' "$DIR/config.env"; then
    printf '\nSCREEN_SCHEDULE=0\n' >> "$DIR/config.env"
  fi
  F_SCREEN=""
}

# ───────────────────────── modes ─────────────────────────
if [ "${1:-}" = "--uninstall" ]; then
  say "Uninstall raspy"
  ask_yn "Remove all raspy services and system changes from this Pi?" n || exit 0
  sudo -v
  remove_boot; remove_updates; remove_screen; remove_fan; remove_buttons; remove_kiosk
  unit_off raspy-web.service
  sudo rm -f /etc/sudoers.d/raspy
  rm -f "$STATE"
  ok "Removed. Your settings (config.env) and this folder are still here – delete the folder to remove everything."
  ok "Backups of changed system files: /var/backups/raspy"
  exit 0
fi

if [ "${1:-}" = "--configure" ]; then
  [ -x "$DIR/venv/bin/python" ] || die "Run ./install.sh first."
  migrate_screen
  before=$(cksum < "$DIR/config.env" 2>/dev/null || true)
  configure "${@:2}"
  [ "$(cksum < "$DIR/config.env" 2>/dev/null || true)" != "$before" ] || exit 0
  sudo systemctl restart raspy-web.service && ok "Web server restarted"
  [ "${F_BUTTONS:-n}" = y ] && sudo systemctl restart raspy-buttons.service || true
  install_screen
  exit 0
fi

say "raspy installer"
echo "  Device: $MODEL"
echo "  Folder: $DIR"
echo "  User:   $ME"

say "Which extras do you want?"
echo "  Always installed: the web server and Chromium in full screen (kiosk)."
def() { [ -n "$1" ] && echo "$1" || echo "$2"; }
ask_yn "Two push buttons on GPIO (switch view / hold to shut down)?" "$(def "$F_BUTTONS" "$IS_PI")" && F_BUTTONS=y || F_BUTTONS=n
if [ "$IS_PI5" = y ]; then
  ask_yn "Smooth fan curve + overheat protection (Pi 5 Active Cooler)?" "$(def "$F_FAN" y)" && F_FAN=y || F_FAN=n
else
  F_FAN=n
fi
ask_yn "Automatic system updates (Tuesdays 09:45, safe to interrupt)?" "$(def "$F_UPDATES" y)" && F_UPDATES=y || F_UPDATES=n
ask_yn "Custom boot animation (changes boot settings – advanced)?" "$(def "$F_BOOT" n)" && F_BOOT=y || F_BOOT=n
[ "$IS_PI" = y ] || [ "$F_BUTTONS$F_BOOT" = nn ] || warn "This does not look like a Raspberry Pi – buttons/boot animation may not work."

say "Packages"
sudo -v
PKGS=(python3-venv python3-pip curl xdotool x11-xserver-utils unclutter)
if ! command -v chromium >/dev/null && ! command -v chromium-browser >/dev/null; then
  if apt-cache show chromium >/dev/null 2>&1; then PKGS+=(chromium); else PKGS+=(chromium-browser); fi
fi
[ "$F_BUTTONS" = y ] && PKGS+=(python3-gpiozero python3-lgpio)
[ "$F_BUTTONS$F_FAN$F_BOOT" != nnn ] && PKGS+=(python3-tk python3-pil fonts-noto-core)
[ "$F_BOOT" = y ] && PKGS+=(plymouth)
sudo apt-get update -q >/dev/null
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${PKGS[@]}" || die "apt-get install failed (see above)"
ok "Installed: ${PKGS[*]}"

say "Python environment"
[ -x "$DIR/venv/bin/python" ] || python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install -q --upgrade pip
"$DIR/venv/bin/pip" install -q -r "$DIR/app/requirements.txt"
ok "venv ready ($DIR/venv, packages from app/requirements.txt)"

say "Settings"
migrate_screen
if [ -f "$DIR/config.env" ] && ! ask_yn "config.env already exists – change settings now?" n; then
  ok "Keeping config.env"
else
  configure
fi

save_state
install_web
install_kiosk
if [ "$F_BUTTONS" = y ]; then install_buttons; else remove_buttons; fi
if [ "$F_FAN" = y ];     then install_fan;     else remove_fan; fi
install_screen
if [ "$F_UPDATES" = y ]; then install_updates; else remove_updates; fi
if [ "$F_BOOT" = y ];    then install_boot;    else remove_boot; fi
[ "$F_BUTTONS$F_FAN$F_UPDATES" = nnn ] && sudo rm -f /etc/sudoers.d/raspy

PORT=$(setting PORT); PORT=${PORT:-5000}
say "Done"
echo "  Open from another device:  http://$(hostname -s 2>/dev/null || hostname).local:$PORT/heute   (week view: /)"
echo "  Change settings later:     ./install.sh --configure"
echo "  Logs:                      journalctl -u raspy-web -f"
if ask_yn "Reboot now to start the kiosk?" y; then sudo reboot; fi
