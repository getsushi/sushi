"""Opening SQLite so that a bad file or a busy writer never takes the app down.

Two things crashed Sushi on real machines:

- ``database is locked``. A refresh writing from a pool of threads while the
  UI saves a setting. The connect timeout covers most of it, not all, and an
  uncaught OperationalError on the UI thread closes the window.
  :class:`RetryConnection` retries those with a short backoff.
- A damaged file (power cut mid-write, a sync tool copying it half-way,
  ``file is not a database``). Every launch died on the first query. Now the
  file is moved aside as ``<name>.corrupt-<time>`` (never deleted, so it can
  be recovered by hand) and a fresh one is started.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

_RETRIES = 8
_BROKEN = ("malformed", "not a database", "file is encrypted",
           "disk image", "unsupported file format")


def _locked(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _retry(fn, *args):
    delay = 0.1
    for attempt in range(_RETRIES):
        try:
            return fn(*args)
        except sqlite3.OperationalError as exc:
            if not _locked(exc) or attempt == _RETRIES - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 2.0)


class RetryConnection(sqlite3.Connection):
    """A connection whose writes wait out a lock instead of raising."""

    def execute(self, *a):            # type: ignore[override]
        return _retry(super().execute, *a)

    def executemany(self, *a):        # type: ignore[override]
        return _retry(super().executemany, *a)

    def executescript(self, *a):      # type: ignore[override]
        return _retry(super().executescript, *a)

    def commit(self):                 # type: ignore[override]
        return _retry(super().commit)


def is_broken(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.DatabaseError) and any(
        b in str(exc).lower() for b in _BROKEN)


def quarantine(path: Path) -> None:
    """Move a damaged database (and its WAL files) out of the way."""
    stamp = time.strftime("%Y-%m-%dT%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            try:
                p.rename(Path(f"{path}.corrupt-{stamp}{suffix}"))
            except OSError:
                p.unlink(missing_ok=True)
    log.error("database %s was damaged; moved aside and starting fresh", path.name)


def open_db(path: Path, timeout: float,
            setup: Callable[[sqlite3.Connection], None]) -> sqlite3.Connection:
    """Connect, run ``setup``; on a damaged file, set it aside and retry once."""
    for attempt in (0, 1):
        c = sqlite3.connect(path, timeout=timeout, factory=RetryConnection)
        c.row_factory = sqlite3.Row
        try:
            c.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
            # Reading the schema is enough to fail on a file that is not a
            # database; a full integrity check on a million rows is not.
            c.execute("SELECT count(*) FROM sqlite_master").fetchone()
            setup(c)
            return c
        except sqlite3.DatabaseError as exc:
            c.close()
            if attempt or not is_broken(exc):
                raise
            quarantine(path)
    raise RuntimeError("unreachable")
