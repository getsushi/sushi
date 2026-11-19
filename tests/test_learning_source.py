"""Who an answer in the bank came from, and the hand-over that got it wrong.

The bank's whole authority rests on one distinction: a value the *agent*
invented is a guess, a value the *user* typed or looked at and left standing
is a correction, and a correction can never be overwritten by a later guess.

Hand-over broke it. When the agent finished, ``leave_open`` started the
polling loop on the form the agent had just typed into — and the loop's rule
("still there at the next poll, so it has been tolerated") fired on answers
nobody had looked at yet. One poll, about two seconds, and every free-text
answer the agent had made up was filed as human-confirmed and announced on
the page as "Learned 6 answer(s) from this form." It was then reused, as
confirmed fact, in every later application.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sushi import learning  # noqa: E402

QUESTION = "Why do you want to work here?"
BY_AGENT = "I admire the mission."
BY_USER = "I have followed the interpretability work for years."


@pytest.fixture
def bank(tmp_path, monkeypatch):
    """An empty answer bank in a temp database.

    ``storage`` does ``from .config import DB_PATH``, so the name it opens
    is its own — patching ``config.DB_PATH`` changes nothing and the tests
    quietly ran against the real bank on this machine, passing alone and
    failing in a suite because they were sharing it. Patch the name the
    module actually reads, and reset the thread-local that caches the
    connection opened from it.
    """
    import sushi.config as cfg
    from sushi import storage

    db = tmp_path / "sushi.db"
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DB_PATH", db)
    monkeypatch.setattr(storage, "DB_PATH", db)
    monkeypatch.setattr(storage, "_LOCAL", type(storage._LOCAL)())
    assert storage.conn().execute(
        "SELECT COUNT(*) n FROM answers").fetchone()["n"] == 0, \
        "the fixture is not isolated — this is the real answer bank"
    return storage


def _watcher(form: dict) -> learning.FormWatcher:
    """A watcher over a one-field form whose value is ``form['v']``."""
    async def evaluate(_js: str) -> str:
        return json.dumps({"url": "https://example.test/apply",
                           "fields": [{"q": QUESTION, "a": form["v"],
                                       "type": "textarea"}]})
    return learning.FormWatcher(evaluate)


def _hand_over(watcher: learning.FormWatcher) -> None:
    """What ``autofill._learn_agent_pass`` does when the agent stops."""
    asyncio.run(watcher.poll())
    snapshot = {}
    for key, (value, question) in list(watcher._pending.items()):
        learning.remember(question, value, source="agent")
        snapshot[key] = value
    watcher.set_baseline(snapshot)


def test_an_agent_answer_does_not_promote_itself(bank):
    form = {"v": BY_AGENT}
    w = _watcher(form)
    _hand_over(w)
    assert learning.source_of(QUESTION) == "agent"

    for _ in range(4):                      # nobody touches the page
        asyncio.run(w.poll())

    assert learning.source_of(QUESTION) == "agent", \
        "the agent's own answer was filed as human-confirmed"
    assert w.stats.learned == 0, \
        "the page was told answers had been learned when none had"


def test_a_real_correction_is_still_learned(bank):
    form = {"v": BY_AGENT}
    w = _watcher(form)
    _hand_over(w)

    form["v"] = BY_USER                     # the user rewrites it
    asyncio.run(w.poll())                   # first sighting: not yet settled
    asyncio.run(w.poll())                   # survived a poll, so it counts

    assert learning.source_of(QUESTION) == "human"
    answers = {a.question: a.answer for a in learning.bank()}
    assert BY_USER in answers.values()


def test_flush_does_not_bank_an_untouched_form(bank):
    """A page going away with Sushi's own answers in it taught nobody anything."""
    w = _watcher({"v": BY_AGENT})
    _hand_over(w)
    assert asyncio.run(w.flush()) == 0


def test_a_baseline_only_covers_the_value_it_was_set_for(bank):
    """Editing a field and putting it back is still the user's answer."""
    form = {"v": BY_AGENT}
    w = _watcher(form)
    _hand_over(w)

    form["v"] = BY_USER                     # off the baseline for good
    asyncio.run(w.poll())
    asyncio.run(w.poll())
    assert learning.source_of(QUESTION) == "human"

    form["v"] = BY_AGENT                    # typed back by hand this time
    asyncio.run(w.poll())
    asyncio.run(w.poll())
    answers = {a.question: a.answer for a in learning.bank()}
    assert BY_AGENT in answers.values()
