"""Insane mode — the one thing Sushi does without you watching.

Formerly "Auto Apply", and formerly a checkbox in a banner. The rename is not
cosmetic: a checkbox reads like a preference, and this is the only control in
the app that lets software act on your behalf while you are elsewhere. So it
gets the treatment the design reserves for a single statement per screen — a
starfield panel with one 216px disc in the middle of it.

Lanes are gone. Naming a lane, giving it keywords, a location filter and a
minimum score was a configuration language standing in front of a feature
whose whole promise is that it runs itself — and the information it asked for
was already known, off the CV and from whatever you tell the assistant.

The quality dial went the same way, and the page it left is the argument for
why. Its three bands were 10, 7 and 5 against a matcher whose parts are title
(6 exact, 4 partial), seniority (3), skills (3) and location (3) — and skills
are matched against the title and department only, because job boards do not
hand over a description, so that 3 is almost never earned. The feed is
already cut at 6. So on a real feed all three settings counted the same 400
roles and selected the same ones: a control offering a choice between three
identical answers, costing a decision every time the page was opened. What it
was really being asked to express — "start with the best" — is not a filter
at all. It is the order, and the queue was already in it.

One decision remains: how many a day.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (QHBoxLayout, QSpinBox, QTableWidget, QVBoxLayout,
                             QWidget)

from ...extras import applied_today, load_autoapply, save_autoapply
from .. import theme
from ..brand import paint_lazy_mascot
from ..components import (ROW_HEIGHT, SettingRow, button, card, company_cell,
                          faint, h3, label, muted, page_title, status_cell,
                          text_cell, tune_table)
from ..controls import InsaneButton
from ..starfield import StarPanel, transparent
from .base import Page, scroll_page


class ConsolePanel(StarPanel):
    """The console sky, with the mascot lounging out in the right-hand stars.

    The panel is mostly empty either side of the disc, and that emptiness was
    doing nothing for the one feature that runs without you. The mascot takes
    the right of it and naps while a run is on — the joke being that the only
    thing on screen that *could* look busy is the thing with the least to do.

    It is painted rather than laid out: the body layout is a centred column,
    and putting a widget beside it would pull the disc off centre.
    """

    #: The mascot's box. Big enough to carry the face at this distance,
    #: small enough not to compete with a 216px disc.
    MASCOT = 92
    #: Clear of the panel's right edge, with room for the z's — which drift
    #: about a third of a mascot past its box.
    MARGIN = 40
    #: The centre column is 460px, so this is the width at which the two stop
    #: touching. Narrower than that the mascot drops out rather than crowding
    #: the thing it is decorating.
    MIN_WIDTH = 760

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._armed = False

    def set_armed(self, on: bool) -> None:
        if on != self._armed:
            self._armed = on
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        w, h = self.width(), self.height()
        if w < self.MIN_WIDTH:
            return
        # A second painter rather than a hook in StarPanel: the base class
        # ends its own, and the sky has no business knowing about mascots.
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.MASCOT
        paint_lazy_mascot(p, w - s - self.MARGIN, h / 2 - s * 0.55, s,
                          t=self._t, asleep=self._armed)
        p.end()


class AutoApplyPage(Page):
    title = "Insane mode"
    assistant_key = "auto_apply"

    def build(self) -> None:
        host, lay, scroll = scroll_page()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        # Guards the write-back while on_show is pushing stored state into the
        # widgets; without it, loading the page saves the page.
        self._loading = False
        # Loaded now, not first in on_show: the window can stop a run before
        # this page has ever been opened, and the signal that tells the page
        # so would otherwise find no config and crash the app.
        self.cfg = load_autoapply()

        lay.addWidget(page_title(
            "Insane mode", over="AUTOMATION",
            body="Arm it and Sushi applies to your best matches on its own, "
                 "up to a cap you set."))

        lay.addWidget(self._console())
        lay.addSpacing(6)

        # The end of the run. Sushi fills a form and leaves it on screen for
        # you, which is right — and a run of fifteen then finished with
        # fifteen tabs up, a table full of "Filled", and nowhere in the app
        # that said "done, shut it". Quitting Sushi was the close button.
        lay.addWidget(self._finished_banner())

        # --- what it is doing, right now ---------------------------------------
        #
        # This is where "How good a match" used to be: a card explaining the
        # quality dial, in the one place on the page you look to find out
        # whether anything is happening. The dial is two decisions you make
        # once; what the run is doing is the thing you came back to check. So
        # the table takes the position and the dial moves into the cap card
        # as three chips.
        lay.addWidget(self._run_table())

        # --- the setting, now that there is one of it --------------------------
        capc = card("Card", spacing=theme.GAP_MD)
        self.cap_spin = QSpinBox()
        self.cap_spin.setRange(1, 100)
        self.cap_spin.setFixedWidth(96)
        self.cap_spin.valueChanged.connect(self._save_cap)
        self.cap_row = SettingRow("Daily cap", "", self.cap_spin)
        self.cap_lbl = self.cap_row.help_label
        capc.layout().addWidget(self.cap_row)
        lay.addWidget(capc)
        lay.addStretch(1)

    # --- the end of a run -----------------------------------------------------

    def _finished_banner(self) -> QWidget:
        """Shown once the queue is empty and browsers are still up."""
        self.finished = card("Card")
        self.finished_lbl = h3("")
        self.finished.layout().addWidget(self.finished_lbl)
        self.finished_note = muted("")
        self.finished.layout().addWidget(self.finished_note)
        row = QHBoxLayout()
        row.addWidget(button("Open the tracker", "Chip",
                             lambda: self.app.navigate("Tracker")))
        row.addStretch()
        # Not Primary. Primary on this page is the 216px disc, and the way
        # out of a run is not the thing the page is asking you to do.
        row.addWidget(button("Close the browser", "", self.app.close_browsers,
                             icon="x"))
        # Transparent, or the holder paints the page background and leaves a
        # pale band across the bottom of the card it sits in.
        holder = transparent(QWidget())
        holder.setLayout(row)
        self.finished.layout().addWidget(holder)
        self.finished.setVisible(False)
        return self.finished

    def _render_finished(self) -> None:
        """Say the run is over, and only when it actually is.

        Three conditions, and all of them are needed. Something must have
        run, or this is a banner about nothing; nothing may still be
        filling, or it is announcing the end half way through; and a browser
        has to be open, or there is nothing left to close.
        """
        rows = self.app.autoapply_rows()
        filled = [r for r in rows
                  if r.get("state") in ("needs_review", "submitted")]
        open_browsers = self.app.open_browsers()
        over = bool(rows) and not self.app.autoapply_filling()
        self.finished.setVisible(over and bool(open_browsers) and bool(filled))
        if not self.finished.isVisible():
            return
        sent = sum(1 for r in filled if r.get("state") == "submitted")
        waiting = len(filled) - sent
        if waiting and sent:
            self.finished_lbl.setText(
                f"{sent} sent, {waiting} filled and waiting for you.")
        elif waiting:
            self.finished_lbl.setText(
                f"{waiting} application{'s' if waiting > 1 else ''} filled "
                f"and waiting for you in the browser.")
        else:
            self.finished_lbl.setText(
                f"{sent} application{'s' if sent > 1 else ''} sent. "
                f"That is the run.")
        self.finished_note.setText(
            "Review and submit what is open, then close it here. Anything "
            "you have not submitted lives in the page and goes with it; "
            "every row is on the Tracker either way.")

    # --- the run table --------------------------------------------------------

    _ROW_HEIGHT = ROW_HEIGHT
    #: Past this the table scrolls rather than growing. Ten rows of 56px is
    #: already most of a screen.
    _MAX_VISIBLE_ROWS = 10

    def _run_table(self) -> QWidget:
        c = card("Card")
        head = QHBoxLayout()
        head.addWidget(h3("What it is doing"))
        head.addStretch()
        self.run_count = faint("")
        head.addWidget(self.run_count)
        # The end of a run. Without it the forms Sushi left open for review
        # stayed open with nothing in the app to say "I'm done".
        self.finish_btn = button("Finish & close browsers", "",
                                 self.app.finish_autoapply_run, icon="x")
        self.finish_btn.setToolTip(
            "Stop, close every browser Sushi opened and clear this list. "
            "Forms you have not submitted are left unsent.")
        self.finish_btn.setVisible(False)
        head.addWidget(self.finish_btn)
        c.layout().addLayout(head)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("AppTable")
        self.table.setHorizontalHeaderLabels(["COMPANY", "ROLE", "STATUS", "DETAIL"])
        # The detail column stretches rather than the role: here the sentence
        # on the right is what changes as it works, and it is the thing you
        # are reading down the page. The other three are cut to what their
        # content actually needs so that sentence is not elided to "2
        # question(s) need…" — the half that matters is the end of it.
        tune_table(self.table, widths={2: 134},
                   weights={0: 1.0, 1: 1.5, 3: 1.8})
        c.layout().addWidget(self.table)

        self.table_empty = muted(
            "Nothing running. Arm it above and the roles it picks appear "
            "here, best match first, filling one at a time.")
        c.layout().addWidget(self.table_empty)
        self.table.setVisible(False)
        return c

    def _paint_rows(self, rows: list) -> None:
        """Render the run. Called on every change, so it stays cheap."""
        t = self.table
        t.setRowCount(0)
        for i, row in enumerate(rows):
            r = t.rowCount()
            t.insertRow(r)
            t.setRowHeight(r, self._ROW_HEIGHT)
            company = row.get("company") or ""
            t.setCellWidget(r, 0, company_cell(company, i) if company
                            else text_cell(""))
            title = row.get("title") or ""
            score = row.get("score") or 0
            t.setCellWidget(r, 1, text_cell(f"{title}  ({score})" if score
                                            else title))
            t.setCellWidget(r, 2, status_cell(row.get("state") or "queued"))
            t.setCellWidget(r, 3, text_cell(row.get("detail") or "",
                                            muted_text=True))
        # Grow with the run rather than keeping a fixed box with its own
        # scrollbar inside the page's scrollbar. Capped, because a cap of 40
        # should not push the settings below it off the end of the world.
        shown = min(len(rows), self._MAX_VISIBLE_ROWS)
        t.setFixedHeight(t.horizontalHeader().height()
                         + shown * self._ROW_HEIGHT + 8)
        t.setVisible(bool(rows))
        self.table_empty.setVisible(not rows)
        self.finish_btn.setVisible(bool(rows))
        done = sum(1 for r in rows
                   if r.get("state") in ("needs_review", "submitted"))
        self.run_count.setText(
            f"{done} of {len(rows)} filled" if rows else "")
        self._render_finished()

    # --- the console ----------------------------------------------------------

    def _console(self) -> QWidget:
        """The starfield panel with the arm button at its centre."""
        panel = self.console_panel = ConsolePanel(
            seed=13, density=1.15, animate=True, padding=(30, 26, 30, 30))
        panel.body.setSpacing(6)
        panel.body.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.console_state = label("", wrap=False)
        self.console_state.setStyleSheet(theme.on_sky(
            theme.SIZE_SMALL, theme.SKY_INK_SOFT, 600, extra="letter-spacing:2px;"))
        self.console_state.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        panel.body.addWidget(self.console_state)

        self.arm_btn = InsaneButton(216)
        self.arm_btn.armed_changed.connect(self._armed)
        self.app.autoapply_changed.connect(self._run_state)
        holder = transparent(QWidget())
        hrow = QHBoxLayout(holder)
        hrow.setContentsMargins(0, 0, 0, 0)
        hrow.addStretch()
        hrow.addWidget(self.arm_btn)
        hrow.addStretch()
        panel.body.addWidget(holder)

        self.console_note = label("", wrap=True)
        self.console_note.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.console_note.setStyleSheet(theme.on_sky(13, theme.SKY_INK_BODY))
        # A fixed width rather than a maximum: a centre-aligned wrapped label
        # gets allocated its sizeHint, which for one long line is one line
        # wide and one line tall — so the second half is simply clipped.
        self.console_note.setFixedWidth(460)
        panel.body.addWidget(self.console_note, 0, Qt.AlignmentFlag.AlignHCenter)

        # What it is doing right now, under the button. Without this the only
        # evidence that anything is happening is a browser appearing on
        # another part of the screen.
        self.run_line = label("", wrap=True)
        self.run_line.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.run_line.setFixedWidth(460)
        self.run_line.setStyleSheet(theme.on_sky(13, "#F0C98A"))
        self.run_line.setVisible(False)
        panel.body.addWidget(self.run_line, 0, Qt.AlignmentFlag.AlignHCenter)
        return panel

    def _run_state(self, armed: bool, note: str) -> None:
        """Follow the window — it may have been armed from elsewhere.

        An empty note leaves whatever was last said in place. Clearing it was
        why an armed run could look like it had never started: opening this
        page calls through here with no note, and the line reporting what it
        was doing was wiped on the way in.
        """
        if note:
            self.run_line.setText(note)
        self.run_line.setVisible(bool(self.run_line.text()) and armed)
        self.console_panel.set_armed(armed)
        if not self._loading:
            self.arm_btn.setChecked(armed)
        # The caption under the glyph is the one thing always on screen, so
        # it carries the honest summary rather than a fixed word.
        if armed:
            self.arm_btn.set_caption(
                "Filling now, tap to stop" if self.app.autoapply_filling()
                else "Armed and waiting, tap to stop")
        else:
            self._render_console()
        self._render_finished()

    def _armed(self, on: bool) -> None:
        if self._loading:
            return
        if on and self.app.applies_myself():
            self._loading = True
            self.arm_btn.setChecked(False)
            self._loading = False
            self.app.set_status(self.app._insane_blocked_note())
            self.console_note.setText(self.app._insane_blocked_note())
            return
        self.cfg.enabled = on
        save_autoapply(self.cfg)
        self._render_console()
        self.app.set_status("Insane mode armed." if on
                            else "Insane mode disarmed. Nothing will be sent on its own.")
        # Refresh the sidebar badge without re-entering this page's on_show.
        self.app.refresh_chrome()
        # And then actually do it. Arming used to end here.
        if on:
            self.app.start_autoapply()
        else:
            self.app.stop_autoapply("Disarmed. Nothing new will start.")

    # --- lifecycle ------------------------------------------------------------

    def on_show(self) -> None:
        # How many are waiting depends on the feed, which changes under us.
        try:
            self.app.matches_changed.disconnect(self._feed_changed)
        except TypeError:
            pass
        self.app.matches_changed.connect(self._feed_changed)
        try:
            self.app.autoapply_rows_changed.disconnect(self._paint_rows)
        except TypeError:
            pass
        self.app.autoapply_rows_changed.connect(self._paint_rows)
        self._loading = True
        self.cfg = load_autoapply()
        self.cap_spin.setValue(self.cfg.daily_cap)
        self.arm_btn.setChecked(self.cfg.enabled)
        self._loading = False
        self._render_console()
        self._render_cap()
        # A run already under way when you open the page: show it, rather
        # than an empty table that contradicts the armed button above it.
        self._paint_rows(self.app.autoapply_rows())
        self._run_state(self.app.autoapplying(), "")

    def _render_console(self) -> None:
        self.console_panel.set_armed(self.cfg.enabled)
        used = applied_today()
        left = max(0, self.cfg.daily_cap - used)
        if self.cfg.enabled:
            self.console_state.setText("ARMED")
            self.console_note.setText(
                f"{used} of {self.cfg.daily_cap} sent today · {left} left · "
                f"best matches first.")
        else:
            self.console_state.setText("STANDBY")
            self.console_note.setText(
                f"Best matches first · cap {self.cfg.daily_cap}/day. "
                f"Nothing is sent until you arm it.")
        self.arm_btn.set_caption(
            f"Up to {left} today" if left else "Today's cap is spent")

    # --- settings -------------------------------------------------------------

    def _feed_changed(self) -> None:
        """The feed moved under us, so the counts on this page did too."""
        self._render_cap()
        self._render_console()

    def _save_cap(self) -> None:
        if self._loading:
            return
        self.cfg.daily_cap = self.cap_spin.value()
        save_autoapply(self.cfg)
        self._render_cap()
        self._render_console()

    def _render_cap(self) -> None:
        """The cap, and what it is being spent out of.

        This row is the only setting left, so it carries what the quality
        dial's help line used to: the size of the pool. Without it the page
        says how many it may send and never how many there are to send to,
        which was the one number the dial was genuinely providing.
        """
        from ...extras import still_open

        used = applied_today()
        waiting = len(still_open(self.app.matches)) if self.app.matches else 0
        note = f"{used} of {self.cfg.daily_cap} used today, best matches first."
        if waiting:
            note += f" {waiting:,} role(s) not yet dealt with to work through."
        self.cap_row.set_help(note)

    # Preview and "Queue these" are gone. Both answered "what would it do?"
    # — a question the table above now answers by doing it: arming puts the
    # same list on screen, in the same order, and then fills it. Queueing for
    # later still exists on the Dashboard, where the feed is.
