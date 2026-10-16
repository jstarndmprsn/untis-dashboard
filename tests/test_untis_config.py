import pytest

import config
import untis


def test_find_class_by_name_or_long_name():
    classes = [{"id": 1, "name": "9a", "longName": "Klasse 9a"}, {"id": 2, "name": "9 b", "longName": ""}]
    assert untis.find_class(classes, "9A")["id"] == 1
    assert untis.find_class(classes, "klasse 9a")["id"] == 1
    assert untis.find_class(classes, "9b")["id"] == 2
    assert untis.find_class(classes, "10c") is None


def test_timegrid_picks_longest_day_sorted():
    units = [{"day": 2, "timeUnits": [{"name": "2", "startTime": 850, "endTime": 935},
                                       {"name": "1", "startTime": 800, "endTime": 845}]},
             {"day": 3, "timeUnits": [{"name": "1", "startTime": 800, "endTime": 845}]}]
    assert untis.timegrid(units) == [{"name": "1", "start": 800, "end": 845}, {"name": "2", "start": 850, "end": 935}]
    assert untis.timegrid([]) is None


def test_subject_colors_only_valid_hex():
    out = untis.subject_colors([{"name": "Mat", "backColor": "C00000"}, {"name": "Deu"},
                                {"name": "Eng", "backColor": "nope!!"}])
    assert out == {"Mat": "#c00000"}


def test_is_auth_error():
    assert untis.is_auth_error(untis.UntisError("x", untis.ERR_BAD_CREDENTIALS))
    assert untis.is_auth_error(RuntimeError("bad credentials"))
    assert not untis.is_auth_error(untis.UntisError("no right for timetable", -8509))


class FakeClient:
    logins = 0

    def __init__(self, fail_first_call=False):
        self.fail = fail_first_call
        self.closed = False

    def __enter__(self):
        FakeClient.logins += 1
        return self

    def __exit__(self, *a):
        self.closed = True


def test_kept_session_reuses_login_and_retries_once(monkeypatch):
    FakeClient.logins = 0
    monkeypatch.setattr(untis, "connect", lambda cfg: FakeClient())
    s = untis.KeptSession({})
    assert s.run(lambda u: 1) == 1 and s.run(lambda u: 2) == 2
    assert FakeClient.logins == 1                          # zweiter Abruf ohne neuen Login

    calls = []

    def flaky(u):                                          # abgelaufene Sitzung: erster Versuch scheitert
        calls.append(u)
        if len(calls) == 1:
            raise untis.UntisError("not authenticated", untis.ERR_NOT_AUTHENTICATED)
        return "ok"
    assert s.run(flaky) == "ok" and FakeClient.logins == 2


def test_kept_session_does_not_retry_bad_password(monkeypatch):
    FakeClient.logins = 0
    monkeypatch.setattr(untis, "connect", lambda cfg: FakeClient())
    s = untis.KeptSession({})
    with pytest.raises(untis.UntisError):
        s.run(lambda u: (_ for _ in ()).throw(untis.UntisError("bad credentials", untis.ERR_BAD_CREDENTIALS)))
    assert FakeClient.logins == 1 and s.client is None


@pytest.mark.parametrize("value", ["", "simple", "with space", 'quo"te', "back\\slash", "pä$$wörd#1", "https://x/y?a=b&c"])
def test_config_quote_roundtrip(tmp_path, value):
    p = tmp_path / "config.env"
    p.write_text(f"KEY={config.quote(value)}\n", encoding="utf-8")
    assert config.load_env(str(p))["KEY"] == value


def test_config_defaults_and_flags(tmp_path):
    p = tmp_path / "config.env"
    p.write_text("GCAL_ICS_URL=https://old\nLAN_ACCESS=1\n")
    cfg = config.load(str(p))
    assert cfg["CALENDAR_ICS_URL"] == "https://old"      # alter Schlüsselname
    assert cfg["PORT"] == "5000" and config.enabled(cfg, "LAN_ACCESS")
    assert not config.enabled(config.load(str(tmp_path / "fehlt.env")), "LAN_ACCESS")
