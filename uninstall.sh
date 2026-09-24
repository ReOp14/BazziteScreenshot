#!/usr/bin/env bash
# Remove BazziteScreenshot for the current user.
set -uo pipefail

PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/bazzite-screenshot"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DBUS_SERVICES="${XDG_DATA_HOME:-$HOME/.local/share}/dbus-1/services"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
BIN="$HOME/.local/bin"

if [ -x "$BIN/bazzite-screenshot" ]; then
    "$BIN/bazzite-screenshot" unregister-shortcut || true
fi

systemctl --user disable --now bazzite-screenshot.service 2>/dev/null || true
rm -f "$UNITS/bazzite-screenshot.service"
systemctl --user daemon-reload

rm -f "$APPS/io.github.bazzitescreenshot.desktop" \
      "$APPS/io.github.bazzitescreenshot.capture.desktop" \
      "$DBUS_SERVICES/io.github.bazzitescreenshot.service" \
      "$BIN/bazzite-screenshot"
rm -rf "$PREFIX"
kbuildsycoca6 >/dev/null 2>&1 || true

echo "BazziteScreenshot removed. Rebind Print in System Settings > Shortcuts if you want Spectacle back."
