"""Sushi — upload a CV, get applications filled."""
from __future__ import annotations

import threading

__version__ = "0.1.0"

#: Stack reserved for every worker thread, in bytes.
#:
#: Scoring runs on fourteen threads over a table of about 1.2 million rows,
#: and on the default thread stack that is enough to run out of C stack. It
#: does not fail cleanly when it does: measured over ten runs of a 150,000-
#: role scoring loop, three died — one with a bare segfault, one with
#: ``SystemError: setobject.c: bad argument to internal function``, one with
#: ``TypeError: 'list_iterator' object is not callable`` raised from a line
#: that calls neither. Those are the interpreter running out of room, not
#: bugs in the code they point at, which is why the same traceback never
#: appeared twice and why it landed in a different function each time.
#:
#: The same loop with this much stack: zero failures in ten runs. 8MB still
#: failed, so the margin is not generous — this is a reservation of address
#: space, not of memory, and on 64-bit it costs nothing until it is touched.
#:
#: Raised from 16MB on 2026-11-05. 16MB was chosen because it passed ten
#: runs of a test; it is still failing in use. Two crashes a day apart, both
#: ``Windows fatal exception: access violation`` on a worker thread, landing
#: in unrelated allocation-heavy C code each time — once inside OpenSSL's
#: read during a sweep, once inside the JSON scanner parsing the company
#: corpus — plus a ``TypeError: 'list' object does not support the
#: asynchronous context manager protocol`` raised from a line holding no
#: ``async with``. That is the same signature this constant was created
#: for, so the first thing to try is the same remedy with a margin that is
#: not borderline. 32 pool threads at 64MB is 2GB of *reserved* address
#: space out of 128TB, and nothing is committed until a frame touches it.
#:
#: It is a wider margin on a known failure mode, not a proven fix. If a
#: crash dump still shows a worker thread faulting after this, the cause is
#: not stack depth and the next step is a native debugger rather than
#: another doubling.
#:
#: Set here rather than at an entry point because the QA scripts and the
#: tests import the package directly, and a crash under threads is exactly
#: what they exist to catch.
THREAD_STACK_BYTES = 64 * 1024 * 1024

try:
    threading.stack_size(THREAD_STACK_BYTES)
except (ValueError, RuntimeError):      # platform refuses: keep the default
    pass


def apply_qt_stack_size() -> None:
    """Give Qt's worker threads the same room.

    ``threading.stack_size`` covers the pools in :mod:`sushi.discovery` and
    :mod:`sushi.jobsdb`, which is most of the work — but not the Qt threads
    the UI runs jobs on, and ``jobsdb.rescore`` scores a million rows on one
    of those. Called from the app at startup; harmless anywhere else.
    """
    try:
        from PyQt6.QtCore import QThreadPool
    except ImportError:                 # headless: nothing to configure
        return
    QThreadPool.globalInstance().setStackSize(THREAD_STACK_BYTES)
