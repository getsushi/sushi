"""Does an answer given on one form come back on the next one's wording?

The bank matched on words. "Do you have the legal right to work in the U.K.?"
and "Please confirm your Right to Work status in the location where this job
is advertised" share five words out of fifteen — a 0.43 similarity against a
0.82 threshold — so the second employer asked again, which is exactly the
work the bank exists to stop.

So each question is also filed under the *thing it is asking about*, and the
lookup falls back to that when the wording misses. This checks both halves:
the paraphrase is recalled, and the questions that merely sound alike stay
apart — filing "do you need sponsorship" under "do you have the right to
work" would type the opposite of the truth into a legal declaration.

Runs against a throwaway data directory, so it never touches your bank.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Must be set before sushi.config computes DATA_DIR at import time.
_TMP = Path(tempfile.mkdtemp(prefix="sushi-qa-topics-"))
os.environ["LOCALAPPDATA"] = str(_TMP)
os.environ["XDG_DATA_HOME"] = str(_TMP)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

from sushi import directfill, learning, models, storage  # noqa: E402

#: Asked here, answered; asked there, in other words, and expected back.
PARAPHRASES: list[tuple[str, str, str]] = [
    ("Do you have the legal right to work in the U.K.?", "Yes",
     "Please confirm your Right to Work status in the location where this "
     "job is advertised."),
    ("Are you legally authorised to work in the United States?", "Yes",
     "Do you have the right to work in the US without restriction?"),
    ("What is your notice period?", "One month",
     "How much notice are you required to serve with your current employer?"),
    ("How soon would you be able to start if offered this position?",
     "Four weeks from offer",
     "What is your earliest start date?"),
    ("What is highest level of education you have completed?", "Master's",
     "Please select your highest degree."),
    ("How did you hear about C3 AI?", "LinkedIn",
     "Where did you hear about this role?"),
]

#: Pairs that must *not* share an answer, whatever they have in common.
APART: list[tuple[str, str]] = [
    ("Do you have the legal right to work in the U.K.?",
     "Do you require visa sponsorship?"),
    ("Are you legally authorised to work in the United States?",
     "If temporary, please provide the expiration date of your current "
     "work authorization."),
    ("What is your current salary?", "What are your salary expectations?"),
    # Employer-specific: the answer is about *them*, so it is not a topic at
    # all and must never be carried across.
    ("Why do you want to work at Stripe?", "Why do you want to work here?"),
]


#: What the page hands over, and the question underneath it. The first is
#: real: Greenhouse's select points ``aria-labelledby`` at the control's own
#: value as well as the label, so the chosen answer arrives glued to the
#: front of the question.
LABELS: list[tuple[str, str]] = [
    ("us Are you a US Person?*", "Are you a US Person?"),
    ("uk Right to Work status in the UK*", "Right to Work status in the UK"),
    ("What is your notice period? (required)", "What is your notice period?"),
    # Left alone: a repeated short word that is simply how the sentence
    # starts, not the widget talking.
    ("I confirm I am over 18", "I confirm I am over 18"),
    ("Do you do shift work?", "Do you do shift work?"),
]

#: A form whose tick-boxes are declarations rather than questions.
CONSENT_FORM: list[dict] = [
    {"key": "privacy", "type": "checkbox", "value": "",
     "label": "I have read and agree to the Privacy Notice *"},
    {"key": "over18", "type": "checkbox", "value": "",
     "label": "I confirm I am over 18"},
    {"key": "name", "type": "text", "value": "", "label": "Full name*"},
    {"key": "usp", "type": "text", "value": "", "label": "us Are you a US Person?*"},
]


def main() -> int:
    storage.conn()  # builds the schema in the throwaway directory
    bad = 0

    print("paraphrase recall")
    for asked, answer, rephrased in PARAPHRASES:
        learning.remember(asked, answer, source="human")
        got = learning.recall(rephrased)
        ok = got == answer
        bad += not ok
        print(f"  {'ok ' if ok else 'FAIL'}  {rephrased[:58]:58} -> {got!r}")

    print("\nkept apart")
    for first, second in APART:
        got = learning.recall(second)
        ok = not got
        bad += not ok
        print(f"  {'ok ' if ok else 'FAIL'}  {second[:58]:58} -> {got!r}")

    print("\ntopic classification")
    for asked, _, rephrased in PARAPHRASES:
        a, b = learning.topic_of(asked), learning.topic_of(rephrased)
        ok = bool(a) and a == b
        bad += not ok
        print(f"  {'ok ' if ok else 'FAIL'}  {a or '(none)':18} {b or '(none)':18}"
              f" {rephrased[:40]}")

    print("\nlabels read back cleanly")
    for raw, want in LABELS:
        got = directfill.tidy_label(raw)
        ok = got == want
        bad += not ok
        print(f"  {'ok ' if ok else 'FAIL'}  {raw[:44]:44} -> {got!r}")

    print("\ndeclarations are not questions")
    plan = directfill.plan_fill(CONSENT_FORM, models.Profile(
        full_name="Rowan Hartley", email="r@example.com"))
    for want_consent in ("I have read and agree to the Privacy Notice",
                         "I confirm I am over 18"):
        ok = want_consent in plan.consents and want_consent not in plan.unanswered
        bad += not ok
        print(f"  {'ok ' if ok else 'FAIL'}  {want_consent[:58]}")
    ok = not any("Privacy" in f.label for f in plan.fills)
    bad += not ok
    print(f"  {'ok ' if ok else 'FAIL'}  nothing agreed to on your behalf")

    # Precedence: a guess must never displace what you said yourself.
    learning.remember("What is your notice period?", "Two weeks", source="agent")
    kept = learning.recall("How much notice do you have to give?")
    ok = kept == "One month"
    bad += not ok
    print(f"\nhuman outranks agent\n  {'ok ' if ok else 'FAIL'}  {kept!r}")

    print(f"\n{'all good' if not bad else str(bad) + ' failure(s)'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
