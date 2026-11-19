/* Carries a button press from the page to the app.
 *
 * The content script cannot reach 127.0.0.1 itself — the page's own origin
 * governs its fetches, and a job board is not allowed to talk to localhost.
 * The service worker is, because the extension declares that host. So this
 * is a relay and nothing more: no state, no storage of its own beyond the
 * settings below, nothing read back into the page except whether the app
 * took the job.
 */
importScripts('config.js');

const SETTINGS = async () => {
  // config.js is written by Sushi with this machine's port and token. The
  // options page can override both, for the case where the app was rebuilt
  // onto a different port after the extension was loaded.
  const saved = await chrome.storage.local.get(['port', 'token']);
  return {
    port: saved.port || SUSHI_CONFIG.port,
    token: saved.token || SUSHI_CONFIG.token
  };
};

const call = async (path, options = {}) => {
  const { port, token } = await SETTINGS();
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Sushi-Token': token,
      ...(options.headers || {})
    }
  });
  let body = {};
  try { body = await res.json(); } catch (e) { }
  if (!res.ok) {
    throw new Error(body.error || `Sushi answered ${res.status}`);
  }
  return body;
};

chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (!message || message.kind !== 'sushi-fill') return false;

  call('/fill', { method: 'POST', body: JSON.stringify(message.payload || {}) })
    .then(() => respond({ ok: true }))
    .catch((err) => respond({
      ok: false,
      // The only failure worth wording carefully: the app not being open is
      // what this will usually be, and "Failed to fetch" does not say so.
      error: /fetch/i.test(String(err && err.message))
        ? 'Sushi is not running'
        : String(err && err.message || err)
    }));

  return true;   // keep the channel open for the async respond
});

// Used by the options page's Test button.
chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (!message || message.kind !== 'sushi-ping') return false;
  call('/ping')
    .then((body) => respond({ ok: true, body }))
    .catch((err) => respond({ ok: false, error: String(err && err.message || err) }));
  return true;
});
