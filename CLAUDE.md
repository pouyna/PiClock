# PiClock

PiClock is a full-screen clock for a Raspberry Pi touchscreen: a big clock, weather, upcoming calendar events and unread email, with a new-mail alert and chime, overnight dimming, an on-screen settings page with encrypted secrets, a hide button that frees the desktop, Claude's mark lit in the colour of what Claude Code on the PC is doing, and self-updating from GitHub. Built in a claude.ai conversation and handed over to Claude Code on 2026-10-21.

## The owner

Uses a Windows PC with PowerShell. Wants things straightforward: as few commands as possible, plain explanations, copy-paste commands. Explain what a command does before asking them to run something on the Pi. Not a professional developer, but comfortable following steps.

## Where things run

- **This PC**: the working copy at `C:/Users/Pouyan/Desktop/PiClock`, with the test environment in `.venv`. Make all code changes here.
- **GitHub**: `pouyna/PiClock`, public, the master copy. Pushing to `main` deploys: the Pi installs it within 10 minutes.
- **The Pi**: `pi@raspberrypi.local`, a Raspberry Pi 4 Model B (8 GB) running Raspberry Pi OS with desktop (Debian 13 "trixie", labwc on Wayland), 10" 1280×800 HDMI screen, time zone America/Chicago. App at `/home/pi/piclock` (a git clone).

Status on 2026-10-21: renamed from "Desk Clock" to PiClock, pushed, installed on the Pi by the owner and running on screen. The same evening the touch features, the hide button, the Settings page with encryption and the Claude light were added. Check before assuming: `git remote -v` here, and `ssh pi@raspberrypi.local piclock status` for the Pi.

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
| `app.py` | Flask server on the Pi (waitress if installed). One background `Poller` thread per source (weather, mail, calendar) keeps the last result; after a failure it retries in a minute, doubling up to the normal interval. `GET /api/all` returns `{boot_id, config, weather, mail, calendar, claude, screen}`, each source as `{enabled, data, error, updated}`. `GET /api/health` for checks. Local-only (`request.remote_addr` must be the loopback address, even when `host = 0.0.0.0`): `POST /api/screen` (`{"action": "hide"|"show"}`), `GET /settings`, `GET/POST /api/settings`, `POST /api/settings/check`. A settings save rewrites config.ini and re-executes the process (`os.execv`), so systemd sees the same service and no sudo is needed. At startup it encrypts any plain secrets in config.ini and writes the desktop launcher files. |
| `static/index.html` | The whole clock screen: one file, vanilla JS, no build step. Polls `/api/all` every 30 s, ticks the clock locally every second, reloads itself when `boot_id` changes (so every server restart pushes new code to the screen). Touch: tap the alert to dismiss, tap the agenda or mail to page through them, tap at night to brighten, a corner button for Settings and Hide, Claude's mark at the bottom left, lit in the status colour. Everything a tap changes reverts by itself within a minute. |
| `static/settings.html` | The Settings screen, drawn from `GET /api/settings` (no secret ever reaches the page, only "set" / "not set"). Toggles, steppers, segmented choices, and an on-screen keyboard of its own, because Chromium on Wayland doesn't open the Pi's keyboard. Returns to the clock after 4 idle minutes. |
| `settings.py` | `SCHEMA` (every setting the page shows), `describe()` for the page, `apply()` that checks the form and rewrites config.ini in place while keeping its comments, `encrypt_in_place()` at startup. |
| `vault.py` | Fernet encryption for mail passwords and calendar links (`enc:` prefix). Key in `~/.config/piclock/secret.key`, made on first use, outside the repo. Plain values pass through `decrypt()`, so old files keep working. |
| `claude_status.py` | The Claude sessions reported by the PC: one JSON file per session in `claude/` (gitignored), `receive()` for `piclock claude`, `sessions()` and `light()` for the API. States `limit`, `attention`, `working`, `done`, `idle`, in that order of importance; `ended` removes a session. |
| `pc/claude_status_hook.py` | Runs on the PC as a Claude Code hook. Maps hook events to states, rate-limits heartbeats to one a minute, sends the report with `ssh pi@raspberrypi.local piclock claude`. Prints nothing, exits 0, has its own deadline. `pc/hooks.example.json` is the settings block that registers it. |
| `static/fonts/`, `static/icon.svg` | Bundled fonts (OFL) and the launcher icon. The pages make no requests to the internet. |
| `piclock` | Bash control command: `status`, `update`, `config`, `restart`, `logs`, `show`, `hide`, `claude` (reads one report from stdin). Also the updater. |
| `install.sh` | Idempotent installer, run as the normal user (refuses root, uses sudo inside). Creates `.venv`, `config.ini` (from the example, chmod 600), the systemd units, `/usr/local/bin/piclock` launcher, the autostart entry, desktop autologin and no screen blanking. |
| `kiosk.sh` | Waits for `/api/health`, then starts Chromium in kiosk mode. Only one copy at a time. |
| `config.example.ini` | Every setting, with comments. Real settings live in `config.ini` on the Pi only. |
| `dev/` | Tests and preview tools (see Testing). Deployed to the Pi too, harmlessly. |

Created on the Pi by `install.sh`: `piclock.service` (runs app.py as the owner, `Restart=on-failure`), `piclock-update.service` + `.timer` (every 10 min, runs `piclock update --auto` as root), `~/.config/autostart/piclock.desktop`. Created by app.py itself: `~/Desktop/PiClock.desktop` and `~/.local/share/applications/piclock.desktop` (bring a hidden clock back by touch), `~/.config/piclock/secret.key`, `~/piclock/claude/`.

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
11. Secrets never leave the Pi through the API: `/api/settings` reports only whether a password is set and labels a calendar link by host and position (for iCloud the path *is* the secret), and error text never contains one. Settings, the mailbox check and the hide button answer only to the loopback address and only to JSON bodies. The mailbox check reuses a stored password only for the same server and username it was saved for. IMAP always verifies the server certificate (`ssl.create_default_context()`).
12. The hook on the PC must never interfere with Claude Code: no stdout, exit 0 always, a deadline on everything (stdin read and ssh). Keep it stdlib-only so `py -I` runs it anywhere.
13. The Claude light is the one place with colours outside the palette, because the owner asked for green/blue/yellow/red. Don't let them spread. Since 2026-10-22 the light is Claude's mark (Anthropic's trademark; the path is Simple Icons' CC0 tracing, inline in index.html) filled with the status colour, at the owner's request; the dots in the sessions sheet stay dots.
14. Touch targets are at least 4.6 `--u` tall, every tap effect reverts by itself, and the hide action needs two taps (corner button, then Hide) so a wipe of the screen can't close the clock. Raspberry Pi OS makes labwc turn touches into mouse events (`mouseEmulation="yes"` on the `<touch>` line in `~/.config/labwc/rc.xml`), so a finger drag reaches Chromium as a mouse drag and scrolls nothing by itself: anything that has to scroll by finger uses `dragScroll()` in settings.html, which steps aside for real touch events.
15. Anything the service starts on the screen must not live in the service's cgroup, or the next `systemctl restart piclock` (every update) kills it: `cmd_show` starts the kiosk with `systemd-run --user`, falling back to `nohup`. The hidden state is kept in `.screen-hidden` so a restart doesn't lose the return timer.
16. Settings validation is the last line between the screen and a service that can't start: whole-number fields reject fractions and `nan`, section headers are matched the way configparser reads them, and every rewrite of config.ini is parsed before it replaces the file. The server reads whole-number settings through `cfg_int()`, which falls back to the default instead of raising.

## Testing (run before every push)

One-time setup on the PC (done on 2026-10-21; repeat only if `.venv` is missing):
```
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r dev\requirements-dev.txt
.venv\Scripts\playwright install chromium
```

- `pytest dev`: 67 tests. `test_backend.py` covers config parsing, calendar, mail, weather and the API; `test_features.py` the Claude sessions and light, the hide endpoint, weather 5xx wording and the retry delay; `test_settings.py` the vault, `describe()`, `apply()` (comments kept, mailboxes added, renamed, removed, encrypted), `encrypt_in_place()` and the settings API; `test_hook.py` the PC hook's event mapping and heartbeat rate limit; `test_pages.py` opens the Settings page in Playwright's Chromium and checks that a mouse-style drag scrolls it without pressing anything (skipped when Playwright's browser is missing).
- `python dev/screenshot.py`: renders the real pages with sample data at 800×480, 1024×600, 1920×1080 and 720×1280 portrait, plus the new-mail alert, the tap states (mail, agenda, hide sheet, light sheet), no light, 24-hour mode, not-set-up and error states, and the Settings page with its keyboard, into `dev/screenshots/` (gitignored). Open and look at them after every change to the pages; text clipping and overflow at 800×480 is the usual failure.
- `python dev/mock_server.py` (`--24h`, `--empty`, `--errors`, `--no-claude`, `--light red`): live preview at http://127.0.0.1:8099, with `/settings` served from `config.example.ini`. Visit `/new-mail` to trigger an alert.
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
- Touch: two quiet corner controls only, Claude's mark bottom left and the arrow bottom right; sheets are bone cards like the alert. The Settings page keeps the same palette and type, with bone for the one primary action per screen.

## Lessons learned (each one bit once)

- configparser treats `:` as a separator by default, so an unindented `https://...` line silently became a setting named `https`. Fixed with `delimiters=("=",)`; now it's a clear error.
- Windows Notepad adds a byte-order mark, which breaks configparser unless `utf-8-sig` is used.
- `pkill -f pattern` also matches the shell running your command if the pattern appears in it. Write patterns like `app[.]py`.
- Chromium blocks sound without a click unless started with `--autoplay-policy=no-user-gesture-required` (in `kiosk.sh`).
- Open-Meteo times and the calendar both rely on the Pi's time zone being set correctly (`raspi-config` → Localisation).
- Outlook.com has been removing password sign-in for IMAP; OAuth isn't implemented, so Outlook mail may not work.
- Claude Code hooks: `"async": true` keeps a hook from blocking, but then no timeout applies, so the script needs its own deadline; stdin may not always be closed, so read it with a deadline too. Exit code 2 or stdout on UserPromptSubmit/Stop changes Claude's behaviour, so the hook prints nothing and exits 0.
- A heartbeat stamp must carry the report time explicitly (`os.utime`), not the file's natural mtime, or tests with a fake clock lie.
- `sudo` on this Pi needs a password, so the service restarts itself with `os.execv` after a settings save instead of `systemctl restart`. Python closes its sockets on exec, so the port is free again at once. A port change makes the Settings page navigate to the new port after a pause, because it can't poll a different origin.
- configparser accepts `[section] trailing text` and `[Email: Personal]`; a header regex anchored at the end of the line missed them and `set_value` appended a duplicate section. Mirror configparser (`^\s*\[(.+)\]`) and compare names normalised.
- `imaplib.IMAP4_SSL` without `ssl_context` does not verify certificates. Always pass `ssl.create_default_context()`.
- The adversarial review of 2026-10-21 (five reviewers, two refuters per finding) ran into the owner's Claude usage limit; keep fan-outs small, as the memory file already said.
- The Bash tool on this PC truncates commands longer than about 8k characters; long edit scripts go in a file first.
- labwc's touch mouse emulation (the Pi OS default for touchscreens) hands touches to Chromium as mouse events, pointerType `mouse`: taps work as clicks, but a finger drag scrolls nothing. Settings scrolls by hand (`dragScroll()`), following the pointer on the document: pointer capture on the list sent the click that ends a plain tap to the list instead of the button, so taps stopped working.
- Chromium overwrites its own environment block with its process title, so `/proc/<pid>/environ` holds no variables. `systemctl --user show-environment` on the Pi shows what the kiosk inherits (`DISPLAY`, `WAYLAND_DISPLAY`, `XDG_SESSION_TYPE=wayland`).

## What has been checked on the real Pi

Seen over SSH on 2026-10-21, before the first install:

- Raspberry Pi 4 Model B, 8 GB, Debian 13 "trixie" based Raspberry Pi OS with desktop, Python 3.13, `python3-venv`, `pip`, `git`, `curl`, `nano`, `chromium` and `raspi-config` all present. The installer's apt step adds nothing new.
- labwc is the desktop. `/etc/xdg/labwc/autostart` runs `lxsession-xdg-autostart`, so the `~/.config/autostart/piclock.desktop` entry should start the kiosk at login. The README keeps the `~/.config/labwc/autostart` line as a fallback.
- `lightdm` already has `autologin-user=pi`, and `/run/user/1000/wayland-0` plus `/tmp/.X11-unix/X0` exist, which is what `piclock show` looks for.
- Audio outputs: HDMI and the headphone jack, through PipeWire. The time zone is America/Chicago and the clock is synchronized.

Confirmed after the first install (2026-10-21, a six-lens read-only check over SSH plus a screenshot of the real screen with `grim`): the kiosk opens at login, the service runs as `pi`, the updater timer fired as root and exited 0, the Pi reads the public repo, config.ini is 0600, port 8080 is loopback-only, the screen stays lit, and Open-Meteo answered 503 for a while right after boot (hence the quick retry and the kinder message).
Also seen, for the owner to decide: wayvnc listens on port 5900 with the pi account's well-known default password, rpcbind on 111; the default audio sink sits at 40%, so the chime will be faint (`ssh pi@raspberrypi.local 'XDG_RUNTIME_DIR=/run/user/1000 wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.8'` raises it without sudo). A screenshot of the real screen: `ssh pi@raspberrypi.local 'WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 grim /tmp/s.png'` then scp it.

Seen on 2026-10-22: `~/.config/labwc/rc.xml` holds `<touch deviceName="TSTP MTouch (USB 1-1.4)" mapToOutput="HDMI-A-1" mouseEmulation="yes"/>`, written by Pi OS for this screen. The owner opened Settings by touch (taps work) but couldn't scroll; `dragScroll()` fixed it the same day. The kiosk Chromium (155) inherits both `WAYLAND_DISPLAY` and `DISPLAY` from the user manager and runs with no `--ozone-platform` flag. `piclock show` from the service's return timer works (confirmed 2026-10-22 by the hide-and-return round trip).

Still to confirm:

- Finger scrolling on Settings after the 2026-10-22 fix, the on-screen keyboard and the rest of the Settings flow on the real touchscreen, and whether the Pi's own keyboard (squeekboard) also pops up over it.
- `raspi-config nonint do_blanking 1` on this OS version.
- The chime playing through the chosen output.
- Real Gmail/iCloud IMAP sign-in, and real Google/iCloud/Outlook .ics feeds.
- The light turning red on a real usage limit (`StopFailure` wording), and `SessionEnd` firing when a terminal is closed rather than exited.

## Ideas, if the owner wants more

- Backlight dimming: this HDMI touchscreen (TSTP MTouch, 1280×800) exposes no `/sys/class/backlight` and no DDC, so the overlay stays.
- A hidden clock could minimise instead of closing Chromium if `wlrctl` were installed (a system package; ask first).
- Severe weather alerts; a second time zone; OAuth for Outlook.com; hourly forecast on a tap of the weather panel.
- Real touch instead of mouse emulation: `mouseEmulation="no"` on the `<touch>` line in `~/.config/labwc/rc.xml`, then a reboot, gives Chromium real touch events (native scrolling with a glide everywhere, multi-touch). Pi OS sets `yes` so that apps which only understand a mouse still react to the screen, and its screen-configuration tool writes `yes` again. The owner's call; `dragScroll()` steps aside when real touch arrives.
