"""Preview the clock on any computer, with sample weather, mail and calendar data.

    python dev/mock_server.py            then open http://127.0.0.1:8099
    http://127.0.0.1:8099/new-mail       simulates a new email (the page shows the alert)
    http://127.0.0.1:8099/reset          back to the starting sample data

Options: --port N, --24h, --empty (shows the "not set up" states), --errors, --no-claude, --light red
"""
import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import settings   # noqa: E402  (the real reader, pointed at config.example.ini)
app = Flask(__name__, static_folder=str(ROOT / "static"), static_url_path="/static")
STATE = {"extra_mail": 0}
OPTS = argparse.Namespace(h24=False, empty=False, errors=False, no_claude=False, light="")


def at(days, hour, minute=0):
    d = datetime.now().astimezone() + timedelta(days=days)
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()


def midnight(days):
    return at(days, 0)


def sample_events():
    now = datetime.now()
    # Keep "today" interesting whatever time you preview: one event now, two coming up.
    return [
        {"title": "Yoga", "location": "", "calendar": "Home", "all_day": False,
         "start": now.timestamp() - 20 * 60, "end": now.timestamp() + 25 * 60},
        {"title": "Call with Mom", "location": "", "calendar": "Home", "all_day": False,
         "start": now.timestamp() + 38 * 60, "end": now.timestamp() + 68 * 60},
        {"title": "Book club", "location": "Lou's place", "calendar": "Home", "all_day": False,
         "start": now.timestamp() + 98 * 60, "end": now.timestamp() + 188 * 60},
        {"title": "Trash day", "location": "", "calendar": "Home", "all_day": True,
         "start": midnight(1), "end": midnight(2)},
        {"title": "Team standup", "location": "", "calendar": "Work", "all_day": False,
         "start": at(1, 9), "end": at(1, 9, 15)},
        {"title": "Lunch with Priya", "location": "Avec", "calendar": "Home", "all_day": False,
         "start": at(1, 12, 30), "end": at(1, 13, 30)},
        {"title": "Dentist", "location": "", "calendar": "Home", "all_day": False,
         "start": at(2, 8, 30), "end": at(2, 9, 30)},
        {"title": "Concert", "location": "Thalia Hall", "calendar": "Home", "all_day": False,
         "start": at(2, 20), "end": at(2, 23)},
    ]


def sample_mail():
    t = time.time()
    msgs = [
        {"id": "P:2", "mailbox": "Personal", "from": "Priya Raman", "subject": "Re: Saturday hike, still on?", "ts": t - 25 * 60},
        {"id": "P:1", "mailbox": "Personal", "from": "Chicago Public Library", "subject": "Your hold is ready for pickup", "ts": t - 3 * 3600},
        {"id": "W:9", "mailbox": "Work", "from": "Dana Whitfield", "subject": "Q4 planning doc: comments by Friday please", "ts": t - 26 * 3600},
        {"id": "P:0", "mailbox": "Personal", "from": "Ravi", "subject": "Photos from the lake", "ts": t - 30 * 3600},
        {"id": "W:8", "mailbox": "Work", "from": "Facilities", "subject": "Parking garage closed Monday", "ts": t - 2 * 86400},
        {"id": "P:-1", "mailbox": "Personal", "from": "Dentist", "subject": "Appointment reminder: Thursday 8:30", "ts": t - 3 * 86400},
        {"id": "P:-2", "mailbox": "Personal", "from": "Nadia", "subject": "Book club pick for November", "ts": t - 4 * 86400},
    ]
    for n in range(STATE["extra_mail"]):
        msgs.insert(0, {"id": f"P:new{n}", "mailbox": "Personal", "from": "Mom",
                        "subject": "Call me when you get a minute", "ts": t - 30})
    personal = 5 + STATE["extra_mail"]
    # The server sends more than the screen shows; a tap on the mail panel shows the rest.
    # A mailbox read by the PC's browser extension: its time is the page's own text, and a note says when the PC last reported.
    msgs.insert(2, {"id": "uic.edu:AAQk1", "mailbox": "uic.edu", "from": "Registrar", "subject": "Spring registration opens Monday", "ts": None, "when": "Yesterday"})
    return {"total": personal + 2 + 1, "messages": msgs[:12], "show": 4,
            "boxes": [{"name": "Personal", "unread": personal, "error": None},
                      {"name": "Work", "unread": 2, "error": None},
                      {"name": "uic.edu", "unread": 1, "error": None, "note": "from the PC 2 h ago."}]}


def sample_claude():
    t = time.time()
    return {"light": OPTS.light or "blue", "sessions": [
        {"id": "a", "project": "PiClock", "state": "working", "pc": "", "since": t - 14 * 60, "updated": t - 20,
         "task": "Add touch gestures and a Claude status strip to the clock"},
        {"id": "b", "project": "splines", "state": "attention", "pc": "", "since": t - 3 * 60, "updated": t - 3 * 60,
         "task": "Rebuild the figures with the new colour map"},
        {"id": "c", "project": "AIforASD", "state": "done", "pc": "", "since": t - 48 * 60, "updated": t - 48 * 60,
         "task": "Summarise the 55 papers in the reading map"},
    ]}


def sample_weather():
    today = datetime.now().date()
    rows = [(2, 58, 44, 10), (61, 54, 41, 70), (3, 49, 38, 20), (0, 55, 40, 0), (80, 60, 47, 40)]
    days = [{"date": (today + timedelta(days=i)).isoformat(), "code": c, "high": hi, "low": lo, "precip": p}
            for i, (c, hi, lo, p) in enumerate(rows)]
    return {"location": "Chicago", "wind_unit": "mph", "temp": 52, "feels": 48, "humidity": 61,
            "wind": 12, "code": 2, "is_day": 7 <= datetime.now().hour < 18,
            "sunrise": f"{today.isoformat()}T07:12", "sunset": f"{today.isoformat()}T18:01", "days": days}


def ok(data):
    return {"enabled": True, "error": None, "updated": time.time(), "data": data}


@app.get("/")
def index():
    return send_from_directory(ROOT / "static", "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/new-mail")
def new_mail():
    STATE["extra_mail"] += 1
    return "Added a new email. The clock page shows it within 30 seconds (or reload it).\n"


@app.get("/reset")
def reset():
    STATE["extra_mail"] = 0
    return "Back to the starting sample data.\n"


@app.get("/settings")
def settings_page():
    return send_from_directory(ROOT / "static", "settings.html")


@app.get("/api/settings")
def settings_get():
    d = settings.describe(ROOT / "config.example.ini")
    d["mailboxes"] = [{"name": "Personal", "host": "imap.gmail.com", "port": "993", "username": "you@gmail.com",
                       "password_set": True, "folder": "INBOX"}]
    d["calendar_links"] = [{"id": 0, "label": "calendar.google.com … basic.ics"}]
    return jsonify(d)


@app.post("/api/settings")
def settings_post():
    print("(preview) settings would be saved:", list((request.get_json(silent=True) or {}).keys()))
    return jsonify({"ok": True, "restarting": False})


@app.post("/api/settings/check")
def settings_check():
    return jsonify({"ok": True, "unread": 3})


PREFS = {"mail_filter": []}


@app.post("/api/prefs")
def prefs():
    PREFS["mail_filter"] = list((request.get_json(silent=True) or {}).get("mail_filter") or [])
    return jsonify({"ok": True, "prefs": PREFS})


@app.post("/api/screen")
def screen():
    action = (request.get_json(silent=True) or {}).get("action")
    print(f"(preview) the screen would now {action}")
    return jsonify({"ok": True})


@app.get("/api/all")
def all_data():
    config = {"clock_24h": OPTS.h24, "show_seconds": False, "dim_start": "", "dim_end": "",
              "dim_amount": 0.6, "chime": True, "hide_minutes": 30}
    claude = {"enabled": False} if OPTS.empty or OPTS.no_claude else ok(sample_claude())
    if OPTS.empty:
        weather = {"enabled": False}; mail = {"enabled": False}; calendar = {"enabled": False}
    elif OPTS.errors:
        weather = {"enabled": True, "data": None, "updated": None,
                   "error": "Couldn't reach the weather service. Check your internet connection."}
        mail = ok({"total": 2, "messages": sample_mail()["messages"][:2],
                   "boxes": [{"name": "Personal", "unread": 2, "error": None},
                             {"name": "Work", "unread": None,
                              "error": "Sign-in failed. Check the username and app password in config.ini."}]})
        calendar = ok({"days": 7, "events": sample_events()[:3],
                       "errors": ["Calendar 2: the link returned error 404. Check it in config.ini."]})
    else:
        weather, mail = ok(sample_weather()), ok(sample_mail())
        calendar = ok({"days": 7, "events": sample_events(), "errors": []})
    return jsonify({"boot_id": "preview", "config": config, "weather": weather, "mail": mail,
                    "calendar": calendar, "claude": claude, "screen": {"hidden_until": None}, "prefs": PREFS})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--24h", dest="h24", action="store_true", help="24-hour clock")
    ap.add_argument("--empty", action="store_true", help="nothing set up yet")
    ap.add_argument("--errors", action="store_true", help="show error states")
    ap.add_argument("--no-claude", dest="no_claude", action="store_true", help="without the Claude light")
    ap.add_argument("--light", choices=["green", "blue", "yellow", "red"], default="", help="colour of the Claude light")
    OPTS = ap.parse_args()
    print(f"Preview: http://127.0.0.1:{OPTS.port}")
    app.run(host="127.0.0.1", port=OPTS.port)
