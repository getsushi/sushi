"""Reading the folder where your previous answers already live.

Everyone who has applied for more than a handful of jobs has a folder like
this: cover letters, a "job applications" doc with the long answers pasted in,
notes on why you left the last place, your visa status written out properly
once so you never have to think about it again. That folder is the best
training data Sushi could have, and the only reason it has not been used is
that nobody ever asked for it.

So onboarding asks. Each document is read, the model pulls out the
question/answer pairs it can actually see in the text, and those land in the
same answer bank the form-watcher writes to — tagged ``human``, because they
are the user's own words, which means a later guess from the agent can never
overwrite them.

Two things this deliberately does not do. It does not learn passwords, bank
details or anything else :mod:`sushi.learning` refuses, and it does not learn
demographic or EEO answers unless the user has turned that on — a diversity
form answered once in 2023 is not consent to answer it everywhere. And it
never uploads the folder: each file is read locally, and only the extracted
pairs are kept.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import learning, storage
from .config import load_settings
from .cv import SUPPORTED, extract_text
from .llm import LLM, LLMError

log = logging.getLogger(__name__)

#: Where the user pointed us. Separate from the workspace: the workspace is
#: where Sushi *writes*, this is where the user's history already is, and
#: making them the same folder would mean re-reading our own output.
FOLDER_KEY = "knowledge_folder"
#: path -> "<mtime>:<size>" for everything already read, so a re-run is cheap
#: and does not re-ask the model about documents that have not changed.
LEDGER_KEY = "knowledge_ledger"

#: Anything bigger is a thesis, not an application answer. Reading one would
#: cost a minute and mostly teach the bank prose it will never need.
MAX_CHARS = 60_000
#: Documents are chunked so a long one still fits a single request.
CHUNK = 12_000

EXTRACT_SYSTEM = (
    "You extract reusable job-application answers from a candidate's own "
    "documents. British spelling. You never invent an answer: every pair you "
    "return must be visibly present in the text."
)

EXTRACT_PROMPT = """Below is one of the candidate's own documents: a cover letter, a saved
application, a notes file, or something similar.

Pull out any answer that could be reused on a future job application form.
Typical examples: notice period, salary expectation, right to work and visa
status, years of experience, why they are leaving, willingness to relocate,
LinkedIn or portfolio URLs, and the paragraph-length answers to questions like
"Why do you want to work here?", but only when the document actually contains
them.

Rules:
- Phrase the question the short way a form field actually labels it: "Notice
  period", "Salary expectation", "Do you require visa sponsorship?", "LinkedIn
  URL". Not a sentence a human would say.
- One question per pair. If the document answers two things at once, split it
  into two pairs; never return a compound question joined by "and".
- Quote the candidate's answer in their own words; tidy it, never rewrite it.
- Skip anything employer-specific ("why Stripe") unless the answer is general.
- Skip passwords, bank details, ID numbers, and anything about race, gender,
  disability, religion or sexuality.
- If the document has nothing reusable, return an empty list. That is a fine
  answer and better than a guess.

DOCUMENT ({name}):
{text}

Reply as JSON: {{"pairs": [{{"question": "...", "answer": "..."}}]}}"""


@dataclass
class Ingested:
    """What one pass over the folder did."""
    files: int = 0            # documents read
    skipped: int = 0          # unchanged since last time, or unreadable
    learned: int = 0          # pairs that were new or better
    pairs: int = 0            # pairs the model returned
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.files and not self.skipped:
            return "No documents found in that folder."
        if not self.files:
            return f"Nothing new. {self.skipped} document(s) already read."
        bits = [f"Read {self.files} document(s)"]
        if self.learned:
            bits.append(f"learned {self.learned} answer(s)")
        elif self.pairs:
            bits.append("nothing new to learn")
        else:
            bits.append("no reusable answers in them")
        if self.skipped:
            bits.append(f"{self.skipped} unchanged")
        return " · ".join(bits) + "."


# --- the folder itself -------------------------------------------------------

def folder() -> Path | None:
    """The knowledge folder, if the user has chosen one that still exists."""
    raw = storage.get_setting(FOLDER_KEY, "").strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def set_folder(path: str | Path) -> Path:
    p = Path(path)
    storage.set_setting(FOLDER_KEY, str(p))
    return p


def documents(root: Path, limit: int = 200) -> list[Path]:
    """Every readable document under *root*, newest first.

    Newest first matters: a folder with four hundred files gets truncated at
    ``limit``, and the recent letters describe the job the user wants now.
    """
    found: list[Path] = []
    for p in sorted(root.rglob("*")):
        if len(found) >= limit:
            break
        if not p.is_file() or p.suffix.lower() not in SUPPORTED:
            continue
        if p.name.startswith((".", "~$")):   # Office lock files
            continue
        found.append(p)
    found.sort(key=lambda f: _stat_mtime(f), reverse=True)
    return found


def _stat_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _stamp(p: Path) -> str:
    try:
        st = p.stat()
        return f"{int(st.st_mtime)}:{st.st_size}"
    except OSError:
        return ""


def _ledger() -> dict[str, str]:
    try:
        data = json.loads(storage.get_setting(LEDGER_KEY, "") or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _save_ledger(led: dict[str, str]) -> None:
    storage.set_setting(LEDGER_KEY, json.dumps(led)[:200_000])


def forget_folder() -> None:
    """Drop the record of what has been read, so the next pass re-reads it."""
    storage.set_setting(LEDGER_KEY, "")


# --- reading ------------------------------------------------------------------

def _chunks(text: str) -> Iterable[str]:
    text = text[:MAX_CHARS]
    for i in range(0, len(text), CHUNK):
        part = text[i:i + CHUNK]
        if part.strip():
            yield part


def extract_pairs(llm: LLM, name: str, text: str) -> list[tuple[str, str]]:
    """Ask the model for the reusable answers in one document."""
    out: list[tuple[str, str]] = []
    for part in _chunks(text):
        data = llm.complete_json(
            EXTRACT_PROMPT.format(name=name, text=part),
            system=EXTRACT_SYSTEM, max_tokens=2500)
        if not isinstance(data, dict):
            continue
        for item in data.get("pairs") or []:
            if not isinstance(item, dict):
                continue
            q = str(item.get("question", "")).strip()
            a = str(item.get("answer", "")).strip()
            if q and a:
                out.append((q, a))
    return out


def ingest(root: Path | None = None, *, force: bool = False,
           progress: Callable[[str, int, int], None] | None = None,
           cancelled: Callable[[], bool] | None = None) -> Ingested:
    """Read the folder and teach the answer bank what is in it.

    Safe to call repeatedly: unchanged documents are skipped against the
    ledger, so the usual cost of a re-run is zero requests.
    """
    result = Ingested()
    root = root or folder()
    if root is None:
        result.errors.append("No knowledge folder chosen.")
        return result

    settings = load_settings()
    if not settings.llm_enabled:
        result.errors.append("Sushi needs a model to read these documents.")
        return result
    llm = LLM(settings)

    led = {} if force else _ledger()
    files = documents(root)
    total = len(files)
    allow_demo = storage.get_setting("learn_demographics", "") == "1"

    for i, path in enumerate(files, 1):
        if cancelled and cancelled():
            break
        key = str(path)
        stamp = _stamp(path)
        if stamp and led.get(key) == stamp:
            result.skipped += 1
            continue
        if progress:
            progress(path.name, i, total)
        try:
            text = extract_text(path)
        except Exception as exc:                      # unreadable PDF, etc.
            log.info("knowledge: skipping %s (%s)", path.name, exc)
            result.skipped += 1
            result.errors.append(f"{path.name}: could not be read")
            continue
        if len(text.strip()) < 80:                    # a scanned image, a stub
            result.skipped += 1
            led[key] = stamp
            continue

        try:
            pairs = extract_pairs(llm, path.name, text)
        except LLMError as exc:
            log.warning("knowledge: %s failed (%s)", path.name, exc)
            result.errors.append(f"{path.name}: {exc}")
            continue

        result.files += 1
        result.pairs += len(pairs)
        for q, a in pairs:
            if not learning.learnable(q, a, include_demographics=allow_demo):
                continue
            if learning.remember(q, a, source="human"):
                result.learned += 1
        led[key] = stamp

    _save_ledger(led)
    log.info("knowledge: %s", result.summary)
    return result


def read_count() -> int:
    """How many documents have been read from the current folder."""
    return len(_ledger()) if folder() is not None else 0


def status() -> str:
    """One line for Settings and onboarding: what Sushi has read so far."""
    root = folder()
    if root is None:
        return "No folder yet. Point Sushi at your old cover letters and answers."
    read = len(_ledger())
    known = len(learning.bank())
    if not read:
        return f"{root}, not read yet."
    return (f"{root} · {read} document(s) read, "
            f"{known} answer(s) in the bank.")
