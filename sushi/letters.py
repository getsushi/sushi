"""Cover letters, written per role from the profile.

The letter is grounded in what the CV actually says. The prompt forbids
inventing experience, and asks the model to name a genuine gap rather than
paper over one, because a recruiter spots the gap anyway and the candidate
looks better for having addressed it.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from .config import ensure_dirs, letters_dir
from .llm import LLM
from .models import Profile, Role

SYSTEM = (
    "You write cover letters that sound like the candidate, not like a "
    "template. You never invent experience, employers, dates or metrics. "
    "British spelling. No clichés such as 'I am thrilled' or 'fast-paced'."
)

PROMPT = """Write a cover letter for this application.

ROLE
  Company:  {company}
  Title:    {title}
  Location: {location}
{jd}

CANDIDATE
  Name:     {name}
  Now:      {current_title} at {current_company}
  Summary:  {summary}
  Skills:   {skills}
  History:
{history}

REQUIREMENTS
- 4-5 short paragraphs, under 400 words.
- Open by naming the role and the single strongest reason they fit.
- Use concrete evidence from the history above. Numbers only if given.
- If there is an obvious gap between this person and the role, name it in one
  sentence and say what they bring instead. Do not pretend it isn't there.
- End with a one-line close, then the candidate's name on its own line.
- Plain prose. No bullet points, no headers, no placeholders to fill in.
- No dashes as punctuation. Use a full stop, a comma or a colon instead."""


def _slug(text: str, limit: int = 40) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return out[:limit].strip("-") or "role"


def letter_path(role: Role) -> Path:
    ensure_dirs()
    stem = f"{_slug(role.company, 24)}-{_slug(role.title)}-{time.strftime('%Y-%m-%d')}"
    # The user's workspace, not AppData: a cover letter is a document they will
    # want to open, edit and attach.
    return letters_dir() / f"{stem}.txt"


def existing_letter(role: Role) -> Path | None:
    """A letter already written for this role, newest first, if there is one.

    Matched on the company-and-title stem rather than the exact filename,
    because the filename carries today's date and a letter written on Monday
    is still this role's letter on Friday. PDF wins over the .txt it came
    from: that is the one an employer gets handed.
    """
    stem = f"{_slug(role.company, 24)}-{_slug(role.title)}"
    try:
        found = sorted(letters_dir().glob(f"{stem}*"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    pdfs = [p for p in found if p.suffix.lower() == ".pdf" and p.stat().st_size > 64]
    if pdfs:
        return pdfs[0]
    others = [p for p in found if p.stat().st_size > 64]
    return others[0] if others else None


def wants_cover_letter(fields: list[dict]) -> bool:
    """Whether this form actually has somewhere to put a cover letter.

    Writing one for every application was waste: most forms never ask, the
    letter costs a model call each time, and a folder of letters for jobs
    that did not want one is noise. The form says whether it wants one —
    there is a field for it or there is not.
    """
    for f in fields or []:
        text = f"{f.get('key', '')} {f.get('label', '')}".lower()
        if "cover" in text and "letter" in text:
            return True
        if "cover_letter" in text or "coverletter" in text:
            return True
    return False


def write_letter(llm: LLM, profile: Profile, role: Role, *,
                 job_description: str = "") -> tuple[Path, str]:
    history = "\n".join(
        f"    - {e.title} at {e.company} ({e.start}–{e.end or 'present'}): "
        + "; ".join(e.highlights[:2])
        for e in profile.experience[:5]
    ) or "    - (not provided)"

    jd = f"\n  Description:\n{job_description[:4000]}\n" if job_description else ""

    text = llm.complete(
        PROMPT.format(
            company=role.company, title=role.title, location=role.location, jd=jd,
            name=profile.full_name, current_title=profile.current_title,
            current_company=profile.current_company, summary=profile.summary,
            skills=", ".join(profile.skills[:18]), history=history,
        ),
        system=SYSTEM, max_tokens=3000, temperature=0.4,
    ).strip()

    path = letter_path(role)
    path.write_text(text, encoding="utf-8")
    return path, text


#: Typographic characters a model reaches for that the PDF's base font has
#: no glyph for. Each one renders as a filled black square — eleven of them
#: in one letter, every single hyphenated compound: "route\u2011planning",
#: "end\u2011to\u2011end", "non\u2011technical". The text is correct and the
#: document is unusable, which is the worst combination to ship.
#:
#: Mapped rather than stripped: a non-breaking hyphen *is* a hyphen, and the
#: only thing wrong with it is that Helvetica cannot draw it.
_TYPOGRAPHIC = {
    "\u2010": "-",   # hyphen
    "\u2011": "-",   # non-breaking hyphen — the usual culprit
    "\u2012": "-",   # figure dash
    "\u2212": "-",   # minus sign
    "\u202f": " ",   # narrow no-break space
    "\u2007": " ",   # figure space
    "\u2009": " ",   # thin space
    "\u200a": " ",   # hair space
    "\u200b": "",    # zero-width space
    "\ufeff": "",    # byte-order mark
    "\u00ad": "",    # soft hyphen
}


def printable(text: str) -> str:
    """Text the PDF font can actually draw.

    Two passes. The known typographic substitutes first, because those have
    a right answer. Then anything still outside the font's encoding is
    reduced to its closest ASCII form, and only what survives neither is
    dropped — better a missing character than a black box in the middle of
    somebody's cover letter.
    """
    import unicodedata

    for bad, good in _TYPOGRAPHIC.items():
        text = text.replace(bad, good)
    out = []
    for ch in text:
        if ch in "\n\r\t" or ch.encode("cp1252", "ignore"):
            out.append(ch)
            continue
        folded = unicodedata.normalize("NFKD", ch)
        keep = "".join(c for c in folded
                       if not unicodedata.combining(c)
                       and c.encode("cp1252", "ignore"))
        out.append(keep)
    return "".join(out)


def render_pdf(text: str, path: Path, *, author: str = "") -> Path | None:
    """Most ATS file fields expect a PDF rather than a .txt.

    The text is run through :func:`printable` first: this is the only place
    the font's encoding matters, and the .txt alongside it stays exactly as
    written.
    """
    text = printable(text)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        return None
    try:
        styles = getSampleStyleSheet()
        body = ParagraphStyle("body", parent=styles["Normal"], fontName="Helvetica",
                              fontSize=10.5, leading=15.5, spaceAfter=10)
        doc = SimpleDocTemplate(str(path), pagesize=A4,
                                leftMargin=25 * mm, rightMargin=25 * mm,
                                topMargin=22 * mm, bottomMargin=22 * mm,
                                title="Cover Letter", author=author)
        flow: list = []
        for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
            flow.append(Paragraph(para.replace("\n", "<br/>"), body))
            flow.append(Spacer(1, 2))
        doc.build(flow)
        return path
    except Exception:
        return None
