# What Sushi is missing

Written 2026-10-26, after a first-principles pass over the whole journey —
cold start, CV, feed, choosing, applying, tracking, and coming back the next
day. Everything here is something I actually hit while testing, not a wish
list. Ordered by what I think the return is.

The question each one is measured against is the product's own: *I have a CV
and no job — take me from that to applications I can track, without making me
understand how you work.*

---

## 1. A profile without a model — unblocks the first five minutes

**What happens now.** `qa_journey` reports: *"CV reads (2338 chars); no
model-free profile path."* Reading the CV works offline. Turning it into a
profile does not — `cv.py` goes straight to the model. So a new user who has
not pasted an API key yet gets a CV upload that appears to do nothing, on the
very first screen.

**What to build.** A regex-and-heuristics first pass: name, email, phone,
LinkedIn, the most recent job title and employer, a skills list from a known
vocabulary. Perhaps 60% as good as the model's, which is 100% better than a
dead first screen. Then let the model enrich it when one is configured, and
mark which fields came from where — `Profile.inferred` already exists for
exactly this.

**Why it is worth the most.** It is the only item here that decides whether
somebody ever reaches the rest of the app.

---

## 2. Fetch the job description — one request, and three features get better

**What happens now.** Greenhouse's board endpoint omits the description, so
most postings have none. The Details drawer used to apologise for it (now it
offers the posting link instead, which is honest but not the same as having
it).

**What it costs us.** Three things are weaker than they look:

- **Match reasons** are computed from the title, department and location
  only. "mentions Logistics" is matching a word in a job *title*. The skills
  part of the score can almost never be earned, which is why real scores top
  out around 10 of a possible 20.
- **Generated answers.** "Why do you want to work here?" is written with the
  company name and nothing else. The answers are good because the CV is good,
  not because Sushi knows anything about the employer.
- **Insane mode's ordering** is therefore built on a thinner signal than the
  match percentage implies. The quality dial that sat on top of it has been
  removed — with a ceiling around 10 its three bands all selected the same
  roles — but the ordering it was filtering is the same score, so the top of
  the queue is only as good as the title match underneath it.

**What to build.** Fetch the posting text lazily — when the drawer opens, and
before an application — and cache it on the row. `extras.page_text()` already
does the fetching and stripping. One request per job you actually look at.

---

## 3. Collapse near-duplicate postings

**What happens now.** A real screenshot from the feed: four rows of
"Senior Event Logistics Associate / Coordinator / Executive / Specialist" from
one employer, then five rows of "Senior Data Analyst" from five. 3,527
matches, and a large share of the first page is the same job wearing different
hats.

**What to build.** Group by (employer, normalised title, location) and show
one row with "+3 similar". Expand on click. Ticking the group ticks the best
one, not all four — applying to four variants of the same role at one company
is worse than applying to one.

**Why.** It is the cheapest large improvement to the thing the user looks at
most. A list of 3,527 that is really ~1,200 distinct roles is a different
product to use.

---

## 4. Remember what was actually submitted

**What happens now.** The tracker records company, title, status, stage and a
note. It does not record **the answers that went out**. Sushi knew every one
of them at fill time and threw them away.

**What to build.** Store the applied plan per application — field, value, and
where it came from (profile / bank / guess). Then:

- The tracker row can show "you told them 6 years and £65–70k" when the
  recruiter calls.
- A second application to the same employer can be made consistent with the
  first.
- A guess that turned out wrong can be corrected *and traced* to every
  application that used it.

This is a small schema addition and the data is already in `Plan.fills`.

---

## 5. Follow-ups, because a tracker that never speaks is a spreadsheet

**What happens now.** Stages move when you move them. Nothing ever tells you
that eleven applications have sat at "applied" for three weeks.

**What to build.** An age column, and one honest nudge: *"14 applications,
none heard back in 21 days — these five were the best matches; want to chase
them?"* Plus auto-ghosting after a configurable period so the board reflects
reality without manual tidying.

---

## 6. Review before submit, per answer

**What happens now.** Autosubmit is all-or-nothing: either Sushi presses
Submit or it stops and leaves the browser open. With Explorative on, the
interesting middle case has no control — *"submit automatically unless
something was guessed"*.

**What to build.** A third setting: **submit unless flagged**. Everything
looked up goes out on its own; anything amber waits for you. That is the
setting most people would actually choose, and the one the feature was built
toward.

---

## 7. Keyboard flow on the list

A 3,500-row table that is worked through by ticking boxes wants `j`/`k` to
move, `space` to tick, `enter` to open the drawer, `a` to apply. Everything
else in the app is mouse-first and that is fine; this one screen is not.

---

## 8. Saved searches

The new filter bar answers "what do I want to see *now*". It does not
remember. "Remote data roles, 80%+, last week" is a query somebody will run
every morning. Saving a filter set — and badging how many new jobs match it
since last time — turns the app from something you search into something that
tells you.

---

## Deliberately not here

- **Degree level / years-of-experience / visa-sponsorship filters.** The
  reference design has them; the data does not exist. Job boards do not
  publish structured requirements, and inferring them from a description we
  do not currently fetch would be a filter that quietly lies. Worth
  revisiting *after* item 2.
- **Per-role resumes.** The resume builder tailors already, and a *generated*
  CV per application is a decision per application for a gain I cannot
  evidence. (A **library** of CVs you wrote yourself is a different thing,
  and it is built: see "More than one CV" in the README. The argument against
  it here — that a second CV doubles every decision in the app — turned out
  to be wrong, because only one is ever active, so nothing downstream had to
  learn about the second one at all.)
- **Email integration.** Reading a mailbox to detect replies is a large
  surface — auth, parsing, privacy — for something the tracker's stages
  already approximate.
