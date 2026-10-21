# PiClock

PiClock is a full-screen clock for a Raspberry Pi: a big clock, weather, upcoming calendar events and unread email, with a new-mail alert and chime, overnight dimming, and self-updating from GitHub. Built in a claude.ai conversation and handed over to Claude Code.

## The owner

Uses a Windows PC with PowerShell. Wants things straightforward: as few commands as possible, plain explanations, copy-paste commands. Explain what a command does before asking them to run something on the Pi. Not a professional developer, but comfortable following steps.

## Where things run

- **This PC**: the working copy at `C:/Users/Pouyan/Desktop/PiClock`, with the test environment in `.venv`. Make all code changes here.
- **GitHub**: `pouyna/PiClock`, public, the master copy. Pushing to `main` deploys: the Pi installs it within 10 minutes.
- **The Pi**: `pi@raspberrypi.local`, a Raspberry Pi 4 Model B (8 GB) running Raspberry Pi OS with desktop (Debian 13 "trixie", labwc on Wayland), 10" 1280×800 HDMI screen, time zone America/Chicago. App at `/home/pi/piclock` (a git clone).

Status on 2026-10-21: the project was renamed from "Desk Clock" to PiClock, the GitHub repo was created and pushed, and key-based SSH from this PC works. Nothing is installed on the Pi yet; the owner runs the install line from README.md. Check before assuming: `git remote -v` here, and `ssh pi@raspberrypi.local piclock status` for the Pi.

## Working with the Pi

- Run commands over SSH: `ssh pi@raspberrypi.local piclock status`. Use `ssh -t` for anything that may prompt for sudo (`piclock update`, `piclock restart`).
- Key-based SSH from this PC is set up (`~/.ssh/id_ed25519`, made 2026-10-21), so plain `ssh pi@raspberrypi.local <command>` runs without a prompt. If it ever asks for a password again, append `~/.ssh/id_ed25519.pub` to `~/.ssh/authorized_keys` on the Pi. Never ask for the Pi password, and never write it into any file.
- `sudo` on this Pi asks for the pi password (there is no NOPASSWD rule), so root commands can't be run from here. Anything that needs root (`piclock update`, `piclock restart`, saving in `piclock config`, `install.sh`, reboots) is for the owner to run with `ssh -t pi@raspberrypi.local <command>`; give them the exact line and say what it does. Automatic updates are unaffected: the timer runs as root.
- Useful: `piclock status`, `piclock update`, `journalctl -u piclock -u piclock-update -n 60 --no-pager`.
- Ask the owner first before: rebooting the Pi, editing `config.ini` on the Pi, installing system packages, or re-running `install.sh` there.
- `config.ini` on the Pi holds mail passwords and private calendar links. Don't print the whole file; read only the keys you need, for example with `grep -E '^(latitude|longitude) ' ~/piclock/config.ini`.

## How it fits together

| File | Role |
|---|---|
| `app.py` | Flask server on the Pi (waitress if installed). One background `Poller` thread per source (weather, mail, calendar) keeps the last result. `GET /api/all` returns `{boot_id, config, weather, mail, calendar}`, each source as `{enabled, data, error, updated}`. `GET /api/health` for checks. |
| `static/index.html` | The whole screen: one file, vanilla JS, no build step. Polls `/api/all` every 30 s, ticks the clock locally every second, reloads itself when `boot_id` changes (so every server restart pushes new code to the screen). |
| `static/fonts/` | Bundled fonts (OFL). The page makes no requests to the internet. |
| `piclock` | Bash control command: `status`, `update`, `config`, `restart`, `logs`, `show`, `hide`. Also the updater. |
| `install.sh` | Idempotent installer, run as the normal user (refuses root, uses sudo inside). Creates `.venv`, `config.ini` (from the example, chmod 600), the systemd units, `/usr/local/bin/piclock` launcher, the autostart entry, desktop autologin and no screen blanking. |
| `kiosk.sh` | Waits for `/api/health`, then starts Chromium in kiosk mode. Only one copy at a time. |
| `config.example.ini` | Every setting, with comments. Real settings live in `config.ini` on the Pi only. |
| `dev/` | Tests and preview tools (see Testing). Deployed to the Pi too, harmlessly. |

Created on the Pi by `install.sh`: `piclock.service` (runs app.py as the owner, `Restart=on-failure`), `piclock-update.service` + `.timer` (every 10 min, runs `piclock update --auto` as root), `~/.config/autostart/piclock.desktop`.

Data sources: Open-Meteo (no key; `timezone=auto`, so sunrise/sunset are local times without an offset), IMAP over SSL with app passwords, any iCal (.ics) links (`webcal://` becomes `https://`).

## How updates work (protect this: it's the Pi's lifeline)

`piclock update [--auto]` re-runs itself with sudo, then runs git and pip as the folder's owner via `runuser` with `HOME` set. It does `git fetch` and `git reset --hard @{upstream}`; untracked files (`config.ini`, `.venv`, `.update-skip`) are kept. It runs pip only if `requirements.txt` changed, restarts the service, and waits up to 45 s for `/api/health`. If the new version doesn't come up, it prints the log tail, rolls back to the previous commit, and writes the bad commit to `.update-skip` so the timer doesn't retry it. A manual `piclock update` always retries. `--auto` does nothing when `[updates] auto_update = no`.

Changes to `install.sh` are not applied automatically: the updater only prints a note. If you change the systemd units or autostart, tell the owner to re-run `bash ~/piclock/install.sh` on the Pi.

## Rules

1. Never commit `config.ini`, passwords, or calendar links. Check `git status` before committing.
2. Mail and calendar text is untrusted. Everything inserted with `innerHTML` goes through `esc()`.
3. Mail stays read-only: `select(..., readonly=True)` and `BODY.PEEK`. Nothing may mark mail as read.
4. Private calendar links never appear in logs, errors or the screen. Errors refer to "Calendar 2", never the URL.
5. Don't rely on file permissions. GitHub's web upload drops the executable bit, so scripts are always run as `/bin/bash script`. `/usr/local/bin/piclock` is a launcher, not a symlink. Always `rm` it before writing, because `tee` onto an old symlink would overwrite `piclock` itself.
6. Line endings stay LF (`.gitattributes`). A CRLF in a bash script breaks it on the Pi.
7. Parse config the same way everywhere (`app.py`, `piclock`, `kiosk.sh`): `ConfigParser(interpolation=None, delimiters=("=",))` and `encoding="utf-8-sig"`. Never put comments on the same line as a setting in `config.example.ini`, because configparser makes them part of the value.
8. Error text says what's wrong and how to fix it, pointing at `config.ini` or a `piclock` command. No apologies, no stack traces on screen.
9. Keep the Pi side light: no build step, no Node, no extra services. It may be an older Pi.
10. After changing `piclock` or `install.sh`, run `dev/test_update.sh` before pushing. A broken updater can strand the Pi.

## Testing (run before every push)

One-time setup on the PC (done on 2026-10-21; repeat only if `.venv` is missing):
```
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r dev\requirements-dev.txt
.venv\Scripts\playwright install chromium
```

- `pytest dev`: backend tests covering config parsing, calendar (recurring, all-day, time zones, cancelled, errors that hide URLs), mail (read-only, encoded headers, sign-in and network errors), weather, and the API. 14 tests.
- `python dev/screenshot.py`: renders the real page with sample data at 800×480, 1024×600, 1920×1080 and 720×1280 portrait, plus the new-mail alert, 24-hour mode, not-set-up and error states, into `dev/screenshots/` (gitignored). Open and look at them after every change to `static/index.html`; text clipping and overflow at 800×480 is the usual failure.
- `python dev/mock_server.py` (`--24h`, `--empty`, `--errors`): live preview at http://127.0.0.1:8099. Visit `/new-mail` to trigger an alert.
- `dev/test_update.sh`: fake GitHub plus fake Pi covering good update, broken update and rollback, no retry, a fix with a package change, config safety, and auto-updates off. 18 checks; about a minute. It needs Linux root. This PC has no WSL distro, so run it in Docker: start Docker Desktop, wait for it, then run this from PowerShell (it passed on 2026-10-21 in a Debian 13 container, the Pi's OS family):
  ```
  docker run --rm -v C:/Users/Pouyan/Desktop/PiClock:/src:ro python:3.13-slim bash -c "apt-get update -qq >/dev/null && apt-get install -y -qq git curl procps >/dev/null && mkdir /work && cd /src && tar --exclude=.venv --exclude=.git --exclude=dev/screenshots -cf - . | tar -xf - -C /work && cd /work && pip install -q -r requirements.txt && bash dev/test_update.sh"
  ```
  The owner can also run it on the Pi: `sudo PYTHON=$HOME/piclock/.venv/bin/python bash ~/piclock/dev/test_update.sh`.
- `bash -n install.sh kiosk.sh piclock` for syntax, if no Linux box is handy.

## Design system

The look: a night-sky screen readable from across a room, calm enough for a bedroom. One bold element, the giant clock with the day ribbon under it; everything else stays quiet.

- Colors (CSS variables): `--night #172031` background, `--dusk #222E44` tracks, `--bone #EEE7D7` main text, `--mist #A5ADBB` secondary, `--haze #67728A` tertiary, `--sodium #F2B45C` amber accent. Amber means only "the sun" or "just happened / happening now": daylight on the ribbon, ongoing events, the "in 12 min" countdown, the new-mail dot. Don't use it for decoration.
- Type: Big Shoulders Display (condensed) for numerals and headings: the clock, temperatures, the mail count, day names, agenda times. Atkinson Hyperlegible Next for all other text; its slashed zero is intentional, for legibility. Big Shoulders has no tabular figures, so the clock's width shifts slightly each minute; that's fine because it's left-aligned.
- Sizing: everything is in `--u`, where the screen is a 100×60 grid (`min(1vw, 1.6667vh)`), so the layout scales identically from 7" to 1080p. Portrait gets its own `--u` and a stacked layout.
- Layout: time on the top left with the day ribbon (midnight-to-midnight track, daylight segment, today's events as bars above, a dot for now), weather top right, agenda bottom left, mail bottom right. All left-aligned. Panels fade at the bottom, so errors go at the top of a panel.
- Copy: sentence case, plain words, active voice. No all-caps labels, no labels above content that doesn't need them, no "·" separators.
- Motion: only the new-mail alert (slides up from the bottom so it never covers the time) and the overnight dim fade. Respect `prefers-reduced-motion`.
- Running 24/7: the layout nudges a few pixels every 10 minutes; the chime stays silent while the screen is dimmed.

## Lessons learned (each one bit once)

- configparser treats `:` as a separator by default, so an unindented `https://...` line silently became a setting named `https`. Fixed with `delimiters=("=",)`; now it's a clear error.
- Windows Notepad adds a byte-order mark, which breaks configparser unless `utf-8-sig` is used.
- `pkill -f pattern` also matches the shell running your command if the pattern appears in it. Write patterns like `app[.]py`.
- Chromium blocks sound without a click unless started with `--autoplay-policy=no-user-gesture-required` (in `kiosk.sh`).
- Open-Meteo times and the calendar both rely on the Pi's time zone being set correctly (`raspi-config` → Localisation).
- Outlook.com has been removing password sign-in for IMAP; OAuth isn't implemented, so Outlook mail may not work.

## What has been checked on the real Pi

Seen over SSH on 2026-10-21, before the first install:

- Raspberry Pi 4 Model B, 8 GB, Debian 13 "trixie" based Raspberry Pi OS with desktop, Python 3.13, `python3-venv`, `pip`, `git`, `curl`, `nano`, `chromium` and `raspi-config` all present. The installer's apt step adds nothing new.
- labwc is the desktop. `/etc/xdg/labwc/autostart` runs `lxsession-xdg-autostart`, so the `~/.config/autostart/piclock.desktop` entry should start the kiosk at login. The README keeps the `~/.config/labwc/autostart` line as a fallback.
- `lightdm` already has `autologin-user=pi`, and `/run/user/1000/wayland-0` plus `/tmp/.X11-unix/X0` exist, which is what `piclock show` looks for.
- Audio outputs: HDMI and the headphone jack, through PipeWire. The time zone is America/Chicago and the clock is synchronized.

Still to confirm after the install:

- The kiosk actually opening at login, and `piclock show` from an SSH session.
- `raspi-config nonint do_blanking 1` on this OS version.
- The chime playing through the chosen output.
- Real Gmail/iCloud IMAP sign-in, and real Google/iCloud/Outlook .ics feeds.

## Ideas, if the owner wants more

- Real backlight dimming on the official touchscreen via `/sys/class/backlight` instead of the black overlay.
- Touch: tap to dismiss the mail alert, tap the mail panel for more messages.
- Severe weather alerts; a second time zone; OAuth for Outlook.com.
