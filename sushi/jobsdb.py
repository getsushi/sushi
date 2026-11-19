"""Every job Sushi can see, kept locally and refreshed daily.

Sushi used to fetch as it searched. Opening the app meant starting a sweep
from wherever the last one stopped, waiting minutes for the first results,
and throwing the rest away when the window closed — so the feed was empty at
startup, a different slice of the corpus every time, and the same 15,589
boards were read again and again for postings that had not changed.

So the fetching and the matching are now separate things. A background
refresh keeps a local table of **every** vacancy on every board in the
corpus; matching is a query against that table and returns instantly. The
consequences are worth stating, because they are the point:

- The feed is full the moment the app opens, from the last refresh.
- Changing your target roles, your location or the quality bar re-reads
  nothing. It is a re-score of rows already on disk.
- A posting that has gone is *known* to have gone. Boards do not announce
  closures, but a job that was on a board yesterday and is not on it today
  has closed — which is how Insane mode stops spending its daily cap on
  adverts for jobs that no longer exist.

Measured on this corpus: about 1.2 million rows and ~300MB, a median of 3
vacancies per board and a mean of 78 — a few very large employers and a long
tail of companies with one opening or none at all. That shape is why the
refresh is tiered rather than uniform (see :func:`due_boards`).

The table lives in its own SQLite file, not in ``sushi.db``. It is a cache:
large, rebuildable from the network, and worth being able to delete without
going anywhere near the profile, the answer bank or the applications.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .ats import read_board
from .config import DATA_DIR, ensure_dirs
from .models import Profile, Role

log = logging.getLogger(__name__)

#: Separate from sushi.db on purpose: see the module docstring.
JOBS_DB_PATH = DATA_DIR / "jobs.db"

#: A board with vacancies is re-read this often.
ACTIVE_EVERY = 24 * 3600
#: One that came back empty is re-read this often instead. Two in five
#: boards in the corpus are empty at any moment, and a company with no
#: openings today rarely has twelve tomorrow — checking them all daily is
#: most of the cost of a sweep for almost none of the jobs.
DORMANT_EVERY = 7 * 24 * 3600

_local = threading.local()


def conn() -> sqlite3.Connection:
    """This thread's connection. The refresh runs on a pool, so one each."""
    existing = getattr(_local, "conn", None)
    if existing is not None:
        return existing
    ensure_dirs()
    from .dbsafe import open_db

    def setup(c: sqlite3.Connection) -> None:
        # WAL so the feed can be queried while a refresh is writing; without
        # it every search during a refresh waits on the writer's lock.
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _create(c)

    c = open_db(JOBS_DB_PATH, 30, setup)
    _local.conn = c
    return c


def _create(c: sqlite3.Connection) -> None:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS jobs (
        url         TEXT PRIMARY KEY,
        company     TEXT NOT NULL,
        title       TEXT NOT NULL,
        location    TEXT NOT NULL DEFAULT '',
        department  TEXT NOT NULL DEFAULT '',
        ats         TEXT NOT NULL DEFAULT '',
        token       TEXT NOT NULL DEFAULT '',
        score       INTEGER NOT NULL DEFAULT -1,
        reasons     TEXT NOT NULL DEFAULT '[]',
        profile_rev TEXT NOT NULL DEFAULT '',
        first_seen  REAL NOT NULL,
        last_seen   REAL NOT NULL
    );
    -- The feed's only query: best first, above a bar.
    CREATE INDEX IF NOT EXISTS jobs_by_score ON jobs(score DESC);
    -- Used by the per-board diff that detects closures.
    CREATE INDEX IF NOT EXISTS jobs_by_board ON jobs(ats, token);
    -- Rows left behind when the profile changes.
    CREATE INDEX IF NOT EXISTS jobs_by_rev ON jobs(profile_rev);

    CREATE TABLE IF NOT EXISTS board_state (
        ats       TEXT NOT NULL,
        token     TEXT NOT NULL,
        last_read REAL NOT NULL DEFAULT 0,
        jobs      INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (ats, token)
    );

    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    );
    """)
    c.commit()


def get_meta(key: str, default: str = "") -> str:
    row = conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    c = conn()
    c.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    c.commit()


# --- what the table knows ---------------------------------------------------


@dataclass
class Stats:
    jobs: int = 0
    companies: int = 0
    boards_read: int = 0
    boards_total: int = 0
    last_refresh: float = 0.0
    scored: int = 0

    @property
    def fresh(self) -> bool:
        """Whether the last full refresh was recent enough to rely on."""
        return bool(self.last_refresh) and (time.time() - self.last_refresh) < ACTIVE_EVERY


def stats(boards_total: int = 0) -> Stats:
    c = conn()
    row = c.execute(
        "SELECT COUNT(*) n, COUNT(DISTINCT company) co, "
        "SUM(CASE WHEN score >= 0 THEN 1 ELSE 0 END) sc FROM jobs").fetchone()
    read = c.execute("SELECT COUNT(*) n FROM board_state WHERE last_read > 0").fetchone()
    return Stats(
        jobs=row["n"] or 0, companies=row["co"] or 0, scored=row["sc"] or 0,
        boards_read=read["n"] or 0, boards_total=boards_total,
        last_refresh=float(get_meta("last_refresh", "0") or 0),
    )


# --- which boards are worth reading today -----------------------------------


def due_boards(all_boards: list[dict[str, Any]], *,
               now: float | None = None,
               force: bool = False) -> list[dict[str, Any]]:
    """The boards to read on this pass, most overdue first.

    Tiered rather than uniform. A board that had vacancies last time is read
    daily; one that came back empty is left for a week. Two in five boards
    are empty at any moment and they are not where the jobs are, so this is
    most of the saving for almost none of the loss — and a dormant company
    that starts hiring is picked up within the week.
    """
    now = time.time() if now is None else now
    state = {(r["ats"], r["token"]): r
             for r in conn().execute(
                 "SELECT ats, token, last_read, jobs FROM board_state")}
    due = []
    for b in all_boards:
        key = (b.get("ats", ""), b.get("token", ""))
        if not key[0] or not key[1]:
            continue
        row = state.get(key)
        if force or row is None:
            due.append((0.0, b))       # never read: the highest priority
            continue
        every = ACTIVE_EVERY if row["jobs"] else DORMANT_EVERY
        if now - row["last_read"] >= every:
            due.append((row["last_read"], b))
    due.sort(key=lambda pair: pair[0])
    return [b for _, b in due]


def restart_boards() -> int:
    """Make every board due again, so the next refresh reads the corpus anew.

    What "from scratch" means here, and what it deliberately does not. The
    vacancies already on file stay: they are the feed, they cost forty
    minutes to fetch, and the answer that changed — where you want to work —
    is scored against rows on disk rather than fetched again. What is thrown
    away is the *schedule*: a board read an hour ago is not due again for a
    day, so without this a sweep interrupted at 560 of 15,918 would carry on
    from 560 and never revisit the ones it had already read under the old
    answer.

    One side effect worth naming: :func:`known_closed` only trusts boards it
    has read, so until the sweep comes round again it answers "cannot know"
    rather than "closed". That is the safe direction — Insane mode declines
    to call a job dead rather than calling a live one dead.

    Returns how many boards were put back in the queue.
    """
    c = conn()
    n = c.execute("UPDATE board_state SET last_read = 0").rowcount
    c.commit()
    # The clock the autoscan judges freshness by. Left alone, a refresh that
    # finished this morning would keep the app sitting on its hands for the
    # rest of the day with a queue it has just been told to re-read.
    set_meta("last_refresh", "0")
    log.info("jobsdb: %d board(s) put back in the queue from scratch", n)
    return n


# --- writing ----------------------------------------------------------------


def store_board(board: dict[str, Any], jobs: list[dict[str, str]], *,
                profile: Profile | None = None,
                now: float | None = None) -> tuple[int, int, list[Role]]:
    """Record one board's vacancies. Returns (new, closed, the new roles).

    The diff is per board and only runs for a board we have just read: a
    posting that was on this board before and is not on it now has been
    taken down. That is the only reliable closure signal boards give —
    none of them publish one — and it is what keeps the queue from
    spending a day's cap on adverts for jobs that are gone.

    It is done by comparing URLs rather than by comparing timestamps. The
    timestamp version read "delete anything whose last_seen predates this
    pass", which is wrong whenever two passes land in the same clock tick —
    and on Windows that tick is coarse enough that a test, or a fast
    second refresh, does exactly that: nothing is detected as closed and
    every unchanged job is reported as new.
    """
    now = time.time() if now is None else now
    ats, token = board.get("ats", ""), board.get("token", "")
    company = board.get("name") or board.get("company") or ""
    c = conn()

    rows = []
    for j in jobs:
        url = (j.get("url") or "").strip()
        title = (j.get("title") or "").strip()
        if not url or not title:
            continue
        employer = (j.get("company") or company).strip()
        score, reasons, rev = -1, "[]", ""
        if profile is not None:
            score, why = _score(employer, title, j.get("location", ""), url,
                                ats, token, profile)
            reasons, rev = json.dumps(why), profile_rev(profile)
        # The board's own posting date where it gives one, so "Last 24
        # hours" means posted in the last day, not "first scanned" — on a
        # new install every job ever listed was "first seen" today.
        try:
            posted = float(j.get("posted") or 0.0)
        except (TypeError, ValueError):
            posted = 0.0
        born = posted if 0 < posted <= now else now
        rows.append((url, employer, title, (j.get("location") or "").strip(),
                     (j.get("department") or "").strip(), ats, token,
                     score, reasons, rev, born, now))

    incoming = {r[0] for r in rows}
    held = {r["url"] for r in c.execute(
        "SELECT url FROM jobs WHERE ats=? AND token=?", (ats, token))}
    fresh, gone = incoming - held, held - incoming

    c.executemany(
        "INSERT INTO jobs(url, company, title, location, department, ats, token,"
        " score, reasons, profile_rev, first_seen, last_seen)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(url) DO UPDATE SET"
        "  company=excluded.company, title=excluded.title,"
        "  location=excluded.location, department=excluded.department,"
        "  last_seen=excluded.last_seen,"
        "  first_seen=MIN(jobs.first_seen, excluded.first_seen)", rows)
    if gone:
        c.executemany("DELETE FROM jobs WHERE url=?", [(u,) for u in gone])
    c.execute(
        "INSERT INTO board_state(ats, token, last_read, jobs) VALUES(?,?,?,?)"
        " ON CONFLICT(ats, token) DO UPDATE SET"
        "  last_read=excluded.last_read, jobs=excluded.jobs",
        (ats, token, now, len(rows)))
    c.commit()

    if not fresh:
        return 0, len(gone), []
    added = c.execute(
        f"SELECT * FROM jobs WHERE url IN ({','.join('?' * len(fresh))})",
        list(fresh)).fetchall()
    return len(fresh), len(gone), [_role(r) for r in added]


#: The global feeds are stored as one pseudo-board. They are not a company
#: board — there is no token to read again — but treating them as one gives
#: them the same closure diff as everything else: a posting that drops off
#: the aggregator drops out of the table.
GLOBAL_BOARD = {"name": "", "ats": "global", "token": "feeds"}


def refresh_global(profile: Profile | None = None, *,
                   on_progress: Callable[[str], None] | None = None,
                   should_stop: Callable[[], bool] | None = None,
                   ) -> tuple[int, int, list[Role]]:
    """Pull the worldwide feeds and grow the corpus from what they name.

    Two things that the corpus alone cannot do. The aggregators publish the
    day's postings worldwide, which is the freshest source there is; and
    every posting whose apply link points at a Greenhouse or Lever board
    tells us that company's whole board, which is how the corpus grew from
    1,078 companies to 15,589. Both were lost when the scan became a
    database refresh and nothing called ``find_roles`` any more.

    Returns (new jobs, new boards, the roles worth showing).
    """
    from .discovery import boards as corpus_boards
    from .discovery import note_companies, persist_boards
    from .sources import fetch_global_postings, harvest_boards

    if on_progress:
        on_progress("Fetching the latest jobs from the global boards…")
    postings = fetch_global_postings(on_progress=on_progress,
                                     should_stop=should_stop)
    if not postings:
        return 0, 0, []

    jobs = [{"company": p.get("company", ""), "title": p.get("title", ""),
             "location": p.get("location", ""), "url": p.get("url", ""),
             "department": p.get("department", "")} for p in postings]
    added, _closed, roles = store_board(GLOBAL_BOARD, jobs, profile=profile)

    # And the company boards those postings point at.
    known = {(c["ats"], c["token"]) for c in corpus_boards()}
    fresh_boards = harvest_boards(postings, known)
    grown = persist_boards(fresh_boards) if fresh_boards else 0

    # Then the employers they merely *name*. That is now the productive half:
    # the feeds have stopped publishing real apply links, so harvesting finds
    # almost nothing, while the same postings name hundreds of employers a day
    # that the corpus has never heard of. Recorded here rather than left to
    # the button in Settings, so the list grows on its own and the next probe
    # pass has something to chase.
    named = note_companies(postings)
    if on_progress and (grown or named):
        on_progress(f"{len(postings)} global postings · {grown} new company "
                    f"page(s) · {named} new employer(s) to look up")
    log.info("jobsdb: global feeds gave %d posting(s), %d new job(s), "
             "%d new board(s), %d new compan(ies)",
             len(postings), added, grown, named)
    return added, grown, roles


def refresh(all_boards: list[dict[str, Any]], *, profile: Profile | None = None,
            workers: int = 10, limit: int = 0,
            on_progress: Callable[[str], None] | None = None,
            on_jobs: Callable[[list[Role]], None] | None = None,
            on_tick: Callable[[int, int], None] | None = None,
            stream_min_score: int = 8, include_global: bool = True,
            should_stop: Callable[[], bool] | None = None) -> Stats:
    """Read the boards that are due and bring the table up to date.

    Resumable by construction: each board is recorded as it is read, so a
    refresh interrupted after ten minutes has kept those ten minutes of
    work and the next one starts from what is still due.

    ``on_jobs`` is handed newly-stored roles that clear ``stream_min_score``,
    so a first run — when the table is empty and there is nothing to show —
    still fills the feed as it goes rather than at the end.

    ``on_tick`` is handed ``(boards done, boards due)`` once up front and
    again after every board. ``on_progress`` says the same thing in a
    sentence, but a sentence cannot be drawn as a bar, and on a first run
    this is the only answer to "how long is this going to take?" — which,
    for the better part of an hour, is the user's whole experience of the
    app.

    The bar matters more than it looks. On a cold start every job is new, so
    handing over *everything* means a quarter of a million objects crossing
    a thread boundary into a window that can show four hundred. Measured on
    2,500 boards: 77,259 roles streamed, of which a few hundred could ever
    have appeared. Only what the feed could show is worth sending.
    """
    new_jobs_global = 0
    if include_global and not (should_stop and should_stop()):
        try:
            new_jobs_global, grown, roles = refresh_global(
                profile, on_progress=on_progress, should_stop=should_stop)
            if roles and on_jobs:
                worth = [r for r in roles if r.score >= stream_min_score]
                if worth:
                    on_jobs(worth)
            if grown:
                # Boards found just now have never been read, so they are
                # due on this very pass rather than tomorrow's.
                from .discovery import boards as corpus_boards
                all_boards = corpus_boards()
        except Exception as exc:          # a feed being down is not fatal
            log.info("jobsdb: global feeds unavailable: %s", exc)

    due = due_boards(all_boards)
    if limit:
        due = due[:limit]
    total, done, closed_jobs = len(due), 0, 0
    # Boards that could not be read this time. Left unrecorded, so they keep
    # their jobs and are still due on the next refresh.
    unreadable = 0
    new_jobs = new_jobs_global
    if on_progress:
        on_progress(f"Refreshing {total} board(s) of {len(all_boards)}…")
    if on_tick:
        on_tick(0, total)

    def work(board: dict[str, Any]) -> tuple[dict, list[dict[str, str]]]:
        from . import throttle
        throttle.wait_while_filling(should_stop)
        # Strict: a board that failed to answer must not be stored as one
        # with no vacancies, or every job on it is deleted as closed.
        return board, read_board(board.get("ats", ""), board.get("token", ""),
                                 strict=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []
        for board in due:
            if should_stop and should_stop():
                break
            try:
                futures.append(pool.submit(work, board))
            except RuntimeError:          # interpreter going away
                break
        for fut in as_completed(futures):
            if should_stop and should_stop():
                for f in futures:
                    f.cancel()
                break
            done += 1
            if on_tick:
                # Before the result is unpacked: a board that fails to read
                # is still a board gone by, and a bar that stalls on the
                # ones that 404 is a bar that lies.
                on_tick(done, total)
            try:
                board, jobs = fut.result()
            except Exception:
                unreadable += 1
                continue
            # Written from this thread, not the pool's: one connection, one
            # writer, no lock contention between fourteen of them.
            try:
                added, closed, roles = store_board(board, jobs, profile=profile)
            except sqlite3.Error as exc:
                log.info("jobsdb: could not store %s/%s: %s",
                         board.get("ats"), board.get("token"), exc)
                continue
            new_jobs += added
            closed_jobs += closed
            if on_jobs and roles:
                worth = [r for r in roles if r.score >= stream_min_score]
                if worth:
                    on_jobs(worth)
            if on_progress and done % 40 == 0:
                on_progress(f"Refreshed {done}/{total} boards · "
                            f"{new_jobs} new job(s), {closed_jobs} closed")

    if not (should_stop and should_stop()) and done >= total and not unreadable:
        set_meta("last_refresh", str(time.time()))
    log.info("jobsdb: refreshed %d/%d boards, +%d jobs, -%d closed, "
             "%d could not be read (kept, still due)",
             done, total, new_jobs, closed_jobs, unreadable)
    return stats(boards_total=len(all_boards))


# --- scoring ----------------------------------------------------------------


def profile_rev(profile: Profile | None) -> str:
    """A fingerprint of everything scoring depends on.

    Rows carry the fingerprint they were scored under, so "your targets
    changed" becomes a query for rows that disagree with it rather than a
    flag somebody has to remember to set.

    The matcher's own revision is part of it. Changing how scoring works
    makes every stored score stale in exactly the same way that changing
    the targets does, and leaving it out meant a fixed scorer sat behind a
    table of scores from the broken one.
    """
    if profile is None:
        return ""
    from .discovery import SCORER_REV

    bits = [
        f"r{SCORER_REV}",
        "|".join(sorted(profile.target_titles or [])),
        "|".join(sorted(profile.target_locations or [])),
        "|".join(sorted(profile.skills or [])),
        profile.seniority or "", profile.headline or "",
        # Workplace is scored (``workplace_fit``) and was not fingerprinted,
        # so switching between remote, hybrid and on site lit the chip up and
        # left a million cached scores from the previous answer in place.
        getattr(profile, "workplace", "") or "",
    ]
    # hashlib, not the built-in hash(): that one is salted per process, so
    # every launch would decide your targets had changed and re-score all
    # 1.2 million rows before showing you anything.
    return hashlib.sha1("\x1f".join(bits).encode("utf-8")).hexdigest()[:16]


def where_rev(profile: Profile | None) -> str:
    """A fingerprint of the Where to look answer on its own.

    Narrower than :func:`profile_rev` on purpose. Every part of the profile
    makes stored scores stale, and a re-score answers that. Only this part —
    the regions, the countries, how you want to work — is an instruction to
    go and look somewhere else, which is the one change worth paying for the
    corpus to be read again.
    """
    if profile is None:
        return ""
    return "\x1f".join([
        "|".join(sorted(profile.target_locations or [])),
        getattr(profile, "workplace", "") or "",
    ])


def _score(company: str, title: str, location: str, url: str,
           ats: str, token: str, profile: Profile) -> tuple[int, list[str]]:
    from .discovery import score_role
    role = Role(company=company, title=title, location=location, url=url,
                ats=ats, token=token)
    return score_role(role, profile)


def stale_count(profile: Profile, *, limit: int = 0) -> int:
    """How many rows were scored under a different fingerprint.

    ``limit`` stops the count early, for the only question a caller usually
    has — "is there any?" — which on a table of a million rows is the
    difference between a scan and a lookup.
    """
    rev = profile_rev(profile)
    sql = "SELECT COUNT(*) n FROM (SELECT 1 FROM jobs WHERE profile_rev IS NOT ?"
    args: list[Any] = [rev]
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    row = conn().execute(sql + ")", args).fetchone()
    return row["n"] if row else 0


def rescore(profile: Profile, *, batch: int = 20_000,
            on_progress: Callable[[str], None] | None = None,
            should_stop: Callable[[], bool] | None = None) -> int:
    """Re-score rows that were scored under different targets. Returns how many.

    This is what makes "every job, stored locally" worth the disk: changing
    what you are looking for costs a pass over a table instead of another
    sweep of fifteen thousand boards.
    """
    rev = profile_rev(profile)
    c = conn()
    total = 0
    # A pass touches each row once. Going far past the table's size means
    # something else keeps flipping rows back, and looping on it forever
    # burns a core for nothing.
    ceiling = 2 * (c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] + batch)
    refused = 0
    while True:
        if should_stop and should_stop():
            break
        if total > ceiling:
            log.warning("rescore: stopped after %d rows; rows keep going stale", total)
            break
        rows = c.execute(
            "SELECT url, company, title, location, ats, token FROM jobs"
            " WHERE profile_rev IS NOT ? LIMIT ?", (rev, batch)).fetchall()
        if not rows:
            break
        updates = []
        for r in rows:
            try:
                score, why = _score(r["company"], r["title"], r["location"],
                                    r["url"], r["ats"], r["token"], profile)
            except Exception as exc:
                # One row that will not score must not cost the other
                # 999,999. This is a heuristic over scraped text, and the
                # pass behind it is what the whole local table is for.
                # Parked at zero and stamped with this revision so the
                # loop makes progress rather than meeting it again on the
                # next batch and spinning forever.
                refused += 1
                if refused <= 3:          # a sample, not a million lines
                    log.warning("rescore: %s · %s would not score: %s: %s",
                                r["company"], r["title"],
                                type(exc).__name__, exc)
                score, why = 0, []
            updates.append((score, json.dumps(why), rev, r["url"]))
        # The background refresh writes to the same table from a pool of
        # threads, and can hold the write lock past the 30s busy timeout.
        # Losing the whole rescore to that is worse than waiting for it.
        for attempt in range(6):
            try:
                c.executemany("UPDATE jobs SET score=?, reasons=?, profile_rev=?"
                              " WHERE url=?", updates)
                c.commit()
                break
            except sqlite3.OperationalError as e:
                c.rollback()
                if "locked" not in str(e) or attempt == 5:
                    raise
                if should_stop and should_stop():
                    return total
                time.sleep(2 * (attempt + 1))
        total += len(updates)
        if on_progress:
            on_progress(f"Re-scored {total} job(s) against your profile…")
        if len(rows) < batch:
            break
    if refused:
        log.warning("rescore: %d of %d row(s) would not score and were "
                    "parked at zero", refused, total)
    # match_percent is display-only and derived from the score, so it is
    # computed on read rather than stored: one less column to invalidate.
    return total


# --- reading ----------------------------------------------------------------


def _role(row: sqlite3.Row) -> Role:
    try:
        reasons = json.loads(row["reasons"])
    except (json.JSONDecodeError, TypeError):
        reasons = []
    try:
        first_seen = float(row["first_seen"] or 0.0)
    except (IndexError, KeyError, TypeError, ValueError):
        first_seen = 0.0
    return Role(company=row["company"], title=row["title"],
                location=row["location"], url=row["url"],
                department=row["department"], ats=row["ats"],
                token=row["token"], score=row["score"],
                first_seen=first_seen,
                reasons=reasons if isinstance(reasons, list) else [])


def search(profile: Profile | None = None, *, min_score: int = 8,
           limit: int = 400, query: str = "",
           exclude: "set[str] | frozenset[str]" = frozenset()) -> list[Role]:
    """The feed: the best jobs on file, best first. No network.

    ``exclude`` is the URLs already dealt with. They are dropped *before*
    the limit, not after: dropped after, the 400 slots filled up with jobs
    already applied to as Insane mode worked, and anything newly fetched
    that scored below them never reached the feed at all.

    This is the whole return on keeping the table. It answers in
    milliseconds from a cold start, where the old path needed minutes of
    fetching before it could show anything at all.
    """
    sql = ["SELECT * FROM jobs WHERE score >= ?"]
    args: list[Any] = [min_score]
    if query:
        sql.append("AND (title LIKE ? OR company LIKE ?)")
        args += [f"%{query}%", f"%{query}%"]
    # Newest first within a score: last_seen is bumped on every refresh, so
    # it put old postings level with ones fetched an hour ago.
    sql.append("ORDER BY score DESC, first_seen DESC LIMIT ?")
    args.append(limit + len(exclude))
    rows = conn().execute(" ".join(sql), args).fetchall()
    roles = [_role(r) for r in rows if r["url"] not in exclude][:limit]
    if profile is not None:
        from .discovery import match_percent
        for r in roles:
            r.match = match_percent(r, profile)
        # Sorted on the number the reader can see, within each band of the
        # score that gated it. SQL can only order by the stored score, so a
        # feed ordered purely by that showed 62%, 67%, 62%, 69%, 48% down
        # the page and looked arbitrary.
        roles.sort(key=lambda r: (-r.score, -r.match))
    return roles


def count(min_score: int = 8, query: str = "") -> int:
    """How many jobs clear the bar. A count, not a list.

    The sidebar badge and the Dashboard's "of N" both want this number and
    nothing else; asking ``search`` meant building two thousand Role objects
    and scoring every one of them for display, on every chrome refresh.
    """
    sql = ["SELECT COUNT(*) n FROM jobs WHERE score >= ?"]
    args: list[Any] = [min_score]
    if query:
        sql.append("AND (title LIKE ? OR company LIKE ?)")
        args += [f"%{query}%", f"%{query}%"]
    row = conn().execute(" ".join(sql), args).fetchone()
    return row["n"] if row else 0


def is_live(url: str) -> bool:
    """Whether this posting was still on its board at the last refresh."""
    if not url:
        return False
    row = conn().execute("SELECT 1 FROM jobs WHERE url=?", (url,)).fetchone()
    return row is not None


def known_closed(role: Role) -> bool:
    """Whether this posting is *known* to have been taken down.

    Absence from the table is not the same as closure, and conflating the
    two would be worse than not checking at all: on a fresh install nothing
    is on file, so every job would look closed and Insane mode would refuse
    to apply to anything. A posting only counts as gone when its board has
    actually been read and the posting was not on it.

    Roles from a pasted link or an aggregator have no board to check, so
    they are never reported as closed from here.
    """
    if not role.url or not role.ats or not role.token:
        return False
    c = conn()
    read = c.execute(
        "SELECT 1 FROM board_state WHERE ats=? AND token=? AND last_read > 0",
        (role.ats, role.token)).fetchone()
    if read is None:
        return False                      # never looked; cannot know
    return not is_live(role.url)


def forget_all() -> None:
    """Throw the cache away. Everything in it is rebuildable."""
    c = conn()
    c.executescript("DELETE FROM jobs; DELETE FROM board_state; DELETE FROM meta;")
    c.commit()


def close() -> None:
    """Release this thread's connection (tests, and app shutdown)."""
    existing = getattr(_local, "conn", None)
    if existing is not None:
        try:
            existing.close()
        finally:
            _local.conn = None


def iter_companies(limit: int = 0) -> Iterable[tuple[str, int]]:
    """Employers in the table and how many vacancies each has."""
    sql = ("SELECT company, COUNT(*) n FROM jobs GROUP BY company"
           " ORDER BY n DESC")
    if limit:
        sql += f" LIMIT {int(limit)}"
    for row in conn().execute(sql):
        yield row["company"], row["n"]
