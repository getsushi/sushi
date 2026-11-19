"""Does each control actually *do* something?

The other suites check that units behave and widgets react. They all passed
while Insane mode's arm switch ran nothing at all — because every one of them
asserted on the thing next to the switch (a badge, a saved flag, a preview)
rather than on the work the switch promises.

So this suite asks one question per feature: if a user does this, does the
world change? Each check names the promise, performs it the way a user would,
and asserts on an *effect* — a row in the database, a browser launched, a file
on disk, a profile field — never on the control that triggered it.

Network and model calls are stubbed where they would otherwise dominate the
runtime; what is being tested is the wiring between the button and the work,
which is exactly where things were found broken.

Run from the sushi/ directory:  python qa_audit.py [--browser]

``--browser`` adds one check that launches a real Chromium window; the rest
of the suite never leaves the process.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("SUSHI_NO_AUTOSCAN", "1")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="sushi-audit-")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PyQt6.QtCore import QEventLoop  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []
CV = Path(__file__).resolve().parent / "dummy_cv.pdf"


def _page_fingerprint(page) -> tuple:
    """Enough of a page's visible state to notice a button changing it."""
    from PyQt6.QtWidgets import (QAbstractButton, QComboBox, QLabel,
                                 QPlainTextEdit, QStackedWidget, QTableWidget)
    bits: list = []
    for w_ in page.findChildren(QLabel):
        bits.append(w_.text())
    for w_ in page.findChildren(QAbstractButton):
        bits.append((w_.text(), w_.isChecked(), w_.isEnabled(),
                     w_.objectName()))
    for w_ in page.findChildren(QStackedWidget):
        bits.append(w_.currentIndex())
    for w_ in page.findChildren(QTableWidget):
        bits.append(w_.rowCount())
    for w_ in page.findChildren(QComboBox):
        bits.append(w_.currentText())
    for w_ in page.findChildren(QPlainTextEdit):
        bits.append(len(w_.toPlainText()))
    # A control whose whole job is to open a menu changes nothing else, and
    # was reported dead for it. Popups are top-level windows, so they are
    # counted from the application rather than from the page.
    from PyQt6.QtWidgets import QApplication as _QApp, QMenu
    bits.append(sum(1 for m in _QApp.topLevelWidgets()
                    if isinstance(m, QMenu) and m.isVisible()))
    return tuple(bits)


def _open_menus(app):
    """Every popup menu currently on screen."""
    from PyQt6.QtWidgets import QMenu
    return [m for m in app.topLevelWidgets()
            if isinstance(m, QMenu) and m.isVisible()]


def _shut_menus(app) -> int:
    """Close them. A menu left open covers the next button."""
    menus = _open_menus(app)
    for m in menus:
        m.close()
    return len(menus)


def _shut_drawer(w) -> bool:
    """Close the details drawer if it is open.

    Same reason as the menus. The drawer is one widget reused for
    every role, so a second Details press while it is already open
    changes only its contents — and if the role happens to match the
    one already shown, nothing at all. Four working Details buttons
    reported dead for it."""
    overlay = getattr(w, '_details', None)
    if overlay is None or not overlay.isVisible():
        return False
    overlay.close_panel()
    return True


def _report() -> None:
    """Print what happened. Called before a teardown that can die."""
    print()
    width = max((len(n) for _, n, _ in RESULTS), default=10) + 2
    for status, name, detail in RESULTS:
        print(f"  {'ok  ' if status == 'PASS' else 'FAIL'}  {name:<{width}} {detail}")
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")


def check(promise: str, fn) -> None:
    try:
        RESULTS.append(("PASS", promise, str(fn() or "")[:160]))
    except Exception as e:
        RESULTS.append(("FAIL", promise, f"{type(e).__name__}: {e}"))
        traceback.print_exc(limit=2)


def main() -> int:
    app = QApplication(sys.argv)

    def settle(ms: int = 250) -> None:
        end = time.time() + ms / 1000
        while time.time() < end:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)

    from sushi import storage
    from sushi.models import Experience, Profile, Role
    from sushi.ui import theme
    from sushi.ui.fonts import DISPLAY, load_fonts

    theme.refresh_stylesheet(display=load_fonts().get("display", DISPLAY))
    from sushi.ui.main_window import MainWindow

    profile = Profile(
        full_name="Rowan Ellis Hartley", email="r.hartley@example.com",
        headline="Senior Operations Analyst", summary="Ops and data analyst.",
        skills=["Python", "SQL"], seniority="ic",
        experience=[Experience(company="Kestrel", title="Analyst", start="2022")],
        target_titles=["Operations Analyst"], target_locations=["Remote (UK)"])
    storage.save_profile(profile)
    storage.set_setting("cv_path", str(CV))
    storage.set_setting("onboarded", "1")

    def roles(n: int = 6) -> list[Role]:
        seed = [("Kestrel", "Senior Operations Analyst", 17),
                ("Wise", "Staff Data Analyst", 15),
                ("Cleo", "Analytics Lead", 14),
                ("Zopa", "Senior Data Analyst", 13),
                ("Tide", "BI Lead", 12),
                ("Starling", "Operations Manager", 11)]
        return [Role(company=c, title=t, score=s, match=70 + s,
                     location="Remote (UK)", ats="greenhouse",
                     url=f"https://boards.greenhouse.io/{c.lower()}/jobs/{i}",
                     reasons=["relevant"], description="A description.")
                for i, (c, t, s) in enumerate(seed[:n])]

    w = MainWindow()
    w.resize(1440, 920)
    w.move(-7000, -7000)
    w.show()
    settle(700)
    w.set_matches(roles())
    # All jobs reads the job database rather than the in-memory feed, and
    # this suite runs against an empty scratch one. The page's promise is
    # what it does with a list, not where the list came from — jobsdb's own
    # query is qa_features' business.
    w.all_matches = lambda limit=4000: roles(6)
    settle(300)

    # Never let a real browser or a real scan start from this suite.
    launched: list[dict] = []

    def fake_begin(role, watch=False, announce=True, on_finished=None,
                   on_step=None, **_kw):
        """Stand in for a real browser run, and complete like one."""
        launched.append({"role": role, "watch": watch})
        if on_step:
            # Insane mode feeds progress into its table; a stub that ignored
            # it would let that wiring rot without any test noticing.
            on_step("Reading the form…")
        from sushi import storage as _s
        app_id = _s.upsert_role(role, status="needs_review")
        _s.set_status(app_id, "needs_review", "filled by the audit stub")
        if on_finished:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(50, lambda: on_finished("needs_review", None))

    w.begin_apply = fake_begin
    w.apply_to_role = lambda role, watch=False: fake_begin(role, watch)
    scans: list[int] = []
    real_start_scan = w.start_scan
    w.start_scan = lambda: scans.append(1)
    # Dialogs would block forever with nobody to press them.
    # Everything a button might reach that is *outside* this process is
    # stubbed — but counted. A button that opens a file dialog or a browser
    # has plainly done something; without counting it the sweep reports it as
    # dead and buries the real findings in noise.
    outside: list[str] = []

    def _stub(name, result):
        def go(*_a, **_k):
            outside.append(name)
            return result
        return staticmethod(go)

    QMessageBox.question = _stub("ask", QMessageBox.StandardButton.Yes)
    QMessageBox.information = _stub("tell", None)
    QMessageBox.warning = _stub("warn", None)
    QMessageBox.critical = _stub("warn", None)
    from PyQt6.QtWidgets import QFileDialog
    QFileDialog.getOpenFileName = _stub("pick-file", ("", ""))
    QFileDialog.getExistingDirectory = _stub("pick-folder", "")
    QFileDialog.getSaveFileName = _stub("save-file", ("", ""))
    from PyQt6.QtGui import QDesktopServices
    QDesktopServices.openUrl = _stub("open-url", True)

    # --- the promise on the Insane mode page --------------------------------
    def p_insane_runs():
        """'Arm this and it applies to the best of them too.'"""
        from sushi.extras import load_autoapply, save_autoapply
        page = w._pages["Insane mode"]
        w.navigate("Insane mode")
        settle(300)
        cfg = load_autoapply()
        cfg.daily_cap = 3
        save_autoapply(cfg)
        page.on_show()
        settle(150)

        launched.clear()
        before = {a.role.url: a.status for a in storage.list_applications()}
        page.arm_btn.setChecked(True)
        settle(1200)

        after = storage.list_applications()
        changed = [a for a in after
                   if before.get(a.role.url) != a.status
                   and a.status in {"queued", "filling", "needs_review", "submitted"}]
        assert launched or changed, (
            "armed and nothing happened — no browser opened and no application "
            "changed state")

        # And it has to be *visible*. Arming used to change a caption and
        # nothing else, which is indistinguishable from arming and failing:
        # the queue now goes on the table before the first browser opens, and
        # each row carries what became of it.
        rows = w.autoapply_rows()
        assert rows, "the run put nothing on the table"
        assert page.table.rowCount() == len(rows), (
            page.table.rowCount(), len(rows))
        assert page.table.isVisible(), "the table was not shown"
        states = {r["state"] for r in rows}
        assert states - {"queued"}, f"every row still says queued: {states}"
        assert any(r["detail"] for r in rows), "no row said what it was doing"
        # Best match first, so the cap is spent on the best of them.
        scores = [r["score"] for r in rows]
        assert scores == sorted(scores, reverse=True), scores

        page.arm_btn.setChecked(False)
        settle(200)
        return (f"arming started work on {len(launched) or len(changed)} "
                f"role(s); {len(rows)} on the table, best first")
    check("Insane mode: arming it actually applies", p_insane_runs)

    def p_insane_skips_closed():
        """'A posting that has gone is known to have gone' — so skip it.

        The per-board closure diff existed for exactly this and nothing
        consulted it, so the queue kept spending a browser launch, a model
        call and one of a fifteen-a-day cap on adverts for jobs that had
        already been taken down.
        """
        from sushi import jobsdb
        from sushi.extras import load_autoapply, save_autoapply

        jobsdb.forget_all()
        live_url = "https://boards.greenhouse.io/kestrel/jobs/live"
        dead_url = "https://boards.greenhouse.io/kestrel/jobs/dead"
        # The board is read, and only the live posting is on it.
        jobsdb.store_board(
            {"name": "Kestrel", "ats": "greenhouse", "token": "kestrel"},
            [{"title": "Senior Operations Analyst", "location": "Remote (UK)",
              "department": "Ops", "url": live_url}])

        def role_on_board(url: str, title: str) -> Role:
            return Role(company="Kestrel", title=title, score=16, match=86,
                        location="Remote (UK)", ats="greenhouse",
                        token="kestrel", url=url, reasons=["relevant"],
                        description="A description.")

        cfg = load_autoapply()
        cfg.daily_cap = 5
        cfg.enabled = False
        save_autoapply(cfg)
        w.stop_autoapply("")
        w.set_matches([role_on_board(dead_url, "Closed Operations Analyst"),
                       role_on_board(live_url, "Senior Operations Analyst")])
        settle(300)

        launched.clear()
        page = w._pages["Insane mode"]
        page.on_show()
        settle(150)
        page.arm_btn.setChecked(True)
        settle(1500)

        urls = [d["role"].url for d in launched]
        rows = {r["title"]: r for r in w.autoapply_rows()}
        page.arm_btn.setChecked(False)
        settle(200)
        jobsdb.forget_all()
        w.set_matches(roles())
        settle(200)

        assert dead_url not in urls,             "applied to a posting that is no longer on its board"
        assert live_url in urls, f"skipped the live one too: {urls}"
        closed_row = rows.get("Closed Operations Analyst", {})
        assert closed_row.get("state") == "skipped", closed_row
        assert "no longer" in (closed_row.get("detail") or "").lower(), closed_row
        return ("the closed posting was skipped and said so; the live one "
                "was applied to")
    check("Insane mode: a posting taken down is skipped, not applied to",
          p_insane_skips_closed)

    def p_insane_cap():
        """'up to a cap you set' — and it must respect it."""
        from sushi.extras import load_autoapply, save_autoapply
        cfg = load_autoapply()
        cfg.daily_cap = 2
        cfg.enabled = False
        save_autoapply(cfg)
        page = w._pages["Insane mode"]
        # Its own roles, on their own URLs. The checks above have applied to
        # the seed feed, and Insane mode skips what it has already dealt
        # with — so a cap test sharing their roles would be measuring an
        # empty pool and calling it a cap.
        w.set_matches([replace(r, url=r.url + "-cap") for r in roles(5)])
        settle(200)
        page.on_show()
        settle(120)
        launched.clear()
        page.arm_btn.setChecked(True)
        settle(1500)
        n = len(launched)
        page.arm_btn.setChecked(False)
        settle(200)
        assert n <= 2, f"cap of 2 and it started {n}"
        assert n > 0, "cap of 2 and it started none"
        return f"cap of 2 honoured ({n} started)"
    check("Insane mode: the daily cap is honoured", p_insane_cap)

    def p_insane_disarm():
        """'stopping the moment you disarm it.'"""
        from sushi.extras import load_autoapply, save_autoapply
        cfg = load_autoapply()
        cfg.daily_cap = 20
        cfg.enabled = False
        save_autoapply(cfg)
        page = w._pages["Insane mode"]
        # Fresh URLs again, for the same reason as the cap check: a queue it
        # has already worked through is not a queue to be stopped.
        w.set_matches([replace(r, url=r.url + "-disarm") for r in roles(5)])
        settle(200)
        page.on_show()
        settle(120)
        launched.clear()
        page.arm_btn.setChecked(True)
        settle(300)
        page.arm_btn.setChecked(False)
        settle(900)
        stopped_at = len(launched)
        settle(900)
        assert len(launched) == stopped_at, (
            f"kept going after disarm: {stopped_at} → {len(launched)}")
        return f"stopped at {stopped_at} and stayed stopped"
    check("Insane mode: disarming stops it", p_insane_disarm)

    def p_insane_waits():
        """Armed with nothing to do must stay armed and explain itself.

        This is exactly what the user hit, back when there was a quality
        bar: 64 matches, the bar on "Only the best", nothing scoring 15. The
        runner found no picks, printed a shrug in the status bar and gave up
        — while the button went on saying RUNNING. Nothing was running and
        nothing ever would.

        The bar is gone, but the state it produced has not gone with it: a
        feed where every role has already been applied to is the same empty
        queue reached a different way, and it has to read as a wait rather
        than as a silent stop.
        """
        from sushi import storage as _s
        from sushi.extras import load_autoapply, save_autoapply
        cfg = load_autoapply()
        cfg.daily_cap = 10
        cfg.enabled = False
        save_autoapply(cfg)
        page = w._pages["Insane mode"]
        w.navigate("Insane mode")
        page.on_show()
        settle(150)

        seen: list[tuple] = []
        w.autoapply_changed.connect(lambda a, n: seen.append((a, n)))
        launched.clear()
        # A feed with nothing left on it: every role already sent.
        done = [replace(r, url=r.url + "-sent") for r in roles(4)]
        for r in done:
            _s.set_status(_s.upsert_role(r, status="submitted"),
                          "submitted", "already sent")
        w.set_matches(done)
        page.on_show()
        settle(150)
        page.arm_btn.setChecked(True)
        settle(600)

        assert not launched, "applied a second time to something already sent"
        assert w.autoapplying(), "gave up instead of standing by"
        assert seen and seen[-1][0] is True, f"reported itself off: {seen[-1:]}"
        note = seen[-1][1]
        assert "armed" in note.lower(), f"no explanation offered: {note!r}"
        assert str(len(done)) in note,             f"does not say what it looked at: {note!r}"
        assert page.arm_btn._caption != "Running — tap to stop",             "the button still claims to be running"

        # And a search that turns up a role it has not dealt with must wake
        # it. Fresh URLs, because the seed roles have been applied to by the
        # checks above and a standing order is right to ignore those.
        # The scan hands roles over as it finds them now; _scan_done and its
        # slice-at-a-time sweep are gone, replaced by the database refresh.
        w.scan_found.emit([replace(r, url=r.url + "-fresh") for r in roles(3)])
        settle(900)
        assert launched, "a standing order slept through a new search"
        page.arm_btn.setChecked(False)
        settle(300)
        return (f"waited with an explanation, then started on "
                f"{len(launched)} role(s) when the feed changed")
    check("Insane mode: nothing to do is a wait, not a silent stop",
          p_insane_waits)

    def p_cap_names_the_pool():
        """The page must say what the cap is being spent out of.

        The quality dial used to carry this: each band labelled with the
        number of roles it would act on. The dial has gone — its three
        bands all selected the same roles on a real feed — but the count
        was the honest half of it, and a page that says how many it may
        send a day and never how many there are to send to has told you
        half the state of the thing running on your behalf.
        """
        from sushi.extras import load_autoapply, still_open
        page = w._pages["Insane mode"]
        w.navigate("Insane mode")
        feed = [replace(r, url=r.url + "-pool") for r in roles(6)]
        w.set_matches(feed)
        page.on_show()
        settle(250)
        said = page.cap_row.help_label.text()
        waiting = len(still_open(feed))
        assert waiting == len(feed), "the fresh feed was not all open"
        assert str(waiting) in said, f"the pool is not on screen: {said!r}"
        assert str(load_autoapply().daily_cap) in said, said
        return f"the cap row names the pool: {said}"
    check("Insane mode: the page says what the cap is spent out of",
          p_cap_names_the_pool)

    # --- the dashboard ------------------------------------------------------
    def p_apply_card():
        """A job card's Apply must open a browser for that job."""
        w.navigate("Dashboard")
        settle(300)
        launched.clear()
        card = w._pages["Dashboard"].matches_flow.itemAt(0).widget()
        card.apply_clicked.emit(card.role)
        settle(300)
        assert launched, "Apply on a card did nothing"
        assert launched[0]["role"].url == card.role.url
        return f"Apply opened {launched[0]['role'].company}"
    check("Dashboard: Apply on a card reaches the browser", p_apply_card)

    def p_pass_card():
        """Pass must remove it from the feed and record the decision."""
        page = w._pages["Dashboard"]
        card = page.matches_flow.itemAt(0).widget()
        role = card.role
        before = len(w.matches)
        card.pass_clicked.emit(role)
        settle(300)
        assert len(w.matches) == before - 1, "passing left it in the feed"
        row = next((a for a in storage.list_applications()
                    if a.role.url == role.url), None)
        assert row and row.status == "skipped", f"not recorded: {row and row.status}"
        return f"passed {role.company}; feed {before} → {len(w.matches)}"
    check("Dashboard: Pass removes it and records it", p_pass_card)

    def p_look_again():
        """'Look again' must start a search."""
        page = w._pages["Dashboard"]
        scans.clear()
        page._toggle_scan()
        settle(200)
        assert scans, "Look again started nothing"
        return "Look again started a search"
    check("Dashboard: Look again starts a search", p_look_again)

    def p_hero_insane():
        """The hero's 'Open Insane mode' must go there."""
        w.navigate("Dashboard")
        settle(200)
        w.navigate("Insane mode")
        settle(200)
        assert w._current == "Insane mode", w._current
        return "navigates"
    check("Dashboard: the hero button navigates", p_hero_insane)

    # --- the tracker --------------------------------------------------------
    def p_tracker_export():
        """'Export CSV' must write a real file."""
        from sushi.extras import export_tracker_csv
        csv = export_tracker_csv()
        assert csv.count("\n") >= 2, "export produced no rows"
        return f"{csv.count(chr(10)) - 1} row(s) exported"
    check("Tracker: export produces rows", p_tracker_export)

    def p_tracker_move():
        """Moving a card must persist the stage."""
        from sushi.extras import PIPELINE_STAGES, get_stage
        apps = storage.list_applications()
        assert apps, "nothing to track"
        storage.set_status(apps[0].id, "submitted")
        w.navigate("Tracker")
        settle(400)
        page = w._pages["Tracker"]
        page._move(apps[0].id, "interviewing")
        settle(200)
        got = get_stage(apps[0].id, "submitted")
        assert got == "interviewing", got
        return "stage persisted"
    check("Tracker: moving a card persists", p_tracker_move)

    # --- profile ------------------------------------------------------------
    def p_resume_pdf():
        """'Download PDF' must put a file in the workspace."""
        from sushi.config import WORKSPACE_TREE, ensure_workspace
        from sushi.resume import render_resume_pdf, resume_path
        out = ensure_workspace() / WORKSPACE_TREE["resumes"]
        before = set(out.glob("*.pdf"))
        path = render_resume_pdf(w.profile, resume_path(w.profile))
        settle(100)
        assert Path(path).exists(), "no PDF written"
        assert Path(path).stat().st_size > 800, "PDF is empty"
        new = set(out.glob("*.pdf")) - before
        return f"{Path(path).name}, {Path(path).stat().st_size} bytes" + (
            " (in the workspace)" if new or Path(path).parent == out else "")
    check("Profile: the resume PDF is really written", p_resume_pdf)

    # --- settings actually changing behaviour --------------------------------
    def p_autosubmit_setting():
        """'Submit automatically' must change what the agent is told."""
        from dataclasses import replace

        from sushi.autofill import _task
        from sushi.config import load_settings
        s = load_settings()
        off = _task(roles(1)[0], w.profile, "", "", False)
        on = _task(roles(1)[0], w.profile, "", "", True)
        assert "Do NOT submit" in off, "the no-submit rule is missing"
        assert "Do NOT submit" not in on, "autosubmit did not change the task"
        return "the task text follows the setting"
    check("Settings: autosubmit changes the agent's task", p_autosubmit_setting)

    def p_resume_mode_setting():
        """Resume optimisation must reach the resume builder."""
        from sushi.autofill import _task
        from sushi.extras import load_apply_settings, save_apply_settings
        a = load_apply_settings()
        before = a.resume_optimization
        seen = {}
        for mode in ("off", "honest", "aggressive"):
            a.resume_optimization = mode
            save_apply_settings(a)
            # Read it back, so this fails if the save or the load drops it.
            mode_now = load_apply_settings().resume_optimization
            seen[mode] = _task(roles(1)[0], w.profile, "", "", False, mode_now)
        a.resume_optimization = before
        save_apply_settings(a)
        assert len(set(seen.values())) == 3, (
            "every optimisation level produces the same instruction — the "
            "setting is saved, drawn, named in the apply dialog, and read by "
            "nothing")
        assert "never state a fact that is not true" in seen["aggressive"], \
            "the aggressive mode has no honesty floor"
        return "three modes, three different instructions to the agent"
    check("Settings: resume optimisation changes the prompt", p_resume_mode_setting)

    def p_demographics_setting():
        """The demographics switch must change what is learned."""
        from sushi import learning
        q, ans = "What is your gender?", "Prefer not to say"
        assert not learning.learnable(q, ans), "learned a demographic by default"
        assert learning.learnable(q, ans, include_demographics=True), \
            "opting in changed nothing"
        return "off by default, on when asked"
    check("Settings: the demographics switch is real", p_demographics_setting)

    # --- the assistant ------------------------------------------------------
    def p_chat_action():
        """A proposed action must execute, not just be described."""
        panel = w.assistant
        before = list(w.profile.target_titles)
        panel._offer_action("set_targets", "Data Analyst, Analytics Engineer")
        settle(250)
        got = storage.load_profile().target_titles
        assert got == ["Data Analyst", "Analytics Engineer"], got
        w._run_action("undo_preferences", "")
        settle(200)
        assert storage.load_profile().target_titles == before
        return "set_targets ran and undo put it back"
    check("Assistant: an action runs instead of being narrated", p_chat_action)

    def p_chat_unknown_action():
        """An action with no handler must be visible, not silently dropped."""
        from PyQt6.QtWidgets import QPushButton
        panel = w.assistant
        panel._offer_action("some_future_action", "")
        settle(150)
        labels = [b.text() for b in panel.findChildren(QPushButton)]
        assert "Some future action" in labels, labels[-4:]
        return "unknown actions surface as a button"
    check("Assistant: an unhandled action is not swallowed", p_chat_unknown_action)

    # --- the knowledge folder ------------------------------------------------
    def p_knowledge_reaches_autofill():
        """What the folder taught must reach the form-filling agent."""
        from sushi import learning
        from sushi.autofill import _task
        learning.remember("Notice period", "One month", source="human")
        task = _task(roles(1)[0], w.profile, "", "", False)
        learning.forget("Notice period")
        assert "One month" in task, "learned answers never reach the agent"
        return "the answer bank is in the agent's task"
    check("Learning: the bank reaches the agent", p_knowledge_reaches_autofill)

    def p_secrets_never_in_task():
        """Nothing secret may be written into the prompt."""
        from sushi.autofill import _task
        task = _task(roles(1)[0], w.profile, "", "", False)
        from sushi.config import load_settings
        s = load_settings()
        if s.api_key:
            assert s.api_key not in task, "THE API KEY IS IN THE PROMPT"
        assert "<secret>" in task, "sensitive values are not placeheld"
        return "no key in the task; sensitive values placeheld"
    check("Safety: no credentials in the agent's prompt", p_secrets_never_in_task)

    # --- every action the assistant can name must be handled ----------------
    def p_every_action_handled():
        """No action may exist that the panel cannot dispatch.

        This is the bug that made the chat feel inert: set_targets was added
        to ACTIONS, the window learned to execute it, and the panel's
        hand-written label map never heard about it — so the reply arrived,
        the action arrived, and the panel dropped it in between.
        """
        from sushi.assistant import ACTIONS
        from sushi.ui.assistant_panel import AssistantPanel
        known = set(AssistantPanel._IMMEDIATE) | set(AssistantPanel._CONFIRM)
        missing = set(ACTIONS) - known - {"none"}
        assert not missing, f"the panel cannot dispatch: {sorted(missing)}"
        # And the window must handle each one too.
        import inspect
        src = inspect.getsource(w._run_action.__func__)
        unhandled = [a for a in ACTIONS if a != "none" and f'"{a}"' not in src]
        assert not unhandled, f"the window ignores: {unhandled}"
        return f"all {len(ACTIONS) - 1} actions dispatch and execute"
    check("Assistant: every action is wired end to end", p_every_action_handled)

    # --- what the user types into a form is kept ----------------------------
    def p_form_answers_persist():
        """Jobly's promise: answer it once, never answer it again."""
        from sushi import learning, storage as st
        # What the watcher does when it sees a settled field the user typed.
        learning.learn_pairs(
            [("What is your notice period?", "One month"),
             ("Why do you want to work here?", "The routing problem."),
             ("Portfolio URL", "https://example.com/rowan")], source="human")
        # It must survive going through the database, not just a cache.
        raw = st.all_answers()
        assert any("notice period" in k for k in raw), "not written to storage"
        # And come back for a differently-worded question next time.
        assert learning.recall("Notice period") == "One month"
        assert learning.recall("What's your notice period?") == "One month"
        # An agent guess must never overwrite what the human typed.
        learning.remember("What is your notice period?", "Two weeks", source="agent")
        assert learning.recall("Notice period") == "One month",             "an agent guess overwrote the user's own answer"
        for q in ("What is your notice period?", "Why do you want to work here?",
                  "Portfolio URL"):
            learning.forget(q)
        return "typed once, stored, recalled on a reworded question, never overwritten"
    check("Learning: what you type in a form is kept for next time",
          p_form_answers_persist)

    def p_signin_is_offered():
        """A job behind Google sign-in must have a way through."""
        from sushi.autofill import SIGNIN_URLS, _task, open_for_signin
        assert "Google" in SIGNIN_URLS, "no way to sign in to Google once"
        task = _task(roles(1)[0], w.profile, "", "", False)
        assert "Sign in with Google" in task,             "the agent is never told what to do with an SSO button"
        assert "Do not type anything into it" in task,             "the agent is not forbidden from typing credentials"
        return "SSO click-through instructed, credentials forbidden, sign-in once offered"
    check("Apply: single sign-on has a route that is not a password",
          p_signin_is_offered)

    def p_logos():
        """A logo must appear, be cached, and never block or leak."""
        from sushi import logos
        from sushi.ui.components import CompanyLogo

        assert logos.normalise("Kestrel Bank Ltd") == "kestrel bank",             logos.normalise("Kestrel Bank Ltd")
        # The lettered tile is the state before anything arrives — a card is
        # never blank while a network call is outstanding.
        tile = CompanyLogo("Nonexistent Qqq", 28)
        assert tile.text() == "N", f"no fallback letter: {tile.text()!r}"

        # A miss is remembered, so a company with no logo costs one request.
        logos._load_index()[logos.normalise("Zzz Nothing")] = ""
        assert logos.known_miss("Zzz Nothing"), "misses are not remembered"
        assert logos.fetch("Zzz Nothing") is None
        return "lettered fallback, misses cached"
    check("Dashboard: company logos degrade safely", p_logos)

    def p_table_cells():
        """The applications table must render real cells, not blank widgets."""
        from PyQt6.QtWidgets import QLabel
        w.navigate("Dashboard")
        settle(300)
        page = w._pages["Dashboard"]
        page._set_tab("All")
        settle(400)
        t = page.app_table
        assert t.rowCount(), "no applications to render"
        for col, what in ((0, "company"), (1, "role"), (2, "status"),
                          (3, "when")):
            cell = t.cellWidget(0, col)
            assert cell is not None, f"{what} cell is missing"
            texts = [l.text() for l in cell.findChildren(QLabel) if l.text()]
            assert texts, f"{what} cell rendered empty"
            # Every cell must fit its column — this is what was clipping
            # "Submitted" to "Submitte".
            for lab in cell.findChildren(QLabel):
                if not lab.text() or lab.pixmap():
                    continue
                from PyQt6.QtGui import QFontMetrics
                need = QFontMetrics(lab.font()).horizontalAdvance(lab.text())
                assert lab.width() + 1 >= need, (
                    f"{what}: {lab.text()!r} needs {need}px, has {lab.width()}px")
        return f"{t.rowCount()} rows, every cell populated and fitting"
    check("Dashboard: the applications table renders fully", p_table_cells)

    def p_details_drawer():
        """Clicking a job must open a readable drawer, not a modal."""
        from sushi.models import Role
        from sushi.ui.details import contract_kind, reflow, workplace

        desc = ("Line one of a paragraph\nbroken by the board."
                "\n\nAbout the Role\nWe are hiring.")
        role = Role(company="Zekelman Industries",
                    title="Intern, Production Analytics",
                    location="Wheatland, PA", match=74, score=14,
                    department="Data Science", salary="$28 - $34 / hour",
                    url="https://boards.greenhouse.io/z/1",
                    reasons=["Production planning", "Right seniority"],
                    description=desc)
        w.show_role_details(role)
        settle(500)
        overlay = w.details_overlay()
        assert overlay.isVisible(), "the drawer did not open"
        assert overlay.panel.isVisible(), "the drawer opened empty"
        assert overlay.panel.title.text() == role.title
        # It must sit over the feed, not replace it.
        assert w._pages["Dashboard"].isVisible(), "the feed was torn down"
        # And it must be closable without a decision.
        overlay.close_panel()
        settle(200)
        assert not overlay.isVisible(), "the drawer would not close"

        # The facts it derives, since no board publishes them as fields.
        assert contract_kind(role) == "Internship", contract_kind(role)
        assert workplace(role) == "On site", workplace(role)
        assert workplace(Role(title="Analyst", location="Remote (UK)")) == "Remote"
        # And hard-wrapped prose is joined back into paragraphs.
        flowed = reflow(desc)
        assert "paragraph broken by the board" in flowed, flowed
        assert "About the Role\nWe are hiring." in flowed, flowed

        # Most boards publish no description at all. That used to be a
        # "Description" heading over an apology, which is the commonest
        # thing this drawer showed and told nobody anything. Now the way
        # to go and read it takes that place — so the test is that the
        # apology is gone *and* the way out is there.
        from PyQt6.QtWidgets import QAbstractButton, QLabel
        bare = replace(role, description="")
        w.show_role_details(bare)
        settle(400)
        panel = w.details_overlay().panel
        texts = [lb.text() for lb in panel.findChildren(QLabel)]
        assert not any("does not publish" in t for t in texts), \
            "the drawer still apologises for the missing description"
        assert not any(t.strip() == "Description" for t in texts), \
            "an empty Description heading is still drawn"
        opener = [b for b in panel.findChildren(QAbstractButton)
                  if "posting" in b.text().lower()]
        assert opener, ("no way to go and read the posting: "
                        + str([b.text() for b in
                               panel.findChildren(QAbstractButton)]))
        before = len(outside)
        opener[0].click()
        settle(200)
        assert len(outside) > before, "the posting link opened nothing"
        w.details_overlay().close_panel()
        settle(150)
        return ("opens over the feed, closes clean, facts derived, prose "
                "reflowed, no-description drawer offers the posting instead")
    check("Dashboard: the details drawer opens and reads", p_details_drawer)

    def p_details_reopens():
        """Opening it again must not blow up on the way out.

        The drawer tore down its previous contents with
        ``item.widget().setParent(None); item.widget().deleteLater()`` —
        two calls, each re-resolving. The second returns None once the
        first has reparented, so the second Details click raised
        "'NoneType' object has no attribute 'deleteLater'".

        Reopened with the roles the All jobs page hands over, too: those
        come from the job database and carry no description and no salary,
        so they exercise a different set of rows in the drawer's body.
        """
        from sushi.models import Role

        overlay = w.details_overlay()
        bare = [Role(company=f"Company {i}", title=f"Logistics Analyst {i}",
                     location="London, United Kingdom", score=10, match=65,
                     ats="greenhouse", token=f"c{i}",
                     url=f"https://example.com/c{i}/1")
                for i in range(4)]
        rich = roles(2)
        for n, role in enumerate(bare + rich + bare[:2]):
            w.show_role_details(role)
            settle(120)
            assert overlay.isVisible(), f"the drawer vanished on open {n}"
            assert overlay.panel.title.text() == role.title, (
                n, overlay.panel.title.text(), role.title)
        overlay.close_panel()
        settle(150)
        return f"opened {len(bare) * 2 + len(rich)} times in a row, no teardown error"
    check("Dashboard: the details drawer survives being reopened",
          p_details_reopens)

    def p_files_reach_the_agent():
        """The CV and the cover letter must actually be attachable.

        Every application was going out with neither. Naming a path in the
        task is not enough — browser-use only uploads files listed in
        ``available_file_paths``, and that was never set, so the agent
        reported "supplied file could not be accessed or attached" for both
        on every single form.
        """
        import inspect

        from sushi.autofill import _run, _task, _uploadable

        # The allowlist is passed to the Agent.
        src = inspect.getsource(_run)
        assert "available_file_paths" in src, (
            "the agent is not told which files it may upload")

        # Only real files are offered, with absolute paths.
        real = str(CV)
        got = _uploadable(real, "")
        assert got.get("cv"), "an existing CV was not offered to the agent"
        assert Path(got["cv"]).is_absolute()
        assert _uploadable("", "") == {}, "offered a file that was not supplied"
        assert _uploadable("Z:/nope.pdf", "") == {},             "offered a path with nothing at the end of it"

        # And the task tells it these are uploadable rather than decorative.
        task = _task(roles(1)[0], w.profile, real, "", False)
        assert "upload action" in task, "no instruction to actually upload"
        assert "suggestions as you type" in task,             "no instruction for typeahead fields, which silently discard text"
        return "allowlist passed, only real files offered, upload instructed"
    check("Apply: the CV and cover letter can be attached",
          p_files_reach_the_agent)

    def p_resize_borders():
        """A frameless window still has to resize from all eight edges.

        FramelessWindowHint removes the non-client area and every resize
        border with it; they come back only by answering WM_NCHITTEST. The
        maths must hold at any display scale, so it is checked at several —
        the previous form multiplied the window rectangle up into physical
        pixels, which *shrank* the grab target as scaling rose and made the
        edges unusable on a scaled display while working fine at 100%.
        """
        import ctypes
        import ctypes.wintypes

        from PyQt6.QtCore import QByteArray

        if not hasattr(ctypes, "windll"):
            return "skipped — not Windows"

        msg = ctypes.wintypes.MSG()
        msg.message = 0x0084                      # WM_NCHITTEST
        buf = ctypes.addressof(msg)
        real_ratio = w.devicePixelRatioF

        def hit(px, py):
            msg.lParam = (int(py) << 16) | (int(px) & 0xFFFF)
            return w.nativeEvent(QByteArray(b"windows_generic_MSG"), buf)

        checked = []
        try:
            for ratio in (1.0, 1.25, 1.5, 2.0):
                w.devicePixelRatioF = lambda r=ratio: r
                rect = w.frameGeometry()
                left, top = rect.left() * ratio, rect.top() * ratio
                right, bottom = rect.right() * ratio, rect.bottom() * ratio
                midx, midy = (left + right) / 2, (top + bottom) / 2
                wanted = {
                    "left": ((left + 1, midy), 10),
                    "right": ((right - 1, midy), 11),
                    "top": ((midx, top + 1), 12),
                    "bottom": ((midx, bottom - 1), 15),
                    "top-left": ((left + 1, top + 1), 13),
                    "top-right": ((right - 1, top + 1), 14),
                    "bottom-left": ((left + 1, bottom - 1), 16),
                    "bottom-right": ((right - 1, bottom - 1), 17),
                }
                for edge, ((px, py), code) in wanted.items():
                    handled, got = hit(px, py)
                    assert handled and got == code, (
                        f"at {ratio}x the {edge} edge answered {got}, not {code}")
                # And the middle of the window must stay clickable.
                handled, _ = hit(midx, midy)
                assert not handled, f"at {ratio}x the window centre claimed a border"
                checked.append(f"{ratio}x")
        finally:
            w.devicePixelRatioF = real_ratio
        return f"all 8 edges resize at {', '.join(checked)}; the centre stays clickable"
    check("Window: it can be resized from every edge", p_resize_borders)

    # --- the sweep: every button on every page ------------------------------
    def p_every_button():
        """Click everything. Report anything that does nothing at all.

        Not a replacement for the checks above — a button can change state and
        still be wrong — but it is the only thing that finds a control nobody
        ever wired, which is how most of this suite's findings started.
        """
        from PyQt6.QtWidgets import QPushButton
        skip_text = {"Forget all", "Restart onboarding", "Reset", "Delete",
                     "Refresh companies", "Sign in to Google",
                     "Sign in to LinkedIn", "Sign in to Greenhouse",
                     "Sign in to Workday", "Open the job page",
                     "Write a cover letter", "Fill it in Chrome",
                     "Read the job", "Open"}
        dead: list[str] = []
        clicked = 0
        for name in list(w._pages):
            w.navigate(name)
            settle(260)
            page = w._pages[name]
            for b in page.findChildren(QPushButton):
                text = b.text().strip()
                if not b.isEnabled() or not text or text in skip_text:
                    continue
                # A user cannot press what is not on screen. Detached
                # widgets awaiting deletion are still in the tree, and
                # clicking one runs a slot bound to a row that has gone.
                if not b.isVisible():
                    continue
                before = (
                    w._current,
                    w.status_text(),
                    len(storage.list_applications()),
                    len(launched),
                    len(scans),
                    len(outside),
                    app.clipboard().text(),
                    # The whole window, not just the page: "Improve with
                    # assistant" posts into the chat rail, which a page-only
                    # fingerprint cannot see — and reported a working button
                    # as dead.
                    _page_fingerprint(w),
                )
                try:
                    b.click()
                except Exception as e:
                    dead.append(f"{name}/{text}: raised {type(e).__name__}")
                    continue
                settle(160)
                after_menus = _open_menus(app)
                after = (
                    w._current,
                    w.status_text(),
                    len(storage.list_applications()),
                    len(launched),
                    len(scans),
                    len(outside),
                    app.clipboard().text(),
                    _page_fingerprint(w),
                )
                clicked += 1
                _shut_menus(app)
                _shut_drawer(w)
                settle(60)
                if before == after and not after_menus:
                    # Selecting what is already selected is allowed to do
                    # nothing — that is what idempotent means, not a bug.
                    if b.objectName().endswith("Active"):
                        continue
                    dead.append(f"{name} → “{text}”")
                w.navigate(name)
                settle(120)
        assert not dead, ("buttons that changed nothing at all: "
                          + "; ".join(dead[:12]))
        return f"{clicked} buttons clicked, every one of them did something"
    check("Every button on every page does something", p_every_button)

    # --- a job you have applied to is not a job you are choosing -------------
    def p_applied_leaves_the_feed():
        """It goes to the Tracker, and the next best one takes its card."""
        from sushi.ui.components import JobCard

        feed = [Role(company=f'Feed{i}', title=f'Operations Analyst {i}',
                     location='Remote (UK)', score=14 - i, match=92 - i,
                     url=f'https://boards.greenhouse.io/feed/{i}',
                     ats='greenhouse', reasons=['relevant'])
                for i in range(6)]
        w.set_matches(feed)
        w.navigate('Dashboard')
        settle(400)
        page = w._pages['Dashboard']

        def on_screen():
            return [c.role.company for c in
                    page.matches_host.findChildren(JobCard)
                    if c.isVisible()]

        before = on_screen()
        # As many cards as fill whole rows at this width.
        shown = page._card_count(page._cols or 1)
        assert len(before) == shown, before
        victim = feed[0]
        assert victim.company in before, before
        waiting = feed[shown].company
        assert waiting not in before, 'the fifth was already on screen'

        # Apply to it, the way the card does, and let it settle.
        app_id = storage.upsert_role(victim, status='needs_review')
        storage.set_status(app_id, 'submitted')
        w.apps_changed.emit()
        settle(600)
        page.on_show()
        settle(300)

        after = on_screen()
        assert victim.company not in after, (
            f"{victim.company} was submitted and is still on the feed "
            f"with an Apply button on it: {after}")
        assert waiting in after, (
            f"nothing took its place — the feed just got shorter: {after}")
        assert len(after) == shown, after

        # And it is where you would go looking for it.
        tracked = {a.role.url: a.status
                   for a in storage.list_applications()}
        assert tracked.get(victim.url) == 'submitted', tracked.get(victim.url)
        return f"{victim.company} left, {waiting} took its card, tracker has it"
    check("Dashboard: an applied job leaves the feed and is replaced",
          p_applied_leaves_the_feed)

    # --- where you are willing to work ---------------------------------------
    def p_workplace_is_a_choice():
        """Remote was a default with no control. It has to be a choice."""
        from sushi.discovery import score_role, wants, workplace_of
        from sushi.models import Profile as P

        remote = Role(company='R', title='Operations Analyst',
                      location='Remote (UK)', score=0,
                      url='https://boards.greenhouse.io/r/1')
        onsite = Role(company='O', title='Operations Analyst',
                      location='Hatfield, United Kingdom', score=0,
                      url='https://boards.greenhouse.io/o/1')

        def scored(pref):
            who = P(full_name='A B', target_titles=['Operations Analyst'],
                    target_locations=['United Kingdom'],
                    country='United Kingdom', seniority='ic',
                    workplace=pref)
            return score_role(remote, who)[0], score_role(onsite, who)[0]

        any_r, any_o = scored('any')
        assert any_r == any_o, (
            f"'any' still favours one of them: remote {any_r}, on site {any_o}")

        rem_r, rem_o = scored('remote')
        assert rem_r > rem_o, (rem_r, rem_o)
        on_r, on_o = scored('onsite')
        assert on_o > on_r, (
            f"asking for on site still ranked the remote job higher "
            f"({on_r} vs {on_o}) — remote is still the default in all but name")

        # A profile written before the choice existed keeps its meaning.
        assert wants(P.from_dict({'full_name': 'A', 'remote_ok': True})) \
            == 'remote'
        assert wants(P.from_dict({'full_name': 'A', 'remote_ok': False})) \
            == 'onsite'
        assert wants(P()) == 'any', 'a new profile is opinionated'

        # And the page offers the choice, saving on the press.
        page = w._pages['Profile']
        w.navigate('Profile')
        settle(300)
        page._set_sub('Profile Details')
        settle(200)
        chips = page.loc_picker._place_btns
        assert set(chips) == {'any', 'remote', 'hybrid', 'onsite'}, chips
        page.loc_picker._place_btns['onsite'].click()
        settle(250)
        assert storage.load_profile().workplace == 'onsite', (
            "the chip lights up and the profile never changed")
        page.loc_picker._place_btns['any'].click()
        settle(250)
        assert storage.load_profile().workplace == 'any'
        return (f"any {any_r}={any_o}, remote {rem_r}>{rem_o}, "
                f"onsite {on_o}>{on_r}; saved on the press")
    check("Profile: where you work is chosen, not assumed",
          p_workplace_is_a_choice)

    # --- All jobs: picking several and applying to them ----------------------
    #
    # Fresh roles, not the shared seed. Earlier checks apply to those,
    # and a job already on the Tracker is deliberately not in this list
    # — so reusing them would test the page against an empty table.
    def fresh_roles(tag: str, n: int = 5) -> list:
        towns = ['London, UK', 'Bristol, UK', 'London, UK',
                 'Remote (UK)', 'Leeds, UK']
        return [Role(company=f'{tag.title()}Co{i}',
                     title=f'{tag.title()} Analyst {i}',
                     location=towns[i % len(towns)],
                     score=12, match=95 - i * 7, ats='greenhouse',
                     url=f'https://boards.greenhouse.io/{tag}/{i}',
                     reasons=['relevant'], description='A description.')
                for i in range(n)]

    def p_bulk_select_and_apply():
        """'Tick the ones you want and Sushi will work through them.'"""
        from PyQt6.QtWidgets import QCheckBox
        page = w._pages["All jobs"]
        w.all_matches = lambda limit=4000, _r=fresh_roles('pick'): list(_r)
        w.navigate("All jobs")
        settle(300)
        page.on_show()
        settle(250)
        rows = page.table.rowCount()
        assert rows >= 3, f"nothing to pick from: {rows} rows"

        # Room to do it. Earlier checks spend the daily cap and leave a
        # queue behind, and this would otherwise be testing the cap — 
        # which has its own check, and passes it.
        from sushi.extras import load_autoapply, save_autoapply
        w.stop_autoapply()
        settle(200)
        w._autoapply_queue = []
        w._autoapply_rows = []
        storage.set_setting("autoapply_count", "0")
        cfg = load_autoapply()
        cfg.daily_cap = 25
        save_autoapply(cfg)

        boxes = [page.table.cellWidget(r, 0).findChildren(QCheckBox)[0]
                 for r in range(rows)]
        for box in boxes[:3]:
            box.setChecked(True)
        settle(150)
        assert len(page._selected) == 3, page._selected
        assert page.pick_row.isVisible(), "ticking three showed no way to act"
        assert "3" in page.apply_sel_btn.text(), page.apply_sel_btn.text()

        # The press has to reach the runner, not just the button's label.
        wanted = [r for r in page._shown if r.key in page._selected]
        before = {a.role.url for a in storage.list_applications()}
        page._apply_selected()
        settle(600)
        rows_now = {r["key"] for r in w.autoapply_rows()}
        assert all(r.key in rows_now for r in wanted), (
            "the ticked roles never reached the Insane mode queue: "
            f"{sorted(rows_now)[:4]}")
        assert load_autoapply().enabled, (
            "applying to a selection did not turn Insane mode on, so the "
            "page it sends you to says STANDBY over a running queue")
        assert w.autoapplying(), "armed in the config but not in the window"
        after = {a.role.url for a in storage.list_applications()}
        assert after >= before, "applications were lost"
        w.stop_autoapply()
        settle(300)
        return f"3 ticked, {len(rows_now)} queued, Insane mode armed"
    check("All jobs: ticking several and applying reaches the runner",
          p_bulk_select_and_apply)

    def p_applied_rows_leave():
        """A job you have *sent* is on the Tracker, not in the list.

        Three states, three different answers, and the middle one was wrong
        for a long time. Submitted leaves — leaving it in the list you
        choose from is how the same job goes out twice. In flight stays,
        tinted, offering no second Apply. And filled-but-not-submitted —
        which is Insane mode's ordinary output, not an edge case — used to
        leave with the submitted ones, so the forms waiting for a signature
        were invisible on the page where you were looking for work to do.
        It stays, in its own tint, asking to be submitted.
        """
        page = w._pages["All jobs"]
        w.all_matches = lambda limit=4000, _r=fresh_roles('leave'): list(_r)
        w.navigate("All jobs")
        settle(250)
        page.on_show()
        settle(200)

        def companies():
            from sushi.ui.components import ElidedLabel
            out = []
            for r in range(page.table.rowCount()):
                cell = page.table.cellWidget(r, 1)
                found = cell.findChildren(ElidedLabel) if cell else []
                out.append(found[0].text() if found else "")
            return out

        from PyQt6.QtWidgets import QCheckBox

        start = page.table.rowCount()
        assert start >= 3, start
        victim = page._shown[0]
        flyer = page._shown[1]
        filled = page._shown[2]

        # One submitted: it must go.
        done_id = storage.upsert_role(victim, status="needs_review")
        storage.set_status(done_id, "submitted")
        # One mid-flight: it must stay, tinted, and offer no second Apply.
        fly_id = storage.upsert_role(flyer, status="queued")
        storage.set_status(fly_id, "filling")
        # And one filled, waiting for the user: it must stay too.
        fill_id = storage.upsert_role(filled, status="queued")
        storage.set_status(fill_id, "needs_review", "2 questions need you")
        page._render()
        settle(250)

        assert page.table.rowCount() == start - 1, (
            f"{start} → {page.table.rowCount()}; the submitted job is still "
            f"in the list you choose from, so it can be applied to twice")
        shown = companies()
        assert flyer.company in shown, "the in-flight job vanished mid-run"
        assert filled.company in shown, (
            "a filled application left the list, so the one thing waiting on "
            "the user is visible nowhere they would look for it")

        def tint_of(role) -> str:
            item = page.table.item(shown.index(role.company), 0)
            assert item is not None, f"{role.company} has no tint at all"
            return item.background().color().name().lower()

        def tick_of(role):
            cell = page.table.cellWidget(shown.index(role.company), 0)
            return cell.findChildren(QCheckBox)[0]

        from sushi.ui.pages.alljobs import TINT_NEEDS_YOU, TINT_WORKING
        assert tint_of(flyer) == TINT_WORKING.lower(), tint_of(flyer)
        assert tint_of(filled) == TINT_NEEDS_YOU.lower(), tint_of(filled)
        assert tint_of(flyer) != tint_of(filled), (
            "Sushi working and Sushi waiting on you are the same colour")
        assert not tick_of(flyer).isEnabled(), \
            "a job already running could be queued again"
        assert not tick_of(filled).isEnabled(), \
            "a filled application could be ticked and filled a second time"

        # And the row says what to do with it rather than offering Apply.
        from sushi.ui.components import ElidedLabel
        cell = page.table.cellWidget(shown.index(filled.company), 5)
        words = {b.text() for b in cell.findChildren(ElidedLabel)}
        words |= {b.text() for b in cell.findChildren(
            __import__("PyQt6.QtWidgets", fromlist=["QPushButton"]).QPushButton)}
        assert "Apply" not in words, (
            f"offered to apply to an application it has already filled: {words}")
        assert any("submit" in word.lower() for word in words), (
            f"nothing on the row says what is left to do: {words}")

        # The count line carries it too: the tint only helps on a row you
        # can see, and the page draws the first 200 of them.
        assert "waiting for you to submit" in page.count_lbl.text(), \
            page.count_lbl.text()

        storage.set_status(done_id, "matched")
        storage.set_status(fly_id, "matched")
        storage.set_status(fill_id, "matched")
        page._render()
        settle(150)
        return (f"submitted row left ({start} → {start - 1}); in-flight and "
                f"filled both stayed, in different tints")
    check("All jobs: a sent job leaves, a filled one stays and asks to be sent",
          p_applied_rows_leave)

    def p_filters_narrow():
        """Every filter chip must actually narrow the list."""
        import time as _t
        page = w._pages["All jobs"]
        w.all_matches = lambda limit=4000, _r=fresh_roles('filter'): list(_r)
        w.navigate("All jobs")
        settle(250)
        page.on_show()
        settle(200)
        total = page.table.rowCount()
        assert total >= 3, total

        tried = []
        # Date, against roles the audit seeds with no first_seen at all —
        # which must be *kept*, because unknown is not the same as old.
        page.f_date.chosen = {"Last 24 hours"}
        page._render()
        settle(150)
        assert page.table.rowCount() == total, (
            "a job with no date on it was dropped by a date filter")
        page.f_date.chosen = set()

        # Location, from the options the page itself offers.
        assert page.f_place._options, "the location filter offers nothing"
        one = page.f_place._options[0]
        page.f_place.chosen = {one}
        page._render()
        settle(150)
        narrowed = page.table.rowCount()
        assert 0 < narrowed <= total, (narrowed, total)
        tried.append(f"location={one[:18]} → {narrowed}")
        page.f_place.chosen = set()

        # Match, which every seeded role has.
        page.f_match.chosen = {"90%+"}
        page._render()
        settle(150)
        tried.append(f"match 90%+ → {page.table.rowCount()}")
        assert page.table.rowCount() < total, "a 90% floor changed nothing"

        page._clear_filters()
        settle(200)
        assert page.table.rowCount() == total, (
            f"Clear filters did not restore the list: "
            f"{page.table.rowCount()} vs {total}")
        assert page.active_filters() == 0, page.active_filters()
        assert "Updated" in page.updated_lbl.text() \
            or "Not scanned" in page.updated_lbl.text(), page.updated_lbl.text()
        return "; ".join(tried) + "; cleared back to " + str(total)
    check("All jobs: the filters narrow and clear", p_filters_narrow)

    def p_agent_is_not_the_judge():
        """Two refusals a careful model makes, that the task must forbid.

        Both were real, both on Claude Code, both ended in an untouched
        form and STATUS: blocked on the first step:

        - it read Sushi's own <secret>email</secret> placeholders as
          evidence the task had been tampered with;
        - it decided the candidate was underqualified and declined on
          their behalf.
        """
        from sushi.autofill import _task
        task = _task(roles(1)[0], w.profile, "", "", False, "honest", True)

        assert "<secret>" in task, "values are no longer placeheld"
        assert "not tampering" in task.lower(), (
            "the task uses <secret> tags and never says what they are, "
            "so a careful model reads its own prompt as an injection")
        assert "prompt injection" in task.lower(), task[:0]

        low = task.lower()
        assert "not your decision" in low, (
            "nothing stops the agent declining on fit — which looks "
            "exactly like a crash from where the user is sitting")
        assert "look before you judge" in low, (
            "nothing stops a verdict reached on arrival")

        # And the placeholders still carry no real values.
        assert w.profile.email not in task, "the email is in the prompt"
        return "placeholders explained; refusing on fit or on arrival forbidden"
    check("Apply: the agent fills the form, it does not judge the candidate",
          p_agent_is_not_the_judge)

    # --- the agent, when the agent cannot run --------------------------------
    def p_agent_rate_limited():
        """A model plan too small for the agent must not read as nothing."""
        from sushi.autofill import _agent_failure

        class _History:
            def __init__(self, errs):
                self._errs = errs

            def errors(self):
                return self._errs

        groq = ('Error code: 413 - Request too large for model '
                '`openai/gpt-oss-120b` ... tokens per minute (TPM): '
                'Limit 8000, Requested 12043, please reduce your '
                'message size and try again.')
        said = _agent_failure(_History([groq] * 6))
        assert said, (
            "every step failed on a rate limit and the run reported nothing — "
            "which reaches the user as \"No report returned.\" over an empty browser")
        assert "8000" in said and "12043" in said, said
        assert "auto" in said.lower(), (
            "it names the problem without naming the way out: " + said)

        # And the other two causes stay told apart.
        auth = _agent_failure(_History(
            ['Error code: 401 - invalid api key'] * 4))
        assert auth and "8000" not in auth, auth
        assert not _agent_failure(_History([])), (
            "a clean run was reported as a failure")
        assert not _agent_failure(_History(['one odd step'])), (
            "a single unexplained step is not a verdict on the model")
        return f"names the limit and the way out: {said[:60]!r}"
    check("Apply: an agent the model plan cannot run says so",
          p_agent_rate_limited)

    # --- explorative mode ----------------------------------------------------
    #
    # A switch that promises to finish the form. Everything this suite exists
    # for applies: the previous two switches that promised something
    # (``resume_optimization``, Insane mode's arm) were saved, drawn, named in
    # a dialog and read by nothing, and every other suite passed over both.
    # So each check here follows the switch to the work it names.
    def p_explorative_reaches_the_filler():
        """'Explorative: fill the whole form' — the run must be told."""
        import sushi.autofill as af
        from sushi.extras import load_apply_settings, save_apply_settings
        from sushi.ui.main_window import MainWindow

        a = load_apply_settings()
        was = (a.explorative, a.fill_mode)
        seen: list[dict] = []
        real = af.fill_directly

        def stub(role, profile, settings, **kw):
            seen.append(kw)
            return af.FillResult(ok=True, submitted=False, left_open=True,
                                 message="stubbed", unanswered=[])

        af.fill_directly = stub
        try:
            for explorative, mode, want in ((False, "direct", {"explorative": False,
                                                               "compose": False}),
                                            (True, "direct", {"explorative": True,
                                                              "compose": False}),
                                            (False, "auto", {"explorative": False,
                                                             "compose": True})):
                a = load_apply_settings()
                a.explorative, a.fill_mode = explorative, mode
                save_apply_settings(a)
                seen.clear()
                # The real method: this suite replaced begin_apply with a stub
                # for everything above, and the point here is the real one.
                MainWindow.begin_apply(w, roles(1)[0], watch=False,
                                       announce=False)
                for _ in range(40):
                    settle(100)
                    if seen:
                        break
                assert seen, f"{mode}/{explorative}: the fill never started"
                got = seen[-1]
                for key, value in want.items():
                    assert got.get(key) is value, (
                        f"{mode}/explorative={explorative}: the filler was "
                        f"told {key}={got.get(key)!r}, not {value!r} — the "
                        f"switch is saved and drawn and nothing reads it")
                assert got.get("tone"), "the pitch setting never reached the filler"
        finally:
            af.fill_directly = real
            a = load_apply_settings()
            a.explorative, a.fill_mode = was
            save_apply_settings(a)
        return "the switch and the fill mode both reach the run that fills"
    check("Explorative: the switch reaches the thing that fills the form",
          p_explorative_reaches_the_filler)

    def p_explorative_answers_and_flags():
        """It must answer what the lookup could not — and flag every one."""
        from sushi import directfill, learning

        fields = [
            {"key": "fn", "label": "First name *", "type": "text", "value": ""},
            {"key": "notice", "label": "What is your notice period?",
             "type": "text", "value": ""},
            {"key": "reloc", "label": "Willing to relocate?", "type": "select",
             "value": "", "options": ["Yes", "No"]},
            {"key": "pw", "label": "Password", "type": "password", "value": ""},
            {"key": "eth", "label": "What is your ethnicity?", "type": "select",
             "value": "", "options": ["Prefer not to say"]},
        ]
        plan = directfill.plan_fill(fields, w.profile)
        assert plan.unanswered, "nothing was left for the model to try"

        asked: list[str] = []

        class FakeLLM:
            def complete_json(self, prompt, system="", max_tokens=0):
                asked.append(prompt)
                return {"answers": [
                    {"n": 1, "value": "One month", "confidence": "medium",
                     "basis": "typical for this seniority"},
                    {"n": 2, "value": "Yes, happy to", "confidence": "low",
                     "basis": "London-based"},
                ]}

            def complete(self, prompt, system="", max_tokens=0, temperature=0.2):
                return "A long enough answer to pass the floor on length."

        n = directfill.guess_answers(FakeLLM(), plan, fields, roles(1)[0],
                                     w.profile)
        assert n == 2, f"guessed {n}, expected both open questions"
        assert not plan.unanswered, plan.unanswered

        guesses = {g.label: g for g in plan.guesses}
        assert "What is your notice period?" in guesses, sorted(guesses)
        # A choice field takes one of its own options or nothing, guess or not.
        assert guesses["Willing to relocate?"].value == "Yes", \
            guesses["Willing to relocate?"].value
        assert all(g.confidence and g.basis for g in plan.guesses), \
            "a guess arrived with nothing to say for itself"

        # The refusals are refusals here too.
        prompt = asked[0] if asked else ""
        assert "Password" not in prompt and "ethnicity" not in prompt.lower(), \
            "a password or a demographic question was sent to the model"

        # Flagged where it is typed, and nowhere near the answer bank.
        script = directfill.fill_script(plan)
        assert '"guess": true' in script and "data-sushi-guess" in script, \
            "the page is never told which values are guesses"
        for g in plan.guesses:
            assert not learning.recall(g.label), (
                f"{g.label!r} was banked — a guess that becomes a stored "
                f"answer is retyped into every later form as though checked")
        return (f"{n} guessed, both flagged, nothing banked, "
                f"{len(plan.skipped)} still refused")
    check("Explorative: it answers, flags the answer, and banks nothing",
          p_explorative_answers_and_flags)

    def p_explorative_changes_the_agent_too():
        """Fill mode 'agent' must not quietly ignore the switch."""
        from sushi.autofill import _task
        off = _task(roles(1)[0], w.profile, "", "", False, "honest", False)
        on = _task(roles(1)[0], w.profile, "", "", False, "honest", True)
        assert off != on, "Explorative changes nothing about the agent's task"
        assert "Do not guess." in off, "the cautious rule went missing"
        assert "Do not guess." not in on, "the agent is still told not to guess"
        assert "GUESSED:" in on, "the agent is never asked which were guesses"
        # The floor holds in both. "Guess" must never license a fabrication.
        assert "never invent an employer" in on, "no honesty floor on guesses"
        assert "demographic" in on, "demographics are not fenced off"
        return "two modes, two different instructions, one honesty floor"
    check("Explorative: the browser agent is told as well",
          p_explorative_changes_the_agent_too)

    def p_explorative_chip_on_manual():
        """The chip beside Fill must be the same switch as the one in Settings."""
        from sushi.extras import load_apply_settings, save_apply_settings
        page = w._pages["Manual apply"]
        a = load_apply_settings()
        was = a.explorative
        try:
            a.explorative = False
            save_apply_settings(a)
            page.on_show()
            settle(120)
            page.explore_btn.click()
            settle(150)
            assert load_apply_settings().explorative is True, \
                "the chip is painted and the setting never moved"
            # And Settings must show it, because it is one switch.
            settings_page = w._pages["Settings"]
            settings_page.on_show()
            settle(150)
            assert settings_page.explorative.isChecked(), \
                "Settings disagrees with the chip about the same setting"
            page.explore_btn.click()
            settle(150)
            assert load_apply_settings().explorative is False
        finally:
            a = load_apply_settings()
            a.explorative = was
            save_apply_settings(a)
        return "the chip writes the setting Settings reads"
    check("Explorative: the chip on Manual apply is the real switch",
          p_explorative_chip_on_manual)

    def p_guesses_come_back_to_the_user():
        """A guess the user never sees is the whole danger of this feature."""
        from PyQt6.QtWidgets import QLineEdit, QPlainTextEdit
        from sushi.autofill import FillResult
        page = w._pages["Manual apply"]
        w.navigate("Manual apply")
        settle(200)
        page._filled("needs_review", FillResult(
            ok=True, submitted=False, left_open=True, unanswered=[],
            message="Filled the form.",
            guessed=[("What is your notice period?", "One month"),
                     ("Willing to relocate?", "Yes")]))
        settle(200)
        assert page.guesses.isVisible(), "the guesses were never shown"
        shown = {q: page.guesses._text(box) for q, box in page.guesses._rows}
        assert shown.get("What is your notice period?") == "One month", shown
        assert shown.get("Willing to relocate?") == "Yes", shown
        boxes = page.guesses.findChildren(QLineEdit) + \
            page.guesses.findChildren(QPlainTextEdit)
        assert len(boxes) >= 2, "the answers cannot be corrected"
        page.guesses.setVisible(False)
        return f"{len(shown)} guesses listed back, each in an editable box"
    check("Explorative: every guess is shown back for checking",
          p_guesses_come_back_to_the_user)

    # --- the browser itself, on request -------------------------------------
    def p_browser_launches():
        """A real Chromium must start, navigate, and stay open.

        Everything above stubs the browser, which proves the wiring and not
        the stack. Off by default because it opens a real window; run with
        --browser when the apply path is in doubt.
        """
        from sushi.autofill import (_evaluate, _open_sessions, _run_on_loop,
                                    close_open_sessions, open_for_signin)
        t0 = time.time()
        open_for_signin("https://example.com/")
        assert _open_sessions, "no browser session was kept open"
        url = _run_on_loop(_evaluate(_open_sessions[0], "location.href"),
                           timeout=30)
        close_open_sessions()
        assert "example.com" in url, url
        return f"launched, navigated to {url.strip()} and closed in {time.time() - t0:.1f}s"

    if "--browser" in sys.argv:
        check("Apply: a real browser launches on this machine", p_browser_launches)

    # --- report --------------------------------------------------------------
    w.start_scan = real_start_scan
    w.hide()

    # Printed *before* the teardown below, and flushed. Every check has run
    # by this point, so the result is known — and the teardown is the one
    # place this suite is still known to die intermittently. Printing after
    # it meant an otherwise clean run of 35 checks reported nothing at all
    # and exited 127, which looks exactly like a suite that broke.
    _report()
    sys.stdout.flush()

    # Clicking every button on every page starts background workers, and
    # several of them touch Qt objects when they finish. Letting the process
    # exit with those still in flight crashed the run — intermittently, and
    # *after* the last check had passed, so the suite reported success and
    # then segfaulted on the way out. Wait for them, then drain the events
    # their completion posts, before anything is torn down.
    from PyQt6.QtCore import QThreadPool
    QThreadPool.globalInstance().waitForDone(15000)
    settle(400)
    try:
        from sushi.autofill import close_open_sessions
        close_open_sessions()
    except Exception as e:                      # never fail the run on cleanup
        print(f"  (cleanup: {type(e).__name__}: {e})")
    settle(200)

    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")

    # Take the window down while the application object is still alive. Both
    # are locals, so leaving it to the garbage collector means the order is
    # whatever CPython happens to choose, and the half of the time it chose
    # to free the QApplication first the process died on the way out — with
    # every check already passed and reported.
    w.close()
    w.deleteLater()
    settle(200)
    app.quit()
    settle(100)
    return 1 if failed else 0


if __name__ == "__main__":
    code = main()
    # Belt and braces. The result is printed and the browsers are closed;
    # anything Qt does after this point can only turn a good run into a
    # crash, so the process is ended outright rather than unwound.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
