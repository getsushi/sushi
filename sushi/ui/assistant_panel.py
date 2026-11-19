"""The assistant rail — the persistent "How can I help?" panel.

Context-aware suggestions, a scrolling conversation, and an input. Calls run
on a worker thread; replies may carry an action the main window executes.

The design's details, all of which earn their place: the panel can be *closed*
from inside itself rather than only from the title bar; suggestions are
full-width rows rather than chips, because they are sentences and chips make
sentences wrap; replies carry a timestamp, which is what turns a transcript
into a record; and the model the answers came from is stated in mono at the
foot of the panel, so there is never a question about which one you were
talking to.
"""
from __future__ import annotations

import time
from typing import Callable

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QScrollArea, QVBoxLayout, QWidget)

from ..assistant import AssistantContext, AssistantReply, QUICK_PROMPTS, Turn, ask
from ..config import load_settings
from ..llm import LLM
from . import theme
from .components import button, faint, icon_button, label
from .workers import Worker

import logging
log = logging.getLogger(__name__)


class Bubble(QFrame):
    """One message, with the time underneath it."""

    def __init__(self, text: str, mine: bool, stamp: bool = True) -> None:
        super().__init__()
        # Object-name scoped: QLabel subclasses QFrame, so an unscoped
        # `QFrame { border: 1px solid }` boxes the bubble's own text as well.
        self.setObjectName("Bubble")
        bg = theme.ACCENT_SOFT if mine else theme.SURFACE
        edge = theme.ACCENT_LINE if mine else theme.LINE_2
        self.setStyleSheet(
            f"QFrame#Bubble{{background:{bg};border:1px solid {edge};"
            f"border-radius:{theme.R_TILE}px;}}"
            "QFrame#Bubble QLabel{background:transparent;border:none;}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 11, 14, 9)
        lay.setSpacing(5)
        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(body)
        if stamp:
            when = QLabel(time.strftime("%H:%M"))
            when.setObjectName("MonoDim")
            lay.addWidget(when)


class AssistantPanel(QFrame):
    def __init__(self, get_context: Callable[[], AssistantContext],
                 on_action: Callable[[str, str], None],
                 on_close: Callable[[], None] | None = None) -> None:
        super().__init__()
        # Right-hand rail now that the sidebar owns the left edge.
        self.setObjectName("RailRight")
        self.setFixedWidth(330)
        self._get_context = get_context
        self._on_action = on_action
        self._history: list[Turn] = []
        self._page = "dashboard"
        self.pool = QThreadPool.globalInstance()

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.heading = label("How can I help?", "H2")
        head.addWidget(self.heading, 1)
        if on_close is not None:
            head.addWidget(icon_button("x", "Close the assistant", on_close,
                                       size=26, icon_size=15))
        root.addLayout(head)

        self.blurb = label(
            "Ask about your feed, applications, or how Sushi works. Every change "
            "is previewed and undoable.", "Faint")
        root.addWidget(self.blurb)

        self.quick_box = QVBoxLayout()
        self.quick_box.setSpacing(8)
        qp = QWidget()
        qp.setStyleSheet("background: transparent;")
        qp.setLayout(self.quick_box)
        root.addWidget(qp)
        self.quick_panel = qp

        self.chat_host = QWidget()
        self.chat_host.setStyleSheet("background: transparent;")
        self.chat_lay = QVBoxLayout(self.chat_host)
        self.chat_lay.setContentsMargins(0, 0, 0, 0)
        self.chat_lay.setSpacing(8)
        self.chat_lay.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.chat_host)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.scroll, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask about your feed or app…")
        self.input.returnPressed.connect(self._send)
        row.addWidget(self.input, 1)
        self.send_btn = icon_button("send", "Send", self._send,
                                    size=34, icon_size=17, colour=theme.ACCENT)
        row.addWidget(self.send_btn)
        root.addLayout(row)

        # Which model answered, stated rather than implied.
        self.model_lbl = label("", "Mono")
        root.addWidget(self.model_lbl)

        self.set_page("dashboard")
        self.refresh_model()

    # --- public -------------------------------------------------------------

    def refresh_model(self) -> None:
        s = load_settings()
        if s.llm_enabled:
            src = s.key_source.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] or "settings"
            self.model_lbl.setText(f"Ready · key from {src}")
        else:
            self.model_lbl.setText("No model configured. Settings → Model")

    def set_page(self, page: str) -> None:
        self._page = page
        while self.quick_box.count():
            item = self.quick_box.takeAt(0)
            if item.widget():
                widget = item.widget()
                widget.setParent(None)
                widget.deleteLater()
        for prompt in QUICK_PROMPTS.get(page, QUICK_PROMPTS["dashboard"]):
            self.quick_box.addWidget(
                button(prompt, "Suggest", lambda p=prompt: self._ask(p)))

    # --- internals ----------------------------------------------------------

    def _send(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self._ask(text)

    def _ask(self, message: str) -> None:
        # Once there is a conversation it gets the room: the suggestions
        # were pushing the replies into a sliver under them.
        self.quick_panel.setVisible(False)
        self.blurb.setVisible(False)
        self._add_bubble(message, mine=True)
        self._history.append(Turn("user", message))
        self._add_bubble("…", mine=False, stamp=False)
        thinking = self._last_bubble
        self.input.setEnabled(False)

        settings = load_settings()
        ctx = self._get_context()

        def job():
            return ask(LLM(settings), ctx, self._history, message)

        state = {"answered": False}

        def done(reply: AssistantReply):
            state["answered"] = True
            log.info("assistant: reply arrived (%d chars, action=%s)",
                     len(getattr(reply, "reply", "") or ""), getattr(reply, "action", ""))
            if thinking:
                thinking.deleteLater()
            self._add_bubble(reply.reply, mine=False)
            self._history.append(Turn("assistant", reply.reply))
            self.input.setEnabled(True)
            self.input.setFocus()
            if reply.action and reply.action != "none":
                self._offer_action(reply.action, reply.action_arg)

        def failed(msg: str):
            state["answered"] = True
            log.info("assistant: failed: %s", msg)
            if thinking:
                thinking.deleteLater()
            self._add_bubble(f"Sorry: {msg}", mine=False)
            self.input.setEnabled(True)

        w = Worker(job)
        w.signals.finished.connect(done)
        w.signals.failed.connect(failed)
        # Held until it reports: see Worker.keep_alive. Dropping the only
        # reference to a started worker frees its signals under the queued
        # result still on its way to this thread.
        if not hasattr(self, "_inflight"):
            self._inflight = set()
        w.keep_alive(self._inflight)
        self.pool.start(w)

        # Never left on "…" for good. If the answer has not shown in 90
        # seconds, say so and give the input back.
        from PyQt6.QtCore import QTimer

        def give_up():
            if state["answered"]:
                return
            state["answered"] = True
            log.warning("assistant: no reply reached the panel after 90 s")
            try:
                if thinking:
                    thinking.deleteLater()
            except RuntimeError:
                pass
            self._add_bubble("No answer came back. Please ask again.", mine=False)
            self.input.setEnabled(True)
        QTimer.singleShot(90_000, give_up)

    #: Actions that run the moment they are proposed. All of them are local
    #: and reversible — a page change, a search, a preference you just stated
    #: out loud. Making someone say "I want data analyst roles" and *then*
    #: press "Update my target roles" is asking them to agree with themselves.
    #: Every one of these says what it did. An action that silently changes
    #: the page behind a chat panel is indistinguishable from an action that
    #: did nothing — which is exactly how this felt before.
    #:
    #: The second item is the action that takes it back, or "" where there is
    #: nothing to take back. It used to be a flag, which meant every undoable
    #: action had to be the preferences one.
    _IMMEDIATE = {
        "set_targets": ("Updated what Sushi is looking for.", "undo_preferences"),
        "set_locations": ("Updated where Sushi is looking.", "undo_preferences"),
        "remember_answer": ("Saved to your answer bank. Forms that ask this "
                            "get filled with it.", "undo_remember"),
        "open_tracker": ("Opened the Tracker.", ""),
        "show_applications": ("Opened your applications.", ""),
        "rescan": ("Searching again…", ""),
        "tailor_resume": ("Opened the resume builder.", ""),
        "write_cover_letter": ("Opened the cover-letter writer.", ""),
        "manual_apply": ("Opened Manual apply.", ""),
    }

    #: Actions that wait for a press. Only one so far, and it is the only one
    #: that reaches outside this machine: it puts real applications in front
    #: of real employers.
    _CONFIRM = {
        "apply_top": "Queue my top matches",
    }

    def _offer_action(self, action: str, arg: str) -> None:
        """Run it, or offer it — but never drop it.

        The first version matched the action against a hand-written label map
        and returned silently when it missed. Two actions were added later and
        nobody added their labels, so the assistant said "I'll update your
        target roles" and then did nothing at all: the reply was real, the
        action was real, and the panel threw it away between the two.
        """
        if action in self._IMMEDIATE:
            self._on_action(action, arg)
            note, undo = self._IMMEDIATE[action]
            self._add_note(note, undo=undo)
            return

        text = self._CONFIRM.get(action)
        if not text:
            # Unknown, so it waits for a press rather than vanishing. The
            # label is generated: a missing entry should look unpolished, not
            # make the feature disappear.
            text = action.replace("_", " ").capitalize()
        b = button(text, "Primary", lambda: self._on_action(action, arg),
                   icon="arrow-right", icon_size=15)
        b.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.chat_lay.insertWidget(self.chat_lay.count() - 1, b)
        self._scroll_down()

    def _add_note(self, text: str, undo: str = "") -> None:
        """Confirm that something just happened, with a way back.

        This is what replaces the confirm button for immediate actions:
        nothing is silent, but you find out by being told it is done rather
        than by being asked whether you meant it.
        """
        row = QWidget()
        row.setObjectName("ActionNote")
        row.setStyleSheet("QWidget#ActionNote{background:transparent;}")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(8)
        lay.addWidget(faint(text), 1)
        if undo:
            lay.addWidget(button("Undo", "Link",
                                 lambda a=undo: self._on_action(a, "")))
        self.chat_lay.insertWidget(self.chat_lay.count() - 1, row)
        self._scroll_down()

    def _add_bubble(self, text: str, mine: bool, stamp: bool = True) -> None:
        b = Bubble(text, mine, stamp)
        self.chat_lay.insertWidget(self.chat_lay.count() - 1, b)
        self._last_bubble = b
        self._scroll_down()

    def _scroll_down(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        # defer so layout settles first
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(30, lambda: bar.setValue(bar.maximum()))
