"""Tests for app.py. Run from the project folder with:  pytest dev"""
import imaplib
from datetime import datetime, timedelta

import pytest
import requests


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, content=b"", status=200, data=None):
        self.content, self.status_code, self._data = content, status, data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} for https://secret.example/private.ics", response=self)

    def json(self):
        return self._data


def make_ics():
    """A calendar with a repeating time-zoned event, an all-day event, a floating
    event with DURATION, a cancelled event and a past event."""
    now = datetime.now()
    today = now.date()
    fmt = lambda dt: dt.strftime("%Y%m%dT%H%M%S")
    ten_days_ago = datetime.combine(today - timedelta(days=10), datetime.min.time())
    lines = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:test
X-WR-CALNAME:Home
BEGIN:VTIMEZONE
TZID:America/Chicago
BEGIN:STANDARD
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
TZOFFSETFROM:-0500
TZOFFSETTO:-0600
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
TZOFFSETFROM:-0600
TZOFFSETTO:-0500
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:1
SUMMARY:Daily standup
DTSTART;TZID=America/Chicago:{fmt(ten_days_ago.replace(hour=9))}
DTEND;TZID=America/Chicago:{fmt(ten_days_ago.replace(hour=9, minute=15))}
RRULE:FREQ=DAILY
END:VEVENT
BEGIN:VEVENT
UID:2
SUMMARY:Trash day
DTSTART;VALUE=DATE:{(today + timedelta(days=1)).strftime('%Y%m%d')}
DTEND;VALUE=DATE:{(today + timedelta(days=2)).strftime('%Y%m%d')}
END:VEVENT
BEGIN:VEVENT
UID:3
SUMMARY:Floating  dinner
LOCATION:Home
DTSTART:{fmt(now + timedelta(hours=1))}
DURATION:PT2H
END:VEVENT
BEGIN:VEVENT
UID:4
SUMMARY:Cancelled thing
STATUS:CANCELLED
DTSTART:{fmt(now + timedelta(hours=2))}
DTEND:{fmt(now + timedelta(hours=3))}
END:VEVENT
BEGIN:VEVENT
UID:5
SUMMARY:Past event
DTSTART:{fmt(now - timedelta(hours=3))}
DTEND:{fmt(now - timedelta(hours=2))}
END:VEVENT
END:VCALENDAR
"""
    return lines.replace("\n", "\r\n").encode()


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def test_config_rejects_unindented_link(app_module, tmp_path):
    bad = tmp_path / "bad.ini"
    bad.write_text("[calendar]\nurls =\nhttps://calendar.example/private.ics\n")
    with pytest.raises(SystemExit) as exc:
        app_module.load_config(bad)
    assert "indented line" in str(exc.value)


def test_config_reads_notepad_file_and_tricky_passwords(app_module, tmp_path):
    path = tmp_path / "notepad.ini"
    text = "# comment first\r\n[email:Personal]\r\nhost = imap.example.com\r\npassword = p@ss:w=rd%1#2\r\n"
    path.write_bytes(b"\xef\xbb\xbf" + text.encode())  # BOM + CRLF, like Windows Notepad
    cfg = app_module.load_config(path)
    assert cfg["email:Personal"]["password"] == "p@ss:w=rd%1#2"


def test_missing_config_says_how_to_fix(app_module, tmp_path):
    with pytest.raises(SystemExit) as exc:
        app_module.load_config(tmp_path / "nope.ini")
    assert "config.example.ini" in str(exc.value)


# --------------------------------------------------------------------------- #
# Calendar
# --------------------------------------------------------------------------- #
def test_calendar_parses_and_filters(app_module, monkeypatch):
    app_module.set_config("[calendar]\nurls =\n    https://cal.example/a.ics\ndays_ahead = 7\n")
    monkeypatch.setattr(app_module.requests, "get", lambda url, timeout: FakeResponse(make_ics()))
    data = app_module.fetch_calendar()

    titles = [e["title"] for e in data["events"]]
    assert "Cancelled thing" not in titles
    assert "Past event" not in titles
    assert "Floating dinner" in titles          # whitespace is tidied
    assert 6 <= titles.count("Daily standup") <= 7
    trash = next(e for e in data["events"] if e["title"] == "Trash day")
    assert trash["all_day"] is True
    dinner = next(e for e in data["events"] if e["title"] == "Floating dinner")
    assert round(dinner["end"] - dinner["start"]) == 2 * 3600
    assert data["errors"] == [] and data["days"] == 7
    starts = [e["start"] for e in data["events"]]
    assert starts == sorted(starts)


def test_calendar_errors_never_reveal_the_link(app_module, monkeypatch):
    app_module.set_config(
        "[calendar]\nurls =\n    https://secret.example/one.ics\n"
        "    https://secret.example/two.ics\n    https://secret.example/three.ics\n"
    )
    responses = iter([
        FakeResponse(status=404),
        FakeResponse(b"<html>not a calendar</html>"),
        requests.ConnectionError("https://secret.example/three.ics unreachable"),
    ])

    def fake_get(url, timeout):
        r = next(responses)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(app_module.requests, "get", fake_get)
    errors = app_module.fetch_calendar()["errors"]
    assert len(errors) == 3
    assert "404" in errors[0] and "iCal" in errors[1] and "internet" in errors[2]
    assert not any("secret.example" in e for e in errors)


def test_webcal_links_become_https(app_module):
    app_module.set_config("[calendar]\nurls =\n    webcal://p01.example/cal\n    # a comment\n")
    assert app_module.calendar_urls() == ["https://p01.example/cal"]


# --------------------------------------------------------------------------- #
# Mail
# --------------------------------------------------------------------------- #
class FakeIMAP:
    def __init__(self, host, port, timeout=None, ssl_context=None):
        assert ssl_context is not None, "the server certificate must be verified"
        if host == "unreachable.example":
            raise OSError("no route")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if password != "good":
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")

    def select(self, folder, readonly=False):
        assert readonly, "mail must be opened read-only"
        return ("OK", [b"3"])

    def uid(self, command, *args):
        if command == "SEARCH":
            return ("OK", [b"101 102 105"])
        uid, what = args
        assert "PEEK" in what, "headers must be fetched with PEEK so mail stays unread"
        headers = {
            b"101": b"From: Bob <bob@x.com>\r\nSubject: Lunch?\r\nDate: Wed, 21 Oct 2026 10:00:00 -0500\r\n\r\n",
            b"102": (b"From: =?UTF-8?B?QW5hIFLDrXo=?= <ana@x.com>\r\n"
                     b"Subject: =?UTF-8?Q?Re:_Concert_tickets_=F0=9F=8E=B5?=\r\n\tfor Friday\r\n"
                     b"Date: Wed, 21 Oct 2026 21:30:00 -0500\r\n\r\n"),
            b"105": b"From: noreply@shop.com\r\nSubject: <script>alert(1)</script>\r\n\r\n",
        }[uid]
        return ("OK", [(b"1 (UID " + uid + b" BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {99}", headers), b")"])


def test_mail_reads_headers_without_marking_read(app_module, monkeypatch):
    monkeypatch.setattr(app_module.imaplib, "IMAP4_SSL", FakeIMAP)
    app_module.set_config(
        "[email]\nshow_latest = 4\n"
        "[email:Personal]\nhost = imap.example\nusername = a\npassword = good\n"
        "[email:Work]\nhost = imap.example\nusername = a\npassword = wrong\n"
        "[email:Old]\nhost = unreachable.example\nusername = a\npassword = good\n"
    )
    data = app_module.fetch_mail()

    assert data["total"] == 3
    boxes = {b["name"]: b for b in data["boxes"]}
    assert boxes["Personal"]["unread"] == 3
    assert "Sign-in failed" in boxes["Work"]["error"]
    assert "Can't reach unreachable.example" in boxes["Old"]["error"]

    first = data["messages"][0]
    assert first["from"] == "Ana Ríz"
    assert first["subject"] == "Re: Concert tickets 🎵 for Friday"
    assert first["id"] == "Personal:102"
    # Raw text is passed through; the page escapes it before display.
    assert any(m["subject"] == "<script>alert(1)</script>" for m in data["messages"])


def test_mail_missing_fields(app_module):
    app_module.set_config("[email:Personal]\nhost = imap.example\n")
    data = app_module.fetch_mail()
    assert "Missing host, username or password" in data["boxes"][0]["error"]


# --------------------------------------------------------------------------- #
# Weather
# --------------------------------------------------------------------------- #
WEATHER_JSON = {
    "current": {"temperature_2m": 51.6, "apparent_temperature": 47.5, "relative_humidity_2m": 61,
                "weather_code": 2, "wind_speed_10m": 12.2, "is_day": 1},
    "daily": {"time": ["2026-10-21", "2026-10-22"], "weather_code": [2, 61],
              "temperature_2m_max": [58.1, 54.4], "temperature_2m_min": [44.2, None],
              "precipitation_probability_max": [10, 70],
              "sunrise": ["2026-10-21T07:12", "2026-10-22T07:13"],
              "sunset": ["2026-10-21T18:01", "2026-10-22T17:59"]},
}


def test_weather_shape(app_module, monkeypatch):
    app_module.set_config("[weather]\nlocation_name = Chicago\nlatitude = 41.88\nlongitude = -87.63\n")
    seen = {}

    def fake_get(url, params, timeout):
        seen.update(params)
        return FakeResponse(data=WEATHER_JSON)

    monkeypatch.setattr(app_module.requests, "get", fake_get)
    w = app_module.fetch_weather()
    assert seen["temperature_unit"] == "fahrenheit" and seen["wind_speed_unit"] == "mph"
    assert (w["temp"], w["feels"], w["wind"], w["is_day"]) == (52, 48, 12, True)
    assert w["days"][1]["low"] is None and w["days"][1]["precip"] == 70
    assert w["sunrise"] == "2026-10-21T07:12"


def test_weather_celsius(app_module, monkeypatch):
    app_module.set_config("[weather]\nlatitude = 1\nlongitude = 2\nunits = celsius\n")
    seen = {}
    monkeypatch.setattr(app_module.requests, "get",
                        lambda url, params, timeout: seen.update(params) or FakeResponse(data=WEATHER_JSON))
    assert app_module.fetch_weather()["wind_unit"] == "km/h"
    assert seen["temperature_unit"] == "celsius"


@pytest.mark.parametrize("failure, expected", [
    (FakeResponse(status=400), "Check latitude and longitude"),
    (requests.ConnectionError("dns"), "Couldn't reach the weather service"),
])
def test_weather_errors_are_friendly(app_module, monkeypatch, failure, expected):
    app_module.set_config("[weather]\nlatitude = 1\nlongitude = 2\n")

    def fake_get(url, params, timeout):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(app_module.requests, "get", fake_get)
    with pytest.raises(RuntimeError) as exc:
        app_module.fetch_weather()
    assert expected in str(exc.value)


# --------------------------------------------------------------------------- #
# Web endpoints
# --------------------------------------------------------------------------- #
def test_api_endpoints(app_module):
    client = app_module.app.test_client()
    assert client.get("/api/health").get_json() == {"ok": True}

    r = client.get("/api/all")
    body = r.get_json()
    assert r.headers["Cache-Control"] == "no-store"
    assert set(body) == {"boot_id", "config", "weather", "mail", "calendar", "claude", "screen", "prefs"}
    assert body["mail"] == {"enabled": False}          # nothing configured
    assert body["config"]["clock_24h"] is False

    page = client.get("/")
    assert page.status_code == 200 and b"<title>" in page.data
    assert client.get("/static/fonts/BigShouldersDisplay.ttf").status_code == 200


def test_display_settings_are_clamped(app_module):
    app_module.set_config("[display]\nclock_24h = yes\ndim_amount = 5\nchime = no\n")
    s = app_module.display_settings()
    assert s["clock_24h"] is True and s["chime"] is False and s["dim_amount"] == 0.95
