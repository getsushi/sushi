"""Let a form being filled have the machine.

The background job search reads thousands of careers pages through a pool
of threads. Running alongside an application it competes for the network
and the CPU the browser needs, and a form that should take under a minute
crawls — which reads as Sushi having stopped. So while anything is being
filled, board readers wait before their next page, and carry on after.
Nothing is cancelled; the search only finishes a little later.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

_lock = threading.Lock()
_filling = 0
#: Never hold a reader for ever: a fill that crashed without saying so must
#: not leave the search paused for the rest of the session.
_MAX_WAIT = 600.0


@contextmanager
def filling():
    """Held for the length of one application."""
    global _filling
    with _lock:
        _filling += 1
    try:
        yield
    finally:
        with _lock:
            _filling -= 1


def busy() -> bool:
    return _filling > 0


def wait_while_filling(should_stop=None) -> None:
    """Called by a background reader before it fetches the next page."""
    start = time.monotonic()
    while _filling > 0 and time.monotonic() - start < _MAX_WAIT:
        if should_stop and should_stop():
            return
        time.sleep(0.5)
