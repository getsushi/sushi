"""Manual apply — paste a link, Sushi fills the form in a real browser.

Discovery runs on its own now, so this page is the other half: the job you
found yourself, on a board Sushi has never heard of, that you want filled in
anyway. Paste the URL and it reads the posting, scores it against your
profile, and opens Chrome to fill the form — stopping before Submit unless
you have explicitly allowed otherwise.

The browser is deliberately *visible* here. On the automatic path a hidden
browser is the point; on this one you chose this job specifically, so watching
it filled is reassuring rather than noise. Everything it types is learned, so
a manual application also teaches the answer bank.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QHBoxLayout, QLineEdit, QPlainTextEdit,
                             QVBoxLayout, QWidget)

from ... import storage
from ...config import load_settings
from ...extras import role_from_url
from ...models import Role
from .. import theme
from ..components import (IconTile, SearchField, button, card, faint,
                          h2, h3, label, muted, page_title, text_col)
from .base import Page, scroll_page

log = logging.getLogger(__name__)


class ManualPage(Page):
    title = "Manual apply"
    assistant_key = "browse"

    def build(self) -> None:
        host, lay, scroll = scroll_page()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        lay.addWidget(page_title(
            "Manual apply", over="ONE JOB AT A TIME",
            body="Paste a link to any job and Sushi fills the form for you."))

        # --- the link ---------------------------------------------------------
        c = card("Card")
        row = QHBoxLayout()
        row.setSpacing(18)
        row.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(IconTile("external-link", 40, theme.TINT_BLUE,
                               theme.TINT_BLUE_INK), 0, Qt.AlignmentFlag.AlignTop)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://… any job posting URL")
        self.url_edit.returnPressed.connect(lambda: self._read())
        self.url_edit.textChanged.connect(self._url_changed)
        row.addWidget(text_col(
            h3("Job link"),
            faint("Greenhouse, Lever, Ashby, Workable and SmartRecruiters are "
                  "read directly. Anything else still works.")), 1)
        c.layout().addLayout(row)
        c.layout().addWidget(self.url_edit)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.read_btn = button("Read the job", "Chip", lambda: self._read(),
                               icon="search")
        actions.addWidget(self.read_btn)
        # Explorative sits next to Fill, not only in Settings, because it
        # changes what this button is about to do to a form you are watching.
        # It writes the same stored setting, so turning it on here turns it on
        # for Insane mode too — one switch, two places to reach it.
        self.explore_btn = button("Explorative", "Chip", self._toggle_explore,
                                  icon="sparkles")
        actions.addWidget(self.explore_btn)
        actions.addStretch()
        self.fill_btn = button("Fill it in Chrome", "Primary", self._fill,
                               icon="arrow-right", icon_size=15)
        self.fill_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        actions.addWidget(self.fill_btn)
        c.layout().addLayout(actions)
        lay.addWidget(c)

        # --- what it found ----------------------------------------------------
        # One line, not the whole posting. The description panel was a dump
        # of whatever the page's text happened to be — "Skip to main
        # content", the nav, the cookie bar — occupying the best part of the
        # screen above the only thing on this page you actually act on. You
        # came here with the job already chosen; you do not need it read
        # back to you.
        self.summary = muted("Nothing read yet. Paste a link above.")
        lay.addWidget(self.summary)

        # Shown only when the answer to "why did it not try?" is "because
        # the switch is off" — with the switch, rather than a sentence
        # telling you where to go and find it.
        offer = QHBoxLayout()
        offer.setSpacing(8)
        self.offer_lbl = muted(
            "Explorative is off, so Sushi only filled what it could look up.")
        self.offer_lbl.setWordWrap(True)
        offer.addWidget(self.offer_lbl, 1)
        self.offer_btn = button("Turn on Explorative and fill again", "Primary",
                                self._explore_and_refill, icon="sparkles")
        offer.addWidget(self.offer_btn)
        self.offer_row = QWidget()
        self.offer_row.setLayout(offer)
        self.offer_row.setVisible(False)
        lay.addWidget(self.offer_row)

        from ..answers import GUESS_COPY, AnswerPanel
        # Guesses first. They are the ones already in the form and about to
        # be sent; the questions nobody answered are still blank and can
        # wait their turn below.
        self.guesses = AnswerPanel(GUESS_COPY, style="CardGuess")
        self.guesses.saved.connect(self._guesses_confirmed)
        lay.addWidget(self.guesses)

        self.answers = AnswerPanel()
        self.answers.saved.connect(self._answers_saved)
        lay.addWidget(self.answers)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.open_btn = button("Open the job page", "Chip", self._open,
                               icon="external-link")
        foot.addWidget(self.open_btn)
        # The other way round: instead of reading it here and pressing Fill,
        # read it in Sushi's own browser and press the button on the page
        # when you have decided. Same browser profile, so the sign-ins carry.
        self.browse_btn = button("Read it in Sushi's browser", "Chip",
                                 self._browse, icon="compass")
        self.browse_btn.setToolTip(
            "Opens the page with Sushi's Fill button on it, so you can read "
            "the job first and fill the form without coming back here.")
        foot.addWidget(self.browse_btn)
        # Opens, rather than writes. Sushi writes one on its own when the
        # form asks for one; pressing this every time produced a letter for
        # applications that never wanted one.
        self.letter_btn = button("Open cover letter", "Chip", self._letter,
                                 icon="file-text")
        foot.addWidget(self.letter_btn)
        foot.addStretch()
        lay.addLayout(foot)

        lay.addStretch(1)

        self._role: Role | None = None
        self._url_changed()
        self._show_explore()

    def on_show(self) -> None:
        # The switch lives in the settings table, so Settings may have moved
        # it since this page was last looked at.
        self._show_explore()

    # --- explorative ---------------------------------------------------------

    def _explorative(self) -> bool:
        from ...extras import load_apply_settings
        return bool(load_apply_settings().explorative)

    def _toggle_explore(self) -> None:
        from ...extras import load_apply_settings, save_apply_settings

        a = load_apply_settings()
        a.explorative = not a.explorative
        save_apply_settings(a)
        self._show_explore()
        self.app.set_status(
            "Explorative on. Sushi will work out the questions it cannot "
            "look up, type them in, and flag every one of them for you."
            if a.explorative else
            "Explorative off. Questions Sushi cannot look up are left blank "
            "for you.")

    def _offer_explorative(self, show: bool) -> None:
        self.offer_row.setVisible(show)

    def _explore_and_refill(self) -> None:
        """Turn it on and run the same job again, in one press."""
        from ...extras import load_apply_settings, save_apply_settings

        a = load_apply_settings()
        a.explorative = True
        save_apply_settings(a)
        self._show_explore()
        self.offer_row.setVisible(False)
        self.app.set_status(
            "Explorative on. Filling again, and flagging anything it works "
            "out rather than looks up.")
        self._fill()

    def _show_explore(self) -> None:
        on = self._explorative()
        self.explore_btn.setObjectName("ChipActive" if on else "Chip")
        self.explore_btn.style().unpolish(self.explore_btn)
        self.explore_btn.style().polish(self.explore_btn)
        self.explore_btn.setToolTip(
            "On: fill the whole form, working out anything that is not on "
            "your CV or in your answer bank, and flag each of those in amber "
            "so you can check them.\n"
            "Off: leave those questions blank and list them for you."
            if on else
            "Off: a question Sushi cannot look up is left blank and listed "
            "for you.\nOn: Sushi guesses it from your CV and flags the "
            "guess.")

    # --- what the link is ----------------------------------------------------

    def _url(self) -> str:
        return self.url_edit.text().strip()

    @staticmethod
    def _plausible(url: str) -> bool:
        return url.lower().startswith(("http://", "https://"))

    def _url_changed(self) -> None:
        """Keep the actions in step with the field.

        Gating every action on a *completed read* was wrong: paste a link,
        press the only obvious button, and nothing happens because the one
        you wanted is greyed out and the one that would un-grey it is at the
        other end of the card. A link is enough to act on — reading it is
        this page's job, not yours.
        """
        url = self._url()
        live = self._plausible(url)
        for b in (self.fill_btn, self.open_btn, self.letter_btn):
            b.setEnabled(live)

        # A new link invalidates the posting shown underneath it.
        if self._role is not None and not self._same(self._role.url, url):
            self._role = None
        if self._role is None:
            self.summary.setText(
                "Press Read the job to see it first, or go straight to "
                "Fill it in Chrome."
                if live else "Nothing read yet. Paste a link above.")

    @staticmethod
    def _same(a: str, b: str) -> bool:
        return (a or "").rstrip("/") == (b or "").rstrip("/")

    # --- reading ------------------------------------------------------------

    def _read(self, then=None) -> None:
        """Fetch the posting. ``then`` runs once it has arrived.

        ``then`` is guarded because this is wired to ``clicked``, which hands
        a slot a ``checked`` bool if it will take one — so a plain button
        press would otherwise arrive here as ``then=False``.
        """
        if not callable(then):
            then = None
        url = self._url()
        # Said on the page rather than in a modal. A mistyped URL is the most
        # ordinary thing that can happen here, and a dialog you have to
        # dismiss before you can fix it is a punishment for a typo.
        if not url:
            self._say("Paste a job link above, then press Read the job.")
            return
        if not self._plausible(url):
            self._say("That does not look like a link. It should start with https://")
            return
        self.read_btn.setEnabled(False)
        self.app.busy(True, "Reading that job…")
        self.summary.setText("Reading that job…")
        log.info("manual: reading %s", url)
        profile = self.app.profile

        def job():
            return role_from_url(url, profile)

        def done(role: Role) -> None:
            self.app.busy(False, "")
            self.read_btn.setEnabled(True)
            self._show(role)
            if then:
                then(role)

        def failed(_msg: str) -> None:
            self.read_btn.setEnabled(True)
            self._say("That page would not load. Check the link, or paste the "
                      "description in yourself and apply from there.")

        self.app.run(job, on_done=done, on_failed=failed)

    def _with_role(self, then) -> None:
        """Run *then* against the posting, reading it first if need be."""
        url = self._url()
        if not url:
            self._say("Paste a job link above first.")
            return
        if not self._plausible(url):
            self._say("That does not look like a link. It should start with https://")
            return
        if self._role is not None and self._same(self._role.url, url):
            then(self._role)
            return
        self._read(then=then)

    def _say(self, text: str) -> None:
        """Report where the answer would have gone."""
        self.app.busy(False, "")
        self.read_btn.setEnabled(True)
        self.summary.setText(text)

    def _show(self, role: Role) -> None:
        self._role = role
        storage.upsert_role(role, status="matched")
        self.app.apps_changed.emit()
        bits = [role.company or "Unknown company", role.title or "Untitled role"]
        if role.location:
            bits.append(role.location)
        if role.ats:
            bits.append(f"via {role.ats.title()}")
        pct = role.match or 0
        self.summary.setText(
            " · ".join(bits) + (f"  ·  {pct}% match" if pct else ""))
        for b in (self.fill_btn, self.open_btn, self.letter_btn):
            b.setEnabled(True)
        self.app.set_status(f"Read {role.title} at {role.company}.")

    # --- acting -------------------------------------------------------------

    def _open(self) -> None:
        """Just open it. This one never needs the posting read first."""
        url = self._role.url if self._role else self._url()
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _browse(self) -> None:
        """Open the link in Sushi's browser, with the Fill button on it.

        No reading first: the point of this route is that you have not
        decided yet. Pressing the button on the page is what starts an
        application, and that lands on ``MainWindow._fill_from_page``.
        """
        url = self._role.url if self._role else self._url()
        if not url:
            self._say("Paste a job link above first.")
            return
        if not self._plausible(url):
            self._say("That does not look like a link. It should start with https://")
            return
        self.browse_btn.setEnabled(False)
        self.app.set_status("Opening it in Sushi's browser…")

        def job():
            from ...autofill import open_for_browsing
            open_for_browsing(url)

        def done(_result) -> None:
            self.browse_btn.setEnabled(True)
            self.app.set_status(
                "Read the job there. The 🍣 button at the bottom right fills "
                "the form when you want it.")

        def failed(msg: str) -> None:
            self.browse_btn.setEnabled(True)
            self._say(f"Could not open the browser. {msg[:120]}")

        self.app.run(job, on_done=done, on_failed=failed)

    def _fill(self) -> None:
        self._with_role(self._launch)

    def _launch(self, role: Role) -> None:
        # The visible browser is the point of this page, so the headless
        # setting is overridden for this one run rather than silently obeyed.
        self.app.set_status(f"Opening Chrome for {role.title} at {role.company}…")
        self.answers.setVisible(False)
        self.guesses.setVisible(False)
        self.offer_row.setVisible(False)
        self.fill_btn.setEnabled(False)
        # begin_apply rather than apply_to_role: this page wants the result
        # back so it can ask you the questions the filler could not answer,
        # instead of reporting them in a dialog you dismiss and forget.
        self.app.begin_apply(role, watch=True, announce=False,
                             on_finished=self._filled)

    def _filled(self, status: str, result) -> None:
        self.fill_btn.setEnabled(True)
        if result is None:
            self._say("That did not get as far as the form. Check the status "
                      "line, and Settings → Model if nothing is configured.")
            return
        self.summary.setText(result.message[:400])
        guessed = list(getattr(result, "guessed", None) or [])
        self.guesses.set_guesses(guessed)
        self.answers.set_questions(list(result.unanswered or []))
        why = getattr(result, "compose_skipped", "")
        if guessed:
            self.app.set_status(
                f"Filled the form. {len(guessed)} answer(s) are Sushi's own "
                f"work. They are outlined in amber in the browser and "
                f"listed below. Check them before you submit.")
        elif result.unanswered and why == "off":
            # The question the screenshot of this screen always raises: why
            # did it hand me back eighteen questions instead of having a go?
            self.app.set_status(
                f"{len(result.unanswered)} question(s) left. Explorative is "
                f"off, so Sushi only filled what it could look up. Turn it "
                f"on and press Fill again to have it work these out.")
        elif result.unanswered and why == "no-model":
            self.app.set_status(
                f"{len(result.unanswered)} question(s) left. Explorative is "
                f"on but no model is configured. Settings → Model.")
        elif result.unanswered and why:
            self.app.set_status(
                f"{len(result.unanswered)} question(s) left, and the model "
                f"would not answer them: {why[:120]}")
        elif result.unanswered:
            self.app.set_status(
                f"Filled what it knew. {len(result.unanswered)} question(s) "
                f"below. Answer them once and they are reused everywhere.")
        self._offer_explorative(bool(result.unanswered) and why == "off")

    def _answers_saved(self, n: int) -> None:
        if not n:
            return
        self.app.set_status(
            f"{n} answer(s) added to your bank. Press Fill it in Chrome again "
            f"to put them into this form.")

    def _guesses_confirmed(self, n: int) -> None:
        """A guess the user has looked at is no longer a guess.

        Saving here is the only route by which anything Explorative mode
        composed reaches the answer bank, and it arrives as a ``human``
        answer — so it outranks every later guess about the same question,
        and the next form that asks it gets this, for nothing.
        """
        if not n:
            return
        self.app.set_status(
            f"{n} answer(s) confirmed and saved to your bank. If you changed "
            f"one here, change it in the form too before you submit. Sushi "
            f"does not retype a form you are working in.")

    def _letter(self) -> None:
        """Open the letter for this role — writing one only if none exists.

        It used to write a fresh letter on every press, for applications
        that had not asked for one. Sushi now writes a letter when the form
        actually has a field for it; this button is for reading or editing
        the one that produced.
        """
        self._with_role(self._open_letter)

    def _open_letter(self, role: Role) -> None:
        from ...letters import existing_letter

        found = existing_letter(role)
        if found:
            self.app.set_status(f"Opening {found.name}.")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(found)))
            return
        self._say("No cover letter for this role yet. Sushi writes one when "
                  "the form asks for it. Writing one now…")
        self._write_letter(role)

    def _write_letter(self, role: Role) -> None:
        p = self.app.profile
        if not p:
            self._say("Build your profile first. Profile → Read my CV.")
            return
        from pathlib import Path

        from ...letters import render_pdf, write_letter
        from ...llm import LLM

        s = load_settings()
        self.app.busy(True, "Writing a cover letter…")

        def job():
            path, text = write_letter(LLM(s), p, role)
            render_pdf(text, path.with_suffix(".pdf"), author=p.full_name)
            return path.with_suffix(".pdf")

        def done(path):
            self.app.busy(False, f"Saved {Path(path).name}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

        self.app.run(job, on_done=done)
