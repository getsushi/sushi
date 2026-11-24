"""Fill application forms with a browser agent.

This replaces driving a coding agent at the terminal. browser-use runs a real
Chromium under an LLM that can see the page, so it copes with the fact that
every ATS lays its form out differently.

Two rules are enforced here rather than left to the model:

1. It never presses Submit unless the user has explicitly turned that on. The
   default is to fill everything and stop, so a human looks before it is sent.
2. Personal data is passed as browser-use ``sensitive_data`` placeholders, so
   values are substituted into the page without being written into the model's
   context or its trace.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import branding, bubble, learning
from .config import BROWSER_PROFILE_DIR, Settings, ensure_dirs
from .models import Profile, Role

log = logging.getLogger(__name__)


class AutofillError(RuntimeError):
    pass


@dataclass
class FillResult:
    ok: bool
    submitted: bool
    message: str
    unanswered: list[str]        # questions the agent could not answer
    screenshot: str = ""
    left_open: bool = False      # the browser was left open for manual completion
    #: question → answer for everything Explorative mode guessed. Kept apart
    #: from the filled count on purpose: a guess is work the user still has
    #: to check, and a result that folds it into "14 fields filled" hides
    #: exactly the thing they need to look at.
    guessed: list[tuple[str, str]] = field(default_factory=list)
    #: Why nothing was composed, when nothing was: "off", "no-model", or the
    #: error the model came back with. Empty when the generative step ran.
    #:
    #: Three different reasons used to produce one identical message —
    #: "18 question(s) need you" — with nothing to tell them apart. The
    #: feature was switched off and read as broken, which is a fair reading
    #: of a program that says the same thing whatever happened.
    compose_skipped: str = ""
    #: Questions the model already saw on this form and could not answer.
    #: The step-by-step agent is the same model, so handing it these costs
    #: minutes and comes back with the same blanks.
    declined: list[str] = field(default_factory=list)
    #: The browser tab this pass ran in, so a second pass can carry on in it.
    #: A form is held in the tab, not on the server: load the page again and
    #: the upload is undone and every typed field is empty. Without this the
    #: agent asked for "a tab for this job", got a brand new one, and started
    #: the form from nothing beside the one that was already half filled.
    tab: str = ""


# --- persistent browser loop ------------------------------------------------
# Applies run on one long-lived event loop in a daemon thread rather than via
# asyncio.run(). That lets a session be *kept open* after the agent stops: when
# the form could not be completed we leave the real browser window on screen so
# the user can finish it by hand, instead of tearing Chromium down with the loop.

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()
_open_sessions: list[Any] = []   # sessions deliberately left alive for the user
_watchers: list[Any] = []        # form watchers still learning from those sessions


def _browser_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        _loop = asyncio.new_event_loop()
        threading.Thread(target=_loop.run_forever, name="sushi-browser-loop",
                         daemon=True).start()
        return _loop


def _run_on_loop(coro, timeout: float | None = None):
    fut = asyncio.run_coroutine_threadsafe(coro, _browser_loop())
    return fut.result(timeout)


#: The watcher reading each open form, by the tab it is reading. So a
#: "learn from this form" press on a page can be answered by the watcher
#: that already knows how to read *that* page, rather than by whichever one
#: happens to be first in the list.
_watch_by_tab: dict[str, Any] = {}


def _spawn_watch(session: Any, watcher: Any, tab: str | None = None) -> None:
    """Start the learning loop as a task on the browser loop.

    ``tab`` also puts the Learn button on that page and keeps it answered:
    the polling loop learns what settles, and the button is for the user who
    has finished correcting and wants it kept now, rather than on whatever
    schedule the poll happens to be on.
    """
    # The form is the user's now. From here, closing the last tab means
    # "I'm done", not "recover" (see _no_blank_tabs).
    try:
        object.__setattr__(session, "_sushi_handed_over", True)
    except Exception:
        pass
    try:
        asyncio.run_coroutine_threadsafe(_watch_form(session, watcher), _browser_loop())
    except Exception:
        pass
    if not tab:
        return
    _watch_by_tab[tab] = watcher
    try:
        asyncio.run_coroutine_threadsafe(
            _watch_bubble(session, tab, accept=("learn",)), _browser_loop())
    except Exception:
        pass


def open_browsers() -> int:
    """How many browsers are still up with filled forms in them.

    One per *browser*, not per application: applications share a browser and
    take a tab each, because Chromium allows one process per user-data
    directory. So this is the number of windows a "close them" control has
    to account for, which is normally one however long the run was.
    """
    return len(_open_sessions)


def close_open_sessions() -> None:
    """Close any browsers left open for manual completion (call on app exit).

    Watchers are flushed before the browsers go: someone who quits Sushi with a
    half-finished form open still typed those answers, and they are exactly the
    ones worth keeping.
    """
    watchers, _watchers[:] = list(_watchers), []
    for w in watchers:
        try:
            _run_on_loop(w.flush(), timeout=5)
        except Exception:
            pass
        w.stop()
    sessions, _open_sessions[:] = list(_open_sessions), []
    for s in sessions:
        try:
            _run_on_loop(s.kill(), timeout=10)
        except Exception:
            pass


#: Chromium processes Sushi started and this session owns. Anything else on
#: our profile directory belongs to a run that has already exited.
_own_pids: set[int] = set()


def _profile_processes() -> list[Any]:
    """Every live process whose command line names Sushi's browser profile.

    Identified by the profile directory rather than by name, which is what
    makes this safe: the user's own Chrome has a different ``user-data-dir``
    and can never match, however many copies of it are running.
    """
    try:
        import psutil
    except ImportError:
        return []

    needle = str(BROWSER_PROFILE_DIR).lower()
    found = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "chrom" not in name:
                continue
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()
            if needle in cmdline:
                found.append(proc)
        except Exception:
            continue      # the process ended while we were looking at it
    return found


def orphaned_browsers() -> list[int]:
    """PIDs of browsers on our profile that no live session owns."""
    return [p.pid for p in _profile_processes() if p.pid not in _own_pids]


def close_orphaned_browsers() -> int:
    """Kill leftover browsers from earlier runs. Returns how many.

    Chromium allows one process per ``user_data_dir``. Sushi leaves each
    filled form open on purpose, so quitting the app — or crashing — strands
    a browser holding that directory. They accumulate: sixty-three of them
    after a few days of testing, and from the second one onwards *every*
    application failed to start with "Browser process exited before CDP
    became available", which names the symptom and not the cause.

    Run at startup, when by definition nothing in this session owns one.
    """
    import contextlib

    victims = [p for p in _profile_processes() if p.pid not in _own_pids]
    if not victims:
        return 0
    log.info("closing %d leftover browser process(es) from a previous run",
             len(victims))
    for proc in victims:
        with contextlib.suppress(Exception):
            proc.terminate()
    gone = 0
    try:
        import psutil
        alive = psutil.wait_procs(victims, timeout=5)[1]
        gone = len(victims) - len(alive)
        for proc in alive:
            with contextlib.suppress(Exception):
                proc.kill()     # it ignored the polite request
                gone += 1
    except Exception:
        gone = len(victims)

    # Chromium leaves this behind when it dies without tidying up, and it
    # alone is enough to stop the next launch.
    with contextlib.suppress(OSError):
        (BROWSER_PROFILE_DIR / "SingletonLock").unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        (BROWSER_PROFILE_DIR / "lockfile").unlink(missing_ok=True)
    return gone


def learn_demographics() -> bool:
    """Whether EEO/demographic answers may be banked. Off unless asked for.

    See ``learning`` for why this is not the default: a durable local record of
    someone's protected characteristics, replayed into the next form on their
    behalf, is not something to start doing quietly.
    """
    from .storage import get_setting
    return get_setting("learn_demographics", "0") == "1"


def learning_summary() -> dict[str, int]:
    """What the watchers have picked up this session, for the UI."""
    return {
        "watchers": len(_watchers),
        "learned": sum(getattr(w.stats, "learned", 0) for w in _watchers),
    }


#: How long one agent step's model call may take, and the step around it.
#: browser-use sizes this from the *model name*: anything with "claude" or
#: "sonnet" in it gets 90 seconds, which is a figure for an API. The Claude
#: Code backend is not one — it shells out to the CLI, and a single step of
#: a Greenhouse form measured 110 seconds. So every call timed out at 90s
#: and was retried, six times, while the app said "Filling" and the form sat
#: perfectly still. Kept under ``claude_code.TIMEOUT`` so browser-use gives
#: up on a call before the subprocess behind it is killed.
CLI_LLM_TIMEOUT = 240
CLI_STEP_TIMEOUT = 420


def _agent_timeouts(s: Settings) -> dict[str, int]:
    """Timeouts for the agent, where the default is wrong for the backend."""
    if s.provider not in ("claude_code", "codex", "opencode"):
        return {}
    return {"llm_timeout": CLI_LLM_TIMEOUT, "step_timeout": CLI_STEP_TIMEOUT}


def _chat_model(s: Settings) -> Any:
    """browser-use's own LLM wrapper, chosen to match the user's provider."""
    try:
        from browser_use import ChatAnthropic, ChatOpenAI
    except ImportError as e:
        raise AutofillError("browser-use is not installed. Run: pip install browser-use") from e

    if s.provider in ("claude_code", "codex", "opencode"):
        # browser-use speaks the OpenAI protocol and nothing else, so the
        # CLI gets fronted by a loopback server that does. Started here
        # rather than at launch: it costs nothing until used.
        from .cli_agents import runtime_for
        rt = runtime_for(s.provider)
        return ChatOpenAI(model=s.model or rt.best_model(),
                          api_key=rt.PLACEHOLDER_KEY,
                          base_url=rt.ensure_running(),
                          temperature=None)

    if s.provider == "anthropic":
        os.environ.setdefault("ANTHROPIC_API_KEY", s.api_key)
        return ChatAnthropic(model=s.model or "claude-sonnet-5")

    os.environ.setdefault("OPENAI_API_KEY", s.api_key)
    model = s.model or "gpt-4o"
    # Many newer models (gpt-5.x/gpt-6, o-series) reject a custom `temperature`
    # and require `max_completion_tokens`. browser-use only drops temperature for
    # models it recognises as "reasoning"; a custom Azure deployment name won't
    # match, so we (a) send no temperature at all and (b) flag it as reasoning so
    # browser-use picks the compatible request shape.
    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": s.api_key,
        "temperature": None,
        "reasoning_models": [model],
    }
    if s.openai_base_url:  # OpenAI-compatible endpoint (e.g. Azure /openai/v1)
        kwargs["base_url"] = s.openai_base_url
    try:
        return ChatOpenAI(**kwargs)
    except TypeError:
        # Older browser-use ChatOpenAI without some kwargs — degrade gracefully.
        for drop in ("reasoning_models", "base_url", "temperature"):
            kwargs.pop(drop, None)
            try:
                return ChatOpenAI(**kwargs)
            except TypeError:
                continue
        return ChatOpenAI(model=model)


#: Models that can actually look at a screenshot. Anything else must be sent
#: text only — and the failure when it is not is unhelpfully indirect: Groq
#: answers "messages[1].content must be a string", because browser-use sends
#: the page image as a list of content parts and a text-only model's API
#: will not accept a list at all. Every step then fails identically and the
#: form is left untouched.
_VISION_HINTS = ("gpt-4o", "gpt-4.1", "gpt-5", "gpt-6", "o3", "o4",
                 "claude", "gemini", "llama-4", "scout", "maverick",
                 "qwen2.5vl", "qwen3-vl", "pixtral", "internvl")


#: How much of the page's interactive content goes to the model each step.
#: browser-use defaults to 40,000 characters — roughly 10,000 tokens, which
#: is the single largest line item in the cost of an application and, on a
#: free tier granting 8,000 tokens a minute, more than the entire budget for
#: one step. Trimmed to a third: an application form is a list of labelled
#: inputs, and the parts of a page that overflow this are navigation,
#: footers and cookie banners.
MAX_PAGE_CHARS = 12_000
#: How many previous steps travel with each request. The whole history is
#: re-sent every step, so this multiplies.
MAX_HISTORY = 6
#: The attributes that matter for filling a form. Everything else — styling
#: hooks, analytics ids, framework bookkeeping — is per-element noise paid
#: for on every step.
FORM_ATTRIBUTES = [
    "id", "name", "type", "placeholder", "value", "aria-label",
    "aria-labelledby", "for", "role", "title", "href", "alt", "required",
]


def supports_vision(model: str) -> bool:
    """Whether this model can be shown the page as well as told about it."""
    name = (model or "").lower()
    # Explicitly text-only families, even where the name looks modern.
    if "gpt-oss" in name or "llama-3" in name or "mixtral" in name:
        return False
    # Claude Code can certainly see images; its headless mode takes a prompt
    # on stdin and has nowhere to put one. The model is a Claude, so the
    # "claude" hint below would otherwise say yes and every step would fail.
    if "claude-code" in name:
        return False
    return any(h in name for h in _VISION_HINTS)


def _sensitive(profile: Profile) -> dict[str, str]:
    """Values the agent may type but should not see.

    browser-use substitutes <secret>name</secret> placeholders at the point of
    typing, so the model reasons about "the email field" without the address
    itself entering the prompt or the saved trace.
    """
    return {k: v for k, v in profile.form_answers().items() if v}


#: Where application forms live. Personal data is only typed on these and on
#: the job's own site, never on a page the agent wanders to.
_ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
              "smartrecruiters.com", "recruitee.com", "myworkdayjobs.com",
              "workday.com", "personio.de", "personio.com", "teamtailor.com",
              "bamboohr.com", "breezy.hr", "pinpointhq.com", "rippling.com",
              "jobvite.com", "icims.com")


def _scoped_sensitive(profile: Profile, url: str) -> dict:
    """``_sensitive``, restricted to the job's site and the known ATS hosts."""
    from urllib.parse import urlparse

    data = _sensitive(profile)
    if not data:
        return data
    host = (urlparse(url or "").hostname or "").lower()
    parts = host.split(".")
    hosts = set(_ATS_HOSTS)
    if len(parts) >= 2:
        hosts.add(".".join(parts[-2:]))
    scoped = {}
    for h in hosts:
        scoped[f"https://{h}"] = data
        scoped[f"https://*.{h}"] = data
    return scoped


#: How hard the agent may push the candidate's experience toward the job in
#: free-text answers. This is Settings → Apply settings → Resume optimisation,
#: which for a long time was saved, drawn as a selected chip, named in the
#: apply confirmation dialog — and read by nothing at all.
_RESUME_RULES = {
    "off": ("Describe the candidate exactly as their CV does. Do not reframe "
            "or re-emphasise anything to suit this job."),
    "honest": ("Lead with the parts of their real experience this job asks "
               "for, and use the job's own vocabulary for them where it "
               "genuinely matches. Never claim experience they do not have."),
    "aggressive": ("Argue hard for them: open every free-text answer with "
                   "the strongest relevant thing they have done, mirror the "
                   "job description's language closely, and frame adjacent "
                   "experience as directly relevant where it defensibly is. "
                   "Even here, never state a fact that is not true: no "
                   "invented employers, titles, dates, tools or numbers."),
}


def resume_rule(mode: str) -> str:
    """The instruction for one optimisation level."""
    return _RESUME_RULES.get(mode, _RESUME_RULES["honest"])


#: Rule 5 of the agent's task, in its two settings. The cautious one is the
#: default and says so plainly; the explorative one widens what the agent may
#: answer but not what it may touch, and still forbids money, immigration
#: status and demographics — the three places a confident wrong answer does
#: real damage.
_GUESS_RULES = {
    False: (
        "For a question you genuinely cannot answer from the information "
        "above, especially anything about salary, notice period, visa "
        "status, or a demographic question, leave it blank and record it in "
        "your final answer under \"UNANSWERED:\". Do not guess."),
    True: (
        "EXPLORATIVE MODE IS ON. Answer every question you can, including "
        "ones the information above does not settle: give the most likely "
        "answer for a real person with this history, and list each one in "
        "your final answer under \"GUESSED:\" as \"question = answer\". Even "
        "here: never invent an employer, a job title, a qualification, a "
        "date or a number that contradicts the material. Salary: a realistic "
        "figure for this seniority in the candidate's country, or the "
        "advertised range. Right to work and sponsorship: work it out from "
        "the candidate's country (location, phone code) against the job's "
        "country. Notice period: one month unless stated. Leave only "
        "demographic questions blank under \"UNANSWERED:\". Never "
        "pick an option claiming a clearance, polygraph, licence, "
        "certification or degree the material does not show: choose No, "
        "None or N/A. \"Have you worked for this company before?\" is No "
        "unless the CV lists that company. A dropdown that already shows an answer is done: do "
        "not open it again."),
}

#: How the agent gets to the form. Two quite different situations, and the
#: wrong instruction in the second one undoes a whole pass of work: the form
#: is already open and part-filled, and a form lives in the tab — navigating
#: to the same URL again hands back an empty one with the upload undone.
_OPEN_RULES = {
    False: ("Open the URL and find the application form. If there is an "
            "\"Apply\" button, click it."),
    True: ("The form is ALREADY OPEN in the tab you are looking at, and it "
           "is already part-filled: Sushi has attached the CV and typed "
           "everything it had an answer for. Do NOT navigate, do NOT "
           "reload, and do NOT use go_to_url at all — this form keeps "
           "nothing if the page is loaded again, so you would be throwing "
           "away the upload and every field that is already in, and the "
           "candidate would be left with an empty form. You have not been "
           "given the address, for that reason. Start from the page as you "
           "find it and fill only what is still blank, leaving the rest "
           "exactly as it is."),
}


def _task(role: Role, profile: Profile, cv_path: str, letter_path: str,
          autosubmit: bool, resume_mode: str = "honest",
          explorative: bool = False, resuming: bool = False) -> str:
    known = "\n".join(f"- {k}: <secret>{k}</secret>" for k in _sensitive(profile))
    # The bank, human-corrected answers first — those are the ones the user has
    # actually looked at, and the only ones worth spending prompt budget on.
    prior = learning.prompt_block(40)
    about = (
        f"{profile.headline or profile.current_title}. "
        f"{profile.summary[:600]}"
    ).strip()

    submit_rule = (
        "When every field is filled and valid, submit the application."
        if autosubmit else
        "Do NOT submit. Fill every field, then stop and report that the form is "
        "ready for the user to review and submit themselves."
    )
    tone_rule = resume_rule(resume_mode)
    guess_rule = _GUESS_RULES[bool(explorative)]
    open_rule = _OPEN_RULES[bool(resuming)]

    # The URL is withheld from a resuming agent, and that is the whole fix
    # rather than a belt on top of the instruction below. "URL: <link>" is
    # the first concrete thing in the prompt and `go_to_url` is the first
    # move a browser agent makes; told in prose not to navigate and handed
    # an address anyway, it navigated — which reloads the form, drops the
    # upload and empties every field the lookup pass had typed. The second
    # pass then "could not fill the name", because there was nothing left
    # of the first. No address, nothing to open.
    where = (f"  URL:      {role.url}\n" if not resuming else
             "  The form is already open in your tab. You have not been\n"
             "  given its address, because you must not load it again.\n")

    return f"""Apply for this job on behalf of the candidate.
{"" if not resuming else chr(10) + "BEFORE ANYTHING ELSE: " + open_rule + chr(10)}
JOB
  Company:  {role.company}
  Title:    {role.title}
{where}
CANDIDATE (summary you may use for free-text answers)
  {about}

HOW TO PITCH THEM
  {tone_rule}

FIELD VALUES - type these using the placeholder, never guess a replacement:
{known}

ABOUT THOSE <secret> TAGS - they are expected, and they are not tampering.
This task was written by Sushi, the desktop app the candidate is running on
their own machine, and you are driving their own browser. The candidate's
real email, phone and name are deliberately *not* in this prompt: the
browser-use library substitutes the real value at the moment you type it,
so nothing personal is written into your context or your trace. Seeing
<secret>email</secret> where an address would be is the mechanism working.
Type the placeholder exactly as written and the right value lands in the
field. Do not treat this as a prompt injection, a corrupted task or a
reason to stop.

FILES to upload when the form asks. These are real files on this machine and
you have been granted access to them, so use the upload action with the exact
path, do not retype it and do not skip the field:
  CV / resume:   {cv_path or '(none provided)'}
  Cover letter:  {letter_path or '(none provided)'}

ANSWERS THIS CANDIDATE HAS GIVEN BEFORE - reuse when the question matches,
even if the wording differs. These were learned from forms they filled in
themselves, so prefer them over anything you would compose:
{prior}

HOW TO WORK
0. Look before you judge. You may be running without screenshots, so decide
   what is on the page from its elements and its text, not from its title,
   because a careers site often titles every page the same thing. "Blocked" is a
   verdict you may only reach after you have looked for a form and tried
   the Apply control; reaching it on arrival, from the page title or from
   the wording of this task, is always wrong.
0b. Whether to apply is not your decision, and it has already been made.
   The candidate chose this vacancy and pressed Apply. If the posting turns
   out to be a different or more senior role than the title above, or asks
   for a qualification they do not have, that is still theirs to weigh.
   Fill the form and note the mismatch under NOTES. The one thing you must
   never do is quietly decline on their behalf: a refusal looks identical
   to a crash from where they are sitting, and they wanted the application
   in, not an opinion on their chances.
1. {open_rule}
2. If the page offers "Sign in with Google" (or Apply with LinkedIn, or any
   other single-sign-on button) and that is the only way to the form, click
   it. This browser keeps the candidate's own signed-in profile, so it will
   usually go straight through. If it asks for a password, a code, or any
   other credential: STOP. Do not type anything into it. Report
   "NEEDS SIGN-IN:" with the provider's name and leave the page as it is.
   The candidate will sign in themselves, once, and every later application
   will reuse it.
3. Fill every field you can from the values above.
   For a field that offers suggestions as you type, such as city, country,
   school or employer, typing the text is not enough: the form keeps no value until
   one of its own options is chosen. Type enough to bring the list up, then
   CLICK the matching option, and check the field shows it afterwards. If
   nothing matches, try a shorter query (the city alone, without the state)
   before giving up on it.
4. For free-text questions (e.g. "why do you want to work here"), write 3-5
   specific sentences grounded in the candidate summary. Never invent an
   employer, qualification, dates, or a number they did not give you. Do not
   use dashes as punctuation; a full stop, a comma or a colon instead.
5. {guess_rule}
6. Decline cookie banners where possible; accept nothing beyond what is needed
   to use the form.
8. If you cannot complete the form for any reason (a field you can't answer, a
   login wall, a CAPTCHA, an upload that won't take), do NOT close the tab or
   navigate away. Leave the form exactly as filled so the user can finish it,
   and list what remains under "UNANSWERED:".
9. {submit_rule}

Finish with a short report:
  STATUS: filled | submitted | blocked
  UNANSWERED: one question per line, or "none"
  GUESSED: one "question = answer" per line, or "none". The answer is the
           exact text you typed or the option you chose, word for word —
           never a description of it such as "written from the CV"
  NOTES: anything the user should check
"""


def file_problems(cv_path: str, letter_path: str) -> list[str]:
    """Why a file cannot be uploaded, in words fit to show a user.

    A refusal that only reaches the log is indistinguishable from a feature
    that does not work: the CV was set, the path existed, the file was zero
    bytes, and the application went out without it while the UI said eight
    fields were filled and all was well.
    """
    out: list[str] = []
    for name, raw in (("CV", cv_path), ("cover letter", letter_path)):
        if not raw:
            continue
        path = Path(raw)
        if not path.exists():
            out.append(f"Your {name} is set to a file that is not there any "
                       f"more ({path.name}). Pick it again in Profile.")
        elif path.stat().st_size < 64:
            out.append(f"Your {name} file is empty ({path.name}, 0 bytes). "
                       f"Nothing was attached. Replace it in Profile.")
    return out


def _uploadable(cv_path: str, letter_path: str) -> dict[str, str]:
    """The files that really exist, as absolute paths the uploader accepts.

    Two things were going wrong at once. A path was handed to the agent
    whether or not anything was at the other end of it — so a cover letter
    that failed to render still appeared in the task as a file to attach.
    And the paths were passed through as typed: "D:/a/b.pdf" is valid to
    Python but is not what a Windows file chooser compares against, so even
    a real file could miss.
    """
    out: dict[str, str] = {}
    for name, raw in (("cv", cv_path), ("letter", letter_path)):
        if not raw:
            continue
        try:
            path = Path(raw).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            log.warning("apply: %s path does not exist, not offering it: %s",
                        name, raw)
            continue
        if path.stat().st_size < 64:
            log.warning("apply: %s file is empty, not offering it: %s", name, path)
            continue
        out[name] = str(path)
    return out


async def _release(session: Any, reused: bool) -> None:
    """Finish with a browser: close the tab if shared, the window if not.

    Killing a shared session would take every other filled form down with
    it — including ones the user had not looked at yet.
    """
    if reused:
        # close_page takes the page (or its target id) — calling it bare
        # raised a TypeError that this very ``except`` swallowed, so every
        # tab Sushi opened stayed open for the rest of the session.
        target = getattr(session, "_sushi_target", None)
        if target:
            try:
                await session.close_page(target)
            except Exception as exc:
                log.info("apply: could not close the tab: %s", exc)
            session._sushi_target = None
        return
    try:
        await session.kill()
    except Exception:
        pass
    try:
        _open_sessions.remove(session)
    except ValueError:
        pass


async def _reusable_session():
    """A browser already on screen that can take another tab, if there is one.

    Chromium allows exactly one process per ``user_data_dir`` — and that
    directory is the whole point here, because it is what keeps the user
    signed in to Greenhouse and Workday between applications. Sushi also
    leaves each filled form open on purpose, for review.

    Those two facts collide. Launching a second browser while the first is
    still up fails with "Browser process exited before CDP became available",
    which meant the *second* application of every Insane mode run died before
    it started, and any apply after a sign-in did too.

    So applications share one browser and take a tab each. That is also the
    shape the feature wants: a row of filled forms to look through, rather
    than a row of windows.
    """
    for session in list(_open_sessions):
        try:
            # Any CDP round-trip proves it is still alive.
            await session.get_or_create_cdp_session(focus=False)
            return session
        except Exception:
            # It has gone away; stop holding a reference to it.
            try:
                _open_sessions.remove(session)
            except ValueError:
                pass
            # And stop it: a dead session left running kept its watchdogs
            # retrying the closed browser once a minute for the whole run.
            try:
                asyncio.ensure_future(session.kill())
            except Exception:
                pass
    return None


async def _close_blank_tabs(session: Any) -> int:
    """Close any tab of ours still sitting on nothing. Returns how many.

    Blank tabs arrive from more than one place: a tab opened for an
    application that then failed before navigating, a tab browser-use's
    session manager creates when it loses focus and recovers, and the new
    tab page Chromium opens with the profile. None of them is a filled form,
    and the browser is a place the user comes back to in order to look
    through filled forms.
    """
    try:
        cdp = await _cdp(session, focus=False)
        infos = await cdp.cdp_client.send.Target.getTargets()
    except Exception:
        return 0
    keep = getattr(session, "_sushi_target", None)
    closed = 0
    for info in infos.get("targetInfos", []):
        if info.get("type") != "page":
            continue
        tid, url = info.get("targetId"), (info.get("url") or "")
        if not tid or tid == keep:
            continue
        if url and not url.startswith(("about:blank", "chrome://newtab")):
            continue
        try:
            await cdp.cdp_client.send.Target.closeTarget({"targetId": tid})
            closed += 1
        except Exception:
            pass
    if closed:
        log.info("apply: closed %d empty tab(s)", closed)
    return closed


async def _session_for_apply(settings: Settings, url: str = "", *,
                             continue_in: str = "") -> tuple[Any, bool]:
    """The browser to fill this application in, and whether it was reused.

    The new tab's target id is recorded on the session, because every CDP
    call after this has to address *that* tab rather than whichever one the
    browser happens to be focused on.

    And it is focused as well as recorded. Recording it only steers Sushi's
    own CDP calls; the agent picks its own target, and ``new_page`` does not
    move it — so the agent carried on in the previous application's tab and
    the one opened for this job sat on about:blank until the user closed it.
    One stray blank tab per application, in a browser whose whole point is
    that you come back to it and look through the forms.

    ``continue_in`` names a tab a previous pass of *this same application*
    left a part-filled form in, and asks for that tab back rather than a new
    one. A second pass is the normal shape of Fill mode "auto" — the lookup
    fills what it knows, the agent is handed what is left — and a new tab
    threw all of the first pass away: the agent arrived at an empty copy of
    the form, re-uploaded the CV, retyped the name and the email, and left
    the half-filled original open beside it saying "review and submit".
    """
    existing = await _reusable_session()
    if existing is not None:
        # A new application on the same browser: it is Sushi's again.
        try:
            object.__setattr__(existing, "_sushi_handed_over", False)
        except Exception:
            pass
    if existing is None:
        session = _make_session(settings)
        session._sushi_target = None
        return session, False
    if continue_in:
        try:
            # Focused as well as adopted, for the reason in the docstring
            # above: the agent follows the browser's focus, not our record
            # of it, so a tab that is only recorded is a tab it will not use.
            await existing.get_or_create_cdp_session(target_id=continue_in,
                                                     focus=True)
            existing._sushi_target = continue_in
            return existing, True
        except Exception as exc:
            # Closed by hand between the two passes. A fresh tab is a worse
            # outcome than carrying on, not a broken one, so fall through.
            log.info("apply: the tab to carry on in has gone (%s); "
                     "opening a new one", exc)
    try:
        # Born at the job, not at about:blank and navigated afterwards. The
        # gap between the two is where a stray tab comes from: anything that
        # fails in between — and an ATS redirect chain gives it plenty of
        # chances — leaves an empty tab nobody opened on purpose.
        page = await existing.new_page(url) if url else await existing.new_page()
        target = getattr(page, "_target_id", None)
        existing._sushi_target = target
        if target:
            await existing.get_or_create_cdp_session(target_id=target,
                                                     focus=True)
        await _close_blank_tabs(existing)
    except Exception as exc:
        log.info("apply: could not open a tab (%s); using the current one", exc)
        existing._sushi_target = None
    return existing, True


def _make_session(settings: Settings):
    from browser_use import BrowserProfile
    try:
        from browser_use import BrowserSession
    except ImportError:
        from browser_use.browser.session import BrowserSession

    ensure_dirs()
    browser_profile = BrowserProfile(
        headless=settings.headless,
        # A persistent profile keeps the user logged into Greenhouse/Workday
        # between applications, which is most of the friction in applying.
        user_data_dir=str(BROWSER_PROFILE_DIR),
        downloads_path=str(BROWSER_PROFILE_DIR / "downloads"),
        # Do not tear the browser down when the agent stops — we decide whether
        # to close it (submitted) or leave it open (needs the user to finish).
        keep_alive=True,
        # browser-use's default makes Chrome show a yellow "unsupported
        # command-line flag" bar over every job page. Sushi does not need it.
        ignore_default_args=["--extensions-on-chrome-urls"],
    )
    session = BrowserSession(browser_profile=browser_profile)
    _no_blank_tabs(session)
    return session


def _no_blank_tabs(session: Any) -> None:
    """Stop browser-use refilling the window with about:blank tabs.

    When the tab it was focused on closes and none are left, browser-use
    "recovers" by opening a blank tab, and again each time that one is
    closed. After a form has been handed over, that is the user closing the
    browser: let it close instead.
    """
    original = getattr(session, "_cdp_create_new_page", None)
    if original is None:
        return

    async def guarded(url: str = "about:blank", background: bool = False,
                      new_window: bool = False) -> str:
        if url == "about:blank" and getattr(session, "_sushi_handed_over", False):
            try:
                pages = session.session_manager.get_all_page_targets()
            except Exception:
                pages = []
            if not pages:
                if session in _open_sessions:
                    _open_sessions.remove(session)
                asyncio.ensure_future(session.kill())
                raise RuntimeError("the user closed the browser")
        return await original(url, background, new_window)

    try:
        object.__setattr__(session, "_cdp_create_new_page", guarded)
    except Exception as exc:
        log.info("apply: could not guard against blank tabs (%s)", exc)


# --- reading and branding the live page -------------------------------------
#
# Both go through CDP's Runtime.evaluate on whichever target the session has
# focus on. browser-use has reworked its page API more than once, so this is
# kept to the one call that has been stable across those versions, and every
# failure degrades to "no branding, no learning" rather than a broken apply.

async def _cdp(session: Any, focus: bool = False, target: str | None = None):
    """The CDP session for the tab this application owns.

    ``get_or_create_cdp_session()`` with no target id returns whatever the
    session is focused on — and ``new_page()`` creates a tab *without*
    focusing it. So every application opened a fresh tab, left it on
    about:blank, and then navigated and filled the **first** tab instead: nine
    blank tabs and one page doing all the work. The tab has to be named.
    """
    target = target or getattr(session, "_sushi_target", None)
    if target:
        try:
            return await session.get_or_create_cdp_session(target_id=target,
                                                           focus=focus)
        except Exception:
            # The tab has been closed by hand; fall back rather than fail.
            pass
    return await session.get_or_create_cdp_session(focus=focus)


async def _adopt_current_tab(session: Any) -> str:
    """Write down which tab this application is working in. Returns its id.

    A *reused* browser is handed a tab of its own by ``new_page`` and the id
    comes back with it. A fresh one is not: the window opens with a tab
    already in it, that tab is the one the application uses, and nothing was
    writing its id down.

    Which made ``FillResult.tab`` empty for the first application of every
    run — and an empty tab id is indistinguishable from "no tab to carry on
    in", so the second pass opened a new one and started the form again
    beside the one the lookup had just half-filled. The whole handover
    worked only from the second application onwards, which is the one nobody
    watches.
    """
    known = getattr(session, "_sushi_target", None)
    if known:
        return known
    tid = ""
    try:
        cdp = await _cdp(session, focus=True)
        info = await cdp.cdp_client.send.Target.getTargetInfo(
            session_id=cdp.session_id)
        tid = ((info or {}).get("targetInfo") or {}).get("targetId", "") or ""
    except Exception as exc:
        log.info("apply: could not identify this tab from its session (%s)", exc)
    if not tid:
        # A fresh browser has exactly one page in it, so there is no
        # ambiguity to resolve — and if there is more than one, guessing
        # would be worse than carrying on without an id.
        try:
            cdp = await _cdp(session, focus=False)
            infos = await cdp.cdp_client.send.Target.getTargets()
            pages = [i.get("targetId", "") for i in infos.get("targetInfos", [])
                     if i.get("type") == "page" and i.get("targetId")]
            if len(pages) == 1:
                tid = pages[0]
        except Exception:
            pass
    session._sushi_target = tid or None
    if tid:
        log.info("apply: working in tab %s", tid[:12])
    return tid


#: How long one script may take to come back from the page.
#:
#: There was no limit at all, and a renderer that stops answering is not a
#: rare event on an ATS: Greenhouse parses an uploaded CV on the main
#: thread, and ``Runtime.evaluate`` with ``awaitPromise`` against a page in
#: that state simply never returns. The await had nothing bounding it, so
#: the whole run stopped there — no exception, no log line, no further
#: step, and a pill still reading "Attaching …" six minutes later with an
#: untouched form underneath it. Measured on exactly that form.
#:
#: Twenty seconds is far longer than any script here needs — the slowest,
#: reading a whole form, is tens of milliseconds — so anything that reaches
#: this is a page that has stopped talking rather than one thinking hard.
_EVAL_TIMEOUT = 20.0


class PageBusy(RuntimeError):
    """The page did not answer in time. Recoverable: it is one script."""


async def _evaluate(session: Any, script: str, target: str | None = None, *,
                    timeout: float | None = None) -> str:
    # Read at call time, not bound as a default: a default argument is
    # evaluated when the function is defined, so the constant above would
    # be a copy nothing could change — including a test trying to prove
    # this very timeout fires.
    timeout = _EVAL_TIMEOUT if timeout is None else timeout
    try:
        cdp = await asyncio.wait_for(
            _cdp(session, focus=False, target=target), timeout)
        result = await asyncio.wait_for(
            cdp.cdp_client.send.Runtime.evaluate(
                {"expression": script, "returnByValue": True,
                 "awaitPromise": True},
                session_id=cdp.session_id,
            ), timeout)
    except asyncio.TimeoutError as exc:
        raise PageBusy(
            f"the page did not answer within {timeout:g}s") from exc
    if "exceptionDetails" in result:
        raise RuntimeError(str(result["exceptionDetails"])[:300])
    return result.get("result", {}).get("value") or ""


async def _mark(session: Any, keys: list[str], state: str) -> None:
    """Put the amber on a set of fields, or take it off. Never fatal.

    One evaluate for the whole set, and swallowed: a ring round a text box
    is not worth losing an application over.
    """
    from . import directfill

    if not keys:
        return
    try:
        await _evaluate(session, directfill.marks_script(keys, state))
    except Exception as exc:                            # noqa: BLE001
        log.info("apply: could not mark %d field(s) (%s)", len(keys), exc)


async def _read_or_empty(session: Any, script: str,
                         target: str | None = None) -> str:
    """Read the page, and treat a page that will not answer as no answer.

    For the reads that are an improvement rather than the job — re-reading
    a form after an upload, looking for what a dropdown revealed. Losing
    one of those costs a round of filling; raising out of one costs the
    application.
    """
    try:
        return await _evaluate(session, script, target)
    except Exception as exc:                            # noqa: BLE001
        log.info("apply: could not read the page (%s)", exc)
        return ""


async def _brand(session: Any, state: str, hot: bool = True,
                 target: str | None = None) -> None:
    """Put Sushi's mark and its pill on *this tab*, now and after it navigates.

    Two scripts, one pill. ``branding`` stamps the tab — favicon and title —
    and the Fill button carries the state, because a run is exactly when
    somebody wants to know what Sushi is doing and the alternative was two
    pills in the same corner. The button knows it is mid-run, so it reports
    rather than offering to start another one.

    Per tab, and that word is the whole of a bug that ran for a while.
    ``addScriptToEvaluateOnNewDocument`` is registered against one page
    target, not against the browser — and ``session._cdp_add_init_script``
    asks for a CDP session without naming a target, so it lands on whichever
    tab happens to be focused. Branding was therefore done only for a
    *fresh* browser, on the one tab it opens with, on the belief that a
    reused browser's tabs would inherit it. They do not. So in Insane mode
    the first application had a title, a favicon and a pill, and every
    application after it ran in a tab with none of the three: no way to tell
    which window Sushi was working in, and no progress anywhere on screen.

    ``runImmediately`` covers the document already loaded; the registration
    covers the navigations an ATS does on its way from a posting to a form.
    Both are needed, and both are safe to repeat — the pill returns early if
    it is already there, and the badge script is written the same way.
    """
    scripts = (branding.init_script(state, hot),
               bubble.script(always=True, label=state))
    target = target or getattr(session, "_sushi_target", None)
    try:
        cdp = await _cdp(session, focus=False, target=target)
        for script in scripts:
            await cdp.cdp_client.send.Page.addScriptToEvaluateOnNewDocument(
                params={"source": script, "runImmediately": True},
                session_id=cdp.session_id)
        return
    except Exception as exc:
        log.info("apply: could not register page scripts on tab %s (%s)",
                 (target or "?")[:12], exc)
    # The session's own helper, which uses whatever tab is focused. Worse,
    # but better than a tab with no sign of Sushi on it at all.
    for script in scripts:
        try:
            await session._cdp_add_init_script(script)
        except Exception as exc:
            log.info("apply: could not register a page script (%s)", exc)


async def _set_badge(session: Any, text: str, hot: bool = False) -> None:
    """Say what is happening, on whichever pill the page has."""
    for script in (branding.set_state_script(text, hot),
                   bubble.set_state_script("busy", text)):
        try:
            await _evaluate(session, script)
        except Exception:
            pass


#: What each browser-use action is, in the words a user would use. Anything
#: not named here falls back to its own name with the underscores taken out,
#: which is usually close enough: "switch_tab" reads as "switch tab".
_ACTION_WORDS = {
    "input_text": "typing an answer",
    "send_keys": "typing",
    "click_element_by_index": "clicking",
    "upload_file": "attaching a file",
    "upload_file_to_element": "attaching a file",
    "select_dropdown_option": "choosing from a list",
    "get_dropdown_options": "reading a list",
    "scroll": "scrolling",
    "scroll_to_text": "looking for a field",
    "go_to_url": "opening the page",
    "wait": "waiting for the page",
    "extract_structured_data": "reading the page",
    "switch_tab": "switching tab",
    "done": "finishing up",
}


def _clip(text: str, limit: int) -> str:
    """Shorten to fit, without cutting a word in half."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return (cut or text[:limit]) + "…"


def _step_note(agent: Any) -> str:
    """What the agent is doing, in its own words.

    This used to be ``f"step {n}"``. A counter is not progress: "step 2" over
    a form with eleven questions left on it says only that the thing has not
    crashed, which is the one fact the user can already see.

    browser-use writes a ``next_goal`` for each step it takes — a sentence
    like "Fill in the Location (City) field and pick the suggestion" — and
    that is the sentence worth showing. Where there is none, the action's own
    name is still better than a number. Nothing here can leak: the model is
    given placeholders rather than the real email and phone, so a goal it
    writes about them names the placeholder.

    Every lookup is defensive. browser-use has reshaped its history objects
    more than once, and an application must not fail because the narration
    could not be read.
    """
    history = getattr(getattr(agent, "history", None), "history", None) or []
    n = len(history)
    goal = ""
    try:
        out = getattr(history[-1], "model_output", None) if history else None
        goal = (getattr(out, "next_goal", "") or "").strip()
    except Exception:
        goal = ""
    if not goal:
        try:
            name = next(iter(agent.history.last_action() or {}), "")
            goal = _ACTION_WORDS.get(name, name.replace("_", " "))
        except Exception:
            goal = ""
    goal = " ".join(goal.split())
    return f"Step {n} · {goal}" if goal else f"Step {n}"


def _echo_to_page(session: Any, on_step: Callable[[str], None] | None):
    """Wrap a step callback so the page's pill says it too.

    Fire and forget: the pill is a courtesy, and a browser that has been
    closed mid-run must not take the application down with it. The original
    callback is always called first, so the app's own status line is never
    waiting on a CDP round trip.
    """
    def echo(text: str) -> None:
        if on_step:
            try:
                on_step(text)
            except Exception:
                pass
        if not text:
            return
        try:
            asyncio.ensure_future(
                _bubble_state(session, "busy", _clip(text, 70),
                              getattr(session, "_sushi_target", None)))
        except Exception:
            pass
    return echo


#: Whether the page is an application's confirmation. Read as text, because
#: every ATS words it differently and none of them marks it in the markup.
_SUBMITTED_JS = r"""
(() => {
  const t = (document.body ? document.body.innerText : '').slice(0, 4000).toLowerCase();
  const u = location.href.toLowerCase();
  // "Thank you for your interest" is on half the job pages there are, so
  // only wording that says an application went in counts, and only once the
  // form itself is gone from the page.
  const said = /thank(s| you) for (applying|your application|submitting)|application (has been |was )?(submitted|received|sent)|we('ve| have) received your application|your application is (in|complete)|successfully (applied|submitted)/.test(t);
  const vis = n => !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length);
  const form = [...document.querySelectorAll('input[type=email], input[type=file], input[name*="first" i], textarea')].some(vis);
  // Or the address says so. Lever lands on /thanks, others on
  // /confirmation or /success, sometimes with wording the check above does
  // not know — or inside a frame it cannot read.
  const sentUrl = /\/(thanks|thank-you|thankyou|confirmation|confirm|success|submitted|application-received|applied)(\/|\?|#|$)/.test(u);
  return ((said || sentUrl) && !form) ? 'yes' : 'no';
})()
"""

#: Called with the job URL when the user submits a form by hand. The app
#: registers here to refresh what it shows.
on_user_submitted: list[Callable[[str], None]] = []


def _mark_sent(url: str) -> None:
    from . import storage
    try:
        row = storage.conn().execute(
            "SELECT id FROM applications WHERE url=?", (url,)).fetchone()
        if row:
            storage.set_status(row["id"], "submitted", "Submitted by you")
            storage.set_questions(row["id"], [])
        log.info("apply: the form for %s was submitted by hand", url)
    except Exception:                                   # noqa: BLE001
        log.debug("could not record a hand submission", exc_info=True)
    for cb in list(on_user_submitted):
        try:
            cb(url)
        except Exception:                               # noqa: BLE001
            pass


_LEARN_ASKED_JS = "(window.__sushiLearnRequest ? 'yes' : 'no')"


async def _watch_form(session: Any, watcher: "learning.FormWatcher") -> None:
    """Keep reading the open form until the browser or the app goes away.

    This is the "learns as you go" loop. It outlives the agent on purpose: the
    most valuable answers are the ones the user types *after* the agent has
    stopped and handed them the form.
    """
    interval = learning.seconds_between_polls()
    idle_errors = 0
    while not watcher.stopped:
        await asyncio.sleep(interval)
        if watcher.stopped:
            break
        # Sent by hand? The form was the user's to finish, and a job they
        # submitted themselves kept saying "Needs you" for ever.
        if getattr(watcher, "role_url", "") and not getattr(watcher, "_sent", False):
            try:
                if (await watcher._evaluate(_SUBMITTED_JS)) == "yes":
                    watcher._sent = True
                    _mark_sent(watcher.role_url)
            except Exception:
                pass
        before = watcher.stats.last_error
        await watcher.poll()
        # "Save my changes to Sushi", pressed in the page's checklist.
        try:
            if await watcher._evaluate(_LEARN_ASKED_JS) == "yes":
                await watcher.poll()
                n = await watcher.flush()
                await watcher._evaluate(
                    f"window.__sushiLearnRequest = 0; window.__sushiLearnDone = {int(n)};"
                    f"window.__sushiEdited = new Set();"
                    f"window.__sushiChecklist && window.__sushiChecklist(); 'ok'")
                log.info("learning: saved %d answer(s) on request", n)
        except Exception:                               # noqa: BLE001
            pass
        # A closed browser raises on every poll; stop rather than spin.
        if watcher.stats.last_error and watcher.stats.last_error != before:
            idle_errors += 1
            if idle_errors >= 3:
                break
        else:
            idle_errors = 0
    try:
        await watcher.flush()
    except Exception:
        pass


async def _run(role: Role, profile: Profile, settings: Settings, cv_path: str,
               letter_path: str, on_step: Callable[[str], None] | None,
               resume_mode: str = "honest",
               explorative: bool = False,
               continue_in: str = "",
               should_stop: Callable[[], bool] | None = None) -> FillResult:
    from browser_use import Agent

    session, reused = await _session_for_apply(settings, role.url,
                                               continue_in=continue_in)
    # Whether we actually got the part-filled tab, rather than whether we
    # asked for it: it may have been closed by hand in between, and telling
    # the agent "the form is already open" when it is looking at a fresh tab
    # would leave it waiting on a page it has been forbidden to load.
    resuming = bool(continue_in) and getattr(session, "_sushi_target",
                                             None) == continue_in
    # Init scripts only apply to pages opened afterwards, so a reused browser
    # gets its badge set directly on the tab instead.
    label = (f"Filling the rest · {role.company}" if resuming
             else f"Applying to {role.company}")
    if reused:
        # Branded as well as badged. A reused browser means a *new tab*, and
        # a new tab has none of Sushi's scripts in it — see _brand. Setting
        # the badge alone was writing to a pill that had never been built,
        # so every application after the first ran unmarked.
        await _brand(session, label, hot=True)
        await _set_badge(session, label, hot=True)
    else:
        # Started here rather than left to ``Agent.run()``: an init script
        # cannot be registered before the browser is up, and branding a
        # session that had not started was throwing an AssertionError into
        # the guard inside ``_brand``. A session that is already started is
        # what the agent is handed on every reuse anyway.
        try:
            await session.start()
        except Exception as exc:
            log.info("apply: could not pre-start the browser (%s)", exc)
        # Named before branded, so _brand registers against this tab rather
        # than whichever one the session happens to be looking at.
        await _adopt_current_tab(session)
        await _brand(session, label, hot=True)

    # Naming a file in the task is not enough to upload it. browser-use will
    # only attach paths it has been given up front in ``available_file_paths``
    # — anything else is refused by the upload action itself, which the agent
    # then reports as "supplied file could not be accessed or attached". The
    # CV and the cover letter were both written, both existed on disk, and
    # every application was going out without either of them.
    # The agent's steps on the page too, for the same reason as the direct
    # path: a run nobody can see the state of looks like a stopped one.
    on_step = _echo_to_page(session, on_step)

    uploads = _uploadable(cv_path, letter_path)
    vision = supports_vision(settings.model)
    log.info("apply: %d file(s) offered to the agent: %s | vision=%s (%s)",
             len(uploads), ", ".join(Path(p).name for p in uploads) or "none",
             vision, settings.model)

    agent = Agent(
        task=_task(role, profile, uploads.get("cv", ""),
                   uploads.get("letter", ""), settings.autosubmit, resume_mode,
                   explorative, resuming=resuming),
        llm=_chat_model(settings),
        browser_session=session,
        sensitive_data=_scoped_sensitive(profile, getattr(role, "url", "")),
        available_file_paths=list(uploads.values()),
        use_vision=vision,
        max_clickable_elements_length=MAX_PAGE_CHARS,
        max_history_items=MAX_HISTORY,
        include_attributes=FORM_ATTRIBUTES,
        # The judge is a second model call after every run that grades the
        # trace (~7s, ~$0.10) and changes nothing Sushi does with the result.
        use_judge=False,
        **_agent_timeouts(settings),
    )

    learned_total = 0

    def note_learned(n: int) -> None:
        nonlocal learned_total
        learned_total += n
        if on_step:
            on_step(f"Learned {learned_total} answer(s) from this form.")

    # Bound to this application's tab, not to the session. A watcher keeps
    # polling after the form is left open for review, and the next
    # application retargets the session — so a session-bound watcher would
    # quietly start reading somebody else's form and bank its answers.
    _tab = getattr(session, "_sushi_target", None)
    watcher = learning.FormWatcher(
        lambda js, t=_tab: _evaluate(session, js, target=t),
        include_demographics=learn_demographics(),
        on_learn=note_learned,
    )
    _watchers.append(watcher)
    watcher.role_url = getattr(role, "url", "")

    async def step_end(a: Any) -> None:
        # Between steps is the only safe place to stop: the agent is not
        # mid-click, the page is settled, and whatever it has typed stays
        # typed. Agent.stop() sets a flag its own loop reads, so this ends
        # the run at the top of the next step rather than tearing anything
        # down underneath it.
        if should_stop and should_stop():
            log.info("apply: stopping the agent at the user's request")
            try:
                a.stop()
            except Exception as exc:              # noqa: BLE001
                log.info("apply: the agent would not stop (%s)", exc)
        note = _step_note(a)
        # Logged as well as shown. A run that goes wrong is read back from
        # the log afterwards, and "step 7" told nobody anything there either.
        log.info("apply: %s", note)
        if on_step:
            on_step(note)

    async def leave_open(reason: str, result: FillResult) -> FillResult:
        """Keep the browser on screen so the user can finish the form by hand.

        This is also where the learning loop starts in earnest: from here on
        every edit the user makes is a correction worth keeping, and none of
        them would survive if the watcher stopped with the agent.
        """
        # One entry per browser, not per application. A shared session fills
        # many forms in many tabs, and registering it each time would mean
        # close_open_sessions tried to kill the same browser repeatedly.
        if session not in _open_sessions:
            _open_sessions.append(session)
        result.left_open = True
        await _set_badge(session, "Review and submit · Sushi is learning your edits")
        _spawn_watch(session, watcher, _tab)
        if on_step:
            on_step(reason)
        return result

    try:
        # Always wired, not only when there is somewhere to report progress:
        # this is also the hook that notices a stop has been asked for.
        history = await agent.run(max_steps=60, on_step_end=step_end)
    except Exception as e:
        # The agent failed partway. Leave whatever it managed to fill on screen.
        res = FillResult(ok=False, submitted=False,
                         message=f"{type(e).__name__}: {e}", unanswered=[])
        if settings.headless:
            # nothing to look at; don't strand an invisible process
            await _release(session, reused)
            raise AutofillError(f"{type(e).__name__}: {e}") from e
        return await leave_open(
            "The agent hit a problem. The browser is left open so you can finish it.", res)

    # Asked to stop. Before the report is read, because the report is the
    # agent's account of a task it did not finish — and before the submit
    # branch below, which is the one thing that must not happen after
    # somebody has pressed stop. What it typed stays on screen for them.
    if should_stop and should_stop():
        await _learn_agent_pass(session, watcher)
        return await leave_open(
            "Stopped. The browser is open with what Sushi had filled so far.",
            FillResult(ok=True, submitted=False,
                       message="Stopped before this one was finished. The "
                               "form is open in the browser with what had "
                               "been filled; nothing was submitted.",
                       unanswered=[], tab=_tab or ""))

    # Did the agent actually get to run? If the model refused every step the
    # form is untouched, and reporting "the browser has been left open so you
    # can review what it filled" over an empty form is worse than saying
    # nothing — it sends the user to look at work that was never done.
    blocked = _credentials_failed(history)
    if blocked:
        watcher.stop()
        await _release(session, reused)
        raise AutofillError(blocked)

    final = (history.final_result() or "") if hasattr(history, "final_result") else ""
    result = _parse_report(final, autosubmit=settings.autosubmit,
                           errored=bool(getattr(history, "has_errors", lambda: False)()))

    # Whatever happens next, bank what the agent managed to fill — read the
    # form once here, while the page is still up. These land as "agent" values,
    # so a later human correction overwrites them.
    await _learn_agent_pass(session, watcher)

    # Close the browser only when the application was actually submitted.
    # Otherwise (fill-and-stop, needs review, or a failure) leave it open.
    if result.submitted:
        watcher.stop()
        await _release(session, reused)
        return result
    if settings.headless:
        watcher.stop()
        await _release(session, reused)
        return result
    return await leave_open(
        "Form is ready in the browser. Review and submit it yourself when happy.", result)


async def _learn_agent_pass(session: Any, watcher: "learning.FormWatcher") -> None:
    """Snapshot the filled form as agent-sourced answers.

    Two polls back to back: the watcher only learns a value that survived one
    poll, and here there is no user mid-keystroke to protect against.

    And the same snapshot becomes the watcher's baseline, because the very
    next thing that happens is ``leave_open`` starting the polling loop on
    this form — and without it that loop found every answer the agent had
    just invented still sitting there, decided the user had tolerated it,
    and re-banked it as ``human``. Nobody had looked at the page yet. See
    ``FormWatcher.set_baseline``.
    """
    try:
        await watcher.poll()
        snapshot = {}
        for key, (value, question) in list(watcher._pending.items()):
            learning.remember(question, value, source="agent")
            snapshot[key] = value
        watcher.set_baseline(snapshot)
    except Exception:
        pass


#: The token-per-minute ceiling in a rate-limit message, if it names one.
#: The trailing group has to end on a digit — "Requested 12043," otherwise
#: reads back as "12043," and the sentence says "about 12043, tokens".
_TPM_LIMIT = re.compile(r"limit\s+(\d[\d,]*\d|\d)", re.I)
_TPM_WANTED = re.compile(r"requested\s+(\d[\d,]*\d|\d)", re.I)


def _agent_failure(history: Any) -> str:
    """Why every step failed, in words the user can act on, or "".

    browser-use swallows a model error into the step's result and carries on
    to the next step, which fails identically. Six steps later it stops with
    a history full of the same error and no actions taken — and the caller
    cannot tell that from a form it genuinely could not fill. What the user
    saw was an empty browser and "No report returned."

    Three causes are worth naming, because each has a different fix and the
    user cannot guess which one they have hit.
    """
    try:
        errors = [str(e) for e in history.errors() if e]
    except Exception:                                   # noqa: BLE001
        return ""
    if not errors:
        return ""

    def dominant(predicate) -> str:
        hits = [e for e in errors if predicate(e)]
        return hits[0] if len(hits) >= max(2, len(errors) // 2) else ""

    from .llm import explain

    auth = dominant(lambda e: "401" in e or "invalid subscription key" in e.lower()
                    or "authenticationerror" in e.lower())
    if auth:
        return explain(RuntimeError(auth))

    # The one that actually bites. browser-use re-sends the page and its own
    # system prompt every step, which is ten to twelve thousand tokens before
    # the form is even read — more than a free tier allows in a *minute*. So
    # every step 413s, the agent stops after six, and nothing was ever typed.
    rate = dominant(lambda e: "rate_limit" in e.lower() or "413" in e
                    or "too large" in e.lower()
                    or "tokens per minute" in e.lower())
    if rate:
        limit = _TPM_LIMIT.search(rate)
        wanted = _TPM_WANTED.search(rate)
        size = (f" Each of its steps needs about {wanted.group(1)} tokens and "
                f"your plan allows {limit.group(1)} a minute."
                if limit and wanted else "")
        return ("The browser agent could not take a single step: your model "
                "plan is too small for it." + size +
                " The agent re-sends the whole page and its own instructions "
                "on every step, so there is no form small enough to fix this. "
                "Use Fill mode “auto” instead. It reads the form and types "
                "what it knows for a fraction of that, and turns Explorative "
                "on for the rest. Settings → Apply settings → Fill mode.")

    context = dominant(lambda e: "context length" in e.lower()
                       or "maximum context" in e.lower())
    if context:
        return ("The page was too big for this model's context window. Try a "
                "model with a larger one, or Fill mode “auto”, which never "
                "sends the whole page.")
    return ""


#: Kept under the old name: qa suites and the apply path both import it.
def _credentials_failed(history: Any) -> str:
    return _agent_failure(history)


def _parse_report(text: str, *, autosubmit: bool, errored: bool) -> FillResult:
    status = ""
    if m := re.search(r"STATUS:\s*(\w+)", text, re.I):
        status = m.group(1).lower()

    unanswered: list[str] = []
    if m := re.search(r"UNANSWERED:\s*(.+?)(?:\n\s*(?:GUESSED|NOTES):|\Z)",
                      text, re.I | re.S):
        for line in m.group(1).splitlines():
            q = line.strip(" -•\t")
            if q and q.lower() not in {"none", "n/a", "-"}:
                unanswered.append(q)

    # Explorative mode's half of the report. Parsed even when the mode is
    # off: a model that volunteers a guess it was told not to make is
    # exactly the thing the user needs shown back to them, not dropped
    # because the section was unexpected.
    guessed: list[tuple[str, str]] = []
    if m := re.search(r"GUESSED:\s*(.+?)(?:\n\s*(?:UNANSWERED|NOTES):|\Z)",
                      text, re.I | re.S):
        for line in m.group(1).splitlines():
            row = line.strip(" -•\t")
            if not row or row.lower() in {"none", "n/a", "-"}:
                continue
            question, _, answer = row.partition("=")
            guessed.append((question.strip(), answer.strip()))

    submitted = status == "submitted" and autosubmit
    ok = status in {"filled", "submitted"} and not errored
    return FillResult(ok=ok, submitted=submitted,
                      message=text.strip()[:2000] or "No report returned.",
                      unanswered=unanswered, guessed=guessed)


async def _goto(session: Any, url: str) -> None:
    """Point this application's tab at a URL."""
    cdp = await _cdp(session, focus=True)
    try:
        await cdp.cdp_client.send.Page.navigate({"url": url},
                                                session_id=cdp.session_id)
    except Exception:
        await _evaluate(session, f"location.href = {url!r}")
    await asyncio.sleep(2.5)       # let the form render before reading it


#: What a closed posting says about itself. Boards are consistent about this
#: — the wording is nearly always one of these, in the page body or its title.
_CLOSED = re.compile(
    r"no longer (?:accept|available|open|taking)|"
    r"(?:position|job|role|vacancy|posting) (?:is )?(?:closed|filled|expired)|"
    r"applications (?:are )?closed|closed to applications|"
    r"this (?:job|role|posting) (?:has been|was) removed|"
    r"(?:job|page) not found|404", re.I)


#: Finds the one link on an advert that leads to the actual form. Returns
#: the href to navigate to, or "" — never clicks, because a click on an
#: aggregator is as likely to open a signup modal as the employer's form.
_APPLY_JS = r"""
(() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  // One line, always: a JavaScript regex literal cannot be wrapped.
  // SmartRecruiters labels its apply control "I'm interested". That is
  // 35,000 of the jobs on file, and it matched nothing here — so every
  // one of them reported "no application form was found".  A board gets
  // to call its own button whatever it likes; this list has to follow.
  const WANT = /^(apply\b|submit application|view job|see job|go to job|i'?m interested|i am interested|interested\b|start (your )?application)/i;
  const here = location.hostname.replace(/^www\./, '');
  const seen = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const text = clean(a.innerText || a.getAttribute('aria-label') || '');
    if (!text || text.length > 40) continue;
    if (!WANT.test(text.replace(/\s+/g, ' '))) continue;
    const href = a.href || '';
    if (!/^https?:/i.test(href)) continue;
    let host = '';
    try { host = new URL(href).hostname.replace(/^www\./, ''); } catch (e) { continue; }
    // Off-site first: on an aggregator the employer's form is elsewhere, and
    // a same-site "Apply" is usually the site's own signup wall.
    seen.push({href: href, offsite: host !== here});
  }
  const offsite = seen.find(s => s.offsite);
  const chosen = offsite || seen[0];
  return JSON.stringify(chosen ? chosen.href : '');
})()
"""


#: The hosts that are an application form rather than a careers website.
#: A company careers page that embeds one of these in an iframe is the
#: common case and was the one Sushi could not do anything with at all.
_ATS_HOSTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
    "smartrecruiters.com", "teamtailor.com", "recruitee.com",
    "personio.de", "personio.com", "myworkdayjobs.com", "icims.com",
    "jobvite.com", "breezy.hr", "pinpointhq.com", "applytojob.com",
    "bamboohr.com", "rippling.com", "jazz.co", "workforcenow.adp.com",
)

#: An application form living in an iframe. Every page that embeds one of
#: these — a company careers site with a Greenhouse form dropped into it —
#: looked to Sushi like a page with no form at all, because the reader only
#: ever ran in the top document. Ocado's careers site is one; the form is a
#: cross-origin Greenhouse embed with thirty-five fields in it.
#:
#: The answer is to go *to* the embed rather than reach into it. These URLs
#: are real pages that work on their own, and once the tab is on one,
#: everything downstream — reading, filling, clicking a dropdown at a
#: coordinate — works unchanged.
_EMBED_JS = r"""
((hosts) => {
  const out = [];
  for (const f of document.querySelectorAll('iframe[src]')) {
    const src = f.src || '';
    if (!/^https?:/i.test(src)) continue;
    let host = '';
    try { host = new URL(src).hostname.replace(/^www\./, ''); } catch (e) { continue; }
    if (!hosts.some(h => host === h || host.endsWith('.' + h))) continue;
    const r = f.getBoundingClientRect();
    out.push({src: src, area: Math.round(r.width * r.height)});
  }
  // The biggest one: a tracking pixel is also an iframe.
  out.sort((a, b) => b.area - a.area);
  return JSON.stringify(out.length ? out[0].src : '');
})(HOSTS_JSON)
"""

#: Where to click the Apply control, link or not.
#:
#: Ocado's is a ``<button>`` with no href, which a link search can never
#: see. SmartRecruiters' is an ``<a>`` whose href, copied and navigated
#: to, lands on "Oops, you've gone too far" — their application app wants
#: the click, not the address. So both are found here and both are
#: *clicked*, which is what a person does and what every board is built
#: to expect; the href is kept only as a fallback.
_APPLY_BUTTON_JS = r"""
(() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const WANT = /^(apply|apply now|apply for this job|apply for this role|start application|start your application|i'?m interested|i am interested|interested)$/i;
  const vis = el => {
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 8 && r.height > 8;
  };
  const nodes = [...document.querySelectorAll(
    'button, [role="button"], input[type="button"], input[type="submit"], a')];
  for (const el of nodes) {
    const text = clean(el.innerText || el.value || el.getAttribute('aria-label'));
    if (!WANT.test(text)) continue;
    if (!vis(el)) continue;
    try { el.scrollIntoView({block: 'center', behavior: 'instant'}); } catch (e) { }
    const r = el.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight) continue;
    return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2,
                           text: text, href: el.href || ''});
  }
  return '';
})()
"""


async def _embedded_form(session: Any) -> str:
    """The src of an ATS form embedded in this page, or ""."""
    try:
        raw = await _evaluate(
            session, _EMBED_JS.replace("HOSTS_JSON", json.dumps(list(_ATS_HOSTS))))
        src = json.loads(raw) if raw else ""
    except Exception:                                   # noqa: BLE001
        return ""
    if src and _GH_BOARD_EMBED.search(src):
        try:
            here = await _evaluate(session, "location.href") or ""
        except Exception:                               # noqa: BLE001
            here = ""
        return greenhouse_form_for(src, here)
    return src


#: Greenhouse's embedded *job list* — "every opening at Airbnb" — as opposed
#: to one job's form (``embed/job_app``).
_GH_BOARD_EMBED = re.compile(r"greenhouse\.io/embed/job_board(?!_)", re.I)


def greenhouse_form_for(board_src: str, page_url: str) -> str:
    """This job's Greenhouse form, from a careers page that embeds the list.

    Airbnb's job page embeds the whole board and shows the form behind an
    "Application" tab. Going to the embed opened a list of every vacancy,
    with no form on it, and the run sat there: "stuck on filling it in".
    The company is the embed's ``for=``, the job is the number in the page's
    own address (``/positions/8394382/``, ``?gh_jid=8394382``). Without both
    there is no form to go to, and "" says so rather than open the list.
    """
    from urllib.parse import parse_qs, urlparse
    company = (parse_qs(urlparse(board_src).query).get("for") or [""])[0]
    page = urlparse(page_url or "")
    jid = (parse_qs(page.query).get("gh_jid") or [""])[0]
    if not jid:
        found = re.findall(r"(?<!\d)(\d{6,})(?!\d)", page.path)
        jid = found[-1] if found else ""
    if not (company and jid):
        return ""
    # The embed address, not the hosted page: a company that runs its own
    # careers site (Airbnb does) has the hosted page redirect back to it,
    # which is the page with no form on it that we started from.
    return (f"https://job-boards.greenhouse.io/embed/job_app"
            f"?for={company}&token={jid}")


async def _follow_apply(session: Any) -> bool:
    """Get to the form this advert is about. True if the page moved.

    Three things to try, in the order that does least damage. Deliberately
    only from a page with no form of its own, so a real application form is
    never abandoned for a link that happens to say "apply".
    """
    # 1. A form already embedded here. No click needed, and no chance of
    #    pressing something that signs the user up for a newsletter.
    embed = await _embedded_form(session)
    if embed:
        log.info("apply: the form is embedded here; going to it: %s", embed[:120])
        await _goto(session, embed)
        return True

    # 2. The Apply control, link or button, *clicked*.
    #
    #    Copying a link's href and navigating to it used to come first.
    #    It is the wrong move: SmartRecruiters' "I'm interested" is an
    #    anchor whose address, opened cold, answers "Oops, you've gone
    #    too far" — their application app wants the click. A click costs
    #    nothing extra and is what every board is built to handle, so the
    #    href is kept only for when the click goes nowhere.
    try:
        raw = await _evaluate(session, _APPLY_BUTTON_JS)
        spot = json.loads(raw) if raw else None
    except Exception:                                   # noqa: BLE001
        spot = None

    if spot:
        log.info("apply: pressing %r", spot.get("text", "Apply")[:40])
        before = await _evaluate(session, "location.href")
        await _click_at(session, spot["x"], spot["y"])
        await asyncio.sleep(3.0)

        # Pressing it often loads an embed rather than navigating.
        embed = await _embedded_form(session)
        if embed:
            log.info("apply: that loaded an embedded form: %s", embed[:120])
            await _goto(session, embed)
            return True
        if await _evaluate(session, "location.href") != before:
            return True
        # It may have revealed the form in place.
        from . import directfill
        if learning._parse_harvest(
                await _read_or_empty(session, directfill.READ_JS)):
            return True
        href = spot.get("href") or ""
        if href:
            log.info("apply: the click went nowhere; opening %s", href[:120])
            await _goto(session, href)
            return True

    # 3. A link the control search did not recognise as clickable.
    try:
        raw = await _evaluate(session, _APPLY_JS)
        href = json.loads(raw) if raw else ""
    except Exception:                                   # noqa: BLE001
        href = ""
    if href:
        log.info("apply: following the Apply link to %s", href[:120])
        await _goto(session, href)
        return True
    return False


async def _looks_closed(session: Any) -> str:
    """The phrase saying this posting is finished, or "" if none is there.

    Read straight off the page rather than from the HTTP status: boards
    serve a cheerful 200 with "This role is no longer accepting
    applications" in the middle of it far more often than they 404.
    """
    try:
        text = await _evaluate(session, (
            "JSON.stringify((document.title + ' ' + "
            "(document.body ? document.body.innerText : ''))"
            ".replace(/\\s+/g, ' ').slice(0, 1200))"))
    except Exception:
        return ""
    try:
        body = json.loads(text) if text else ""
    except (json.JSONDecodeError, TypeError):
        body = str(text or "")
    found = _CLOSED.search(body)
    if not found:
        return ""
    # Hand back the sentence it was found in, so the row says what the page
    # said rather than Sushi's paraphrase of it.
    start = body.rfind(".", 0, found.start()) + 1
    end = body.find(".", found.end())
    sentence = body[start:end if end != -1 else found.end() + 60].strip()
    return sentence[:120] or found.group(0)


async def _upload(session: Any, key: str, path: str) -> bool:
    """Attach a file to an input, the way the file chooser would.

    CDP's setFileInputFiles is the only reliable route: a file input's value
    cannot be assigned from script, by design.
    """
    cdp = await _cdp(session, focus=False)
    try:
        doc = await cdp.cdp_client.send.DOM.getDocument(
            {"depth": -1}, session_id=cdp.session_id)
        root = doc["root"]["nodeId"]
        selector_id = key
        for selector in (f'#{key}', f'[name="{key}"]'):
            found = await cdp.cdp_client.send.DOM.querySelector(
                {"nodeId": root, "selector": selector},
                session_id=cdp.session_id)
            node = found.get("nodeId")
            if node:
                await cdp.cdp_client.send.DOM.setFileInputFiles(
                    {"files": [path], "nodeId": node},
                    session_id=cdp.session_id)
                # CDP puts the file on the element, and nothing else happens.
                # A React uploader renders from its own state and only updates
                # it on the change event a real chooser would have fired — so
                # without this the file is attached as far as the DOM is
                # concerned and invisible as far as the page is concerned.
                await _evaluate(session, f"""
                  (() => {{
                    const el = document.getElementById({selector_id!r})
                      || document.querySelector('[name="{key}"]');
                    if (!el) return 'missing';
                    for (const name of ['input', 'change']) {{
                      el.dispatchEvent(new Event(name, {{bubbles: true}}));
                    }}
                    return el.files.length ? el.files[0].name : 'none';
                  }})()""")
                return True
    except Exception as exc:                        # noqa: BLE001
        log.info("direct: upload to %s failed (%s)", key, exc)
    return False


# --- real mouse and keyboard ------------------------------------------------
#
# Everything above reaches the page by evaluating JavaScript in it, which is
# right for reading a form and for typing into an ordinary input. It is not
# enough for a dropdown. A synthesised Escape does not close a react-select;
# a synthesised mousedown on the body does not close an intl-tel-input. The
# first version of the click-through path used those, and the result was a
# form left with five menus hanging open on top of one another, every one of
# them unanswered, because each control was opened to read its options and
# none of them could be shut again.
#
# So the widgets are driven through CDP's Input domain instead: real mouse
# events at real coordinates, real key presses. That is what every one of
# these libraries is built to handle, and it does not depend on knowing how
# any particular one of them is wired.


async def _input(session: Any, method: str, params: dict) -> Any:
    cdp = await _cdp(session, focus=False)
    return await getattr(cdp.cdp_client.send.Input, method)(
        params, session_id=cdp.session_id)


async def _click_at(session: Any, x: float, y: float) -> None:
    """One real left click, where a user would put the pointer."""
    base = {"x": float(x), "y": float(y), "button": "left", "clickCount": 1}
    await _input(session, "dispatchMouseEvent",
                 {"type": "mouseMoved", "x": base["x"], "y": base["y"],
                  "button": "none", "buttons": 0})
    await _input(session, "dispatchMouseEvent",
                 {**base, "type": "mousePressed", "buttons": 1})
    await _input(session, "dispatchMouseEvent",
                 {**base, "type": "mouseReleased", "buttons": 0})


async def _press(session: Any, key: str, code: str, vk: int) -> None:
    for kind in ("keyDown", "keyUp"):
        await _input(session, "dispatchKeyEvent",
                     {"type": kind, "key": key, "code": code,
                      "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk})


async def _type_text(session: Any, text: str) -> None:
    """Type into whatever has focus, as a keyboard would."""
    await _input(session, "insertText", {"text": text})


async def _clear_focused(session: Any, count: int) -> None:
    """Backspace over what was typed, so the next query starts clean."""
    for _ in range(min(count, 120)):
        await _press(session, "Backspace", "Backspace", 8)


async def _close_menus(session: Any, tries: int = 3) -> bool:
    """Shut any choice menu that is open. True if the page ends up clear.

    Escape first, because that is what closes one without changing it. If
    something is still open after that, click a sliver of empty page down
    the left edge — outside every control, which is the other thing these
    widgets listen for.
    """
    from . import directfill

    for attempt in range(tries):
        try:
            open_now = int(await _evaluate(session, directfill.OPEN_MENUS_JS) or 0)
        except Exception:                               # noqa: BLE001
            return True
        if not open_now:
            return True
        await _press(session, "Escape", "Escape", 27)
        await asyncio.sleep(0.25)
        if attempt:
            await _click_at(session, 3, 300)
            await asyncio.sleep(0.25)
    return False


#: How long a dropdown gets to render its options after being opened. A
#: react-select list is one render away; a Workday one fetches.
_POPUP_WAIT = 0.6
#: And how long a list that was not there yet gets, on the one retry. A
#: university search goes to the network.
_POPUP_RETRY = 1.2

#: How many times the form is read. A question that only appears once
#: another has been answered — "do you need sponsorship?" → "which visa?" —
#: exists on most real forms and is usually required, and reading the page
#: once meant it was never seen at all. Three is enough for every chain
#: observed; it is bounded because a form that keeps growing is a form with
#: a bug in it, and following it forever means never finishing.
_MAX_PASSES = 3


async def _choose(session: Any, f: Any) -> str:
    """Work one dropdown the way a person does: click, type, click.

    Returns ``picked:<option text>`` when an option was actually clicked,
    or a short reason why not. The reason matters — "the list never
    appeared" and "the list appeared and held nothing like this" are
    different problems and only one of them is worth retrying.
    """
    from . import directfill

    # Amber while Sushi is on it. A dropdown takes a couple of seconds to
    # open, read and click, and without this the browser sits apparently
    # idle with no sign of which field is being worked.
    try:
        await _evaluate(session, directfill.mark_script(f.key, "working"))
    except Exception:                                   # noqa: BLE001
        pass

    raw = await _evaluate(session, directfill.rect_script(f.key))
    if not raw:
        return "no-control"
    try:
        spot = json.loads(raw)
    except (ValueError, TypeError):
        return "no-control"

    await _click_at(session, spot["x"], spot["y"])
    await asyncio.sleep(_POPUP_WAIT)

    # A searchable list is narrowed by typing; a short one is just open.
    if f.widget == "typeahead":
        await _type_text(session, f.value)
        await asyncio.sleep(_POPUP_WAIT)

    option = await _evaluate(session,
                             directfill.option_rect_script(f.key, f.value))
    if not option:
        # A school or city list is fetched rather than filtered in the
        # page, so the first look can land before the results do.
        await asyncio.sleep(_POPUP_RETRY)
        option = await _evaluate(session,
                                 directfill.option_rect_script(f.key, f.value))
    if not option and f.widget == "typeahead":
        # Ask it something shorter. A search box given the whole answer —
        # "Bristol, United Kingdom" — often returns nothing at all, where
        # the first word of it returns the row that was wanted. The match
        # is still made against the full answer, so a shorter *query* does
        # not mean a looser *choice*.
        short = directfill.search_query(f.value)
        if short and short != f.value:
            await _type_text(session, "")
            await _clear_focused(session, len(f.value))
            await _type_text(session, short)
            await asyncio.sleep(_POPUP_RETRY)
            option = await _evaluate(
                session, directfill.option_rect_script(f.key, f.value))
    if not option:
        if f.widget == "typeahead":
            # Leave nothing behind. The search text is not an answer, and
            # left in the box it reads as one — to the user and to the
            # agent that picks up after this.
            await _clear_focused(session, max(len(f.value), 20))
        return "no-option"
    try:
        hit = json.loads(option)
    except (ValueError, TypeError):
        return "no-option"

    await _click_at(session, hit["x"], hit["y"])
    return f"picked:{hit.get('text', '')}"


async def _apply_plan(session: Any, plan: Any, on_step=None) -> tuple[int, list]:
    """Write a plan into the page. Returns (fields filled, guesses that landed).

    Two phases, because they are two different kinds of control. Everything
    that can be written to is written in one script. Everything that has to
    be *clicked through* — a react-select dropdown, a typeahead — is then
    driven one at a time, with a wait in between, because the options do not
    exist in the page until the control has been opened.
    """
    from . import directfill

    if not plan.fills:
        return 0, []

    filled, landed = 0, []

    # Already lit by ``fill_rounds`` before the slow part — this is the
    # re-assert, for anything the page re-rendered out from under it while
    # the model was thinking.
    await _mark(session, [f.key for f in plan.fills], "working")

    result_raw = await _evaluate(session, directfill.fill_script(plan))
    # A React form applies the change on its next render, so a read-back
    # in the same tick sees the old value and reports a working field as
    # having failed.
    await asyncio.sleep(1.5)
    deferred_keys: set[str] = set()
    try:
        applied = json.loads(result_raw) if result_raw else {}
        filled = len(applied.get("filled", []))
        deferred_keys = set(applied.get("deferred") or [])
        guessed_keys = set(applied.get("guessed") or [])
        landed = [g for g in plan.guesses if g.key in guessed_keys]
    except (ValueError, TypeError):
        filled = len([f for f in plan.fills if not f.widget])
        landed = [g for g in plan.guesses if not g.widget]
        guessed_keys = {g.key for g in landed}

    # Written, so they stop pulsing: a guess settles to the standing amber
    # ring that asks to be checked, everything else lets go entirely. The
    # ones still to come — the dropdowns, which are the slow part — keep
    # their glow, so what is lit is exactly what is left.
    written = [f.key for f in plan.fills if f.key not in deferred_keys]
    await _mark(session, [k for k in written if k in guessed_keys], "guess")
    await _mark(session, [k for k in written if k not in guessed_keys], "clear")

    for f in plan.fills:
        if f.key not in deferred_keys:
            continue
        if on_step:
            on_step(f"Choosing “{f.value[:40]}” for {f.label[:40]}…")
        _o, _p, verify = directfill.choose_scripts(f)
        outcome = ""
        shown = ""
        try:
            # Nothing else may be open: an abandoned menu sits on top of the
            # control about to be clicked, so the click lands in the wrong
            # list — which is exactly how a form ends up with five menus
            # open and nothing chosen.
            await _close_menus(session)
            outcome = await _choose(session, f)
            await asyncio.sleep(0.35)
            shown = (await _evaluate(session, verify) or "").strip()
            await _close_menus(session)
        except Exception as exc:                        # noqa: BLE001
            log.info("direct: could not work %s (%s)", f.label[:50], exc)
            plan.unanswered.append(f.label or f.key)
            continue
        # Verified, not assumed. A dropdown that quietly refused the click
        # is exactly the failure this path exists to prevent, and reporting
        # it as filled is worse than reporting it as outstanding.
        #
        # An option must have been *clicked*. Nothing else counts, and the
        # tempting shortcut — "the control now shows what we wanted" — is
        # exactly wrong here: opening a typeahead means typing the answer
        # into its search box, so a control that was typed into and never
        # committed shows the right text and holds nothing. Three required
        # education fields passed that check and submitted empty.
        #
        # What it ends up *showing* can legitimately differ: Greenhouse's
        # phone country picker is asked for "United Kingdom", offers
        # "United Kingdom +44", and afterwards displays "+44". So the pick
        # is the gate and a non-placeholder display is the corroboration.
        picked = str(outcome or "").startswith("picked:")
        answered = bool(shown) and not directfill.is_placeholder(shown)
        if picked and answered and _wrong_match(f.value, shown):
            # A typeahead's first hit for "University of Agriculture,
            # Faisalabad" was Tokyo's. Clicked, shown, and wrong: worse than
            # blank, because nobody looks twice at a filled box.
            log.info("direct: %s wanted %r but the list gave %r; cleared",
                     f.label[:50], f.value, shown[:60])
            picked = False
        if picked and answered:
            filled += 1
            # A guessed dropdown keeps its amber; a looked-up one loses it.
            # Until now a guess that was *clicked* rather than typed got no
            # mark at all, so the one kind of answer that most needs
            # checking was the one kind that was never flagged.
            state = "guess" if f.guessed else "clear"
            try:
                await _evaluate(session, directfill.mark_script(f.key, state))
            except Exception:                           # noqa: BLE001
                pass
            if f.guessed:
                landed.append(f)
            if f.value.lower() not in shown.lower():
                log.info("direct: %s took %r and now shows %r",
                         f.label[:50], f.value, shown[:60])
        else:
            log.info("direct: %s did not take %r (%s, showing %r)",
                     f.label[:50], f.value, outcome, shown[:60])
            # Take the search text back out. The widget is holding nothing,
            # so leaving "Nowhereton" sitting in the box is a field that
            # looks answered to the next read, to the person checking the
            # form, and on some widgets to the server.
            try:
                await _evaluate(session, directfill.clear_script(f.key))
                await _evaluate(session, directfill.mark_script(f.key, "clear"))
            except Exception:                           # noqa: BLE001
                pass
            plan.unanswered.append(f.label or f.key)
    return filled, landed


_GENERIC_WORDS = {
    "the", "of", "and", "in", "at", "for", "a", "an", "university", "college",
    "institute", "school", "degree", "national", "state", "city", "s",
}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower())
            if len(w) > 1 and w not in _GENERIC_WORDS}


def _wrong_match(wanted: str, shown: str) -> bool:
    """Whether a dropdown settled on a different thing from the one asked for.

    Different on both sides: something we asked for is missing *and*
    something we did not ask for is there. One-sided differences are how
    lists legitimately abbreviate ("United Kingdom" shows "+44", "Bachelor's
    Degree" shows "Bachelor's"), so those pass.
    """
    want, got = _words(wanted), _words(shown)
    if not want or not got:
        return False
    return bool(want - got) and bool(got - want)


#: How many closed dropdowns are worth opening to find out what they offer.
#: Each costs about a second. A Canonical form has twenty; a form with more
#: than this is one where the cost stops being worth it.
_MAX_DISCOVER = 30


async def discover_options(session: Any, fields: list[dict],
                           *, include_demographics: bool = False,
                           on_step=None) -> int:
    """Open each closed choice control, read what it offers, shut it again.

    Mutates ``fields`` in place. Returns how many controls were opened.

    This is the difference between a Greenhouse form being fillable and not.
    react-select renders its options only while the menu is open, so reading
    the page finds a text box with no choices — and then the lookup has
    nothing to match an answer against and the model has nothing to pick
    from. Every dropdown on the form comes back as a question for the user.

    A list longer than ``SEARCHABLE_FROM`` is recorded as *searchable*
    rather than kept: the school list is every university in the world, and
    the right way to answer one of those is to type the answer and click the
    match, not to choose from the first sixty names alphabetically.
    """
    from . import directfill

    todo = [f for f in fields if directfill.needs_options(f)]
    # Not the ones we would refuse to answer anyway. Opening a race or
    # disability dropdown to read its options is work done to no purpose.
    todo = [f for f in todo
            if not (learning.is_demographic(f.get("label") or "",
                                            f.get("key") or "")
                    or learning.options_are_demographic(f.get("options"))
                    and not include_demographics)
            and not directfill._NEVER.search(f.get("label") or "")]
    if not todo:
        return 0
    if len(todo) > _MAX_DISCOVER:
        log.info("direct: %d closed dropdowns, reading the first %d",
                 len(todo), _MAX_DISCOVER)
        todo = todo[:_MAX_DISCOVER]
    if on_step:
        on_step(f"Reading {len(todo)} dropdown(s)…")

    opened = 0
    for f in todo:
        _reveal, read = directfill.reveal_scripts(f.get("key", ""))
        try:
            # Clicked open and pressed shut, both for real. The synthesised
            # version of this left every menu it opened lying open on top of
            # the next field, which is worse than never having looked.
            spot = await _evaluate(session, directfill.rect_script(f.get("key", "")))
            if not spot:
                continue
            at = json.loads(spot)
            await _click_at(session, at["x"], at["y"])
            await asyncio.sleep(0.5)
            raw = await _evaluate(session, read)
            await _close_menus(session)
        except Exception as exc:                        # noqa: BLE001
            log.info("direct: could not open %s (%s)",
                     (f.get("label") or f.get("key"))[:50], exc)
            await _close_menus(session)
            continue
        try:
            got = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            continue
        options = got.get("options") or []
        total = int(got.get("total") or 0)
        if not options:
            continue
        opened += 1
        if total > directfill.SEARCHABLE_FROM:
            # A catalogue, not a choice. Keep a sample for matching against,
            # but mark it so nothing treats the sample as the whole list.
            f["searchable"] = True
            f["options"] = []
            f["sample_options"] = options[:8]
        else:
            f["options"] = options
        log.info("direct: %s offers %d option(s)%s",
                 (f.get("label") or f.get("key"))[:50], total,
                 " (searchable)" if total > directfill.SEARCHABLE_FROM else "")
    return opened


#: How long a board gets to parse an uploaded CV and redraw its form.
#: Ashby reads the file, fills what it can and re-renders; filling during
#: that is filling a form that is about to be replaced.
_AFTER_UPLOAD = 4.0


async def _attach_files(session: Any, fields: list[dict], cv_path: str,
                        letter_path: str, on_step=None) -> tuple[int, set[str]]:
    """Attach the CV and the letter. Returns (how many, their labels)."""
    uploads = _uploadable(cv_path, letter_path)
    if not uploads:
        return 0, set()

    done, labels = 0, set()
    for f in fields:
        if (f.get("type") or "").lower() != "file":
            continue
        # Match on the field's *key*, not its label. Greenhouse labels both
        # the CV and the cover-letter inputs "Attach" — identical text, two
        # different fields — so a label match sent the CV to both and the
        # letter nowhere.
        haystack = f"{f.get('key', '')} {f.get('label', '')}".lower()
        # A photo, ID, portfolio or transcript upload is not a CV slot.
        # Every file input used to get the CV, so "Please upload a recent
        # photo" got MOAZAM_TEST_CV.PDF.
        if re.search(r"photo|picture|headshot|selfie|avatar|image|passport|"
                     r"\bid\b|identity|transcript|certificate|portfolio|"
                     r"writing sample|work sample|video", haystack):
            continue
        is_letter = "cover" in haystack or "letter" in haystack
        path = uploads.get("letter" if is_letter else "cv")
        if not path and not is_letter:
            path = uploads.get("cv", "")
        if not path:
            continue
        label = f.get("label") or f.get("key") or "file upload"
        if on_step:
            on_step(f"Attaching {Path(path).name}…")
        if await _upload(session, f.get("key", ""), path):
            done += 1
            labels.add(label)
        else:
            log.info("direct: could not attach %s to %s", Path(path).name, label)
    return done, labels


#: How many pages of one application Sushi will work through. Taleo's
#: longest run to four or five; past that it is a loop, not a form.
_MAX_STEPS = 6
#: How long a step gets to load. These are server round trips, not renders.
_AFTER_STEP = 3.0


#: The control that sends the application. Exact labels only: a page has
#: "Apply" links to other jobs, and the wrong one of those is not a submit.
SUBMIT_JS = r"""
(() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const SEND = /^(submit|submit (my |your )?application|send( my)? application|apply|apply now|submit and apply|complete application|finish( application)?)$/i;
  const vis = el => {
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 10 && r.height > 8;
  };
  const nodes = [...document.querySelectorAll(
    'button, input[type="submit"], [role="button"]')].filter(el => !el.closest('[id^="sushi-"]'));
  for (const el of nodes.reverse()) {
    const text = clean(el.innerText || el.value || el.getAttribute('aria-label'));
    if (!SEND.test(text) || !vis(el) || el.disabled) continue;
    if (el.tagName === 'A' || !(el.closest('form') || nodes.length < 40)) continue;
    try { el.scrollIntoView({block: 'center', behavior: 'instant'}); } catch (e) { }
    const r = el.getBoundingClientRect();
    return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2, text});
  }
  return '';
})()
"""

#: Whether the live checklist says nothing required is left. Read from the
#: panel itself so "ready" means exactly what the user is looking at.
_READY_JS = r"""
(() => {
  const box = document.getElementById('sushi-checklist');
  return box && box.dataset.ready === '1' ? 'yes' : 'no';
})()
"""


async def _press_submit(session: Any, on_step=None) -> bool:
    """Press the form's Submit, and say whether the application went in.

    Only once the checklist shows every required field filled, and only
    counted as sent when the page then says so: a Submit that comes back
    with a red "this field is required" has not sent anything.
    """
    from .checklist import CHECKLIST_JS
    try:
        await _evaluate(session, CHECKLIST_JS)
        await asyncio.sleep(0.4)
        if await _evaluate(session, _READY_JS) != "yes":
            log.info("direct: not submitting, the checklist shows required fields left")
            return False
        raw = await _evaluate(session, SUBMIT_JS)
        if not raw:
            log.info("direct: not submitting, no submit button found")
            return False
        spot = json.loads(raw)
        if on_step:
            on_step(f"Submitting: pressing “{spot.get('text', 'Submit')[:30]}”…")
        await _click_at(session, spot["x"], spot["y"])
        for _ in range(15):
            await asyncio.sleep(1.0)
            try:
                if await _evaluate(session, _SUBMITTED_JS) == "yes":
                    log.info("direct: submitted via %r", spot.get("text", ""))
                    return True
            except Exception:                           # noqa: BLE001
                pass            # mid-navigation
        log.info("direct: pressed %r but no confirmation page appeared",
                 spot.get("text", ""))
    except Exception:                                   # noqa: BLE001
        log.info("direct: submitting failed", exc_info=True)
    return False


async def _advance(session: Any, on_step=None) -> bool:
    """Move to the next page of a multi-page application. True if it moved.

    Only ever presses a control that says Next or Continue, never one that
    says Submit, Apply or Send — see ``directfill.NEXT_JS``. And it checks
    the page actually changed: pressing Continue with a required field
    still blank leaves you where you were with a red message, which is a
    refusal and not a step.
    """
    from . import directfill

    raw = await _evaluate(session, directfill.NEXT_JS)
    if not raw:
        return False
    try:
        spot = json.loads(raw)
    except (ValueError, TypeError):
        return False

    before = await _evaluate(session, directfill.STEP_ID_JS)
    if on_step:
        on_step(f"Next page, pressing “{spot.get('text', 'Next')[:30]}”…")
    await _click_at(session, spot["x"], spot["y"])
    await asyncio.sleep(_AFTER_STEP)
    after = await _evaluate(session, directfill.STEP_ID_JS)
    if after == before:
        log.info("direct: %r did not move the form on, most likely a field "
                 "it still wants", spot.get("text", "")[:40])
        return False
    log.info("direct: moved to the next page via %r", spot.get("text", "")[:40])
    return True


async def fill_rounds(session: Any, fields: list[dict], role: Role,
                      profile: Profile, *, demographics: bool = False,
                      llm: Any = None, explorative: bool = False,
                      tone: str = "", on_step=None):
    """Fill the form, then come back for whatever answering it revealed.

    Returns ``(filled, guesses, plan, all_fields)``. ``plan`` is the last
    round's, with ``unanswered``/``consents``/``skipped`` accumulated across
    all of them, and ``all_fields`` is every field seen in any round.

    Separate from :func:`_run_direct` because this is the part worth testing
    against a real page: everything around it is sessions, uploads and
    reporting, and this is the part that decides whether the form is
    actually filled. ``session`` is used only through :func:`_evaluate`.
    """
    from . import directfill

    filled = 0
    #: The guesses that actually landed in a field. A guess the script could
    #: not apply is not in the form, so listing it back as something to check
    #: sends the user hunting for an amber outline that is not there.
    guesses: list = []
    #: Questions already put to the model this run. A later pass must not
    #: pay for the same question twice: it was asked, it was not answered,
    #: and nothing about the page has made it answerable.
    asked: set[str] = set()
    #: Labels the model did answer, even where the page would not take it.
    tried: set[str] = set()
    #: Why the generative step produced nothing, when it produced nothing.
    why = "" if llm is not None else "off"
    #: Carried across passes, both of them. A question that could not be
    #: answered on pass one must still be reported even if a later read
    #: thinks its box has something in it, and a question answered on pass
    #: one must not be reported just because the last pass did not see it.
    stuck: list[str] = []
    done: set[str] = set()
    #: Every field seen in any pass, keyed so the caller's upload loop sees
    #: each one once however many times it was read.
    all_fields: dict[str, dict] = {}
    plan = directfill.Plan()

    # Round by round, because answering a question makes more of them. A
    # form read once is a form where "which visa do you hold?" — revealed by
    # the sponsorship answer Sushi had just given — was never seen, and the
    # application went out with a required field empty.
    #
    # Every pass re-plans the *whole* form rather than only the fields that
    # are new. It costs nothing — ``plan_fill`` skips anything that already
    # has a value in it, which after a pass is everything it filled — and it
    # catches the other half of this problem: a dropdown whose options only
    # arrive once another question has been answered. Narrowing to new keys
    # missed those entirely, because the field had been there all along with
    # nothing in it to choose.
    attempts: dict[tuple[str, str], int] = {}
    for round_no in range(_MAX_PASSES):
        if round_no:
            await asyncio.sleep(1.0)        # let the reveal render
            fields = learning._parse_harvest(
                await _read_or_empty(session, directfill.READ_JS))

        # What each closed dropdown actually offers, before anything tries
        # to answer it. Only for controls not already discovered, so a
        # second pass pays only for what the first pass revealed.
        await discover_options(session, fields,
                               include_demographics=demographics,
                               on_step=on_step if not round_no else None)

        for f in fields:
            if f.get("key"):
                all_fields.setdefault(f["key"], f)
        plan = directfill.plan_fill(fields, profile,
                                    include_demographics=demographics,
                                    role=role)
        # Required choice questions the profile settles (based in these
        # countries? live in these states? where did you hear?) — before
        # any model, and in every mode.
        n_derived = directfill.answer_from_profile(plan, fields, profile)
        if n_derived:
            log.info("direct: answered %d from the profile", n_derived)

        # Light the form *here*, before the slow part, not after it. The
        # glow used to go on inside ``_apply_plan`` — which is the last
        # thing that happens, and by then the writing takes milliseconds.
        # Everything before it is where the minutes go: opening each
        # dropdown to read its options, then a model call per question. So
        # the form sat untouched and unmarked for the whole run and looked
        # stopped. Every field with nothing in it is what Sushi is working
        # on, and that is what is lit.
        await _mark(session, [f["key"] for f in fields
                              if f.get("key") and not (f.get("value") or "")
                              and not f.get("readonly")], "working")

        # Everything the lookup could not reach, if the user asked for that.
        # The order matters: the lookup has already taken every field it is
        # certain about, so the model is only ever asked about what is
        # genuinely left, and a guess can never displace a known answer.
        # Already put to the model on an earlier form and left blank: not
        # asked again (seconds each, the same blank back). Straight to you.
        for q in plan.unanswered:
            if explorative and q not in asked and learning.was_declined(q):
                asked.add(q)
        fresh_questions = [q for q in plan.unanswered if q not in asked]
        if llm is not None and fresh_questions:
            if on_step:
                on_step(f"{len(fresh_questions)} question(s) the lookup could "
                        f"not answer, "
                        + ("working them out…" if explorative
                           else "writing the free-text ones…"))
            held_back = [q for q in plan.unanswered if q in asked]
            plan.unanswered = fresh_questions
            asked |= set(fresh_questions)
            try:
                if explorative:
                    n = directfill.guess_answers(
                        llm, plan, fields, role, profile, tone=tone,
                        include_demographics=demographics)
                else:
                    n = directfill.answer_free_text(
                        llm, plan, fields, role, profile, tone=tone)
                log.info("direct: answered %d by model", n)
            except Exception as exc:                    # noqa: BLE001
                # A model that will not answer must not cost you the fields
                # that were already filled from the lookup — but it must not
                # be swallowed either. An error nobody is shown is the same
                # thing, from the outside, as a feature that does nothing.
                log.info("direct: could not finish the form with a model (%s)",
                         exc)
                why = why or str(exc)[:160]
            plan.unanswered += held_back

        log.info("direct: pass %d · %s", round_no + 1, plan.summary)
        if on_step and not round_no:
            on_step(plan.summary)

        # A value already typed twice that still reads back empty is not
        # going to take on a third go; it only costs another ~3s pass.
        # The log showed passes 2 and 3 re-filling the same three fields.
        plan.fills = [f for f in plan.fills
                      if attempts.get((f.key, f.value), 0) < 2]
        for f in plan.fills:
            attempts[(f.key, f.value)] = attempts.get((f.key, f.value), 0) + 1
        # An empty plan falls through to the break below, after what is
        # still outstanding has been recorded.

        n_filled, landed = await _apply_plan(session, plan, on_step)
        filled += n_filled
        guesses += landed
        stuck += plan.unanswered
        tried |= {f.label for f in plan.fills if f.label}
        done |= {f.label for f in plan.fills
                 if f.label and f.label not in plan.unanswered}
        if not plan.fills:
            # Nothing left that this run knows how to answer. The plan in
            # hand is the final word on what is still outstanding.
            break

    plan.unanswered = list(dict.fromkeys(
        list(plan.unanswered) + [q for q in stuck if q not in done]))
    plan.declined = ([q for q in plan.unanswered if q in asked and q not in tried]
                     if explorative else [])
    plan.consents = list(dict.fromkeys(plan.consents))
    plan.skipped = list(dict.fromkeys(plan.skipped))
    return filled, guesses, plan, list(all_fields.values()), why


async def _run_direct(role: Role, profile: Profile, settings: Settings,
                      cv_path: str, letter_path: str,
                      on_step: Callable[[str], None] | None,
                      on_letter_needed: Callable[[], str] | None = None,
                      explorative: bool = False,
                      compose: bool = False,
                      tone: str = "",
                      may_hand_over: bool = False,
                      continue_in: str = "",
                      should_stop: Callable[[], bool] | None = None) -> FillResult:
    """Fill a form from what is already known, with no agent and no model.

    The agent costs about ten thousand tokens *per step* — most of it the
    framework's own system prompt, which does not shrink for a simple form.
    That is more than a typical free tier allows in a minute, so on those
    plans the agent cannot complete a single step, however small the page.

    This path costs nothing. It reads the fields, matches each label against
    the profile and the answer bank, types the ones it knows and attaches the
    CV. Whatever is left — genuinely new prose, an unfamiliar question — is
    reported for the user, or handed to the agent afterwards if they asked
    for that.

    ``compose`` adds one short model call per free-text question, which is
    what Fill mode "auto" wants before it reaches for the agent.
    ``explorative`` goes further and answers everything still blank with a
    flagged guess — see ``directfill.guess_answers``. Both still cost a
    fraction of an agent step, and both leave the lookup in charge of
    anything it could answer itself.

    ``may_hand_over`` says the caller will pass anything left unanswered to
    the agent rather than to the user. It changes nothing about the filling;
    it decides only whether the pill on the page is allowed to say the form
    is ready for review, which is a lie while a second pass is still coming.

    ``continue_in`` names a tab an earlier pass already filled part of, for
    the other direction of the same handover: the agent could not run at all
    and the lookup is being asked to finish what it started. The form is
    taken as it stands and *not* navigated to again, because loading it
    again is what empties it.
    """
    from . import directfill

    session, reused = await _session_for_apply(settings, role.url,
                                               continue_in=continue_in)
    resuming = bool(continue_in) and getattr(session, "_sushi_target",
                                             None) == continue_in
    if reused:
        # Same as the agent path: a reused browser means a new tab, and a
        # new tab carries none of Sushi's scripts until they are put there.
        await _brand(session, f"Applying to {role.company}", hot=True)
        await _set_badge(session, f"Applying to {role.company}", hot=True)
    else:
        # The agent path gets this for free — Agent.run() starts the session
        # on its way to the first step. With no agent there is nothing to do
        # it, and every CDP call then fails on an uninitialised client.
        try:
            await session.start()
        except Exception as exc:
            raise AutofillError(f"Could not start the browser: {exc}") from exc
        # Named first, so the tab can be given to the agent later and so
        # ``_brand`` registers against *this* tab rather than whichever one
        # the session happens to be looking at.
        await _adopt_current_tab(session)
        # Branded *after* the start, not before. Registering an init script
        # needs a live CDP connection: called first it raised a bare
        # AssertionError into the guard inside ``_brand``, so a fresh browser
        # carried no Sushi badge at all and nothing said why.
        await _brand(session, f"Applying to {role.company}", hot=True)

    # Every step the app reports, reported on the page as well. The window
    # doing the work is the one being watched — during Insane mode it is the
    # *only* thing being watched, because the app is deliberately somewhere
    # behind it — and "Filling" with a form that has not moved for ninety
    # seconds is indistinguishable from a crash.
    on_step = _echo_to_page(session, on_step)

    # Bound to this application's tab, not to the session. A watcher keeps
    # polling after the form is left open for review, and the next
    # application retargets the session — so a session-bound watcher would
    # quietly start reading somebody else's form and bank its answers.
    _tab = getattr(session, "_sushi_target", None)
    watcher = learning.FormWatcher(
        lambda script, t=_tab: _evaluate(session, script, target=t),
        include_demographics=learn_demographics())
    watcher.role_url = getattr(role, "url", "")

    if not resuming:
        # Skipped when carrying on in a tab that already holds the form: a
        # form is state in the page, and navigating to the same URL is how
        # you lose the upload and every field already typed into it.
        await _goto(session, role.url)
    if on_step:
        on_step("Reading the form…")

    # Retried once, because a page that has only just finished loading is
    # the most likely one to be too busy to answer, and this read is the
    # whole run — everything below it needs the form.
    raw = await _read_or_empty(session, directfill.READ_JS)
    if not raw:
        await asyncio.sleep(2.0)
        raw = await _evaluate(session, directfill.READ_JS)
    fields = learning._parse_harvest(raw)
    if not fields:
        # Most of tier one is an aggregator — Jobicy, The Muse, Himalayas —
        # whose "url" is a page *about* the job with an Apply link on it, not
        # a form. Arriving there and reporting "0 fields filled" spends one
        # of a fifteen-a-day cap on reading an advert. A person would press
        # Apply, so press Apply: once, and only when there is no form here.
        if await _follow_apply(session):
            if on_step:
                on_step("Followed Apply, reading the form…")
            raw = await _evaluate(session, directfill.READ_JS)
            fields = learning._parse_harvest(raw)

    if not fields:
        _open_sessions.append(session) if session not in _open_sessions else None
        _spawn_watch(session, watcher, _tab)
        # No form has two quite different causes, and telling them apart
        # matters in a batch: a posting that is gone should read as gone
        # rather than as something you might fix by pressing Apply. Feeds go
        # stale between the scan and the run, and a closed role at the top of
        # the queue otherwise looks like Insane mode failing.
        closed = await _looks_closed(session)
        # The pill said "Reading the form…" here for good, on a page that
        # had nothing to read.
        await _set_badge(session, "This job is closed · nothing to fill"
                         if closed else "No form here · over to you")
        return FillResult(
            ok=False, submitted=False, left_open=True,
            message=(f"This posting is no longer taking applications"
                     f"{': ' + closed if closed else ''}.")
            if closed else
            ("No application form was found, and no Apply link to follow. "
             "The browser is open for you."),
            unanswered=[], tab=_tab or "")

    # A letter, but only if there is somewhere to put one. The caller does
    # not know what the form asks until the form has been read, which is
    # here — so the decision belongs here too, not before the browser opens.
    if on_letter_needed is not None and not letter_path:
        from .letters import wants_cover_letter
        if wants_cover_letter(fields):
            try:
                letter_path = on_letter_needed() or ""
            except Exception as exc:                    # noqa: BLE001
                log.info("direct: no cover letter (%s)", exc)

    demographics = learn_demographics()
    llm = None
    no_model = False
    if (explorative or compose):
        if settings.llm_enabled:
            from .llm import LLM

            llm = LLM(settings)
        else:
            no_model = True
            log.info("direct: %s mode wanted, but no model is configured",
                     "explorative" if explorative else "auto")

    # Files first, and then read the form again.
    #
    # This used to come last, and on Ashby that silently undid most of the
    # work: their upload box says "upload your resume here to autofill key
    # application fields", and doing so re-renders the form and clears it.
    # Sushi filled salary, notice period and right-to-work, attached the
    # CV, and the page threw all three away — so the form looked complete
    # and submitted with "Missing entry for required field" against every
    # one of them.
    #
    # Uploading first is better anyway: a board that parses the CV gets to
    # do it before Sushi types, so Sushi only fills what the parser left
    # blank instead of fighting it.
    uploaded, attached = await _attach_files(session, fields,
                                             cv_path, letter_path, on_step)
    if uploaded:
        # The board is busy here — Greenhouse parses the CV it has just
        # been handed — so this read is the one most likely to find a page
        # that will not answer, and it is an improvement rather than the
        # job. Losing it costs a re-read; raising out of it costs the
        # application.
        if on_step:
            on_step(f"{uploaded} file(s) attached. Reading the form again…")
        await asyncio.sleep(_AFTER_UPLOAD)
        fresh = learning._parse_harvest(
            await _read_or_empty(session, directfill.READ_JS))
        if fresh:
            fields = fresh

    # Page by page. Taleo, Workday and iCIMS split an application across
    # steps, and Sushi used to fill the first one and report itself done —
    # so three quarters of those forms were never touched, and the one it
    # did fill was left sitting on step one of five.
    filled = uploaded
    guesses: list = []
    all_fields: list[dict] = []
    why = ""
    unanswered: list[str] = []
    declined: list[str] = []
    consents: list[str] = []
    skipped: list[str] = []
    plan = directfill.Plan()
    steps = 0

    stopped = False
    for step in range(_MAX_STEPS):
        # Between pages, for the same reason the agent stops between steps:
        # a page that has been filled is left filled, and the next one is
        # simply never started. Mid-page there is nothing safe to interrupt
        # — fill_rounds is already typing.
        if should_stop and should_stop():
            stopped = True
            log.info("direct: stopping after page %d at the user's request", steps)
            break
        steps = step + 1
        if step:
            fields = learning._parse_harvest(
                await _read_or_empty(session, directfill.READ_JS))
            if not fields:
                break
            if on_step:
                on_step(f"Page {steps}: {len(fields)} field(s)…")

        n, landed, plan, seen, step_why = await fill_rounds(
            session, fields, role, profile, demographics=demographics,
            llm=llm, explorative=explorative, tone=tone, on_step=on_step)
        filled += n
        guesses += landed
        all_fields += seen
        why = why or step_why
        unanswered += plan.unanswered
        declined += plan.declined
        consents += plan.consents
        skipped += plan.skipped

        if not await _advance(session, on_step):
            break

    if no_model:
        why = "no-model"
    if steps > 1:
        log.info("direct: worked through %d page(s)", steps)
    plan.unanswered = list(dict.fromkeys(unanswered))
    plan.consents = list(dict.fromkeys(consents))
    plan.skipped = list(dict.fromkeys(skipped))
    # A file field is not something the profile can answer, so plan_fill
    # listed it as needing the user. It no longer does.
    plan.unanswered = [q for q in plan.unanswered if q not in attached]

    # "Review and submit" is a claim that Sushi has finished, and this pass
    # is not always the end of the run. In Fill mode "auto" the caller hands
    # whatever is still unanswered to the agent, which then goes on filling
    # the same application — so the pill sat there saying the form was ready
    # for review while it was visibly still being filled. Said here rather
    # than by the caller because the pill lives on this session, and the gap
    # between the two passes is a cover letter and an agent starting up:
    # tens of seconds with nothing to say for itself.
    # A run that was stopped has not handed anything over: the caller is
    # about to be told to stop too, and a pill saying "asking the model"
    # would be describing something that is not going to happen.
    plan.declined = [q for q in dict.fromkeys(declined) if q in plan.unanswered]
    worth_handing = [q for q in plan.unanswered if q not in plan.declined]
    if may_hand_over and plan.unanswered and not worth_handing:
        log.info("direct: %d question(s) left, all already put to the model; "
                 "not starting the agent", len(plan.unanswered))
    handing_over = may_hand_over and bool(worth_handing) and not stopped
    await _set_badge(
        session,
        (f"{len(plan.unanswered)} question(s) left · asking the model"
         if handing_over else
         f"Check the {len(guesses)} amber field(s), then submit"
         if guesses else "Review and submit · Sushi filled what it knew"),
        hot=handing_over)
    if session not in _open_sessions:
        _open_sessions.append(session)
    if handing_over:
        # No watcher while the agent has this tab. It banks what it reads as
        # ``human`` — the highest authority in the bank, above anything the
        # agent or the lookup decided — on the reasonable assumption that
        # the only person typing into a form left open for review is the
        # user. The agent is about to type into this very form, and every
        # answer it invents would be filed as one the user had confirmed.
        # ``_run`` starts its own watcher when it hands the form back.
        watcher.stop()
    else:
        # Baselined on what this pass just wrote, for the same reason the
        # agent pass is: the loop about to start would otherwise read
        # Sushi's own answers back out of the form and file them as the
        # user's. Taken from the plan rather than from the page — the
        # values are the same and this costs no round trip.
        watcher.set_baseline({learning.normalise(f.label or f.key): f.value
                              for f in plan.fills if f.value})
        _spawn_watch(session, watcher, _tab)

    known = filled - len(guesses)
    note = (("Stopped partway. " if stopped else "")
            + f"Filled {max(known, 0)} field(s) from your profile and answer "
            f"bank"
            + (f" across {steps} pages" if steps > 1 else "")
            + ". Nothing was submitted.")
    problems = file_problems(cv_path, letter_path)
    if problems:
        note = " ".join(problems) + " " + note
    if guesses:
        note += (f" {len(guesses)} more were worked out from your CV rather "
                 f"than looked up. They are outlined in amber in the form "
                 f"and listed below. Check them before you submit; none of "
                 f"them has been saved to your answer bank.")
    if plan.unanswered:
        note += (f" {len(plan.unanswered)} question(s) need you. They are "
                 f"listed below and the browser is open.")
        # And why Sushi did not have a go at them itself. Saying only "18
        # questions need you" when the switch that would have answered
        # them is off reads as the feature being broken, which is a fair
        # reading of a program that will not say which of the two it is.
        if why == "off":
            note += (" Sushi did not try to work these out because "
                     "Explorative is off. Turn it on beside Fill, or in "
                     "Settings → Apply settings, and it will answer them "
                     "from your CV and flag each one as a guess.")
        elif why == "no-model":
            note += (" Explorative is on, but no model is set up. Choose "
                     "one in Settings → Model.")
        elif why:
            note += f" Explorative is on but the model would not answer: {why}"
    if plan.consents:
        # Said, not shown as a question: there is nothing to type and nothing
        # to remember, and agreeing to a privacy notice is yours to do.
        note += (f" {len(plan.consents)} box(es) are yours to tick in the "
                 f"form, because Sushi does not agree to anything on your behalf.")
    if (settings.autosubmit and not handing_over and not stopped
            and not plan.unanswered and not plan.consents and filled > 0):
        # "Press Submit for me" used to mean nothing on this path: only the
        # agent could submit, so a form this pass finished completely sat
        # waiting with every field filled.
        if await _press_submit(session, on_step):
            watcher.stop()
            _mark_sent(role.url)
            await _set_badge(session, "Submitted by Sushi")
            return FillResult(
                ok=True, submitted=True, left_open=True,
                message=note.replace(". Nothing was submitted.", ".")
                + " Every required field was filled, so Sushi submitted it.",
                unanswered=[], guessed=[(g.label, g.value) for g in guesses],
                compose_skipped=why, tab=_tab or "")
    return FillResult(ok=filled > 0, submitted=False, left_open=True,
                      message=note, unanswered=plan.unanswered,
                      guessed=[(g.label, g.value) for g in guesses],
                      compose_skipped=why, tab=_tab or "",
                      declined=plan.declined)


def fill_directly(role: Role, profile: Profile, settings: Settings, *,
                  cv_path: str = "", letter_path: str = "",
                  on_step: Callable[[str], None] | None = None,
                  on_letter_needed: Callable[[], str] | None = None,
                  explorative: bool = False,
                  compose: bool = False,
                  tone: str = "",
                  may_hand_over: bool = False,
                  continue_in: str = "",
                  should_stop: Callable[[], bool] | None = None) -> FillResult:
    """Blocking wrapper for the lookup path. Call from a worker thread.

    ``on_letter_needed`` is called only if the form turns out to have a
    cover-letter field, and should return a path to one.

    With neither ``explorative`` nor ``compose`` this makes no model call at
    all, which is the point of the path. ``compose`` writes the free-text
    questions; ``explorative`` answers everything left, flagged.

    Pass ``may_hand_over`` when this is the first of two passes, so the page
    does not announce itself finished while the agent is still to come, and
    ``continue_in`` when this is the *second* of two, so it finishes the form
    already open in that tab instead of starting another copy of it.
    """
    from . import throttle
    with throttle.filling():        # the background search waits meanwhile
        def go():
            return _run_on_loop(
                _run_direct(role, profile, settings, cv_path, letter_path,
                            on_step, on_letter_needed, explorative=explorative,
                            compose=compose, tone=tone,
                            may_hand_over=may_hand_over,
                            continue_in=continue_in, should_stop=should_stop))
        try:
            try:
                return go()
            except AutofillError as exc:
                # Chrome exits at launch while the previous one is still
                # letting go of the profile. A moment later it starts fine.
                if "Could not start the browser" not in str(exc) or (
                        should_stop and should_stop()):
                    raise
                log.info("direct: the browser would not start; trying again")
                time.sleep(4)
                return go()
        except Exception as exc:                        # noqa: BLE001
            # The browser was closed under the fill ("Finish & close
            # browsers", or the window shut). That is a stop, not a crash.
            if not _browser_gone(exc):
                raise
            log.info("direct: the browser closed during the fill (%s)",
                     type(exc).__name__)
            return FillResult(ok=False, submitted=False, left_open=False,
                              message="Stopped: the browser was closed "
                                      "before this form was finished.",
                              unanswered=[])


def _browser_gone(exc: BaseException) -> bool:
    """True when ``exc`` means the browser or its connection went away."""
    names = {"ConnectionClosedError", "ConnectionClosedOK",
             "ConnectionClosed", "ConnectionError"}
    e: BaseException | None = exc
    while e is not None:
        text = str(e)
        if type(e).__name__ in names or any(t in text for t in (
                "Client is stopping", "user closed the browser",
                "SessionManager not initialized",
                "CDP client not initialized", "WebSocket connection closed",
                "no close frame")):
            return True
        e = e.__cause__ or e.__context__
    return False


def fill_application(role: Role, profile: Profile, settings: Settings, *,
                     cv_path: str = "", letter_path: str = "",
                     resume_mode: str = "honest",
                     explorative: bool = False,
                     on_step: Callable[[str], None] | None = None,
                     continue_in: str = "",
                     should_stop: Callable[[], bool] | None = None) -> FillResult:
    """Blocking wrapper. Call from a worker thread, never the UI thread.

    Runs on a persistent background loop so the browser can be left open after
    the agent stops (see ``_run``).

    ``continue_in`` is the tab a lookup pass has already part-filled — pass
    ``FillResult.tab`` from it, and the agent finishes that form instead of
    opening a second one and starting it again.
    """
    if not settings.llm_enabled:
        raise AutofillError("No API key configured. Open Settings and add one.")
    from . import throttle
    with throttle.filling():        # the background search waits meanwhile
        return _run_on_loop(_run(role, profile, settings, cv_path, letter_path,
                                 on_step, resume_mode, explorative,
                                 continue_in=continue_in,
                                 should_stop=should_stop))


#: Where a user signs in once so every later application inherits it.
SIGNIN_URLS = {
    "Google": "https://accounts.google.com/",
    "LinkedIn": "https://www.linkedin.com/login",
    "Greenhouse": "https://my.greenhouse.io/applications",
    "Workday": "https://www.myworkday.com/",
}


def open_for_signin(url: str) -> None:
    """Open the persistent browser profile so the user can sign in by hand.

    Sushi must never type someone's Google password — not into a form, not
    from a setting, not from the answer bank, which explicitly refuses to
    learn credentials. But it does not have to: every application runs in one
    persistent Chromium profile, so a sign-in done *once* in that profile is
    still there for every application afterwards.

    This is that once. The window is left open and belongs to the user; the
    agent inherits the cookies next time it runs.
    """
    from dataclasses import replace as _replace

    from .config import load_settings

    settings = _replace(load_settings(), headless=False)

    async def go() -> None:
        # Share the browser, like everything else does. Launching a second
        # Chromium on the same profile directory is what produced "Browser
        # process exited before CDP became available".
        session, reused = await _session_for_apply(settings)
        if reused:
            await _set_badge(session, "Sign in · Sushi will remember")
        else:
            await _brand(session, "Sign in · Sushi will remember", hot=False)
            try:
                await session.start()
            except Exception as exc:
                # Not swallowed any more. Starting is the step that fails,
                # and hiding it only moved the error one line down, to an
                # assertion about an uninitialised CDP client that says
                # nothing about what actually went wrong.
                raise AutofillError(
                    f"Could not start the browser: {exc}") from exc
        cdp = await _cdp(session, focus=True)
        try:
            await cdp.cdp_client.send.Page.navigate(
                {"url": url}, session_id=cdp.session_id)
        except Exception:
            # Older/newer shapes of the CDP wrapper; the page is open either
            # way, so fall back to navigating from inside it.
            await _evaluate(session, f"location.href = {url!r}")
        if session not in _open_sessions:
            _open_sessions.append(session)

    _run_on_loop(go(), timeout=90)


# --- browsing with the Fill button on the page ------------------------------
#
# The other way into an application: instead of pasting a link into Sushi and
# pressing Fill, you read the job the way you would read any job, and the
# button is already there when you decide. Same browser, same profile, same
# pipeline underneath — the only new thing is who starts it.

#: Where a click on the button goes. Set by the UI, which is the only layer
#: that can turn a URL into a Role and start an application; this module is
#: not allowed to import it.
_on_fill: Callable[[str, str], None] | None = None


def set_fill_handler(fn: Callable[[str, str], None] | None) -> None:
    """Register what happens when somebody presses the button.

    Called with ``(url, title)``, from the browser loop's thread — so the
    handler must hand straight over to the UI thread rather than touch a
    widget itself.
    """
    global _on_fill
    _on_fill = fn


async def _watch_bubble(session: Any, target: str | None,
                        accept: tuple[str, ...] = ("fill",)) -> None:
    """Drain the button's queue until the browser goes away.

    Poll rather than a binding: see the note in :mod:`sushi.bubble`. Any
    failure to evaluate means the tab or the browser has been closed, which
    is a normal end to this loop rather than an error worth reporting.

    ``accept`` is which requests this tab is allowed to make. A page the
    user is browsing can ask to be filled; a form Sushi has already filled
    and left open for review can only ask to be learned from — pressing the
    pill there must not quietly start the same application a second time.
    """
    while True:
        await asyncio.sleep(0.4)
        try:
            raw = await _evaluate(session, bubble.drain_script(), target=target)
        except Exception:
            return
        try:
            queued = json.loads(raw or "[]")
        except (TypeError, ValueError):
            continue
        for req in queued if isinstance(queued, list) else []:
            if not isinstance(req, dict) or req.get("type") not in accept:
                continue
            if req.get("type") == "learn":
                await _learn_now(session, target)
                continue
            url = str(req.get("url") or "")
            if not url:
                continue
            log.info("bubble: fill requested for %s", url[:120])
            handler = _on_fill
            if handler is None:
                await _bubble_state(session, "error",
                                    "Sushi is not ready — open the app first",
                                    target)
                continue
            try:
                handler(url, str(req.get("title") or ""))
            except Exception as exc:          # a bad handler is not the page's problem
                log.warning("bubble: handler failed: %s", exc)
                await _bubble_state(session, "error", "", target)


async def _learn_now(session: Any, target: str | None) -> None:
    """Bank this form exactly as it stands. The Learn button's whole job.

    The polling watcher deliberately waits for a value to survive two reads
    before it keeps it, so that typing "London" one letter at a time does
    not bank "Lond". A press of this button is the user saying the form is
    finished and correct, which is the signal the poll is trying to infer —
    so it reads once to see what is there and then banks the lot without
    waiting for it to settle again.
    """
    watcher = _watch_by_tab.get(target or "")
    if watcher is None or getattr(watcher, "stopped", False):
        await _learn_state(session, "error", "", target)
        return
    try:
        learned = await watcher.poll()
        learned += await watcher.flush()
    except Exception as exc:                          # noqa: BLE001
        log.info("bubble: could not learn from this form (%s)", exc)
        await _learn_state(session, "error", "", target)
        return
    log.info("bubble: learned %d answer(s) on request", learned)
    await _learn_state(
        session, "done",
        # The press is answered in the words of what was asked of it, with
        # the count kept on the end because "how many" is the part worth
        # knowing twice.
        f"Learnt from this form · {learned} new" if learned
        # Nothing new is a real outcome and a common one — press it twice
        # and the second press is correctly a no-op. Saying so is better
        # than a tick that cannot be told apart from a broken button.
        else "Already had these answers", target)


async def _learn_state(session: Any, state: str, text: str = "",
                       target: str | None = None) -> None:
    try:
        await _evaluate(session, bubble.set_learn_script(state, text),
                        target=target)
    except Exception:
        pass


async def _bubble_state(session: Any, state: str, text: str = "",
                        target: str | None = None) -> None:
    try:
        await _evaluate(session, bubble.set_state_script(state, text), target=target)
    except Exception:
        pass
    # And the checklist beside it: which fields are in, which still need
    # the user. Installs once, then keeps itself current.
    try:
        from .checklist import CHECKLIST_JS
        await _evaluate(session, CHECKLIST_JS, target=target)
    except Exception:
        pass


def set_bubble_state(state: str, text: str = "") -> None:
    """Re-label the button from the app: 'idle', 'busy', 'done', 'error'.

    Best effort by design. The browser may have been closed between the
    click and the answer, and that is not worth an error in the UI.
    """
    try:
        _run_on_loop(_bubble_state(_browse_session[0], state, text,
                                   _browse_target[0]), timeout=10)
    except Exception:
        pass


#: The tab the user is browsing in, so the button can be re-labelled later.
#: A one-item list rather than a global rebind, so the helpers above can be
#: written without ``global``.
_browse_session: list[Any] = [None]
_browse_target: list[str | None] = [None]


def open_for_browsing(url: str) -> None:
    """Open a job page in Sushi's browser, with the Fill button on it.

    Shares the one persistent profile like everything else here, so the
    sign-ins already done carry over — which is the difference between the
    button filling a Workday form and the button landing on a login screen.
    """
    from dataclasses import replace as _replace

    from .config import load_settings

    settings = _replace(load_settings(), headless=False)

    async def go() -> None:
        session, reused = await _session_for_apply(settings, url)
        target = getattr(session, "_sushi_target", None)
        if not reused:
            try:
                await session.start()
            except Exception as exc:
                raise AutofillError(f"Could not start the browser: {exc}") from exc

        # *After* start, and that is the whole of it: registering an init
        # script needs a CDP connection, and before `start()` there is none —
        # it raises a bare AssertionError, which the guard below then threw
        # away. The page opened, the button did not, and nothing was logged.
        # Registered here it covers every document the tab loads afterwards,
        # which is what gets the button through an ATS's own redirects.
        try:
            await session._cdp_add_init_script(bubble.script(always=True))
        except Exception as exc:
            log.info("bubble: init script not registered (%s)", exc)

        cdp = await _cdp(session, focus=True, target=target)
        try:
            await cdp.cdp_client.send.Page.navigate(
                {"url": url}, session_id=cdp.session_id)
        except Exception:
            await _evaluate(session, f"location.href = {url!r}", target=target)

        # And the page in front of us right now, which may have started
        # loading before any of the above. bubble.js returns early if it is
        # already there, so arriving twice costs nothing.
        await asyncio.sleep(1.2)
        try:
            await _evaluate(session, bubble.script(always=True), target=target)
            log.info("bubble: on the page at %s", url[:120])
        except Exception as exc:
            log.warning("bubble: could not inject (%s)", exc)

        if session not in _open_sessions:
            _open_sessions.append(session)
        _browse_session[0], _browse_target[0] = session, target
        asyncio.ensure_future(_watch_bubble(session, target))

    _run_on_loop(go(), timeout=90)


def learn_answers(pairs: dict[str, str]) -> int:
    """Remember explicit question/answer pairs. Returns how many landed.

    Kept for callers that have pairs in hand already; the normal path is the
    watcher, which needs nobody to remember to call anything.
    """
    return learning.learn_pairs(pairs.items(), source="human")
