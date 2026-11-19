"""Home — the wireframe's first screen (1g, 1h, 1i).

Everything Sushi is doing, and the one thing it needs from you, on one page:
the Insane Mode switch with its daily limit and Pause, three numbers, the
questions only you can answer (answer once and it is remembered), and what
went out today.
"""
from __future__ import annotations

import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QBoxLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QSpinBox,
                             QVBoxLayout, QWidget)

from ... import learning, storage
from ...extras import applied_today, load_autoapply, save_autoapply
from .. import theme
from ..components import ElidedLabel, Switch, button, card
from .base import Page, scroll_page

#: How many questions and sent rows Home shows before "See all".
_LIST = 5

#: Placeholder text and answer options that forms report as questions.
_NOT_QUESTIONS = ("type your response", "type here", "your answer",
                  "select", "choose", "please select", "enter")

#: Questions that are answered Yes or No get two buttons, as in the wireframe.
_YES_NO = ("are you", "do you", "have you", "will you", "would you", "can you",
           "is your", "did you", "were you")


def _is_question(q: str) -> bool:
    """A real question, not an option ("Yes", "Managerial (e.g. …)") or a
    placeholder ("Type your response") that a form reported as one."""
    t = " ".join(q.split()).strip()
    low = t.lower().rstrip(".:…")
    if not t or low in ("yes", "no", "other", "n/a", "none"):
        return False
    if any(low == p or low.startswith(p + " ") for p in _NOT_QUESTIONS):
        return False
    if "?" in t:
        return True
    # No question mark: it has to read like a request, or it is an option or
    # a link text a form reported ("Entrata India LinkedIn Page").
    if not re.match(r"(?i)^(what|how|why|which|when|where|who|are|do|does|"
                    r"have|has|will|would|can|could|is|please|tell|describe|"
                    r"explain|list|provide|enter|share|give|upload|select|"
                    r"current|desired|expected|preferred|your|years|notice|"
                    r"salary|linkedin|github|portfolio|website)\b", t):
        return False
    words = t.split()
    if len(words) < 2:
        return False
    if "e.g." in low and len(words) < 8:
        return False
    return True


def _plain(w: QWidget) -> QWidget:
    """No background of its own: rows sit on the page, not on grey bands."""
    w.setObjectName("HomeRow")
    w.setStyleSheet("QWidget#HomeRow{background:transparent;}")
    return w


def _text(text: str, size: int, colour: str, display: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    if display:
        # In the sheet, not setFont: the app's QLabel rules outrank a font set
        # on the widget, and titles came out at body size.
        lbl.setStyleSheet(f"color:{colour};background:transparent;"
                          f"font-family:'{theme.DISPLAY_FONT}';font-size:{size}px;"
                          "font-weight:500;")
    else:
        lbl.setStyleSheet(f"color:{colour};font-size:{size}px;background:transparent;")
    return lbl


def _rule() -> QFrame:
    r = QFrame()
    r.setFixedHeight(1)
    r.setStyleSheet(f"background:{theme.LINE_2};border:none;")
    return r


def _section_head(title: str, hint: str = "", action=None) -> QWidget:
    """A section title in the display face, its hint on the right, and a rule
    under both — the wireframe's "Needs you … Answer once" line."""
    w = _plain(QWidget())
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(10)
    row = QHBoxLayout()
    row.addWidget(_text(title, 26, theme.INK, display=True), 1)
    if hint:
        h = _text(hint, theme.SIZE_SMALL, theme.MUTED)
        h.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        h.setWordWrap(False)
        row.addWidget(h, 0, Qt.AlignmentFlag.AlignBottom)
    if action is not None:
        row.addWidget(action, 0, Qt.AlignmentFlag.AlignBottom)
    v.addLayout(row)
    v.addWidget(_rule())
    return w


def _stat() -> tuple[QFrame, QLabel, QLabel]:
    box = card("Card", margins=(24, 22, 24, 22), spacing=6)
    num = QLabel("0")
    num.setStyleSheet(f"color:{theme.INK};background:transparent;border:none;"
                      f"font-family:'{theme.DISPLAY_FONT}';font-size:46px;")
    cap = QLabel("")
    cap.setStyleSheet(f"color:{theme.MUTED};font-size:{theme.SIZE_SMALL + 1}px;"
                      "background:transparent;border:none;")
    box.layout().addWidget(num)
    box.layout().addWidget(cap)
    return box, num, cap


class HomePage(Page):
    title = "Home"
    assistant_key = "dashboard"

    def build(self) -> None:
        host, lay, scroll = scroll_page()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        lay.setSpacing(28)

        # --- Insane Mode ----------------------------------------------------
        self.mode = card("Card", margins=(28, 26, 28, 26), spacing=0)
        row = QHBoxLayout()
        row.setSpacing(24)
        self.switch = Switch(False)
        self.switch.toggled.connect(self._toggle)
        row.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(4)
        self.mode_title = _text("Insane Mode is off", 32, theme.INK, display=True)
        sub = QHBoxLayout()
        sub.setSpacing(0)
        self.mode_sub = ElidedLabel("")
        self.mode_sub.setStyleSheet(f"color:{theme.MUTED};background:transparent;"
                                    f"font-size:{theme.SIZE_SMALL + 1}px;")
        sub.addWidget(self.mode_sub)
        self.limit_link = button("Change limit", "Link", self._focus_cap)
        self.switch_link = button("Switch to Manual", "Link", self._pause)
        # The older Insane mode page: the live queue, row by row.
        self.queue_link = button("See the queue", "Link",
                                 lambda: self.app.navigate("Insane mode"))
        for b in (self.limit_link, self.switch_link, self.queue_link):
            sub.addWidget(_text(" · ", theme.SIZE_SMALL + 1, theme.MUTED))
            sub.addWidget(b)
        sub.addStretch(1)
        words.addWidget(self.mode_title)
        words.addLayout(sub)
        row.addLayout(words, 1)
        self.cap = QSpinBox()
        self.cap.setRange(1, 200)
        self.cap.setFixedWidth(84)
        self.cap.setToolTip("Applications a day")
        self.cap.valueChanged.connect(self._set_cap)
        row.addWidget(self.cap, 0, Qt.AlignmentFlag.AlignVCenter)
        self.pause_btn = button("Pause", "", self._pause)
        row.addWidget(self.pause_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.mode.layout().addLayout(row)
        lay.addWidget(self.mode)

        # --- three numbers --------------------------------------------------
        stats = QHBoxLayout()
        stats.setSpacing(20)
        b1, self.n_matches, self.c_matches = _stat()
        b2, self.n_sent, self.c_sent = _stat()
        b3, self.n_needs, self.c_needs = _stat()
        self.c_matches.setText("New matches")
        self.c_needs.setText("Need your input")
        for b in (b1, b2, b3):
            stats.addWidget(b, 1)
        lay.addLayout(stats)

        # --- Manual: matches waiting ----------------------------------------
        self.ready = card("Card", margins=(28, 24, 28, 24), spacing=0)
        rr = QHBoxLayout()
        rv = QVBoxLayout()
        rv.setSpacing(4)
        self.ready_title = _text("", 26, theme.INK, display=True)
        # Elided, not wrapped: a title like "Engineer 1/2/3 - Grafana/Bash/…"
        # has nowhere to break, and its full width pushed the page past the
        # window's right edge.
        self.ready_sub = ElidedLabel("")
        self.ready_sub.setStyleSheet(f"color:{theme.MUTED};background:transparent;"
                                     f"font-size:{theme.SIZE_SMALL + 1}px;")
        rv.addWidget(self.ready_title)
        rv.addWidget(self.ready_sub)
        rr.addLayout(rv, 1)
        rr.addWidget(button("Review matches", "Primary",
                            lambda: self.app.navigate("Matches")),
                     0, Qt.AlignmentFlag.AlignVCenter)
        self.ready.layout().addLayout(rr)
        lay.addWidget(self.ready)

        # --- Needs you | Sent today, side by side ---------------------------
        cols = QHBoxLayout()
        cols.setSpacing(32)
        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(_section_head("Check Sushi's answers",
                                    "Sushi filled these in. Fix anything wrong, then save."))
        self.needs_host = QVBoxLayout()
        self.needs_host.setSpacing(0)
        left.addLayout(self.needs_host)
        left.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(0)
        right.addWidget(_section_head(
            "Sent today", action=button("See all", "Link",
                                        lambda: self.app.navigate("Applications"))))
        self.sent_host = QVBoxLayout()
        self.sent_host.setSpacing(0)
        right.addLayout(self.sent_host)
        right.addStretch(1)
        cols.addLayout(left, 3)
        cols.addLayout(right, 2)
        lay.addLayout(cols)
        self._cols = cols
        lay.addStretch(1)

        self.app.autoapply_changed.connect(lambda *_: self._render_mode())
        self.app.apps_changed.connect(self.on_show)
        self.app.matches_changed.connect(self._render_numbers)

    #: Below this width the two sections stack rather than squeeze.
    _STACK_BELOW = 1000

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        cols = getattr(self, "_cols", None)
        if cols is None:
            return
        want = (QBoxLayout.Direction.TopToBottom if self.width() < self._STACK_BELOW
                else QBoxLayout.Direction.LeftToRight)
        if cols.direction() != want:
            cols.setDirection(want)

    # --- rendering ----------------------------------------------------------

    def on_show(self) -> None:
        if not self._built:
            return
        self._render_mode()
        self._render_numbers()
        self._render_needs()
        self._render_sent()

    def _render_mode(self) -> None:
        cfg = load_autoapply()
        on = bool(cfg.enabled)
        self.switch.blockSignals(True)
        self.switch.setChecked(on)
        self.switch.blockSignals(False)
        self.cap.blockSignals(True)
        self.cap.setValue(cfg.daily_cap)
        self.cap.blockSignals(False)
        self.mode.setObjectName("CardOn" if on else "Card")
        self.mode.style().unpolish(self.mode)
        self.mode.style().polish(self.mode)
        self.mode_title.setText("Insane Mode is on" if on else "Insane Mode is off")
        self.mode_sub.setText(
            f"Up to {cfg.daily_cap} applications a day" if on
            else "You're in Manual. Turn on to let us apply for you.")
        for w in (self.limit_link, self.switch_link, self.queue_link,
                  self.pause_btn, self.cap):
            w.setVisible(on)
        for lbl in self.mode.findChildren(QLabel):
            if lbl.text() == " · ":
                lbl.setVisible(on)

    def _render_numbers(self) -> None:
        if not self._built:
            return
        cfg = load_autoapply()
        n = self.app.open_match_total()
        self.n_matches.setText(f"{n:,}")
        self.n_sent.setText(str(applied_today()))
        self.c_sent.setText(f"Sent today of {cfg.daily_cap}" if cfg.enabled
                            else "Sent today")
        counts = storage.counts_by_status()
        self.n_needs.setText(str(counts.get("needs_review", 0)))
        top = self.app.matches[0] if self.app.matches else None
        show = bool(n) and not cfg.enabled
        self.ready.setVisible(show)
        if show:
            self.ready_title.setText(
                f"{n:,} job{'s are' if n != 1 else ' is'} ready for you")
            self.ready_sub.setText(
                f"Best match: {top.title} at {top.company}, {top.match}%"
                if top else "")

    def _questions(self) -> list[tuple[str, str]]:
        """(where it came from, question), newest applications first."""
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for a in storage.list_applications(status="needs_review", limit=50):
            for q in a.questions:
                if q in seen or not _is_question(q) or learning.recall_exact(q):
                    continue
                seen.add(q)
                out.append((f"{a.role.title} · {a.role.company} · paused", q))
        for q in learning.open_questions():
            if q not in seen and _is_question(q):
                seen.add(q)
                out.append(("", q))
        return out

    def _clear(self, lay) -> None:
        """Empty a layout completely, nested layouts and all.

        Only removing widgets left any nested layout's children behind,
        still drawn on the page, stacking up on every refresh.
        """
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
                continue
            inner = item.layout()
            if inner is not None:
                self._clear(inner)
                inner.deleteLater()

    def _guesses(self) -> list[tuple[str, str]]:
        """(question, answer) Sushi worked out and typed in, unconfirmed."""
        return [(q, a) for q, a in learning.open_guesses().items()
                if a and _is_question(q)]

    #: Questions per page in "Check Sushi's answers".
    PAGE = 5

    def _render_needs(self, animate: bool = True) -> None:
        self._clear(self.needs_host)
        guesses = self._guesses()
        taken = {q for q, _ in guesses}
        open_qs = [(w, q) for w, q in self._questions() if q not in taken]
        self._all_guesses = dict(guesses)
        self._edits = getattr(self, "_edits", {})
        rows = [("Sushi's answer", q, a) for q, a in guesses]
        rows += [(w, q, "") for w, q in open_qs]
        self._boxes: list[tuple[str, QLineEdit]] = []
        if not rows:
            empty = _text("All caught up. When Sushi fills a form, its answers "
                          "show up here for you to check.",
                          theme.SIZE_SMALL + 1, theme.MUTED)
            empty.setContentsMargins(0, 18, 0, 0)
            self.needs_host.addWidget(empty)
            self._fade_in(empty)
            return

        pages = max(1, -(-len(rows) // self.PAGE))
        self._page = min(max(getattr(self, "_page", 0), 0), pages - 1)
        first = self._page * self.PAGE
        shown = rows[first:first + self.PAGE]

        # Header: how many, and Save all. A widget, not a bare layout, so a
        # refresh can remove it whole.
        bar = _plain(QWidget())
        top = QHBoxLayout(bar)
        top.setContentsMargins(0, 12, 0, 4)
        label = (f"{len(guesses)} answer(s) filled by Sushi"
                 if guesses else f"{len(rows)} question(s) for you")
        if guesses and open_qs:
            label += f" · {len(open_qs)} for you"
        top.addWidget(_text(label, theme.SIZE_SMALL, theme.FAINT), 1)
        if guesses:
            top.addWidget(button("Save all", "Primary", self._save_all))
        self.needs_host.addWidget(bar)

        for i, (where, q, a) in enumerate(shown):
            row = self._question_row(where, q, self._edits.get(q, a))
            self.needs_host.addWidget(row)
            if animate:
                self._fade_in(row, delay=40 * i)

        if pages > 1:
            nav = _plain(QWidget())
            h = QHBoxLayout(nav)
            h.setContentsMargins(0, 14, 0, 0)
            h.setSpacing(8)
            prev = button("‹ Previous", "", lambda: self._go_page(-1))
            nxt = button("Next ›", "", lambda: self._go_page(1))
            prev.setEnabled(self._page > 0)
            nxt.setEnabled(self._page < pages - 1)
            h.addWidget(prev)
            h.addStretch(1)
            h.addWidget(_text(f"{first + 1}–{first + len(shown)} of {len(rows)}"
                              f"  ·  page {self._page + 1} of {pages}",
                              theme.SIZE_SMALL, theme.MUTED))
            h.addStretch(1)
            h.addWidget(nxt)
            self.needs_host.addWidget(nav)

    def _keep_edits(self) -> None:
        """Remember what was typed on this page before leaving it."""
        for q, box in getattr(self, "_boxes", []):
            self._edits[q] = box.text()

    def _go_page(self, step: int) -> None:
        self._keep_edits()
        self._page = getattr(self, "_page", 0) + step
        self._render_needs()

    def _fade_in(self, w: QWidget, delay: int = 0) -> None:
        """Rows ease in rather than pop, a little after one another."""
        from PyQt6.QtCore import QPropertyAnimation, QTimer, QEasingCurve
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        fx = QGraphicsOpacityEffect(w)
        fx.setOpacity(0.0)
        w.setGraphicsEffect(fx)
        anim = QPropertyAnimation(fx, b"opacity", w)
        anim.setDuration(260)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Drop the effect afterwards: a live opacity effect blurs text and
        # costs a repaint on every frame for nothing.
        anim.finished.connect(lambda: w.setGraphicsEffect(None))
        QTimer.singleShot(delay, anim.start)

    def _fade_out_then(self, w: QWidget, then) -> None:
        """Shrink and fade a row away, then carry on."""
        from PyQt6.QtCore import (QEasingCurve, QParallelAnimationGroup,
                                  QPropertyAnimation)
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        fx = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(fx)
        group = QParallelAnimationGroup(w)
        fade = QPropertyAnimation(fx, b"opacity", group)
        fade.setDuration(200)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        shrink = QPropertyAnimation(w, b"maximumHeight", group)
        shrink.setDuration(240)
        shrink.setStartValue(w.height())
        shrink.setEndValue(0)
        shrink.setEasingCurve(QEasingCurve.Type.InCubic)
        group.addAnimation(fade)
        group.addAnimation(shrink)
        group.finished.connect(then)
        group.start()

    def _save_all(self) -> None:
        """Every guess, on every page — with whatever was edited on this one."""
        self._keep_edits()
        n = 0
        for q, guess in getattr(self, "_all_guesses", {}).items():
            text = (self._edits.get(q) or guess or "").strip()
            if text and learning.remember(q, text, source="human"):
                n += 1
        self._edits = {}
        self._page = 0
        self.app.set_status(f"Saved {n} answer(s). Sushi will reuse them on every form.")
        self._render_needs()

    def _question_row(self, where: str, q: str, guess: str = "") -> QWidget:
        w = _plain(QWidget())
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 18, 0, 18)
        v.setSpacing(10)
        if where:
            v.addWidget(_text(where, theme.SIZE_SMALL, theme.FAINT))
        v.addWidget(_text(q, theme.SIZE_SMALL + 2, theme.INK))
        row = QHBoxLayout()
        row.setSpacing(10)

        def keep(text: str) -> None:
            text = text.strip()
            if not text:
                return
            learning.remember(q, text, source="human")
            self._keep_edits()
            self._edits.pop(q, None)
            self.app.set_status("Saved. Sushi will use it on every form that asks.")
            self._fade_out_then(outer, lambda: self._render_needs(animate=False))

        if not guess and q.lower().startswith(_YES_NO):
            for word in ("Yes", "No"):
                b = button(word, "")
                b.clicked.connect(lambda _=False, a=word: keep(a))
                row.addWidget(b)
            row.addStretch(1)
        else:
            box = QLineEdit()
            box.setPlaceholderText("Your answer")
            if guess:
                box.setText(guess)
                box.setCursorPosition(0)
                self._boxes.append((q, box))
            row.addWidget(box, 1)
            save = button("Save", "")
            save.clicked.connect(lambda: keep(box.text()))
            box.returnPressed.connect(lambda: keep(box.text()))
            row.addWidget(save)
        v.addLayout(row)
        outer = _plain(QWidget())
        ov = QVBoxLayout(outer)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(0)
        ov.addWidget(w)
        ov.addWidget(_rule())
        return outer

    def _render_sent(self) -> None:
        self._clear(self.sent_host)
        today = storage._now()[:10]
        rows = [a for a in storage.list_applications(limit=200)
                if a.status in ("submitted", "needs_review")
                and (a.updated_at or "").startswith(today)]
        if not rows:
            empty = _text("Nothing sent yet today.", theme.SIZE_SMALL + 1, theme.MUTED)
            empty.setContentsMargins(0, 18, 0, 0)
            self.sent_host.addWidget(empty)
            return
        for a in rows[:_LIST]:
            w = _plain(QWidget())
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)
            r = QHBoxLayout()
            r.setContentsMargins(0, 14, 0, 14)
            name = ElidedLabel(f"{a.role.title} · {a.role.company}")
            name.setStyleSheet(f"color:{theme.INK};background:transparent;"
                               f"font-size:{theme.SIZE_SMALL + 2}px;")
            r.addWidget(name, 1)
            when = a.updated_at[11:16]
            t = _text(when if a.status == "submitted" else f"to check · {when}",
                      theme.SIZE_SMALL, theme.MUTED)
            t.setWordWrap(False)
            r.addWidget(t)
            v.addLayout(r)
            v.addWidget(_rule())
            self.sent_host.addWidget(w)

    # --- actions ------------------------------------------------------------

    def _toggle(self, on: bool) -> None:
        if on and self.app.applies_myself():
            self.switch.blockSignals(True)
            self.switch.setChecked(False)
            self.switch.blockSignals(False)
            self.app.set_status(self.app._insane_blocked_note())
            return
        cfg = load_autoapply()
        cfg.enabled = on
        save_autoapply(cfg)
        self.app.set_status("Insane Mode is on." if on
                            else "Insane Mode is off. Nothing will be sent on its own.")
        self.app.refresh_chrome()
        if on:
            self.app.start_autoapply()
        else:
            self.app.stop_autoapply("Paused. Nothing new will start.")
        self._render_mode()
        self._render_numbers()

    def _pause(self) -> None:
        self.switch.setChecked(False)

    def _focus_cap(self) -> None:
        self.cap.setFocus()
        self.cap.selectAll()

    def _set_cap(self, n: int) -> None:
        cfg = load_autoapply()
        cfg.daily_cap = n
        save_autoapply(cfg)
        self._render_mode()
        self._render_numbers()
