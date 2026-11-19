"""The local job database: does it hold every job, and stay honest about it?

Sushi used to fetch while it searched, so the feed was empty at startup and
a different slice of the corpus every time. Now a background refresh keeps
every vacancy on every board in a local table and matching is a query.

Three properties matter, and each is a bug that was actually possible:

1. **A job that has gone is gone.** No board publishes a closure, so the
   only signal is absence: on the board yesterday, not on it today. Get
   this wrong and Insane mode spends a day's cap on adverts for jobs that
   no longer exist.
2. **Changing your targets re-reads nothing.** That is the whole return on
   300MB of disk — a re-score, not another sweep of 15,589 boards.
3. **The refresh is tiered and resumable.** Daily for boards with
   vacancies, weekly for the two in five that are empty, and an interrupted
   run keeps what it had read.

Runs against a throwaway data directory with a fake board reader, so it
touches neither your database nor the network.

    python qa_jobsdb.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sushi-jobsdb-"))
os.environ["LOCALAPPDATA"] = str(_TMP)
os.environ["XDG_DATA_HOME"] = str(_TMP)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

from sushi import jobsdb  # noqa: E402
from sushi.models import Experience, Profile  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []

PROFILE = Profile(
    full_name="Rowan Ellis Hartley", email="r.hartley@example.com",
    headline="Senior Operations Analyst", summary="Ops and data analyst.",
    skills=["Python", "SQL"], seniority="ic",
    experience=[Experience(company="Kestrel", title="Analyst", start="2022")],
    target_titles=["Operations Analyst"], target_locations=["Remote (UK)"])

#: What the fake reader returns, keyed by token. Mutated by the tests to
#: stand for a board changing between one refresh and the next.
BOARDS: dict[str, list[dict[str, str]]] = {}


def board(token: str, n: int = 2, ats: str = "greenhouse") -> dict:
    return {"name": token.title(), "ats": ats, "token": token}


def jobs_for(token: str, titles: list[str]) -> list[dict[str, str]]:
    return [{"title": t, "location": "Remote (UK)", "department": "Operations",
             "url": f"https://example.com/{token}/{i}"}
            for i, t in enumerate(titles)]


def fake_read_board(ats: str, token: str, **_kw) -> list[dict[str, str]]:
    return BOARDS.get(token, [])


def check(name: str, fn) -> None:
    try:
        RESULTS.append(("PASS", name, str(fn() or "")[:150]))
        print(f"  ok    {name}")
    except Exception as e:
        RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=2)


def main() -> int:
    jobsdb.read_board = fake_read_board          # no network in this suite

    all_boards = [board("kestrel"), board("wise"), board("quiet")]
    BOARDS["kestrel"] = jobs_for("kestrel", [
        "Senior Operations Analyst", "Operations Analyst", "Head Chef"])
    BOARDS["wise"] = jobs_for("wise", ["Staff Data Analyst"])
    BOARDS["quiet"] = []                          # a company not hiring

    def t_first_build():
        """A cold start reads every board and stores what it finds."""
        streamed: list = []
        st = jobsdb.refresh(all_boards, profile=PROFILE, workers=3,
                            include_global=False, on_jobs=streamed.extend)
        assert st.jobs == 4, f"{st.jobs} jobs stored, expected 4"
        assert st.companies == 2, st.companies
        assert st.boards_read == 3, st.boards_read
        # Streamed as they were stored, so a first run fills the feed as it
        # goes instead of at the end — but only what the feed could show. On
        # a cold start every job is new, and handing all of them over meant
        # a quarter of a million objects crossing a thread boundary into a
        # window that displays four hundred.
        above = [r for r in jobsdb.search(PROFILE, min_score=8, limit=100)]
        assert len(streamed) == len(above), (
            f"{len(streamed)} streamed but {len(above)} are above the bar")
        assert all(r.score >= 8 for r in streamed), [r.score for r in streamed]
        assert not any("Chef" in r.title for r in streamed),             f"an irrelevant role was streamed to the feed: {streamed}"
        assert st.fresh, "a refresh that just finished is not marked fresh"
        return f"{st.jobs} jobs from {st.boards_read} boards, {len(streamed)} streamed"
    check("first build: every board read, every job stored", t_first_build)

    def t_search_is_local_and_ranked():
        """The feed is a query: instant, best first, and above the bar."""
        t0 = time.time()
        roles = jobsdb.search(PROFILE, min_score=8)
        took = (time.time() - t0) * 1000
        assert roles, "nothing came back above the bar"
        scores = [r.score for r in roles]
        assert scores == sorted(scores, reverse=True), scores
        assert all(r.score >= 8 for r in roles), scores
        assert all("Chef" not in r.title for r in roles), \
            f"an irrelevant role cleared the bar: {[r.title for r in roles]}"
        assert all(r.match for r in roles), "match percent was not filled in"
        # A search by keyword narrows without touching the network.
        narrowed = jobsdb.search(PROFILE, min_score=0, query="Staff")
        assert len(narrowed) == 1 and narrowed[0].company == "Wise", narrowed
        return f"{len(roles)} above the bar in {took:.0f}ms, best first"
    check("search: the feed comes from disk, ranked", t_search_is_local_and_ranked)

    def t_closed_jobs_disappear():
        """A posting taken off its board is removed from the table.

        The only closure signal a board gives is absence. Without the
        per-board diff a job stays in the feed for ever and the queue keeps
        opening a page that says the role is gone.
        """
        gone = "https://example.com/kestrel/1"
        assert jobsdb.is_live(gone), "setup: that job should be on file"
        BOARDS["kestrel"] = jobs_for("kestrel", ["Senior Operations Analyst"])
        # [0] survives; [1] and [2] have been taken down.
        added, closed, _ = jobsdb.store_board(board("kestrel"),
                                              BOARDS["kestrel"], profile=PROFILE)
        assert closed == 2, f"{closed} closed, expected 2"
        assert added == 0, f"{added} new, expected 0 — an unchanged job was re-added"
        assert not jobsdb.is_live(gone), "a closed job is still on file"
        assert jobsdb.is_live("https://example.com/kestrel/0"), \
            "a job still on the board was deleted"
        return f"{closed} removed, the surviving one kept, nothing re-added"
    check("closures: a job off the board leaves the table", t_closed_jobs_disappear)

    def t_new_jobs_are_new():
        """A job that appears is counted once, and only once."""
        BOARDS["wise"] = jobs_for("wise", ["Staff Data Analyst",
                                           "Operations Analyst"])
        added, closed, roles = jobsdb.store_board(board("wise"), BOARDS["wise"],
                                                  profile=PROFILE)
        assert (added, closed) == (1, 0), (added, closed)
        assert len(roles) == 1 and roles[0].title == "Operations Analyst", roles
        again, _, _ = jobsdb.store_board(board("wise"), BOARDS["wise"],
                                         profile=PROFILE)
        assert again == 0, f"storing the same board twice invented {again} jobs"
        return "one new job, reported once, idempotent on a repeat"
    check("new jobs: counted once, not on every refresh", t_new_jobs_are_new)

    def t_tiering():
        """Active boards come round daily, empty ones weekly."""
        now = time.time()
        # Nothing is due straight after a refresh.
        assert not jobsdb.due_boards(all_boards, now=now), \
            "a board was due again immediately"
        # Tomorrow: the two with vacancies, but not the dormant one.
        tomorrow = now + jobsdb.ACTIVE_EVERY + 60
        due = {b["token"] for b in jobsdb.due_boards(all_boards, now=tomorrow)}
        assert due == {"kestrel", "wise"}, due
        # Next week: all three.
        later = now + jobsdb.DORMANT_EVERY + 60
        due = {b["token"] for b in jobsdb.due_boards(all_boards, now=later)}
        assert due == {"kestrel", "wise", "quiet"}, due
        # A board never read is always due, and goes first.
        fresh_board = board("newcomer")
        order = jobsdb.due_boards(all_boards + [fresh_board], now=now)
        assert order and order[0]["token"] == "newcomer", \
            [b["token"] for b in order]
        return "daily when hiring, weekly when not, unread boards first"
    check("refresh: tiered by whether the board has jobs", t_tiering)

    def t_retargeting_costs_no_fetch():
        """Changing what you want re-scores; it does not re-read anything."""
        reads: list[str] = []

        def counted(ats: str, token: str, **_kw):
            reads.append(token)
            return fake_read_board(ats, token)

        jobsdb.read_board = counted
        try:
            chef = Profile(full_name="Rowan Ellis Hartley",
                           email="r.hartley@example.com",
                           headline="Head Chef", summary="Kitchens.",
                           skills=["Kitchen"], seniority="ic",
                           target_titles=["Head Chef"],
                           target_locations=["Remote (UK)"])
            assert jobsdb.profile_rev(chef) != jobsdb.profile_rev(PROFILE), \
                "two different profiles fingerprint the same"
            n = jobsdb.rescore(chef)
            assert n, "nothing was re-scored"
            assert not reads, f"re-targeting hit the network: {reads}"
            # Nothing is left disagreeing with the new targets.
            assert jobsdb.rescore(chef) == 0, "a second pass found more to do"
        finally:
            jobsdb.read_board = fake_read_board
        jobsdb.rescore(PROFILE)                 # put it back for later checks
        return f"{n} rows re-scored, 0 boards fetched"
    check("targets: changing them re-scores rather than re-fetches",
          t_retargeting_costs_no_fetch)

    def t_fingerprint_is_stable():
        """The same profile fingerprints the same, run after run.

        ``hash()`` is salted per process. Used here it would mark every row
        stale on every launch, and the app would re-score 1.2 million rows
        before it could show you anything.
        """
        import subprocess
        code = ("import sys; sys.path.insert(0, %r);"
                "from sushi import jobsdb;"
                "from sushi.models import Profile;"
                "print(jobsdb.profile_rev(Profile(full_name='A', email='a@b.c',"
                " target_titles=['Operations Analyst'],"
                " target_locations=['Remote (UK)'], skills=['Python','SQL'],"
                " seniority='ic', headline='Senior Operations Analyst')))"
                % str(Path(__file__).resolve().parents[2]))
        out = [subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, env={**os.environ},
                              timeout=120).stdout.strip() for _ in range(2)]
        assert out[0] and out[0] == out[1], f"unstable across processes: {out}"
        return f"stable across processes: {out[0]}"
    check("targets: the fingerprint survives a restart", t_fingerprint_is_stable)

    def t_interrupted_refresh_keeps_its_work():
        """Stopping halfway keeps what was read; the rest is still due."""
        jobsdb.forget_all()
        BOARDS["kestrel"] = jobs_for("kestrel", ["Operations Analyst"])
        BOARDS["wise"] = jobs_for("wise", ["Staff Data Analyst"])
        stop = {"now": False}
        seen: list = []

        def on_jobs(roles):
            seen.extend(roles)
            stop["now"] = True          # stop as soon as anything lands

        jobsdb.refresh(all_boards, profile=PROFILE, workers=1,
                       include_global=False, on_jobs=on_jobs,
                       should_stop=lambda: stop["now"])
        st = jobsdb.stats()
        assert st.jobs, "an interrupted refresh kept nothing"
        assert st.boards_read < 3, f"it read everything anyway: {st.boards_read}"
        assert not st.fresh, "an interrupted refresh was marked complete"
        left = jobsdb.due_boards(all_boards)
        assert left, "nothing was left due after stopping early"
        # And finishing the job completes it.
        jobsdb.refresh(all_boards, profile=PROFILE, workers=3,
                       include_global=False)
        assert jobsdb.stats().fresh, "the follow-up refresh did not complete it"
        return f"kept {st.jobs} job(s), {len(left)} board(s) still due, then finished"
    check("refresh: interrupted work is kept, not lost",
          t_interrupted_refresh_keeps_its_work)

    def t_known_closed_is_careful():
        """"Gone" means read-and-absent, never merely absent.

        The distinction is the whole safety of the feature. Insane mode
        skips a role this reports on, so treating "not on file" as "closed"
        would make a fresh install — where nothing is on file — refuse to
        apply to anything at all, silently.
        """
        from sushi.models import Role

        jobsdb.forget_all()
        role = Role(company="Kestrel", title="Operations Analyst",
                    url="https://example.com/kestrel/0",
                    ats="greenhouse", token="kestrel")
        assert not jobsdb.known_closed(role),             "an unread board was reported as closed — a fresh install would "             "apply to nothing"

        # Read the board: now absence is evidence.
        BOARDS["kestrel"] = jobs_for("kestrel", ["Operations Analyst"])
        jobsdb.store_board(board("kestrel"), BOARDS["kestrel"], profile=PROFILE)
        assert not jobsdb.known_closed(role), "a live posting was called closed"

        # A different posting at a different URL: jobs_for numbers its URLs
        # by position, so reusing it here would replace the title at the
        # same URL — an update, not a closure.
        BOARDS["kestrel"] = [{"title": "Something Else",
                              "location": "Remote (UK)", "department": "Ops",
                              "url": "https://example.com/kestrel/99"}]
        jobsdb.store_board(board("kestrel"), BOARDS["kestrel"], profile=PROFILE)
        assert jobsdb.known_closed(role),             "a posting taken off a board we just read was not reported closed"

        # A pasted link or an aggregator posting has no board to check.
        loose = Role(company="Somewhere", title="Analyst",
                     url="https://example.com/elsewhere/1")
        assert not jobsdb.known_closed(loose),             "a role with no board was reported as closed"
        return "unread boards are not evidence; read-and-absent is"
    check("closures: unread is not the same as closed",
          t_known_closed_is_careful)

    def t_global_tier():
        """The worldwide feeds are stored, and grow the company corpus.

        Both were lost for a while: the scan became a database refresh and
        nothing called ``find_roles`` any more, so the aggregators stopped
        being read and the corpus — which had grown from 1,078 companies to
        15,589 precisely by harvesting board tokens out of those postings —
        stopped growing at all.
        """
        from sushi import discovery, sources

        jobsdb.forget_all()
        postings = [
            {"company": "Pleo", "title": "Operations Analyst",
             "location": "Remote (UK)", "department": "Ops",
             "url": "https://boards.greenhouse.io/kestrelqa/jobs/1"},
            {"company": "Tide", "title": "Data Analyst",
             "location": "London", "department": "Data",
             "url": "https://jobs.lever.co/wiseqa/abc"},
            {"company": "Nowhere Ltd", "title": "Head Chef",
             "location": "Mars", "department": "", "url": "https://example.com/x"},
        ]
        grew: list = []
        real_fetch = sources.fetch_global_postings
        real_persist = discovery.persist_boards
        sources.fetch_global_postings = lambda **k: list(postings)
        discovery.persist_boards = lambda bs: (grew.extend(bs), len(bs))[1]
        try:
            added, grown, roles = jobsdb.refresh_global(PROFILE)
        finally:
            sources.fetch_global_postings = real_fetch
            discovery.persist_boards = real_persist

        assert added == 3, f"{added} stored, expected 3"
        # Each posting keeps its own employer. A board names one company for
        # all of its jobs; an aggregator names one per posting, and storing
        # them under the board's name left the feed full of blank employers.
        companies = {r.company for r in jobsdb.search(PROFILE, min_score=0)}
        assert {"Pleo", "Tide", "Nowhere Ltd"} <= companies, companies
        # The two apply links that point at real boards grew the corpus; the
        # one that points nowhere did not.
        assert grown == 2, f"{grown} boards harvested, expected 2: {grew}"
        # Fictional tokens on purpose: harvest_boards skips boards the
        # corpus already has, and the corpus ships with the real ones.
        assert ("greenhouse", "kestrelqa") in grew, grew
        assert ("lever", "wiseqa") in grew, grew
        # It hands back everything it stored; refresh() is what filters to
        # the feed bar before anything crosses into the UI.
        assert any(r.score >= 8 for r in roles), [r.score for r in roles]
        assert len(roles) == 3, [r.title for r in roles]
        jobsdb.forget_all()
        return f"{added} postings stored under their own employers, {grown} boards harvested"
    check("global feeds: stored per employer, and they grow the corpus",
          t_global_tier)

    def t_a_partial_build_is_not_done():
        """An interrupted build must not look finished.

        This is what left a real database at 76 of 15,654 boards for a day:
        freshness was judged by when a scan last ran, so a refresh that
        crashed eight seconds in still counted as "recent" and nothing
        resumed it. Only working through everything due marks it done.
        """
        jobsdb.forget_all()
        BOARDS["kestrel"] = jobs_for("kestrel", ["Operations Analyst"])
        BOARDS["wise"] = jobs_for("wise", ["Staff Data Analyst"])

        # One board read by hand: a build that started and stopped.
        jobsdb.store_board(board("kestrel"), BOARDS["kestrel"], profile=PROFILE)
        st = jobsdb.stats()
        assert st.jobs, "setup: something should be on file"
        assert not st.fresh,             "a partial build reports itself as a completed refresh"
        assert jobsdb.due_boards(all_boards),             "nothing was due after reading one board of three"

        # Finishing it is what makes it fresh.
        jobsdb.refresh(all_boards, profile=PROFILE, workers=2,
                       include_global=False)
        assert jobsdb.stats().fresh, "a completed refresh is not marked done"
        assert not jobsdb.due_boards(all_boards), "boards still due after it finished"
        jobsdb.forget_all()
        return "partial builds stay due; a completed one does not"
    check("refresh: a partial build is not mistaken for a finished one",
          t_a_partial_build_is_not_done)

    def t_forget_all():
        """The cache can be thrown away — it is all rebuildable."""
        jobsdb.forget_all()
        st = jobsdb.stats()
        assert (st.jobs, st.boards_read) == (0, 0), st
        assert jobsdb.due_boards(all_boards), "nothing was due after a wipe"
        return "emptied, and every board due again"
    check("cache: it can be deleted and rebuilt", t_forget_all)

    print()
    width = max(len(n) for _, n, _ in RESULTS) + 2
    for status, name, detail in RESULTS:
        print(f"  {'ok  ' if status == 'PASS' else 'FAIL'}  {name:<{width}} {detail}")
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
