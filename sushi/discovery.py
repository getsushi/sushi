"""Find roles worth this particular person's time.

Sushi ships with a corpus of remote-first companies already resolved to their
ATS boards, so a new user gets results on first run without waiting for a
crawl. The corpus refreshes in the background.

Matching is driven entirely by the Profile the AI built from the CV - there are
no hardcoded job titles here. That is the difference between a product and
somebody's personal script.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterator

from .ats import (PROBE_REV, ats_from_url, company_from_token, fetch_json,
                  fetch_text, probe, read_board)
from .config import COMPANIES_PATH, CACHE_DIR
from .models import Profile, Role

log = logging.getLogger(__name__)

#: Guards the company corpus — the parsed cache, and the file under it.
#: Reentrant because :func:`editing_companies` holds it across the
#: ``load_companies``/``save_companies`` pair, both of which take it too.
_LOCK = threading.RLock()


# --- the company corpus -----------------------------------------------------

#: The parsed corpus, and the (mtime, size) of the file it came from.
#: Guarded by ``_LOCK``, which was declared for this and never used.
#:
#: Starting one scan parses this file four times — ``maybe_autoscan`` to see
#: what is due, ``start_scan`` for the board list, ``corpus_size`` to warm
#: the count, then ``refresh_global`` again on the worker — while another
#: thread may be replacing it underneath. Five megabytes and twenty-six
#: thousand records each time, for a file that changes a few times an hour.
_CORPUS: dict[str, dict[str, Any]] | None = None
_CORPUS_STAMP: tuple[float, int] | None = None


def _file_stamp() -> tuple[float, int] | None:
    try:
        st = COMPANIES_PATH.stat()
    except OSError:
        return None
    return (st.st_mtime, st.st_size)


def _parse_corpus() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not COMPANIES_PATH.exists():
        return out
    with COMPANIES_PATH.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    rec = json.loads(line)
                    out[rec["key"]] = rec
                except (json.JSONDecodeError, KeyError):
                    continue
    return out


def load_companies() -> dict[str, dict[str, Any]]:
    """The corpus, parsed once per change to the file.

    Every caller gets its own copy of the mapping *and* of each record,
    because half of them mutate what they are given — ``resolve_boards``
    stamps ``probed`` onto records, ``persist_boards`` adds keys — and then
    hand it to :func:`save_companies`. Copying twenty-six thousand small
    dicts costs about 6ms against about 57ms to parse them, so the cache is
    worth having even though it cannot be shared.
    """
    global _CORPUS, _CORPUS_STAMP

    with _LOCK:
        stamp = _file_stamp()
        if _CORPUS is None or stamp != _CORPUS_STAMP:
            _CORPUS = _parse_corpus()
            _CORPUS_STAMP = stamp
        return {key: dict(rec) for key, rec in _CORPUS.items()}


def save_companies(companies: dict[str, dict[str, Any]]) -> None:
    """Write the corpus, atomically and one writer at a time.

    The temp file is named for the writer. It used to be one fixed path, so
    two threads saving at once — the sweep harvesting boards while a feed
    pass adds employers — wrote into the same file at the same time and then
    both renamed it over the corpus. What survived was the interleaving of
    two JSON streams, which ``load_companies`` then skipped line by line:
    companies silently disappearing from a 26,000-row file with nothing
    logged. The lock makes that impossible within one process; the unique
    name covers the app and a QA harness running against the same corpus,
    and means a crash part-way leaves a stray temp rather than a wreck.

    This writes whatever it is given, so two callers that each read the
    corpus and then save it back still lose each other's work. That is what
    :func:`editing_companies` is for, and what the writers here use.
    """
    global _CORPUS, _CORPUS_STAMP

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        tmp = COMPANIES_PATH.with_name(
            f"{COMPANIES_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                for rec in companies.values():
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            tmp.replace(COMPANIES_PATH)
        finally:
            tmp.unlink(missing_ok=True)   # only still there if the write failed
        # Kept rather than invalidated: this thread already holds the whole
        # corpus in memory, so the next reader need not parse it back in.
        _CORPUS = {key: dict(rec) for key, rec in companies.items()}
        _CORPUS_STAMP = _file_stamp()


@contextlib.contextmanager
def editing_companies() -> Iterator[dict[str, dict[str, Any]]]:
    """Read the corpus, change it, and keep what others changed meanwhile.

    Every writer of this file does read, change, write — and the middle
    step is minutes of network work, so two of them overlap routinely:
    ``resolve_boards`` probing careers pages while the sweep harvests new
    employers off the global feeds. Writing a whole snapshot back then
    silently discards everything the other one added. Measured with four
    writers making six additions each: ten of the twenty-four survived.

    So the slow part happens on a private copy, outside the lock, and only
    the merge is serialised — re-read what is on disk now, apply the
    records this caller actually touched, write that. Both writers keep
    their work; the only thing that can still be lost is two callers
    changing the *same* record, where last-write-wins is the honest answer
    anyway.
    """
    before = load_companies()
    working = {key: dict(rec) for key, rec in before.items()}
    yield working
    with _LOCK:
        latest = load_companies()
        for key, rec in working.items():
            if key not in before or rec != before[key]:
                latest[key] = rec
        for key in before:
            if key not in working:          # deliberately dropped
                latest.pop(key, None)
        save_companies(latest)


_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")
_SUFFIX_RE = re.compile(
    r"\b(inc|llc|ltd|limited|gmbh|bv|corp|corporation|plc|sa|ag|srl|pty)\b")


def norm_name(name: str) -> str:
    # Precompiled, like every other pattern in this module's hot path: a
    # dynamic `re.search` loop here is what crashed the scanner.
    s = _NON_ALNUM_RE.sub(" ", (name or "").lower())
    s = _SUFFIX_RE.sub(" ", s)
    return " ".join(s.split())


def boards(companies: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Companies we can actually read vacancies from."""
    cos = companies if companies is not None else load_companies()
    return [c for c in cos.values() if c.get("ats") and c.get("token")]


#: Where the rotation left off, so the next scan reads companies this one
#: did not. Kept in settings rather than memory: the point is that closing
#: the app and opening it tomorrow continues the sweep.
CURSOR_KEY = "corpus_cursor"


def scan_slice(limit: int, *, advance: bool = True) -> list[dict[str, Any]]:
    """The next ``limit`` boards to read, rotating through the whole corpus.

    The corpus went from about a thousand boards to fifteen thousand, and a
    scan that always reads the first twelve hundred alphabetically would see
    the same companies forever — fourteen thousand of them permanently
    invisible, and a second scan that could only ever return what the first
    one did.

    So each scan takes the next slice and remembers where it stopped. Jobs
    accumulate across scans, which is the behaviour you want anyway: leave
    it running and the feed keeps filling, rather than finishing once.
    """
    from . import storage

    all_boards = boards()
    if not all_boards:
        return []
    total = len(all_boards)
    if limit >= total:
        return all_boards

    try:
        start = int(storage.get_setting(CURSOR_KEY, "0") or 0) % total
    except ValueError:
        start = 0
    end = start + limit
    # Wrap, so the slice is always full even at the end of the corpus.
    chunk = (all_boards[start:end] if end <= total
             else all_boards[start:] + all_boards[:end - total])
    if advance:
        storage.set_setting(CURSOR_KEY, str(end % total))
    log.info("corpus: reading boards %d-%d of %d", start, end, total)
    return chunk


def reset_scan_cursor() -> None:
    """Start the sweep again from the top of the corpus."""
    from . import storage
    storage.set_setting(CURSOR_KEY, "0")


def scan_cursor() -> int:
    """How far through the corpus the sweep has read."""
    from . import storage
    try:
        return int(storage.get_setting(CURSOR_KEY, "0") or 0)
    except ValueError:
        return 0


def corpus_size() -> int:
    return len(boards())


def persist_boards(found: list[tuple[str, str]]) -> int:
    """Save newly-discovered (ats, token) boards into the corpus so the company
    list grows with every scan. Returns how many were added."""
    if not found:
        return 0
    added = 0
    with editing_companies() as companies:
        existing = {(c.get("ats"), c.get("token")) for c in companies.values()}
        for ats, token in found:
            if (ats, token) in existing:
                continue
            key = f"{ats}:{token}"
            if key in companies:
                continue
            companies[key] = {
                "key": key, "name": company_from_token(token),
                "sources": ["harvested"], "ats": ats, "token": token,
                "slug": token, "locations": [], "first_seen": time.strftime("%Y-%m-%d"),
                "probed": True,
            }
            existing.add((ats, token))
            added += 1
    return added


# --- refreshing the corpus --------------------------------------------------

def refresh_companies(max_pages: int = 300, on_progress: Callable[[str], None] | None = None) -> int:
    """Top up the corpus. Returns the number of new companies.

    Two passes. The deep one is Himalayas, which cursors twenty at a time and
    is the only feed here that will hand over thousands of companies if you
    keep asking. The broad one is every other feed Sushi reads, which between
    them name well over a thousand employers a sweep that Himalayas does not.

    A company's name is the whole point of this: no feed exposes a real apply
    URL any more — Himalayas' ``applicationLink`` now points back at
    Himalayas — so a board can only be found by taking the name and probing
    for it. That makes this list the mouth of the funnel: every company added
    here is one :func:`resolve_boards` can go looking for a careers page for.
    """
    added = 0
    with editing_companies() as companies:
        before = len(companies)
        cursor = ""
        for page in range(max_pages):
            url = "https://himalayas.app/jobs/api?limit=20" + (f"&cursor={cursor}" if cursor else "")
            try:
                d = fetch_json(url)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
                break
            jobs = d.get("jobs") or []
            if not jobs:
                break
            for j in jobs:
                name = j.get("companyName") or ""
                nk = norm_name(name)
                if not nk or nk in companies:
                    continue
                companies[nk] = {
                    "key": nk, "name": name.strip(), "sources": ["himalayas"],
                    "ats": None, "token": None, "slug": j.get("companySlug", ""),
                    "locations": [], "first_seen": time.strftime("%Y-%m-%d"),
                }
                if found := ats_from_url(j.get("applicationLink", "")):
                    companies[nk]["ats"], companies[nk]["token"] = found
            cursor = d.get("nextCursor") or ""
            if not cursor:
                break
            if on_progress and page % 20 == 0:
                on_progress(f"Scanning job boards… {len(companies)} companies known")
            time.sleep(0.15)

        _add_companies_from_feeds(companies, on_progress)
        added = len(companies) - before
    return added


def _add_companies_from_feeds(companies: dict[str, dict[str, Any]],
                              on_progress: Callable[[str], None] | None = None) -> int:
    """Name every employer the other feeds mention, for the prober to chase."""
    from .sources import fetch_global_postings

    if on_progress:
        on_progress("Reading the global feeds for company names…")
    try:
        postings = fetch_global_postings()
    except Exception as exc:                  # a feed being down is not fatal
        log.info("corpus: global feeds unavailable (%s)", exc)
        return 0
    added = add_companies(companies, postings)
    if on_progress:
        on_progress(f"{added} new companies from {len(postings)} postings")
    return added


def note_companies(postings: list[dict[str, str]]) -> int:
    """Record the employers in a set of postings, if any are new to us.

    Called from the daily job-database refresh, which already has the
    postings in hand. Without it the company list only grew when somebody
    pressed a button in Settings, so the feeds could name eight hundred
    employers a day and Sushi would go on not knowing any of them.
    """
    if not postings:
        return 0
    with editing_companies() as companies:
        added = add_companies(companies, postings)
    return added


def add_companies(companies: dict[str, dict[str, Any]],
                  postings: list[dict[str, str]]) -> int:
    """Add any unknown employer in ``postings`` to an in-memory corpus."""
    added = 0
    for p in postings:
        name = (p.get("company") or "").strip()
        nk = norm_name(name)
        if not nk or nk in companies:
            continue
        companies[nk] = {
            "key": nk, "name": name, "sources": ["feeds"],
            "ats": None, "token": None, "slug": "",
            "locations": [l for l in [(p.get("location") or "").strip()] if l],
            "first_seen": time.strftime("%Y-%m-%d"),
            "sample_title": (p.get("title") or "")[:120],
        }
        if found := ats_from_url(p.get("url", "")):
            companies[nk]["ats"], companies[nk]["token"] = found
        added += 1
    return added


def _probe_rev(rec: dict[str, Any]) -> int:
    """Which generation of the prober last looked at this company.

    Companies probed before the stamp existed carry ``probed: True`` and no
    revision, which is revision 1 — the six-provider list.
    """
    return int(rec.get("probe_rev") or (1 if rec.get("probed") else 0))


#: A company with no board found is looked at again after this long. Two
#: reasons, both real: a company that was not hiring through an ATS in March
#: may well be in September, and a probe that failed because the network
#: hiccupped would otherwise be a permanent verdict.
PROBE_STALE_AFTER = 180 * 24 * 3600


def _probe_due(rec: dict[str, Any], now: float) -> bool:
    if rec.get("ats"):
        return False
    if _probe_rev(rec) < PROBE_REV:
        return True
    return (now - float(rec.get("probed_at") or 0)) > PROBE_STALE_AFTER


def pending_probes() -> int:
    """How many companies are still waiting to be checked for a board.

    Adding a provider puts every previously-unresolved company back on this
    list, and the list is worked through a slice at a time — so the number
    belongs on screen, otherwise a refresh that found four boards out of
    thousands looks like the feature doing nothing.
    """
    now = time.time()
    return sum(1 for c in load_companies().values() if _probe_due(c, now))


def resolve_boards(limit: int = 300, workers: int = 12,
                   on_progress: Callable[[str], None] | None = None) -> int:
    """Work out which ATS the unresolved companies use.

    A company is re-probed when the provider list has grown since it was last
    looked at, and again once its last look is six months old. Without the
    first, the thousands already marked "probed, nothing found" would never
    be tried against Workday, Personio or the rest, and adding a provider
    would only help companies discovered afterwards. Without the second, a
    company that starts hiring later — or whose probe happened to land while
    the network was down — stays unresolved for ever.
    """
    now = time.time()
    found = 0
    with editing_companies() as companies:
        todo = [c for c in companies.values() if _probe_due(c, now)][:limit]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(probe, c["name"], seed=c.get("slug") or ""): c for c in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                rec = futures[fut]
                rec["probed"] = True
                rec["probe_rev"] = PROBE_REV
                rec["probed_at"] = now
                try:
                    if hit := fut.result():
                        rec["ats"], rec["token"] = hit[0], hit[1]
                        found += 1
                except Exception:
                    pass
                if on_progress and i % 25 == 0:
                    on_progress(f"Finding careers pages… {found} found of {i} checked")
    return found


# --- matching ---------------------------------------------------------------

#: The most a vacancy can score without resembling any target title. Below
#: every band on the quality dial, on purpose.
NO_TITLE_CEILING = 4

#: Bumped whenever scoring changes. It goes into the fingerprint stored
#: against every row, so a change to the *matcher* invalidates them just as
#: a change to the profile does. Without it, fixing the scorer left a
#: million rows carrying scores from the old one and ``rescore`` reported
#: nothing to do.
#: 7: location is now the user's answer and nothing else. The CV's country
#: no longer widens a stated answer or stands in for a missing one,
#: Worldwide is read as "no country required", and a location field saying
#: "Remote - US" is the United States rather than nowhere in particular.
#: 8: a country named inside a region narrows it rather than widening the
#: search alongside it, so "APAC, Pakistan" is Pakistan. Without the bump
#: every row keeps the score it got when it meant "APAC or Pakistan". Also
#: "Worldwide, remote only", which now means what the picker says it means.
#: 9: a job in a country the user did not ask for is dropped rather than
#: docked, so "Where to look" filters the feed instead of nudging it.
#: 10: and a place the resolver cannot name is a wrong place rather than an
#: unknown one, once the user has said where they want to work. Rev 9 only
#: dropped jobs whose country it could *read* — "Santa Clara, California"
#: read as nothing at all and scored -1, which a matching title paid off
#: with room to spare. Plus the fifty states, their two-letter forms and a
#: hundred-odd cities, so that verdict is reached far less often.
SCORER_REV = 10

NOISE_RE = re.compile(
    r"\b(intern|internship|graduate|apprentice|placement|work experience|volunteer)\b", re.I)

#: One split pattern for the whole module, compiled once.
_WORD_SPLIT_RE = re.compile(r"\W+")

#: Whole-word matchers, kept for ever and never evicted.
_WORD_RE_CACHE: dict[str, re.Pattern[str]] = {}


def word_re(word: str) -> re.Pattern[str]:
    """A cached ``\\bword\\b`` matcher.

    ``re.search(rf"\\b{re.escape(w)}\\b", hay)`` looks harmless and is not:
    every call goes through ``re``'s own module-level cache, a plain dict
    that is wiped wholesale once it passes 512 entries. Fourteen scanner
    threads filling and clearing that dict concurrently is what corrupted
    memory in :func:`countries_in` — and three places in the scorer were
    still doing it, once per skill per vacancy, which is hundreds of
    thousands of times in a rescore of the job table. It surfaced as
    ``TypeError: 'list_iterator' object is not callable`` raised from a line
    that calls neither, and it killed the rescore.

    This cache is append-only and bounded by the user's own vocabulary —
    their target titles and skills — so it is a few dozen entries that are
    compiled once and then only read.
    """
    pattern = _WORD_RE_CACHE.get(word)
    if pattern is None:
        pattern = re.compile(rf"\b{re.escape(word)}\b")
        _WORD_RE_CACHE[word] = pattern
    return pattern

SENIORITY_WORDS = {
    "exec": ["chief", "cto", "cio", "cpo", "vp ", "vice president", "head of", "founder"],
    "director": ["director", "head of", "vp "],
    "manager": ["manager", "lead", "head of"],
    "lead": ["lead", "principal", "staff"],
    "ic": ["engineer", "developer", "scientist", "analyst", "designer", "manager"],
}

# How far from the user's level we are willing to go. Showing an exec a
# mid-level IC job is the fastest way to lose them.
LEVELS = ["ic", "lead", "manager", "director", "exec"]


def _level_index(seniority: str) -> int:
    try:
        return LEVELS.index((seniority or "ic").lower())
    except ValueError:
        return 0


def _title_level(title: str) -> int:
    t = title.lower()
    for i in range(len(LEVELS) - 1, -1, -1):
        if any(w in t for w in SENIORITY_WORDS[LEVELS[i]]):
            return i
    return 0


# "Remote" is a way of working, not a place. Treated as a target location it
# substring-matches every "X (Remote)" posting on earth, which would tell a
# user in California that a Berlin-only job is local to them.
MODALITY = {"remote", "anywhere", "worldwide", "global", "hybrid", "onsite", "on-site", ""}

#: The subset of those that answer "which country?" with "it does not
#: matter". See :func:`location_fit` — this is the whole of the Worldwide fix.
ANYWHERE_WORDS = {"worldwide", "anywhere", "global"}

# A job in "Remote - USA" and a candidate in "United States" are the same
# place, so countries are compared canonically rather than by substring.
_ALIASES = {
    # The states are here because the cities never end. A board writes
    # "Santa Clara, California" or "Southaven, MS; Memphis, TN", and no list
    # of city names is ever going to cover that — but every one of those
    # lines carries a state, and there are fifty of them. See also
    # ``_STATE_ABBR`` for the two-letter form, which is read only out of a
    # location field.
    "US": ["united states", "usa", "u.s.a", "u.s.", "america", "stateside",
           "new york", "nyc", "san francisco", "bay area", "los angeles",
           "seattle", "austin", "chicago", "denver", "atlanta", "miami",
           "dallas", "houston", "phoenix", "san diego", "washington dc",
           "philadelphia", "boston",
           "alabama", "alaska", "arizona", "arkansas", "california",
           "colorado", "connecticut", "delaware", "florida", "georgia",
           "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
           "kentucky", "louisiana", "maine", "maryland", "massachusetts",
           "michigan", "minnesota", "mississippi", "missouri", "montana",
           "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
           "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
           "pennsylvania", "rhode island", "south carolina", "south dakota",
           "tennessee", "texas", "utah", "vermont", "virginia", "washington",
           "west virginia", "wisconsin", "wyoming"],
    "UK": ["united kingdom", "uk", "u.k.", "england", "scotland", "wales", "britain",
           "great britain", "northern ireland", "london", "edinburgh",
           "glasgow", "leeds", "bristol", "cardiff", "belfast",
           "manchester", "birmingham", "liverpool", "sheffield", "newcastle",
           "nottingham", "oxford", "cambridge", "reading", "brighton",
           "southampton", "bath", "york", "aberdeen", "milton keynes"],
    "CA": ["canada", "toronto", "vancouver", "montreal", "ottawa", "calgary",
           "edmonton", "winnipeg", "halifax", "waterloo", "mississauga"],
    "IE": ["ireland", "republic of ireland", "eire"],
    "DE": ["germany", "deutschland", "berlin", "munich", "münchen", "hamburg",
           "frankfurt", "cologne", "stuttgart", "düsseldorf", "dusseldorf",
           "leipzig", "dresden", "nuremberg"],
    "FR": ["france", "paris", "lyon", "marseille", "toulouse", "bordeaux",
           "lille", "nantes", "nice"],
    "ES": ["spain", "madrid", "barcelona", "valencia", "seville", "málaga",
           "malaga", "bilbao", "zaragoza"],
    "PT": ["portugal", "lisbon", "lisboa", "porto"],
    "NL": ["netherlands", "holland", "amsterdam", "rotterdam", "utrecht"],
    "BE": ["belgium", "brussels"],
    "PL": ["poland", "warsaw", "krakow", "kraków", "wroclaw"],
    "SE": ["sweden", "stockholm", "gothenburg"],
    "NO": ["norway", "oslo"], "DK": ["denmark", "copenhagen"],
    "FI": ["finland", "helsinki"], "IT": ["italy", "milan", "rome", "roma"],
    "CH": ["switzerland", "zurich", "zürich", "geneva"],
    "AT": ["austria", "vienna"],
    "CZ": ["czechia", "czech republic", "prague"],
    "HU": ["hungary", "budapest"], "RO": ["romania", "bucharest"],
    "GR": ["greece", "athens"], "BG": ["bulgaria", "sofia"],
    "HR": ["croatia", "zagreb"], "SK": ["slovakia", "bratislava"],
    "SI": ["slovenia", "ljubljana"], "RS": ["serbia", "belgrade"],
    "EE": ["estonia", "tallinn"], "LV": ["latvia", "riga"],
    "LT": ["lithuania", "vilnius"], "LU": ["luxembourg"], "IS": ["iceland"],
    "UA": ["ukraine", "kyiv", "kiev"], "TR": ["turkey", "türkiye", "istanbul"],
    "AU": ["australia", "sydney", "melbourne", "brisbane", "perth",
           "adelaide", "canberra", "hobart", "gold coast"],
    "NZ": ["new zealand", "auckland", "wellington"],
    "IN": ["india", "bangalore", "bengaluru", "mumbai", "new delhi", "hyderabad",
           "pune", "chennai", "gurgaon", "gurugram", "noida", "kolkata",
           "ahmedabad", "jaipur", "indore", "coimbatore", "kochi"],
    "SG": ["singapore"], "JP": ["japan", "tokyo", "osaka"],
    "CN": ["china", "shanghai", "beijing", "shenzhen"],
    "HK": ["hong kong"], "TW": ["taiwan", "taipei"],
    "KR": ["south korea", "seoul"], "MY": ["malaysia", "kuala lumpur"],
    "PH": ["philippines", "manila"], "ID": ["indonesia", "jakarta"],
    "TH": ["thailand", "bangkok"],
    "VN": ["vietnam", "hanoi", "ho chi minh"],
    "PK": ["pakistan", "karachi", "lahore", "islamabad", "rawalpindi",
           "faisalabad", "peshawar", "multan", "quetta", "sialkot"], "BD": ["bangladesh", "dhaka"],
    "BR": ["brazil", "brasil", "sao paulo", "são paulo", "rio de janeiro"],
    "MX": ["mexico", "méxico", "mexico city", "guadalajara"],
    "AR": ["argentina", "buenos aires"], "CL": ["chile", "santiago"],
    "CO": ["colombia", "bogota", "bogotá", "medellin", "medellín", "cali"], "PE": ["peru", "lima"],
    "UY": ["uruguay", "montevideo"], "CR": ["costa rica"], "PA": ["panama"],
    "ZA": ["south africa", "cape town", "johannesburg"],
    "NG": ["nigeria", "lagos"], "KE": ["kenya", "nairobi"],
    "EG": ["egypt", "cairo"], "MA": ["morocco", "casablanca"], "GH": ["ghana"],
    "IL": ["israel", "tel aviv"],
    "AE": ["uae", "united arab emirates", "dubai", "abu dhabi"],
    "SA": ["saudi arabia", "riyadh"], "QA": ["qatar", "doha"],
    "KW": ["kuwait"], "BH": ["bahrain"], "OM": ["oman"], "JO": ["jordan"],
    # Regions behave like countries for this purpose. EMEA is its own thing
    # rather than a synonym for Europe, which is what it used to be: a job
    # open to EMEA is open to Dubai and Cape Town as well as Dublin, and a
    # candidate in either was being told it was not for them.
    "EU": ["europe", "european union", "eea", "schengen"],
    "EMEA": ["emea", "europe middle east and africa"],
    "NA": ["north america"],
    "APAC": ["apac", "asia pacific", "asia-pacific", "asia", "oceania"],
    "LATAM": ["latam", "latin america", "south america"],
    "MEA": ["mea", "middle east", "africa"],
}
# Longest first so "united states" wins before "us".
_ALIAS_PAIRS = sorted(
    ((alias, code) for code, aliases in _ALIASES.items() for alias in aliases),
    key=lambda p: -len(p[0]))

# Membership, so a UK candidate matches an EMEA-wide posting — and, now, so
# an EMEA candidate matches a posting in Munich. Both directions matter: one
# is "the job is open to a region I am in", the other is "I said a region and
# the job is in it", and the second is what the location step on first run
# produces.
_EUROPE = {"UK", "IE", "DE", "FR", "ES", "PT", "NL", "BE", "PL", "SE", "NO",
           "DK", "FI", "IT", "CH", "AT", "CZ", "HU", "RO", "GR", "BG", "HR",
           "SK", "SI", "RS", "EE", "LV", "LT", "LU", "IS", "UA", "TR"}
_MIDDLE_EAST = {"AE", "IL", "SA", "QA", "KW", "BH", "OM", "JO", "TR"}
_AFRICA = {"ZA", "NG", "KE", "EG", "MA", "GH"}

_WITHIN: dict[str, frozenset[str]] = {
    "EU": frozenset(_EUROPE),
    "EMEA": frozenset(_EUROPE | _MIDDLE_EAST | _AFRICA),
    "MEA": frozenset(_MIDDLE_EAST | _AFRICA),
    "NA": frozenset({"US", "CA", "MX"}),
    "APAC": frozenset({"AU", "NZ", "IN", "SG", "JP", "CN", "HK", "TW", "KR",
                       "MY", "PH", "ID", "TH", "VN", "PK", "BD"}),
    "LATAM": frozenset({"BR", "MX", "AR", "CL", "CO", "PE", "UY", "CR", "PA"}),
}

#: The clusters offered on first run, in the order they are offered. The
#: label is what is stored on the profile — these are read back by
#: :func:`countries_in` like any other place name, so a saved profile stays
#: plain text rather than a set of codes only this app can read.
REGION_CHOICES = [
    ("WORLD", "Worldwide"),
    ("EU", "Europe"),
    ("EMEA", "EMEA"),
    ("NA", "North America"),
    ("LATAM", "LATAM"),
    ("APAC", "APAC"),
    ("MEA", "Middle East / Africa"),
]

#: Countries offered by name on first run. Not every code above — this is a
#: list someone scrolls, so it is the ones people actually pick, with the
#: rest reachable by typing.
COUNTRY_CHOICES = [
    ("UK", "United Kingdom"), ("IE", "Ireland"), ("US", "United States"),
    ("CA", "Canada"), ("DE", "Germany"), ("FR", "France"), ("ES", "Spain"),
    ("PT", "Portugal"), ("NL", "Netherlands"), ("BE", "Belgium"),
    ("CH", "Switzerland"), ("AT", "Austria"), ("IT", "Italy"),
    ("PL", "Poland"), ("SE", "Sweden"), ("NO", "Norway"), ("DK", "Denmark"),
    ("FI", "Finland"), ("CZ", "Czechia"), ("RO", "Romania"), ("GR", "Greece"),
    ("TR", "Turkey"), ("UA", "Ukraine"), ("AE", "UAE"), ("SA", "Saudi Arabia"),
    ("IL", "Israel"), ("ZA", "South Africa"), ("NG", "Nigeria"),
    ("KE", "Kenya"), ("EG", "Egypt"), ("IN", "India"), ("SG", "Singapore"),
    ("JP", "Japan"), ("CN", "China"), ("HK", "Hong Kong"),
    ("KR", "South Korea"), ("MY", "Malaysia"), ("PH", "Philippines"),
    ("ID", "Indonesia"), ("TH", "Thailand"), ("VN", "Vietnam"),
    ("PK", "Pakistan"), ("AU", "Australia"), ("NZ", "New Zealand"),
    ("BR", "Brazil"), ("MX", "Mexico"), ("AR", "Argentina"),
    ("CL", "Chile"), ("CO", "Colombia"),
]

#: label -> code, for reading a stored profile back into the picker.
PLACE_LABELS = {label: code for code, label in REGION_CHOICES + COUNTRY_CHOICES}


_ANYWHERE_RE = re.compile(r"\b(anywhere|worldwide|fully remote)\b")

#: alias -> code. Dotted spellings ("u.s.a") are also stored with the dots
#: removed, because the text is split on non-word characters before it is
#: looked up — but only where what is left is still distinctive. "u.s."
#: would become "us", which occurs in ordinary prose ("join us"), so it is
#: dropped rather than made into a false positive.
_ALIAS_TO_CODE: dict[str, str] = {}
for _alias, _code in _ALIAS_PAIRS:
    _ALIAS_TO_CODE.setdefault(_alias, _code)
    _plain = _alias.replace(".", "").strip()
    if len(_plain) > 2:
        _ALIAS_TO_CODE.setdefault(_plain, _code)

#: The longest alias, in words, so the scan below knows how far to look.
_MAX_ALIAS_WORDS = max(len(a.split()) for a in _ALIAS_TO_CODE)

#: The countries the matcher knows but the first-run picker does not offer.
#: Written out rather than derived from the alias table: the aliases hold
#: cities too, and any rule for picking the country out of them ("the
#: longest", "the first") gets Serbia from Belgrade on one row and Bulgaria
#: from Sofia on the next.
_EXTRA_NAMES = {
    "BD": "Bangladesh", "BG": "Bulgaria", "BH": "Bahrain",
    "CR": "Costa Rica", "EE": "Estonia", "GH": "Ghana", "HR": "Croatia",
    "HU": "Hungary", "IS": "Iceland", "JO": "Jordan", "KW": "Kuwait",
    "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia", "MA": "Morocco",
    "OM": "Oman", "PA": "Panama", "PE": "Peru", "QA": "Qatar",
    "RS": "Serbia", "SI": "Slovenia", "SK": "Slovakia", "TW": "Taiwan",
    "UY": "Uruguay",
}

#: code -> a name a person recognises. A two-letter code is not a place
#: anyone reads at a glance, and these are what a filter menu offers.
_CODE_TO_LABEL: dict[str, str] = dict(_EXTRA_NAMES)
_CODE_TO_LABEL.update({code: label for code, label in
                       REGION_CHOICES + COUNTRY_CHOICES})


def place_label(code: str) -> str:
    """The display name for a country or region code."""
    return _CODE_TO_LABEL.get(code, code)


#: In a location field — short, not prose — a bare "US" is the country.
#: :func:`countries_in` cannot assume that, because it also reads job
#: descriptions, where "us" is a pronoun ("join us"). Normalised before the
#: scan rather than added to the alias table, so the pronoun stays safe.
_BARE_US_RE = re.compile(r"(?<![\w.])(u\.?s\.?a?|usa)(?![\w.])", re.I)


#: US state abbreviations, read *only* out of a location field and only when
#: nothing else in it named a country. Two uppercase letters are far too
#: common to put in the alias table — "MS", "IN", "OR", "OK" and "ME" are all
#: states and all ordinary words — but "Arlington, VA" and "Fort Liberty, NC"
#: are how half the corpus writes an address, and unplaceable is now a
#: verdict rather than a shrug.
#:
#: "CA" is the live one: California to a US board, Canada to a Canadian one.
#: It stays, because this whole pass only runs when the field named no
#: country at all — "Toronto, Canada" resolves before it is reached.
_STATE_ABBR = re.compile(
    r"(?<![A-Za-z])("
    r"AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|"
    r"MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|"
    r"WA|WV|WI|WY|DC"
    r")(?![A-Za-z])")


def countries_in_place(location: str) -> set[str]:
    """:func:`countries_in`, for a location field rather than for prose."""
    text = _BARE_US_RE.sub("united states", location or "")
    found = countries_in(text)
    if found:
        return found
    # Nothing named a country. Before giving up, read the line the way a
    # person would: a two-letter state code beside a town is an address in
    # the United States.
    if _STATE_ABBR.search(text):
        return {"US"}
    return found


def countries_in(text: str) -> set[str]:
    """Canonical country/region codes named in a string.

    A word scan against a dict, not a regex. The previous version built one
    alternation of every alias and ran ``finditer`` over it, which was itself
    a fix for something worse — ~150 dynamically compiled patterns per role
    per thread, which corrupted ``re``'s module-level cache and killed the
    process with a native access violation.

    The alternation was fine at 40 aliases. At 244 — every country and the
    major cities, which is what makes the location step work — it brought
    back the same class of failure: running the scorer on fourteen threads
    produced segfaults, ``SystemError: setobject.c: bad argument to internal
    function``, and a ``TypeError`` insisting that ``any`` was a list
    iterator, about one run in five. Merely importing the module was enough.

    So: no pattern at all. Split once on the precompiled word splitter, then
    walk the words looking up runs of up to :data:`_MAX_ALIAS_WORDS`,
    longest first so "south africa" wins over "africa" and "new zealand"
    over "zealand". Dict lookups, no backtracking, nothing to compile.
    """
    words = [w for w in _WORD_SPLIT_RE.split((text or "").lower()) if w]
    found: set[str] = set()
    i, n = 0, len(words)
    while i < n:
        for size in range(min(_MAX_ALIAS_WORDS, n - i), 0, -1):
            run = words[i:i + size]
            code = _ALIAS_TO_CODE.get(" ".join(run))
            if not code and size > 1 and all(len(w) <= 2 for w in run):
                # "U.S.A" arrives here as three one-letter words, because the
                # dots it was written with are what the splitter split on.
                code = _ALIAS_TO_CODE.get("".join(run))
            if code:
                found.add(code)
                i += size
                break
        else:
            i += 1
    return found


#: Returned for a code that names no region, so the lookup below never
#: allocates. Shared and immutable, which matters more than it looks: see
#: the note in :func:`_compatible`.
_NO_CODES: frozenset[str] = frozenset()


def countries_within(code: str) -> frozenset[str]:
    """The country codes a region covers, empty for anything that is not one.

    "Worldwide" is deliberately not a region here: it covers everywhere, so
    there is nothing for it to narrow.
    """
    return _WITHIN.get(code, _NO_CODES)


def narrowed_places(codes: set[str]) -> set[str]:
    """A candidate's places, with each region cut down to countries they named.

    "APAC, and Pakistan" is not a request for all of APAC. The country list
    is offered underneath the region as a narrowing of it, so reading the two
    as a union answered a question nobody asked: someone who picked Pakistan
    got Singapore, India and the Philippines at the top of the feed, which is
    the region they had just narrowed, back again.

    Only the regions that were actually narrowed are dropped. "APAC, Europe,
    and Germany" still means the whole of APAC — there is no country named
    inside it — plus Germany out of Europe.

    Job places never come through here. A posting open to EMEA that also
    names Berlin is open to both, and that is a union.
    """
    named = {c for c in codes if c not in _WITHIN}
    if not named:
        return codes
    return {c for c in codes
            if c not in _WITHIN or not (_WITHIN[c] & named)}


def _compatible(job: set[str], user: set[str]) -> bool:
    """Whether a posting's places and a candidate's places can be the same place.

    Symmetric, and it has to be. "A posting open to EMEA suits someone in
    Dublin" was the only direction handled, so picking a cluster — which is
    what the first-run location step offers — matched nothing at all: every
    real vacancy names a city or a country, never "EMEA", so a candidate who
    said EMEA was compared against Germany and told no.

    Written as set intersections over explicit loops rather than the obvious
    ``any(u in _WITHIN.get(j, set()) for j in job for u in user)``. That
    version was correct and it crashed the interpreter: run from fourteen
    scanner threads it produced ``SystemError: setobject.c: bad argument to
    internal function``, a ``TypeError`` claiming ``any`` was a list
    iterator, and bare segfaults — roughly one run in three, always only
    when a region was involved, because an exact country match returns on
    the line above and never reaches it. Nested generator expressions whose
    ``in`` operand is a freshly-built set appear to defeat CPython 3.12's
    adaptive specialisation when several threads share the code object.
    Intersections against a stable frozenset do the same work, faster, and
    have survived every run since.
    """
    if job & user:
        return True
    for code in job:
        if _WITHIN.get(code, _NO_CODES) & user:
            return True
    for code in user:
        if _WITHIN.get(code, _NO_CODES) & job:
            return True
    return False


#: How a vacancy describes where the work happens. Lives here rather than
#: in the UI because it is now a scoring input, not a badge on a drawer.
_REMOTE_RE = re.compile(r"\bremote\b|\bwork from home\b|\bwfh\b", re.I)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)

#: The four answers to "where are you willing to work". "any" is a real
#: answer and the default: plenty of people will take whatever is good.
WORKPLACES = ["any", "remote", "hybrid", "onsite"]
WORKPLACE_WORDS = {
    "any": "Anywhere: remote, hybrid or on site",
    "remote": "Remote only",
    "hybrid": "Hybrid",
    "onsite": "On site",
}


def workplace_of(location: str = "", title: str = "", description: str = "") -> str:
    """Whether a vacancy is Remote, Hybrid or On site, from how it reads."""
    hay = f"{location} {title} {(description or '')[:1500]}"
    if _HYBRID_RE.search(hay):
        return "Hybrid"
    if _REMOTE_RE.search(hay):
        return "Remote"
    return "On site"


def wanted(profile: Profile) -> set[str]:
    """Every way of working this person said yes to.

    More than one is allowed, and it is the common case: plenty of people
    will take remote or hybrid and not an office five days a week, which the
    single answer could not say. Stored comma-separated on the profile's one
    ``workplace`` field rather than in a new one, so profiles written by
    older versions — and the Settings and QA code that reads them — keep
    working unchanged.

    "Any" is exclusive: it already means all of them.
    """
    raw = (getattr(profile, "workplace", "") or "").strip().lower()
    picks = {p for p in _WORD_SPLIT_RE.split(raw) if p in WORKPLACES}
    if not picks:
        # Nothing chosen: fall back to the flag the CV reader used to set.
        return {"remote" if getattr(profile, "remote_ok", True) else "onsite"}
    return {"any"} if "any" in picks else picks


def wants(profile: Profile) -> str:
    """One representative answer, for copy and for callers that need a word."""
    picks = wanted(profile)
    return next((key for key in WORKPLACES if key in picks), "any")


def workplace_fit(role_location: str, profile: Profile, title: str = "",
                  description: str = "") -> int:
    """How well the way this job is worked matches what was asked for.

    Deliberately small either way. It is a preference, not a hard filter:
    somebody who wants remote should see remote first, not be prevented
    from ever seeing the perfect on-site job round the corner. The All jobs
    Workplace filter is there when they do want it absolute.
    """
    picks = wanted(profile)
    if "any" in picks:
        return 0
    got = workplace_of(role_location, title, description).lower().replace(" ", "")
    return 2 if got in picks else -2


def _named_places(targets: list[str]) -> list[str]:
    """The answers that name a place, as opposed to a way of working.

    "Remote" is in ``target_locations`` on plenty of older profiles and is
    not a place, so a profile holding only that has still said nothing about
    where.
    """
    return [t for t in targets if t and t not in MODALITY]


#: Words a location field uses that are *about* the job rather than about
#: where it is. Whatever is left once these are gone is a place name.
_NOT_A_PLACE = {
    "remote", "hybrid", "onsite", "on", "site", "office", "based", "in",
    "or", "and", "the", "at", "from", "work", "home", "wfh", "anywhere",
    "worldwide", "global", "globally", "flexible", "full", "part", "time",
    "only", "preferred", "optional", "eligible", "multiple", "locations",
    "location", "various", "any", "all", "other", "new", "n", "a", "of",
    "to", "hq", "headquarters", "campus", "onsite/hybrid", "distributed",
    "virtual", "telecommute", "travel", "required", "open",
}


def _names_a_place(loc: str) -> bool:
    """Whether a location field names somewhere, as opposed to a way to work.

    "Remote" names nowhere. "Remote - Des Moines" names Des Moines, even to
    a reader who has never heard of it. The difference decides whether an
    unresolvable field is an unknown country or a known wrong one.
    """
    for word in _WORD_SPLIT_RE.split(loc.lower()):
        if len(word) > 1 and not word.isdigit() and word not in _NOT_A_PLACE:
            return True
    return False


def _open_to_anywhere(loc: str, countries: set[str] | None = None) -> bool:
    """Whether a location field means "we do not mind where you are".

    The words alone were enough, and they are not: "US Fully Remote" says
    *fully remote* and says *US*, and it is the US that decides whether
    somebody in Riyadh can take it. Every one of these phrases is a claim
    about having no country, so a field that names one is not making it —
    which is already how "global" was read, and now how all of them are.
    """
    if countries is None:
        countries = countries_in_place(loc)
    if countries:
        return False
    return bool(_ANYWHERE_RE.search(loc)) or "global" in loc


def location_fit(location: str, profile: Profile) -> int:
    """Can this person take this job? Negative means almost certainly not.

    Location is the commonest reason a well-matched role is useless, so it is
    scored explicitly rather than folded into a keyword bag.
    """
    loc = (location or "").strip().lower()
    if not loc:
        return 0

    targets = [t.lower().strip() for t in (profile.target_locations or [])]
    # "Worldwide" is an answer, not a place, and it has to be read before
    # anything else: the country of the job is simply not a question any
    # more. It was being treated as a place that names no country, which
    # left `profile.country` as the only thing in `user_countries` — so
    # picking Worldwide in Karachi scored every role outside Pakistan at -3
    # and the feed came back as a handful of countries. Below the cut it
    # looked like a scan that had found nothing, which is the opposite of
    # what the user asked for.
    #
    # Two different answers wear the same chip, and only one of them is a
    # country filter. "Worldwide, any arrangement" means *I will work
    # anywhere*, so every job qualifies. "Worldwide, remote only" means
    # *remote from where I am* — and a post tied to a country is tied to
    # living there, which is the one thing that answer rules out. The picker
    # already promises exactly this ("roles open worldwide, not ones tied to
    # a country you are not in") and the scorer was ignoring the promise.
    if ANYWHERE_WORDS.intersection(targets):
        if wanted(profile) != {"remote"}:
            return 2
        return 2 if _open_to_anywhere(loc) else (
            -3 if countries_in_place(loc) else 1)

    # Where someone *wants* to work is a question with one source: the answer
    # they gave. Their CV's address used to stand in for it — first added to
    # whatever they chose, which silently widened it, and then as a fallback
    # when they chose nothing. Both are the same mistake. A CV says where you
    # live; plenty of people are reading it precisely because they want to
    # live somewhere else, and nobody can correct a filter they were never
    # shown. Nothing stated means location does not rank at all, which is
    # honest: the app has not been told, so it does not pretend to know.
    user_countries = narrowed_places(countries_in(" ".join(targets)))
    if not user_countries and not _named_places(targets):
        return 0
    # The place-field reader, not the prose one: a vacancy written "Remote -
    # US" names a country, and the plain scan deliberately will not read a
    # bare "us" because it also reads descriptions, where it is a pronoun.
    # So every US-remote job looked like a job with no country in it and
    # scored +1 as "somewhere unplaceable, and remote" — which put American
    # roles near the top of a feed for someone who asked for APAC.
    job_countries = countries_in_place(loc)

    if job_countries and _compatible(job_countries, user_countries):
        return 3
    # A city or region they named that isn't a country, e.g. "San Francisco".
    cities = [t for t in _named_places(targets) if not countries_in(t)]
    if any(c and c in loc for c in cities):
        return 3
    if _open_to_anywhere(loc, job_countries):
        return 2

    if job_countries:
        return -3  # names somewhere they can't work

    # Nothing resolved. That is two different situations and they were being
    # scored as one.
    #
    # "Remote" on its own names no country, and a remote role might be open
    # to anyone — that is worth a point to someone who has not ruled remote
    # out. But "Des Moines", "Santa Clara, California", "Medellín",
    # "Bioggio" name a place; the resolver simply does not know which one.
    # Scoring those as merely unknown, at -1, is what put California at the
    # top of a feed that said "Showing roles in Pakistan": a matching title,
    # the right seniority and two skills come to 12, and -1 does not touch
    # it. A list of city names is never going to be complete, so the rule
    # has to be the posture instead — somebody who named their countries is
    # telling the app to leave out what is not in them, including the places
    # it cannot pin down.
    if _names_a_place(loc):
        return -3
    return 1 if (wanted(profile) != {"onsite"} and "remote" in loc) else -1


def score_role(role: Role, profile: Profile) -> tuple[int, list[str]]:
    """Score a vacancy for this person and explain why.

    The reasons are shown in the UI. A score with no explanation is not
    something a user can trust or correct.
    """
    title = role.title or ""
    if NOISE_RE.search(title):
        return 0, []

    reasons: list[str] = []
    score = 0
    title_matched = False

    # 1. Does the title look like one of their targets?
    tl = title.lower()
    for target in profile.target_titles or []:
        t = target.lower().strip()
        if not t:
            continue
        if t in tl:
            score += 6
            title_matched = True
            reasons.append(f"matches your target role '{target}'")
            break
        # partial: most significant words of the target appear, as whole
        # words. Substring matching made "Senior Software Engineer - Core
        # Databases" a near-match for "Senior Data Analyst", because
        # "data" is inside "Databases".
        words = [w for w in _WORD_SPLIT_RE.split(t) if len(w) > 3]
        # An explicit loop, not ``sum(bool(word_re(w).search(tl)) for w in
        # words)``. That line read better and killed the rescore: a
        # generator expression calling a module-level function, shared by
        # fourteen scoring threads, is the shape that defeats CPython
        # 3.12's adaptive specialisation here — and it surfaces as
        # ``TypeError: 'list_iterator' object is not callable`` pointing at
        # the call, as though `word_re` itself had been replaced. The same
        # rewrite is what fixed `_compatible`; see the note there.
        present = 0
        for w in words:
            if word_re(w).search(tl):
                present += 1
        if words and present >= max(2, len(words) - 1):
            score += 4
            title_matched = True
            reasons.append(f"close to '{target}'")
            break

    # 2. Seniority alignment.
    want, got = _level_index(profile.seniority), _title_level(title)
    gap = got - want
    if gap == 0:
        score += 3
        reasons.append("right seniority")
    elif gap == 1:
        score += 2
        reasons.append("a step up")
    elif gap == -1:
        score += 1
    else:
        score -= 3
        reasons.append("seniority mismatch")

    # 3. Skills and domains mentioned in the title/department.
    #
    # On word boundaries, not as substrings. "Git" was matching the "git"
    # inside "Digital", so half the sales roles on the board came back
    # reading "mentions Git" and scored a point for it.
    hay = f"{title} {role.department}".lower()
    hits = [s for s in (profile.skills + profile.domains)
            if s and word_re(s.lower()).search(hay)]
    if hits:
        score += min(3, len(hits))
        reasons.append("mentions " + ", ".join(hits[:3]))

    # 4. Can they take it? A job that names a country they did not ask for
    # is not a weak match, it is not a match — so it is dropped here rather
    # than docked three points and left to float up on the strength of its
    # title. Picking Saudi Arabia and being shown "Remote - US Only" at the
    # top of the feed is the whole complaint: -3 still cleared the bar once
    # the title, seniority and skills had paid for it.
    #
    # Only -3 — "names somewhere they cannot work". A job whose location
    # cannot be placed at all scores -1 and stays: unknown is not the same
    # as wrong, and a role open worldwide or to anywhere remote never
    # reaches either, so a stated country never costs the user those.
    geo = location_fit(role.location, profile)
    if geo <= -3:
        return 0, []
    score += geo
    if geo >= 2:
        reasons.append("in your locations")
    elif geo < 0:
        reasons.append("may be outside where you can work")

    # 5. And is it worked the way they asked for? Remote used to be
    # assumed — in the corpus, in the scoring and in the copy — with no
    # control anywhere to say otherwise. Now it is answered, and the
    # answer shows up in the reasons so the feed can be argued with.
    place = workplace_fit(role.location, profile, title, role.description)
    if place:
        score += place
        kind = workplace_of(role.location, title, role.description)
        reasons.append(kind.lower() + ", as you asked" if place > 0
                       else f"{kind.lower()}, and you asked for "
                            f"{wants(profile)}")

    # Nothing about the title resembles what they are looking for. Seniority
    # and location alone were enough to reach 6 — and "senior, in the UK"
    # describes an enormous share of every board, so the feed filled with
    # "Senior Backend Engineer" and "Content Executive" for an operations
    # analyst. A vacancy that does not look like the work is not a match,
    # however well situated it is.
    if not title_matched:
        score = min(score, NO_TITLE_CEILING)

    return max(0, score), reasons


def match_percent(role: Role, profile: Profile) -> int:
    """A 0-100 match figure for display, spread across real signals.

    Separate from the internal ``score`` (which gates what to show): this is
    the number on the card, and it has to *vary*, because a column of
    identical percentages tells the reader nothing and looks broken.

    It used to look broken for a good reason. Title relevance was the
    fraction of target words present, capped at 0.82; seniority was one of
    four fixed values; skills were matched against title + department +
    description, and boards publish no description, so that term was almost
    always zero; and location was one of six. A role with two of three
    target words, the right seniority and a workable location therefore
    scored 0.82*2/3*42 + 20 + 0 + 15 = 57.96 — and so did every other role
    in that bucket. Whole screens of 58%.

    So title similarity is continuous now: the harmonic mean of how much of
    the target the title covers and how much of the title the target
    explains. That separates "Senior Data Analyst" from "Senior Data
    Analyst, Investor" from "Senior Cloud Operations Engineer", which are
    genuinely three different degrees of fit.
    """
    title = (role.title or "").lower()
    title_words = {w for w in _WORD_SPLIT_RE.split(title) if len(w) > 3}

    # title relevance — up to 46, continuous
    best = 0.0
    for t in profile.target_titles or []:
        t = t.lower().strip()
        if not t:
            continue
        want = {w for w in _WORD_SPLIT_RE.split(t) if len(w) > 3}
        if not want:
            continue
        if t in title:
            # Exact, but a title carrying a lot else is a looser fit than a
            # title that is only the thing you asked for.
            extra = len(title_words - want)
            best = max(best, 1.0 - min(0.18, 0.045 * extra))
            continue
        shared = want & title_words
        if not shared:
            continue
        recall = len(shared) / len(want)           # how much of the target
        precision = len(shared) / max(1, len(title_words))  # how much of the title
        f1 = 2 * recall * precision / (recall + precision)
        best = max(best, 0.9 * f1)
    pct = 46 * best

    # seniority alignment — up to 18
    gap = abs(_title_level(role.title) - _level_index(profile.seniority))
    pct += max(0, 18 - gap * 8)

    # skill / domain overlap — up to 20. On word boundaries, so "Git" stops
    # matching the "git" in "Digital". Usually zero: boards give a title and
    # a department and nothing else, which is why it cannot carry the score.
    hay = f"{role.title} {role.department} {role.description}".lower()
    # A loop rather than a genexp, for the reason given in `score_role`.
    overlap = 0
    for s in (profile.skills + profile.domains):
        if s and word_re(s.lower()).search(hay):
            overlap += 1
    pct += min(20, overlap * 7)

    # location fit — up to 16
    pct += {3: 16, 2: 12, 1: 8, 0: 6, -1: 3, -3: 0}.get(
        location_fit(role.location, profile), 5)

    return int(max(12, min(99, round(pct))))


def find_roles(profile: Profile, *, limit_boards: int = 1200, workers: int = 14,
               min_score: int = 8, include_global: bool = True,
               on_progress: Callable[[str], None] | None = None,
               on_match: Callable[[list[Role]], None] | None = None,
               should_stop: Callable[[], bool] | None = None) -> list[Role]:
    """Find roles worth this person's time, best first.

    Three tiers, merged and de-duplicated:
      1. Live global job-board aggregators (the latest postings worldwide).
      2. Company careers pages identified from those postings' apply links.
      3. The shipped corpus of company ATS boards.

    ``on_match`` is handed each batch as it is accepted, rather than only the
    whole list at the end. A pass over 1,200 boards takes minutes, and the
    feed said "matches appear as they are found" above an empty page while
    the status bar counted fourteen of them — the count came from the
    progress text and the roles themselves were held back until the pass
    returned. It is called from the thread that called ``find_roles``, never
    from a scanner thread.
    """
    out: list[Role] = []

    def keep(role: Role) -> None:
        s, why = score_role(role, profile)
        if s >= min_score:
            role.score, role.reasons = s, why
            role.match = match_percent(role, profile)
            out.append(role)
            if on_match:
                on_match([role])

    # --- tier 1 + 2: live global feeds and the company pages behind them -----
    harvested_boards: list[tuple[str, str]] = []
    if include_global:
        from .sources import (fetch_global_postings, harvest_boards,
                              read_harvested)
        if on_progress:
            on_progress("Fetching the latest jobs from global boards…")
        postings = fetch_global_postings(on_progress=on_progress, should_stop=should_stop)
        for p in postings:
            keep(Role(company=p["company"], title=p["title"], location=p["location"],
                      url=p["url"], department=p.get("department", ""),
                      description=p.get("description", "")))
        if on_progress:
            on_progress(f"{len(postings)} global postings · {len(out)} relevant so far")

        if not (should_stop and should_stop()):
            known = {(c["ats"], c["token"]) for c in boards()}
            harvested_boards = harvest_boards(postings, known)
            if harvested_boards:
                added = persist_boards(harvested_boards)  # grow the corpus
                if on_progress:
                    on_progress(f"Found {len(harvested_boards)} company pages "
                                f"({added} new), reading them…")
                for p in read_harvested(harvested_boards, workers=workers,
                                        on_progress=on_progress, should_stop=should_stop):
                    keep(Role(company=p["company"], title=p["title"], location=p["location"],
                              url=p["url"], department=p.get("department", "")))

    # --- tier 3: the shipped corpus -----------------------------------------
    todo = scan_slice(limit_boards)
    done = 0

    def work(rec: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
        """Fetch only. The scoring happens on the thread collecting these.

        It used to score here, inside the pool, which put fourteen threads
        through the scorer at once — and that is what made a scan able to
        kill the process. Measured on this machine, a 150,000-role scoring
        loop on fourteen threads failed three runs in ten: a segfault, a
        ``SystemError`` out of CPython's set code, and a ``TypeError``
        claiming ``any`` was a list iterator. The same loop on four threads
        failed none of eight, and on one thread none at all.

        Nothing is lost by moving it. The pool exists to overlap *network*
        reads of fifteen thousand careers pages, which is the slow part;
        scoring is CPU-bound Python and the GIL serialises it anyway. This
        is also how ``jobsdb.refresh`` has always worked — it reads boards
        on the pool and writes from the collecting thread.
        """
        from . import throttle
        throttle.wait_while_filling(should_stop)
        return rec, read_board(rec["ats"], rec["token"])

    def score_board(rec: dict[str, Any], jobs: list[dict[str, str]]) -> list[Role]:
        found = []
        for j in jobs:
            role = Role(company=rec["name"], title=j["title"], location=j["location"],
                        url=j["url"], department=j["department"],
                        ats=rec["ats"], token=rec["token"])
            s, why = score_role(role, profile)
            if s >= min_score:
                role.score, role.reasons = s, why
                role.match = match_percent(role, profile)
                found.append(role)
        return found

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Submitted one at a time with the stop flag checked between, rather
        # than in a comprehension. Queueing all ~160 up front means a cancel —
        # or a window closed mid-scan — still has the whole corpus to drain,
        # and submitting into a pool the interpreter is tearing down raises
        # "cannot schedule new futures after interpreter shutdown".
        futures = []
        for rec in todo:
            if should_stop and should_stop():
                break
            try:
                futures.append(pool.submit(work, rec))
            except RuntimeError:        # the interpreter is going away
                break
        for fut in as_completed(futures):
            done += 1
            if should_stop and should_stop():
                for f in futures:
                    f.cancel()
                break
            try:
                rec, jobs = fut.result()
                found = score_board(rec, jobs) if jobs else []
            except Exception:
                found = []
            if found:
                out.extend(found)
                if on_match:
                    on_match(found)
            if on_progress and done % 40 == 0:
                on_progress(f"Read {done}/{len(todo)} careers pages · {len(out)} matches")

    # de-duplicate by apply URL (a role can surface from several tiers)
    seen: set[str] = set()
    unique: list[Role] = []
    for r in sorted(out, key=lambda r: -r.score):
        k = r.url or r.key
        if k not in seen:
            seen.add(k)
            unique.append(r)
    return unique
