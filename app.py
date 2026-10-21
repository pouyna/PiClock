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
import threading
import time
from datetime import datetime, time as dtime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

import icalendar
import recurring_ical_events
import requests
from flask import Flask, jsonify, send_from_directory

BASE = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("PICLOCK_CONFIG", BASE / "config.ini"))  # tests point this elsewhere
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
    return max(1.0, cfg.getfloat(section, "refresh_minutes", fallback=default_minutes)) * 60


def clean(text):
    return " ".join(str(text or "").split())


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
        while True:
            try:
                result = self.func()
                with self.lock:
                    self.data, self.error, self.updated = result, None, time.time()
                log.info("%s updated", self.name)
            except Exception as exc:  # keep the last good data, report the problem
                log.exception("%s update failed", self.name)
                with self.lock:
                    self.error = str(exc) or exc.__class__.__name__
            time.sleep(self.interval)

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
        raise RuntimeError(f"The weather service returned error {exc.response.status_code}. "
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
        imap = imaplib.IMAP4_SSL(host, sec.getint("port", fallback=993), timeout=30)
    except OSError:
        raise MailError(f"Can't reach {host}. Check the server name and your internet connection.")

    with imap:
        try:
            imap.login(sec["username"].strip(), sec["password"].strip())
        except imaplib.IMAP4.error:
            raise MailError("Sign-in failed. Check the username and app password in config.ini.")

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
    show_latest = max(1, cfg.getint("email", "show_latest", fallback=4))
    boxes, messages, total = [], [], 0
    for section in mailbox_sections():
        label = section.split(":", 1)[1].strip() or "Mail"
        try:
            count, msgs = check_mailbox(label, cfg[section], show_latest)
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
    return {"total": total, "boxes": boxes, "messages": messages[:show_latest]}


# --------------------------------------------------------------------------- #
# Calendar (any iCal / .ics link: Google, iCloud, Outlook, Proton, Fastmail...)
# --------------------------------------------------------------------------- #
def calendar_urls():
    raw = cfg.get("calendar", "urls", fallback="")
    urls = [u.strip() for u in raw.splitlines() if u.strip() and not u.strip().startswith("#")]
    return [("https://" + u[len("webcal://"):]) if u.lower().startswith("webcal://") else u for u in urls]


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
    days_ahead = max(1, cfg.getint("calendar", "days_ahead", fallback=7))
    now = datetime.now().astimezone()
    window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(days=days_ahead)

    events, errors = [], []
    for n, url in enumerate(calendar_urls(), 1):
        # Error messages never include the URL: private calendar links work like passwords.
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
        "mail": snapshot("mail"),
        "calendar": snapshot("calendar"),
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp


def start_pollers():
    if weather_configured():
        POLLERS["weather"] = Poller("weather", fetch_weather, refresh_seconds("weather", 15))
    if mailbox_sections():
        POLLERS["mail"] = Poller("mail", fetch_mail, refresh_seconds("email", 2))
    if calendar_urls():
        POLLERS["calendar"] = Poller("calendar", fetch_calendar, refresh_seconds("calendar", 10))
    log.info("Enabled: %s", ", ".join(POLLERS) or "clock only")


if __name__ == "__main__":
    start_pollers()
    host = cfg.get("server", "host", fallback="127.0.0.1")
    port = cfg.getint("server", "port", fallback=8080)
    log.info("PiClock running at http://%s:%s", host, port)
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=4)
    except ImportError:
        app.run(host=host, port=port, threaded=True)
