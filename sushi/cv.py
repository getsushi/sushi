"""Turn an uploaded CV into a structured profile.

Two steps, deliberately separate: get the text out of the file (mechanical, no
AI, must never silently fail), then have the model read it (judgement). Keeping
them apart means a bad PDF reports a parsing problem rather than producing a
confidently wrong profile from an empty string.
"""
from __future__ import annotations

import re
from pathlib import Path

from .llm import LLM, LLMError
from .models import Experience, Profile

SUPPORTED = {".pdf", ".docx", ".txt", ".md"}


class CVError(RuntimeError):
    pass


# --- text extraction --------------------------------------------------------

def extract_text(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        raise CVError(f"File not found: {p}")
    ext = p.suffix.lower()
    if ext not in SUPPORTED:
        raise CVError(f"Unsupported file type {ext!r}. Use PDF, DOCX, TXT or MD.")

    if ext in {".txt", ".md"}:
        text = p.read_text(encoding="utf-8", errors="replace")
    elif ext == ".docx":
        text = _from_docx(p)
    else:
        text = _from_pdf(p)

    text = _tidy(text)
    if len(text) < 120:
        raise CVError(
            "Could not read meaningful text from this file. If it is a scanned "
            "PDF it needs OCR first. Export a text-based PDF and try again."
        )
    return text


def _from_docx(p: Path) -> str:
    try:
        import docx
    except ImportError as e:
        raise CVError("python-docx is not installed") from e
    d = docx.Document(str(p))
    parts = [para.text for para in d.paragraphs]
    for table in d.tables:  # CVs often hide contact details in a table
        for row in table.rows:
            parts.append("\t".join(c.text for c in row.cells))
    return "\n".join(parts)


def _from_pdf(p: Path) -> str:
    """pdfplumber keeps column layout better; pypdf is the fallback."""
    try:
        import pdfplumber
        with pdfplumber.open(str(p)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        if len(text.strip()) > 120:
            return text
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        return "\n".join(pg.extract_text() or "" for pg in PdfReader(str(p)).pages)
    except Exception as e:
        raise CVError(f"Could not read the PDF: {e}") from e


def _tidy(text: str) -> str:
    text = text.replace("\xa0", " ").replace("•", "- ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --- deterministic pre-extraction ------------------------------------------
# Pulled with regex before the model sees the CV: these are the fields that go
# straight into application forms, where a hallucinated value is worst.

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:(?:\+|00)\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?){2,4}\d{2,4}")
LINKEDIN_RE = re.compile(r"(?:https?://)?(?:[\w-]+\.)?linkedin\.com/in/[\w%-]+/?", re.I)
GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[\w-]+/?", re.I)


def hard_facts(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if m := EMAIL_RE.search(text):
        out["email"] = m.group(0)
    if m := LINKEDIN_RE.search(text):
        out["linkedin"] = m.group(0)
    if m := GITHUB_RE.search(text):
        out["github"] = m.group(0)
    # Phone patterns are loose, so only accept a candidate with enough digits.
    for m in PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 9 <= len(digits) <= 15:
            out["phone"] = m.group(0).strip()
            break
    return out


# --- model-driven understanding --------------------------------------------

SYSTEM = (
    "You read a CV and extract a structured profile. You are precise and you "
    "do not invent facts. If something is not stated, leave it empty rather "
    "than guessing. Record anything you did infer in the 'inferred' list so "
    "the user can check it."
)

PROMPT = """Read this CV and return a JSON object with exactly these keys:

{{
  "full_name": "", "email": "", "phone": "", "location": "", "country": "",
  "linkedin": "", "github": "", "website": "",
  "headline": "one line positioning this person for their next role",
  "summary": "3-4 sentences on what they do and what they are strong at",
  "years_experience": "e.g. '12'",
  "seniority": "one of: ic, lead, manager, director, exec",
  "current_title": "", "current_company": "",
  "skills": ["concrete skills and technologies, max 25"],
  "domains": ["industries/problem areas they know, max 10"],
  "education": ["Degree, Institution, Year"],
  "experience": [
    {{"company": "", "title": "", "start": "", "end": "", "highlights": ["max 3 each"]}}
  ],
  "target_titles": ["6-10 job titles they should realistically be shown, matched to their seniority"],
  "remote_ok": true,
  "work_authorisation": "only if clearly stated, else ''",
  "requires_sponsorship": "'yes' or 'no' only if the CV states it, else ''",
  "salary_expectation": "expected salary exactly as the CV states it, with currency and period, else ''",
  "inferred": ["names of fields you inferred rather than read"]
}}

Rules:
- "end" is "" for a current role.
- target_titles must match their actual level. Someone who has run an
  engineering function should not be sent junior roles, and vice versa.
- Do not invent an email, phone number or employer.
- "location" and "country" are where they live, read off the CV. They are
  for the address on a form, not a guess at where they want to work: that
  is a question the app asks them directly.

CV:
---
{cv}
---"""


def build_profile(llm: LLM, cv_text: str) -> Profile:
    """Ask the model to read the CV, then overwrite contact fields with the
    regex-extracted ones, which are more reliable than generation."""
    if not llm.enabled:
        raise LLMError("No API key configured. Open Settings and add one.")

    data = llm.complete_json(PROMPT.format(cv=cv_text[:60000]), system=SYSTEM, max_tokens=6000)
    if not isinstance(data, dict):
        raise LLMError("The model did not return a profile object.")

    exp = []
    for e in data.get("experience") or []:
        if isinstance(e, dict):
            exp.append(Experience(
                company=str(e.get("company", "")), title=str(e.get("title", "")),
                start=str(e.get("start", "")), end=str(e.get("end", "")),
                highlights=[str(h) for h in (e.get("highlights") or [])][:3],
            ))
    data["experience"] = exp
    data["raw_cv_text"] = cv_text

    profile = Profile.from_dict(data)
    # Deterministic values win: the model may normalise or mistype them.
    for key, value in hard_facts(cv_text).items():
        if value:
            setattr(profile, key, value)
    return profile
