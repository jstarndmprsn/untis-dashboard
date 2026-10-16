from datetime import date

import week
from helpers import GRID, lesson

MON = date(2026, 10, 12)


def test_build_periods_keeps_only_used_rows():
    lessons = [lesson(start=800, end=845), lesson(date=20261013, start=1045, end=1130)]
    rows = week.build_periods(GRID, lessons)
    assert [r["name"] for r in rows] == ["1", "4"]
    assert rows[0]["label"] == "1. Std" and rows[0]["time"] == "08:00 - 08:45"


def test_build_periods_without_timegrid():
    rows = week.build_periods(None, [lesson(start=900, end=945), lesson(start=800, end=845)])
    assert [(r["start"], r["end"]) for r in rows] == [(800, 845), (900, 945)]


def test_covered_periods_block_and_outside():
    assert week.covered_periods(GRID, 800, 935) == [0, 1]
    assert week.covered_periods(GRID, 1330, 1400) == [5]   # nach dem Raster -> letzte Stunde


def test_cell_status_classes():
    assert week.cell([lesson(status="cancelled")])["class"] == "normal cancelled"
    assert week.cell([lesson(status="changed")])["class"] == "normal substitution"
    assert week.cell([lesson(status="event", subject="Kinobesuch")])["class"] == "event"
    assert week.cell([lesson()])["class"] == "normal"
    mixed = week.cell([lesson(subject="Bio"), lesson(subject="Che", status="cancelled")])
    assert mixed["class"] == "normal substitution multi"


def test_cell_cancelled_is_struck_through_and_note_shown():
    c = week.cell([lesson(status="cancelled", note="Entfall")])
    assert '<span class="old-teacher">Mat</span>' in c["text"]
    assert '<span class="old-teacher">Mül</span>' in c["text"]
    assert "· Entfall" in c["text"]


def test_cell_substitution_and_room_change():
    c = week.cell([lesson(status="changed", teachers=[{"t": "Hof", "k": "old"}, {"t": "Fis", "k": "new"}],
                          rooms=[{"t": "R204", "k": "old"}, {"t": "Aula", "k": "new"}])])
    assert '<span class="old-teacher">Hof</span> <span class="new-teacher">Fis</span>' in c["text"]
    assert '<span class="new-room">Aula</span>' in c["text"]


def test_cell_full_names_for_parallel_courses_with_same_short_name():
    c = week.cell([lesson(subject="Kun", subject_full="BKu1", group="BKu1"),
                   lesson(subject="Kun", subject_full="BKu2", group="BKu2")])
    assert "BKu1" in c["text"] and "BKu2" in c["text"]
    c = week.cell([lesson(subject="Bio", subject_full="Bio B1"), lesson(subject="Che", subject_full="Che B1")])
    assert "B1" not in c["text"]


def test_cell_escapes_untis_text():
    c = week.cell([lesson(subject="<b>x</b>", note="<script>")])
    assert "<script>" not in c["text"] and "&lt;script&gt;" in c["text"]


def test_note_equal_to_title_is_not_repeated():
    c = week.cell([lesson(status="event", subject="Kinobesuch", note="Kinobesuch")])
    assert c["text"].count("Kinobesuch") == 1


def test_marks_icons():
    c = week.cell([lesson(marks=["hw", "notes"])])
    assert 'class="mark hw"' in c["text"] and 'class="mark notes"' in c["text"]


def test_week_grid_merges_multi_period_event():
    ev = lesson(status="event", subject="Ausflug", start=955, end=1235, teachers=[], rooms=[])
    grid = week.week_grid([ev], GRID, MON)
    assert grid[(0, 2)]["rowspan"] == 3
    assert grid[(0, 3)]["rowspan"] == 0 and grid[(0, 4)]["rowspan"] == 0
    assert grid[(0, 0)]["class"] == "empty"


def test_render_table_hides_lessons_cancelled_by_event_and_marks_free_days():
    lessons = [
        lesson(date=20261015, status="event", subject="Kinobesuch", start=955, end=1130),
        lesson(date=20261015, status="cancelled", subject="Deu", start=955, end=1040),
        lesson(date=20261012),
    ]
    html = week.render_table(lessons, GRID, MON, date(2026, 10, 13))
    assert "Kinobesuch" in html and "Deu" not in html
    assert html.count("Kein Unterricht") == 3          # Di, Mi, Fr ohne Stunden
    assert '<th class="today">Di' in html


def test_render_table_empty_week_and_stale_note():
    assert week.render_table([], GRID, MON, MON) is None
    html = week.render_table([lesson()], GRID, MON, MON, stale_note="Untis nicht erreichbar – Stand 10:15")
    assert "class='stale'" in html and "Stand 10:15" in html


def test_compact_table_above_nine_rows():
    grid = [{"name": str(i), "start": 700 + i * 100, "end": 745 + i * 100} for i in range(11)]
    lessons = [lesson(start=p["start"], end=p["end"]) for p in grid]
    assert "class='compact'" in week.render_table(lessons, grid, MON, MON)
