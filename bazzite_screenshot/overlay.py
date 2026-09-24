"""Frozen-screen selection overlay: one fullscreen window per monitor."""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QRegion,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from .capture import ScreenShot, WindowInfo

ACCENT = QColor(61, 174, 233)
DIM = QColor(0, 0, 0, 120)
HANDLE = 8
GRAB_MARGIN = 8
DRAG_THRESHOLD = 5
MIN_SIZE = 3

HANDLE_CURSORS = {
    "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
    "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
    "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
    "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
    "move": Qt.SizeAllCursor,
}

TOOLBAR_STYLE = """
QFrame#toolbar {
    background: rgba(32, 35, 38, 235);
    border: 1px solid rgba(255, 255, 255, 40);
    border-radius: 10px;
}
QPushButton, QComboBox {
    color: #eff0f1;
    background: rgba(255, 255, 255, 18);
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 10pt;
}
QPushButton:hover, QComboBox:hover { background: rgba(255, 255, 255, 36); }
QPushButton:checked { background: rgba(61, 174, 233, 90); border-color: #3daee9; }
QPushButton#confirm { background: #3daee9; border-color: #3daee9; color: white; font-weight: bold; }
QPushButton#confirm:hover { background: #56bdf0; }
QPushButton#confirm:disabled { background: rgba(61, 174, 233, 60); color: rgba(255, 255, 255, 120); }
QComboBox { min-width: 240px; }
QComboBox QAbstractItemView {
    color: #eff0f1; background: #232629; selection-background-color: #3daee9;
}
"""


def edges(r: QRect) -> tuple[int, int, int, int]:
    return r.x(), r.y(), r.x() + r.width(), r.y() + r.height()


def from_edges(left: int, top: int, right: int, bottom: int) -> QRect:
    x0, x1 = sorted((left, right))
    y0, y1 = sorted((top, bottom))
    return QRect(x0, y0, x1 - x0, y1 - y0)


class Controller(QObject):
    """Selection state shared by all per-screen overlays."""

    finished = Signal(object)  # QImage on confirm, None on cancel

    def __init__(self, shots: list[ScreenShot]) -> None:
        super().__init__()
        self.shots = shots
        self.desktop = QRect()
        for s in shots:
            self.desktop = self.desktop.united(s.geometry)

        self.mode = "region"
        self.selection: QRect | None = None
        self.windows: list[WindowInfo] = []
        self.hover: WindowInfo | None = None
        self.picked: WindowInfo | None = None
        self.dragging = False
        self._drag_kind: str | None = None
        self._press: QPoint | None = None
        self._orig: QRect | None = None
        self._done = False
        self._pointer_seen = False

        self.overlays = [Overlay(self, s) for s in shots]
        cursor_screen = QGuiApplication.screenAt(QCursor.pos())
        self.active = next(
            (o for o in self.overlays if o.shot.screen is cursor_screen), self.overlays[0]
        )

    # ------------------------------------------------------------------ lifecycle
    def show(self) -> None:
        for o in self.overlays:
            o.show_on_screen()
        self.active.activateWindow()
        self.active.raise_()
        self.changed()

    def close(self) -> None:
        for o in self.overlays:
            o.close()
            o.deleteLater()
        self.overlays = []

    def confirm(self) -> None:
        if self._done or not self.selection or self.selection.isEmpty():
            return
        self._done = True
        self.finished.emit(self.compose(self.selection))

    def cancel(self) -> None:
        if self._done:
            return
        self._done = True
        self.finished.emit(None)

    # ------------------------------------------------------------------ windows
    def set_windows(self, windows: list[WindowInfo], cursor: QPoint | None = None) -> None:
        own = os.getpid()
        self.windows = [w for w in windows if w.pid != own]
        if cursor is not None and not self._pointer_seen:
            self.active = next(
                (o for o in self.overlays if o.shot.geometry.contains(cursor)), self.active
            )
        for o in self.overlays:
            o.toolbar.populate(self.targets())
        self.changed()

    def screen_targets(self) -> list[WindowInfo]:
        targets = [
            WindowInfo(f"Entire screen: {s.screen.name()}", "", 0, QRect(s.geometry), -1)
            for s in self.shots
        ]
        if len(self.shots) > 1:
            targets.append(WindowInfo("All screens", "", 0, QRect(self.desktop), -2))
        return targets

    def targets(self) -> list[WindowInfo]:
        return sorted(self.windows, key=lambda w: -w.z) + self.screen_targets()

    def window_at(self, p: QPoint) -> WindowInfo | None:
        hits = [w for w in self.windows if w.rect.contains(p)]
        if hits:
            return max(hits, key=lambda w: w.z)
        for s in self.screen_targets()[: len(self.shots)]:
            if s.rect.contains(p):
                return s
        return None

    def pick(self, target: WindowInfo) -> None:
        self.mode = "window"
        self.picked = target
        self.hover = target
        self.selection = target.rect.intersected(self.desktop)
        self.changed()

    # ------------------------------------------------------------------ modes
    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if mode == "region":
            self.hover = None
        self.changed()

    def bright_rect(self) -> QRect | None:
        if self.mode == "window" and self.hover and not self.dragging:
            return self.hover.rect.intersected(self.desktop)
        return self.selection

    def output_scale(self, rect: QRect) -> float:
        scales = [s.scale for s in self.shots if s.geometry.intersects(rect)]
        return max(scales) if scales else 1.0

    def output_size(self, rect: QRect) -> tuple[int, int]:
        scale = self.output_scale(rect)
        return round(rect.width() * scale), round(rect.height() * scale)

    # ------------------------------------------------------------------ composition
    def compose(self, rect: QRect) -> QImage:
        rect = rect.intersected(self.desktop)
        involved = [s for s in self.shots if s.geometry.intersects(rect)]

        def src_rect(s: ScreenShot, area: QRect) -> QRectF:
            return QRectF(
                (area.x() - s.geometry.x()) * s.scale,
                (area.y() - s.geometry.y()) * s.scale,
                area.width() * s.scale,
                area.height() * s.scale,
            )

        if len(involved) == 1:
            s = involved[0]
            return s.image.copy(src_rect(s, rect).toAlignedRect().intersected(s.image.rect()))

        scale = self.output_scale(rect)
        w, h = self.output_size(rect)
        out = QImage(max(1, w), max(1, h), QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        for s in involved:
            area = rect.intersected(s.geometry)
            dst = QRectF(
                (area.x() - rect.x()) * scale,
                (area.y() - rect.y()) * scale,
                area.width() * scale,
                area.height() * scale,
            )
            painter.drawImage(dst, s.image, src_rect(s, area))
        painter.end()
        return out

    # ------------------------------------------------------------------ input
    def hit_test(self, p: QPoint) -> str | None:
        if not self.selection or self.mode != "region":
            return None
        left, top, right, bottom = edges(self.selection)
        m = GRAB_MARGIN
        if not (left - m <= p.x() <= right + m and top - m <= p.y() <= bottom + m):
            return None
        vertical = "t" if abs(p.y() - top) <= m else "b" if abs(p.y() - bottom) <= m else ""
        horizontal = "l" if abs(p.x() - left) <= m else "r" if abs(p.x() - right) <= m else ""
        if vertical or horizontal:
            return vertical + horizontal
        return "move" if self.selection.contains(p) else None

    def press(self, overlay: "Overlay", p: QPoint, event: QMouseEvent) -> None:
        self.active = overlay
        if event.button() == Qt.RightButton:
            if self.selection:
                self.selection = None
                self.picked = None
                self.changed()
            else:
                self.cancel()
            return
        if event.button() != Qt.LeftButton:
            return

        self._press = QPoint(p)
        self._orig = QRect(self.selection) if self.selection else None
        if self.mode == "window":
            self._drag_kind = "window"
        else:
            self._drag_kind = self.hit_test(p) or "new"
        self.dragging = self._drag_kind != "window"
        if self._drag_kind == "new":
            self.picked = None
            self.selection = QRect(p, p)
        self.changed()

    def move(self, overlay: "Overlay", p: QPoint, buttons) -> None:
        if not (buttons & Qt.LeftButton) or self._press is None:
            self._pointer_seen = True
            if overlay is not self.active:
                self.active = overlay
                self.changed()
            if self.mode == "window":
                target = self.window_at(p)
                if target != self.hover:
                    self.hover = target
                    self.changed()
            else:
                overlay.setCursor(HANDLE_CURSORS.get(self.hit_test(p), Qt.CrossCursor))
            return

        delta = p - self._press
        kind = self._drag_kind
        if kind == "window":
            if delta.manhattanLength() < DRAG_THRESHOLD:
                return
            # Dragging while in window mode starts a free-form region instead.
            self.mode = "region"
            self.hover = None
            self.picked = None
            kind = self._drag_kind = "new"
            self.dragging = True

        if kind == "new":
            self.selection = from_edges(self._press.x(), self._press.y(), p.x(), p.y())
        elif kind == "move" and self._orig:
            moved = self._orig.translated(delta)
            dx = min(0, self.desktop.right() + 1 - (moved.x() + moved.width())) + max(0, self.desktop.x() - moved.x())
            dy = min(0, self.desktop.bottom() + 1 - (moved.y() + moved.height())) + max(0, self.desktop.y() - moved.y())
            self.selection = moved.translated(dx, dy)
        elif kind and self._orig:
            left, top, right, bottom = edges(self._orig)
            if "l" in kind:
                left += delta.x()
            if "r" in kind:
                right += delta.x()
            if "t" in kind:
                top += delta.y()
            if "b" in kind:
                bottom += delta.y()
            self.selection = from_edges(left, top, right, bottom)
        if self.selection:
            self.selection = self.selection.intersected(self.desktop)
        self.changed()

    def release(self, overlay: "Overlay", p: QPoint, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton or self._press is None:
            return
        if self._drag_kind == "window":
            target = self.window_at(p)
            if target:
                self.pick(target)
        elif self.selection and (self.selection.width() < MIN_SIZE or self.selection.height() < MIN_SIZE):
            self.selection = self._orig if self._drag_kind != "new" else None
        self._press = None
        self._drag_kind = None
        self.dragging = False
        self.changed()

    def double_click(self, p: QPoint) -> None:
        if self.mode == "window":
            target = self.window_at(p)
            if target:
                self.pick(target)
        if self.selection and self.selection.contains(p):
            self.confirm()

    def key(self, event: QKeyEvent) -> bool:
        key = event.key()
        if key == Qt.Key_Escape:
            self.cancel()
        elif key in (Qt.Key_Return, Qt.Key_Enter) or (
            key == Qt.Key_C and event.modifiers() & Qt.ControlModifier
        ):
            self.confirm()
        elif key == Qt.Key_W:
            self.set_mode("window")
        elif key == Qt.Key_R:
            self.set_mode("region")
        else:
            return False
        return True

    # ------------------------------------------------------------------ repaint
    def toolbar_overlay(self) -> "Overlay":
        if self.selection:
            center = self.selection.center()
            for o in self.overlays:
                if o.shot.geometry.contains(center):
                    return o
            return max(
                self.overlays,
                key=lambda o: (lambda r: r.width() * r.height())(o.shot.geometry.intersected(self.selection)),
            )
        return self.active

    def changed(self) -> None:
        if not self.overlays:
            return
        host = None if self.dragging else self.toolbar_overlay()
        for o in self.overlays:
            o.toolbar.sync()
            if o is host:
                o.place_toolbar()
            else:
                o.toolbar.hide()
            o.update()


class Toolbar(QFrame):
    def __init__(self, ctl: Controller, parent: QWidget) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.setObjectName("toolbar")
        self.setStyleSheet(TOOLBAR_STYLE)
        self.setCursor(Qt.ArrowCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.region_btn = self._button("Region", "select-rectangular", checkable=True)
        self.window_btn = self._button("Window", "window", checkable=True)
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.region_btn)
        group.addButton(self.window_btn)
        self.region_btn.clicked.connect(lambda: ctl.set_mode("region"))
        self.window_btn.clicked.connect(lambda: ctl.set_mode("window"))

        self.combo = QComboBox(self)
        self.combo.setFocusPolicy(Qt.NoFocus)
        self.combo.setMaxVisibleItems(15)
        self.combo.activated.connect(self._combo_activated)
        self._targets: list[WindowInfo] = []
        self.populate(ctl.targets())

        self.confirm_btn = self._button("Copy", "edit-copy")
        self.confirm_btn.setObjectName("confirm")
        self.confirm_btn.setToolTip("Copy the selection to the clipboard (Enter)")
        self.confirm_btn.clicked.connect(ctl.confirm)
        cancel_btn = self._button("Cancel", "dialog-cancel")
        cancel_btn.setToolTip("Discard (Esc)")
        cancel_btn.clicked.connect(ctl.cancel)

        for w in (self.region_btn, self.window_btn, self.combo, self.confirm_btn, cancel_btn):
            layout.addWidget(w)
        self.hide()

    def _button(self, text: str, icon: str, checkable: bool = False) -> QPushButton:
        btn = QPushButton(QIcon.fromTheme(icon), text, self)
        btn.setCheckable(checkable)
        btn.setFocusPolicy(Qt.NoFocus)
        return btn

    def populate(self, targets: list[WindowInfo]) -> None:
        self._targets = targets
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(QIcon.fromTheme("window"), "Select an application...")
        for t in targets:
            icon = QIcon.fromTheme(t.resource_class.lower()) if t.resource_class else QIcon.fromTheme("monitor")
            self.combo.addItem(icon, t.label)
        self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)

    def _combo_activated(self, index: int) -> None:
        if 1 <= index <= len(self._targets):
            self.ctl.pick(self._targets[index - 1])

    def sync(self) -> None:
        self.region_btn.setChecked(self.ctl.mode == "region")
        self.window_btn.setChecked(self.ctl.mode == "window")
        self.confirm_btn.setEnabled(bool(self.ctl.selection and not self.ctl.selection.isEmpty()))
        picked = self.ctl.picked
        index = self._targets.index(picked) + 1 if picked in self._targets else 0
        if self.combo.currentIndex() != index:
            self.combo.blockSignals(True)
            self.combo.setCurrentIndex(index)
            self.combo.blockSignals(False)


class Overlay(QWidget):
    def __init__(self, ctl: Controller, shot: ScreenShot) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.ctl = ctl
        self.shot = shot
        self.pixmap = QPixmap.fromImage(shot.image)
        self.setWindowTitle("BazziteScreenshot")
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.toolbar = Toolbar(ctl, self)

    def show_on_screen(self) -> None:
        # The native window must exist before its screen is set, otherwise
        # Qt asks KWin to fullscreen it on whichever output is active.
        self.create()
        self.windowHandle().setScreen(self.shot.screen)
        self.setGeometry(self.shot.geometry)
        self.showFullScreen()

    def to_global(self, event: QMouseEvent) -> QPoint:
        return event.position().toPoint() + self.shot.geometry.topLeft()

    def local(self, rect: QRect) -> QRect:
        return rect.translated(-self.shot.geometry.topLeft())

    # ------------------------------------------------------------------ events
    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.ctl.press(self, self.to_global(event), event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.ctl.move(self, self.to_global(event), event.buttons())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.ctl.release(self, self.to_global(event), event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.ctl.double_click(self.to_global(event))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self.ctl.key(event):
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        if not self.ctl._done:
            self.ctl.cancel()
        super().closeEvent(event)

    # ------------------------------------------------------------------ layout
    def place_toolbar(self) -> None:
        tb = self.toolbar
        tb.adjustSize()
        size = tb.sizeHint()
        w, h = self.width(), self.height()
        sel = self.ctl.selection
        if sel and sel.intersects(self.shot.geometry):
            r = self.local(sel).intersected(self.rect())
            x = r.center().x() - size.width() // 2
            if r.bottom() + 12 + size.height() < h:
                y = r.bottom() + 12
            elif r.top() - 12 - size.height() > 0:
                y = r.top() - 12 - size.height()
            else:
                y = r.bottom() - 12 - size.height()
        else:
            x = (w - size.width()) // 2
            y = 24
        x = max(8, min(x, w - size.width() - 8))
        y = max(8, min(y, h - size.height() - 8))
        tb.setGeometry(x, y, size.width(), size.height())
        tb.show()
        tb.raise_()

    # ------------------------------------------------------------------ painting
    def paintEvent(self, event: QPaintEvent) -> None:
        ctl = self.ctl
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(self.rect(), self.pixmap)

        dim = QRegion(self.rect())
        bright = ctl.bright_rect()
        if bright:
            dim -= QRegion(self.local(bright))
        p.setClipRegion(dim)
        p.fillRect(self.rect(), DIM)
        p.setClipping(False)
        p.setRenderHint(QPainter.Antialiasing)

        sel = ctl.selection
        if sel and sel.intersects(self.shot.geometry.adjusted(-2, -2, 2, 2)):
            r = self.local(sel)
            p.setPen(QPen(ACCENT, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            if ctl.mode == "region" and not ctl.dragging:
                self._draw_handles(p, r)
            w, h = ctl.output_size(sel)
            self._draw_label(p, r, f"{w} \u00d7 {h}")

        hover = ctl.hover
        if ctl.mode == "window" and hover and not ctl.dragging:
            hr = hover.rect.intersected(ctl.desktop)
            if hr != sel and hr.intersects(self.shot.geometry):
                r = self.local(hr)
                pen = QPen(ACCENT, 2, Qt.DashLine)
                p.setPen(pen)
                p.setBrush(QColor(61, 174, 233, 25))
                p.drawRect(r.adjusted(1, 1, -1, -1))
                self._draw_label(p, r, hover.label, inside=True)

        if not sel and self.toolbar.isVisible():
            self._draw_hint(p)
        p.end()

    def _draw_handles(self, p: QPainter, r: QRect) -> None:
        p.setPen(QPen(Qt.white, 1))
        p.setBrush(ACCENT)
        cx, cy = r.center().x(), r.center().y()
        left, top, right, bottom = r.x(), r.y(), r.x() + r.width(), r.y() + r.height()
        for x, y in ((left, top), (cx, top), (right, top), (right, cy),
                     (right, bottom), (cx, bottom), (left, bottom), (left, cy)):
            p.drawEllipse(QPoint(x, y), HANDLE // 2 + 1, HANDLE // 2 + 1)

    def _draw_label(self, p: QPainter, r: QRect, text: str, inside: bool = False) -> None:
        font = QFont(self.font())
        font.setPointSizeF(10)
        font.setBold(True)
        fm = QFontMetrics(font)
        text = fm.elidedText(text, Qt.ElideRight, max(120, min(r.width(), 600)))
        box = QRect(0, 0, fm.horizontalAdvance(text) + 16, fm.height() + 8)
        top_left = QPoint(r.x(), r.y() - box.height() - 6)
        if inside or top_left.y() < 0:
            top_left = QPoint(r.x() + 6, r.y() + 6)
        box.moveTopLeft(top_left)
        box.moveLeft(max(4, min(box.x(), self.width() - box.width() - 4)))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(32, 35, 38, 220))
        p.drawRoundedRect(box, 5, 5)
        p.setFont(font)
        p.setPen(Qt.white)
        p.drawText(box, Qt.AlignCenter, text)

    def _draw_hint(self, p: QPainter) -> None:
        if self.ctl.mode == "window":
            text = "Click a window to select it  \u2022  drag for a region  \u2022  Enter copies  \u2022  Esc cancels"
        else:
            text = "Drag to select a region  \u2022  W picks a window  \u2022  Enter copies  \u2022  Esc cancels"
        font = QFont(self.font())
        font.setPointSizeF(11)
        fm = QFontMetrics(font)
        box = QRect(0, 0, fm.horizontalAdvance(text) + 32, fm.height() + 16)
        tb = self.toolbar.geometry()
        box.moveCenter(QPoint(self.width() // 2, tb.bottom() + 16 + box.height() // 2))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(32, 35, 38, 200))
        p.drawRoundedRect(box, 8, 8)
        p.setFont(font)
        p.setPen(QColor(239, 240, 241))
        p.drawText(box, Qt.AlignCenter, text)
