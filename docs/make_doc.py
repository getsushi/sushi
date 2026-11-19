"""Build the Sushi feature document.

Everything stated here is either in the code or was measured against a real
job board. Where a thing is not yet true, it says so.
"""
from __future__ import annotations

import pathlib

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

HERE = pathlib.Path(__file__).parent
FIG = HERE / "figures"
OUT = HERE / "Sushi - Features and Proposals.docx"

INK = RGBColor(0x0B, 0x0B, 0x0B)
INK_2 = RGBColor(0x52, 0x51, 0x4E)
INK_3 = RGBColor(0x8A, 0x89, 0x84)
ACCENT = RGBColor(0xC4, 0x54, 0x3C)
GOOD = RGBColor(0x0C, 0xA3, 0x0C)
CRIT = RGBColor(0xD0, 0x3B, 0x3B)

BODY = "Segoe UI"
DISPLAY = "Georgia"


def style(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = BODY
    normal.font.size = Pt(10)
    normal.font.color.rgb = INK
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.1
    for section in doc.sections:
        section.top_margin = Inches(0.7)
        section.bottom_margin = Inches(0.7)
        section.left_margin = Inches(0.9)
        section.right_margin = Inches(0.9)


def h(doc: Document, text: str, size: int = 15, space_before: int = 14,
      colour: RGBColor = INK, face: str = DISPLAY) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.keep_with_next = True
    run = p.add_run(text)
    run.font.name = face
    run.font.size = Pt(size)
    run.font.color.rgb = colour
    run.bold = face == BODY


def para(doc: Document, text: str, size: int = 10,
         colour: RGBColor = INK, italic: bool = False,
         space_after: int = 7) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    run = p.add_run(text)
    run.font.size = Pt(size)
    run.font.color.rgb = colour
    run.italic = italic


def bullets(doc: Document, items: list[str]) -> None:
    for item in items:
        p = doc.add_paragraph(style="List Bullet")
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.left_indent = Inches(0.26)
        bold, _, rest = item.partition(" — ")
        run = p.add_run(bold)
        run.bold = True
        run.font.size = Pt(10)
        if rest:
            tail = p.add_run(" — " + rest)
            tail.font.size = Pt(10)
            tail.font.color.rgb = INK_2


def figure(doc: Document, name: str, caption: str, width: float = 6.5) -> None:
    doc.add_picture(str(FIG / name), width=Inches(width))
    holder = doc.paragraphs[-1]
    holder.alignment = WD_ALIGN_PARAGRAPH.CENTER
    holder.paragraph_format.space_before = Pt(4)
    holder.paragraph_format.keep_with_next = True
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_before = Pt(2)
    cap.paragraph_format.space_after = Pt(9)
    run = cap.add_run(caption)
    run.font.size = Pt(8)
    run.font.color.rgb = INK_3
    run.italic = True


def shade(cell, hex_colour: str) -> None:
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")
    el.set(qn("w:fill"), hex_colour)
    cell._tc.get_or_add_tcPr().append(el)


def table(doc: Document, headers: list[str], rows: list[list[str]],
          widths: list[float] | None = None) -> None:
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, head in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(head)
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = INK
        shade(cell, "F3F1EE")
    for row in rows:
        cells = t.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            p.paragraph_format.space_after = Pt(2)
            run = p.add_run(value)
            run.font.size = Pt(9)
            run.font.color.rgb = INK if i == 0 else INK_2
    for row in t.rows:
        keep = OxmlElement("w:cantSplit")
        row._tr.get_or_add_trPr().append(keep)
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Inches(w)
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(0)
    spacer.add_run().font.size = Pt(2)


def page_break(doc: Document) -> None:
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


# ---------------------------------------------------------------- the document

doc = Document()
style(doc)

# --- page 1 -----------------------------------------------------------------
title = doc.add_paragraph()
title.paragraph_format.space_after = Pt(2)
run = title.add_run("Sushi")
run.font.name = DISPLAY
run.font.size = Pt(30)
run.font.color.rgb = ACCENT

sub = doc.add_paragraph()
sub.paragraph_format.space_after = Pt(14)
run = sub.add_run("Features as built, and what is worth adding next"
                  "      ·      27 October 2026")
run.font.size = Pt(9.5)
run.font.color.rgb = INK_3

para(doc,
     "Sushi is a desktop job-application autopilot. You give it a CV; it works "
     "out who you are, keeps a local database of live vacancies, scores them "
     "against you, fills the application form in a real Chrome window, and "
     "tracks what happened afterwards. Everything — the CV, the model key, the "
     "answer history — stays on the machine.",
     size=10.5)
para(doc,
     "This document describes what the software does today, where it has been "
     "verified against real job boards, and the eight additions I would make "
     "next. Every figure in it is measured rather than estimated; anything not "
     "yet true is marked as such.",
     colour=INK_2)

figure(doc, "pipeline.png",
       "Figure 1 — the whole product in one line. Only one step leaves the "
       "machine: the browser that fills the employer's form.")

h(doc, "What it is for")
para(doc,
     "A serious job hunt is two hundred applications of mostly identical "
     "information, typed by hand, into forms that each ask it differently. The "
     "repetition is the problem, not the thinking. Sushi takes the repetition: "
     "the candidate decides which jobs are worth applying to, and the software "
     "does the typing — accurately enough to be trusted, and visibly enough to "
     "be checked.")

h(doc, "Design commitments")
bullets(doc, [
    "Local by default — the CV, the credentials and the answer bank live on "
    "this machine. The only network calls are to job boards and to whichever "
    "model you configure.",
    "Nothing is sent without permission — the browser fills and stops. "
    "Submitting is a separate, explicit setting, off by default.",
    "A guess is always marked — anything the software worked out rather than "
    "looked up is outlined in amber in the form, listed back in the app, and "
    "never saved as fact until you confirm it.",
    "It gets better as you use it — every answer typed into a form is learned, "
    "so the next form that asks the same question is already answered.",
])

# --- page 2 -----------------------------------------------------------------
page_break(doc)
h(doc, "Features today", size=17, space_before=0)

h(doc, "1. Discovery — a local database, not a search", size=12, face=BODY)
para(doc,
     "Sushi keeps its own copy of the job market: 296,968 vacancies from 9,051 "
     "companies, harvested from 15,788 careers pages and refreshed daily — "
     "boards with openings every day, the rest weekly. Matches are therefore "
     "there the moment the app opens, and changing your target roles re-scores "
     "the whole corpus instantly instead of starting another sweep.")

h(doc, "2. Matching — a score you can argue with", size=12, face=BODY)
para(doc,
     "Each vacancy is scored on title fit, seniority distance, skills named in "
     "the title or department, whether you could legally take it, and whether "
     "it is worked the way you asked for. Every component produces a reason in "
     "words — “right seniority”, “in your locations”, “remote, as you asked” — "
     "because a score with no explanation is not something anyone can correct.")

h(doc, "3. Choosing — the list, and the shortlist", size=12, face=BODY)
bullets(doc, [
    "Dashboard — the four best, as cards. A morning glance.",
    "All jobs — the whole list, with filters for date, location, workplace, "
    "company, match percentage and saved; tick boxes; and one button that "
    "hands the selection to the automation.",
    "Rows that leave — a job you have applied to moves to the Tracker and the "
    "next best one takes its place, so the feed never offers the same job twice.",
])

h(doc, "4. Filling — the part that is hard", size=12, face=BODY)
para(doc,
     "Sushi drives a real Chrome window with your own signed-in profile. It "
     "reads the form, decides an answer for every field from your profile and "
     "answer bank, types them, attaches the CV, and stops before Submit. The "
     "detail of what it handles is on the next page.")

h(doc, "5. Tracking and learning", size=12, face=BODY)
bullets(doc, [
    "Tracker — a pipeline from Applied through Ghosted, Interviewing, Rejected "
    "and Offer, with CSV export.",
    "Answer bank — every answer typed into a form, by Sushi or by you, is kept "
    "and reused. A correction you make always outranks a guess.",
    "Assistant — a panel that answers questions about the feed and can act: "
    "queue the top matches, change targets, open the tracker.",
])

figure(doc, "coverage.png", width=5.2, caption=
       "Figure 2 — where the vacancies come from, and how far the filler has "
       "been proven on each. Green boards were filled end to end against live "
       "postings; SmartRecruiters is a known gap, described under Known limits.")

# --- page 3 -----------------------------------------------------------------
h(doc, "The fill engine", size=17, space_before=16)
para(doc,
     "Three modes, which are not variations of one another but two different "
     "machines with a bridge between them. Times and costs below were measured "
     "on the same live Greenhouse form.", colour=INK_2)

table(doc,
      ["Mode", "How it works", "One real form", "Cost"],
      [["Direct",
        "Reads the form, matches each label to your profile and answer bank, "
        "types what it knows. No model call at all.",
        "~2 seconds", "Nothing"],
       ["Auto",
        "The lookup first, then one short model call per question it could not "
        "look up. Only if questions still remain does it fall back to the "
        "browser agent.",
        "43 seconds — 12 fields filled, 4 flagged as guesses, 2 left",
        "~7 small calls"],
       ["Agent",
        "Hands the whole page to a browser agent: look, act, look again. Never "
        "uses the lookup. Re-sends the page and its own instructions each step.",
        "Did not finish a form in 15 minutes (24 steps)",
        "~12,000 tokens and ~$0.12 per step"]],
      widths=[0.75, 2.6, 1.85, 1.3])

para(doc,
     "The recommendation is auto. Agent mode earns its place only when the "
     "reader cannot see the form at all; it is the fallback, not the better "
     "option. If the agent cannot run — a token limit too small, for instance — "
     "Sushi says so plainly and fills the form the other way rather than "
     "leaving an empty browser.", colour=INK_2)

h(doc, "Explorative mode", size=12, face=BODY)
para(doc,
     "Off, a question Sushi cannot look up is left blank and handed back. On, "
     "it answers from your CV with the most likely answer for someone with your "
     "history — and flags every one: amber in the browser with the reason on "
     "hover, listed in the app for checking, and never written to the answer "
     "bank until you confirm it. Passwords, pay, immigration status and "
     "protected characteristics are never guessed, whatever the setting.")

h(doc, "What the filler copes with", size=12, face=BODY)
table(doc,
      ["Kind of field", "What makes it hard", "Handled"],
      [["Text, email, phone, URL", "React ignores a value set without the "
        "right events", "Yes"],
       ["Number and date", "Silently reject “6 years” and “1 March 2026”",
        "Coerced to 6 and 2026-03-01"],
       ["Select", "Option text differs from its value; a placeholder row",
        "Matched on text; placeholders never chosen"],
       ["Multi-select, tick-box groups", "Need every answer, not the first",
        "All matching options"],
       ["Custom comboboxes", "No <select> anywhere — Greenhouse and Workday",
        "Opened, read and clicked with real input"],
       ["Typeaheads", "Keep nothing until one of their own options is clicked",
        "Typed, then the match clicked and verified"],
       ["Salary bands", "“£65,000” against “65,000 – 70,000 £”",
        "Matched by arithmetic, not guessed"],
       ["Conditional questions", "Appear only after another is answered",
        "Up to three passes per page"],
       ["Multi-page forms", "Taleo and Workday run to four or five steps",
        "Walks up to six, never pressing Submit"],
       ["Embedded forms", "The form is in an iframe on a careers site",
        "Followed to the ATS and filled"],
       ["Declarations", "A privacy notice is not a question",
        "Listed for you; never agreed to on your behalf"]],
      widths=[1.55, 2.95, 2.0])

# --- page 4 -----------------------------------------------------------------
h(doc, "How it is verified", size=17, space_before=16)
para(doc,
     "212 checks across six suites, plus a screenshot renderer. The principle "
     "throughout is to assert on an effect — a row in the database, a value in "
     "a real browser, a file on disk — never on the control that triggered it. "
     "That rule exists because an earlier suite passed completely while the "
     "headline feature ran nothing at all.", colour=INK_2)

figure(doc, "verification.png", width=5.5, caption=
       "Figure 3 — what each suite is responsible for. qa_fields and "
       "qa_browser drive real headless Chrome; qa_journey walks the whole "
       "product from a cold start through a restart.")

para(doc,
     "Two habits matter more than the count. The first is that fills are "
     "verified against the page's own state rather than by reading the value "
     "back — a value read back can be correct right up to the moment something "
     "else clears it. The second is that fixtures are checked both ways: the "
     "test which proves the CV must be attached before filling is paired with "
     "one proving the fixture still fails if it is attached afterwards.",
     colour=INK_2)

h(doc, "Known limits")
bullets(doc, [
    "SmartRecruiters — 35,257 vacancies on file and the reader sees none of "
    "them. Their application is built from web components: 1,805 shadow roots "
    "with the fields inside, and ordinary DOM queries cannot cross a shadow "
    "boundary. Diagnosed, not yet fixed.",
    "Taleo, Workday and iCIMS — not in the discovery layer at all. They do not "
    "publish a clean public board, so no vacancy from them is on file. The "
    "multi-page support they would need is built and tested; the discovery is "
    "not.",
    "No job description — most boards publish only a title, location and "
    "department. Match reasons and generated answers are both thinner for it.",
    "Agent mode is slow on a local-CLI provider — each step is a full round "
    "trip, so an agent run costs minutes where auto costs seconds.",
])

# --- page 5 -----------------------------------------------------------------
h(doc, "Proposed next", size=17, space_before=16)
para(doc,
     "Ordered by return. The first three are each worth more than the rest "
     "combined.", colour=INK_2)


def prop(title: str, text: str, first: bool = False) -> None:
    h(doc, title, size=11.5, space_before=9, face=BODY,
      colour=ACCENT if first else INK)
    para(doc, text, space_after=3)


prop("1. See through shadow DOM",
     "A deep query that crosses shadow boundaries, through the reader, the "
     "filler and the coordinate scripts. This single change takes 35,257 "
     "SmartRecruiters vacancies from “agent or nothing” to the 40-second auto "
     "path, and future-proofs the reader against every ATS that moves to web "
     "components — which is the direction all of them are going.", first=True)

prop("2. Fetch the job description",
     "One request per posting you actually open, cached. It improves three "
     "things at once: match reasons stop being computed from a job title, "
     "generated answers learn something about the employer, and the quality "
     "dial starts sorting on a real signal. The fetching and stripping code "
     "already exists for pasted links.", first=True)

prop("3. Collapse near-duplicate postings",
     "The feed routinely shows four variants of one role at one employer, then "
     "five identically-titled roles at five others. Grouping by employer, "
     "normalised title and location — with “+3 similar” — would make a list of "
     "3,500 behave like the 1,200 distinct roles it really is.", first=True)

h(doc, "Then, in order", size=11.5, space_before=11, face=BODY)
table(doc,
      ["", "What it is", "Why it earns a place"],
      [["4", "Remember what was actually submitted",
        "The tracker keeps a status, not the answers that went out — though "
        "Sushi knew every one at fill time. Storing them tells you what you "
        "told an employer when they ring, and traces a wrong guess to every "
        "application that used it."],
       ["5", "Submit unless something was guessed",
        "Autosubmit is all-or-nothing. The setting most people want is the "
        "middle one: send it when everything was looked up, wait for me when "
        "anything is amber. The flag already exists."],
       ["6", "Follow-ups",
        "A tracker that never speaks is a spreadsheet. An age column, one "
        "honest nudge, and auto-ghosting after a set period."],
       ["7", "Keyboard flow on the list",
        "A 3,500-row table worked through by ticking boxes wants j/k to move, "
        "space to tick, enter to open and a to apply. One screen, not the "
        "whole app."],
       ["8", "Saved searches",
        "The filters answer “what do I want to see now”, then forget. Saving "
        "a set — and badging what is new since last time — turns the app from "
        "something you search into something that tells you."]],
      widths=[0.25, 1.95, 4.3])

h(doc, "Deliberately not proposed", size=12, space_before=4)
para(doc,
     "Degree-level, years-of-experience and visa-sponsorship filters: the "
     "reference designs have them and the data does not exist. Boards do not "
     "publish structured requirements, and inferring them from a description "
     "Sushi does not yet fetch would be a filter that quietly lies — worth "
     "revisiting after item 2. Likewise multi-CV support, which doubles every "
     "decision in the app for a gain I cannot evidence, and mailbox "
     "integration: a large surface for what the tracker approximates already.",
     colour=INK_2)

doc.save(OUT)
print("wrote", OUT)
