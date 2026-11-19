"""The Sushi mascot — a piece of nigiri with a face.

The reference was a stack of soft clay toys, the bottom one a coral smiley.
Taking that literally gave a friendly blob that could have belonged to any
app. Nigiri keeps the same character — the same squishy proportions, the same
two dots and a wide arc — while actually being the thing the app is named
after: a salmon slice draped over a bed of rice, with the face on the salmon.

The depth is painted rather than faked with a drop shadow:

- a **contact shadow** on the ground, wider than it is tall, so the piece sits
  on the surface instead of floating above it;
- **two bodies**, rice behind and salmon in front, the salmon overhanging at
  both ends the way a real slice does;
- an **occlusion shadow** where the salmon meets the rice, clipped to the
  rice, which is what sells one object resting on another;
- a **specular highlight** up and to the left, and **marbling** — the pale
  curved striations that make salmon read as salmon and not as a coral
  rounded rectangle.

Everything is proportional to the requested size, and the detail drops in
stages as it shrinks: marbling and highlight go first, then the face, so the
18px mark beside the wordmark is still a clean two-tone silhouette.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QColor, QLinearGradient, QPainter, QPainterPath, QPen,
                         QRadialGradient)
from PyQt6.QtWidgets import QWidget

from . import theme

# Coral — orange leaning pink, which is what it was before it was pulled
# onto the app's red and went flat. The ramp is built around theme.ACCENT
# (#C4543C) so the mark and the interface are one colour, not two warm ones
# arguing.
#
# Still three tones, not one. The mascot's depth is painted — a lit top, a
# shaded underside, an occlusion line where the slice meets the rice — and
# flattening it to a single coral would lose the thing that makes it sit on
# the rice instead of beside it.
SALMON_LIGHT = "#F4836A"   # lit face, up and to the left
SALMON_MID = "#E0654A"     # the body of the slice
SALMON_DEEP = "#B8442C"    # the shaded underside and the far edge
MARBLE = "#FFD2C2"         # striations: pale enough to read on the red

# Rice: the app's own cream, shaded.
RICE_LIGHT = "#FFFDF6"
RICE_DEEP = "#E2DAC6"

FACE = "#2B1A15"          # warm near-black, never pure black


def paint_mascot(p: QPainter, x: float, y: float, size: float,
                 smiling: bool = True, asleep: bool = False) -> None:
    """Draw the mascot into a ``size``×``size`` box at (x, y).

    ``asleep`` swaps the two dots for closed lids. It is the same face
    otherwise — a sleeping mascot that also loses its smile reads as unwell
    rather than untroubled.
    """
    p.save()
    p.translate(x, y)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    s = size

    # --- contact shadow -----------------------------------------------------
    if s >= 20:
        shade = QRadialGradient(QPointF(s * 0.5, s * 0.925), s * 0.42)
        shade.setColorAt(0.0, QColor(43, 26, 21, 64))
        shade.setColorAt(1.0, QColor(43, 26, 21, 0))
        p.setBrush(shade)
        p.drawEllipse(QPointF(s * 0.5, s * 0.925), s * 0.42, s * 0.085)

    # --- rice ---------------------------------------------------------------
    rice = QRectF(s * 0.115, s * 0.515, s * 0.77, s * 0.40)
    rice_grad = QLinearGradient(0, rice.top(), 0, rice.bottom())
    rice_grad.setColorAt(0.0, QColor(RICE_LIGHT))
    rice_grad.setColorAt(1.0, QColor(RICE_DEEP))
    rice_path = QPainterPath()
    rice_path.addRoundedRect(rice, s * 0.175, s * 0.175)
    p.setBrush(rice_grad)
    p.drawPath(rice_path)

    if s >= 44:
        p.save()
        p.setClipPath(rice_path)
        p.setBrush(QColor(255, 255, 255, 150))
        for cx, cy, rx in ((0.30, 0.845, 0.085), (0.5, 0.875, 0.095),
                           (0.70, 0.845, 0.085)):
            p.drawEllipse(QPointF(s * cx, s * cy), s * rx, s * rx * 0.58)
        p.restore()

    # --- salmon -------------------------------------------------------------
    # Overhangs the rice at both ends, which is most of what makes it read as
    # a slice laid over something rather than a second block stacked on it.
    salmon = QRectF(s * 0.045, s * 0.075, s * 0.91, s * 0.545)
    radius = s * 0.255
    salmon_path = QPainterPath()
    salmon_path.addRoundedRect(salmon, radius, radius)

    body = QLinearGradient(salmon.left(), salmon.top(),
                           salmon.right(), salmon.bottom())
    body.setColorAt(0.0, QColor(SALMON_LIGHT))
    body.setColorAt(0.55, QColor(SALMON_MID))
    body.setColorAt(1.0, QColor(SALMON_DEEP))

    # The shadow the slice casts onto the rice, clipped so it only darkens the
    # rice and never spills onto the background.
    if s >= 20:
        p.save()
        p.setClipPath(rice_path)
        drop = QLinearGradient(0, salmon.bottom() - s * 0.03,
                               0, salmon.bottom() + s * 0.13)
        drop.setColorAt(0.0, QColor(120, 70, 50, 85))
        drop.setColorAt(1.0, QColor(120, 70, 50, 0))
        p.setBrush(drop)
        p.drawRect(QRectF(0, salmon.bottom() - s * 0.03, s, s * 0.2))
        p.restore()

    p.setBrush(body)
    p.drawPath(salmon_path)

    p.save()
    p.setClipPath(salmon_path)

    # Marbling — the pale striations across a salmon slice.
    if s >= 30:
        marble = QColor(MARBLE)
        marble.setAlpha(140)
        pen = QPen(marble)
        pen.setWidthF(s * 0.036)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for dy, width in ((0.075, 0.44), (0.455, 0.60)):
            band = QPainterPath()
            band.moveTo(s * (0.5 - width / 2), salmon.top() + s * dy)
            band.quadTo(s * 0.5, salmon.top() + s * (dy - 0.05),
                        s * (0.5 + width / 2), salmon.top() + s * dy)
            p.drawPath(band)
        p.setPen(Qt.PenStyle.NoPen)

    # Specular highlight, up and to the left of the light.
    if s >= 24:
        gloss = QRadialGradient(QPointF(s * 0.34, salmon.top() + s * 0.13),
                                s * 0.42)
        gloss.setColorAt(0.0, QColor(255, 255, 255, 92))
        gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(gloss)
        p.drawRect(salmon)
    p.restore()

    # --- face ---------------------------------------------------------------
    if s < 13:
        p.restore()
        return

    eye_r = max(0.9, s * 0.047)
    eye_y = salmon.top() + salmon.height() * 0.38
    if asleep:
        # Lids as ∩ arcs rather than ∪ ones: the cup-up version is the
        # squeezed-shut eye of a laugh, and this piece is not laughing, it is
        # having a lie-down.
        lid = QPen(QColor(FACE))
        lid.setWidthF(max(1.0, s * 0.058))
        lid.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(lid)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for cx in (s * 0.355, s * 0.645):
            p.drawArc(QRectF(cx - eye_r * 1.8, eye_y - eye_r * 0.5,
                             eye_r * 3.6, eye_r * 2.4), 18 * 16, 144 * 16)
    else:
        p.setBrush(QColor(FACE))
        p.setPen(Qt.PenStyle.NoPen)
        for cx in (s * 0.355, s * 0.645):
            p.drawEllipse(QPointF(cx, eye_y), eye_r, eye_r * 1.14)

    if smiling:
        # An arc, not a curve through three points: a quadratic sags in the
        # middle and reads as a frown once it is small.
        mouth_w = s * 0.40
        mouth_h = s * 0.26
        box = QRectF(s * 0.5 - mouth_w / 2,
                     salmon.top() + salmon.height() * 0.40, mouth_w, mouth_h)
        pen = QPen(QColor(FACE))
        pen.setWidthF(max(1.0, s * 0.072))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(box, 198 * 16, 144 * 16)
    p.restore()


def paint_lazy_mascot(p: QPainter, x: float, y: float, size: float,
                      t: float = 0.0, asleep: bool = True) -> None:
    """The mascot lounging: tipped back, drifting, and — ``asleep`` — snoring.

    Insane mode's claim is that it works while you do not, and the console
    only ever said so in words. This is the same piece of nigiri as the
    sidebar mark, leaning back far enough to read as reclining and bobbing on
    a slow sine, with three z's lifting off it while a run is on.

    ``t`` is the host panel's animation clock, so the bob stays in step with
    the twinkling behind it rather than running its own timer.
    """
    s = float(size)
    bob = math.sin(t * 0.8) * s * 0.028

    p.save()
    # Rotated about its own centre, so the lean does not walk the piece out
    # of the box the caller reserved for it.
    p.translate(x + s / 2, y + s / 2 + bob)
    p.rotate(-15 + math.sin(t * 0.37) * 2.2)
    paint_mascot(p, -s / 2, -s / 2, s, asleep=asleep)
    p.restore()

    if not asleep:
        return

    # Three z's on a staggered loop, each rising to the right as it fades.
    # They fade *in* as well, otherwise one pops into existence on the
    # mascot's head every three seconds.
    for i in range(3):
        phase = (t * 0.17 + i / 3) % 1.0
        glyph = theme.body_font(max(8, int(s * (0.15 + phase * 0.11))), 700)
        glyph.setItalic(True)
        p.setFont(glyph)
        colour = QColor(MARBLE)
        colour.setAlpha(int(215 * (1 - phase) * min(1.0, phase * 5)))
        p.setPen(colour)
        p.drawText(QRectF(x + s * (0.84 + phase * 0.34),
                          y + s * (0.16 - phase * 0.50) + bob,
                          s * 0.45, s * 0.45),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "z")


#: The app's logo: the roll with a magnifier, cut from the brand sheet.
LOGO_PATH = __import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "logo.png"
_LOGO_CACHE: dict[int, "QPixmap"] = {}


def logo_pixmap(px: int):
    """The logo at ``px`` device pixels, smoothly scaled and cached."""
    from PyQt6.QtGui import QPixmap
    px = max(1, int(px))
    pm = _LOGO_CACHE.get(px)
    if pm is None:
        src = QPixmap(str(LOGO_PATH))
        pm = src.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation) if not src.isNull() else src
        _LOGO_CACHE[px] = pm
    return pm


def paint_logo(p: QPainter, x: float, y: float, size: float) -> None:
    """Draw the logo in a ``size`` square; the drawn mascot if it is missing."""
    dpr = p.device().devicePixelRatioF() if p.device() else 1.0
    pm = logo_pixmap(round(size * dpr))
    if pm.isNull():
        paint_mascot(p, x, y, size)
        return
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.drawPixmap(QRectF(x, y, size, size), pm, QRectF(pm.rect()))


def app_icon():
    """The window and taskbar icon."""
    from PyQt6.QtGui import QIcon
    ico = LOGO_PATH.with_suffix(".ico")
    return QIcon(str(ico if ico.exists() else LOGO_PATH))


class SushiMark(QWidget):
    """The mascot as a widget. ``size`` is its full width in logical pixels."""

    def __init__(self, size: int = 18, on_dark: bool = False) -> None:
        super().__init__()
        self._size = size
        # Kept for call-site compatibility. Salmon and cream both read on the
        # starfield as they are; inverting would make it another character.
        self._on_dark = on_dark
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        paint_logo(p, 0, 0, float(self._size))
        p.end()
