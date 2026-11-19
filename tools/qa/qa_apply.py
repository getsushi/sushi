"""End-to-end testing of the two things that apply to jobs.

`qa_audit.py` checks the wiring with the browser stubbed. This runs the real
thing: a real Chromium, a real form, the real watcher. It is slower and it is
the only suite that proves an application can actually be filled — which is
what kept being broken while every other suite was green.

The form is served locally rather than fetched from a real board, for three
reasons: nobody's real vacancy receives a test application, the fields are
known so the assertions can be exact, and it still exercises every piece that
was failing (file upload, typeahead, the learning watcher, tab sharing).

    python qa_apply.py              # everything except the LLM agent
    python qa_apply.py --live       # adds a real agent run against the form

``--live`` spends model tokens and needs working credentials.
"""
from __future__ import annotations

import http.server
import os
import socketserver
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

os.environ.setdefault("SUSHI_NO_AUTOSCAN", "1")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="sushi-apply-")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

RESULTS: list[tuple[str, str, str]] = []
CV = Path(__file__).resolve().parent / "dummy_cv.pdf"
LIVE = "--live" in sys.argv

FORM = """<!doctype html><html><head><meta charset="utf-8">
<title>Senior Operations Analyst - Kestrel</title></head><body>
<h1>Senior Operations Analyst</h1>
<form id="app" method="post" action="/submitted">
  <label for="fn">First name</label><input id="fn" name="fn">
  <label for="ln">Last name</label><input id="ln" name="ln">
  <label for="em">Email</label><input id="em" name="em" type="email">
  <label for="ph">Phone</label><input id="ph" name="ph">
  <label for="city">Location (City)</label>
  <input id="city" name="city" list="cities" autocomplete="off">
  <datalist id="cities"><option value="Bristol, UK"><option value="London, UK"></datalist>
  <label for="cv">Resume/CV</label><input id="cv" name="cv" type="file">
  <label for="cl">Cover Letter</label><input id="cl" name="cl" type="file">
  <label for="np">Notice period</label><input id="np" name="np">
  <label for="why">Why do you want to work here?</label>
  <textarea id="why" name="why"></textarea>
  <label for="pw">Password</label><input id="pw" name="pw" type="password">
  <fieldset><legend>Do you require visa sponsorship?</legend>
    <label><input type="radio" name="visa" value="Yes"> Yes</label>
    <label><input type="radio" name="visa" value="No"> No</label>
  </fieldset>
  <label><input type="checkbox" id="privacy" name="privacy">
    I have read and agree to the Privacy Notice *</label>
  <label><input type="checkbox" id="onsite" name="onsite">
    Are you willing to work onsite three days a week?</label>
  <label for="gender">Gender identity</label>
  <select id="gender" name="gender">
    <option value="">Select</option><option>Prefer not to say</option>
  </select>
  <button type="submit">Submit application</button>
</form></body></html>"""


def check(name: str, fn) -> None:
    try:
        RESULTS.append(("PASS", name, str(fn() or "")[:170]))
        print(f"  ok    {name}")
    except Exception as e:
        RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=2)


#: A posting that has closed since the feed saw it — with a 200, which is
#: what boards actually serve. The queue has to read this as "gone" rather
#: than as a form it failed to find.
CLOSED = """<!doctype html><html><head><meta charset="utf-8">
<title>Senior Operations Analyst - Kestrel</title></head><body>
<h1>Senior Operations Analyst</h1>
<p>This role is no longer accepting applications. Browse our other openings.</p>
</body></html>"""


#: What an aggregator serves: a page *about* the job, with the form
#: somewhere behind an Apply link. Jobicy, The Muse and Himalayas all work
#: this way, and they are most of tier one.
ADVERT = """<!doctype html><html><head><meta charset="utf-8">
<title>Senior Operations Analyst at Kestrel - JobBoard</title></head><body>
<nav><a href="/browse">Browse all jobs</a><a href="/alerts">Job alerts</a></nav>
<h1>Senior Operations Analyst</h1>
<p>Remote (UK) · Full time · Posted today</p>
<a href="{apply}">Apply for this job</a>
<a href="/company/kestrel">View company</a>
</body></html>"""


class _Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):          # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if self.path.startswith("/closed"):
            body = CLOSED
        elif self.path.startswith("/advert"):
            body = ADVERT.format(apply=f"http://127.0.0.1:{self.server.server_address[1]}/")
        else:
            body = FORM
        self.wfile.write(body.encode())

    def do_POST(self):         # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"submitted")

    def log_message(self, *_a):
        pass


class _Server(socketserver.ThreadingTCPServer):
    """Threaded on purpose.

    ``TCPServer`` serves one connection at a time, and Chromium opens
    several — the page, a favicon, a keep-alive it holds open. A test that
    navigates twice in one tab (an advert, then the form behind its Apply
    link) deadlocked against that: the second page never loaded and the CDP
    call waited its full sixty seconds.
    """
    daemon_threads = True
    allow_reuse_address = True


def serve() -> str:
    srv = _Server(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/"


def main() -> int:
    from PyQt6.QtCore import QEventLoop
    from PyQt6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)

    def settle(ms: int = 250) -> None:
        end = time.time() + ms / 1000
        while time.time() < end:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)

    from sushi import autofill, learning, storage
    from sushi.models import Experience, Profile, Role
    from sushi.ui import theme
    from sushi.ui.fonts import DISPLAY, load_fonts

    theme.refresh_stylesheet(display=load_fonts().get("display", DISPLAY))
    from sushi.ui.main_window import MainWindow

    url = serve()
    print(f"test form at {url}\n")

    storage.save_profile(Profile(
        full_name="Rowan Ellis Hartley", email="r.hartley@example.com",
        phone="+44 7700 900100", location="Bristol, UK",
        headline="Senior Operations Analyst", summary="Ops and data analyst.",
        skills=["Python", "SQL"], seniority="ic",
        experience=[Experience(company="Kestrel", title="Analyst", start="2022")],
        target_titles=["Operations Analyst"], target_locations=["Remote (UK)"]))
    storage.set_setting("cv_path", str(CV))
    storage.set_setting("onboarded", "1")

    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.information = staticmethod(lambda *a, **k: None)

    w = MainWindow()
    w.resize(1280, 860)
    w.move(-7000, -7000)
    w.show()
    settle(700)

    def role(n: int = 1, score: int = 15) -> Role:
        return Role(company=f"Kestrel {n}", title="Senior Operations Analyst",
                    location="Remote (UK)", score=score, match=80,
                    url=f"{url}?job={n}", ats="greenhouse", token="kestrel",
                    description="Operations analytics. Route optimisation.")

    # --- 0. a clean browser ------------------------------------------------
    def p_clean_start():
        """Leftovers from an earlier run must not block anything."""
        n = autofill.close_orphaned_browsers()
        assert not autofill.orphaned_browsers(), "orphans survived the cleanup"
        return f"closed {n} leftover browser(s); none remain"
    check("browser: a stranded browser is cleared, not fatal", p_clean_start)

    # --- 1. Manual apply ---------------------------------------------------
    def p_manual_read():
        w.navigate("Manual apply")
        settle(300)
        page = w._pages["Manual apply"]
        page.url_edit.setText(url)
        settle(200)
        assert page.fill_btn.isEnabled(), "Fill is dead with a link pasted"
        page._read()
        for _ in range(40):
            settle(500)
            if page._role is not None:
                break
        assert page._role is not None, "the posting never arrived"
        # The posting dump is gone — it showed the page's nav and cookie
        # bar above the only thing on this page you act on. One line now.
        assert "Senior Operations Analyst" in page.summary.text(), page.summary.text()
        assert "Senior Operations Analyst" in page._role.title, page._role.title
        return f"read: {page.summary.text()[:90]}"
    check("manual: pasting a link reads the posting", p_manual_read)

    def p_manual_recorded():
        rows = [a for a in storage.list_applications() if a.role.url == url]
        assert rows, "the posting was not recorded"
        return f"recorded as {rows[0].status}"
    check("manual: the posting is recorded", p_manual_recorded)

    # --- 2. Insane mode, with the browser stage intercepted ----------------
    filled: list[dict] = []
    real_begin = w.begin_apply

    def fake_begin(role_, watch=False, announce=True, on_finished=None,
                   on_step=None, **_kw):
        filled.append({"role": role_, "watch": watch})
        if on_step:
            on_step("Reading the form…")
        app_id = storage.upsert_role(role_, status="needs_review")
        storage.set_status(app_id, "needs_review", "filled")
        if on_finished:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(40, lambda: on_finished("needs_review", None))

    def p_insane_full_run():
        """Arm it and it works through the queue, in a visible browser."""
        from sushi.extras import load_autoapply, save_autoapply
        w.begin_apply = fake_begin
        cfg = load_autoapply()
        cfg.daily_cap = 4
        cfg.enabled = False
        save_autoapply(cfg)
        w.set_matches([role(i, 10 + i) for i in range(1, 7)])
        page = w._pages["Insane mode"]
        w.navigate("Insane mode")
        page.on_show()
        settle(200)

        filled.clear()
        page.arm_btn.setChecked(True)
        for _ in range(40):
            settle(250)
            if len(filled) >= 4:
                break
        assert filled, "armed and nothing was filled"
        assert len(filled) == 4, f"cap of 4, filled {len(filled)}"
        assert all(f["watch"] for f in filled), "a browser would have been hidden"
        # Best first.
        scores = [f["role"].score for f in filled]
        assert scores == sorted(scores, reverse=True), scores
        page.arm_btn.setChecked(False)
        settle(300)
        return f"filled {len(filled)} best-first, all visible, cap honoured"
    check("insane: a full run fills the queue", p_insane_full_run)

    def p_insane_tracked():
        """Everything it filled must be in the tracker, needing review."""
        urls = {f["role"].url for f in filled}
        rows = {a.role.url: a.status for a in storage.list_applications()}
        missing = [u for u in urls if u not in rows]
        assert not missing, f"{len(missing)} filled application(s) untracked"
        bad = {u: rows[u] for u in urls if rows[u] != "needs_review"}
        assert not bad, f"wrong status: {bad}"
        return f"{len(urls)} application(s) tracked as needs_review"
    check("insane: what it filled is tracked", p_insane_tracked)

    def p_insane_resumes():
        """Disarm mid-queue, re-arm, and it carries on rather than restarting."""
        from sushi.extras import load_autoapply, save_autoapply
        cfg = load_autoapply()
        cfg.daily_cap = 20
        cfg.enabled = False
        save_autoapply(cfg)
        w.set_matches([role(i, 10 + (i % 5)) for i in range(10, 20)])
        page = w._pages["Insane mode"]
        page.on_show()
        settle(150)
        filled.clear()
        page.arm_btn.setChecked(True)
        settle(500)
        page.arm_btn.setChecked(False)
        settle(800)
        stopped = len(filled)
        settle(800)
        assert len(filled) == stopped, "kept going after disarm"
        page.arm_btn.setChecked(True)
        settle(900)
        page.arm_btn.setChecked(False)
        settle(300)
        assert len(filled) > stopped, "re-arming did not resume"
        return f"stopped at {stopped}, resumed to {len(filled)}"
    check("insane: stop and resume", p_insane_resumes)

    w.begin_apply = real_begin

    # --- 3. the real browser, really filling -------------------------------
    def p_real_fill():
        """The whole path, with a real Chromium on a real form."""
        from dataclasses import replace

        from sushi.autofill import fill_application
        from sushi.config import load_settings

        s = replace(load_settings(), headless=False, autosubmit=False)
        if not s.llm_enabled:
            return "skipped — no model configured"
        from sushi.autofill import AutofillError
        t0 = time.time()
        try:
            result = fill_application(
                role(99), w.profile, s,
                cv_path=storage.get_setting("cv_path", ""),
                letter_path="", resume_mode="honest")
        except AutofillError as exc:
            # Distinguish "Sushi is broken" from "the model refused us".
            if "credentials were refused" in str(exc):
                raise AssertionError(
                    "BLOCKED ON CREDENTIALS — the agent could not make a "
                    "single model call, so nothing about the apply path "
                    "itself was exercised. Fix the key in Settings → Model "
                    "and re-run.") from None
            raise
        took = time.time() - t0
        assert result.left_open or result.ok, f"nothing filled: {result.message[:200]}"

        # What did it actually put in the page?
        from sushi.autofill import _evaluate, _open_sessions, _run_on_loop
        assert _open_sessions, "the browser was not left open for review"
        values = _run_on_loop(_evaluate(
            _open_sessions[0],
            "JSON.stringify(Object.fromEntries("
            "[...document.querySelectorAll('input,textarea')]"
            # A tick-box reports value 'on' whether or not it is ticked, so
            # reading .value would have shown the privacy notice as agreed to
            # when nothing had touched it — and hidden it if something had.
            ".map(e=>[e.id||e.name,"
            " e.type==='file' ? (e.files[0]?.name||'')"
            " : (e.type==='checkbox'||e.type==='radio')"
            "   ? (e.checked ? (e.value||'on') : '')"
            "   : e.value])))"),
            timeout=30)
        import json as _json
        got = _json.loads(values) if values else {}
        print("     page values:", {k: v for k, v in got.items() if v})

        assert got.get("fn"), "first name was not filled"
        assert got.get("em"), "email was not filled"
        assert got.get("cv"), "the CV was not attached — the upload is broken"
        assert not got.get("pw"), "a password was typed into the form"
        return (f"filled in {took:.0f}s; CV attached as {got.get('cv')!r}; "
                f"{len(result.unanswered)} left for the user")
    # --- the no-model path --------------------------------------------------
    def p_direct_fill():
        """Fill the form with no agent and no model at all."""
        from dataclasses import replace
        import json as _json

        from sushi.autofill import (_evaluate, _open_sessions, _run_on_loop,
                                    close_open_sessions, fill_directly)
        from sushi.config import load_settings

        close_open_sessions()
        learning.remember("Notice period", "One month", source="human")
        # One of each tick-box shape: a radio group, where the answer picks
        # the member, and a lone checkbox, where it decides the state.
        learning.remember("Do you require visa sponsorship?", "No",
                          source="human")
        learning.remember("Are you willing to work onsite three days a week?",
                          "Yes", source="human")
        s = replace(load_settings(), headless=False)
        t0 = time.time()
        result = fill_directly(role(50), w.profile, s,
                               cv_path=storage.get_setting("cv_path", ""))
        took = time.time() - t0

        assert _open_sessions, "the browser was not left open"
        values = _run_on_loop(_evaluate(
            _open_sessions[0],
            "JSON.stringify(Object.fromEntries("
            "[...document.querySelectorAll('input,textarea,select')]"
            # A tick-box reports value 'on' whether or not it is ticked, so
            # reading .value would have shown the privacy notice as agreed to
            # when nothing had touched it — and hidden it if something had.
            ".map(e=>[e.id||e.name,"
            " e.type==='file' ? (e.files[0]?.name||'')"
            " : (e.type==='checkbox'||e.type==='radio')"
            "   ? (e.checked ? (e.value||'on') : '')"
            "   : e.value])))"),
            timeout=30)
        got = _json.loads(values) if values else {}
        print("     page values:", {k: v for k, v in got.items() if v})
        for q in ("Notice period", "Do you require visa sponsorship?",
                  "Are you willing to work onsite three days a week?"):
            learning.forget(q)

        assert got.get("visa") == "No", \
            f"the radio group was not set from the bank: {got.get('visa')!r}"
        assert got.get("onsite"), "a lone checkbox was not ticked from the bank"
        assert got.get("fn") == "Rowan", f"first name: {got.get('fn')!r}"
        assert got.get("ln"), "last name not filled"
        assert "@" in (got.get("em") or ""), f"email: {got.get('em')!r}"
        assert got.get("ph"), "phone not filled"
        assert got.get("np") == "One month", f"notice period: {got.get('np')!r}"
        assert got.get("cv"), "the CV was not attached"
        assert not got.get("pw"), "a password was typed into the form"
        assert not got.get("gender"), "a demographic question was answered"
        # Agreeing to a privacy notice is the user's to give. It is also not
        # a question worth putting to them in the panel — there is nothing to
        # type and nothing to remember — so it must be in neither list.
        assert not got.get("privacy"), "Sushi agreed to a privacy notice itself"
        assert not any("Privacy Notice" in q for q in result.unanswered), \
            f"a consent box was put as a question: {result.unanswered}"
        assert "tick" in result.message, \
            f"the consent box was not mentioned at all: {result.message[:200]}"
        assert "Why do you want to work here?" in result.unanswered, result.unanswered
        return (f"{took:.1f}s, no model calls; filled "
                f"{len([v for v in got.values() if v])} field(s); "
                f"{len(result.unanswered)} left for the user")
    check("direct: the form is filled with no model at all", p_direct_fill)

    def p_each_application_gets_its_own_tab():
        """Two applications, two filled tabs — and no blank ones.

        Applications share one browser and take a tab each, because Chromium
        allows one process per profile directory. But ``new_page()`` creates a
        tab without focusing it, and every CDP call here asked for "the
        focused tab": so each application opened a tab, abandoned it on
        about:blank, and then navigated and filled the *first* tab again. The
        window filled up with blank tabs — nine of them in one run — while one
        page did all the work and each application overwrote the last.
        """
        from dataclasses import replace

        from sushi.autofill import (_open_sessions, _run_on_loop,
                                    close_open_sessions, fill_directly)
        from sushi.config import load_settings

        close_open_sessions()
        s = replace(load_settings(), headless=False)
        first = fill_directly(role(11), w.profile, s,
                              cv_path=storage.get_setting("cv_path", ""))
        second = fill_directly(role(12), w.profile, s,
                               cv_path=storage.get_setting("cv_path", ""))
        assert first.left_open and second.left_open, (first.message, second.message)
        assert _open_sessions, "no browser was left open"

        tabs = _run_on_loop(_open_sessions[0].get_tabs(), timeout=30)
        urls = [(getattr(t, "url", "") or "") for t in tabs]
        blanks = [u for u in urls if u.startswith("about:blank")]
        on_form = [u for u in urls if url.rstrip("/") in u]
        close_open_sessions()
        assert len(on_form) >= 2, f"both applications shared one tab: {urls}"
        assert not blanks, f"abandoned blank tab(s): {urls}"
        # Different jobs, so the second did not simply reload over the first.
        assert len(set(on_form)) >= 2, f"the same page twice: {on_form}"
        return f"{len(on_form)} filled tabs, {len(blanks)} blank"
    check("insane: each application fills its own tab",
          p_each_application_gets_its_own_tab)

    def p_advert_leads_to_the_form():
        """An advert with an Apply link is followed, then filled.

        Tier one is aggregators, whose posting URL is an advert rather than a
        form. Sushi arrived, found no fields, and reported "0 field(s)
        filled" — spending one of a fifteen-a-day cap on reading an advert,
        when a person would simply have pressed Apply.
        """
        from dataclasses import replace

        import json as _json

        from sushi.autofill import (_evaluate, _open_sessions, _run_on_loop,
                                    close_open_sessions, fill_directly)
        from sushi.config import load_settings

        close_open_sessions()
        advert = replace(role(77), url=f"{url}advert?job=77")
        result = fill_directly(advert, w.profile,
                               replace(load_settings(), headless=False),
                               cv_path=storage.get_setting("cv_path", ""))
        assert _open_sessions, "the browser was not left open"
        got = _json.loads(_run_on_loop(_evaluate(
            _open_sessions[0],
            "JSON.stringify({url: location.pathname,"
            " fn: (document.getElementById('fn')||{}).value || ''})"),
            timeout=30) or "{}")
        close_open_sessions()
        assert result.ok, f"the advert was not followed through: {result.message[:200]}"
        assert got.get("fn") == "Rowan", (
            f"followed the link but filled nothing: {got}")
        return f"advert → form, filled as {got.get('fn')!r}"
    check("direct: an advert's Apply link is followed to the form",
          p_advert_leads_to_the_form)

    def p_closed_posting_reads_as_closed():
        """A role that closed since the scan says so, in those words.

        Feeds go stale: the queue's best match is often a posting taken down
        since it was found. Reporting that as "no form was found, press Apply
        first" sends you to a dead page to look for a button that is not
        there, and in a batch it reads as Insane mode failing.
        """
        from dataclasses import replace

        from sushi.autofill import close_open_sessions, fill_directly
        from sushi.config import load_settings

        close_open_sessions()
        dead = replace(role(99), url=f"{url}closed?job=99")
        result = fill_directly(dead, w.profile,
                               replace(load_settings(), headless=False))
        close_open_sessions()
        assert not result.ok, "a closed posting reported success"
        assert "no longer taking applications" in result.message.lower(), \
            result.message[:200]
        assert "press Apply" not in result.message, \
            f"sent the user to a dead page: {result.message[:160]}"
        return f"reported as closed: {result.message[:90]}"
    check("direct: a posting that has closed says so",
          p_closed_posting_reads_as_closed)

    def p_unusable_cv_is_reported():
        """A CV that cannot be uploaded must say so, not vanish quietly."""
        import tempfile as _tf

        from sushi.autofill import file_problems

        empty = Path(_tf.mkdtemp()) / "empty.pdf"
        empty.write_bytes(b"")
        problems = file_problems(str(empty), "")
        assert problems, "a zero-byte CV was accepted in silence"
        assert "empty" in problems[0].lower(), problems
        gone = file_problems("Z:/not-here.pdf", "")
        assert gone and "not there" in gone[0].lower(), gone
        assert not file_problems(str(CV), ""), "a real CV was refused"
        return "empty and missing CVs are both reported in words"
    check("direct: an unusable CV is reported, not swallowed",
          p_unusable_cv_is_reported)

    def p_file_fields_matched_by_key():
        """Greenhouse labels both file inputs 'Attach' — match on the key."""
        import inspect

        from sushi import autofill as _af
        src = inspect.getsource(_af._run_direct)
        assert "f.get('key', '')" in src or 'f.get("key", "")' in src,             "file fields are matched on label, which is 'Attach' for both"
        assert "dispatchEvent" in inspect.getsource(_af._upload),             "no change event after upload — a React uploader will not notice"
        return "matched on key; change event fired after attaching"
    check("direct: the CV and the cover letter go to different inputs",
          p_file_fields_matched_by_key)

    def p_answer_panel():
        """Unanswered questions must become a place to answer them."""
        from sushi.ui.answers import AnswerPanel

        panel = AnswerPanel()
        assert not panel.isVisible(), "the panel shows with nothing to ask"
        panel.set_questions([
            "What is your highest level of education?",
            "Notice period",
            "What is your password?",          # must be refused
            "What is your gender identity?",   # must be refused
        ])
        assert panel.isVisible(), "questions arrived and the panel stayed hidden"
        assert len(panel._rows) == 4, len(panel._rows)
        # A long question gets a paragraph box, a short one a single line.
        from PyQt6.QtWidgets import QLineEdit, QPlainTextEdit
        kinds = {q: type(w).__name__ for q, w in panel._rows}
        assert kinds["Notice period"] == "QLineEdit", kinds
        assert "QPlainTextEdit" in kinds.values(), kinds

        before = {a.key for a in learning.bank()}
        for q, widget in panel._rows:
            text = "Master's degree" if "education" in q else "hunter2"
            if isinstance(widget, QPlainTextEdit):
                widget.setPlainText(text)
            else:
                widget.setText(text)
        panel._save()
        settle(200)

        bank = {a.question.lower(): a.answer for a in learning.bank()}
        assert any("education" in q for q in bank), "a fair answer was not banked"
        assert not any("password" in q for q in bank), "a password was banked"
        assert not any("gender" in q for q in bank), "a demographic was banked"
        assert "not stored" in panel.note.text(), panel.note.text()

        # It is a correction surface too: a known answer is pre-filled.
        again = AnswerPanel()
        again.set_questions(["What is your highest level of education?"])
        _, widget = again._rows[0]
        shown = (widget.toPlainText() if isinstance(widget, QPlainTextEdit)
                 else widget.text())
        assert "Master" in shown, f"a known answer was not offered back: {shown!r}"

        for a in learning.bank():
            if a.key not in before:
                learning.forget(a.question)
        return "asks, banks, refuses passwords and demographics, pre-fills known"

    def p_answer_panel_clears():
        """Saving must confirm visibly and take the answered rows away."""
        from PyQt6.QtWidgets import QPlainTextEdit

        from sushi.ui import theme
        from sushi.ui.answers import AnswerPanel

        before = {a.key for a in learning.bank()}
        panel = AnswerPanel()
        panel.set_questions(["How soon could you start?", "Notice period",
                             "Preferred pronouns"])
        settle(150)
        assert len(panel._rows) == 3

        for q, w in panel._rows:
            if "start" in q:
                w.setPlainText("Four weeks") if isinstance(w, QPlainTextEdit)                     else w.setText("Four weeks")
            elif q == "Notice period":
                w.setText("One month")
        panel._save()
        settle(150)
        assert theme.GOOD.lower() in panel.note.styleSheet().lower(),             "saving gave no visible confirmation"
        assert panel.card.objectName() == "CardSaved", "the card did not flash"
        settle(2600)
        assert panel.card.objectName() == "Card", "the flash never faded"
        # Answered rows gone, the unanswerable one still there.
        assert [q for q, _ in panel._rows] == ["Preferred pronouns"], panel._rows

        # A question Sushi will never store is dropped rather than stranded.
        for _q, w in panel._rows:
            w.setPlainText("They/them") if isinstance(w, QPlainTextEdit)                 else w.setText("They/them")
        panel._save()
        settle(1600)
        assert not panel.isVisible(), "the panel stayed up with nothing to ask"

        for a in learning.bank():
            if a.key not in before:
                learning.forget(a.question)
        return "green confirm, answered rows clear, refused ones dropped"
    check("answers: saving confirms and clears", p_answer_panel_clears)

    def p_pdf_has_no_boxes():
        """Every character in a letter must be one the PDF font can draw."""
        from sushi.letters import printable

        awkward = ("route\u2011planning, end\u2011to\u2011end, 95\u202f000 km, "
                   "Scientist \u2013 caf\u00e9, \u201cquoted\u201d")
        out = printable(awkward)
        undrawable = [c for c in out
                      if c not in "\n\r\t" and not c.encode("cp1252", "ignore")]
        assert not undrawable, f"would render as boxes: {undrawable}"
        assert "route-planning" in out and "end-to-end" in out, out
        # Characters the font *can* draw are left alone.
        assert "\u2013" in out and "caf\u00e9" in out, out
        return "non-breaking hyphens folded; en dashes and accents kept"
    check("letters: the PDF draws every character", p_pdf_has_no_boxes)
    check("answers: unanswered questions become a place to answer them",
          p_answer_panel)

    def p_letter_on_demand():
        """A letter is written when the form asks, not on every application."""
        from sushi.letters import existing_letter, wants_cover_letter
        from sushi.models import Role as _R

        greenhouse = [{"key": "resume", "label": "Attach"},
                      {"key": "cover_letter", "label": "Attach"}]
        plain = [{"key": "resume", "label": "Attach"},
                 {"key": "first_name", "label": "First Name"}]
        assert wants_cover_letter(greenhouse), "missed a cover-letter field"
        assert not wants_cover_letter(plain), "invented a cover-letter field"
        assert existing_letter(_R(company="Nobody Ltd", title="Nothing")) is None

        # And the apply path only calls for one once the form has been read.
        import inspect

        from sushi import autofill as _af
        src = inspect.getsource(_af._run_direct)
        assert "wants_cover_letter" in src,             "the letter is still decided before the form is read"
        return "written only when the form has a field for it"
    check("letters: written on demand, not every time", p_letter_on_demand)

    def p_read_js_skips_chrome():
        """A nav search box is not an application question."""
        from sushi.directfill import READ_JS
        assert "JUNK" in READ_JS, "no filter for page chrome"
        assert "search" in READ_JS.lower()
        # One line: a newline inside a JS regex literal is a syntax error in
        # the page, and the whole read then returns nothing.
        junk = [l for l in READ_JS.splitlines() if "const JUNK" in l]
        assert len(junk) == 1 and junk[0].rstrip().endswith("/i;"), junk
        assert "forms.length" in READ_JS, "does not scope to the application form"
        return "scopes to the application form and skips search boxes"
    check("direct: the reader ignores page furniture", p_read_js_skips_chrome)

    filled_for_real = {"ok": False}
    if LIVE:
        before = len(RESULTS)
        check("apply: a real browser really fills the form", p_real_fill)
        filled_for_real["ok"] = RESULTS[before][0] == "PASS"
    else:
        print("  ..    apply: a real browser really fills the form "
              "(skipped — pass --live)")

    def p_learned():
        """Whatever reached the form must be in the answer bank."""
        if not LIVE:
            return "skipped — needs --live"
        if not filled_for_real["ok"]:
            # Nothing was typed, so there is nothing to have learned. This
            # is not a second fault, it is the first one again.
            return "skipped — the form was never filled"
        settle(3000)                      # let the watcher poll
        bank = {a.question.lower(): a.answer for a in learning.bank()}
        assert bank, "the watcher learned nothing from a filled form"
        assert not any("password" in q for q in bank), "a password was learned"
        return f"{len(bank)} answer(s) banked from the form"
    check("learning: a filled form teaches the bank", p_learned)

    # --- 4. tidy up ---------------------------------------------------------
    def p_cleanup():
        autofill.close_open_sessions()
        settle(500)
        left = autofill.orphaned_browsers()
        for _ in range(10):
            if not left:
                break
            time.sleep(0.5)
            left = autofill.orphaned_browsers()
        autofill.close_orphaned_browsers()
        return "every browser closed"
    check("browser: everything closes on exit", p_cleanup)

    w.hide()
    print()
    width = max(len(n) for _, n, _ in RESULTS) + 2
    for status, name, detail in RESULTS:
        print(f"  {'ok  ' if status == 'PASS' else 'FAIL'}  {name:<{width}} {detail}")
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed"
          + ("" if LIVE else "  (re-run with --live for the real agent)"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
