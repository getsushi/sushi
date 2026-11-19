"""The answer bank that teaches itself.

Jobly's trick was that the second application was easier than the first: it
filled the form, you corrected it, and it remembered. The weak part was the
"learn from this form" button — it only learned when you remembered to press
it, which is exactly when you are least likely to, because you have just
finished a form and want to be done.

So nothing is pressed here. While the application browser is open, a watcher
reads the form every couple of seconds and keeps the answer bank up to date
with whatever is currently in the fields. The user corrects a wrong answer,
and the correction is learned on the next poll — including the ones they
typed themselves into a form Sushi never filled.

What makes a stored answer trustworthy
--------------------------------------
Two things, and they are tracked separately from the answer itself:

- **Source.** A value the agent typed is a guess; a value sitting in the field
  when the human was last looking at it has been, at minimum, tolerated. Human
  values overwrite agent values, never the other way round.
- **Stability.** A value is only learned once it has survived one poll
  unchanged, so half-typed text ("Londo") never lands in the bank.

What is deliberately not learned
--------------------------------
Passwords and anything in a password field; one-time codes; and EEO and
demographic questions (race, gender, disability, veteran status). The last of
those are answers the user really did give, but silently building a durable
local record of someone's protected characteristics — and then replaying it
into the next form on their behalf — is not a thing to do without being asked.
``include_demographics`` exists for the user who wants exactly that.
"""
from __future__ import annotations

import difflib
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from . import storage

log = logging.getLogger(__name__)

# --- question normalisation -------------------------------------------------

# Boilerplate every ATS wraps its labels in. Stripped so "Why do you want to
# work here? *" and "why do you want to work here (required)" are one question.
_NOISE = re.compile(
    r"\s*(\*|\(required\)|\(optional\)|required|optional|this field is required)\s*",
    re.I)
_PUNCT = re.compile(r"[^\w\s]+")
_SPACE = re.compile(r"\s+")

#: Never learned. Matched against the normalised question.
_SENSITIVE_PATTERNS = (
    r"\bpassword\b", r"\bpasscode\b", r"\bverification code\b", r"\botp\b",
    r"\bsecurity (question|answer)\b", r"\bcredit card\b", r"\bcvv\b",
    r"\bsocial security\b", r"\bsin\b", r"\bnational insurance\b",
    r"\bbank\b", r"\bsort code\b", r"\biban\b",
)
_SENSITIVE = re.compile("|".join(_SENSITIVE_PATTERNS), re.I)

#: Learned only when the user opts in. Matched against the normalised question.
_DEMOGRAPHIC_PATTERNS = (
    r"\bgender\b", r"\bsex\b", r"\bpronoun", r"\brace\b", r"\bethnic",
    r"\bdisabilit", r"\bveteran\b", r"\bmilitary\b", r"\bsexual orientation\b",
    r"\breligio", r"\bage\b", r"\bdate of birth\b", r"\bmarital\b",
    r"\beeo\b", r"\bequal opportunit", r"\bdiversity\b",
    # Named outright rather than left to "ethnic". Greenhouse's standard
    # EEO block asks "Are you Hispanic/Latino?" — which contains none of
    # the words above, so it went to the model and came back answered.
    r"\bhispanic\b", r"\blatino\b", r"\blatina\b", r"\blatinx\b",
    r"\btransgender\b", r"\bcaregiver\b", r"\bpregnan",
)
_DEMOGRAPHIC = re.compile("|".join(_DEMOGRAPHIC_PATTERNS), re.I)

#: Below this length a "question" is a layout artefact, not a question.
_MIN_QUESTION = 4
#: Above this an "answer" is almost always the page's own prose, scraped wrong.
_MAX_ANSWER = 4000


def normalise(question: str) -> str:
    """The key an answer is stored and looked up under.

    Case, punctuation, required-markers and runs of whitespace all vary between
    boards for the same question, and every one of those variations used to be
    a separate row that never matched the next form.
    """
    q = _NOISE.sub(" ", question or "")
    q = _PUNCT.sub(" ", q)
    return _SPACE.sub(" ", q).strip().lower()


def is_sensitive(question: str) -> bool:
    return bool(_SENSITIVE.search(normalise(question)))


#: Answers that only a protected-characteristic question has. Matched
#: against a control's *options*, because the question itself may not say
#: what it is asking: Butternut Box's gender question opens "At Butternut
#: Box, we understand and value that every team member is unique…" and
#: never uses the word. Nothing in the label or the field name gives it
#: away. The four options do.
_DEMOGRAPHIC_ANSWERS = re.compile(
    r"^\s*(male|female|non[- ]?binary|transgender|intersex|"
    r"prefer not to (say|disclose|answer)|decline to (self[- ]?identify|answer)|"
    r"i prefer not to (say|disclose|answer)|"
    r"(white|black|asian|hispanic|latino|latina|arab)\b.*|"
    r"mixed or multiple ethnic.*|heterosexual|gay|lesbian|bisexual|"
    r"yes,? i (have|am) a disab\w+.*|no,? i (do not|don't) have a disab\w+.*|"
    r"veteran|not a (protected )?veteran.*)\s*$", re.I)

#: How many of a control's options have to look like that before the
#: question is treated as demographic whatever its wording. Two, because
#: one on its own is too easy to hit by accident — "Prefer not to say" is
#: offered on plenty of ordinary questions.
_DEMOGRAPHIC_ANSWER_HITS = 2


def options_are_demographic(options: list[str] | None) -> bool:
    """Whether a control's choices give away what it is really asking."""
    hits = {o.strip().lower() for o in (options or [])
            if o and _DEMOGRAPHIC_ANSWERS.match(o.strip())}
    return len(hits) >= _DEMOGRAPHIC_ANSWER_HITS


def is_demographic(question: str, key: str = "") -> bool:
    """Whether this is a protected-characteristic question.

    ``key`` is the field's id or name, and is worth checking as well as the
    label: a form that asks "Are you Hispanic/Latino?" names the input
    ``hispanic_ethnicity``, so the thing the label does not say outright,
    the markup does.
    """
    if _DEMOGRAPHIC.search(normalise(question)):
        return True
    return bool(key) and bool(_DEMOGRAPHIC.search(normalise(key.replace("_", " "))))


def learnable(question: str, answer: str, *, include_demographics: bool = False,
              field_type: str = "") -> bool:
    """Whether this pair should go into the bank at all."""
    q, a = (question or "").strip(), (answer or "").strip()
    if len(q) < _MIN_QUESTION or not a or len(a) > _MAX_ANSWER:
        return False
    if field_type.lower() == "password":
        return False
    if is_sensitive(q):
        return False
    if is_junk_question(q) or is_report_text(a):
        return False
    if is_demographic(q) and not include_demographics:
        return False
    return True


# --- the bank ---------------------------------------------------------------

@dataclass
class Answer:
    question: str               # as the form worded it, for reading
    answer: str
    source: str = "human"       # "human" beats "agent"
    key: str = ""               # the normalised form, for matching and deletion


def _key(question: str) -> str:
    """How a question is stored: the normalised form, tagged by source.

    The existing ``answers`` table is a flat question→answer map with no room
    for provenance, and adding a column means a migration in an app that
    deliberately has no migration framework. Encoding the source in the key
    keeps one row per (question, source) and leaves old rows readable.
    """
    return normalise(question)


# Sidecar rows, keyed off the question's normalised form. The record-separator
# character cannot occur in a scraped label, so these can never collide with a
# real question, and anything reading the table that does not know about them
# simply sees a few rows it ignores.
_SOURCE_PREFIX = "\x1esrc="
_TEXT_PREFIX = "\x1etxt="
_SIDECARS = (_SOURCE_PREFIX, _TEXT_PREFIX, "\x1etopic=")


def _is_sidecar(key: str) -> bool:
    return any(p in key for p in _SIDECARS)


#: Profile fields a correction in a form is allowed to rewrite. The ones
#: Sushi guesses from your country and seniority, so fixing one wording of
#: the question fixes every wording of it, on every later form.
_TEACHABLE = ("salary_expectation", "requires_sponsorship",
              "work_authorisation", "country", "notice_period", "location",
              "full_name", "email", "phone", "linkedin", "github", "website",
              "current_company", "current_title")

#: A location question that is about the job, not about where you live.
_NOT_HOME = re.compile(r"prefer|office|relocat|willing|happy|commut|open to|"
                       r"which (site|location)|work from", re.I)


def _teach_profile(question: str, answer: str) -> None:
    """Carry a user's correction back into the profile, where it applies."""
    answer = (answer or "").strip()
    if not answer or len(answer) > 80:
        return
    try:
        from .directfill import match_rule
        field = match_rule(question)
        if field not in _TEACHABLE:
            return
        if field == "location" and (_NOT_HOME.search(question)
                                    or answer.lower() in ("yes", "no")):
            return
        profile = storage.load_profile()
        if profile is None or getattr(profile, field, "") == answer:
            return
        setattr(profile, field, answer)
        if field in (profile.inferred or []):
            profile.inferred.remove(field)
        storage.save_profile(profile)
        log.info("learning: profile %s updated from a correction", field)
    except Exception:                                   # noqa: BLE001
        log.debug("could not update the profile", exc_info=True)


def remember(question: str, answer: str, source: str = "human") -> bool:
    """Store an answer. Returns whether anything changed.

    A human value always wins. An agent value is only written when there is no
    human answer for that question, so a guess can never overwrite a
    correction — which is the whole point of watching the form.
    """
    key = _key(question)
    if not key:
        return False
    if source == "human":
        _teach_profile(question, answer)
    existing = storage.all_answers()
    current = existing.get(key, "")
    current_source = existing.get(key + _SOURCE_PREFIX, "agent")

    if source == "agent" and is_report_text(answer):
        return False
    if source == "agent" and current_source == "human":
        return False
    if current == answer.strip() and current_source == source:
        return False

    storage.remember_answer(key, answer.strip())
    storage.remember_answer(key + _SOURCE_PREFIX, source)
    # Keep the wording the form actually used. Matching needs the normalised
    # key, but a settings list of "are you legally authorised to work in the
    # uk" is unreadable, and so is a prompt built from it.
    storage.remember_answer(key + _TEXT_PREFIX, (question or "").strip()[:300])

    # And file it under what the question is *about*, so a differently
    # worded version of the same question finds it. Same precedence rule:
    # a human answer is never replaced by a guess.
    topic = topic_of(question)
    if topic:
        tkey = _TOPIC_PREFIX + topic
        if not (source == "agent"
                and existing.get(tkey + _SOURCE_PREFIX, "agent") == "human"):
            storage.remember_answer(tkey, answer.strip())
            storage.remember_answer(tkey + _SOURCE_PREFIX, source)
    return True


#: A stored question has to be at least this specific before it is allowed to
#: answer a longer one by containment. Without it "age" would answer "What age
#: group do you fall into?" and, worse, "Do you have the right to work?".
_MIN_CONTAINMENT_TOKENS = 2
_MIN_CONTAINMENT_CHARS = 9

#: Suffixes stripped before two questions are compared word by word, longest
#: first. Only for matching — stored keys keep the wording they arrived with,
#: both because the Settings list has to stay readable and because stemming
#: the key would orphan every row an earlier version wrote.
_SUFFIXES = ("ness", "ing", "ies", "es", "ed", "s")
#: What has to survive the strip. Below this a suffix rule is eating the word.
_MIN_STEM = 4


def _stem(word: str) -> str:
    """A deliberately crude stem, so near-identical labels collide.

    Real forms do not agree with each other, or with a cover letter, on
    inflection: "Salary expectation" / "What are your salary expectations?",
    "Willingness to relocate" / "Are you willing to relocate?". Those are the
    same question, score about 0.7 on ``difflib``, and share not one token
    once you compare them literally.

    No stemmer library for six suffixes — and a real one would be worse here,
    since this needs to be stable across versions of the app rather than
    linguistically correct.
    """
    for suffix in _SUFFIXES:
        # "ss" only blocks the plural rule — "address" is not "addres", but
        # "willingness" really is "willing", and a blanket guard was quietly
        # exempting every -ness word from being stemmed at all.
        if suffix == "s" and word.endswith("ss"):
            continue
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            # "salaries" is "salary", not "salar" — dropping -ies outright
            # left it one letter short of the singular it is supposed to meet.
            rest = word[:-len(suffix)]
            return _stem(rest + "y" if suffix == "ies" else rest)
    # A trailing silent -e last, so the pairs the rules above leave half a
    # letter apart finally meet: leave/leaving both reach "leav",
    # authorise/authorised both reach "authori".
    if len(word) > _MIN_STEM and word.endswith("e"):
        return _stem(word[:-1])
    return word


def _stems(key: str) -> set[str]:
    return {_stem(w) for w in key.split()}


# --- what a question is *about* ----------------------------------------------
#
# Two forms asking the same thing in different words defeated every string
# method here. Measured on a real pair:
#
#     stored  "Do you have the legal right to work in the U.K.?"
#     asked   "Please confirm your Right to Work status in the location
#              where this job is advertised."
#
#     difflib similarity   0.43   (needs 0.82)
#     shared words         in, right, the, to, work
#     containment          fails both ways
#
# They are the same question. So answers are also filed under the *topic*
# they belong to, and a question with no lexical match can still be answered
# by whatever was stored for its topic.
#
# The table is deliberately narrow. A topic earns a place only when the
# answer is the same whoever is asking — notice period, right to work,
# education. "Why do you want to work here" is excluded on purpose: the
# answer is about the employer, and cross-applying it would put the wrong
# company's reasons in someone's form.

TOPICS: list[tuple[str, re.Pattern]] = [
    # Order matters — first match wins, so the narrow patterns go first.
    # "the expiration date of your current work authorization" contains
    # "work authoris…", and filed under right_to_work it would answer "do
    # you have the right to work?" with a date.
    ("visa_expiry", re.compile(
        r"\bexpir\w*\b.*\b(visa|authoris|authoriz|permit)|"
        r"\b(visa|work authoris\w+|work authoriz\w+|permit)\b.*\bexpir", re.I)),
    # Kept apart from sponsorship on purpose: "I have the right to work" and
    # "I need sponsorship" are opposite answers to adjacent questions, and
    # one filed as the other is worse than no answer at all.
    ("right_to_work", re.compile(
        r"\bright to work\b|\blegally (?:allowed|entitled|authoris|authoriz)|"
        r"\bwork authoris|\bwork authoriz|\bauthoris(?:ed|ation) to work\b|"
        r"\beligible to work\b|\bwork permit\b", re.I)),
    ("visa_sponsorship", re.compile(r"\bsponsor|\bvisa\b", re.I)),
    ("notice_period", re.compile(
        r"\bnotice period\b|\bhow much notice\b|"
        r"\bnotice\b.{0,40}\b(serve|give|required|obliged)\b|"
        r"\b(serve|give|giving)\b.{0,20}\bnotice\b", re.I)),
    ("start_date", re.compile(
        r"\b(start date|available to start|when (?:could|can|would) you start|"
        r"how soon.*start|earliest start)\b", re.I)),
    # Before salary_expectation: "what is your current salary" also contains
    # "salary", and the two are different numbers.
    ("current_salary", re.compile(
        r"\bcurrent\b.*\b(salary|compensation|pay)\b", re.I)),
    ("salary_expectation", re.compile(
        r"\b(salary|compensation|remuneration|pay)\b.*"
        r"\b(expect\w*|desired|requirement|range)\b|"
        r"\bexpected (?:salary|compensation|pay)\b", re.I)),
    ("education_level", re.compile(
        r"\b(highest|level of)\b.*\beducation\b|"
        r"\bhighest (?:degree|qualification)\b", re.I)),
    ("years_experience", re.compile(
        r"\byears?\b.{0,30}\bexperience\b|\bexperience\b.{0,30}\byears?\b", re.I)),
    ("how_heard", re.compile(
        r"\bhow did you (?:hear|find out|learn)\b|\bhow did you find\b|"
        r"\bwhere did you (?:hear|find)\b|\breferr?ed by\b", re.I)),
    ("relocation", re.compile(r"\brelocat", re.I)),
    ("onsite", re.compile(
        r"\b(onsite|on site|in office|in the office|hybrid|commute)\b", re.I)),
    ("criminal_record", re.compile(
        r"\b(criminal|convict\w+|unspent conviction)\b", re.I)),
    ("us_person", re.compile(r"\bu\s?s person\b|\bexport control\b", re.I)),
    ("security_clearance", re.compile(
        r"\bsecurity clearance\b|\bclearance level\b", re.I)),
    ("linkedin", re.compile(r"\blinked\s?in\b", re.I)),
    ("portfolio", re.compile(
        r"\b(portfolio|personal (?:site|website)|github)\b", re.I)),
]

#: Topic rows live in the same table, behind a prefix that cannot occur in a
#: scraped label — the same trick the source and text sidecars use.
_TOPIC_PREFIX = "\x1etopic="


def topic_of(question: str) -> str:
    """What this question is about, or "" if it is not a known topic.

    First match wins, so the order above matters: the sponsorship pattern
    would otherwise swallow half the right-to-work phrasings.
    """
    q = normalise(question)
    if not q:
        return ""
    for name, pattern in TOPICS:
        if pattern.search(q):
            return name
    return ""


def _recall_exact_raw(question: str) -> str:
    """The stored answer to *this* question, worded exactly this way.

    Separate from :func:`recall` because the caller needs to know the
    difference. A fuzzy hit is a reasonable inference about a question; an
    exact hit is the user's own answer to the question in front of them, and
    it outranks anything a rule about wording would conclude. Without this
    "Which visa do you currently hold?" matched a rule about sponsorship and
    was answered "No" — a wrong answer, typed into a real form, over the
    right one sitting in the bank.
    """
    key = _key(question)
    if not key:
        return ""
    return storage.all_answers().get(key, "")


def _usable(question: str, answer: str) -> str:
    """Never hand back junk, whatever got into the bank before it was
    filtered: a model's refusal, a report note, or an answer to a field id."""
    if not answer or is_report_text(answer) or is_junk_question(question):
        return ""
    return answer


def recall_exact(question: str) -> str:
    return _usable(question, _recall_exact_raw(question))


def source_of(question: str) -> str:
    """Who gave the stored answer to this exact question — "" if nobody did.

    The bank's precedence rule is the reason this is public: putting a value
    back after an undo has to put its provenance back too, or a guess that
    was overwritten returns as though the user had confirmed it.
    """
    key = _key(question)
    if not key:
        return ""
    raw = storage.all_answers()
    return raw.get(key + _SOURCE_PREFIX, "") if key in raw else ""


def recall(question: str, cutoff: float = 0.82) -> str:
    return _usable(question, _recall_raw(question, cutoff))


def _recall_raw(question: str, cutoff: float = 0.82) -> str:
    """The best stored answer for a question. Exact, then near, then contained.

    Boards word the same question three ways. Two of them defeat an exact-key
    bank and one defeats similarity scoring as well:

    - spelling and phrasing drift — "authorised"/"authorized", "the UK"/"the
      United Kingdom" — which ``difflib`` handles;
    - padding — a bank that learned "Notice period" from a Lever form being
      asked "What is your notice period?" by Greenhouse. Those two score about
      0.67, far under any sane cutoff, but they are plainly the same question.
      So a stored key whose words all appear in the asked question counts as a
      match, provided it is specific enough to mean something on its own.

    Containment runs both ways, because the two sources of answers pad in
    opposite directions. A form teaches the bank "Notice period" and a longer
    form later asks for it in a sentence. A document teaches it "What is your
    notice period and when could you start?" — nobody's field label, but it is
    what a cover letter actually contains — and a form then asks the short
    version. The reverse match takes the *shortest* stored key that covers the
    question, since every extra word in a stored key is meaning the asked
    question did not ask for.
    """
    key = _key(question)
    if not key:
        return ""
    bank = {k: v for k, v in storage.all_answers().items() if not _is_sidecar(k)}
    if not bank:
        return ""
    if key in bank:
        return bank[key]

    close = difflib.get_close_matches(key, list(bank), n=1, cutoff=cutoff)
    if close:
        return bank[close[0]]

    asked = _stems(key)
    best = ""
    for stored in bank:
        tokens = stored.split()
        if len(tokens) < _MIN_CONTAINMENT_TOKENS or len(stored) < _MIN_CONTAINMENT_CHARS:
            continue
        if _stems(stored) <= asked and len(stored) > len(best):
            best = stored
    if best:
        return bank[best]

    # The other direction: the asked question is the short one.
    if len(asked) < _MIN_CONTAINMENT_TOKENS or len(key) < _MIN_CONTAINMENT_CHARS:
        return ""
    best = ""
    for stored in bank:
        if not asked <= _stems(stored):
            continue
        if not best or len(stored) < len(best):
            best = stored
    if best:
        return bank[best]

    # Nothing matched on words. Does anything match on meaning?
    topic = topic_of(question)
    if topic:
        backfill_topics()
        by_topic = storage.all_answers().get(_TOPIC_PREFIX + topic, "")
        if by_topic:
            return by_topic
    return ""


#: Done once per process. The bank is small and the pass is cheap, but there
#: is no reason to walk it on every miss.
_backfilled = False


def backfill_topics(force: bool = False) -> int:
    """File answers stored before topics existed under their topic.

    Everything already in the bank was written by a version that only knew
    how to match on words, so none of it has a topic row — which means the
    feature would only start working on answers given from now on, and the
    answer you gave last week stays unreachable. This walks what is there
    once and files it.

    Human answers are preferred, and an existing human topic row is never
    overwritten; beyond that the bank keeps no timestamps, so where two
    human answers share a topic the first one wins rather than the newest.
    Correcting that is what the Settings list is for.
    """
    global _backfilled
    if _backfilled and not force:
        return 0
    _backfilled = True

    raw = storage.all_answers()
    existing_source = {
        k: raw.get(k + _SOURCE_PREFIX, "agent")
        for k in raw if not _is_sidecar(k)
    }
    filed = 0
    # Humans first, so a guess cannot claim a topic a real answer wants.
    for want in ("human", "agent"):
        # A list, not a view: the loop writes topic rows back into ``raw`` so
        # the second pass can see what the first one claimed.
        for key, answer in list(raw.items()):
            if _is_sidecar(key) or not answer:
                continue
            if existing_source.get(key, "agent") != want:
                continue
            topic = topic_of(raw.get(key + _TEXT_PREFIX) or key)
            if not topic:
                continue
            tkey = _TOPIC_PREFIX + topic
            if raw.get(tkey):
                continue
            storage.remember_answer(tkey, answer)
            storage.remember_answer(tkey + _SOURCE_PREFIX, want)
            raw[tkey] = answer
            filed += 1
    return filed


def bank(limit: int = 0) -> list[Answer]:
    """Everything learned, confirmed answers first. For the Settings page."""
    raw = storage.all_answers()
    out = [
        Answer(question=raw.get(k + _TEXT_PREFIX) or k, answer=v,
               source=raw.get(k + _SOURCE_PREFIX, "agent"), key=k)
        for k, v in raw.items() if not _is_sidecar(k)
    ]
    out.sort(key=lambda a: (a.source != "human", a.question.lower()))
    return out[:limit] if limit else out


# --- what to show a model that is writing a new answer -----------------------
#
# ``recall`` and ``related`` answer two different questions and must not be
# confused. ``recall`` asks "is this the same question, so may I type the
# stored answer straight into the form?" — one answer, or none, and a wrong
# yes puts the wrong text in somebody's application. ``related`` asks "what
# does the bank hold that is worth *reading* while composing a new answer?"
# — several, ranked, and a loose match costs nothing because the model is
# writing something new rather than replaying what it is given.
#
# That difference is why the bar here is so much lower than the 0.82 cutoff
# on recall. The whole point is the near-misses: asked "What draws you to
# this role?", an answer stored for "Why do you want to work here?" is not
# the same question and must never be typed in verbatim — the company has
# changed — but it is exactly the material the new answer should be built
# from.

#: Below this a bank entry has nothing to do with the question and would be
#: noise in the prompt.
_RELATED_CUTOFF = 0.30
#: What being about the same topic is worth. Enough to carry an entry that
#: shares no wording — which is the case the topic table exists for.
_TOPIC_WEIGHT = 0.45

#: The scaffolding every application question is built from. Stripped before
#: two questions are compared, because leaving it in is what made the first
#: version of this useless: "What is your favourite colour?" scored 0.59
#: against "What draws you to our company?" on the strength of "what", "is",
#: "your" and "you", and outranked the answer that was actually about the
#: question being asked.
_QUESTION_WORDS = frozenset("""
a an the and or but if is are am was were be been being do does did have has
had can could will would shall should may might must to of in on at by for
with from about into over after you your yours we our us they their it its
this that these those what which who whom whose why how when where please
tell describe give share any some all more most other such no nor not only
own same so than too very just now here there as
""".split())


def _content(question: str) -> set[str]:
    """The words in a question that carry its meaning, stemmed."""
    return {_stem(w) for w in _key(question).split()
            if w not in _QUESTION_WORDS and len(w) > 2}


def relevance(question: str, stored_key: str, *,
              stored_topic: str = "", asked_topic: str | None = None) -> float:
    """How worth reading ``stored_key`` is, for someone answering ``question``.

    Two signals, taken at their best rather than averaged: a shared
    vocabulary once the question-scaffolding is stripped, and a shared topic.
    Averaging would punish the pair that matches strongly on one and not at
    all on the other, which is most real pairs — "Notice period" and "What is
    your notice period and when could you start?" share a topic and little
    else.

    Measured, and worth stating plainly: this is precise and deaf. Over a
    realistic bank it pulls in *nothing* irrelevant, and it finds the exact
    and same-topic matches — but it cannot see that "Why are you interested
    in this role?" and "Why do you want to work here?" are the same question,
    because they share no content word at all. Neither can it pair "Describe
    a challenging project" with "Tell us about a time you solved a hard
    technical problem". That is the recall :mod:`sushi.embed` adds; this is
    what is left when there is no embedder, and returning nothing is the
    right way to be deaf.
    """
    if not stored_key:
        return 0.0
    asked, stored = _content(question), _content(stored_key)
    score = len(asked & stored) / len(asked | stored) if asked and stored else 0.0
    if asked_topic is None:
        asked_topic = topic_of(question)
    if asked_topic and asked_topic == stored_topic:
        score = max(score, _TOPIC_WEIGHT)
    return min(score, 1.0)


#: Cosine below this is not evidence of anything. Measured on real pairs
#: with the model in :mod:`sushi.embed`: questions that are genuinely the
#: same score 0.46 to 0.83, unrelated ones score 0.26 or less, and — the
#: pair that matters — "Do you need visa sponsorship?" against "Do you have
#: the legal right to work in the UK?" scores 0.30. Those two are opposite
#: answers to adjacent questions and the topic table already keeps them
#: apart on purpose; the floor sits in the gap above them.
_VECTOR_FLOOR = 0.40

#: Vectors for this process. A form label's vector never changes, and the
#: bank is hundreds of rows rather than millions, so there is nothing to
#: evict. Not kept on disk yet: the first composed answer of a session pays
#: for the whole bank at about 18ms a row, inside a path that is already
#: waiting on a model.
_vectors: dict[str, list[float]] = {}


def _vector_scores(question: str, texts: list[str]) -> list[float] | None:
    """Cosine of ``question`` against each of ``texts``, or None without a model.

    One batch for everything not already cached, because the cost here is
    per call rather than per row.
    """
    from . import embed

    if not embed.available():
        return None
    wanted = [question] + texts
    missing = [t for t in dict.fromkeys(wanted) if embed.text_id(t) not in _vectors]
    if missing:
        fresh = embed.encode(missing)
        if fresh is None:
            return None
        for text, vector in zip(missing, fresh):
            _vectors[embed.text_id(text)] = vector
    asked = _vectors.get(embed.text_id(question))
    if not asked:
        return None
    return [embed.similarity(asked, _vectors.get(embed.text_id(t), []))
            for t in texts]


def related(question: str, limit: int = 6,
            cutoff: float = _RELATED_CUTOFF) -> list[Answer]:
    """The stored answers worth reading while composing an answer to this.

    Ranked, best first. Returns [] rather than padding with whatever the
    bank happened to hold: an unrelated answer in the prompt is not neutral,
    it is an invitation to work the wrong material in.

    Two signals, whichever is more confident. The wording match is precise
    and deaf — see :func:`relevance`. The vector match hears the paraphrases
    it cannot, and is only allowed to speak above ``_VECTOR_FLOOR``, so a
    question that is merely adjacent does not get to answer.
    """
    if not question.strip():
        return []
    entries = [a for a in bank() if a.key]
    if not entries:
        return []
    asked_topic = topic_of(question)
    vectors = _vector_scores(question, [a.question for a in entries])
    scored: list[tuple[float, Answer]] = []
    for i, a in enumerate(entries):
        score = relevance(question, a.key, stored_topic=topic_of(a.question),
                          asked_topic=asked_topic)
        if vectors is not None and vectors[i] >= _VECTOR_FLOOR:
            score = max(score, vectors[i])
        if score >= cutoff:
            # Human-corrected answers break ties: they are the ones this
            # person has actually looked at and approved.
            scored.append((score + (0.01 if a.source == "human" else 0.0), a))
    scored.sort(key=lambda pair: -pair[0])
    return [a for _s, a in scored[:limit]]


def material_block(question: str, limit: int = 6, clip: int = 600) -> str:
    """What the bank holds about this question, as material to draw on.

    Not the same thing as :func:`prompt_block`, which hands over the first
    few rows of the bank whatever was asked. Composing "why do you want to
    work here" used to be shown the candidate's notice period, right to work
    and LinkedIn URL — true, stored, and no help at all — while the answer
    they wrote to that very question for a different employer sat further
    down the bank and never made it into the prompt.
    """
    found = related(question, limit)
    if not found:
        return "  (nothing on file that bears on this one)"
    return "\n".join(
        f"- Q: {a.question}\n  A: "
        f"{a.answer if len(a.answer) <= clip else a.answer[:clip - 1] + '…'}"
        for a in found)


def forget(question: str) -> None:
    """Drop an answer, including the topic row it is filed under.

    Deleting the row alone was not enough: an answer also lives under its
    topic, which is what lets a differently worded question find it, and that
    copy survived. So a forgotten answer went on being typed into forms
    through the back door — "Forget" in Settings looked like it had worked,
    and the next application proved it had not.

    Only the topic row this answer owns is removed, by comparing the value;
    what is left is then refiled, so a second answer on the same topic takes
    the row over rather than the topic going dark.
    """
    key = _key(question)
    answer = storage.all_answers().get(key, "")
    storage.forget_answer(key)
    for prefix in _SIDECARS:
        storage.forget_answer(key + prefix)

    topic = topic_of(question)
    if not topic or not answer:
        return
    tkey = _TOPIC_PREFIX + topic
    if storage.all_answers().get(tkey) != answer:
        return
    storage.forget_answer(tkey)
    storage.forget_answer(tkey + _SOURCE_PREFIX)
    _refile_topic(topic)


def _refile_topic(topic: str) -> None:
    """Give a vacated topic to the best answer still in the bank.

    Only this topic, rather than :func:`backfill_topics` over the whole bank:
    "Forget all" walks every answer, and a full backfill per deletion makes
    that quadratic for no gain.
    """
    raw = storage.all_answers()
    for want in ("human", "agent"):
        for key, answer in raw.items():
            if _is_sidecar(key) or not answer:
                continue
            if raw.get(key + _SOURCE_PREFIX, "agent") != want:
                continue
            if topic_of(raw.get(key + _TEXT_PREFIX) or key) != topic:
                continue
            storage.remember_answer(_TOPIC_PREFIX + topic, answer)
            storage.remember_answer(_TOPIC_PREFIX + topic + _SOURCE_PREFIX, want)
            return


def prompt_block(limit: int = 40, clip: int = 0) -> str:
    """The learned answers, formatted for the fill agent's task prompt.

    ``clip`` shortens each answer. The filler wants them whole — it is about
    to type them into a form — but the assistant only needs to know what is
    on record, and a bank holding four cover-letter paragraphs would
    otherwise be most of its context window.
    """
    entries = [a for a in bank() if a.source == "human"][:limit]
    if len(entries) < limit:
        entries += [a for a in bank() if a.source == "agent"][: limit - len(entries)]
    if not entries:
        return "- (nothing learned yet)"
    return "\n".join(
        f"- Q: {a.question}\n  A: "
        f"{a.answer if not clip or len(a.answer) <= clip else a.answer[:clip - 1] + '…'}"
        for a in entries)


# --- reading a live form ----------------------------------------------------

# Returns [{q, a, type}] for every field on the page that has a value and a
# label we can name. Label resolution follows the same order a screen reader
# would, which is also the order ATS forms actually use.
HARVEST_JS = r"""
(() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();

  const labelFor = el => {
    // 1. aria-label / aria-labelledby win: when a form sets them they are the
    //    accessible name, and they are stable across the re-renders that make
    //    scraping surrounding text unreliable.
    const aria = el.getAttribute('aria-label');
    if (clean(aria)) return clean(aria);
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const parts = by.split(/\s+/).map(id => document.getElementById(id))
        .filter(Boolean).map(n => clean(n.innerText || n.textContent));
      if (parts.join(' ').trim()) return clean(parts.join(' '));
    }
    // 2. <label for=id>, then an ancestor <label>.
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l && clean(l.innerText)) return clean(l.innerText);
    }
    const wrap = el.closest('label');
    if (wrap && clean(wrap.innerText)) return clean(wrap.innerText);
    // 3. The nearest preceding heading-ish text inside the field's group —
    //    Greenhouse and Lever both render questions this way.
    //    Only inside the field's own box: once an ancestor holds another
    //    control it is the form, and its first label is some other
    //    question's ("What is your salary?" got the answer to "Location").
    let node = el.parentElement;
    const own = el.name || el;
    for (let i = 0; node && i < 6; i++, node = node.parentElement) {
      const others = [...node.querySelectorAll('input:not([type=hidden]), select, textarea')]
        .filter(e => (e.name || e) !== own);
      if (others.length) break;
      const t = node.querySelector('label, legend, h1, h2, h3, h4, .question, [class*="label"]');
      if (t && !t.contains(el) && clean(t.innerText)) return clean(t.innerText);
      const opts = new Set([...node.querySelectorAll('label, option')].map(x => clean(x.innerText)));
      const line = (node.innerText || '').split('\\n').map(clean)
        .find(x => x.length > 2 && !opts.has(x));
      if (line) return line;
    }
    // 4. Last resort: the placeholder or the field's own name.
    return clean(el.getAttribute('placeholder') || el.name || '');
  };

  const valueOf = el => {
    const tag = el.tagName.toLowerCase();
    if (tag === 'select') {
      const picked = Array.from(el.selectedOptions || [])
        .map(o => clean(o.textContent)).filter(Boolean);
      return picked.join(', ');
    }
    if (el.type === 'checkbox' || el.type === 'radio') {
      if (!el.checked) return '';
      // The useful answer is the option's visible text, not "on".
      const own = clean(labelFor(el));
      return own || 'yes';
    }
    return clean(el.value);
  };

  const skip = el =>
    el.disabled || el.type === 'hidden' || el.type === 'password' ||
    el.type === 'file' || el.type === 'submit' || el.type === 'button' ||
    el.getAttribute('autocomplete') === 'one-time-code' ||
    !(el.offsetWidth || el.offsetHeight || el.getClientRects().length);

  const out = [];
  const seen = new Set();
  document.querySelectorAll('input, textarea, select').forEach(el => {
    try {
      if (skip(el)) return;
      const a = valueOf(el);
      if (!a) return;
      // A radio group's question is the fieldset, not the chosen option.
      let q = labelFor(el);
      if (el.type === 'radio' || el.type === 'checkbox') {
        const fs = el.closest('fieldset');
        const legend = fs && fs.querySelector('legend');
        const group = clean(legend && legend.innerText) ||
                      clean(el.closest('[role="group"]')?.getAttribute('aria-label'));
        if (group) q = group;
      }
      q = clean(q);
      if (!q) return;
      const k = q.toLowerCase() + '|' + a.toLowerCase();
      if (seen.has(k)) return;
      seen.add(k);
      // Still carrying Explorative mode's mark, so this value is Sushi's
      // guess and not the user's answer. The watcher must not bank it: a
      // guess that quietly became a stored human answer would be typed into
      // every later form as though it had been checked. The mark is removed
      // by the page itself the moment a real keystroke touches the field,
      // so an edited guess comes back here learnable.
      const guessed = el.getAttribute('data-sushi-guess') === '1';
      out.push({ q: q.slice(0, 300), a: a.slice(0, 4000),
                 type: (el.type || el.tagName).toLowerCase(),
                 guess: guessed });
    } catch (e) { }
  });
  return JSON.stringify({ url: location.href, fields: out });
})()
"""


# --- the watcher ------------------------------------------------------------

@dataclass
class WatchStats:
    polls: int = 0
    learned: int = 0
    last_error: str = ""


class FormWatcher:
    """Polls an open application form and keeps the answer bank current.

    Driven from the autofill module's own event loop. ``poll`` is a coroutine
    because reading the page is; everything else is plain state.

    Every failure mode is swallowed to a counter. This is a background nicety
    attached to the user's actual application — a changed CDP API or a page
    that navigates mid-read must never surface as an error on a form they are
    in the middle of submitting.
    """

    def __init__(self, evaluate: Callable[[str], Any], *,
                 include_demographics: bool = False,
                 on_learn: Callable[[int], None] | None = None) -> None:
        self._evaluate = evaluate            # async (js: str) -> str
        self._include_demographics = include_demographics
        self._on_learn = on_learn
        self._pending: dict[str, tuple[str, str]] = {}   # key -> (answer, type)
        #: What was in the form when Sushi handed it over — key -> answer.
        #: See :meth:`set_baseline`.
        self._baseline: dict[str, str] = {}
        self.stats = WatchStats()
        self.stopped = False

    def set_baseline(self, answers: dict[str, str]) -> None:
        """Declare these values as Sushi's own work, not the user's.

        The watcher's rule is "a value still standing at the next poll has
        been, at minimum, tolerated by the person looking at it". That rule
        is right for a form someone is working through and wrong the instant
        the agent stops typing in it: nobody has looked at anything yet, so
        the first poll after hand-over promoted every answer the agent had
        invented to ``human`` — the highest authority in the bank, the one
        that can never be overwritten by a later guess, and the one that gets
        typed into every future form as though it had been checked.

        Measured before this existed: an agent-written "Why do you want to
        work here?" was banked as ``agent``, and one poll later — with the
        page untouched — it was ``human``.

        So the values Sushi left are baselined. They are watched, not
        learned. The moment one of them *differs* from the baseline the user
        has been at it, the baseline for that field is dropped, and the
        ordinary settle rule takes over from there.
        """
        self._baseline = dict(answers)

    async def poll(self) -> int:
        """Read the form once; learn anything that has settled. Returns the count."""
        if self.stopped:
            return 0
        self.stats.polls += 1
        try:
            raw = await self._evaluate(HARVEST_JS)
        except Exception as e:                      # noqa: BLE001 — see class docstring
            self.stats.last_error = f"{type(e).__name__}: {e}"
            return 0
        fields = _parse_harvest(raw)
        if not fields:
            return 0

        learned = 0
        fresh: dict[str, tuple[str, str]] = {}
        for item in fields:
            q, a, ftype = item.get("q", ""), item.get("a", ""), item.get("type", "")
            if item.get("guess"):
                # Sushi's own guess, still untouched. Not learned, and not
                # held pending either — a value that never settles into the
                # bank must not sit in ``_pending`` waiting for ``flush`` to
                # bank it on the way out.
                continue
            if not learnable(q, a, include_demographics=self._include_demographics,
                             field_type=ftype):
                continue
            key = normalise(q)
            fresh[key] = (a, q)
            # Sushi's own work, untouched. Watched but never learned — see
            # set_baseline. A field that has moved off its baseline is the
            # user's from now on.
            base = self._baseline.get(key)
            if base is not None:
                if base == a:
                    continue
                self._baseline.pop(key, None)
            # Only learn a value that was already there last time. Polling a
            # field someone is typing into otherwise banks "Lond", then
            # "Londo", then "London".
            was = self._pending.get(key)
            if was and was[0] == a and remember(was[1], a, source="human"):
                learned += 1
        self._pending = fresh

        if learned:
            self.stats.learned += learned
            if self._on_learn:
                self._on_learn(learned)
        return learned

    async def flush(self) -> int:
        """Learn whatever is in the form right now, settled or not.

        Called when the page is about to go — a submit navigates away, and the
        answers on the last screen would otherwise never get their second poll.
        """
        if self.stopped:
            return 0
        learned = 0
        for key, (a, q) in list(self._pending.items()):
            # The baseline holds here too: a form going away with Sushi's
            # own answers still standing in it has taught nobody anything.
            if self._baseline.get(key) == a:
                continue
            if remember(q, a, source="human"):
                learned += 1
        self.stats.learned += learned
        if learned and self._on_learn:
            self._on_learn(learned)
        return learned

    def stop(self) -> None:
        self.stopped = True


def _parse_harvest(raw: Any) -> list[dict]:
    """Decode whatever the evaluator handed back into a list of fields."""
    import json

    if not raw:
        return []
    if isinstance(raw, (dict, list)):
        payload: Any = raw
    else:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if isinstance(payload, str):          # double-encoded by some CDP wrappers
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return []
    if isinstance(payload, dict):
        payload = payload.get("fields", [])
    return [f for f in payload if isinstance(f, dict)] if isinstance(payload, list) else []


def learn_pairs(pairs: Iterable[tuple[str, str]], source: str = "human") -> int:
    """Bulk-learn explicit question/answer pairs. Returns how many landed."""
    return sum(1 for q, a in pairs
               if learnable(q, a) and remember(q, a, source=source))


def seconds_between_polls() -> float:
    """How often to read the form.

    Fast enough that a correction is picked up before the user navigates away,
    slow enough that it is invisible: a DOM query over a few hundred inputs is
    sub-millisecond, and 2.5s makes it roughly nothing.
    """
    return 2.5


def now() -> float:
    return time.monotonic()


# --- questions still waiting for the user ------------------------------------
#
# A question the filler could not answer used to be listed once, on the page
# the run finished on, and forgotten. These are kept until answered, so the
# Profile page can list them and every later form gets the answer.

_OPEN_KEY = "open_questions"
_OPEN_CAP = 80


def _load_open() -> list[str]:
    import json

    from . import storage
    try:
        data = json.loads(storage.get_setting(_OPEN_KEY, "[]") or "[]")
        return [q for q in data if isinstance(q, str) and q.strip()]
    except Exception:                                   # noqa: BLE001
        return []


def note_unanswered(questions: Iterable[str]) -> int:
    """Keep questions a run could not answer, newest first. Returns how many
    were new."""
    import json

    from . import storage
    have = _load_open()
    seen = {normalise(q) for q in have}
    new = []
    for q in questions or []:
        q = (q or "").strip()
        if (not q or len(q) < 3 or normalise(q) in seen or is_sensitive(q)
                or is_demographic(q) or q.lower() in ("attach", "resume", "cv")):
            continue
        seen.add(normalise(q))
        new.append(q)
    if new:
        try:
            storage.set_setting(_OPEN_KEY, json.dumps((new + have)[:_OPEN_CAP]))
        except Exception:                               # noqa: BLE001
            return 0
    return len(new)


def open_questions() -> list[str]:
    """The kept questions that still have no answer in the bank."""
    import json

    from . import storage
    have = _load_open()
    left = [q for q in have if not recall_exact(q)]
    if len(left) != len(have):
        try:
            storage.set_setting(_OPEN_KEY, json.dumps(left))
        except Exception:                               # noqa: BLE001
            pass
    return left


_GUESS_KEY = "open_guesses"

# Questions the model was asked and could not answer — a clearance level, a
# polygraph. Asking again on the next form costs seconds per question and
# comes back blank, because nothing it knows has changed. Kept until the CV
# or profile changes, or a week has gone by, whichever comes first.
_DECLINED_KEY = "declined_questions"
_DECLINED_TTL = 7 * 24 * 3600


def _load_declined() -> dict[str, float]:
    import json

    from . import storage
    try:
        data = json.loads(storage.get_setting(_DECLINED_KEY, "{}") or "{}")
    except Exception:                                   # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    cut = time.time() - _DECLINED_TTL
    return {k: float(v) for k, v in data.items()
            if isinstance(v, (int, float)) and v > cut}


def note_declined(questions: Iterable[str]) -> int:
    """Remember questions the model could not answer. Returns how many new."""
    import json

    from . import storage
    have = _load_declined()
    n = 0
    for q in questions or []:
        k = normalise(q or "")
        if k and k not in have:
            have[k] = time.time()
            n += 1
    if n:
        try:
            storage.set_setting(_DECLINED_KEY, json.dumps(have))
        except Exception:                               # noqa: BLE001
            return 0
    return n


def was_declined(question: str) -> bool:
    """True when the model already could not answer this, and nothing changed."""
    k = normalise(question or "")
    return bool(k) and k in _load_declined() and not recall_exact(question)


def forget_declined() -> None:
    """The CV or profile changed: the model may know the answer now."""
    from . import storage
    try:
        storage.set_setting(_DECLINED_KEY, "{}")
    except Exception:                                   # noqa: BLE001
        pass


#: An agent's account of an answer rather than the answer: "written from the
#: CV, framed around the database and API layer", "filled per the Sushi
#: checklist". Its final report lists these under GUESSED, and banked as
#: answers they were typed word for word into the next form that asked.
_REPORT_TEXT = re.compile(
    r"^(?:written|filled|drafted|composed|answered|left|selected|chosen|"
    r"typed|entered|uploaded)\b"
    r"|\bsushi'?s (?:draft|checklist)\b|\bper the (?:sushi )?checklist\b"
    r"|\bi did not (?:re-?read|check|verify)\b"
    # A model declining, saved as if it were the answer: "I can't answer
    # this one yet. The question I received is only a form field
    # identifier…" was banked and offered back on the next form.
    r"|^i (?:can'?t|cannot|can not|am unable to|won'?t) (?:answer|fill|provide|help)"
    r"|\bplease (?:send|share|provide) the (?:visible )?(?:label|prompt|question)\b"
    r"|\bas an ai\b|\bwithout the wording\b",
    re.I)

#: Not a question at all: a widget's internal name ("cards[a9c0…][field12]",
#: "field_123") or a button's text ("UPLOAD FILE", "ATTACH RESUME/CV").
_NOT_A_QUESTION = re.compile(
    r"\[[^\]]*\]|^[a-z_]+\d+$|^_?systemfield|^[a-f0-9-]{16,}$"
    r"|^(?:upload|attach|choose|browse|select)\b.{0,20}$"
    r"|^(?:upload|attach) (?:file|resume|cv|resume/cv)$", re.I)


_REFUSAL = re.compile(r"^i (?:can'?t|cannot|am unable)|\bas an ai\b|without the wording", re.I)


def is_junk_question(question: str) -> bool:
    return bool(_NOT_A_QUESTION.search((question or "").strip()))


def is_report_text(answer: str) -> bool:
    """True when this "answer" is a note about an answer, not one."""
    return bool(_REPORT_TEXT.search((answer or "").strip()))


def purge_report_answers() -> int:
    """Drop agent answers that are really report notes. Returns how many."""
    import json

    from . import storage
    gone = 0
    for a in bank():
        # Junk goes whoever saved it: a "question" that is a field id or a
        # button label, or an answer that is a model saying it cannot.
        if is_junk_question(a.question) or (
                is_report_text(a.answer)
                and (a.source != "human" or _REFUSAL.search(a.answer))):
            forget(a.question)
            gone += 1
    try:
        have = json.loads(storage.get_setting(_GUESS_KEY, "{}") or "{}")
        if isinstance(have, dict):
            kept = {q: v for q, v in have.items() if not is_report_text(v)}
            if len(kept) != len(have):
                storage.set_setting(_GUESS_KEY, json.dumps(kept))
    except Exception:                                   # noqa: BLE001
        pass
    return gone


def note_guesses(pairs: Iterable[tuple[str, str]]) -> int:
    """Keep what the model worked out, so the user can confirm it once."""
    import json

    from . import storage
    try:
        have = json.loads(storage.get_setting(_GUESS_KEY, "{}") or "{}")
        if not isinstance(have, dict):
            have = {}
    except Exception:                                   # noqa: BLE001
        have = {}
    n = 0
    for q, a in pairs or []:
        q, a = (q or "").strip(), (a or "").strip()
        if not q or not a or is_report_text(a) or is_sensitive(q) or is_demographic(q)                 or source_of(q) == "human":
            continue
        # Banked straight away, as the agent's: the next form that asks gets
        # it without another model call, and anything the user says later
        # outranks it.
        try:
            remember(q, a, source="agent")
        except Exception:                               # noqa: BLE001
            pass
        if q not in have:
            n += 1
        have[q] = a
    if n:
        items = list(have.items())[-_OPEN_CAP:]
        storage.set_setting(_GUESS_KEY, json.dumps(dict(items)))
    return n


def open_guesses() -> dict[str, str]:
    """Guesses still waiting for the user to confirm or correct."""
    import json

    from . import storage
    try:
        have = json.loads(storage.get_setting(_GUESS_KEY, "{}") or "{}")
    except Exception:                                   # noqa: BLE001
        return {}
    if not isinstance(have, dict):
        return {}
    # Listed until the user has said it themselves.
    left = {q: (recall_exact(q) or a) for q, a in have.items()
            if source_of(q) != "human"}
    if len(left) != len(have):
        storage.set_setting(_GUESS_KEY, json.dumps(left))
    return left


def forget_agent_answers() -> int:
    forget_declined()
    return _forget_agent_answers()


def _forget_agent_answers() -> int:
    """Drop every answer the model worked out, and the list of them.

    Called when the CV changes. Those answers were read off the old CV —
    a notice period, years of experience, a salary — and left in the bank
    they go on being typed into forms long after the CV that justified them
    is gone, which is how switching CV "still shows the old one's text".
    What the user typed themselves is theirs and stays.
    """
    from . import storage
    gone = 0
    for a in bank():
        if a.source != "human":
            forget(a.question)
            gone += 1
    storage.set_setting(_GUESS_KEY, "{}")
    return gone
