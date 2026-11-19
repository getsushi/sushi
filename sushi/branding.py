"""Make the application browser look like Sushi's, not like a robot's.

When Sushi opens Chromium to fill a form, what the user sees is a bare browser
window with nothing on it that says where it came from — and if browser-use's
demo mode is ever switched on, what it *does* say is browser-use. Either way
the window that is typing the user's name into a form should be identifiably
Sushi's.

So one init script is registered on the session before any page loads. It runs
in every frame of every page and does three things, all cosmetic and all
removable:

- stamps the tab: a 🍣 favicon and a ``Sushi · `` title prefix, which is what
  the taskbar and the tab strip actually show;
- draws a small fixed badge in the top-right corner naming the app and the
  state it is in (filling, or waiting for you);
- leaves a ``window.__sushi`` hook so the state can be updated later without
  re-injecting.

It is deliberately defensive: wrapped in try/catch, keyed by element id so it
cannot stack up over navigations, and it never touches the form.
"""
from __future__ import annotations

import json

# The badge's palette is the app's: the dark band's own ground, cream
# text, a sage dot at rest and the accent while it is working.
_BADGE_BG = "#201F1D"
_BADGE_INK = "#FFFBF2"
_BADGE_DIM = "rgba(255,251,242,.62)"
_ACCENT = "#87A878"
_HOT = "#B68235"

_FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E"
    "%3Ctext y='.9em' font-size='90'%3E%F0%9F%8D%A3%3C/text%3E%3C/svg%3E"
)


def init_script(state: str = "Filling your application", hot: bool = True) -> str:
    """The script to register with ``BrowserSession._cdp_add_init_script``."""
    return f"""
(() => {{
  if (window.__sushi) return;
  const BADGE = 'sushi-badge-9f3a';

  // Set only if different. Assigning an identical textContent still replaces
  // the text node, which is a childList mutation, which wakes the observer
  // below, which repaints — a loop that crashes the renderer outright. Found
  // exactly that in QA before it ever reached a real application form.
  const setText = (node, value) => {{
    if (node && node.textContent !== value) node.textContent = value;
  }};
  const setStyle = (node, prop, value) => {{
    if (node && node.style[prop] !== value) node.style[prop] = value;
  }};

  const paint = (text, hot) => {{
    try {{
      if (!document.body) return;
      // One pill, not two. The Fill button lives in the same corner and
      // says the same kind of thing, so when it is on the page it carries
      // the state and this stands down — without setting its text. This
      // paint runs from a MutationObserver, which on a form being filled
      // is constantly; pushing the badge's own state in from here put
      // "Applying to Kestrel" back over every step the filler reported.
      if (window.__sushiBubble) {{
        const mine = document.getElementById(BADGE);
        if (mine && mine.parentNode) mine.parentNode.removeChild(mine);
        return;
      }}
      let el = document.getElementById(BADGE);
      if (!el) {{
        el = document.createElement('div');
        el.id = BADGE;
        el.setAttribute('aria-hidden', 'true');
        el.style.cssText = [
          'position:fixed', 'z-index:2147483647', 'right:16px', 'top:16px',
          'display:flex', 'align-items:center', 'gap:9px',
          'padding:9px 14px 9px 11px', 'border-radius:999px',
          'background:{_BADGE_BG}', 'color:{_BADGE_INK}',
          'box-shadow:0 10px 26px rgba(0,0,0,.28)',
          'font:500 13px/1.2 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif',
          'letter-spacing:-.01em', 'pointer-events:none', 'user-select:none'
        ].join(';');
        el.innerHTML =
          '<span style="font-size:15px">\\u{{1F363}}</span>' +
          '<span style="font-weight:600">Sushi</span>' +
          '<span style="width:1px;height:12px;background:rgba(255,251,242,.22)"></span>' +
          '<span id="' + BADGE + '-dot" style="width:7px;height:7px;border-radius:50%"></span>' +
          '<span id="' + BADGE + '-txt" style="color:{_BADGE_DIM}"></span>';
        document.body.appendChild(el);
      }}
      setStyle(document.getElementById(BADGE + '-dot'), 'background',
               hot ? '{_HOT}' : '{_ACCENT}');
      setText(document.getElementById(BADGE + '-txt'), text);
    }} catch (e) {{ /* a badge is never worth breaking a page over */ }}
  }};

  const stamp = () => {{
    try {{
      // The tab strip and the taskbar read these, so this is the part that
      // actually replaces "whatever this window is" with "Sushi".
      const title = document.title || '';
      if (!title.startsWith('Sushi \\u00B7 ')) {{
        document.title = 'Sushi \\u00B7 ' + (title || 'Application');
      }}
      let link = document.querySelector("link[rel~='icon'][data-sushi]");
      if (!link) {{
        document.querySelectorAll("link[rel~='icon']").forEach(n => n.remove());
        link = document.createElement('link');
        link.rel = 'icon';
        link.setAttribute('data-sushi', '1');
        document.head && document.head.appendChild(link);
      }}
      if (link) link.href = {json.dumps(_FAVICON)};
    }} catch (e) {{ }}
  }};

  window.__sushi = {{
    state: {json.dumps(state)},
    hot: {str(bool(hot)).lower()},
    set(text, hot) {{ this.state = text; this.hot = !!hot; paint(text, this.hot); }},
    render() {{ stamp(); paint(this.state, this.hot); }}
  }};

  const start = () => {{
    window.__sushi.render();
    // Single-page ATS forms rewrite the title and the head on every route
    // change, so a one-shot stamp lasts about two seconds on Workday.
    //
    // Three guards, all of them load-bearing: mutations inside our own badge
    // are ignored, a re-entrancy flag stops a render triggering a render, and
    // the work is deferred to an animation frame so a page doing a thousand
    // DOM writes costs one repaint rather than a thousand.
    try {{
      let queued = false;
      const observer = new MutationObserver(records => {{
        if (queued) return;
        const ours = records.every(r => {{
          const n = r.target;
          return n && n.closest && n.closest('#' + BADGE);
        }});
        if (ours) return;
        queued = true;
        requestAnimationFrame(() => {{
          queued = false;
          observer.disconnect();
          try {{ window.__sushi.render(); }} catch (e) {{ }}
          observer.observe(document.documentElement,
                           {{ childList: true, subtree: true }});
        }});
      }});
      observer.observe(document.documentElement,
                       {{ childList: true, subtree: true }});
    }} catch (e) {{ }}
  }};

  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', start);
  }} else {{
    start();
  }}
}})();
"""


def set_state_script(text: str, hot: bool = False) -> str:
    """A one-liner to re-label the badge on an already-loaded page."""
    return (f"window.__sushi && window.__sushi.set({json.dumps(text)}, "
            f"{str(bool(hot)).lower()});")
