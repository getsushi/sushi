"""Open a page the way the app does, and check the button is on it.

``qa_bubble_browser.py`` proves the button works in a browser. This proves
the *delivery*: ``autofill.open_for_browsing`` against a real Chromium, over
the real CDP connection, with the real init-script registration — the three
steps between "the file is correct" and "it is on the page in front of you".

Runs on a throwaway browser profile so it cannot collide with the profile
the app itself is holding open.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

URL = "https://example.com/"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    from sushi import autofill

    # A profile of its own: Chromium allows one process per profile, so
    # borrowing the app's would either fail or steal its browser.
    autofill.BROWSER_PROFILE_DIR = Path(tempfile.mkdtemp(prefix="sushi-bubble-"))

    pressed: list[tuple[str, str]] = []
    autofill.set_fill_handler(lambda url, title: pressed.append((url, title)))

    print(f"opening {URL} …")
    autofill.open_for_browsing(URL)

    session = autofill._browse_session[0]
    target = autofill._browse_target[0]
    assert session is not None, "no session was recorded"

    def js(expr: str):
        return autofill._run_on_loop(
            autofill._evaluate(session, expr, target=target), timeout=20)

    ok = True

    def check(name: str, fn) -> None:
        nonlocal ok
        try:
            print(f"  ok    {name}: {fn()}")
        except Exception as e:                              # noqa: BLE001
            ok = False
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")

    # Give the page a moment to finish loading before asking it anything.
    time.sleep(2.5)

    check("the page is the one asked for",
          lambda: js("location.href"))
    check("the button's code reached the page",
          lambda: js("String(!!window.__sushiBubble)") or "false")
    check("the pill is in the DOM",
          lambda: js("(document.getElementById('sushi-bubble-7c21')||{}).innerText || 'MISSING'"))
    check("the host is attached",
          lambda: js("String(!!(window.__sushiHost && window.__sushiHost.send))"))

    def press() -> str:
        # What a click does, without needing a mouse.
        js("document.getElementById('sushi-bubble-7c21').click()")
        for _ in range(20):
            if pressed:
                return f"{pressed[0][0]} · {pressed[0][1]}"
            time.sleep(0.3)
        raise AssertionError("the press never reached Python")
    check("a press reaches the app", press)

    check("and the button says it is working",
          lambda: js("window.__sushiBubble.state"))

    autofill.set_fill_handler(None)
    try:
        autofill.close_open_sessions()
    except Exception:
        pass
    print("PASSED" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
