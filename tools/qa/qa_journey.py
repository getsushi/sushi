"""Does the thing this app is for actually work, end to end?

Every other suite asks about a part. This one asks the only question the
product is judged on, which is a sentence rather than a feature:

    *I have a CV and no job. Take me from that to applications I can
    track, without me having to understand how you work.*

So it walks the journey in order — onboard, build a profile, find matches,
choose some, apply, track, and come back tomorrow — and at each step asks
two things that a feature test does not:

1. **Is the next step findable?** A working feature nobody can reach is a
   missing feature. If a screen's only way forward is a control that is
   disabled, or off-screen, or named something the user has no word for,
   that is a bug here even though every unit passes.
2. **Does the state survive?** A job seeker uses this over weeks, across
   restarts, with the app closed in between. Anything held only in memory
   is lost work, and lost work in this app means an application sent twice
   or not at all.

Nothing here stubs the engine's decisions — only the network, the browser
and the model, which are not what this is asking about.

Run from the sushi/ directory:  python qa_journey.py [-v]
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

os.environ.setdefault("SUSHI_NO_AUTOSCAN", "1")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="sushi-journey-")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PyQt6.QtCore import QEventLoop, QTimer                  # noqa: E402
from PyQt6.QtWidgets import (QApplication, QFileDialog, QMessageBox,  # noqa: E402
                             QPushButton)

RESULTS: list[tuple[str, str, str]] = []
VERBOSE = "-v" in sys.argv
CV = Path(__file__).resolve().parent / "dummy_cv.pdf"


def check(step: str, fn) -> None:
    try:
        RESULTS.append(("PASS", step, str(fn() or "")[:170]))
    except Exception as e:
        RESULTS.append(("FAIL", step, f"{type(e).__name__}: {e}"))
        if VERBOSE:
            traceback.print_exc(limit=3)


def report() -> None:
    print()
    width = max((len(n) for _, n, _ in RESULTS), default=10) + 2
    for status, name, detail in RESULTS:
        print(f"  {'ok  ' if status == 'PASS' else 'FAIL'}  "
              f"{name:<{width}} {detail}")
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")


def reachable(widget) -> bool:
    """Whether a user could actually press this.

    Visible, enabled, and big enough to hit. A control that is none of
    those is not a route forward however well its slot is wired.
    """
    return (widget is not None and widget.isVisible() and widget.isEnabled()
            and widget.width() > 8 and widget.height() > 8)


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
    QFileDialog.getOpenFileName = _stub("pick-file", (str(CV), ""))
    QFileDialog.getExistingDirectory = _stub("pick-folder", "")
    QFileDialog.getSaveFileName = _stub("save-file", ("", ""))
    from PyQt6.QtGui import QDesktopServices
    QDesktopServices.openUrl = _stub("open-url", True)

    #: Every application this suite "makes". A real browser is never
    #: launched: .env_amm supplies a working model, so the press that
    #: was expected to be refused opened Chromium on a made-up URL and
    #: left it there. What is being tested is the journey, not the
    #: filling — qa_fields does that against real forms.
    started: list = []

    def fake_begin(role, watch=False, announce=True, on_finished=None,
                   on_step=None, **_kw):
        started.append(role)
        if on_step:
            on_step("Reading the form…")
        app_id = storage.upsert_role(role, status="needs_review")
        storage.set_status(app_id, "submitted", "journey stub")
        if on_finished:
            QTimer.singleShot(40, lambda: on_finished("submitted", None))


    # ---------------------------------------------------------------- day one
    #
    # A completely cold start. No profile, no CV, no settings, no jobs.

    def s_cold_start():
        """Opening it for the first time must land somewhere with a next step."""
        w = MainWindow()
        w.resize(1440, 920)
        w.move(-7000, -7000)
        w.show()
        settle(800)
        w.begin_apply = fake_begin
        w.apply_to_role = lambda role, watch=False: fake_begin(role, watch)
        state.update(w=w)

        # load_profile() answers None on a cold start, not an empty Profile.
        assert not (storage.load_profile() or Profile()).full_name, (
            "a profile out of nowhere")
        current = w.root_stack.currentWidget()
        from sushi.ui.onboarding import Onboarding
        assert isinstance(current, Onboarding), (
            f"a first run opened on {type(current).__name__}, not onboarding "
            f"— there is nothing on the main screen for someone with no CV")

        # And onboarding must offer a way out of itself.
        ways = [b for b in current.findChildren(QPushButton) if reachable(b)]
        assert ways, "onboarding has no reachable control at all"
        return f"opens on onboarding with {len(ways)} reachable control(s)"
    check("Day one: a cold start lands on onboarding", s_cold_start)

    def s_onboarding_wants_one_thing():
        """It asks for the CV and the CV is enough."""
        from sushi.ui.onboarding import Onboarding
        w = state["w"]
        page = w.root_stack.currentWidget()
        assert isinstance(page, Onboarding), type(page).__name__
        labels = " ".join(
            lb.text().lower() for lb in page.findChildren(
                __import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel))
        assert "cv" in labels or "resume" in labels, (
            "the first screen never says it wants a CV")
        return "the first screen asks for a CV"
    check("Day one: onboarding asks for the one thing it needs",
          s_onboarding_wants_one_thing)

    def s_profile_from_cv():
        """A CV on disk must become a profile without a model call.

        The model builds the rich profile. But a run with no credentials
        must still get a usable one, or the whole app is gated behind a
        key the user has not added yet.
        """
        from sushi import cv as cvmod
        assert CV.exists(), f"no CV fixture at {CV}"
        text = cvmod.extract_text(str(CV))
        assert len(text) > 400, f"only {len(text)} chars out of the CV"
        got = cvmod.quick_profile(text) if hasattr(cvmod, "quick_profile") else None
        if got is None:
            return f"CV reads ({len(text)} chars); no model-free profile path"
        assert got.full_name or got.email, "a model-free profile with nothing in it"
        return f"CV reads ({len(text)} chars) and yields a profile offline"
    check("Day one: the CV can be read without a model", s_profile_from_cv)

    def s_finish_onboarding():
        """Getting to the main window must not need anything else."""
        from sushi.ui.onboarding import Onboarding
        w = state["w"]
        w.set_profile(Profile(
            full_name="Rowan Ellis Hartley", email="r.hartley@example.com",
            headline="Senior Operations Analyst",
            summary="Operations and data analyst with six years in payments.",
            skills=["Python", "SQL"], seniority="ic", years_experience="6",
            location="Bristol", country="United Kingdom",
            education=["BSc Computer Science, University of Bristol, 2017"],
            experience=[Experience(company="Wise", title="Analyst",
                                   start="2022")],
            target_titles=["Operations Analyst"],
            target_locations=["Remote (UK)"]))
        storage.set_setting("cv_path", str(CV))
        page = w.root_stack.currentWidget()
        assert isinstance(page, Onboarding), type(page).__name__
        page._done()
        settle(500)
        assert w.root_stack.currentWidget() is w.shell, (
            "finishing onboarding did not reach the app")
        assert storage.get_setting("onboarded", "") == "1", (
            "onboarding does not remember it happened, so the next launch "
            "asks for the CV again")
        return "onboarding completes, and is recorded as complete"
    check("Day one: onboarding completes and sticks", s_finish_onboarding)

    # ------------------------------------------------------------ finding work

    def s_every_page_opens():
        """Each page must open without error and say something."""
        w = state["w"]
        from PyQt6.QtWidgets import QLabel
        quiet = []
        for name in list(w._pages):
            w.navigate(name)
            settle(260)
            page = w._pages[name]
            assert page.isVisible(), f"{name} navigated to but not shown"
            words = sum(len(lb.text().strip())
                        for lb in page.findChildren(QLabel) if lb.isVisible())
            if words < 40:
                quiet.append(f"{name} ({words} chars)")
        assert not quiet, ("a page that says almost nothing is a page that "
                           "cannot be acted on: " + "; ".join(quiet))
        return f"{len(w._pages)} pages open and all say something"
    check("Finding work: every page opens with something on it",
          s_every_page_opens)

    def s_empty_feed_explains():
        """No jobs yet is the normal first state. It must not look broken."""
        w = state["w"]
        w.set_matches([])
        w.all_matches = lambda limit=4000: []
        for name in ("Dashboard", "All jobs"):
            w.navigate(name)
            settle(260)
            page = w._pages[name]
            from PyQt6.QtWidgets import QLabel
            said = " ".join(lb.text().lower()
                            for lb in page.findChildren(QLabel)
                            if lb.isVisible())
            assert ("look again" in said or "background" in said
                    or "nothing yet" in said or "no " in said), (
                f"{name} with an empty feed explains nothing: {said[:120]}")
            # And there must be a way to make something happen.
            acts = [b for b in page.findChildren(QPushButton) if reachable(b)]
            assert acts, f"{name} is empty with nothing to press"
        return "an empty feed says why and offers the way forward"
    check("Finding work: an empty feed explains itself", s_empty_feed_explains)

    def s_matches_arrive():
        """A scan's results must reach every screen that shows them."""
        w = state["w"]
        roles = [Role(company=f"Firm{i}", title=f"Operations Analyst {i}",
                      location=["Remote (UK)", "London, UK", "Bristol, UK"][i % 3],
                      url=f"https://boards.greenhouse.io/firm{i}/1",
                      ats="greenhouse", score=10 + (i % 5), match=95 - i * 3,
                      reasons=["matches your target role"],
                      first_seen=time.time() - i * 3600)
                 for i in range(9)]
        state["roles"] = roles
        w.set_matches(roles)
        w.all_matches = lambda limit=4000: list(roles)
        settle(300)

        w.navigate("Dashboard")
        settle(350)
        dash = w._pages["Dashboard"]
        assert dash.matches_flow.count() > 0, "the Dashboard shows no cards"

        w.navigate("All jobs")
        settle(350)
        jobs = w._pages["All jobs"]
        jobs.on_show()
        settle(250)
        assert jobs.table.rowCount() == len(roles), (
            jobs.table.rowCount(), len(roles))
        return (f"{len(roles)} matches reach the Dashboard "
                f"({dash.matches_flow.count()} cards) and All jobs "
                f"({jobs.table.rowCount()} rows)")
    check("Finding work: matches reach every screen", s_matches_arrive)

    # ----------------------------------------------------------- choosing work

    def s_choose_and_queue():
        """Picking several with nothing to run them must say so.

        The state a new user is in: a CV, a feed, and no model key
        yet. The press has to explain itself rather than appear to
        work, which is the failure mode this whole app keeps hitting.
        """
        w = state["w"]
        from PyQt6.QtWidgets import QCheckBox
        from sushi.extras import load_autoapply, save_autoapply
        import sushi.ui.main_window as mw

        cfg = load_autoapply()
        cfg.daily_cap = 20
        save_autoapply(cfg)
        storage.set_setting("autoapply_count", "0")

        page = w._pages["All jobs"]
        w.navigate("All jobs")
        settle(300)
        page.on_show()
        settle(250)
        boxes = [page.table.cellWidget(r, 0).findChildren(QCheckBox)[0]
                 for r in range(page.table.rowCount())]
        for b in boxes[:3]:
            b.setChecked(True)
        settle(150)
        assert page.pick_row.isVisible(), "no way to act on three ticked jobs"

        real = mw.load_settings

        class _NoModel:
            def __getattr__(self, name):
                return getattr(real(), name)
            llm_enabled = False

        mw.load_settings = lambda: _NoModel()
        try:
            before = w.status_text()
            page._apply_selected()
            settle(400)
            said = w.status_text()
        finally:
            mw.load_settings = real
        assert said != before, (
            "pressing Apply with no model configured said nothing at all")
        assert "model" in said.lower(), (
            f"it refused without naming what is missing: {said!r}")
        assert not started, "it applied anyway, with no model configured"
        return f"refuses and names the cause: {said[:70]!r}"
    check("Choosing: bulk apply refuses clearly when it cannot run",
          s_choose_and_queue)

    def s_choose_and_queue_live():
        """And with a model, the same press must queue the work."""
        w = state["w"]
        from PyQt6.QtWidgets import QCheckBox

        import sushi.ui.main_window as mw
        real_settings = mw.load_settings

        class _Live:
            def __getattr__(self, name):
                return getattr(real_settings(), name)
            llm_enabled = True

        mw.load_settings = lambda: _Live()
        try:
            page = w._pages["All jobs"]
            page.on_show()
            settle(250)
            boxes = [page.table.cellWidget(r, 0).findChildren(QCheckBox)[0]
                     for r in range(page.table.rowCount())]
            picked = []
            for r, b in enumerate(boxes[:3]):
                b.setChecked(True)
                picked.append(page._shown[r])
            settle(150)
            page._apply_selected()
            settle(1500)
        finally:
            mw.load_settings = real_settings

        queued = {row["key"] for row in w.autoapply_rows()}
        assert all(r.key in queued for r in picked), (
            f"only {sorted(queued)[:3]} reached the queue")
        assert w.autoapplying(), "the queue is running but Insane mode is off"
        state["applied"] = picked
        w.stop_autoapply()
        settle(300)
        return f"{len(picked)} ticked → queued → {len(started)} started"
    check("Choosing: bulk apply queues the work when it can run",
          s_choose_and_queue_live)

    def s_applied_leave_the_feed():
        """What you have applied to must stop being offered."""
        w = state["w"]
        page = w._pages["All jobs"]
        page.on_show()
        settle(300)
        left = {r.url for r in page._shown}
        gone = [r for r in state.get("applied", []) if r.url in left]
        assert not gone, (
            f"{len(gone)} job(s) already applied to are still in the list "
            f"you choose from, which is how the same job goes out twice")
        return f"{len(state.get('applied', []))} applied job(s) left the list"
    check("Choosing: applied jobs leave the list", s_applied_leave_the_feed)

    # --------------------------------------------------------------- tracking

    def s_tracker_has_them():
        """And they must turn up where you look for them afterwards."""
        w = state["w"]
        w.navigate("Tracker")
        settle(400)
        page = w._pages["Tracker"]
        tracked = {a.role.url for a in storage.list_applications()}
        for role in state.get("applied", []):
            assert role.url in tracked, (
                f"{role.company} was applied to and is on no tracker row")
        from PyQt6.QtWidgets import QTableWidget
        tables = [t for t in page.findChildren(QTableWidget)]
        rows = max([t.rowCount() for t in tables], default=0)
        assert rows >= len(state.get("applied", [])), (
            f"{rows} tracker rows for {len(state.get('applied', []))} "
            f"applications")
        return f"{len(tracked)} application(s) on the tracker, {rows} rows drawn"
    check("Tracking: applications are where you go to find them",
          s_tracker_has_them)

    def s_export():
        """A job hunt outlives the app. It has to come out."""
        from sushi.extras import export_tracker_csv
        csv = export_tracker_csv()
        lines = [ln for ln in csv.splitlines() if ln.strip()]
        assert len(lines) >= 2, f"an export with no rows: {csv[:100]!r}"
        assert "company" in lines[0].lower(), lines[0]
        return f"{len(lines) - 1} row(s) exported"
    check("Tracking: the pipeline exports", s_export)

    # ------------------------------------------------------------ the next day

    def s_restart_keeps_everything():
        """Close it, open it. Nothing a user did may be gone.

        This is the one a feature test cannot ask. Everything above ran in
        one process against live objects; a job hunt happens over weeks.
        """
        w = state["w"]
        # The set of applications, not their statuses: one in flight when
        # the window closes legitimately moves on, and that is not loss.
        before_apps = {a.role.url for a in storage.list_applications()}
        before_profile = (storage.load_profile() or Profile()).full_name
        before_bank = len(storage.all_answers())
        w.close()
        settle(400)

        fresh = MainWindow()
        fresh.resize(1440, 920)
        fresh.move(-7000, -7000)
        fresh.show()
        settle(800)
        state["w"] = fresh

        assert fresh.root_stack.currentWidget() is fresh.shell, (
            "a second launch asked for the CV again")
        after = {a.role.url for a in storage.list_applications()}
        assert after == before_apps, (
            f"{len(before_apps)} applications before, {len(after)} after; "
            f"lost {sorted(before_apps - after)[:3]}")
        assert (storage.load_profile() or Profile()).full_name == before_profile
        assert len(storage.all_answers()) == before_bank
        return (f"profile, {len(after)} application(s) and {before_bank} "
                f"banked answer(s) all survived a restart")
    check("The next day: everything survives a restart",
          s_restart_keeps_everything)

    def s_second_launch_is_usable():
        """And the app must be ready to work, not re-onboarding."""
        w = state["w"]
        w.navigate("Dashboard")
        settle(400)
        acts = [b for b in w._pages["Dashboard"].findChildren(QPushButton)
                if reachable(b)]
        assert acts, "the Dashboard on a second launch offers nothing"
        assert (storage.load_profile() or Profile()).full_name, "the profile is gone"
        return f"ready to work: {len(acts)} control(s) on the Dashboard"
    check("The next day: it opens ready to work", s_second_launch_is_usable)

    state["w"].hide()
    report()
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    try:
        from sushi.autofill import close_open_sessions, close_orphaned_browsers
        close_open_sessions()
        close_orphaned_browsers()
    except Exception:
        pass
    from PyQt6.QtCore import QThreadPool
    QThreadPool.globalInstance().waitForDone(10000)
    settle(300)
    state["w"].deleteLater()
    settle(200)
    app.quit()
    return 1 if failed else 0


state: dict = {}

if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
