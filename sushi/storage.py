"""SQLite persistence.

One file, no migrations framework: the schema is created on demand and new
columns are added defensively, which is enough for a single-user desktop app
and avoids shipping Alembic for four tables.

The API key is deliberately the only setting we try to keep out of the DB when
a keyring is available, because a plaintext key in a file the user might sync
to cloud storage is a real risk.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from dataclasses import asdict
from typing import Any

from .config import DB_PATH, ensure_dirs
from .models import Application, Profile, Resume, Role

log = logging.getLogger(__name__)

_LOCAL = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    url               TEXT NOT NULL UNIQUE,
    company           TEXT NOT NULL,
    title             TEXT NOT NULL,
    location          TEXT,
    ats               TEXT,
    token             TEXT,
    score             INTEGER DEFAULT 0,
    reasons           TEXT,
    status            TEXT NOT NULL DEFAULT 'matched',
    note              TEXT,
    cover_letter_path TEXT,
    screenshot_path   TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_app_score  ON applications(score DESC);

-- Answers the user has given to free-text application questions, reused so the
-- same question is never asked twice.
CREATE TABLE IF NOT EXISTS answers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    used_count INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_answers_q ON answers(question);
"""

#: Every CV attached to the app. One is active at a time — that one is what
#: gets uploaded to forms and what the feed is scored against — and each
#: keeps the profile it was read into, so switching back is a query rather
#: than another model request, and a CV that has never been read shows
#: nothing rather than the last one's text.
#:
#: Out of SCHEMA and into ``_ensure_resumes`` because it is the one table
#: that has already existed in more than one shape on a real machine. See
#: there for what that cost.
_RESUMES_DDL = """
CREATE TABLE IF NOT EXISTS resumes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT NOT NULL UNIQUE,
    label      TEXT NOT NULL DEFAULT '',
    profile    TEXT NOT NULL DEFAULT '',
    read_mtime REAL NOT NULL DEFAULT 0,
    added_at   TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

#: What a usable ``resumes`` table has to carry.
_RESUME_COLUMNS = {"id", "path", "label", "profile", "read_mtime",
                   "added_at", "updated_at"}


def conn() -> sqlite3.Connection:
    """One connection per thread; SQLite objects are not thread-safe."""
    c = getattr(_LOCAL, "conn", None)
    if c is None:
        ensure_dirs()
        from .dbsafe import open_db

        def setup(c: sqlite3.Connection) -> None:
            c.execute("PRAGMA journal_mode=WAL")  # UI reads while a worker writes
            c.executescript(SCHEMA)
            _migrate(c)

        c = open_db(Path(DB_PATH), 15, setup)
        _LOCAL.conn = c
    return c


#: Columns added after the first release. ``CREATE TABLE IF NOT EXISTS`` does
#: nothing to a table that already exists, so a column added to SCHEMA alone
#: would be there for new installs and missing for everyone else.
_ADDED_COLUMNS = {
    # The questions a fill could not answer, as JSON. They used to live only
    # in `note`, as "Needs: a; b; c" — readable, and not something the app
    # could offer back to the user as a form, because a question may contain
    # a semicolon and no amount of splitting gets the list back reliably.
    "applications": {"questions": "TEXT"},
}


def _migrate(c: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    _ensure_resumes(c)
    c.commit()


def _ensure_resumes(c: sqlite3.Connection) -> None:
    """Make ``resumes`` the shape this module expects, whatever it was.

    ``CREATE TABLE IF NOT EXISTS`` does nothing to a table that already
    exists — which is the whole reason ``_ADDED_COLUMNS`` is up there — and
    ``resumes`` turned out to exist already on a real machine, from an
    earlier run of a different take on the same feature: keyed on ``path``
    with no ``id`` at all, and a composite ``stamp`` of "mtime:size" where
    this one keeps ``read_mtime``. So the table was never created, every
    query against it died on ``no such column: id``, and the app could not
    open its window.

    Rebuilt rather than patched column by column, because the primary key
    itself differs and SQLite will not add one. What the old table held is
    carried across — the CV, its label and the profile it was read into are
    the user's, not a cache — and the old table only goes once the new one
    has them.
    """
    row = c.execute("SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='resumes'").fetchone()
    if row is None:
        c.executescript(_RESUMES_DDL)
        return
    have = {r["name"] for r in c.execute("PRAGMA table_info(resumes)")}
    if _RESUME_COLUMNS <= have:
        return
    if "path" not in have:
        # Nothing recognisable to carry over; the name is all it shares.
        log.info("storage: replacing an unrecognisable resumes table")
        c.execute("DROP TABLE resumes")
        c.executescript(_RESUMES_DDL)
        return

    log.info("storage: rebuilding the resumes table (had: %s)",
             ", ".join(sorted(have)))
    kept = [k for k in ("path", "label", "profile", "stamp", "read_mtime",
                        "added_at", "updated_at") if k in have]
    old = c.execute(f"SELECT {', '.join(kept)} FROM resumes").fetchall()
    c.execute("ALTER TABLE resumes RENAME TO resumes_superseded")
    c.executescript(_RESUMES_DDL)
    for r in old:
        path = (r["path"] or "").strip()
        if not path:
            continue
        when = (r["added_at"] if "added_at" in kept else "") or _now()
        c.execute(
            "INSERT OR IGNORE INTO resumes"
            "(path,label,profile,read_mtime,added_at,updated_at)"
            " VALUES(?,?,?,?,?,?)",
            (path,
             (r["label"] if "label" in kept else "") or "",
             (r["profile"] if "profile" in kept else "") or "",
             _read_mtime_from(r, kept, path),
             when,
             (r["updated_at"] if "updated_at" in kept else "") or when))
    c.execute("DROP TABLE resumes_superseded")


def _read_mtime_from(row: sqlite3.Row, kept: list[str], path: str) -> float:
    """When the old row says its CV was last read, in whatever dialect.

    ``stamp`` was "<mtime>:<size>". A row that carries no usable figure but
    does carry a profile is stamped with the file's mtime *now* rather than
    with zero: the profile was read from the file as it stands, and zero
    would have the library announce a CV as "changed since Sushi read it"
    on first launch and spend a model request re-reading it.
    """
    if "read_mtime" in kept:
        try:
            return float(row["read_mtime"] or 0)
        except (TypeError, ValueError):
            pass
    if "stamp" in kept:
        try:
            return float(str(row["stamp"] or "").split(":")[0])
        except (TypeError, ValueError):
            pass
    if "profile" in kept and (row["profile"] or ""):
        try:
            import os
            return os.path.getmtime(path)
        except OSError:
            pass
    return 0.0


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# --- settings ---------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    row = conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    c = conn()
    c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    c.commit()


# --- profile ----------------------------------------------------------------

def save_profile(p: Profile) -> None:
    c = conn()
    c.execute("INSERT INTO profile(id,data,updated_at) VALUES(1,?,?) "
              "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
              (json.dumps(asdict(p), ensure_ascii=False), _now()))
    c.commit()
    try:                       # new facts: the model may answer them now
        from . import learning
        learning.forget_declined()
    except Exception:                                   # noqa: BLE001
        pass


def load_profile() -> Profile | None:
    row = conn().execute("SELECT data FROM profile WHERE id=1").fetchone()
    if not row:
        return None
    try:
        return Profile.from_dict(json.loads(row["data"]))
    except (json.JSONDecodeError, TypeError):
        return None


# --- the CV library ---------------------------------------------------------
#
# ``cv_path`` is still the single setting everything downstream reads — the
# form filler, the agent, the onboarding screen. It is not replaced here; it
# is kept in step with whichever resume is active, so attaching a second CV
# did not mean touching every caller that only ever wanted "the" CV.

ACTIVE_RESUME = "active_resume_id"


def _row_to_resume(r: sqlite3.Row) -> Resume:
    raw = r["profile"] or ""
    profile = None
    if raw:
        try:
            profile = Profile.from_dict(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            profile = None
    return Resume(id=int(r["id"]), path=r["path"], label=r["label"] or "",
                  profile=profile, read_mtime=float(r["read_mtime"] or 0),
                  added_at=r["added_at"], updated_at=r["updated_at"])


def list_resumes() -> list[Resume]:
    rows = conn().execute("SELECT * FROM resumes ORDER BY id").fetchall()
    return [_row_to_resume(r) for r in rows]


def resume(rid: int) -> Resume | None:
    row = conn().execute("SELECT * FROM resumes WHERE id=?", (rid,)).fetchone()
    return _row_to_resume(row) if row else None


def resume_by_path(path: str) -> Resume | None:
    row = conn().execute("SELECT * FROM resumes WHERE path=?",
                         (str(path),)).fetchone()
    return _row_to_resume(row) if row else None


def add_resume(path: str, label: str = "") -> int:
    """Attach a CV. Returns its id; attaching the same file twice is a no-op.

    Keyed on the path rather than on the contents: two files with the same
    bytes under different names are two resumes as far as the user is
    concerned, and the same file chosen twice is one.
    """
    c = conn()
    existing = resume_by_path(path)
    if existing is not None:
        if label and label != existing.label:
            c.execute("UPDATE resumes SET label=?, updated_at=? WHERE id=?",
                      (label, _now(), existing.id))
            c.commit()
        return existing.id
    c.execute("INSERT INTO resumes(path,label,added_at,updated_at) "
              "VALUES(?,?,?,?)", (str(path), label, _now(), _now()))
    c.commit()
    row = c.execute("SELECT id FROM resumes WHERE path=?", (str(path),)).fetchone()
    return int(row["id"])


def forget_resume(rid: int) -> None:
    c = conn()
    c.execute("DELETE FROM resumes WHERE id=?", (rid,))
    c.commit()
    if get_setting(ACTIVE_RESUME, "") == str(rid):
        rest = list_resumes()
        if rest:
            set_active_resume(rest[0].id)
        else:
            set_setting(ACTIVE_RESUME, "")
            set_setting("cv_path", "")


def active_resume() -> Resume | None:
    rid = get_setting(ACTIVE_RESUME, "")
    found = resume(int(rid)) if rid.isdigit() else None
    if found is not None:
        return found
    # The pointer is missing or names a row that has gone. Fall back to the
    # first on file rather than to nothing: an app with a CV attached must
    # never report that it has none.
    rest = list_resumes()
    if not rest:
        return None
    set_active_resume(rest[0].id)
    return rest[0]


def set_active_resume(rid: int) -> Resume | None:
    """Make this the CV everything downstream uses."""
    found = resume(rid)
    if found is None:
        return None
    before = get_setting(ACTIVE_RESUME, "")
    set_setting(ACTIVE_RESUME, str(rid))
    set_setting("cv_path", found.path)
    if before and before != str(rid):
        # Answers the model worked out from the other CV — a notice period,
        # years of experience — must not be typed in as this one's. What
        # the user typed stays.
        try:
            from . import learning
            learning.forget_agent_answers()
        except Exception:                               # noqa: BLE001
            pass
    return found


def save_resume_profile(rid: int, p: Profile, mtime: float | None = None) -> None:
    """Keep the profile this CV was read into.

    ``mtime`` is only written when the file was actually read: editing your
    target titles banks the profile against the resume, and must not make a
    CV that has changed on disk look as though it had just been read.
    """
    c = conn()
    if mtime is None:
        c.execute("UPDATE resumes SET profile=?, updated_at=? WHERE id=?",
                  (json.dumps(asdict(p), ensure_ascii=False), _now(), rid))
    else:
        c.execute("UPDATE resumes SET profile=?, read_mtime=?, updated_at=? "
                  "WHERE id=?",
                  (json.dumps(asdict(p), ensure_ascii=False), float(mtime),
                   _now(), rid))
    c.commit()


def record_cv_read(path: str, p: Profile) -> None:
    """A CV has just been read into a profile. Stamp both against the file."""
    import os

    rid = add_resume(path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    save_resume_profile(rid, p, mtime=mtime)
    if get_setting(ACTIVE_RESUME, "") != str(rid):
        set_active_resume(rid)


def bank_active_profile(p: Profile) -> None:
    """Keep an edited profile with the CV it belongs to. Quiet when there is none.

    Only onto a resume that has already been read. A CV attached a moment
    ago and not yet read has no profile, and that is the fact the library
    is for: writing the previous CV's profile onto it here is precisely how
    a new resume came to show the old one's name and employers.
    """
    found = active_resume()
    if found is not None and found.read:
        save_resume_profile(found.id, p)


def adopt_legacy_cv(p: Profile | None = None) -> None:
    """Bring a pre-library install into the library.

    Everyone who used Sushi before this had exactly one CV, in ``cv_path``,
    and one profile read from it. Both are real and neither should have to be
    chosen again, so the first launch after the upgrade adopts them as the
    first resume.
    """
    cv = get_setting("cv_path", "")
    if list_resumes():
        if cv and active_resume() is None:
            set_active_resume(add_resume(cv))
        return
    if not cv:
        return
    rid = add_resume(cv)
    if p is not None:
        import os
        try:
            mtime = os.path.getmtime(cv)
        except OSError:
            mtime = 0.0
        save_resume_profile(rid, p, mtime=mtime)
    set_active_resume(rid)


# --- applications -----------------------------------------------------------

def upsert_role(role: Role, status: str = "matched") -> int:
    """Add a matched role. Existing rows keep their status, so a rescan never
    resets something the user has already acted on."""
    c = conn()
    c.execute(
        "INSERT INTO applications(url,company,title,location,ats,token,score,reasons,"
        "status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(url) DO UPDATE SET score=excluded.score, reasons=excluded.reasons, "
        "updated_at=excluded.updated_at",
        (role.url, role.company, role.title, role.location, role.ats, role.token,
         role.score, json.dumps(role.reasons), status, _now(), _now()))
    c.commit()
    row = c.execute("SELECT id FROM applications WHERE url=?", (role.url,)).fetchone()
    return int(row["id"])


def set_status(app_id: int, status: str, note: str = "") -> None:
    c = conn()
    c.execute("UPDATE applications SET status=?, note=?, updated_at=? WHERE id=?",
              (status, note, _now(), app_id))
    c.commit()


def set_questions(app_id: int, questions: list[str]) -> None:
    """What this application still wants from the user, as a list.

    Written whenever a fill finishes, including with an empty list: the
    answers it was waiting for may have been banked since, and a row that
    keeps offering questions already answered is worse than one that offers
    none.
    """
    c = conn()
    clean = [q.strip() for q in (questions or []) if q and q.strip()]
    c.execute("UPDATE applications SET questions=?, updated_at=? WHERE id=?",
              (json.dumps(clean, ensure_ascii=False), _now(), app_id))
    c.commit()


def questions_for(url: str) -> list[str]:
    """The outstanding questions for an application, by its URL."""
    row = conn().execute("SELECT questions FROM applications WHERE url=?",
                         (url,)).fetchone()
    if not row:
        return []
    try:
        got = json.loads(row["questions"] or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [q for q in got if isinstance(q, str) and q.strip()]


def set_field(app_id: int, column: str, value: str) -> None:
    if column not in {"cover_letter_path", "screenshot_path", "note"}:
        raise ValueError(f"refusing to write unknown column {column!r}")
    c = conn()
    c.execute(f"UPDATE applications SET {column}=?, updated_at=? WHERE id=?",
              (value, _now(), app_id))
    c.commit()


def _row_to_app(r: sqlite3.Row) -> Application:
    try:
        reasons = json.loads(r["reasons"] or "[]")
    except json.JSONDecodeError:
        reasons = []
    try:
        questions = json.loads(r["questions"] or "[]")
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        questions = []
    return Application(
        id=r["id"], status=r["status"], note=r["note"] or "",
        questions=[q for q in questions if isinstance(q, str) and q.strip()],
        cover_letter_path=r["cover_letter_path"] or "",
        screenshot_path=r["screenshot_path"] or "",
        created_at=r["created_at"], updated_at=r["updated_at"],
        role=Role(company=r["company"], title=r["title"], location=r["location"] or "",
                  url=r["url"], ats=r["ats"] or "", token=r["token"] or "",
                  score=r["score"] or 0, reasons=reasons),
    )


def list_applications(status: str | None = None, limit: int = 500) -> list[Application]:
    sql = "SELECT * FROM applications"
    args: list[Any] = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    # Newest first, because this is a history and that is the only order a
    # history has. It used to lead on score, which sorted a record of what
    # you did by how good the match was — so something applied to an hour
    # ago sat below a three-week-old row that happened to score higher,
    # under a column headed WHEN, which is also the column it is now sorted
    # by. The two agreeing is the point.
    #
    # Timestamps are "%Y-%m-%d %H:%M:%S", fixed width, so comparing them as
    # text is comparing them as time. The id breaks ties: a batch can write
    # several rows inside the same second, and insertion order is the right
    # answer for those rather than whatever the table happens to return.
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    args.append(limit)
    return [_row_to_app(r) for r in conn().execute(sql, args).fetchall()]


def status_by_url() -> dict[str, str]:
    """url → status for every record, with no cap.

    The feeds used ``list_applications``, which stops at 500: past that, the
    oldest applications dropped out of "already dealt with" and their jobs
    came back into the feed and the Insane queue.
    """
    return {r["url"]: r["status"] for r in conn().execute(
        "SELECT url, status FROM applications WHERE url != ''")}


def reset_interrupted() -> int:
    """Put fills a crash or a closed app left at "filling" back in the queue.

    Run once at startup, before anything can be filling. Without it they sat
    in the Tracker as Filling forever.
    """
    c = conn()
    n = c.execute("UPDATE applications SET status='matched', updated_at=? "
                  "WHERE status='filling'", (_now(),)).rowcount
    c.commit()
    return n


def counts_by_status() -> dict[str, int]:
    rows = conn().execute("SELECT status, COUNT(*) n FROM applications GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


# --- answer bank ------------------------------------------------------------

def remember_answer(question: str, answer: str) -> None:
    c = conn()
    row = c.execute("SELECT id FROM answers WHERE question=?", (question,)).fetchone()
    if row:
        c.execute("UPDATE answers SET answer=?, used_count=used_count+1, updated_at=? WHERE id=?",
                  (answer, _now(), row["id"]))
    else:
        c.execute("INSERT INTO answers(question,answer,used_count,updated_at) VALUES(?,?,1,?)",
                  (question, answer, _now()))
    c.commit()


def recall_answer(question: str) -> str:
    row = conn().execute("SELECT answer FROM answers WHERE question=?", (question,)).fetchone()
    return row["answer"] if row else ""


def forget_answer(question: str) -> None:
    c = conn()
    c.execute("DELETE FROM answers WHERE question=?", (question,))
    c.commit()


def all_answers() -> dict[str, str]:
    return {r["question"]: r["answer"] for r in conn().execute("SELECT question,answer FROM answers")}
