"""Tests for the mailboxes the PC's browser extension reports. Run with:  pytest dev"""
import json
import time

import pytest

import browser_mail
from test_backend import FakeIMAP

DAY = 86400
T0 = 1_700_000_000.0   # a real date: the module rejects message times before the year 2000


def report(now=T0, **extra):
    base = {"account": "Me@UIC.edu", "provider": "Outlook", "seq": now, "unread": 2, "pc": "DESKTOP-1",
            "messages": [{"id": "AAQk1", "from": "Priya Raman", "subject": "Re: hike", "ts": now - 600},
                         {"id": "AAQk2", "from": "  Facilities ", "subject": "Parking   garage", "ts": None, "when": "Mon 10/19"}]}
    base.update(extra)
    return json.dumps(base)


def test_a_report_becomes_a_mailbox(tmp_path):
    assert browser_mail.receive(report(), tmp_path, now=T0) == "ok"
    boxes = browser_mail.mailboxes(tmp_path, now=T0 + 1)
    assert len(boxes) == 1
    b = boxes[0]
    assert (b["account"], b["label"], b["provider"], b["unread"], b["pc"], b["needs_login"]) == ("Me@UIC.edu", "uic.edu", "Outlook", 2, "DESKTOP-1", False)
    assert b["messages"][0] == {"id": "AAQk1", "from": "Priya Raman", "subject": "Re: hike", "ts": T0 - 600, "when": ""}
    assert b["messages"][1] == {"id": "AAQk2", "from": "Facilities", "subject": "Parking garage", "ts": None, "when": "Mon 10/19"}
    assert [p.name for p in tmp_path.iterdir()] == ["me_uic.edu.json"]


def test_junk_is_ignored_or_tidied_and_never_a_path(tmp_path):
    assert browser_mail.receive("nope", tmp_path) == "ignored: not JSON"
    assert browser_mail.receive("[1]", tmp_path) == "ignored: not a JSON object"
    assert browser_mail.receive(json.dumps({"provider": "x"}), tmp_path) == "ignored: no account"
    raw = {"account": "../../etc/passwd", "seq": 5, "unread": -3, "label": "<b>x</b>" * 30,
           "messages": [{"from": "x" * 500, "subject": "", "ts": 1e12}, "junk", {"id": "", "from": "A", "subject": "B", "when": "now"}]
                       + [{"id": str(i), "from": "F", "subject": "S"} for i in range(40)]}
    assert browser_mail.receive(json.dumps(raw), tmp_path, now=10) == "ok"
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and files[0].parent == tmp_path and ".." not in files[0].name
    b = browser_mail.mailboxes(tmp_path, now=11)[0]
    assert len(b["messages"]) <= browser_mail.MAX_MESSAGES and b["unread"] == len(b["messages"])   # a bad count: count what was read
    first, hashed = b["messages"][0], b["messages"][1]
    assert len(first["from"]) == 80 and first["from"].endswith("…") and first["subject"] == "(no subject)" and first["ts"] is None
    assert first["id"].startswith("h:") and hashed["id"].startswith("h:") and first["id"] != hashed["id"]
    assert len(b["label"]) == 40


def test_order_sign_in_and_removal(tmp_path):
    browser_mail.receive(report(), tmp_path, now=T0)
    assert browser_mail.receive(report(seq=T0 - 100, unread=9), tmp_path, now=T0 + 1) == "ignored: older than the last report"
    assert browser_mail.receive(json.dumps({"account": "me@uic.edu", "seq": T0 + 100, "needs_login": True}), tmp_path, now=T0 + 100) == "ok"
    b = browser_mail.mailboxes(tmp_path, now=T0 + 101)[0]
    assert b["needs_login"] and b["unread"] == 2 and len(b["messages"]) == 2   # keeps what it last saw
    assert b["updated"] == T0 + 100 and b["read_at"] == T0
    assert browser_mail.receive(json.dumps({"account": "ME@uic.edu", "seq": T0 + 200, "state": "removed"}), tmp_path) == "removed"
    assert browser_mail.mailboxes(tmp_path) == []
    assert browser_mail.receive(json.dumps({"account": "ME@uic.edu", "seq": T0 + 300, "state": "removed"}), tmp_path) == "removed"


def test_quiet_mailboxes_hide_their_mail_then_disappear(tmp_path):
    browser_mail.receive(report(), tmp_path, now=T0)
    b = browser_mail.mailboxes(tmp_path, now=T0 + DAY + 1)[0]
    assert b["unread"] is None and b["messages"] == []
    assert browser_mail.mailboxes(tmp_path, now=T0 + 3 * DAY + 1) == [] and not list(tmp_path.glob("*.json"))


class FakePoller:
    def __init__(self, data):
        self.data = data

    def snapshot(self):
        return self.data


def test_api_shows_browser_mailboxes_next_to_the_clocks_own(app_module, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "BROWSER_MAIL_DIR", tmp_path)
    now = time.time()
    browser_mail.receive(report(now=now), tmp_path, now=now)
    client = app_module.app.test_client()
    m = client.get("/api/all").get_json()["mail"]
    assert m["enabled"] and m["data"]["total"] == 2 and m["data"]["show"] == 4
    assert m["data"]["boxes"] == [{"name": "uic.edu", "unread": 2, "error": None, "note": None}]
    assert [x["id"] for x in m["data"]["messages"]] == ["uic.edu:AAQk2", "uic.edu:AAQk1"]   # text-only times count as "just read"
    assert m["data"]["messages"][0]["when"] == "Mon 10/19" and m["data"]["messages"][1]["when"] == ""

    # next to a mailbox the clock checks itself
    app_module.POLLERS["mail"] = FakePoller({"enabled": True, "updated": now, "error": None, "data": {
        "total": 1, "show": 4, "boxes": [{"name": "Personal", "unread": 1, "error": None}],
        "messages": [{"id": "Personal:7", "mailbox": "Personal", "from": "Bob", "subject": "Lunch?", "ts": now - 60}]}})
    m = client.get("/api/all").get_json()["mail"]
    assert m["data"]["total"] == 3 and [b["name"] for b in m["data"]["boxes"]] == ["Personal", "uic.edu"]
    assert [x["id"] for x in m["data"]["messages"]] == ["uic.edu:AAQk2", "Personal:7", "uic.edu:AAQk1"]
    app_module.POLLERS.clear()

    # the notes: an old report, a sign-in page, a day of silence
    browser_mail.receive(report(now=now - 20 * 60, seq=now + 0.5), tmp_path, now=now - 20 * 60)
    assert client.get("/api/all").get_json()["mail"]["data"]["boxes"][0]["note"] == "Last update from the PC 20 min ago."
    browser_mail.receive(json.dumps({"account": "me@uic.edu", "seq": now + 1, "needs_login": True}), tmp_path, now=now)
    assert client.get("/api/all").get_json()["mail"]["data"]["boxes"][0]["note"] == "Outlook is asking for a sign-in on the PC."
    browser_mail.receive(report(now=now - 2 * DAY, seq=now + 2), tmp_path, now=now - 2 * DAY)
    box = client.get("/api/all").get_json()["mail"]["data"]["boxes"][0]
    assert box["unread"] is None and box["note"] == "Last update from the PC 2 days ago."


def test_without_reports_mail_is_as_before(app_module, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "BROWSER_MAIL_DIR", tmp_path / "none")
    assert client_mail(app_module)["enabled"] is False


def client_mail(app_module):
    return app_module.app.test_client().get("/api/all").get_json()["mail"]


def test_age_text_speaks_plainly(app_module):
    assert [app_module.age_text(s) for s in (30, 90, 20 * 60, 3 * 3600, 25 * 3600, 3 * DAY)] == ["a minute", "a minute", "20 min", "3 h", "a day", "3 days"]


def test_microsoft_hosts_get_the_extension_advice(app_module, monkeypatch):
    monkeypatch.setattr(app_module.imaplib, "IMAP4_SSL", FakeIMAP)
    app_module.set_config("[email:UIC]\nhost = outlook.office365.com\nusername = me@uic.edu\npassword = bad\n")
    with pytest.raises(app_module.MailError) as exc:
        app_module.check_mailbox("UIC", app_module.cfg["email:UIC"], 4)
    assert "browser extension" in str(exc.value)
    app_module.set_config("[email:G]\nhost = imap.gmail.com\nusername = me@gmail.com\npassword = bad\n")
    with pytest.raises(app_module.MailError) as exc:
        app_module.check_mailbox("G", app_module.cfg["email:G"], 4)
    assert "app password" in str(exc.value)
