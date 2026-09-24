#!/usr/bin/env bash
# Install BazziteScreenshot for the current user (no root / rpm-ostree needed).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/bazzite-screenshot"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DBUS_SERVICES="${XDG_DATA_HOME:-$HOME/.local/share}/dbus-1/services"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
BIN="$HOME/.local/bin"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

say "Checking requirements"
command -v python3 >/dev/null || die "python3 not found"
python3 -c 'import PySide6.QtWidgets, PySide6.QtDBus' 2>/dev/null \
    || die "PySide6 is missing (python3-pyside6 ships with Bazzite; on other systems install it first)"
python3 -c 'import dbus' 2>/dev/null || die "dbus-python is missing (python3-dbus)"
command -v busctl >/dev/null || die "busctl not found"
command -v wl-copy >/dev/null || echo "   note: wl-copy not found, falling back to the Qt clipboard"
case "${XDG_CURRENT_DESKTOP:-}" in
    *KDE*) ;;
    *) echo "   warning: this tool targets KDE Plasma (detected '${XDG_CURRENT_DESKTOP:-unknown}')" ;;
esac

say "Installing files to $PREFIX"
mkdir -p "$PREFIX/bin" "$PREFIX/lib" "$APPS" "$DBUS_SERVICES" "$UNITS" "$BIN"
install -m 755 "$SRC/data/refresh-python.sh" "$PREFIX/refresh-python.sh"
sh "$PREFIX/refresh-python.sh"
rm -rf "$PREFIX/lib/bazzite_screenshot"
cp -r "$SRC/bazzite_screenshot" "$PREFIX/lib/"
find "$PREFIX/lib" -name '__pycache__' -type d -prune -exec rm -rf {} +

subst() { sed "s|@PREFIX@|$PREFIX|g" "$1" > "$2"; }
subst "$SRC/data/io.github.bazzitescreenshot.capture.desktop" "$APPS/io.github.bazzitescreenshot.capture.desktop"
install -m 644 "$SRC/data/io.github.bazzitescreenshot.desktop" "$APPS/io.github.bazzitescreenshot.desktop"
install -m 644 "$SRC/data/io.github.bazzitescreenshot.dbus.service" "$DBUS_SERVICES/io.github.bazzitescreenshot.service"
subst "$SRC/data/bazzite-screenshot.service" "$UNITS/bazzite-screenshot.service"

cat > "$BIN/bazzite-screenshot" <<EOF
#!/bin/sh
PYTHONPATH="$PREFIX/lib" exec "$PREFIX/bin/bazzite-screenshot-python" -m bazzite_screenshot "\$@"
EOF
chmod 755 "$BIN/bazzite-screenshot"

say "Refreshing KDE's application cache"
kbuildsycoca6 >/dev/null 2>&1 || true

say "Enabling the background service (starts with every login)"
systemctl --user daemon-reload
systemctl --user enable bazzite-screenshot.service >/dev/null
systemctl --user restart bazzite-screenshot.service

say "Binding Print Screen"
"$BIN/bazzite-screenshot" register-shortcut

say "Done. Press Print Screen to take a screenshot."
