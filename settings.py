"""Reading and writing config.ini for the on-screen Settings page.

`describe()` turns config.ini into a form the page can draw, without ever including a
secret. `apply()` takes the filled form back, checks it, encrypts the secrets, and
rewrites config.ini while keeping its comments and layout. `encrypt_in_place()` is run
when the clock starts, so a password typed into the file by hand is encrypted too.
"""
import configparser
import math
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import vault

# One row per setting: key, label on screen, type, default, extra options.
# Types: bool, number, decimal, text, time, choice. Secrets are handled separately.
SCHEMA = [
    ("display", "Display", [
        ("clock_24h", "24-hour clock", "bool", "no", {}),
        ("show_seconds", "Show seconds", "bool", "no", {}),
        ("dim_start", "Dim the screen from", "time", "22:00", {"hint": "Leave empty to never dim"}),
        ("dim_end", "Dim until", "time", "07:00", {}),
        ("dim_amount", "How dark", "number", "0.6", {"min": 0, "max": 0.9, "step": 0.1}),
        ("chime", "Chime when mail arrives", "bool", "yes", {}),
        ("hide_minutes", "A hidden clock comes back after", "number", "30", {"min": 0, "max": 720, "step": 5, "unit": "min", "zero": "never"}),
    ]),
    ("weather", "Weather", [
        ("location_name", "Place name", "text", "", {}),
        ("latitude", "Latitude", "decimal", "", {"hint": "Right-click your home in Google Maps to copy both"}),
        ("longitude", "Longitude", "decimal", "", {}),
        ("units", "Units", "choice", "fahrenheit", {"choices": ["fahrenheit", "celsius"]}),
        ("refresh_minutes", "Check every", "number", "15", {"min": 5, "max": 180, "step": 5, "unit": "min"}),
    ]),
    ("calendar", "Calendar", [
        ("days_ahead", "Days ahead", "number", "7", {"min": 1, "max": 31, "step": 1, "unit": "days"}),
        ("refresh_minutes", "Check every", "number", "10", {"min": 2, "max": 180, "step": 1, "unit": "min"}),
    ]),
    ("email", "Mail", [
        ("refresh_minutes", "Check every", "number", "2", {"min": 1, "max": 60, "step": 1, "unit": "min"}),
        ("show_latest", "Messages shown", "number", "4", {"min": 1, "max": 12, "step": 1}),
    ]),
    ("claude", "Claude", [
        ("show", "Show the Claude light", "bool", "yes",
         {"hint": "Green: nothing running. Blue: working. Yellow: done or needs you. Red: session limit."}),
    ]),
    ("updates", "Updates", [
        ("auto_update", "Install changes from GitHub by themselves", "bool", "yes", {}),
    ]),
    ("server", "Advanced", [
        ("host", "Reachable from", "choice", "127.0.0.1", {"choices": ["127.0.0.1", "0.0.0.0"],
                                                           "labels": ["this Pi only", "the whole network"],
                                                           "hint": "On the whole network, anyone nearby can read your mail subjects and calendar"}),
        ("port", "Port", "number", "8080", {"min": 1024, "max": 65535, "step": 1, "hint": "The screen follows the new port when you save"}),
    ]),
]
MAILBOX_FIELDS = [("host", "Server", "text"), ("port", "Port", "number"), ("username", "Username", "text"),
                  ("password", "App password", "secret"), ("folder", "Folder", "text")]
PROVIDERS = [
    {"name": "Gmail", "host": "imap.gmail.com", "port": 993,
     "hint": "Turn on 2-Step Verification, then make an app password at myaccount.google.com/apppasswords. Type it without spaces."},
    {"name": "iCloud", "host": "imap.mail.me.com", "port": 993,
     "hint": "account.apple.com, Sign-In and Security, App-Specific Passwords. The username is your full iCloud address."},
    {"name": "Yahoo", "host": "imap.mail.yahoo.com", "port": 993, "hint": "Account Info, Account Security, Generate app password."},
    {"name": "Fastmail", "host": "imap.fastmail.com", "port": 993, "hint": "Settings, Privacy & Security, App passwords."},
    {"name": "Outlook", "host": "outlook.office365.com", "port": 993,
     "hint": "Microsoft has been switching off password sign-in for mail apps, so this may not work."},
    {"name": "Other", "host": "", "port": 993, "hint": "Your provider's IMAP server name, with an app password if it offers them."},
]
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
# A section header the way configparser sees it: everything up to the last "]", trailing text ignored.
SECTION_RE = re.compile(r"^\s*\[(.+)\]")


def _norm(name):
    """Section names compared the way the owner sees them: spacing and case don't matter."""
    return " ".join(str(name).split()).lower()


def mailbox_sections(p):
    """{normalised mailbox name: real section name} for every [email:Name] section."""
    out = {}
    for s in p.sections():
        if s.lower().startswith("email:"):
            out[_norm(s.split(":", 1)[1])] = s
    return out


class SettingsError(Exception):
    """A form value that can't be saved. The message says which one and why."""


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def parser(path):
    p = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    p.read(path, encoding="utf-8-sig")
    return p


def link_label(url, n):
    """Something the owner recognises without any secret part: the host and the position.
    (For iCloud and similar links the path itself is the secret, so no path segment ever appears.)"""
    try:
        host = urlsplit(url).netloc
    except ValueError:
        host = ""
    return f"{host}, link {n}" if host else f"link {n}"


def calendar_lines(p):
    raw = p.get("calendar", "urls", fallback="")
    return [line.strip() for line in raw.splitlines() if line.strip() and not line.strip().startswith("#")]


def describe(path):
    """Everything the Settings page needs to draw itself. No secret values, only whether they are set."""
    p = parser(path)
    sections = []
    for section, title, fields in SCHEMA:
        rows = []
        for key, label, kind, default, extra in fields:
            value = p.get(section, key, fallback=default).strip()
            if kind == "bool":
                value = value.lower() in ("1", "yes", "true", "on")
            rows.append({"key": key, "label": label, "type": kind, "value": value, **extra})
        sections.append({"id": section, "title": title, "fields": rows})

    mailboxes = []
    for name in mailbox_sections(p).values():
        sec = p[name]
        mailboxes.append({
            "name": " ".join(name.split(":", 1)[1].split()),
            "host": sec.get("host", "").strip(),
            "port": sec.get("port", "993").strip(),
            "username": sec.get("username", "").strip(),
            "password_set": bool(sec.get("password", "").strip()),
            "folder": sec.get("folder", "INBOX").strip() or "INBOX",
        })
    links = []
    for i, line in enumerate(calendar_lines(p)):
        try:
            label = link_label(vault.decrypt(line), i + 1)
        except vault.VaultError:
            label = f"link {i + 1}: can't be read any more (the key file changed)"
        links.append({"id": i, "label": label})
    return {"sections": sections, "mailboxes": mailboxes, "mailbox_fields": MAILBOX_FIELDS,
            "providers": PROVIDERS, "calendar_links": links}


# --------------------------------------------------------------------------- #
# Editing the file while keeping its comments
# --------------------------------------------------------------------------- #
def _section_span(lines, section):
    """(start, end) line indexes of a section, header included; None if absent."""
    start = None
    for i, line in enumerate(lines):
        m = SECTION_RE.match(line)
        if m and start is not None:
            return start, i
        if m and _norm(m.group(1)) == _norm(section):
            start = i
    return (start, len(lines)) if start is not None else None


def _is_continuation(line):
    return bool(line) and line[0] in " \t" and line.strip() != ""


def _value_end(lines, i, end):
    """Index just past the value that starts at line i: its indented continuation lines, and blank
    lines between them (configparser keeps a blank line inside a value when more indented lines follow)."""
    j = i + 1
    while j < end:
        if _is_continuation(lines[j]):
            j += 1
            continue
        if not lines[j].strip():
            k = j + 1
            while k < end and not lines[k].strip():
                k += 1
            if k < end and _is_continuation(lines[k]):
                j = k + 1
                continue
        break
    return j


def set_value(lines, section, key, value):
    """Set `section.key` to `value` (a string, or a list for a multi-line value) in place."""
    value_lines = ([f"{key} ="] + [f"    {v}" for v in value]) if isinstance(value, list) else [f"{key} = {value}".rstrip()]
    span = _section_span(lines, section)
    if span is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([f"[{section}]"] + value_lines)
        return lines
    start, end = span
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=", re.IGNORECASE)
    for i in range(start + 1, end):
        if key_re.match(lines[i]):
            lines[i:_value_end(lines, i, end)] = value_lines
            return lines
    insert = end
    while insert > start + 1 and not lines[insert - 1].strip():
        insert -= 1   # before the blank lines that separate sections
    lines[insert:insert] = value_lines
    return lines


def remove_section(lines, section):
    span = _section_span(lines, section)
    if span is None:
        return lines
    start, end = span
    while end > start and not lines[end - 1].strip():
        end -= 1
    del lines[start:end]
    while start < len(lines) and start > 0 and not lines[start].strip() and not lines[start - 1].strip():
        del lines[start]
    return lines


def read_lines(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    return text.split("\n")


def check_parses(lines):
    """Prove a rewritten file still parses before it replaces the real one."""
    check = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    try:
        check.read_string("\n".join(lines))
    except configparser.Error as exc:
        raise SettingsError(f"These settings can't be saved: {exc}")


def write_lines(path, lines):
    check_parses(lines)
    path = Path(path)
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Checking and saving the form
# --------------------------------------------------------------------------- #
def _check(section, field, raw):
    key, label, kind, default, extra = field
    text = str(raw if raw is not None else "").strip()
    if kind == "bool":
        return "yes" if raw in (True, "yes", "true", "on", "1", 1) else "no"
    if kind == "number":
        if text == "" and "min" in extra and extra["min"] <= 0:
            text = "0"
        try:
            num = float(text)
        except ValueError:
            num = math.nan
        if not math.isfinite(num):
            raise SettingsError(f"{label}: enter a number.")
        if num < extra.get("min", -1e9) or num > extra.get("max", 1e9):
            raise SettingsError(f"{label}: use a number from {extra['min']} to {extra['max']}.")
        if float(extra.get("step", 1)).is_integer():
            if not num.is_integer():
                raise SettingsError(f"{label}: use a whole number.")
            return str(int(num))
        return f"{num:g}"
    if kind == "decimal":
        if text == "":
            return ""
        try:
            if not math.isfinite(float(text)):
                raise ValueError
        except ValueError:
            raise SettingsError(f"{label}: enter a number such as 41.88 or -87.63.")
        return text
    if kind == "time":
        if text == "":
            return ""
        m = TIME_RE.match(text)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise SettingsError(f"{label}: use a 24-hour time such as 22:00.")
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    if kind == "choice":
        if text not in extra["choices"]:
            raise SettingsError(f"{label}: choose one of {', '.join(extra['choices'])}.")
        return text
    if "\n" in text:
        raise SettingsError(f"{label}: one line only.")
    return text


def apply(path, form):
    """Check the form from the Settings page and write config.ini. Raises SettingsError with a plain message."""
    lines = read_lines(path)
    p = parser(path)
    values = form.get("values") or {}
    for section, title, fields in SCHEMA:
        for field in fields:
            key = field[0]
            if f"{section}.{key}" in values:
                set_value(lines, section, key, _check(section, field, values[f"{section}.{key}"]))

    # Mailboxes: the form carries every mailbox that should exist. Others are removed.
    if "mailboxes" in form:
        existing = mailbox_sections(p)           # normalised name -> real section name
        wanted = {}                              # normalised name -> (name, old section or None, fields)
        for box in form["mailboxes"] or []:
            name = " ".join(str(box.get("name") or "").split())
            if not name:
                raise SettingsError("Each mailbox needs a name, such as Personal.")
            if any(ch in name for ch in "[]:=\n"):
                raise SettingsError(f"Mailbox name {name!r}: use letters, numbers and spaces only.")
            key = _norm(name)
            if key in wanted:
                raise SettingsError(f"Two mailboxes are called {name}. Give them different names.")
            host = str(box.get("host") or "").strip()
            if not host:
                raise SettingsError(f"{name}: enter the mail server, such as imap.gmail.com.")
            try:
                port = int(str(box.get("port") or "993").strip())
            except ValueError:
                raise SettingsError(f"{name}: the port must be a number, usually 993.")
            username = str(box.get("username") or "").strip()
            if not username:
                raise SettingsError(f"{name}: enter the username, usually your email address.")
            was = " ".join(str(box.get("was") or "").split())
            old_section = existing.get(_norm(was)) if was else existing.get(key)
            new_password = str(box.get("password") or "")
            if new_password.strip():
                password = vault.encrypt(new_password.strip())
            elif old_section and p.get(old_section, "password", fallback="").strip():
                password = p.get(old_section, "password").strip()   # unchanged: keep what is stored
            else:
                raise SettingsError(f"{name}: enter the app password.")
            folder = str(box.get("folder") or "INBOX").strip() or "INBOX"
            wanted[key] = (name, old_section, {"host": host, "port": str(port), "username": username,
                                               "password": password, "folder": folder})
        targets = {}
        for key, (name, old_section, fields) in wanted.items():
            # Keep the section's own spelling when the name didn't change; a rename moves it.
            target = old_section if old_section and _norm(old_section.split(":", 1)[1]) == key else f"email:{name}"
            if old_section and target != old_section:
                remove_section(lines, old_section)
            targets[key] = target
        for key, section in existing.items():
            if key not in targets:
                remove_section(lines, section)
        for key, (name, old_section, fields) in wanted.items():
            for field_key, value in fields.items():
                set_value(lines, targets[key], field_key, value)

    # Calendar links: keep some of the existing ones (by position), add new ones.
    if "calendar" in form:
        existing = calendar_lines(p)
        keep = set(int(i) for i in (form["calendar"].get("keep") or []))
        links = [line for i, line in enumerate(existing) if i in keep]
        for url in form["calendar"].get("add") or []:
            url = str(url or "").strip()
            if not url:
                continue
            if url.lower().startswith("webcal://"):
                url = "https://" + url[len("webcal://"):]
            if not url.lower().startswith("https://") and not url.lower().startswith("http://"):
                raise SettingsError("A calendar link starts with https:// or webcal://.")
            links.append(vault.encrypt(url))
        set_value(lines, "calendar", "urls", links)

    write_lines(path, lines)   # proves the result parses before it replaces the real file


def encrypt_in_place(path):
    """Encrypt plain passwords and calendar links found in config.ini. Returns how many were changed.
    Raises SettingsError, leaving the file untouched, if the rewrite would not parse."""
    p = parser(path)
    lines = read_lines(path)
    changed = 0
    for section in mailbox_sections(p).values():
        pw = p.get(section, "password", fallback="").strip()
        if pw and not vault.is_encrypted(pw):
            set_value(lines, section, "password", vault.encrypt(pw))
            changed += 1
    links = calendar_lines(p)
    if any(not vault.is_encrypted(u) for u in links):
        set_value(lines, "calendar", "urls", [vault.encrypt(u) for u in links])
        changed += sum(1 for u in links if not vault.is_encrypted(u))
    if changed:
        write_lines(path, lines)
        after = parser(path)
        if len(mailbox_sections(after)) != len(mailbox_sections(p)):
            raise SettingsError("the rewritten file would not keep the same mailboxes")
    return changed
