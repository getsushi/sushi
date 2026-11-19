"""A page that stops answering must not stop the run.

``_evaluate`` awaited ``Runtime.evaluate`` with nothing bounding it. A
renderer that stops answering is not a rare event on an ATS — Greenhouse
parses an uploaded CV on the main thread, and an evaluate against a page in
that state never returns. So the await never returned either: no exception,
no log line, no next step. Observed on a real application — the last log
entry was the model call that wrote the cover letter, and six minutes later
the pill still read "Attaching …pdf…" over a form with nothing in it.

The fix is a timeout, and a split between the reads that *are* the job and
the reads that are an improvement on it. Losing one of the latter costs a
round of filling; raising out of one costs the application.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sushi import autofill  # noqa: E402


class _Client:
    """A CDP client whose evaluate hangs, or answers, on demand."""

    def __init__(self, hang: bool, value: str = "ok") -> None:
        self._hang = hang
        self._value = value
        self.calls = 0

        class _Send:
            class Runtime:
                @staticmethod
                async def evaluate(_params, session_id=None):
                    outer.calls += 1
                    if outer._hang:
                        await asyncio.sleep(3600)      # never answers
                    return {"result": {"value": outer._value}}
        outer = self
        self.send = _Send()


class _Session:
    def __init__(self, hang: bool, value: str = "ok") -> None:
        self.cdp_client = _Client(hang, value)
        self.session_id = "s1"


@pytest.fixture
def quick_timeout(monkeypatch):
    """A tenth of a second, so the test is a test and not a wait."""
    monkeypatch.setattr(autofill, "_EVAL_TIMEOUT", 0.1)

    async def _cdp(session, focus=False, target=None):
        return session
    monkeypatch.setattr(autofill, "_cdp", _cdp)


def test_a_page_that_never_answers_raises_instead_of_hanging(quick_timeout):
    session = _Session(hang=True)
    started = time.monotonic()

    with pytest.raises(autofill.PageBusy):
        asyncio.run(autofill._evaluate(session, "1+1"))

    assert time.monotonic() - started < 5, "it hung rather than timing out"


def test_a_page_that_answers_is_unaffected(quick_timeout):
    session = _Session(hang=False, value="the form")
    assert asyncio.run(autofill._evaluate(session, "1+1")) == "the form"


def test_an_incidental_read_degrades_to_nothing(quick_timeout):
    """``_read_or_empty`` is for the reads that improve a run, not make it."""
    session = _Session(hang=True)
    started = time.monotonic()

    assert asyncio.run(autofill._read_or_empty(session, "read()")) == ""

    assert time.monotonic() - started < 5


def test_marking_a_field_never_takes_the_run_down(quick_timeout):
    """A ring round a text box is not worth losing an application over."""
    session = _Session(hang=True)
    asyncio.run(autofill._mark(session, ["first_name", "email"], "working"))


def test_marking_nothing_does_not_touch_the_page(quick_timeout):
    session = _Session(hang=False)
    asyncio.run(autofill._mark(session, [], "working"))
    assert session.cdp_client.calls == 0


def test_a_script_error_is_still_reported(quick_timeout, monkeypatch):
    """A timeout must not swallow the page telling us the script was wrong."""
    session = _Session(hang=False)

    async def _boom(_params, session_id=None):
        return {"exceptionDetails": {"text": "ReferenceError: nope"}}
    monkeypatch.setattr(session.cdp_client.send.Runtime, "evaluate", _boom)

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(autofill._evaluate(session, "nope()"))
    assert not isinstance(caught.value, autofill.PageBusy)
    assert "ReferenceError" in str(caught.value)
