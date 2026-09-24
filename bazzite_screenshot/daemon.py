"""Resident daemon: owns the D-Bus name, captures on Trigger and serves the clipboard."""

from __future__ import annotations

import ctypes
import gc
import json
import logging
import shutil
import subprocess
import sys

import dbus
from PySide6.QtCore import ClassInfo, QBuffer, QByteArray, QIODevice, QMimeData, QObject, QTimer, Slot
from PySide6.QtDBus import QDBusConnection
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication

from . import DBUS_INTERFACE, DBUS_PATH, DBUS_SERVICE
from .capture import CaptureError, KWinCapture, parse_window_list
from .overlay import Controller

log = logging.getLogger(__name__)


def release_memory() -> None:
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def encode_png(image: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG", 1)
    buf.close()
    return bytes(data.data())


def copy_with_wl_copy(png: bytes) -> bool:
    exe = shutil.which("wl-copy")
    if not exe:
        return False
    try:
        # wl-copy forks a background server that keeps offering the data.
        subprocess.run(
            [exe, "--type", "image/png"],
            input=png,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=10,
        )
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("wl-copy failed: %s", exc)
        return False


def copy_with_qt(image: QImage, png: bytes) -> None:
    mime = QMimeData()
    mime.setImageData(image)
    mime.setData("image/png", QByteArray(png))
    QGuiApplication.clipboard().setMimeData(mime)


@ClassInfo({"D-Bus Interface": DBUS_INTERFACE})
class Service(QObject):
    """Exported on the session bus as io.github.bazzitescreenshot at /."""

    def __init__(self, daemon: "Daemon") -> None:
        super().__init__()
        self._daemon = daemon

    @Slot()
    def Trigger(self) -> None:
        QTimer.singleShot(0, self._daemon.trigger)

    @Slot(str)
    def WindowList(self, payload: str) -> None:
        self._daemon.receive_windows(payload)

    @Slot()
    def Cancel(self) -> None:
        if self._daemon.controller is not None:
            QTimer.singleShot(0, self._daemon.controller.cancel)

    @Slot()
    def Quit(self) -> None:
        QTimer.singleShot(0, QApplication.quit)


class Daemon(QObject):
    def __init__(self, app: QApplication, notify: bool = False) -> None:
        super().__init__()
        self.app = app
        self.notify_enabled = notify
        self.capture = KWinCapture()
        self.controller: Controller | None = None
        self.service = Service(self)
        self._script_timer = QTimer(self, singleShot=True, interval=3000)
        self._script_timer.timeout.connect(self.capture.unload_window_script)

    def register(self) -> bool:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            log.error("Cannot connect to the D-Bus session bus")
            return False
        if not bus.registerObject(DBUS_PATH, self.service, QDBusConnection.ExportAllSlots):
            log.error("Could not export D-Bus object: %s", bus.lastError().message())
            return False
        if not bus.registerService(DBUS_SERVICE):
            log.error("%s is already running (%s)", DBUS_SERVICE, bus.lastError().message())
            return False
        return True

    # ------------------------------------------------------------------ capture
    def trigger(self) -> None:
        if self.controller is not None:
            for o in self.controller.overlays:
                o.raise_()
            if self.controller.overlays:
                self.controller.overlays[0].activateWindow()
            return

        try:
            shots = self.capture.capture(self.app.screens())
        except (CaptureError, subprocess.SubprocessError, OSError) as exc:
            log.error("Capture failed: %s", exc)
            self.notify("Screenshot failed", str(exc))
            return

        try:
            self.capture.request_window_list()
            self._script_timer.start()
        except (dbus.DBusException, CaptureError) as exc:
            log.warning("Window list unavailable: %s", exc)

        self.controller = Controller(shots)
        self.controller.finished.connect(self.finish)
        self.controller.show()

    def receive_windows(self, payload: str) -> None:
        self._script_timer.stop()
        self.capture.unload_window_script()
        try:
            cursor, windows = parse_window_list(json.loads(payload))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            log.warning("Bad window list from KWin: %s", exc)
            return
        log.debug("KWin reported %d windows, cursor at %s", len(windows), cursor)
        if self.controller is not None:
            self.controller.set_windows(windows, cursor)

    def finish(self, image: QImage | None) -> None:
        controller = self.controller
        if image is not None and not image.isNull():
            png = encode_png(image)
            copy_with_qt(image, png)
            if not copy_with_wl_copy(png):
                log.info("Using the Qt clipboard only")
            self.notify("Screenshot copied", f"{image.width()} \u00d7 {image.height()} image is on the clipboard")
        # Let the compositor process the clipboard offer before the overlays unmap.
        QTimer.singleShot(50, lambda: self._teardown(controller))

    def _teardown(self, controller: Controller | None) -> None:
        if controller is not None:
            controller.close()
            controller.deleteLater()
        if self.controller is controller:
            self.controller = None
        # The frozen images (~50 MB per 4K screen) sit in reference cycles
        # between widgets and the controller; free them now rather than at
        # the garbage collector's leisure, and hand the pages back to the OS.
        QTimer.singleShot(100, release_memory)

    def notify(self, summary: str, body: str) -> None:
        if not self.notify_enabled:
            return
        try:
            bus = dbus.SessionBus()
            obj = bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications")
            dbus.Interface(obj, "org.freedesktop.Notifications").Notify(
                "BazziteScreenshot", dbus.UInt32(0), "spectacle", summary, body,
                dbus.Array([], signature="s"), dbus.Dictionary({}, signature="sv"), 2500,
            )
        except dbus.DBusException as exc:
            log.debug("Notification failed: %s", exc)


def run_daemon(notify: bool = False) -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("BazziteScreenshot")
    app.setDesktopFileName("io.github.bazzitescreenshot.capture")
    app.setQuitOnLastWindowClosed(False)

    daemon = Daemon(app, notify=notify)
    if not daemon.register():
        return 1
    log.info("BazziteScreenshot daemon ready on %s", DBUS_SERVICE)
    return app.exec()


def send_trigger() -> int:
    try:
        obj = dbus.SessionBus().get_object(DBUS_SERVICE, DBUS_PATH)
        dbus.Interface(obj, DBUS_INTERFACE).Trigger()
        return 0
    except dbus.DBusException as exc:
        print(f"Could not reach the daemon: {exc.get_dbus_message()}", file=sys.stderr)
        print("Start it with: systemctl --user start bazzite-screenshot", file=sys.stderr)
        return 1
