"""The ``resumes`` table, on a machine that already had one.

``CREATE TABLE IF NOT EXISTS`` does nothing to a table that already exists.
That is why ``_ADDED_COLUMNS`` is in ``storage`` at all — and ``resumes``
still shipped assuming a clean slate. A real install turned out to carry a
``resumes`` of its own from an earlier take on the same feature: keyed on
``path``, no ``id``, and a composite ``stamp`` of "mtime:size" where this
one keeps ``read_mtime``. The create was a no-op, every query died on
``no such column: id``, and the window could not open at all.

What the old table holds is the user's — the CV, its label, the profile it
was read into — so the rebuild has to carry it, not start fresh.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sushi import storage  # noqa: E402

#: Exactly the table found on the machine this was diagnosed on.
LEGACY_DDL = """
CREATE TABLE resumes (
    path       TEXT PRIMARY KEY,
    label      TEXT NOT NULL DEFAULT '',
    profile    TEXT,
    stamp      TEXT NOT NULL DEFAULT '',
    added_at   TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A database file this module will open, and nothing else."""
    path = tmp_path / "sushi.db"
    monkeypatch.setattr(storage, "DB_PATH", path)
    monkeypatch.setattr(storage, "_LOCAL", type(storage._LOCAL)())
    return path


def _legacy_row(db: Path, cv: Path, *, stamp: str, label: str = "My CV",
                profile: dict | None = None) -> None:
    c = sqlite3.connect(db)
    c.executescript(LEGACY_DDL)
    c.execute("INSERT INTO resumes VALUES(?,?,?,?,?,?)",
              (str(cv), label,
               json.dumps(profile) if profile else None,
               stamp, "2026-11-04 20:26:01", "2026-11-04 20:26:01"))
    c.commit()
    c.close()


def test_a_legacy_table_is_rebuilt_not_fatal(db, tmp_path):
    cv = tmp_path / "dummy_cv.pdf"
    cv.write_bytes(b"%PDF-1.4\n")
    _legacy_row(db, cv, stamp=f"{int(cv.stat().st_mtime)}:4395",
                profile={"full_name": "Rowan Ellis Hartley"})

    resumes = storage.list_resumes()          # this used to raise

    assert len(resumes) == 1
    r = resumes[0]
    assert r.id > 0, "the rebuilt table must have the id the module queries"
    assert r.path == str(cv)
    assert r.label == "My CV"
    assert r.read and r.profile.full_name == "Rowan Ellis Hartley", \
        "the profile the CV was read into was thrown away"


def test_the_read_stamp_survives_so_the_cv_is_not_re_read(db, tmp_path):
    """``stamp`` was "<mtime>:<size>"; losing it means a wasted model call."""
    cv = tmp_path / "dummy_cv.pdf"
    cv.write_bytes(b"%PDF-1.4\n")
    _legacy_row(db, cv, stamp=f"{int(cv.stat().st_mtime)}:9",
                profile={"full_name": "Rowan Ellis Hartley"})

    r = storage.list_resumes()[0]
    assert not r.changed(), "a CV nobody edited was reported as changed"


def test_a_profile_with_no_usable_stamp_is_not_called_changed(db, tmp_path):
    cv = tmp_path / "dummy_cv.pdf"
    cv.write_bytes(b"%PDF-1.4\n")
    _legacy_row(db, cv, stamp="", profile={"full_name": "Rowan Ellis Hartley"})

    r = storage.list_resumes()[0]
    assert r.read
    assert not r.changed()


def test_the_old_table_is_not_left_behind(db, tmp_path):
    cv = tmp_path / "dummy_cv.pdf"
    cv.write_bytes(b"%PDF-1.4\n")
    _legacy_row(db, cv, stamp="1:1")
    storage.list_resumes()

    tables = {r[0] for r in sqlite3.connect(db).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "resumes_superseded" not in tables
    assert "resumes" in tables


def test_the_library_works_after_the_rebuild(db, tmp_path):
    cv = tmp_path / "dummy_cv.pdf"
    cv.write_bytes(b"%PDF-1.4\n")
    other = tmp_path / "second.pdf"
    other.write_bytes(b"%PDF-1.4\n")
    _legacy_row(db, cv, stamp="1:1", profile={"full_name": "Rowan Ellis Hartley"})

    first = storage.list_resumes()[0]
    storage.set_active_resume(first.id)
    assert storage.get_setting("cv_path") == str(cv)

    storage.add_resume(str(other))
    assert len(storage.list_resumes()) == 2


def test_opening_twice_does_not_migrate_twice(db, tmp_path):
    cv = tmp_path / "dummy_cv.pdf"
    cv.write_bytes(b"%PDF-1.4\n")
    _legacy_row(db, cv, stamp="1:1", profile={"full_name": "Rowan Ellis Hartley"})

    before = storage.list_resumes()
    storage._LOCAL = type(storage._LOCAL)()       # a fresh connection
    after = storage.list_resumes()

    assert [r.path for r in before] == [r.path for r in after]
    assert len(after) == 1


def test_an_unrecognisable_table_is_replaced(db):
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE resumes (nonsense TEXT);")
    c.commit()
    c.close()

    assert storage.list_resumes() == []
    cols = {r[1] for r in sqlite3.connect(db).execute(
        "PRAGMA table_info(resumes)")}
    assert "id" in cols and "path" in cols


def test_a_clean_install_still_gets_the_table(db):
    assert storage.list_resumes() == []
    cols = {r[1] for r in sqlite3.connect(db).execute(
        "PRAGMA table_info(resumes)")}
    assert cols >= {"id", "path", "label", "profile", "read_mtime",
                    "added_at", "updated_at"}
