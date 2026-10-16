# Changelog

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
