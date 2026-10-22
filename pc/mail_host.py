#!/usr/bin/env python3
"""The helper between the PiClock mail extension and the Pi (Chrome native messaging).

Chrome starts this program through pc/mail_host.bat (registered by pc/setup_extension.py)
when the extension connects, and talks to it on stdin/stdout: each message is a 4-byte
little-endian length followed by JSON. For

    {"id": 1, "cmd": "send", "report": {...}}

it adds this PC's name, runs `ssh pi@raspberrypi.local piclock mail` with the report on
stdin, and answers {"id": 1, "ok": true, "detail": "ok"}. {"cmd": "ping"} answers at once.
Stdlib only; a small log in %LOCALAPPDATA%\\PiClock\\mail_host.log, never the mail itself.
"""
import json
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

PI = os.environ.get("PICLOCK_PI", "pi@raspberrypi.local")
STATE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PiClock"
SSH_SECONDS = 25


def log(line):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = STATE_DIR / "mail_host.log"
        if path.exists() and path.stat().st_size > 200_000:
            path.write_text("", encoding="utf-8")
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass


def send(report):
    """Push one report to the Pi. Returns (ok, what the Pi said or why it failed)."""
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", "-o", "ServerAliveInterval=5",
           "-o", "ServerAliveCountMax=2", PI, "piclock", "mail"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run(cmd, input=json.dumps(report).encode("utf-8"), capture_output=True,
                              timeout=SSH_SECONDS, creationflags=flags)
    except subprocess.TimeoutExpired:
        return False, "the Pi didn't answer in time"
    except OSError as exc:
        return False, f"couldn't run ssh: {exc}"
    if done.returncode != 0:
        if b"Usage:" in done.stdout:   # the Pi answered with the command list: its PiClock predates "piclock mail"
            return False, "the Pi's PiClock is older than this extension; it updates itself within 10 minutes of a push"
        return False, (done.stderr.decode("utf-8", "replace").strip().splitlines() or ["ssh error"])[-1]
    answer = done.stdout.decode("utf-8", "replace").strip() or "ok"
    return answer in ("ok", "removed"), answer


def handle(msg, sender=send, pc=None):
    """The answer to one message from the extension."""
    ident = msg.get("id") if isinstance(msg, dict) else None
    cmd = msg.get("cmd") if isinstance(msg, dict) else None
    if cmd == "ping":
        return {"id": ident, "ok": True, "detail": "ready"}
    if cmd == "send":
        report = msg.get("report")
        if not isinstance(report, dict) or not report.get("account"):
            return {"id": ident, "ok": False, "detail": "the report has no account"}
        report = dict(report)
        report.setdefault("pc", pc if pc is not None else socket.gethostname())
        report.setdefault("seq", time.time())
        ok, detail = sender(report)
        return {"id": ident, "ok": ok, "detail": detail}
    return {"id": ident, "ok": False, "detail": f"unknown command {cmd!r}"}


def read_message(stream):
    head = stream.read(4)
    if len(head) < 4:
        return None
    (length,) = struct.unpack("<I", head)
    if length > 4_000_000:
        return None
    body = stream.read(length)
    if len(body) < length:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        return {"cmd": "bad"}


def write_message(stream, msg):
    body = json.dumps(msg).encode("utf-8")
    stream.write(struct.pack("<I", len(body)) + body)
    stream.flush()


def main():
    if sys.platform == "win32":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    log("started")
    while True:
        msg = read_message(stdin)
        if msg is None:
            break
        answer = handle(msg)
        kind = msg.get("cmd") if isinstance(msg, dict) else "?"
        what = (msg.get("report") or {}).get("account", "") if kind == "send" and isinstance(msg, dict) else ""
        log(f"{kind:<5} {what:<30} {'ok' if answer['ok'] else 'failed'}: {answer['detail']}")
        write_message(stdout, answer)
    log("stopped")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"error: {exc.__class__.__name__}: {exc}")
