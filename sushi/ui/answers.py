"""The questions Sushi could not answer, and a place to answer them.

Jobly learned one way: it watched the form and kept whatever you typed into
it. That is the right mechanism and it stays — but it only fires when you are
sitting in front of the browser, and it cannot help with the question the
filler skipped before you ever got there.

So there are two routes into the answer bank now, and they feed the same
store:

- **The watcher**, which reads the live form every couple of seconds and
  keeps what settles. Nothing to press.
- **This panel**, which lists exactly what the lookup could not answer and
  lets you type it once, here, in the app.

Answering here is worth more than answering in the form, because it happens
*before* the browser is sitting there half-filled: next time the same
question appears on anyone's form it is already known and gets typed
automatically.

Everything written here is stored as a ``human`` answer, which outranks
anything the agent guessed and can never be overwritten by one.

The same panel does a second job in Explorative mode, with
:func:`guess_panel`. There the questions arrive already answered — by the
model, from the CV — and the point of listing them is the opposite: not "we
could not do this", but "we did this and you should look at it". A guess is
never in the bank when it reaches this panel, so pressing Save here is how a
guess becomes a known answer, and leaving it alone is how it stays a guess.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLineEdit, QPlainTextEdit,
                             QVBoxLayout, QWidget)

from .. import learning
from . import theme
from .components import button, card, faint, h3, label, muted

#: Past this, a question wants a paragraph rather than a line.
LONG = 58


#: The two things this panel is for, in its own words. Kept here rather than
#: at the call site because a page showing both of them must not have to
#: decide what either one means.
UNANSWERED_COPY = (
    "Questions Sushi could not answer",
    "Answer these once and they go into your answer bank. The next form that "
    "asks the same thing, at any employer, gets filled in automatically.",
    "Save answers",
)
GUESS_COPY = (
    "Answers Sushi worked out, worth a check",
    "These were not looked up. Sushi worked them out from your CV and your "
    "history because Explorative mode is on, and typed them into the form, "
    "where they are outlined in amber. None of them is in your answer bank: "
    "correct anything that is wrong in the form, or press Save here to "
    "confirm them so they are reused next time.",
    "Confirm these answers",
)


class AnswerPanel(QWidget):
    """Lists questions with a box for each, and banks the replies."""

    #: Emitted with how many were saved, so the page can offer to go back
    #: and put them into the form.
    saved = pyqtSignal(int)

    def __init__(self, copy: tuple[str, str, str] = UNANSWERED_COPY,
                 style: str = "Card") -> None:
        super().__init__()
        self._rows: list[tuple[str, QWidget]] = []
        heading, intro, button_text = copy
        self._copy = copy
        #: What the card looks like at rest. Remembered rather than assumed,
        #: because the save flash swaps the object name and has to be able to
        #: put back the one this panel started with — the guess panel is
        #: amber, and flashing it green once turned it white for good.
        self._style = style

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self.card = card(style)
        self.card.layout().addWidget(h3(heading))
        self.intro = muted(intro)

        self.host = QWidget()
        self.host.setObjectName("AnswerRows")
        self.host.setStyleSheet("QWidget#AnswerRows{background:transparent;}")
        self.rows = QVBoxLayout(self.host)
        self.rows.setContentsMargins(0, 6, 0, 0)
        self.rows.setSpacing(12)
        self.card.layout().addWidget(self.host)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.note = faint("")
        foot.addWidget(self.note, 1)
        self.save_btn = button(button_text, "Primary", self._save)
        foot.addWidget(self.save_btn)
        self.card.layout().addLayout(foot)

        root.addWidget(self.card)
        self.setVisible(False)

    # --- filling the panel ---------------------------------------------------

    def set_questions(self, questions: list[str],
                      prefill: dict[str, str] | None = None) -> None:
        """Show a box per question. Hides itself when there are none.

        ``prefill`` is what is already in the form for that question — the
        guesses, in Explorative mode. It takes precedence over the bank,
        because it is what the employer is about to be sent.
        """
        self._clear()
        prefill = prefill or {}
        questions = [q for q in dict.fromkeys(q.strip() for q in questions) if q]
        if not questions:
            self.setVisible(False)
            return

        for question in questions:
            self.rows.addWidget(label(question, "Muted"))
            widget: QWidget
            # An answer already in hand decides the shape of the box too: a
            # three-sentence guess in a one-line field cannot be read, let
            # alone corrected.
            given = prefill.get(question, "")
            if (len(question) > LONG or question.rstrip().endswith("?")
                    or len(given) > LONG):
                box = QPlainTextEdit()
                box.setMinimumHeight(74)
                box.setPlaceholderText("Your answer, in your own words.")
                widget = box
            else:
                line = QLineEdit()
                line.setPlaceholderText("Your answer")
                widget = line
            # An answer already known is shown, so this doubles as a way to
            # correct one that is wrong rather than only to add a new one.
            existing = given or learning.recall(question)
            if existing:
                if isinstance(widget, QPlainTextEdit):
                    widget.setPlainText(existing)
                else:
                    widget.setText(existing)
            self.rows.addWidget(widget)
            self._rows.append((question, widget))

        self.note.setText(f"{len(questions)} question(s) to answer.")
        self.setVisible(True)

    def set_guesses(self, guessed: list[tuple[str, str]]) -> None:
        """Show what Explorative mode worked out, answers already in place."""
        pairs = [(q.strip(), a.strip()) for q, a in guessed if q and q.strip()]
        self.set_questions([q for q, _ in pairs], dict(pairs))
        if pairs:
            self.note.setText(
                f"{len(pairs)} answer(s) are Sushi's, not yours. Nothing here "
                f"has been saved to your bank.")

    def _clear(self) -> None:
        self._rows.clear()
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item is None:
                continue
            # Bound once. ``item.widget()`` is re-resolved on every call, and
            # the second one returns None after the first has reparented it —
            # so calling it twice crashes on the way out.
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    # --- banking -------------------------------------------------------------

    @staticmethod
    def _text(widget: QWidget) -> str:
        if isinstance(widget, QPlainTextEdit):
            return widget.toPlainText().strip()
        return widget.text().strip() if isinstance(widget, QLineEdit) else ""

    def answers(self) -> dict[str, str]:
        """What is currently typed in, question → answer."""
        return {q: self._text(w) for q, w in self._rows if self._text(w)}

    def _save(self) -> None:
        typed = self.answers()
        if not typed:
            self._flash("Nothing typed in yet.", ok=False)
            return
        saved, refused, left = 0, 0, []
        allow_demo = learning_demographics_allowed()
        for question, answer in typed.items():
            if not learning.learnable(question, answer,
                                      include_demographics=allow_demo):
                # The gates apply here as everywhere: a password or a
                # demographic question is not banked just because it was
                # typed into Sushi rather than into a form.
                # Not kept, and not left on screen either: Sushi will never
                # store this one, so a row inviting you to try again is a
                # trap. The note says why; answer it in the form yourself.
                refused += 1
                continue
            if learning.remember(question, answer, source="human"):
                saved += 1

        # Anything left blank stays on screen — it is still unanswered, and
        # clearing it away would quietly lose the question.
        left += [q for q, w in self._rows if not self._text(w)]

        if refused:
            self._flash(
                f"Saved {saved}. {refused} not stored and removed from this "
                f"list: Sushi never keeps passwords or demographic answers "
                f"unless you turn that on in Settings. Answer those in the "
                f"form itself.",
                ok=saved > 0)
        else:
            self._flash(f"Saved {saved} answer(s) to your bank.", ok=True)

        # The answered ones go. A list that still shows what you have just
        # dealt with gives you no way to see what is left.
        QTimer.singleShot(1200, lambda: self._settle(left))
        self.saved.emit(saved)

    def _settle(self, remaining: list[str]) -> None:
        """Re-show only what is still outstanding, or stand down entirely."""
        if not remaining:
            self.setVisible(False)
            self._clear()
            return
        note = self.note.text()
        self.set_questions(remaining)
        self.note.setText(note)

    def _flash(self, message: str, ok: bool) -> None:
        """Say what happened, in a colour that says it without being read."""
        self.note.setText(message)
        colour = theme.GOOD if ok else theme.WARN
        self.note.setStyleSheet(
            f"color:{colour};font-size:{theme.SIZE_SMALL}px;font-weight:600;"
            f"background:transparent;")
        self.card.setObjectName("CardSaved" if ok else self._style)
        self.card.style().unpolish(self.card)
        self.card.style().polish(self.card)
        if ok:
            # The wash is a confirmation, not a state — it should fade out
            # of the way rather than leave the card permanently green.
            QTimer.singleShot(1600, self._unflash)

    def _unflash(self) -> None:
        self.card.setObjectName(self._style)
        self.card.style().unpolish(self.card)
        self.card.style().polish(self.card)


def learning_demographics_allowed() -> bool:
    from .. import storage
    return storage.get_setting("learn_demographics", "0") == "1"
