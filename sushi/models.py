"""The things Sushi works with: a person, a job, and an attempt to apply.

These are plain dataclasses so they can cross the UI/worker thread boundary
and be serialised to SQLite without an ORM in the way.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# --- what the AI works out about the user from their CV --------------------


@dataclass
class Experience:
    company: str = ""
    title: str = ""
    start: str = ""          # free text as written on the CV, e.g. "Oct 2022"
    end: str = ""            # "" means current
    highlights: list[str] = field(default_factory=list)


@dataclass
class Profile:
    """Everything we inferred about the candidate. Every field is optional.

    ``confidence`` and ``inferred`` matter: the user must be able to see what
    the AI guessed versus what it read, because a wrong guess goes into real
    application forms.
    """

    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    country: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""

    headline: str = ""                     # one-line positioning
    summary: str = ""
    years_experience: str = ""
    seniority: str = ""                    # ic | lead | manager | director | exec
    current_title: str = ""
    current_company: str = ""

    skills: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    experience: list[Experience] = field(default_factory=list)

    # What roles this person should be shown. Derived, then user-editable.
    target_titles: list[str] = field(default_factory=list)
    target_locations: list[str] = field(default_factory=list)
    #: Where you are willing to work: "any", "remote", "hybrid" or "onsite".
    #:
    #: Sushi shipped assuming remote, which is a preference wearing the
    #: clothes of a default — it was in the corpus, in the scoring and in
    #: the copy, and nowhere a user could change it. ``remote_ok`` was the
    #: only knob, it was set by the CV reader rather than by the person,
    #: read by exactly one line, and had no control anywhere in the app.
    workplace: str = "any"
    remote_ok: bool = True        # kept so profiles saved earlier still load
    work_authorisation: str = ""
    requires_sponsorship: str = ""
    salary_expectation: str = ""
    notice_period: str = ""

    inferred: list[str] = field(default_factory=list)   # field names the AI guessed
    raw_cv_text: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Profile":
        exp = [Experience(**e) if isinstance(e, dict) else e for e in d.get("experience", [])]
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        known["experience"] = exp
        # A profile written before there was a choice carries only
        # ``remote_ok``. Read it as the preference it was standing in for,
        # rather than silently resetting the person to "any".
        if "workplace" not in d and "remote_ok" in d:
            known["workplace"] = "remote" if d.get("remote_ok") else "onsite"
        return cls(**known)

    def form_answers(self) -> dict[str, str]:
        """Flat key/value the form filler can draw on for common fields."""
        first, _, last = (self.full_name or "").partition(" ")
        return {k: v for k, v in {
            "first_name": first, "last_name": last, "full_name": self.full_name,
            "email": self.email, "phone": self.phone, "location": self.location,
            "country": self.country, "linkedin": self.linkedin, "github": self.github,
            "website": self.website, "current_company": self.current_company,
            "current_title": self.current_title, "years_experience": self.years_experience,
            "work_authorisation": self.work_authorisation,
            "requires_sponsorship": self.requires_sponsorship,
            "salary_expectation": self.salary_expectation,
            "notice_period": self.notice_period,
        }.items() if v}


# --- the CVs on file --------------------------------------------------------


@dataclass
class Resume:
    """One CV the user has attached, and the profile it was read into.

    The profile is kept *per CV* rather than only as "the" profile. That is
    the whole of what makes switching honest: a second CV that has not been
    read yet has no profile, so there is nothing to show — which is better
    than showing the last one's name, summary and employers under the new
    file's name, which is what happened when there was one profile and
    several files passing through it.
    """

    id: int = 0
    path: str = ""
    label: str = ""
    #: ``None`` when the file has been attached but never read.
    profile: "Profile | None" = None
    #: The file's modification time when it was last read, so "has this
    #: changed since?" is a stat call rather than another model request.
    read_mtime: float = 0.0
    added_at: str = ""
    updated_at: str = ""

    @property
    def name(self) -> str:
        return self.label or Path(self.path).name or "resume"

    @property
    def read(self) -> bool:
        return self.profile is not None

    def exists(self) -> bool:
        try:
            return bool(self.path) and Path(self.path).is_file()
        except OSError:
            return False

    def mtime(self) -> float:
        try:
            return Path(self.path).stat().st_mtime
        except OSError:
            return 0.0

    def changed(self) -> bool:
        """Has the file moved on since Sushi last read it?

        A second of tolerance: some editors and cloud-sync clients rewrite a
        file byte-identical, and a re-read costs a model request.
        """
        if not self.exists():
            return False
        if not self.read:
            return True
        return self.mtime() - self.read_mtime > 1.0


# --- jobs and applications --------------------------------------------------


@dataclass
class Role:
    company: str = ""
    title: str = ""
    location: str = ""
    url: str = ""
    department: str = ""
    ats: str = ""
    token: str = ""
    score: int = 0
    match: int = 0                 # heuristic match percentage, 0-100 (for display)
    salary: str = ""               # as advertised, when the board publishes it
    reasons: list[str] = field(default_factory=list)
    description: str = ""
    #: When Sushi first saw this vacancy, as a Unix time. Not when it was
    #: posted — no board publishes that reliably — but close enough to
    #: answer "what is new since I last looked", which is the only thing a
    #: date filter on a job feed is ever asked.
    first_seen: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.company}::{self.title}::{self.url}"


# An application moves in one direction through these states.
STATUSES = ["matched", "queued", "filling", "needs_review", "submitted", "failed", "skipped"]


@dataclass
class Application:
    id: int | None = None
    role: Role = field(default_factory=Role)
    status: str = "matched"
    note: str = ""
    #: What the last fill could not answer. Kept so "Needs you" is something
    #: the user can act on from the tracker rather than a label describing a
    #: dialog they have already dismissed.
    questions: list[str] = field(default_factory=list)
    cover_letter_path: str = ""
    screenshot_path: str = ""
    created_at: str = ""
    updated_at: str = ""
