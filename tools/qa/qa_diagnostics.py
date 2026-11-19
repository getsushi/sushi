"""The layer that is supposed to still work when everything else is broken.

Sushi has crashed natively five times this week, and every diagnosis rested
entirely on this module: the rotating log, the crash file faulthandler
writes into while the interpreter is already dying, and the run header that
finally made a dump attributable to a run. None of it had a single test.

That is the wrong way round. A logger that silently fails is worse than no
logger, because the absence of a line reads as the absence of an event — and
during the crash hunt it did exactly that: seven dumps in one undated file
with no way to tell which run, or which version of the code, each belonged
to.

    python qa_diagnostics.py
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sushi-diag-"))
os.environ["LOCALAPPDATA"] = str(_TMP)
os.environ["XDG_DATA_HOME"] = str(_TMP)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, fn) -> None:
    try:
        RESULTS.append(("PASS", name, str(fn() or "")[:150]))
        print(f"  ok    {name}")
    except Exception as e:
        RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=2)


def main() -> int:
    from sushi import diagnostics

    log_path = _TMP / "logs" / "sushi.log"

    def t_install():
        """Logging starts, in a directory that did not exist a moment ago."""
        got = diagnostics.install(log_path)
        assert got == log_path, got
        assert log_path.exists(), "no log file was created"
        assert diagnostics.LOG_FILE == log_path, diagnostics.LOG_FILE
        logging.getLogger("sushi.qa").info("hello from the suite")
        text = log_path.read_text(encoding="utf-8", errors="replace")
        assert "hello from the suite" in text, "a logged line never arrived"
        assert "sushi starting" in text, "the start of the run is not marked"
        return f"{log_path.name} created and written to"
    check("logging: a log file is created and written", t_install)

    def t_run_header():
        """The crash file says which run, and which interpreter, wrote it.

        A faulthandler dump carries no timestamp, and this file is appended
        to for ever. Without a header, seven dumps are seven anonymous
        stacks — and their line numbers refer to whatever the code was that
        day, which makes reading them against today's source misleading
        rather than merely useless.
        """
        crash = log_path.with_name("sushi-crash.log")
        assert crash.exists(), "no crash file was opened"
        text = crash.read_text(encoding="utf-8", errors="replace")
        assert "run started" in text, "no run header was written"
        assert str(os.getpid()) in text, "the header does not name the process"
        assert sys.version.split()[0] in text, \
            "the header does not name the interpreter"
        return text.strip().splitlines()[-1][:80]
    check("crashes: each run is marked in the crash file", t_run_header)

    def t_unhandled_exception_is_logged_not_fatal():
        """An unhandled exception is recorded, and the app stays up.

        PyQt aborts the process on an exception that escapes a slot. That
        turns a one-line bug into a vanished window with nothing written
        down anywhere.
        """
        seen: list[tuple[str, str]] = []
        diagnostics.install(log_path, on_error=lambda t, d: seen.append((t, d)))
        try:
            raise ValueError("a deliberate test failure")
        except ValueError:
            sys.excepthook(*sys.exc_info())

        text = log_path.read_text(encoding="utf-8", errors="replace")
        assert "a deliberate test failure" in text, \
            "an unhandled exception was not logged"
        assert "Traceback" in text, "the traceback was not kept"
        assert seen and "ValueError" in seen[-1][0], seen
        return f"logged, and handed to the window as {seen[-1][0][:40]!r}"
    check("errors: an unhandled exception is logged, not fatal",
          t_unhandled_exception_is_logged_not_fatal)

    def t_keyboard_interrupt_passes_through():
        """Ctrl-C is not an application error and must not be swallowed."""
        before = log_path.read_text(encoding="utf-8", errors="replace")
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            try:
                sys.excepthook(*sys.exc_info())
            except SystemExit:
                pass
        after = log_path.read_text(encoding="utf-8", errors="replace")
        assert "KeyboardInterrupt" not in after[len(before):], \
            "Ctrl-C was logged as a crash"
        return "passed through to the default hook"
    check("errors: Ctrl-C is not treated as a crash",
          t_keyboard_interrupt_passes_through)

    def t_tail():
        """The log can be read back, for the diagnostics panel."""
        for i in range(50):
            logging.getLogger("sushi.qa").info("line %d", i)
        text = diagnostics.tail(10)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert lines, "tail returned nothing"
        assert len(lines) <= 12, f"asked for 10 lines, got {len(lines)}"
        assert "line 49" in text, "the newest line is missing from the tail"
        return f"{len(lines)} lines, newest last"
    check("logging: the tail can be read back", t_tail)

    def t_rotation_is_bounded():
        """The log cannot grow without bound across a long-running install."""
        handlers = [h for h in logging.getLogger().handlers
                    if getattr(h, "_sushi", False)]
        rotating = [h for h in handlers if hasattr(h, "maxBytes")]
        assert rotating, "the log file handler does not rotate"
        h = rotating[0]
        assert 0 < h.maxBytes <= 20_000_000, h.maxBytes
        assert h.backupCount >= 1, h.backupCount
        return f"rotates at {h.maxBytes / 1e6:.0f} MB, {h.backupCount} kept"
    check("logging: the log file is bounded", t_rotation_is_bounded)

    def t_install_twice_does_not_double_log():
        """Opening the window twice must not write everything twice."""
        diagnostics.install(log_path)
        diagnostics.install(log_path)
        marker = "double-logging probe"
        logging.getLogger("sushi.qa").info(marker)
        text = log_path.read_text(encoding="utf-8", errors="replace")
        assert text.count(marker) == 1, \
            f"logged {text.count(marker)} times — handlers are accumulating"
        return "one handler, however many times install runs"
    check("logging: installing twice does not duplicate lines",
          t_install_twice_does_not_double_log)

    def t_survives_an_unwritable_directory():
        """A log it cannot open must not stop the app from starting."""
        blocked = Path("Z:/definitely/not/a/real/path/sushi.log")
        got = diagnostics.install(blocked)
        assert got == blocked, got
        logging.getLogger("sushi.qa").info("still alive")
        diagnostics.install(log_path)          # put it back
        return "an unwritable path is survived, not fatal"
    check("logging: an unwritable location does not stop startup",
          t_survives_an_unwritable_directory)

    def t_faulthandler_really_writes():
        """A hard crash leaves a stack behind — the whole point of the file.

        Verified in a child process, because the only way to prove it is to
        crash: this sends SIGSEGV to a real interpreter with the module
        installed and then reads what it managed to write.
        """
        child = _TMP / "child"
        code = (
            "import sys, ctypes; sys.path.insert(0, %r);\n"
            "from pathlib import Path\n"
            "from sushi import diagnostics\n"
            "diagnostics.install(Path(%r) / 'sushi.log')\n"
            "def deep():\n"
            "    ctypes.string_at(0)\n"
            "deep()\n" % (str(Path(__file__).resolve().parents[2]), str(child))
        )
        child.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run([sys.executable, "-c", code],
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode != 0, "the child did not actually crash"
        crash = child / "sushi-crash.log"
        assert crash.exists(), "a native crash left no crash file"
        text = crash.read_text(encoding="utf-8", errors="replace")
        assert "run started" in text, "the dump is not attributable to a run"
        assert "Current thread" in text or "Stack" in text, \
            f"no stack was written: {text[:200]!r}"
        assert "in deep" in text, \
            "the dump does not name the frame that crashed"
        return "a real SIGSEGV left an attributable stack on disk"
    check("crashes: a native crash writes a stack we can read",
          t_faulthandler_really_writes)

    print()
    width = max(len(n) for _, n, _ in RESULTS) + 2
    for status, name, detail in RESULTS:
        print(f"  {'ok  ' if status == 'PASS' else 'FAIL'}  {name:<{width}} {detail}")
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
