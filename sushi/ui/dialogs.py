"""The app's own dialogs, in the app's own language.

Every question Sushi asks used to be a ``QMessageBox``: a Windows title bar,
a blue system question mark, a grey slab, and the whole question written as
one paragraph with blank lines in it. It is the one surface where the app
stopped looking like itself, and it appears at the moments that matter most
— the confirmation before a browser opens and fills a form in your name.

So: the splash screen's language instead. A starfield card with the mascot
on it, the question as a title, and the body split into the three things a
confirmation actually contains:

- **the subject** — what this is about, big enough to read at a glance
  ("Senior Data Analyst", "9fin · Content");
- **what will happen**, in one line rather than a paragraph;
- **the choice**, as two real buttons with verbs on them, not Yes and No.

:func:`ask` and :func:`tell` are the whole interface. Both are modal, both
sit low and to the right of the window that called them — see
:meth:`SushiDialog.place_on_parent` — and both can be dragged.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QScrollArea,
                             QVBoxLayout, QWidget)

from . import theme
from .components import button, card, faint, label, muted
from .splash import Mark
from .starfield import StarPanel, transparent


class SushiDialog(QDialog):
    """A starfield card with a question on it.

    Built rather than themed: a styled ``QMessageBox`` still owns its own
    layout, its icon column and its button box, and fighting all three for a
    result that is merely less ugly is more code than this.
    """

    #: Dialogs are sized to their text, between these. Narrower than a card
    #: on a page: a question is read in one pass, and a 700px line is not.
    _MIN_W, _MAX_W = 380, 460
    #: How much body text is shown before it starts scrolling instead.
    _BODY_MAX = 260

    def __init__(self, parent, title: str, subject: str = "", sub: str = "",
                 body: str = "", confirm: str = "", cancel: str = "",
                 danger: bool = False) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self._drag: QPoint | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # The sky is the dialog's whole ground, with the cream card inset in
        # it. A band across the top instead would need its lower corners
        # square and its upper ones round, which is a custom clip path for a
        # result nobody would be able to name.
        sky = StarPanel(radius=theme.R_CARD, seed=13, density=1.5,
                        animate=True, parallax=10,
                        padding=(16, 14, 16, 16))
        sky.body.setSpacing(12)
        root.addWidget(sky)

        head = transparent(QWidget())
        hrow = QHBoxLayout(head)
        hrow.setContentsMargins(6, 0, 6, 0)
        hrow.setSpacing(12)
        hrow.addWidget(Mark(34), 0, Qt.AlignmentFlag.AlignVCenter)
        head_lbl = label(title, wrap=True)
        head_lbl.setStyleSheet(theme.on_sky(18, theme.SKY_INK, display=True))
        hrow.addWidget(head_lbl, 1, Qt.AlignmentFlag.AlignVCenter)
        sky.body.addWidget(head)

        inner = card("Card", margins=(20, 18, 20, 18), spacing=10)
        sky.body.addWidget(inner)
        lay = inner.layout()

        if subject:
            hero = label(subject, "H3", wrap=True)
            hero.setFont(theme.body_font(17, 600))
            lay.addWidget(hero)
        if sub:
            lay.addWidget(muted(sub))
        self._text = self._scroll = None
        if body:
            # Scrollable, but only once it has to be: an apply can come back
            # with a page of detail, and a dialog that grows past the window
            # cannot be dismissed — its buttons are off the bottom of the
            # screen. Short bodies get their full height in `showEvent`,
            # where the wrapped text finally knows how tall it is.
            self._text = label(body, wrap=True)
            self._text.setStyleSheet(
                f"color:{theme.MUTED};font-size:{theme.SIZE_BODY}px;")
            self._scroll = QScrollArea()
            self._scroll.setWidgetResizable(True)
            self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            self._scroll.setWidget(self._text)
            self._scroll.setMaximumHeight(self._BODY_MAX)
            self._scroll.setStyleSheet(
                "QScrollArea{background:transparent;}"
                "QScrollArea > QWidget > QWidget{background:transparent;}")
            lay.addWidget(self._scroll)

        lay.addSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        if cancel:
            row.addWidget(button(cancel, "Chip", self.reject))
        go = button(confirm or "Got it", "DangerSolid" if danger else "Primary",
                    self.accept)
        go.setMinimumWidth(120)
        go.setDefault(True)
        row.addWidget(go)
        lay.addLayout(row)

        self.setMinimumWidth(self._MIN_W)
        self.setMaximumWidth(self._MAX_W)
        self.adjustSize()

    # --- placement and dragging ----------------------------------------------

    #: How far the dialog sits from the corner of the window that asked.
    _MARGIN = 28

    def place_on_parent(self) -> None:
        """Low and to the right of the window that asked, not over its middle.

        On the window that asked, not the desktop: centred on the screen it
        lands somewhere else entirely when the app is not maximised, which
        reads as another application.

        And low, because the middle is where the thing being asked about is.
        The confirmation before an application landed squarely on the match
        card for that very job — so the title, the company and the match
        score all disappeared behind a dialog quoting them back at you, at
        the one moment you might want to check them. Down here it covers the
        end of the applications table instead, which is not what anybody is
        reading while deciding whether to apply.
        """
        host = self.parentWidget()
        if host is None:
            return
        frame = host.window().frameGeometry()
        # left+width rather than right(): QRect.right() is inclusive, so the
        # obvious arithmetic leaves every gap a pixel wider than it says.
        x = frame.left() + frame.width() - self.width() - self._MARGIN
        y = frame.top() + frame.height() - self.height() - self._MARGIN

        # Never past the window's own corner, for a window smaller than the
        # dialog: the mascot and the title belong over the app rather than
        # off the side of it.
        x, y = max(x, frame.left()), max(y, frame.top())

        # And never off the screen, which overrules the line above rather
        # than the other way round. A window dragged most of the way off the
        # display would otherwise take the dialog with it, and this one is
        # modal — buttons that cannot be reached are no way back into the app.
        screen = host.window().screen()
        if screen is not None:
            avail = screen.availableGeometry()
            x = min(x, avail.left() + avail.width() - self.width())
            y = min(y, avail.top() + avail.height() - self.height())
            x, y = max(x, avail.left()), max(y, avail.top())
        self.move(x, y)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit_body()
        self.place_on_parent()

    def _fit_body(self) -> None:
        """Give the body its real height, now that its width is settled.

        A QScrollArea reports a sizeHint of its own rather than its widget's,
        so a four-line message arrived in a box two lines tall with a
        scrollbar beside it — a dialog with room to spare asking to be
        scrolled.
        """
        if self._scroll is None or self._text is None:
            return
        width = self._scroll.viewport().width()
        need = (self._text.heightForWidth(width) if width > 0
                else self._text.sizeHint().height())
        self._scroll.setFixedHeight(max(24, min(self._BODY_MAX, need + 2)))
        self.adjustSize()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = (event.globalPosition().toPoint()
                          - self.frameGeometry().topLeft())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._drag = None


def ask(parent, title: str, *, subject: str = "", sub: str = "",
        body: str = "", confirm: str = "Continue", cancel: str = "Not now",
        danger: bool = False) -> bool:
    """Ask, and say whether they said yes."""
    d = SushiDialog(parent, title, subject, sub, body, confirm, cancel, danger)
    return d.exec() == QDialog.DialogCode.Accepted


def confirm(parent, title: str, on_yes, *, on_no=None, subject: str = "",
            sub: str = "", body: str = "", confirm: str = "Continue",
            cancel: str = "Not now", danger: bool = False) -> None:
    """Ask without freezing the app. ``on_yes`` runs if they say yes.

    ``ask`` is ``exec()``: the whole window refuses every click until the
    question is answered. In the corner, looking like a notice, that read as
    the app having hung ("it doesn't let me click anywhere"). This one is
    shown and left: the app keeps working, and the action happens only
    when its button is pressed. One question at a time — a new one replaces
    the last, which counts as "not now".
    """
    d = SushiDialog(parent, title, subject, sub, body, confirm, cancel, danger)
    d.setModal(False)
    d.setWindowModality(Qt.WindowModality.NonModal)
    d.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    host = parent.window() if parent is not None else None
    old = getattr(host, "_sushi_question", None) if host is not None else None
    if old is not None:
        try:
            old.reject()
        except RuntimeError:
            pass
    if host is not None:
        host._sushi_question = d
    d.accepted.connect(on_yes)
    if on_no is not None:
        d.rejected.connect(on_no)
    d.show()
    d.raise_()
    d.activateWindow()


def tell(parent, title: str, *, subject: str = "", sub: str = "",
         body: str = "", button_text: str = "Got it") -> None:
    """Say something that has already happened. One button, no question.

    Not modal. This used to be ``exec()``, and a notice in the corner that
    has nothing to decide froze the whole app until it was closed — while
    the browser it was talking about sat in front of it, so people never
    saw why nothing would click. Now it is shown and left; the app keeps
    working, and the notice goes when its button is pressed.
    """
    d = SushiDialog(parent, title, subject, sub, body, button_text, "")
    d.setModal(False)
    d.setWindowModality(Qt.WindowModality.NonModal)
    d.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    # One notice at a time: a second apply finishing replaces the first
    # rather than stacking cards in the same corner.
    host = parent.window() if parent is not None else None
    old = getattr(host, "_sushi_notice", None) if host is not None else None
    if old is not None:
        try:
            old.close()
        except RuntimeError:
            pass
    if host is not None:
        host._sushi_notice = d
    d.show()
    d.raise_()
