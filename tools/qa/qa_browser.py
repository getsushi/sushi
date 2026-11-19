"""QA for the two things that only exist inside a real page.

``learning.HARVEST_JS`` reads an application form's fields, and
``branding.init_script`` stamps Sushi onto the browser. Neither can be tested
by importing Python — they are JavaScript that has to run against a DOM. This
loads a synthetic ATS form in headless Chromium and checks both.

The form deliberately mixes the label patterns real boards use, because that
is where a scraper breaks: Greenhouse uses ``<label for>``, Lever wraps the
input in a label, Workday leans on ``aria-labelledby``, and radio groups put
the question in a ``<legend>`` with the answer in the option's own label.

Run from the sushi/ directory:  python qa_browser.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# The console here is cp1252; the badge text contains an emoji.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# The suite runs against a scratch database so the user's real answer bank is
# untouched — but LOCALAPPDATA is also where Playwright keeps its browsers, so
# point it back at the real one before the override.
_REAL_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      str(Path(_REAL_LOCALAPPDATA) / "ms-playwright"))
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="sushi-browserqa-")

FORM = """<!doctype html>
<html><head><meta charset="utf-8"><title>Apply - Acme</title></head><body>
<h1>Application</h1>
<form>
  <!-- Greenhouse style: label[for] -->
  <label for="fn">First name *</label>
  <input id="fn" name="first_name" value="Rowan">

  <label for="em">Email *</label>
  <input id="em" name="email" type="email" value="r.hartley@example.com">

  <!-- Lever style: input wrapped in its label -->
  <label>Current city
    <input name="city" value="Bristol">
  </label>

  <!-- Workday style: aria-labelledby -->
  <div id="q-notice">What is your notice period?</div>
  <input aria-labelledby="q-notice" name="notice" value="One month">

  <!-- aria-label only -->
  <input aria-label="LinkedIn profile" name="li" value="linkedin.com/in/example">

  <!-- a select -->
  <label for="auth">Are you legally authorised to work in the UK?</label>
  <select id="auth" name="auth">
    <option value="">Please select</option>
    <option value="y" selected>Yes, I am a British citizen</option>
    <option value="n">No</option>
  </select>

  <!-- a radio group: question in the legend, answer in the option label -->
  <fieldset>
    <legend>Do you require visa sponsorship?</legend>
    <label><input type="radio" name="spon" value="y"> Yes</label>
    <label><input type="radio" name="spon" value="n" checked> No</label>
  </fieldset>

  <!-- free text -->
  <label for="why">Why do you want to work here?</label>
  <textarea id="why" name="why">Because the routing work is the problem I know best.</textarea>

  <!-- must never be learned -->
  <label for="pw">Password</label>
  <input id="pw" name="pw" type="password" value="hunter2">

  <label for="otp">Verification code</label>
  <input id="otp" name="otp" autocomplete="one-time-code" value="123456">

  <!-- demographic: learned only on opt-in -->
  <label for="eth">What is your ethnicity?</label>
  <select id="eth" name="eth">
    <option value="a" selected>Prefer not to say</option>
  </select>

  <!-- empty and hidden fields must be ignored -->
  <label for="sal">Salary expectation</label>
  <input id="sal" name="sal" value="">
  <input type="hidden" name="csrf" value="should-not-appear">
  <label for="gone">Hidden field</label>
  <input id="gone" name="gone" value="invisible" style="display:none">
</form>
</body></html>
"""

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, fn):
    try:
        detail = fn()
        RESULTS.append(("PASS", name, str(detail)[:160] if detail else ""))
        return True
    except Exception as e:
        RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"))
        return False


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed — skipping browser QA")
        return 0

    from sushi import branding, learning

    tmp = Path(tempfile.mkdtemp(prefix="sushi-form-"))
    page_path = tmp / "apply.html"
    page_path.write_text(FORM, encoding="utf-8")

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as e:
            print(f"could not launch chromium: {e}")
            print("run: python -m playwright install chromium")
            return 0

        page = browser.new_page()
        # Branding is registered before navigation, as the real session does.
        page.add_init_script(branding.init_script("Applying to Acme"))
        page.goto(page_path.as_uri())
        page.wait_for_timeout(300)

        # --- branding ------------------------------------------------------
        def t_badge():
            el = page.query_selector("#sushi-badge-9f3a")
            assert el is not None, "badge not injected"
            text = el.inner_text()
            assert "Sushi" in text, text
            assert "Applying to Acme" in text, text
            return text.replace("\n", " ")
        check("branding: badge renders in the page", t_badge)

        def t_title():
            title = page.title()
            assert title.startswith("Sushi · "), title
            return title
        check("branding: tab title is stamped", t_title)

        def t_favicon():
            href = page.eval_on_selector("link[rel~='icon'][data-sushi]", "e => e.href")
            assert href.startswith("data:image/svg+xml"), href[:40]
            return href[:46] + "…"
        check("branding: favicon replaced", t_favicon)

        def t_badge_update():
            page.evaluate(branding.set_state_script("Review and submit", False))
            text = page.inner_text("#sushi-badge-9f3a")
            assert "Review and submit" in text, text
            return text.replace("\n", " ")
        check("branding: badge state can be updated", t_badge_update)

        # --- harvesting ----------------------------------------------------
        raw = page.evaluate(learning.HARVEST_JS)
        fields = learning._parse_harvest(raw)
        found = {f["q"]: f["a"] for f in fields}

        def t_found_count():
            assert fields, f"nothing harvested from the form: {raw!r}"
            return f"{len(fields)} fields: {sorted(found)[:4]}…"
        check("harvest: reads the form at all", t_found_count)

        def t_label_for():
            assert any("first name" in q.lower() for q in found), sorted(found)
            v = next(a for q, a in found.items() if "first name" in q.lower())
            assert v == "Rowan", v
            return "label[for] → Rowan"
        check("harvest: label[for] (Greenhouse style)", t_label_for)

        def t_wrapped_label():
            hit = [q for q in found if "current city" in q.lower()]
            assert hit, sorted(found)
            assert found[hit[0]] == "Bristol", found[hit[0]]
            return "wrapping <label> → Bristol"
        check("harvest: wrapped label (Lever style)", t_wrapped_label)

        def t_aria_labelledby():
            hit = [q for q in found if "notice period" in q.lower()]
            assert hit, sorted(found)
            assert found[hit[0]] == "One month", found[hit[0]]
            return "aria-labelledby → One month"
        check("harvest: aria-labelledby (Workday style)", t_aria_labelledby)

        def t_aria_label():
            hit = [q for q in found if "linkedin" in q.lower()]
            assert hit, sorted(found)
            return f"aria-label → {found[hit[0]]}"
        check("harvest: aria-label", t_aria_label)

        def t_select():
            hit = [q for q in found if "authorised" in q.lower()]
            assert hit, sorted(found)
            assert "British citizen" in found[hit[0]], found[hit[0]]
            return f"select → {found[hit[0]]}"
        check("harvest: select reads the chosen option's text", t_select)

        def t_radio():
            hit = [q for q in found if "sponsorship" in q.lower()]
            assert hit, f"radio group question not found: {sorted(found)}"
            assert found[hit[0]].strip().lower() == "no", found[hit[0]]
            return f"{hit[0]} → {found[hit[0]]}"
        check("harvest: radio group uses the legend as the question", t_radio)

        def t_textarea():
            hit = [q for q in found if "why do you want" in q.lower()]
            assert hit, sorted(found)
            assert "routing work" in found[hit[0]]
            return f"textarea → {found[hit[0]][:50]}…"
        check("harvest: textarea", t_textarea)

        def t_excluded():
            lower = " ".join(found).lower()
            vals = " ".join(found.values())
            assert "hunter2" not in vals, "a password value was harvested"
            assert "123456" not in vals, "a one-time code was harvested"
            assert "should-not-appear" not in vals, "a hidden input was harvested"
            assert "invisible" not in vals, "a display:none input was harvested"
            assert "salary" not in lower or not found.get("Salary expectation"), \
                "an empty field was harvested"
            return "password, OTP, hidden, display:none and empty all skipped"
        check("harvest: skips passwords, codes, hidden and empty fields", t_excluded)

        # --- the gates on top of the harvest --------------------------------
        def t_gates():
            keep = [(f["q"], f["a"]) for f in fields
                    if learning.learnable(f["q"], f["a"], field_type=f.get("type", ""))]
            kept = " ".join(q.lower() for q, _ in keep)
            assert "ethnicity" not in kept, "a demographic answer passed the gate"
            assert "notice period" in kept, kept
            with_demo = [(f["q"], f["a"]) for f in fields
                         if learning.learnable(f["q"], f["a"], include_demographics=True,
                                               field_type=f.get("type", ""))]
            assert len(with_demo) > len(keep), "opt-in did not add the demographic answer"
            return f"{len(keep)} learnable, {len(with_demo)} with demographics opted in"
        check("harvest: demographic gate applies to real fields", t_gates)

        # --- the watcher against a page the user edits ----------------------
        # Snapshots are captured here with the sync API and replayed into the
        # watcher, because Playwright's sync API cannot be called from inside
        # an asyncio loop and FormWatcher.poll is a coroutine.
        before = page.evaluate(learning.HARVEST_JS)
        page.fill("#why", "Corrected answer after reading it back.")
        page.wait_for_timeout(100)
        after = page.evaluate(learning.HARVEST_JS)

        def t_watcher():
            import asyncio

            frames = iter([before, before, after, after])

            async def evaluate(_js):
                return next(frames)

            w = learning.FormWatcher(evaluate)

            async def run():
                return [await w.poll() for _ in range(4)]

            # sync_playwright owns a loop on this thread, so asyncio.run here
            # raises; the watcher gets its own thread and its own loop.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(1) as pool:
                counts = pool.submit(lambda: asyncio.run(run())).result(30)
            assert counts[0] == 0, f"learned on the very first poll: {counts}"
            assert counts[1] > 0, f"learned nothing once values settled: {counts}"
            got = learning.recall("Why do you want to work here?")
            assert got == "Corrected answer after reading it back.", got
            return f"polls learned {counts}; the correction overwrote the original"
        check("watcher: learns settled values and picks up a correction", t_watcher)

        # --- explorative mode's flag, in the page ---------------------------
        #
        # The promise is specific and entirely browser-side: a guessed answer
        # is typed in, outlined, and *not* banked — while the identical
        # mechanism keeps banking everything the user types. There is no way
        # to test that from Python, and getting it wrong is the one way this
        # feature does real harm: a guess silently stored as a human answer
        # is retyped into every later form as though it had been checked.
        from sushi import directfill

        guess_page = browser.new_page()
        guess_page.goto(page_path.as_uri())
        guess_page.wait_for_timeout(200)

        plan = directfill.Plan(fills=[
            directfill.Filled(key="sal", label="Salary expectation",
                              value="£65,000", source="guess",
                              confidence="low", basis="typical for seniority"),
        ])
        applied = guess_page.evaluate(directfill.fill_script(plan))

        def t_guess_typed():
            import json
            out = json.loads(applied)
            assert out["filled"] == ["sal"], out
            assert out["guessed"] == ["sal"], out
            assert guess_page.input_value("#sal") == "£65,000"
            return "the guess reached the field"
        check("explorative: a guessed value is actually typed in", t_guess_typed)

        def t_guess_marked():
            assert guess_page.get_attribute("#sal", "data-sushi-guess") == "1"
            outline = guess_page.eval_on_selector(
                "#sal", "e => getComputedStyle(e).outlineColor")
            assert outline == "rgb(197, 138, 0)", outline
            title = guess_page.get_attribute("#sal", "title") or ""
            assert "guessed this" in title and "typical for seniority" in title, title
            return f"outlined {outline}, titled {title[:40]}…"
        check("explorative: the guessed field is flagged in the page",
              t_guess_marked)

        def t_guess_not_banked():
            import asyncio
            from concurrent.futures import ThreadPoolExecutor

            raw_g = guess_page.evaluate(learning.HARVEST_JS)
            harvested = {f["q"]: f for f in learning._parse_harvest(raw_g)}
            hit = [q for q in harvested if "salary" in q.lower()]
            assert hit, f"the guessed field was not harvested at all: {sorted(harvested)}"
            assert harvested[hit[0]].get("guess") is True, harvested[hit[0]]

            # Two identical polls — which is exactly what makes the watcher
            # bank a settled value. It must still refuse this one.
            frames = iter([raw_g, raw_g, raw_g])

            async def evaluate(_js):
                return next(frames)

            w = learning.FormWatcher(evaluate)

            async def run():
                counts = [await w.poll() for _ in range(2)]
                counts.append(await w.flush())
                return counts

            with ThreadPoolExecutor(1) as pool:
                counts = pool.submit(lambda: asyncio.run(run())).result(30)
            got = learning.recall("Salary expectation")
            assert not got, f"a guess was banked as the user's own answer: {got!r}"
            return f"two settled polls and a flush learned {counts}; bank still empty"
        check("explorative: a guess is never banked as the user's answer",
              t_guess_not_banked)

        def t_edit_clears_the_flag():
            import asyncio
            from concurrent.futures import ThreadPoolExecutor

            # A real keystroke, not fill(): the whole discriminator is
            # ``event.isTrusted``, and page.fill dispatches an untrusted one.
            guess_page.click("#sal")
            guess_page.keyboard.press("Control+A")
            guess_page.keyboard.type("£72,000")
            guess_page.wait_for_timeout(150)
            assert guess_page.get_attribute("#sal", "data-sushi-guess") is None, \
                "editing the field left it marked as a guess"

            raw_e = guess_page.evaluate(learning.HARVEST_JS)
            frames = iter([raw_e, raw_e])

            async def evaluate(_js):
                return next(frames)

            w = learning.FormWatcher(evaluate)

            async def run():
                return [await w.poll() for _ in range(2)]

            with ThreadPoolExecutor(1) as pool:
                counts = pool.submit(lambda: asyncio.run(run())).result(30)
            got = learning.recall("Salary expectation")
            assert got == "£72,000", f"the corrected answer was not learned: {got!r}"
            return f"polls learned {counts}; the user's correction is in the bank"
        check("explorative: correcting a guess makes it the user's answer",
              t_edit_clears_the_flag)

        browser.close()

    print()
    width = max(len(n) for _, n, _ in RESULTS) + 2
    for status, name, detail in RESULTS:
        print(f"  {'ok  ' if status == 'PASS' else 'FAIL'}  {name:<{width}} {detail}")
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
