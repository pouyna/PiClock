#!/usr/bin/env python3
"""Mailboxes read by the browser extension on the PC, for the PiClock screen.

Microsoft mail (Outlook, Hotmail, work and school accounts such as UIC) no longer
accepts password sign-in, so for those the PC's browser extension (pc/extension)
reads the inbox the owner already has open in a tab and reports it over SSH:

    ssh pi@raspberrypi.local piclock mail      (one JSON object on stdin)

which ends up here as `receive()`. Each mailbox is a small file in the mail/ folder
next to app.py, and app.py reads them with `mailboxes()` to show them next to the
mailboxes it checks itself. Nothing in this file talks to the network.

A full report looks like {"account": "you@uic.edu", "provider": "Outlook",
"seq": 1761100000.5, "unread": 12, "messages": [{"id": "AAMk...", "from": "Priya Raman",
"subject": "Re: hike", "ts": 1761099000}], "pc": "DESKTOP-1"}. "ts" may be null when the
page shows a time only as text; then "when" carries that text. A report with
"needs_login": true says the page is asking for a sign-in and keeps the last messages.
{"account": ..., "state": "removed"} forgets the mailbox; the extension sends it the moment
the tab closes, so the clock mirrors the tabs that are open. Reports that arrive out of
order (an older "seq") are ignored. Everything in a report is text from a web page
and is treated as untrusted: tidied, length-capped, never used as a path.
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

MAX_MESSAGES = 20
EXPIRE_SECONDS = 20 * 60   # a tab reports at least every 5 min while open; after this long without one, the mailbox is gone
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def clean(text, limit):
    text = " ".join(str(text if text is not None else "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _path(folder, account):
    name = _UNSAFE.sub("_", account.lower())[:80].strip("._") or "mailbox"   # never a path, never hidden
    return Path(folder) / f"{name}.json"


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write(path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        os.chmod(tmp, 0o600)   # senders and subjects: owner-only
    except OSError:
        pass
    os.replace(tmp, path)   # atomic, so app.py never reads a half-written file


def _number(value, low, high):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if low <= v <= high else None


def _messages(raw, now):
    out = []
    for item in (raw if isinstance(raw, list) else [])[:MAX_MESSAGES]:
        if not isinstance(item, dict):
            continue
        sender = clean(item.get("from"), 80) or "Unknown sender"
        subject = clean(item.get("subject"), 200) or "(no subject)"
        when = clean(item.get("when"), 40)
        ts = _number(item.get("ts"), 946684800, now + 86400)   # a real date between 2000 and tomorrow
        ident = clean(item.get("id"), 200)
        if not ident:   # pages that show no message id: the same mail keeps the same id as long as it reads the same
            ident = "h:" + hashlib.sha1(f"{sender}\n{subject}\n{when or ts}".encode("utf-8")).hexdigest()[:20]
        out.append({"id": ident, "from": sender, "subject": subject, "ts": ts, "when": when})
    return out


def receive(text, folder, now=None):
    """Apply one report from the PC. Returns a short message for the sender."""
    now = time.time() if now is None else now
    try:
        msg = json.loads(text)
    except ValueError:
        return "ignored: not JSON"
    if not isinstance(msg, dict):
        return "ignored: not a JSON object"
    account = clean(msg.get("account"), 120)
    if not account:
        return "ignored: no account"
    seq = _number(msg.get("seq"), 0, 4102444800) or 0.0

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = _path(folder, account)
    old = _read(path) or {}
    if old and seq < float(old.get("seq") or 0):
        return "ignored: older than the last report"
    if str(msg.get("state") or "") == "removed":
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return "removed"

    provider = clean(msg.get("provider"), 40) or old.get("provider") or "Mail"
    label = clean(msg.get("label"), 40) or old.get("label") or (account.rsplit("@", 1)[1].lower() if "@" in account else provider)
    new = {
        "account": account,
        "provider": provider,
        "label": label,
        "seq": seq,
        "pc": clean(msg.get("pc"), 40) or old.get("pc", ""),
        "updated": now,
    }
    if msg.get("needs_login"):
        # The page wants a sign-in: keep what we last saw, but say so.
        new.update({"needs_login": True, "unread": old.get("unread"), "messages": old.get("messages", []),
                    "read_at": old.get("read_at", now)})
    else:
        unread = _number(msg.get("unread"), 0, 99999)
        messages = _messages(msg.get("messages"), now)
        new.update({"needs_login": False, "unread": int(unread) if unread is not None else len(messages),
                    "messages": messages, "read_at": now})
    _write(path, new)
    return "ok"


def mailboxes(folder, now=None):
    """Every mailbox the PC has reported lately, by label. One that has gone quiet is forgotten."""
    now = time.time() if now is None else now
    folder = Path(folder)
    if not folder.is_dir():
        return []
    boxes = []
    for path in sorted(folder.glob("*.json")):
        data = _read(path)
        if not data or not data.get("account"):
            continue
        updated = float(data.get("updated") or 0)
        if now - updated > EXPIRE_SECONDS:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        read_at = float(data.get("read_at") or updated)
        boxes.append({
            "account": data["account"],
            "provider": data.get("provider") or "Mail",
            "label": data.get("label") or data["account"],
            "unread": data.get("unread"),
            "messages": [m for m in data.get("messages", []) if isinstance(m, dict)],
            "needs_login": bool(data.get("needs_login")),
            "pc": data.get("pc") or "",
            "updated": updated,
            "read_at": read_at,
        })
    boxes.sort(key=lambda b: b["label"].lower())
    return boxes


if __name__ == "__main__":
    # `piclock mail` runs this with the report on stdin.
    print(receive(sys.stdin.read(), Path(__file__).resolve().parent / "mail"))
