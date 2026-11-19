"""The collapsible sidebar.

Navigation lives here rather than across the top: a row of tabs does not
survive a sixth destination, and the header is worth more as breathing room
than as a tab strip.

The structure is the design's. Each group is a bordered white container with
its label *outside* it, in the margin — which reads as a quieter hierarchy
than a tinted ground behind the label, and lets the groups themselves look
like the cards everything else on screen is made of. Rows carry a trailing
slot: a count, a "new" badge, or a chevron for the destinations that are
really sub-sections.

Two deliberate differences from a web drawer:

- it is a real layout column whose width animates, not an overlay that floats
  over the content — Qt has no CSS transition and a panel covering the page is
  the wrong default for a window you resize;
- collapsed is a 64px rail of icon tiles rather than nothing, so navigation
  stays one click away.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import (QEasingCurve, QParallelAnimationGroup,
                          QPropertyAnimation, Qt, pyqtSignal)
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

from . import icons, theme
from .components import IconTile, icon_button, label

# The design's drawer is 256px. The rail is 64 — a 24px tile plus the row's
# own gutters, so a collapsed row is exactly a centred tile.
OPEN_WIDTH = 256
RAIL_WIDTH = 64

#: (key, icon, tint, ink, trailing-kind). ``key`` is also the page title.
#: Trailing kinds: "count" a number, "chevron" a disclosure, "" nothing.
#:
#: Automation comes first because it is the default way to use Sushi: finding
#: and applying runs on its own, and the rest of the app is for looking at
#: what it did. Browse jobs is gone — there is nothing to browse *to* when the
#: feed fills itself, and a page whose only job was "press scan" is a chore.
#: The wireframe's four tabs. Settings is the gear beside your name, Help is
#: the sparkles in the title bar. Insane mode and Manual apply now live on
#: Home and on each match, not as places of their own.
_PLAIN = ("transparent", theme.MUTED)
NAV = [
    ("Home", "grid", *_PLAIN, "count"),
    ("Matches", "search", *_PLAIN, "count"),
    ("Applications", "layers", *_PLAIN, "count"),
    ("Profile", "user", *_PLAIN, ""),
]
# Kept for anything that still imports them.
AUTOMATION: list = []
WORKSPACE: list = []
PERSONAL: list = []
ALL_ITEMS = NAV


class NavRow(QFrame):
    """One destination: tile, label, and a trailing count, badge or chevron."""

    clicked = pyqtSignal(str)

    def __init__(self, key: str, icon: str, tint: str, ink: str,
                 trailing: str = "") -> None:
        super().__init__()
        self.key = key
        self._trailing_kind = trailing
        self.setObjectName("NavRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(key)
        self.setMinimumHeight(40)

        row = QHBoxLayout(self)
        row.setContentsMargins(9, 0, 10, 0)
        row.setSpacing(10)
        self._tint, self._ink = tint, ink
        self.tile = IconTile(icon, 24, tint, ink)
        row.addWidget(self.tile)
        self.name = QLabel(key)
        self.name.setObjectName("NavLabel")
        row.addWidget(self.name, 1)

        # One slot, three possible occupants — a count, a word, or a chevron.
        self.count = QLabel("")
        self.count.setObjectName("NavCount")
        row.addWidget(self.count)
        self.badge = QLabel("")
        self.badge.setObjectName("BadgeNew")
        self.badge.setVisible(False)
        row.addWidget(self.badge)
        self.chevron = QLabel()
        self.chevron.setPixmap(icons.pixmap("chevron-right", 14, theme.LINE_3))
        self.chevron.setVisible(trailing == "chevron")
        row.addWidget(self.chevron)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)

    #: The tile on a selected row. Translucent white over the solid olive
    #: rather than a flat colour, so the ground still reads through it and the
    #: tile does not become a second, lighter block sitting on the first.
    _ACTIVE_TILE = "transparent"
    _ACTIVE_GLYPH = theme.ACCENT

    def set_active(self, on: bool) -> None:
        self.setObjectName("NavRowActive" if on else "NavRow")
        self.name.setObjectName("NavLabelActive" if on else "NavLabel")
        self.count.setObjectName("NavCountActive" if on else "NavCount")
        # Everything on the row inverts together. The label alone turning
        # white leaves the glyph and the count sitting on the olive in their
        # own colours, which is how a solid selection ends up looking broken.
        self.tile.set_tint(*((self._ACTIVE_TILE, self._ACTIVE_GLYPH) if on
                             else (self._tint, self._ink)))
        self.chevron.setPixmap(icons.pixmap(
            "chevron-right", 14, theme.ACCENT if on else theme.LINE_3))
        for w in (self, self.name, self.count):
            w.style().unpolish(w)
            w.style().polish(w)

    def set_count(self, text: str) -> None:
        self.count.setText(text)

    def set_badge(self, text: str) -> None:
        self.badge.setText(text)
        self.badge.setVisible(bool(text))

    def set_collapsed(self, on: bool) -> None:
        self.name.setVisible(not on)
        self.count.setVisible(not on)
        self.badge.setVisible(not on and bool(self.badge.text()))
        self.chevron.setVisible(not on and self._trailing_kind == "chevron")
        self.layout().setContentsMargins(9, 0, 9 if on else 10, 0)


class _Group(QFrame):
    """A bordered container of rows. Its label sits outside, in the margin."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("NavGroup")
        v = QVBoxLayout(self)
        v.setContentsMargins(5, 5, 5, 5)
        v.setSpacing(2)

    def add(self, row: NavRow) -> None:
        self.layout().addWidget(row)


class Sidebar(QFrame):
    navigate = pyqtSignal(str)
    #: The show/hide button, which lives in the sidebar itself now.
    toggle_requested = pyqtSignal()

    def __init__(self, on_toggle: Callable[[bool], None] | None = None) -> None:
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(OPEN_WIDTH)
        self._collapsed = False
        self._on_toggle = on_toggle
        self._rows: dict[str, NavRow] = {}
        self._labels: list[QLabel] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # No header here. It said "Sushi · AGENT" over a status word, under a
        # title bar already saying "Sushi" forty pixels above — the brand
        # twice and a state nobody was reading, where the first nav group
        # should start.

        body = QWidget()
        # Scoped to this widget, never bare. An unscoped
        # `background: transparent` on a container is inherited by every
        # descendant *and* outranks the application stylesheet, because a
        # nearer ancestor's sheet wins — which is why the selected nav row
        # could only ever show its border. Its fill was being cancelled from
        # four levels up.
        body.setObjectName("SidebarBody")
        body.setStyleSheet("QWidget#SidebarBody{background:transparent;}")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(12, 10, 12, 14)

        # The wireframe puts the brand at the head of the sidebar: a ring
        # and the name in the title face.
        brand = QWidget()
        brand.setObjectName("SidebarBrand")
        brand.setStyleSheet("QWidget#SidebarBrand{background:transparent;}")
        bl = QHBoxLayout(brand)
        bl.setContentsMargins(10, 14, 0, 18)
        self._brand_row = bl
        bl.setSpacing(10)
        from .brand import SushiMark
        bl.addStretch(0)            # takes the slack in the rail, centring the logo
        bl.addWidget(SushiMark(30), 0, Qt.AlignmentFlag.AlignVCenter)
        word = QLabel("Sushi")
        word.setStyleSheet(f"color:{theme.INK};background:transparent;"
                           f"font-family:'{theme.DISPLAY_FONT}';font-size:28px;"
                           "font-weight:500;")
        bl.addWidget(word)
        bl.addStretch(1)
        self._brand_word = word
        tip = "Hide or show the sidebar  (Ctrl+B)"
        self._toggle_open = icon_button("panel-left", tip, self.toggle_requested.emit)
        bl.addWidget(self._toggle_open, 0, Qt.AlignmentFlag.AlignVCenter)
        bv.addWidget(brand)
        # In the rail there is no room beside the logo, so the button sits
        # under it, on the icons' centre line.
        self._toggle_rail = icon_button("panel-left", tip, self.toggle_requested.emit)
        self._toggle_rail.setVisible(False)
        rr = QHBoxLayout()
        rr.setContentsMargins(0, 0, 0, 10)
        rr.addStretch(1)
        rr.addWidget(self._toggle_rail)
        rr.addStretch(1)
        bv.addLayout(rr)
        bv.setSpacing(6)

        self._groups: list[_Group] = []
        for title, items in (("", NAV),):

            g = _Group()
            for key, icon, tint, ink, trailing in items:
                row = NavRow(key, icon, tint, ink, trailing)
                row.clicked.connect(self.navigate.emit)
                self._rows[key] = row
                g.add(row)
            bv.addWidget(g)
            self._groups.append(g)
        bv.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll, 1)
        root.addWidget(self._footer())

    # --- footer ---------------------------------------------------------------

    def _footer(self) -> QWidget:
        """The wireframe's foot: a MODE box, then you, with Settings."""
        wrap = QFrame()
        wrap.setObjectName("SidebarFooter")
        wrap.setStyleSheet("QFrame#SidebarFooter{background:transparent;border:none;}")
        col = QVBoxLayout(wrap)
        col.setContentsMargins(16, 8, 16, 16)
        self._footer_col = col
        col.setSpacing(14)

        self._mode_box = QFrame()
        self._mode_box.setObjectName("ModeBox")
        self._mode_box.setStyleSheet(
            f"QFrame#ModeBox{{background:{theme.SURFACE};border:1px solid {theme.LINE_2};"
            f"border-radius:{theme.R_TILE}px;}}"
            "QFrame#ModeBox QLabel{background:transparent;border:none;}")
        self._mode_box.setCursor(Qt.CursorShape.PointingHandCursor)
        mv = QVBoxLayout(self._mode_box)
        mv.setContentsMargins(12, 9, 12, 10)
        mv.setSpacing(2)
        cap = QLabel("MODE")
        cap.setStyleSheet(f"color:{theme.FAINT};font-size:10px;letter-spacing:1px;")
        self.mode_lbl = QLabel("Manual")
        self.mode_lbl.setStyleSheet(f"color:{theme.INK};font-size:{theme.SIZE_SMALL}px;")
        mv.addWidget(cap)
        mv.addWidget(self.mode_lbl)
        self._mode_box.mousePressEvent = lambda _e: self.navigate.emit("Home")
        col.addWidget(self._mode_box)

        row = QHBoxLayout()
        row.setSpacing(11)
        self._foot_row = row
        row.addStretch(0)           # with the trailing one, centres the avatar in the rail
        self.avatar = Avatar("")
        row.addWidget(self.avatar)
        who = QVBoxLayout()
        who.setSpacing(0)
        self.who_name = QLabel("No profile yet")
        self.who_name.setStyleSheet(f"font-size:{theme.SIZE_SMALL + 1}px;color:{theme.INK};")
        self.who_plan = QLabel("Settings ▾")
        self.who_plan.setStyleSheet(f"font-size:12px;color:{theme.MUTED};")
        self.who_plan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.who_plan.mousePressEvent = lambda _e: self.navigate.emit("Settings")
        who.addWidget(self.who_name)
        who.addWidget(self.who_plan)
        self._who_box = QWidget()
        self._who_box.setObjectName("SidebarWho")
        self._who_box.setStyleSheet("QWidget#SidebarWho{background:transparent;}")
        self._who_box.setLayout(who)
        self._who_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self._who_box, 1)
        # The gear stays for the collapsed rail, where the words are hidden.
        # Kept for anything that refers to it; the avatar is the way to
        # Settings in the rail, where there is no room for both.
        self._gear = icon_button("gear", "Settings",
                                 lambda: self.navigate.emit("Settings"))
        self._gear.setVisible(False)
        self.avatar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.avatar.setToolTip("Settings")
        self.avatar.mousePressEvent = lambda _e: self.navigate.emit("Settings")
        row.addStretch(0)
        col.addLayout(row)
        return wrap

    def set_mode(self, text: str) -> None:
        self.mode_lbl.setText(text)

    # --- public ---------------------------------------------------------------

    def set_active(self, key: str) -> None:
        for name, row in self._rows.items():
            row.set_active(name == key)

    def set_count(self, key: str, text: str) -> None:
        if key in self._rows:
            self._rows[key].set_count(text)

    def set_badge(self, key: str, text: str) -> None:
        if key in self._rows:
            self._rows[key].set_badge(text)

    def set_identity(self, name: str, plan: str) -> None:
        self.avatar.set_name(name)
        self.who_name.setText(name or "No profile yet")

    def is_collapsed(self) -> bool:
        return self._collapsed

    def toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, on: bool) -> None:
        if on == self._collapsed:
            return
        self._collapsed = on
        # Labels go first on the way in and last on the way out, so text never
        # wraps or clips against a width it no longer fits.
        if on:
            self._apply_collapsed(True)

        # Stop and drop the previous group explicitly. Rebinding self._anim
        # while the old group was still running left Python holding the last
        # reference to a live QAbstractAnimation and freeing it mid-tick,
        # which Qt does not survive. Reachable from resizeEvent, so it is a
        # question of when rather than whether.
        self._stop_animation()

        target = RAIL_WIDTH if on else OPEN_WIDTH
        group = QParallelAnimationGroup(self)
        for prop in (b"minimumWidth", b"maximumWidth"):
            a = QPropertyAnimation(self, prop, group)
            a.setDuration(220)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.setStartValue(self.width())
            a.setEndValue(target)
            group.addAnimation(a)
        # Always land on a clean end state, whatever happened on the way:
        # toggling again mid-slide, or a resize collapsing it, used to leave
        # the bar stuck half-wide with the labels clipped ("s 44").
        group.finished.connect(self._settle)
        self._anim = group
        group.start()

        if self._on_toggle:
            self._on_toggle(on)

    def _settle(self) -> None:
        """Snap to exactly the width and contents the current state wants."""
        self.setFixedWidth(RAIL_WIDTH if self._collapsed else OPEN_WIDTH)
        self._apply_collapsed(self._collapsed)

    def _stop_animation(self) -> None:
        anim = getattr(self, "_anim", None)
        if anim is None:
            return
        try:
            anim.stop()
            anim.deleteLater()          # let Qt free it, not the GC mid-tick
        except RuntimeError:
            pass                        # already gone; nothing to stop
        self._anim = None

    def _apply_collapsed(self, on: bool) -> None:
        for caption in self._labels:
            caption.setVisible(not on)
        for g in self._groups:
            g.layout().setContentsMargins(3 if on else 5, 5, 3 if on else 5, 5)
        for row in self._rows.values():
            row.set_collapsed(on)
        self._who_box.setVisible(not on)
        self._mode_box.setVisible(not on)
        # Centred in the rail: the avatar alone, under the icons.
        # The nav column is inset by the scroll area's margin; match it so the
        # avatar shares the icons' centre line.
        self._footer_col.setContentsMargins(*((8, 8, 0, 16) if on else (16, 8, 16, 16)))
        self._brand_word.setVisible(not on)
        self._toggle_open.setVisible(not on)
        self._toggle_rail.setVisible(on)
        self._brand_row.setContentsMargins(*((0, 14, 0, 18) if on else (10, 14, 0, 18)))
        self._brand_row.setStretch(0, 1 if on else 0)
        self._foot_row.setStretch(0, 1 if on else 0)
        self._foot_row.setStretch(self._foot_row.count() - 1, 1 if on else 0)


class Avatar(QLabel):
    """A round initial. A photo would be nicer; there isn't one to use."""

    def __init__(self, name: str, size: int = 30) -> None:
        super().__init__()
        self._size = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"background:{theme.LINE_2};color:{theme.INK};"
            f"border-radius:{size // 2}px;font-weight:600;font-size:13px;")
        self.set_name(name)

    def set_name(self, name: str) -> None:
        self.setText((name or "—")[:1].upper())
