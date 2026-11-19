"""The Fill button, in a real browser.

``qa_bubble.py`` covers the Python either side of the button. This drives the
button itself: it loads ``sushi/data/bubble.js`` — the file, not a copy — into
a page that looks like a job posting, and checks the three things that can
only be true or false on screen.

  * it draws, and says what it is for
  * clicking it hands over a payload naming the page it was clicked on
  * it survives the page being rewritten under it, which is what every
    single-page ATS does between the posting and the form

Writes ``qa/bubble_idle.png`` and ``qa/bubble_busy.png`` to look at.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SHOTS = Path(__file__).resolve().parent / "shots"
RESULTS: list[tuple[str, str, str]] = []


def check(name: str, fn) -> None:
    try:
        RESULTS.append(("ok", name, str(fn() or "")[:140]))
    except Exception as e:                                  # noqa: BLE001
        RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"))


PAGE = """
<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Senior Data Analyst — Kestrel</title></head>
<body style="font:400 15px/1.6 system-ui;margin:0">
<main id="body" style="max-width:640px;margin:0 auto;padding:48px 24px">
  <h1 style="font-size:28px;margin:0 0 4px">Senior Data Analyst</h1>
  <div style="color:#666;margin-bottom:28px">Kestrel · Remote — Saudi Arabia</div>
  <form onsubmit="return false">
    <label>First name</label><br><input id="first"><br>
    <label>Email</label><br><input id="email">
  </form>
</main>
<script>
  window.__sushiOutbox = [];
  window.__sushiHost = {
    always: true,
    label: 'Fill this application',
    send(payload) { window.__sushiOutbox.push(payload); }
  };
</script>
<script>BUBBLE_SRC</script>
</body></html>
"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    from sushi import bubble

    # The button has a 🍣 in it, and a Windows console is cp1252.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    SHOTS.mkdir(exist_ok=True)
    # Inlined rather than linked: a file:// script will not load into an
    # https page, and the page has to be on a real origin for the host
    # heuristic below to have anything to read. Still the file on disk.
    html = PAGE.replace("BUBBLE_SRC", bubble.source())

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1000, "height": 640})
        page.goto("https://example.com/careers/senior-data-analyst",
                  wait_until="domcontentloaded")
        page.set_content(html)
        page.wait_for_timeout(400)

        pill = page.locator("#sushi-bubble-7c21")

        def p_draws():
            assert pill.count() == 1, f"{pill.count()} buttons on the page"
            assert pill.is_visible(), "drawn but not visible"
            text = pill.inner_text()
            assert "Fill this application" in text, text
            assert "\U0001F363" in text, "no sushi"
            box = pill.bounding_box() or {}
            # Top right. It sat bottom-right until a long form made the case
            # against it: the bottom of a page is where the submit button
            # and the cookie bar live, and on a short window it is the first
            # thing to go under the fold.
            assert box.get("x", 0) > 600 and box.get("y", 0) < 80, box
            page.screenshot(path=str(SHOTS / "bubble_idle.png"))
            return f"{text.strip()[:48]!r} at ({int(box['x'])}, {int(box['y'])})"
        check("button: draws where it should, saying what it does", p_draws)

        def p_click():
            pill.click()
            page.wait_for_timeout(250)
            sent = page.evaluate("window.__sushiOutbox")
            assert len(sent) == 1, f"{len(sent)} payloads"
            one = sent[0]
            assert one["type"] == "fill", one
            assert one["url"].endswith("/careers/senior-data-analyst"), one["url"]
            assert "Senior Data Analyst" in one["title"], one["title"]
            page.screenshot(path=str(SHOTS / "bubble_busy.png"))
            return f"{one['url'].split('/')[-1]} · {one['title'][:30]}"
        check("button: a press names the page it was pressed on", p_click)

        def p_busy():
            # And it says so, rather than looking like nothing happened.
            assert page.evaluate("window.__sushiBubble.state") == "busy", "not busy"
            assert "Filling it in" in pill.inner_text(), pill.inner_text()
            # A second press while it is working must not queue a second job.
            pill.click()
            page.wait_for_timeout(150)
            assert len(page.evaluate("window.__sushiOutbox")) == 1, "double fired"
            return "shows 'Filling it in…', ignores a second press"
        check("button: busy means busy", p_busy)

        def p_labelled_from_outside():
            page.evaluate("window.__sushiBubble.set('done', 'Filled — check it')")
            page.wait_for_timeout(120)
            assert "Filled" in pill.inner_text(), pill.inner_text()
            return pill.inner_text().strip()[:48]
        check("button: the app can re-label it", p_labelled_from_outside)

        def p_survives_rewrite():
            # What Workday does between a posting and its form.
            page.evaluate("document.body.innerHTML = '<main>the form</main>'")
            page.wait_for_timeout(500)
            again = page.locator("#sushi-bubble-7c21")
            assert again.count() == 1, f"{again.count()} after a rewrite"
            assert again.is_visible(), "lost on a rewrite"
            return "still there, exactly once"
        check("button: survives the page being rewritten", p_survives_rewrite)

        def p_dismiss():
            page.locator("#sushi-bubble-7c21-x").click()
            page.wait_for_timeout(200)
            assert page.locator("#sushi-bubble-7c21").count() == 0, "would not go"
            # And the observer must not put it straight back.
            page.evaluate("document.body.appendChild(document.createElement('p'))")
            page.wait_for_timeout(400)
            assert page.locator("#sushi-bubble-7c21").count() == 0, "came back uninvited"
            return "dismissed, and stays dismissed"
        check("button: can be sent away", p_dismiss)

        def p_top_frame_only():
            """An ATS form arrives in an iframe. The button is not for it.

            Greenhouse serves the form from /embed/job_app inside the
            company's page, and Sushi registers the script on every frame.
            A fixed pill inside that iframe is fixed to the *iframe's*
            viewport, so it sat below the fold and only appeared when the
            window was maximised and the frame's bottom edge came into view.
            """
            framed = browser.new_page(viewport={"width": 1000, "height": 640})
            framed.goto("https://example.com/careers/role",
                        wait_until="domcontentloaded")
            # The host and the button in the top document and in the frame,
            # exactly as an init script registered on all frames would do.
            framed.set_content(
                html.replace("</body>",
                             "<iframe id='ats' style='width:900px;height:1800px'"
                             " srcdoc=\"<body><p>form</p></body>\"></iframe></body>"))
            framed.wait_for_timeout(300)
            frame = framed.frames[1]
            frame.evaluate(
                "([host, src]) => { eval(host); eval(src); }",
                ["window.__sushiOutbox=[];window.__sushiHost={always:true,"
                 "send(p){window.__sushiOutbox.push(p)}};",
                 bubble.source()])
            framed.wait_for_timeout(300)
            top = framed.locator("#sushi-bubble-7c21").count()
            inner = frame.locator("#sushi-bubble-7c21").count()
            framed.close()
            assert top == 1, f"{top} in the page itself"
            assert inner == 0, "the iframe drew one too"
            return "one in the page, none in the form's frame"
        check("button: only the page gets one, never a frame", p_top_frame_only)

        def p_heuristic():
            # The extension's half: not every page gets a button.
            other = browser.new_page()
            other.goto("https://example.com/blog/how-we-hire",
                       wait_until="domcontentloaded")
            other.set_content(
                html.replace("always: true", "always: false"))
            other.wait_for_timeout(400)
            quiet = other.locator("#sushi-bubble-7c21").count()
            other.goto("https://boards.greenhouse.io/acme/jobs/42",
                       wait_until="domcontentloaded")
            other.set_content(html.replace("always: true", "always: false"))
            other.wait_for_timeout(400)
            loud = other.locator("#sushi-bubble-7c21").count()
            other.close()
            assert quiet == 0, "offered on a blog post"
            assert loud == 1, "missed a greenhouse board"
            return "quiet on a blog, there on a job board"
        check("button: in your own Chrome it picks its moments", p_heuristic)

        browser.close()

    width = max(len(n) for _, n, _ in RESULTS)
    for status, name, detail in RESULTS:
        print(f"  {status:<5} {name:<{width}}  {detail}")
    failed = sum(1 for s, _, _ in RESULTS if s != "ok")
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    print(f"screenshots in {SHOTS}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
