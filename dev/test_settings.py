"""Tests for the Settings page's reader/writer and the secret vault. Run with:  pytest dev"""
import configparser
import time

import pytest

import settings
import vault

EXAMPLE = """# PiClock settings
[server]
host = 127.0.0.1
port = 8080


[display]
# yes = 14:05, no = 2:05 pm
clock_24h = no
dim_start = 22:00
dim_end = 07:00
chime = yes


[weather]
location_name = Chicago
latitude = 41.88
longitude = -87.63
units = fahrenheit


[calendar]
# urls =
#     https://calendar.google.com/calendar/ical/you%40gmail.com/private-abc123/basic.ics
urls =
    https://calendar.example/one.ics
    webcal://p01.example/two
days_ahead = 7


[email:Personal]
host = imap.gmail.com
port = 993
username = you@gmail.com
password = plain-app-password
folder = INBOX
"""


def parse(path):
    p = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    p.read(path, encoding="utf-8-sig")
    return p


@pytest.fixture
def ini(tmp_path):
    path = tmp_path / "config.ini"
    path.write_text(EXAMPLE, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Vault
# --------------------------------------------------------------------------- #
def test_vault_round_trip_and_pass_through():
    token = vault.encrypt("p@ss:w=rd%1#2")
    assert token.startswith("enc:") and "p@ss" not in token
    assert vault.decrypt(token) == "p@ss:w=rd%1#2"
    assert vault.encrypt(token) == token            # never encrypted twice
    assert vault.decrypt("plain") == "plain"        # old config files keep working
    assert vault.encrypt("") == "" and vault.decrypt("") == ""


def test_vault_rejects_tokens_from_another_key(tmp_path, monkeypatch):
    token = vault.encrypt("secret")
    monkeypatch.setattr(vault, "KEY_FILE", tmp_path / "other.key")
    vault.encrypt("make a new key")
    with pytest.raises(vault.VaultError):
        vault.decrypt(token)


# --------------------------------------------------------------------------- #
# Describing the file for the page
# --------------------------------------------------------------------------- #
def test_describe_shows_everything_but_secrets(ini):
    d = settings.describe(ini)
    display = next(s for s in d["sections"] if s["id"] == "display")
    values = {f["key"]: f["value"] for f in display["fields"]}
    assert values["clock_24h"] is False and values["dim_start"] == "22:00" and values["hide_minutes"] == "30"
    box = d["mailboxes"][0]
    assert box["name"] == "Personal" and box["username"] == "you@gmail.com" and box["password_set"] is True
    assert "password" not in box
    assert [l["label"] for l in d["calendar_links"]] == ["calendar.example, link 1", "p01.example, link 2"]
    assert all("one.ics" not in str(l) and "/two" not in str(l) for l in d["calendar_links"])   # paths can be the secret
    assert d["providers"][0]["name"] == "Gmail"


def test_link_labels_never_show_the_path():
    assert settings.link_label("webcal://p58-caldav.icloud.com/published/2/MTIzNDU2Nzg5", 2) == "p58-caldav.icloud.com, link 2"
    assert settings.link_label("not a url", 1) == "link 1"


# --------------------------------------------------------------------------- #
# Saving the form
# --------------------------------------------------------------------------- #
def test_apply_keeps_comments_and_rewrites_values(ini):
    settings.apply(ini, {"values": {"display.clock_24h": True, "display.dim_start": "", "display.hide_minutes": "45",
                                    "weather.latitude": "41.9", "server.port": "8081"}})
    text = ini.read_text(encoding="utf-8")
    assert "# yes = 14:05, no = 2:05 pm" in text and "# PiClock settings" in text
    p = parse(ini)
    assert p.getboolean("display", "clock_24h") is True
    assert p.get("display", "dim_start") == ""
    assert p.get("display", "hide_minutes") == "45"       # added to the section
    assert p.get("weather", "latitude") == "41.9" and p.getint("server", "port") == 8081
    assert p.get("email:Personal", "password") == "plain-app-password"   # untouched


def test_apply_rejects_bad_values_without_writing(ini):
    before = ini.read_text(encoding="utf-8")
    for values, words in [({"display.dim_start": "25:00"}, "24-hour time"),
                          ({"display.hide_minutes": "abc"}, "number"),
                          ({"display.hide_minutes": "7.5"}, "whole number"),      # a fraction here once broke /api/all
                          ({"server.port": "8080.5"}, "whole number"),            # and this one stopped the service
                          ({"server.port": "nan"}, "number"),
                          ({"weather.latitude": "inf"}, "41.88"),
                          ({"weather.units": "kelvin"}, "choose"),
                          ({"weather.latitude": "north"}, "41.88")]:
        with pytest.raises(settings.SettingsError) as exc:
            settings.apply(ini, {"values": values})
        assert words in str(exc.value)
    assert ini.read_text(encoding="utf-8") == before
    settings.apply(ini, {"values": {"display.dim_amount": "0.7", "display.hide_minutes": "45.0"}})
    p = parse(ini)
    assert p.get("display", "dim_amount") == "0.7" and p.get("display", "hide_minutes") == "45"


def test_headers_with_trailing_text_or_odd_spacing_are_still_one_section(tmp_path):
    path = tmp_path / "config.ini"
    path.write_text("[calendar]\nurls =\n\n[Email: Personal] # my gmail\nhost = imap.gmail.com\nusername = me\npassword = plain\n", encoding="utf-8")
    assert settings.encrypt_in_place(path) == 1
    p = parse(path)
    assert [s for s in p.sections() if s.lower().startswith("email:")] == ["Email: Personal"]   # not duplicated
    assert vault.decrypt(p.get("Email: Personal", "password")) == "plain"
    assert settings.describe(path)["mailboxes"][0]["name"] == "Personal"
    # the page sends the name it was shown; the stored password is found and the spelling kept
    settings.apply(path, {"mailboxes": [{"was": "Personal", "name": "Personal", "host": "imap.gmail.com",
                                         "port": "993", "username": "me", "password": "", "folder": "INBOX"}]})
    p = parse(path)
    assert p.sections() == ["calendar", "Email: Personal"] and vault.decrypt(p.get("Email: Personal", "password")) == "plain"


def test_mailbox_names_differing_only_in_case_are_one_name(ini):
    with pytest.raises(settings.SettingsError) as exc:
        settings.apply(ini, {"mailboxes": [
            {"was": "Personal", "name": "Personal", "host": "h", "port": "993", "username": "u", "password": "", "folder": ""},
            {"name": "personal", "host": "h2", "port": "993", "username": "u2", "password": "pw", "folder": ""}]})
    assert "Two mailboxes" in str(exc.value)
    assert parse(ini).get("email:Personal", "password") == "plain-app-password"


def test_blank_line_inside_the_links_block(tmp_path):
    path = tmp_path / "config.ini"
    path.write_text("[calendar]\nurls =\n    https://a.example/a.ics\n\n    https://b.example/b.ics\ndays_ahead = 7\n", encoding="utf-8")
    assert settings.encrypt_in_place(path) == 2
    p = parse(path)
    links = [l.strip() for l in p.get("calendar", "urls").splitlines() if l.strip()]
    assert len(links) == 2 and all(l.startswith("enc:") for l in links)
    assert p.get("calendar", "days_ahead") == "7"
    settings.apply(path, {"calendar": {"keep": [1], "add": []}})
    links = [l.strip() for l in parse(path).get("calendar", "urls").splitlines() if l.strip()]
    assert len(links) == 1 and vault.decrypt(links[0]) == "https://b.example/b.ics"


def test_apply_mailboxes_add_rename_remove_and_encrypt(ini):
    form = {"mailboxes": [
        {"was": "Personal", "name": "Home", "host": "imap.gmail.com", "port": "993", "username": "you@gmail.com",
         "password": "", "folder": "INBOX"},                      # renamed, password unchanged
        {"name": "Work", "host": "imap.fastmail.com", "port": "993", "username": "me@work.example",
         "password": "new app password", "folder": ""},
    ]}
    settings.apply(ini, form)
    p = parse(ini)
    assert not p.has_section("email:Personal")
    assert p.get("email:Home", "password") == "plain-app-password"
    assert p.get("email:Work", "host") == "imap.fastmail.com" and p.get("email:Work", "folder") == "INBOX"
    stored = p.get("email:Work", "password")
    assert stored.startswith("enc:") and vault.decrypt(stored) == "new app password"

    settings.apply(ini, {"mailboxes": []})
    assert not any(s.lower().startswith("email:") for s in parse(ini).sections())

    with pytest.raises(settings.SettingsError) as exc:
        settings.apply(ini, {"mailboxes": [{"name": "New", "host": "imap.x", "username": "a", "password": ""}]})
    assert "app password" in str(exc.value)


def test_apply_calendar_links_keep_add_and_encrypt(ini):
    settings.apply(ini, {"calendar": {"keep": [1], "add": ["webcal://p02.example/three", ""]}})
    p = parse(ini)
    lines = [l.strip() for l in p.get("calendar", "urls").splitlines() if l.strip()]
    assert lines[0] == "webcal://p01.example/two"             # kept as stored
    assert lines[1].startswith("enc:") and vault.decrypt(lines[1]) == "https://p02.example/three"
    assert p.getint("calendar", "days_ahead") == 7           # the rest of the section survived
    with pytest.raises(settings.SettingsError):
        settings.apply(ini, {"calendar": {"keep": [], "add": ["not a link"]}})


def test_encrypt_in_place_handles_passwords_and_links_once(ini):
    assert settings.encrypt_in_place(ini) == 3                # one password, two links
    p = parse(ini)
    assert vault.decrypt(p.get("email:Personal", "password")) == "plain-app-password"
    links = [l.strip() for l in p.get("calendar", "urls").splitlines() if l.strip()]
    assert all(l.startswith("enc:") for l in links)
    assert [vault.decrypt(l) for l in links] == ["https://calendar.example/one.ics", "webcal://p01.example/two"]
    assert settings.encrypt_in_place(ini) == 0
    assert "# yes = 14:05, no = 2:05 pm" in ini.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# The server side
# --------------------------------------------------------------------------- #
def test_settings_api_is_local_only_and_round_trips(app_module, monkeypatch, tmp_path):
    path = tmp_path / "config.ini"
    path.write_text(EXAMPLE, encoding="utf-8")
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    restarts = []
    monkeypatch.setattr(app_module, "restart_self", lambda: restarts.append(1))
    client = app_module.app.test_client()

    assert client.get("/api/settings", environ_base={"REMOTE_ADDR": "10.0.0.9"}).status_code == 403
    assert client.get("/settings", environ_base={"REMOTE_ADDR": "10.0.0.9"}).status_code == 403
    page = client.get("/settings", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert page.status_code == 200 and b"<title>" in page.data

    d = client.get("/api/settings", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()
    assert d["mailboxes"][0]["password_set"] is True and "password" not in d["mailboxes"][0]

    r = client.post("/api/settings", data="not json", content_type="text/plain", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 400 and not restarts

    r = client.post("/api/settings", json={"values": {"display.chime": False}}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200 and r.get_json()["restarting"] is True and r.get_json()["port"] == 8080
    assert parse(path).getboolean("display", "chime") is False
    for _ in range(60):
        if restarts:
            break
        time.sleep(0.05)
    assert restarts == [1]

    r = client.post("/api/settings", json={"values": {"display.dim_end": "7pm"}}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 400 and "24-hour" in r.get_json()["error"]


def test_mailbox_check_reuses_a_stored_password_only_for_the_same_server(app_module, monkeypatch):
    seen = []

    def fake_check(name, sec, n):
        seen.append((sec.get("host"), sec.get("username"), sec.get("password")))
        return 2, []

    monkeypatch.setattr(app_module, "check_mailbox", fake_check)
    app_module.set_config("[email:Personal]\nhost = imap.gmail.com\nusername = me@gmail.com\npassword = secret\n")
    client = app_module.app.test_client()
    local = {"REMOTE_ADDR": "127.0.0.1"}
    same = {"was": "Personal", "name": "Personal", "host": "IMAP.gmail.com", "port": "993", "username": "me@gmail.com", "password": ""}
    assert client.post("/api/settings/check", json=same, environ_base=local).get_json() == {"ok": True, "unread": 2}
    assert seen[-1] == ("IMAP.gmail.com", "me@gmail.com", "secret")
    other = dict(same, host="imap.evil.example")
    r = client.post("/api/settings/check", json=other, environ_base=local).get_json()
    assert r["ok"] is False and "Enter the app password" in r["error"] and len(seen) == 1
    typed = dict(other, password="typed")
    assert client.post("/api/settings/check", json=typed, environ_base=local).get_json()["ok"] is True
    assert seen[-1][2] == "typed"
    assert client.post("/api/settings/check", json=same, environ_base={"REMOTE_ADDR": "10.0.0.9"}).status_code == 403


def test_hidden_screen_is_remembered_across_a_restart(app_module, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(app_module, "control", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(app_module, "HIDDEN_FILE", tmp_path / ".screen-hidden")
    app_module.set_config("[display]\nhide_minutes = 30\n")
    app_module.hide_screen()
    assert float((tmp_path / ".screen-hidden").read_text()) > time.time() + 29 * 60
    app_module.SCREEN["timer"].cancel()
    app_module.SCREEN.update({"timer": None, "hidden_until": None})   # as after a restart
    app_module.restore_screen()
    assert app_module.SCREEN["timer"] is not None and app_module.SCREEN["hidden_until"] > time.time() + 29 * 60
    app_module.show_screen()
    assert calls == ["hide", "show"] and not (tmp_path / ".screen-hidden").exists()

    (tmp_path / ".screen-hidden").write_text(str(time.time() - 5))   # the wait ended while the service was down
    app_module.restore_screen()
    for _ in range(60):
        if len(calls) == 3:
            break
        time.sleep(0.05)
    assert calls == ["hide", "show", "show"]


def test_mail_uses_decrypted_password_and_reports_a_lost_key(app_module, monkeypatch):
    from test_backend import FakeIMAP
    monkeypatch.setattr(app_module.imaplib, "IMAP4_SSL", FakeIMAP)
    token = vault.encrypt("good")
    app_module.set_config(f"[email:Personal]\nhost = imap.example\nusername = a\npassword = {token}\n")
    assert app_module.fetch_mail()["boxes"][0]["unread"] == 3

    app_module.set_config("[email:Personal]\nhost = imap.example\nusername = a\npassword = enc:not-a-real-token\n")
    assert "key file changed" in app_module.fetch_mail()["boxes"][0]["error"]


def test_calendar_links_are_decrypted_and_lost_ones_are_reported(app_module, monkeypatch):
    token = vault.encrypt("webcal://p01.example/cal")
    app_module.set_config(f"[calendar]\nurls =\n    {token}\n    enc:not-a-real-token\n")
    assert app_module.calendar_urls() == ["https://p01.example/cal", None]
    monkeypatch.setattr(app_module.requests, "get", lambda url, timeout: (_ for _ in ()).throw(app_module.requests.ConnectionError()))
    errors = app_module.fetch_calendar()["errors"]
    assert "Calendar 1: couldn't download" in errors[0] and "Calendar 2" in errors[1] and "key file" in errors[1]
