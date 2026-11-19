"""Filling a form without a model.

The browser agent is a large language model reasoning its way around a page
over thirty-odd steps. That is the right tool for a page nobody has seen
before, and the wrong tool for this: an application form is a list of
labelled questions, and for most of them the answer is already known —
it is on the CV, or in the answer bank because the user typed it into the
last form.

So this does what Jobly actually did. Read the fields, match each label to
something we already hold, type it in, upload the CV, and leave whatever is
left for the person. Nothing here calls a model, which means it runs with no
API credit, no GPU, and in about two seconds rather than ten minutes.

The split that matters: *identity and facts* are deterministic, and only
genuinely new prose — "why do you want to work here" — needs generating. So
free text is left blank by default and offered to whatever model is
configured, local or otherwise, as a separate opt-in step. A form filled
with everything except one paragraph is a minute's work to finish. A form
that needed a GPU is not usable at all.

Two opt-in steps sit on top of that lookup, in increasing order of nerve:

- ``answer_free_text`` writes the prose questions from the CV and banks them
  as agent answers. This is what Settings → Fill mode "auto" runs.
- ``guess_answers`` is **Explorative**: it answers *everything* still blank
  with the most likely answer for this person, flags each one in the page and
  in the app, and banks none of it. See the long note above it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import learning
from .models import Profile

log = logging.getLogger(__name__)


@dataclass
class Filled:
    """One field we can answer, and why.

    ``source`` is also how a guess is told apart from a fact, everywhere
    downstream: the browser outlines a guessed field in amber, the app lists
    it back for checking, and nothing stores it in the answer bank until the
    user has said it is right.
    """
    key: str          # the field's id/name, as the page knows it
    label: str        # the question as the form asked it
    value: str
    source: str       # "profile" | "bank" | "derived" | "written" | "guess"
    confidence: str = ""   # "high" | "medium" | "low" — guesses only
    basis: str = ""        # why the model answered this way — guesses only
    #: Every answer, when the question takes more than one — a multiple
    #: select, a group of tick-boxes. ``value`` is the same thing joined up,
    #: for reading back to a person.
    choices: list[str] = field(default_factory=list)
    #: "" for an ordinary control, or "combobox"/"typeahead" for one that has
    #: to be clicked through rather than written to. See ``choose_scripts``.
    widget: str = ""

    @property
    def guessed(self) -> bool:
        """Composed rather than looked up, so it has to be checked."""
        return self.source in ("written", "guess")


@dataclass
class Plan:
    fills: list[Filled] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # refused on purpose
    consents: list[str] = field(default_factory=list)  # yours to tick
    declined: list[str] = field(default_factory=list)  # the model saw, could not answer

    @property
    def guesses(self) -> list[Filled]:
        """The fills that are the model's work rather than a lookup."""
        return [f for f in self.fills if f.guessed]

    @property
    def summary(self) -> str:
        known = [f for f in self.fills if not f.guessed]
        bits = [f"{len(known)} field(s) filled"]
        if self.guesses:
            bits.append(f"{len(self.guesses)} guessed")
        if self.unanswered:
            bits.append(f"{len(self.unanswered)} left for you")
        if self.consents:
            bits.append(f"{len(self.consents)} box(es) to tick yourself")
        if self.skipped:
            bits.append(f"{len(self.skipped)} skipped on purpose")
        return ", ".join(bits)


#: label pattern -> the profile key that answers it, and whether the word
#: has to be near the front of the label.
#:
#: Ordered: the first match wins, so the specific patterns come before the
#: general ones ("first name" before "name", "current company" before
#: "company").
#:
#: The third column is the one that matters. A word like "sponsorship" or
#: "linkedin" means the same thing wherever it appears, so it is allowed
#: anywhere in the label — which it has to be, because the standard wording
#: is "Will you now or in the future require sponsorship…", eight words in.
#: A word like "city" or "country" does not: "Are you happy to work in our
#: office in White City 3 days a week?" is a yes/no question, and treating
#: it as "where do you live" answered it "Bristol".
_RULES: list[tuple[re.Pattern, str, bool]] = [
    (re.compile(r"\b(first|given|fore)\s*name\b|\bfirstname\b", re.I), "first_name", True),
    (re.compile(r"\b(last|family|sur)\s*name\b|\blastname\b|\bsurname\b", re.I), "last_name", True),
    (re.compile(r"\b(full|legal|preferred)\s*name\b|^name$", re.I), "full_name", True),
    (re.compile(r"\be-?mail\b", re.I), "email", False),
    (re.compile(r"\b(phone|mobile|telephone|cell)\b", re.I), "phone", False),
    (re.compile(r"\blinked ?in\b", re.I), "linkedin", False),
    (re.compile(r"\bgit ?hub\b", re.I), "github", False),
    (re.compile(r"\b(portfolio|personal (site|website)|website|web ?site)\b", re.I), "website", True),
    (re.compile(r"\bcurrent (employer|company|organisation|organization)\b", re.I), "current_company", False),
    (re.compile(r"\bcurrent (title|role|position|job title)\b|\bjob title\b", re.I), "current_title", False),
    (re.compile(r"\byears?\b.*\bexperience\b|\bexperience\b.*\byears?\b", re.I), "years_experience", False),
    (re.compile(r"\b(work (authori[sz]ation|permit)|right to work|legally (allowed|authori))", re.I),
     "work_authorisation", False),
    # "sponsorship" on its own is always this question. "visa" on its own is
    # not: "Which visa do you currently hold?" is a different question with
    # a different answer, and answering it "No" is worse than leaving it.
    (re.compile(r"\bsponsorship\b|"
                r"\b(requir|need|sponsor)\w*\b[^?]{0,40}\bvisa\b|"
                r"\bvisa\b[^?]{0,40}\b(requir|need|sponsor)\w*\b", re.I),
     "requires_sponsorship", False),
    (re.compile(r"\b(salary|compensation|pay)\b.*\b(expect|desired|requirement)|"
                r"\bexpected (salary|compensation)\b", re.I), "salary_expectation", False),
    (re.compile(r"\bnotice period\b|\bweeks?\W{0,2} notice\b|\b(earliest|available) (start|to start)\b|"
                r"\bwhen can you start\b", re.I), "notice_period", False),
    (re.compile(r"\b(city|town|location|where are you based|based in|address)\b", re.I), "location", True),
    # "nation" was here too, and it matched "nation-wide" inside a 250-word
    # essay question on a real Canonical form — so "United Kingdom" was
    # typed into a box asking for evidence of school performance.
    (re.compile(r"\bcountry\b", re.I), "country", True),
]

#: Past this, a label is a question, not the name of a field. Every rule
#: above looks for a word *somewhere* in the label, which is right for
#: "Current city" and catastrophic for a paragraph that happens to contain
#: it. No real identity field is labelled with an essay prompt.
_RULE_LABEL_LIMIT = 120

#: And the matching word has to be near the front, because that is what it
#: means for a question to be *about* something. Two real examples, both of
#: which were answered wrongly before this:
#:
#:   "Are you happy to work in our office in White City 3 days a week?"
#:       — "city" at word nine, answered "Bristol"
#:   "Let us know your UK Right to Work status:"
#:       — "right to work" at word six, answered "Yes" to a question whose
#:         options are six different immigration statuses
#:
#: Against the ones that must keep working: "Location (City)" word one,
#: "In which country do you currently work?" word three, "What are your
#: salary expectations for this role?" word four.
_RULE_HEAD_WORDS = 6

#: Field types and labels we will not touch, whatever we happen to know.
_NEVER = re.compile(
    r"\bpassword\b|\bpasscode\b|\botp\b|\bverification code\b|\bcvv\b|"
    r"\bcredit card\b|\bsort code\b|\biban\b|\bbank\b|\bnational insurance\b|"
    r"\bsocial security\b", re.I)

#: Long-form questions. Known answers are reused; nothing is invented.
_FREE_TEXT = re.compile(
    r"\bwhy\b|\btell us\b|\bdescribe\b|\bexplain\b|\bcover letter\b|"
    r"\bwhat (makes|interests|excites)\b|\bin your own words\b", re.I)

#: Tick-boxes that are a declaration rather than a question: a privacy
#: notice, terms, a confirmation that what you wrote is true. There is no
#: answer to learn and nothing to type — you either agree or you do not, and
#: that is yours to say. So these are neither filled nor put to you as
#: questions; they are listed as the boxes still to tick.
_CONSENT = re.compile(
    r"\bprivacy (notice|policy|statement)\b|\bterms (and|&) conditions\b|"
    r"\bi (agree|consent|confirm|acknowledge|certify)\b|\bi have read\b|"
    r"\bconsent to\b|\bdata protection\b|\bgdpr\b|\bdeclaration\b", re.I)

#: Trailing "*", "(required)" and friends — kept out of the question when it
#: is shown back to you.
_REQUIRED_MARK = re.compile(
    r"\s*[\*∗]?\s*(\((required|optional)\)|required|optional)?\s*[\*∗]?\s*$",
    re.I)


#: Short words a real question legitimately begins with. Without this the
#: repeated-head rule eats the "I" from "I confirm I am over 18".
_OPENERS = {
    "i", "a", "an", "do", "is", "are", "am", "as", "at", "be", "by", "if",
    "in", "it", "my", "no", "of", "on", "or", "so", "to", "we", "you", "was",
    "has", "had", "can", "did", "for", "any", "all", "how", "why", "who",
    "the", "may", "and", "not", "its", "our",
}


def tidy_label(label: str) -> str:
    """The question as it should be read back, without the page's furniture.

    Two things get picked up along with the text. The required marker, which
    is punctuation rather than part of the question. And, on the React
    select widgets Greenhouse uses, the control's own value display, which
    ``aria-labelledby`` points at alongside the real label — that is where
    "us Are you a US Person?*" came from: "us" is the answer showing in the
    box, not a word in the question.

    The second is only stripped when the stray fragment is short, already
    appears in the rest, and is not a word a question would open with — "I
    confirm I am over 18" opens with a repeated "I" and is not noise.
    """
    text = _REQUIRED_MARK.sub("", (label or "").strip())
    words = text.split()
    if len(words) > 2 and len(words[0]) <= 3:
        head = words[0].strip(".,:;").lower()
        rest = words[1:]
        if (head and head not in _OPENERS
                and head in {w.strip(".,:;*?!()").lower() for w in rest}):
            text = " ".join(rest)
    return text.strip()


#: Reads the form for *filling*: identity, label, current value and, where
#: the field is a choice, the options it will actually take.
#:
#: Deliberately a separate script from ``learning.HARVEST_JS``. That one
#: answers "what does this form say and what is in it", which is what the
#: watcher needs; this one answers "what can I write to, and what will it
#: accept", which needs the element's id or name and its option list. One
#: script trying to do both would serve neither well.
READ_JS = r"""
(() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();

  // A radio is labelled by its group, not by itself: the thing next to the
  // button says "Yes", and the question is the legend above the pair. This
  // has to be asked *before* the element's own label, because each radio is
  // normally wrapped in one — so the group question lost every time and the
  // bank was asked to recall an answer to "Yes".
  // Lever and many hand-built forms have no fieldset: each button sits in
  // "<label><input> Yes</label>" and the question is a div above the list.
  // The group was read as a question called "Yes", which nothing could
  // answer. Climb while the ancestor holds only this group, and take the
  // first line of text that is not one of the group's own option labels.
  const untaggedGroup = (el, type) => {
    const name = el.name || '';
    if (!name) return '';
    const mates = document.querySelectorAll(`input[name="${CSS.escape(name)}"]`);
    if (type === 'checkbox' && mates.length < 2) return '';
    const own = new Set();
    let n = el.parentElement, last = null;
    for (let i = 0; n && i < 8; i++, n = n.parentElement) {
      const others = [...n.querySelectorAll('input:not([type=hidden]), select, textarea')]
        .filter(e => e.name !== name);
      if (others.length) {
        // The group shares its box with an "Other / Custom" text field, so
        // the climb stops short of the heading. Read the heading just above
        // the group instead ("Pronouns" above Lever's pronoun boxes).
        const stop = n.parentElement;
        for (let k = last || el; k; k = k === stop ? null : k.parentElement) {
          const prev = k.previousElementSibling;
          const t = prev && clean(prev.innerText).split('\n')[0];
          if (t && t.length > 2 && t.length < 150 && !own.has(t)) return t;
        }
        break;
      }
      last = n;
      if (n.matches('label')) own.add(clean(n.innerText));
      n.querySelectorAll('label').forEach(l => own.add(clean(l.innerText)));
      const lines = (n.innerText || '').split('\n').map(clean)
        .filter(t => t.length > 2 && !own.has(t));
      if (lines.length) return lines[0];
    }
    return '';
  };

  const groupLabel = el => {
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type !== 'radio' && type !== 'checkbox') return '';
    const grp = el.closest('fieldset, [role="radiogroup"], [role="group"]');
    if (!grp) return untaggedGroup(el, type) || containerText(el);
    const aria = grp.getAttribute('aria-label');
    if (aria) return clean(aria);
    const by = grp.getAttribute('aria-labelledby');
    if (by) {
      const n = document.getElementById(by.split(/\s+/)[0]);
      if (n) return clean(n.innerText);
    }
    const legend = grp.querySelector('legend');
    if (legend) return clean(legend.innerText);
    // A group wrapper with no name of its own: same as having none.
    return untaggedGroup(el, type) || containerText(el);
  };

  const labelFor = el => {
    const grouped = groupLabel(el);
    if (grouped) return grouped;
    const aria = el.getAttribute('aria-label');
    if (aria) return clean(aria);
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const parts = by.split(/\s+/)
        .map(id => document.getElementById(id))
        .filter(Boolean).map(n => n.innerText);
      if (parts.length) return clean(parts.join(' '));
    }
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab) return clean(lab.innerText);
    }
    const wrap = el.closest('label');
    if (wrap) return clean(wrap.innerText);
    const prev = el.previousElementSibling;
    if (prev && /label|span|div|p/i.test(prev.tagName)) return clean(prev.innerText);
    // Lever, and plenty of hand-built forms, put the question in a div
    // above the box and only "Type your response" inside it. The
    // placeholder used to be the label, so six Entrata questions (expected
    // pay, notice, years of experience) reached the model as "Type your
    // response" and came back unanswered.
    const above = containerText(el);
    if (above) return above;
    return clean(el.getAttribute('placeholder') || el.name || el.id);
  };

  // The question text in this field's own box: climb while no other
  // control shares the ancestor, take its first line that is not one of
  // the field's own option labels or its placeholder.
  const containerText = el => {
    const own = el.name || el;
    const skip = new Set([clean(el.getAttribute('placeholder') || '')]);
    let n = el.parentElement;
    for (let i = 0; n && i < 8; i++, n = n.parentElement) {
      const others = [...n.querySelectorAll('input:not([type=hidden]), select, textarea')]
        .filter(e => (e.name || e) !== own);
      if (others.length) break;
      n.querySelectorAll('label, option').forEach(x => skip.add(clean(x.innerText)));
      if (n.matches('label')) skip.add(clean(n.innerText));
      const line = (n.innerText || '').split('\n').map(clean)
        .find(t => t.length > 2 && !skip.has(t));
      if (line) return line;
    }
    return '';
  };

  const visible = el => {
    if (el.type === 'hidden') return false;
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  };

  const disabled = el => el.disabled
    || el.getAttribute('aria-disabled') === 'true'
    || !!el.closest('fieldset[disabled]');
  const readonly = el => !!el.readOnly
    || el.getAttribute('aria-readonly') === 'true';

  // The popup list belonging to a control. Half of every modern ATS form is
  // one of these: a div you click, a list that appears, an option you pick.
  // There is no <select> anywhere in it, and nothing below will find it
  // unless the control says which list is its own.
  const listboxFor = el => {
    const ref = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
    if (ref) {
      for (const id of ref.split(/\s+/)) {
        const n = document.getElementById(id);
        if (n && (n.getAttribute('role') === 'listbox'
                  || n.querySelector('[role="option"]'))) return n;
      }
    }
    // Otherwise only the two elements straight after it. A popup belongs to
    // the control it follows; a list further away than that belongs to some
    // other control, and guessing wrongly puts one field's options on
    // another field's question.
    let n = el.nextElementSibling;
    for (let i = 0; n && i < 2; i++, n = n.nextElementSibling) {
      if (n.getAttribute && n.getAttribute('role') === 'listbox') return n;
      const inner = n.querySelector && n.querySelector('[role="listbox"]');
      if (inner) return inner;
    }
    return null;
  };

  const optionsOf = box => box
    ? [...box.querySelectorAll('[role="option"]')]
        .map(o => clean(o.innerText || o.textContent)).filter(Boolean)
    : [];

  // What a widget is showing as its answer. react-select empties its own
  // input once an option is committed and renders the label in a sibling,
  // so reading `.value` says "blank" about a field that is answered —
  // which made every pass re-fill the same dropdowns, and inflated the
  // count of fields filled by doing it.
  const PLACEHOLDER = /^(-+\s*)?(please\s+)?(select|choose|pick)\b|^-+$|^(none|n\/a)$|^start typing\b|^search\b/i;
  const shownValue = el => {
    const own = ('value' in el) ? clean(el.value) : '';
    if (own) return own;
    const active = el.getAttribute('aria-activedescendant');
    if (active) {
      const n = document.getElementById(active);
      if (n && clean(n.innerText)) return clean(n.innerText);
    }
    let n = el.parentElement;
    for (let i = 0; n && i < 3; i++, n = n.parentElement) {
      const slot = n.querySelector(
        '[class*="single-value"], [class*="multi-value__label"], [class*="selected"]');
      if (slot && clean(slot.innerText)) return clean(slot.innerText);
    }
    return '';
  };

  // A widget with no id cannot be found again when it comes to filling it,
  // so it is given one. Marking the page during a read is not free, but the
  // alternative is reading a control we have no way to write to.
  let minted = 0;
  const keyOf = el => {
    if (el.id) return el.id;
    if (el.getAttribute('name')) return el.getAttribute('name');
    let k = el.getAttribute('data-sushi-key');
    if (!k) {
      k = 'sushi-field-' + (++minted);
      el.setAttribute('data-sushi-key', k);
    }
    return k;
  };

  // A job page is a job page plus a website around it: a nav search box, a
  // newsletter signup, a cookie bar. Those are inputs too, and asking the
  // user to answer "Search C3 AI" as though it were an application question
  // is worse than missing a real one. So: work inside the form that holds
  // the application, identified as the one with the most fields, and fall
  // back to the whole page only when there is no form at all.
  const forms = [...document.querySelectorAll('form')];
  let scope = document;
  if (forms.length) {
    const best = forms
      .map(f => [f, f.querySelectorAll('input,textarea,select').length])
      .sort((a, b) => b[1] - a[1])[0];
    if (best && best[1] >= 3) scope = best[0];
  }

  // Chrome that looks like a field.
  const JUNK = /\b(search|newsletter|subscribe|cookie|consent banner|filter|sort by|query|keyword)\b/i;

  const seen = new Set();
  const out = [];
  for (const el of scope.querySelectorAll('input, textarea, select')) {
    if (!visible(el)) continue;
    // A field nobody can type into is not a field. Offering it means
    // planning an answer for it, reporting it filled, and submitting a form
    // where that answer is not there.
    if (disabled(el)) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || tag).toLowerCase();
    if (['submit', 'button', 'reset', 'search', 'image'].includes(type)) continue;

    const key = el.id || el.name || '';
    if (!key) continue;
    const groupKey = (type === 'radio' || type === 'checkbox') ? (el.name || key) : key;
    if (seen.has(groupKey)) continue;

    const label = labelFor(el).slice(0, 300);
    if (JUNK.test(`${key} ${label}`)) continue;
    seen.add(groupKey);

    const optionLabel = r => clean(r.closest('label')?.innerText
      || document.querySelector(`label[for="${CSS.escape(r.id)}"]`)?.innerText
      || r.value);

    let options = [];
    let multiple = false;
    let widget = '';
    if (tag === 'select') {
      options = [...el.options].map(o => clean(o.text)).filter(Boolean);
      multiple = !!el.multiple;
    } else if (type === 'radio') {
      options = [...scope.querySelectorAll(
        `input[type="radio"][name="${CSS.escape(el.name || '')}"]`)]
        .map(optionLabel).filter(Boolean);
    } else if (type === 'checkbox') {
      // A group of tick-boxes under one question is a multi-answer
      // question, and it was read as a single unlabelled box: the options
      // were never collected, so nothing could ever be matched against
      // them and every one of these went to the user as unanswered.
      const group = [...scope.querySelectorAll(
        `input[type="checkbox"][name="${CSS.escape(el.name || '')}"]`)];
      if (el.name && group.length > 1) {
        options = group.map(optionLabel).filter(Boolean);
        multiple = true;
      }
    } else if (el.list) {
      options = [...el.list.options].map(o => clean(o.value)).filter(Boolean);
      widget = 'typeahead';
    }

    // A text box with a popup list attached is a typeahead: typing into it
    // leaves the form holding nothing, because it keeps no value until one
    // of its own options has been clicked.
    if (!widget && tag === 'input'
        && !['radio', 'checkbox', 'file'].includes(type)) {
      const box = listboxFor(el);
      if (box || el.getAttribute('aria-autocomplete')
          || el.getAttribute('role') === 'combobox') {
        widget = 'typeahead';
        if (!options.length) options = optionsOf(box);
      }
    }

    let value = '';
    if (type === 'radio' || type === 'checkbox') {
      const checked = [...scope.querySelectorAll(
        `input[name="${CSS.escape(el.name || key)}"]:checked`)];
      value = checked.map(c => clean(c.value)).join(', ');
    } else if (tag === 'select' && el.multiple) {
      value = [...el.selectedOptions].map(o => clean(o.text)).join(', ');
    } else if (widget) {
      value = shownValue(el);
      if (PLACEHOLDER.test(value)) value = '';
    } else if (type !== 'file') {
      value = clean(el.value);
    }

    out.push({
      key: groupKey,
      label: label,
      type: type,
      value: value.slice(0, 2000),
      options: options.slice(0, 60),
      multiple: multiple,
      widget: widget,
      readonly: readonly(el),
      maxlength: el.maxLength > 0 ? el.maxLength : 0,
      required: el.required || el.getAttribute('aria-required') === 'true',
    });
  }

  // --- the widgets that are not form controls at all ---------------------
  //
  // Greenhouse's current UI, Workday and anything built on react-select
  // render a choice as a div you click with a list that appears. There is
  // no <select> and often no input either, so everything above walks
  // straight past the question — and it is usually a required one.
  for (const el of scope.querySelectorAll(
      '[role="combobox"], [aria-haspopup="listbox"]')) {
    const tag = el.tagName.toLowerCase();
    if (['input', 'select', 'textarea'].includes(tag)) continue;
    if (!visible(el) || disabled(el)) continue;
    const key = keyOf(el);
    if (seen.has(key)) continue;

    const box = listboxFor(el);
    const options = optionsOf(box);
    if (!options.length) continue;      // nothing to choose from: not a choice

    const label = labelFor(el).slice(0, 300);
    if (!label || JUNK.test(`${key} ${label}`)) continue;
    seen.add(key);

    // Whatever it is showing, but only if that is one of its own options —
    // otherwise the text is a placeholder ("Select…") and the answer is
    // still blank.
    const shown = clean(el.innerText || el.textContent);
    const chosen = options.find(o => o.toLowerCase() === shown.toLowerCase());

    out.push({
      key: key,
      label: label,
      type: 'select',
      value: chosen || '',
      options: options.slice(0, 60),
      multiple: el.getAttribute('aria-multiselectable') === 'true',
      widget: 'combobox',
      readonly: false,
      maxlength: 0,
      required: el.getAttribute('aria-required') === 'true',
    });
  }
  return JSON.stringify({fields: out});
})()
"""


def profile_value(key: str, profile: Profile) -> str:
    return (profile.form_answers() or {}).get(key, "")


# --- making an answer fit the box it goes in ---------------------------------
#
# This is where a form quietly loses an answer. A `number` input discards
# anything that is not a bare number; a `date` input discards anything that
# is not YYYY-MM-DD. Neither reports it — `el.value` simply comes back empty
# — so Sushi typed "6 years" into the years field, read back "", counted it
# as filled anyway, and left a required field blank on a submitted form.

#: The first number in a string, which is the answer inside "6 years",
#: "£65,000" and "about 6".
_NUMBER = re.compile(r"-?\d[\d,.]*")

#: Matched whole, not by prefix. On a prefix, "Marchish" is March and
#: "Augmented" is August — and a date that is confidently wrong is the worst
#: thing to put on a form, because nothing about it looks unanswered.
_MONTHS: dict[str, int] = {}
for _i, (_short, _long) in enumerate([
        ("jan", "january"), ("feb", "february"), ("mar", "march"),
        ("apr", "april"), ("may", "may"), ("jun", "june"),
        ("jul", "july"), ("aug", "august"), ("sep", "september"),
        ("oct", "october"), ("nov", "november"), ("dec", "december")], 1):
    _MONTHS[_short] = _i
    _MONTHS[_long] = _i
_MONTHS["sept"] = 9

#: The date formats a person actually types. Anything else is left alone and
#: reported unanswered rather than mangled into a plausible wrong date.
_DATE_PATTERNS = (
    # 2026-03-01
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), ("y", "m", "d")),
    # 01/03/2026 and 1-3-2026 — day first, which is the British reading.
    (re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b"), ("d", "m", "y")),
    # 1 March 2026, 1st Mar 2026
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?\s+(\d{4})\b", re.I),
     ("d", "M", "y")),
    # March 1, 2026
    (re.compile(r"\b([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I),
     ("M", "d", "y")),
)


def as_number(value: str) -> str:
    """The bare number in an answer, or "" if there is not one."""
    m = _NUMBER.search(value or "")
    if not m:
        return ""
    text = m.group(0).rstrip(".,").replace(",", "")
    try:
        number = float(text)
    except ValueError:
        return ""
    return str(int(number)) if number.is_integer() else str(number)


def as_date(value: str) -> str:
    """An answer as YYYY-MM-DD, or "" if it cannot be read as a date."""
    text = (value or "").strip()
    for pattern, order in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        parts = dict(zip(order, m.groups()))
        month = parts.get("m")
        if month is None:
            month = _MONTHS.get((parts.get("M") or "").lower())
            if month is None:
                continue
        try:
            y, mo, d = int(parts["y"]), int(month), int(parts["d"])
        except (KeyError, ValueError):
            continue
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return ""


#: Where a long answer may be cut. A sentence end first, then a word break —
#: never mid-word, which is what the browser does on its own and is the one
#: ending that looks like a mistake rather than a short answer.
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def fit(value: str, limit: int) -> str:
    """An answer trimmed to a length limit, ending somewhere sensible."""
    text = (value or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    window = text[:limit]
    # The last sentence that fits, which is as much of the box as whole
    # sentences can fill. Stopping short of the limit reads as a short
    # answer; stopping mid-clause reads as a bug.
    ends = [m.end() for m in _SENTENCE_END.finditer(window)]
    if ends:
        return window[:ends[-1]].strip()
    cut = window.rsplit(" ", 1)[0].strip() if " " in window else window
    return (cut or window).rstrip(",;:-") .strip()


def coerce(value: str, field_type: str, maxlength: int = 0) -> str:
    """An answer in the form this particular input will accept.

    Returns "" when the answer cannot be made to fit, which sends the
    question back to the user rather than into the form as something the
    browser will drop on the floor.
    """
    value = (value or "").strip()
    ftype = (field_type or "").lower()
    if ftype == "number":
        return as_number(value)
    if ftype == "date":
        return as_date(value)
    if ftype == "month":
        iso = as_date(value)
        return iso[:7] if iso else ""
    return fit(value, maxlength)


def search_query(value: str) -> str:
    """A shorter thing to type into a search box than the whole answer.

    The first phrase, before any comma or bracket. Greenhouse's location
    search answers "Bristol" with "Bristol, England, United Kingdom" and
    answers "Bristol, United Kingdom" with nothing at all, so the full
    answer is the worst possible query for it.
    """
    head = re.split(r"[,(/|]", (value or "").strip(), maxsplit=1)[0].strip()
    return head if len(head) >= 3 else (value or "").strip()


def split_answers(value: str) -> list[str]:
    """One answer per thing, for a question that takes several."""
    parts = re.split(r"\s*(?:,|;|\band\b|/|\|)\s*", value or "")
    return [p.strip() for p in parts if p.strip()]


#: Profile fields that describe the candidate, and wording that says a
#: field is asking about a different person.
_ABOUT_YOU = {"first_name", "last_name", "full_name", "email", "phone",
              "linkedin", "github", "website", "location", "country"}
_SOMEONE_ELSE = re.compile(
    r"\b(employee|referr\w*|referee|reference|recruiter|manager|supervisor|"
    r"emergency|colleague|friend|contact person|hiring)\b", re.I)

#: A question answered Yes or No, by how it opens.
_YES_NO_Q = re.compile(
    r"\s*(are|do|have|is|will|would|can|could|did)\s+(you|your)\b", re.I)


def match_rule(label: str, field_type: str = "") -> str:
    """Which profile field answers this label, by its wording.

    Refuses on an essay box and on a label too long to be a field name.
    Both guards exist because the rules match a word anywhere in the text:
    without them "…provincial, state or nation-wide scoring systems…" is a
    question about which country you live in.
    """
    if (field_type or "").lower() == "textarea":
        return ""
    label = label or ""
    if len(label) > _RULE_LABEL_LIMIT:
        return ""
    for pattern, key, strict in _RULES:
        m = pattern.search(label)
        if not m:
            continue
        if strict and len(label[:m.start()].split()) >= _RULE_HEAD_WORDS:
            continue        # the word is in the question, not its subject
        if key in _ABOUT_YOU and _SOMEONE_ELSE.search(label):
            # "Capco employee email address", "Referrer's name": somebody
            # else's details, not the candidate's.
            continue
        if key in ("location", "country") and _YES_NO_Q.match(label):
            # "Are you based in Germany?" wants Yes or No, not "Faisalabad".
            continue
        return key
    return ""


def plan_fill(fields: Iterable[dict], profile: Profile, *,
              include_demographics: bool = False,
              free_text: bool = False, role=None) -> Plan:
    """Decide what to put in each field. Pure — no browser, no network.

    ``fields`` are the harvester's dicts: ``label``, ``key``, ``type``,
    ``value``, and for a choice field ``options``.
    """
    plan = Plan()
    answers = profile.form_answers() if profile else {}
    # Blanks in the profile worked out from the rest of it (phone code,
    # location, job's country). Used only where the profile is empty, and
    # always planned as a guess so the user sees it on Home.
    from .infer import inferred_answers
    guessed_keys = {k: v for k, v in inferred_answers(profile, role).items()
                    if not answers.get(k)}
    answers = {**answers, **guessed_keys}

    for f in fields:
        label = tidy_label(f.get("label") or f.get("key") or "")
        key = f.get("key") or ""
        ftype = (f.get("type") or "").lower()
        if not key:
            continue

        # A declaration, not a question. Agreeing to a privacy notice on
        # someone's behalf is not Sushi's to do, and it is not an answer
        # worth banking either — so it is listed for you to agree to, once.
        #
        # Any control, not only a tick-box. Canonical puts both of theirs
        # behind a dropdown — "Acknowledge/Confirm", and "I agree to use
        # only my own words… the use of AI or other generated content will
        # disqualify my application" — and with the check tied to
        # checkboxes, Sushi answered both of them. The second one it
        # answered "Yes" to while composing the rest of the form.
        if _CONSENT.search(label):
            if not (f.get("value") or "").strip():
                plan.consents.append(label or key)
            continue

        # A button's text or a widget id read as a question ("UPLOAD FILE",
        # "cards[…][field12]"): nothing to answer.
        if learning.is_junk_question(label) and ftype != "file":
            continue

        # Never, whatever we know.
        if ftype == "password" or _NEVER.search(label):
            plan.skipped.append(label or key)
            continue
        if ((learning.is_demographic(label, key)
                or learning.options_are_demographic(f.get("options")))
                and not include_demographics):
            # Never answered for the user, and never put to them either:
            # "Gender?" arriving as a question on every form was the
            # loudest complaint. Where the form offers a way to decline,
            # that is chosen; where it does not, the box is left as it is.
            out = decline_option(f.get("options"))
            if out and not (f.get("value") or "").strip():
                plan.fills.append(Filled(
                    key=key, label=label, value=out, source="derived",
                    choices=[out] if ftype == "checkbox" else [],
                    widget=str(f.get("widget") or "")))
            else:
                plan.skipped.append(label or key)
            continue
        # A box nobody can type into is not ours to plan an answer for.
        if f.get("readonly"):
            continue
        # Something already in the box is the user's, not ours to replace.
        if (f.get("value") or "").strip():
            continue

        # Order of precedence, and it matters more than it looks.
        #
        # 1. The user's own answer to this exact question. A rule below is a
        #    guess about what a question means from how it is worded;
        #    this is the answer they typed into the same question last time.
        # 2. The profile, via a wording rule.
        # 3. A banked answer to a question worded differently.
        #
        # With 1 and 2 the other way round, "Which visa do you currently
        # hold?" matched the sponsorship rule and went into the form as
        # "No", and "Preferred office location" matched the location rule,
        # produced "Bristol", matched none of the three options offered and
        # was handed back as unanswerable — with "Remote (UK)" in the bank.
        value, source = "", ""
        exact = learning.recall_exact(label)
        if exact:
            value, source = exact, "bank"
        if not value:
            rule = match_rule(label, ftype)
            if rule and answers.get(rule):
                value, source = answers[rule], (
                    "guess" if rule in guessed_keys else "profile")
        if not value:
            recalled = learning.recall(label)
            if recalled:
                value, source = recalled, "bank"
        if not value and _FREE_TEXT.search(label) and not free_text:
            # Deliberately not guessed. A paragraph invented about why you
            # want to work somewhere is the one thing on a form that must
            # not be fabricated on your behalf.
            plan.unanswered.append(label or key)
            continue
        if not value:
            plan.unanswered.append(label or key)
            continue

        options = [o for o in (f.get("options") or []) if o and o.strip()]
        choices: list[str] = []
        if options and f.get("multiple"):
            # More than one answer, and each one has to be an option the
            # control will take. Read as a single answer, "English, French"
            # matched no option at all and the whole question was handed
            # back to the user as unanswerable.
            choices = [c for c in
                       (_closest_option(part, options)
                        for part in split_answers(value)) if c]
            # Dedupe, keeping the order the user gave them in.
            choices = list(dict.fromkeys(choices))
            if not choices:
                plan.unanswered.append(label or key)
                continue
            value = ", ".join(choices)
        elif options:
            # A choice field can only take one of its own options.
            chosen = _closest_option(value, options)
            if not chosen:
                plan.unanswered.append(label or key)
                continue
            value = chosen
        else:
            # A free box, where the input's own type decides what it will
            # accept — a number, an ISO date, or at most so many characters.
            value = coerce(value, ftype, int(f.get("maxlength") or 0))
            if not value:
                plan.unanswered.append(label or key)
                continue

        plan.fills.append(Filled(key=key, label=label, value=value,
                                 source=source, choices=choices,
                                 widget=str(f.get("widget") or "")))
    return plan


#: The way out every EEO question offers, in the words boards use for it.
_DECLINE = re.compile(
    r"prefer not|decline|(do not|don.t|dont) (wish|want)|not to (say|disclose|answer|specify)|"
    r"rather not|choose not|not specified|wish not|no answer", re.I)


def decline_option(options) -> str:
    """The "prefer not to say" choice among ``options``, or ""."""
    for o in options or []:
        if o and _DECLINE.search(o) and not _PLACEHOLDER.match(o):
            return o
    return ""


_YES = re.compile(r"^\s*(y|yes|true|1|authoris|authoriz)", re.I)
_NO = re.compile(r"^\s*(n|no|false|0|not? )", re.I)

#: The first row of nearly every dropdown, which is not an answer. Also
#: what an unanswered react-select shows, which is how a control that took
#: nothing is told from one that took something.
_PLACEHOLDER = re.compile(
    r"^\s*(-+\s*)?(please\s+)?(select|choose|pick)\b|^\s*-+\s*$|"
    r"^\s*\(?\s*(none|n/a)\s*\)?\s*$|^\s*start typing\b|^\s*search\b", re.I)


def is_placeholder(text: str) -> bool:
    """Whether what a control is showing means "nothing chosen yet"."""
    return bool(_PLACEHOLDER.match((text or "").strip()))


def _closest_option(value: str, options: list[str]) -> str:
    """The option a known answer corresponds to, or "" if none clearly does.

    A select is the easiest place to put a wrong answer on a form and the
    hardest place to notice it, so this only matches when it is sure: an
    exact option, a clean yes/no, or one unambiguous substring.
    """
    want = value.strip().lower()
    # "Please select" is the absence of an answer wearing an option's
    # clothing. Matching it looks like a filled field and submits a blank.
    opts = [o for o in options
            if o and o.strip() and not _PLACEHOLDER.match(o.strip())]
    for o in opts:
        if o.strip().lower() == want:
            return o

    if _YES.match(want) or _NO.match(want):
        yes = _YES.match(want) is not None
        for o in opts:
            ol = o.strip().lower()
            if yes and ol in {"yes", "y", "true"}:
                return o
            if not yes and ol in {"no", "n", "false"}:
                return o

    hits = [o for o in opts if want and want in o.strip().lower()]
    if len(hits) == 1:
        return hits[0]
    hits = [o for o in opts if o.strip().lower() and o.strip().lower() in want]
    if len(hits) == 1:
        return hits[0]
    return _band_for(value, opts)


#: Two numbers with something between them: "65,000 - 70,000 £",
#: "£30k–£40k", "20000 to 25000".
#: The currency symbol between the separator and the second number is
#: optional and easy to forget: "£30k - £40k" matched nothing without it.
_BAND = re.compile(
    r"([\d][\d,.]*)\s*(k)?\s*(?:-|–|—|to)\s*[^\d\s]{0,2}\s*([\d][\d,.]*)\s*(k)?",
    re.I)


def _band_for(value: str, options: list[str]) -> str:
    """The band an amount falls in, when the options are ranges.

    A salary expectation of £65,000 against "60,000 - 65,000 £" and
    "65,000 - 70,000 £" is a fact, not something to ask a model about —
    and asking produced "70,000 - 80,000 £" with low confidence.

    Bands are read as inclusive of their lower bound, which is how these
    lists are always meant: 65,000 picks "65,000 - 70,000".
    """
    amount = as_number(value)
    if not amount:
        return ""
    try:
        n = float(amount)
    except ValueError:
        return ""

    def num(text: str, kilo: str | None) -> float:
        got = float(text.replace(",", ""))
        return got * 1000 if kilo else got

    for option in options:
        m = _BAND.search(option)
        if not m:
            continue
        try:
            lo = num(m.group(1), m.group(2))
            hi = num(m.group(3), m.group(4))
        except ValueError:
            continue
        if lo <= n < hi or (n == hi and n == lo):
            return option
    return ""


#: Applies a plan in the page. Values are set the way a user would produce
#: them — focus, set, then fire the events frameworks listen for — because a
#: React or Vue form ignores a value assigned without an `input` event and
#: silently submits an empty field.
#:
#: A guessed value is marked where it was typed, not only in the app. The
#: person checking this form is looking at the form, and an amber outline on
#: the three fields that were guessed is the difference between "check these
#: four answers" and re-reading all twenty.
FILL_JS = r"""
(plan => {
  const out = {filled: [], missed: [], guessed: [], deferred: []};
  const fire = (el, names) => names.forEach(n =>
    el.dispatchEvent(new Event(n, {bubbles: true})));

  const find = key => document.getElementById(key)
    || document.querySelector(`[name="${CSS.escape(key)}"]`)
    || document.querySelector(`[data-qa="${CSS.escape(key)}"]`)
    || document.querySelector(`[data-sushi-key="${CSS.escape(key)}"]`);

  const same = (a, b) => String(a || '').trim().toLowerCase()
    === String(b || '').trim().toLowerCase();

  // A control a long way down the page is filled the same as any other,
  // but a form that validates on blur puts its error where nobody is
  // looking, and a control inside a virtualised list is not rendered until
  // it is near the viewport. Both are fixed by going there first.
  const reveal = el => {
    try { el.scrollIntoView({block: 'center', inline: 'nearest'}); }
    catch (e) { /* older engines take no options */ }
  };

  // Outlined rather than coloured in: a background colour loses against a
  // form's own styling, and on a tick-box or a radio there is no box to
  // colour. The outline is drawn on the control the user would click.
  const flag = (el, why) => {
    try {
      const t = (el.getAttribute('type') || '').toLowerCase();
      const mark = (t === 'radio' || t === 'checkbox')
        ? (el.closest('label') || el.parentElement || el) : el;
      mark.style.setProperty('outline', '2px solid #C58A00', 'important');
      mark.style.setProperty('outline-offset', '1px', 'important');
      mark.style.setProperty('border-radius', '4px', 'important');
      el.setAttribute('data-sushi-guess', '1');
      el.setAttribute('title', 'Sushi guessed this — check it before you submit.'
        + (why ? ' (' + why + ')' : ''));

      // The moment a real keystroke lands on the field it stops being a
      // guess: the amber comes off, and the watcher is free to bank what is
      // there as the user's own answer. `isTrusted` is the whole test — it
      // is false for every event this script and the page's own framework
      // dispatch, and true only for one made by a person.
      const clear = ev => {
        if (!ev.isTrusted) return;
        el.removeAttribute('data-sushi-guess');
        el.removeAttribute('title');
        mark.style.removeProperty('outline');
        mark.style.removeProperty('outline-offset');
        ['input', 'change', 'keydown'].forEach(n =>
          el.removeEventListener(n, clear, true));
      };
      ['input', 'change', 'keydown'].forEach(n =>
        el.addEventListener(n, clear, true));
    } catch (e) { /* styling a field must never lose the value in it */ }
  };

  for (const item of plan) {
    // A control that needs clicking through is not written to here; the
    // caller drives it one step at a time, because a list that appears on
    // click has not appeared yet in the same tick.
    if (item.widget === 'combobox' || item.widget === 'typeahead') {
      out.deferred.push(item.key);
      continue;
    }
    const el = find(item.key);
    if (!el) { out.missed.push(item.key); continue; }
    if (el.disabled || el.readOnly) { out.missed.push(item.key); continue; }
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    // Every answer this question takes. One, for all but a multiple select
    // and a group of tick-boxes.
    const values = (item.values && item.values.length)
      ? item.values : [item.value];
    // What a guess gets outlined on. For a radio group that is the option
    // actually chosen, not the first input the key happened to resolve to.
    let marked = el;
    reveal(el);

    try {
      if (tag === 'select') {
        const wanted = [...el.options].filter(o =>
          values.some(v => same(o.text, v) || o.value === v));
        if (!wanted.length) { out.missed.push(item.key); continue; }
        if (el.multiple) {
          for (const o of el.options) o.selected = wanted.includes(o);
        } else {
          el.value = wanted[0].value;
        }
        fire(el, ['input', 'change']);
      } else if (type === 'radio' || type === 'checkbox') {
        const group = [...document.querySelectorAll(
          `input[name="${CSS.escape(el.name || item.key)}"]`)];
        const labelOf = r => (r.closest('label')?.innerText
          || document.querySelector(`label[for="${CSS.escape(r.id)}"]`)?.innerText
          || r.value || '').trim();

        // A group of tick-boxes takes every answer, not the first one that
        // matched. Ticking one of three and reporting the question filled
        // is how "Monday, Wednesday" became "Monday".
        if (type === 'checkbox' && group.length > 1 && values.length > 1) {
          let ticked = 0;
          for (const r of group) {
            const want = values.some(v => same(labelOf(r), v) || r.value === v);
            if (!want) continue;
            reveal(r);
            if (!r.checked) {
              r.click();
              if (!r.checked) { r.checked = true; fire(r, ['input', 'change']); }
            }
            marked = r;
            ticked++;
          }
          if (!ticked) { out.missed.push(item.key); continue; }
          out.filled.push(item.key);
          if (item.guess) { flag(marked, item.basis || ''); out.guessed.push(item.key); }
          continue;
        }

        let hit = null;
        for (const r of group) {
          if (same(labelOf(r), item.value) || r.value === item.value) {
            hit = r;
            break;
          }
        }
        // A lone checkbox has no option to match: its value is "on" and its
        // label is the question, so "Yes" matched nothing and the box was
        // never ticked. For that one, the answer decides the state.
        let want = true;
        if (!hit && type === 'checkbox' && group.length === 1) {
          hit = group[0];
          want = /^\s*(y|yes|true|1|on|agree|accept|confirm)/i
            .test(String(item.value));
        }
        if (!hit) { out.missed.push(item.key); continue; }
        marked = hit;
        if (hit.checked !== want) {
          // .click() rather than setting .checked and dispatching one: the
          // native call runs the activation behaviour, which is what React
          // listens for on a tick-box. Dispatching a click *after* setting
          // .checked was worse than useless — activation toggles, so it
          // un-ticked the box this had just ticked.
          hit.click();
          if (hit.checked !== want) {       // click intercepted; do it by hand
            hit.checked = want;
            fire(hit, ['input', 'change']);
          }
        } else {
          fire(hit, ['input', 'change']);
        }
      } else {
        // The native setter, not el.value: React installs its own property
        // descriptor and tracks the last value it wrote, so a plain
        // assignment is reverted on the next render.
        const proto = tag === 'textarea'
          ? window.HTMLTextAreaElement.prototype
          : window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
        el.focus();
        setter.call(el, item.value);
        fire(el, ['input', 'change', 'blur']);
      }
      out.filled.push(item.key);
      if (item.guess) {
        flag(marked, item.basis || '');
        out.guessed.push(item.key);
      } else {
        // A moment of amber on every field as it is written, so a run
        // reads as something happening in a particular place rather than
        // as a form that changes all at once when you are not looking.
        try {
          marked.style.setProperty('outline', '2px solid #C58A00', 'important');
          marked.style.setProperty('outline-offset', '2px', 'important');
          const m = marked;
          setTimeout(() => {
            if (m.closest && m.closest('[data-sushi-guess="1"]')) return;
            m.style.removeProperty('outline');
            m.style.removeProperty('outline-offset');
          }, 2200);
        } catch (e) { }
      }
    } catch (e) {
      out.missed.push(item.key);
    }
  }
  return JSON.stringify(out);
})(PLAN_JSON)
"""


# --- the part that does need a model -----------------------------------------
#
# Everything above is a lookup. "Why do you want to work here" is not, and
# pretending otherwise would mean either leaving it blank forever or making
# something up — and an invented paragraph about your motivation is the one
# thing on a form that must not be fabricated for you.
#
# So it is generated, but as *one short call per question* rather than as a
# step in an agent loop. The difference is the whole argument: a browser
# agent spends about ten thousand tokens per step because it re-sends the
# page and its own system prompt every time, while this sends a few hundred
# and gets one paragraph back. That fits inside free tiers the agent cannot
# even start on.
#
# And it is written down afterwards. The second employer who asks the same
# question gets the banked answer, for nothing.

WRITE_SYSTEM = (
    "You answer job-application questions as the candidate, in their voice. "
    "British spelling. Plain, specific, no flattery and no filler. You never "
    "invent an employer, a qualification, a date, a tool or a number that is "
    "not in the material you are given."
)

WRITE_PROMPT = """Answer this application question as the candidate.

QUESTION
  {question}

THE JOB
  {title} at {company}
  {description}

THE CANDIDATE
  {about}
  Seniority: {seniority}, {years} of experience
  Skills: {skills}
  Experience: {experience}
  Education: {education}

WHAT THEY HAVE WRITTEN BEFORE, ABOUT THINGS LIKE THIS
{prior}

Rules:
- {length}
- Ground every claim in the candidate material above. If the job asks for
  something they do not have, do not claim it; write about what they do have.
- The answers above were written for *other* applications. Take the facts,
  the examples and the voice from them, and take only the parts that bear on
  the question asked here — a sentence or a single example is usually all
  that carries over. Never reuse one whole: a reason to want one job is not
  a reason to want this one, and a company name from a different application
  in this answer is the worst thing you can put in it.
- No greeting, no sign-off, no "I am excited to". Start with the substance.
- {tone}

Write only the answer."""


def _length_rule(field_type: str, label: str) -> str:
    if field_type == "textarea" or len(label) > 60:
        return "Three to five sentences."
    return "One or two sentences, under 60 words."


def write_answer(llm, question: str, role, profile, *,
                 field_type: str = "textarea", tone: str = "") -> str:
    """Compose one free-text answer. Returns "" rather than guessing badly.

    Built from the same dossier the batched guesses use. It used to have
    only the headline, the summary and the skills — so asked "what was your
    bachelor's degree result?" the model answered "I do not hold a
    bachelor's degree", about a candidate whose CV names one.
    """
    prompt = WRITE_PROMPT.format(
        question=question.strip(),
        # What the bank holds *about this question*, not the first twelve
        # rows of it. See ``learning.material_block``.
        prior=learning.material_block(question),
        length=_length_rule(field_type, question),
        tone=tone or "Keep it matter-of-fact.",
        **_dossier(role, profile),
    )
    try:
        text = llm.complete(prompt, system=WRITE_SYSTEM, max_tokens=3000)
    except Exception as exc:                        # noqa: BLE001
        log.info("free text: could not answer %r (%s)", question[:60], exc)
        return ""
    text = (text or "").strip().strip('"')
    # A model that returns a refusal, a preamble or nothing is not an answer.
    if len(text) < 25 or text.lower().startswith(("i cannot", "i'm sorry", "as an ai")):
        return ""
    return text


def _pending(plan: Plan, fields: list[dict]) -> list[tuple[str, dict]]:
    """Each outstanding question paired with the field it belongs to.

    ``plan.unanswered`` holds the *tidied* label, which is what gets shown
    back to the user — so a map built from the raw ``label`` misses every
    field whose label needed tidying, which on Greenhouse is most of them.
    An entry whose field cannot be found at all is paired with ``{}`` and
    dropped by the callers, because there is nothing to write it into.
    """
    by_label: dict[str, dict] = {}
    for f in fields:
        for name in (tidy_label(f.get("label") or ""), (f.get("label") or "").strip(),
                     f.get("key") or ""):
            if name and name not in by_label:
                by_label[name] = f
    return [(q, by_label.get(q, {})) for q in plan.unanswered]


def _is_prose(label: str, ftype: str, f: dict | None = None) -> bool:
    """Whether a question wants sentences rather than a value.

    A choice control never does, however long its question. Canonical asks
    "We require all colleagues to meet in person 2-4 times a year…" through
    a two-option dropdown, and the length of the question had three
    paragraphs composed for a control that only accepts Yes or No.
    """
    if f is not None and (f.get("widget") or f.get("options")
                          or f.get("searchable")):
        return False
    return ftype == "textarea" or bool(_FREE_TEXT.search(label)) or len(label) > 60


def answer_free_text(llm, plan: Plan, fields: list[dict], role, profile, *,
                     tone: str = "") -> int:
    """Fill in the blanks the lookup could not, and bank what is written.

    Returns how many were answered. Each one is remembered as an *agent*
    answer, so it is reused next time but a human correction still overrides
    it — which is exactly the precedence the watcher already enforces.

    Only prose. A date, a dropdown or a number the lookup could not find is
    not something to compose; that is Explorative mode's job, and it is
    flagged rather than banked.
    """
    if llm is None or not plan.unanswered:
        return 0
    answered = 0
    still: list[str] = []

    for label, f in _pending(plan, fields):
        key = f.get("key", "")
        ftype = (f.get("type") or "").lower()
        if not key or f.get("options") or ftype not in {"textarea", "text"}:
            still.append(label)
            continue
        if not _is_prose(label, ftype, f):
            still.append(label)
            continue

        text = write_answer(llm, label, role, profile, field_type=ftype, tone=tone)
        if not text:
            still.append(label)
            continue
        plan.fills.append(Filled(key=key, label=label, value=text,
                                 source="written", confidence="medium",
                                 basis="written from your CV",
                                 widget=str(f.get("widget") or "")))
        learning.remember(label, text, source="agent")
        answered += 1

    plan.unanswered = still
    return answered


# --- explorative mode: finish the form, and say which parts are guesses ------
#
# The default is deliberately cautious: anything not on the CV and not in the
# bank is left blank and handed back as "3 questions need you". That is right
# when you are sitting there. It is useless when you are not — a form that is
# 80% filled is still a form nobody has submitted.
#
# So Explorative answers the rest with the most likely answer for *this*
# person, and then makes very sure you can tell which answers those were:
#
# - the field is outlined amber in the page, with the reason on hover;
# - the app lists every guess back with the answer in an editable box;
# - nothing guessed is written to the answer bank. A guess that silently
#   became a stored answer would propagate to every later form as though you
#   had typed it, which is the one way this feature could do real harm.
#
# The honesty floor from the agent path applies unchanged: a guess may be
# wrong, but it may never be a fabricated employer, qualification, date or
# number. "Most likely value for someone with this history" is a guess;
# "worked at Google" is a lie.

GUESS_SYSTEM = (
    "You complete job-application forms for a candidate, from their CV and "
    "the answers they have given before. British spelling. Where the material "
    "settles a question, answer from it. Where it does not, give the most "
    "likely answer for a real person with this history and mark it as a "
    "guess. You never invent an employer, a job title, a qualification, a "
    "date or a number that contradicts the material you were given, and you "
    "never answer a question about a protected characteristic. For salary, "
    "give a realistic figure for this seniority in the candidate's country "
    "(or the advertised range). For right to work and sponsorship, answer "
    "from the candidate's country (location, phone code) against the job's "
    "country: same country means authorised and no sponsorship."
)

GUESS_PROMPT = """Answer as many of these application questions as you
reasonably can, for this candidate.

THE JOB
  {title} at {company}
  {description}

THE CANDIDATE
  {about}
  Location: {location}
  Seniority: {seniority}, {years} of experience
  Skills: {skills}
  Experience: {experience}
  Education: {education}

WHAT THEY HAVE ANSWERED BEFORE
{prior}

QUESTIONS
{questions}

For each question give:
  value       - the answer, exactly as it should be typed. For a question
                that lists OPTIONS it must be one of those options, copied
                character for character.
  confidence  - "high" if the material above settles it, "medium" if it is
                the obvious answer for someone with this history, "low" if
                you are genuinely unsure.
  basis       - six words or fewer on where the answer came from, e.g.
                "from CV: 6 years at Kestrel" or "typical for this seniority".

Leave a question out entirely rather than answering it badly. Leave out
anything you would have to invent a fact to answer.

But a question that lists OPTIONS is asking you to choose, and one of them
is almost always right for this candidate, so answer those, marking the
confidence honestly, rather than leaving the field blank. The only ones to
leave out are where every option would be a fabrication: how they heard
about this company, who referred them, a reference's name.

Never choose an option that claims something the material above does not
show. A security clearance, polygraph, licence, certification, citizenship,
degree or past employer the candidate is not shown to have means the honest
option: "No", "None", "N/A" or "Not applicable". A follow-up such as "If yes,
which level…" whose earlier question is answered No gets "None" or "N/A" if
offered, and is left out otherwise.

Reply as JSON: {{"answers": [{{"n": 1, "value": "...",
"confidence": "medium", "basis": "..."}}]}}"""

#: A guess this unsure is still offered — flagged low, outlined like the
#: rest — because the alternative is an empty field, and an empty field is
#: not more honest, only less finished.
_CONFIDENCE = ("high", "medium", "low")


def _dossier(role, profile) -> dict[str, str]:
    """The candidate and the job, in the shape both prompts want."""
    about = " ".join(filter(None, [
        profile.headline or profile.current_title,
        (profile.summary or "")[:700],
    ])).strip()
    jobs = "; ".join(
        f"{e.title} at {e.company} ({e.start}–{e.end or 'now'})"
        for e in (profile.experience or [])[:6]) or "(none listed)"
    return {
        "title": getattr(role, "title", ""),
        "company": getattr(role, "company", ""),
        "description": (getattr(role, "description", "") or "")[:1500],
        "about": about or "(no profile summary)",
        "location": ", ".join(filter(None, [profile.location, profile.country]))
                    or "(not given)",
        "skills": ", ".join((profile.skills or [])[:18]) or "(none listed)",
        "experience": jobs,
        # Left out until now, which made "School", "Degree" and
        # "Discipline" — three required fields on every Greenhouse form —
        # unanswerable even with a model: it was asked which university
        # this person attended and given nothing that said.
        "education": "; ".join((profile.education or [])[:6]) or "(none listed)",
        "seniority": profile.seniority or "(not given)",
        "years": profile.years_experience or "(not given)",
    }


def _question_block(items: list[tuple[str, dict]]) -> str:
    """The numbered questions, with their type and whatever options exist."""
    lines = []
    for i, (label, f) in enumerate(items, 1):
        ftype = (f.get("type") or "text").lower()
        line = f"{i}. [{ftype}] {label}"
        options = [o for o in (f.get("options") or []) if o and o.strip()]
        if options:
            line += "\n     OPTIONS: " + " | ".join(options[:25])
        elif f.get("searchable"):
            # A search box with a catalogue behind it — a university list,
            # a country list. There is no menu to choose from, so the
            # answer is the real thing, spelled as the catalogue spells it.
            sample = [o for o in (f.get("sample_options") or []) if o]
            line += ("\n     SEARCHABLE LIST: give the real full name, "
                     "spelled as an official list would spell it")
            if sample:
                line += f" (entries look like: {', '.join(sample[:3])})"
        lines.append(line)
    return "\n".join(lines)


#: The longest a *short* field's guessed answer may be. Generous — a city
#: and country, a salary with a currency and a note, a long job title all
#: fit well inside it — so anything over is not a long answer, it is the
#: wrong kind of answer.
_SHORT_ANSWER_MAX = 120


def guess_short(llm, items: list[tuple[str, dict]], role, profile) -> dict[int, dict]:
    """One call for every short question left. Returns index → answer dict.

    Batched on purpose. A call per field is the obvious way to write this and
    costs twenty round trips on a Workday form — and the model answers each
    one without seeing the others, so "years of experience" and "years in
    this industry" come back inconsistent.
    """
    if not items:
        return {}
    prompt = GUESS_PROMPT.format(
        questions=_question_block(items),
        prior=learning.prompt_block(16) or "  (nothing yet)",
        **_dossier(role, profile))
    try:
        try:
            data = llm.complete_json(prompt, system=GUESS_SYSTEM, max_tokens=4000,
                                     fast=True)
        except Exception:                               # noqa: BLE001
            # The quick model may not be available on this plan or key;
            # the configured one always is.
            data = llm.complete_json(prompt, system=GUESS_SYSTEM, max_tokens=4000)
    except Exception as exc:                            # noqa: BLE001
        log.info("explorative: no answers back (%s)", exc)
        return {}

    rows = data.get("answers") if isinstance(data, dict) else data
    out: dict[int, dict] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            n = int(row.get("n", 0))
        except (TypeError, ValueError):
            continue
        value = str(row.get("value", "") or "").strip()
        if not (1 <= n <= len(items)) or not value:
            continue
        confidence = str(row.get("confidence", "") or "").strip().lower()
        out[n] = {
            "value": value,
            "confidence": confidence if confidence in _CONFIDENCE else "low",
            "basis": str(row.get("basis", "") or "").strip()[:80],
        }
    return out


def guess_answers(llm, plan: Plan, fields: list[dict], role, profile, *,
                  tone: str = "", include_demographics: bool = False) -> int:
    """Explorative mode: answer everything still blank, and flag it.

    Returns how many were guessed. The plan's ``unanswered`` list shrinks to
    whatever even a guess could not reach — a file upload, a password, a
    demographic question with the switch off — and ``plan.guesses`` holds
    what was composed, for the page to list back.
    """
    if llm is None or not plan.unanswered:
        return 0

    prose: list[tuple[str, dict]] = []
    short: list[tuple[str, dict]] = []
    still: list[str] = []

    for label, f in _pending(plan, fields):
        key = f.get("key", "")
        ftype = (f.get("type") or "").lower()
        # The refusals are refusals here too. Explorative widens what Sushi
        # will attempt, not what it will touch: a password is never typed and
        # a demographic question is never answered on someone's behalf,
        # however confident a model would be about it.
        if not key or ftype in {"file", "password"} or _NEVER.search(label):
            still.append(label)
            continue
        if ((learning.is_demographic(label, key)
                or learning.options_are_demographic(f.get("options")))
                and not include_demographics):
            plan.skipped.append(label)       # not asked: see plan_fill
            continue
        (prose if _is_prose(label, ftype, f)
         else short).append((label, f))

    guessed = 0

    # The short ones: one batched call for all of them.
    answers = guess_short(llm, short, role, profile)
    for i, (label, f) in enumerate(short, 1):
        got = answers.get(i)
        if not got:
            still.append(label)
            continue
        value = got["value"]
        options = [o for o in (f.get("options") or []) if o and o.strip()]
        if not options and len(value) > _SHORT_ANSWER_MAX:
            # These are the fields ``_is_prose`` said were *not* prose: a
            # name, a number, a country, a yes or no. An answer this long is
            # the model explaining itself into the box rather than answering
            # — "No referral — applying without one, so this should be left
            # blank or marked ..." went into a referral field verbatim, in
            # amber, for the user to find and delete. Reasoning has a home
            # already: it is `basis`, and it belongs beside the answer
            # rather than inside it.
            log.info("explorative: %r came back %d chars long; left for you",
                     label[:60], len(value))
            still.append(label)
            continue
        if options:
            # Same rule as a looked-up answer: a choice field takes one of its
            # own options or nothing. A model asked to copy an option
            # character for character still returns "Yes, I am" for "Yes".
            value = _closest_option(value, options)
            if not value:
                still.append(label)
                continue
        # The widget comes with it. Without this a guessed answer for a
        # react-select was written straight to the input's value — which
        # that widget ignores and clears — so sixteen answers were composed,
        # counted as filled, and eleven of them were never in the form.
        plan.fills.append(Filled(key=f.get("key", ""), label=label, value=value,
                                 source="guess", confidence=got["confidence"],
                                 basis=got["basis"],
                                 widget=str(f.get("widget") or "")))
        guessed += 1

    # The prose, one call each — a paragraph is not something to batch.
    for label, f in prose:
        text = write_answer(llm, label, role, profile,
                            field_type=(f.get("type") or "").lower(), tone=tone)
        if not text:
            still.append(label)
            continue
        plan.fills.append(Filled(key=f.get("key", ""), label=label, value=text,
                                 source="guess", confidence="medium",
                                 basis="written from your CV",
                                 widget=str(f.get("widget") or "")))
        guessed += 1

    plan.unanswered = still
    return guessed


_PLACE_Q = re.compile(
    r"\b(based|located|live|living|reside|residing|currently in)\b", re.I)
_SOURCE_Q = re.compile(
    r"(hear|heard|learn|find|found|come across)\b.*\b(about|of)\b.*"
    r"\b(role|job|position|opening|opportunity|us|company)\b"
    r"|\bhow did you (hear|find|learn)\b|\breferral source\b"
    r"|\b(channel|source)\b.{0,40}\b(found|find|hear|heard|learn)"
    r"|\b(found|find|hear|heard|learn)\w*\b.{0,40}\b(channel|source)\b", re.I)
#: "work from our Pune office", "commute to our London office", "in our
#: Berlin office 3 days a week".
_OFFICE_Q = re.compile(
    r"\b(?:our|the)\s+(?P<place>[A-Z][\w\-]+(?:\s+[A-Z][\w\-]+)?)\s+"
    r"(?:office|hub|site|campus|studio)\b")

#: Truthful answers to "where did you hear about us", best first. Sushi reads
#: the company's own careers board, so that is what actually happened.
_SOURCE_PICKS = ("company website", "careers page", "career site", "careers site",
                 "company careers", "website", "job board", "online job",
                 "internet", "other")
_NONE_PICKS = ("none of the above", "none of these", "other", "no")


#: "Have you ever worked for Thoughtworks before?", "Have you previously been
#: employed by Acme?" — the company is captured so the CV can be checked for it.
_WORKED_HERE_Q = re.compile(
    r"(?=.*\b(?:ever|before|previously|formerly|in the past)\b)"
    r".*?\b(?:work(?:ed)?|been employed|employed)\s+(?:for|at|by|with)\s+"
    r"(?P<co>[A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})", re.S)


def _cv_names(profile: Profile, company: str | None) -> bool:
    company = (company or "").strip().lower()
    if not company:
        return False
    seen = [profile.current_company or "", profile.raw_cv_text or ""]
    seen += [getattr(e, "company", "") or "" for e in (profile.experience or [])]
    return any(company in t.lower() for t in seen if t)


def answer_from_profile(plan: Plan, fields: list[dict], profile: Profile) -> int:
    """Answer the choice questions whose answer follows from known facts.

    Without Explorative mode the model only writes free text, so a required
    dropdown like "Are you based in any of these countries?" or "Where did
    you hear about this role?" was always left blank, though the profile
    settles it. Only those are answered here, as ``derived``: anything about
    visas, work authorisation or demographics stays the user's.
    """
    if not plan.unanswered:
        return 0
    from .discovery import countries_in

    home = countries_in(" ".join(filter(None, [
        profile.country or "", profile.location or ""])))
    still: list[str] = []
    done = 0
    for label, f in _pending(plan, fields):
        # Without the "Select…" row: with it, a plain Yes/No dropdown was
        # not recognised as one and the question was left unanswered.
        options = [o for o in (f.get("options") or [])
                   if o and o.strip() and not _PLACEHOLDER.match(o)]
        key = f.get("key", "")
        value = ""
        if key and options and not _NEVER.search(label)                 and not learning.is_demographic(label, key):
            low = [o.lower().strip() for o in options]
            before = _WORKED_HERE_Q.search(label)
            if before and set(low) <= {"yes", "no"}:
                # A fact the CV settles, so never left to a guess: the
                # model answered "Yes" for a company the CV never names.
                value = next(o for o, l in zip(options, low) if l == (
                    "yes" if _cv_names(profile, before.group("co")) else "no"))
            elif (_OFFICE_Q.search(label) and set(low) <= {"yes", "no"}
                    and profile.location):
                # "Are you willing to work from our Pune office 3 days per
                # week?" was answered Yes for someone in Faisalabad. If the
                # office's place is not where they live, the honest answer
                # is No; if it is, Yes.
                place = _OFFICE_Q.search(label).group("place").lower()
                here = (profile.location + " " + (profile.country or "")).lower()
                value = next(o for o, l in zip(options, low)
                             if l == ("yes" if place in here else "no"))
            elif _SOURCE_Q.search(label):
                for pick in _SOURCE_PICKS:
                    hit = next((o for o, l in zip(options, low) if pick in l), "")
                    if hit:
                        value = hit
                        break
            elif _PLACE_Q.search(label) and home and not re.search(
                    r"visa|sponsor|authori[sz]|eligib|permit|relocat", label, re.I):
                yes_no = set(low) <= {"yes", "no"}
                if yes_no:
                    # "Do you live in one of the following states? Alabama…"
                    named = countries_in(label)
                    if named:
                        value = next(o for o, l in zip(options, low)
                                     if l == ("yes" if named & home else "no"))
                    elif profile.country and profile.country.lower() not in (
                            "united states", "usa", "us") and re.search(
                            r"\bstates?\b", label, re.I):
                        # US states listed by name; someone outside the US
                        # does not live in any of them.
                        value = next(o for o, l in zip(options, low) if l == "no")
                else:
                    value = next((o for o in options if countries_in(o) & home), "")
                    if not value:
                        for pick in _NONE_PICKS:
                            value = next((o for o, l in zip(options, low)
                                          if l == pick or l.startswith(pick)), "")
                            if value:
                                break
        elif key and not options and _SOURCE_Q.search(label)                 and str(f.get("widget") or "") in ("", "text", "input", "textarea"):
            # Ashby asks this as a plain text box rather than a dropdown.
            value = "Company website"
        if value:
            plan.fills.append(Filled(key=key, label=label, value=value,
                                     source="derived",
                                     widget=str(f.get("widget") or "")))
            done += 1
        else:
            still.append(label)
    plan.unanswered = still
    return done


def fill_script(plan: Plan) -> str:
    """The script that applies this plan, with the plan baked in."""
    import json

    payload = json.dumps([
        {"key": f.key, "value": f.value, "values": f.choices,
         "widget": f.widget, "guess": f.guessed, "basis": f.basis}
        for f in plan.fills])
    return FILL_JS.replace("PLAN_JSON", payload)


# --- the controls that have to be clicked through ----------------------------
#
# A react-select dropdown and a typeahead cannot be written to. The list of
# options does not exist in the page until the control has been opened or
# typed into, so there is nothing to click in the same tick that opens it —
# which is why these are three scripts with a wait between them rather than
# one, and why they are driven from ``autofill`` where there is an event
# loop to wait on.

# react-select — which is what Greenhouse's current boards are built from,
# and Ashby, and most of the rest — renders its option list only while the
# menu is open, and only sets ``aria-controls`` at the same moment. So a
# control that is shut has no options, no listbox and nothing linking it to
# one. Reading the page found twenty of these on one Canonical form and
# zero options on every one of them, which made every question behind one
# unanswerable: the lookup had nothing to match against and the model had
# nothing to choose from.
#
# So they are opened, read and shut again before anything is planned.

#: Where a control is on screen, having first scrolled it into view.
#:
#: These widgets are worked with real mouse and keyboard input through CDP
#: rather than with synthesised DOM events, which is why a position is
#: needed at all. Synthesised events got Sushi a long way and then stopped:
#: a dispatched Escape does not close a react-select, a dispatched
#: mousedown on the body does not close an intl-tel-input, and a form was
#: left with five menus hanging open over each other and nothing chosen.
#: A real click is what every one of these widgets is built to expect.
RECT_JS = r"""
((key) => {
  const find = k => document.getElementById(k)
    || document.querySelector(`[name="${CSS.escape(k)}"]`)
    || document.querySelector(`[data-sushi-key="${CSS.escape(k)}"]`);
  const el = find(key);
  if (!el) return '';
  // 'instant', explicitly. Greenhouse's boards set `scroll-behavior:
  // smooth`, so the default animates — and a position measured in the
  // same tick is the one from *before* the scroll, which is off-screen.
  // Every control on the page then measured as out of view and nothing
  // could be clicked at all.
  try { el.scrollIntoView({block: 'center', inline: 'nearest',
                           behavior: 'instant'}); } catch (e) {
    try { el.scrollIntoView(true); } catch (e2) { }
  }
  // The control, not the hidden input inside it. react-select's input is
  // one pixel wide; clicking that misses the thing that opens the menu.
  let target = el;
  const r0 = el.getBoundingClientRect();
  if (r0.width < 12 || r0.height < 8) {
    const ctrl = el.closest('[class*="control"], [class*="container"], label');
    if (ctrl) target = ctrl;
  }
  const r = target.getBoundingClientRect();
  if (!r.width || !r.height) return '';
  if (r.bottom < 0 || r.top > window.innerHeight) return '';
  return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2});
})(KEY_JSON)
"""

#: Where the option to click is, having scrolled it into view inside its
#: own list. Returns "" when the list has not appeared or holds no match.
OPTION_RECT_JS = r"""
((key, text) => {
  const find = k => document.getElementById(k)
    || document.querySelector(`[name="${CSS.escape(k)}"]`)
    || document.querySelector(`[data-sushi-key="${CSS.escape(k)}"]`);
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = n => !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length);
  const el = find(key);
  if (!el) return '';

  // This control's own list first. With several menus open at once —
  // which is what happens when one of them refuses to close — picking
  // "the only visible listbox" picks somebody else's options.
  let boxes = [];
  const ref = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
  if (ref) {
    for (const id of ref.split(/\s+/)) {
      const n = document.getElementById(id);
      if (n && vis(n)) boxes.push(n);
    }
  }
  if (!boxes.length) {
    let n = el.nextElementSibling;
    for (let i = 0; n && i < 2; i++, n = n.nextElementSibling) {
      if (n.getAttribute && n.getAttribute('role') === 'listbox' && vis(n)) boxes.push(n);
    }
  }
  if (!boxes.length) {
    boxes = [...document.querySelectorAll('[role="listbox"]')].filter(vis)
      .filter(b => { const o = b.id && document.querySelector(`[aria-controls~="${CSS.escape(b.id)}"],[aria-owns~="${CSS.escape(b.id)}"]`); return (!o || o === el) && (!/^iti-/.test(b.id || '') || !!el.closest('.iti')); });
    if (boxes.length > 1) return '';     // ambiguous: refuse rather than guess
  }
  if (!boxes.length) return '';

  const want = clean(text).toLowerCase();
  let options = [];
  for (const box of boxes) {
    options = options.concat([...box.querySelectorAll('[role="option"]')].filter(vis));
  }
  if (!options.length) return '';
  const textOf = o => clean(o.innerText || o.textContent).toLowerCase();
  const shortest = list => list.sort(
    (a, b) => textOf(a).length - textOf(b).length)[0];

  let hit = options.find(o => textOf(o) === want);
  if (!hit) {
    // The shortest containing match. "United Kingdom" must not land on
    // "United Kingdom of Great Britain and Northern Ireland" when the
    // plain one is right there.
    hit = shortest(options.filter(
      o => textOf(o).includes(want) || want.includes(textOf(o))));
  }
  if (!hit) {
    // Every word of the answer, in any order, with the option free to add
    // its own. A location search asked for "Bristol, United Kingdom"
    // answers "Bristol, England, United Kingdom": neither string contains
    // the other, and the right option was being refused.
    const words = want.split(/[^a-z0-9]+/i).filter(w => w.length > 1);
    if (words.length) {
      hit = shortest(options.filter(o => {
        const t = textOf(o);
        return words.every(w => t.includes(w));
      }));
    }
    // And failing that, the other way round: the option's words all
    // appear in the answer, which covers an option worded more tersely
    // than the answer was.
    if (!hit && words.length > 1) {
      hit = shortest(options.filter(o => {
        const parts = textOf(o).split(/[^a-z0-9]+/i).filter(w => w.length > 1);
        return parts.length && parts.every(w => want.includes(w));
      }));
    }
  }
  if (!hit && options.length === 1) hit = options[0];
  if (!hit) return '';
  try { hit.scrollIntoView({block: 'nearest', inline: 'nearest',
                            behavior: 'instant'}); } catch (e) { }
  const r = hit.getBoundingClientRect();
  if (!r.width || !r.height) return '';
  if (r.bottom < 0 || r.top > window.innerHeight) return '';
  return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2,
                         text: clean(hit.innerText || hit.textContent)});
})(KEY_JSON, TEXT_JSON)
"""

#: How many choice menus are open anywhere on the page. Used to notice a
#: menu that refused to close before it covers the next field.
OPEN_MENUS_JS = r"""
(() => {
  const vis = n => !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length);
  const boxes = [...document.querySelectorAll('[role="listbox"]')].filter(vis);
  const expanded = [...document.querySelectorAll('[aria-expanded="true"]')]
    .filter(vis);
  return String(Math.max(boxes.length, expanded.length));
})()
"""


#: Put a mark on a field, or take one off.
#:
#: Two marks, and they say different things. "working" is amber and
#: temporary: this is the field Sushi is on right now, which is the only
#: way to follow a run that spends two seconds per dropdown. "guess" is
#: amber and permanent: this answer is Sushi's own work and wants checking
#: before you submit.
#:
#: The permanent one existed already but only ever reached fields written
#: by the fill script — so a dropdown, which is clicked rather than
#: written, never got one, and with Explorative off nothing was marked at
#: all and a run looked like nothing happening.
#: The pulse. A static ring is a border; this is what "Sushi is on this
#: field right now" looks like from across the room, and it is the whole
#: difference between a run that looks like it is doing something and one
#: that looks frozen while a dropdown takes four seconds to answer.
#:
#: Set as an ``animation`` and *not* as an inline ``box-shadow``: an inline
#: ``!important`` shadow beats a keyframed one, so writing both would mean
#: the ring never moved. The outline underneath it stays important, so even
#: on a page that fights the animation the field is still marked.
MARK_STYLE_ID = "sushi-mark-style"

MARK_JS = r"""
((keys, state) => {
  const STYLE_ID = 'sushi-mark-style';
  const ensureStyle = () => {
    try {
      if (document.getElementById(STYLE_ID)) return;
      const s = document.createElement('style');
      s.id = STYLE_ID;
      s.textContent =
        '@keyframes sushi-working{' +
        '0%,100%{box-shadow:0 0 0 3px rgba(197,138,0,.16)}' +
        '50%{box-shadow:0 0 0 9px rgba(197,138,0,.32)}}';
      (document.head || document.documentElement).appendChild(s);
    } catch (e) { /* a page without a head is not worth failing over */ }
  };

  const find = k => document.getElementById(k)
    || document.querySelector(`[name="${CSS.escape(k)}"]`)
    || document.querySelector(`[data-sushi-key="${CSS.escape(k)}"]`);

  // The control the user would click. A tick box or a radio is a few
  // pixels of box with the label beside it, so the ring goes round the
  // pair or it marks something nobody can see.
  const target = el => {
    const t = (el.getAttribute('type') || '').toLowerCase();
    const r = el.getBoundingClientRect();
    if (t === 'radio' || t === 'checkbox' || r.width < 12 || r.height < 8) {
      return el.closest('label, [class*="control"], [class*="container"]')
        || el.parentElement || el;
    }
    return el;
  };

  if (state === 'working') ensureStyle();

  let hit = 0;
  for (const key of keys) {
    const el = find(key);
    if (!el) continue;
    const mark = target(el);
    hit++;
    if (state === 'clear') {
      // A guess keeps its ring: it is the one answer on the form that
      // still wants a human eye, and finishing the field is not the same
      // as having checked it.
      if (el.getAttribute('data-sushi-guess') === '1') {
        mark.style.removeProperty('animation');
        mark.style.setProperty('box-shadow', '0 0 0 4px rgba(197,138,0,.16)',
                               'important');
        el.removeAttribute('data-sushi-working');
        continue;
      }
      mark.style.removeProperty('outline');
      mark.style.removeProperty('outline-offset');
      mark.style.removeProperty('box-shadow');
      mark.style.removeProperty('animation');
      el.removeAttribute('data-sushi-working');
      continue;
    }
    mark.style.setProperty('outline', '2px solid #C58A00', 'important');
    mark.style.setProperty('outline-offset', '2px', 'important');
    if (state === 'working') {
      mark.style.removeProperty('box-shadow');
      mark.style.setProperty('animation',
                             'sushi-working 1.3s ease-in-out infinite',
                             'important');
      el.setAttribute('data-sushi-working', '1');
    } else {
      mark.style.removeProperty('animation');
      mark.style.setProperty('box-shadow', '0 0 0 4px rgba(197,138,0,.16)',
                             'important');
      el.setAttribute('data-sushi-guess', '1');
      el.removeAttribute('data-sushi-working');
    }
  }
  return hit ? 'marked:' + hit : 'missing';
})(KEYS_JSON, STATE_JSON)
"""


def mark_script(key: str, state: str) -> str:
    """``state`` is "working", "guess" or "clear"."""
    return marks_script([key], state)


def marks_script(keys: list[str], state: str) -> str:
    """The same, for a whole set of fields in one round trip.

    One evaluate rather than one per field. The write pass lights every
    field it is about to touch, and on a Greenhouse application that is
    thirty of them — thirty CDP round trips before a single character is
    typed would be slower than the fill itself.
    """
    import json

    return (MARK_JS.replace("KEYS_JSON", json.dumps(list(keys)))
                   .replace("STATE_JSON", json.dumps(state)))


#: Where the "go to the next page" control is, on a form that has pages.
#:
#: Taleo, Workday and iCIMS all split an application across steps — contact
#: details, then experience, then questions, then review — and Sushi filled
#: the first one and reported itself finished. Three quarters of those
#: forms were never seen.
#:
#: The words are a short allow-list and a longer deny-list, in that order,
#: because the cost of the two mistakes is not remotely the same. Missing a
#: "Next" leaves a half-filled form on screen for the user to continue.
#: Pressing a "Submit" sends an application they have not read.
NEXT_JS = r"""
(() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  // Anything that sends the application. Checked first, and a control that
  // matches is never pressed, whatever else its label says.
  const SEND = /\b(submit|apply|send|finish|complete|confirm and|i agree and)\b/i;
  const NEXT = /^(next|continue|save and continue|save & continue|next step|next:|proceed|go to next|save and next)\b/i;
  const vis = el => {
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 10 && r.height > 8;
  };
  const nodes = [...document.querySelectorAll(
    'button, [role="button"], input[type="button"], input[type="submit"], a')];
  for (const el of nodes) {
    const text = clean(el.innerText || el.value || el.getAttribute('aria-label'));
    if (!text || text.length > 40) continue;
    if (SEND.test(text)) continue;
    if (!NEXT.test(text)) continue;
    if (!vis(el) || el.disabled) continue;
    try { el.scrollIntoView({block: 'center', behavior: 'instant'}); } catch (e) { }
    const r = el.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight) continue;
    return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2,
                           text: text});
  }
  return '';
})()
"""

#: What page we are on, for telling "it advanced" from "it refused".
#: The URL alone is not enough — a single-page app changes its fields
#: without changing its address.
STEP_ID_JS = r"""
(() => {
  const keys = [];
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const k = el.id || el.getAttribute('name') || '';
    const t = (el.getAttribute('type') || '').toLowerCase();
    if (k && t !== 'hidden') keys.push(k);
  }
  keys.sort();
  return location.pathname + '|' + keys.join(',');
})()
"""


def rect_script(key: str) -> str:
    import json

    return RECT_JS.replace("KEY_JSON", json.dumps(key))


def option_rect_script(key: str, text: str) -> str:
    import json

    return (OPTION_RECT_JS.replace("KEY_JSON", json.dumps(key))
                          .replace("TEXT_JSON", json.dumps(text)))


#: Open a closed choice control. react-select commits to a *mousedown* on
#: its control div rather than a click on the input, and opens on ArrowDown
#: as well — all three are sent, because which one works varies by widget.
REVEAL_JS = r"""
((key) => {
  const find = k => document.getElementById(k)
    || document.querySelector(`[name="${CSS.escape(k)}"]`)
    || document.querySelector(`[data-sushi-key="${CSS.escape(k)}"]`);
  const el = find(key);
  if (!el) return 'missing';
  try { el.scrollIntoView({block: 'center'}); } catch (e) { }
  try { el.focus(); } catch (e) { }
  const ctrl = el.closest('[class*="control"]') || el.parentElement || el;
  for (const n of [ctrl, el]) {
    if (!n) continue;
    n.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, button: 0}));
    n.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, button: 0}));
    n.dispatchEvent(new MouseEvent('click', {bubbles: true, button: 0}));
  }
  el.dispatchEvent(new KeyboardEvent('keydown',
    {bubbles: true, key: 'ArrowDown', code: 'ArrowDown', keyCode: 40}));
  return 'opened';
})(KEY_JSON)
"""

#: Read the options of a control that is now open, then shut it again.
#:
#: A list of more than ``SEARCHABLE_FROM`` options is reported as
#: *searchable* rather than returned in full: the school list on a
#: Greenhouse form is every university in the world, and the first sixty
#: alphabetically are no use to anybody. Those are answered by typing the
#: answer and clicking the match, which is what the typeahead path does.
OPTIONS_JS = r"""
((key, cap) => {
  const find = k => document.getElementById(k)
    || document.querySelector(`[name="${CSS.escape(k)}"]`)
    || document.querySelector(`[data-sushi-key="${CSS.escape(k)}"]`);
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = n => !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length);
  const el = find(key);
  if (!el) return JSON.stringify({options: [], total: 0});

  let box = null;
  const ref = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
  if (ref) box = document.getElementById(ref.split(/\s+/)[0]);
  if (!box) {
    const open = [...document.querySelectorAll('[role="listbox"]')].filter(vis)
      .filter(b => { const o = b.id && document.querySelector(`[aria-controls~="${CSS.escape(b.id)}"],[aria-owns~="${CSS.escape(b.id)}"]`); return (!o || o === el) && (!/^iti-/.test(b.id || '') || !!el.closest('.iti')); });
    if (open.length === 1) box = open[0];
  }
  let options = [];
  if (box) {
    options = [...box.querySelectorAll('[role="option"]')]
      .map(o => clean(o.innerText || o.textContent)).filter(Boolean);
  }
  const total = options.length;

  // Shut it again. A form left with four menus hanging open is one the
  // user cannot read, and an open menu covers the next field.
  el.dispatchEvent(new KeyboardEvent('keydown',
    {bubbles: true, key: 'Escape', code: 'Escape', keyCode: 27}));
  try { el.blur(); } catch (e) { }
  document.body.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));

  return JSON.stringify({options: options.slice(0, cap), total: total});
})(KEY_JSON, CAP_JSON)
"""

#: Past this an option list is a search box with a catalogue behind it, not
#: a choice to put in front of a model.
SEARCHABLE_FROM = 40


def reveal_scripts(key: str, cap: int = 60) -> tuple[str, str]:
    """The open / read-and-close scripts for discovering one control."""
    import json

    return (REVEAL_JS.replace("KEY_JSON", json.dumps(key)),
            OPTIONS_JS.replace("KEY_JSON", json.dumps(key))
                      .replace("CAP_JSON", json.dumps(cap)))


def needs_options(f: dict) -> bool:
    """Whether this field is a choice whose choices have not been read."""
    return bool(f.get("widget")) and not (f.get("options") or [])


#: Open the control: click a combobox, or type into a typeahead so its
#: suggestions are fetched.
OPEN_JS = r"""
((key, text, widget) => {
  const find = k => document.getElementById(k)
    || document.querySelector(`[name="${CSS.escape(k)}"]`)
    || document.querySelector(`[data-sushi-key="${CSS.escape(k)}"]`);
  const el = find(key);
  if (!el) return 'missing';
  try { el.scrollIntoView({block: 'center'}); } catch (e) { }
  const tag = el.tagName.toLowerCase();

  // Open it. react-select opens on a *mousedown over its control div* or
  // on ArrowDown — never on a value being assigned to its input. Typing
  // alone left the menu shut, so the search text went in, no list ever
  // appeared, and three required education fields on every Greenhouse
  // form came back as "no options to pick from".
  try { el.focus(); } catch (e) { }
  const ctrl = el.closest('[class*="control"]') || el.parentElement || el;
  for (const n of [ctrl, el]) {
    if (!n) continue;
    n.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, button: 0}));
    n.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, button: 0}));
    n.dispatchEvent(new MouseEvent('click', {bubbles: true, button: 0}));
  }
  el.dispatchEvent(new KeyboardEvent('keydown',
    {bubbles: true, key: 'ArrowDown', code: 'ArrowDown', keyCode: 40}));

  // Then narrow it, where there is something to type into. A list of every
  // university in the world has to be searched, not scrolled.
  if (widget === 'typeahead' && (tag === 'input' || tag === 'textarea')) {
    const proto = tag === 'textarea'
      ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, '');
    el.dispatchEvent(new Event('input', {bubbles: true}));
    setter.call(el, text);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'e'}));
    return 'typed';
  }
  return 'opened';
})(KEY_JSON, TEXT_JSON, WIDGET_JSON)
"""

#: Click the option that matches. Run after the list has had time to appear.
PICK_JS = r"""
((key, text) => {
  const find = k => document.getElementById(k)
    || document.querySelector(`[name="${CSS.escape(k)}"]`)
    || document.querySelector(`[data-sushi-key="${CSS.escape(k)}"]`);
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const el = find(key);
  if (!el) return 'missing';

  // Where this control's options are. The same rule the reader used, so a
  // control whose options were read is a control whose options can be
  // clicked.
  let box = null;
  const ref = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
  if (ref) {
    for (const id of ref.split(/\s+/)) {
      const n = document.getElementById(id);
      if (n && (n.getAttribute('role') === 'listbox'
                || n.querySelector('[role="option"]'))) { box = n; break; }
    }
  }
  if (!box) {
    let n = el.nextElementSibling;
    for (let i = 0; n && i < 2; i++, n = n.nextElementSibling) {
      if (n.getAttribute && n.getAttribute('role') === 'listbox') { box = n; break; }
      const inner = n.querySelector && n.querySelector('[role="listbox"]');
      if (inner) { box = inner; break; }
    }
  }
  // Last resort: whatever listbox is open on the page right now. A portalled
  // dropdown — react-select's default — renders at the end of <body> with no
  // relationship to the control at all.
  if (!box) {
    const open = [...document.querySelectorAll('[role="listbox"]')]
      .filter(b => b.offsetWidth || b.offsetHeight || b.getClientRects().length)
      .filter(b => { const o = b.id && document.querySelector(`[aria-controls~="${CSS.escape(b.id)}"],[aria-owns~="${CSS.escape(b.id)}"]`); return (!o || o === el) && (!/^iti-/.test(b.id || '') || !!el.closest('.iti')); });
    if (open.length === 1) box = open[0];
  }
  if (!box) return 'no-list';

  const options = [...box.querySelectorAll('[role="option"]')];
  const want = clean(text).toLowerCase();
  let hit = options.find(o => clean(o.innerText).toLowerCase() === want);
  if (!hit) hit = options.find(o => clean(o.innerText).toLowerCase().includes(want));
  if (!hit && options.length === 1) hit = options[0];
  if (!hit) return 'no-option:' + options.length;
  try { hit.scrollIntoView({block: 'nearest'}); } catch (e) { }
  // Some widgets commit on mousedown and never see a click.
  hit.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
  hit.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
  hit.click();
  return 'picked:' + clean(hit.innerText);
})(KEY_JSON, TEXT_JSON)
"""

#: What the control is showing now, so the pick can be verified rather than
#: assumed. A dropdown that silently refused the click is the whole reason
#: this path exists.
#:
#: Reading ``el.value`` is not enough, and getting that wrong cost real
#: applications: react-select **clears its input** once an option is
#: committed and renders the chosen label in a sibling div. So every
#: dropdown on a Greenhouse form was picked correctly, verified as empty,
#: reported as "did not take", pushed back to the user as an unanswered
#: question, and retried on all three passes.
VERIFY_JS = r"""
((key) => {
  const find = k => document.getElementById(k)
    || document.querySelector(`[name="${CSS.escape(k)}"]`)
    || document.querySelector(`[data-sushi-key="${CSS.escape(k)}"]`);
  const el = find(key);
  if (!el) return '';
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();

  const own = ('value' in el) ? clean(el.value) : '';
  if (own) return own;

  // The option it says is active, where the widget tracks one.
  const active = el.getAttribute('aria-activedescendant');
  if (active) {
    const n = document.getElementById(active);
    if (n && clean(n.innerText)) return clean(n.innerText);
  }

  // The label the widget renders beside its (now empty) input. Walk out
  // until something has text, stopping well before the whole form does.
  let n = el.parentElement;
  for (let i = 0; n && i < 4; i++, n = n.parentElement) {
    const slot = n.querySelector(
      '[class*="single-value"], [class*="multi-value__label"], [class*="selected"]');
    if (slot && clean(slot.innerText)) return clean(slot.innerText);
    const text = clean(n.innerText || n.textContent);
    if (text && text.length <= 120) return text;
  }
  return clean(el.innerText || el.textContent);
})(KEY_JSON)
"""


#: Undo a typeahead that was typed into and never committed. The text left
#: in the search box is not an answer — the widget holds nothing — but it
#: looks like one to the next read, to the user glancing at the form, and
#: on some widgets it is submitted as free text.
CLEAR_JS = r"""
((key) => {
  const find = k => document.getElementById(k)
    || document.querySelector(`[name="${CSS.escape(k)}"]`)
    || document.querySelector(`[data-sushi-key="${CSS.escape(k)}"]`);
  const el = find(key);
  if (!el || !('value' in el) || !el.value) return 'nothing';
  const tag = el.tagName.toLowerCase();
  if (tag !== 'input' && tag !== 'textarea') return 'nothing';
  const proto = tag === 'textarea'
    ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, '');
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  el.dispatchEvent(new KeyboardEvent('keydown',
    {bubbles: true, key: 'Escape', code: 'Escape', keyCode: 27}));
  try { el.blur(); } catch (e) { }
  return 'cleared';
})(KEY_JSON)
"""


def clear_script(key: str) -> str:
    import json

    return CLEAR_JS.replace("KEY_JSON", json.dumps(key))


def choose_scripts(f: Filled) -> tuple[str, str, str]:
    """The open / pick / verify scripts for one click-through control."""
    import json

    def bake(src: str) -> str:
        return (src.replace("KEY_JSON", json.dumps(f.key))
                   .replace("TEXT_JSON", json.dumps(f.value))
                   .replace("WIDGET_JSON", json.dumps(f.widget)))

    return bake(OPEN_JS), bake(PICK_JS), bake(VERIFY_JS)
