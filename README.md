# PiClock

![PiClock showing the time, weather, today's events and unread mail](docs/preview.png)

A full-screen clock for a Raspberry Pi that also shows the weather, your next calendar events and your unread email. When new mail arrives it shows who it's from and plays a soft chime (silent overnight). The screen dims at night.

It runs entirely on your Pi. Mail is checked read-only, so nothing gets marked as read.

You'll need a Raspberry Pi running **Raspberry Pi OS with desktop** (not Lite), a screen, and an internet connection.

## 1. The code lives on GitHub

The project is at https://github.com/pouyna/PiClock. The Pi installs from there and checks it for changes every 10 minutes, so pushing to GitHub is how a change reaches the clock. The repository is public, which is fine: your passwords and calendar links live only in `config.ini` on the Pi, and this project never uploads that file.

The working copy on your PC is the `PiClock` folder on the Desktop. Claude Code makes changes there, tests them, and pushes them.

## 2. Install on the Pi (once)

From PowerShell on your PC, log in to the Pi:

```
ssh pi@raspberrypi.local
```

If this is the first time you've logged in, change the well-known default password with `passwd`. Then run this one line:

```
sudo apt install -y git && git clone https://github.com/pouyna/PiClock.git ~/piclock && bash ~/piclock/install.sh
```

It asks for your Pi password once, for `sudo`. The installer sets up everything it needs, starts a background service, turns on updates from GitHub, makes the Pi log in to the desktop automatically, turns off screen blanking, and opens the clock full screen at every login. Reboot once afterwards with `sudo reboot`.

Also check the Pi's time zone is right, or the clock and calendar times will be off: `sudo raspi-config` → Localisation Options → Timezone.

If `raspberrypi.local` doesn't work, use the Pi's IP address instead (find it in your router's device list). If SSH is refused, turn it on in Raspberry Pi Configuration → Interfaces, or put an empty file named `ssh` on the SD card's boot drive.

## 3. Add your weather, calendar and email

Open the settings file:

```
piclock config
```

Move around with the arrow keys and paste with a right-click. Save with Ctrl+O, Enter, then exit with Ctrl+X. The clock checks the file for mistakes, restarts, and shows you whether weather, mail and calendar are working. The screen updates on its own within 30 seconds.

Prefer editing on your PC? Copy `config.example.ini` to `config.ini`, edit it in Notepad, then send it over with `scp config.ini pi@raspberrypi.local:~/piclock/` and run `ssh -t pi@raspberrypi.local piclock restart`. Don't upload `config.ini` to GitHub.

### Weather

Weather comes from Open-Meteo, which needs no account. It's set to Chicago; to change it, right-click your home in Google Maps, click the coordinates to copy them, and put them in `latitude` and `longitude`.

### Calendar

The clock reads your calendar from a private iCal link. Paste each link on its own indented line under `urls =`:

```
urls =
    https://calendar.google.com/calendar/ical/.../basic.ics
    webcal://p01-caldav.icloud.com/published/2/...
```

Where to find the link:

**Google Calendar** (on a computer): Settings → pick your calendar under "Settings for my calendars" → Integrate calendar → copy "Secret address in iCal format".

**iCloud**: in the Calendar app on your iPhone, tap Calendars → the ⓘ next to a calendar → turn on Public Calendar → Share Link. Anyone with this link can see that calendar.

**Outlook.com**: Settings → Calendar → Shared calendars → Publish a calendar → choose the calendar and "Can view all details" → Publish → copy the ICS link.

Treat these links like passwords.

### Email

Uncomment the `[email:Personal]` example (remove the `#` at the start of each line) and fill it in. Add more `[email:Name]` sections for more mailboxes. Use an **app password**, a separate password made just for this device, rather than your normal one:

| Provider | `host` | Where to make an app password |
|---|---|---|
| Gmail | `imap.gmail.com` | Turn on 2-Step Verification, then go to myaccount.google.com/apppasswords. Type it without spaces. |
| iCloud Mail | `imap.mail.me.com` | account.apple.com → Sign-In and Security → App-Specific Passwords. Username is your full iCloud email. |
| Yahoo | `imap.mail.yahoo.com` | Account Info → Account Security → Generate app password |
| Fastmail | `imap.fastmail.com` | Settings → Privacy & Security → App passwords |
| Outlook.com / Hotmail | `outlook.office365.com` | Microsoft has been switching off password sign-in for mail apps, so this may not work. |

## Changing the clock

Make changes on GitHub and the Pi picks them up by itself within 10 minutes. To edit a file, open it on github.com, click the pencil icon, make your change, and click **Commit changes**. Or ask Claude Code to make the change and push it (see below). The screen reloads on its own after each update.

To install a change right away instead of waiting, run this from PowerShell on your PC:

```
ssh -t pi@raspberrypi.local piclock update
```

Each update is tested before it's kept. If a change stops the clock from starting, the Pi goes back to the last version that worked and shows what went wrong. `piclock status` will mention it too. Fix the problem on GitHub and the next change is installed as usual.

Two things to know:

- Make code changes on GitHub, not on the Pi. Updates replace the Pi's copy of the code to match GitHub.
- Your settings are the exception. `config.ini` stays on the Pi and updates never touch it, so keep editing it with `piclock config`.

To update only when you ask, set `auto_update = no` in the `[updates]` section of `config.ini`.

**Prefer a private repository?** The Pi then needs its own read-only key. On the Pi, run `ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519` and then `cat ~/.ssh/id_ed25519.pub`. Copy the line it prints. On GitHub, go to your repository → Settings → Deploy keys → **Add deploy key**, paste it, and leave write access off. Then install using `git@github.com:pouyna/PiClock.git` in place of the `https://` address, and type `yes` when asked to trust github.com.

## Working on it with Claude Code

Open this folder in Claude Code. It reads `CLAUDE.md` automatically, which explains how everything fits together and the rules to keep the Pi working. Ask for changes in plain words, such as "make the clock bigger" or "add a second time zone". Claude Code can test them, push them to GitHub, and check on the Pi over SSH.

The `dev` folder holds the tests and a preview: `python dev/mock_server.py` shows the clock with sample data at http://127.0.0.1:8099 in your browser, no Pi needed.

## Controlling it from your PC

Log in with `ssh pi@raspberrypi.local`, then use these. The ones marked with `sudo` ask for your Pi password:

| Command | What it does |
|---|---|
| `piclock status` | Shows whether the clock is running and on screen, which version it's on, and whether weather, mail and calendar are working |
| `piclock update` | Install the latest version from GitHub now (`sudo`) |
| `piclock config` | Edit your settings (`sudo` when you save a change) |
| `piclock restart` | Restart after changing files (`sudo`) |
| `piclock logs` | Watch the live log (Ctrl+C to stop) |
| `piclock show` | Open the clock on the Pi's screen |
| `piclock hide` | Close it, so you can use the Pi's desktop |
| `sudo reboot` | Restart the whole Pi |

Type `exit` to leave the SSH session. The clock keeps running.

You can also run any of these straight from your PC without logging in first, for example `ssh -t pi@raspberrypi.local piclock status`.

## Everyday use

With a keyboard plugged into the Pi, **Alt+F4** closes the full-screen clock. Bring it back with `piclock show` or a reboot.

The display settings in `config.ini` let you switch to a 24-hour clock, show seconds, change the overnight dimming hours and brightness, or turn off the chime.

## If something isn't working

**Start with** `piclock status`. It names the part that isn't working and why. For more detail, use `piclock logs`.

**Mail says sign-in failed:** double-check the username and app password. For Gmail, make sure it's an app password, not your Google password.

**An update didn't arrive:** run `ssh -t pi@raspberrypi.local piclock update` to see what happens. It will say if GitHub can't be reached or if the new version failed to start.

**`piclock show` says no desktop is running:** the Pi isn't logged in to its desktop. Run `sudo reboot`; the installer set it to log in automatically.

**The clock doesn't open at login:** add the launcher to your desktop's own autostart file. On newer Raspberry Pi OS (labwc), run:

```
echo "/bin/bash $HOME/piclock/kiosk.sh &" >> ~/.config/labwc/autostart
```

On Raspberry Pi OS Bookworm with Wayfire, add this line under the `[autostart]` section of `~/.config/wayfire.ini` (create the section if it's missing):

```
piclock = /bin/bash /home/pi/piclock/kiosk.sh
```

**The screen still goes black after a while:** `sudo raspi-config` → Display Options → Screen Blanking → No, then reboot.

**Want to view it from another computer?** Set `host = 0.0.0.0` in `[server]`, restart, and open `http://raspberrypi.local:8080`. Anyone on your network could then see your mail subjects and calendar.

## Files

`CLAUDE.md` is the project guide for Claude Code, `dev/` has the tests and preview tools, `app.py` gathers the data, `static/index.html` is the clock screen, `install.sh` sets everything up, `kiosk.sh` opens the browser full screen, `piclock` is the control command and updater, and `config.ini` (on the Pi only) holds your settings. Fonts are Big Shoulders Display and Atkinson Hyperlegible Next, included under the SIL Open Font License.
