"""A live checklist of the form's fields, drawn on the job page.

The pill says what Sushi is doing this second. This says where the form
stands: every field, ticked when it holds an answer, flagged when it is
required and still empty. It reads the page itself every second and a half,
so it is right on any form — Greenhouse, Lever, Workday or a company's own —
and stays right after Sushi stops and the user carries on by hand.
"""
from __future__ import annotations

CHECKLIST_JS = r"""
(() => {
  if (window.__sushiChecklist) { window.__sushiHook && window.__sushiHook(); window.__sushiChecklist(); return 'ok'; }
  const clean = s => (s || '').replace(/\s+/g, ' ').replace(/\*/g, '').trim();
  const vis = n => !!(n && (n.offsetWidth || n.offsetHeight || n.getClientRects().length));
  const mine = n => n.closest && n.closest('#sushi-checklist, #sushi-bubble, [id^="sushi-"]');

  // A field inside a web component has its label in the same shadow root,
  // where document.querySelector cannot see it.
  const rootOf = el => { const r = el.getRootNode(); return r && r.querySelector ? r : document; };

  function labelOf(el) {
    if (el.id) {
      const l = rootOf(el).querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return clean(l.innerText);
    }
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = by.split(/\s+/).map(i => document.getElementById(i))
        .filter(Boolean).map(n => n.innerText).join(' ');
      if (clean(t)) return clean(t);
    }
    // A box inside its own label — "<label><input> I agree to … *</label>" —
    // is named by that label. Searching it for a nested label found none,
    // so the box fell back to its name ("privacy") and lost its asterisk,
    // and a required consent was listed as optional.
    const own = el.closest('label');
    if (own && clean(own.innerText)) return clean(own.innerText);
    const wrap = el.closest('label, fieldset, [class*="field"], [class*="question"]');
    if (wrap) {
      const head = wrap.querySelector('label, legend');
      if (head && clean(head.innerText)) return clean(head.innerText);
    }
    return clean(el.getAttribute('aria-label') || el.placeholder || el.name || '');
  }

  // The question a choice or a bare box belongs to. Lever puts each radio
  // inside its own "<label><input> Yes</label>" with the question in a div
  // above, so the list showed "Yes", "Yes", "Naukri" and "Type your
  // response" instead of what was being asked. Climb while the ancestor
  // still holds only this one field, and take the first line of its text
  // that is not one of the field's own option labels.
  function questionOf(el) {
    const name = el.name || '';
    const optionText = new Set();
    let n = el.parentElement, last = null;
    for (let i = 0; n && i < 8; i++, n = n.parentElement) {
      const names = new Set([...n.querySelectorAll('input:not([type=hidden]), select, textarea')]
        .map(e => (e.type === 'radio' || e.type === 'checkbox') && e.name ? 'n:' + e.name : e));
      if (names.size > 1) {
        // Shares its box with another control (Lever's pronouns and their
        // "Custom" box): read the heading just above instead.
        const stop = n.parentElement;
        for (let k = last || el; k; k = k === stop ? null : k.parentElement) {
          const prev = k.previousElementSibling;
          const t = prev && !prev.querySelector('input, select, textarea') && clean(prev.innerText);
          if (t && t.length > 2 && t.length < 150 && !optionText.has(t)) return t;
        }
        break;
      }
      last = n;
      if (n.matches('label')) optionText.add(clean(n.innerText));
      n.querySelectorAll('label').forEach(l => optionText.add(clean(l.innerText)));
      n.querySelectorAll('option').forEach(o => optionText.add(clean(o.innerText)));
      const lines = (n.innerText || '').split('\n').map(clean)
        .filter(t => t.length > 2 && !optionText.has(t) && !/^(upload|attach|choose|browse)\b.{0,20}$/i.test(t));
      if (lines.length) return lines[0];
    }
    return '';
  }
  const _PLACEHOLDERISH = /^(type|write|enter|select|choose)\b|^(yes|no|other|custom|none)$/i;

  function filled(el) {
    if (el.type === 'checkbox' || el.type === 'radio') {
      const name = el.name;
      if (!name) return el.checked;
      return [...document.querySelectorAll(`input[name="${CSS.escape(name)}"]`)]
        .some(i => i.checked);
    }
    if (el.type === 'file') {
      if (el.files && el.files.length) return true;
      const box = el.closest('[class*="upload"], [class*="file"], fieldset, div');
      return !!(box && /\.(pdf|docx?|txt|rtf)\b/i.test(box.innerText || ''));
    }
    if ((el.value || '').trim()) return true;
    // react-select and its kind keep the answer beside the input.
    let n = el.parentElement;
    for (let i = 0; n && i < 5; i++, n = n.parentElement) {
      // Only as far out as this field's own box: one step further and the
      // neighbour's answer counts as this one's.
      if (n.querySelectorAll('input:not([type=hidden]), select, textarea').length > 1) break;
      const sv = n.querySelector('[class*="single-value"], [class*="singleValue"], [class*="multi-value"]');
      if (sv && clean(sv.innerText)) return true;
    }
    return false;
  }

  function required(el, label) {
    return el.required || el.getAttribute('aria-required') === 'true' ||
      /[*✱]\s*$/.test((rootOf(el).querySelector(el.id ? `label[for="${CSS.escape(el.id)}"]` : 'x') || {}).innerText || '') ||
      /[*✱]/.test(label) ||
      /[*✱]/.test((el.closest('label') || {}).innerText || '');
  }

  function read() {
    const seen = new Set(); const rows = [];
    // Some boards (SmartRecruiters) build the form inside web components,
    // where document.querySelectorAll cannot see it: the list came back
    // empty and said every required field was filled.
    const deep = (root, out) => {
      root.querySelectorAll('input, select, textarea').forEach(e => out.push(e));
      root.querySelectorAll('*').forEach(n => { if (n.shadowRoot && n.id !== 'sushi-checklist') deep(n.shadowRoot, out); });
      return out;
    };
    const els = deep(document, [])
      .filter(el => !mine(el) && !/recaptcha/i.test(el.name || el.id || '') && !['hidden', 'submit', 'button', 'search', 'image', 'reset']
        .includes((el.type || '').toLowerCase()));
    for (const el of els) {
      if (el.type !== 'file' && !vis(el) && !vis(el.parentElement)) continue;
      let label = labelOf(el);
      if (!label) continue;
      if (el.type === 'radio' || el.type === 'checkbox') {
        const fs = el.closest('fieldset');
        const legend = fs && fs.querySelector('legend');
        if (legend) label = clean(legend.innerText);
        else if (el.type === 'radio' || el.closest('label')) label = questionOf(el) || label;
        if (label === clean(el.value || '')) label = questionOf(el) || label;
      } else if (_PLACEHOLDERISH.test(label) || label === clean(el.placeholder || '') || label === clean(el.name || '')) {
        label = questionOf(el) || label;
      }
      const key = (el.name && (el.type === 'radio' || el.type === 'checkbox')) ? 'n:' + el.name : label;
      if (seen.has(key)) continue;
      seen.add(key);
      const rawLabel = (el.id && rootOf(el).querySelector(`label[for="${CSS.escape(el.id)}"]`)) ?
        rootOf(el).querySelector(`label[for="${CSS.escape(el.id)}"]`).innerText : label;
      // First line only, without the required marker or whatever the
      // board wrote beside it ("Resume/CV ✱ FILE.PDF Couldn't auto-read").
      const shown = (label.split(/\s*[✱*]\s*/)[0] || label).replace(/[✱*]/g, '').trim();
      if (/^(upload|attach|choose|browse)\b.{0,20}$|^(upload|attach) (file|resume|cv)/i.test(shown)) {
        // An upload button's caption: the question is the text above it.
        const q = questionOf(el);
        if (!q || /^(upload|attach)/i.test(q)) continue;
        label = q;
      }
      const shown2 = (label.split(/\s*[✱*]\s*/)[0] || label).replace(/[✱*]/g, '').trim();
      rows.push({label: (shown2 || label).slice(0, 60), done: filled(el),
                 req: required(el, rawLabel || '')});
    }
    return rows;
  }

  function footer() {
    const n = (window.__sushiEdited || new Set()).size;
    const done = window.__sushiLearnDone;
    const wrap = c => `<div style="margin-top:10px !important;padding-top:10px;border-top:1px solid #EEE9E6">${c}</div>`;
    if (done !== undefined)
      return wrap(`<span style="color:#5C7A4F;font-weight:600">✓ Saved to Sushi. It will use your answers from now on.</span>`);
    if (window.__sushiLearnRequest) {
      if (Date.now() - window.__sushiLearnRequest > 15000) {
        // Nobody picked it up: Sushi was closed, or this tab is not watched.
        window.__sushiLearnRequest = 0;
        window.__sushiLearnFailed = true;
      } else {
        return wrap(`<span style="color:#9A9A9A">Saving your changes…</span>`);
      }
    }
    if (window.__sushiLearnFailed && n)
      return wrap(`<span style="color:#A62A1A">Sushi did not answer. Is the app still open?</span>` +
        `<div data-sushi-learn style="margin-top:8px !important;cursor:pointer;text-align:center;border:1px solid #B5651D;color:#B5651D;border-radius:9px;padding:7px 10px;font-weight:600">Try again</div>`);
    if (!n) return '';
    return wrap(`<div data-sushi-learn style="cursor:pointer;text-align:center;background:#B5651D;color:#FFF;border-radius:9px;padding:8px 10px;font-weight:600">Save ${n} change${n === 1 ? '' : 's'} to Sushi</div>` +
      `<div style="margin-top:6px !important;color:#9A9A9A;font-size:11px">Updates your profile and answers, so the next form gets them right.</div>`);
  }

  function draw() {
    let box = document.getElementById('sushi-checklist');
    if (!box) {
      // Drawn inside a shadow root. On the page itself the site's own CSS
      // reached in — a fixed line-height, absolutely positioned divs — and
      // on some boards every line of the list landed on top of the others.
      box = document.createElement('div');
      box.id = 'sushi-checklist';
      box.style.cssText = 'all:initial;position:fixed;top:72px;right:20px;z-index:2147483646';
      const root = box.attachShadow({mode: 'open'});
      // Pointer-down, not click: pressing the button blurs the field just
      // edited, its change event redraws the panel, and a click aimed at the
      // old button could land on nothing.
      root.addEventListener('pointerdown', e => {
        const t = e.target && e.target.closest && e.target.closest('[data-sushi-learn]');
        if (t) {
          // Picked up by the app's form watcher on its next read.
          e.preventDefault();
          window.__sushiLearnFailed = false; window.__sushiLearnRequest = Date.now(); draw();
        }
      });
      root.addEventListener('click', e => {
        const t = e.target;
        if (t && t.dataset && t.dataset.sushiFold !== undefined) {
          box.dataset.folded = box.dataset.folded === '1' ? '0' : '1'; draw();
        }
      });
      document.body.appendChild(box);
    }
    const root = box.shadowRoot;
    const rows = read();
    const need = rows.filter(r => !r.done && r.req);
    const opt = rows.filter(r => !r.done && !r.req);
    const done = rows.filter(r => r.done);
    const esc = s => s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    const line = (mark, colour, r) =>
      `<div style="display:flex;gap:8px;padding:3px 0"><span style="color:${colour};font-weight:700;width:12px">${mark}</span><span>${esc(r.label)}</span></div>`;
    const head = !rows.length
      ? `<b style="color:#9A9A9A">Reading the form…</b>`
      : need.length
      ? `<b style="color:#A62A1A">${need.length} required left</b>`
      : `<b style="color:#5C7A4F">All required fields filled</b>`;
    const folded = box.dataset.folded === '1';
    box.dataset.ready = (need.length || !rows.length) ? '0' : '1';
    // No form on this page (a closed posting, an advert before Apply): no list.
    box.style.display = rows.length ? 'block' : 'none';
    const STYLE = `<style>
      :host { all: initial; }
      .p { box-sizing: border-box; width: 300px; max-height: 60vh; overflow: auto;
           background: #FFFFFF; color: #201F1D; border: 1px solid #D7D3D3;
           border-radius: 14px; box-shadow: 0 10px 30px rgba(0,0,0,.14);
           font: 13px/1.35 system-ui, -apple-system, "Segoe UI", sans-serif;
           padding: 12px 14px; text-align: left; }
      .p div, .p span, .p b { position: static; float: none; height: auto;
           line-height: 1.35; margin: 0; text-transform: none; }
      .p b { font-weight: 700; }
    </style>`;
    const html = STYLE + '<div class="p">' +
      `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
         <span>${head}<span style="color:#9A9A9A"> · ${done.length}/${rows.length} filled</span></span>
         <span data-sushi-fold style="cursor:pointer;color:#9A9A9A">${folded ? 'show' : 'hide'}</span>
       </div>` +
      (folded ? '' :
        (need.length ? `<div style="margin-top:8px !important;color:#9A9A9A;font-size:11px;letter-spacing:.04em">NEEDS YOU</div>` + need.map(r => line('!', '#A62A1A', r)).join('') : '') +
        (opt.length ? `<div style="margin-top:8px !important;color:#9A9A9A;font-size:11px;letter-spacing:.04em">OPTIONAL, EMPTY</div>` + opt.map(r => line('○', '#9A9A9A', r)).join('') : '') +
        (done.length ? `<div style="margin-top:8px !important;color:#9A9A9A;font-size:11px;letter-spacing:.04em">FILLED</div>` + done.map(r => line('✓', '#5C7A4F', r)).join('') : '')) +
      footer() + '</div>';
    // Only when something changed. Rewriting it every 1.5s replaced the
    // button under the pointer, so a click could land on nothing.
    if (root.__last !== html) { root.innerHTML = html; root.__last = html; }
  }

  // Fields the user has changed by hand. Only trusted events: Sushi's own
  // typing is synthetic and must not count as the user's answer.
  window.__sushiEdited = window.__sushiEdited || new Set();
  // Per document, not per window: a form that swaps its page in place
  // (a new step, a re-render) gets a fresh document and must be hooked again.
  function hook() {
    if (!document.documentElement.__sushiEditHook) {
      document.documentElement.__sushiEditHook = true;
      const mark = e => { const t = e.target;
        if (e.isTrusted && t && !mine(t) && /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName) && t.type !== 'file') {
          window.__sushiEdited.add(t.name || t.id || labelOf(t));
          window.__sushiLearnDone = undefined; draw(); } };
      document.addEventListener('input', mark, true);
      document.addEventListener('change', mark, true);
    }
  }
  window.__sushiHook = hook;
  hook();
  window.__sushiChecklist = draw;
  draw();
  setInterval(() => { try { hook(); draw(); } catch (e) {} }, 1500);
  return 'ok';
})()
"""
