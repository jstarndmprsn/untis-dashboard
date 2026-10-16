from datetime import date, datetime

import requests

import dashboard
import demo
import untis
from helpers import GRID, lesson


def test_safe_error_hides_secret_calendar_address():
    url = "https://calendar.google.com/calendar/ical/geheim123/private-abc/basic.ics"
    for e in (requests.HTTPError(f"404 Client Error: Not Found for url: {url}"),
              requests.ConnectionError("HTTPSConnectionPool(host='calendar.google.com', port=443): "
                                       "Max retries exceeded with url: /calendar/ical/geheim123/basic.ics")):
        msg = dashboard.safe_error(e)
        assert "geheim123" not in msg and "private-abc" not in msg
        assert msg.startswith(type(e).__name__)


def test_source_stores_only_safe_error_and_backs_off_on_bad_password():
    def boom():
        raise requests.HTTPError("500 for url: https://example.org/secret-token")
    src = dashboard.Source("Kalender", 600, boom)
    before = datetime.now().timestamp()
    src.run()
    assert "secret-token" not in src.error and not src.auth_failed
    assert src.next_run - before <= 61                    # normaler Fehler: schneller Neuversuch

    def bad_login():
        raise untis.UntisError("bad credentials", untis.ERR_BAD_CREDENTIALS)
    src = dashboard.Source("Untis", 120, bad_login)
    before = datetime.now().timestamp()
    src.run()
    assert src.auth_failed and src.next_run - before >= dashboard.AUTH_RETRY - 1
    assert dashboard.source_status(src)["auth"] is True


def test_week_monday_advances_on_weekend():
    assert dashboard.week_monday(date(2026, 10, 14)) == date(2026, 10, 12)   # Mittwoch
    assert dashboard.week_monday(date(2026, 10, 17)) == date(2026, 10, 19)   # Samstag -> nächste Woche
    assert dashboard.week_monday(date(2026, 10, 18)) == date(2026, 10, 19)   # Sonntag


def test_hide_covered_by_events():
    ev = lesson(status="event", start=955, end=1235)
    covered = lesson(status="cancelled", start=1045, end=1130)
    other_day = lesson(date=20261013, status="cancelled", start=1045, end=1130)
    kept = lesson(status="cancelled", start=800, end=845)
    out = dashboard.hide_covered_by_events([ev, covered, other_day, kept])
    assert covered not in out and other_day in out and kept in out and ev in out


def test_back_to_back_uses_timegrid_not_fixed_gap():
    grid = [{"name": "1", "start": 800, "end": 845}, {"name": "2", "start": 910, "end": 955},
            {"name": "3", "start": 1000, "end": 1045}]
    assert dashboard.back_to_back(845, 910, grid)          # 25 min Pause, aber direkt nacheinander
    assert not dashboard.back_to_back(845, 1000, grid)     # Stunde 2 liegt dazwischen
    assert dashboard.back_to_back(845, 905)                # ohne Raster: <= 20 min
    assert not dashboard.back_to_back(845, 910)


def test_build_day_merges_double_period_and_picks_next_day():
    lessons = [lesson(date=20261012, start=800, end=845), lesson(date=20261012, start=850, end=935),
               lesson(date=20261013, subject="Deu", start=800, end=845)]
    day = dashboard.build_day(lessons, datetime(2026, 10, 12, 7, 30), GRID)
    assert day["label"] == "Heute" and len(day["slots"]) == 1
    assert (day["slots"][0]["start"], day["slots"][0]["end"]) == ("08:00", "09:35")
    later = dashboard.build_day(lessons, datetime(2026, 10, 12, 15, 0), GRID)
    assert later["label"] == "Morgen"


def test_subject_color_prefers_official_untis_colour():
    assert dashboard.subject_color("Mat (8-10)", {"Mat (8-10)": "#c00000"}) == "#c00000"
    assert dashboard.subject_color("Mat (8-10)") == dashboard.SUBJECT_COLORS["Mat"]
    assert dashboard.subject_color("Xyz").startswith("hsl(")


def demo_fetcher():
    cfg = {"DEMO": "1", "UNTIS_CLASS": "9a", "DUTY_LABEL": "Klassenordner"}
    return dashboard.UntisFetcher(cfg, untis.MasterData(cfg))


def test_untis_fetcher_with_demo_data():
    data = demo_fetcher()()
    statuses = {l["status"] for l in data["lessons"]}
    assert {"normal", "changed", "cancelled", "event"} <= statuses
    assert any(l["marks"] for l in data["lessons"])
    dues = [h["due"] for h in data["homework"]]
    assert dues == sorted(dues)


def test_lesson_without_subject_but_with_teacher_is_event():
    f = demo_fetcher()
    f.master.ensure(demo.FakeUntisClient())
    l = f._lesson({"id": 1, "date": 20261015, "startTime": 955, "endTime": 1520, "code": "irregular",
                   "te": [{"id": 1}], "su": [], "ro": [], "lstext": "Kinobesuch", "substText": "Kinobesuch"})
    assert l["status"] == "event" and l["subject"] == "Kinobesuch"
    gone = f._lesson({"id": 2, "date": 20261015, "startTime": 800, "endTime": 845,
                      "te": [{"id": 0, "orgid": 1}], "su": [{"id": 1}], "ro": [{"id": 1}]})
    assert gone["status"] == "cancelled"
