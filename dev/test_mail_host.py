"""Tests for the PC-side helper between the browser extension and the Pi. Run with:  pytest dev"""
import importlib.util
import io
import struct
from pathlib import Path

spec = importlib.util.spec_from_file_location("mail_host", Path(__file__).resolve().parent.parent / "pc" / "mail_host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


def test_ping_send_and_bad_messages():
    sent = []

    def fake(report):
        sent.append(report)
        return True, "ok"

    assert host.handle({"id": 1, "cmd": "ping"}) == {"id": 1, "ok": True, "detail": "ready"}
    r = host.handle({"id": 2, "cmd": "send", "report": {"account": "me@uic.edu", "unread": 1}}, sender=fake, pc="PC")
    assert r == {"id": 2, "ok": True, "detail": "ok"}
    assert sent[0]["account"] == "me@uic.edu" and sent[0]["pc"] == "PC" and sent[0]["seq"] > 0   # the PC's name and clock are added
    assert host.handle({"id": 3, "cmd": "send", "report": {}}, sender=fake) == {"id": 3, "ok": False, "detail": "the report has no account"}
    assert host.handle({"id": 4, "cmd": "dance"}, sender=fake)["ok"] is False
    assert host.handle("junk", sender=fake)["ok"] is False
    assert len(sent) == 1

    def failing(report):
        return False, "the Pi didn't answer in time"

    assert host.handle({"id": 5, "cmd": "send", "report": {"account": "x"}}, sender=failing, pc="PC") == {"id": 5, "ok": False, "detail": "the Pi didn't answer in time"}


def test_sample_is_kept_on_the_pc(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "STATE_DIR", tmp_path)
    r = host.handle({"id": 7, "cmd": "sample", "name": "Outlook/../x", "html": "<div>row</div>" + "x" * 40000})
    assert r == {"id": 7, "ok": True, "detail": "sample kept"}
    files = list(tmp_path.iterdir())
    assert [f.name for f in files] == ["sample-Outlook----x.html"] and files[0].stat().st_size == 30000


def test_framing_round_trip():
    out = io.BytesIO()
    host.write_message(out, {"id": 1, "ok": True})
    raw = out.getvalue()
    assert struct.unpack("<I", raw[:4])[0] == len(raw) - 4
    assert host.read_message(io.BytesIO(raw)) == {"id": 1, "ok": True}
    assert host.read_message(io.BytesIO(b"\x01\x00")) is None                        # the pipe closed
    assert host.read_message(io.BytesIO(struct.pack("<I", 3) + b"{x}")) == {"cmd": "bad"}
    assert host.read_message(io.BytesIO(struct.pack("<I", 5_000_000))) is None       # absurd length
