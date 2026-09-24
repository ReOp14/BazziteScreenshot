"""Bind Print Screen to BazziteScreenshot through KDE's kglobalaccel D-Bus API."""

from __future__ import annotations

import configparser
import shutil
import subprocess
import sys
from pathlib import Path

import dbus

DESKTOP_ID = "io.github.bazzitescreenshot.desktop"
ACTION = [DESKTOP_ID, "_launch", "BazziteScreenshot", "Take Screenshot"]
KEY_PRINT = 0x01000009  # Qt::Key_Print

SET_PRESENT = 2
NO_AUTOLOADING = 4
IS_DEFAULT = 8

CONFIG = Path.home() / ".config" / "kglobalshortcutsrc"


def _kglobalaccel() -> dbus.Interface:
    obj = dbus.SessionBus().get_object("org.kde.kglobalaccel", "/kglobalaccel")
    return dbus.Interface(obj, "org.kde.KGlobalAccel")


def _keys(*sequences: list[int]) -> dbus.Array:
    return dbus.Array(
        [dbus.Struct([dbus.Array(seq, signature="i")], signature="ai") for seq in sequences],
        signature="(ai)",
    )


def _clear_stale_config_entries() -> None:
    """Drop Print from shortcuts of apps that are not currently loaded (e.g. an old Flameshot)."""
    if not CONFIG.exists() or not shutil.which("kwriteconfig6"):
        return
    parser = configparser.RawConfigParser(strict=False, interpolation=None, delimiters=("=",))
    parser.optionxform = str
    try:
        parser.read(CONFIG, encoding="utf-8")
    except configparser.Error:
        return
    for section in parser.sections():
        groups = section.split("][")
        if groups == ["services", DESKTOP_ID]:
            continue
        for key, value in parser.items(section):
            fields = value.split(",")
            if "Print" not in fields[0].split("\t"):
                continue
            remaining = "\t".join(k for k in fields[0].split("\t") if k != "Print") or "none"
            fields[0] = remaining
            args = ["kwriteconfig6", "--file", "kglobalshortcutsrc"]
            for g in groups:
                args += ["--group", g]
            subprocess.run(args + ["--key", key, ",".join(fields)], check=False)
            print(f"Removed Print from [{section}] {key}")


def _sequences(reply) -> list[list[int]]:
    """kglobalaccel pads each key sequence to four ints; drop the padding."""
    return [[int(k) for k in seq[0] if int(k)] for seq in reply]


def register(force: bool = True) -> int:
    g = _kglobalaccel()
    # Entries are (action, actionFriendly, component, componentFriendly, context, ...).
    for owner in g.getGlobalShortcutsByKey(KEY_PRINT):
        action, action_name, component, component_name = (str(x) for x in owner[:4])
        if component == DESKTOP_ID:
            continue
        if not force:
            print(f"Print is already used by {component_name} ({action_name}); rerun without --no-force",
                  file=sys.stderr)
            return 1
        owner_id = [component, action, component_name, action_name]
        remaining = [s for s in _sequences(g.shortcutKeys(owner_id)) if s and s != [KEY_PRINT]]
        g.setForeignShortcutKeys(owner_id, _keys(*remaining))
        print(f"Removed Print from {component_name} ({action_name})")
    _clear_stale_config_entries()

    g.doRegister(ACTION)
    g.setShortcutKeys(ACTION, _keys([KEY_PRINT]), dbus.UInt32(IS_DEFAULT))
    assigned = _sequences(g.setShortcutKeys(ACTION, _keys([KEY_PRINT]), dbus.UInt32(SET_PRESENT | NO_AUTOLOADING)))
    if [KEY_PRINT] not in assigned:
        print("kglobalaccel refused to assign Print", file=sys.stderr)
        return 1
    print("Print Screen is bound to BazziteScreenshot")
    return 0


def unregister() -> int:
    g = _kglobalaccel()
    try:
        g.setShortcutKeys(ACTION, _keys(), dbus.UInt32(SET_PRESENT | NO_AUTOLOADING))
        g.unregister(DESKTOP_ID, "_launch")
    except dbus.DBusException as exc:
        print(f"Could not unregister shortcut: {exc.get_dbus_message()}", file=sys.stderr)
        return 1
    print("Print Screen shortcut removed")
    return 0
