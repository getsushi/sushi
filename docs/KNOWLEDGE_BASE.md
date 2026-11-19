# Sushi knowledge base

The whole repository in one document. Written 2026-11-05 against branch
`fix/smooth-feed-steady-apply` (commit `b52a0276`), from a full read of the
code. File references are relative to the repository root.

It is written so that a website can be brought into line with the product:
sections 1–3 are what Sushi *is* and what can safely be said about it,
sections 9–10 are how it looks and sounds, and section 12 lists where the
existing docs (README, comments) disagree with the code — so the website does
not copy a stale claim.

---

## 1. The product in one paragraph

Sushi is a **local desktop app** (Python, PyQt6) for Windows, macOS and Linux.
You give it a CV; it works out who you are, finds roles that fit by reading
companies' own careers boards, writes cover letters, fills the application
form in a real browser, and tracks everything afterwards — with an assistant
alongside. Your CV, your data and your model key stay on your machine; the
only thing that leaves it is what is sent to the AI model provider you chose.

**Taglines in the product**

| Where | Text |
|---|---|
| Splash screen | "Applies for you." |
| Window title | "Sushi · your job search, handled" |
| Insane mode | "Arm it and Sushi applies to your best matches on its own, up to a cap you set." |
| Manual apply | "Paste a link to any job and Sushi fills the form for you." |
| Onboarding, last screen | "You're set." |

Version: `0.1.0` (`sushi/__init__.py`). Launch with `python main.py`.

---

## 2. Claims a website can safely make

Each of these is true in the code today.

**Finding jobs**
- Reads jobs straight from companies' own applicant tracking systems, through
  their public job-board APIs — no scraping of job boards, no login.
- **13 ATS platforms read directly:** Greenhouse, Lever, Ashby, Workable,
  SmartRecruiters, Recruitee, BambooHR, Breezy, Pinpoint, Rippling, Workday,
  Personio, Teamtailor (`sushi/ats.py:451`).
- **8 public job feeds** on top: Remotive, Arbeitnow, Jobicy, The Muse,
  Himalayas, RemoteOK, We Work Remotely, Working Nomads (`sushi/sources.py:226`).
- Ships with a corpus of **25,219 companies, 15,918 of them with a readable
  job board** (`sushi/data/companies.jsonl`). The corpus grows as it finds new
  employers in apply links.
- A role appears on a company's own board the moment it opens — often before
  aggregators index it.
- Every match carries plain-English reasons ("matches your target role",
  "right seniority", "in your locations") and a match percentage.
- Paste any job link and Sushi will read it and fill it in, whatever the site.

**Applying**
- Fills the real application form in a real, visible Chrome window: text,
  drop-downs, radio buttons, tick-boxes, typeaheads, multi-page forms, CV and
  cover-letter uploads, and forms embedded in careers pages.
- Three fill modes: **Direct** (no AI, fills what your profile settles),
  **Auto** (direct, then AI for free-text answers — the default) and **Agent**
  (an AI browser agent drives the page).
- **Nothing is submitted unless you turn that on.** By default Sushi fills the
  form and stops so you can review and press Submit yourself. Turning on
  auto-submit needs a confirmation.
- Anything Sushi guessed is outlined in amber in the form so you can check it.
- It never ticks consent or privacy boxes for you, never types passwords,
  one-time codes, card or bank details, and does not answer demographic or
  equal-opportunity questions unless you opt in.
- Salary, notice period and visa questions are left blank and reported back
  rather than invented.

**Learning**
- Sushi learns from you while the form is open: it reads the fields every 2.5
  seconds and remembers your answers for next time.
- **Your corrections always win** — a value you typed can never be
  overwritten by a later AI guess.
- It recognises the same question worded differently ("authorised to work in
  the UK" / "authorized to work in the United Kingdom").
- It can read a folder of your old cover letters and saved answers, so forms
  are filled in your words.
- Everything it has learned can be viewed and deleted in Settings → Learning.

**Insane mode**
- Arm one button and Sushi applies to your best matches on its own, one at a
  time, up to a daily cap you set (default 10, up to 100), at a quality bar
  you choose ("Only the best" / "Strong matches" / "Worth a look").

**Tracking**
- Every application is tracked: Submitted, In flight, Needs you, Failed,
  Passed.
- One list of every application, by status, with CSV export. (The old
  Applied → Ghosted → Interviewing → Rejected → Offer board was removed in the
  2026-11 redesign.)

**Models and cost**
- Works with Claude Code (no API key), Groq, Google Gemini, OpenAI,
  Anthropic, Azure OpenAI, or a free local model through Ollama.
- Free options: Claude Code (uses an existing subscription), Groq and Gemini
  free tiers, and Ollama on your own machine.

**Privacy**
- "Your data lives on this machine… Nothing is uploaded except to your model
  provider." (Settings → Account)
- Personal details are handed to the browser agent as placeholders, so your
  email and phone are typed into the page without passing through the model.
- Contact details are extracted from the CV by pattern matching, not
  generated, so they cannot be hallucinated.

**Your files**
- Cover letters, tailored resumes and exports are ordinary files in
  `Documents/Sushi` (movable), not locked in a database.
- Resume builder with two templates ("Standard" and "Jake"), PDF download.

### Claims to avoid or qualify

| Don't say | Why |
|---|---|
| "1,039 companies" or "six ATS" | Out of date (README). Use 25,219 companies / 13 ATS. |
| "Keys are stored in your OS keychain" | No keyring is used. A key typed in Settings is stored in the local SQLite database in plain text. Say "stored locally". |
| "Tailors your resume to each job with AI" | The resume optimisation setting (Off / Honest / Aggressive) exists, but the resume renderer does not read it yet. |
| "Reads the full job description to match you" | Most Greenhouse postings arrive without a description, so matching is on title, department and location. (Roadmap item 2.) |
| "Works offline" | Reading a CV needs a model (roadmap item 1). |
| "Dark mode" | Light theme only, deliberately. |
| "Reads scanned PDFs" | No OCR; a scanned-image CV is rejected. |
| "Model switcher in the title bar" | Not there any more; the model is chosen in Settings → Model and onboarding. |

---

## 3. Screens and features

The window: a custom 64 px title bar, a collapsible left sidebar, the page,
an optional assistant rail on the right, and a status bar at the bottom.

**Sidebar** (`sushi/ui/sidebar.py`), in order:

| Group | Items |
|---|---|
| Automation | Insane mode (shows "ON" when armed), Manual apply |
| Workspace | Dashboard, All jobs (count), Tracker (count) |
| You | Profile, Help (opens the assistant) |
| Footer | Your initial and name, "local", a gear for Settings |

**Title bar:** sidebar toggle (Ctrl+B, the only keyboard shortcut), the
mascot and "Sushi" wordmark, a pair of toggles for the assistant and Insane
mode, and window controls.

Since the 2026-11 redesign (from the team's wireframe) the app has four tabs:
gold on greyscale, Cormorant Garamond titles over Lora body text, and a status
line along the bottom with a live bar for the job search.

| Screen | What it does | Key copy |
|---|---|---|
| **Home** | Insane Mode switch with daily limit, Pause and "See the queue"; three numbers (new matches, sent today, need your input); "N jobs are ready for you" with the best match; **Needs you** (questions answered in place, Yes/No where it fits) beside **Sent today**. | "Insane Mode is on" · "Answer once. We'll remember it." · "Review matches" |
| **Matches** | Every match, best first: role with a "Why:" line, match %, Skip / Apply; filters Date, Location, Workplace, Company, Match, Saved; tick several to apply; "Paste a job link" opens Manual apply. | "Best first" · "Paste a job link" |
| **Applications** | One table of every application, tabs All / Submitted / In flight / Needs you / Failed / Skipped, CSV export. | "Newest first" · "Export CSV" |
| **Profile** | CV library, Resume (templates, PDF) / Cover Letter / Profile Details. | "Add resume" · "Read my CV" |
| **Settings** (from your name in the sidebar) | Where to look, Apply settings, Learning, Model, Files, Companies, Account. | "Your corrections always win." |
| **Manual apply / Insane queue** | Still there, reached from Matches ("Paste a job link") and Home ("See the queue"). | "Fill it in Chrome" |
| **Job details drawer** | Facts, "Why this matches you", description, original posting, Not interested / Save / Apply. | "Why this matches you" |
| **Assistant rail** | Chat on the right (the ✦ button), answers questions and can act; every action previewed and undoable. | "How can I help?" |

**First run** (`sushi/ui/onboarding.py`), "Step n of 4":
1. **"Pick a model."** — "Sushi needs one to read your CV and fill a form. The free ones first." (required)
2. **"Now your CV."** — "Sushi works everything else out from it: matching, tailoring, filling forms." Drop the file anywhere. (required)
3. **"Where do you want to work?"** — regions, countries, Anywhere / Remote only / Hybrid / On site. (optional)
4. **"What have you written before?"** — point it at old letters and answers. (optional)
5. Finish: **"You're set."** — "Nice to meet you, {first}. Sushi starts looking for roles the moment this opens." Button: **"Open Sushi"**.

Screenshots of every screen are rendered by `tools/qa/qa_shots.py` into
`tools/qa/shots/` (not tracked in git). Re-render them after the redesign
before using any for website imagery; the older ones show the previous look.
`docs/figures/clay_dash.png` is an **old, superseded design** (green/lime,
top tabs) — do not use it.

---

## 4. How it works

```
CV (PDF / DOCX / TXT / MD)
  │  text extraction; an LLM turns it into a profile;
  │  email, phone, LinkedIn, GitHub taken by regex and override the model
  ▼
Profile ── target titles, locations, seniority, skills, workplace
  ▼
Discovery ── 8 feeds + 15,918 company boards on 13 ATS, read as JSON
  │          into a local job database; active boards re-read every 24 h,
  │          empty ones every 7 days; vanished postings marked closed
  ▼
Scoring ── every job scored against the profile, with reasons
  ▼
Cover letter (only if the form asks for one)
  ▼
Fill the form in Chrome ── direct fill, then AI for what is left
  ▼
You review → Submit (or Sushi submits, if you turned that on)
  ▼
Tracker ── status + pipeline stage; the answer bank learns from what you typed
```

Code: `sushi/cv.py` → `sushi/discovery.py`, `sushi/ats.py`,
`sushi/sources.py`, `sushi/jobsdb.py` → `sushi/letters.py` →
`sushi/autofill.py`, `sushi/directfill.py` → `sushi/learning.py`.

---

## 5. Matching

`score_role()` in `sushi/discovery.py:777`. Internal score, max 17:

| Part | Points | Reason shown |
|---|---|---|
| Title | +6 target title in the job title; +4 most of its words | "matches your target role" / "close to …" |
| Seniority (ic < lead < manager < director < exec) | +3 same, +2 a step up, +1 a step down, −3 otherwise | "right seniority" / "a step up" / "seniority mismatch" |
| Skills and domains | +1 each, up to +3 | "mentions …" |
| Location | +3 compatible country/region/city, +2 worldwide, +1 remote with no country, −3 a country you can't work in | "in your locations" / "may be outside where you can work" |
| Workplace | ±2 against remote / hybrid / on site | |

Internships, graduate schemes and similar are scored 0. A job whose title
doesn't match is capped at 4. The default feed bar is 8.

The **match percentage** users see (`match_percent()`, `:873`) is separate:
title up to 46, seniority 18, skills 20, location 16, clamped to 12–99%.

---

## 6. Applying, in more detail

- **Browser:** one persistent Chrome profile in the app-data folder, so
  sign-ins to Greenhouse, Workday, LinkedIn and Google survive between
  applications. Each application opens a new tab. Visible by default.
- **Direct fill** (`sushi/directfill.py`): reads the largest form on the page,
  follows an embedded ATS iframe or an Apply button if needed, uploads the CV
  and letter first, then fills up to 6 pages, 3 passes per page (so questions
  that appear after an answer are filled too). It presses only Next/Continue,
  never Submit.
- **Answer order:** exact match in the answer bank → profile rule → close
  match in the bank → AI.
- **Agent fill** (`sushi/autofill.py`): browser-use agent, up to 60 steps,
  reports what it left unanswered and what it guessed. Falls back to direct
  fill if it fails.
- **After filling:** status becomes "Needs you" (left open for review),
  "Submitted", or "Failed" with the reason.
- **Statuses:** matched, queued, filling, needs_review ("Needs you"),
  submitted, failed, skipped ("Passed").

## 7. The learning bank

`sushi/learning.py`. Stored in the `answers` table of the local database.

- Polls the open form every 2.5 s; a value must be stable across two polls.
- Your values are saved as **human**, AI values as **agent**; agent never
  overwrites human. Amber (guessed) fields are not learned until you touch
  them.
- Question matching: exact → 82% text similarity → shared words → topic
  (visa, right to work, sponsorship, notice period, start date, salary,
  education, experience, how you heard, relocation, on-site, criminal record,
  clearance, LinkedIn, portfolio).
- Never learned: passwords, codes, security questions, card, bank, sort code,
  IBAN, National Insurance, social security numbers.
- Demographics are learned only when turned on in Settings → Learning.

## 8. Insane mode

`sushi/extras.py:147`, `sushi/ui/pages/auto_apply.py`.

- Settings: on/off, daily cap (default 10, range 1–100), quality bar
  (Only the best = 10, Strong matches = 7 (default), Worth a look = 5).
- Arming is a standing order: if nothing qualifies it waits, and starts when
  new matches arrive. Disarming lets the current application finish.
- Applies one at a time, skips closed postings, submits only if auto-submit
  is on — otherwise each form is left open for review.

## 9. Models and cost

Presets in onboarding (`sushi/providers.py`), recommended first:

| Preset | Default model | Cost note in the app |
|---|---|---|
| Claude Code | `claude-code:sonnet` | No extra cost (uses your Claude Code sign-in) |
| Groq | `llama-3.3-70b-versatile` | Free within the tier (daily limits, no card) |
| Google Gemini | `gemini-2.0-flash` | ~$0.03 per application |
| OpenAI | `gpt-4o-mini` | ~$0.05 |
| Anthropic | `claude-haiku-4-5-20251001` | ~$0.34 |
| Local (Ollama) | best installed (qwen2.5:14b first) | Free |

Also supported: Azure OpenAI, and any OpenAI-compatible endpoint by base URL.
Keys are read from Settings, then a `.env_amm` / `.env` file, then the
environment (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`).
The app shows where the key came from, never the key.

Cost basis (`sushi/providers.py:3`): an agent-driven application is about
25 steps and 320k input tokens. Direct and Auto fill use far fewer.

---

## 10. Design system

Source: `sushi/ui/theme.py`, `sushi/ui/brand.py`, `sushi/ui/starfield.py`.
Light theme only. One saturated colour (coral); the primary button is
near-black ink, not coral.

### Colours

| Token | Hex | Use |
|---|---|---|
| Paper (BG) | `#F6F6F0` | Page background |
| Surface | `#FFFFFF` | Cards |
| Surface 2 / 3 | `#EFEFE7` / `#E8E8E0` | Sunken fills, hover, segment tracks |
| Line / 2 / 3 | `#E6E6E2` / `#DDDDD5` / `#CBCBC7` | Hairlines, card edges, pressed edges |
| Ink | `#252525` | Body text, primary buttons (hover `#3A3A36`) |
| Muted / Faint | `#686864` / `#9A9A9A` | Secondary text, captions |
| **Accent (coral)** | **`#C4543C`** | Selection, links, match %, table header |
| Accent hover / deep | `#A64229` / `#7C2E1A` | Hover; text on accent wash |
| Accent soft / line | `#FBEBE4` / `#F0CFC0` | Wash behind selections and pills; its edge |
| Hero gradient (135°) | `#C4543C` → `#A23E2A` → `#7C2E1A` | Starfield panels |
| Text on gradient | `#FFF8F4` / `#FBE7DF` / `#F2CEC2` | Titles / body / eyebrows |
| Good / Warn / Bad | `#4C7A2E` / `#A8761A` / `#A62A1A` | Status |
| Guess card | `#FFFBEF` bg, `#C58A00` border | "Sushi guessed this" |
| Sidebar ground | `#FCFCF9` | Sidebar and rail |

Mascot palette: salmon `#F4836A` / `#E0654A` / `#B8442C`, marbling
`#FFD2C2`, rice `#FFFDF6` / `#E2DAC6`, face `#2B1A15` (never pure black).

### Type

| Role | Family | Notes |
|---|---|---|
| Display | Fraunces → Source Serif 4 → Georgia | Weight 400, tight tracking, never bold. H1 28, H2 20, hero 25. Fraunces is not bundled, so it renders in Georgia today. |
| Body | Segoe UI → Inter → Helvetica Neue → Arial | 16 px base, small 13, micro 11 |
| Mono | Cascadia Mono → Consolas | Reported facts: "via Greenhouse", salary bands, model line, status |
| Eyebrows | Body, 13 px / 600, capitals | "DASHBOARD", "EVERY MATCH" |

Bricolage Grotesque and Hanken Grotesk are in `sushi/assets/fonts/` but
unused.

### Shape and space

- Radii: card 20, panel 16, input/tile 12, button 10, chip 8, job card 14, pills fully round.
- Spacing scale 6 / 10 / 16 / 24 / 32; page padding 28; card padding 22; controls 40 px high.
- Almost no shadows: 1 px hairlines instead.
- Icons: Lucide geometry, 24 grid, 1.9 stroke, round caps, in tinted tiles in the sidebar.

### Signature elements

- **Starfield panel:** the coral → brown gradient with scattered specks and
  four-point sparkles, denser top-right, gently twinkling. Used for hero
  areas, onboarding and Insane mode. A **dotted-grid** variant is used for
  the Dashboard stats banner.
- **Mascot:** a piece of salmon nigiri with two dot eyes and a wide smile;
  sleeps with floating "z"s while Insane mode runs.
- **Wordmark:** mascot + "Sushi" in the serif; the splash uses tracked
  capitals "SUSHI".
- **Job card:** logo, coral "88% match", title, location, mono salary,
  "via Ashby", coral "Instant Apply →".
- **Insane disc:** large round button, ink on standby, red with pulsing rings
  when armed, lightning bolt, "ARM" / "DISARM".

## 11. Voice

- Plain verbs; buttons say what happens: "Look again", "Read my CV", "Fill it
  in Chrome", "Submit it" / "Fill it in" — never Yes / OK.
- Sushi is a third-person actor: "Sushi fills the form…", "Sushi never types
  or stores a password."
- Short sentences ending in a full stop, titles included: "Pick a model.",
  "You're set."
- Honest about limits and reassuring: "Never untrue.", "Nothing is sent until
  you arm it.", "Your corrections always win."
- Empty states say what to do next. Errors are calm and point to the fix:
  "That didn't work." … "Settings → Model".
- British spelling, sentence case, capitals only for eyebrows, "·" as a
  separator, "…" for things in progress.
- One playful edge: "Insane mode", ARM / DISARM, the sleeping mascot.

---

## 12. Where the docs disagree with the code

Fix these on the website (and ideally in the README):

| Source says | Code says |
|---|---|
| Older README: another product's design language, cream and olive | Fixed. The accent is now gold `#B68235` on greyscale, from the team's wireframe. |
| README: 1,039 companies, six ATS | 25,219 companies, 15,918 readable boards, 13 ATS. |
| README: page names "Browse jobs", "Insane mode lanes", "The four screens: Your CV / What we understood / Matches / Applications" | Pages are Dashboard, All jobs, Insane mode, Manual apply, Tracker, Profile, Settings. Lanes were removed. |
| README: model switcher in the title bar, daily cap in the title bar | Neither is in the title bar now. |
| README: default model `claude-sonnet-5` | Onboarding recommends Claude Code first; `claude-sonnet-5` is only the Anthropic provider default. |
| README: Workday not supported | Workday and Personio readers exist (Taleo still doesn't). |
| Settings: "six first-run steps" | There are four. |
| Comments: "fifteen-a-day cap" | Default is 10. |
| Settings: Resume optimisation Off / Honest / Aggressive | Not used by the resume renderer yet. |
| `config.py` / `storage.py` docstrings: keyring | Keys are stored in the local database. |

## 13. Code map

| Path | Purpose |
|---|---|
| `main.py` | Launcher; re-runs itself under `.venv`, sets up logging, splash, main window |
| `sushi/config.py` | Paths, settings, default models, key resolution |
| `sushi/env.py` | Reads keys from `.env_amm` / `.env`, including loose notes |
| `sushi/models.py` | `Profile`, `Role`, `Application`, statuses |
| `sushi/storage.py` | `sushi.db`: settings, profile, applications, answers |
| `sushi/cv.py` | CV text extraction and profile building |
| `sushi/llm.py`, `providers.py`, `local_llm.py`, `claude_code.py` | Model access: Anthropic, OpenAI, Azure, Ollama, Claude Code CLI |
| `sushi/ats.py` | The 13 ATS readers, board probing |
| `sushi/sources.py` | The 8 job feeds |
| `sushi/discovery.py` | Company corpus, scoring, match % |
| `sushi/jobsdb.py` | `jobs.db` job cache, refresh schedule, rescoring, search |
| `sushi/letters.py`, `resume.py` | Cover letters (AI), resume PDF (templates) |
| `sushi/autofill.py`, `directfill.py` | Browser sessions, direct fill, agent fill |
| `sushi/learning.py`, `knowledge.py` | Answer bank, form watcher, import of old answers |
| `sushi/extras.py` | Apply settings, Insane mode config, tracker stages, CSV, pasted links |
| `sushi/assistant.py` | Assistant replies and actions |
| `sushi/logos.py`, `branding.py`, `diagnostics.py` | Company logos, the browser's Sushi branding, logging |
| `sushi/ui/` | PyQt6 interface; `pages/` holds one file per screen |
| `sushi/data/companies.jsonl` | The shipped company corpus |
| `tests/`, `tools/qa/qa_*.py` | Unit tests; QA suites (journey, fields, browser, UI, screenshots) and their fictional test CV |
| `docs/` | Feature document generator (the .docx/.pdf are generated, not tracked), `MISSING.md` (roadmap), this file |
| `tools/` | Dev launchers: `main_refreshed.py` (a clean first run), `import_boards.py` |

**Data on disk:** app data in `%LOCALAPPDATA%\sushi` (Windows),
`~/Library/Application Support/sushi` (macOS) or `~/.local/share/sushi` —
`sushi.db`, `jobs.db` (a rebuildable cache, about 300 MB), the browser
profile, logs and logos. User files in `~/Documents/Sushi` (CVs, Cover
letters, Tailored resumes, Exports).

## 14. Roadmap and known gaps

From `MISSING.md`, in priority order:
1. A profile without a model, so the first screen works before a key is set.
2. Fetch job descriptions, improving match reasons, generated answers and Insane mode.
3. Collapse near-duplicate postings ("+3 similar").
4. Remember the answers that were actually submitted, per application.
5. Follow-up nudges and auto-ghosting in the tracker.
6. "Submit unless something was guessed" setting.
7. Keyboard flow on the jobs list (j/k, space, enter, a).
8. Saved searches with "new since last time" counts.

Deliberately not planned: degree / experience / sponsorship filters, multiple
CVs, email integration.

Known gaps (README): no Taleo; corpus includes some agencies and
universities; no OCR; title matching is by words, not meaning.
