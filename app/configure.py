#!/usr/bin/env python3
"""
Interactive setup for config.env – school, Untis login, class, optional calendar
and weather, screen schedule; tests everything and writes config.env.

    python3 app/configure.py              menu: pick only what you want to change
                                          (step-by-step setup if there is no config.env yet)
    python3 app/configure.py --wizard     step-by-step through everything
    python3 app/configure.py --check      only test the current config.env
    python3 app/configure.py --buttons    also offer the buttons (pins, wiring, hold times)
    python3 app/configure.py --fan        also offer the overheat protection + fan curve

install.sh runs this for you. Press Enter to keep the value shown in [brackets].
"""
import argparse
import getpass
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import untis  # noqa: E402

TTY = sys.stdout.isatty()


def c(code, s):
    return f"\033[{code}m{s}\033[0m" if TTY else s


def bold(s): return c("1", s)
def ok(s): print(c("32", "  ✓ ") + s)
def warn(s): print(c("33", "  ! ") + s)
def err(s): print(c("31", "  ✗ ") + s)


def heading(s):
    print("\n" + bold(s))


def ask(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    v = input(f"  {prompt}{suffix}: ").strip()
    return v or default


def yes(prompt, default=True):
    v = input(f"  {prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not v else v in ("y", "yes", "j", "ja")


def choose(items, label):
    """Numbered list -> chosen item, or None (0 = none of these)."""
    for i, it in enumerate(items, 1):
        print(f"    {i:2d}) {label(it)}")
    while True:
        v = input("  Number (0 = none of these): ").strip()
        if v.isdigit() and 0 <= int(v) <= len(items):
            return items[int(v) - 1] if int(v) else None


def friendly(e):
    m = str(e)
    if "bad credentials" in m.lower():
        return "Wrong username or password"
    if "Unerwartete Antwort" in m:
        return "WebUntis did not answer as expected – check the server/school name"
    if isinstance(e, OSError) or "Connection" in type(e).__name__ or "Max retries" in m:
        return f"Could not reach WebUntis ({type(e).__name__}) – is the Pi online?"
    return m


# ───────────────────────── steps ─────────────────────────
def parse_untis_url(s):
    """'https://xyz.webuntis.com/WebUntis/?school=abc#/basic/login' -> (host, school)"""
    u = urlparse(s if "://" in s else "https://" + s)
    q = parse_qs(u.query)
    if "school" not in q and "?" in u.fragment:      # …/#/basic/login?school=abc
        q = parse_qs(u.fragment.split("?", 1)[1])
    return u.hostname, (q.get("school") or [None])[0]


def step_school(cfg, offer_keep=True):
    if offer_keep and cfg["UNTIS_SERVER"] and cfg["UNTIS_SCHOOL"]:
        print(f"  Current: {bold(cfg['UNTIS_SCHOOL'])} on {cfg['UNTIS_SERVER']}")
        if yes("Keep this school?"):
            return
    print("  Type your school's name (and city), or paste the address of your WebUntis login page.")
    while True:
        q = ask("School")
        if not q:
            continue
        if "webuntis" in q.lower() or q.startswith("http"):
            host, school = parse_untis_url(q)
            school = school or ask("School login name (the part after ?school= in the address)")
            if host and school:
                cfg["UNTIS_SERVER"], cfg["UNTIS_SCHOOL"] = host, school
                ok(f"{school} on {host}")
                return
            err("Could not read server and school from that address")
            continue
        try:
            schools = untis.search_schools(q)
        except Exception as e:
            err(friendly(e))
            if yes("Enter server and school login name by hand instead?", False):
                cfg["UNTIS_SERVER"] = untis.clean_server(ask("Server (e.g. xyz.webuntis.com)"))
                cfg["UNTIS_SCHOOL"] = ask("School login name")
                return
            continue
        if not schools:
            warn("No school found – try another spelling or paste the WebUntis address")
            continue
        s = choose(schools[:12], lambda s: f"{s['displayName']}  {c('2', '– ' + (s.get('address') or ''))}")
        if s:
            cfg["UNTIS_SERVER"], cfg["UNTIS_SCHOOL"] = s["server"], s["loginName"]
            ok(f"{s['displayName']} ({s['loginName']} on {s['server']})")
            return


def step_login(cfg):
    """Asks for username/password until the login works. Returns an open session or None."""
    print("  The same username and password you use in the Untis app.")
    print("  " + c("2", "Tip: a separate account just for the display is ideal, if your school offers one."))
    while True:
        cfg["UNTIS_USER"] = ask("Username", cfg["UNTIS_USER"])
        pw = getpass.getpass("  Password" + (" [Enter = keep saved password]" if cfg["UNTIS_PASSWORD"] else "") + ": ")
        if pw:
            cfg["UNTIS_PASSWORD"] = pw
        if not cfg["UNTIS_USER"] or not cfg["UNTIS_PASSWORD"]:
            err("Username and password are required")
            continue
        client = untis.UntisClient(cfg["UNTIS_SERVER"], cfg["UNTIS_SCHOOL"], cfg["UNTIS_USER"], cfg["UNTIS_PASSWORD"])
        try:
            client.__enter__()
            ok("Login works")
            return client
        except Exception as e:
            err(friendly(e))
            if not yes("Try again?"):
                warn("Saving without a working login – the display will show an error until this is fixed")
                return None


def open_session(cfg):
    """Logs in with the saved username/password; asks again only if that fails."""
    if cfg["UNTIS_SERVER"] and cfg["UNTIS_USER"] and cfg["UNTIS_PASSWORD"]:
        client = untis.UntisClient(cfg["UNTIS_SERVER"], cfg["UNTIS_SCHOOL"], cfg["UNTIS_USER"], cfg["UNTIS_PASSWORD"])
        try:
            client.__enter__()
            return client
        except Exception as e:
            err(f"Saved login does not work: {friendly(e)}")
    return step_login(cfg)


def close(u):
    if u is not None:
        u.__exit__()


def step_class(cfg, u):
    if u is None:
        cfg["UNTIS_CLASS"] = ask("Class name (e.g. 7a)", cfg["UNTIS_CLASS"])
        return
    try:
        classes = sorted(u.rpc("getKlassen", {}), key=lambda k: k.get("name", ""))
    except Exception as e:
        warn(f"Could not load the class list ({friendly(e)})")
        cfg["UNTIS_CLASS"] = ask("Class name (e.g. 7a)", cfg["UNTIS_CLASS"])
        return
    own = next((k["name"] for k in classes if k["id"] == u.auth.get("klasseId")), None)
    names = [k["name"] for k in classes]
    if len(names) <= 80:
        print("  Classes at your school: " + c("2", ", ".join(names)))
    while True:
        name = ask("Which class should the display show?", cfg["UNTIS_CLASS"] or own or "")
        k = untis.find_class(classes, name)
        if k:
            cfg["UNTIS_CLASS"], cfg["UNTIS_CLASS_ID"] = k["name"], ""
            break
        err(f"No class called „{name}“")
    # Probe: Stunden dieser (oder nächster) Woche laden
    try:
        mon = date.today() - timedelta(days=date.today().weekday())
        rows = u.rpc("getTimetable", {"id": k["id"], "type": 1, "startDate": mon.strftime("%Y%m%d"),
                                      "endDate": (mon + timedelta(days=11)).strftime("%Y%m%d")})
        ok(f"Class {k['name']}: {len(rows)} lessons in this and next week")
    except Exception as e:
        warn(f"Timetable for {k['name']} could not be loaded: {friendly(e)}")


def step_duty(cfg):
    print("  The class duty shown on the Today page, named exactly as in Untis.")
    v = input(f"  Class duty [{cfg['DUTY_LABEL'] or 'hidden'}] (Enter = keep, - = hide): ").strip()
    if v == "-":
        cfg["DUTY_LABEL"] = ""
        ok("Class duty hidden")
    elif v:
        cfg["DUTY_LABEL"] = v
        ok(f"Class duty: {v}")


def step_calendar(cfg):
    print("  Shows upcoming dates (tests, trips, holidays …) in a third column.")
    print("  Paste a secret iCal address – Google Calendar: Settings → your calendar →")
    print("  „Secret address in iCal format“. iCloud/Outlook links work too.")
    print("  " + c("2", "Without a calendar the page uses a two-column layout."))
    cur = cfg["CALENDAR_ICS_URL"]
    hint = "Enter = keep current, - = remove" if cur else "Enter = no calendar"
    while True:
        v = input(f"  iCal address ({hint}): ").strip()
        if not v:
            if cur:
                ok("Calendar kept")
            return
        if v == "-":
            cfg["CALENDAR_ICS_URL"] = ""
            ok("Calendar removed")
            return
        try:
            import dashboard
            events = dashboard.CalendarFetcher(v)()
            cfg["CALENDAR_ICS_URL"] = v
            ok(f"Calendar works – {len(events)} upcoming events")
            return
        except Exception as e:
            err(f"Could not read that calendar ({type(e).__name__}: {e})")
            if yes("Save it anyway?", False):
                cfg["CALENDAR_ICS_URL"] = v
                return


def step_weather(cfg):
    if cfg["WEATHER_LAT"]:
        print(f"  Current: {cfg['WEATHER_PLACE'] or (cfg['WEATHER_LAT'] + ', ' + cfg['WEATHER_LON'])}")
    hint = "Enter = keep, - = no weather" if cfg["WEATHER_LAT"] else "Enter = no weather"
    import requests
    while True:
        q = input(f"  Town or district for the weather ({hint}): ").strip()
        if not q:
            return
        if q == "-":
            cfg["WEATHER_LAT"] = cfg["WEATHER_LON"] = cfg["WEATHER_PLACE"] = ""
            ok("Weather off")
            return
        try:
            r = requests.get("https://geocoding-api.open-meteo.com/v1/search", timeout=15,
                             params={"name": q, "count": 8, "language": "de", "format": "json"})
            results = r.json().get("results") or []
        except Exception as e:
            err(friendly(e))
            continue
        if not results:
            warn("Nothing found – try the nearest bigger town")
            continue
        p = choose(results, lambda p: ", ".join(x for x in (p.get("name"), p.get("admin1"), p.get("country")) if x))
        if p:
            cfg["WEATHER_LAT"], cfg["WEATHER_LON"] = f"{p['latitude']:.4f}", f"{p['longitude']:.4f}"
            cfg["WEATHER_PLACE"] = p["name"]
            ok(f"Weather for {p['name']}")
            return


def hhmm_ok(v):
    return len(v) == 4 and v.isdigit() and int(v[:2]) < 24 and int(v[2:]) < 60


def screen_on(cfg):
    return str(cfg["SCREEN_SCHEDULE"]).lower() not in ("0", "no", "false", "off")


def idle_minutes(cfg):
    v = str(cfg["SCREEN_IDLE_OFF"])
    return str(max(1, int(v) // 60)) if v.isdigit() else "10"


DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def parse_days(s):
    """'Mo-Fr', 'Mon,Wed,Fri', 'täglich' or '12345' -> weekday digits '12345' (1 = Monday)."""
    s = s.strip().lower()
    if s and all(ch in "1234567" for ch in s):
        return "".join(sorted(set(s)))
    if s in ("täglich", "taeglich", "daily", "every day", "*", "alle"):
        return "1234567"
    names = guard_module().DAYS            # mo/mon … so/sun -> 0 … 6

    def day(d):
        k = d[:3] if d[:3] in names else d[:2]
        if k not in names:
            raise ValueError(f"Unknown weekday \"{d}\" – use Mo Di Mi Do Fr Sa So, Mon Tue …, or 1–7")
        return names[k]
    days = set()
    s = re.sub(r"\s*[-–]\s*", "-", s)          # "Mo - Fr" -> "Mo-Fr"
    for part in re.split(r"[,\s]+", s):
        if not part:
            continue
        rng = re.fullmatch(r"(\w+)-(\w+)", part)
        if rng:
            a, b = day(rng.group(1)), day(rng.group(2))
            days |= set(range(a, b + 1)) if a <= b else set(range(a, 7)) | set(range(0, b + 1))
        else:
            days.add(day(part))
    if not days:
        raise ValueError("Please name at least one day, e.g. Mo-Fr")
    return "".join(str(d + 1) for d in sorted(days))


def days_text(digits):
    """'12345' -> 'Mon–Fri', '135' -> 'Mon, Wed, Fri', '1234567' -> 'every day'."""
    d = sorted(int(x) - 1 for x in set(digits) if x in "1234567")
    if len(d) == 7:
        return "every day"
    runs, out = [], []
    for x in d:
        if runs and x == runs[-1][-1] + 1:
            runs[-1].append(x)
        else:
            runs.append([x])
    for r in runs:
        out += [f"{DAY_NAMES[r[0]]}–{DAY_NAMES[r[-1]]}"] if len(r) >= 3 else [DAY_NAMES[x] for x in r]
    return ", ".join(out) or "no day"


def step_screen(cfg):
    print("  With the screen schedule the screen stays on during school hours and turns off")
    print("  when idle at other times (any button wakes it up). Without it, the screen is always on.")
    if not yes("Use the screen schedule?", screen_on(cfg)):
        cfg["SCREEN_SCHEDULE"] = "0"
        ok("Screen stays on all the time")
        return
    cfg["SCREEN_SCHEDULE"] = "1"
    while True:
        v = ask("School days (e.g. Mo-Fr, Mo-Sa or Mon,Wed,Fri)", days_text(config.screen_days(cfg)))
        try:
            cfg["SCREEN_ON_DAYS"] = parse_days(v)
            break
        except ValueError as e:
            err(str(e))
    for key, label in (("SCREEN_ON_FROM", "Always on from (HHMM)"), ("SCREEN_ON_UNTIL", "until (HHMM)")):
        while True:
            v = ask(label, cfg[key]).replace(":", "").replace(".", "").zfill(4)
            if not hhmm_ok(v):
                err("Please enter a time like 0730 or 7:30")
            elif key == "SCREEN_ON_UNTIL" and v <= cfg["SCREEN_ON_FROM"]:
                err(f"Must be later than {cfg['SCREEN_ON_FROM']}")
            else:
                cfg[key] = v
                break
    while True:
        v = ask("Outside those hours: turn off after how many idle minutes", idle_minutes(cfg))
        if v.isdigit() and int(v) > 0:
            cfg["SCREEN_IDLE_OFF"] = str(int(v) * 60)
            break
        err("Please enter a number of minutes, e.g. 10")
    ok(screen_summary(cfg))


def guard_module():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "guard"))
    import raspy_guard
    return raspy_guard


def guard_summary(cfg):
    if not cfg["GUARD_WINDOWS"]:
        return c("2", "no time window – updates only with: sudo raspy-guard update-now")
    rg = guard_module()
    try:
        nxt = rg.next_window(cfg["GUARD_WINDOWS"], datetime.now())
    except ValueError:
        nxt = None
    apt = " · also system updates" if config.enabled(cfg, "GUARD_APT") else ""
    return cfg["GUARD_WINDOWS"] + (f" (next: {nxt:%a %H:%M})" if nxt else "") + apt


def step_guard(cfg):
    print("  The watchdog installs released versions (git tags v…) only in these time windows –")
    print("  e.g. a break or after school. Download and checks happen before, invisibly; in the window")
    print("  it only switches over (a few seconds) and goes back by itself if something doesn't work.")
    print("  Format: days + time, several separated by ';' – e.g.  Di,Do 11:35-11:50; Mo-Fr 16:15-18:00")
    print("  Days: Mo Di Mi Do Fr Sa So (or Mon Tue …), ranges like Mo-Fr, or 'täglich'. Empty = only by hand.")
    rg = guard_module()
    while True:
        v = ask("Time windows", cfg["GUARD_WINDOWS"])
        if v == "-":
            v = ""
        try:
            wins = rg.parse_windows(v)
        except ValueError as e:
            err(str(e))
            continue
        short = [f"{(end - start)} min" for _, start, end in wins if end - start < 2 * rg.MIN_LEFT // 60]
        if short:
            warn(f"Very short window ({', '.join(short)}) – the switch needs about {rg.MIN_LEFT // 60} minutes")
        cfg["GUARD_WINDOWS"] = v
        break
    cfg["GUARD_APT"] = "1" if yes("Also run the system updates (apt) in these windows instead of Tuesday 09:45?",
                                  config.enabled(cfg, "GUARD_APT")) else "0"
    ok(guard_summary(cfg))


def ask_value(cfg, key, label, check, hint=""):
    """Asks until check(value) returns None (= fine) or an error text. '-' = empty."""
    while True:
        v = input(f"  {label}{(' ' + c('2', hint)) if hint else ''} [{cfg[key] or '–'}]: ").strip()
        v = cfg[key] if v == "" else ("" if v == "-" else v)
        problem = check(v)
        if problem is None:
            cfg[key] = v
            return v
        err(problem)


def number_check(key, unit=""):
    lo, hi = config.LIMITS[key]

    def check(v):
        n = config._num(v)
        return None if n is not None and lo <= n <= hi else f"Please enter a number from {lo:g} to {hi:g}{unit}"
    return check


def ask_number(cfg, key, label, unit=""):
    v = ask_value(cfg, key, label, number_check(key, unit))
    cfg[key] = f"{config._num(v):g}"


WIRINGS = [
    ("gnd", "button between the GPIO pin and GND (internal pull-up) – the usual way"),
    ("3v3", "button between the GPIO pin and 3.3 V (internal pull-down)"),
    ("active-low", "module with its own resistor that pulls the pin LOW when pressed"),
    ("active-high", "module that drives the pin HIGH when pressed (e.g. TTP223 touch sensor)"),
]


def gpio_check(other_key, cfg):
    def check(v):
        if v == "":
            return None
        if not (v.isdigit() and 0 <= int(v) <= 27):
            return "Please enter a BCM GPIO number from 0 to 27 (or - for no button)"
        if v == str(cfg[other_key]).strip():
            return f"GPIO {v} is already used by the other button"
        return None
    return check


def step_buttons(cfg):
    print("  Pins are BCM GPIO numbers (GPIO 17 = pin 11, GPIO 27 = pin 13). '-' = no such button.")
    ask_value(cfg, "BUTTON_TAB_GPIO", "GPIO of the tab button (switch view)",
              gpio_check("BUTTON_POWER_GPIO", cfg))
    ask_value(cfg, "BUTTON_POWER_GPIO", "GPIO of the power button (hold to shut down)",
              gpio_check("BUTTON_TAB_GPIO", cfg))
    if not cfg["BUTTON_TAB_GPIO"] and not cfg["BUTTON_POWER_GPIO"]:
        warn("No button configured – remove the buttons extra with ./install.sh if you have none")
    print("  How are the buttons wired?")
    cur = config.button_wiring(cfg) if cfg["BUTTON_WIRING"] in config.BUTTON_WIRINGS else "gnd"
    for i, (key, text) in enumerate(WIRINGS, 1):
        print(f"    {i}) {key:<12} {text}" + (c("2", "  (current)") if key == cur else ""))
    while True:
        v = input("  Wiring [Enter = keep]: ").strip().lower()
        if not v:
            cfg["BUTTON_WIRING"] = cur
            break
        hit = next((k for i, (k, _) in enumerate(WIRINGS, 1) if v in (str(i), k)), None)
        if hit:
            cfg["BUTTON_WIRING"] = hit
            break
        err("Please choose 1–4")
    if cfg["BUTTON_POWER_GPIO"]:
        ask_number(cfg, "BUTTON_POWER_HOLD", "Hold the power button this many seconds to shut down", " s")
    if cfg["BUTTON_TAB_GPIO"]:
        ask_number(cfg, "BUTTON_TAB_HOLD", "Hold the tab button this many seconds to close the browser", " s")
    if cfg["BUTTON_TAB_GPIO"] and cfg["BUTTON_POWER_GPIO"]:
        ask_number(cfg, "BUTTON_COMBO_HOLD", "Hold both this many seconds for the system dashboard", " s")
    ok(buttons_summary(cfg))


def buttons_summary(cfg):
    pins = []
    for key, name in (("BUTTON_TAB_GPIO", "tab"), ("BUTTON_POWER_GPIO", "power")):
        if str(cfg[key]).strip():
            pins.append(f"{name} = GPIO {cfg[key]}")
    wiring = cfg["BUTTON_WIRING"] if cfg["BUTTON_WIRING"] in config.BUTTON_WIRINGS else "gnd"
    return (", ".join(pins) or "no buttons") + ("" if wiring == "gnd" else f" ({wiring})")


def display_list():
    """Connected outputs and their resolutions from xrandr (empty if no desktop is running)."""
    script = os.path.join(config.ROOT, "device", "display.sh")
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    env.setdefault("XAUTHORITY", os.path.expanduser("~/.Xauthority"))
    try:
        out = subprocess.run(["sh", script, "--list"], capture_output=True, text=True, timeout=10, env=env).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return out if out.startswith("Output ") else ""


def step_display(cfg):
    print("  The pages are drawn for 1920×1080 and scaled to fit any screen automatically.")
    print("  Here you only need to change what your screen doesn't do by itself.")
    modes = display_list()
    if modes:
        print(c("2", "\n".join("  " + ln for ln in modes.rstrip().splitlines()[:24])))
        if modes.count("Output ") > 1 or cfg["DISPLAY_OUTPUT"]:
            ask_value(cfg, "DISPLAY_OUTPUT", "Output", lambda v: None, "(e.g. HDMI-1; - = first connected)")
    ask_value(cfg, "DISPLAY_RESOLUTION", "Resolution",
              lambda v: None if not v or config.valid_resolution(v) else "Like 1920x1080 or 1280x720@60",
              "(e.g. 1920x1080 on a slow 4K TV; - = system default)")
    ask_value(cfg, "DISPLAY_ROTATE", "Rotation in degrees clockwise",
              lambda v: None if v in ("",) + config.ROTATIONS else "0, 90, 180 or 270",
              "(180 = mounted upside down; - = system default)")
    ask_number(cfg, "DISPLAY_MARGIN", "Margin around the page in %", " %")
    if config.number(cfg, "DISPLAY_MARGIN", warn=False) == 0:
        print("  " + c("2", "Tip: a TV that cuts off the edges (overscan) needs a margin of about 2–5 %."))
    ok(display_summary(cfg))


def display_summary(cfg):
    parts = [cfg["DISPLAY_RESOLUTION"] or "resolution as set by the system"]
    if cfg["DISPLAY_ROTATE"] not in ("", "0"):
        parts.append(f"rotated {cfg['DISPLAY_ROTATE']}°")
    if cfg["DISPLAY_OUTPUT"]:
        parts.append(f"on {cfg['DISPLAY_OUTPUT']}")
    margin = config.number(cfg, "DISPLAY_MARGIN", warn=False)
    if margin:
        parts.append(f"margin {margin:g} %")
    return ", ".join(parts)


def step_temperature(cfg):
    print("  The overheat protection shows a warning with a countdown and shuts the Pi down safely")
    print("  when it stays too hot. The defaults suit a Raspberry Pi 5 (it slows itself down from 85 °C).")
    ask_number(cfg, "THERMAL_CRIT", "Emergency shutdown when hotter than (°C)", " °C")
    ask_number(cfg, "THERMAL_CRIT_SECONDS", "… for this many seconds", " s")
    ask_number(cfg, "THERMAL_PANIC", "Start the countdown right away from (°C)", " °C")
    ask_number(cfg, "THERMAL_ABORT", "Cancel the countdown when it cools below (°C)", " °C")
    ask_number(cfg, "THERMAL_COUNTDOWN", "Countdown before shutting down (seconds)", " s")
    ask_number(cfg, "THERMAL_WARN", "Show as 'warm' in the system dashboard from (°C)", " °C")
    t = {k: config._num(cfg[k]) for k in ("THERMAL_ABORT", "THERMAL_CRIT", "THERMAL_PANIC")}
    if not t["THERMAL_ABORT"] < t["THERMAL_CRIT"] <= t["THERMAL_PANIC"]:
        warn("Cancel < shutdown ≤ right away doesn't hold – the defaults are used until you fix it")
    print("  Fan curve: temperature:speed pairs, speed 0–255 (Pi 5 Active Cooler, or another PWM fan –")
    print("  see FAN_HWMON in docs/hardware.md). In between, the speed is interpolated.")

    def curve_check(v):
        try:
            config.parse_fan_curve(v)
        except ValueError as e:
            return str(e)
        return None
    ask_value(cfg, "FAN_CURVE", "Fan curve", curve_check)
    ok(temperature_summary(cfg))


def temperature_summary(cfg):
    t = config.thermal_limits(cfg, warn=False)
    curve = config.fan_curve(cfg, warn=False)
    return (f"shutdown above {t['THERMAL_CRIT']:g} °C, fan from {curve[0][0]:g} °C, "
            f"full at {curve[-1][0]:g} °C")


# ───────────────────────── writing ─────────────────────────
SECTIONS = [
    ("WebUntis", ["UNTIS_SERVER", "UNTIS_SCHOOL", "UNTIS_USER", "UNTIS_PASSWORD", "UNTIS_CLASS", "UNTIS_CLASS_ID",
                  "DUTY_LABEL"],
     "UNTIS_CLASS is the class name; its ID is looked up automatically (IDs change every school year).\n"
     "UNTIS_CLASS_ID only if you need to force a fixed ID. DUTY_LABEL = class duty shown on /heute\n"
     "(as named in Untis; empty = hide)."),
    ("Calendar (optional)", ["CALENDAR_ICS_URL"],
     "Secret iCal address (Google/iCloud/Outlook). Empty = two-column layout without calendar."),
    ("Weather (optional, Open-Meteo, no API key)", ["WEATHER_LAT", "WEATHER_LON", "WEATHER_PLACE"],
     "Empty = no weather."),
    ("Screen schedule (device/screen_schedule.sh)",
     ["SCREEN_SCHEDULE", "SCREEN_ON_DAYS", "SCREEN_ON_FROM", "SCREEN_ON_UNTIL", "SCREEN_IDLE_OFF"],
     "SCREEN_SCHEDULE=1: on ON_DAYS (1 = Mon … 7 = Sun) between FROM and UNTIL (HHMM) the screen stays on;\n"
     "otherwise off after IDLE_OFF seconds without a button press. SCREEN_SCHEDULE=0: the screen is always on."),
    ("Display (see docs/hardware.md)",
     ["DISPLAY_OUTPUT", "DISPLAY_RESOLUTION", "DISPLAY_ROTATE", "DISPLAY_MARGIN", "BROWSER_FLAGS"],
     "The pages scale to any screen by themselves. Empty = leave it as the system has it.\n"
     "OUTPUT e.g. HDMI-1 (empty = first connected), RESOLUTION e.g. 1920x1080 or 1280x720@60,\n"
     "ROTATE 0/90/180/270 degrees clockwise, MARGIN in % against overscan on TVs (0–20),\n"
     "BROWSER_FLAGS extra Chromium options, e.g. --disable-gpu."),
    ("Buttons (only with the buttons extra, see docs/hardware.md)",
     ["BUTTON_TAB_GPIO", "BUTTON_POWER_GPIO", "BUTTON_WIRING", "BUTTON_TAB_HOLD", "BUTTON_POWER_HOLD",
      "BUTTON_COMBO_HOLD", "BUTTON_MIN_PRESS"],
     "BCM GPIO numbers (empty = no such button). WIRING: gnd (button to GND), 3v3 (button to 3.3 V),\n"
     "active-low / active-high (modules with their own resistor). Times in seconds; MIN_PRESS: shorter\n"
     "presses are ignored as noise (raise it with long cables)."),
    ("Overheat protection + fan (only with that extra, see docs/hardware.md)",
     ["THERMAL_SENSOR", "THERMAL_WARN", "THERMAL_CRIT", "THERMAL_CRIT_SECONDS", "THERMAL_PANIC",
      "THERMAL_ABORT", "THERMAL_COUNTDOWN", "FAN_CURVE", "FAN_HWMON", "FAN_MAX_RPM"],
     "Shutdown countdown after CRIT °C for CRIT_SECONDS (right away from PANIC), cancelled below ABORT.\n"
     "FAN_CURVE: °C:PWM pairs (PWM 0–255). FAN_HWMON: name of the fan in /sys/class/hwmon/*/name\n"
     "(Pi 5 Active Cooler: pwmfan). FAN_MAX_RPM: scale of the speed chart in the system dashboard."),
    ("Software watchdog (only with the watchdog extra)", ["GUARD_WINDOWS", "GUARD_APT"],
     "GUARD_WINDOWS: when released versions may be installed, e.g. \"Di,Do 11:35-11:50; Mo-Fr 16:15-18:00\"\n"
     "(empty = only with: sudo raspy-guard update-now). GUARD_APT=1: system updates in the same windows."),
    ("Web server", ["PORT", "LAN_ACCESS", "STATUS_FILE"],
     "PORT of the web server.\n"
     "LAN_ACCESS=1: pages also reachable from other devices (no login – only on a network you trust).\n"
     "STATUS_FILE: optional file another program writes a short notice into (JSON {\"message\": …,\n"
     "\"updated\": unix time} or plain text); shown in the status line of /heute while it is fresh."),
]


def write_config(cfg, path=config.CONFIG_FILE):
    known = {k for _, keys, _ in SECTIONS for k in keys}
    extra = {k: v for k, v in config.load_env(path).items() if k not in known and k != "GCAL_ICS_URL"}
    lines = [f"# raspy settings – written by app/configure.py on {datetime.now():%Y-%m-%d %H:%M}",
             "# Contains your Untis password: keep it private, never commit it (it is in .gitignore).",
             "# Re-run `python3 app/configure.py` to change things, then restart: sudo systemctl restart raspy-web"]
    for title, keys, note in SECTIONS:
        lines += ["", f"# ── {title} ──"] + [f"# {n}" for n in note.split("\n")]
        lines += [f"{k}={config.quote(cfg.get(k, ''))}" for k in keys]
    if extra:
        lines += ["", "# ── other ──"] + [f"{k}={config.quote(v)}" for k, v in extra.items()]
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    os.chmod(path, 0o600)


# ───────────────────────── check ─────────────────────────
def check(cfg):
    heading("Checking config.env")
    good = True
    try:
        m = untis.MasterData(cfg)
        with untis.connect(cfg) as u:
            ok(f"Login as {cfg['UNTIS_USER']} at {cfg['UNTIS_SCHOOL']}")
            m.ensure(u)
            ok(f"Class {m.class_name} (ID {m.class_id})")
            if m.periods:
                ok(f"Timetable grid: {len(m.periods)} periods, {untis_time(m.periods[0]['start'])}"
                   f"–{untis_time(m.periods[-1]['end'])}")
            else:
                warn("No timetable grid from Untis – rows will be derived from the lessons")
            for path, label in (("/api/homeworks/lessons", "Homework"),):
                try:
                    t = date.today().strftime("%Y%m%d")
                    u.rest(path, startDate=t, endDate=t)
                    ok(f"{label} available")
                except Exception:
                    warn(f"{label} not available for this account – that column stays empty")
    except Exception as e:
        err(f"Untis: {friendly(e)}")
        good = False
    if cfg["CALENDAR_ICS_URL"]:
        try:
            import dashboard
            ok(f"Calendar: {len(dashboard.CalendarFetcher(cfg['CALENDAR_ICS_URL'])())} upcoming events")
        except Exception as e:
            err(f"Calendar: {type(e).__name__}: {e}")
            good = False
    else:
        ok("No calendar (two-column layout)")
    ok(f"Weather: {cfg['WEATHER_PLACE'] or 'on'}" if cfg["WEATHER_LAT"] else "No weather")
    bad = config.problems(cfg)
    for p in bad:
        err(f"Hardware setting {p} – the default is used instead")
    if not bad:
        ok("Hardware settings (display, buttons, temperature) look fine")
    return good and not bad


def untis_time(v):
    return f"{v // 100}:{v % 100:02d}"


# ───────────────────────── wizard + menu ─────────────────────────
def wizard(cfg, buttons, guard=False):
    """Step by step through everything (first setup)."""
    titles = ["School", "Untis login", "Class", "Calendar (optional)", "Weather (optional)", "Screen schedule"]
    titles += ["Buttons"] if buttons else []
    titles += ["Software watchdog"] if guard else []
    steps = iter(enumerate(titles, 1))

    def head():
        i, t = next(steps)
        heading(f"{i}/{len(titles)}  {t}")

    head(); step_school(cfg)
    head(); u = step_login(cfg)
    head(); step_class(cfg, u); close(u)
    head(); step_calendar(cfg)
    head(); step_weather(cfg)
    head(); step_screen(cfg)
    if buttons:
        head(); step_buttons(cfg)
    if guard:
        head(); step_guard(cfg)


def do_school(cfg):
    old = (cfg["UNTIS_SERVER"], cfg["UNTIS_SCHOOL"])
    step_school(cfg, offer_keep=False)
    if (cfg["UNTIS_SERVER"], cfg["UNTIS_SCHOOL"]) != old:   # andere Schule: Login + Klasse passen nicht mehr
        heading("Untis login")
        u = step_login(cfg)
        heading("Class")
        step_class(cfg, u)
        close(u)


def do_login(cfg):
    close(step_login(cfg))


def do_class(cfg):
    u = open_session(cfg)
    step_class(cfg, u)
    close(u)


def screen_summary(cfg):
    if not screen_on(cfg):
        return "off – screen always on"
    t = lambda v: f"{v[:2]}:{v[2:]}" if hhmm_ok(v) else v  # noqa: E731
    return (f"{days_text(config.screen_days(cfg))} {t(cfg['SCREEN_ON_FROM'])}–{t(cfg['SCREEN_ON_UNTIL'])} on, "
            f"otherwise off after {idle_minutes(cfg)} min idle")


def menu_items(buttons, guard=False, fan=False):
    dim = lambda s: c("2", s)  # noqa: E731
    items = [
        ("School", lambda g: f"{g['UNTIS_SCHOOL']} on {g['UNTIS_SERVER']}"
         if g["UNTIS_SCHOOL"] else dim("not set"), do_school),
        ("Login", lambda g: (g["UNTIS_USER"] or dim("not set")) + ("" if g["UNTIS_PASSWORD"] else dim(" (no password)")),
         do_login),
        ("Class", lambda g: g["UNTIS_CLASS"] or (f"ID {g['UNTIS_CLASS_ID']}" if g["UNTIS_CLASS_ID"] else dim("not set")),
         do_class),
        ("Class duty", lambda g: g["DUTY_LABEL"] or dim("hidden"), step_duty),
        ("Calendar", lambda g: "on" if g["CALENDAR_ICS_URL"] else dim("off"), step_calendar),
        ("Weather", lambda g: (g["WEATHER_PLACE"] or f"{g['WEATHER_LAT']}, {g['WEATHER_LON']}")
         if g["WEATHER_LAT"] else dim("off"), step_weather),
        ("Screen schedule", screen_summary, step_screen),
        ("Display", display_summary, step_display),
    ]
    if buttons:
        items.append(("Buttons", buttons_summary, step_buttons))
    if fan:
        items.append(("Temperature", temperature_summary, step_temperature))
    if guard:
        items.append(("Watchdog", guard_summary, step_guard))
    return items


def menu(cfg, buttons, guard=False, fan=False):
    """Pick only what you want to change; saves at the end. Returns True if something was saved."""
    items = menu_items(buttons, guard, fan)
    saved = dict(cfg)
    while True:
        dirty = cfg != saved
        heading("raspy settings" + (c("33", "  – unsaved changes") if dirty else ""))
        for i, (title, show, _) in enumerate(items, 1):
            print(f"  {i:>2}  {title:<16} {show(cfg)}")
        print()
        print("   t  Test the settings")
        print("   s  Save and exit" if dirty else c("2", "   s  Save and exit (nothing changed)"))
        print("   q  Quit without saving")
        v = input("  Choose: ").strip().lower()
        action = None
        if v == "t":
            action = check
        elif v.isdigit() and 1 <= int(v) <= len(items):
            title, _, action = items[int(v) - 1]
            heading(title)
        if action:
            before = dict(cfg)
            try:
                action(cfg)
            except KeyboardInterrupt:      # Strg+C in einem Punkt: nur diesen Punkt abbrechen
                cfg.clear()
                cfg.update(before)
                print("\n  Cancelled – back to the menu.")
        elif v == "s":
            if dirty:
                write_config(cfg)
                heading("Saved")
                ok(f"{config.CONFIG_FILE} (only readable by you)")
            else:
                print("  Nothing changed.")
            return dirty
        elif v == "q":
            if not dirty or yes("Discard your changes?", False):
                print("  Nothing was saved.")
                return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only test the current config.env")
    ap.add_argument("--wizard", action="store_true", help="step by step through everything instead of the menu")
    ap.add_argument("--buttons", action="store_true", help="also offer the buttons (pins, wiring, hold times)")
    ap.add_argument("--fan", action="store_true", help="also offer the overheat protection + fan curve")
    ap.add_argument("--guard", action="store_true", help="also offer the software watchdog's time windows")
    a = ap.parse_args()

    cfg = config.load()
    if a.check:
        sys.exit(0 if check(cfg) else 1)

    print(bold("raspy setup") + f" – {config.CONFIG_FILE}")
    try:
        if cfg["_exists"] and not a.wizard:
            menu(cfg, a.buttons, a.guard, a.fan)
            return
        wizard(cfg, a.buttons, a.guard)
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled – nothing was saved.")
        sys.exit(130)
    write_config(cfg)
    heading("Saved")
    ok(f"{config.CONFIG_FILE} (only readable by you)")


if __name__ == "__main__":
    main()
