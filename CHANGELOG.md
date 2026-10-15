# Changelog

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
