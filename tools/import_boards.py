"""Merge an external board-token corpus into Sushi's company list.

Sushi shipped with 1,078 readable boards, gathered by probing companies seen
in aggregator postings. That is a slow way to build a corpus and it caps what
the scanner can ever find: a company whose jobs never showed up in a feed was
invisible, however well it matched.

Several open projects publish their token lists outright. This merges one in.
Only platforms Sushi can already read are taken — a token for an ATS with no
reader is a row that can never produce a job — and nothing already present is
touched, so a probed record with a real name and a known role count is never
downgraded to a guess.

    python import_boards.py all_repos/job-forager/data      # merge
    python import_boards.py all_repos/job-forager/data --dry-run

Tokens are *claims*, not facts: a list like this always contains boards that
have since closed. They are written with ``probed: false``, and the scanner's
own error handling drops the dead ones on first read.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sushi.config import ensure_dirs  # noqa: E402
from sushi.discovery import COMPANIES_PATH, load_companies  # noqa: E402

#: file stem -> the ats name Sushi's reader uses. Anything not listed here is
#: skipped: Sushi has no reader for Workday or Personio yet, and importing
#: those tokens would add 7,000 rows that can never return a job.
PLATFORMS = {
    "greenhouse_companies": "greenhouse",
    "lever_companies": "lever",
    "ashby_companies": "ashby",
    "smartrecruiters_companies": "smartrecruiters",
    "workable_companies": "workable",
    "recruitee_companies": "recruitee",
}

#: A token that is only digits is an internal board id with no company behind
#: it; one with a space or a slash is a parsing accident upstream.
_BAD = re.compile(r"^\d+$|[\s/\\?#]")


def pretty(token: str) -> str:
    """A readable company name from a board token.

    "thyme-care" -> "Thyme Care". Wrong often enough to be worth replacing
    the moment the board is actually read, which is why these are written
    unprobed.
    """
    words = re.split(r"[-_]+", token)
    out = []
    for w in words:
        if not w:
            continue
        out.append(w if w.isupper() and len(w) <= 4 else w.capitalize())
    return " ".join(out) or token


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    dry = "--dry-run" in sys.argv
    if not src.is_dir():
        print(f"not a directory: {src}")
        return 1

    ensure_dirs()
    companies = load_companies()
    have = {(c.get("ats"), c.get("token")) for c in companies.values()
            if c.get("ats") and c.get("token")}
    before_boards = len(have)
    print(f"corpus: {len(companies)} companies, {before_boards} readable boards")

    added = 0
    per_platform: dict[str, int] = {}
    for path in sorted(src.glob("*_companies.json")):
        ats = PLATFORMS.get(path.stem)
        if not ats:
            print(f"  skip {path.name}: no reader for this platform")
            continue
        try:
            tokens = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"  skip {path.name}: {exc}")
            continue
        if not isinstance(tokens, list):
            print(f"  skip {path.name}: not a list")
            continue

        new = 0
        for token in tokens:
            token = str(token).strip()
            if not token or _BAD.search(token):
                continue
            if (ats, token) in have:
                continue
            name = pretty(token)
            key = name.lower()
            # Never overwrite a company we already know something real about.
            if key in companies and companies[key].get("token"):
                continue
            companies[key] = {
                "key": key,
                "name": name,
                "sources": [f"corpus:{src.name}"],
                "ats": ats,
                "token": token,
                "locations": [],
                "probed": False,
            }
            have.add((ats, token))
            new += 1
        per_platform[ats] = new
        added += new
        print(f"  {path.name:34} {len(tokens):>6} tokens -> {new:>6} new")

    print(f"\n{added} new boards; {before_boards} -> {before_boards + added}")
    if dry:
        print("dry run — nothing written")
        return 0

    tmp = COMPANIES_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for rec in companies.values():
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(COMPANIES_PATH)
    print(f"written: {COMPANIES_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
