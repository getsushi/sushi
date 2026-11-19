"""Stress test for discovery — the thing that was crashing the app.

The crash was a native access violation inside OpenSSL: `urlopen` builds a
fresh SSLContext per HTTPS call, building one on Windows loads the system
certificate store, and fourteen scanner threads doing that at once took the
process down with no Python traceback. The fix is a single shared context.

This proves it three ways:

1. **Hammer** — many threads fetching concurrently through `ats.fetch_json`,
   which is the exact call pattern that crashed, run far harder than a real
   scan does.
2. **Full scans** — complete `find_roles` runs end to end, repeated, checking
   the result is sane each time.
3. **In the app** — a scan driven through the real window while the UI is
   resized and the rails are toggled underneath it.

Run from the sushi/ directory:  python qa_scan.py [--rounds N]
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="sushi-scanqa-")

ROUNDS = 2
if "--rounds" in sys.argv:
    ROUNDS = int(sys.argv[sys.argv.index("--rounds") + 1])

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, fn):
    t0 = time.time()
    try:
        detail = fn()
        RESULTS.append(("PASS", name, f"{detail}  [{time.time() - t0:.0f}s]"))
    except Exception as e:
        RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"))
        traceback.print_exc(limit=4)


def profile():
    from sushi.models import Experience, Profile
    return Profile(
        full_name="Rowan Ellis Hartley", headline="Senior Operations Analyst",
        skills=["Python", "SQL", "Power BI", "Route optimisation"], seniority="ic",
        target_titles=["Senior Operations Analyst", "Senior Data Analyst",
                       "Analytics Engineer"],
        target_locations=["Remote (UK)", "London", "Bristol"],
        experience=[Experience(company="Kestrel Freight Ltd",
                               title="Senior Operations Analyst", start="2022")])


def main() -> int:
    import faulthandler
    crash = open("qa_scan_crash.log", "w", encoding="utf-8")
    faulthandler.enable(file=crash, all_threads=True)

    from sushi import ats
    from sushi.discovery import boards, find_roles

    # --- 1. the shared TLS context ----------------------------------------
    def t_context():
        first = ats.ssl_context()
        seen = []

        def grab():
            seen.append(ats.ssl_context())

        threads = [threading.Thread(target=grab) for _ in range(32)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(c is first for c in seen), "more than one TLS context was built"
        return f"one context across {len(seen)} threads"
    check("tls: a single shared context", t_context)

    # --- 2. hammer the exact call that crashed -----------------------------
    def t_hammer():
        """Far more concurrent TLS handshakes than a real scan performs."""
        targets = [r for r in boards() if r.get("ats") and r.get("token")][:160]
        assert targets, "no boards to read"
        ok, failed = 0, 0

        def read(rec):
            from sushi.ats import read_board
            try:
                return len(read_board(rec["ats"], rec["token"]))
            except Exception:
                return -1

        with ThreadPoolExecutor(max_workers=24) as pool:
            for fut in as_completed([pool.submit(read, r) for r in targets]):
                if fut.result() >= 0:
                    ok += 1
                else:
                    failed += 1
        # Individual boards 404 or rate-limit all the time; that is not the
        # thing under test. Surviving the concurrency is.
        assert ok > 0, "every board read failed — network down?"
        return f"{ok} boards read, {failed} errored, 24 threads, no crash"
    check("tls: 160 boards over 24 threads", t_hammer)

    # --- 3. full scans ------------------------------------------------------
    p = profile()
    counts = []

    def t_scan():
        steps = []
        roles = find_roles(p, on_progress=lambda m: steps.append(m))
        counts.append(len(roles))
        assert steps, "the scan never reported progress"
        for r in roles[:40]:
            assert r.company and r.title, f"malformed role: {r}"
            assert r.url.startswith("http"), r.url
            assert 0 <= r.match <= 100, r.match
            assert r.score > 0, r.score
        urls = [r.url for r in roles]
        assert len(urls) == len(set(urls)), "duplicate roles in one scan"
        assert roles == sorted(roles, key=lambda r: -r.score), "not sorted best first"
        return f"{len(roles)} roles, {len(steps)} progress updates, sorted and unique"

    for i in range(ROUNDS):
        check(f"scan: full run {i + 1}/{ROUNDS}", t_scan)

    def t_stable():
        assert len(counts) >= 2, "need two runs to compare"
        lo, hi = min(counts), max(counts)
        # Boards change between runs; an order-of-magnitude swing would mean
        # the scan is dropping work rather than finding different jobs.
        assert hi == 0 or lo >= hi * 0.5, f"run-to-run counts swung: {counts}"
        return f"counts {counts}"
    check("scan: repeat runs agree", t_stable)

    # --- 4. cancellation ----------------------------------------------------
    def t_stop():
        stop = {"v": False}
        done = {}

        def run():
            done["roles"] = find_roles(p, should_stop=lambda: stop["v"])

        t = threading.Thread(target=run)
        t.start()
        time.sleep(6)
        stop["v"] = True
        t.join(timeout=90)
        assert not t.is_alive(), "a cancelled scan never returned"
        return f"stopped cleanly with {len(done.get('roles', []))} roles so far"
    check("scan: stop actually stops", t_stop)

    # --- 5. through the real window ----------------------------------------
    def t_in_app():
        from PyQt6.QtCore import QEventLoop
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication(sys.argv)
        from sushi import storage
        from sushi.ui import theme
        from sushi.ui.fonts import DISPLAY, load_fonts

        theme.refresh_stylesheet(display=load_fonts().get("display", DISPLAY))
        from sushi.ui.main_window import MainWindow

        storage.save_profile(p)
        storage.set_setting("onboarded", "1")
        w = MainWindow()
        w.resize(1440, 920)
        w.move(-7000, -7000)
        w.show()

        def settle(ms):
            end = time.time() + ms / 1000
            while time.time() < end:
                app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)

        settle(600)
        # Discovery is the window's now, not a page's — Browse jobs is gone
        # and the Dashboard only reflects the scan it is told about.
        page = w.scan_page()
        w.start_scan()
        settle(300)
        assert w.scanning(), "start_scan did not start one"
        assert page.scan_btn.text() == "Stop",             f"dashboard button says {page.scan_btn.text()!r} during a scan"
        assert page.scan_lbl.isVisible(), "no progress line during a scan"
        t0, step = time.time(), 0
        while time.time() - t0 < 420:
            settle(1500)
            step += 1
            # Shake the UI while the scan runs — resize, collapse, navigate.
            if step % 3 == 0:
                w.resize(1180 if step % 6 == 0 else 1440, 920)
            if step % 5 == 0:
                w.sidebar.toggle()
            if step % 7 == 0:
                w.navigate("Tracker")
                settle(200)
                w.navigate(page.title)
            if not w.scanning():
                break
        settle(2500)
        found = len(w.matches)
        assert not w.scanning(), "the scan never finished"
        w.navigate("Dashboard")
        settle(300)
        assert page.scan_btn.text() == "Look again",             f"button stuck on {page.scan_btn.text()!r} after the scan"
        assert not page.scan_lbl.isVisible(), "progress line left on screen"
        w.hide()
        assert found >= 0
        return (f"{found} matches, survived resize/collapse/navigate mid-scan; "
                f"the dashboard followed the state both ways")
    check("app: scan under a moving UI", t_in_app)

    print()
    width = max(len(n) for _, n, _ in RESULTS) + 2
    for status, name, detail in RESULTS:
        print(f"  {'ok  ' if status == 'PASS' else 'FAIL'}  {name:<{width}} {detail}")
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    crash.flush()
    size = os.path.getsize("qa_scan_crash.log")
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    print(f"native crash dump: {size} bytes"
          f"{' — A CRASH WAS CAUGHT' if size else ' (empty, as it should be)'}")
    return 1 if failed or size else 0


if __name__ == "__main__":
    raise SystemExit(main())
