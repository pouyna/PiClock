#!/usr/bin/env bash
# End-to-end test of `piclock update`, the Pi's lifeline to GitHub.
# Builds a fake GitHub (a local bare repo) and a fake Pi install in a temp folder,
# stubs out systemctl/journalctl, then pushes good, broken and fixed versions.
#
# Linux only (WSL, the Pi itself, or any Linux machine). Needs root because the
# updater runs as root on the Pi:
#     sudo bash dev/test_update.sh
# Python with the app's packages is needed. On the Pi:
#     sudo PYTHON=$HOME/piclock/.venv/bin/python bash dev/test_update.sh
set -uo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash dev/test_update.sh"; exit 2; fi
PYTHON="${PYTHON:-python3}"
"$PYTHON" -c "import flask, icalendar, recurring_ical_events, requests" 2>/dev/null \
  || { echo "$PYTHON is missing the app's packages. Install them with: $PYTHON -m pip install -r requirements.txt"; exit 2; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T="$(mktemp -d)"
PORT=8196
PI="$T/pi/piclock"
PASS=0; FAIL=0
ok()   { echo "  ok    $1"; PASS=$((PASS + 1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
cleanup() { pkill -f "$PI/app[.]py" 2>/dev/null; rm -rf "$T"; }
trap cleanup EXIT

# ---- fake GitHub with the current project ----------------------------------
git init -q --bare -b main "$T/origin.git"
git clone -q "$T/origin.git" "$T/work" 2>/dev/null
( cd "$REPO" && tar --exclude=.git --exclude=.venv --exclude=config.ini --exclude=__pycache__ \
    --exclude=dev/screenshots --exclude=.update-skip -cf - . ) | tar -xf - -C "$T/work"
cd "$T/work"
git config user.email test@example.com; git config user.name Test
git add -A && git commit -qm "Starting version" && git push -q origin main

# ---- fake Pi install --------------------------------------------------------
mkdir -p "$T/pi" && git clone -q "$T/origin.git" "$PI"
git -C "$PI" config core.fileMode false
mkdir -p "$PI/.venv/bin" && ln -s "$(command -v "$PYTHON")" "$PI/.venv/bin/python"
printf '#!/bin/bash\necho "pip $*" >> "%s/pip.log"\n' "$T" > "$PI/.venv/bin/pip"; chmod +x "$PI/.venv/bin/pip"
sed "s/^port = 8080/port = $PORT/" "$PI/config.example.ini" > "$PI/config.ini"
CONFIG_SUM="$(md5sum < "$PI/config.ini")"

mkdir -p "$T/stubs"
cat > "$T/stubs/systemctl" <<EOF
#!/bin/bash
case "\$1" in
  restart)
    pkill -f "$PI/app[.]py"; sleep 0.5
    nohup "$PYTHON" "$PI/app.py" >> "$T/app.log" 2>&1 &
    ;;
  is-enabled|is-active) exit 0 ;;
esac
EOF
printf '#!/bin/bash\ntail -n 12 "%s/app.log"\n' "$T" > "$T/stubs/journalctl"
chmod +x "$T/stubs/"*
export PATH="$T/stubs:$PATH"
DC="/bin/bash $PI/piclock"
head_msg() { git -C "$PI" log -1 --format=%s; }
healthy() { curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null; }
push() { ( cd "$T/work" && git commit -qam "$1" && git push -q ); }

systemctl restart piclock; sleep 3

echo "1. Nothing new on GitHub"
out="$($DC update)"
check "says it's up to date" '[[ "$out" == *"Already up to date"* ]]'

echo "2. A good change"
sed -i 's|<title>PiClock</title>|<title>Test Title</title>|' "$T/work/static/index.html"; push "Change the title"
out="$($DC update --auto)"; code=$?
check "succeeds" '[[ $code -eq 0 && "$out" == *"Updated."* ]]'
check "new page is served" 'curl -s "http://127.0.0.1:$PORT/" | grep -q "<title>Test Title</title>"'

echo "3. A change that stops the clock from starting (takes about a minute)"
sed -i 's/^def clean(text):/def clean(text)/' "$T/work/app.py"; push "Broken change"
BAD="$(git -C "$T/work" rev-parse HEAD)"
out="$($DC update --auto)"; code=$?
check "reports failure" '[[ $code -ne 0 && "$out" == *"didn'"'"'t start"* ]]'
check "shows the error from the log" '[[ "$out" == *"SyntaxError"* ]]'
check "goes back to the working version" '[[ "$(head_msg)" == "Change the title" ]]'
check "clock is running again" 'healthy'
check "remembers the bad version" '[[ "$(cat "$PI/.update-skip")" == "$BAD" ]]'

echo "4. The timer runs again"
out="$($DC update --auto)"; code=$?
check "doesn't retry the bad version" '[[ $code -eq 0 && -z "$out" && "$(head_msg)" == "Change the title" ]]'
status="$($DC status 2>&1)"
check "status mentions the failed version" '[[ "$status" == *"failed to start"* ]]'

echo "5. A fix that also adds a Python package"
sed -i 's/^def clean(text)$/def clean(text):/' "$T/work/app.py"
echo "python-dateutil>=2.8" >> "$T/work/requirements.txt"; push "Fix it and add a package"
: > "$T/pip.log"
out="$($DC update --auto)"; code=$?
check "installs the fix" '[[ $code -eq 0 && "$(head_msg)" == "Fix it and add a package" ]]'
check "installs the new package list" 'grep -q "install --quiet -r" "$T/pip.log"'
check "forgets the bad version" '[[ ! -f "$PI/.update-skip" ]]'
check "clock is running" 'healthy'

echo "6. Settings are safe"
check "config.ini unchanged" '[[ "$(md5sum < "$PI/config.ini")" == "$CONFIG_SUM" ]]'
check "nothing stray in git" '[[ -z "$(git -C "$PI" status --porcelain)" ]]'

echo "7. Automatic updates turned off"
sed -i 's/^auto_update = yes/auto_update = no/' "$PI/config.ini"
sed -i 's|Test Title|PiClock|' "$T/work/static/index.html"; push "Title back"
out="$($DC update --auto)"
check "timer does nothing" '[[ -z "$out" && "$(head_msg)" == "Fix it and add a package" ]]'
out="$($DC update)"
check "manual update still works" '[[ "$out" == *"Updated."* && "$(head_msg)" == "Title back" ]]'

echo
echo "$PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
