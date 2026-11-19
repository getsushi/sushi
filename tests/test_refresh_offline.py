"""A refresh that cannot reach a board must not delete that board's jobs.

``read_board`` answers [] on any failure, and ``store_board`` reads [] as
"every posting here has closed". Together they meant a refresh run while
the connection dropped — or while boards were rate-limiting — deleted the
jobs of every board it failed to reach, and recorded each one as empty, so
it was not read again for a week. On the machine this was found on, 15,784
of 15,788 boards had been emptied that way and the feed was down to 25 jobs.
"""
from __future__ import annotations

import sys
import threading
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sushi import ats, jobsdb  # noqa: E402

BOARD = {"name": "Acme", "ats": "greenhouse", "token": "acme"}
JOBS = [{"title": f"Engineer {i}", "url": f"https://example.test/acme/{i}",
         "location": "Remote"} for i in range(3)]


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A job database this module will open, and nothing else."""
    monkeypatch.setattr(jobsdb, "JOBS_DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(jobsdb, "_local", threading.local())
    jobsdb.store_board(BOARD, JOBS, now=1.0)       # read long ago, three jobs
    return tmp_path


def _reader_raising(exc):
    def reader(_token):
        raise exc
    return reader


def _refresh():
    return jobsdb.refresh([BOARD], include_global=False, workers=1)


def _held() -> int:
    return jobsdb.conn().execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


@pytest.mark.parametrize("exc", [
    urllib.error.URLError("network is unreachable"),
    TimeoutError("timed out"),
    ConnectionResetError("reset by peer"),
    urllib.error.HTTPError("https://x", 429, "Too Many Requests", {}, None),
    urllib.error.HTTPError("https://x", 503, "Unavailable", {}, None),
])
def test_unreachable_board_keeps_its_jobs(db, monkeypatch, exc):
    monkeypatch.setitem(ats.READERS, "greenhouse", _reader_raising(exc))
    _refresh()
    assert _held() == 3
    # And it is still due, so the next refresh tries it again.
    assert jobsdb.due_boards([BOARD]) == [BOARD]


def test_board_that_is_gone_is_emptied(db, monkeypatch):
    """A 404 is the provider saying there is no board: that still clears it."""
    gone = urllib.error.HTTPError("https://x", 404, "Not Found", {}, None)
    monkeypatch.setitem(ats.READERS, "greenhouse", _reader_raising(gone))
    _refresh()
    assert _held() == 0


def test_board_that_closed_everything_is_emptied(db, monkeypatch):
    monkeypatch.setitem(ats.READERS, "greenhouse", lambda _token: [])
    _refresh()
    assert _held() == 0


def test_read_board_is_lenient_unless_asked(monkeypatch):
    """Every other caller still gets [] for a failure, as before."""
    monkeypatch.setitem(ats.READERS, "greenhouse",
                        _reader_raising(TimeoutError("timed out")))
    assert ats.read_board("greenhouse", "acme") == []
    with pytest.raises(ats.BoardUnavailable):
        ats.read_board("greenhouse", "acme", strict=True)
