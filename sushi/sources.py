"""Global job-board sources.

The original discovery only read a shipped corpus of company ATS boards. This
adds live aggregator feeds that return *actual postings* from across the web, so
a scan surfaces the latest jobs globally and then scores them for relevance.

Eight feeds, read in parallel and de-duplicated, and they are read *deep*:
following Arbeitnow's pages and asking The Muse and Himalayas for more than
their first twenty takes the sweep from about 500 postings to about 4,000 for
roughly the same wall-clock time, because the slow part was never the data.

One thing these feeds no longer give is an apply link that points anywhere but
back at themselves — Himalayas' ``applicationLink`` used to be the employer's
real ATS and is now a himalayas.app URL — so :func:`harvest_boards` comes back
empty where it once found boards. What they do still give is the *employer's
name*, and a name is enough for :func:`sushi.ats.probe` to go and find the
board. That is why :func:`sushi.discovery.refresh_companies` reads all of this
into the company list.

Every source is public and unauthenticated. One of them (We Work Remotely)
only publishes RSS. Failures are swallowed per-source so one dead feed never
breaks a scan.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from .ats import ats_from_url, fetch_json, fetch_text, read_board

# A normalised posting before it becomes a Role.
Posting = dict[str, str]


def _norm(company: str, title: str, location: str, url: str,
          dept: str = "", desc: str = "") -> Posting:
    company = (company or "").strip()
    # Arbeitnow's regional domains hand back percent-encoded names, and
    # "1St%20Formations" becomes a company of its own in the corpus.
    if "%" in company:
        company = urllib.parse.unquote(company).strip()
    return {
        "company": company,
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": (url or "").strip(),
        "department": (dept or "").strip(),
        "description": (desc or "")[:2000],
    }


# --- individual aggregator clients -----------------------------------------

#: Anything network-shaped a feed client can throw. Each client swallows its
#: own, so one source paginating into a 500 never shortens anybody else's read.
_NET = (urllib.error.URLError, urllib.error.HTTPError, OSError,
        json.JSONDecodeError, ET.ParseError, UnicodeDecodeError)

#: How deep each paged feed is read. These are measured, not guessed: at
#: these depths the five paged sources together take about fifteen seconds
#: and return roughly 4,000 postings, against 500 for the one-page-each
#: version that shipped. Arbeitnow earns the most pages because it is the
#: only one of them that hands back 300 at a time.
PAGES = {"arbeitnow": 10, "themuse": 25, "himalayas": 15}


def from_remotive() -> list[Posting]:
    """Remotive.

    Note the size of it: this feed returns eleven postings and no more. The
    ``limit`` parameter does nothing, and so does ``category`` — all thirty
    categories hand back the same eleven. It stays because eleven fresh
    postings cost one request, but it is not worth paginating.
    """
    d = fetch_json("https://remotive.com/api/remote-jobs?limit=200")
    return [_norm(j.get("company_name", ""), j.get("title", ""),
                  j.get("candidate_required_location", "Remote"), j.get("url", ""),
                  j.get("category", ""), j.get("description", ""))
            for j in d.get("jobs", [])]


def from_arbeitnow(pages: int = 0) -> list[Posting]:
    """Arbeitnow, followed page by page — by far the deepest free feed here.

    300 postings a page, and it hands back the next page's URL rather than
    taking a page number, so the loop follows ``links.next`` rather than
    counting. Reading one page was leaving about nine tenths of it unread.
    """
    out: list[Posting] = []
    url = "https://www.arbeitnow.com/api/job-board-api"
    for _ in range(pages or PAGES["arbeitnow"]):
        try:
            d = fetch_json(url)
        except _NET:
            break
        for j in d.get("data", []):
            out.append(_norm(j.get("company_name", ""), j.get("title", ""),
                             j.get("location", ""), j.get("url", ""),
                             ", ".join(j.get("tags", []) or [])[:60],
                             j.get("description", "")))
        url = (d.get("links") or {}).get("next") or ""
        if not url:
            break
    return out


def from_jobicy() -> list[Posting]:
    """Jobicy. ``count`` tops out at 100, and it has no offset to page with."""
    d = fetch_json("https://jobicy.com/api/v2/remote-jobs?count=100")
    return [_norm(j.get("companyName", ""), j.get("jobTitle", ""),
                  j.get("jobGeo", "Remote"), j.get("url", ""),
                  (j.get("jobIndustry") or [""])[0] if isinstance(j.get("jobIndustry"), list) else "",
                  j.get("jobExcerpt", ""))
            for j in d.get("jobs", [])]


def from_themuse(pages: int = 0) -> list[Posting]:
    """The Muse: 20 a page out of four hundred thousand, so depth is all there is.

    Pages are fetched together rather than in sequence. They are numbered
    rather than chained, so there is nothing to wait for, and twenty-five
    round trips end to end was most of the time the whole sweep took.
    """
    def page(n: int) -> list[Posting]:
        try:
            d = fetch_json(f"https://www.themuse.com/api/public/jobs?page={n}")
        except _NET:
            return []
        got = []
        for j in d.get("results", []):
            locs = ", ".join(l.get("name", "") for l in (j.get("locations") or []))
            got.append(_norm((j.get("company") or {}).get("name", ""), j.get("name", ""),
                             locs, (j.get("refs") or {}).get("landing_page", ""),
                             ", ".join(c.get("short_name", "") for c in (j.get("categories") or []))))
        return got

    out: list[Posting] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for got in pool.map(page, range(pages or PAGES["themuse"])):
            out.extend(got)
    return out


def from_himalayas(pages: int = 0) -> list[Posting]:
    """Himalayas. Caps a page at 20 whatever ``limit`` says, so this cursors.

    Worth more than its size suggests: its ``applicationLink`` is the real
    apply URL rather than a link back to itself, which is what
    :func:`harvest_boards` reads company ATS boards out of.
    """
    out: list[Posting] = []
    cursor = ""
    for _ in range(pages or PAGES["himalayas"]):
        url = "https://himalayas.app/jobs/api?limit=20" + (f"&cursor={cursor}" if cursor else "")
        try:
            d = fetch_json(url)
        except _NET:
            break
        for j in d.get("jobs", []):
            locs = ", ".join(j.get("locationRestrictions") or []) or "Remote"
            out.append(_norm(j.get("companyName", ""), j.get("title", ""), locs,
                             j.get("applicationLink") or j.get("url", ""),
                             "", j.get("excerpt", "")))
        cursor = d.get("nextCursor") or ""
        if not cursor:
            break
    return out


def from_remoteok() -> list[Posting]:
    """RemoteOK. The whole feed in one request, with a licence notice on top.

    The first element of the array is not a job — it is RemoteOK's terms —
    so it is filtered out by having no position rather than by index, in case
    they ever stop sending it.
    """
    d = fetch_json("https://remoteok.com/api")
    out = []
    for j in d if isinstance(d, list) else []:
        if not j.get("position"):
            continue
        tags = j.get("tags") or []
        out.append(_norm(j.get("company", ""), j.get("position", ""),
                         j.get("location") or "Remote",
                         j.get("apply_url") or j.get("url", ""),
                         ", ".join(t for t in tags if t)[:60],
                         j.get("description", "")))
    return out


def from_weworkremotely() -> list[Posting]:
    """We Work Remotely, from its RSS feed — it has no JSON one.

    Its ``<title>`` is "Company: Role", which is the only place the company
    name appears, so it is split rather than parsed out of a field.
    """
    xml = fetch_text("https://weworkremotely.com/remote-jobs.rss")
    root = ET.fromstring(xml)
    out = []
    for item in root.iterfind("./channel/item"):
        def text(tag: str) -> str:
            return (item.findtext(tag) or "").strip()

        title = text("title")
        company, _, role = title.partition(":")
        if not role.strip():            # no colon: all we have is a title
            company, role = "", title
        where = text("region") or text("country") or "Remote"
        out.append(_norm(company.strip(), role.strip(), where, text("link"),
                         text("category"), text("description")[:2000]))
    return out


def from_workingnomads() -> list[Posting]:
    d = fetch_json("https://www.workingnomads.com/api/exposed_jobs/")
    return [_norm(j.get("company_name", ""), j.get("title", ""),
                  j.get("location") or "Remote", j.get("url", ""),
                  j.get("category_name", ""), j.get("description", ""))
            for j in d if isinstance(d, list) and j.get("title")]


SOURCES: dict[str, Callable[[], list[Posting]]] = {
    "Remotive": from_remotive,
    "Arbeitnow": from_arbeitnow,
    "Jobicy": from_jobicy,
    "The Muse": from_themuse,
    "Himalayas": from_himalayas,
    "RemoteOK": from_remoteok,
    "We Work Remotely": from_weworkremotely,
    "Working Nomads": from_workingnomads,
}


# --- orchestration ----------------------------------------------------------

def _dedup_key(p: Posting) -> str:
    """What makes two postings the same job.

    The URL where there is one — the feeds mostly link to their own pages, so
    a job carried by three of them is three URLs — and company plus title
    otherwise, which catches the rest. Lower-cased and squeezed because
    "Senior  Engineer" and "senior engineer" are not two vacancies.
    """
    url = (p.get("url") or "").strip().rstrip("/").lower()
    if url:
        return url
    return " ".join(f"{p.get('company','')} {p.get('title','')}".lower().split())


def fetch_global_postings(on_progress: Callable[[str], None] | None = None,
                          should_stop: Callable[[], bool] | None = None) -> list[Posting]:
    """Pull the latest postings from every global source, in parallel.

    De-duplicated on the way out: eight feeds carrying the same remote roles
    is eight chances at the same job, and the scanner would otherwise score
    and show each copy.
    """
    out: list[Posting] = []
    seen: set[str] = set()
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        futures = {pool.submit(fn): name for name, fn in SOURCES.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            if should_stop and should_stop():
                break
            try:
                got = fut.result()
            except Exception:
                if on_progress:
                    on_progress(f"{name}: unavailable")
                continue
            kept = 0
            for p in got:
                key = _dedup_key(p)
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(p)
                kept += 1
            if on_progress:
                on_progress(f"{name}: {kept} jobs"
                            + (f" ({len(got) - kept} already seen)"
                               if kept < len(got) else ""))
    return out


def harvest_boards(postings: list[Posting],
                   known: set[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    """Identify company ATS boards from posting apply links.

    This is the "find company pages" step: a posting that applies via a
    Greenhouse/Lever/Ashby/… board tells us that company's whole board token, so
    we can read every open role there, not just the one that surfaced.
    """
    known = known or set()
    found: dict[tuple[str, str], None] = {}
    for p in postings:
        hit = ats_from_url(p.get("url", ""))
        if hit and hit not in known:
            found[hit] = None
    return list(found)


def read_harvested(boards: list[tuple[str, str]], workers: int = 10,
                   on_progress: Callable[[str], None] | None = None,
                   should_stop: Callable[[], bool] | None = None) -> list[Posting]:
    """Read every open role from freshly-identified company boards."""
    out: list[Posting] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(read_board, ats, token): (ats, token) for ats, token in boards}
        for fut in as_completed(futures):
            done += 1
            if should_stop and should_stop():
                break
            try:
                for j in fut.result():
                    # read_board gives title/location/url/department but no company name
                    out.append(_norm("", j.get("title", ""), j.get("location", ""),
                                     j.get("url", ""), j.get("department", "")))
            except Exception:
                pass
            if on_progress and done % 15 == 0:
                on_progress(f"Reading company pages… {done}/{len(boards)}")
    return out
