"""A drawn icon set — crisp at any size, nothing to ship.

The first version of this UI used Unicode glyphs (◆ ⌕ ▤ ⚡ ⚙) out of Segoe UI
Symbol. They were a dated, inconsistent mix: different optical weights,
different grid alignments, some of them bitmap-era shapes, and several the
emoji font hijacked into full colour. Next to a design built on one hairline
weight and one accent they looked exactly like what they were — whatever the
font happened to have.

So the icons are geometry instead. Each is described on Lucide's 24×24 grid as
a short list of primitives, stroked at a consistent weight with round caps and
joins. That buys three things a font cannot: they are sharp at any size and
any DPI because they are re-rendered rather than scaled, they take the colour
they are asked for, and the stroke weight stays optically even from a 14px
chip to a 40px tile.

Adding one is a few lines in :data:`ICONS` — no asset, no licence, no build
step.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QColor, QIcon, QImage, QPainter, QPainterPath, QPen,
                         QPixmap)

GRID = 24.0          # the coordinate space every icon is drawn in
STROKE = 1.9         # in grid units, so it scales with the icon

# --- primitives -------------------------------------------------------------
#
#   ("line",  x1, y1, x2, y2)
#   ("poly",  [(x, y), …], closed)      stroked polyline
#   ("circle", cx, cy, r)               stroked
#   ("dot",   cx, cy, r)                filled
#   ("rect",  x, y, w, h, radius)       stroked, rounded
#   ("arc",   cx, cy, r, start°, sweep°)
#   ("star",  cx, cy, r)                filled four-point sparkle
#   ("curve", (x, y), [(c1x, c1y, c2x, c2y, x, y), …])

ICONS: dict[str, list[tuple]] = {
    # --- navigation ---------------------------------------------------------
    "grid": [
        ("rect", 3.5, 3.5, 7.5, 7.5, 1.8),
        ("rect", 13, 3.5, 7.5, 7.5, 1.8),
        ("rect", 3.5, 13, 7.5, 7.5, 1.8),
        ("rect", 13, 13, 7.5, 7.5, 1.8),
    ],
    "search": [
        ("circle", 10.8, 10.8, 6.8),
        ("line", 15.8, 15.8, 20.5, 20.5),
    ],
    "columns": [
        ("rect", 3.2, 4.2, 17.6, 15.6, 2.4),
        ("line", 9.1, 4.2, 9.1, 19.8),
        ("line", 14.9, 4.2, 14.9, 19.8),
    ],
    "zap": [
        ("poly", [(13.2, 2.4), (4.2, 13.8), (11.0, 13.8),
                  (10.3, 21.6), (19.4, 10.0), (12.6, 10.0)], True),
    ],
    "user": [
        ("circle", 12, 7.6, 3.9),
        ("curve", (4.4, 20.6),
         [(4.4, 16.6, 7.8, 14.4, 12, 14.4),
          (16.2, 14.4, 19.6, 16.6, 19.6, 20.6)]),
    ],
    "sliders": [
        ("line", 3.2, 7.4, 20.8, 7.4),
        ("circle", 9.0, 7.4, 2.5),
        ("line", 3.2, 16.6, 20.8, 16.6),
        ("circle", 15.4, 16.6, 2.5),
    ],
    # --- chrome -------------------------------------------------------------
    "panel-left": [
        ("rect", 3.2, 4.2, 17.6, 15.6, 2.6),
        ("line", 9.4, 4.2, 9.4, 19.8),
    ],
    "sparkles": [
        ("star", 9.6, 9.8, 6.4),
        ("star", 18.0, 17.2, 3.3),
    ],
    # --- documents ----------------------------------------------------------
    "file-text": [
        ("poly", [(5.2, 2.8), (14.2, 2.8), (19.2, 7.8), (19.2, 21.2), (5.2, 21.2)], True),
        ("poly", [(14.0, 2.9), (14.0, 8.0), (19.1, 8.0)], False),
        ("line", 8.6, 13.4, 15.6, 13.4),
        ("line", 8.6, 17.0, 15.6, 17.0),
    ],
    "folder": [
        ("poly", [(3.2, 19.8), (3.2, 5.6), (9.2, 5.6), (11.4, 8.6),
                  (20.8, 8.6), (20.8, 19.8)], True),
    ],
    # An open book, for the folder of things already written. A closed book
    # reads as "documentation"; the spine and two leaves read as "what you
    # wrote", which is what the knowledge folder actually holds.
    "book": [
        ("line", 12, 6.6, 12, 20.4),
        # Left leaf: top edge out to the spine-board, then back along the
        # bottom. Drawn as one path so the corners join rather than butt.
        ("curve", (12, 6.6),
         [(9.5, 4.5, 6.6, 3.8, 3.4, 3.8)]),
        ("line", 3.4, 3.8, 3.4, 17.9),
        ("curve", (3.4, 17.9),
         [(6.6, 17.9, 9.5, 18.6, 12, 20.4)]),
        ("curve", (12, 6.6),
         [(14.5, 4.5, 17.4, 3.8, 20.6, 3.8)]),
        ("line", 20.6, 3.8, 20.6, 17.9),
        ("curve", (20.6, 17.9),
         [(17.4, 17.9, 14.5, 18.6, 12, 20.4)]),
    ],
    "upload": [
        ("line", 12, 15.6, 12, 3.6),
        ("poly", [(7.4, 8.2), (12, 3.6), (16.6, 8.2)], False),
        ("poly", [(4.0, 15.2), (4.0, 20.2), (20.0, 20.2), (20.0, 15.2)], False),
    ],
    "download": [
        ("line", 12, 3.6, 12, 15.6),
        ("poly", [(7.4, 11.0), (12, 15.6), (16.6, 11.0)], False),
        ("poly", [(4.0, 16.4), (4.0, 20.2), (20.0, 20.2), (20.0, 16.4)], False),
    ],
    "clock": [
        ("circle", 12, 12, 8.6),
        ("poly", [(12, 6.6), (12, 12.3), (16.2, 14.4)], False),
    ],
    # --- actions ------------------------------------------------------------
    "check": [("poly", [(5.0, 12.6), (9.9, 17.5), (19.0, 6.9)], False)],
    "plus": [("line", 12, 4.8, 12, 19.2), ("line", 4.8, 12, 19.2, 12)],
    "x": [("line", 6.2, 6.2, 17.8, 17.8), ("line", 17.8, 6.2, 6.2, 17.8)],
    "arrow-up": [
        ("line", 12, 19.4, 12, 5.2),
        ("poly", [(6.2, 11.0), (12, 5.2), (17.8, 11.0)], False),
    ],
    "chevron-down": [("poly", [(6.2, 9.4), (12, 15.4), (17.8, 9.4)], False)],
    # The arc opens at 50°; the chevron sits on that opening so the stroke
    # reads as one continuous arrow rather than a circle with a tick near it.
    "refresh": [
        ("arc", 12, 12, 8.2, 50, -300),
        ("poly", [(12.9, 3.0), (17.3, 5.7), (14.9, 10.0)], False),
    ],
    "trash": [
        ("line", 3.8, 6.4, 20.2, 6.4),
        ("poly", [(9.4, 6.4), (9.4, 4.0), (14.6, 4.0), (14.6, 6.4)], False),
        ("poly", [(6.0, 6.4), (7.0, 20.4), (17.0, 20.4), (18.0, 6.4)], False),
    ],
    "bookmark": [
        ("poly", [(6.2, 3.4), (17.8, 3.4), (17.8, 20.6), (12, 15.6), (6.2, 20.6)], True),
    ],
    "external-link": [
        ("poly", [(13.8, 3.6), (20.4, 3.6), (20.4, 10.2)], False),
        ("line", 20.4, 3.6, 11.2, 12.8),
        ("poly", [(17.0, 13.8), (17.0, 20.4), (3.6, 20.4), (3.6, 7.0), (10.2, 7.0)], False),
    ],
    "wand": [
        ("line", 3.8, 20.2, 13.4, 10.6),
        ("star", 17.2, 6.4, 3.6),
        ("star", 20.6, 13.0, 1.9),
    ],
    # --- added for the dashboard redesign -----------------------------------
    "compass": [
        ("circle", 12, 12, 8.6),
        ("poly", [(15.4, 8.6), (13.6, 13.6), (8.6, 15.4), (10.4, 10.4)], True),
    ],
    "layers": [
        ("poly", [(12, 2.8), (21.2, 7.6), (12, 12.4), (2.8, 7.6)], True),
        ("poly", [(2.8, 12.4), (12, 17.2), (21.2, 12.4)], False),
        ("poly", [(2.8, 16.8), (12, 21.6), (21.2, 16.8)], False),
    ],
    "flame": [
        ("curve", (12, 2.6),
         [(12, 2.6, 18.6, 7.4, 18.6, 13.4),
          (18.6, 17.6, 15.6, 21.4, 12, 21.4),
          (8.4, 21.4, 5.4, 17.6, 5.4, 13.4),
          (5.4, 9.4, 8.2, 8.6, 9.4, 5.6),
          (10.8, 8.0, 12, 9.0, 12, 2.6)]),
        ("curve", (12, 12.4),
         [(12, 12.4, 15.0, 14.6, 15.0, 17.0),
          (15.0, 19.0, 13.7, 20.4, 12, 20.4),
          (10.3, 20.4, 9.0, 19.0, 9.0, 17.0),
          (9.0, 15.0, 10.6, 14.4, 12, 12.4)]),
    ],
    "gear": [
        ("poly", [(21.12, 10.19), (18.77, 13.35), (19.73, 17.17), (15.83, 17.74), (13.81, 21.12), (10.65, 18.77), (6.83, 19.73), (6.26, 15.83), (2.88, 13.81), (5.23, 10.65), (4.27, 6.83), (8.17, 6.26), (10.19, 2.88), (13.35, 5.23), (17.17, 4.27), (17.74, 8.17)], True),
        ("circle", 12, 12, 3.3),
    ],
    "dot-filled": [("dot", 12, 12, 7.5)],
    "chevron-right": [("poly", [(9.6, 5.8), (15.6, 12), (9.6, 18.2)], False)],
    "chevron-up": [("poly", [(6.2, 14.6), (12, 8.6), (17.8, 14.6)], False)],
    "info": [
        ("circle", 12, 12, 8.8),
        ("line", 12, 11.2, 12, 16.6),
        ("dot", 12, 7.9, 1.15),
    ],
    "send": [
        ("poly", [(21.2, 2.8), (10.6, 13.4)], False),
        ("poly", [(21.2, 2.8), (14.4, 21.4), (10.6, 13.4), (2.6, 9.6)], True),
    ],
    "moon": [
        ("curve", (20.4, 14.6),
         [(19.3, 15.0, 18.1, 15.2, 16.9, 15.2),
          (11.4, 15.2, 7.0, 10.8, 7.0, 5.4),
          (7.0, 4.6, 7.1, 3.8, 7.3, 3.1),
          (4.1, 4.6, 2.0, 7.9, 2.0, 11.6),
          (2.0, 16.9, 6.3, 21.2, 11.6, 21.2),
          (15.6, 21.2, 19.1, 18.5, 20.4, 14.6)]),
    ],
    "sun": [
        ("circle", 12, 12, 4.2),
        ("line", 12, 1.9, 12, 4.1),
        ("line", 12, 19.9, 12, 22.1),
        ("line", 1.9, 12, 4.1, 12),
        ("line", 19.9, 12, 22.1, 12),
        ("line", 4.9, 4.9, 6.4, 6.4),
        ("line", 17.6, 17.6, 19.1, 19.1),
        ("line", 19.1, 4.9, 17.6, 6.4),
        ("line", 6.4, 17.6, 4.9, 19.1),
    ],
    "arrow-right": [
        ("line", 4.4, 12, 18.6, 12),
        ("poly", [(13.0, 6.2), (18.8, 12), (13.0, 17.8)], False),
    ],
    "eye": [
        ("curve", (2.6, 12),
         [(5.6, 6.8, 8.6, 4.8, 12, 4.8), (15.4, 4.8, 18.4, 6.8, 21.4, 12)]),
        ("curve", (21.4, 12),
         [(18.4, 17.2, 15.4, 19.2, 12, 19.2), (8.6, 19.2, 5.6, 17.2, 2.6, 12)]),
        ("circle", 12, 12, 3.1),
    ],
}


# --- rendering --------------------------------------------------------------

def _build(ops: list[tuple]) -> tuple[QPainterPath, QPainterPath]:
    """Split an icon's primitives into a stroked path and a filled one."""
    stroked, filled = QPainterPath(), QPainterPath()
    for op in ops:
        kind = op[0]
        if kind == "line":
            _, x1, y1, x2, y2 = op
            stroked.moveTo(x1, y1)
            stroked.lineTo(x2, y2)
        elif kind == "poly":
            _, pts, closed = op
            stroked.moveTo(*pts[0])
            for pt in pts[1:]:
                stroked.lineTo(*pt)
            if closed:
                stroked.closeSubpath()
        elif kind == "circle":
            _, cx, cy, r = op
            stroked.addEllipse(QPointF(cx, cy), r, r)
        elif kind == "dot":
            _, cx, cy, r = op
            filled.addEllipse(QPointF(cx, cy), r, r)
        elif kind == "rect":
            _, x, y, w, h, radius = op
            stroked.addRoundedRect(QRectF(x, y, w, h), radius, radius)
        elif kind == "arc":
            _, cx, cy, r, start, sweep = op
            box = QRectF(cx - r, cy - r, r * 2, r * 2)
            stroked.arcMoveTo(box, start)
            stroked.arcTo(box, start, sweep)
        elif kind == "star":
            _, cx, cy, r = op
            filled.addPath(_star(cx, cy, r))
        elif kind == "curve":
            _, start, segments = op
            stroked.moveTo(*start)
            for c1x, c1y, c2x, c2y, x, y in segments:
                stroked.cubicTo(c1x, c1y, c2x, c2y, x, y)
    return stroked, filled


def _star(cx: float, cy: float, r: float) -> QPainterPath:
    """A four-point sparkle with concave sides — the same mark as the sky."""
    path = QPainterPath()
    waist = r * 0.24
    path.moveTo(cx, cy - r)
    path.quadTo(cx + waist, cy - waist, cx + r, cy)
    path.quadTo(cx + waist, cy + waist, cx, cy + r)
    path.quadTo(cx - waist, cy + waist, cx - r, cy)
    path.quadTo(cx - waist, cy - waist, cx, cy - r)
    return path


_cache: dict[tuple, QPixmap] = {}


def pixmap(name: str, size: int = 20, colour: str = "#686864",
           stroke: float = STROKE, ratio: float = 0.0) -> QPixmap:
    """Render an icon. Cached, because rows of them repaint constantly.

    ``ratio`` is the device pixel ratio; the image is rendered at that
    multiple and tagged, which is what keeps it sharp on a scaled display
    instead of being a small bitmap stretched up.
    """
    if ratio <= 0:
        try:
            from PyQt6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
            ratio = screen.devicePixelRatio() if screen else 1.0
        except Exception:
            ratio = 1.0
    key = (name, size, colour, stroke, round(ratio, 2))
    hit = _cache.get(key)
    if hit is not None:
        return hit

    ops = ICONS.get(name)
    px = int(round(size * ratio))
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    if ops:
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.scale(px / GRID, px / GRID)
        stroked, filled = _build(ops)
        pen = QPen(QColor(colour))
        pen.setWidthF(stroke)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(stroked)
        if not filled.isEmpty():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(colour))
            p.drawPath(filled)
        p.end()

    out = QPixmap.fromImage(image)
    out.setDevicePixelRatio(ratio)
    _cache[key] = out
    return out


def icon(name: str, size: int = 18, colour: str = "#686864",
         stroke: float = STROKE) -> QIcon:
    """A QIcon for buttons and anything else that takes one."""
    return QIcon(pixmap(name, size, colour, stroke))


def names() -> list[str]:
    return sorted(ICONS)
