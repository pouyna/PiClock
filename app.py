#!/usr/bin/env python3
"""PiClock server.

Runs on the Raspberry Pi. Every few minutes it checks the weather, your
mailboxes (read-only, nothing is marked as read) and your calendars, then
serves everything to the full-screen clock page at http://127.0.0.1:8080.
"""

import configparser
import email
import imaplib
import logging
import os
import re
import ssl
import subprocess
import sys
import threading
import time
from datetime import datetime, time as dtime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

import icalendar
import recurring_ical_events
import requests
from flask import Flask, jsonify, request, send_from_directory

import browser_mail
import claude_status
import settings
import vault

BASE = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("PICLOCK_CONFIG", BASE / "config.ini"))  # tests point this elsewhere
CLAUDE_DIR = Path(os.environ.get("PICLOCK_CLAUDE_DIR", BASE / "claude"))   # session reports from the PC
BROWSER_MAIL_DIR = Path(os.environ.get("PICLOCK_MAIL_DIR", BASE / "mail"))   # mailboxes read by the PC's browser extension
BOOT_ID = str(int(time.time()))  # the page reloads itself when this changes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("piclock")

def load_config(path):
    """Read config.ini, or exit with a message that says how to fix it."""
    # Only "=" separates settings, so a stray link like https://... is reported instead of
    # being read as a setting called "https". Interpolation is off because passwords may contain %.
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    try:
        if not parser.read(path, encoding="utf-8-sig"):  # utf-8-sig: tolerate Windows Notepad files
            raise SystemExit(f"{path} not found. Copy config.example.ini to config.ini and edit it.")
    except configparser.Error as exc:
        raise SystemExit(f"config.ini has a mistake. {exc}\n"
                         "Tip: each calendar link goes on its own indented line under 'urls ='.")
    return parser


cfg = load_config(CONFIG_PATH)


def refresh_seconds(section, default_minutes):
    try:
        minutes = cfg.getfloat(section, "refresh_minutes", fallback=default_minutes)
    except ValueError:
        minutes = default_minutes
    return max(1.0, minutes) * 60


def cfg_int(section, key, default):
    """A whole-number setting that can't take the service down: a stray value falls back to the default."""
    try:
        return int(float(cfg.get(section, key, fallback=default)))
    except (ValueError, TypeError):
        return default


def clean(text):
    return " ".join(str(text or "").split())


def age_text(seconds):
    """'3 min', '2 h', '1 day': how long ago, in the clock's plain words."""
    m = int(seconds // 60)
    if m < 2:
        return "a minute"
    if m < 60:
        return f"{m} min"
    h = m // 60
    if h < 24:
        return f"{h} h"
    d = h // 24
    return "a day" if d == 1 else f"{d} days"


def retry_delay(interval, failures):
    """After a failure, try again soon (a minute, then doubling up to the normal interval).
    The first weather check often runs before the Pi's network is fully up."""
    if not failures:
        return interval
    return min(interval, 60 * 2 ** (failures - 1))


# --------------------------------------------------------------------------- #
# Background polling
# --------------------------------------------------------------------------- #
class Poller:
    """Runs `func` every `interval` seconds in a thread and keeps the last result."""

    def __init__(self, name, func, interval):
        self.name, self.func, self.interval = name, func, interval
        self.data, self.error, self.updated = None, None, None
        self.lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name=name).start()

    def _loop(self):
        failures = 0
        while True:
            try:
                result = self.func()
                with self.lock:
                    self.data, self.error, self.updated = result, None, time.time()
                failures = 0
                log.info("%s updated", self.name)
            except Exception as exc:  # keep the last good data, report the problem
                failures += 1
                if isinstance(exc, RuntimeError):   # an expected failure with a friendly message: no traceback
                    log.warning("%s update failed: %s", self.name, exc)
                else:
                    log.exception("%s update failed", self.name)
                with self.lock:
                    self.error = str(exc) or exc.__class__.__name__
            time.sleep(retry_delay(self.interval, failures))

    def snapshot(self):
        with self.lock:
            return {"enabled": True, "data": self.data, "error": self.error, "updated": self.updated}


POLLERS = {}


def snapshot(name):
    poller = POLLERS.get(name)
    return poller.snapshot() if poller else {"enabled": False}


# --------------------------------------------------------------------------- #
# Weather (Open-Meteo, no API key needed)
# --------------------------------------------------------------------------- #
def weather_configured():
    return bool(cfg.get("weather", "latitude", fallback="").strip()
                and cfg.get("weather", "longitude", fallback="").strip())


def fetch_weather():
    sec = cfg["weather"]
    metric = sec.get("units", "fahrenheit").strip().lower().startswith("c")
    params = {
        "latitude": sec["latitude"].strip(),
        "longitude": sec["longitude"].strip(),
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                   "weather_code,wind_speed_10m,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max,sunrise,sunset",
        "temperature_unit": "celsius" if metric else "fahrenheit",
        "wind_speed_unit": "kmh" if metric else "mph",
        "timezone": "auto",
        "forecast_days": 5,
    }
    try:
        resp = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=15)
        resp.raise_for_status()
        j = resp.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code
        if status >= 500:   # their problem, not a settings problem
            raise RuntimeError(f"The weather service is having trouble (error {status}). Trying again soon.") from None
        raise RuntimeError(f"The weather service rejected the request (error {status}). "
                           "Check latitude and longitude in config.ini.") from None
    except requests.RequestException:
        raise RuntimeError("Couldn't reach the weather service. Check your internet connection.") from None
    cur, daily = j["current"], j["daily"]

    def rnd(v):
        return None if v is None else round(v)

    days = [
        {
            "date": d,
            "code": daily["weather_code"][i],
            "high": rnd(daily["temperature_2m_max"][i]),
            "low": rnd(daily["temperature_2m_min"][i]),
            "precip": daily["precipitation_probability_max"][i],
        }
        for i, d in enumerate(daily["time"])
    ]
    return {
        "location": sec.get("location_name", "").strip(),
        "wind_unit": "km/h" if metric else "mph",
        "temp": rnd(cur["temperature_2m"]),
        "feels": rnd(cur["apparent_temperature"]),
        "humidity": cur.get("relative_humidity_2m"),
        "wind": rnd(cur["wind_speed_10m"]),
        "code": cur["weather_code"],
        "is_day": bool(cur["is_day"]),
        "sunrise": daily["sunrise"][0],  # local time, e.g. 2026-10-21T07:08
        "sunset": daily["sunset"][0],
        "days": days,
    }


# --------------------------------------------------------------------------- #
# Email (IMAP, read-only)
# --------------------------------------------------------------------------- #
MICROSOFT_HOST = re.compile(r"(^|\.)(office365|outlook|live|hotmail)\.com$", re.I)


class MailError(Exception):
    pass


def mailbox_sections():
    return [s for s in cfg.sections() if s.lower().startswith("email:")]


def header_text(raw):
    if not raw:
        return ""
    try:
        return clean(make_header(decode_header(raw)))
    except Exception:
        return clean(raw)


def quote_folder(name):
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def check_mailbox(label, sec, show_latest):
    host = sec.get("host", "").strip()
    if not host or not sec.get("username") or not sec.get("password"):
        raise MailError("Missing host, username or password in config.ini.")
    folder = sec.get("folder", "INBOX").strip() or "INBOX"
    try:
        password = vault.decrypt(sec.get("password", "")).strip()
    except vault.VaultError:
        raise MailError("The saved password can't be read any more (the key file changed). Enter it again in Settings.")
    try:
        port = int(float(sec.get("port", "993").strip() or 993))
    except ValueError:
        raise MailError("The port must be a number, usually 993.")
    try:
        # The default context verifies the server's certificate, so the password only ever goes to the real server.
        imap = imaplib.IMAP4_SSL(host, port, timeout=30, ssl_context=ssl.create_default_context())
    except ssl.SSLError:
        raise MailError(f"{host} didn't present a valid certificate, so the password wasn't sent. Check the server name.")
    except OSError:
        raise MailError(f"Can't reach {host}. Check the server name and your internet connection.")

    with imap:
        try:
            imap.login(sec["username"].strip(), password)
        except imaplib.IMAP4.error:
            if MICROSOFT_HOST.search(host):
                raise MailError("Microsoft mail no longer accepts password sign-in. Use the browser extension on the PC instead (README, Mail from the browser).")
            raise MailError("Sign-in failed. Check the username and app password in Settings.")

        typ, _ = imap.select(quote_folder(folder), readonly=True)  # readonly: never marks mail as read
        if typ != "OK":
            raise MailError(f"Folder “{folder}” wasn't found on the server.")

        typ, data = imap.uid("SEARCH", None, "UNSEEN")
        uids = data[0].split() if typ == "OK" and data and data[0] else []

        messages = []
        for uid in reversed(uids[-show_latest:]):  # highest UID = newest
            typ, parts = imap.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if typ != "OK":
                continue
            for part in parts:
                if not isinstance(part, tuple):
                    continue
                msg = email.message_from_bytes(part[1])
                name, addr = parseaddr(header_text(msg.get("From")))
                try:
                    ts = parsedate_to_datetime(msg.get("Date")).timestamp()
                except Exception:
                    ts = None
                messages.append({
                    "id": f"{label}:{uid.decode()}",
                    "mailbox": label,
                    "from": name or addr or "Unknown sender",
                    "subject": header_text(msg.get("Subject")) or "(no subject)",
                    "ts": ts,
                })
                break
    return len(uids), messages


def fetch_mail():
    show_latest = max(1, cfg_int("email", "show_latest", 4))
    keep = max(12, show_latest * 3)   # the screen shows show_latest; a tap on the mail panel shows the rest
    boxes, messages, total = [], [], 0
    for section in mailbox_sections():
        label = section.split(":", 1)[1].strip() or "Mail"
        try:
            count, msgs = check_mailbox(label, cfg[section], keep)
            boxes.append({"name": label, "unread": count, "error": None})
            total += count
            messages.extend(msgs)
        except MailError as exc:
            log.warning("Mailbox %s: %s", label, exc)
            boxes.append({"name": label, "unread": None, "error": str(exc)})
        except Exception as exc:
            log.exception("Mailbox %s failed", label)
            boxes.append({"name": label, "unread": None, "error": f"Couldn't check mail ({exc.__class__.__name__})."})
    messages.sort(key=lambda m: m["ts"] or 0, reverse=True)
    return {"total": total, "boxes": boxes, "messages": messages[:keep], "show": show_latest}


# --------------------------------------------------------------------------- #
# Calendar (any iCal / .ics link: Google, iCloud, Outlook, Proton, Fastmail...)
# --------------------------------------------------------------------------- #
def calendar_urls():
    """The calendar links, decrypted. A link that can't be decrypted any more is None."""
    raw = cfg.get("calendar", "urls", fallback="")
    urls = []
    for u in raw.splitlines():
        u = u.strip()
        if not u or u.startswith("#"):
            continue
        try:
            u = vault.decrypt(u)
        except vault.VaultError:
            urls.append(None)
            continue
        urls.append(("https://" + u[len("webcal://"):]) if u.lower().startswith("webcal://") else u)
    return urls


def event_to_dict(ev, cal_name, now):
    if str(ev.get("STATUS", "")).upper() == "CANCELLED" or ev.get("DTSTART") is None:
        return None
    start = ev.get("DTSTART").dt
    all_day = not isinstance(start, datetime)  # a plain date means an all-day event

    if ev.get("DTEND") is not None:
        end = ev.get("DTEND").dt
    elif ev.get("DURATION") is not None:
        end = start + ev.get("DURATION").dt
    else:
        end = start + timedelta(days=1) if all_day else start

    if all_day:
        if isinstance(end, datetime):
            end = end.date()
        if end <= now.date():
            return None
        start_local = datetime.combine(start, dtime()).astimezone()
        end_local = datetime.combine(end, dtime()).astimezone()
    else:
        if not isinstance(end, datetime):
            end = datetime.combine(end, dtime())
        start_local = start.astimezone()  # floating times are treated as local
        end_local = end.astimezone()
        if end_local <= now:
            return None

    return {
        "title": clean(ev.get("SUMMARY")) or "(untitled)",
        "location": clean(ev.get("LOCATION")),
        "calendar": cal_name,
        "all_day": all_day,
        "start": start_local.timestamp(),
        "end": end_local.timestamp(),
    }


def fetch_calendar():
    days_ahead = max(1, cfg_int("calendar", "days_ahead", 7))
    now = datetime.now().astimezone()
    window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(days=days_ahead)

    events, errors = [], []
    for n, url in enumerate(calendar_urls(), 1):
        # Error messages never include the URL: private calendar links work like passwords.
        if url is None:
            errors.append(f"Calendar {n}: the saved link can't be read any more (the key file changed). Add it again in Settings.")
            continue
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            cal = icalendar.Calendar.from_ical(resp.content)
        except requests.HTTPError as exc:
            errors.append(f"Calendar {n}: the link returned error {exc.response.status_code}. Check it in config.ini.")
            continue
        except requests.RequestException:
            errors.append(f"Calendar {n}: couldn't download it. Check your internet connection.")
            continue
        except ValueError:
            errors.append(f"Calendar {n}: that link isn't an iCal (.ics) calendar feed.")
            continue

        cal_name = clean(cal.get("X-WR-CALNAME")) or f"Calendar {n}"
        try:
            occurrences = recurring_ical_events.of(cal).between(window_start, window_end)
        except Exception:
            log.exception("Calendar %s could not be expanded", n)
            errors.append(f"Calendar {n}: couldn't read its events.")
            continue
        for ev in occurrences:
            item = event_to_dict(ev, cal_name, now)
            if item:
                events.append(item)

    events.sort(key=lambda e: (e["start"], not e["all_day"]))
    return {"events": events[:40], "errors": errors, "days": days_ahead}


# --------------------------------------------------------------------------- #
# Claude sessions (reported by the PC through `piclock claude`) and the screen
# --------------------------------------------------------------------------- #
def mail_snapshot():
    """The mailboxes the clock checks itself (IMAP) plus the ones the PC's browser extension reports, as one panel."""
    base = snapshot("mail")
    now = time.time()
    extra = browser_mail.mailboxes(BROWSER_MAIL_DIR, now)
    if not extra:
        return base
    show_latest = max(1, cfg_int("email", "show_latest", 4))
    data = base.get("data") if base.get("enabled") else None
    boxes = list(data["boxes"]) if data else []
    messages = list(data["messages"]) if data else []
    total = data["total"] if data else 0
    if base.get("enabled") and not data and base.get("error"):
        boxes.append({"name": "Mail", "unread": None, "error": base["error"]})
    for b in extra:
        note = None
        if b["needs_login"]:
            note = f"{b['provider']} is asking for a sign-in on the PC."
        elif now - b["updated"] > browser_mail.STALE_SECONDS:
            note = f"Last update from the PC {age_text(now - b['updated'])} ago."
        if b["unread"] is None:   # nothing read for a day: the mail is probably read by now
            boxes.append({"name": b["label"], "unread": None, "error": None, "note": note or "No update from the PC for a day."})
            continue
        boxes.append({"name": b["label"], "unread": b["unread"], "error": None, "note": note})
        total += b["unread"]
        for i, m in enumerate(b["messages"]):
            messages.append({"id": f"{b['label']}:{m['id']}", "mailbox": b["label"], "from": m["from"], "subject": m["subject"],
                             "ts": m["ts"], "when": m.get("when") or "", "_order": b["read_at"] - i})
    messages.sort(key=lambda m: m["ts"] or m.get("_order") or 0, reverse=True)
    keep = max(12, show_latest * 3)
    for m in messages:
        m.pop("_order", None)
    return {"enabled": True, "data": {"total": total, "boxes": boxes, "messages": messages[:keep], "show": show_latest},
            "error": None, "updated": max([base.get("updated") or 0] + [b["updated"] for b in extra])}


def claude_snapshot():
    if not cfg.getboolean("claude", "show", fallback=True):
        return {"enabled": False}
    sessions = claude_status.sessions(CLAUDE_DIR)
    return {"enabled": True, "data": {"sessions": sessions, "light": claude_status.light(sessions)},
            "error": None, "updated": time.time()}


SCREEN = {"hidden_until": None, "timer": None}
SCREEN_LOCK = threading.Lock()
HIDDEN_FILE = BASE / ".screen-hidden"   # remembers a hidden clock across a restart; 0 = hidden until the owner opens it


def hide_minutes():
    return max(0, cfg_int("display", "hide_minutes", 30))


def control(command):
    """Run `piclock show` or `piclock hide`; the control script knows how to reach the Pi's screen."""
    subprocess.run(["/bin/bash", str(BASE / "piclock"), command], timeout=60,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _arm_return(seconds):
    """Schedule the clock's return. Call with SCREEN_LOCK held."""
    if SCREEN["timer"]:
        SCREEN["timer"].cancel()
    SCREEN["timer"] = None
    SCREEN["hidden_until"] = time.time() + seconds if seconds else None
    if seconds:
        SCREEN["timer"] = threading.Timer(seconds, show_screen)
        SCREEN["timer"].daemon = True
        SCREEN["timer"].start()


def hide_screen():
    """Close the clock so the desktop can be used, and bring it back after [display] hide_minutes."""
    minutes = hide_minutes()
    with SCREEN_LOCK:
        _arm_return(minutes * 60)
        try:
            HIDDEN_FILE.write_text(str(SCREEN["hidden_until"] or 0), encoding="utf-8")
        except OSError as exc:
            log.warning("Couldn't remember the hidden screen: %s", exc)
    control("hide")


def show_screen():
    with SCREEN_LOCK:
        if SCREEN["timer"]:
            SCREEN["timer"].cancel()
        SCREEN["timer"], SCREEN["hidden_until"] = None, None
        try:
            HIDDEN_FILE.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("Couldn't clear the hidden-screen note: %s", exc)
    control("show")


def restore_screen():
    """After a restart while the clock was hidden: finish the wait, or bring the clock back now."""
    try:
        until = float(HIDDEN_FILE.read_text(encoding="utf-8").strip() or 0)
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        until = 0
    if until == 0:
        return   # hidden until the owner opens PiClock from the desktop
    remaining = until - time.time()
    if remaining > 1:
        with SCREEN_LOCK:
            _arm_return(remaining)
        log.info("The clock is hidden; it comes back in %d min", remaining // 60)
    else:
        threading.Thread(target=show_screen, daemon=True).start()


def ensure_launchers():
    """A PiClock icon on the desktop and in the menu, so a hidden clock can be brought back by touch."""
    if os.name == "nt" or os.geteuid() == 0 or not cfg.getboolean("display", "desktop_icon", fallback=True):
        return
    entry = ("[Desktop Entry]\nType=Application\nName=PiClock\nComment=Show the clock full screen\n"
             f"Exec=/bin/bash {BASE}/kiosk.sh\nIcon={BASE}/static/icon.svg\nTerminal=false\nCategories=Utility;\n")
    home = Path.home()
    for path in (home / "Desktop" / "PiClock.desktop", home / ".local" / "share" / "applications" / "piclock.desktop"):
        try:
            if path.name == "PiClock.desktop" and not path.parent.is_dir():
                continue   # no desktop folder to put it on
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists() or path.read_text(encoding="utf-8") != entry:
                path.write_text(entry, encoding="utf-8")
                path.chmod(0o755)
        except OSError as exc:
            log.warning("Couldn't write %s: %s", path, exc)


# --------------------------------------------------------------------------- #
# Web server
# --------------------------------------------------------------------------- #
def display_settings():
    return {
        "clock_24h": cfg.getboolean("display", "clock_24h", fallback=False),
        "show_seconds": cfg.getboolean("display", "show_seconds", fallback=False),
        "dim_start": cfg.get("display", "dim_start", fallback="").strip(),
        "dim_end": cfg.get("display", "dim_end", fallback="").strip(),
        "dim_amount": min(0.95, max(0.0, cfg.getfloat("display", "dim_amount", fallback=0.6))),
        "chime": cfg.getboolean("display", "chime", fallback=True),
        "hide_minutes": hide_minutes(),
    }


app = Flask(__name__, static_folder=str(BASE / "static"), static_url_path="/static")


@app.get("/")
def index():
    resp = send_from_directory(BASE / "static", "index.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/all")
def all_data():
    resp = jsonify({
        "boot_id": BOOT_ID,
        "config": display_settings(),
        "weather": snapshot("weather"),
        "mail": mail_snapshot(),
        "calendar": snapshot("calendar"),
        "claude": claude_snapshot(),
        "screen": {"hidden_until": SCREEN["hidden_until"]},
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp


def local_only():
    """Settings and the hide button are for the Pi's own screen, even when host = 0.0.0.0."""
    return request.remote_addr in ("127.0.0.1", "::1")


@app.post("/api/screen")
def screen():
    """The hide button on the touchscreen."""
    if not local_only():
        return jsonify({"error": "Only the Pi's own screen can hide or show the clock."}), 403
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Expected JSON."}), 400
    action = body.get("action")
    if action not in ("hide", "show"):
        return jsonify({"error": "action must be hide or show"}), 400
    # Answer first, then act: hiding closes the browser that asked.
    threading.Timer(0.5, hide_screen if action == "hide" else show_screen).start()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Settings page
# --------------------------------------------------------------------------- #
def restart_self():
    """Start this program afresh so new settings take effect. systemd keeps seeing the same service."""
    log.info("Restarting to apply the new settings")
    os.execv(sys.executable, [sys.executable] + sys.argv)


@app.get("/settings")
def settings_page():
    if not local_only():
        return "Settings can only be opened on the Pi's own screen.", 403
    resp = send_from_directory(BASE / "static", "settings.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/settings")
def settings_get():
    if not local_only():
        return jsonify({"error": "Settings can only be read on the Pi's own screen."}), 403
    return jsonify(settings.describe(CONFIG_PATH))


@app.post("/api/settings")
def settings_post():
    if not local_only():
        return jsonify({"error": "Settings can only be changed on the Pi's own screen."}), 403
    form = request.get_json(silent=True)
    if not isinstance(form, dict):
        return jsonify({"error": "Expected JSON."}), 400
    try:
        settings.apply(CONFIG_PATH, form)
    except settings.SettingsError as exc:
        return jsonify({"error": str(exc)}), 400
    saved = settings.parser(CONFIG_PATH)
    try:
        port = int(saved.get("server", "port", fallback="8080"))
    except ValueError:
        port = 8080
    log.info("Settings saved from the screen")
    threading.Timer(0.7, restart_self).start()   # answer first, then start afresh with the new file
    return jsonify({"ok": True, "restarting": True, "port": port})


@app.post("/api/settings/check")
def settings_check():
    """Try signing in to a mailbox with the details on the screen, before they are saved."""
    if not local_only():
        return jsonify({"error": "Settings can only be checked on the Pi's own screen."}), 403
    box = request.get_json(silent=True)
    if not isinstance(box, dict):
        return jsonify({"error": "Expected JSON."}), 400
    fields = {k: str(box.get(k) or "").strip() for k in ("host", "port", "username", "password", "folder")}
    if not fields["password"] and box.get("was"):
        # An unchanged password may be reused, but only for the server and username it was saved for,
        # so the stored password can't be sent to a server typed on the screen.
        stored = settings.mailbox_sections(cfg).get(settings._norm(box["was"]))
        same = (stored and cfg.get(stored, "host", fallback="").strip().lower() == fields["host"].lower()
                and cfg.get(stored, "username", fallback="").strip() == fields["username"])
        if not same:
            return jsonify({"ok": False, "error": "Enter the app password to check a different server or username."})
        fields["password"] = cfg.get(stored, "password", fallback="")
    trial = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    trial["email:check"] = {k: v for k, v in fields.items() if v}
    try:
        unread, _ = check_mailbox(str(box.get("name") or "Mail"), trial["email:check"], 1)
    except MailError as exc:
        return jsonify({"ok": False, "error": str(exc)})
    except Exception as exc:
        log.exception("Mailbox check failed")
        return jsonify({"ok": False, "error": f"Couldn't check the mailbox ({exc.__class__.__name__})."})
    return jsonify({"ok": True, "unread": unread})


def start_pollers():
    if weather_configured():
        POLLERS["weather"] = Poller("weather", fetch_weather, refresh_seconds("weather", 15))
    if mailbox_sections():
        POLLERS["mail"] = Poller("mail", fetch_mail, refresh_seconds("email", 2))
    if calendar_urls():
        POLLERS["calendar"] = Poller("calendar", fetch_calendar, refresh_seconds("calendar", 10))
    log.info("Enabled: %s", ", ".join(POLLERS) or "clock only")


if __name__ == "__main__":
    try:
        changed = settings.encrypt_in_place(CONFIG_PATH)   # passwords typed into the file by hand
        if changed:
            log.info("Encrypted %d secret(s) found in config.ini", changed)
            cfg = load_config(CONFIG_PATH)
    except settings.SettingsError as exc:
        log.warning("Left the secrets in config.ini as they are: %s", exc)
    except Exception:
        log.exception("Couldn't encrypt the secrets in config.ini")
    ensure_launchers()
    restore_screen()
    start_pollers()
    host = cfg.get("server", "host", fallback="127.0.0.1").strip() or "127.0.0.1"
    port = cfg_int("server", "port", 8080)
    log.info("PiClock running at http://%s:%s", host, port)
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=4)
    except ImportError:
        app.run(host=host, port=port, threaded=True)
