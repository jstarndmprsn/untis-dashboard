# Adapting raspy to your hardware

raspy's defaults fit the setup it was built on: a **Raspberry Pi 5** with the **Active Cooler**,
a **1920×1080 screen** and **two push buttons wired to GND**. If your hardware differs, you can
change it in settings – you don't need to edit any code.

- **The easy way:** `./install.sh --configure` → *Display*, *Buttons*, *Temperature*, *Screen schedule*.
  Saving applies everything right away, and `t` (*Test the settings*) points out values that don't work.
- **By hand:** everything is in `config.env` (see [`config.example.env`](../config.example.env) for every
  setting with its default). [What to restart](#after-editing-configenv-by-hand) after a hand edit is listed at the end.

A typo never stops a service. If a value isn't valid (say `DISPLAY_ROTATE=45`), raspy logs a warning
and uses the default for that one setting. `t` in the menu lists every value like that.

*Buttons* only shows up in the menu with the buttons extra, and *Temperature* only with the overheat
protection extra. Add or remove extras with `./install.sh`.

---

## Screen

### Any resolution, any shape – no setting needed

The pages are designed for 1920×1080 and **scale themselves to fit your screen**. On a 1280×720
monitor you get the same layout, smaller. On a 4K TV you get the same layout, sharper. If your
screen has a different shape, the pages use the extra space:

| Screen | What you get |
|---|---|
| 16:9 (720p, 1080p, 1440p, 4K) | exactly the 1920×1080 layout, scaled |
| 16:10, 5:4, 4:3 (1920×1200, 1280×1024, 1024×768 …) | full width, extra height for lessons and dates |
| 21:9, 32:9 ultra-wide | full height, a wider middle column |
| portrait (taller than wide) | the landscape layout, centred with bars above and below |

The layout is landscape only. On a screen mounted upright, you get the landscape page with bars
above and below. This applies to the today view (`/heute`), the week view (`/`) and the system
dashboard (`/thermal`), and to the shutdown and overheat warnings shown by the buttons and the
overheat protection.

### Resolution – `DISPLAY_RESOLUTION`

Leave it empty and the Pi uses the resolution the screen asks for, or whatever you set in
Raspberry Pi's *Screen Configuration*. Set it when that's not what you want, for example:

- **A 4K TV.** The Pi then draws every page at 3840×2160, which is noticeably slower. `1920x1080` is
  plenty for a classroom display.
- **A screen that picks the wrong mode** or flickers at its default. Add the refresh rate:
  `1920x1080@50`, `1280x720@60`.

See what your screen offers (on the Pi, in a terminal on the desktop or over SSH):

```bash
DISPLAY=:0 device/display.sh --list
```

### Rotation – `DISPLAY_ROTATE`

Degrees clockwise: `0`, `90`, `180`, `270`. Leave it empty to keep the system's setting.

- **`180`** – the screen is mounted upside down (handy to keep the cables at the top).
- **`90` / `270`** – for panels whose native orientation is portrait but which you mount landscape
  (common with small HDMI displays), or to turn a monitor on its side. The page is landscape, so on
  an upright screen it shows with bars above and below (see above).

Only the picture is rotated; raspy doesn't use touch input.

### Several screens – `DISPLAY_OUTPUT`

A Pi 4 and Pi 5 have two HDMI ports. By default raspy sets up the first connected one. To pick one
explicitly, use the name from `display.sh --list`, e.g. `HDMI-1` or `HDMI-2`. The kiosk opens on the
main screen. Use only one screen, or mirror both in Raspberry Pi's *Screen Configuration*.

### Edges cut off on a TV – `DISPLAY_MARGIN`

Many TVs zoom in a little ("overscan"), so the clock or the class duty at the edge is cut off.
First look in the TV's picture menu for an option such as *Just Scan*, *Screen Fit*, *1:1* or
*Full Pixel*. That gives you the sharpest picture. If there's no such option, set a margin in
percent of the screen. `2` to `5` is usually enough (allowed: 0–20). The page shrinks into the
middle and leaves the edges black.

### Browser options – `BROWSER_FLAGS`

Extra command-line options for Chromium, separated by spaces. Most setups don't need any. Things
that can help on some hardware:

| Problem | Try |
|---|---|
| Black or flickering page, graphics errors in the log | `--disable-gpu` |
| Slow on an older Pi | first set a lower `DISPLAY_RESOLUTION`, then `--disable-smooth-scrolling` |

The options are used at the next start of the kiosk (`sudo systemctl restart lightdm` or a reboot).
They also apply to the system dashboard window.

### Screen schedule – `SCREEN_ON_DAYS`

The screen stays on during school hours on the days you choose. Outside those hours it turns off
when idle (see [Screen schedule](../README.md#screen-schedule)). In the menu, type the days the way
you'd write them: `Mo-Fr`, `Mo-Sa`, `Mon,Wed,Fri`. In `config.env` they're stored as digits:
`1` = Monday … `7` = Sunday, e.g. `12345` or `123456`.

The screen is switched off with DPMS, the standard "monitor standby" signal. Monitors go to sleep.
Some TVs show "No signal" instead and only turn off after their own timer. Check the TV's settings
for an option like *Auto power off / No signal power off*.

---

## Buttons

### Pins – `BUTTON_TAB_GPIO`, `BUTTON_POWER_GPIO`

BCM GPIO numbers, not the physical pin numbers. The defaults are GPIO 17 (pin 11) and GPIO 27
(pin 13). Any free GPIO from 0 to 27 works. Avoid pins that a HAT or screen on your Pi already uses
(for example GPIO 2/3 for I²C, GPIO 18–21 for many audio HATs, GPIO 7–11 for SPI displays).
`pinout` in a terminal on the Pi shows a map.

### Only one button

Leave the other pin empty (in the menu: type `-`).

- **Only a power button:** wake the screen, hold to shut down. Switching views isn't possible; the
  kiosk stays on the today view.
- **Only a tab button:** switch views, hold to close Chromium. To shut down, use the desktop menu or
  `sudo shutdown now`.

The system dashboard (both buttons together) needs both buttons.

### Wiring – `BUTTON_WIRING`

| Setting | Wiring | Typical for |
|---|---|---|
| `gnd` (default) | button between the GPIO pin and **GND** – raspy turns on the Pi's internal pull-up | plain push buttons, arcade buttons |
| `3v3` | button between the GPIO pin and **3.3 V** – raspy turns on the internal pull-down | buttons already wired to 3.3 V |
| `active-low` | a module with its own resistor that pulls the pin **LOW** when pressed – no internal resistor | button boards / modules with a pull-up on board |
| `active-high` | a module that drives the pin **HIGH** when pressed – no internal resistor | capacitive touch sensors (e.g. TTP223), modules with a pull-down |

Never connect a GPIO pin to 5 V. The Pi's pins take 3.3 V only. Power button modules from 3.3 V,
not from 5 V. The setting applies to both buttons.

### Timing

| Setting | Default | Meaning |
|---|---|---|
| `BUTTON_POWER_HOLD` | `4` | hold the power button this many seconds to shut down. The countdown appears after 1 s; release to cancel. |
| `BUTTON_TAB_HOLD` | `3` | hold the tab button this many seconds to close Chromium (shows the desktop) |
| `BUTTON_COMBO_HOLD` | `2` | hold both buttons this many seconds to open the system dashboard |
| `BUTTON_MIN_PRESS` | `0.05` | presses shorter than this (seconds) are ignored as electrical noise |

**Phantom presses** (the view switches by itself, or presses register twice): long or unshielded
cables pick up noise. Raise `BUTTON_MIN_PRESS` to `0.1`–`0.2`. If that doesn't help, add a 10 kΩ
external pull-up to 3.3 V and use `active-low`.

**Check the buttons:** `journalctl -u raspy-buttons -f` logs every press ("Button 1 gedrückt!")
and, at startup, the wiring and hold times in use.

---

## Temperature and fan

The *overheat protection* extra watches the CPU temperature. If it stays too hot, it shows a
full-screen warning with a countdown and shuts the Pi down safely. If the Pi cools down in time,
the shutdown is cancelled. With a PWM fan it also runs the fan on a smooth curve instead of the
kernel's four loud steps.

### Which Pi

| Hardware | What raspy does |
|---|---|
| Pi 5 + Active Cooler (or another fan on the Pi 5's fan connector) | overheat protection + smooth fan curve; the kernel's own fan curve stays as an emergency backup from 82 °C |
| Pi 5 without a fan | overheat protection only |
| Pi 4, Pi 3, others | overheat protection only (the installer offers it; default off). Fans on the 5 V pins always run at full speed; raspy can't control them. |
| another fan the kernel exposes as a PWM fan | set `FAN_HWMON` (below), then the fan curve works too |

The installer and the system dashboard tell you when no fan was found. That's fine: the
protection still works.

### Shutdown thresholds

| Setting | Default | Meaning |
|---|---|---|
| `THERMAL_CRIT` | `85` | when the CPU is at least this hot (°C) … |
| `THERMAL_CRIT_SECONDS` | `10` | … for this many seconds, the warning and countdown start |
| `THERMAL_PANIC` | `90` | from this temperature the countdown starts immediately |
| `THERMAL_ABORT` | `82` | the countdown is cancelled when it cools below this |
| `THERMAL_COUNTDOWN` | `20` | seconds of warning before shutting down |
| `THERMAL_WARN` | `75` | shown as "warm" in the system dashboard (display only) |

`THERMAL_ABORT` must be below `THERMAL_CRIT`, and `THERMAL_CRIT` must not be above
`THERMAL_PANIC`. Otherwise the defaults are used and `t` in the menu says so.

The Raspberry Pi slows itself down from about 80–85 °C, so the defaults sit just above that.
In a hot room or a closed case, lower them a little rather than raising them.

### Fan curve – `FAN_CURVE`

Pairs of `temperature:speed`, speed from `0` (off) to `255` (full). Between two points the speed is
interpolated; it rises at most 3 steps per second and falls at most 1, so you don't hear it jump.
Above the last point the fan goes straight to that point's speed.

```bash
FAN_CURVE="45:0 50:55 60:70 68:100 73:120 77:150 80:200 83:255"   # default (Pi 5 Active Cooler)
FAN_CURVE="50:0 58:60 70:120 78:255"                               # quieter, a bit warmer
FAN_CURVE="40:80 60:150 70:255"                                    # cooler, never fully off
```

Many fans don't start below a certain speed. If yours only clicks at the first steps, start the
curve higher (e.g. `50:0 51:70 …`). The system dashboard (both buttons, 2 s) shows temperature,
fan speed and the curve's effect over the last 15 minutes. That's the easiest way to tune it.

### Other sensors and fans

| Setting | Default | Meaning |
|---|---|---|
| `FAN_HWMON` | `pwmfan` | name of the fan's entry in `/sys/class/hwmon/*/name`. List them with `cat /sys/class/hwmon/hwmon*/name`. raspy needs a PWM fan with a `pwm1` file there. |
| `THERMAL_SENSOR` | `/sys/class/thermal/thermal_zone0/temp` | file with the temperature in millidegrees (the CPU on every Raspberry Pi). On other boards: `cat /sys/class/thermal/thermal_zone*/type` shows which zone is which. |
| `FAN_MAX_RPM` | `9200` | top of the fan-speed chart in the system dashboard (the Active Cooler's maximum). Set it to your fan's maximum. |

The fan's permissions come from a udev rule that contains this name. The menu updates the rule when
you save. After a hand edit, run `./install.sh` again (it keeps your settings).

### Test without heating the Pi

In the raspy folder, with the desktop running (the test never touches the real fan or shuts down):

```bash
echo 50 > /tmp/faketemp
python3 device/thermal_guard.py --dry-run --fake-temp-file /tmp/faketemp &
echo 88 > /tmp/faketemp     # after THERMAL_CRIT_SECONDS: warning with countdown on the screen
echo 70 > /tmp/faketemp     # below THERMAL_ABORT: cancelled
kill %1
```

---

## After editing `config.env` by hand

The menu does all of this for you when you save. After a hand edit:

| Changed | Run |
|---|---|
| `DISPLAY_RESOLUTION`, `DISPLAY_ROTATE`, `DISPLAY_OUTPUT` | `DISPLAY=:0 device/display.sh` (also applied at every start of the kiosk) |
| `DISPLAY_MARGIN` | `sudo systemctl restart raspy-web` (open pages reload by themselves) |
| `BROWSER_FLAGS` | `sudo systemctl restart lightdm` (restarts the kiosk) |
| `BUTTON_*` | `sudo systemctl restart raspy-buttons` |
| `THERMAL_*`, `FAN_CURVE` | `sudo systemctl restart raspy-thermal raspy-web` |
| `FAN_HWMON` | `./install.sh` (it keeps your settings) |
| `SCREEN_*` | nothing – checked every 5 minutes |

To return to the system's own screen settings, empty `DISPLAY_RESOLUTION`/`DISPLAY_ROTATE` and
reboot. raspy only changes what is set.

## Troubleshooting

```bash
./install.sh --configure                  # then t: lists hardware values that aren't valid
DISPLAY=:0 device/display.sh --list       # screens and resolutions
journalctl -u raspy-buttons -f            # button presses, wiring in use
journalctl -u raspy-thermal -f            # thresholds, fan found or not, warnings
journalctl -u raspy-buttons -u raspy-thermal | grep WARNUNG   # values replaced by their defaults
```
