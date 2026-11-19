"""Does the refresh crash the process on its own, with no Qt in it?

The app has died four times with a native access violation — inside
``python312.dll``, from pure-Python frames (``re._compile``, ``json.dumps``,
``enum.value``) that cannot fault by themselves. The faulting frame is
wherever execution happened to be, which means something had already
corrupted memory, and the dumps alone cannot say what.

This separates the two candidates. It runs the same board reading, scoring
and SQLite writing the real refresh does, at the same concurrency, for as
long as you ask — with **no QApplication, no widgets and no cross-thread Qt
signals** in the process.

    python qa_soak.py [boards] [--loops N]

If this crashes, the fault is in the fetch/score/store layer: TLS across
threads, the SQLite writes, or the regex scoring. If it runs clean while the
app keeps dying, the fault is in the Qt interaction — the queued signals
carrying Python objects between the refresh thread and the UI thread, and
the few hundred widgets the UI throws away and rebuilds while they arrive.

Writes to a throwaway data directory, so it leaves nothing behind.
"""
from __future__ import annotations

import faulthandler
import os
import sys
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sushi-soak-"))
os.environ["LOCALAPPDATA"] = str(_TMP)
os.environ["XDG_DATA_HOME"] = str(_TMP)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

CRASH = _TMP / "soak-crash.log"
_stream = open(CRASH, "a", encoding="utf-8")
faulthandler.enable(file=_stream, all_threads=True)


def soak_with_ui(how_many: int) -> int:
    """The same refresh, with the real window in the process.

    This is the half that matters. Without Qt the refresh is clean for as
    long as you care to run it; the app died eight seconds into the same
    work. The difference is here: a queued cross-thread signal carrying
    Python objects into a UI thread that rebuilds a few hundred widgets
    whenever the feed changes.
    """
    os.environ.setdefault("SUSHI_NO_AUTOSCAN", "1")
    from PyQt6.QtCore import QEventLoop
    from PyQt6.QtWidgets import QApplication

    from sushi import jobsdb, storage
    from sushi.models import Experience, Profile
    from sushi.ui import theme
    from sushi.ui.fonts import DISPLAY, load_fonts

    app = QApplication(sys.argv)
    theme.refresh_stylesheet(display=load_fonts().get("display", DISPLAY))
    from sushi.ui.main_window import MainWindow

    storage.save_profile(Profile(
        full_name="Rowan Ellis Hartley", email="r.hartley@example.com",
        headline="Senior Operations Analyst", summary="Ops and data analyst.",
        skills=["Python", "SQL"], seniority="ic",
        experience=[Experience(company="Kestrel", title="Analyst", start="2022")],
        target_titles=["Operations Analyst", "Data Analyst"],
        target_locations=["Remote (UK)", "London", "Bristol"]))
    storage.set_setting("onboarded", "1")

    w = MainWindow()
    w.resize(1440, 920)
    w.move(-7000, -7000)
    w.show()

    def settle(ms: int) -> None:
        end = time.time() + ms / 1000
        while time.time() < end:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)

    settle(800)
    w.navigate("Dashboard")          # the page that rebuilds on every change
    settle(400)

    jobsdb.forget_all()              # a cold start: every job is new
    print(f"ui soak: {how_many:,} boards into a live window, cold database",
          flush=True)

    t0 = time.time()
    w.start_scan()
    # The refresh runs on a worker; this is the UI thread doing what it does
    # during one — draining queued arrivals and rebuilding pages.
    deadline = t0 + 600
    while w.scanning() and time.time() < deadline:
        settle(250)
        if jobsdb.stats().boards_read >= how_many:
            w.stop_scan()
            break
    settle(1500)
    st = jobsdb.stats()
    print(f"ui soak: {st.boards_read:,} boards, {st.jobs:,} jobs, "
          f"{len(w.matches)} in the feed, {time.time() - t0:.0f}s", flush=True)
    w.close()
    settle(300)
    dumped = CRASH.stat().st_size if CRASH.exists() else 0
    print(f"crash log is {dumped} bytes "
          f"({'EMPTY — clean run' if not dumped else 'NOT empty'})")
    return 1 if dumped else 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    how_many = int(args[0]) if args else 2000
    loops = 1
    if "--loops" in sys.argv:
        loops = int(sys.argv[sys.argv.index("--loops") + 1])

    if "--ui" in sys.argv:
        return soak_with_ui(how_many)

    from sushi import jobsdb, storage
    from sushi.discovery import boards
    from sushi.models import Experience, Profile

    profile = Profile(
        full_name="Rowan Ellis Hartley", email="r.hartley@example.com",
        headline="Senior Operations Analyst", summary="Ops and data analyst.",
        skills=["Python", "SQL"], seniority="ic",
        experience=[Experience(company="Kestrel", title="Analyst", start="2022")],
        target_titles=["Operations Analyst", "Data Analyst"],
        target_locations=["Remote (UK)", "London", "Bristol"])
    storage.save_profile(profile)

    all_boards = boards()
    print(f"corpus: {len(all_boards):,} boards · reading {how_many:,} "
          f"per loop · {loops} loop(s)")
    print(f"crash log: {CRASH}\n")

    streamed = 0

    def on_jobs(roles) -> None:
        # Stands in for the signal the app emits to its UI thread: the same
        # objects are built and handed on, minus Qt.
        nonlocal streamed
        streamed += len(roles)

    for loop in range(1, loops + 1):
        t0 = time.time()
        st = jobsdb.refresh(
            all_boards, profile=profile, workers=14, limit=how_many,
            on_jobs=on_jobs,
            on_progress=lambda m: print(f"   {m}", flush=True))
        took = time.time() - t0
        print(f"loop {loop}: {st.jobs:,} jobs on file, {st.boards_read:,} boards "
              f"read, {streamed:,} streamed, {took:.0f}s", flush=True)
        # Re-scoring is the other half of the work the app does, and it is
        # the pass that touches every row.
        t0 = time.time()
        n = jobsdb.rescore(profile)
        print(f"loop {loop}: re-scored {n:,} rows in {time.time() - t0:.0f}s",
              flush=True)

    size = jobsdb.JOBS_DB_PATH.stat().st_size / 1e6
    print(f"\nsurvived: {st.jobs:,} jobs, {size:.0f} MB, no crash")
    dumped = CRASH.stat().st_size if CRASH.exists() else 0
    print(f"crash log is {dumped} bytes "
          f"({'EMPTY — clean run' if not dumped else 'NOT empty — look at it'})")
    return 1 if dumped else 0


if __name__ == "__main__":
    code = main()
    # Ended outright. The verdict is already printed and the crash log
    # already checked; Qt tearing a window down after that can only turn a
    # clean result into a confusing exit status.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
