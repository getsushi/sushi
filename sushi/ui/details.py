"""The job details drawer — what opens when you click a card.

A modal dialog was the wrong shape for this. Reading a posting is something
you do *while* scanning a feed: you open one, decide in four seconds, close
it, open the next. A dialog takes the whole window hostage for that, has to
be dismissed before you can look at anything else, and loses your place in
the grid every time.

So it is a drawer over the right-hand side, as in Tsenta: the feed stays
where it was underneath, a scrim dims it without hiding it, and Escape or a
click outside puts you straight back. The facts are a two-column grid of
labelled rows rather than a paragraph, because location / contract /
on-site / department are four lookups, not a sentence — and the one action
that matters sits in a footer that never scrolls away.
"""
from __future__ import annotations

import re

from PyQt6.QtCore import (QEasingCurve, QEvent, QPoint, QPropertyAnimation,
                          Qt, pyqtSignal)
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea,
                             QVBoxLayout, QWidget)

from ..models import Role
from . import icons, theme
from .components import CompanyLogo, FlowLayout, button, divider, label

#: The drawer's width, and the widest it is allowed to get on a small window.
WIDTH = 520
MIN_MARGIN = 80


class Scrim(QWidget):
    """The dim layer behind the drawer. Clicking it closes."""

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._alpha = 0

    def set_alpha(self, value: int) -> None:
        self._alpha = value
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 24, 12, self._alpha))
        p.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()
        event.accept()


def _fact(icon: str, text: str) -> QWidget:
    """One labelled fact: a glyph and a value, on one line."""
    host = QWidget()
    host.setObjectName("Fact")
    host.setStyleSheet("QWidget#Fact{background:transparent;}")
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(9)
    glyph = QLabel()
    glyph.setPixmap(icons.pixmap(icon, 15, theme.MUTED))
    glyph.setStyleSheet("background:transparent;")
    row.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)
    value = QLabel(text)
    value.setStyleSheet(
        f"background:transparent;color:{theme.INK};"
        f"font-size:{theme.SIZE_SMALL}px;")
    value.setWordWrap(False)
    row.addWidget(value, 1, Qt.AlignmentFlag.AlignVCenter)
    return host


def _chip(text: str) -> QLabel:
    tag = QLabel(text)
    tag.setObjectName("TagGreen")
    return tag


#: Patterns that say what kind of contract a posting is, read off the title
#: and description because almost no board puts it in a field of its own.
_KIND = [
    (re.compile(r"\bintern(ship)?\b", re.I), "Internship"),
    (re.compile(r"\bgraduate|\bnew grad\b", re.I), "Graduate"),
    (re.compile(r"\bcontract(or)?\b|\bfixed[- ]term\b", re.I), "Contract"),
    (re.compile(r"\bpart[- ]time\b", re.I), "Part time"),
    (re.compile(r"\btemp(orary)?\b", re.I), "Temporary"),
]
_REMOTE = re.compile(r"\bremote\b|\bwork from home\b|\bwfh\b", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)


#: A line that is its own item whatever follows it.
_BULLET = re.compile(r"^\s*([-*\u2022\u00b7\u2013]|\d+[.)])\s")
#: A continuation starts lower-case. This is the whole heuristic, and it is
#: far better than guessing from the *previous* line's length: "About the
#: Role" and "Line one of a paragraph" are both short, capitalised and
#: punctuation-free, and only one of them is a heading. What separates them
#: is what comes next — "We are hiring." begins a new thought, "broken by
#: the board." is the rest of this one.
_CONTINUES = re.compile(r"^\s*[a-z(\u201c\"']")


def reflow(text: str) -> str:
    """Join lines the source broke mid-sentence; keep the real breaks.

    Job descriptions arrive hard-wrapped at whatever width the board's HTML
    happened to be. Rendered into a column of a different width that reads
    like a ransom note — every third line stops early. Blank lines, bullets
    and headings keep their breaks, because those are the author's and not
    the layout's.
    """
    out: list[str] = []
    for para in (text or "").split("\n\n"):
        lines = [l.rstrip() for l in para.splitlines() if l.strip()]
        joined: list[str] = []
        for line in lines:
            glue = (joined
                    and _CONTINUES.match(line)
                    and not _BULLET.match(line)
                    and not _BULLET.match(joined[-1]))
            if glue:
                joined[-1] = f"{joined[-1]} {line.strip()}"
            else:
                joined.append(line)
        if joined:
            out.append("\n".join(joined))
    return "\n\n".join(out)


def contract_kind(role: Role) -> str:
    hay = f"{role.title} {role.description[:1500]}"
    for pattern, name in _KIND:
        if pattern.search(hay):
            return name
    return "Full time"


def workplace(role: Role) -> str:
    """Remote, Hybrid or On site. The rule lives in ``discovery`` now that
    it decides a score and not just a line in this drawer."""
    from ..discovery import workplace_of

    return workplace_of(role.location, role.title, role.description)


class DetailsPanel(QFrame):
    """The drawer itself. One instance, refilled per role."""

    apply_clicked = pyqtSignal(object)
    pass_clicked = pyqtSignal(object)
    save_clicked = pyqtSignal(object)
    open_clicked = pyqtSignal(object)
    closed = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("DetailsPanel")
        self._role: Role | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- header: title, company, close -------------------------------
        head = QWidget()
        head.setObjectName("DetailsHead")
        head.setStyleSheet("QWidget#DetailsHead{background:transparent;}")
        hv = QVBoxLayout(head)
        hv.setContentsMargins(30, 26, 22, 18)
        hv.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.title = label("", "H2")
        self.title.setWordWrap(True)
        top.addWidget(self.title, 1)
        close = button("", "IconBtn", self.hide_panel, icon="x", icon_size=16)
        close.setFixedSize(30, 30)
        top.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        hv.addLayout(top)

        who = QHBoxLayout()
        who.setSpacing(9)
        self.logo_slot = QWidget()
        self.logo_slot.setFixedSize(22, 22)
        self.logo_slot.setStyleSheet("background:transparent;")
        self._logo_lay = QHBoxLayout(self.logo_slot)
        self._logo_lay.setContentsMargins(0, 0, 0, 0)
        who.addWidget(self.logo_slot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.company = label("", "Muted", wrap=False)
        who.addWidget(self.company, 0, Qt.AlignmentFlag.AlignVCenter)
        self.match = label("", "Match", wrap=False)
        who.addStretch(1)
        who.addWidget(self.match, 0, Qt.AlignmentFlag.AlignVCenter)
        hv.addLayout(who)
        root.addWidget(head)

        # --- body ----------------------------------------------------------
        self.body_host = QWidget()
        self.body_host.setObjectName("DetailsBody")
        self.body_host.setStyleSheet("QWidget#DetailsBody{background:transparent;}")
        self.body = QVBoxLayout(self.body_host)
        self.body.setContentsMargins(30, 4, 26, 26)
        self.body.setSpacing(14)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.body_host)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Nothing inside may demand more width than the drawer has. Without
        # this one long unbroken line in a description silently widens the
        # whole column and everything else stops wrapping with it.
        self.body_host.setMinimumWidth(1)
        root.addWidget(scroll, 1)

        # --- footer: the actions, which never scroll away ------------------
        foot = QWidget()
        foot.setObjectName("DetailsFoot")
        fv = QHBoxLayout(foot)
        fv.setContentsMargins(22, 14, 22, 16)
        fv.setSpacing(8)
        # "View original posting" used to live here. It has moved up into
        # the body, where the description would be — see ``show_role``.
        fv.addStretch(1)
        fv.addWidget(button("Not interested", "Link",
                            lambda: self._act(self.pass_clicked)))
        fv.addWidget(button("", "IconBtn", lambda: self._act(self.save_clicked),
                            icon="bookmark", icon_size=16))
        self.apply_btn = button("Apply", "Primary",
                                lambda: self._act(self.apply_clicked))
        self.apply_btn.setMinimumWidth(104)
        fv.addWidget(self.apply_btn)
        root.addWidget(foot)

        self.hide()

    # --- filling ----------------------------------------------------------

    def show_role(self, role: Role) -> None:
        self._role = role
        self.title.setText(role.title or "Untitled role")
        self.company.setText(role.company or "Unknown company")
        pct = role.match or 0
        self.match.setText(f"{pct}% match" if pct else "")
        self.match.setVisible(bool(pct))

        while self._logo_lay.count():
            item = self._logo_lay.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._logo_lay.addWidget(
            CompanyLogo(role.company, 22, hint=role.token or ""))

        self._clear(self.body)

        # Facts, two to a row — the shape the eye expects for a lookup.
        facts = [("compass", role.location or "Location not given"),
                 ("clock", contract_kind(role)),
                 ("grid", workplace(role)),
                 ("layers", role.department or (role.ats or "").title() or "—")]
        if role.salary:
            facts.append(("flame", role.salary))
        grid = QVBoxLayout()
        grid.setSpacing(10)
        for i in range(0, len(facts), 2):
            pair = QHBoxLayout()
            pair.setSpacing(18)
            for icon, text in facts[i:i + 2]:
                pair.addWidget(_fact(icon, text), 1)
            if len(facts[i:i + 2]) == 1:
                pair.addStretch(1)
            grid.addLayout(pair)
        self.body.addLayout(grid)

        if role.reasons:
            self.body.addWidget(divider())
            self.body.addWidget(label("Why this matches you", "H3"))
            # A flow, not a row. Three chips in a QHBoxLayout set a minimum
            # width wider than the drawer, which pushed the scroll area's
            # content past the panel edge — and with it the description,
            # which then had nothing to wrap against.
            holder = QWidget()
            holder.setObjectName("Chips")
            holder.setStyleSheet("QWidget#Chips{background:transparent;}")
            chips = FlowLayout(holder, spacing=6)
            for reason in role.reasons[:5]:
                chips.addWidget(_chip(reason[:52]))
            self.body.addWidget(holder)

        self.body.addWidget(divider())

        # The description, when the board publishes one. Most do not: a
        # Greenhouse board listing carries the title, the location and the
        # department and nothing else, so a "Description" heading over an
        # apology was the commonest thing this drawer showed. Where there
        # is no text, the heading and the apology both go and the link to
        # the real posting takes their place — which is what you wanted
        # from the section anyway.
        text = reflow((role.description or "").strip())
        if text:
            self.body.addWidget(label("Description", "H3"))
            desc = label(text[:12000], "Muted")
            desc.setWordWrap(True)
            desc.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            self.body.addWidget(desc)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(button(
            "Read the full posting" if not text else "View original posting",
            "Chip", lambda: self._emit(self.open_clicked),
            icon="external-link"))
        row.addStretch(1)
        self.body.addLayout(row)
        self.body.addStretch(1)

    @staticmethod
    def _clear(lay) -> None:
        while lay.count():
            item = lay.takeAt(0)
            if item is None:
                continue
            # Bound once. ``item.widget()`` is re-resolved on every call and
            # the second one returns None after the first has reparented it,
            # so calling it three times raised "'NoneType' object has no
            # attribute 'deleteLater'" on the way out — which is the error
            # that came up on Details.
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
                continue
            child = item.layout()
            if child is not None:
                DetailsPanel._clear(child)

    # --- actions ----------------------------------------------------------

    def _emit(self, signal) -> None:
        if self._role is not None:
            signal.emit(self._role)

    def _act(self, signal) -> None:
        """Act, then get out of the way — the next job is behind this one."""
        self._emit(signal)
        self.hide_panel()

    def hide_panel(self) -> None:
        self.closed.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.hide_panel()
            return
        super().keyPressEvent(event)


class DetailsOverlay(QWidget):
    """Scrim plus drawer, sized and animated over the window."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self._host = host
        self.scrim = Scrim(self)
        self.panel = DetailsPanel(self)
        self.scrim.clicked.connect(self.close_panel)
        self.panel.closed.connect(self.close_panel)
        self._anim: QPropertyAnimation | None = None
        self.hide()
        host.installEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self._fit()
        return False

    def _fit(self) -> None:
        self.setGeometry(0, 0, self._host.width(), self._host.height())
        self.scrim.setGeometry(0, 0, self.width(), self.height())
        w = min(WIDTH, max(320, self.width() - MIN_MARGIN))
        self.panel.setGeometry(self.width() - w, 0, w, self.height())

    def open_role(self, role) -> None:
        self._fit()
        self.panel.show_role(role)
        self.show()
        self.raise_()
        # Showing the overlay is not enough: the panel hides itself on
        # construction so it never flashes before the first role, and a
        # hidden child of a shown parent stays hidden.
        self.scrim.show()
        self.panel.show()
        self.panel.raise_()
        self.panel.setFocus()
        # Slide in from the edge. 180ms: long enough to read as movement,
        # short enough that opening six jobs in a row is not a wait.
        start = QPoint(self.width(), 0)
        end = QPoint(self.panel.x(), 0)
        self.panel.move(start)
        self._anim = QPropertyAnimation(self.panel, b"pos", self)
        self._anim.setDuration(180)
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()
        self.scrim.set_alpha(70)

    def close_panel(self) -> None:
        self.hide()
