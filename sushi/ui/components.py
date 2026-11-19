"""Reusable widgets in Sushi's design language.

The earlier design's vocabulary is small and repeats everywhere: a white card with one
hairline and a 20px radius; a serif title with a 13px 600-weight eyebrow above
it; a 40px tinted tile holding an icon; a keycap; a soft olive pill. Pages
compose these rather than styling inline, so the system stays consistent.

Type is set through QFont rather than the stylesheet wherever tracking matters
— QSS has no ``letter-spacing``, and the earlier design tracks nearly all of its type at
-.02em, which is visible at the 28px display size.
"""
from __future__ import annotations

import weakref
from typing import Callable

from PyQt6.QtCore import (QEasingCurve, QEvent, QObject, QPoint,
                          QPropertyAnimation, QRect, QSize, Qt, QThreadPool,
                          pyqtProperty, pyqtSignal)
from PyQt6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import (QAbstractButton, QAbstractItemView, QFrame,
                             QHBoxLayout, QHeaderView, QLabel, QLayout,
                             QLayoutItem, QLineEdit, QPushButton, QSizePolicy,
                             QTableWidget, QVBoxLayout, QWidget)

from . import icons, theme
from .workers import Worker


# --- simple helpers ---------------------------------------------------------

class WrapLabel(QLabel):
    """A word-wrapped label that keeps its own height honest.

    Qt sizes a wrapped QLabel from ``sizeHint``, which is the *unwrapped*
    single-line size. In a vertical layout that mostly works out; inside a
    horizontal row, or any nested container, the label is handed one line's
    height and the rest of the sentence is silently clipped — which is how
    three different pages shipped with their second line cut in half.

    Re-asserting the minimum height from the width actually allocated fixes it
    at any width, including after a resize. The equality guard matters: calling
    setMinimumHeight unconditionally from resizeEvent re-triggers layout and
    the window flickers forever.
    """

    #: Widths this label has already answered for. Re-asserting a height
    #: relayouts the parent, which can hand the label a *different* width —
    #: and if the two widths each imply the other's height, Qt ping-pongs
    #: between them until the stack runs out. Remembering the answers turns
    #: that into a no-op on the second pass.
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._hfw_seen: dict[int, int] = {}

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self.wordWrap():
            return
        width = self.width()
        if width <= 0:
            return
        needed = self._hfw_seen.get(width)
        if needed is None:
            needed = self.heightForWidth(width)
            # Bounded: a label that has been through a dozen widths is in a
            # loop, and the cache would only make it a faster loop.
            if len(self._hfw_seen) < 16:
                self._hfw_seen[width] = needed
        if needed > 0 and needed != self.minimumHeight():
            self.setMinimumHeight(needed)

    def setText(self, text: str) -> None:  # noqa: N802
        if text != self.text():
            self._hfw_seen.clear()      # new text, new heights
        super().setText(text)


def label(text: str, kind: str = "", wrap: bool = True) -> QLabel:
    lbl = WrapLabel(text) if wrap else QLabel(text)
    if kind:
        lbl.setObjectName(kind)
    lbl.setWordWrap(wrap)
    # Tracking has to come from the font object; the stylesheet cannot set it.
    if kind == "H1":
        lbl.setFont(theme.display_font(theme.SIZE_H1))
    elif kind == "H2":
        lbl.setFont(theme.display_font(theme.SIZE_H2))
    elif kind in ("Eyebrow", "SectionLabel"):
        lbl.setFont(theme.eyebrow_font())
    return lbl


def h1(text: str) -> QLabel:
    return label(text, "H1")


def h2(text: str) -> QLabel:
    return label(text, "H2")


def h3(text: str) -> QLabel:
    return label(text, "H3")


def muted(text: str) -> QLabel:
    return label(text, "Muted")


def faint(text: str) -> QLabel:
    return label(text, "Faint")


def eyebrow(text: str) -> QLabel:
    """The small label the earlier design puts above every page title."""
    return label(text, "Eyebrow")


class Block(QWidget):
    """A bare container that tells its parent how tall it needs to be.

    A word-wrapped QLabel sitting directly in a page's layout sizes itself
    correctly. Put the same label inside a plain QWidget — which is what any
    "title over body" or "icon beside two lines" grouping needs — and the
    wrapping is silently lost: QWidget does not forward ``heightForWidth`` to
    its layout, so the parent allocates a single line's height and the rest of
    the sentence is clipped. Every grouped block in this UI goes through here.
    """

    def __init__(self, spacing: int = 9, margins=(0, 0, 0, 0)) -> None:
        super().__init__()
        transparent(self)
        # Vertically Fixed, not Minimum: Minimum means "may grow", and in a
        # stretched row the block swallowed the page's spare height and shoved
        # everything below it to the bottom of the screen.
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)   # Qt ignores the override without this
        self.setSizePolicy(policy)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*margins)
        lay.setSpacing(spacing)
        # Surplus height goes to the spacer at the bottom. Without it a block
        # given more room than it needs shares the slack out between its
        # labels, and an eyebrow/title/body group drifts into three bands with
        # a third of the page between them.
        lay.addStretch(1)

    def add(self, *widgets: QWidget) -> "Block":
        lay = self.layout()
        for w in widgets:
            lay.insertWidget(lay.count() - 1, w)
        return self

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self.layout().heightForWidth(width)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.layout().minimumSize()


def text_col(*widgets: QWidget, spacing: int = 3) -> Block:
    """The text half of an icon-beside-text row."""
    return Block(spacing=spacing).add(*widgets)


def page_title(title: str, body: str = "", over: str = "") -> Block:
    """The earlier design's standard screen opener: eyebrow / serif title / one line of body.

    The labels are hung off the returned widget (``title_label``,
    ``body_label``) so a page can retitle itself without indexing into the
    layout, which breaks the moment the opener gains an eyebrow.
    """
    w = Block(spacing=9)
    if over:
        w.add(eyebrow(over))
    w.title_label = h1(title)
    w.add(w.title_label)
    w.body_label = None
    if body:
        w.body_label = muted(body)
        w.body_label.setMaximumWidth(640)
        w.add(w.body_label)
    return w


class ModelPill(QFrame):
    """The title bar's model switcher: a status dot, the name, a chevron.

    A QPushButton carries one icon, and this needs a mark at each end, so it
    is a frame that behaves like a button.
    """

    clicked = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("ModelPillFrame")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        row = QHBoxLayout(self)
        row.setContentsMargins(11, 0, 9, 0)
        row.setSpacing(8)
        self._dot = QLabel()
        row.addWidget(self._dot)
        self._name = QLabel("no model")
        self._name.setStyleSheet(
            f"font-size:{theme.SIZE_SMALL}px;font-weight:600;color:{theme.INK};")
        row.addWidget(self._name)
        chev = QLabel()
        chev.setPixmap(icons.pixmap("chevron-down", 13, theme.FAINT))
        row.addWidget(chev)

    def set_model(self, name: str, ready: bool) -> None:
        self._name.setText(name or "no model")
        self._dot.setPixmap(icons.pixmap(
            "dot-filled", 9, theme.ACCENT if ready else theme.WARN))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class SearchField(QWidget):
    """A search box with the magnifier inside it.

    Qt has no `padding-left` that text and a child widget both respect, so the
    icon is a label positioned over a QLineEdit whose own left padding leaves
    room for it. Exposes the line edit as ``.edit`` so callers wire signals to
    the real thing.
    """

    def __init__(self, placeholder: str = "Search…") -> None:
        super().__init__()
        self.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setObjectName("Search")
        self.edit.setPlaceholderText(placeholder)
        lay.addWidget(self.edit)

        self._icon = QLabel(self)
        self._icon.setPixmap(icons.pixmap("search", 17, theme.FAINT))
        self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._icon.setStyleSheet("background: transparent;")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._icon.move(17, (self.height() - 17) // 2)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, value: str) -> None:  # noqa: N802
        self.edit.setText(value)

    def clear(self) -> None:
        self.edit.clear()


def card(object_name: str = "Card",
         margins=(theme.CARD_PAD, theme.CARD_PAD, theme.CARD_PAD, theme.CARD_PAD),
         spacing: int = theme.GAP_SM) -> QFrame:
    """A card. Square padding by default — it was 22/20, which is a gap you
    cannot name looking at it and is why stacked cards never quite lined up."""
    f = QFrame()
    f.setObjectName(object_name)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return f


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    return f


#: Which ink an icon takes inside each button style, so a drawn icon never
#: ends up dark-on-dark or washed out against its own background.
_BUTTON_INK = {
    "Primary": theme.ACCENT_INK,
    "Accent": theme.ACCENT_INK,
    "Lime": theme.ACCENT_DEEP,
    "ChipActive": theme.ACCENT_DEEP,
    "Danger": theme.REC,
}


def button(text: str, kind: str = "", on_click: Callable[[], None] | None = None,
           icon: str = "", icon_size: int = 16) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon:
        b.setIcon(icons.icon(icon, icon_size, _BUTTON_INK.get(kind, theme.MUTED)))
        b.setIconSize(QSize(icon_size, icon_size))
    if on_click:
        b.clicked.connect(lambda: on_click())
    return b


def icon_button(name: str, tooltip: str = "", on_click: Callable[[], None] | None = None,
                size: int = 30, icon_size: int = 18, kind: str = "IconBtn",
                colour: str = theme.MUTED) -> QPushButton:
    """A square, icon-only affordance — the toggles in the title bar."""
    b = QPushButton()
    b.setObjectName(kind)
    b.setFixedSize(size, size)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setIcon(icons.icon(name, icon_size, colour))
    b.setIconSize(QSize(icon_size, icon_size))
    if tooltip:
        b.setToolTip(tooltip)
    if on_click:
        b.clicked.connect(lambda: on_click())
    return b


#: Counter for the object names `transparent` hands out.
_TRANSPARENT_SEQ = [0]


def transparent(w: QWidget) -> QWidget:
    """Let a plain container show whatever is behind it.

    The global ``QWidget`` rule paints every widget with the app background,
    so a bare QWidget used purely to hold a layout would punch a cream hole
    in a starfield.

    The rule is scoped to this widget by object name, and that scoping is the
    whole point. An unscoped ``background: transparent`` propagates to every
    descendant — Qt applies a widget's stylesheet to its children too — which
    has now twice been a bug nobody could see the cause of: once erasing the
    fill of a white pill button inside the hero, and once turning a combo
    box's popup into a black slab with unreadable text, because the popup is
    a child of the combo and inherited the transparency from a container four
    levels up.
    """
    _TRANSPARENT_SEQ[0] += 1
    name = f"Transparent{_TRANSPARENT_SEQ[0]}"
    w.setObjectName(name)
    w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    w.setStyleSheet(f"QWidget#{name} {{ background: transparent; }}")
    return w


def tag(text: str, kind: str = "Pill") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName(kind)
    lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
    return lbl


def kbd(*keys: str) -> QWidget:
    """A row of keycaps, drawn the way the design draws a shortcut."""
    w = transparent(QWidget())
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(5)
    for k in keys:
        cap = QLabel(k)
        cap.setObjectName("Kbd")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setMinimumWidth(32)
        cap.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        row.addWidget(cap)
    return w


def hrow(*widgets, spacing: int = 10, stretch_at: int | None = None) -> QWidget:
    """A horizontal strip. ``stretch_at`` inserts the spring before that index."""
    w = transparent(QWidget())
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    for i, item in enumerate(widgets):
        if stretch_at is not None and i == stretch_at:
            row.addStretch(1)
        row.addWidget(item)
    if stretch_at is not None and stretch_at >= len(widgets):
        row.addStretch(1)
    return w


# --- choosing, switching, stating -------------------------------------------
#
# Three widgets that between them replace most of the app's prose. A page that
# had to *explain* a choice usually had the wrong control for it: three loose
# chips do not say "pick one", and a checkbox with a sentence after it does not
# say "this is a switch and it is off". Give the choice a shape and the
# sentence is no longer load-bearing.


class Segmented(QWidget):
    """One-of-N on a single track — the shape that means "pick exactly one".

    The options used to be three separate ``Chip`` buttons with the selected
    one tinted, which is the same picture as three unrelated actions where one
    happens to be highlighted. Sitting them in a shared sunken track and
    lifting the selected one out of it is what makes the group read as a
    single control with a current value.
    """

    changed = pyqtSignal(str)

    def __init__(self, options: list[str], value: str = "",
                 labels: dict[str, str] | None = None) -> None:
        super().__init__()
        self.setObjectName("SegTrackHost")
        self.setStyleSheet("QWidget#SegTrackHost{background:transparent;}")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        track = QFrame()
        track.setObjectName("SegTrack")
        track.setFixedHeight(theme.H_CONTROL)
        row = QHBoxLayout(track)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(3)

        self._buttons: dict[str, QPushButton] = {}
        for opt in options:
            b = QPushButton((labels or {}).get(opt, opt))
            b.setObjectName("SegOption")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, o=opt: self.set_value(o, notify=True))
            self._buttons[opt] = b
            row.addWidget(b)
        outer.addWidget(track)

        self._value = ""
        self.set_value(value or (options[0] if options else ""))

    def value(self) -> str:
        return self._value

    def set_label(self, option: str, text: str) -> None:
        """Retitle one option. Insane mode counts the roles each would act on."""
        if option in self._buttons:
            self._buttons[option].setText(text)

    def set_value(self, value: str, notify: bool = False) -> None:
        if value not in self._buttons:
            return
        changed = value != self._value
        self._value = value
        for opt, b in self._buttons.items():
            b.setObjectName("SegOptionOn" if opt == value else "SegOption")
            b.style().unpolish(b)
            b.style().polish(b)
        if notify and changed:
            self.changed.emit(value)


class Switch(QAbstractButton):
    """An on/off switch, painted — Qt ships no such control.

    A checkbox is a list affordance: it answers "is this one of the ones you
    picked?". Settings are not a list, they are a set of independent states,
    and a switch is the only shape that reads as one at a glance from across
    the row. The knob animates because the movement *is* the feedback —
    without it a switch and a checkbox convey the same amount.
    """

    _W, _H, _PAD = 42, 24, 3

    def __init__(self, checked: bool = False) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self._W, self._H)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._travel = float(checked)
        self._anim = QPropertyAnimation(self, b"travel", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._travel)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_travel(self) -> float:
        return self._travel

    def set_travel(self, value: float) -> None:
        self._travel = value
        self.update()

    #: How far along the knob is, 0 to 1. A Qt property so it can be animated.
    travel = pyqtProperty(float, fget=get_travel, fset=set_travel)

    def set_state(self, on: bool) -> None:
        """Set the state without animating it and without reporting it.

        Loading a settings page sets a dozen of these at once. Going through
        ``setChecked`` there would both animate every switch — the page
        arrives mid-slide — and fire each one's ``toggled`` handler, which
        saves the value that was just loaded back over itself.
        """
        blocked = self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(blocked)
        self._anim.stop()
        self.set_travel(1.0 if on else 0.0)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._W, self._H)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        enabled = self.isEnabled()
        if enabled:
            # Cross-fade the track rather than switching it at the halfway
            # point, so the colour and the knob arrive together.
            off, on = QColor(theme.LINE_3), QColor(theme.ACCENT)
            t = max(0.0, min(1.0, self._travel))
            track = QColor(
                int(off.red() + (on.red() - off.red()) * t),
                int(off.green() + (on.green() - off.green()) * t),
                int(off.blue() + (on.blue() - off.blue()) * t))
        else:
            track = QColor(theme.SURFACE_3)
        p.setBrush(track)
        p.drawRoundedRect(self.rect(), self._H / 2, self._H / 2)

        knob = self._H - 2 * self._PAD
        x = self._PAD + self._travel * (self._W - knob - 2 * self._PAD)
        p.setBrush(QColor("#FFFFFF" if enabled else theme.SURFACE_2))
        p.drawEllipse(QRect(int(round(x)), self._PAD, knob, knob))
        p.end()


class SettingRow(QWidget):
    """One setting: what it is on the left, its control on the right.

    Every setting in the app used to be a checkbox whose *label* was the
    explanation — "Fill the whole form — work out the questions Sushi cannot
    look up" — followed by a paragraph. Two problems. The row could not wrap,
    so the widest label set a floor under the whole page's width and the
    Settings card was being clipped at the right edge on a 1440px window. And
    a reader scanning for a switch had to read a sentence to find each one.

    Here the name is the name, the help is one wrapped line under it in muted
    type, and the control is always in the same place down the right edge —
    which is what lets the eye run down the column instead of reading.
    """

    def __init__(self, title: str, help_text: str = "",
                 control: QWidget | None = None) -> None:
        super().__init__()
        self.setObjectName("SettingRow")
        self.setStyleSheet("QWidget#SettingRow{background:transparent;}")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.GAP_MD)

        text = Block(spacing=2)
        self.title_label = label(title, "H3")
        text.add(self.title_label)
        # Always built, even when there is nothing to say yet: the help line
        # on a segmented row describes the *selected* option, so it only
        # arrives when the page loads its values. Creating it lazily meant
        # ``set_help`` had nowhere to put the text and those two rows shipped
        # with their explanation silently dropped.
        self.help_label = faint(help_text)
        self.help_label.setVisible(bool(help_text))
        text.add(self.help_label)
        row.addWidget(text, 1)

        self.control = control
        if control is not None:
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_help(self, text: str) -> None:
        self.help_label.setText(text)
        self.help_label.setVisible(bool(text))


def switch_row(title: str, help_text: str = "", checked: bool = False,
               on_toggle: Callable[[bool], None] | None = None
               ) -> tuple[SettingRow, Switch]:
    """A :class:`SettingRow` whose control is a :class:`Switch`."""
    sw = Switch(checked)
    if on_toggle:
        sw.toggled.connect(lambda on: on_toggle(on))
    return SettingRow(title, help_text, sw), sw


class Stat(QWidget):
    """A number over what it counts. The unit of a glanceable summary.

    Reads in about a tenth of the time the sentence it replaced did — "15
    applications left in today's cap. Insane mode is on standby." is two
    numbers and a state wearing eleven words.
    """

    def __init__(self, value: str, caption: str, on_dark: bool = False,
                 size: int = 30) -> None:
        super().__init__()
        self.setObjectName("StatBlock")
        self.setStyleSheet("QWidget#StatBlock{background:transparent;}")
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)

        # ``size`` so the same block can carry a panel. The earlier design sets its headline
        # figures far larger than its body — the number is the content, and
        # at 30px on a full-width band it reads as a caption with a number
        # attached rather than the other way round.
        self.value_label = QLabel(value)
        # Size through the stylesheet, family and tracking through the font.
        # The application sheet sets `QWidget { font-size }`, and a stylesheet
        # font-size beats anything `setFont` asks for — so every Stat in the
        # app had been drawing its figure at body size while the code said
        # 30px. QSS has no letter-spacing, hence both.
        self.value_label.setFont(theme.display_font(size, 400))
        self.value_label.setStyleSheet(
            f"background:transparent;font-size:{size}px;"
            f"color:{'#FFFFFF' if on_dark else theme.INK};")
        col.addWidget(self.value_label)

        self.caption_label = QLabel(caption)
        self.caption_label.setStyleSheet(
            f"background:transparent;font-size:{theme.SIZE_SMALL}px;"
            f"font-weight:600;color:"
            f"{'rgba(255,255,255,0.72)' if on_dark else theme.MUTED};")
        col.addWidget(self.caption_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)

    def set_caption(self, caption: str) -> None:
        self.caption_label.setText(caption)


# --- icon tile --------------------------------------------------------------

class IconTile(QLabel):
    """The rounded tinted square the design puts beside every list row.

    Holds a glyph (an emoji or a letter) rather than an SVG: The earlier design's icon set is
    inline SVG paths exported from the design tool, which do not transfer, and
    Windows renders emoji as full-colour glyphs at any size.
    """

    def __init__(self, what: str, size: int = 40, tint: str = theme.TINT_GREEN,
                 ink: str = theme.TINT_GREEN_INK) -> None:
        """``what`` is an icon name from :mod:`icons`, or a letter to set.

        Company badges want the initial; everything else wants a drawn icon.
        One widget covers both so the tile geometry only exists in one place.
        """
        super().__init__()
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._what = what
        self._size = size
        self._radius = theme.R_TILE if size >= 32 else 6
        self.set_tint(tint, ink)

    def set_tint(self, tint: str, ink: str) -> None:
        """Re-colour in place.

        A selected sidebar row is a solid olive block, and a tile that keeps
        its pale wash sits on it as a lighter rectangle — the one element that
        did not get the memo. Re-tinting beats rebuilding the row: the icon is
        redrawn at the right colour rather than scaled or recoloured as a
        bitmap, so it stays crisp.
        """
        self._tint, self._ink = tint, ink
        if self._what in icons.ICONS:
            # Drawn, not a font glyph: the symbol font's shapes were a mix of
            # weights and eras, and Windows handed several of them to the
            # colour emoji font regardless of what we asked for.
            self.setPixmap(icons.pixmap(
                self._what, max(13, int(self._size * 0.62)), ink))
            self.setStyleSheet(f"background:{tint};border-radius:{self._radius}px;")
        else:
            self.setText(self._what)
            self.setStyleSheet(
                f"background:{tint};color:{ink};border-radius:{self._radius}px;"
                f"font-size:{int(self._size * 0.42)}px;font-weight:600;")


class _LogoLoader(QObject):
    """Fetches company logos off the UI thread and hands them to the waiters.

    One loader for the whole app, for two reasons. A feed of nine cards plus
    a table of twelve rows contains the same employer several times, and
    without a shared in-flight set each copy would issue its own request.
    And a widget must never be touched from a worker, so the result comes
    back as a path rather than as a pixmap.

    Waiters are held as **weak references, not signal connections**. This is
    the whole point of the class now. It used to expose a ``ready`` signal
    that every ``CompanyLogo`` connected to and none ever disconnected from —
    and the loader is a module-level global, so it outlives all of them. The
    feed and the applications table rebuild from scratch on every refresh, a
    couple of hundred widgets at a time, which during an Insane mode run
    happened every few seconds: tens of thousands of connections to receivers
    that had already been destroyed, each one walked on every arrival. A
    weakref list skips a dead widget instead of reaching into it, and the
    list is swept each time rather than growing for ever.
    """

    def __init__(self) -> None:
        super().__init__()
        self._inflight: set[str] = set()
        self._pool = QThreadPool.globalInstance()
        self._waiting: dict[str, list[weakref.ref]] = {}
        self._inflight_workers: set = set()   # see Worker.keep_alive

    def wait_for(self, key: str, widget: QWidget) -> None:
        """Tell this widget when *key*'s logo arrives, if it is still alive."""
        if not key:
            return
        waiters = [w for w in self._waiting.get(key, []) if w() is not None]
        waiters.append(weakref.ref(widget))
        self._waiting[key] = waiters

    def _deliver(self, key: str, path: str) -> None:
        for ref in self._waiting.pop(key, []):
            widget = ref()
            if widget is None:
                continue
            try:
                widget.logo_arrived(path)
            except RuntimeError:
                # The C++ half has gone even though Python still holds the
                # wrapper. Nothing to tell, and nothing to repair.
                continue

    def request(self, company: str, hint: str = "") -> None:
        from .. import logos

        key = logos.normalise(company)
        if not key or key in self._inflight or logos.known_miss(company):
            return
        self._inflight.add(key)

        def job():
            try:
                return logos.fetch(company, hint)
            finally:
                self._inflight.discard(key)

        def done(path) -> None:
            if path:
                self._deliver(key, str(path))

        worker = Worker(job)
        worker.signals.finished.connect(done)
        # A logo is decoration: a failure must never surface to the user.
        worker.signals.failed.connect(lambda _m: None)
        worker.keep_alive(self._inflight_workers)
        self._pool.start(worker)


_LOADER: _LogoLoader | None = None


def logo_loader() -> _LogoLoader:
    global _LOADER
    if _LOADER is None:
        _LOADER = _LogoLoader()
    return _LOADER


class CompanyLogo(QLabel):
    """The employer's mark, with the lettered tile as its loading state.

    The letter is not a placeholder to be apologised for — it is what gets
    shown when a company has no findable logo, which is many of them. So it
    is drawn first, always, and replaced only if a real mark arrives.
    """

    def __init__(self, company: str, size: int = 28, hint: str = "",
                 tint: str = theme.TINT_GREEN, ink: str = theme.TINT_GREEN_INK) -> None:
        super().__init__()
        from .. import logos

        self._company = company
        self._key = logos.normalise(company)
        self._size = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._radius = theme.R_TILE if size >= 32 else 6

        self._show_letter(tint, ink)
        cached = logos.cached_path(company)
        if cached:
            self._show_logo(str(cached))
        else:
            loader = logo_loader()
            loader.wait_for(self._key, self)
            loader.request(company, hint)

    def _show_letter(self, tint: str, ink: str) -> None:
        self.setText((self._company or "?")[:1].upper())
        self.setStyleSheet(
            f"background:{tint};color:{ink};border-radius:{self._radius}px;"
            f"font-size:{int(self._size * 0.42)}px;font-weight:600;")

    #: (path, size) -> scaled pixmap. Decoding and smooth-scaling a PNG cost
    #: ~2.5ms each, which was a third of a 200-row table redraw — for the
    #: same few dozen logos, every time.
    _PIXMAPS: dict[tuple[str, int], QPixmap] = {}

    def _show_logo(self, path: str) -> None:
        # Logos arrive anywhere from 16px to 128px square. Scaled down with a
        # smooth transform and inset slightly, so a mark with no padding of
        # its own does not touch the edge of its tile.
        inner = max(8, self._size - 8)
        pm = self._PIXMAPS.get((path, inner))
        if pm is None:
            pm = QPixmap(path)
            if pm.isNull():
                return
            pm = pm.scaled(inner, inner, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            if len(self._PIXMAPS) > 2000:
                self._PIXMAPS.clear()
            self._PIXMAPS[(path, inner)] = pm
        self.setText("")
        self.setPixmap(pm)
        self.setStyleSheet(
            f"background:{theme.SURFACE};border:1px solid {theme.LINE};"
            f"border-radius:{self._radius}px;")

    def logo_arrived(self, path: str) -> None:
        """Called by the loader, on the UI thread, for this company only."""
        self._show_logo(path)


class ElidedLabel(QLabel):
    """A single-line label that ends in an ellipsis instead of being cut.

    A table column is whatever width the table gives it, and a plain QLabel
    handed too little simply loses the end of the word — "Senior Operations
    Analys". An ellipsis is a smaller lie, and it tells you one was told.
    """

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self._full = text
        self.setMinimumWidth(1)   # never force the column open

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        super().setText(text)
        self._elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        from PyQt6.QtGui import QFontMetrics
        if not self._full:
            return
        fm = QFontMetrics(self.font())
        shown = fm.elidedText(self._full, Qt.TextElideMode.ElideRight,
                              max(0, self.width()))
        if shown != super().text():
            super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


#: status -> (label, icon, object name). The tick and the cross are the
#: reference table's whole trick: a column you can read down at a glance
#: without parsing a word of it. The in-between states get a dot rather than
#: a third symbol, because "not finished yet" is not a verdict.
STATUS_LOOK = {
    "submitted":    ("Submitted",  "check",      "StatusGood"),
    "needs_review": ("Needs you",  "dot-filled", "StatusWarn"),
    "filling":      ("Filling",    "dot-filled", "StatusWarn"),
    "queued":       ("Queued",     "dot-filled", "StatusIdle"),
    "matched":      ("Matched",    "dot-filled", "StatusIdle"),
    "failed":       ("Failed",     "x",          "StatusBad"),
    "skipped":      ("Passed",     "x",          "StatusIdle"),
    # Not a state an application reaches — it is Insane mode reporting that
    # the run itself cannot start, in the row where a role would have been.
    "blocked":      ("Blocked",    "x",          "StatusBad"),
}

STATUS_INK = {
    "StatusGood": theme.GOOD, "StatusWarn": theme.WARN,
    "StatusBad": theme.BAD, "StatusIdle": theme.FAINT,
}


def status_cell(status: str) -> QWidget:
    """A glyph and a word, for one row of the applications table."""
    label_text, icon_name, kind = STATUS_LOOK.get(
        status, (status.replace("_", " ").title(), "dot-filled", "StatusIdle"))
    host = QWidget()
    host.setObjectName("StatusCell")
    host.setStyleSheet("QWidget#StatusCell{background:transparent;}")
    row = QHBoxLayout(host)
    row.setContentsMargins(18, 0, 18, 0)
    row.setSpacing(8)

    glyph = QLabel()
    glyph.setPixmap(icons.pixmap(icon_name, 15 if icon_name != "dot-filled" else 9,
                                 STATUS_INK[kind]))
    glyph.setStyleSheet("background:transparent;")
    row.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)

    text = QLabel(label_text)
    text.setObjectName(kind)
    row.addWidget(text, 1, Qt.AlignmentFlag.AlignVCenter)
    return host


#: Tall enough for a 28px tile with air around it. The default 30px row is
#: what made these read as a spreadsheet rather than a list of things that
#: happened.
ROW_HEIGHT = 56


class _ColumnWeights(QObject):
    """Keeps a table's flexible columns in proportion at any width.

    Qt's ``Stretch`` mode would do this if every flexible column deserved the
    same share of the slack. They do not. ROLE is the column you actually
    read and LOCATION is one you glance at, and the old arrangement — one
    stretch column and four fixed ones adding up to 618px — left ROLE with
    whatever was left over. On a 1440px window with the assistant open that
    was 170px, so every role on the page read "Senior Data An…" while
    LOCATION sat at 176px showing "Remote, Spain" and most of a blank cell.

    Weights are resolved against the viewport on every resize, so the row
    ends on the right edge at any window size instead of only the one it was
    measured on.
    """

    def __init__(self, table: QTableWidget, fixed: dict[int, int],
                 weights: dict[int, float], minimum: int = 88) -> None:
        super().__init__(table)
        self._table = table
        self._fixed = fixed
        self._weights = weights
        self._minimum = minimum
        table.viewport().installEventFilter(self)
        self.apply()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize:
            self.apply()
        return False

    def apply(self) -> None:
        t = self._table
        spare = t.viewport().width() - sum(self._fixed.values())
        total = sum(self._weights.values()) or 1.0
        cols = list(self._weights)
        used = 0
        # The last column absorbs the rounding remainder, so the columns add
        # up to the viewport exactly rather than leaving a two-pixel gutter
        # the header's banding would show as a notch.
        for col in cols[:-1]:
            w = max(self._minimum, int(spare * self._weights[col] / total))
            t.setColumnWidth(col, w)
            used += w
        if cols:
            t.setColumnWidth(cols[-1], max(self._minimum, spare - used))


def tune_table(t: QTableWidget, widths: dict[int, int] | None = None,
               stretch: int = 1, weights: dict[int, float] | None = None
               ) -> None:
    """The house style for a data table — banding, no grid, sized columns.

    Lives here rather than on a page because there are three of these now:
    the applications table on the Dashboard, All jobs, and the live run on
    Insane mode. They are the same object in several places, and a page
    importing another page to borrow its private helper is not a
    relationship those two should have.

    ``widths`` fixes specific columns. Then either ``stretch`` names the one
    column that takes whatever is left, or ``weights`` shares the slack out
    between several in proportion — see :class:`_ColumnWeights` for why a
    table with two readable columns needs the second form.
    """
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    t.verticalHeader().setVisible(False)
    t.setShowGrid(False)
    # The banding does the work vertical rules would, and does it without
    # drawing a line across a white card for every column.
    t.setAlternatingRowColors(True)
    t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    t.setMinimumHeight(260)
    t.setWordWrap(False)

    head = t.horizontalHeader()
    head.setFixedHeight(46)
    head.setHighlightSections(False)
    head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter)
    # Fixed, not ResizeToContents. Sizing to contents let one long company
    # name squeeze every other column, and the columns that lost were the
    # ones holding a whole word — "Submitte", "Ope".
    head.setMinimumSectionSize(40 if weights else 96)
    head.setStretchLastSection(False)
    for col in range(t.columnCount()):
        head.setSectionResizeMode(
            col, QHeaderView.ResizeMode.Stretch
            if (weights is None and col == stretch)
            else QHeaderView.ResizeMode.Fixed)
    for col, width in (widths or {}).items():
        t.setColumnWidth(col, width)
    if weights:
        # Parented to the table, so it lives exactly as long as the columns
        # it is keeping in proportion.
        t._column_weights = _ColumnWeights(t, dict(widths or {}), weights)

    # Banding has to be set on the palette; the stylesheet's
    # alternate-background-color is ignored once a cell holds a widget.
    pal = t.palette()
    pal.setColor(QPalette.ColorRole.Base, QColor(theme.SURFACE))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(theme.BG))
    t.setPalette(pal)

    scroll_below_header(t)


#: How far short of the table's bottom edge the scrollbar stops, so the
#: track ends inside the card's own rounded corner rather than running into
#: it. The same reason it is pushed off the top.
_SCROLL_INSET = 4


class _ScrollBelowHeader(QObject):
    """Keeps a table's vertical scrollbar level with its rows.

    Qt runs the scrollbar down the whole height of the scroll area, the
    band the header sits in included — so on the applications table it
    reached up beside the clay header's rounded top-right corner and sat
    there as a grey bar starting level with it. It can only ever scroll the
    rows, so it should start where they do.

    Not a stylesheet margin: ``QAbstractScrollArea`` lays its own
    scrollbars out and overwrites whatever the style asked for. Measured —
    the very same selector will happily change the scrollbar's *width*,
    which is what makes the failure worth writing down rather than
    rediscovering.

    The geometry is worked out from the table rather than from the
    viewport, and that is not arbitrary either: Qt resizes the scrollbar
    before it resizes the viewport, so a viewport-derived height is one
    layout pass stale and the bar hangs past the bottom of the table after
    every resize. The table's own height is right at the moment either of
    them is asked about.
    """

    def __init__(self, table) -> None:
        super().__init__(table)
        self._table = table
        self._busy = False
        table.verticalScrollBar().installEventFilter(self)
        table.installEventFilter(self)

    def _fix(self) -> None:
        if self._busy:
            return
        table = self._table
        bar = table.verticalScrollBar()
        frame = table.frameWidth()
        top = table.horizontalHeader().height() + frame
        want = QRect(bar.x(), top, bar.width(),
                     max(0, table.height() - top - frame - _SCROLL_INSET))
        if bar.geometry() != want:
            # Guarded, because setting the geometry is itself a move and a
            # resize, and the filter below would hear both.
            self._busy = True
            bar.setGeometry(want)
            self._busy = False

    def eventFilter(self, _obj, event) -> bool:
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move,
                            QEvent.Type.Show, QEvent.Type.LayoutRequest):
            self._fix()
        return False


def scroll_below_header(table) -> None:
    """Start this table's scrollbar under its header. See the class above."""
    # Parented to the table, so it lives exactly as long as the scrollbar
    # it is keeping in place.
    table._scroll_below_header = _ScrollBelowHeader(table)


def company_cell(name: str, tint_index: int = 0) -> QWidget:
    """The initial in a tinted square, then the name.

    Six rows of plain left-aligned text are hard to scan; an initial gives
    each row an anchor the eye can find again after it has tracked right.
    """
    tints = [(theme.TINT_GREEN, theme.TINT_GREEN_INK),
             (theme.TINT_BLUE, theme.TINT_BLUE_INK),
             (theme.TINT_SAND, theme.TINT_SAND_INK),
             (theme.TINT_CLAY, theme.TINT_CLAY_INK),
             (theme.TINT_ROSE, theme.TINT_ROSE_INK)]
    tint, ink = tints[tint_index % len(tints)]

    host = QWidget()
    host.setObjectName("CompanyCell")
    host.setStyleSheet("QWidget#CompanyCell{background:transparent;}")
    row = QHBoxLayout(host)
    row.setContentsMargins(theme.CELL_PAD, 0, theme.GAP_SM, 0)
    row.setSpacing(theme.GAP_SM)
    row.addWidget(CompanyLogo(name, 26, tint=tint, ink=ink),
                  0, Qt.AlignmentFlag.AlignVCenter)
    text = ElidedLabel(name or "Unknown")
    text.setStyleSheet(
        f"background:transparent;color:{theme.INK};font-size:{theme.SIZE_SMALL}px;"
        f"font-weight:600;")
    row.addWidget(text, 1, Qt.AlignmentFlag.AlignVCenter)
    return host


def text_cell(text: str, muted_text: bool = False, mono: bool = False) -> QWidget:
    """A plain cell, padded to match the ones that hold widgets.

    QTableWidgetItem padding and QWidget margins do not agree with each
    other, so a table that mixes them steps in and out by a few pixels down
    the row. Everything is a widget here instead.
    """
    host = QWidget()
    host.setObjectName("TextCell")
    host.setStyleSheet("QWidget#TextCell{background:transparent;}")
    row = QHBoxLayout(host)
    row.setContentsMargins(theme.CELL_PAD, 0, theme.CELL_PAD, 0)
    row.setSpacing(0)
    label_ = ElidedLabel(text)
    colour = theme.FAINT if muted_text else theme.INK
    family = f"font-family:'{theme.MONO_FONT}';" if mono else ""
    label_.setStyleSheet(
        f"background:transparent;color:{colour};"
        f"font-size:{theme.SIZE_SMALL}px;{family}")
    row.addWidget(label_, 1, Qt.AlignmentFlag.AlignVCenter)
    return host


class Dot(QLabel):
    """A small filled circle — a status light, or an onboarding step marker."""

    def __init__(self, colour: str = theme.ACCENT, size: int = 8) -> None:
        super().__init__("")
        self.setFixedSize(size, size)
        self.setStyleSheet(f"background:{colour};border-radius:{size // 2}px;")


# --- flow layout ------------------------------------------------------------

class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, margin: int = 0, spacing: int = 12) -> None:
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = spacing
        self._items: list[QLayoutItem] = []

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            next_x = x + w + self._spacing
            if next_x - self._spacing > rect.right() and line_h > 0:
                x = rect.x()
                y = y + line_h + self._spacing
                next_x = x + w + self._spacing
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_h = max(line_h, h)
        return y + line_h - rect.y()


# --- match score ring -------------------------------------------------------

class RingBadge(QWidget):
    """A thin circular ring showing the match percentage.

    Drawn in the olive accent rather than a traffic-light scale: the design
    allows one saturated colour per screen, and a grid of nine cards each with
    its own green/amber/red ring is the first thing that breaks that.
    """

    def __init__(self, percent: int, diameter: int = 46) -> None:
        super().__init__()
        self.percent = max(0, min(100, percent))
        self.d = diameter
        self.setFixedSize(diameter, diameter)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRect(3, 3, self.d - 6, self.d - 6)
        p.setPen(QPen(QColor(theme.SURFACE_3), 3))
        p.drawArc(rect, 0, 360 * 16)
        p.setPen(QPen(QColor(theme.ACCENT), 3, cap=Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 90 * 16, -int(360 * 16 * self.percent / 100))
        p.setPen(QColor(theme.INK))
        p.setFont(theme.body_font(12, 600))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{self.percent}")
        p.end()


def score_to_percent(score: int) -> int:
    """Map an internal score (roughly 0-20) to a friendly match percentage."""
    return max(52, min(99, 58 + score * 2))


class LogoDot(QLabel):
    """A round company badge showing the first initial."""

    def __init__(self, name: str, size: int = 24) -> None:
        super().__init__((name or "?")[:1].upper())
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"background:{theme.SURFACE_2};border:1px solid {theme.LINE};"
            f"border-radius:{size // 2}px;color:{theme.MUTED};font-weight:600;font-size:11px;")


def _elide(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# --- job card ---------------------------------------------------------------

class JobCard(QFrame):
    """A job, as the design presents one.

    The first version led with the company in bold and buried the title in
    muted body text, which read as a list of employers rather than a list of
    jobs. This is the redesign: company and match percentage on one line, the
    role as the headline, then the facts — where and how much — in a ruled
    row, and the board it came from in mono beside the action.

    Mono on the salary and the board is doing real work. It marks them as
    *reported* rather than written, which is the distinction that matters on a
    screen where an agent composes some of the text.
    """

    apply_clicked = pyqtSignal(object)
    pass_clicked = pyqtSignal(object)
    details_clicked = pyqtSignal(object)
    save_clicked = pyqtSignal(object)

    def __init__(self, role, show_save: bool = True, tint_index: int = 0) -> None:
        super().__init__()
        self.role = role
        self.setObjectName("JobCard")
        self.setFixedWidth(300)
        # Every card is the same height whatever its title does, because the
        # title's own box is fixed (two lines) rather than the card having a
        # floor and a spring. With a spring, a one-line title left a 40px
        # band of nothing between the role and the rule under it on some
        # cards and not others — a ragged grid for no reason the reader can
        # see. Fixed: one line sits at the top of a two-line box, two lines
        # fill it, and the facts row is at the same y on every card.
        self.setFixedHeight(200)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 15, 18, 14)
        lay.setSpacing(0)

        # logo · company · match
        top = QHBoxLayout()
        top.setSpacing(8)
        # The mark goes first and the name keeps its quiet weight. A logo you
        # recognise does the identifying; making the name bold as well would
        # be two elements competing to say the same thing.
        tints = [(theme.TINT_GREEN, theme.TINT_GREEN_INK),
                 (theme.TINT_BLUE, theme.TINT_BLUE_INK),
                 (theme.TINT_SAND, theme.TINT_SAND_INK),
                 (theme.TINT_CLAY, theme.TINT_CLAY_INK),
                 (theme.TINT_ROSE, theme.TINT_ROSE_INK)]
        tint, ink = tints[tint_index % len(tints)]
        top.addWidget(CompanyLogo(role.company, 22, hint=role.token or "",
                                  tint=tint, ink=ink),
                      0, Qt.AlignmentFlag.AlignVCenter)
        company = faint(_elide(role.company, 18))
        company.setWordWrap(False)
        top.addWidget(company, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch()
        pct = role.match or score_to_percent(role.score)
        match = label(f"{pct}% match", "Match", wrap=False)
        top.addWidget(match)
        lay.addLayout(top)
        lay.addSpacing(10)

        title = label(_elide(role.title, 58), "CardTitle")
        title.setFixedHeight(44)
        title.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(title)
        # Why it matched, in the band that was empty under the title. One
        # line, so every card stays the same height.
        why = " · ".join(r for r in (role.reasons or [])[:3] if r)
        if why:
            reasons = label(_elide(why, 46), "Faint", wrap=False)
            reasons.setToolTip(" · ".join(role.reasons))
            lay.addWidget(reasons)
        lay.addStretch()
        lay.addSpacing(8)

        # where · how much, ruled off from the headline above
        lay.addWidget(divider())
        lay.addSpacing(10)
        facts = QHBoxLayout()
        facts.setSpacing(10)
        # One line: the text is already elided to twenty characters, and a
        # wrapping label handed a left alignment takes its single-line hint
        # and then wraps inside it — "Remote (UK)" came out as two lines in a
        # row sized for one.
        where = label(_elide(role.location or "Remote", 20), "Faint", wrap=False)
        where.setMaximumWidth(120)
        # The alignment has to be the *layout's*, not the label's. The label
        # is capped at 120px but handed a stretched cell, and a widget
        # narrower than its cell is centred in it — so on any card without a
        # salary beside it the location drifted into the middle of the row
        # while every other card had it against the left edge. Aligning the
        # text inside the label does not move the label.
        facts.addWidget(where, 1, Qt.AlignmentFlag.AlignLeft
                        | Qt.AlignmentFlag.AlignVCenter)
        if role.salary:
            money = label(_elide(role.salary, 22), "Mono", wrap=False)
            money.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            facts.addWidget(money)
        lay.addLayout(facts)
        lay.addSpacing(12)

        # provenance · action
        foot = QHBoxLayout()
        foot.setSpacing(8)
        if role.ats:
            foot.addWidget(label(f"via {role.ats.title()}", "Mono", wrap=False))
        foot.addStretch()
        apply_btn = button("Instant Apply", "InstantApply",
                           lambda: self.apply_clicked.emit(self.role),
                           icon="arrow-right", icon_size=14)
        apply_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        foot.addWidget(apply_btn)
        lay.addLayout(foot)

        # Save and Pass are not on the face of the card any more — the design
        # gives the card one action and puts the rest behind the row. Kept as
        # a context menu so neither is lost.
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self._show_save = show_save

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.details_clicked.emit(self.role)
        super().mouseReleaseEvent(event)

    def _menu(self, pos) -> None:
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction("Details", lambda: self.details_clicked.emit(self.role))
        if self._show_save:
            menu.addAction("Save", lambda: self.save_clicked.emit(self.role))
        menu.addAction("Pass", lambda: self.pass_clicked.emit(self.role))
        menu.exec(self.mapToGlobal(pos))
