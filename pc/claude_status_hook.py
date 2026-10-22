#!/usr/bin/env python3
"""Tells the PiClock screen what this Claude Code session is doing.

Claude Code runs this as a hook on this PC (README.md, "The Claude light"). It reads
the hook event from stdin, turns it into one of five states (working, done,
attention, limit, idle, or ended when the session closes) and sends it to the Pi:

    ssh pi@raspberrypi.local piclock claude

It prints nothing and always exits 0, so it can never interfere with Claude. The
only things kept on this PC are heartbeat stamps and a small log in
%LOCALAPPDATA%\\PiClock. Run it by hand with --test to check the link to the Pi.
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

PI = os.environ.get("PICLOCK_PI", "pi@raspberrypi.local")
STATE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PiClock"
HEARTBEAT_SECONDS = 60      # tool calls fire constantly; one "still working" a minute is plenty
# Whole phrases only: "could not generate a response" must not count as "rate".
LIMIT_RE = re.compile(r"\b(rate[ _-]?limit\w*|usage limit|session limit|limit reached|hit your (?:\w+ )?limit|quota|429)\b", re.I)
SSH_SECONDS = 25


def first_line(text, limit=120):
    line = next((l.strip() for l in str(text or "").splitlines() if l.strip()), "")
    return line if len(line) <= limit else line[:limit - 1].rstrip() + "…"


def state_for(event):
    """The report for one hook event, or None when the event says nothing new.
    A report is {"state": ..., "task": ... (optional), "heartbeat": True (optional)}."""
    name = event.get("hook_event_name")
    if event.get("agent_id"):
        return None   # a subagent's own events; the main session speaks for itself
    if name == "SessionStart":
        if event.get("source") == "compact":
            return None   # the conversation was compacted mid-task; nothing changed
        return {"state": "idle", "task": ""}
    if name == "UserPromptSubmit":
        return {"state": "working", "task": first_line(event.get("prompt"))}
    if name in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        return {"state": "working", "heartbeat": True}
    if name == "PermissionRequest":
        return {"state": "attention"}
    if name == "Notification":
        kind = event.get("notification_type") or ""
        if kind == "permission_prompt":
            return {"state": "attention"}
        if kind == "idle_prompt":
            return {"state": "done"}
        if kind == "quota_auto_resume_fired":
            return {"state": "working"}
        if kind in ("quota_auto_resume_stale", "quota_auto_resume_disabled"):
            return {"state": "limit", "task": first_line(event.get("message"))}
        return None
    if name == "Stop":
        return {"state": "done"}
    if name == "StopFailure":   # the turn ended on an API error; a usage limit is the usual one
        # "error" is a key such as rate_limit or server_error; the readable text is elsewhere
        kind = str(event.get("error") or "").lower()
        text = f"{event.get('error_details') or ''} {event.get('last_assistant_message') or ''}"
        limit = kind == "rate_limit" or bool(LIMIT_RE.search(text))
        message = event.get("last_assistant_message") or event.get("error_details") or kind.replace("_", " ")
        return {"state": "limit" if limit else "done", "task": first_line(message)}
    if name == "SessionEnd":
        return {"state": "ended"}
    return None


def mark(stamp, now):
    """Remember that a report went out at `now` (the stamp file's time is the report time)."""
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch()
        os.utime(stamp, (now, now))
    except OSError:
        pass


def heartbeat_due(stamp, now, every=HEARTBEAT_SECONDS):
    """True when a heartbeat should be sent: nothing went out in the last `every` seconds."""
    try:
        if now - stamp.stat().st_mtime < every:
            return False
    except OSError:
        pass
    mark(stamp, now)
    return True


def build_report(event, now=None, pc=None):
    """The JSON object for the Pi, or None. Heartbeats are rate-limited."""
    now = time.time() if now is None else now
    report = state_for(event)
    session_id = str(event.get("session_id") or "").strip()
    if not report or not session_id:
        return None
    stamp = STATE_DIR / "beats" / "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in session_id)
    if report.pop("heartbeat", False) and not heartbeat_due(stamp, now):
        return None
    if report["state"] != "ended":
        mark(stamp, now)
    else:
        try:
            stamp.unlink()
        except OSError:
            pass
    cwd = str(event.get("cwd") or "")
    report.update({
        "session_id": session_id,
        "seq": now,
        "project": Path(cwd).name or "Claude",
        "pc": pc if pc is not None else socket.gethostname(),
    })
    return report


def send(report):
    """Push one report to the Pi. Returns what the Pi said, or why it failed."""
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", "-o", "ServerAliveInterval=5",
           "-o", "ServerAliveCountMax=2", PI, "piclock", "claude"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run(cmd, input=json.dumps(report).encode("utf-8"), capture_output=True,
                              timeout=SSH_SECONDS, creationflags=flags)
    except subprocess.TimeoutExpired:
        return "failed: the Pi didn't answer in time"
    except OSError as exc:
        return f"failed: {exc}"
    if done.returncode != 0:
        return "failed: " + (done.stderr.decode("utf-8", "replace").strip().splitlines() or ["ssh error"])[-1]
    return done.stdout.decode("utf-8", "replace").strip() or "ok"


def log(line):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = STATE_DIR / "hook.log"
        if path.exists() and path.stat().st_size > 200_000:
            path.write_text("", encoding="utf-8")
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass


def read_stdin(deadline=3.0):
    """The hook event, read with a deadline: Claude Code may not always close the pipe."""
    chunks = []

    def reader():
        try:
            chunks.append(sys.stdin.buffer.read())
        except Exception:
            pass

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(deadline)
    return chunks[0] if chunks else b""


def main():
    if "--test" in sys.argv:   # the one way this script talks: a manual check of the link
        report = {"session_id": "test", "seq": time.time(), "state": "idle", "project": "test", "pc": socket.gethostname()}
        print("Sending a test report to", PI, "...")
        print(send(report))
        print("Now remove it:", send({"session_id": "test", "seq": time.time(), "state": "ended"}))
        return
    try:
        event = json.loads(read_stdin() or b"{}")
    except ValueError:
        return
    if not isinstance(event, dict):
        return
    report = build_report(event)
    if not report:
        return
    result = send(report)
    log(f"{report['state']:<9} {report['project']:<20} {result}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:   # never let anything reach Claude
        log(f"error: {exc.__class__.__name__}: {exc}")
    finally:
        sys.stdout.flush()
        os._exit(0)
