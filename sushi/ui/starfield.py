"""The starry header panel.

The earlier design's one piece of decoration is a deep diagonal panel sitting under
the biggest statement on a screen. Here it gains the starfield: a scatter of
faint specks and a handful of four-point sparkles over the same gradient,
which is what makes a header read as a *sky* rather than a coloured box.

The gradient itself is :data:`theme.HERO_FROM` and its two deeper stops —
olive once, then clay, and blue now that a starfield is what stands on it.
Nothing here names a colour, so that stays a decision taken in one place.

Everything is drawn with QPainter — no image assets — and the scatter is
generated from a fixed seed per panel, so a repaint never reshuffles the stars.
Twinkling is opt-in: it costs a timer and a repaint, which is worth it for the
one hero on a screen and not for a row of cards. So is parallax — the earlier design's sky
drifts with the pointer, the near stars further than the far ones, which is
what gives a flat gradient any depth at all.
"""
from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (QBrush, QColor, QCursor, QLinearGradient, QPainter,
                         QPainterPath)
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from . import theme


class _Star:
    __slots__ = ("x", "y", "r", "alpha", "phase", "speed", "sparkle", "layer")

    def __init__(self, x: float, y: float, r: float, alpha: int,
                 phase: float, speed: float, sparkle: bool,
                 layer: float) -> None:
        self.x, self.y, self.r, self.alpha = x, y, r, alpha
        self.phase, self.speed, self.sparkle = phase, speed, sparkle
        #: 0 = far away, 1 = close. Only parallax uses it: a field that
        #: slides as one sheet reads as a texture being dragged, and the
        #: difference in how far each star travels is the whole effect.
        self.layer = layer


class StarPanel(QWidget):
    """A rounded dark-olive panel with a starfield, which hosts a layout.

    Add content with ``panel.body`` (a QVBoxLayout). Children need a
    transparent background to let the sky through; :func:`transparent` does
    that for plain container widgets, and the stylesheet already handles
    QLabel.
    """

    def __init__(self, radius: int = theme.R_CARD, seed: int = 7,
                 density: float = 1.0, animate: bool = False,
                 padding: tuple[int, int, int, int] = (30, 28, 30, 28),
                 parallax: float = 0.0) -> None:
        super().__init__()
        self._radius = radius
        self._seed = seed
        self._density = density
        self._parallax = parallax
        self._stars: list[_Star] = []
        self._star_size = (0, 0)
        self._t = 0.0
        #: Where the field is, and where the pointer says it should be, each
        #: as -1..1 across the panel.
        self._pan = [0.0, 0.0]

        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*padding)
        self.body.setSpacing(11)

        if animate:
            # 11 fps. The twinkle is meant to be noticed only if you look for
            # it, and a slow timer keeps an idle window off the CPU.
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(90)

        if parallax:
            # Polled rather than driven by mouse events: the sky sits *behind*
            # a layout full of labels and buttons, and those children eat the
            # moves, so a panel with mouse tracking on hears almost nothing.
            # 60fps, not 30: at 30 a field this size arrives in visible
            # stepping rather than a drift, which is what "subtle" was really
            # describing.
            self._ptimer = QTimer(self)
            self._ptimer.timeout.connect(self._follow_cursor)
            self._ptimer.start(16)

    # --- stars --------------------------------------------------------------

    def _tick(self) -> None:
        self._t += 0.09
        self.update()

    def _follow_cursor(self) -> None:
        """Ease the field towards the pointer, and repaint if it moved.

        Eased rather than tracked: at 30fps a field pinned to the cursor
        twitches with every jitter of the hand, and it is the lag that reads
        as distance. Off-panel — and in a window that is not the active one —
        it settles back to centre rather than staying wherever the pointer
        left it.
        """
        w, h = self.width(), self.height()
        if not self.isVisible() or w <= 0 or h <= 0:
            return
        p = self.mapFromGlobal(QCursor.pos())
        if self.isActiveWindow() and 0 <= p.x() <= w and 0 <= p.y() <= h:
            target = ((p.x() / w - 0.5) * 2.0, (p.y() / h - 0.5) * 2.0)
        else:
            target = (0.0, 0.0)

        before = tuple(self._pan)
        self._pan[0] += (target[0] - self._pan[0]) * 0.10
        self._pan[1] += (target[1] - self._pan[1]) * 0.10
        moved = (abs(self._pan[0] - before[0]) + abs(self._pan[1] - before[1]))
        # Sub-pixel moves are a repaint nobody can see. Without this the panel
        # keeps redrawing for a second after the pointer stops, chasing the
        # last thousandth of the ease.
        if moved * self._parallax > 0.15:
            self.update()

    def _ensure_stars(self, w: int, h: int) -> None:
        """(Re)generate the scatter for this size, from the panel's own seed.

        Seeded rather than random so that a resize produces a stable-looking
        sky and a repaint produces an identical one — an unseeded scatter
        flickers into a new constellation on every expose event.
        """
        if self._star_size == (w, h) and self._stars:
            return
        self._star_size = (w, h)
        rng = random.Random(self._seed)
        count = int(w * h / 1700 * self._density)
        # Scattered past the edges by as far as the parallax can shift them,
        # or a panel that drifts left arrives with a bald strip down its side.
        m = self._parallax + 4
        stars: list[_Star] = []
        for _ in range(max(12, count)):
            x = rng.uniform(-m, w + m)
            y = rng.uniform(-m, h + m)
            # Denser and brighter towards the top-right, where the gradient is
            # darkest; a uniform field over a diagonal gradient looks wrong in
            # the bright corner.
            depth = (x / max(w, 1)) * 0.6 + (1 - y / max(h, 1)) * 0.4
            if rng.random() > 0.25 + depth * 0.85:
                continue
            # Brighter and a little bigger than the earlier design's, because clay is a
            # lighter ground than its olive was: the same specks that read as
            # a sky on near-black were almost gone on this gradient.
            sparkle = rng.random() < 0.075
            stars.append(_Star(
                x=x, y=y,
                r=rng.uniform(2.0, 3.8) if sparkle else rng.uniform(0.7, 1.9),
                alpha=int(rng.uniform(75, 150) + depth * 100),
                phase=rng.uniform(0, math.tau),
                speed=rng.uniform(0.5, 1.6),
                sparkle=sparkle,
                # The sparkles are the big ones, so they are the near ones.
                layer=rng.uniform(0.7, 1.0) if sparkle else rng.uniform(0.15, 0.8),
            ))
        self._stars = stars

    # --- painting -----------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        self._ensure_stars(w, h)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), self._radius, self._radius)
        p.setClipPath(clip)

        # The design's 135° gradient: lightest in the top-left, near-black
        # in the bottom-right. Which hue that is belongs to the theme.
        grad = QLinearGradient(0, 0, w, h)
        grad.setColorAt(0.0, QColor(theme.HERO_FROM))
        grad.setColorAt(0.58, QColor(theme.HERO_MID))
        grad.setColorAt(1.0, QColor(theme.HERO_TO))
        p.fillRect(0, 0, w, h, QBrush(grad))

        px = self._pan[0] * self._parallax
        py = self._pan[1] * self._parallax

        p.setPen(Qt.PenStyle.NoPen)
        for s in self._stars:
            a = s.alpha
            if self._t:
                a = int(a * (0.62 + 0.38 * math.sin(self._t * s.speed + s.phase)))
            colour = QColor(255, 250, 246, max(0, min(255, a)))
            x, y = s.x + px * s.layer, s.y + py * s.layer
            if s.sparkle:
                self._sparkle(p, x, y, s.r, colour)
            else:
                p.setBrush(colour)
                p.drawEllipse(QPointF(x, y), s.r, s.r)

        # The hairline the design puts on every surface, dark here.
        p.setClipping(False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QColor(0, 0, 0, 46))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), self._radius, self._radius)
        p.end()

    @staticmethod
    def _sparkle(p: QPainter, x: float, y: float, r: float, colour: QColor) -> None:
        """A four-point star: two tapered lozenges crossed at the centre."""
        path = QPainterPath()
        waist = r * 0.26
        path.moveTo(x, y - r)
        path.quadTo(x + waist, y - waist, x + r, y)
        path.quadTo(x + waist, y + waist, x, y + r)
        path.quadTo(x - waist, y + waist, x - r, y)
        path.quadTo(x - waist, y - waist, x, y - r)
        p.setBrush(colour)
        p.drawPath(path)


class GridPanel(StarPanel):
    """The same dark panel, textured with a regular dot lattice.

    A starfield says "sky", which is right for the one control that runs on
    its own and wrong for a dashboard banner. A lattice says "surface", and
    being regular it sits behind text without competing with it.
    """

    def __init__(self, radius: int = theme.R_CARD,
                 padding: tuple[int, int, int, int] = (30, 28, 30, 28),
                 spacing: int = 13) -> None:
        super().__init__(radius=radius, animate=False, padding=padding)
        self._dot_spacing = spacing

    def paintEvent(self, _event) -> None:  # noqa: N802
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), self._radius, self._radius)
        p.setClipPath(clip)

        grad = QLinearGradient(0, 0, w, h)
        grad.setColorAt(0.0, QColor(theme.HERO_FROM))
        grad.setColorAt(0.58, QColor(theme.HERO_MID))
        grad.setColorAt(1.0, QColor(theme.HERO_TO))
        p.fillRect(0, 0, w, h, QBrush(grad))

        step = self._dot_spacing
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(247, 248, 239, 30))
        y = step
        while y < h:
            x = step
            while x < w:
                p.drawEllipse(QPointF(x, y), 1.05, 1.05)
                x += step
            y += step

        p.setClipping(False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QColor(0, 0, 0, 46))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), self._radius, self._radius)
        p.end()


class StarStrip(StarPanel):
    """The square-cornered, full-bleed variant used behind onboarding."""

    def __init__(self, height: int = 132, seed: int = 21, animate: bool = True,
                 parallax: float = 0.0) -> None:
        super().__init__(radius=0, seed=seed, density=1.25, animate=animate,
                         padding=(32, 22, 32, 22), parallax=parallax)
        self.setFixedHeight(height)


#: Re-exported: this used to live here, and three modules import it from
#: here. It is a general container helper, not a starfield one, so it moved
#: to ``components`` once the rest of the app started needing it.
from .components import transparent  # noqa: E402,F401  (re-export)
