"""Screen capture and window enumeration through KWin's D-Bus APIs."""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import dbus
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QImage, QScreen

log = logging.getLogger(__name__)

KWIN_SERVICE = "org.kde.KWin"
SCREENSHOT_PATH = "/org/kde/KWin/ScreenShot2"
SCREENSHOT_IFACE = "org.kde.KWin.ScreenShot2"
SCRIPTING_IFACE = "org.kde.kwin.Scripting"
SCRIPT_IFACE = "org.kde.kwin.Script"

WINDOW_SCRIPT = Path(__file__).with_name("kwin_windows.js")
WINDOW_SCRIPT_PLUGIN = "bazzite-screenshot-windows"


class CaptureError(RuntimeError):
    pass


@dataclass
class ScreenShot:
    screen: QScreen
    geometry: QRect  # logical (global) coordinates of the screen
    image: QImage  # native-resolution pixels of that screen

    @property
    def scale(self) -> float:
        return self.image.width() / max(1, self.geometry.width())


@dataclass
class WindowInfo:
    caption: str
    resource_class: str
    pid: int
    rect: QRect  # logical (global) frame geometry
    z: int  # stacking order, higher is on top

    @property
    def label(self) -> str:
        caption = self.caption or self.resource_class or "Untitled"
        if self.resource_class and self.resource_class.lower() not in caption.lower():
            return f"{caption}  ({self.resource_class})"
        return caption


class KWinCapture:
    """Thin wrapper around the KWin ScreenShot2 and Scripting interfaces."""

    def __init__(self) -> None:
        self._bus = dbus.SessionBus()

    def _screenshot_iface(self) -> dbus.Interface:
        obj = self._bus.get_object(KWIN_SERVICE, SCREENSHOT_PATH, introspect=False)
        return dbus.Interface(obj, SCREENSHOT_IFACE)

    def capture_screen(self, name: str) -> QImage:
        read_fd, write_fd = os.pipe()
        try:
            try:
                meta = self._screenshot_iface().CaptureScreen(
                    name,
                    dbus.Dictionary(
                        {"native-resolution": True, "include-cursor": False},
                        signature="sv",
                    ),
                    dbus.types.UnixFd(write_fd),
                    timeout=5,
                )
            finally:
                os.close(write_fd)
            with os.fdopen(read_fd, "rb") as pipe:
                read_fd = -1
                data = pipe.read()
        except dbus.DBusException as exc:
            raise CaptureError(f"KWin refused to capture {name}: {exc.get_dbus_message()}") from exc
        finally:
            if read_fd >= 0:
                os.close(read_fd)

        width, height = int(meta["width"]), int(meta["height"])
        stride, fmt = int(meta["stride"]), int(meta["format"])
        if len(data) < stride * height:
            raise CaptureError(f"Short read from KWin for {name}: {len(data)} bytes")
        return QImage(data, width, height, stride, QImage.Format(fmt)).copy()

    def capture(self, screens: list[QScreen]) -> list[ScreenShot]:
        try:
            return [ScreenShot(s, s.geometry(), self.capture_screen(s.name())) for s in screens]
        except CaptureError as exc:
            log.warning("%s; falling back to spectacle", exc)
            return capture_with_spectacle(screens)

    def request_window_list(self) -> None:
        """Run the KWin script that calls back WindowList() on our D-Bus service."""
        scripting = dbus.Interface(
            self._bus.get_object(KWIN_SERVICE, "/Scripting", introspect=False), SCRIPTING_IFACE
        )
        if scripting.isScriptLoaded(WINDOW_SCRIPT_PLUGIN):
            scripting.unloadScript(WINDOW_SCRIPT_PLUGIN)
        script_id = int(scripting.loadScript(str(WINDOW_SCRIPT), WINDOW_SCRIPT_PLUGIN))
        if script_id < 0:
            raise CaptureError("KWin could not load the window-list script")
        script = self._bus.get_object(KWIN_SERVICE, f"/Scripting/Script{script_id}", introspect=False)
        dbus.Interface(script, SCRIPT_IFACE).run()

    def unload_window_script(self) -> None:
        try:
            scripting = dbus.Interface(
                self._bus.get_object(KWIN_SERVICE, "/Scripting", introspect=False), SCRIPTING_IFACE
            )
            scripting.unloadScript(WINDOW_SCRIPT_PLUGIN)
        except dbus.DBusException as exc:
            log.debug("unloadScript failed: %s", exc)


def capture_with_spectacle(screens: list[QScreen]) -> list[ScreenShot]:
    """Slower fallback used when KWin has not authorized our interpreter."""
    with tempfile.TemporaryDirectory(prefix="bzshot-") as tmp:
        out = Path(tmp) / "full.png"
        subprocess.run(
            ["spectacle", "--background", "--nonotify", "--fullscreen", "--output", str(out)],
            check=True,
            timeout=15,
        )
        full = QImage(str(out))
    if full.isNull():
        raise CaptureError("spectacle did not produce an image")

    desktop = QRect()
    for s in screens:
        desktop = desktop.united(s.geometry())
    scale = full.width() / max(1, desktop.width())
    shots = []
    for s in screens:
        g = s.geometry().translated(-desktop.topLeft())
        src = QRect(round(g.x() * scale), round(g.y() * scale),
                    round(g.width() * scale), round(g.height() * scale))
        shots.append(ScreenShot(s, s.geometry(), full.copy(src)))
    return shots


def parse_window_list(payload: dict) -> tuple[QPoint | None, list[WindowInfo]]:
    cursor = payload.get("cursor")
    cursor_pos = QPoint(round(cursor["x"]), round(cursor["y"])) if cursor else None
    windows = []
    for item in payload.get("windows", []):
        rect = QRect(round(item["x"]), round(item["y"]), round(item["width"]), round(item["height"]))
        if rect.width() < 2 or rect.height() < 2:
            continue
        windows.append(WindowInfo(
            caption=item.get("caption", ""),
            resource_class=item.get("resourceClass", ""),
            pid=int(item.get("pid", 0)),
            rect=rect,
            z=int(item.get("z", 0)),
        ))
    return cursor_pos, windows
