#!/usr/bin/env python3
"""
Interactive setup for config.env – asks for your school, Untis login, class,
optional calendar and weather, tests everything and writes config.env.

    python3 app/configure.py              full setup (re-run any time to change things)
    python3 app/configure.py --check      only test the current config.env
    python3 app/configure.py --screen     also ask for the screen-on hours
    python3 app/configure.py --buttons    also ask for the button GPIO pins

install.sh runs this for you. Press Enter to keep the value shown in [brackets].
"""
import argparse
import getpass
import os
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


def step_school(cfg):
    heading("1/5  School")
    if cfg["UNTIS_SERVER"] and cfg["UNTIS_SCHOOL"]:
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
    heading("2/5  Untis login")
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


def step_class(cfg, u):
    heading("3/5  Class")
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


def step_calendar(cfg):
    heading("4/5  Calendar (optional)")
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
    heading("5/5  Weather (optional)")
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


def step_screen(cfg):
    heading("Screen schedule")
    print("  Mon–Fri during school hours the screen stays on; otherwise it turns off when idle")
    print("  (any button wakes it up).")
    for key, label in (("SCREEN_ON_FROM", "Always on from (HHMM)"), ("SCREEN_ON_UNTIL", "until (HHMM)")):
        while True:
            v = ask(label, cfg[key]).replace(":", "")
            if hhmm_ok(v):
                cfg[key] = v
                break
            err("Please use 4 digits, e.g. 0730")
    v = ask("Outside those hours: turn off after how many idle minutes", str(int(cfg["SCREEN_IDLE_OFF"]) // 60))
    cfg["SCREEN_IDLE_OFF"] = str(int(v) * 60) if v.isdigit() else cfg["SCREEN_IDLE_OFF"]


def step_buttons(cfg):
    heading("Buttons")
    print("  Two push buttons between a GPIO pin and GND (internal pull-up).")
    cfg["BUTTON_TAB_GPIO"] = ask("GPIO of the tab button (switch view)", cfg["BUTTON_TAB_GPIO"])
    cfg["BUTTON_POWER_GPIO"] = ask("GPIO of the power button (hold to shut down)", cfg["BUTTON_POWER_GPIO"])


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
    ("Screen schedule (device/screen_schedule.sh)", ["SCREEN_ON_FROM", "SCREEN_ON_UNTIL", "SCREEN_IDLE_OFF"],
     "Mon–Fri between FROM and UNTIL (HHMM) the screen stays on; otherwise off after IDLE_OFF seconds."),
    ("Hardware", ["BUTTON_TAB_GPIO", "BUTTON_POWER_GPIO", "PORT"], "BCM GPIO numbers; PORT of the web server."),
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
    return good


def untis_time(v):
    return f"{v // 100}:{v % 100:02d}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only test the current config.env")
    ap.add_argument("--screen", action="store_true", help="also ask for the screen-on hours")
    ap.add_argument("--buttons", action="store_true", help="also ask for the button GPIO pins")
    a = ap.parse_args()

    cfg = config.load()
    if a.check:
        sys.exit(0 if check(cfg) else 1)

    print(bold("raspy setup") + f" – writes {config.CONFIG_FILE}")
    try:
        step_school(cfg)
        u = step_login(cfg)
        step_class(cfg, u)
        if u is not None:
            u.__exit__()
        step_calendar(cfg)
        step_weather(cfg)
        if a.screen:
            step_screen(cfg)
        if a.buttons:
            step_buttons(cfg)
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled – nothing was saved.")
        sys.exit(130)
    write_config(cfg)
    heading("Saved")
    ok(f"{config.CONFIG_FILE} (only readable by you)")


if __name__ == "__main__":
    main()
