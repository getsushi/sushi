/* Sushi's in-page button: the thing you click on a job page to have the
 * form filled. One file, two hosts.
 *
 * It knows nothing about how it is delivered. The host defines
 * `window.__sushiHost` before this runs and supplies:
 *
 *   send(payload)   hand a request to whoever is driving the page. Sushi's
 *                   own browser queues it for the Python side to poll; the
 *                   Chrome extension posts it to the app. Either may return
 *                   a promise, and neither is expected to answer in place:
 *                   the label is set from outside once something happens.
 *   always          true to show on every page. The extension leaves it off
 *                   and lets the heuristic below decide, because it is
 *                   loaded into every tab the user opens.
 *   label           optional override for the resting label.
 *
 * Defensive in the same three ways branding.js learned to be, for the same
 * reasons: keyed by element id so navigations cannot stack copies up, text
 * written only when it differs so the observer does not wake itself, and the
 * repaint deferred to an animation frame so a page doing a thousand DOM
 * writes costs one. A button is never worth breaking somebody's page over.
 */
(() => {
  const HOST = window.__sushiHost || {};
  const ID = 'sushi-bubble-7c21';

  // The top frame only. Sushi's browser registers this on every frame of
  // every document, and an ATS is frames all the way down — Greenhouse
  // serves its form from job-boards.greenhouse.io/embed/job_app inside the
  // company's page. A `position:fixed` pill inside that iframe is fixed to
  // the *iframe's* viewport, so it drew a second button halfway down the
  // page, clipped by the frame's own edge. One page, one button.
  try {
    if (window.top !== window.self) return;
  } catch (e) {
    // Cross-origin parent: the comparison throws, which itself means this
    // is not the top frame.
    return;
  }

  if (window.__sushiBubble) {
    try { window.__sushiBubble.render(); } catch (e) { }
    return;
  }

  const INK = '#201F1D';
  const CREAM = '#FFFBF2';
  const DIM = 'rgba(255,251,242,.62)';
  const SAGE = '#87A878';
  const CLAY = '#B68235';

  const SAY = {
    idle: HOST.label || 'Fill this application',
    busy: 'Filling it in…',
    done: 'Filled — check it before you send',
    error: 'That did not work'
  };
  const DOT = { idle: SAGE, busy: CLAY, done: SAGE, error: CLAY };

  // At rest the learn button says nothing — it is a mark, not a sentence,
  // and `idle` being empty is what collapses it to a circle in `paint`.
  // The hover title carries the invitation instead, which is where a
  // 32px affordance is allowed to explain itself.
  const LEARN_SAY = {
    idle: '',
    busy: 'Reading your answers…',
    done: 'Learnt from this form',
    error: 'Could not read this form'
  };
  const LEARN_TITLE = 'Click me to make me learn from this form';

  // Where a form is worth offering on. Deliberately broad — the cost of
  // offering on a page that turns out not to be an application is a button
  // nobody presses, and the cost of missing one is the feature not being
  // there when it was wanted.
  const JOBBY = new RegExp([
    'greenhouse', 'lever\\.co', 'ashbyhq', 'workday', 'myworkdayjobs',
    'smartrecruiters', 'workable', 'breezy', 'jobvite', 'icims',
    'teamtailor', 'recruitee', 'personio', 'bamboohr', 'join\\.com',
    'rippling', 'pinpointhq', 'careers', 'jobs', 'vacanc', 'apply'
  ].join('|'), 'i');

  const LID = ID + '-learn';

  let state = 'idle';
  let note = '';
  let dismissed = false;
  let learnState = 'idle';
  let learnNote = '';
  // Three things decide whether the Learn button is there and how wide.
  //
  // `armed` is the whole of why it is not there to begin with. Offering
  // "learn from this form" over a form Sushi has just filled is offering to
  // learn Sushi's own answers back, which is worth nothing — the value only
  // exists once a person has changed something. So the button does not
  // exist until they do, and its arrival is itself the question being
  // asked: you have rewritten an answer, shall I keep it?
  let learnArmed = false;
  let learnHover = false;
  //: Expanded on arrival so it can say what it is, then collapsed to the
  //: mark. A sushi logo that appeared silently would be a mystery button.
  let learnAnnounce = false;
  let announceTimer = null;

  const relevant = () => {
    if (dismissed) return false;
    if (HOST.always) return true;
    try {
      return JOBBY.test(location.hostname + location.pathname);
    } catch (e) {
      return false;
    }
  };

  // A slow breath around the pill while Sushi is working, and nothing at
  // all when it is not. The point is to answer "is this still going?"
  // without a spinner or a number: a form that has not moved for ninety
  // seconds looks identical to a crash, and the pill is often the only part
  // of Sushi on screen while an application runs.
  //
  // Clay, the app's own accent, rather than the rainbow it was sketched
  // from. A saturated spectrum on a page full of warm earth tones would be
  // the one thing on screen that belongs to a different program — which is
  // exactly the reason the dialogs stopped being blue.
  //
  // In a stylesheet because keyframes cannot be expressed inline. One
  // element, carrying our own id, with a keyframe name nothing else will
  // have picked.
  const GLOW = 'sushiGlow7c21';
  const ensureGlowStyle = () => {
    try {
      if (document.getElementById(ID + '-style')) return;
      const s = document.createElement('style');
      s.id = ID + '-style';
      // Three layers, because one is either invisible or a blob: a hard
      // ring that draws the edge, a tight bloom just outside it, and a wide
      // soft halo that does most of the moving. The first version used one
      // faint pair and could not be seen at all against the pill's own drop
      // shadow, which is darker than the glow was.
      s.textContent =
        '@keyframes ' + GLOW + '{' +
        '0%,100%{box-shadow:0 10px 26px rgba(0,0,0,.28),' +
        '0 0 0 2px rgba(196,101,74,.55),' +
        '0 0 14px 2px rgba(196,101,74,.45),' +
        '0 0 28px 7px rgba(196,101,74,.20)}' +
        '50%{box-shadow:0 10px 26px rgba(0,0,0,.28),' +
        '0 0 0 2px rgba(196,101,74,.95),' +
        '0 0 24px 6px rgba(196,101,74,.80),' +
        '0 0 48px 15px rgba(196,101,74,.42)}}';
      (document.head || document.documentElement).appendChild(s);
    } catch (e) { /* never break the page */ }
  };

  const setText = (node, value) => {
    if (node && node.textContent !== value) node.textContent = value;
  };
  const setStyle = (node, prop, value) => {
    if (node && node.style[prop] !== value) node.style[prop] = value;
  };

  const build = () => {
    const el = document.createElement('div');
    el.id = ID;
    el.style.cssText = [
      'position:fixed', 'z-index:2147483647', 'right:16px', 'top:16px',
      'display:flex', 'align-items:center', 'gap:10px',
      'padding:10px 14px 10px 12px', 'border-radius:999px',
      'background:' + INK, 'color:' + CREAM,
      'box-shadow:0 10px 26px rgba(0,0,0,.28)',
      'font:500 13px/1.2 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif',
      'letter-spacing:-.01em', 'cursor:pointer', 'user-select:none',
      'transition:transform .12s ease, box-shadow .12s ease'
    ].join(';');

    el.innerHTML =
      '<span style="font-size:15px">\u{1F363}</span>' +
      '<span id="' + ID + '-txt" style="font-weight:600"></span>' +
      '<span id="' + ID + '-dot" style="width:7px;height:7px;border-radius:50%"></span>' +
      '<span id="' + ID + '-x" title="Hide until the next page" ' +
      'style="margin-left:2px;padding:0 2px;color:' + DIM + ';font-size:15px;' +
      'line-height:1">×</span>';

    el.addEventListener('mouseenter', () => {
      setStyle(el, 'transform', 'translateY(-1px)');
      setStyle(el, 'boxShadow', '0 14px 30px rgba(0,0,0,.34)');
    });
    el.addEventListener('mouseleave', () => {
      setStyle(el, 'transform', 'none');
      setStyle(el, 'boxShadow', '0 10px 26px rgba(0,0,0,.28)');
    });

    el.addEventListener('click', (ev) => {
      // The dismiss cross is inside the pill, so it has to claim the click
      // before the pill reads it as "fill this".
      const x = document.getElementById(ID + '-x');
      if (x && (ev.target === x || x.contains(ev.target))) {
        ev.preventDefault();
        ev.stopPropagation();
        dismissed = true;
        remove();
        return;
      }
      ev.preventDefault();
      ev.stopPropagation();
      ask();
    }, true);

    return el;
  };

  // The second button: "these answers are right, keep them". Sage rather
  // than ink, because it is a different kind of act from the one above it —
  // that one asks Sushi to do something to the form, this one tells Sushi
  // something about it. Ink on sage is 4.79:1, which clears AA for the
  // normal-weight text it carries; cream on sage is 2.57:1 and would not.
  //
  // A mark at rest, a sentence when it has one. It used to sit there as a
  // full green pill reading "Learn from this form" — the loudest thing on
  // an employer's page, permanently, for an action most people take once.
  // So it rests as a 32px disc the size of the brand mark, says what it did
  // for four seconds after a press, and goes quiet again.
  const LEARN_REST = '0 4px 12px rgba(0,0,0,.16)';
  const LEARN_HOVER = '0 8px 18px rgba(0,0,0,.24)';

  const buildLearn = () => {
    const el = document.createElement('div');
    el.id = LID;
    el.style.cssText = [
      'position:fixed', 'z-index:2147483647', 'right:16px', 'top:62px',
      'display:flex', 'align-items:center', 'justify-content:center',
      'box-sizing:border-box', 'height:32px', 'gap:0', 'padding:0',
      'border-radius:999px',
      'background:' + SAGE, 'color:' + INK,
      'box-shadow:' + LEARN_REST,
      'font:600 12.5px/1.2 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif',
      'letter-spacing:-.01em', 'cursor:pointer', 'user-select:none',
      'transition:padding .14s ease, gap .14s ease, transform .12s ease,'
        + ' box-shadow .12s ease'
    ].join(';');
    el.innerHTML =
      '<span style="font-size:14px;line-height:1;width:32px;'
        + 'text-align:center;flex:none">\u{1F4D2}</span>' +
      '<span id="' + LID + '-txt" style="white-space:nowrap"></span>';
    el.title = LEARN_TITLE;
    el.addEventListener('mouseenter', () => {
      learnHover = true;
      setStyle(el, 'transform', 'translateY(-1px)');
      setStyle(el, 'boxShadow', LEARN_HOVER);
    });
    el.addEventListener('mouseleave', () => {
      learnHover = false;
      setStyle(el, 'transform', 'none');
      setStyle(el, 'boxShadow', LEARN_REST);
    });
    el.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      askLearn();
    }, true);
    return el;
  };

  // An edit by the person at the keyboard, which is the only thing that
  // brings the button out.
  //
  // Two filters, and both are needed. `isTrusted` throws out the synthetic
  // input/change events Sushi's own lookup fires after it writes a value —
  // a React field keeps nothing without them, so they are not optional and
  // there are a lot of them. And the agent types over CDP, which *is*
  // trusted, so the second filter is the main pill: nothing counts as a
  // human edit while Sushi is still working.
  const noticeEdit = (ev) => {
    try {
      if (learnArmed || !ev || !ev.isTrusted || state === 'busy') return;
      const t = ev.target;
      if (!t || !t.tagName) return;
      if (!/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      if (t.type === 'password' || t.type === 'hidden') return;
      learnArmed = true;
      learnAnnounce = true;
      if (announceTimer) clearTimeout(announceTimer);
      announceTimer = setTimeout(() => {
        learnAnnounce = false;
        paint();
      }, 4200);
      paint();
    } catch (e) { /* never break the page */ }
  };

  const remove = () => {
    [ID, LID].forEach((id) => {
      const el = document.getElementById(id);
      if (el && el.parentNode) el.parentNode.removeChild(el);
    });
  };

  // Only where there is something to learn from. A page with no fields on
  // it has no answers to keep, and a button that can only ever report
  // "learned 0" is worse than no button.
  const hasForm = () => {
    try {
      return !!document.querySelector('input:not([type=hidden]),textarea,select');
    } catch (e) {
      return false;
    }
  };

  const paint = () => {
    try {
      if (!document.body) return;
      if (!relevant()) { remove(); return; }
      ensureGlowStyle();
      let el = document.getElementById(ID);
      if (!el) {
        el = build();
        document.body.appendChild(el);
      }
      setText(document.getElementById(ID + '-txt'), note || SAY[state] || SAY.idle);
      setStyle(document.getElementById(ID + '-dot'), 'background',
               DOT[state] || SAGE);
      setStyle(el, 'cursor', state === 'busy' ? 'default' : 'pointer');
      setStyle(el, 'opacity', state === 'busy' ? '.86' : '1');
      // While the animation runs it owns box-shadow outright — a CSS
      // animation beats an inline style on the property it animates — so
      // the hover lift below simply has no effect until the work is done,
      // which is right: the pill does not take a click while it is busy.
      setStyle(el, 'animation',
               state === 'busy' ? GLOW + ' 1.8s ease-in-out infinite' : 'none');

      let lel = document.getElementById(LID);
      // Offered only where a host is listening for it, where there are
      // fields to read, and only once somebody has actually changed one.
      // The extension loads this same file into every tab and has nowhere
      // to send a learn request, so it never asks for the button.
      if (!HOST.learn || !hasForm() || !learnArmed) {
        if (lel && lel.parentNode) lel.parentNode.removeChild(lel);
      } else {
        if (!lel) {
          lel = buildLearn();
          document.body.appendChild(lel);
        }
        // A disc while it has nothing to say, a pill while it has. The
        // right-hand edge is pinned, so it grows leftwards out of the mark
        // rather than shunting itself across the page.
        const says = learnNote || LEARN_SAY[learnState] || '';
        setText(document.getElementById(LID + '-txt'), says);
        setStyle(lel, 'padding', says ? '0 14px 0 2px' : '0');
        setStyle(lel, 'gap', says ? '6px' : '0');
        setStyle(lel, 'cursor', learnState === 'busy' ? 'default' : 'pointer');
        setStyle(lel, 'opacity', learnState === 'busy' ? '.8' : '1');
        if (lel.title !== LEARN_TITLE) lel.title = LEARN_TITLE;
      }
    } catch (e) { /* never break the page */ }
  };

  const askLearn = () => {
    if (learnState === 'busy') return;
    window.__sushiBubble.setLearn('busy', '');
    let payload;
    try {
      payload = { type: 'learn', url: location.href,
                  title: document.title || '', at: Date.now() };
    } catch (e) {
      payload = { type: 'learn', url: '', title: '', at: Date.now() };
    }
    try {
      const sent = HOST.send && HOST.send(payload);
      if (sent && typeof sent.catch === 'function') {
        sent.catch(() => window.__sushiBubble.setLearn('error', ''));
      }
    } catch (err) {
      window.__sushiBubble.setLearn('error', '');
    }
  };

  const ask = () => {
    if (state === 'busy') return;
    window.__sushiBubble.set('busy', '');
    let payload;
    try {
      payload = {
        type: 'fill',
        url: location.href,
        title: document.title || '',
        at: Date.now()
      };
    } catch (e) {
      payload = { type: 'fill', url: '', title: '', at: Date.now() };
    }
    try {
      const sent = HOST.send && HOST.send(payload);
      if (sent && typeof sent.catch === 'function') {
        sent.catch((err) => window.__sushiBubble.set(
          'error', 'Sushi is not listening — is the app open?'));
      }
    } catch (err) {
      window.__sushiBubble.set('error', 'Sushi is not listening — is the app open?');
    }
  };

  window.__sushiBubble = {
    get state() { return state; },
    set(next, text) {
      state = next || 'idle';
      note = text || '';
      paint();
    },
    setLearn(next, text) {
      learnState = next || 'idle';
      learnNote = text || '';
      paint();
      // A result is news, not a resting state: it says what just happened
      // and then the button goes back to being a button, so the next round
      // of corrections has something to press.
      if (learnState === 'done' || learnState === 'error') {
        setTimeout(() => {
          if (learnState === 'done' || learnState === 'error') {
            learnState = 'idle';
            learnNote = '';
            paint();
          }
        }, 4000);
      }
    },
    render() { paint(); },
    // So a host can put the button back after the user has dismissed it,
    // which is what a fresh navigation should do.
    revive() { dismissed = false; paint(); }
  };

  const start = () => {
    paint();
    // On the document and in the capture phase, because an ATS form will
    // happily stop a change event on its way up — and passive, because this
    // only ever reads the event. Both are registered once; `noticeEdit`
    // returns immediately once the button is out, so the steady-state cost
    // of this is a tag-name test per keystroke.
    try {
      if (HOST.learn) {
        document.addEventListener('input', noticeEdit, { capture: true, passive: true });
        document.addEventListener('change', noticeEdit, { capture: true, passive: true });
      }
    } catch (e) { }
    // Single-page ATS forms rewrite the whole body on a route change, which
    // takes the button with it. Same shape of observer as the badge: our own
    // mutations ignored, re-entrancy guarded, repaint deferred.
    try {
      let queued = false;
      const observer = new MutationObserver(records => {
        if (queued) return;
        const ours = records.every(r => {
          const n = r.target;
          return n && n.closest && (n.closest('#' + ID) || n.closest('#' + LID));
        });
        if (ours) return;
        queued = true;
        requestAnimationFrame(() => {
          queued = false;
          observer.disconnect();
          try { paint(); } catch (e) { }
          observer.observe(document.documentElement,
                           { childList: true, subtree: true });
        });
      });
      observer.observe(document.documentElement,
                       { childList: true, subtree: true });
    } catch (e) { }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
