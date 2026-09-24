#!/bin/sh
# KWin only lets whitelisted executables take screenshots, so the daemon runs
# on a private copy of the system interpreter. Re-copy it after OS updates.
set -e
prefix="$(dirname "$(readlink -f "$0")")"
src="$(readlink -f /usr/bin/python3)"
dst="$prefix/bin/bazzite-screenshot-python"
if ! cmp -s "$src" "$dst"; then
    mkdir -p "$prefix/bin"
    install -m 755 "$src" "$dst.new"
    mv -f "$dst.new" "$dst"
fi
