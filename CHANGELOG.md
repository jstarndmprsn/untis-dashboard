# Changelog

## Unreleased

### Added – calendar colours in the settings

- **Event colours can be changed** in `./install.sh --configure` → *Event colours* or with
  `CALENDAR_COLORS` in `config.env` (e.g. `"geburtstag=#ff6b6b; ferien|frei=#5ac8fa; *=#d8d8e0"`).
  Existing installations keep their colours: empty means the same built-in rules as before.
  Invalid entries are skipped and logged; the rest still applies.

### Added – software watchdog (opt-in extra in `./install.sh`)

- **Installs released versions automatically, only in your time windows.** Tag a release
  (`git tag v1.2.0 && git push --tags`); the watchdog fetches it, builds and checks it in the
  background (checksums against git, compile, a test run with demo data) and switches over in the
  next window you set in `./install.sh --configure` → *Watchdog* (e.g. `Di,Do 11:35-11:50`).
  Only what changed is restarted; if the page or Untis data don't come back, it goes back to the
  previous version by itself. Never during a running system update, never by rebooting.
- **Every version in its own folder** (`releases/<version>`, link `current`). Switching is a single
  atomic step, so a power cut leaves either the old or the new version – never a mix.
- **Repairs damaged program files at boot** before the display starts (from git), undoes an
  interrupted switch, and falls back to the previous version if needed. With the boot animation it
  shows "Software Watchdog: Programmdateien werden repariert …".
- **Protects itself:** installed outside the version folders, started through a checksum check; a
  damaged copy is replaced by the previous one. It updates itself only after a new version has proven itself.
- Optional: system updates (apt) in the same windows instead of Tuesday 09:45 (`GUARD_APT=1`).
- Log with the list of changes for every update: `/var/log/raspy-guard.log`; overview: `raspy-guard status`.
- Boot animation: with the watchdog, two red eyes ("SOFTWARE WATCHDOG" … "ACTIVE") come before the logo,
  and before that the running version (e.g. `v1.2.0`) is shown for 3 seconds. After an update the
  watchdog swaps only that version image and rebuilds the initramfs of the running kernel – backed up
  first, checked afterwards, and restored automatically if it doesn't look right.

### Fixed

- The boot animation is built into the initramfs of the *running* kernel (`update-initramfs -u -k $(uname -r)`).
  Plain `-u` could pick the other kernel variant on a Pi 5, so the animation didn't show.

### Fixed

- **`./install.sh` over SSH without a terminal no longer stops halfway.** It used `sudo -v`,
  which asks for a password even when sudo otherwise works without one.
- **The boot animation no longer disappears after system updates.** Raspberry Pi OS's screen
  settings package (raindrop) resets LightDM's `display-setup-script` on every update, which
  silently removed the second half of the animation. A small repair script now puts it back after
  every package installation and before LightDM starts; your screen settings are still applied.

### Added

- `STATUS_FILE` in `config.env`: another program on the Pi (a Wi-Fi watchdog, a maintenance
  script …) can write a short notice into a file – JSON `{"message": …, "updated": <unix time>}` or
  plain text – and `/heute` shows it in the status line. Notices older than 3 minutes are ignored,
  so nothing sticks around if that program stops.
- `./install.sh --keep-boot` leaves the boot setup (boot animation, `cmdline.txt`, initramfs)
  untouched, and `--no-reboot` skips the reboot at the end. Meant for updates over SSH when
  nobody can get to the Pi if it doesn't boot.

## 2026-10-16 – Safer defaults, tests and CI

### Security

- **The secret calendar link no longer leaks.** Error messages used to contain the full iCal
  address (the HTTP library puts it into its errors), and they were shown in `/api/heute` and the
  log. Addresses are now removed from every stored or logged error.
- **The pages are only reachable on the Pi itself by default.** The kiosk doesn't need more.
  Set `LAN_ACCESS=1` in `config.env` to open them from other devices (no login – only on a network
  you trust). The server now runs on waitress instead of Flask's development server.
- **Wrong password: no more account lockouts.** After a rejected Untis login raspy only retries
  every 30 minutes, and the screen says "Untis-Login abgelehnt – Passwort prüfen".
- Only the display user can set the fan speed (instead of everyone).

### Fixed

- **The power button can't die silently any more.** If the button loop crashes, the service
  exits and systemd restarts it.
- **Fan safety net.** When the fan service stops or crashes, the fan is handed back to the
  kernel's own curve instead of staying at its last speed.
- The week view shows the coming week on Saturday and Sunday.
- Double periods are recognised from your school's period times, so schools with breaks longer
  than 20 minutes get merged cards too.

### Changed

- One Untis login session is kept between updates instead of logging in every 2 minutes.
- Automatic system updates are off by default for new installs (your earlier choice is kept).
- State files moved from `/tmp` to `/run/raspy`.
- The week view is a normal template (`templates/woche.html`, `static/woche.*`); the table code
  lives in `app/week.py`. Browsers reload automatically after an update (no manual version number).

### New

- Test suite (`python -m pytest`) and a GitHub Action that runs it together with `shellcheck`.

## 2026-10-15 – Settings menu and optional screen schedule

### New

- **Settings menu.** `./install.sh --configure` now opens a menu that shows your current settings.
  Pick only what you want to change (school, login, class, class duty, calendar, weather,
  screen schedule, buttons) instead of going through every step again. Press `t` to test the
  settings, `s` to save, `q` to quit without saving. Nothing is written until you save.
- **Screen schedule can be turned off.** The screen schedule is now a normal setting
  (`SCREEN_SCHEDULE` in `config.env`). Turn it off in the menu and the screen stays on all the time.
- **Class duty in the menu.** Rename the class duty shown on the Today page, or hide it with `-`.
- `./install.sh --configure --wizard` walks through every step, like the first setup.

### Changed

- The installer no longer asks about the screen schedule as an extra. It is part of the settings
  instead (asked during the first setup, changeable in the menu).
- Settings changes apply right away: the web server is restarted and the screen schedule is applied
  as soon as you save. If nothing changed, nothing is restarted.
- Times like `7:45` are accepted, and the end time must be after the start time.
- Pressing Ctrl+C inside a menu item only cancels that item and returns to the menu.

### Updating

```bash
git pull && ./install.sh
```

If you had turned the screen schedule off in an earlier version, it stays off
(`SCREEN_SCHEDULE=0` is added to your `config.env` automatically). Note: "off" now means the
screen is always on. Before, it still went dark after 10 minutes without input.
