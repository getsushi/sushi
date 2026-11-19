"""Profile — the CV library, resume builder, cover letter and profile details.

The library is the part worth explaining. Sushi used to hold exactly one CV,
in one setting, with one profile read from it — so "attach a second resume"
was not a thing the app could do, and "switch resume" was a replace that left
the previous CV's name, summary and employers on screen under the new file's
name until a model finished reading it. Both of those are the same missing
idea: a CV and the profile read from it belong together.

So each attached CV keeps its own profile. Switching is then a query, not a
request — instant, and never showing you someone else's text — and a CV that
has never been read says so rather than borrowing the last one's.
"""
from __future__ import annotations

import html as _h
from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLineEdit,
                             QPlainTextEdit, QStackedWidget, QTextBrowser,
                             QVBoxLayout, QWidget)

from ... import storage
from ...config import CV_DIR, load_settings
from ...cv import SUPPORTED
from ...discovery import wanted
from ...llm import LLM
from ...models import Profile, Resume
from ...resume import TEMPLATES, render_resume_pdf, resume_html, resume_path
from .. import theme
from ..components import (FlowLayout, Segmented, button, card, divider, faint,
                          h3, icon_button, muted, page_title)
from ..locations import LOCATIONS_CHOSEN, LocationPicker
from .base import Page, scroll_page


class ProfilePage(Page):
    title = "Profile"
    assistant_key = "profile"

    def build(self) -> None:
        host, lay, scroll = scroll_page()
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.addWidget(scroll)

        head = QHBoxLayout()
        head.setSpacing(theme.GAP_SM)
        head.addWidget(page_title("Profile", over="YOU"), 1)
        # A plain button, not a Chip. Chips are H_CHIP — a size down, meant for
        # filters — and set beside an H_CONTROL Primary the pair came out two
        # heights, two corner radii and two text sizes, which reads as a
        # mistake rather than as a hierarchy. Same weight, same size, one of
        # them dark: that is what "secondary next to primary" looks like, and
        # it is what "Download PDF" below already does.
        self.upload_btn = button("Add resume", "", self._pick_cv, icon="plus")
        # One primary per screen. These were a Chip and an Accent side by
        # side, and further down the page a second Accent and a Primary — four
        # buttons in three weights, none of which told you which one the page
        # wanted you to press.
        self.read_btn = button("Read my CV", "Primary", self._analyse)
        head.addWidget(self.upload_btn, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(self.read_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)

        # Every CV on file, the active one lit. A row of chips rather than a
        # dropdown: there are two or three of these, not thirty, and the one
        # thing you want to see without opening anything is which is live.
        self.library_host = QWidget()
        self.library_host.setObjectName("Library")
        self.library_host.setStyleSheet("QWidget#Library{background:transparent;}")
        self.library = FlowLayout(self.library_host, spacing=6)
        lay.addWidget(self.library_host)

        self.cv_lbl = faint("No CV uploaded yet.")
        lay.addWidget(self.cv_lbl)

        # Three views of one profile — a segmented control, because that is
        # what "pick one" looks like. As three loose chips they read as three
        # actions, and the selected one as merely the one you last hovered.
        self.subtabs = Segmented(["Resume", "Cover Letter", "Profile Details"])
        self.subtabs.changed.connect(self._set_sub)
        lay.addWidget(self.subtabs, 0, Qt.AlignmentFlag.AlignLeft)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._resume_view())
        self.stack.addWidget(self._cover_view())
        self.stack.addWidget(self._details_view())
        lay.addWidget(self.stack)
        lay.addStretch(1)
        self._set_sub("Resume")

    # --- resume view --------------------------------------------------------

    def _resume_view(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(muted("Template"))
        self.template_box = QComboBox(); self.template_box.addItems(TEMPLATES)
        self.template_box.currentTextChanged.connect(lambda _: self._render_resume())
        bar.addWidget(self.template_box)
        bar.addStretch()
        self.improve_btn = button("Improve with assistant", "Chip",
                                  lambda: self.app.ask_assistant("Improve my resume"))
        bar.addWidget(self.improve_btn)
        # Not Primary. The page already has one dark button in its header, and
        # two saturated buttons sixty pixels apart — in two different
        # saturated treatments — is the thing that made this screen hard to
        # read: neither of them is "the" action if both look like it.
        self.download_btn = button("Download PDF", "", self._download_resume,
                                   icon="download")
        bar.addWidget(self.download_btn)
        bw = QWidget(); bw.setLayout(bar)
        v.addWidget(bw)

        self.resume_preview = QTextBrowser()
        self.resume_preview.setMinimumHeight(520)
        self.resume_preview.setStyleSheet(
            f"QTextBrowser{{background:white;border:1px solid {theme.LINE};border-radius:12px;padding:26px;}}")
        v.addWidget(self.resume_preview)
        return w

    # --- cover letter view --------------------------------------------------

    def _cover_view(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(h3("Base cover letter"))
        v.addWidget(muted("A reusable base. Sushi tailors a fresh letter per job from your "
                          "profile and the posting; this is the voice it starts from."))
        self.cover_edit = QPlainTextEdit()
        self.cover_edit.setMinimumHeight(360)
        self.cover_edit.setPlaceholderText("Write or generate a base cover letter…")
        v.addWidget(self.cover_edit)
        row = QHBoxLayout()
        row.addWidget(button("Generate from profile", "Chip", self._gen_cover))
        row.addStretch()
        row.addWidget(button("Save", "Primary", self._save_cover))
        rw = QWidget(); rw.setLayout(row)
        v.addWidget(rw)
        return w

    # --- details view -------------------------------------------------------

    def _details_view(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)

        # What application forms asked that Sushi did not know. Answered
        # once here, filled on every form after.
        from ..answers import AnswerPanel
        self.open_q = AnswerPanel((
            "Details forms asked for that Sushi doesn't have",
            "Application forms asked these and Sushi had no answer, so they "
            "were left for you. Answer them once and every future form that "
            "asks fills them in automatically.",
            "Save answers"))
        self.open_q.saved.connect(lambda _n: self._load_open_questions())
        v.addWidget(self.open_q)
        self.guess_q = AnswerPanel((
            "Answers Sushi worked out for you, worth a check",
            "Sushi filled these from your CV. Correct anything that is wrong "
            "and press Save: they become your answers and are used on every "
            "future form.",
            "Save answers"))
        self.guess_q.saved.connect(lambda _n: self._load_open_questions())
        v.addWidget(self.guess_q)

        self.details = QTextBrowser()
        self.details.setMinimumHeight(300)
        self.details.setStyleSheet(
            f"QTextBrowser{{background:{theme.SURFACE};border:1px solid {theme.LINE};border-radius:12px;padding:18px;}}")
        v.addWidget(self.details)

        edit = card("Card")
        edit.layout().addWidget(h3("What roles should Sushi show you?"))
        edit.layout().addWidget(muted("These drive matching. Comma separated."))
        self.targets_edit = QLineEdit(); self.targets_edit.setPlaceholderText("Target job titles")
        edit.layout().addWidget(self.targets_edit)

        # Where you are willing to work. Sushi assumed remote — in the
        # corpus it ships, in the scoring, and in the copy — and there was
        # nowhere to say otherwise. It is a preference, so it is asked: on
        # first run, and here afterwards, with the same control both times.
        edit.layout().addWidget(divider())
        edit.layout().addWidget(h3("Where do you want to work?"))
        self.loc_picker = LocationPicker()
        self.loc_picker.changed.connect(self._save_places)
        edit.layout().addWidget(self.loc_picker)
        self.place_note = faint(
            "This filters the feed. A job tied to a country you did not ask "
            "for is left out, however well the rest of it fits; roles open "
            "worldwide, or to anywhere remote, always count as yours. How "
            "you want to work only moves the list around — use the Workplace "
            "filter on All jobs to hide the rest outright.")
        edit.layout().addWidget(self.place_note)

        edit.layout().addWidget(divider())
        edit.layout().addWidget(h3("Application defaults"))
        edit.layout().addWidget(muted("What Sushi auto-fills on every ATS form."))
        self.workauth_edit = QLineEdit(); self.workauth_edit.setPlaceholderText("Work authorisation, e.g. UK citizen")
        self.sponsor_edit = QLineEdit(); self.sponsor_edit.setPlaceholderText("Needs sponsorship? yes / no")
        self.salary_edit = QLineEdit(); self.salary_edit.setPlaceholderText("Salary expectation (optional)")
        edit.layout().addWidget(self.workauth_edit)
        edit.layout().addWidget(self.sponsor_edit)
        edit.layout().addWidget(self.salary_edit)
        edit.layout().addWidget(button("Save", "Primary", self._save_details))
        v.addWidget(edit)
        return w

    # --- the CV library ------------------------------------------------------

    def _render_library(self) -> None:
        """Repaint the chips. Cheap, and called on every change."""
        while self.library.count():
            item = self.library.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        resumes = storage.list_resumes()
        active = storage.active_resume()
        active_id = active.id if active else 0
        for r in resumes:
            self.library.addWidget(self._resume_chip(r, r.id == active_id))
        self.library_host.setVisible(bool(resumes))
        self.library_host.updateGeometry()
        self.read_btn.setEnabled(active is not None and active.exists())
        self.cv_lbl.setText(self._library_note(active, len(resumes)))

    @staticmethod
    def _library_note(active: Resume | None, total: int) -> str:
        if active is None:
            return ("No CV attached yet. Add one and Sushi reads it, works "
                    "out who you are, and starts looking.")
        if not active.exists():
            return (f"{active.name} is not where it was. Add it again, or "
                    f"pick another.")
        others = (f" {total - 1} other{'s' if total > 2 else ''} on file."
                  if total > 1 else "")
        if not active.read:
            return f"{active.name} has not been read yet.{others}"
        if active.changed():
            return (f"{active.name} has changed since Sushi read it — press "
                    f"Read my CV, or Refresh on All jobs.{others}")
        return f"Applying as {active.name}.{others}"

    def _resume_chip(self, r: Resume, active: bool) -> QWidget:
        w = QWidget()
        w.setObjectName("ResumeChip")
        w.setStyleSheet("QWidget#ResumeChip{background:transparent;}")
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)

        if not r.exists():
            state = " · missing"
        elif not r.read:
            state = " · not read"
        elif r.changed():
            state = " · changed"
        else:
            state = ""
        pick = button(r.name + state, "ChipActive" if active else "Chip",
                      lambda rid=r.id: self._switch_resume(rid),
                      icon="file-text")
        pick.setToolTip(r.path)
        row.addWidget(pick)
        row.addWidget(icon_button(
            "x", f"Take {r.name} out of the library",
            lambda rid=r.id: self._remove_resume(rid), size=24, icon_size=11))
        return w

    def _switch_resume(self, rid: int) -> None:
        """Make this the CV Sushi applies with, and show *its* profile.

        Nothing of the previous one survives on screen. If this CV has been
        read before, its profile comes straight back out of the library and
        the feed is re-scored against it; if it has not, the page says so
        and the read starts — rather than leaving the last CV's summary
        under this one's name, which is the bug this replaced.
        """
        was = storage.active_resume()
        if was is not None and was.id == rid:
            return
        found = storage.set_active_resume(rid)
        if found is None:
            self.app.error("That resume is no longer on file.")
            self._render_library()
            return
        if found.read and found.profile is not None:
            self.app.set_profile(found.profile)
            self._render_all()
            self.app.set_status(
                f"Applying as {found.name}. The feed has been re-scored "
                f"against it.")
            return
        # Not read yet: blank the page before anything else, so there is no
        # moment where the old CV's text sits under the new one's name.
        self._render_all()
        if not found.exists():
            self.app.error(f"{found.name} is not where it was.")
        elif load_settings().llm_enabled:
            self._analyse()
        else:
            self.app.set_status(
                f"{found.name} is ready to read. Add a model in Settings.")

    def _remove_resume(self, rid: int) -> None:
        """Take a CV out of the library. The file itself is untouched.

        No confirmation: nothing is deleted from disk, and attaching it
        again is one button away. What does need saying is when the one you
        removed was the live one, because the profile behind your matches
        changes with it.
        """
        found = storage.resume(rid)
        if found is None:
            return
        was_active = (storage.active_resume() or found).id == rid
        storage.forget_resume(rid)
        now = storage.active_resume()
        if was_active and now is not None and now.read and now.profile is not None:
            self.app.set_profile(now.profile)
            self.app.set_status(
                f"{found.name} removed. Applying as {now.name} now.")
        elif was_active and now is None:
            self.app.set_status(
                f"{found.name} removed. No CV is attached, so nothing can be "
                f"filled in until you add one — your matches still stand.")
        else:
            self.app.set_status(f"{found.name} removed from the library. "
                                f"The file itself is untouched.")
        self._render_all()
        # Keeps the "no CV" wording in the status bar and sidebar honest.
        self.app.refresh_chrome()

    # --- where you are willing to work --------------------------------------

    def _save_places(self) -> None:
        """Keep the location choice the moment it is made.

        Saved on the press rather than on the Save button below it: these are
        chips, and a chip that lights up without taking effect is the kind of
        control this app has been bitten by before.
        """
        if self._loading_places:
            return
        p = self.app.profile
        if not p:
            return
        storage.set_setting(LOCATIONS_CHOSEN, "1")
        p.target_locations = self.loc_picker.values()
        p.workplace = self.loc_picker.workplace()
        self.app.set_profile(p)
        self.app.set_status(
            f"{self.loc_picker.summary.text()} The feed has been re-scored.")

    #: Guards the write-back while the page is pushing a stored profile into
    #: the picker — without it, opening the page saves the page.
    _loading_places = False

    def _load_places(self, p) -> None:
        self._loading_places = True
        try:
            self.loc_picker.set_values(p.target_locations)
            self.loc_picker.set_workplace(wanted(p))
        finally:
            self._loading_places = False

    # --- lifecycle ----------------------------------------------------------

    def on_show(self) -> None:
        self.cover_edit.setPlainText(storage.get_setting("base_cover_letter", ""))
        self._render_all()
        self._load_open_questions()

    def _load_open_questions(self) -> None:
        from ... import learning
        self.open_q.set_questions(learning.open_questions())
        guesses = learning.open_guesses()
        self.guess_q.set_questions(list(guesses), guesses)

    def _shown(self) -> Profile | None:
        """The profile this page is entitled to draw.

        The active CV's own, and nothing else. ``app.profile`` is what the
        matcher is using, which during the seconds between attaching a CV
        and reading it is still the *previous* CV's — drawing that here is
        the whole of the "it shows text from the last one" complaint.
        """
        active = storage.active_resume()
        return active.profile if (active is not None and active.read) else None

    def _render_all(self) -> None:
        self._render_library()
        p = self._shown()
        # Nothing to download or improve until a CV has been read.
        for b in (self.improve_btn, self.download_btn):
            b.setEnabled(bool(p))
        if p:
            self.resume_preview.setHtml(resume_html(p))
            self.details.setHtml(self._details_html(p))
        else:
            self.resume_preview.setHtml(self._blank_html())
            self.details.setHtml(self._blank_html())
        # The editors below the details are live settings rather than a view
        # of the CV, so they follow the profile the matcher is actually using
        # — which is still the previous one until this CV has been read.
        live = self.app.profile
        if live:
            self.targets_edit.setText(", ".join(live.target_titles))
            self._load_places(live)
            self.workauth_edit.setText(live.work_authorisation)
            self.sponsor_edit.setText(live.requires_sponsorship)
            self.salary_edit.setText(live.salary_expectation)

    def _blank_html(self) -> str:
        """What stands where a profile would be. Never the last CV's text."""
        active = storage.active_resume()
        if active is None:
            body = ("Add a resume and Sushi reads it, works out who you are, "
                    "and starts looking.")
        elif not active.exists():
            body = (f"<b>{_h.escape(active.name)}</b> is not where it was. "
                    f"Add the file again, or pick another resume.")
        elif self.app.profile is not None:
            body = (f"<b>{_h.escape(active.name)}</b> has not been read yet. "
                    f"Press <b>Read my CV</b> and this fills in. Until then "
                    f"your matches are scored against the profile you "
                    f"already had.")
        else:
            body = (f"<b>{_h.escape(active.name)}</b> has not been read yet. "
                    f"Press <b>Read my CV</b> to build your profile from it.")
        return f"<p style='color:#6b7280'>{body}</p>"

    def _render_resume(self) -> None:
        p = self._shown()
        self.resume_preview.setHtml(resume_html(p) if p else self._blank_html())

    # --- actions ------------------------------------------------------------

    def _set_sub(self, name: str) -> None:
        idx = {"Resume": 0, "Cover Letter": 1, "Profile Details": 2}[name]
        self.stack.setCurrentIndex(idx)
        # Idempotent, and safe to call from the control's own signal: the
        # segmented control only reports a value that actually changed.
        self.subtabs.set_value(name)

    def _pick_cv(self) -> None:
        """Attach one CV, or several.

        Several, because the complaint that started this was literally "I
        cannot attach two resumes" — and a file dialog that only ever
        returns one is where that begins. The last one chosen becomes the
        active one, which is what picking a file means everywhere else.
        """
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose your CV", "", "CV files (*.pdf *.docx *.txt *.md)")
        if paths:
            self.use_cv(*paths)

    def use_cv(self, *paths: str) -> None:
        """Take a CV from anywhere — the dialog, a drop on the window — and
        read it.

        Choosing a file and then being told to press a second button is a step
        that exists for no reason: there is nothing else anyone picks a CV
        *for*. The button stays, because a re-read after editing the file is a
        real thing to want, and because without a model there is nothing to
        read it with — and an error box the instant you choose a file is a bad
        way to learn that.
        """
        rid = 0
        for path in paths:
            if path:
                rid = storage.add_resume(str(path))
        if not rid:
            return
        storage.set_active_resume(rid)
        # Repaint before anything slow starts, so the chip for the new file
        # is on screen — lit, and honestly labelled "not read" — rather than
        # the page still describing the CV this one replaced.
        self._render_all()
        if len(paths) > 1:
            self.app.set_status(
                f"{len(paths)} resumes attached. Applying as "
                f"{Path(paths[-1]).name}.")
        if load_settings().llm_enabled:
            self._analyse()
        else:
            self.cv_lbl.setText(
                f"{Path(paths[-1]).name}. Add a model in Settings to read it.")

    def _analyse(self) -> None:
        """Read the active CV. The window owns the work; this owns the page.

        Blanked first and repainted after: the read takes half a minute, and
        for all of it the preview would otherwise be showing the profile
        that is about to be replaced.
        """
        active = storage.active_resume()
        if active is None:
            self.app.error("Add a resume first.")
            return
        if not active.read:
            self.resume_preview.setHtml(self._blank_html())
            self.details.setHtml(self._blank_html())
        self.app.read_cv(then=self._render_all)

    def _download_resume(self) -> None:
        # The one on screen, not whichever profile the matcher happens to be
        # using: a preview and a download that disagree is the same class of
        # bug as the stale preview was.
        p = self._shown()
        if not p:
            self.app.error("Read this CV first, and the resume builds from it.")
            return
        path = resume_path(p)
        out = render_resume_pdf(p, path, template=self.template_box.currentText())
        if out:
            self.app.set_status(f"Saved {out.name}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))
        else:
            self.app.error("Could not render the PDF (reportlab missing?).")

    def _gen_cover(self) -> None:
        p = self.app.profile
        if not p:
            self.app.error("Build your profile first.")
            return
        settings = load_settings()
        self.app.busy(True, "Writing a base cover letter…")

        def job():
            prompt = (f"Write a concise, reusable base cover letter (not for a specific job) "
                      f"for {p.full_name}, a {p.headline or p.current_title}. Use their summary: "
                      f"{p.summary}. British spelling, 3 short paragraphs, no placeholders.")
            return LLM(settings).complete(prompt, max_tokens=2500, temperature=0.4)

        def done(text: str):
            self.app.busy(False, "")
            self.cover_edit.setPlainText(text.strip())

        self.app.run(job, on_done=done)

    def _save_cover(self) -> None:
        storage.set_setting("base_cover_letter", self.cover_edit.toPlainText().strip())
        self.app.set_status("Base cover letter saved.")

    def _save_details(self) -> None:
        p = self.app.profile
        if not p:
            self.app.error("Build your profile first.")
            return
        p.target_titles = [t.strip() for t in self.targets_edit.text().split(",") if t.strip()]
        p.target_locations = self.loc_picker.values()
        p.workplace = self.loc_picker.workplace()
        p.work_authorisation = self.workauth_edit.text().strip()
        p.requires_sponsorship = self.sponsor_edit.text().strip()
        p.salary_expectation = self.salary_edit.text().strip()
        self.app.set_profile(p)
        self.app.set_status("Saved. Re-run a scan in Browse jobs to use the new targets.")

    # --- html ---------------------------------------------------------------

    @staticmethod
    def _details_html(p: Profile) -> str:
        def row(k, val):
            return (f"<tr><td style='color:#6b7280;padding:2px 14px 2px 0'>{k}</td>"
                    f"<td>{_h.escape(val)}</td></tr>") if val else ""
        exp = "".join(f"<li><b>{_h.escape(e.title)}</b> at {_h.escape(e.company)} "
                      f"<span style='color:#6b7280'>({_h.escape(e.start)}–{_h.escape(e.end) or 'present'})</span></li>"
                      for e in p.experience[:6])
        inferred = (f"<p style='color:#c2810a'><i>Inferred (please check): {', '.join(p.inferred)}</i></p>"
                    if p.inferred else "")
        return f"""
        <div style='font-size:13px'>
          <h2 style='margin:0'>{_h.escape(p.full_name)}</h2>
          <p style='color:#14432f;margin:3px 0'>{_h.escape(p.headline)}</p>
          <p>{_h.escape(p.summary)}</p>
          <table>{row('Level', p.seniority)}{row('Experience', (p.years_experience + ' years') if p.years_experience else '')}
          {row('Now', (p.current_title + ' at ' + p.current_company) if p.current_title else '')}
          {row('Location', p.location)}{row('Email', p.email)}{row('Phone', p.phone)}</table>
          <h3>Experience</h3><ul>{exp}</ul>
          <h3>Skills</h3><p style='color:#6b7280'>{_h.escape(', '.join(p.skills[:24]))}</p>
          <h3>Education</h3><p style='color:#6b7280'>{_h.escape('; '.join(p.education))}</p>
          {inferred}
        </div>"""
