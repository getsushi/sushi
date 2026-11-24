"""Features added in the Tsenta-style redo.

Kept out of the original engine modules so the clean CV→discovery→letters→
autofill core stays readable. Everything here is persisted as JSON in the
settings table, so no schema migration is needed.

- Apply settings: resume optimisation (off/honest/aggressive), auto-approve,
  review-before-submit — mirrors Tsenta's Settings → Apply settings.
- Auto-apply lanes: named role profiles with their own filters, drawing from a
  single shared daily cap (Tsenta's "lanes" + "shared daily cap").
- Tracker pipeline: the post-application Kanban stages.
- Add-your-own-link: turn a pasted job URL into a scored Role.
- CSV export of the tracker.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
from html import unescape
from dataclasses import asdict, dataclass

from . import storage
from .ats import ats_from_url, company_from_token, fetch_text, read_board
from .discovery import match_percent, score_role
from .models import Profile, Role

# --- apply settings ---------------------------------------------------------

RESUME_MODES = ["off", "honest", "aggressive"]

#: How a form gets filled.
#:
#: ``direct`` reads the fields and types what is already known — the profile,
#: the answer bank — with no model call at all. It handles the identity half
#: of a form completely and costs nothing.
#:
#: ``agent`` is browser-use: a model reasoning around the page over twenty-odd
#: steps. It copes with pages the lookup cannot, and costs about ten thousand
#: tokens *per step* — more than a typical free tier allows in a minute.
#:
#: ``auto`` does the lookup first and only calls the agent for what is left,
#: which is the combination worth having and the default.
FILL_MODES = ["direct", "auto", "agent"]
FILL_MODE_WORDS = {
    "direct": "fill what Sushi already knows, and leave the rest to you",
    "auto": "fill what Sushi knows, then let the model finish the rest",
    "agent": "let the model drive the whole form",
}

#: What each mode actually instructs, in words fit to show a user. Named here
#: rather than in the UI because the resume builder and the apply dialog both
#: have to describe the same behaviour, and they disagreed: the dialog claimed
#: an optimisation level that nothing read.
RESUME_MODE_WORDS = {
    "off": "sent as written",
    "honest": "reworded to foreground the experience this role asks for",
    "aggressive": "rewritten hard against the job description",
}


#: Explorative mode. Off, a question Sushi cannot look up is left blank and
#: handed back to you — which is honest and leaves you a form to finish. On,
#: it answers the rest with the most likely answer for *you*, from your CV
#: and your history, and then flags every one of those: outlined amber in the
#: page, listed back in the app, and deliberately not written to the answer
#: bank until you have said it is right.
EXPLORATIVE_WORDS = (
    "guess the questions it cannot look up, and flag every guess")


@dataclass
class ApplySettings:
    """What happens on the way to a submitted application.

    ``auto_approve`` and ``review_before_submit`` are kept only so settings
    saved by an earlier version still load. Neither was ever read by anything:
    one named a resume preview step that does not exist, the other duplicated
    Settings → autosubmit. Their switches have been taken out of the UI.
    """

    resume_optimization: str = "honest"   # off | honest | aggressive
    fill_mode: str = "auto"               # direct | auto | agent
    auto_cover_letter: bool = True
    explorative: bool = True              # guess the rest, and say so
    guess_default_v2: bool = True         # migration marker, see load
    auto_approve: bool = True             # unused; retained for old configs
    review_before_submit: bool = True     # unused; retained for old configs


def load_apply_settings() -> ApplySettings:
    raw = storage.get_setting("apply_settings", "")
    if raw:
        try:
            d = json.loads(raw)
            # Guessing became the default way of working: a form full of
            # blanks handed back as questions was the slow part. Settings
            # saved before that are moved over once; turning it off again
            # afterwards sticks.
            if not d.get("guess_default_v2"):
                d["explorative"] = True
                d["guess_default_v2"] = True
                storage.set_setting("apply_settings", json.dumps(d))
            return ApplySettings(**{k: v for k, v in d.items()
                                    if k in ApplySettings.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError):
            pass
    return ApplySettings()


def save_apply_settings(s: ApplySettings) -> None:
    storage.set_setting("apply_settings", json.dumps(asdict(s)))


# --- what counts as "already dealt with" ------------------------------------
#
# One definition, because there were two and they disagreed. All jobs hid
# four statuses; the Dashboard treated five as handled when bulk-queueing
# and hid none at all — so a job you had applied to stayed on the feed as
# a card with an Apply button on it, and the only way to notice was to
# press it again.

#: Applied to, or deliberately passed on. Not a choice any more: it lives
#: on the Tracker, and leaving it in a feed is how the same job goes out
#: twice.
SETTLED = {"needs_review", "submitted", "skipped", "failed"}

#: On its way. Still shown — a row that vanished the instant you pressed
#: Apply would look like the press had done nothing — but not offered
#: again.
IN_FLIGHT = {"queued", "filling"}

#: Filled, and waiting for you to look at it and press submit.
NEEDS_YOU = "needs_review"

#: What All jobs drops: everything settled except the one status that is
#: not settled at all. A filled application nobody has submitted is the
#: single state in the app where the next move is the user's, and it was
#: counted as finished — so the row left All jobs the moment Insane mode
#: filled it, and the forms waiting for a signature were visible only by
#: going looking for them on the Tracker.
#:
#: It is a second definition of "gone" and that is the thing this module
#: exists to prevent, so it lives here beside the first rather than in the
#: page: ``_settled_count`` in the window subtracts exactly this set to
#: size the sidebar badge, and the two disagreeing by six is the bug the
#: comment above is about.
#:
#: ``still_open`` keeps using ``SETTLED``: the Dashboard is a shortlist of
#: what to apply to next, and a filled application is not that.
OFF_THE_LIST = SETTLED - {NEEDS_YOU}


def application_status() -> dict[str, str]:
    """url → status for everything Sushi has a record of."""
    try:
        return storage.status_by_url()
    except Exception:                                   # noqa: BLE001
        return {}


def dealt_with() -> set[str]:
    """URLs no feed should spend a slot on: see ``OFF_THE_LIST``."""
    return {u for u, st in application_status().items() if st in OFF_THE_LIST}


def still_open(roles: list[Role],
               status: dict[str, str] | None = None) -> list[Role]:
    """The roles that are still a decision, in the order given."""
    status = application_status() if status is None else status
    return [r for r in roles if status.get(r.url, "") not in SETTLED]


# --- auto-apply lanes -------------------------------------------------------


@dataclass
class AutoApplyConfig:
    """How far Insane mode may go on its own.

    One decision: how many a day. What roles you want is already known — it
    comes off your CV and whatever you tell the assistant — and which of
    them to start on is the ordering, not a filter: best match first, down
    the feed until the cap is spent.

    Two settings have been removed from here, for the same reason both
    times. Lanes were named filters with their own keywords, locations and
    minimum score; a config that still held one kept filtering by it after
    the UI stopped offering them, invisibly, with no screen able to show it.
    ``min_score`` was the quality dial, and ended the same way — a bar the
    matcher's realistic ceiling had drifted past, so every setting on it
    selected the same roles and the choice was between three identical
    answers. A stored setting that cannot be seen, or that makes no
    difference when it can, is not a feature kept for compatibility; it is a
    trap. Loading a config drops both, and nothing reads them again.
    """

    enabled: bool = False
    daily_cap: int = 10


def load_autoapply() -> AutoApplyConfig:
    raw = storage.get_setting("autoapply", "")
    if raw:
        try:
            d = json.loads(raw)
            cfg = AutoApplyConfig(
                enabled=bool(d.get("enabled", False)),
                daily_cap=int(d.get("daily_cap", 10)),
            )
            if d.get("lanes") or "min_score" in d:
                # Rewritten on the way past, so the dead filter is gone from
                # disk too rather than waiting for the next save.
                save_autoapply(cfg)
            return cfg
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return AutoApplyConfig()


def save_autoapply(cfg: AutoApplyConfig) -> None:
    storage.set_setting("autoapply", json.dumps({
        "enabled": cfg.enabled, "daily_cap": cfg.daily_cap,
    }))


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def applied_today() -> int:
    if storage.get_setting("autoapply_day", "") != _today():
        return 0
    try:
        return int(storage.get_setting("autoapply_count", "0"))
    except ValueError:
        return 0


def note_applied(n: int = 1) -> None:
    if storage.get_setting("autoapply_day", "") != _today():
        storage.set_setting("autoapply_day", _today())
        storage.set_setting("autoapply_count", "0")
    storage.set_setting("autoapply_count", str(applied_today() + n))


def autoapply_preview(cfg: AutoApplyConfig, roles: list[Role]) -> list[Role]:
    """What Insane mode will work through now: the feed, best first.

    One rule, and it is the one the page shows you — everything you have
    not already dealt with, ordered by how well it matches, until the daily
    cap is spent. No bar: a cut-off that excluded the ninth-best match on a
    day with room for twenty was spending the cap on nothing.

    Already-settled roles are dropped rather than ranked. Without a bar
    narrowing the pool, the top of the feed is mostly jobs that have already
    been applied to, and re-applying to them is both the cap wasted and the
    same application arriving at an employer twice.
    """
    remaining = max(0, cfg.daily_cap - applied_today())
    if remaining <= 0:
        return []
    picked = still_open(roles)
    # Score first, percentage to break the tie — the same order the feed and
    # the All jobs table are already in, so the queue reads as the top of the
    # list rather than as a reshuffle of it.
    picked.sort(key=lambda r: (-r.score, -(r.match or 0)))
    return picked[:remaining]


# --- tracker pipeline -------------------------------------------------------

PIPELINE_STAGES = ["applied", "ghosted", "interviewing", "rejected", "offer"]
STAGE_LABELS = {
    "applied": "Applied", "ghosted": "Ghosted", "interviewing": "Interviewing",
    "rejected": "Rejected", "offer": "Offer",
}


def _stage_map() -> dict[str, str]:
    raw = storage.get_setting("pipeline_stages", "")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def get_stage(app_id: int, status: str) -> str:
    """Stage for the tracker. Submitted applications default to 'applied'."""
    m = _stage_map()
    if str(app_id) in m:
        return m[str(app_id)]
    return "applied" if status == "submitted" else ""


def set_stage(app_id: int, stage: str) -> None:
    if stage not in PIPELINE_STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    m = _stage_map()
    m[str(app_id)] = stage
    storage.set_setting("pipeline_stages", json.dumps(m))


def export_tracker_csv() -> str:
    """CSV of every tracked application — Tsenta's Export CSV."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["company", "title", "location", "status", "stage", "url",
                "score", "applied_at"])
    for a in storage.list_applications():
        w.writerow([a.role.company, a.role.title, a.role.location, a.status,
                    get_stage(a.id or 0, a.status), a.role.url, a.role.score,
                    a.updated_at])
    return buf.getvalue()


# --- add your own link ------------------------------------------------------

#: Tags whose contents are markup machinery, not words on the page.
_DEAD_TAGS = re.compile(
    r"<(script|style|noscript|svg|head|nav|footer)\b[^>]*>.*?</\1\s*>",
    re.I | re.S)
#: Block-level tags, turned into line breaks before the rest are dropped —
#: otherwise every bullet of a requirements list runs into the next one.
_BLOCKS = re.compile(r"</?(p|div|br|li|tr|h[1-6]|section|article)\b[^>]*>", re.I)
_TAGS = re.compile(r"<[^>]+>")
_BLANKS = re.compile(r"\n\s*\n\s*\n+")
_SPACES = re.compile(r"[ \t]{2,}")

#: Enough for any posting; past this it is the page's footer and its cookie
#: policy, which cost prompt budget and teach the model nothing.
_MAX_DESCRIPTION = 6000


def page_text(url: str, limit: int = _MAX_DESCRIPTION) -> str:
    """The readable text of a job posting.

    Manual apply offers to read *any* URL, so this cannot depend on an ATS
    having an API. It is deliberately crude — strip the machinery, keep the
    line breaks, drop the tags — because the consumer is a model reading a
    job advert, not a parser. Returns "" rather than raising: a posting that
    will not load should leave the page usable, not show an error where a
    description belongs.
    """
    try:
        html = fetch_text(url)
    except Exception:
        return ""
    text = _DEAD_TAGS.sub(" ", html)
    text = _BLOCKS.sub("\n", text)
    text = _TAGS.sub(" ", text)
    text = unescape(text)
    # &nbsp; unescapes to U+00A0, which is not whitespace to str.split or to
    # the collapse below — so a posting arrives full of words glued together
    # by a character that looks exactly like the space beside it.
    text = text.replace(" ", " ").replace("​", "")
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANKS.sub("\n\n", text).strip()[:limit]


def role_from_url(url: str, profile: Profile | None = None,
                  description: str = "") -> Role:
    """Turn a pasted job URL into a scored Role.

    If the URL points at a supported ATS board we try to pull the exact posting;
    otherwise we build a minimal Role from the URL and any pasted description so
    the user can still tailor + apply to it.
    """
    url = url.strip()
    if not url:
        raise ValueError("No URL provided.")

    company, title, location, ats, token = "", "", "", "", ""
    found = ats_from_url(url)
    if found:
        ats, token = found
        try:
            for j in read_board(ats, token):
                if j["url"].rstrip("/") == url.rstrip("/"):
                    title, location = j["title"], j["location"]
                    break
        except Exception:
            pass

    if not title:
        try:
            html = fetch_text(url)
        except Exception:
            html = ""
        # A company careers site that embeds Greenhouse (Airbnb's does):
        # the board and the job number give the exact posting, title and
        # location included, instead of whatever the page's <title> says.
        gh = _greenhouse_behind(html, url)
        if gh:
            ats, token, title, location = gh
        if not title and html:
            if m := re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S):
                title = re.sub(r"\s+", " ", m.group(1)).strip()[:160]
        if html and not company:
            company = _site_name(html)
        title, from_title = _clean_title(title)
        company = company or from_title

    # The posting itself. Without it Manual apply showed an empty panel under
    # "The posting", the match score was computed from a job title alone, and
    # the fill agent was asked to tailor to a job it had never read.
    if not description:
        description = page_text(url)

    # The board token is the company. Falling straight through to the host
    # labelled every manually-added job "job-boards.greenhouse.io", which is
    # the card, the tracker row and the cover letter's addressee.
    if not company and token:
        company = company_from_token(token)

    role = Role(company=company or _company_from_host(url), title=title or "Pasted job",
                location=location, url=url, ats=ats, token=token,
                description=description[:_MAX_DESCRIPTION])
    if profile:
        role.score, role.reasons = score_role(role, profile)
        role.match = match_percent(role, profile)
    return role


def _greenhouse_behind(html: str, url: str):
    """(ats, token, title, location) for a page embedding Greenhouse, or None."""
    from urllib.parse import parse_qs, urlparse
    m = re.search(r"greenhouse\.io/embed/job_(?:board|app)[^\"'<>\s]*?[?&]for=([\w-]+)",
                  html or "", re.I)
    if not m:
        return None
    token = m.group(1)
    page = urlparse(url)
    jid = (parse_qs(page.query).get("gh_jid") or [""])[0]
    if not jid:
        nums = re.findall(r"(?<!\d)(\d{6,})(?!\d)", page.path)
        jid = nums[-1] if nums else ""
    if not jid:
        return None
    try:
        import json as _json
        d = _json.loads(fetch_text(
            f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}"))
    except Exception:
        return None
    return ("greenhouse", token, str(d.get("title") or ""),
            str((d.get("location") or {}).get("name") or ""))


def _site_name(html: str) -> str:
    m = re.search(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)',
                  html or "", re.I)
    if not m:
        return ""
    import html as _html
    name = _html.unescape(m.group(1)).strip()
    # "Careers at Airbnb", "Airbnb Careers": the company, not the site.
    name = re.sub(r"^(?:careers|jobs) (?:at|@) ", "", name, flags=re.I)
    name = re.sub(r" (?:careers|jobs)$", "", name, flags=re.I)
    return name[:60]


#: " - Careers at Airbnb", " | Airbnb Careers", " – Jobs at Acme".
_TITLE_TAIL = re.compile(
    r"\s*[-|–—:]\s*(?:careers?|jobs?)\s+(?:at|@)\s+(?P<co1>[^|–—-]+)$"
    r"|\s*[-|–—:]\s*(?P<co2>[^|–—-]+?)\s+(?:careers?|jobs?)$", re.I)


def _clean_title(title: str) -> tuple[str, str]:
    """A page <title> as a job title, and the company it named, if any."""
    import html as _html
    t = re.sub(r"\s+", " ", _html.unescape(title or "")).strip()
    m = _TITLE_TAIL.search(t)
    if not m:
        return t, ""
    co = (m.group("co1") or m.group("co2") or "").strip()
    return t[:m.start()].strip(), co


def _company_from_host(url: str) -> str:
    """"careers.airbnb.com" -> "Airbnb": the name, not the address."""
    host = _host(url)
    parts = [p for p in host.split(".") if p not in
             ("careers", "jobs", "apply", "boards", "join", "work", "www")]
    # Two-part endings ("acme.co.uk", "acme.com.au") end in two labels.
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac", "gov"):
        parts = parts[:-1]
    core = parts[-2] if len(parts) >= 2 else (parts[0] if parts else host)
    return core.replace("-", " ").title() if core else host


def _host(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return (urlparse(url).hostname or "link").replace("www.", "")
    except Exception:
        return "link"


# --- how applications happen -------------------------------------------------
#
# Two ways to use Sushi, chosen once in Settings:
#
#   "self"  — Sushi finds and ranks the jobs; you apply. Apply opens the
#             posting in your own browser and Sushi does nothing after that.
#             No model needed, nothing typed on your behalf.
#   "sushi" — Sushi fills the forms with your own model (your API key or
#             your Claude Code login). This is what unlocks Insane mode.
#
# "sushi" without a model behaves as "self": there is nothing to fill with,
# and an Apply button that only errors is worse than one that opens the job.

APPLY_MODES = ("self", "sushi")
_APPLY_MODE_KEY = "apply_mode"


def chosen_apply_mode() -> str:
    """What the user picked. Someone who already has a model set up and has
    never chosen keeps the behaviour they had."""
    from . import storage
    from .config import load_settings
    # Unset means "sushi": the app behaves exactly as it always has until
    # someone actually picks "I apply myself".
    mode = storage.get_setting(_APPLY_MODE_KEY, "")
    return mode if mode in APPLY_MODES else "sushi"


def set_apply_mode(mode: str) -> None:
    from . import storage
    if mode in APPLY_MODES:
        storage.set_setting(_APPLY_MODE_KEY, mode)


def sushi_applies() -> bool:
    """True when Sushi itself fills forms: chosen, and a model to do it with."""
    from .config import load_settings
    return chosen_apply_mode() == "sushi" and load_settings().llm_enabled
