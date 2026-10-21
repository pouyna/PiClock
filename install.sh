#!/usr/bin/env bash
# PiClock installer for Raspberry Pi OS with desktop.
# Run from inside the piclock folder as your normal user:   ./install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="$(id -un)"

if [[ $EUID -eq 0 ]]; then
  echo "Run this as your normal user (without sudo). It asks for sudo when it needs it."
  exit 1
fi

echo "==> Installing system packages"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip curl git
if ! command -v chromium >/dev/null 2>&1 && ! command -v chromium-browser >/dev/null 2>&1; then
  sudo apt-get install -y chromium || sudo apt-get install -y chromium-browser
fi

echo "==> Setting up Python environment"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$APP_DIR/config.ini" ]]; then
  cp "$APP_DIR/config.example.ini" "$APP_DIR/config.ini"
  echo "==> Created config.ini (edit it to add your mail and calendars)"
fi
chmod 600 "$APP_DIR/config.ini"   # it holds your mail passwords: owner-only
chmod +x "$APP_DIR/kiosk.sh" "$APP_DIR/piclock" 2>/dev/null || true

# The piclock command. A small launcher rather than a link, so it works even when
# file permissions get lost (GitHub's web upload drops them).
sudo rm -f /usr/local/bin/piclock
printf '#!/bin/sh\nexec /bin/bash "%s/piclock" "$@"\n' "$APP_DIR" | sudo tee /usr/local/bin/piclock >/dev/null
sudo chmod 755 /usr/local/bin/piclock

echo "==> Installing the background service"
sudo tee /etc/systemd/system/piclock.service >/dev/null <<UNIT
[Unit]
Description=PiClock data service
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/app.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable piclock.service
sudo systemctl restart piclock.service

echo "==> Opening the clock full screen at every login"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/piclock.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=PiClock
Exec=/bin/bash $APP_DIR/kiosk.sh
X-GNOME-Autostart-enabled=true
DESKTOP

if [[ -d "$APP_DIR/.git" ]]; then
  echo "==> Checking GitHub for updates every 10 minutes"
  git -C "$APP_DIR" config core.fileMode false   # ignore permission-only differences
  sudo tee /etc/systemd/system/piclock-update.service >/dev/null <<UNIT
[Unit]
Description=Update PiClock from GitHub
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
TimeoutStartSec=15min
ExecStart=/bin/bash $APP_DIR/piclock update --auto
UNIT
  sudo tee /etc/systemd/system/piclock-update.timer >/dev/null <<UNIT
[Unit]
Description=Check GitHub for PiClock updates

[Timer]
OnBootSec=3min
OnUnitActiveSec=10min
RandomizedDelaySec=30

[Install]
WantedBy=timers.target
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable --now piclock-update.timer
else
  echo "==> Not installed from GitHub, so automatic updates are off"
fi

if command -v raspi-config >/dev/null 2>&1; then
  echo "==> Logging in to the desktop automatically and keeping the screen on"
  sudo raspi-config nonint do_boot_behaviour B4 || true   # desktop, auto-login
  sudo raspi-config nonint do_blanking 1 || true          # disable screen blanking
fi

echo
echo "Done. Next steps:"
echo "  1. Add your settings:   piclock config"
echo "  2. Reboot once so auto-login and screen settings apply:   sudo reboot"
echo
echo "After that, control the clock any time with:"
echo "  piclock status | update | config | restart | logs | show | hide"
