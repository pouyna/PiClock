"""Tests for the Claude status strip, the hide button and the touch-related server changes.
Run from the project folder with:  pytest dev"""
import json
import time

import pytest
import requests

import claude_status
from test_backend import FakeIMAP


class FakeResponse:
    def __init__(self, status):
        self.status_code, self.content = status, b""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)

    def json(self):
        return {}


def report(**fields):
    return json.dumps(fields)


# --------------------------------------------------------------------------- #
# Claude sessions
# --------------------------------------------------------------------------- #
def test_claude_reports_update_and_order_sessions(tmp_path):
    d = tmp_path / "claude"
    assert claude_status.receive(report(session_id="s1", seq=1, state="working", project="PiClock", task="Add touch", pc="PC"), d, now=1000) == "ok"
    assert claude_status.receive(report(session_id="s2", seq=1, state="done", project="splines", task="Fix refs"), d, now=1001) == "ok"
    # a heartbeat carries no task or project: both are kept, and so is "since"
    assert claude_status.receive(report(session_id="s1", seq=2, state="working"), d, now=1060) == "ok"
    s = claude_status.sessions(d, now=1100)
    assert [x["id"] for x in s] == ["s1", "s2"]          # working before done
    assert s[0]["task"] == "Add touch" and s[0]["project"] == "PiClock" and s[0]["pc"] == "PC"
    assert s[0]["since"] == 1000 and s[0]["updated"] == 1060

    # a state change resets "since"; a report older than the last one is ignored
    assert claude_status.receive(report(session_id="s1", seq=3, state="done"), d, now=1200) == "ok"
    assert claude_status.receive(report(session_id="s1", seq=2.5, state="working"), d, now=1300).startswith("ignored")
    s1 = next(x for x in claude_status.sessions(d, now=1300) if x["id"] == "s1")
    assert s1["state"] == "done" and s1["since"] == 1200

    # attention comes first; "ended" removes the session
    claude_status.receive(report(session_id="s2", seq=2, state="attention"), d, now=1400)
    assert [x["state"] for x in claude_status.sessions(d, now=1400)] == ["attention", "done"]
    assert claude_status.receive(report(session_id="s1", seq=4, state="ended"), d, now=1500) == "removed"
    assert [x["id"] for x in claude_status.sessions(d, now=1500)] == ["s2"]
    # a late report from the closed session can't bring it back; the tombstone is swept after an hour
    assert claude_status.receive(report(session_id="s1", seq=3.5, state="working"), d, now=1501).startswith("ignored")
    assert [x["id"] for x in claude_status.sessions(d, now=1502)] == ["s2"]
    assert (d / "s1.json").exists()
    claude_status.sessions(d, now=1500 + 3601)
    assert not (d / "s1.json").exists()


def test_claude_light_picks_the_most_important_state():
    assert claude_status.light([]) == "green"
    assert claude_status.light([{"state": "idle"}]) == "green"
    assert claude_status.light([{"state": "idle"}, {"state": "done"}]) == "yellow"
    assert claude_status.light([{"state": "done"}, {"state": "working"}]) == "blue"
    assert claude_status.light([{"state": "attention"}, {"state": "working"}]) == "yellow"
    assert claude_status.light([{"state": "working"}, {"state": "limit"}]) == "red"


def test_claude_ignores_junk_and_never_writes_outside_its_folder(tmp_path):
    d = tmp_path / "claude"
    assert claude_status.receive("not json", d) == "ignored: not JSON"
    assert claude_status.receive("[1, 2]", d) == "ignored: not a JSON object"
    assert claude_status.receive(report(state="working"), d) == "ignored: no session_id"
    assert claude_status.receive(report(session_id="x", state="dancing"), d).startswith("ignored")
    assert claude_status.receive(report(session_id="../../etc/passwd", seq=1, state="idle"), d) == "ok"
    files = list(d.glob("*.json"))
    assert len(files) == 1 and files[0].parent == d and not files[0].name.startswith(".")
    assert claude_status.sessions(tmp_path / "missing") == []


def test_claude_sessions_expire_and_are_cleaned_up(tmp_path):
    d = tmp_path / "claude"
    claude_status.receive(report(session_id="old", seq=1, state="working"), d, now=0)
    claude_status.receive(report(session_id="new", seq=1, state="working"), d, now=5000)
    assert [x["id"] for x in claude_status.sessions(d, now=3 * 3600)] == ["new"]
    assert not (d / "old.json").exists()


def test_claude_task_text_is_tidied_and_truncated(tmp_path):
    d = tmp_path / "claude"
    claude_status.receive(report(session_id="s", seq=1, state="working", task="  many\n\n  lines " + "x" * 300), d)
    task = claude_status.sessions(d)[0]["task"]
    assert task.startswith("many lines x") and len(task) <= 120 and task.endswith("…")


def test_api_all_carries_claude_sessions_and_screen_state(app_module, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "CLAUDE_DIR", tmp_path / "claude")
    claude_status.receive(report(session_id="s", seq=1, state="working", project="PiClock"), tmp_path / "claude")
    body = app_module.app.test_client().get("/api/all").get_json()
    assert body["claude"]["enabled"] is True
    assert body["claude"]["data"]["sessions"][0]["project"] == "PiClock"
    assert body["claude"]["data"]["light"] == "blue"
    assert body["screen"] == {"hidden_until": None}
    assert body["config"]["hide_minutes"] == 30

    app_module.set_config("[claude]\nshow = no\n")
    assert app_module.app.test_client().get("/api/all").get_json()["claude"] == {"enabled": False}


# --------------------------------------------------------------------------- #
# The hide button
# --------------------------------------------------------------------------- #
def test_prefs_remember_the_mailbox_choice(app_module, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "PREFS_FILE", tmp_path / "prefs.json")
    client = app_module.app.test_client()
    assert client.post("/api/prefs", json={"mail_hidden": ["Work"]}, environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    local = {"REMOTE_ADDR": "127.0.0.1"}
    assert client.post("/api/prefs", data="x", environ_base=local).status_code == 400
    assert client.post("/api/prefs", json={"mail_hidden": "Work"}, environ_base=local).status_code == 400
    assert client.post("/api/prefs", json={"mail_hidden": [1]}, environ_base=local).status_code == 400
    assert client.post("/api/prefs", json={"mail_hidden": ["x" * 61]}, environ_base=local).status_code == 400
    app_module.write_prefs({"mail_filter": ["Outlook"]})   # the earlier form of the choice is dropped
    r = client.post("/api/prefs", json={"mail_hidden": ["Work", "uic.edu", "Work"]}, environ_base=local)
    assert r.status_code == 200 and r.get_json()["prefs"] == {"mail_hidden": ["Work", "uic.edu"]}
    assert client.get("/api/all").get_json()["prefs"] == {"mail_hidden": ["Work", "uic.edu"]}   # kept on disk, read back
    assert client.post("/api/prefs", json={"mail_hidden": []}, environ_base=local).get_json()["prefs"] == {"mail_hidden": []}


def test_screen_endpoint_only_listens_to_the_pi_itself(app_module, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "control", lambda cmd: calls.append(cmd))
    client = app_module.app.test_client()
    r = client.post("/api/screen", json={"action": "hide"}, environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert r.status_code == 403 and calls == []
    r = client.post("/api/screen", json={"action": "dance"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 400 and calls == []

    app_module.set_config("[display]\nhide_minutes = 0\n")
    r = client.post("/api/screen", json={"action": "hide"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    for _ in range(60):   # the hide runs just after the reply, so the browser hears back first
        if calls:
            break
        time.sleep(0.05)
    assert calls == ["hide"]
    assert app_module.SCREEN["hidden_until"] is None and app_module.SCREEN["timer"] is None   # 0 = no automatic return


def test_hiding_schedules_the_return_and_showing_cancels_it(app_module, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "control", lambda cmd: calls.append(cmd))
    app_module.set_config("[display]\nhide_minutes = 30\n")
    app_module.hide_screen()
    assert calls == ["hide"]
    assert app_module.SCREEN["hidden_until"] > time.time() + 29 * 60
    assert app_module.SCREEN["timer"] is not None
    app_module.show_screen()
    assert calls == ["hide", "show"]
    assert app_module.SCREEN["hidden_until"] is None and app_module.SCREEN["timer"] is None


# --------------------------------------------------------------------------- #
# Smaller server changes
# --------------------------------------------------------------------------- #
def test_weather_server_errors_do_not_blame_the_settings(app_module, monkeypatch):
    app_module.set_config("[weather]\nlatitude = 1\nlongitude = 2\n")
    monkeypatch.setattr(app_module.requests, "get", lambda url, params, timeout: FakeResponse(503))
    with pytest.raises(RuntimeError) as exc:
        app_module.fetch_weather()
    assert "having trouble" in str(exc.value) and "latitude" not in str(exc.value)


def test_retry_delay_backs_off_then_settles(app_module):
    assert app_module.retry_delay(900, 0) == 900
    assert [app_module.retry_delay(900, n) for n in (1, 2, 3, 4, 5)] == [60, 120, 240, 480, 900]
    assert app_module.retry_delay(30, 1) == 30


def test_mail_sends_more_than_the_screen_shows(app_module, monkeypatch):
    monkeypatch.setattr(app_module.imaplib, "IMAP4_SSL", FakeIMAP)
    app_module.set_config("[email]\nshow_latest = 2\n[email:Personal]\nhost = imap.example\nusername = a\npassword = good\n")
    data = app_module.fetch_mail()
    assert data["show"] == 2 and len(data["messages"]) == 3   # the fake mailbox has three unread
