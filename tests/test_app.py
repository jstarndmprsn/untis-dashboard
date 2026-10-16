"""Ganze App im Demo-Modus: Seiten antworten, Daten sind da, nichts Geheimes in /api/heute."""
import argparse

import pytest

import dashboard
import server


@pytest.fixture(scope="module")
def client():
    cfg = server.load_config(argparse.Namespace(demo=True, no_calendar=False, port=None))
    app = server.create_app(cfg)
    for src in dashboard._sources.values():
        src.run()                 # einmal laden statt Hintergrund-Threads
    return app.test_client()


def test_week_view(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"<table" in r.data and b"/static/woche.js" in r.data
    c = client.get("/content")
    assert c.headers["X-Version"] and b"<table" in c.data


def test_today_api(client):
    j = client.get("/api/heute").get_json()
    assert j["status"]["untis"]["ok"] and j["day"]["slots"]
    assert j["homework"] and j["events"] and j["weather"]


def test_status_never_contains_urls(client):
    def leak():
        raise RuntimeError("Max retries exceeded with url: https://calendar.google.com/ical/geheim/basic.ics")
    src = dashboard._sources["calendar"]
    old = src.fn
    src.fn = leak
    try:
        src.run()
        body = client.get("/api/heute").get_data(as_text=True)
        assert "geheim" not in body and "calendar.google.com/ical" not in body
    finally:
        src.fn = old
        src.run()


def test_thermal_page(client):
    assert client.get("/thermal").status_code == 200
    assert "cur" in client.get("/api/thermal").get_json()


def test_week_css_has_subject_stripes(client):
    css = client.get("/static/woche.css").get_data(as_text=True)
    assert "td.normal::before" in css and ".no-school" in css and "table.compact" in css
