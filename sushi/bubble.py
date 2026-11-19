"""The floating Fill button, and the two ways it gets onto a page.

The button itself is ``data/bubble.js`` and is deliberately host-agnostic: it
draws a pill, and when it is clicked it hands a small dict to whatever
``window.__sushiHost.send`` turns out to be. This module supplies that host
for the browser Sushi drives itself; ``extension/`` supplies the other one,
loading the *same* file so there is one button to fix rather than two that
drift.

Sushi's own browser has no message channel back to Python — the session is
driven over CDP, one ``Runtime.evaluate`` at a time, and browser-use has
moved its page API around enough that bindings are not worth depending on.
So the host here queues: a click pushes onto an array on ``window``, and the
Python side drains that array every few hundred milliseconds with the same
``Runtime.evaluate`` everything else in :mod:`sushi.autofill` uses. The
latency nobody can perceive is the price of not having a transport that
breaks on the next browser-use release.
"""
from __future__ import annotations

import json
from pathlib import Path

#: The button, shared with the extension. Read once per call rather than
#: cached, so editing it during development does not need a restart.
ASSET = Path(__file__).resolve().parent / "data" / "bubble.js"


def source() -> str:
    """The button's own code, with no host attached."""
    return ASSET.read_text(encoding="utf-8")


def script(*, always: bool = True, label: str = "") -> str:
    """The button plus the host Sushi's own browser uses.

    Registered as an init script, so it survives the navigations an ATS does
    on its way from a posting to a form.
    """
    host = f"""
(() => {{
  if (!window.__sushiOutbox) window.__sushiOutbox = [];
  window.__sushiHost = {{
    always: {str(bool(always)).lower()},
    label: {json.dumps(label)},
    // Sushi's own browser drains the queue, so it can answer a "learn from
    // this form" request. The extension's host cannot — it has nowhere to
    // send one — and leaving this off is how the button knows not to offer.
    learn: true,
    send(payload) {{ window.__sushiOutbox.push(payload); }}
  }};
}})();
"""
    return host + source()


def drain_script() -> str:
    """Take everything the button has queued, and leave the queue empty.

    Returns JSON rather than an array: ``Runtime.evaluate`` with
    ``returnByValue`` will hand back structured values, but a string crosses
    every version of that API the same way.
    """
    return ("(() => { const q = window.__sushiOutbox || []; "
            "window.__sushiOutbox = []; return JSON.stringify(q); })()")


def set_state_script(state: str, text: str = "") -> str:
    """Re-label the button from outside: 'idle', 'busy', 'done', 'error'."""
    return (f"window.__sushiBubble && window.__sushiBubble.set("
            f"{json.dumps(state)}, {json.dumps(text)});")


def set_learn_script(state: str, text: str = "") -> str:
    """Re-label the Learn button: 'idle', 'busy', 'done', 'error'.

    Separate from :func:`set_state_script` because the two pills report
    different things and must not overwrite each other: a fill finishing
    should not wipe "Learned 6 answer(s)" off the screen, and learning
    should not claim the form has been filled.
    """
    return (f"window.__sushiBubble && window.__sushiBubble.setLearn("
            f"{json.dumps(state)}, {json.dumps(text)});")


def revive_script() -> str:
    """Put the button back after the user has dismissed it."""
    return "window.__sushiBubble && window.__sushiBubble.revive();"
