#!/usr/bin/env python3
"""Claude Code session status for the PiClock screen.

The PC reports what each Claude Code session is doing by running, over SSH,

    ssh pi@raspberrypi.local piclock claude      (one JSON object on stdin)

which ends up here as `receive()`. Every session is kept as a small file in the
claude/ folder next to app.py, and app.py reads them with `sessions()` for the
screen. Nothing in this file talks to the network.

A report looks like {"session_id": "...", "seq": 1761100000.5, "state": "working",
"project": "PiClock", "task": "Add touch gestures", "pc": "DESKTOP-1"}. Fields other
than session_id, seq and state are optional: a missing field keeps the previous
value, so heartbeats can be small. `seq` is the PC's clock at the time of the
event; reports that arrive out of order are ignored. State "ended" removes the
session.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

STATES = ("limit", "attention", "working", "done", "idle")   # also the order of importance
ENDED = "ended"
# A session drops off the screen when nothing has been heard from it for this long.
# Working sessions send a heartbeat at least every minute while tools run, but a
# long think can be quiet for a while, so give them time.
EXPIRE_SECONDS = {"limit": 5 * 3600, "attention": 6 * 3600, "working": 2 * 3600, "done": 6 * 3600, "idle": 3600}
# The one light on the clock: the most important state of any session wins.
LIGHT = {"limit": "red", "attention": "yellow", "working": "blue", "done": "yellow", "idle": "green"}


def light(sessions):
    """red: a session hit the usage limit. blue: working. yellow: done or waiting for the owner. green: nothing running."""
    for state in STATES:
        if any(s["state"] == state for s in sessions):
            return LIGHT[state]
    return "green"
TASK_CHARS = 120
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def clean(text, limit=TASK_CHARS):
    text = " ".join(str(text if text is not None else "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _path(folder, session_id):
    name = _UNSAFE.sub("_", session_id)[:80].strip("._") or "session"   # never a path, never hidden
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
        os.chmod(tmp, 0o600)   # the task line comes from the owner's prompts: owner-only
    except OSError:
        pass
    os.replace(tmp, path)   # atomic, so app.py never reads a half-written file


def receive(text, folder, now=None):
    """Apply one report from the PC. Returns a short message for the sender."""
    now = time.time() if now is None else now
    try:
        msg = json.loads(text)
    except ValueError:
        return "ignored: not JSON"
    if not isinstance(msg, dict):
        return "ignored: not a JSON object"
    session_id = str(msg.get("session_id") or "").strip()
    state = str(msg.get("state") or "").strip()
    if not session_id:
        return "ignored: no session_id"
    if state not in STATES and state != ENDED:
        return f"ignored: unknown state {state!r}"
    try:
        seq = float(msg.get("seq") or 0)
    except (TypeError, ValueError):
        seq = 0.0

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = _path(folder, session_id)
    old = _read(path) or {}
    if old and seq < float(old.get("seq") or 0):
        return "ignored: older than the last report"
    if state == ENDED:
        # A tombstone rather than a deletion, so a late report from the closing session can't bring it back.
        _write(path, {"id": session_id, "seq": seq, "state": ENDED, "updated": now})
        return "removed"

    new = {
        "id": session_id,
        "seq": seq,
        "state": state,
        "project": clean(msg["project"], 60) if msg.get("project") else old.get("project") or "Claude",
        "task": clean(msg["task"]) if "task" in msg else old.get("task", ""),
        "pc": clean(msg["pc"], 40) if msg.get("pc") else old.get("pc", ""),
        # "since" is when the current state began; it survives heartbeats in the same state
        "since": old.get("since", now) if old.get("state") == state else now,
        "updated": now,
    }
    _write(path, new)
    return "ok"


def sessions(folder, now=None):
    """Every live session, most urgent first. Expired ones are removed."""
    now = time.time() if now is None else now
    folder = Path(folder)
    if not folder.is_dir():
        return []
    live = []
    for path in sorted(folder.glob("*.json")):
        data = _read(path)
        if not data or data.get("state") not in STATES:
            if data and data.get("state") == ENDED and now - float(data.get("updated") or 0) > 3600:
                try:
                    path.unlink()   # an old tombstone
                except OSError:
                    pass
            continue
        age = now - float(data.get("updated") or 0)
        if age > EXPIRE_SECONDS[data["state"]]:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        live.append({
            "id": data.get("id", path.stem),
            "project": data.get("project") or "Claude",
            "task": data.get("task") or "",
            "state": data["state"],
            "pc": data.get("pc") or "",
            "since": float(data.get("since") or data.get("updated") or now),
            "updated": float(data.get("updated") or now),
        })
    live.sort(key=lambda s: (STATES.index(s["state"]), -s["since"]))
    return live


if __name__ == "__main__":
    # `piclock claude` runs this with the report on stdin.
    print(receive(sys.stdin.read(), Path(__file__).resolve().parent / "claude"))
