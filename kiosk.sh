#!/usr/bin/env bash
# Opens PiClock full screen. Started automatically when the desktop loads.
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Only ever one copy on screen.
if pgrep -f "chromium.*--kiosk" >/dev/null; then exit 0; fi

PORT="$("$APP_DIR/.venv/bin/python" -c "import configparser as c; p=c.ConfigParser(interpolation=None, delimiters=('=',)); p.read('$APP_DIR/config.ini', encoding='utf-8-sig'); print(p.get('server','port',fallback='8080'))" 2>/dev/null || echo 8080)"
URL="http://127.0.0.1:${PORT}"

# Wait up to two minutes for the clock service to start.
for _ in $(seq 1 60); do
  curl -fs "$URL/api/health" >/dev/null 2>&1 && break
  sleep 2
done

BROWSER="$(command -v chromium || command -v chromium-browser)"
if [ -z "$BROWSER" ]; then
  echo "Chromium isn't installed. Run: sudo apt install chromium" >&2
  exit 1
fi

exec "$BROWSER" \
  --kiosk --incognito --noerrdialogs --disable-infobars \
  --disable-session-crashed-bubble --disable-features=Translate \
  --check-for-update-interval=31536000 --password-store=basic \
  --autoplay-policy=no-user-gesture-required \
  "$URL"
