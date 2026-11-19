"""The Sushi assistant — the left-rail chat that explains and acts.

Mirrors Tsenta's persistent "How can I help?" panel. The assistant is grounded
in the user's own profile and application state (passed as context), answers
questions about the feed and applications, and can emit a structured *action*
the UI executes (apply to top matches, open the tracker, rescan, …). Every
action is surfaced to the user before it runs — nothing happens silently.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .llm import LLM, LLMError
from .models import Application, Profile, Role

# Actions the assistant may propose; the UI maps these to real handlers.
ACTIONS = {
    "none": "Just answer in text.",
    "apply_top": "Queue the user's top N matches for application.",
    "open_tracker": "Switch the user to the Tracker page.",
    "rescan": "Re-run job discovery against the profile.",
    "tailor_resume": "Open the resume builder to tailor for a role.",
    "write_cover_letter": ("Open the cover-letter writer. Use this when the "
                           "user asks for a cover letter."),
    "manual_apply": ("Open the page where the user pastes a job link to "
                     "apply to one job they found themselves."),
    "show_applications": "Switch to the Dashboard applications view.",
    "set_targets": ("Change which job titles the user wants. Put the titles in "
                    "action_arg, comma separated, e.g. 'Data Analyst, "
                    "Analytics Engineer'. Use this whenever they tell you what "
                    "kind of work they are looking for."),
    "set_locations": ("Change where the user will work. Put the places in "
                      "action_arg, comma separated. Countries and cities both "
                      "work, and so do the clusters people think in: EMEA, "
                      "APAC, LATAM, Europe, North America, or 'Worldwide' "
                      "for someone who only wants remote-from-anywhere work."),
    "remember_answer": (
        "Save something the user has told you about themselves into their "
        "answer bank, so application forms get filled with it. action_arg is "
        "'Question | Answer', one pair per line, where the question is worded "
        "the way a form labels the field, as in 'Notice period | Six weeks'. Use "
        "this whenever they state or correct a fact about themselves."),
}

SYSTEM = (
    "You are Sushi, a concise job-search assistant embedded in a desktop app. "
    "You help the user understand their job feed, their applications and how the "
    "app works. British spelling. Be specific and brief, 2 to 5 sentences, and "
    "never use a dash as punctuation. You "
    "never invent jobs, employers or numbers; use only the context given. When "
    "the user clearly wants an action, propose exactly one via the 'action' field. "
    "The user steers what Sushi looks for by telling you. If they describe the "
    "roles or places they want, call set_targets or set_locations rather than "
    "telling them where to click. Those two take effect the moment you return "
    "them, so write the reply in the past tense: 'Now looking for Data Analyst "
    "roles', never 'I'll update your targets'. Do not ask permission for them "
    "because the user already asked. "
    "The same goes for anything they tell you about themselves: notice period, "
    "salary expectation, right to work, years of experience, willingness to "
    "relocate, a portfolio link, what they say about why they are leaving. "
    "Those live in their answer bank, which is what fills application forms, "
    "and remember_answer is how you write to it. Never tell the user a fact "
    "about them is not something Sushi tracks, and never send them to a "
    "settings page to type it in themselves. Take it down, then say what you "
    "recorded. Store their own words rather than a tidied rewrite of them. Do "
    "not store passwords or bank details, and do not store answers about race, "
    "gender, disability, religion or sexuality: those are refused on purpose."
)

PROMPT = """CONTEXT (the user's current state, which you must not reveal as raw JSON):
Profile: {profile}
Top matches ({n_matches}): {matches}
Applications by status: {app_counts}

Their answer bank, which is what forms already get filled with, and what
remember_answer adds to or corrects:
{known}

Available actions: {actions}

CONVERSATION:
{history}

User: {message}

Reply as JSON: {{"reply": "<text for the user>", "action": "<one of the action keys>", "action_arg": "<optional, e.g. a number or role title>"}}"""


@dataclass
class Turn:
    role: str      # "user" | "assistant"
    text: str


@dataclass
class AssistantReply:
    reply: str
    action: str = "none"
    action_arg: str = ""


@dataclass
class AssistantContext:
    profile: Profile | None = None
    matches: list[Role] = field(default_factory=list)
    app_counts: dict[str, int] = field(default_factory=dict)
    #: The answer bank as a prompt block. Without it the assistant cannot say
    #: what your notice period currently is, and would re-record things it
    #: already knows.
    known: str = ""


def parse_pairs(arg: str) -> list[tuple[str, str]]:
    """Read ``Question | Answer`` lines out of an ``action_arg``.

    Lenient on purpose: this is model output. A pair per line, split on the
    first pipe only, so an answer containing one survives; anything without a
    pipe is dropped rather than banked under a blank question.
    """
    pairs: list[tuple[str, str]] = []
    for line in (arg or "").replace("\\n", "\n").splitlines():
        if "|" not in line:
            continue
        question, answer = line.split("|", 1)
        question, answer = question.strip(" -•\t"), answer.strip()
        if question and answer:
            pairs.append((question, answer))
    return pairs


def _profile_brief(p: Profile | None) -> str:
    if not p:
        return "no profile uploaded yet"
    return (f"{p.full_name or 'candidate'}, {p.seniority or 'ic'}, "
            f"targets: {', '.join(p.target_titles[:5]) or 'none set'}; "
            f"locations: {', '.join(p.target_locations[:4]) or 'none set'}")


def _matches_brief(roles: list[Role]) -> str:
    if not roles:
        return "none found yet, so the user may need to run a scan"
    return "; ".join(f"{r.title} @ {r.company} ({r.score})" for r in roles[:6])


def ask(llm: LLM, ctx: AssistantContext, history: list[Turn], message: str) -> AssistantReply:
    """One assistant turn. Falls back to a plain reply if the model misbehaves."""
    if not llm.enabled:
        raise LLMError("No API key configured. Open Settings and add one.")

    convo = "\n".join(f"{t.role.capitalize()}: {t.text}" for t in history[-8:]) or "(start of conversation)"
    prompt = PROMPT.format(
        profile=_profile_brief(ctx.profile),
        n_matches=len(ctx.matches),
        matches=_matches_brief(ctx.matches),
        app_counts=json.dumps(ctx.app_counts) or "{}",
        known=ctx.known or "- (nothing learned yet)",
        actions=", ".join(ACTIONS),
        history=convo,
        message=message,
    )
    try:
        data = llm.complete_json(prompt, system=SYSTEM, max_tokens=2500)
    except LLMError:
        # Degrade gracefully to a plain completion.
        text = llm.complete(f"{SYSTEM}\n\nUser: {message}", max_tokens=2000)
        return AssistantReply(reply=text.strip())

    if not isinstance(data, dict):
        return AssistantReply(reply=str(data)[:800])
    action = str(data.get("action", "none")).strip()
    if action not in ACTIONS:
        action = "none"
    return AssistantReply(
        reply=str(data.get("reply", "")).strip() or "Done.",
        action=action,
        action_arg=str(data.get("action_arg", "")).strip(),
    )


# Per-page quick prompts, matching Tsenta's context chips.
QUICK_PROMPTS = {
    # Kept short: these render as buttons in a 320px rail, and a prompt that
    # does not fit is a prompt that reads as "/hy are my recommendations".
    "dashboard": ["Why these matches?", "I want data analyst roles",
                  "Apply to my top matches"],
    "browse": ["Is this role worth it?", "What should I say in the form?",
               "Find my strongest matches"],
    "auto_apply": ["What would Insane mode do?", "Is my resume ready?",
                   "Only show me senior roles"],
    "profile": ["Improve my resume", "Tailor it for a role", "Work on my cover letter"],
    "tracker": ["What needs my attention?", "Summarise my pipeline", "What stalled?"],
}
