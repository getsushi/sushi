"""The splash — the first second, before the window exists.

Sushi's cold start is not instant: the database opens, the display face is
registered, the stylesheet is rebuilt against whatever actually loaded, and
the main window builds six pages before it can be shown. On this machine that
is roughly a second, and a second of nothing on screen after a double-click
reads as a failed launch.

So: the mascot, the wordmark in the title face, a thin gold line that keeps
moving, and a line naming what is happening, in the same light, flat style
as the rest of the app.

Deliberately not ``QSplashScreen``. That paints a pixmap and nothing else —
no layout, no rounded corners, no live status line — and getting a starfield
and a progress line into one means fighting it the whole way.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

from . import theme
from .brand import paint_logo

#: The mascot, large enough to show its face.
MARK = 88

#: Seconds the splash stays up, however fast the window was ready. Long
#: enough to read, short enough not to feel like waiting.
MIN_VISIBLE = 2.0


class Mark(QWidget):
    """The mascot, painted large."""

    def __init__(self, size: int = MARK) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self._size = size
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        paint_logo(p, 0, 0, float(self._size))
        p.end()


class GoldBar(QWidget):
    """The wireframe's thin gold progress line, sliding while work runs.

    ``_t`` advances every frame; the QA sweep reads it to prove the splash
    keeps moving while it holds.
    """

    def __init__(self, width: int = 220) -> None:
        super().__init__()
        self.setFixedSize(width, 4)
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def _tick(self) -> None:
        self._t = (self._t + 0.012) % 1.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        w, h = float(self.width()), float(self.height())
        p.setBrush(QColor(theme.LINE))
        p.drawRoundedRect(QRectF(0, 0, w, h), 2, 2)
        seg = w * 0.38
        x = -seg + (w + seg) * self._t
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(QRectF(max(0.0, x), 0, min(seg, w - x, seg + x), h), 2, 2)
        p.end()


class Splash(QWidget):
    """A light card in the wireframe's style, shown while the app builds."""

    def __init__(self, width: int = 420, height: int = 320) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.SplashScreen
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(width, height)

        self._shown_at: float | None = None
        self._window: QWidget | None = None
        self._ticker: QTimer | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("SplashCard")
        card.setStyleSheet(
            f"QFrame#SplashCard{{background:{theme.SURFACE};"
            f"border:1px solid {theme.LINE_2};border-radius:{theme.R_CARD + 2}px;}}"
            "QFrame#SplashCard QLabel{background:transparent;border:none;}")
        root.addWidget(card)
        body = QVBoxLayout(card)
        body.setContentsMargins(0, 36, 0, 28)
        body.setSpacing(0)
        body.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        body.addStretch(1)
        body.addWidget(Mark(), 0, Qt.AlignmentFlag.AlignHCenter)
        body.addSpacing(14)
        word = QLabel("Sushi")
        word.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        word.setStyleSheet(f"color:{theme.INK};font-family:'{theme.DISPLAY_FONT}';"
                           "font-size:44px;font-weight:500;")
        body.addWidget(word)
        tag = QLabel("Upload your CV once. We find the jobs and apply for you.")
        tag.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        tag.setStyleSheet(f"color:{theme.MUTED};font-size:12px;")
        body.addWidget(tag)
        body.addStretch(1)

        self._panel = GoldBar()
        body.addWidget(self._panel, 0, Qt.AlignmentFlag.AlignHCenter)
        body.addSpacing(12)
        self.status = QLabel("Starting…")
        self.status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.status.setStyleSheet(f"color:{theme.FAINT};font-size:12px;")
        body.addWidget(self.status)
        self._centre()

    # --- geometry ------------------------------------------------------------

    def _centre(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2,
                  area.center().y() - self.height() // 2)

    def paintEvent(self, _event) -> None:  # noqa: N802
        """Clip the translucent window to the panel's own corner radius.

        Without this the panel paints its rounded card and the window still
        has square corners, which on Windows shows as four dark pips.
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(self.width()), float(self.height()),
                            theme.R_CARD + 2, theme.R_CARD + 2)
        p.setClipPath(path)
        p.end()

    # --- use -----------------------------------------------------------------

    def step(self, text: str) -> None:
        """Name what is happening, and let it actually reach the screen."""
        self.status.setText(text if text.endswith(("…", ".")) else text + "…")
        QApplication.processEvents()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._shown_at is None:
            self._shown_at = time.monotonic()

    def _held_for(self) -> float:
        """Seconds the splash has actually been on screen."""
        if self._shown_at is None:
            return 0.0
        return time.monotonic() - self._shown_at

    def finish(self, window: QWidget | None = None) -> None:
        """Hand over to the real window, but not before :data:`MIN_VISIBLE`.

        The window is usually ready in about a second, and a splash that
        appears and vanishes inside one reads as a flicker — you register that
        *something* happened without ever seeing what. So the handover waits
        out the remainder of four seconds.

        It waits on a timer rather than sleeping. A sleep would freeze the
        starfield and the status line for the whole hold, which is a worse
        four seconds than no splash at all: the app would look hung at exactly
        the moment it is trying to look alive.
        """
        self._window = window
        remaining = MIN_VISIBLE - self._held_for()
        if remaining <= 0:
            self._hand_over()
            return
        # Keep the line honest while we wait: by now the window exists and may
        # already be searching, so say which.
        self._ticker = QTimer(self)
        self._ticker.timeout.connect(self._narrate)
        self._ticker.start(500)
        self._narrate()
        QTimer.singleShot(int(remaining * 1000), self._hand_over)

    def _narrate(self) -> None:
        """Report what the not-yet-visible window is doing."""
        window = self._window
        scanning = bool(window is not None and getattr(window, "scanning", None)
                        and window.scanning())
        self.step("Looking for jobs that match your CV" if scanning else "Ready.")

    def _hand_over(self) -> None:
        """Show the window, then close.

        The 120ms delay is not decoration: closing the splash in the same tick
        the main window is shown leaves a frame where neither is on screen,
        and the app looks like it blinked.
        """
        if self._ticker is not None:
            self._ticker.stop()
            self._ticker = None
        window, self._window = self._window, None
        if window is not None:
            window.show()
            window.raise_()
            window.activateWindow()
        QTimer.singleShot(120, self.close)
