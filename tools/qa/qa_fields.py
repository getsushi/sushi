"""Can Sushi actually fill every kind of field a real form has?

``qa_browser.py`` proves the harvester reads labels the four ways boards
write them. This asks the next question, which is the one that decides
whether an application goes out: given a field, does the value *land in it*?

The form below is deliberately hostile, and every awkward thing in it is
copied from a real ATS rather than invented:

- a ``<select>`` whose option text is not its value (Greenhouse)
- a placeholder option that must never be chosen ("Please select")
- a multiple ``<select>`` and a group of checkboxes, where the answer is
  more than one thing
- a radio group whose question is in the legend (Lever)
- a ``number`` field, which refuses "6 years", and a ``date`` field, which
  refuses "1 March 2026" — both silently, leaving an empty required field
- a ``textarea`` with ``maxlength``, which truncates a composed answer
  mid-word without saying so
- a custom combobox: a div with ``role="combobox"`` over a hidden input,
  which is what Greenhouse's current UI and Workday both use, and which is
  not a ``<select>`` at all
- a typeahead that keeps no value until one of *its* options is clicked
- a question that only appears once you have answered another one
- a field below the fold, and a field inside a collapsed section
- disabled, readonly and already-filled fields, none of which are ours

Run from the sushi/ directory:  python qa_fields.py [-v]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REAL_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      str(Path(_REAL_LOCALAPPDATA) / "ms-playwright"))
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="sushi-fieldqa-")

FORM = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Apply - Hostile Corp</title>
<style>
  body { font-family: sans-serif; max-width: 640px; margin: 20px auto; }
  label { display: block; margin-top: 14px; font-weight: 600; }
  input, select, textarea { width: 100%; padding: 6px; box-sizing: border-box; }
  .spacer { height: 1600px; }
  .hidden { display: none; }
  .combo { border: 1px solid #999; padding: 6px; cursor: pointer; }
  .listbox { border: 1px solid #999; margin: 0; padding: 0; list-style: none; }
  .listbox li { padding: 4px 6px; cursor: pointer; }
</style>
</head><body>
<h1>Application</h1>
<form id="apply">

  <!-- ---- plain identity ------------------------------------------------ -->
  <label for="fn">First name *</label>
  <input id="fn" name="first_name" type="text">

  <label for="ln">Last name *</label>
  <input id="ln" name="last_name" type="text">

  <label for="em">Email *</label>
  <input id="em" name="email" type="email">

  <label for="ph">Phone number</label>
  <input id="ph" name="phone" type="tel">

  <label for="li">LinkedIn profile</label>
  <input id="li" name="linkedin" type="url">

  <!-- ---- a number input, which will not take "6 years" ----------------- -->
  <label for="yrs">How many years of experience do you have? *</label>
  <input id="yrs" name="years" type="number" min="0" max="50">

  <!-- ---- a date input, which will not take "1 March 2026" -------------- -->
  <label for="start">What is your earliest start date?</label>
  <input id="start" name="start_date" type="date">

  <!-- ---- a select whose option text is not its value ------------------- -->
  <label for="auth">Are you legally authorised to work in the UK? *</label>
  <select id="auth" name="auth">
    <option value="">Please select</option>
    <option value="AUTH_Y">Yes, I have the right to work</option>
    <option value="AUTH_N">No, I would need sponsorship</option>
  </select>

  <!-- ---- a radio group: question in the legend ------------------------- -->
  <fieldset>
    <legend>Do you require visa sponsorship? *</legend>
    <label><input type="radio" name="spon" value="yes"> Yes</label>
    <label><input type="radio" name="spon" value="no"> No</label>
  </fieldset>

  <!-- ---- a question that only appears once another is answered --------- -->
  <div id="visa-wrap" class="hidden">
    <label for="visa">Which visa do you currently hold? *</label>
    <input id="visa" name="visa_type" type="text">
  </div>

  <!-- ---- a dropdown whose options arrive after another answer ---------- -->
  <!-- Workday and Greenhouse both do this: pick a country, and only then
       does the region list have anything in it. The field is on screen the
       whole time, so "read the new fields" does not find it — it was never
       new, it was empty. -->
  <label for="region">Which region are you based in?</label>
  <select id="region" name="region">
    <option value="">Select a country first</option>
  </select>

  <!-- ---- more than one answer: a multiple select ----------------------- -->
  <label for="langs">Which languages do you speak?</label>
  <select id="langs" name="languages" multiple size="4">
    <option value="en">English</option>
    <option value="fr">French</option>
    <option value="de">German</option>
    <option value="es">Spanish</option>
  </select>

  <!-- ---- more than one answer: a checkbox group ------------------------ -->
  <fieldset>
    <legend>Which days are you available?</legend>
    <label><input type="checkbox" name="days" value="mon"> Monday</label>
    <label><input type="checkbox" name="days" value="tue"> Tuesday</label>
    <label><input type="checkbox" name="days" value="wed"> Wednesday</label>
  </fieldset>

  <!-- ---- a custom combobox: no <select> anywhere ----------------------- -->
  <div id="loc-label">Preferred office location *</div>
  <div id="loc-combo" class="combo" role="combobox" aria-labelledby="loc-label"
       aria-expanded="false" aria-controls="loc-list" tabindex="0">Select…</div>
  <ul id="loc-list" class="listbox hidden" role="listbox"
      aria-labelledby="loc-label">
    <li role="option" data-value="LON">London</li>
    <li role="option" data-value="MAN">Manchester</li>
    <li role="option" data-value="REM">Remote (UK)</li>
  </ul>
  <input id="loc" name="location_choice" type="hidden">

  <!-- ---- a typeahead that keeps nothing until an option is clicked ----- -->
  <label for="uni">Which university did you attend?</label>
  <input id="uni" name="university" type="text" autocomplete="off"
         aria-describedby="uni-hint">
  <div id="uni-hint">Start typing and choose from the list.</div>
  <ul id="uni-list" class="listbox hidden" role="listbox"></ul>

  <!-- ---- a typeahead whose list will never contain the answer ---------- -->
  <!-- Typing into one of these leaves the search text in the box, which
       looks exactly like a committed answer and is not one. -->
  <label for="town">Which town were you born in?</label>
  <input id="town" name="town" type="text" autocomplete="off">
  <ul id="town-list" class="listbox hidden" role="listbox"></ul>

  <!-- ---- a declaration behind a dropdown, not a tick-box --------------- -->
  <!-- Canonical does exactly this, twice. A consent check that only looks
       at checkboxes agrees to both on the candidate's behalf. -->
  <div id="ack-label">Please confirm that you have read and agree to our
    Recruitment Privacy Notice</div>
  <div id="ack-combo" class="combo" role="combobox" aria-labelledby="ack-label"
       aria-controls="ack-list" tabindex="0">Select…</div>
  <ul id="ack-list" class="listbox hidden" role="listbox">
    <li role="option" data-value="ok">Acknowledge/Confirm</li>
  </ul>

  <!-- ---- a protected characteristic the label does not name ------------ -->
  <label for="hispanic_ethnicity">Are you Hispanic/Latino?</label>
  <select id="hispanic_ethnicity" name="hispanic_ethnicity">
    <option value="">Please select</option>
    <option value="y">Yes</option>
    <option value="n">No</option>
  </select>

  <!-- ---- an essay prompt that happens to contain a field's word -------- -->
  <label for="essay">Please share your rationale or evidence for the high
    school performance selections above. Make reference to provincial, state
    or nation-wide scoring systems, rankings or recognition awards.</label>
  <textarea id="essay" name="essay" rows="3"></textarea>

  <!-- ---- a textarea with a hard limit ---------------------------------- -->
  <label for="why">Why do you want to work here? *</label>
  <textarea id="why" name="why" rows="4" maxlength="120"></textarea>

  <!-- ---- not ours to touch --------------------------------------------- -->
  <label for="ref">Your reference number</label>
  <input id="ref" name="refno" type="text" value="REF-99812" readonly>

  <label for="dept">Department</label>
  <input id="dept" name="dept" type="text" value="Engineering" disabled>

  <label for="city">Current city</label>
  <input id="city" name="city" type="text" value="Bristol">

  <!-- ---- a declaration, which is yours to tick ------------------------- -->
  <label><input type="checkbox" id="terms" name="terms">
    I have read and agree to the Privacy Notice</label>

  <!-- ---- a field inside a collapsed section ---------------------------- -->
  <button type="button" id="more-btn">Additional information</button>
  <div id="more" class="hidden">
    <label for="notice">What is your notice period?</label>
    <input id="notice" name="notice" type="text">
  </div>

  <!-- ---- and one a long way down the page ------------------------------ -->
  <div class="spacer"></div>
  <label for="salary">What are your salary expectations?</label>
  <input id="salary" name="salary" type="text">
</form>

<script>
  // Every input and change event this page receives, so a test can prove a
  // React-style form would have seen the value rather than just the DOM.
  window.__events = [];
  document.addEventListener('input', e =>
    window.__events.push(['input', e.target.id || e.target.name, e.isTrusted]), true);
  document.addEventListener('change', e =>
    window.__events.push(['change', e.target.id || e.target.name, e.isTrusted]), true);

  // The conditional question. Answering "yes" to sponsorship reveals it,
  // exactly as Greenhouse and Ashby do.
  document.querySelectorAll('input[name="spon"]').forEach(r =>
    r.addEventListener('change', () => {
      const need = document.querySelector('input[name="spon"]:checked');
      document.getElementById('visa-wrap').classList
        .toggle('hidden', !(need && need.value === 'yes'));
    }));
  // And the select does it too, the other way round.
  document.getElementById('auth').addEventListener('change', e => {
    if (e.target.value === 'AUTH_N') {
      document.getElementById('visa-wrap').classList.remove('hidden');
    }
  });

  // The dependent dropdown: its options appear only once the work
  // authorisation question has been answered.
  document.getElementById('auth').addEventListener('change', e => {
    if (!e.target.value) return;
    const region = document.getElementById('region');
    if (region.options.length > 1) return;
    for (const [v, t] of [['sw', 'South West'], ['ldn', 'Greater London'],
                          ['nw', 'North West']]) {
      const o = document.createElement('option');
      o.value = v; o.textContent = t;
      region.appendChild(o);
    }
  });

  // The custom combobox.
  const combo = document.getElementById('loc-combo');
  const list = document.getElementById('loc-list');
  combo.addEventListener('click', () => {
    list.classList.toggle('hidden');
    combo.setAttribute('aria-expanded', String(!list.classList.contains('hidden')));
  });
  list.querySelectorAll('[role="option"]').forEach(li =>
    li.addEventListener('click', () => {
      combo.textContent = li.textContent;
      combo.setAttribute('data-value', li.dataset.value);
      const h = document.getElementById('loc');
      h.value = li.dataset.value;
      h.dispatchEvent(new Event('change', {bubbles: true}));
      list.classList.add('hidden');
      combo.setAttribute('aria-expanded', 'false');
    }));

  // The typeahead: typing shows options, and only a click commits one.
  const UNIS = ['University of Bristol', 'University of Bath',
                'University of Manchester'];
  const uni = document.getElementById('uni');
  const unilist = document.getElementById('uni-list');
  uni.dataset.committed = '';
  uni.addEventListener('input', () => {
    const q = uni.value.trim().toLowerCase();
    uni.dataset.committed = '';
    unilist.innerHTML = '';
    if (!q) { unilist.classList.add('hidden'); return; }
    UNIS.filter(u => u.toLowerCase().includes(q)).forEach(u => {
      const li = document.createElement('li');
      li.setAttribute('role', 'option');
      li.textContent = u;
      li.addEventListener('click', () => {
        uni.value = u;
        uni.dataset.committed = u;
        unilist.classList.add('hidden');
        uni.dispatchEvent(new Event('change', {bubbles: true}));
      });
      unilist.appendChild(li);
    });
    unilist.classList.remove('hidden');
  });

  document.getElementById('more-btn').addEventListener('click', () =>
    document.getElementById('more').classList.toggle('hidden'));
</script>
</body></html>
"""

#: A company careers page with the form embedded in it, which is what most
#: employers actually publish. There is no form here and no link to one —
#: just a button that loads a Greenhouse iframe. Sushi used to report "no
#: application form was found, and no Apply link to follow" and stop.
CAREERS_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Careers - Example</title></head>
<body>
  <h1>Senior Operations Analyst</h1>
  <p>A job at a company with its own careers site.</p>
  <button id="apply-btn" type="button">Apply</button>
  <div id="slot"></div>
<script>
  document.getElementById('apply-btn').addEventListener('click', () => {
    const f = document.createElement('iframe');
    f.src = 'https://job-boards.greenhouse.io/embed/job_app?token=qa1';
    f.width = 900; f.height = 600;
    document.getElementById('slot').appendChild(f);
  });
</script>
</body></html>
"""

EMBEDDED_FORM = """<!doctype html>
<html><head><meta charset="utf-8"><title>Apply</title></head><body>
<form>
  <label for="fn">First Name</label><input id="fn" name="first_name">
  <label for="em">Email</label><input id="em" name="email" type="email">
  <label for="ph">Phone</label><input id="ph" name="phone" type="tel">
</form>
</body></html>
"""

#: A form that rewrites itself when you attach a CV, which is what Ashby
#: does — "upload your resume here to autofill key application fields".
#: Sushi filled the form and attached the CV afterwards, the page cleared
#: three answers on the way to parsing the file, and the application came
#: back "Missing entry for required field" against every one of them. The
#: DOM said they were filled right up until the upload.
PARSING_FORM = """<!doctype html>
<html><head><meta charset="utf-8"><title>Apply - Parsing Corp</title></head>
<body>
<h1>Application</h1>
<form id="apply">
  <label for="pname">Name *</label>
  <input id="pname" name="pname" type="text" required>
  <label for="pmail">Email *</label>
  <input id="pmail" name="pmail" type="email" required>
  <label for="pcv">Resume *</label>
  <input id="pcv" name="pcv" type="file">
  <label for="pnotice">What is your notice period? *</label>
  <input id="pnotice" name="pnotice" type="text" required>
  <label for="psalary">What are your salary expectations? *</label>
  <input id="psalary" name="psalary" type="text" required>
</form>
<script>
  // Attaching the CV "parses" it: the name is filled in from the file and
  // every question is wiped, because the component re-renders.
  document.getElementById('pcv').addEventListener('change', () => {
    setTimeout(() => {
      document.getElementById('pname').value = 'Rowan Ellis Hartley';
      for (const id of ['pnotice', 'psalary']) {
        document.getElementById(id).value = '';
      }
    }, 150);
  });
</script>
</body></html>
"""

#: An application in four steps, the way Taleo, Workday and iCIMS build
#: one. Sushi filled page one and reported itself finished, so most of
#: these forms were never touched.
#:
#: The last page is the one that matters for safety: "Back", "Next" and
#: "Submit Application" side by side. Sushi has to find Next on steps one
#: to three and then stop — pressing Submit sends an application nobody
#: has read.
STEPPED_FORM = """<!doctype html>
<html><head><meta charset="utf-8"><title>Apply - Stepwise Inc</title></head>
<body>
<h1>Application</h1>
<div id="step"></div>
<div id="nav"></div>
<p id="sent" style="display:none">SUBMITTED</p>
<script>
  const PAGES = [
    `<label for="s1name">Full name *</label>
     <input id="s1name" name="s1name" type="text">
     <label for="s1mail">Email *</label>
     <input id="s1mail" name="s1mail" type="email">`,
    `<label for="s2phone">Phone *</label>
     <input id="s2phone" name="s2phone" type="tel">
     <label for="s2city">Current city *</label>
     <input id="s2city" name="s2city" type="text">`,
    `<label for="s3notice">What is your notice period? *</label>
     <input id="s3notice" name="s3notice" type="text">
     <label for="s3auth">Are you legally authorised to work in the UK? *</label>
     <select id="s3auth" name="s3auth">
       <option value="">Please select</option>
       <option value="y">Yes</option>
       <option value="n">No</option>
     </select>`,
    `<label for="s4ref">How did you hear about us?</label>
     <input id="s4ref" name="s4ref" type="text">`,
  ];
  let page = 0;
  // A real multi-page application remembers what you put on page one when
  // you come back to it — the values live on the server, not in the DOM.
  // A fixture that forgets would let a genuine "the next page wipes the
  // last one" bug pass unnoticed.
  const saved = {};
  function keep() {
    for (const el of document.querySelectorAll('#step input, #step select')) {
      if (el.id) saved[el.id] = el.value;
    }
  }
  function draw() {
    document.getElementById('step').innerHTML = PAGES[page];
    for (const el of document.querySelectorAll('#step input, #step select')) {
      if (el.id && saved[el.id] !== undefined) el.value = saved[el.id];
      el.addEventListener('input', keep);
      el.addEventListener('change', keep);
    }
    const nav = document.getElementById('nav');
    nav.innerHTML = '';
    if (page > 0) {
      const back = document.createElement('button');
      back.type = 'button'; back.textContent = 'Back';
      back.onclick = () => { keep(); page -= 1; draw(); };
      nav.appendChild(back);
    }
    if (page < PAGES.length - 1) {
      const next = document.createElement('button');
      next.type = 'button'; next.textContent = 'Save and Continue';
      next.onclick = () => { keep(); page += 1; draw(); };
      nav.appendChild(next);
    } else {
      // The last page offers Next *and* Submit. Only one of them is ours.
      const more = document.createElement('button');
      more.type = 'button'; more.textContent = 'Next';
      more.disabled = true;
      nav.appendChild(more);
      const send = document.createElement('button');
      send.type = 'button'; send.textContent = 'Submit Application';
      send.onclick = () => {
        document.getElementById('sent').style.display = 'block';
      };
      nav.appendChild(send);
    }
  }
  draw();
</script>
</body></html>
"""

RESULTS: list[tuple[str, str, str]] = []
VERBOSE = "-v" in sys.argv


async def check(name: str, fn) -> bool:
    try:
        detail = await fn()
        RESULTS.append(("PASS", name, str(detail)[:200] if detail else ""))
        return True
    except Exception as e:
        RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"))
        if VERBOSE:
            import traceback
            traceback.print_exc(limit=3)
        return False


class FakeInput:
    """CDP's Input domain, mapped onto Playwright's real mouse and keyboard.

    Dropdowns are driven with real input rather than synthesised events,
    so a stand-in that only forwarded ``Runtime.evaluate`` would skip the
    part most likely to break. Playwright's mouse and keyboard produce
    genuine trusted events, same as CDP's — so what runs here is what runs
    in the app.
    """

    def __init__(self, page) -> None:
        self.page = page

    async def dispatchMouseEvent(self, params, session_id=None):  # noqa: N802
        kind, x, y = params["type"], params["x"], params["y"]
        if kind == "mouseMoved":
            await self.page.mouse.move(x, y)
        elif kind == "mousePressed":
            await self.page.mouse.move(x, y)
            await self.page.mouse.down()
        elif kind == "mouseReleased":
            await self.page.mouse.up()
        return {}

    async def dispatchKeyEvent(self, params, session_id=None):    # noqa: N802
        key = params.get("key", "")
        if params["type"] == "keyDown":
            await self.page.keyboard.down(key)
        else:
            await self.page.keyboard.up(key)
        return {}

    async def insertText(self, params, session_id=None):          # noqa: N802
        await self.page.keyboard.insert_text(params["text"])
        return {}


class FakeDom:
    """CDP's DOM domain, enough of it for ``_upload``.

    Attaching a file is the one thing script cannot do, so the fill path
    uses ``DOM.setFileInputFiles``. A stand-in without it makes every
    upload fail silently — which is not a finding about the app, and hid
    the fact that uploading *before* filling is what matters on Ashby.
    """

    def __init__(self, page) -> None:
        self.page = page
        self._nodes: dict[int, str] = {}
        self._next = 1

    async def getDocument(self, params=None, session_id=None):   # noqa: N802
        return {"root": {"nodeId": 0}}

    async def querySelector(self, params, session_id=None):      # noqa: N802
        selector = params["selector"]
        found = await self.page.query_selector(selector)
        if not found:
            return {"nodeId": 0}
        self._next += 1
        self._nodes[self._next] = selector
        return {"nodeId": self._next}

    async def setFileInputFiles(self, params, session_id=None):  # noqa: N802
        selector = self._nodes.get(params["nodeId"])
        if selector:
            await self.page.set_input_files(selector, params["files"])
        return {}


class FakeCdp:
    """Just enough of a browser-use CDP session for the real fill path.

    Two domains are used: ``Runtime.evaluate`` to read and write the page,
    and ``Input`` to work the widgets that only respond to real clicks. A
    stand-in forwarding both to a Playwright page lets the production
    ``fill_rounds`` run against a real form with nothing about the filling
    stubbed — only the transport.
    """

    def __init__(self, page) -> None:
        self.page = page
        self.session_id = "qa"
        self.Input = FakeInput(page)       # noqa: N803 — CDP domain name
        self.DOM = FakeDom(page)           # noqa: N803 — CDP domain name

    @property
    def cdp_client(self):
        return self

    @property
    def send(self):
        return self

    @property
    def Runtime(self):            # noqa: N802 — mirrors the CDP domain name
        return self

    async def evaluate(self, params, session_id=None):
        value = await self.page.evaluate(params["expression"])
        return {"result": {"value": value}}


class FakeSession:
    def __init__(self, page) -> None:
        self._cdp = FakeCdp(page)

    async def get_or_create_cdp_session(self, target_id=None, focus=False):
        return self._cdp


async def run() -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("playwright is not installed — skipping field QA")
        return 0

    from sushi import autofill, directfill, learning
    from sushi.models import Experience, Profile, Role

    profile = Profile(
        full_name="Rowan Hartley", email="r.hartley@example.com",
        phone="+44 7700 900123", linkedin="linkedin.com/in/rowanhartley",
        location="Bristol", country="United Kingdom",
        headline="Senior Operations Analyst",
        summary="Operations and data analyst with six years in payments.",
        skills=["Python", "SQL"], years_experience="6 years",
        work_authorisation="Yes", requires_sponsorship="No",
        salary_expectation="£65,000",
        experience=[Experience(company="Kestrel", title="Analyst", start="2019")])
    role = Role(company="Hostile Corp", title="Operations Analyst",
                description="An operations role.")

    # The answer bank, as it would be after a few applications. These are the
    # answers the lookup is expected to reuse here.
    for q, a in [
        ("What is your earliest start date?", "1 March 2026"),
        ("Which languages do you speak?", "English, French"),
        ("Which days are you available?", "Monday, Wednesday"),
        ("Preferred office location", "Remote (UK)"),
        ("Which university did you attend?", "University of Bristol"),
        ("What is your notice period?", "One month"),
        ("Which visa do you currently hold?", "Skilled Worker"),
        ("Which region are you based in?", "South West"),
        # No option will ever match this one — the list stays empty — so it
        # has to come back as a question, not as a field "filled" with the
        # text that was typed into the search box.
        ("Which town were you born in?", "Nowhereton"),
        # Comfortably over the 120-character limit on the textarea, so the
        # truncation path is exercised by the lookup alone — no model call.
        ("Why do you want to work here?",
         "Six years of payments analytics, most of it on reconciliation and "
         "settlement. This role is the same problem at a larger scale, and "
         "the team publishes its incident reviews, which is rare."),
    ]:
        learning.remember(q, a, source="human")

    tmp = Path(tempfile.mkdtemp(prefix="sushi-fields-"))
    page_path = tmp / "apply.html"
    page_path.write_text(FORM, encoding="utf-8")

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Exception as e:
            print(f"could not launch chromium: {e}")
            print("run: python -m playwright install chromium")
            return 0

        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.goto(page_path.as_uri())
        await page.wait_for_timeout(250)

        async def read() -> list[dict]:
            return learning._parse_harvest(await page.evaluate(directfill.READ_JS))

        async def value_of(sel: str) -> str:
            return await page.eval_on_selector(sel, "e => e.value")

        fields = await read()
        by_key = {f["key"]: f for f in fields}

        if VERBOSE:
            for f in fields:
                print(f"  read  {f['key']:<16} {f['type']:<9} "
                      f"{(f.get('widget') or '-'):<10} "
                      f"{f['label'][:40]:<42} opts={len(f.get('options') or [])}")

        # --- what the reader finds ------------------------------------------
        async def t_every_type_read():
            # Keyed by id where there is one, which is what the reader uses
            # to find the field again when it comes to fill it.
            want = {"fn": "text", "ln": "text", "em": "email", "ph": "tel",
                    "li": "url", "yrs": "number", "start": "date",
                    "auth": "select", "spon": "radio", "langs": "select",
                    "days": "checkbox", "why": "textarea", "city": "text",
                    "salary": "text"}
            missing = {k: t for k, t in want.items()
                       if by_key.get(k, {}).get("type") != t}
            assert not missing, f"not read, or read as the wrong type: {missing}"
            return f"{len(fields)} fields read, all {len(want)} core types correct"
        await check("read: every ordinary input type is found", t_every_type_read)

        async def t_select_options():
            auth = by_key["auth"]
            assert auth["options"][:1] == ["Please select"], auth["options"]
            assert "Yes, I have the right to work" in auth["options"], auth["options"]
            return f"options read as text: {auth['options'][1:]}"
        await check("read: a select's options are read as their text",
                    t_select_options)

        async def t_radio_options():
            spon = by_key["spon"]
            assert spon["label"].startswith("Do you require visa sponsorship"), \
                spon["label"]
            assert [o.lower() for o in spon["options"]] == ["yes", "no"], \
                spon["options"]
            return f"{spon['label']} -> {spon['options']}"
        await check("read: a radio group reads its legend and its options",
                    t_radio_options)

        async def t_multi_select_known():
            assert by_key["langs"].get("multiple") is True, (
                "a <select multiple> is read as an ordinary select, so only "
                "one of the answers can ever be chosen")
            return "multiple is flagged"
        await check("read: a multiple select says that it is multiple",
                    t_multi_select_known)

        async def t_checkbox_group_options():
            days = by_key["days"]
            assert [o.lower() for o in days["options"]] == \
                ["monday", "tuesday", "wednesday"], days["options"]
            assert days.get("multiple") is True, (
                "a checkbox group is not flagged as taking several answers")
            return f"{days['label']} -> {days['options']}"
        await check("read: a checkbox group reads as a multi-answer question",
                    t_checkbox_group_options)

        async def t_custom_combobox():
            hit = [f for f in fields
                   if "office location" in (f.get("label") or "").lower()]
            assert hit, ("the custom combobox was not read at all — this is "
                         "what Greenhouse's current UI and Workday both use")
            f = hit[0]
            assert [o.lower() for o in f.get("options") or []] == \
                ["london", "manchester", "remote (uk)"], f.get("options")
            assert f.get("widget") == "combobox", f.get("widget")
            return f"combobox read with {len(f['options'])} options"
        await check("read: a custom combobox is read like a select",
                    t_custom_combobox)

        async def t_typeahead_known():
            uni = by_key["uni"]
            assert uni.get("widget") == "typeahead", (
                "a text box with a popup list is read as an ordinary text "
                "box, so it is typed into and keeps nothing")
            return "typeahead recognised"
        await check("read: a typeahead is recognised as one", t_typeahead_known)

        async def t_maxlength_known():
            assert by_key["why"].get("maxlength") == 120, (
                "the character limit is not reported, so a composed answer is "
                "truncated mid-word by the browser and nobody is told")
            return "maxlength 120 reported"
        await check("read: a length limit is reported", t_maxlength_known)

        async def t_not_ours():
            assert by_key.get("dept") is None, \
                "a disabled field was offered for filling"
            ref = by_key.get("ref")
            assert ref is None or ref.get("readonly") is True, \
                "a readonly field was offered as writable"
            assert by_key["city"]["value"] == "Bristol", \
                "an already-filled field lost its value on the way in"
            return "disabled skipped, readonly flagged, prefilled preserved"
        await check("read: disabled, readonly and prefilled fields are respected",
                    t_not_ours)

        async def t_collapsed_and_hidden():
            assert by_key.get("visa") is None, \
                "a question that has not appeared yet was read as if it had"
            assert by_key.get("notice") is None, \
                "a field inside a collapsed section was read as visible"
            return "neither the hidden conditional nor the collapsed field is read"
        await check("read: fields that are not on screen are not invented",
                    t_collapsed_and_hidden)

        # --- what the planner decides ----------------------------------------
        plan = directfill.plan_fill(fields, profile)
        planned = {f.key: f for f in plan.fills}

        async def t_placeholder_never_chosen():
            auth = planned.get("auth")
            assert auth is not None, (
                "work authorisation was not planned at all, though the "
                f"profile says {profile.work_authorisation!r}")
            assert auth.value != "Please select", \
                "the placeholder option was chosen as the answer"
            assert auth.value == "Yes, I have the right to work", auth.value
            return f"auth -> {auth.value!r}"
        await check("plan: a placeholder option is never the answer",
                    t_placeholder_never_chosen)

        async def t_number_coerced():
            yrs = planned.get("yrs")
            assert yrs is not None, "years of experience was not planned"
            assert yrs.value == "6", (
                f"a number field was planned as {yrs.value!r}; the browser "
                f"will refuse it and leave a required field empty")
            return "'6 years' -> '6'"
        await check("plan: a number field gets a number", t_number_coerced)

        async def t_date_coerced():
            start = planned.get("start")
            assert start is not None, "the start date was not planned"
            assert start.value == "2026-03-01", (
                f"a date field was planned as {start.value!r}; a date input "
                f"only accepts YYYY-MM-DD and silently drops anything else")
            return "'1 March 2026' -> '2026-03-01'"
        await check("plan: a date field gets an ISO date", t_date_coerced)

        async def t_consent_not_ticked():
            assert "terms" not in planned, \
                "Sushi agreed to a privacy notice on the user's behalf"
            assert any("privacy notice" in c.lower() for c in plan.consents), \
                plan.consents
            return "the declaration is listed, not ticked"
        await check("plan: a declaration is listed for you, never ticked",
                    t_consent_not_ticked)

        async def t_prefilled_left_alone():
            assert "city" not in planned, \
                "a field the user had already filled was overwritten"
            assert "ref" not in planned, "a readonly field was planned"
            return "Bristol and the reference number both left alone"
        await check("plan: an answer already in the form is left alone",
                    t_prefilled_left_alone)

        # --- the real fill, end to end ---------------------------------------
        #
        # Not fill_script on its own: the whole point of the click-through
        # and the second pass is that they happen *between* scripts, so the
        # production coroutine is what runs here, against a real page.
        await page.evaluate("window.__events = []")
        steps: list[str] = []
        filled, guesses, final, all_fields, why = await autofill.fill_rounds(
            FakeSession(page), fields, role, profile, on_step=steps.append)
        await page.wait_for_timeout(300)

        async def t_text_landed():
            assert await value_of("#fn") == "Rowan", await value_of("#fn")
            assert await value_of("#em") == "r.hartley@example.com"
            assert await value_of("#ph") == "+44 7700 900123"
            assert (await value_of("#li")).endswith("rowanhartley")
            return "name, email, phone and URL all landed"
        await check("fill: plain text, email, tel and url land", t_text_landed)

        async def t_number_landed():
            got = await value_of("#yrs")
            assert got == "6", (
                f"the number field holds {got!r} — a number input rejects "
                f"anything that is not a bare number, and does it silently")
            return "years = 6"
        await check("fill: a number field actually holds the number",
                    t_number_landed)

        async def t_date_landed():
            got = await value_of("#start")
            assert got == "2026-03-01", (
                f"the date field holds {got!r}, so the form would submit with "
                f"an empty required date")
            return "start date = 2026-03-01"
        await check("fill: a date field actually holds the date", t_date_landed)

        async def t_select_landed():
            assert await value_of("#auth") == "AUTH_Y", await value_of("#auth")
            picked = await page.eval_on_selector(
                "#auth", "e => e.options[e.selectedIndex].text")
            assert picked == "Yes, I have the right to work", picked
            return "select set by its option text, value AUTH_Y"
        await check("fill: a select is set by its option text, not its value",
                    t_select_landed)

        async def t_radio_landed():
            checked = await page.eval_on_selector_all(
                "input[name='spon']:checked", "els => els.map(e => e.value)")
            assert checked == ["no"], f"sponsorship radio: {checked}"
            return "sponsorship = no"
        await check("fill: a radio group picks the right option", t_radio_landed)

        async def t_multi_select_landed():
            picked = await page.eval_on_selector_all(
                "#langs option:checked", "els => els.map(e => e.text)")
            assert sorted(picked) == ["English", "French"], (
                f"only {picked} selected — a multiple select was treated as "
                f"though it took one answer")
            return f"languages = {picked}"
        await check("fill: a multiple select takes all of the answers",
                    t_multi_select_landed)

        async def t_checkbox_group_landed():
            ticked = await page.eval_on_selector_all(
                "input[name='days']:checked", "els => els.map(e => e.value)")
            assert sorted(ticked) == ["mon", "wed"], (
                f"only {ticked} ticked — a checkbox group was treated as "
                f"though it took one answer")
            return f"days = {ticked}"
        await check("fill: a checkbox group ticks every answer",
                    t_checkbox_group_landed)

        async def t_combobox_landed():
            shown = (await page.inner_text("#loc-combo")).strip()
            assert shown == "Remote (UK)", (
                f"the combobox still reads {shown!r} — a custom widget needs "
                f"its own option clicked, not a value assigned")
            assert await value_of("#loc") == "REM", await value_of("#loc")
            return "combobox opened, option clicked, REM committed"
        await check("fill: a custom combobox gets its option chosen",
                    t_combobox_landed)

        async def t_typeahead_landed():
            committed = await page.eval_on_selector(
                "#uni", "e => e.dataset.committed")
            held = await value_of("#uni")
            assert committed == "University of Bristol", (
                f"the typeahead holds {held!r} but committed {committed!r} — "
                f"typing into one of these keeps no value until one of its "
                f"own options is clicked")
            return "typeahead typed into, suggestion clicked"
        await check("fill: a typeahead has its suggestion clicked",
                    t_typeahead_landed)

        async def t_maxlength_respected():
            got = await value_of("#why")
            assert got, "nothing was written into the free-text box"
            assert len(got) <= 120, f"{len(got)} characters in a 120 limit"
            assert got[-1] in ".!?", (
                f"the answer was cut off mid-sentence: ...{got[-40:]!r}")
            return f"{len(got)}/120 characters, ending on a full stop"
        await check("fill: a length limit is respected, not hit",
                    t_maxlength_respected)

        async def t_untouched():
            assert await value_of("#ref") == "REF-99812", \
                "a readonly field was written to"
            assert await value_of("#dept") == "Engineering", \
                "a disabled field was written to"
            assert await value_of("#city") == "Bristol", \
                "a filled field was overwritten"
            ticked = await page.eval_on_selector("#terms", "e => e.checked")
            assert ticked is False, "a privacy notice was agreed to"
            return "readonly, disabled, prefilled and the declaration untouched"
        await check("fill: nothing is written where it should not be", t_untouched)

        async def t_events_fired():
            events = await page.evaluate("window.__events")
            scripted = {n for k, n, trusted in events
                        if k == "change" and not trusted}
            for want in ("fn", "auth", "yrs"):
                assert want in scripted, (
                    f"no scripted change event for {want} — a React or Vue "
                    f"form ignores a value set without one and submits empty")
            return (f"{len(events)} events; change fired by the script for "
                    f"{len(scripted)} fields")
        await check("fill: the events a framework listens for are fired",
                    t_events_fired)

        async def t_below_the_fold():
            got = await value_of("#salary")
            assert got == "£65,000", f"the field below the fold holds {got!r}"
            return "the field 1600px down was filled"
        await check("fill: a field below the fold is filled too", t_below_the_fold)

        async def t_typed_is_not_chosen():
            """A typeahead that was typed into is not a typeahead answered.

            Opening one of these means putting the answer in its search
            box. So the control shows the right text whether or not an
            option was ever committed — and taking that as proof is how
            three required education fields on a Greenhouse form were
            reported filled while holding nothing.
            """
            held = await value_of("#town")
            assert "Which town were you born in?" in final.unanswered, (
                f"the field shows {held!r} and nothing matched it in the "
                f"list, but it was reported as answered")
            return f"typed {held!r}, nothing clicked, reported as outstanding"
        await check("fill: typing into a typeahead is not answering it",
                    t_typed_is_not_chosen)

        async def t_consent_behind_a_dropdown():
            shown = (await page.inner_text("#ack-combo")).strip()
            assert shown == "Select…", (
                f"Sushi agreed to a privacy notice on the user's behalf "
                f"(the control now reads {shown!r})")
            assert any("privacy notice" in c.lower() for c in final.consents), \
                final.consents
            return "listed to agree to, not agreed to"
        await check("fill: a declaration behind a dropdown is still yours",
                    t_consent_behind_a_dropdown)

        async def t_demographic_behind_a_plain_label():
            got = await value_of("#hispanic_ethnicity")
            assert got == "", f"a protected characteristic was answered: {got!r}"
            assert any("hispanic" in s.lower() for s in final.skipped), \
                final.skipped
            return "refused, though the label names none of the usual words"
        await check("fill: a demographic question the label does not name",
                    t_demographic_behind_a_plain_label)

        async def t_essay_is_not_a_field_name():
            got = await value_of("#essay")
            assert got == "", (
                f"an essay box holds {got!r} — a profile rule matched a word "
                f"inside the question ('nation-wide') and answered it")
            return "a 200-character question is not a field label"
        await check("fill: a word inside an essay is not a field to fill",
                    t_essay_is_not_a_field_name)

        async def t_nothing_left_open():
            """No menu may be left hanging open when the run is done.

            Reading a closed dropdown means opening it, and the first
            version of that could not shut them again: a real form came
            back with five menus stacked over each other, each covering
            the field below it, none of them chosen. A menu left open is
            not cosmetic — the next click lands in the wrong list.
            """
            open_now = int(await page.evaluate(directfill.OPEN_MENUS_JS))
            assert open_now == 0, (
                f"{open_now} menu(s) still open over the form after filling")
            # And the combobox that *was* used is shut, with its answer in.
            hidden = await page.eval_on_selector(
                "#loc-list", "e => e.classList.contains('hidden')")
            assert hidden, "the combobox that was answered is still open"
            return "every menu opened was shut again"
        await check("fill: no dropdown is left hanging open", t_nothing_left_open)

        async def t_reported_honestly():
            assert filled >= 12, f"only {filled} reported filled"
            # Anything the form would not take has to come back as a question,
            # not be quietly counted as done.
            assert "Which university did you attend?" not in final.unanswered, \
                final.unanswered
            return (f"{filled} filled, {len(final.unanswered)} handed back, "
                    f"{len(final.consents)} to tick")
        await check("fill: the count and the leftovers are honest",
                    t_reported_honestly)

        # --- the question that only appears once you answer another ----------
        async def t_dependent_dropdown():
            """A dropdown whose options only arrive after another answer.

            The field is on screen from the start and never changes — only
            its option list does. Looking for *new fields* on a second pass
            finds nothing, so this one needs the pass to re-plan the whole
            form, not just the part of it that appeared.
            """
            got = await value_of("#region")
            assert got == "sw", (
                f"the dependent dropdown holds {got!r} — its options only "
                f"appeared once the work-authorisation question was "
                f"answered, and nothing went back for it")
            picked = await page.eval_on_selector(
                "#region", "e => e.options[e.selectedIndex].text")
            assert picked == "South West", picked
            return "options arrived on pass one, chosen on pass two"
        await check("conditional: a dropdown that fills its options later",
                    t_dependent_dropdown)

        async def t_second_pass_is_the_runs_own():
            """Not the test driving it twice — the run must come back itself."""
            await page.goto(page_path.as_uri())
            await page.wait_for_timeout(250)
            # One conditional at a time. The fixture has two that drive the
            # same block — the select reveals it, the radio group hides it
            # again — so the radios are replaced by listener-free clones
            # before the select's reveal is wired up. Otherwise this would
            # be testing the fixture arguing with itself.
            await page.evaluate(
                "document.querySelectorAll(\"input[name='spon']\")"
                "  .forEach(r => r.replaceWith(r.cloneNode(true)));"
                "document.querySelector('#auth').addEventListener('change',"
                " e => { if (e.target.value === 'AUTH_Y')"
                "   document.getElementById('visa-wrap')"
                "     .classList.remove('hidden'); })")
            start = await read()
            assert "visa" not in {f["key"] for f in start}, \
                "the conditional was already visible before the run"
            await autofill.fill_rounds(FakeSession(page), start, role, profile)
            got = await value_of("#visa")
            assert got == "Skilled Worker", (
                f"after one call the revealed field holds {got!r} — the fill "
                f"path reads the form once and never looks again, so every "
                f"question that appears after an answer is left blank")
            return "one call, two passes, the revealed field filled"
        await check("conditional: the run makes its own second pass",
                    t_second_pass_is_the_runs_own)

        async def t_conditional_from_a_radio():
            """The other direction: a radio group reveals the question."""
            await page.goto(page_path.as_uri())
            await page.wait_for_timeout(250)
            await page.check("input[name='spon'][value='yes']")
            await page.wait_for_timeout(200)
            again = await read()
            assert "visa" in {f["key"] for f in again}, \
                "a question revealed by a radio is not read"
            await autofill.fill_rounds(FakeSession(page), again, role, profile)
            got = await value_of("#visa")
            assert got == "Skilled Worker", f"the visa field holds {got!r}"
            return "revealed by a radio, read and filled"
        await check("conditional: a question revealed by a radio is filled",
                    t_conditional_from_a_radio)

        async def t_passes_are_bounded():
            assert autofill._MAX_PASSES >= 2, autofill._MAX_PASSES
            assert autofill._MAX_PASSES <= 5, (
                "a form that keeps growing would be followed for ever")
            return f"bounded at {autofill._MAX_PASSES} passes"
        await check("conditional: the passes are bounded", t_passes_are_bounded)

        async def t_collapsed_section():
            await page.click("#more-btn")
            await page.wait_for_timeout(200)
            again = await read()
            assert "notice" in {f["key"] for f in again}, \
                "a field revealed by opening a section is still not read"
            await autofill.fill_rounds(FakeSession(page), again, role, profile)
            got = await value_of("#notice")
            assert got == "One month", f"the notice period holds {got!r}"
            return "opening a section makes its fields readable and fillable"
        await check("conditional: a collapsed section's fields work once open",
                    t_collapsed_section)

        # --- the form that is not on the page you were given ---------------
        #
        # Routed rather than hosted, so the real host list is what decides:
        # ``_embedded_form`` only follows an iframe whose host is a known
        # ATS, and a fixture served from localhost would not test that.
        embed_page = await browser.new_page(viewport={"width": 1200,
                                                      "height": 800})
        await embed_page.route(
            "https://careers.example.com/**",
            lambda route: route.fulfill(status=200, content_type="text/html",
                                        body=CAREERS_PAGE))
        await embed_page.route(
            "https://job-boards.greenhouse.io/**",
            lambda route: route.fulfill(status=200, content_type="text/html",
                                        body=EMBEDDED_FORM))
        await embed_page.goto("https://careers.example.com/jobs/1")
        await embed_page.wait_for_timeout(300)
        embed_session = FakeSession(embed_page)

        async def t_apply_is_a_button():
            """An Apply control with no href must still be pressed."""
            start = learning._parse_harvest(
                await embed_page.evaluate(directfill.READ_JS))
            assert not start, f"the careers page should have no form: {start}"
            link = await embed_page.evaluate(autofill._APPLY_JS)
            assert not link or link in ('""', "''"), (
                f"there is no Apply *link* here, but one was found: {link}")
            moved = await autofill._follow_apply(embed_session)
            assert moved, (
                "Apply is a <button> with no href, so nothing was followed "
                "and the run reported 'no application form was found'")
            return "the button was pressed and the page moved"
        await check("embed: an Apply button with no link is still pressed",
                    t_apply_is_a_button)

        async def t_embedded_form_is_reached():
            """And the form inside the iframe must end up readable."""
            assert "greenhouse.io" in embed_page.url, embed_page.url
            got = learning._parse_harvest(
                await embed_page.evaluate(directfill.READ_JS))
            keys = {f["key"] for f in got}
            assert {"fn", "em", "ph"} <= keys, (
                f"the embedded form is still not readable: {sorted(keys)}")
            # And fillable, which is the whole point of going there.
            plan = directfill.plan_fill(got, profile)
            await autofill._apply_plan(embed_session, plan)
            await embed_page.wait_for_timeout(200)
            value = await embed_page.eval_on_selector("#em", "e => e.value")
            assert value == "r.hartley@example.com", value
            return f"{len(got)} fields read and filled inside the embed"
        await check("embed: the form inside the iframe is read and filled",
                    t_embedded_form_is_reached)

        async def t_ats_hosts_only():
            """Any old iframe is not an application form."""
            await embed_page.route(
                "https://ads.example.net/**",
                lambda route: route.fulfill(status=200,
                                            content_type="text/html",
                                            body="<html><body>ad</body></html>"))
            await embed_page.goto("https://careers.example.com/jobs/2")
            await embed_page.evaluate(
                "const f = document.createElement('iframe');"
                "f.src = 'https://ads.example.net/banner';"
                "f.width = 900; f.height = 600;"
                "document.body.appendChild(f);")
            await embed_page.wait_for_timeout(300)
            found = await autofill._embedded_form(embed_session)
            assert not found, f"followed a tracking iframe: {found}"
            return "a non-ATS iframe is ignored"
        await check("embed: only an ATS iframe counts as a form",
                    t_ats_hosts_only)

        await embed_page.close()

        # --- a form that rewrites itself when the CV lands -----------------
        parse_page = await browser.new_page(viewport={"width": 1200,
                                                      "height": 800})
        parse_path = tmp / "parsing.html"
        parse_path.write_text(PARSING_FORM, encoding="utf-8")
        cv = Path(__file__).resolve().parent / "dummy_cv.pdf"

        async def t_upload_comes_first():
            """Attach the CV before filling, because it can clear the form.

            The order used to be the other way round. Everything Sushi
            typed was correct, present in the DOM, and agreed with React —
            and then the upload threw three answers away on its way to
            parsing the file. Nothing downstream could notice: a read-back
            after the fill says "filled", and the upload happens after it.
            """
            assert cv.exists(), f"no CV fixture at {cv}"
            for q, a in [("What is your notice period?", "One month"),
                         ("What are your salary expectations?", "£65,000")]:
                learning.remember(q, a, source="human")

            await parse_page.goto(parse_path.as_uri())
            await parse_page.wait_for_timeout(250)
            sess = FakeSession(parse_page)
            fields = learning._parse_harvest(
                await parse_page.evaluate(directfill.READ_JS))

            uploaded, _labels = await autofill._attach_files(
                sess, fields, str(cv), "")
            assert uploaded == 1, (
                f"the CV was not attached ({uploaded}); the rest of this "
                f"check would pass for the wrong reason")
            await asyncio.sleep(autofill._AFTER_UPLOAD)
            fields = learning._parse_harvest(
                await parse_page.evaluate(directfill.READ_JS))

            plan = directfill.plan_fill(fields, profile)
            await autofill._apply_plan(sess, plan)
            await parse_page.wait_for_timeout(400)

            held = {}
            for key in ("pname", "pmail", "pnotice", "psalary"):
                held[key] = await parse_page.eval_on_selector(
                    f"#{key}", "e => e.value")
            empty = [k for k, v in held.items() if not v]
            assert not empty, (
                f"{empty} still empty after the run — the upload cleared "
                f"what was typed, which is how a form that looks complete "
                f"submits with 'missing required field'. Held: {held}")
            return f"CV attached first; all 4 fields survive: {held['pnotice']!r}"
        await check("upload: the CV goes on before the form is filled",
                    t_upload_comes_first)

        async def t_upload_after_would_lose_it():
            """And prove the fixture really does punish the old order."""
            await parse_page.goto(parse_path.as_uri())
            await parse_page.wait_for_timeout(250)
            sess = FakeSession(parse_page)
            fields = learning._parse_harvest(
                await parse_page.evaluate(directfill.READ_JS))
            plan = directfill.plan_fill(fields, profile)
            await autofill._apply_plan(sess, plan)          # fill first…
            await autofill._attach_files(sess, fields, str(cv), "")  # …then upload
            await asyncio.sleep(autofill._AFTER_UPLOAD)
            after = await parse_page.eval_on_selector("#pnotice", "e => e.value")
            assert not after, (
                "the fixture no longer loses the answer when the upload "
                "comes last, so the check above proves nothing")
            return "filling first really does lose the answer — the order matters"
        await check("upload: the old order demonstrably lost answers",
                    t_upload_after_would_lose_it)

        await parse_page.close()

        # --- an application that runs to four pages ------------------------
        step_page = await browser.new_page(viewport={"width": 1200,
                                                     "height": 800})
        step_path = tmp / "stepped.html"
        step_path.write_text(STEPPED_FORM, encoding="utf-8")

        async def t_walks_the_pages():
            """Taleo-shaped: fill a page, press Continue, fill the next."""
            for q, a in [("What is your notice period?", "One month"),
                         ("How did you hear about us?", "A friend")]:
                learning.remember(q, a, source="human")
            await step_page.goto(step_path.as_uri())
            await step_page.wait_for_timeout(250)
            sess = FakeSession(step_page)

            seen, filled = [], 0
            for step in range(autofill._MAX_STEPS):
                fields = learning._parse_harvest(
                    await step_page.evaluate(directfill.READ_JS))
                seen.append(sorted(f["key"] for f in fields))
                plan = directfill.plan_fill(fields, profile)
                n, _g = await autofill._apply_plan(sess, plan)
                filled += n
                if not await autofill._advance(sess):
                    break

            assert len(seen) == 4, (
                f"got through {len(seen)} page(s), not 4: {seen}")
            assert seen[0] == ["s1mail", "s1name"], seen[0]
            assert seen[3] == ["s4ref"], seen[3]
            return f"four pages walked, {filled} field(s) filled across them"
        await check("steps: a four-page application is worked through",
                    t_walks_the_pages)

        async def t_values_survive_the_steps():
            """And going back shows what was filled on the way."""
            # Back to page one by pressing Back three times.
            for _ in range(3):
                await step_page.click("text=Back")
                await step_page.wait_for_timeout(200)
            name = await step_page.eval_on_selector("#s1name", "e => e.value")
            mail = await step_page.eval_on_selector("#s1mail", "e => e.value")
            assert name == profile.full_name, (name, profile.full_name)
            assert mail == profile.email, mail
            return f"page one still holds {name!r} and {mail!r}"
        await check("steps: what was filled on an earlier page is still there",
                    t_values_survive_the_steps)

        async def t_never_presses_submit():
            """The one thing it must not do, on the page that offers it."""
            await step_page.goto(step_path.as_uri())
            await step_page.wait_for_timeout(250)
            sess = FakeSession(step_page)
            for _ in range(autofill._MAX_STEPS):
                if not await autofill._advance(sess):
                    break
            # On the last page, Next is disabled and Submit is right there.
            buttons = await step_page.eval_on_selector_all(
                "#nav button", "els => els.map(e => e.textContent)")
            assert "Submit Application" in buttons, buttons
            sent = await step_page.eval_on_selector(
                "#sent", "e => getComputedStyle(e).display !== 'none'")
            assert not sent, (
                "it pressed Submit Application — an application went out "
                "that nobody read")
            # And asked once more, it still refuses.
            assert not await autofill._advance(sess), (
                "it would press something on a page whose only live control "
                "is Submit")
            return "walked to the last page and stopped; Submit untouched"
        await check("steps: Submit is never pressed to turn a page",
                    t_never_presses_submit)

        async def t_refusal_is_not_a_step():
            """Continue that does not continue must not count as progress."""
            await step_page.goto(step_path.as_uri())
            await step_page.wait_for_timeout(250)
            # Make Continue do nothing, the way a validation failure does.
            await step_page.evaluate(
                "document.querySelectorAll('#nav button').forEach("
                "  b => { if (b.textContent.indexOf('Continue') >= 0)"
                "           b.onclick = () => {}; })")
            moved = await autofill._advance(FakeSession(step_page))
            assert not moved, (
                "a Continue that left the page exactly as it was counted as "
                "a step, so the run would walk a form it is not moving")
            return "a refusal reads as a refusal"
        await check("steps: a Continue that changes nothing is not a step",
                    t_refusal_is_not_a_step)

        await step_page.close()
        await browser.close()

    print()
    width = max(len(n) for _, n, _ in RESULTS) + 2
    for status, name, detail in RESULTS:
        mark = "ok  " if status == "PASS" else "FAIL"
        print(f"  {mark}  {name:<{width}} {detail}")
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(run()))
