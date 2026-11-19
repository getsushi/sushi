"""Make the app say why it died.

Sushi used to fail the worst way a desktop app can: the window disappeared and
left nothing behind. ``config.LOG_PATH`` existed but nothing ever wrote to it,
so a crash during a 1,000-board scan was unreportable — there was literally
nothing to look at afterwards.

Three separate failure modes, three separate catches:

1. **An unhandled Python exception inside a Qt slot.** PyQt6 does not let this
   propagate: it prints to stderr and calls ``abort()``. Launched from a
   shortcut or ``pythonw`` there *is* no stderr, so the process vanishes
   mid-click with no dialog and no message. Replacing ``sys.excepthook`` both
   records the traceback and stops the abort — PyQt only aborts when the hook
   is still the default one.
2. **A hard crash** in Qt or a C extension. Python can't catch that, but
   ``faulthandler`` can write the native stack of every thread on the way
   down, which is the only artefact that survives a segfault.
3. **Qt's own warnings** (layout loops, "cannot create children for a parent
   in a different thread", painter errors) go to a handler nobody installs,
   so they are invisible in a windowed build. They are the early warning for
   the first two.

Everything lands in one file, kept small and rotated, next to the database.
"""
from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Callable

log = logging.getLogger("sushi")

#: Set by :func:`install` so the UI can offer "show me the log".
LOG_FILE: Path | None = None

_on_error: Callable[[str, str], None] | None = None
_crash_stream = None


def install(log_path: Path, *, level: int = logging.INFO,
            on_error: Callable[[str, str], None] | None = None) -> Path:
    """Start logging and catch what would otherwise kill the app silently.

    ``on_error`` is called with (title, detail) for an unhandled exception, so
    the window can show it. It is optional and called defensively: the whole
    point of this module is to be the thing that still works when something
    else is broken.
    """
    global LOG_FILE, _on_error, _crash_stream
    LOG_FILE = Path(log_path)
    _on_error = on_error

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return LOG_FILE

    root = logging.getLogger()
    root.setLevel(level)
    if not any(getattr(h, "_sushi", False) for h in root.handlers):
        handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(threadName)-18s %(name)s: %(message)s"))
        handler._sushi = True  # type: ignore[attr-defined]
        root.addHandler(handler)
        if sys.stderr is not None:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
            stream._sushi = True  # type: ignore[attr-defined]
            root.addHandler(stream)

    # A segfault cannot be caught in Python; this is the only trace it leaves.
    # Kept open for the life of the process on purpose — faulthandler writes
    # to the raw file descriptor while the interpreter is already dying.
    try:
        _crash_stream = open(LOG_FILE.with_name("sushi-crash.log"), "a",
                             encoding="utf-8")
        # A header per run, because the file is appended to for ever and a
        # faulthandler dump carries no timestamp. Without this a crash log
        # holding seven dumps cannot be split into runs at all — and the line
        # numbers in them refer to whatever the code was that day, so reading
        # the old ones against today's source is actively misleading.
        _crash_stream.write(
            f"\n===== run started {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"· pid {os.getpid()} · python {sys.version.split()[0]} =====\n")
        _crash_stream.flush()
        faulthandler.enable(file=_crash_stream, all_threads=True)
    except OSError:
        pass

    sys.excepthook = _excepthook
    _install_qt_handler()
    log.info("--- sushi starting, logging to %s ---", LOG_FILE)
    return LOG_FILE


def _excepthook(exc_type, exc, tb) -> None:
    """Log an unhandled exception instead of letting PyQt abort the process."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    detail = "".join(traceback.format_exception(exc_type, exc, tb))
    log.critical("Unhandled exception\n%s", detail)
    if _on_error is not None:
        try:
            _on_error(f"{exc_type.__name__}: {exc}", detail)
        except Exception:        # the reporter must never raise on its way out
            log.exception("The error reporter itself failed")


def _install_qt_handler() -> None:
    """Route Qt's own warnings into the log.

    Layout loops and cross-thread widget access announce themselves here long
    before they turn into a crash, and in a windowed build nobody ever sees
    them.
    """
    try:
        from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:
        return

    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }
    qt_log = logging.getLogger("sushi.qt")

    def handler(mode, context, message) -> None:
        qt_log.log(levels.get(mode, logging.INFO), "%s", message)

    qInstallMessageHandler(handler)


def tail(lines: int = 200) -> str:
    """The end of the log, for showing the user what just happened."""
    if LOG_FILE is None or not LOG_FILE.exists():
        return "No log yet."
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"Could not read the log: {e}"
    return "\n".join(text.splitlines()[-lines:])
