"""The Insane mode control — the one big button on the screen.

Auto Apply used to be a checkbox in a banner, which is the wrong weight for
the only thing in Sushi that acts on its own. This is the whole page's centre
of gravity: a 216px disc you arm deliberately, dark when idle and the design's
one saturated colour when it is running, with rings going out from it so an
armed state is obvious from across the room.

The colours are the earlier design's: idle is the ink the design uses for its primary
button, armed is `REC` — the red the earlier design reserves for "recording right now", which
is the closest thing it has to "something is happening without you".
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF, QRadialGradient
from PyQt6.QtWidgets import QAbstractButton, QHBoxLayout, QWidget

from . import theme


class InsaneButton(QAbstractButton):
    """A big circular arm/disarm switch.

    Checkable: ``toggled`` carries the new state. The caller owns persistence —
    the button only renders what it is told and reports the click.
    """

    armed_changed = pyqtSignal(bool)

    def __init__(self, diameter: int = 216) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(diameter + 56, diameter + 56)  # room for the rings
        self._d = diameter
        self._t = 0.0
        self._hover = False
        self._caption = "Arm it and Sushi applies on its own"
        self._armed_caption = "Tap to stop"

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.toggled.connect(self._on_toggled)

    # --- state ----------------------------------------------------------------

    def set_caption(self, text: str) -> None:
        self._caption = text
        self.update()

    def _on_toggled(self, on: bool) -> None:
        # The rings only animate while armed; an idle window should not be
        # repainting a 270px widget 20 times a second forever.
        if on:
            self._timer.start(50)
        else:
            self._timer.stop()
            self._t = 0.0
        self.armed_changed.emit(on)
        self.update()

    def _tick(self) -> None:
        self._t += 0.05
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    # --- painting -------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        armed = self.isChecked()
        base = QColor(theme.REC if armed else theme.INK)
        r = self._d / 2 * (1.015 if self._hover else 1.0)

        p.setPen(Qt.PenStyle.NoPen)

        if armed:
            # Three rings on a staggered loop, each fading as it grows.
            for i in range(3):
                phase = (self._t * 0.45 + i / 3) % 1.0
                ring_r = r + phase * 30
                colour = QColor(base)
                colour.setAlpha(int(90 * (1 - phase)))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(colour)
                p.drawEllipse(QPointF(cx, cy), ring_r, ring_r)
            p.setPen(Qt.PenStyle.NoPen)

        # Soft contact shadow, so the disc sits on the page rather than in it.
        shadow = QColor(0, 0, 0, 34)
        p.setBrush(shadow)
        p.drawEllipse(QPointF(cx, cy + r * 0.10), r * 0.98, r * 0.98)

        grad = QRadialGradient(QPointF(cx - r * 0.3, cy - r * 0.35), r * 1.7)
        grad.setColorAt(0.0, base.lighter(132))
        grad.setColorAt(0.6, base)
        grad.setColorAt(1.0, base.darker(132))
        p.setBrush(grad)
        p.drawEllipse(QPointF(cx, cy), r, r)

        # Inner hairline — the design puts an edge on every filled surface.
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QColor(255, 255, 255, 38))
        p.drawEllipse(QPointF(cx, cy), r - 1.5, r - 1.5)
        p.setPen(Qt.PenStyle.NoPen)

        # Bolt glyph.
        p.setPen(Qt.PenStyle.NoPen)
        glyph = QColor("#F7F8EF")
        if armed:
            glyph.setAlpha(int(205 + 50 * math.sin(self._t * 2.4)))
        p.setBrush(glyph)
        p.drawPolygon(self._bolt(cx, cy - r * 0.18, r * 0.40))

        # Labels.
        p.setPen(QColor("#F7F8EF"))
        p.setFont(theme.body_font(19, 600))
        # A verb, not a title. The disc said "INSANE MODE" under the page
        # heading "Insane mode", so the one control on the screen read as a
        # badge for the feature rather than the switch that turns it on —
        # and the word for turning it off is the app's own word for it
        # everywhere else: armed, disarm, "nothing will be sent on its own".
        p.drawText(QRectF(cx - r, cy + r * 0.18, r * 2, 26),
                   Qt.AlignmentFlag.AlignCenter,
                   "DISARM" if armed else "ARM")

        sub = QColor("#F7F8EF")
        sub.setAlpha(165)
        p.setPen(sub)
        p.setFont(theme.body_font(13, 500))
        p.drawText(QRectF(cx - r * 0.86, cy + r * 0.46, r * 1.72, 40),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop |
                   Qt.TextFlag.TextWordWrap,
                   # Always the caller's caption. This used to be hard-coded
                   # to "Running — tap to stop" whenever the button was down,
                   # so it reported a run that had already found nothing to do
                   # and stopped.
                   self._caption)
        p.end()

    @staticmethod
    def _bolt(cx: float, cy: float, s: float) -> QPolygonF:
        """A lightning bolt, as points on a unit shape scaled by ``s``."""
        pts = [(0.18, -1.0), (-0.62, 0.12), (-0.06, 0.12), (-0.20, 1.0),
               (0.62, -0.12), (0.04, -0.12)]
        return QPolygonF([QPointF(cx + x * s, cy + y * s) for x, y in pts])


# --- window chrome ----------------------------------------------------------
#
# The earlier design draws its own title bar. The numbers below are its numbers, not
# approximations of them: a 42x34 hit area with a 9px radius, 3px apart, and a
# 14px glyph struck at 1.2 in #3A3A36 inside a 12-unit box. Hovering a button
# washes it #E7E7DF; hovering Close turns it #D64B3F with a white glyph.

BTN_W, BTN_H = 42, 34
BTN_RADIUS = 9
BTN_GAP = 3
GLYPH_PX = 14
GLYPH_BOX = 12          # the SVG viewBox the paths were authored in
GLYPH_STROKE = 1.2
GLYPH_INK = "#3A3A36"
HOVER_BG = "#E7E7DF"
CLOSE_BG = "#D64B3F"


class WindowButton(QAbstractButton):
    """One of minimise / maximise / close, drawn rather than styled.

    QSS cannot draw a 1.2px rounded stroke, and a font glyph would not match
    the design's weight at any size, so these are painted from the same path
    data the design exports.
    """

    def __init__(self, kind: str, ink: str = GLYPH_INK, hover: str = HOVER_BG,
                 ground: str = theme.BG) -> None:
        super().__init__()
        self.kind = kind                      # "min" | "max" | "close"
        self._ink = ink
        self._hover_bg = hover
        self._ground = ground                 # what the restore glyph occludes with
        self._maximised = False
        self._hover = False
        self.setFixedSize(BTN_W, BTN_H)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setToolTip({"min": "Minimise", "max": "Maximise",
                         "close": "Close"}[kind])

    def set_maximised(self, on: bool) -> None:
        """Show the restore glyph instead of the maximise one, and vice versa."""
        self._maximised = on
        self.setToolTip("Restore" if on else "Maximise")
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        danger = self.kind == "close" and self._hover
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(CLOSE_BG if danger else self._hover_bg))
            p.drawRoundedRect(QRectF(0, 0, BTN_W, BTN_H), BTN_RADIUS, BTN_RADIUS)

        # Map the 12-unit authoring box onto a 14px glyph, centred.
        scale = GLYPH_PX / GLYPH_BOX
        p.translate((BTN_W - GLYPH_PX) / 2, (BTN_H - GLYPH_PX) / 2)
        p.scale(scale, scale)

        pen = QPen(QColor("#FFFFFF" if danger else self._ink))
        pen.setWidthF(GLYPH_STROKE / scale)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        if self.kind == "min":
            p.drawLine(QPointF(1.5, 6), QPointF(10.5, 6))
        elif self.kind == "max":
            if self._maximised:
                # The back pane, then the front one filled with the button's
                # own ground so the overlap reads as "behind".
                p.drawRoundedRect(QRectF(3.9, 1.1, 7, 7), 1.5, 1.5)
                p.setBrush(QColor(CLOSE_BG if danger else
                                  (self._hover_bg if self._hover else self._ground)))
                p.drawRoundedRect(QRectF(1.4, 3.3, 7, 7), 1.5, 1.5)
            else:
                p.drawRoundedRect(QRectF(1.9, 1.9, 8.2, 8.2), 1.7, 1.7)
        else:
            p.drawLine(QPointF(2.4, 2.4), QPointF(9.6, 9.6))
            p.drawLine(QPointF(9.6, 2.4), QPointF(2.4, 9.6))
        p.end()


class WindowButtons(QWidget):
    """The three of them, spaced the way the design spaces them."""

    def __init__(self, on_minimise, on_maximise, on_close,
                 ink: str = GLYPH_INK, hover: str = HOVER_BG,
                 ground: str = theme.BG) -> None:
        super().__init__()
        self.setStyleSheet("background: transparent;")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 0, 0)
        row.setSpacing(BTN_GAP)
        self.min_btn = WindowButton("min", ink, hover, ground)
        self.max_btn = WindowButton("max", ink, hover, ground)
        self.close_btn = WindowButton("close", ink, hover, ground)
        self.min_btn.clicked.connect(on_minimise)
        self.max_btn.clicked.connect(on_maximise)
        self.close_btn.clicked.connect(on_close)
        for b in (self.min_btn, self.max_btn, self.close_btn):
            row.addWidget(b)

    def set_maximised(self, on: bool) -> None:
        self.max_btn.set_maximised(on)


class WindowDrag:
    """Makes a widget the window's drag handle. Mix in *before* the QWidget base.

    ``startSystemMove`` is preferred over moving the window by hand: it hands
    the drag to the compositor, so Aero Snap, the snap-layouts flyout and
    cross-monitor DPI changes all keep working. The manual fallback covers
    platforms where it returns False.

    Only presses no child consumed arrive here, so buttons and inputs in the
    bar keep their clicks; labels ignore mouse events and fall through to a
    drag, which is what you want from a title bar.

    The host must expose ``window_host`` — the MainWindow to move.
    """

    window_host = None

    def _host(self):
        return self.window_host or self.window()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        host = self._host()
        handle = host.windowHandle()
        self._drag_from = None
        if handle is not None and not host.isMaximized() and handle.startSystemMove():
            return
        self._drag_from = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):  # noqa: N802
        if getattr(self, "_drag_from", None) is None:
            return super().mouseMoveEvent(event)
        now = event.globalPosition().toPoint()
        delta = now - self._drag_from
        self._drag_from = now
        self._host().drag_window_by(delta.x(), delta.y())

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._drag_from = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._host().toggle_maximise()
