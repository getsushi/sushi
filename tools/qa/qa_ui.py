"""QA for the UI itself: click things and check the app reacts.

The other suites test the engine. This one drives the widgets — navigation,
the search filter, status tabs, Save/Pass on a card, the Insane mode button,
the tracker's stage picker, settings persistence, onboarding — because those
are the paths a user actually takes and none of them are covered by importing
a module.

Runs against a scratch database (``LOCALAPPDATA`` is redirected before any
sushi import) with the window parked off-screen on the real platform, since
the offscreen plugin has no fonts on this machine.

Run from the sushi/ directory:  python qa_ui.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

os.environ.setdefault("SUSHI_NO_AUTOSCAN", "1")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="sushi-uiqa-")

from PyQt6.QtCore import QEventLoop, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []
CV = Path(__file__).resolve().parent / "dummy_cv.pdf"


def check(name: str, fn):
    try:
        detail = fn()
        RESULTS.append(("PASS", name, str(detail)[:150] if detail else ""))
    except Exception as e:
        RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"))
        traceback.print_exc(limit=3)


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

    matches = [
        Role(company="Kestrel", title="Senior Operations Analyst", location="London",
             url="https://example.com/jobs/kestrel-ops-analyst", score=17,
             reasons=["Route optimisation"], description="Long description."),
        Role(company="Wise", title="Staff Data Analyst", location="Remote (EU)",
             url="https://boards.greenhouse.io/wise/jobs/2", score=13,
             reasons=["Payments ops"]),
        Role(company="Cleo", title="Analytics Lead", location="London",
             url="https://boards.greenhouse.io/cleo/jobs/3", score=11,
             reasons=["Broad ownership"]),
    ]

    # --- onboarding --------------------------------------------------------
    # A real first run: no CV read yet. A saved profile now counts as setup
    # done, so it is saved after the window opens.
    storage.set_setting("onboarded", "")
    w = MainWindow()
    storage.save_profile(profile)
    storage.set_setting("cv_path", str(CV))
    storage.adopt_legacy_cv(profile)
    w._profile = storage.load_profile()
    w.resize(1440, 920)
    w.move(-7000, -7000)
    w.show()
    settle(600)

    # No real browser from this suite, ever.
    #
    # Arming Insane mode below genuinely arms it, which genuinely starts an
    # application — and Manual apply forces a *visible* window on purpose,
    # so running these checks threw a Chrome up on the user's desktop at a
    # placeholder URL and left it there, because Sushi leaves filled forms
    # open by design. Every one of those also strands a Chromium holding
    # the browser profile, and Chromium allows one process per profile: a
    # few runs of this file and the next real application cannot start.
    #
    # Nothing here is weakened by the stub. These checks are about the
    # widgets — the flag, the badge, the console text, the table. Whether
    # arming reaches a browser is qa_audit's promise, and it asserts on it
    # there with the same stub.
    launched: list[dict] = []

    def fake_begin(role, watch=False, announce=True, on_finished=None,
                   on_step=None):
        launched.append({"role": role, "watch": watch})
        if on_step:
            on_step("Reading the form…")
        app_id = storage.upsert_role(role, status="needs_review")
        storage.set_status(app_id, "needs_review", "filled by the ui-qa stub")
        if on_finished:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(40, lambda: on_finished("needs_review", None))

    w.begin_apply = fake_begin
    w.apply_to_role = lambda role, watch=False: fake_begin(role, watch)

    def t_onboarding_shows():
        from sushi.ui.onboarding import Onboarding
        cur = w.root_stack.currentWidget()
        assert isinstance(cur, Onboarding), type(cur).__name__
        return "first run opens onboarding"
    check("onboarding: shown on first run", t_onboarding_shows)

    def t_onboarding_cv():
        flow = w.root_stack.currentWidget()
        flow.set_cv(str(CV))
        settle(150)
        assert CV.name in flow.cv_lbl.text(), flow.cv_lbl.text()
        # There is no "Read it" any more: a CV is read as soon as there is a
        # model to read it with. This suite runs without one, so the card
        # should be sitting there offering a different file and nothing else.
        assert flow.pick_btn.text() == "Replace", flow.pick_btn.text()
        assert not flow._reading, "a read started with no model configured"
        return f"CV shown as {flow.cv_lbl.text()}"
    check("onboarding: choosing a CV updates the card", t_onboarding_cv)

    def t_onboarding_done():
        w.root_stack.currentWidget()._done()
        settle(300)
        assert storage.get_setting("onboarded") == "1"
        assert w.root_stack.currentWidget() is w.shell
        return "Open Sushi hands over to the shell and records it"
    check("onboarding: finishing switches to the app", t_onboarding_done)

    def t_no_repeat():
        w2 = MainWindow()
        try:
            assert w2.root_stack.currentWidget() is w2.shell, "onboarding shown twice"
        finally:
            w2.deleteLater()
        return "second launch goes straight to the app"
    check("onboarding: not shown again", t_no_repeat)

    w.set_matches(matches)
    settle(300)

    # --- navigation --------------------------------------------------------
    def t_nav():
        seen = []
        for name in sorted(w._pages):
            w.navigate(name)
            settle(120)
            seen.append(name)
            assert w.crumb.text() == name, (w.crumb.text(), name)
            assert w.stack.currentWidget() is w._pages[name]
            row = w.sidebar._rows.get(name)
            if row is not None:
                assert row.objectName() == "NavRowActive", f"{name} not highlighted"
        return f"{len(seen)} pages: {', '.join(seen)}"
    check("nav: every page opens and highlights its row", t_nav)

    def t_unknown_page():
        w.navigate("Dashboard")
        settle(80)
        w.navigate("Nonsense")          # must be ignored, not crash
        assert w.crumb.text() == "Dashboard"
        return "unknown destination ignored"
    check("nav: unknown destination is a no-op", t_unknown_page)

    # --- dashboard ---------------------------------------------------------
    def t_cards():
        w.navigate("Dashboard")
        settle(250)
        dash = w._pages["Dashboard"]
        n = dash.matches_flow.count()
        assert n == 3, f"{n} cards for 3 matches"
        assert not dash.empty_matches.isVisible(), "empty state shown with matches present"
        return f"{n} cards rendered"
    check("dashboard: a card per match", t_cards)

    def t_search():
        dash = w._pages["Dashboard"]
        dash.search.setText("kestrel")
        settle(200)
        assert dash.matches_flow.count() == 1, dash.matches_flow.count()
        dash.search.setText("zzzz")
        settle(200)
        assert dash.matches_flow.count() == 0
        assert dash.empty_matches.isVisible(), "no empty state when the filter matches nothing"
        dash.search.clear()
        settle(200)
        assert dash.matches_flow.count() == 3
        return "filter narrows to 1, then 0 with an empty state, then restores"
    check("dashboard: search filters the grid", t_search)

    def t_tabs():
        dash = w._pages["Dashboard"]
        storage.upsert_role(matches[0], status="submitted")
        storage.upsert_role(matches[1], status="failed")
        w.apps_changed.emit()
        settle(250)
        dash._set_tab("All")
        settle(120)
        all_rows = dash.app_table.rowCount()
        dash._set_tab("Submitted")
        settle(120)
        sub_rows = dash.app_table.rowCount()
        assert all_rows >= 2 and sub_rows == 1, (all_rows, sub_rows)
        assert dash._tab_buttons["Submitted"].objectName() == "ChipActive"
        dash._set_tab("All")
        return f"All={all_rows} rows, Submitted={sub_rows} row"
    check("dashboard: status tabs filter the table", t_tabs)

    def t_save_pass():
        dash = w._pages["Dashboard"]
        before = len(w.matches)
        w.save_role(matches[2])
        settle(100)
        assert "Saved" in w.status.text(), w.status.text()
        w.pass_role(matches[2])
        settle(200)
        assert len(w.matches) == before - 1, (len(w.matches), before)
        # The card is gone, which is the promise. Not "one fewer card": the
        # feed now hides everything already dealt with and caps at four, so
        # the count depends on what earlier checks applied to — and an
        # assertion on it fails for reasons that have nothing to do with
        # Pass.
        from sushi.ui.components import JobCard
        on_screen = [c.role.company
                     for c in dash.matches_host.findChildren(JobCard)
                     if c.isVisible()]
        assert matches[2].company not in on_screen, (
            f"{matches[2].company} was passed on and is still on the feed: "
            f"{on_screen}")
        app = [a for a in storage.list_applications() if a.role.company == "Cleo"][0]
        assert app.status == "skipped", app.status
        return f"saved then passed; {before} → {len(w.matches)} matches, row marked skipped"
    check("dashboard: Save and Pass act on a role", t_save_pass)

    def t_apply_top():
        dash = w._pages["Dashboard"]
        # Kestrel is already submitted and Wise already failed, so a fresh role
        # is added to prove the new ones are queued and the settled ones are
        # reported rather than silently skipped.
        fresh = Role(company="Octopus", title="Operations Data Scientist",
                     location="London", url="https://boards.greenhouse.io/octopus/jobs/9",
                     score=15, reasons=["Same stack"])
        w.set_matches([matches[0], fresh])
        settle(200)
        dash._apply_top5()
        settle(250)
        queued = [a for a in storage.list_applications() if a.status == "queued"]
        assert len(queued) == 1 and queued[0].role.company == "Octopus",             [(a.role.company, a.status) for a in queued]
        # The one already handled is not on a card, so it is not counted.
        assert "Queued 1" in w.status.text(), w.status.text()
        assert dash._active_tab == "In flight", dash._active_tab
        return f"queued the new one, reported the rest: {w.status.text()}"
    check("dashboard: Apply to top 5 queues and switches tab", t_apply_top)

    # --- insane mode -------------------------------------------------------
    def t_arm():
        from sushi.extras import load_autoapply
        w.navigate("Insane mode")
        settle(300)
        page = w._pages["Insane mode"]
        assert not load_autoapply().enabled, "armed before anyone asked"
        page.arm_btn.setChecked(True)
        settle(250)
        assert load_autoapply().enabled, "arming did not persist"
        assert page.console_state.text() == "ARMED", page.console_state.text()
        assert w.sidebar._rows["Insane mode"].count.text() == "ON"
        page.arm_btn.setChecked(False)
        settle(250)
        assert not load_autoapply().enabled
        assert page.console_state.text() == "STANDBY"
        return "arm → persists + badge; disarm → clears"
    check("insane mode: the big button arms and disarms", t_arm)

    def t_reload_no_write():
        """Opening the page must not save over what is stored."""
        from sushi.extras import load_autoapply, save_autoapply
        cfg = load_autoapply()
        cfg.daily_cap = 37
        save_autoapply(cfg)
        w.navigate("Dashboard")
        settle(100)
        w.navigate("Insane mode")
        settle(300)
        assert load_autoapply().daily_cap == 37, load_autoapply().daily_cap
        assert not load_autoapply().enabled, "re-opening the page armed it"
        return "cap 37 survived a page visit; still disarmed"
    check("insane mode: visiting the page does not rewrite state", t_reload_no_write)

    def t_one_setting():
        """Lanes went, and the quality dial went after them.

        Both were configuration standing in front of a feature whose promise
        is that it runs itself, and the dial had stopped meaning anything:
        its three bands all selected the same roles on a real feed. What is
        left is the cap, and the cap has to be the whole of the page's
        state — a dial removed from the UI but still read off disk is the
        lanes bug a second time.
        """
        from dataclasses import fields

        from sushi.extras import AutoApplyConfig, load_autoapply
        page = w._pages["Insane mode"]
        names = {f.name for f in fields(AutoApplyConfig)}
        assert names == {"enabled", "daily_cap"}, \
            f"a setting nothing on screen can show: {sorted(names)}"
        for gone in ("quality_seg", "quality_row", "_set_quality"):
            assert not hasattr(page, gone), f"the dial is still here: {gone}"

        page.cap_spin.setValue(23)
        settle(120)
        assert load_autoapply().daily_cap == 23, "the cap did not persist"
        assert page.cap_row.help_label.text(), "no explanation under the cap"
        return f"one setting, and it persists: cap {load_autoapply().daily_cap}"
    check("insane mode: the cap is the only setting", t_one_setting)

    def t_chat_targets():
        """The chat replaced lanes as the way to say what you want."""
        before = list(w.profile.target_titles)
        w._run_action("set_targets", "Data Analyst, Analytics Engineer")
        settle(200)
        # Steering the search starts one. Stop it: this suite is about the
        # widgets, and a real scan is a minute of network per call.
        assert w.scanning(), "changing targets did not trigger a search"
        w.stop_scan()
        settle(150)
        got = storage.load_profile().target_titles
        assert got == ["Data Analyst", "Analytics Engineer"], got
        w._run_action("set_locations", "Bristol, Remote")
        settle(200)
        w.stop_scan()
        settle(150)
        assert storage.load_profile().target_locations == ["Bristol", "Remote"]
        # An empty instruction must not wipe the profile.
        w._run_action("set_targets", "   ")
        settle(120)
        assert storage.load_profile().target_titles == got, "blank arg cleared targets"
        p2 = storage.load_profile()
        p2.target_titles = before
        w.set_profile(p2)
        return f"chat set titles and locations; blank ignored ({', '.join(got)})"
    check("chat: the assistant can steer the search", t_chat_targets)

    def t_feed_comes_from_the_database():
        """The feed is read off disk, and follows the profile.

        Sushi used to fetch while it searched: the feed started empty every
        launch and filled minutes later. Now every vacancy lives in a local
        table, so opening the app is a query — and changing what you are
        looking for is a re-score of rows already on disk rather than
        another forty-minute sweep of 15,589 boards.
        """
        from sushi import jobsdb

        # Its own profile. Earlier checks in this suite steer the targets
        # through the assistant and do not put the locations back, so a
        # fixture written against the suite's opening profile scores
        # differently depending on what ran before it.
        restore = storage.load_profile()
        mine = Profile(
            full_name="Rowan Ellis Hartley", email="r.hartley@example.com",
            headline="Senior Operations Analyst", summary="Ops analyst.",
            skills=["Python", "SQL"], seniority="ic",
            experience=[Experience(company="Kestrel", title="Analyst",
                                   start="2022")],
            target_titles=["Operations Analyst"],
            target_locations=["Remote (UK)"])
        w.set_profile(mine)
        settle(300)

        jobsdb.forget_all()
        board = {"name": "Pleo", "ats": "greenhouse", "token": "pleo"}
        jobs = [{"title": t, "location": "Remote (UK)", "department": "Ops",
                 "url": f"https://example.com/pleo/{i}"}
                for i, t in enumerate(["Senior Operations Analyst",
                                       "Operations Analyst", "Head Chef"])]
        jobsdb.store_board(board, jobs, profile=mine)

        w.set_matches([])
        settle(500)
        w._load_feed()
        settle(500)
        assert w.matches, "the feed did not come off disk"
        assert all("Chef" not in r.title for r in w.matches), \
            [r.title for r in w.matches]
        before = len(w.matches)

        # Re-targeting: the rows are re-scored, nothing is fetched.
        chef = Profile(**{**mine.__dict__, "target_titles": ["Head Chef"]})
        w.set_profile(chef)
        for _ in range(40):
            settle(100)
            if any("Chef" in r.title for r in w.matches):
                break
        assert any("Chef" in r.title for r in w.matches), (
            f"changing targets did not re-score the table: "
            f"{[r.title for r in w.matches]}")

        if restore is not None:
            w.set_profile(restore)
        settle(600)
        jobsdb.forget_all()
        w.set_matches(matches)
        settle(400)
        return f"{before} from disk, then re-scored to the new targets"
    check("dashboard: the feed is served from the local job database",
          t_feed_comes_from_the_database)

    def t_all_jobs_page():
        """The Dashboard is a shortlist; All jobs is the list.

        Four cards out of a couple of thousand is a sample, and before this
        page existed the rest were unreachable — the grid was capped and
        nothing said so, which is part of why a feed of nine looked
        identical to a feed of fifteen hundred.
        """
        from sushi import jobsdb

        jobsdb.forget_all()
        board = {"name": "Pleo", "ats": "greenhouse", "token": "pleoqa"}
        titles = [f"Senior Operations Analyst {i}" for i in range(12)]
        jobsdb.store_board(board, [
            {"title": t, "location": "Remote (UK)", "department": "Ops",
             "url": f"https://example.com/pleoqa/{i}"}
            for i, t in enumerate(titles)], profile=storage.load_profile())

        w.navigate("All jobs")
        settle(500)
        page = w._pages["All jobs"]
        # The list is read off the UI thread now; wait for it to land.
        for _ in range(100):
            if not page._loading and page.table.rowCount():
                break
            settle(50)
        total = w.match_total()
        assert total >= 12, f"the database says {total}"
        assert page.table.rowCount() == min(total, page.PAGE_SIZE), (
            page.table.rowCount(), total)
        assert page.table.isVisible(), "the table is hidden with jobs on file"
        assert f"{total:,}" in page.count_lbl.text(), page.count_lbl.text()

        # The search narrows without touching the network.
        page.search.setText("Analyst 3")
        settle(400)
        assert page.table.rowCount() == 1, page.table.rowCount()
        page.search.setText("zzzznothing")
        settle(400)
        assert page.table.rowCount() == 0 and page.empty.isVisible()
        page.search.clear()
        settle(400)

        # The Dashboard shows four of them and offers the way through.
        w.navigate("Dashboard")
        settle(500)
        dash = w._pages["Dashboard"]
        assert dash.matches_flow.count() <= dash._FEED_CARDS,             dash.matches_flow.count()
        assert dash.see_all_btn.isVisible(), "no way through to the full list"
        dash.see_all_btn.click()
        settle(400)
        assert w._current == "All jobs", w._current

        jobsdb.forget_all()
        w.set_matches(matches)
        settle(300)
        return f"{total} on the full page, {dash._FEED_CARDS} on the Dashboard"
    check("all jobs: the full list is reachable and searchable", t_all_jobs_page)

    def t_worker_outlives_its_result():
        """A started worker is held until its result has been delivered.

        This is the crash. ``QRunnable`` auto-deletes the moment ``run()``
        returns, and ``run()`` returns straight after emitting ``finished``
        — a cross-thread emit, so it is queued and not delivered until the
        UI thread next spins. Nothing kept a reference to the worker, so the
        Python wrapper went with the C++ object and took ``WorkerSignals``
        with it; the UI thread then dispatched a queued call from a freed
        sender. That is a native access violation inside the interpreter,
        reported in the frame of whichever slot was being invoked — which is
        why two crash dumps in a row named ``main_window.done`` with a line
        number Python could not even resolve.
        """
        seen = []
        worker = w.run(lambda: "result", on_done=seen.append)
        assert not worker.autoDelete(), "the pool may still delete it mid-flight"
        assert worker in w._inflight, "a started worker was not held"
        for _ in range(60):
            settle(50)
            if seen:
                break
        assert seen == ["result"], seen
        # Released, but only after the slots have run.
        for _ in range(20):
            settle(50)
            if worker not in w._inflight:
                break
        assert worker not in w._inflight, "workers are never released — a leak"
        return "held while in flight, released after delivery, pool cannot free it"
    check("workers: a worker outlives its own result", t_worker_outlives_its_result)

    def t_matches_stream_in():
        """The feed fills while the scan runs, not when it ends.

        ``find_roles`` collected everything and returned it at the end of a
        pass, which is minutes — so the dashboard showed "Nothing yet" under
        the line "matches appear as they are found" while the status bar,
        which reads the progress text, counted fourteen.
        """
        from sushi.models import Role

        w.navigate("Dashboard")
        settle(200)
        dash = w._pages["Dashboard"]
        w.set_matches([])
        settle(500)                       # past the rebuild throttle
        assert dash.matches_flow.count() == 0, dash.matches_flow.count()

        # Exactly what discovery hands to on_match, by the same route: the
        # signal is emitted from the scan's worker thread.
        w.scan_found.emit([Role(company="Pleo", title="Operations Analyst",
                                location="Remote (EU)", score=15,
                                url="https://example.com/jobs/pleo-ops")])
        # Past the coalescing window: the refresh hands over a board at a
        # time, many times a second, so arrivals are collected and folded in
        # once rather than re-sorting the feed on every one.
        settle(1600)
        assert dash.matches_flow.count() == 1, (
            f"a match found mid-scan did not reach the feed: "
            f"{dash.matches_flow.count()} card(s)")
        assert len(w.matches) == 1, w.matches

        # And a second batch adds rather than replaces.
        w.scan_found.emit([Role(company="Tide", title="Data Analyst",
                                location="London", score=12,
                                url="https://example.com/jobs/tide-data")])
        settle(1600)
        assert dash.matches_flow.count() == 2, dash.matches_flow.count()
        # Best first, so the cap is spent on the best of them.
        assert [r.company for r in w.matches] == ["Pleo", "Tide"], w.matches

        w.set_matches(matches)
        settle(500)
        return "matches appear during the scan, best first, batches merge"
    check("dashboard: matches appear as they are found", t_matches_stream_in)

    def t_run_table():
        """The run is on screen, row by row, as it happens.

        Replaces the preview, which answered "what would it do?" with a
        paragraph. The table answers it by doing it — and, more to the point,
        says which role it is on and why nothing is moving when nothing is.
        """
        w.navigate("Insane mode")
        settle(150)
        page = w._pages["Insane mode"]

        page._paint_rows([])
        settle(80)
        assert not page.table.isVisible(), "an empty table was left on screen"
        assert page.table_empty.isVisible(), "nothing explained the empty table"

        rows = [
            {"key": "a", "company": "Kestrel", "title": "Ops Analyst",
             "score": 14, "state": "filling", "detail": "Reading the form…",
             "url": ""},
            {"key": "b", "company": "Kestrel", "title": "Data Analyst",
             "score": 12, "state": "needs_review", "detail": "2 question(s) need you.",
             "url": ""},
            {"key": "c", "company": "Wise", "title": "Analyst",
             "score": 11, "state": "queued", "detail": "", "url": ""},
        ]
        # Through the signal, not the method: the page has to follow a run
        # that started while you were on another page.
        w.autoapply_rows_changed.emit(rows)
        settle(150)
        assert page.table.isVisible(), "the table did not appear"
        assert page.table.rowCount() == 3, page.table.rowCount()
        for r in range(3):
            for col in range(4):
                assert page.table.cellWidget(r, col) is not None, (r, col)
        assert "1 of 3" in page.run_count.text(), page.run_count.text()

        # A run that cannot start says so in the table rather than nowhere.
        w._rows_blocked("No model configured — Settings → Model.")
        settle(150)
        assert page.table.rowCount() == 1, page.table.rowCount()
        assert page.table.isVisible(), "the blocked reason was not shown"
        w._autoapply_rows = []
        page._paint_rows([])
        return ("3 rows rendered live, count tracked, blocked runs explained "
                "in the table")
    check("insane mode: the run appears in a table as it happens", t_run_table)

    # --- splash --------------------------------------------------------------
    def t_splash():
        """It holds for its minimum, and stays alive while it holds."""
        from PyQt6.QtWidgets import QWidget
        from sushi.ui.splash import MIN_VISIBLE, Splash

        sp = Splash()
        sp.move(-7000, -7000)
        sp.show()
        sp.step("Loading your profile")
        t0 = time.time()
        host = QWidget()
        host.move(-7000, -7000)
        settle(250)                      # a fast cold start
        sp.finish(host)

        settle(900)
        assert sp.isVisible(), "splash gone before its minimum"
        assert not host.isVisible(), "window shown while the splash was still up"
        advanced = sp._panel._t
        settle(600)
        assert sp._panel._t != advanced, "the starfield froze during the hold"

        while sp.isVisible() and time.time() - t0 < 12:
            settle(80)
        held = time.time() - t0
        assert held >= MIN_VISIBLE - 0.4, f"only held {held:.2f}s"
        assert host.isVisible(), "the window never took over"
        host.close()
        return f"held {held:.2f}s (minimum {MIN_VISIBLE}s), animating throughout"
    check("splash: holds its minimum without freezing", t_splash)

    def t_splash_slow_start():
        """A slow build must not pay the hold on top of its own time."""
        from PyQt6.QtWidgets import QWidget
        from sushi.ui.splash import MIN_VISIBLE, Splash

        sp = Splash()
        sp.move(-7000, -7000)
        sp.show()
        sp.step("Loading")
        t0 = time.time()
        host = QWidget()
        host.move(-7000, -7000)
        settle(int(MIN_VISIBLE * 1000) + 400)   # the build itself outlasts it
        sp.finish(host)
        while sp.isVisible() and time.time() - t0 < 14:
            settle(60)
        held = time.time() - t0
        assert held < MIN_VISIBLE + 1.5, f"waited {held:.2f}s on an already-slow start"
        host.close()
        return f"handed over at {held:.2f}s — no extra wait stacked on a slow build"
    check("splash: a slow build is not made slower", t_splash_slow_start)

    # --- manual apply ------------------------------------------------------
    def t_manual():
        """Paste a link, see the posting, get the actions.

        The read itself is network, so this feeds the page a posting directly
        and checks everything around it — what is gated before a read, what
        the summary line says, what the read records.
        """
        from sushi.models import Role
        w.navigate("Manual apply")
        settle(300)
        page = w._pages["Manual apply"]
        page.url_edit.setText("")
        settle(120)
        for name in ("fill_btn", "open_btn", "letter_btn"):
            assert not getattr(page, name).isEnabled(), f"{name} live with no link"

        url = "https://example.com/jobs/kestrel-anaplan-analyst"
        # A link alone arms every action — the read happens on the way.
        page.url_edit.setText(url)
        settle(150)
        for name in ("fill_btn", "open_btn", "letter_btn"):
            assert getattr(page, name).isEnabled(), f"{name} dead with a link pasted"

        page._show(Role(company="Kestrel", title="Anaplan Support Analyst",
                        location="Cardiff, London or Remote (UK)", url=url,
                        ats="greenhouse", token="kestrel", score=12, match=77,
                        description="A real description, several lines long."))
        settle(200)
        line = page.summary.text()
        for bit in ("Kestrel", "Anaplan Support Analyst", "via Greenhouse", "77%"):
            assert bit in line, f"{bit!r} missing from {line!r}"
        assert "Anaplan Support Analyst" in page.summary.text(), page.summary.text()
        for name in ("fill_btn", "open_btn", "letter_btn"):
            assert getattr(page, name).isEnabled(), f"{name} still dead after a read"
        assert any(a.role.url == url for a in storage.list_applications()),             "the read posting was not recorded"

        # A different link drops what was read rather than leaving it under
        # a URL it does not belong to.
        page.url_edit.setText("https://example.com/somewhere-else")
        settle(150)
        assert page._role is None, "a stale posting survived a new link"
        assert "Press Read the job" in page.summary.text(), page.summary.text()
        return f"posting shown and recorded: {line[:70]}"
    check("manual apply: a pasted posting lands on the page", t_manual)

    def t_manual_fill_reads_first():
        """Fill it in Chrome works straight from a pasted link."""
        from sushi.models import Role
        page = w._pages["Manual apply"]
        url = "https://example.com/jobs/kestrel-anaplan-analyst-2"

        # Stand in for the network read and for Chrome; this is about the
        # path between them, which is what was broken.
        captured = {}
        # Manual apply goes through begin_apply now, not apply_to_role: the
        # page needs the result back so it can ask you the questions the
        # filler could not answer.
        real_apply = w.begin_apply
        real_read = page._read

        def fake_read(then=None):
            if not callable(then):
                then = None
            role = Role(company="Kestrel", title="Anaplan Support Analyst",
                        url=url, ats="greenhouse", token="kestrel", match=77,
                        description="Read on the way to the browser.")
            page._show(role)
            if then:
                then(role)

        page._read = fake_read

        def fake_begin(role, watch=False, announce=True, on_finished=None,
                       on_step=None):
            captured.update(role=role, watch=watch)
            if on_finished:
                from sushi.autofill import FillResult
                on_finished("needs_review", FillResult(
                    ok=True, submitted=False, left_open=True,
                    message="Filled 6 field(s).",
                    unanswered=["What is your notice period?"]))

        w.begin_apply = fake_begin
        try:
            page.url_edit.setText(url)
            settle(150)
            assert page._role is None, "nothing should be read yet"
            page._fill()
            settle(200)
            assert captured, "Fill never reached the browser step"
            assert captured["watch"] is True, "the browser would have been headless"
            assert captured["role"].description, "handed over a job it had not read"
            # Pressing again reuses what was read rather than fetching twice.
            fetches = []
            page._read = lambda then=None: fetches.append(1)
            captured.clear()
            page._fill()
            settle(150)
            assert captured and not fetches, "re-read a posting it already had"
            # And the questions it could not answer become answerable.
            assert page.answers.isVisible(),                 "unanswered questions did not surface as a panel"
            assert len(page.answers._rows) == 1, page.answers._rows
        finally:
            page._read = real_read
            w.begin_apply = real_apply
        return ("paste → Fill reads the posting, opens a visible browser, "
                "and asks what it could not answer")
    check("manual apply: Fill works from a pasted link", t_manual_fill_reads_first)

    def t_manual_bad_url():
        """A typo is answered on the page, not in a modal you must dismiss."""
        page = w._pages["Manual apply"]
        page.url_edit.setText("not a url")
        page._read()
        settle(250)
        assert "does not look like a link" in page.summary.text(), page.summary.text()
        assert page.read_btn.isEnabled(), "Read left disabled after a bad URL"
        page.url_edit.setText("")
        page._read()
        settle(250)
        assert "Paste a job link" in page.summary.text(), page.summary.text()
        return "bad and empty URLs both answered inline, Read stays usable"
    check("manual apply: a bad link is answered inline", t_manual_bad_url)

    # --- tracker -----------------------------------------------------------
    def t_tracker():
        from sushi.extras import PIPELINE_STAGES, get_stage
        w.navigate("Tracker")
        settle(300)
        page = w._pages["Tracker"]
        # Seed a row of this test's own. Taking whichever application
        # happened to be first made this flaky: earlier tests leave rows in
        # assorted states, and the ordering between them is not fixed.
        from sushi.models import Role as _Role
        marker = _Role(company="Tracker Fixture",
                       title="Senior Operations Analyst",
                       url="https://example.invalid/tracker-fixture",
                       location="Remote (UK)", score=12)
        app_id = storage.upsert_role(marker, status="submitted")
        storage.set_status(app_id, "submitted")
        w.apps_changed.emit()
        w.navigate("Tracker")
        settle(300)
        tracked = [a for a in storage.list_applications()
                   if a.status == "submitted" or get_stage(a.id or 0, a.status)]
        assert tracked, "nothing submitted to track"
        # The stage board is out of the new design: the page is the table.
        assert not page.board_scroll.isVisible(), "the stage board is showing again"
        assert page.board.count() == len(PIPELINE_STAGES), page.board.count()
        page._move(app_id, "interviewing")
        settle(200)
        assert get_stage(app_id, "submitted") == "interviewing"
        return f"{len(tracked)} tracked across {page.board.count()} columns; moved one to interviewing"
    check("tracker: board stays hidden and stages still move", t_tracker)

    def t_tracker_shows_everything_applied_to():
        """The Tracker answers "what have I applied to", stage or not.

        Its board only holds applications that have reached a stage, so a
        user with thirty-three applications and none staged saw "Nothing in
        the pipeline yet" beneath a sidebar badge reading 33. The badge was
        right and the board was right; the page was useless.
        """
        from sushi.models import Role
        from sushi.ui.components import ROW_HEIGHT

        # A spread of statuses, none of them staged and none submitted —
        # exactly the state that produced an empty page.
        seeded = []
        for i, status in enumerate(("needs_review", "failed", "skipped",
                                    "queued")):
            role = Role(company=f"Unstaged {i}", title="Operations Analyst",
                        location="Remote (UK)",
                        url=f"https://example.com/unstaged/{i}")
            app_id = storage.upsert_role(role, status=status)
            storage.set_status(app_id, status)
            seeded.append(role.url)

        w.navigate("Tracker")
        settle(400)
        page = w._pages["Tracker"]
        table = page.applications

        assert table.row_count() >= len(seeded), (
            f"the Tracker shows {table.row_count()} of "
            f"{len(storage.list_applications())} applications")
        # The board is legitimately empty here — but the page must not be.
        assert table.table.isVisible(), "the applications table is hidden"
        assert not table.empty.isVisible(),             "the table claimed to be empty while applications exist"

        # And the count on screen agrees with the sidebar badge, which is
        # what made the old behaviour read as a bug rather than a design.
        badge = w.sidebar._rows.get("Tracker")
        table.set_tab("All")
        settle(250)
        total = len(storage.list_applications())
        assert table.row_count() == total, (table.row_count(), total)

        # Tabs narrow it, same as the Dashboard — it is the same widget.
        table.set_tab("Failed")
        settle(250)
        failed = [a for a in storage.list_applications() if a.status == "failed"]
        assert table.row_count() == len(failed), (table.row_count(), len(failed))
        table.set_tab("All")
        settle(250)

        # And it uses the room it has. Left at the default the table was a
        # 260px box with its own scrollbar inside the page's scrollbar:
        # four rows visible, two nested scrollbars, half a screen of
        # nothing underneath it.
        rows_shown = min(total, table.MAX_GROWN_ROWS)
        expected = (table.table.horizontalHeader().height()
                    + rows_shown * ROW_HEIGHT + 4)
        assert table.table.height() == expected, (
            f"the table is {table.table.height()}px for {rows_shown} rows, "
            f"expected {expected}px")
        assert table.table.height() > 300,             "the table is still boxed to its old fixed height"
        return (f"{total} application(s) on the Tracker with an empty board; "
                f"tabs filter; table {table.table.height()}px for "
                f"{rows_shown} rows; badge {'present' if badge else 'absent'}")
    check("tracker: every application is listed, staged or not",
          t_tracker_shows_everything_applied_to)

    # --- settings ----------------------------------------------------------
    def t_settings_sections():
        from sushi.ui.pages.settings import SECTIONS
        w.navigate("Settings")
        settle(250)
        page = w._pages["Settings"]
        for name in SECTIONS:
            page._go(name)
            settle(80)
            assert page.stack.currentIndex() == SECTIONS.index(name)
            assert page._nav[name].objectName() == "NavItemActive"
        return f"{len(SECTIONS)} sections open and highlight"
    check("settings: every section opens", t_settings_sections)

    def t_settings_persist():
        from sushi.extras import load_apply_settings
        page = w._pages["Settings"]
        page._go("Apply settings")
        settle(100)
        page.resume_seg.set_value("aggressive", notify=True)
        settle(100)
        assert load_apply_settings().resume_optimization == "aggressive"
        assert page.resume_seg.value() == "aggressive"
        page.auto_cl.setChecked(False)
        settle(100)
        assert load_apply_settings().auto_cover_letter is False
        page._set_mode("honest")
        page.auto_cl.setChecked(True)
        return "resume mode and the cover-letter toggle both persist"
    check("settings: apply settings save on change", t_settings_persist)

    def t_settings_provider_rows():
        page = w._pages["Settings"]
        page._go("Model")
        settle(120)
        page._provider_changed("azure")
        settle(80)
        assert page._model_rows["azure_url"].isVisible()
        assert not page._model_rows["openai_base"].isVisible()
        page._provider_changed("openai")
        settle(80)
        assert page._model_rows["openai_base"].isVisible()
        assert not page._model_rows["azure_url"].isVisible()
        return "azure and openai each show only their own fields"
    check("settings: provider switches the visible fields", t_settings_provider_rows)

    def t_learning_ui():
        from sushi import learning
        page = w._pages["Settings"]
        learning.remember("Notice period?", "One month", "human")
        learning.remember("Salary expectation", "£72,000", "agent")
        page._go("Learning")
        page._load_learning()
        settle(150)
        assert page.learn_lay.count() == 2, page.learn_lay.count()
        assert "2 answer" in page.learn_count.text(), page.learn_count.text()
        page._forget(learning.normalise("Salary expectation"))
        settle(150)
        assert page.learn_lay.count() == 1, page.learn_lay.count()
        return "two answers listed, one forgotten from the UI"
    check("settings: the answer bank lists and forgets", t_learning_ui)

    # --- chrome ------------------------------------------------------------
    def t_rails():
        w.navigate("Dashboard")
        settle(150)
        w._toggle_sidebar()
        settle(400)
        assert w.sidebar.is_collapsed()
        assert w.sidebar.width() <= 80, w.sidebar.width()
        w._toggle_sidebar()
        settle(400)
        assert not w.sidebar.is_collapsed()
        w._toggle_assistant()
        settle(150)
        assert not w.assistant.isVisible()
        w._toggle_assistant()
        settle(150)
        assert w.assistant.isVisible()
        return "sidebar collapses to a rail and back; assistant hides and returns"
    check("chrome: sidebar and assistant toggle", t_rails)

    def t_responsive():
        w._rails_pinned = False
        w.resize(960, 760)
        settle(400)
        assert w.sidebar.is_collapsed(), "sidebar stayed open on a narrow window"
        assert not w.assistant.isVisible(), "assistant stayed open on a narrow window"
        w.resize(1440, 920)
        settle(400)
        assert not w.sidebar.is_collapsed(), "sidebar did not reopen when there was room"
        assert w.assistant.isVisible()
        return "rails stand down below 1180/1000 and come back"
    check("chrome: rails adapt to a narrow window", t_responsive)

    def t_pinned():
        w._toggle_sidebar()            # user takes control
        collapsed = w.sidebar.is_collapsed()
        w.resize(1000, 760)
        settle(300)
        assert w.sidebar.is_collapsed() == collapsed, "auto-fit overrode the user"
        w.resize(1440, 920)
        settle(300)
        return "a manual toggle stops the window second-guessing it"
    check("chrome: auto-fit yields to the user", t_pinned)

    def t_maximise():
        w.toggle_maximise()
        settle(250)
        assert w.isMaximized()
        assert w.win_btns.max_btn._maximised, "restore glyph not shown while maximised"
        w.toggle_maximise()
        settle(250)
        assert not w.isMaximized()
        assert not w.win_btns.max_btn._maximised
        return "maximise and restore, glyph follows"
    check("chrome: window buttons maximise and restore", t_maximise)

    def t_drop_cv():
        # The drop handler is what the whole window accepts a CV through.
        from PyQt6.QtCore import QMimeData, QPointF, QUrl
        from PyQt6.QtGui import QDropEvent
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(CV))])
        ev = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        w.dropEvent(ev)
        settle(200)
        assert storage.get_setting("cv_path") == str(CV)
        assert w.crumb.text() == "Profile", w.crumb.text()
        return "dropping a CV stores it and opens Profile"
    check("chrome: dropping a CV anywhere works", t_drop_cv)

    def t_resume_library():
        """Two CVs on file, switching between them, and no stale text.

        The bug this covers: with one CV and one profile, replacing the file
        left the previous CV's name, summary and employers on screen under
        the new file's name until a model had read it — and there was no way
        to keep both files at all.
        """
        second = Path(__file__).resolve().parent / "dummy_cv.md"
        w.navigate("Profile")
        settle(250)
        page = w._pages["Profile"]
        first = storage.active_resume()
        assert first is not None and first.read, "the first CV was not adopted"
        before = page.resume_preview.toPlainText()
        assert profile.full_name in before, before[:120]

        page.use_cv(str(second))          # no model here, so nothing is read
        settle(250)
        assert len(storage.list_resumes()) == 2, storage.list_resumes()
        assert storage.active_resume().path == str(second)
        assert storage.get_setting("cv_path") == str(second), "cv_path adrift"
        shown = page.resume_preview.toPlainText()
        assert profile.full_name not in shown, f"stale text: {shown[:120]}"
        assert "not been read yet" in shown, shown[:120]

        # And an edit while an unread CV is active must not be banked onto
        # it: that is how an unread resume came to show someone else's CV.
        w.set_profile(w.profile)
        settle(150)
        assert storage.active_resume().profile is None

        page._switch_resume(first.id)
        settle(250)
        assert storage.active_resume().path == first.path
        assert profile.full_name in page.resume_preview.toPlainText()

        page._remove_resume([r for r in storage.list_resumes()
                             if r.path == str(second)][0].id)
        settle(150)
        assert len(storage.list_resumes()) == 1
        assert second.exists(), "removing a resume deleted the file"
        return "two CVs attached, switched, and neither showed the other's text"
    check("profile: the CV library holds more than one", t_resume_library)

    def t_refresh_reads_the_cv():
        """All jobs' Refresh asks the CV first, then the boards."""
        w.navigate("All jobs")
        settle(250)
        page = w._pages["All jobs"]
        assert page.refresh_btn.text() == "Refresh", page.refresh_btn.text()
        seen: dict = {}
        real_read, real_scan = w.read_cv, w.start_scan
        w.read_cv = lambda **kw: (seen.update(kw), kw["then"](), True)[-1]
        w.start_scan = lambda *a, **k: seen.update(scanned=True)
        try:
            page.refresh_btn.click()
            settle(150)
        finally:
            w.read_cv, w.start_scan = real_read, real_scan
        assert seen.get("only_if_changed") is True, seen
        assert seen.get("scanned") is True, seen
        return "Refresh re-reads a changed CV, then sweeps the boards"
    check("all jobs: Refresh picks up a changed CV", t_refresh_reads_the_cv)

    def t_close_browsers():
        """The way out of a run — in the chrome, and on Insane mode."""
        from sushi import autofill

        w.navigate("Insane mode")
        settle(250)
        page = w._pages["Insane mode"]
        assert not w.browsers_btn.isVisible(), "offered with nothing open"
        assert not page.finished.isVisible()

        autofill._open_sessions.append(object())
        try:
            w.refresh_chrome()
            settle(100)
            assert w.browsers_btn.isVisible()
            assert w.browsers_btn.text() == "Close browser", w.browsers_btn.text()

            w._autoapply_rows = [
                {"key": "a", "company": "Kestrel", "title": "Analyst",
                 "score": 17, "state": "needs_review", "detail": "Filled."}]
            w._autoapply_running = True
            page._paint_rows(w.autoapply_rows())
            settle(100)
            assert not page.finished.isVisible(), "announced the end mid-run"
            w._autoapply_running = False
            page._paint_rows(w.autoapply_rows())
            settle(100)
            assert page.finished.isVisible(), "no way to end a finished run"
        finally:
            autofill._open_sessions.clear()
            w._autoapply_rows = []
        w.refresh_chrome()
        page._paint_rows([])
        settle(100)
        assert not w.browsers_btn.isVisible()
        assert not page.finished.isVisible()
        return "close offered in the title bar and at the end of a run, and only then"
    check("apply: a finished run can be closed", t_close_browsers)

    def t_rescore_failure_is_not_a_dialog():
        """A re-score that dies must not put a raw exception in front of you.

        What was reported: switching resume raised "TypeError: 'list' object
        does not support the asynchronous context manager protocol" in a
        modal box over the Profile page. The re-score runs itself off the
        back of a profile change — nobody pressed "re-score" — so it belongs
        in the status line and the log.
        """
        from sushi import jobsdb

        shown: list[str] = []
        real_error, real_rescore = w.error, jobsdb.rescore
        jobsdb.rescore = lambda *a, **k: (_ for _ in ()).throw(
            TypeError("'list' object does not support the asynchronous "
                      "context manager protocol"))
        w.error = lambda msg: shown.append(msg)
        try:
            w.rescore_feed()
            for _ in range(40):
                settle(100)
                if w._rescore_worker is None:
                    break
        finally:
            w.error, jobsdb.rescore = real_error, real_rescore
        assert not shown, f"a background re-score opened a dialog: {shown}"
        assert "could not be re-scored" in w.status_text(), w.status_text()
        return "a failed re-score reports in the status line, not a modal box"
    check("feed: a failed re-score is not thrown at the user",
          t_rescore_failure_is_not_a_dialog)

    def t_one_rescore_at_a_time():
        """Two resume switches in a second must not be two passes at once."""
        from sushi import jobsdb

        started, release = [], threading.Event()

        def slow(*_a, **_k):
            started.append(1)
            release.wait(5)
            return 0

        real = jobsdb.rescore
        jobsdb.rescore = slow
        try:
            w.rescore_feed()
            w.rescore_feed()
            w.rescore_feed()
            settle(400)
            assert len(started) == 1, f"{len(started)} passes ran at once"
            release.set()
            for _ in range(40):
                settle(100)
                if len(started) > 1:
                    break
            # The changes made during the pass are picked up by exactly one
            # more, not by one per click.
            assert len(started) == 2, f"{len(started)} passes in total"
            release.set()
            for _ in range(30):
                settle(100)
                if w._rescore_worker is None:
                    break
        finally:
            release.set()
            jobsdb.rescore = real
            settle(200)
        return "three requests became one pass and one catch-up, not three"
    check("feed: re-scores do not pile up on each other", t_one_rescore_at_a_time)

    def t_restart_onboarding():
        w.restart_onboarding()
        settle(300)
        from sushi.ui.onboarding import Onboarding
        assert isinstance(w.root_stack.currentWidget(), Onboarding)
        w.root_stack.currentWidget()._done()
        settle(250)
        assert w.root_stack.currentWidget() is w.shell
        return "re-entered setup and came back out"
    check("settings: restart onboarding round-trips", t_restart_onboarding)

    w.hide()

    # Belt and braces on the stub above: if anything did reach a browser,
    # it goes now rather than being left on the user's screen holding the
    # profile directory that the next real application needs.
    try:
        from sushi.autofill import close_open_sessions, close_orphaned_browsers
        close_open_sessions()
        stranded = close_orphaned_browsers()
        if stranded:
            print(f"  (closed {stranded} browser(s) this suite should not "
                  f"have opened)")
    except Exception as e:                      # never fail a run on cleanup
        print(f"  (cleanup: {type(e).__name__}: {e})")

    print()
    width = max(len(n) for _, n, _ in RESULTS) + 2
    for status, name, detail in RESULTS:
        print(f"  {'ok  ' if status == 'PASS' else 'FAIL'}  {name:<{width}} {detail}")
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
