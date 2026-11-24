"""Settings — apply behaviour, model credentials, company corpus, account."""
from __future__ import annotations

import time

from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLineEdit,
                             QPlainTextEdit, QSizePolicy, QStackedWidget,
                             QVBoxLayout, QWidget)

from ... import knowledge, learning, storage
from ...config import DATA_DIR, ensure_workspace, load_settings, set_workspace, workspace
from ...autofill import SIGNIN_URLS, open_for_signin
from ...discovery import (boards, pending_probes, refresh_companies,
                          resolve_boards)
from ...extras import (APPLY_MODES, chosen_apply_mode, set_apply_mode, FILL_MODE_WORDS, FILL_MODES, RESUME_MODES,
                       load_apply_settings, save_apply_settings)
from .. import dialogs, theme
from ..locations import LOCATIONS_CHOSEN, LocationPicker
from ..components import (Block, IconTile, Segmented, SettingRow, button,
                          card, divider, faint, h2, h3, label, muted,
                          switch_row, text_col)
from .base import Page, scroll_page

SECTIONS = ["Where to look", "Apply settings", "Learning", "Model", "Files",
            "Companies", "Account"]


class SettingsPage(Page):
    title = "Settings"
    assistant_key = "dashboard"

    def build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        navcol = QVBoxLayout()
        navcol.setContentsMargins(theme.PAGE_PAD, theme.PAGE_PAD,
                                  theme.GAP_SM, theme.PAGE_PAD)
        navcol.setSpacing(2)
        navcol.addWidget(label("Settings", "H2"))
        navcol.addSpacing(theme.GAP_MD)
        # The section the user is on. ``on_show`` is not only "the page was
        # opened" — the scanner's refresh calls it on whatever page is up,
        # several times a minute during a scan — so anything it resets is a
        # thing that will not stay put while the app is working.
        self._section = SECTIONS[0]
        self._nav = {}
        for name in SECTIONS:
            b = button(name, "NavItem", lambda n=name: self._go(n))
            self._nav[name] = b
            navcol.addWidget(b)
        navcol.addStretch()
        navw = QWidget(); navw.setFixedWidth(186); navw.setLayout(navcol)
        root.addWidget(navw)

        # One section in the scroll area at a time. They used to share a
        # stack, which is as tall as its tallest page: "Apply settings" sat
        # on the Model section's height, a screen of empty scroll under it.
        self._sections = [self._where_section(), self._apply_section(),
                          self._learning_section(), self._model_section(),
                          self._files_section(), self._companies_section(),
                          self._account_section()]
        self._holder = QWidget()       # keeps the ones not on show alive
        for w in self._sections:
            w.setParent(self._holder)
            w.hide()
        _, _, scroll = scroll_page()
        self._scroll = scroll
        root.addWidget(scroll, 1)

    def on_show(self) -> None:
        self._load_apply()
        self._load_where()
        self._load_model()
        self._load_learning()
        self._load_files()
        self._go(self._section)
        # The counts read the whole company list, the job database and the
        # process table — a second or more on a busy laptop. Worked out off
        # the UI thread, so the page appears at once and the numbers follow.
        self._load_counts()

    def _load_counts(self) -> None:
        def work():
            from ...autofill import orphaned_browsers
            jobs = self._jobs_note() if hasattr(self, "jobsdb_lbl") else ""
            return self._corpus_note(), jobs, len(orphaned_browsers())

        def done(res) -> None:
            corpus, jobs, n = res
            self.corpus_lbl.setText(corpus)
            if hasattr(self, "jobsdb_lbl"):
                self.jobsdb_lbl.setText(jobs)
            if hasattr(self, "browsers_lbl"):
                self.browsers_lbl.setText(
                    f"{n} browser(s) left open by an earlier run." if n
                    else "No browsers left over.")

        self.app.run(work, on_done=done, on_failed=lambda _m: None)

    def _go(self, name: str) -> None:
        self._section = name
        idx = SECTIONS.index(name)
        want = self._sections[idx]
        if self._scroll.widget() is not want:
            old = self._scroll.takeWidget()
            if old is not None:
                old.hide()
                old.setParent(self._holder)
            self._scroll.setWidget(want)
            want.show()
        if getattr(self, "_scroll", None) is not None:
            self._scroll.verticalScrollBar().setValue(0)
        for n, b in self._nav.items():
            b.setObjectName("NavItemActive" if n == name else "NavItem")
            b.style().unpolish(b); b.style().polish(b)

    # --- apply settings -----------------------------------------------------

    #: The one-line version of each resume mode. The four-sentence paragraph
    #: that used to sit under the control said the same thing about all three
    #: at once, so whichever one you had chosen you read two descriptions of
    #: modes you had not. One line, about the mode you are actually on.
    _RESUME_HELP = {
        "off": "Describes you exactly as your CV does.",
        "honest": "Leads with the experience this job asks for. Never untrue.",
        "aggressive": "Mirrors the job description closely. Never untrue.",
    }

    def _apply_section(self) -> QWidget:
        c = card("Card", spacing=theme.GAP_MD)
        c.layout().addWidget(h2("Apply settings"))
        c.layout().addWidget(muted("How applications are tailored and sent."))
        c.layout().addWidget(divider())

        # The one choice that decides what everything else here means.
        self.mode_seg = Segmented(list(APPLY_MODES), labels={
            "self": "I apply myself", "sushi": "Sushi applies"})
        self.mode_seg.changed.connect(self._set_apply_mode)
        self.mode_row = SettingRow("How do you want to apply?", "", self.mode_seg)
        c.layout().addWidget(self.mode_row)
        c.layout().addWidget(divider())

        self.resume_seg = Segmented(RESUME_MODES, labels={
            m: m.capitalize() for m in RESUME_MODES})
        self.resume_seg.changed.connect(self._set_mode)
        self.resume_row = SettingRow(
            "Resume optimisation", "", self.resume_seg)
        c.layout().addWidget(self.resume_row)

        # Fill mode had no control anywhere in the app. It defaulted to
        # "direct", so "auto" — the lookup, then one short model call per
        # free-text question, and only then the agent — existed in the code,
        # was documented in extras.py, and could not be chosen by anyone.
        self.fill_seg = Segmented(FILL_MODES, labels={
            m: m.capitalize() for m in FILL_MODES})
        self.fill_seg.changed.connect(self._set_fill_mode)
        self.fill_row = SettingRow("Fill mode", "", self.fill_seg)
        c.layout().addWidget(self.fill_row)

        c.layout().addWidget(divider())

        # "Auto-approve tailored resumes (skip the preview step)" and "Always
        # review before submit" used to sit here. Both were saved, both were
        # drawn with their state, and nothing anywhere read either one — the
        # first referred to a preview step that does not exist, and the second
        # duplicated "Press Submit for me" below, which is real. A switch
        # that does nothing is worse than a missing one: it is a promise.
        row, self.auto_cl = switch_row(
            "Cover letters", "Written for you when a job asks for one.",
            on_toggle=lambda _on: self._save_apply())
        c.layout().addWidget(row)

        row, self.explorative = switch_row(
            "Answer every question",
            "Sushi works out the ones it cannot look up, and flags each one "
            "for you. Pay, immigration status and demographics are never "
            "guessed.",
            on_toggle=lambda _on: self._save_apply())
        c.layout().addWidget(row)

        # Stated as what you get rather than as what is suppressed. The switch
        # was "Run the apply browser hidden (uncheck to watch it)" — a
        # double negative you had to parse twice to answer "do I see it?".
        row, self.watch_browser = switch_row(
            "Watch it work", "Shows the browser window while a form is filled.",
            on_toggle=self._set_watch)
        c.layout().addWidget(row)

        row, self.autosubmit = switch_row(
            "Press Submit for me",
            "Off, Sushi fills the form and stops for your review.",
            on_toggle=self._toggle_autosubmit)
        c.layout().addWidget(row)

        c.layout().addWidget(divider())
        c.layout().addWidget(h3("Signed-in sites"))
        c.layout().addWidget(faint(
            "Sign in once and every later application inherits the session, "
            "including “Sign in with Google” and “Apply with LinkedIn”. Sushi "
            "never types or stores a password."))
        srow = QHBoxLayout()
        srow.setSpacing(theme.GAP_XS)
        for name in SIGNIN_URLS:
            srow.addWidget(button(name, "Chip",
                                  lambda _=False, n=name: self._signin(n),
                                  icon="external-link"))
        srow.addStretch()
        c.layout().addLayout(srow)

        brow = QHBoxLayout()
        brow.setSpacing(theme.GAP_SM)
        self.browsers_lbl = faint("")
        brow.addWidget(self.browsers_lbl, 1)
        brow.addWidget(button("Close leftover browsers", "Chip",
                              self._close_browsers, icon="x"))
        c.layout().addLayout(brow)
        return self._wrap(c)

    def _close_browsers(self) -> None:
        """Clear browsers stranded by an earlier run.

        Sushi leaves each filled form open on purpose, so quitting without
        closing them strands a Chromium holding the profile directory — and
        Chromium allows only one process per directory, so the next
        application cannot start at all. Normally handled at launch; this is
        here for when it happens mid-session.
        """
        from ...autofill import close_orphaned_browsers
        n = close_orphaned_browsers()
        self.app.set_status(
            f"Closed {n} leftover browser(s)." if n
            else "No leftover browsers to close.")
        self._load_apply()
        self._load_counts()

    def _signin(self, name: str) -> None:
        """Open the apply browser at a sign-in page and hand it to the user."""
        url = SIGNIN_URLS.get(name)
        if not url:
            return
        self.app.busy(True, f"Opening {name}…")

        def job():
            open_for_signin(url)
            return name

        def done(_n):
            self.app.busy(False, "")
            self.app.set_status(
                f"Signed-in window open. Finish signing in to {name} there, and "
                f"applications will use it from now on.")

        def failed(msg):
            self.app.busy(False, "")
            self.app.error(f"Could not open the browser: {msg}")

        self.app.run(job, on_done=done, on_failed=failed)

    _MODE_HELP = {
        "self": ("Sushi finds the jobs that match your CV. Apply opens the "
                 "posting in your own browser and you take it from there. "
                 "No model needed."),
        "sushi": ("Sushi fills the forms with your own model and unlocks "
                  "Insane mode. Set the model up under Model."),
    }

    def _paint_apply_mode(self) -> None:
        mode = chosen_apply_mode()
        self.mode_seg.set_value(mode)
        help_text = self._MODE_HELP[mode]
        if mode == "sushi" and not load_settings().llm_enabled:
            help_text = ("No model set up yet, so Apply only opens the job "
                         "for now. Add your API key under Model.")
        self.mode_row.set_help(help_text)

    def _set_apply_mode(self, mode: str) -> None:
        set_apply_mode(mode)
        if mode == "self":
            cfg = load_autoapply()
            if cfg.enabled:
                cfg.enabled = False
                save_autoapply(cfg)
                self.app.stop_autoapply("Stopped: you now apply yourself.")
        self._paint_apply_mode()
        self.app.refresh_chrome()
        if mode == "sushi" and not load_settings().llm_enabled:
            self._go("Model")

    def _load_apply(self) -> None:
        self._paint_apply_mode()
        a = load_apply_settings()
        self.resume_seg.set_value(a.resume_optimization)
        self.resume_row.set_help(self._RESUME_HELP.get(a.resume_optimization, ""))
        self.fill_seg.set_value(a.fill_mode)
        words = FILL_MODE_WORDS.get(a.fill_mode, "").capitalize() + "."
        if a.fill_mode == "agent":
            # Said here rather than discovered afterwards. The agent re-sends
            # the page and its own instructions every step — ten to twelve
            # thousand tokens — so on a free tier of eight thousand a minute
            # it cannot take a single step, and what the user sees is an
            # empty browser and no explanation.
            words += (" Needs about 12,000 tokens per step, more than a free "
                      "tier allows. Auto does the same job for a fraction.")
        self.fill_row.set_help(words)
        # ``set_state``, not ``setChecked``: these are being loaded, and a
        # loaded value must not animate or fire the handler that would save it
        # straight back over itself.
        self.auto_cl.set_state(a.auto_cover_letter)
        self.explorative.set_state(a.explorative)
        s = load_settings()
        self.watch_browser.set_state(not s.headless)
        self.autosubmit.set_state(s.autosubmit)

    def _set_mode(self, mode: str) -> None:
        a = load_apply_settings(); a.resume_optimization = mode; save_apply_settings(a)
        self.resume_row.set_help(self._RESUME_HELP.get(mode, ""))

    def _set_fill_mode(self, mode: str) -> None:
        a = load_apply_settings(); a.fill_mode = mode; save_apply_settings(a)
        self._load_apply()

    def _save_apply(self) -> None:
        a = load_apply_settings()
        a.auto_cover_letter = self.auto_cl.isChecked()
        a.explorative = self.explorative.isChecked()
        save_apply_settings(a)

    def _set_watch(self, on: bool) -> None:
        """The switch says "watch it"; the setting stores "headless"."""
        storage.set_setting("headless", "0" if on else "1")

    def _toggle_autosubmit(self, on: bool) -> None:
        if on:
            # Off until they say yes: the switch must not read "on" while
            # the question is still open.
            self.autosubmit.set_state(False)
            dialogs.confirm(
                    self, "Let Sushi submit on its own?",
                    lambda: (storage.set_setting("autosubmit", "1"),
                             self.autosubmit.set_state(True)),
                    subject="You won't see the form first",
                    body="Sushi fills and sends in one go. A submitted "
                         "application cannot be taken back.",
                    confirm="Turn it on", danger=True)
            return
        storage.set_setting("autosubmit", "0")

    # --- what it has learned --------------------------------------------------

    # --- where to look ------------------------------------------------------

    def _where_section(self) -> QWidget:
        """The one answer that decides which half of the world you are shown.

        First in the list, and the first thing Settings opens on, because it
        is the setting people come here to change: it used to live only on
        the Profile page, behind a tab, under the resume — and it is not a
        fact about you, it is an instruction to the app.
        """
        c = card("Card", spacing=theme.GAP_MD)
        c.layout().addWidget(h2("Where to look"))
        intro = muted(
            "The only thing that decides which countries your feed comes "
            "from. Sushi does not guess this from your CV — that says where "
            "you live, which is often the thing you are trying to change.")
        # Top, not centred: the box is sized for a narrower width, and
        # centred text sat under a gap that read as a missing line.
        intro.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        c.layout().addWidget(intro)
        c.layout().addWidget(divider())

        self.where_picker = LocationPicker()
        self.where_picker.changed.connect(self._save_where)
        c.layout().addWidget(self.where_picker)
        return self._wrap(c)

    def _load_where(self) -> None:
        p = self.app.profile
        if p is None:
            return
        self.where_picker.blockSignals(True)
        self.where_picker.set_values(p.target_locations)
        self.where_picker.set_workplace(getattr(p, "workplace", "any") or "any")
        self.where_picker.blockSignals(False)

    def _save_where(self) -> None:
        """Write the answer through, which starts the search again behind it.

        ``set_profile`` notices this particular answer moved and does two
        things: re-scores every row on file — about fifteen seconds — so the
        feed matches by the time the user navigates back to it, and puts the
        whole corpus back in the refresh queue so the boards are read again
        under the new answer rather than carried on from wherever the last
        sweep stopped. See ``MainWindow.restart_sweep``.
        """
        p = self.app.profile
        if p is None:
            return
        if self.where_picker.is_set():
            storage.set_setting(LOCATIONS_CHOSEN, "1")
        p.target_locations = self.where_picker.values()
        p.workplace = self.where_picker.workplace()
        self.app.set_profile(p)

    def _learning_section(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        c = card("Card", spacing=theme.GAP_MD)
        c.layout().addWidget(h2("What Sushi has learned"))
        c.layout().addWidget(muted(
            "Sushi keeps what it sees in each form, so the next one starts "
            "further along. Your corrections always win."))
        self.learn_count = faint("")
        c.layout().addWidget(self.learn_count)
        c.layout().addWidget(divider())

        row, self.learn_demo = switch_row(
            "Remember demographic answers",
            "Gender, ethnicity, disability and veteran status. Off by default: "
            "a durable local record of your protected characteristics should "
            "be something you ask for.",
            on_toggle=lambda on: storage.set_setting(
                "learn_demographics", "1" if on else "0"))
        c.layout().addWidget(row)
        v.addWidget(c)
        v.addWidget(self._matcher_card())
        v.addWidget(self._knowledge_card())
        v.addWidget(self._add_answer_card())

        head = QHBoxLayout()
        head.addWidget(h3("Answers in the bank"))
        head.addStretch()
        head.addWidget(button("Refresh", "Chip", self._refresh_learning,
                              icon="refresh"))
        head.addWidget(button("Forget all", "Danger", self._forget_all, icon="trash"))
        v.addLayout(head)

        self.learn_host = QWidget()
        self.learn_lay = QVBoxLayout(self.learn_host)
        self.learn_lay.setContentsMargins(0, 0, 0, 0)
        self.learn_lay.setSpacing(8)
        v.addWidget(self.learn_host)
        v.addStretch(1)
        return self._wrap_plain(w)

    def _matcher_card(self) -> QWidget:
        """The optional local model that matches questions by meaning.

        Offered rather than downloaded. It is 24MB off the internet onto
        somebody's disk, and the app works without it — less well, in a way
        worth describing honestly here rather than discovering later.
        """
        from ... import embed

        c = card("Card")
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(IconTile("search", 40, theme.TINT_BLUE, theme.TINT_BLUE_INK),
                      0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(text_col(
            h3("Match questions by meaning"),
            muted("Without this, Sushi only matches a question it has seen "
                  "before if the words line up. “Why are you interested in "
                  "this role?” and “Why do you want to work here?” are the "
                  "same question and share no words at all — so the answer "
                  "you already wrote never reaches the one being composed.")), 1)
        self.matcher_btn = button("Download it", "Primary", self._get_matcher)
        row.addWidget(self.matcher_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        c.layout().addLayout(row)
        self.matcher_lbl = faint(embed.describe())
        c.layout().addWidget(self.matcher_lbl)
        return c

    def _get_matcher(self) -> None:
        from ... import embed

        if embed.installed():
            self.app.set_status("The matcher is already here.")
            return
        self.matcher_btn.setEnabled(False)
        self.matcher_lbl.setText("Downloading…")

        def done(ok: bool) -> None:
            self.matcher_btn.setEnabled(not ok)
            self.matcher_lbl.setText(
                embed.describe() if ok else
                "That download did not finish. Check the connection and try "
                "again — nothing else is affected.")

        self.app.run(embed.fetch, on_done=done, on_failed=lambda _m: done(False))

    def _knowledge_card(self) -> QWidget:
        """The folder of things the user wrote before Sushi existed.

        Chosen during onboarding, but it is the kind of folder that gains a
        file every few weeks — so re-reading it has to live somewhere
        permanent, and a re-read only costs requests for what has changed.
        """
        c = card("Card")
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(IconTile("book", 40, theme.TINT_BLUE, theme.TINT_BLUE_INK),
                      0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(h3("Your knowledge folder"), 1, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(button("Change", "Chip", self._pick_knowledge), 0,
                      Qt.AlignmentFlag.AlignVCenter)
        self.know_btn = button("Read again", "Primary", self._read_knowledge)
        row.addWidget(self.know_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        c.layout().addLayout(row)
        # Full width under the row: a path beside two buttons gets clipped.
        self.know_lbl = muted("")
        c.layout().addWidget(self.know_lbl)
        c.layout().addWidget(faint(
            "Old cover letters and saved answers. Unchanged documents are "
            "skipped, so this is cheap to run after you add one."))
        self.know_note = faint("")
        self.know_note.setVisible(False)
        c.layout().addWidget(self.know_note)
        return c

    def _pick_knowledge(self) -> None:
        start = knowledge.folder() or Path.home()
        path = QFileDialog.getExistingDirectory(
            self, "Where are your old cover letters and answers?", str(start))
        if path:
            knowledge.set_folder(path)
            self._load_learning()

    def _read_knowledge(self) -> None:
        if knowledge.folder() is None:
            self._pick_knowledge()
            return
        self.know_btn.setEnabled(False)
        self.know_note.setVisible(True)
        self.know_note.setText("Reading…")

        def done(result) -> None:
            self.know_btn.setEnabled(True)
            self.know_note.setText(result.summary)
            self._load_learning()

        def failed(_msg: str) -> None:
            self.know_btn.setEnabled(True)
            self.know_note.setText("That didn't finish. Try again.")

        self.app.run(knowledge.ingest, on_done=done, on_failed=failed)

    def _add_answer_card(self) -> QWidget:
        """Type something in before a form ever asks for it.

        Until now the bank could only be filled by applying to something, by
        reading the knowledge folder, or by answering a question a form had
        already asked. Everything the user knows about themselves on a Tuesday
        — a new notice period, a number they have decided on — had nowhere to
        go. It does now, and it goes in as ``human``, so it outranks every
        guess the agent has made about the same question.
        """
        c = card("Card")
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(IconTile("plus", 40, theme.TINT_SAND, theme.TINT_SAND_INK),
                      0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(text_col(
            h3("Add something about yourself"),
            muted("Word the question the way a form would label the field: "
                  "“Notice period”, “Salary expectation”, "
                  "“LinkedIn URL”.")), 1)
        c.layout().addLayout(row)

        self.new_q = QLineEdit()
        self.new_q.setPlaceholderText("Notice period")
        c.layout().addWidget(self.new_q)

        self.new_a = QPlainTextEdit()
        self.new_a.setPlaceholderText("Six weeks")
        self.new_a.setFixedHeight(74)
        c.layout().addWidget(self.new_a)

        foot = QHBoxLayout()
        self.new_note = faint("")
        foot.addWidget(self.new_note, 1)
        foot.addWidget(button("Save to my answers", "Primary", self._add_answer))
        c.layout().addLayout(foot)
        return c

    def _add_answer(self) -> None:
        question = self.new_q.text().strip()
        answer = self.new_a.toPlainText().strip()
        if not question or not answer:
            self.new_note.setText("Both the question and the answer, please.")
            return
        if not learning.learnable(
                question, answer,
                include_demographics=storage.get_setting(
                    "learn_demographics", "0") == "1"):
            # Same refusal as everywhere else, and said plainly: a row that
            # looked saved and was not would be worse than a no.
            self.new_note.setText(
                "Not stored. Sushi keeps passwords and demographic answers "
                "out of the bank. Answer that one in the form itself.")
            return
        learning.remember(question, answer, source="human")
        self.new_q.clear()
        self.new_a.clear()
        self.new_note.setText(f"Saved. “{question}” is answered from now on.")
        self._load_learning()
        self.app.set_status(f"{question} saved to your answers.")

    def _refresh_learning(self) -> None:
        """Re-read the bank, and say so.

        Re-rendering identical content is the correct result for a refresh
        and indistinguishable from a broken button. The count goes to the
        status bar so pressing it always produces a visible answer.
        """
        self._load_learning()
        n = len(learning.bank())
        self.app.set_status(
            f"Answer bank reloaded · {n} answer(s) known." if n
            else "Answer bank reloaded, nothing learned yet.")

    def _load_learning(self) -> None:
        from ... import embed

        if hasattr(self, "matcher_lbl"):
            self.matcher_lbl.setText(embed.describe())
            self.matcher_btn.setEnabled(not embed.installed())
        self.know_lbl.setText(knowledge.status())
        self.know_btn.setText(
            "Read again" if knowledge.read_count() else "Read them")
        self.learn_demo.set_state(storage.get_setting("learn_demographics", "0") == "1")
        entries = learning.bank()
        human = sum(1 for a in entries if a.source == "human")
        self.learn_count.setText(
            f"{len(entries)} answer(s) known: {human} confirmed by you, "
            f"{len(entries) - human} filled by the agent and not yet corrected."
            if entries else
            "Nothing learned yet. Apply to something and this fills itself.")

        while self.learn_lay.count():
            item = self.learn_lay.takeAt(0)
            if item and item.widget():
                widget = item.widget()
                widget.setParent(None)
                widget.deleteLater()
        # Capped: this is a review surface, not a database browser, and a
        # thousand rows of QFrame would make the page crawl.
        for a in entries[:60]:
            self.learn_lay.addWidget(self._answer_row(a))

    def _answer_row(self, a) -> QWidget:
        c = card("Card", margins=(18, 14, 18, 14), spacing=4)
        top = QHBoxLayout()
        top.setSpacing(12)
        confirmed = a.source == "human"
        top.addWidget(IconTile("check" if confirmed else "wand", 24,
                               theme.ACCENT_SOFT if confirmed else theme.SURFACE_2,
                               theme.ACCENT if confirmed else theme.MUTED))
        q = label(a.question, "H3")
        top.addWidget(q, 1)
        top.addWidget(button("Forget", "Link", lambda key=a.key or a.question: self._forget(key)))
        c.layout().addLayout(top)
        body = muted(a.answer if len(a.answer) <= 320 else a.answer[:319] + "…")
        c.layout().addWidget(body)
        return c

    def _forget(self, question: str) -> None:
        learning.forget(question)
        self._load_learning()

    def _forget_all(self) -> None:
        dialogs.confirm(
                self, "Forget every learned answer?", self._forget_all_now,
                subject=f"{len(learning.bank())} answer(s) go",
                body="Sushi starts from nothing and relearns as you apply. "
                     "Your profile, applications and settings are untouched.",
                confirm="Forget them", danger=True)

    def _forget_all_now(self) -> None:
        for a in learning.bank():
            learning.forget(a.key or a.question)
        self._load_learning()
        self.app.set_status("Answer bank cleared.")

    # --- files ----------------------------------------------------------------

    def _files_section(self) -> QWidget:
        c = card("Card")
        c.layout().addWidget(h2("Files"))
        c.layout().addWidget(muted(
            "Cover letters, tailored resumes and exports are written as ordinary "
            "files in a folder you own, not into the app's database."))
        row = QHBoxLayout()
        row.setSpacing(18)
        row.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(IconTile("folder", 40, theme.TINT_SAND, theme.TINT_SAND_INK),
                      0, Qt.AlignmentFlag.AlignTop)
        self.files_lbl = muted("")
        self.files_lbl.setWordWrap(False)
        row.addWidget(text_col(h3("Workspace folder"), self.files_lbl), 1)
        row.addWidget(button("Change", "Chip", self._pick_workspace))
        row.addWidget(button("Open", "Chip", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(ensure_workspace())))))
        c.layout().addLayout(row)

        c.layout().addWidget(divider())
        c.layout().addWidget(h3("The Fill button in your own Chrome"))
        c.layout().addWidget(muted(
            "Sushi's browser already carries the button. This puts the same "
            "one on job pages you read in your everyday Chrome: press it and "
            "the job comes back here to be filled in, exactly as if you had "
            "pasted the link into Manual apply."))
        self.ext_lbl = faint("")
        c.layout().addWidget(self.ext_lbl)
        erow = QHBoxLayout()
        erow.setSpacing(8)
        erow.addWidget(button("Generate the extension", "Chip",
                              self._build_extension, icon="download"))
        self.ext_open_btn = button("Open its folder", "Chip",
                                   self._open_extension, icon="folder")
        erow.addWidget(self.ext_open_btn)
        erow.addWidget(button("Issue a new token", "Chip", self._new_token,
                              icon="refresh"))
        erow.addStretch()
        c.layout().addLayout(erow)

        c.layout().addWidget(divider())
        c.layout().addWidget(faint(
            f"The app's own data, the job database and the browser profile, "
            f"stays in {DATA_DIR}."))
        return self._wrap(c)

    # --- the Chrome extension ----------------------------------------------

    def _extension_dir(self):
        return DATA_DIR / "extension"

    def _build_extension(self) -> None:
        """Write the extension out, token and all, and say what to do next.

        Generated rather than shipped: it carries this machine's token, and
        the button inside it is copied from the same file the app's own
        browser injects, so the two cannot drift.
        """
        from ... import bridge

        try:
            out = bridge.build_extension()
            running = bridge.start(
                lambda url, title: self.app.fill_requested.emit(url, title))
        except Exception as exc:
            self.app.error(f"Could not write the extension: {exc}")
            return
        self._load_files()
        if not running:
            self.app.set_status(
                "Extension written, but no free port to listen on. Close "
                "whatever is using 8765 and press Generate again.")
            return
        dialogs.tell(
            self, "Load it into Chrome",
            body=("1.  Open  chrome://extensions\n"
                  "2.  Turn on  Developer mode  (top right)\n"
                  "3.  Load unpacked, and choose the folder that just "
                  "opened\n\nThen open any job page: the 🍣 button sits in "
                  "the bottom right. Sushi has to be running for it to "
                  "work — it only listens while this window is open."),
            subject="The extension is ready")
        self._open_extension()

    def _open_extension(self) -> None:
        out = self._extension_dir()
        if not out.is_dir():
            self.app.set_status("Press Generate the extension first.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))

    def _new_token(self) -> None:
        """Invalidate the old token. The extension has to be regenerated."""
        from ... import bridge

        dialogs.confirm(
                self, "Issue a new token?", self._new_token_now,
                body=("The extension stops working until you generate it "
                      "again and reload it in Chrome. Worth doing if the "
                      "folder has been copied somewhere it should not have "
                      "been."),
                confirm="Issue a new one")

    def _new_token_now(self) -> None:
        from ... import bridge
        bridge.new_token()
        if self._extension_dir().is_dir():
            bridge.build_extension()
            self.app.set_status(
                "New token issued and the extension rewritten. Reload it in "
                "chrome://extensions for the change to take.")
        else:
            self.app.set_status("New token issued.")
        self._load_files()

    def _load_files(self) -> None:
        self.files_lbl.setText(str(workspace()))

        from ... import bridge
        out = self._extension_dir()
        if not out.is_dir():
            self.ext_lbl.setText("Not generated yet.")
        elif bridge.running():
            self.ext_lbl.setText(
                f"Generated in {out}. Sushi is listening on 127.0.0.1:"
                f"{bridge.port()}.")
        else:
            self.ext_lbl.setText(
                f"Generated in {out}, but Sushi is not listening — press "
                f"Generate the extension to start again.")
        self.ext_open_btn.setEnabled(out.is_dir())

    def _pick_workspace(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Where should Sushi keep your documents?", str(workspace()))
        if path:
            set_workspace(path)
            self._load_files()
            self.app.set_status(f"Documents will be written to {path}.")

    # --- model --------------------------------------------------------------

    def _model_section(self) -> QWidget:
        """Which model or coding agent, how hard it thinks, and the rest.

        The picker is the one onboarding uses — every option, the setup
        guide for the command-line agents, the model box editable — so
        there is one way to choose and it looks the same in both places.
        """
        from ..modelpicker import ModelPicker
        host = QWidget()
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(theme.GAP_MD)

        pick = card("Card")
        pick.layout().addWidget(h2("Model or coding agent"))
        self.model_picker = ModelPicker(compact=True)
        self.model_picker.changed.connect(self._load_model)
        pick.layout().addWidget(self.model_picker)
        col.addWidget(pick)

        tune = card("Card")
        tune.layout().addWidget(h2("How it thinks"))
        self.effort_seg = Segmented(["Auto", "Low", "Medium", "High", "Max"])
        self.effort_seg.changed.connect(self._set_effort)
        row = Block(spacing=5)
        row.add(label("Reasoning effort", "Eyebrow"), self.effort_seg)
        tune.layout().addWidget(row)
        self.effort_help = faint("")
        tune.layout().addWidget(self.effort_help)

        self.fast_box = QComboBox()
        self.fast_box.setEditable(True)
        self.fast_box.setMinimumWidth(280)
        self.fast_box.activated.connect(lambda _i: self._set_fast())
        self.fast_box.lineEdit().editingFinished.connect(self._set_fast)
        row = Block(spacing=5)
        row.add(label("Model for quick answers", "Eyebrow"), self.fast_box)
        tune.layout().addWidget(row)
        tune.layout().addWidget(faint(
            "Short form answers (a yes/no, a notice period) go to this one: "
            "faster and cheaper. Pick “Same as main model” to always use the "
            "main one."))
        col.addWidget(tune)

        c = card("Card")
        c.layout().addWidget(h2("Advanced: set a provider by hand"))
        self.key_status = muted("")
        c.layout().addWidget(self.key_status)
        self.provider_box = QComboBox(); self.provider_box.addItems(["anthropic", "openai", "azure"])
        self.provider_box.currentTextChanged.connect(self._provider_changed)
        self.model_edit = QLineEdit(); self.model_edit.setPlaceholderText("e.g. claude-sonnet-5")
        self.key_edit = QLineEdit(); self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Leave blank to keep the key already found")
        self.openai_base_edit = QLineEdit(); self.openai_base_edit.setPlaceholderText("https://…/openai/v1")
        self.azure_url_edit = QLineEdit(); self.azure_url_edit.setPlaceholderText("https://….openai.azure.com")
        self.azure_dep_edit = QLineEdit(); self.azure_dep_edit.setPlaceholderText("Your deployment name")

        # Every field used to be a bare box with only a placeholder, so once
        # it had a value there was nothing on screen saying what it was.
        self._model_rows = {}
        for key, name, wdg in (
            ("provider", "Provider", self.provider_box),
            ("model", "Model or deployment", self.model_edit),
            ("key", "API key", self.key_edit),
            ("openai_base", "Base URL", self.openai_base_edit),
            ("azure_url", "Azure endpoint", self.azure_url_edit),
            ("azure_dep", "Azure deployment", self.azure_dep_edit),
        ):
            row = Block(spacing=5)
            row.add(label(name, "Eyebrow"), wdg)
            self._model_rows[key] = row
            c.layout().addWidget(row)

        save = button("Save model settings", "Primary", self._save_model)
        save.setMinimumHeight(40)
        c.layout().addWidget(save, 0, Qt.AlignmentFlag.AlignLeft)
        c.layout().addWidget(faint("Tip: put your key in a .env_amm file and Sushi reads it "
                                   "automatically, and no key is stored in the app."))
        col.addWidget(c)
        return self._wrap(host)

    _EFFORT_HELP = {
        "claude_code": "Passed to Claude Code as --effort.",
        "codex": "Passed to Codex as its reasoning effort (Max is sent as High).",
        "opencode": "OpenCode chooses this per model; set it inside OpenCode.",
    }

    def _set_effort(self, value: str) -> None:
        storage.set_setting("reasoning_effort", "" if value == "Auto" else value.lower())
        self._load_model()

    def _set_fast(self) -> None:
        text = self.fast_box.currentText().strip()
        storage.set_setting("fast_model", "same" if text.startswith("Same as") else text)

    @staticmethod
    def _fill(edit: QLineEdit, value: str) -> None:
        """Write a saved value into a box the user is not typing in.

        Same reason as the section above: ``on_show`` runs again every time
        the scanner reports in, so an unconditional ``setText`` rewrote the
        model name from disk between one keystroke and the next.
        """
        if not edit.hasFocus() and edit.text() != value:
            edit.setText(value)

    def _load_model(self) -> None:
        s = load_settings()
        if hasattr(self, "effort_seg"):
            effort = storage.get_setting("reasoning_effort", "") or "auto"
            self.effort_seg.set_value(effort.capitalize())
            self.effort_help.setText(self._EFFORT_HELP.get(
                s.provider, "Used where the provider supports it; ignored otherwise."))
            if not self.fast_box.hasFocus():
                from ...llm import LLM
                from ..modelpicker import providers as _p
                default = LLM.FAST.get(s.provider, "")
                rt = _p.runtime_module({"claude_code": "claude_code", "codex": "codex",
                                        "opencode": "opencode"}.get(s.provider, ""))
                options = ["Same as main model"] + ([default] if default else []) + (
                    [m for m in rt.models() if m != default] if rt is not None else [])
                self.fast_box.blockSignals(True)
                self.fast_box.clear()
                self.fast_box.addItems(list(dict.fromkeys(options)))
                saved = storage.get_setting("fast_model", "")
                self.fast_box.setCurrentText("Same as main model" if saved == "same"
                                             else saved or default or "Same as main model")
                self.fast_box.blockSignals(False)
        if not self.provider_box.hasFocus():
            self.provider_box.setCurrentText(s.provider)
        self._fill(self.model_edit, s.model)
        self._fill(self.openai_base_edit, s.openai_base_url)
        self._fill(self.azure_url_edit, s.azure_base_url)
        self._fill(self.azure_dep_edit, s.azure_deployment)
        if s.llm_enabled:
            src = s.key_source or "configured"
            self.key_status.setText(f"✓ Model ready · {s.provider}/{s.model}. Key from: {src}")
        else:
            self.key_status.setText("⚠ No model set up yet. Pick one below.")
        self._provider_changed(s.provider)

    def _provider_changed(self, provider: str) -> None:
        # Hide the whole labelled row, not just the input — a stranded
        # "Azure endpoint" caption over nothing is worse than either.
        self._model_rows["azure_url"].setVisible(provider == "azure")
        self._model_rows["azure_dep"].setVisible(provider == "azure")
        self._model_rows["openai_base"].setVisible(provider == "openai")

    def _save_model(self) -> None:
        storage.set_setting("provider", self.provider_box.currentText())
        storage.set_setting("model", self.model_edit.text().strip())
        if key := self.key_edit.text().strip():
            storage.set_setting("api_key", key)
        storage.set_setting("openai_base_url", self.openai_base_edit.text().strip())
        storage.set_setting("azure_base_url", self.azure_url_edit.text().strip())
        storage.set_setting("azure_deployment", self.azure_dep_edit.text().strip())
        self.key_edit.clear()
        self._load_model()
        self.app.set_status("Model settings saved.")

    # --- companies ----------------------------------------------------------

    def _corpus_note(self) -> str:
        left = pending_probes()
        return (f"{len(boards()):,} companies with live boards available."
                + (f" {left:,} more to check for one." if left else ""))

    def _companies_section(self) -> QWidget:
        c = card("Card")
        c.layout().addWidget(h2("Companies"))
        self.corpus_lbl = muted("")
        c.layout().addWidget(self.corpus_lbl)
        # Said accurately. The old line — "Sushi ships with remote-first
        # companies" — read as a product decision the user could not undo,
        # when what it describes is where the *company list* was seeded
        # from. Sushi then reads each company's whole careers page, which
        # is where the on-site and hybrid roles come from.
        c.layout().addWidget(muted(
            "The company list was seeded from firms that advertise remote "
            "roles, because that is a list that can be built. Sushi then "
            "reads each one's whole careers page, so on-site and hybrid "
            "vacancies at those companies are in here too. Which kind you "
            "are shown first is yours to set: Profile → Where are you "
            "willing to work."))
        c.layout().addWidget(faint(
            "Thirteen applicant tracking systems are read directly: "
            "Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, "
            "Personio, Teamtailor, BambooHR, Breezy, Pinpoint, Recruitee and "
            "Rippling. A refresh adds every employer the job feeds name, then "
            "checks a few hundred of the companies that have no board yet, "
            "guessing their board from the company name and reading their "
            "own careers page when that misses. It is worth pressing more "
            "than once."))
        c.layout().addWidget(button("Refresh company list", "Primary", self._refresh, icon="refresh"))

        c.layout().addWidget(divider())
        c.layout().addWidget(h3("Jobs on file"))
        self.jobsdb_lbl = muted("")
        c.layout().addWidget(self.jobsdb_lbl)
        c.layout().addWidget(muted(
            "Sushi keeps every vacancy it can see in a local database and "
            "refreshes it daily: boards with openings every day, the rest "
            "weekly. That is why your matches are there the moment you open "
            "the app, and why changing your target roles is instant instead "
            "of another sweep. It is a cache: deleting it loses nothing but "
            "the time to read it again."))
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(button("Refresh now", "Chip", self._refresh_jobs,
                             icon="refresh"))
        row.addWidget(button("Delete the job database", "Chip",
                             self._forget_jobs, icon="trash"))
        row.addStretch()
        c.layout().addLayout(row)
        return self._wrap(c)

    def _jobs_note(self) -> str:
        from ... import jobsdb
        from ...discovery import corpus_size

        try:
            st = jobsdb.stats(boards_total=corpus_size())
        except Exception as exc:
            return f"The job database could not be read: {exc}"
        if not st.jobs:
            return ("Nothing on file yet. The first refresh reads every "
                    "careers page Sushi knows, which takes about forty minutes. "
                    "After that it only reads what has changed.")
        size = 0
        try:
            size = jobsdb.JOBS_DB_PATH.stat().st_size / 1e6
        except OSError:
            pass
        when = ("not yet" if not st.last_refresh else
                time.strftime("%d %b %H:%M", time.localtime(st.last_refresh)))
        return (f"{st.jobs:,} jobs from {st.companies:,} companies · "
                f"{st.boards_read:,} of {st.boards_total:,} careers pages read "
                f"· {size:.0f} MB · last complete refresh {when}")

    def _refresh_jobs(self) -> None:
        self.app.start_scan()
        self.jobsdb_lbl.setText(self._jobs_note())

    def _forget_jobs(self) -> None:
        from ... import jobsdb

        jobsdb.forget_all()
        self.jobsdb_lbl.setText(self._jobs_note())
        self.app.set_status(
            "Job database deleted. The next refresh rebuilds it.")

    def _refresh(self) -> None:
        self.app.busy(True, "Refreshing companies…")

        def job(on_progress=None):
            added = refresh_companies(on_progress=on_progress)
            found = resolve_boards(on_progress=on_progress)
            return added, found, pending_probes()

        def done(res):
            added, found, left = res
            self.app.busy(
                False,
                f"Added {added} companies, {found} new boards."
                + (f" {left:,} still to check. Press again to carry on."
                   if left else ""))
            self.corpus_lbl.setText(self._corpus_note())

        self.app.run(job, on_done=done)

    # --- account ------------------------------------------------------------

    def _account_section(self) -> QWidget:
        c = card("Card")
        c.layout().addWidget(h2("Account"))
        c.layout().addWidget(muted(f"Your data lives on this machine at:\n{DATA_DIR}"))
        c.layout().addWidget(muted("Profile, applications and settings are stored in a local SQLite "
                                   "database. Nothing is uploaded except to your model provider."))
        c.layout().addWidget(divider())
        c.layout().addWidget(h3("Diagnostics"))
        c.layout().addWidget(muted(
            "If Sushi ever closes on you or something goes wrong, the log says "
            "what it was doing at the time. It records activity and errors, "
            "never your API key."))
        log_row = QHBoxLayout()
        log_row.addWidget(button("Open log folder", "Chip", self._open_log, icon="folder"))
        log_row.addWidget(button("Copy log path", "Chip", self._copy_log_path))
        log_row.addStretch()
        c.layout().addLayout(log_row)

        c.layout().addWidget(divider())
        c.layout().addWidget(h3("Setup"))
        c.layout().addWidget(muted("Walk through the six first-run steps again. Nothing is reset: "
                                   "the flow shows what you already have and lets you change it."))
        row = QHBoxLayout()
        row.addWidget(button("Restart onboarding", "Chip", self.app.restart_onboarding))
        row.addStretch()
        c.layout().addLayout(row)
        return self._wrap(c)

    def _open_log(self) -> None:
        from ... import diagnostics
        target = diagnostics.LOG_FILE
        if target is None:
            self.app.error("Logging is not running in this session.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))

    def _copy_log_path(self) -> None:
        from PyQt6.QtWidgets import QApplication

        from ... import diagnostics
        if diagnostics.LOG_FILE is None:
            self.app.error("Logging is not running in this session.")
            return
        QApplication.clipboard().setText(str(diagnostics.LOG_FILE))
        self.app.set_status(f"Copied {diagnostics.LOG_FILE}")

    @staticmethod
    def _wrap(inner: QWidget) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(theme.PAGE_PAD, theme.PAGE_PAD,
                             theme.PAGE_PAD, theme.PAGE_PAD)
        v.addWidget(inner); v.addStretch()
        return w

    @staticmethod
    def _wrap_plain(inner: QWidget) -> QWidget:
        """Pad a section that is a list of cards rather than one card."""
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(theme.PAGE_PAD, theme.PAGE_PAD,
                             theme.PAGE_PAD, theme.PAGE_PAD)
        v.addWidget(inner)
        return w
