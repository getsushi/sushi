"""First run — four steps, and the last one is just a door.

The earlier design's onboarding is six steps because it has six things to set up
before dictation works at all. Sushi has four, in the order each one becomes
possible:

1. **A model.** It reads the CV, writes the letters and fills the forms.
   Nothing else on this screen can happen without one, which is why it is
   first rather than a line of amber on the step that needs it.
2. **Your CV.** Everything else — matching, tailoring, which forms to fill —
   is worked out from it.
3. **Where you want to work.** The one thing the CV cannot say. It gives an
   address, which is not an answer: somebody in Bristol may want the whole
   UK, or EMEA, or anything remote from anywhere on earth. Asked here
   because a feed full of jobs on the wrong continent is the fastest way to
   make the app look broken.
4. **Your knowledge folder.** The cover letters and saved answers you already
   have. This is the step that makes Sushi sound like you instead of like a
   model: the answers are lifted from your own documents and never
   overwritten by a guess afterwards.

Then the door: a full starfield and one button, in the earlier design's language.

The first two are gates and the last two are not. Sushi with no model is a
window that cannot answer anything, and Sushi with no CV has nothing to match
against — it would open on an empty feed and a search for nobody, which reads
as a broken app rather than an unfinished setup. Where and the knowledge
folder are genuinely optional, so they keep their Skip.
"""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QPainter
from PyQt6.QtWidgets import (QWIDGETSIZE_MAX, QFileDialog, QFrame,
                             QHBoxLayout, QLabel, QProgressBar, QPushButton,
                             QScrollArea, QStackedWidget, QVBoxLayout,
                             QWidget)

from .. import knowledge, storage
from ..config import (WORKSPACE_TREE, ensure_workspace, load_settings,
                      set_workspace, workspace)
from ..cv import SUPPORTED, build_profile, extract_text
from ..llm import LLM
from ..models import Profile
from . import theme
from .brand import SushiMark
from .components import (IconTile, button, card, faint, h3, label, muted,
                         text_col)
from .controls import WindowButtons, WindowDrag
from .locations import LOCATIONS_CHOSEN, LocationPicker
from .splash import Mark
from .starfield import StarPanel, StarStrip, transparent

#: Title and one line of lead per step. The eyebrow is generated — it used
#: to be the literal string "STEP 1", which tells you where you are without
#: telling you how far that is from the end; four screens of unknown length
#: is the part of a first run people abandon.
STEPS = [
    ("Pick a model.",
     "Free with what you have: Claude Code, Codex, OpenCode or Ollama on "
     "this PC. Pick one and Sushi walks you through the setup."),
    ("Now your CV.",
     "Sushi works everything else out from it: matching, tailoring, filling "
     "forms."),
    ("Where do you want to work?",
     "A region, a country, or anywhere at all if the work is remote. Your CV "
     "says where you live, not where you would take a job."),
    ("What have you written before?",
     "Point Sushi at old cover letters and saved answers, and it fills forms "
     "in your words rather than its own."),
]


class StepBar(QWidget):
    """How many steps there are and which one you are on, as four bars.

    The header used to say "STEP 1" and nothing else, so the only honest
    answer to "how long is this?" was to keep pressing Next and find out.
    """

    _W, _H = 34, 4

    def __init__(self, count: int) -> None:
        super().__init__()
        self.setStyleSheet("background: transparent;")
        self.setFixedHeight(self._H)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._bars: list[QLabel] = []
        for _ in range(count):
            bar = QLabel()
            bar.setFixedSize(self._W, self._H)
            self._bars.append(bar)
            row.addWidget(bar)
        self.set_step(0)

    def set_step(self, step: int) -> None:
        for i, bar in enumerate(self._bars):
            # The step you are on is as bright as the ones behind it: this is
            # a position, not a completion meter, and dimming the current one
            # reads as "not done yet" on a step you are actively doing.
            done = i <= step
            bar.setStyleSheet(
                f"background:{theme.SKY_INK if done else 'rgba(255,248,244,0.3)'};"
                f"border-radius:2px;")


def _head_row(icon: str, tint: str, ink: str, title: str, *buttons) -> QHBoxLayout:
    """Tile · title · the card's actions, all on one optically level line.

    ``AlignVCenter`` rather than ``AlignTop``: a 40px tile beside a single
    line of 15px type aligned to the top leaves the title floating above the
    glyph's optical centre, which on two stacked cards reads as a mistake.
    """
    row = QHBoxLayout()
    row.setSpacing(16)
    row.addWidget(IconTile(icon, 40, tint, ink), 0, Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(h3(title), 1, Qt.AlignmentFlag.AlignVCenter)
    for b in buttons:
        row.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class HeaderSky(WindowDrag, StarStrip):
    """The starfield header — and the window's drag handle.

    Onboarding replaces the whole window, so it carries the chrome too:
    without this the window cannot be moved or closed until setup is done,
    which is exactly when someone is most likely to want to move it.
    """

    def __init__(self, window, **kwargs) -> None:
        super().__init__(**kwargs)
        self.window_host = window


class FinalSky(WindowDrag, StarPanel):
    """The last step: a full-bleed sky, square-cornered, with one button."""

    def __init__(self, window, **kwargs) -> None:
        super().__init__(radius=0, **kwargs)
        self.window_host = window


class DoorButton(QPushButton):
    """The door's one button, painted rather than stylesheeted.

    A pill is a corner radius of exactly half the height, and Qt Style Sheets
    will not draw one. At half or more the radius is discarded and the widget
    comes out a rectangle; just under half — which is what the rule here used
    to say — closes the curve early and leaves a few pixels of flat edge in
    the middle of each end. At this size that flat is plainly visible: the
    ends look dented rather than round.

    So the shape is a painted path, where the radius can be exactly half and
    the ends are true semicircles.
    """

    _PAD_X = 34

    def __init__(self, text: str, on_click, height: int = 54) -> None:
        super().__init__(text)
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(theme.body_font(theme.SIZE_BODY, 600))
        # Nothing for the stylesheet to paint: the global QPushButton rule
        # would otherwise draw its own square background under this one.
        self.setStyleSheet("QPushButton{background:transparent;border:none;}")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.clicked.connect(lambda: on_click())

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        hint.setWidth(self.fontMetrics().horizontalAdvance(self.text())
                      + 2 * self._PAD_X)
        hint.setHeight(self.height())
        return hint

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = box.height() / 2

        if self.isDown():
            fill = QColor(theme.ACCENT_SOFT)
        elif self.underMouse():
            fill = QColor("#FFFFFF")
        else:
            fill = QColor(theme.SKY_INK)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(box, radius, radius)

        p.setPen(QColor(theme.ACCENT_DEEP))
        p.setFont(self.font())
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()


class Onboarding(QWidget):
    """The four-step first run. Emits :attr:`finished` when it is done."""

    finished = pyqtSignal()

    def __init__(self, app) -> None:  # app: MainWindow
        super().__init__()
        self.app = app
        self.setAcceptDrops(True)
        self._step = 0
        #: The Next buttons that are held until their step is done, by name.
        self._gates: dict[str, QWidget] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = self._header()
        root.addWidget(self.header)

        self.stack = QStackedWidget()
        # In STEPS order: model, CV, where, knowledge.
        self.stack.addWidget(self._model_step())
        self.stack.addWidget(self._cv_step())
        self.stack.addWidget(self._where_step())
        self.stack.addWidget(self._knowledge_step())
        root.addWidget(self.stack, 1)

        # The door lives outside the stack: it hides the header too, so the
        # sky is the entire window rather than a panel under a smaller sky.
        self.door = self._door()
        self.door.setVisible(False)
        root.addWidget(self.door, 1)

        self.go_to(0)

    # --- chrome ---------------------------------------------------------------

    def _header(self) -> QWidget:
        # A small drift on the header: it is a band under a form, so the sky
        # moving as far as it does on the door would pull the eye off the
        # thing being filled in.
        strip = HeaderSky(self.app, height=196, seed=5, animate=True, parallax=14)
        # A StarStrip is a fixed-height band, which is right everywhere else
        # and wrong here: the lead is two lines on half these steps, and the
        # overflow was painted straight through the step bar underneath it.
        # A minimum instead, so the band grows for the step that needs it.
        # Fixed at the two-line height. Growing to fit made the band 196px on
        # one step and 220px on the next, so every Next shifted the whole
        # page down by a line — the form you were about to read jumped.
        strip.setFixedHeight(222)
        strip.body.setContentsMargins(40, 14, 16, 28)
        strip.body.setSpacing(12)

        top = QWidget()
        top.setStyleSheet("background: transparent;")
        trow = QHBoxLayout(top)
        trow.setContentsMargins(0, 0, 0, 0)
        trow.setSpacing(10)
        trow.addWidget(SushiMark(22, on_dark=True))
        brand = QLabel("Sushi")
        brand.setStyleSheet(theme.on_sky(16, theme.SKY_INK, 500, display=True))
        trow.addWidget(brand)
        trow.addStretch(1)

        self.skip_btn = button("Skip for now", "Ghost", self._skip)
        self.skip_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;"
            f"color:{theme.SKY_INK_BODY};"
            f"font-size:13px;font-weight:600;padding:4px 2px;}}"
            f"QPushButton:hover{{color:{theme.SKY_INK};}}")
        trow.addWidget(self.skip_btn)

        # Cream glyphs on a translucent wash — the shell's ink would disappear
        # against the sky.
        self.win_btns = WindowButtons(
            self.app.showMinimized, self.app.toggle_maximise, self.app.close,
            ink=theme.SKY_INK_SOFT, hover="#33FFF8F4", ground=theme.HERO_MID)
        trow.addWidget(self.win_btns)
        strip.body.addWidget(top)

        strip.body.addStretch(1)
        self.eyebrow = QLabel("")
        self.eyebrow.setStyleSheet(theme.on_sky(
            theme.SIZE_SMALL, theme.SKY_INK_BODY, 600, extra="letter-spacing:1px;"))
        self.title = QLabel("")
        self.title.setStyleSheet(
            theme.on_sky(theme.SIZE_H1, theme.SKY_INK, display=True))
        self.lead = label("", wrap=True)
        self.lead.setFixedWidth(560)
        self.lead.setStyleSheet(theme.on_sky(15, theme.SKY_INK_BODY))
        for w in (self.eyebrow, self.title, self.lead):
            strip.body.addWidget(w)
        # How far along, as a shape rather than a number you have to read
        # against a total you were never told. Four bars, the done ones
        # filled — which also makes "Back" obviously available.
        self.progress = StepBar(len(STEPS))
        strip.body.addSpacing(4)
        strip.body.addWidget(self.progress, 0, Qt.AlignmentFlag.AlignLeft)
        return strip

    @staticmethod
    def _column(*widgets: QWidget) -> QWidget:
        """A centred 680px column in a scroll area — the body of a step."""
        host = QWidget()
        hv = QHBoxLayout(host)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.addStretch(1)

        col = QWidget()
        # Both bounds, not just the maximum. With a stretch either side the
        # column was handed a third of the window and the cards collapsed to
        # ~400px, which turned every card title into one word per line.
        col.setMinimumWidth(600)
        col.setMaximumWidth(680)
        v = QVBoxLayout(col)
        v.setContentsMargins(40, 32, 40, 36)
        v.setSpacing(12)
        # A spring at each end, so a short step sits in the middle of the
        # space it has instead of hugging the header with four hundred pixels
        # of cream below it. A step taller than the viewport pushes both
        # springs to zero and the scroll area takes over, as before.
        v.addStretch(1)
        for w in widgets:
            if isinstance(w, QWidget):
                v.addWidget(w)
            else:
                v.addLayout(w)
        v.addStretch(1)

        hv.addWidget(col, 1)
        hv.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        return scroll

    def _nav(self, next_text: str, on_next, back: bool = True,
             gate: str = "") -> QHBoxLayout:
        """Back / primary, directly under what they act on.

        Pushed to the bottom of the viewport the primary reads as unrelated to
        the cards above it, and on a tall window it is a screen away.

        The progress bar says which of four steps you are on, which is only
        half an answer if the one you want is behind you: a step skipped by
        accident, a CV that turned out to be the wrong file. The first step
        has nothing behind it, so it passes ``back=False`` rather than showing
        a button that does nothing.

        ``gate`` names a step whose Next is held until the step is actually
        done — see :meth:`refresh`. The button is kept rather than hidden:
        a disabled primary with a line under it saying what it is waiting for
        is a state someone can act on, where a missing one is a dead end.
        """
        row = QHBoxLayout()
        row.setSpacing(8)
        row.setContentsMargins(0, 10, 0, 0)
        b = button(next_text, "Primary", on_next)
        b.setMinimumHeight(46)
        b.setMinimumWidth(160)
        if gate:
            self._gates[gate] = b
        row.addWidget(b)
        if back:
            row.addWidget(button("Back", "Ghost",
                                 lambda: self.go_to(self._step - 1)))
        row.addStretch(1)
        return row

    # --- step 2: the CV --------------------------------------------------------

    def _cv_step(self) -> QWidget:
        self.cv_gate_note = faint("")
        return self._column(
            self._cv_card(), self._model_line(), self.cv_gate_note,
            self._nav("Next", lambda: self.go_to(2), gate="cv"))

    def _cv_card(self) -> QWidget:
        c = card("Card")
        row = QHBoxLayout()
        row.setSpacing(18)
        row.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(IconTile("file-text", 40, theme.TINT_GREEN, theme.TINT_GREEN_INK),
                      0, Qt.AlignmentFlag.AlignTop)
        self.cv_lbl = muted("A pdf, docx, txt or markdown file. You can also just "
                            "drop it anywhere on this window.")
        row.addWidget(text_col(h3("Your CV"), self.cv_lbl), 1)
        # One button. "Read it" was the second half of a two-step that had no
        # decision in the middle of it — Sushi reads the CV the moment it has
        # one and a model to read it with, so the only thing left to offer is
        # a different file.
        self.pick_btn = button("Choose file", "Chip", self._pick_cv)
        row.addWidget(self.pick_btn)
        c.layout().addLayout(row)

        # Half a minute of one request, with nothing to report part-way: a
        # count of seconds asks the reader to hold the expected total in
        # their head and do the subtraction. A bar is the same fact without
        # the arithmetic. It fills against how long this usually takes
        # rather than against real progress — there is none to measure — so
        # it stops short of the end and waits there rather than claiming to
        # be finished.
        self.cv_bar = QProgressBar()
        self.cv_bar.setRange(0, 100)
        self.cv_bar.setTextVisible(False)
        self.cv_bar.setFixedHeight(6)
        self.cv_bar.setVisible(False)
        c.layout().addWidget(self.cv_bar)

        self.cv_result = faint("")
        self.cv_result.setVisible(False)
        c.layout().addWidget(self.cv_result)
        return c

    def _pick_cv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose your CV", str(Path.home()),
            "CV files (*.pdf *.docx *.txt *.md)")
        if path:
            self.set_cv(path)

    def set_cv(self, path: str) -> None:
        # Into the library, and active. Onboarding attaches the first one;
        # the Profile page is where a second is added later.
        storage.set_active_resume(storage.add_resume(str(path)))
        self._read_error = False      # a new file deserves a fresh attempt
        self.refresh()

    #: A read is in flight. Without it the auto-read would start again on
    #: every refresh — and refresh runs on every step change and whenever
    #: the model picker reports a change.
    _reading = False
    #: Set when a read failed, so the retry is the user's to ask for rather
    #: than a loop that burns a request each time this screen redraws.
    _read_error = False

    def _read_cv(self) -> None:
        cv = storage.get_setting("cv_path", "")
        settings = load_settings()
        if self._reading or not cv:
            return
        if not Path(cv).exists():
            # The path is remembered, the file may not be: this one had been
            # moved, and the only feedback was a worker failing in the log.
            self.cv_result.setVisible(True)
            self.cv_result.setText(
                f"{Path(cv).name} is not where it was. Choose it again.")
            self._pick_cv()
            return
        if not settings.llm_enabled:
            # Not an error dialog: the model line on this very card already
            # says there is no model and offers the way to add one, and
            # `refresh` starts the read the moment there is one.
            return
        self._reading = True
        self.pick_btn.setEnabled(False)
        self.cv_result.setVisible(True)
        self._start_reading_clock()

        def job():
            return build_profile(LLM(settings), extract_text(cv))

        def done(profile: Profile) -> None:
            self._reading = False
            self._stop_reading_clock()
            # The CV guesses at where this person can work. If they have
            # already answered that themselves, their answer wins.
            profile = self._apply_pending_where(profile)
            # Stamped against the file, so the library knows this CV has
            # been read and All jobs' Refresh can tell when it changes.
            storage.record_cv_read(cv, profile)
            self.app.set_profile(profile)
            self.pick_btn.setEnabled(True)
            self.refresh()

        def failed(msg: str) -> None:
            self._reading = False
            self._read_error = True
            self._stop_reading_clock()
            self.pick_btn.setEnabled(True)
            self.cv_result.setVisible(True)
            self.cv_result.setText(
                f"That didn't work: {msg}. Choose the file again to retry, "
                f"or carry on and read it later from Profile.")

        # ``on_failed`` rather than a second connection to the worker: this
        # card answers a failed read inline, and wiring both put a modal box
        # with the raw exception in it on top of that answer.
        self.app.run(job, on_done=done, on_failed=failed)

    #: How long a CV read usually takes, from the models people actually use
    #: here: 20-30 seconds of one request. Long enough that a static
    #: "Reading…" reads as a hang, which is exactly what it looked like.
    READ_SECONDS = 30
    #: Where the bar waits once it has run past that. Not 100: a full bar
    #: that is still moving is the one thing worse than no bar.
    READ_CEILING = 92

    def _start_reading_clock(self) -> None:
        self._read_started = time.monotonic()
        if not hasattr(self, "_read_timer"):
            self._read_timer = QTimer(self)
            self._read_timer.timeout.connect(self._tick_reading)
        self.cv_bar.setValue(0)
        self.cv_bar.setVisible(True)
        self._tick_reading()
        self._read_timer.start(250)

    def _tick_reading(self) -> None:
        secs = time.monotonic() - getattr(self, "_read_started", 0)
        self.cv_bar.setValue(
            min(self.READ_CEILING, int(self.READ_CEILING * secs / self.READ_SECONDS)))
        if secs > self.READ_SECONDS * 1.5:
            self.cv_result.setText(
                f"Reading your CV, {int(secs)}s and longer than usual. Still "
                f"going; nothing has gone wrong.")
        else:
            self.cv_result.setText(f"Reading your CV, {int(secs)}s")

    def _stop_reading_clock(self) -> None:
        timer = getattr(self, "_read_timer", None)
        if timer is not None:
            timer.stop()
        self.cv_bar.setVisible(False)

    # --- step 3: where you want to work -----------------------------------------

    def _where_step(self) -> QWidget:
        return self._column(
            self._where_card(), self._nav("Next", self._next_from_where))

    def _next_from_where(self) -> None:
        """Save, then move on. Saving alone left the button doing nothing."""
        self._save_where()
        self.go_to(3)

    def _where_card(self) -> QWidget:
        c = card("Card")
        row = QHBoxLayout()
        row.setSpacing(18)
        row.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(IconTile("compass", 40, theme.TINT_SAND, theme.TINT_SAND_INK),
                      0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(text_col(
            h3("Where should Sushi look?"),
            muted("Pick as many as apply. A region covers every country in "
                  "it, so EMEA finds roles in Berlin, Dubai and Cape Town "
                  "alike. Leave it all empty and Sushi goes by your CV.")), 1)
        c.layout().addLayout(row)

        self.where = LocationPicker()
        # Saved as it is touched as well as on Next, because the Back button
        # and "Skip this" both leave this step without pressing Next, and a
        # choice that is only kept down the happy path is a choice that gets
        # lost.
        self.where.changed.connect(self._save_where)
        c.layout().addWidget(self.where)
        return c

    def _save_where(self) -> None:
        """Put the choice on the profile, or hold it until there is one."""
        places, workplace = self.where.values(), self.where.workplace()
        if self.where.is_set():
            storage.set_setting(LOCATIONS_CHOSEN, "1")
        p = self.app.profile
        if p is None:
            # The CV step is skippable, so there may be no profile yet. Held
            # here and applied the moment one arrives.
            self._pending_where = (places, workplace)
            return
        self._pending_where = None
        p.target_locations = places or p.target_locations
        p.workplace = workplace
        self.app.set_profile(p)

    def _apply_pending_where(self, profile: Profile) -> Profile:
        """Re-apply the location choice over a profile the CV just produced."""
        held = getattr(self, "_pending_where", None)
        if held:
            places, workplace = held
            if places:
                profile.target_locations = places
            profile.workplace = workplace
            self._pending_where = None
        elif storage.get_setting(LOCATIONS_CHOSEN, "") == "1":
            profile.target_locations = self.where.values() or profile.target_locations
            profile.workplace = self.where.workplace()
        return profile

    # --- step 4: the knowledge folder -------------------------------------------

    def _knowledge_step(self) -> QWidget:
        return self._column(
            self._knowledge_card(), self._folder_card(),
            self._nav("Next", lambda: self.go_to(self._DOOR)))

    def _knowledge_card(self) -> QWidget:
        c = card("Card")
        self.know_pick = button("Choose folder", "Chip", self._pick_knowledge)
        self.know_read = button("Read them", "Primary", self._read_knowledge)
        self.know_read.setEnabled(False)
        c.layout().addLayout(_head_row(
            "book", theme.TINT_BLUE, theme.TINT_BLUE_INK,
            "What you have written", self.know_pick, self.know_read))

        # Both of these go full width, under the row rather than beside the
        # buttons. In a text column squeezed to ~230px by two buttons, a
        # three-line caption becomes an eight-line one and drags the card to
        # twice its height — and a path gets clipped mid-drive.
        self.know_lbl = muted("")
        c.layout().addWidget(self.know_lbl)
        self.know_sub = faint(
            "Old cover letters, saved answers, notes on your notice period or "
            "visa status. Read on this machine, and only the answers are kept.")
        c.layout().addWidget(self.know_sub)

        self.know_bar = QProgressBar()
        self.know_bar.setTextVisible(False)
        self.know_bar.setFixedHeight(6)
        self.know_bar.setVisible(False)
        c.layout().addWidget(self.know_bar)

        self.know_result = faint("")
        self.know_result.setVisible(False)
        c.layout().addWidget(self.know_result)
        return c

    def _pick_knowledge(self) -> None:
        start = knowledge.folder() or Path.home()
        path = QFileDialog.getExistingDirectory(
            self, "Where are your old cover letters and answers?", str(start))
        if path:
            knowledge.set_folder(path)
            self.refresh()

    def _read_knowledge(self) -> None:
        root = knowledge.folder()
        if root is None:
            self._pick_knowledge()
            return
        if not load_settings().llm_enabled:
            self.app.error("Sushi needs a model to read these. Add one in "
                           "Settings → Model, then come back to this.")
            return

        files = knowledge.documents(root)
        if not files:
            self.know_result.setVisible(True)
            self.know_result.setText(
                "No documents in there. Sushi reads pdf, docx, txt and "
                "markdown files, including the ones in subfolders.")
            return

        self.know_read.setEnabled(False)
        self.know_bar.setVisible(True)
        self.know_bar.setRange(0, len(files))
        self.know_bar.setValue(0)
        self.know_result.setVisible(True)
        self.know_result.setText(f"Reading {len(files)} document(s)…")

        def job():
            # No progress callback: this runs on a worker thread, and touching
            # a widget from one is the fastest way to a native crash. The bar
            # is indeterminate-by-proxy instead — see below.
            return knowledge.ingest(root)

        def done(result: knowledge.Ingested) -> None:
            self.know_bar.setVisible(False)
            self.know_read.setEnabled(True)
            self.know_result.setText(result.summary)
            if result.errors:
                self.app.set_status(result.errors[0])
            self.refresh()

        def failed(_msg: str) -> None:
            self.know_bar.setVisible(False)
            self.know_read.setEnabled(True)
            self.know_result.setText(
                "That didn't finish. You can try again from Settings → Learning.")

        # A busy bar rather than a counted one: the work happens off-thread and
        # the honest alternative is a bar that sits at zero the whole time.
        self.know_bar.setRange(0, 0)
        self.app.run(job, on_done=done, on_failed=failed)

    # --- where things are kept -------------------------------------------------

    def _folder_card(self) -> QWidget:
        c = card("Card")
        c.layout().addLayout(_head_row(
            "folder", theme.TINT_SAND, theme.TINT_SAND_INK,
            "Where Sushi saves things",
            button("Change", "Chip", self._pick_folder),
            button("Open", "Chip", self._open_folder)))
        self.folder_lbl = muted("")
        c.layout().addWidget(self.folder_lbl)
        self.folder_sub = faint("")
        c.layout().addWidget(self.folder_sub)
        return c

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Where should Sushi keep your documents?", str(workspace()))
        if path:
            set_workspace(path)
            self.refresh()

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ensure_workspace())))

    # --- the model, confirmed on the step that uses it -------------------------

    def _model_line(self) -> QWidget:
        """A line on the CV step saying which model is about to read it.

        It used to be the only mention of a model at all, in amber, with a
        button that left onboarding for Settings — which is how someone
        arrived in the app with no model, no profile and no idea that the two
        were related. The model is step one now, so this is normally a green
        line confirming it; the button goes back to that step rather than out.
        """
        self.model_card = card("Sunken", margins=(20, 16, 20, 16), spacing=4)
        inner = QHBoxLayout()
        inner.setSpacing(12)
        self.model_dot = IconTile("", 10, theme.ACCENT, theme.ACCENT)
        inner.addWidget(self.model_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.model_lbl = faint("")
        inner.addWidget(self.model_lbl, 1)
        self.model_btn = button("Pick one", "Chip", lambda: self.go_to(0))
        inner.addWidget(self.model_btn)
        self.model_card.layout().addLayout(inner)
        return self.model_card

    # --- step 1: a model to think with ------------------------------------------

    def _model_step(self) -> QWidget:
        from .modelpicker import ModelPicker

        c = card("Card")
        self.picker = ModelPicker()
        self.picker.changed.connect(self.refresh)
        # One decision, one button. The picker's own "Use this" made the
        # first screen a two-step confirm: the free option was already
        # highlighted, yet Next stayed grey until a second button was found.
        # Here Next is the confirm.
        self.picker.use_outer_confirm()
        c.layout().addWidget(self.picker)
        self.model_gate_note = faint("")
        return self._column(
            c, self.model_gate_note,
            self._nav("Continue", self._next_from_model, back=False, gate="model"))

    def _next_from_model(self) -> None:
        if self.picker.save():
            self.go_to(1)
        elif self.picker._pending is not None:
            # A pasted key is being checked; carry on once it passes.
            def onward():
                self.picker.changed.disconnect(onward)
                self.go_to(1)
            self.picker.changed.connect(onward)
        else:
            self.refresh()

    # --- the door ---------------------------------------------------------------

    def _door(self) -> QWidget:
        sky = FinalSky(self.app, seed=21, density=1.9, animate=True,
                       padding=(40, 20, 20, 60), parallax=38)
        sky.body.setSpacing(0)

        # The chrome again: this step replaces the header, so without its own
        # copy the window loses its close button at the last screen.
        top = transparent(QWidget())
        trow = QHBoxLayout(top)
        trow.setContentsMargins(0, 0, 0, 0)
        trow.addStretch(1)
        self.door_btns = WindowButtons(
            self.app.showMinimized, self.app.toggle_maximise, self.app.close,
            ink=theme.SKY_INK_SOFT, hover="#33FFF8F4", ground=theme.HERO_MID)
        trow.addWidget(self.door_btns)
        sky.body.addWidget(top)

        sky.body.addStretch(2)
        sky.body.addWidget(Mark(96), 0, Qt.AlignmentFlag.AlignHCenter)
        sky.body.addSpacing(22)

        title = QLabel("You're set.")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title.setStyleSheet(theme.on_sky(42, theme.SKY_INK, display=True))
        sky.body.addWidget(title)
        sky.body.addSpacing(8)

        # A WrapLabel, not a QLabel. A centred wrapped QLabel is allocated its
        # sizeHint — one line wide and one line tall — so the second half of
        # the sentence is simply clipped.
        self.door_lead = label("", wrap=True)
        self.door_lead.setFixedWidth(520)
        self.door_lead.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.door_lead.setStyleSheet(theme.on_sky(15, theme.SKY_INK_BODY))
        sky.body.addWidget(self.door_lead, 0, Qt.AlignmentFlag.AlignHCenter)
        sky.body.addSpacing(28)

        go = DoorButton("Open Sushi", self._done)
        self.door_go = go
        go.setMinimumWidth(216)
        # No drop shadow. It was meant to lift the button off the sky and
        # instead smeared a dark grey halo down one side of it: a soft black
        # blur over a saturated clay goes muddy rather than dim, and at this
        # size the smear was wider than the button's own edge. Near-white on
        # clay already separates; it did not need the help.
        holder = transparent(QWidget())
        hrow = QHBoxLayout(holder)
        hrow.setContentsMargins(0, 0, 0, 0)
        hrow.addStretch(1)
        hrow.addWidget(go)
        hrow.addStretch(1)
        sky.body.addWidget(holder)
        sky.body.addSpacing(10)

        # The wireframe's last question: who presses Submit. Manual is the
        # default and the big button; Insane Mode is one deliberate click.
        self.door_insane = button("Open with Insane Mode on", "Ghost",
                                  self._done_insane)
        self.door_insane.setStyleSheet(
            f"QPushButton{{background:transparent;border:1px solid #55FFF8F4;"
            f"border-radius:20px;color:{theme.SKY_INK};font-size:13px;"
            f"font-weight:600;padding:9px 20px;}}"
            f"QPushButton:hover{{background:#22FFF8F4;}}")
        sky.body.addWidget(self.door_insane, 0, Qt.AlignmentFlag.AlignHCenter)
        sky.body.addSpacing(8)
        note = QLabel("Insane Mode applies for you, up to a daily limit. "
                      "You can switch any time from Home.")
        note.setStyleSheet(theme.on_sky(12, theme.SKY_INK_BODY))
        note.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        sky.body.addWidget(note, 0, Qt.AlignmentFlag.AlignHCenter)
        self.door_insane_note = note
        sky.body.addSpacing(14)

        back = button("Back a step", "Ghost", lambda: self.go_to(self._DOOR - 1))
        # Underlined, and in the body ink rather than a dimmer one: on the
        # door this is the only other thing you can do, and as a dim link in
        # a colour close to the sky it read as a caption.
        back.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;"
            f"color:{theme.SKY_INK_BODY};text-decoration:underline;"
            f"font-size:13px;font-weight:600;padding:4px 2px;}}"
            f"QPushButton:hover{{color:{theme.SKY_INK};}}")
        sky.body.addWidget(back, 0, Qt.AlignmentFlag.AlignHCenter)
        sky.body.addStretch(3)
        return sky

    # --- steps -----------------------------------------------------------------

    #: The last index is the door; everything before it is a form step.
    _DOOR = 4
    #: The two steps that have to be done rather than passed through, and the
    #: furthest anyone can get without them. Enforced here as well as on the
    #: buttons, so a stray ``go_to`` cannot walk round the gate.
    _MODEL_STEP = 0
    _CV_STEP = 1

    def _furthest(self) -> int:
        """The highest step reachable given what has actually been set up."""
        if not load_settings().llm_enabled:
            return self._MODEL_STEP
        if self.app.profile is None:
            return self._CV_STEP
        return self._DOOR

    def go_to(self, step: int) -> None:
        """Show one step. The last one is the sky, and takes over the window."""
        self._step = max(0, min(self._furthest(), step))
        door = self._step == self._DOOR
        self.header.setVisible(not door)
        self.stack.setVisible(not door)
        self.door.setVisible(door)
        if not door:
            changed = self.stack.currentIndex() != self._step
            self.stack.setCurrentIndex(self._step)
            if changed and self.isVisible():
                self._fade_in(self.stack.currentWidget())
            title, lead = STEPS[self._step]
            self.eyebrow.setText(f"STEP {self._step + 1} OF {len(STEPS)}")
            self.title.setText(title)
            self.lead.setText(lead)
            self.progress.set_step(self._step)
        self.refresh()

    def _fade_in(self, w: QWidget) -> None:
        """A step arriving: 220ms opacity, ease-out. Rare, so it may move;
        opacity only, so nothing reflows while it plays."""
        from PyQt6.QtCore import QEasingCurve, QPropertyAnimation
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        eff = QGraphicsOpacityEffect(w)
        eff.setOpacity(0.0)
        w.setGraphicsEffect(eff)
        a = QPropertyAnimation(eff, b"opacity", w)
        a.setDuration(220)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Dropped at the end: a lingering effect makes Qt render the whole
        # step offscreen on every repaint, which is what blurs text.
        a.finished.connect(lambda: w.setGraphicsEffect(None))
        a.start()
        self._step_anim = a

    def _skip(self) -> None:
        """"Skip this" moves on rather than bailing out entirely.

        Only ever shown on the two optional steps — see :meth:`refresh` — so
        it never has to decide whether a gate may be passed.
        """
        if self._step < self._DOOR:
            self.go_to(self._step + 1)
        else:
            self._done()

    # --- state -----------------------------------------------------------------

    def refresh(self) -> None:
        cv = storage.get_setting("cv_path", "")
        self.cv_lbl.setText(
            Path(cv).name if cv else
            "A pdf, docx, txt or markdown file. You can also just drop it "
            "anywhere on this window.")
        self.pick_btn.setText("Replace" if cv else "Choose file")

        p = self.app.profile
        # The read starts itself. This is the one place that knows all four
        # conditions — there is a CV, no profile yet, a model to read with,
        # and nothing already running — and it is reached again when any of
        # them changes, which is what makes "add a model later" work without
        # a button to come back and press.
        if (cv and p is None and not self._reading and not self._read_error
                and load_settings().llm_enabled):
            QTimer.singleShot(0, self._read_cv)
        self.cv_result.setVisible(bool(p) or self._reading or self._read_error)
        if p:
            self.cv_result.setText(
                f"Read: {p.full_name or 'your CV'} · {p.headline or p.current_title} · "
                f"{len(p.experience)} roles, {len(p.skills)} skills.")
            # The CV no longer pre-fills the next step. It used to put its
            # own guess at where this person can work into the picker, and a
            # pre-filled answer is an answer: most people press Next, and
            # the feed then filters on the city printed at the top of a CV —
            # which for anyone looking to move is the one place they do not
            # want. Where is asked, not inferred.
            if not self.where.is_set():
                self.where.set_workplace(getattr(p, "workplace", "any") or "any")

        know = knowledge.folder()
        self.know_lbl.setText(
            str(know) if know else "No folder chosen yet. This step is optional.")
        self.know_pick.setText("Change" if know else "Choose folder")
        self.know_read.setEnabled(bool(know))

        root = ensure_workspace()
        self.folder_lbl.setText(str(root))
        self.folder_sub.setText(
            "Holds " + ", ".join(WORKSPACE_TREE.values()).lower()
            + " as ordinary files. Nothing is locked inside the app.")

        s = load_settings()
        ready = s.llm_enabled
        self.model_dot.setStyleSheet(
            f"background:{theme.ACCENT if ready else theme.WARN};border-radius:5px;")
        self.model_lbl.setText(
            "Model ready."
            if ready else
            "No model yet. Sushi can't read a CV or fill a form until there is one.")
        self.model_btn.setVisible(not ready)

        # The two gates. Each says what it is waiting for rather than leaving
        # a dead button to be worked out.
        # Always pressable: Continue saves the highlighted option. If that
        # option cannot be used here (no key, not installed) the picker says
        # why under it and the step stays.
        self._gates["model"].setEnabled(True)
        self.model_gate_note.setText(
            "" if ready else
            "Not sure? Already pay for Claude or ChatGPT: pick Claude Code or "
            "Codex. Neither: OpenCode is free, or Ollama if your PC is strong.")
        self._gates["cv"].setEnabled(p is not None)
        self.cv_gate_note.setText(
            "" if p is not None else
            "Sushi needs your CV before it can start: it is what every match, "
            "letter and filled form is worked out from."
            if not self._reading else
            "Reading it now — this takes about half a minute.")

        # Skip belongs to the optional steps only. On a gated step it would be
        # a second way out of a wall, which is worse than no button at all.
        optional = self._CV_STEP < self._step < self._DOOR
        self.skip_btn.setVisible(optional or self._step == self._DOOR)
        self.skip_btn.setText(
            "Skip this" if self._step < self._DOOR else "Skip for now")
        self._render_door()

    def _render_door(self) -> None:
        """Tell the last screen what it is actually about to open.

        The no-CV wording is kept as a floor rather than a path: the CV step
        is a gate now, so nobody should reach this without a profile — but a
        door that greets a missing one with "you're set" would be a lie, and
        this screen is reached by a plain index.
        """
        p = self.app.profile
        learned = knowledge.read_count()
        if p:
            first = p.full_name.split()[0] if p.full_name else "there"
            lead = (f"Nice to meet you, {first}. Sushi starts looking for roles "
                    f"the moment this opens. Tell it what you want in the chat "
                    f"on the right and it will look again.")
            if learned:
                lead += f" It has read {learned} of your documents."
        else:
            lead = ("No CV yet, so Sushi has nothing to match against. You can "
                    "add one any time from Profile and it will start looking.")
        self.door_lead.setText(lead)
        can = bool(p) and load_settings().llm_enabled and not self.app.applies_myself()
        self.door_insane.setVisible(can)
        self.door_insane_note.setVisible(can)

    def _done(self) -> None:
        storage.set_setting("onboarded", "1")
        self.finished.emit()

    def _done_insane(self) -> None:
        from ..extras import load_autoapply, save_autoapply
        self._done()
        if self.app.applies_myself() or not load_settings().llm_enabled:
            return
        cfg = load_autoapply()
        cfg.enabled = True
        save_autoapply(cfg)
        # After the window has swapped to the app, so Home paints it on.
        QTimer.singleShot(300, self.app.start_autoapply)
        QTimer.singleShot(350, lambda: self.app.autoapply_changed.emit(
            self.app.autoapplying(), "Insane mode armed."))

    # --- drop a CV straight onto the screen ------------------------------------

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        """A file sets the CV; a folder sets the knowledge folder.

        Both are things someone will try to drop here, and guessing by type is
        unambiguous — there is exactly one slot for each.
        """
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_dir():
                knowledge.set_folder(p)
                # The knowledge step, if the gates before it are already
                # passed; `go_to` clamps to the furthest allowed otherwise.
                self.go_to(3)
                return
            if p.suffix.lower() in SUPPORTED:
                self.set_cv(str(p))
                return
        self.app.error("That file type isn't supported. Use PDF, DOCX, TXT or MD.")
