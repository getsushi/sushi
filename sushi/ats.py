"""Public ATS job-board APIs.

Most companies host their careers page on an applicant tracking system, and
most of those serve the board as public JSON (or, for Personio, public XML).
That means we can read a company's live vacancies without scraping HTML,
driving a browser, or holding an account anywhere.

The token is the company's board identifier, which usually appears in the apply
URL (``job-boards.greenhouse.io/<token>``), so we can harvest it for free from
job feeds rather than guessing.

Thirteen providers are read here. The first six were the original set; the rest
were added because a third of the shipped corpus — 7,955 companies — had been
probed against those six and resolved to nothing, which is what a company on
Workday or Personio looks like from here. Every endpoint below was verified
against a live tenant before it was written, and anything that could not be
verified was left out rather than guessed at.

The one awkward member is Workday. Its board is not identified by a single
slug: it needs the tenant, the data centre its tenant sits in (``wd1``…
``wd12``) and the career site's name, which is case-sensitive. Those three are
packed into one token with :data:`_JOIN`, because everything downstream — the
corpus, the scanner, the stored roles — treats a board as ``(provider,
token)`` and should not have to learn otherwise.
"""
from __future__ import annotations

import json
import re
import ssl
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Callable, Iterable

UA = "Mozilla/5.0 (compatible; jobly/1.0; personal job search)"
TIMEOUT = 25

# --- one TLS context, shared ------------------------------------------------
#
# `urlopen` builds a fresh SSLContext for every HTTPS call, and building one on
# Windows loads the system certificate store. Doing that from fourteen scanner
# threads at once crashed the whole process with a native access violation
# inside OpenSSL's handshake — no Python traceback, no exception to catch, the
# window simply vanished mid-scan. The faulthandler dump caught it in the end:
# every thread was inside `ssl.do_handshake` or `enum_certificates`.
#
# One context, built once behind a lock and reused, removes the race. It is
# also just faster: the certificate store is parsed once per run instead of
# once per request.

_ssl_lock = threading.Lock()
_ssl_context: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """The process-wide TLS context. Built on first use, never rebuilt."""
    global _ssl_context
    if _ssl_context is None:
        with _ssl_lock:
            if _ssl_context is None:          # another thread may have won
                _ssl_context = ssl.create_default_context()
    return _ssl_context


def _open(req: urllib.request.Request, timeout: int):
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())


def fetch_json(url: str, *, timeout: int = TIMEOUT, data: Any = None) -> Any:
    """GET, or POST when ``data`` is given — Workday's board API is a POST."""
    headers = {"User-Agent": UA, "Accept": "application/json"}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with _open(req, timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def fetch_text(url: str, *, timeout: int = TIMEOUT, max_bytes: int = 0) -> str:
    """``max_bytes`` caps the read — a careers page can be megabytes of app."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with _open(req, timeout) as resp:
        raw = resp.read(max_bytes) if max_bytes else resp.read()
        return raw.decode("utf-8", "replace")


# --- token extraction -------------------------------------------------------

# Each pattern pulls the board token out of an apply URL. Ordered most common
# first; the first match wins.
_TOKEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([a-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board\?for=([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([a-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.-]+)", re.I)),
    ("workable", re.compile(r"(?:apply\.workable\.com|([a-z0-9-]+)\.workable\.com)/(?:j/)?([a-z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([a-z0-9_-]+)", re.I)),
    ("recruitee", re.compile(r"([a-z0-9-]+)\.recruitee\.com", re.I)),
    # Workday, both of its URL shapes. On myworkdayjobs the tenant is the
    # subdomain; on myworkdaysite the host is shared per data centre and the
    # tenant moves into the path. `wday/cxs/<tenant>/` is the API URL itself,
    # which turns up in harvested links; the locale segment is optional and
    # comes in every language ("en-US", "de-DE", "fr-CA").
    ("workday", re.compile(
        r"(?P<tenant>[a-z0-9][a-z0-9-]*)\.(?P<dc>wd\d+)\.myworkdayjobs\.com/"
        r"(?:wday/cxs/[^/]+/)?(?:[a-z]{2}-[a-z]{2}/)?(?P<site>[a-z0-9_-]+)", re.I)),
    ("workday", re.compile(
        r"(?P<dc>wd\d+)\.myworkdaysite\.com/"
        r"(?:[a-z]{2}-[a-z]{2}/)?(?:wday/cxs|recruiting)/"
        r"(?P<tenant>[a-z0-9_.-]+)/(?P<site>[a-z0-9_-]+)", re.I)),
    ("personio", re.compile(r"([a-z0-9-]+)\.jobs\.personio\.(?:de|com)", re.I)),
    ("teamtailor", re.compile(r"([a-z0-9-]+)\.teamtailor\.com", re.I)),
    ("bamboohr", re.compile(r"([a-z0-9-]+)\.bamboohr\.com", re.I)),
    ("breezy", re.compile(r"([a-z0-9-]+)\.breezy\.hr", re.I)),
    ("pinpoint", re.compile(r"([a-z0-9-]+)\.pinpointhq\.com", re.I)),
    ("rippling", re.compile(r"ats\.rippling\.com/([a-z0-9-]+)", re.I)),
]

# Tokens that are really URL path segments, not companies.
_TOKEN_STOPWORDS = {
    "embed", "job_board", "jobs", "j", "apply", "careers", "api", "www",
    "search", "board", "post", "share", "o", "en", "job", "recruiting",
    "wday", "cxs", "en-us",
}

#: Providers whose board needs more than one value to address it. The parts
#: are named groups in the pattern, joined in this order with :data:`_JOIN`
#: and split again by the reader — named rather than positional because the
#: two Workday URL shapes capture the same three values in different orders.
_JOINED = {"workday": ("tenant", "dc", "site")}
_JOIN = "|"

#: The stopword list above is wrong for a Workday site name: "Careers" and
#: "Jobs" are what tenants actually call their career sites, and rejecting
#: them threw away real boards. Only the API's own path words are junk here.
_SITE_STOPWORDS = {"wday", "cxs", "job", "recruiting"}

#: Providers whose token must keep the case it was written in. Workday career
#: sites are named things like ``NVIDIAExternalCareerSite`` and the API 404s
#: on the lower-cased spelling; every other provider here is a slug.
_CASE_SENSITIVE = {"workday"}


def ats_from_url(url: str) -> tuple[str, str] | None:
    """Return ``(provider, token)`` parsed out of an apply URL, if recognisable."""
    if not url:
        return None
    for provider, pattern in _TOKEN_PATTERNS:
        m = pattern.search(url)
        if not m:
            continue
        if provider in _JOINED:
            parts = [(m.groupdict().get(name) or "").strip()
                     for name in _JOINED[provider]]
            # Every part has to be there: a half-addressed board is not a
            # board, and storing one would cost a failing request per scan.
            if not all(parts) or parts[-1].lower() in _SITE_STOPWORDS:
                continue
            token = _JOIN.join(parts)
            return provider, token if provider in _CASE_SENSITIVE else token.lower()
        token = next((g for g in m.groups() if g), "").strip().lower()
        if token and token not in _TOKEN_STOPWORDS and len(token) > 1:
            return provider, token
    return None


def company_from_token(token: str) -> str:
    """The company-ish name inside a board token, for labelling a card.

    For most providers the token *is* the company slug. For Workday it is
    three values joined, and the first of them is the tenant — so a harvested
    Workday board reads as "Nvidia" rather than
    "Nvidia|Wd5|Nvidiaexternalcareersite".
    """
    head = (token or "").split(_JOIN)[0]
    return head.replace("-", " ").replace("_", " ").strip().title()


def slug_candidates(name: str) -> list[str]:
    """Plausible board tokens for a company name, best guess first.

    Used only as a fallback for companies whose feed entries had no usable
    apply URL, so we keep the list short to bound the probe cost.
    """
    base = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    base = re.sub(r"\b(inc|llc|ltd|limited|gmbh|bv|corp|corporation|co|plc|sa|ag|srl|pty|the)\b", " ", base)
    words = base.split()
    if not words:
        return []
    joined = "".join(words)
    hyphen = "-".join(words)
    out = [joined, hyphen]
    if len(words) > 1:  # many boards drop a trailing "ai"/"labs"/"technologies"
        out.append("".join(words[:-1]))
    seen: set[str] = set()
    return [t for t in out if t and not (t in seen or seen.add(t)) and len(t) > 1]


# --- per-provider board readers --------------------------------------------

def _norm(title: str, location: str, url: str, dept: str = "",
          posted=None) -> dict:
    out = {
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": (url or "").strip(),
        "department": (dept or "").strip(),
    }
    when = _when(posted)
    if when:
        out["posted"] = when
    return out


def _when(value) -> float:
    """A board's posting date as Unix time, or 0.0 if there is none.

    Boards say it as ISO text ("2026-10-02T14:03:11-04:00", Greenhouse,
    Ashby, SmartRecruiters) or as epoch milliseconds (Lever's createdAt).
    """
    if value in (None, "", 0):
        return 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e11 else v
    from datetime import datetime, timezone
    text = str(value).strip().replace("Z", "+00:00")
    for cut in (None, 10):
        try:
            d = datetime.fromisoformat(text if cut is None else text[:cut])
        except ValueError:
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    return 0.0


def _greenhouse(token: str) -> list[dict[str, str]]:
    d = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    return [
        _norm(j.get("title", ""), (j.get("location") or {}).get("name", ""), j.get("absolute_url", ""),
              posted=j.get("first_published") or j.get("updated_at"))
        for j in d.get("jobs", [])
    ]


def _lever(token: str) -> list[dict[str, str]]:
    d = fetch_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    out = []
    for j in d if isinstance(d, list) else []:
        cat = j.get("categories") or {}
        out.append(_norm(j.get("text", ""), cat.get("location", ""), j.get("hostedUrl", ""), cat.get("department", ""),
                         posted=j.get("createdAt")))
    return out


def _ashby(token: str) -> list[dict[str, str]]:
    d = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    return [
        _norm(j.get("title", ""), j.get("location", ""), j.get("jobUrl", ""), j.get("department", ""),
              posted=j.get("publishedAt") or j.get("publishedDate"))
        for j in d.get("jobs", [])
    ]


def _workable(token: str) -> list[dict[str, str]]:
    d = fetch_json(f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true")
    out = []
    for j in d.get("jobs", []):
        loc = ", ".join(x for x in (j.get("city"), j.get("country")) if x)
        out.append(_norm(j.get("title", ""), loc, j.get("url", ""), j.get("department", ""),
                         posted=j.get("published_on") or j.get("created_at")))
    return out


def _smartrecruiters(token: str) -> list[dict[str, str]]:
    d = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100")
    out = []
    for j in d.get("content", []):
        loc = (j.get("location") or {})
        city = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
        url = f"https://jobs.smartrecruiters.com/{token}/{j.get('id', '')}"
        out.append(_norm(j.get("name", ""), city, url, (j.get("department") or {}).get("label", ""),
                         posted=j.get("releasedDate")))
    return out


def _recruitee(token: str) -> list[dict[str, str]]:
    d = fetch_json(f"https://{token}.recruitee.com/api/offers/")
    return [
        _norm(j.get("title", ""), j.get("location", ""), j.get("careers_url", ""), j.get("department", ""),
              posted=j.get("published_at") or j.get("created_at"))
        for j in d.get("offers", [])
    ]


#: Workday pages its board 20 at a time and some tenants hold thousands of
#: vacancies, so reading one whole is a hundred POSTs. This is the ceiling per
#: board: enough that a large employer contributes properly to the feed,
#: little enough that one tenant cannot eat a scan.
WORKDAY_LIMIT = 200
_WORKDAY_PAGE = 20


def _workday_shapes(tenant: str, dc: str, site: str) -> tuple[tuple[str, str], ...]:
    """``(api, page base)`` for each host a Workday board can live at.

    Two shapes, not one, and they are not the same URL with the host swapped:
    on ``myworkdayjobs.com`` the tenant is the subdomain, while
    ``myworkdaysite.com`` is a single host per data centre with the tenant in
    the path. Both serve the same board — PwC answers on either — so a token
    harvested from one works on the other.
    """
    return (
        (f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
         f"https://{tenant}.{dc}.myworkdayjobs.com/en-US/{site}"),
        (f"https://{dc}.myworkdaysite.com/wday/cxs/{tenant}/{site}/jobs",
         f"https://{dc}.myworkdaysite.com/en-US/recruiting/{tenant}/{site}"),
    )


def _workday(token: str) -> list[dict[str, str]]:
    """Workday, the one that runs most large employers' careers pages.

    ``token`` is ``tenant|wd5|CareerSiteName``. The board itself is a POST
    API — the same one the careers page calls — and the posting URL is the
    site URL with the returned ``externalPath`` appended.
    """
    parts = token.split(_JOIN)
    if len(parts) != 3:
        return []
    for api, base in _workday_shapes(*parts):
        out: list[dict[str, str]] = []
        offset = 0
        try:
            while offset < WORKDAY_LIMIT:
                d = fetch_json(api, data={"appliedFacets": {}, "limit": _WORKDAY_PAGE,
                                          "offset": offset, "searchText": ""})
                page = d.get("jobPostings") or []
                for j in page:
                    path = j.get("externalPath", "")
                    out.append(_norm(
                        j.get("title", ""),
                        # "6 Locations" where a posting spans several, which
                        # is honest and useless; the rest read "London, UK".
                        j.get("locationsText", ""),
                        f"{base}{path}" if path else base,
                    ))
                if len(page) < _WORKDAY_PAGE:
                    break
                offset += _WORKDAY_PAGE
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, TimeoutError, OSError):
            # The wrong host of the two, or a page that failed part way
            # through. Anything already read is still good.
            if out:
                return out
            continue
        return out
    return []


def _personio(token: str) -> list[dict[str, str]]:
    """Personio, which serves its board as XML rather than JSON.

    The feed is the same on both of its hosts; ``.de`` is the original and
    still the one every tenant answers on.
    """
    xml = fetch_text(f"https://{token}.jobs.personio.de/xml")
    root = ET.fromstring(xml)
    out = []
    for pos in root.findall("position"):
        def text(tag: str) -> str:
            node = pos.find(tag)
            return (node.text or "").strip() if node is not None else ""
        jid = text("id")
        if not jid:
            continue
        out.append(_norm(text("name"), text("office"),
                         f"https://{token}.jobs.personio.de/job/{jid}",
                         text("department")))
    return out


#: Teamtailor's own namespace in its RSS feed, which is where the fields that
#: matter live.
_TT_NS = {"tt": "https://teamtailor.com/locations"}


def _teamtailor(token: str) -> list[dict[str, str]]:
    """Teamtailor, from its RSS feed rather than its JSON one.

    The obvious endpoint — ``/jobs.json`` — is a JSON Feed with no location in
    it at all, not even in the schema.org block it carries, and a posting with
    no location loses the matcher's location points for nothing. The RSS feed
    at ``/jobs.rss`` is the same board with city, country, department and
    remote status attached, so it is worth the XML.
    """
    xml = fetch_text(f"https://{token}.teamtailor.com/jobs.rss")
    root = ET.fromstring(xml)
    out = []
    for item in root.iterfind("./channel/item"):
        def text(path: str) -> str:
            node = item.find(path, _TT_NS)
            return (node.text or "").strip() if node is not None else ""

        where = text("tt:locations/tt:location/tt:name")
        if not where:
            where = ", ".join(x for x in (
                text("tt:locations/tt:location/tt:city"),
                text("tt:locations/tt:location/tt:country")) if x)
        if not where and text("remoteStatus").lower() in ("fully", "temporary"):
            where = "Remote"
        title = text("title")
        if title:
            out.append(_norm(title, where, text("link"), text("tt:department")))
    return out


def _bamboohr(token: str) -> list[dict[str, str]]:
    """BambooHR.

    Note that ``*.bamboohr.com`` answers for every subdomain, serving the
    product's own marketing page to anything that is not a real tenant. That
    is HTML, so it fails to parse as JSON and :func:`read_board` turns it into
    "no board" — which is what makes probing by slug safe here.
    """
    d = fetch_json(f"https://{token}.bamboohr.com/careers/list")
    out = []
    for j in d.get("result", []):
        loc = j.get("atsLocation") or j.get("location") or {}
        where = ", ".join(x for x in (loc.get("city"), loc.get("state"),
                                      loc.get("country")) if x)
        if not where and j.get("isRemote"):
            where = "Remote"
        out.append(_norm(j.get("jobOpeningName", ""), where,
                         f"https://{token}.bamboohr.com/careers/{j.get('id', '')}",
                         j.get("departmentLabel", "")))
    return out


def _breezy(token: str) -> list[dict[str, str]]:
    d = fetch_json(f"https://{token}.breezy.hr/json")
    out = []
    for j in d if isinstance(d, list) else []:
        loc = j.get("location") or {}
        country = (loc.get("country") or {}).get("name", "")
        state = (loc.get("state") or {}).get("name", "")
        where = ", ".join(x for x in (loc.get("city"), state, country) if x)
        out.append(_norm(j.get("name", ""), where, j.get("url", ""),
                         (j.get("department") or {}).get("name", "")
                         if isinstance(j.get("department"), dict)
                         else j.get("department", "")))
    return out


def _pinpoint(token: str) -> list[dict[str, str]]:
    d = fetch_json(f"https://{token}.pinpointhq.com/postings.json")
    out = []
    for j in d.get("data", []):
        loc = j.get("location") or {}
        where = loc.get("name") or ", ".join(
            x for x in (loc.get("city"), loc.get("country")) if x)
        if not where and j.get("workplace_type") == "remote":
            where = "Remote"
        out.append(_norm(j.get("title", ""), where, j.get("url", "")))
    return out


def _rippling(token: str) -> list[dict[str, str]]:
    d = fetch_json(f"https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs")
    out = []
    for j in d if isinstance(d, list) else []:
        out.append(_norm(j.get("name", ""),
                         (j.get("workLocation") or {}).get("label", ""),
                         j.get("url", ""),
                         (j.get("department") or {}).get("label", "")))
    return out


READERS: dict[str, Callable[[str], list[dict[str, str]]]] = {
    "greenhouse": _greenhouse,
    "lever": _lever,
    "ashby": _ashby,
    "workable": _workable,
    "smartrecruiters": _smartrecruiters,
    "recruitee": _recruitee,
    "workday": _workday,
    "personio": _personio,
    "teamtailor": _teamtailor,
    "bamboohr": _bamboohr,
    "breezy": _breezy,
    "pinpoint": _pinpoint,
    "rippling": _rippling,
}

# Probing order for the fallback path, commonest ATS first. Every provider
# whose board can be addressed by a plain company slug is in here; Workday is
# not, because its token also needs a data centre and a site name, neither of
# which can be guessed from a company's name. Workday boards therefore only
# arrive by being harvested out of a real apply URL.
PROBE_ORDER = ["greenhouse", "lever", "ashby", "smartrecruiters", "workable",
               "recruitee", "bamboohr", "breezy", "teamtailor", "personio",
               "pinpoint", "rippling"]

#: Bumped whenever :data:`PROBE_ORDER` grows. It is stamped on every company
#: the prober has looked at, so adding a provider re-opens the ones that were
#: checked against the shorter list and came back empty — without it, the
#: 7,955 corpus companies already marked "probed, nothing found" would stay
#: that way for ever and the new providers would only ever apply to companies
#: discovered after today.
PROBE_REV = 2


class BoardUnavailable(Exception):
    """The board could not be read just now. Not the same as an empty board."""


def _transient(exc: BaseException) -> bool:
    """A failure that says nothing about whether the board has vacancies.

    A 404 or 410 is the provider saying there is no board there, and a page
    that will not parse is a provider answering for a token it does not
    have. Everything else — no network, a timeout, a refused or reset
    connection, rate limiting, a server error — is the request failing.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    return isinstance(exc, (urllib.error.URLError, TimeoutError, OSError))


def read_board(provider: str, token: str, *,
               strict: bool = False) -> list[dict[str, str]]:
    """Live vacancies for one company board. Returns [] on any failure.

    ``strict`` raises :class:`BoardUnavailable` instead when the request
    failed rather than the board being empty or absent. The job database
    needs that difference: it reads [] as "every posting on this board has
    closed" and deletes them, so a refresh run while the connection dropped
    used to empty the whole table and then leave it empty for a week.
    """
    reader = READERS.get(provider)
    if not reader:
        return []
    try:
        return reader(token)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
            ET.ParseError, UnicodeDecodeError, TimeoutError, OSError) as exc:
        if strict and _transient(exc):
            raise BoardUnavailable(f"{provider}/{token}: {exc}") from exc
        # A provider that answers for every subdomain — BambooHR serves its
        # own marketing page — lands here as a parse error, which is the
        # correct reading of it: there is no board at that token.
        return []


#: Where a company's own careers page tends to live, and the endings small
#: companies tend to use. Ordered by how often they pay off, and kept short:
#: every combination is a request, and this only runs for companies that
#: have already failed the cheap test.
_CAREERS_TLDS = (".com", ".io", ".ai", ".co")
_CAREERS_PATHS = ("/careers", "/jobs")

#: Enough of a careers page to find the links in. Some of them are several
#: megabytes of bundled application, and none of that is link.
_PAGE_CAP = 400_000


def sniff_careers(name: str, *, timeout: int = 10) -> tuple[str, str] | None:
    """Find a board by reading the company's own careers page.

    Guessing tokens only finds a board when the company's board token looks
    like its name, and plenty do not: ExtraHop's Greenhouse board is
    ``extrahopnetworks``, HighLevel's Lever board is ``gohighlevel``, Vytalize
    Health's Ashby board is ``vytalize``. No amount of slug-generating finds
    those, but every one of them is linked from the company's own careers
    page, which is a single request away.

    The most-linked board on the page wins, because a careers page links its
    own ATS many times over (every vacancy) and somebody else's at most once.
    """
    for slug in slug_candidates(name)[:1]:
        for tld in _CAREERS_TLDS:
            for path in _CAREERS_PATHS:
                try:
                    html = fetch_text(f"https://{slug}{tld}{path}",
                                      timeout=timeout, max_bytes=_PAGE_CAP)
                except (urllib.error.URLError, urllib.error.HTTPError,
                        UnicodeDecodeError, TimeoutError, OSError, ValueError):
                    continue
                hits: dict[tuple[str, str], int] = {}
                for m in re.finditer(r'https?://[^\s"\'<>]{10,160}', html):
                    if found := ats_from_url(m.group(0)):
                        hits[found] = hits.get(found, 0) + 1
                if hits:
                    return max(hits.items(), key=lambda kv: kv[1])[0]
    return None


def probe(name: str, providers: Iterable[str] = PROBE_ORDER, *,
          seed: str = "", sniff: bool = True) -> tuple[str, str, int] | None:
    """Find a company's board by trying likely tokens. ``(provider, token, n_jobs)``.

    ``seed`` is a slug from a source that already knows the company (e.g. the
    Himalayas company slug); it is tried first because it beats our guesses.

    When no guess lands and ``sniff`` is on, the company's own careers page is
    read for the answer — see :func:`sniff_careers`. That runs last because it
    is the expensive half: a handful of requests to a site that may not exist,
    against an API that answers in one.
    """
    candidates = ([seed] if seed else []) + slug_candidates(name)[:2]
    seen: set[str] = set()
    for token in [t for t in candidates if not (t in seen or seen.add(t))]:
        for provider in providers:
            jobs = read_board(provider, token)
            if jobs:
                return provider, token, len(jobs)

    if not sniff:
        return None
    found = sniff_careers(name)
    if not found:
        return None
    # Confirmed before it is kept: a careers page can link a board that has
    # been closed, moved or renamed, and a corpus entry that reads nothing
    # costs a request on every scan for ever.
    jobs = read_board(*found)
    return (found[0], found[1], len(jobs)) if jobs else None
