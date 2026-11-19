"""Company logos, fetched once and kept on disk.

A job card with the employer's actual mark is markedly easier to scan than
one with their name set in the same type as everything else — you recognise
a logo before you have read anything. Tsenta's cards do this, and it is most
of why their feed reads as a list of *companies* rather than a list of rows.

Three things make this awkward, and all three are handled here:

- **The board URL is not the company.** A Greenhouse posting lives at
  ``boards.greenhouse.io/monzo``; the mark belongs to monzo.com. So a name is
  resolved to a domain first, and the ATS token is used as a hint when the
  name is ambiguous.
- **It is a network call per company.** Everything is cached on disk,
  including the misses — a company with no findable logo must not be looked
  up again on every repaint.
- **It tells someone else what you are looking at.** Each lookup reveals one
  employer's name to the logo service. Nothing about the user is sent, and
  there is no key and no cookie. The cached logos can be cleared from the
  Files page; a company that has no findable mark keeps the lettered tile.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

from .ats import fetch_text, ssl_context
from .config import DATA_DIR

log = logging.getLogger(__name__)

LOGO_DIR = DATA_DIR / "logos"
#: name -> domain, and the misses, so a failure costs one request ever.
INDEX = LOGO_DIR / "index.json"
#: Resolves a company name to a domain. No key, no cookie, and the only thing
#: sent is the employer's name.
SUGGEST = "https://autocomplete.clearbit.com/v1/companies/suggest?query={name}"
#: The mark itself, square and transparent where the company has one.
LOGO = "https://logo.clearbit.com/{domain}?size=128"
#: When the name cannot be resolved but a domain is already known.
FAVICON = "https://icons.duckduckgo.com/ip3/{domain}.ico"

_lock = threading.Lock()
_index: dict[str, str] | None = None

#: Suffixes that stop a name matching its own domain.
_NOISE = re.compile(
    r"\b(inc|llc|ltd|limited|plc|gmbh|corp|corporation|co|company|group|"
    r"holdings|technologies|technology|labs|software|solutions)\b\.?",
    re.I)
_PUNCT = re.compile(r"[^a-z0-9 ]+")


# Logos are always looked up. The switch that turned them off is gone: it
# was a setting about how the app looks, worded as a privacy decision, and
# the only thing it bought was a lettered tile. Clearing the cache is still
# on the Files page, which is the part that was actually worth keeping.


def normalise(name: str) -> str:
    """The key a company is cached under. Two spellings must agree."""
    n = _PUNCT.sub(" ", (name or "").lower())
    n = _NOISE.sub(" ", n)
    return " ".join(n.split())


def _load_index() -> dict[str, str]:
    global _index
    if _index is None:
        try:
            _index = json.loads(INDEX.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _index = {}
    return _index


def _save_index() -> None:
    try:
        LOGO_DIR.mkdir(parents=True, exist_ok=True)
        INDEX.write_text(json.dumps(_load_index()), encoding="utf-8")
    except OSError:
        pass


def cached_path(company: str) -> Path | None:
    """The logo file for *company*, if one has already been fetched."""
    key = normalise(company)
    if not key:
        return None
    path = LOGO_DIR / f"{key.replace(' ', '-')}.png"
    return path if path.exists() and path.stat().st_size > 64 else None


def known_miss(company: str) -> bool:
    """Whether this company has already been looked up and come back empty."""
    return _load_index().get(normalise(company)) == ""


def resolve_domain(company: str, hint: str = "") -> str:
    """A domain for *company*, from the cache or the suggest endpoint."""
    key = normalise(company)
    if not key:
        return ""
    index = _load_index()
    if key in index:
        return index[key]

    domain = ""
    try:
        raw = fetch_text(SUGGEST.format(name=key.replace(" ", "%20")), timeout=8)
        for item in json.loads(raw) or []:
            name = normalise(item.get("name", ""))
            dom = (item.get("domain") or "").strip().lower()
            if not dom:
                continue
            # Prefer an exact name match, then the ATS token appearing in the
            # domain ("monzo" in "monzo.com"), then simply the first result —
            # which the endpoint already ranks by relevance.
            if name == key:
                domain = dom
                break
            if hint and hint.lower() in dom and not domain:
                domain = dom
            elif not domain:
                domain = dom
    except Exception as exc:
        log.debug("logo: could not resolve %s (%s)", company, exc)
        return ""

    with _lock:
        index[key] = domain
        _save_index()
    return domain


def _download(url: str, dest: Path) -> bool:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Sushi/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10,
                                    context=ssl_context()) as resp:
            if resp.status != 200:
                return False
            data = resp.read(400_000)
    except Exception:
        return False
    # A 1x1 tracking pixel or an error page is not a logo.
    if len(data) < 128:
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    except OSError:
        return False
    return True


def fetch(company: str, hint: str = "") -> Path | None:
    """Get this company's logo, from disk if possible. Blocking — use a worker.

    Returns ``None`` for anything it cannot find, and remembers that so the
    same company is never looked up twice.
    """
    existing = cached_path(company)
    if existing:
        return existing
    if known_miss(company):
        return None

    key = normalise(company)
    if not key:
        return None
    domain = resolve_domain(company, hint)
    if not domain:
        return None

    dest = LOGO_DIR / f"{key.replace(' ', '-')}.png"
    for url in (LOGO.format(domain=domain), FAVICON.format(domain=domain)):
        if _download(url, dest):
            log.debug("logo: %s -> %s", company, domain)
            return dest

    with _lock:
        _load_index()[key] = ""      # a miss, remembered
        _save_index()
    return None


def forget_all() -> None:
    """Drop every cached logo. For Settings, and for a bad run."""
    global _index
    _index = None
    try:
        for p in LOGO_DIR.glob("*.png"):
            p.unlink()
        INDEX.unlink(missing_ok=True)
    except OSError:
        pass
