#!/usr/bin/env python3
"""One-time setup for the PiClock mail extension on this PC. Run from the project folder:

    py pc\\setup_extension.py            set it up (safe to run again)
    py pc\\setup_extension.py --remove   undo it

It registers pc/mail_host.bat with Chrome and Edge as the extension's helper (a small
JSON file in %LOCALAPPDATA%\\PiClock and a key in the current user's registry; no admin
rights), checks that the helper answers and that the Pi can be reached, and prints the
two clicks left to do in Chrome: Developer mode, Load unpacked, this folder's pc\\extension.
"""
import base64
import hashlib
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXT_DIR = HERE / "extension"
HOST_NAME = "com.piclock.mail"
STATE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PiClock"
MANIFEST = STATE_DIR / f"{HOST_NAME}.json"
BROWSERS = {
    "Chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "Edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
    "Chromium": r"Software\Chromium\NativeMessagingHosts",
}


def extension_id():
    """Chrome derives the ID from the key in manifest.json, so it is the same on every PC."""
    key = json.loads((EXT_DIR / "manifest.json").read_text(encoding="utf-8"))["key"]
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def registry(action):
    import winreg
    for browser, path in BROWSERS.items():
        full = path + "\\" + HOST_NAME
        if action == "set":
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, full) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(MANIFEST))
        else:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, full)
            except FileNotFoundError:
                pass


def helper_answers():
    """Start the helper the way Chrome does and send it a ping."""
    ping = json.dumps({"id": 1, "cmd": "ping"}).encode()
    try:
        done = subprocess.run([str(HERE / "mail_host.bat")], input=struct.pack("<I", len(ping)) + ping,
                              capture_output=True, timeout=30, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    out = done.stdout
    if len(out) < 4:
        return False, done.stderr.decode("utf-8", "replace").strip() or "no answer"
    (n,) = struct.unpack("<I", out[:4])
    try:
        answer = json.loads(out[4:4 + n].decode("utf-8"))
    except ValueError:
        return False, "garbled answer"
    return bool(answer.get("ok")), answer.get("detail", "")


def pi_answers():
    """Send the Pi a harmless report (forget a mailbox that doesn't exist)."""
    sys.path.insert(0, str(HERE))
    import mail_host
    ok, detail = mail_host.send({"account": "setup-check", "state": "removed", "seq": 0})
    return ok, detail


def main():
    if sys.platform != "win32":
        sys.exit("This setup is for the Windows PC that holds the browser.")
    if "--remove" in sys.argv:
        registry("delete")
        try:
            MANIFEST.unlink()
        except FileNotFoundError:
            pass
        print("Removed the helper registration. Remove the extension itself at chrome://extensions.")
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ext_id = extension_id()
    MANIFEST.write_text(json.dumps({
        "name": HOST_NAME,
        "description": "PiClock mail: sends what the extension reads to the Pi over SSH",
        "path": str(HERE / "mail_host.bat"),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{ext_id}/"],
    }, indent=2), encoding="utf-8")
    registry("set")
    print(f"Registered the helper for {', '.join(BROWSERS)} (current user only).")
    ok, detail = helper_answers()
    print(f"Helper answers: {'yes' if ok else 'NO'} ({detail})")
    ok, detail = pi_answers()
    print(f"Pi reachable:   {'yes' if ok else 'NO'} ({detail})")
    print()
    print("Now in Chrome (or Edge):")
    print("  1. Open chrome://extensions and turn on Developer mode (top right).")
    print("  2. Click Load unpacked and choose this folder:")
    print(f"       {EXT_DIR}")
    print(f"     The extension's ID should read {ext_id}.")
    print("  3. Open your webmail in a tab and keep it open. The clock shows the mailbox within a minute;")
    print("     the extension's icon shows what it read and whether the Pi answered.")


if __name__ == "__main__":
    main()
