/* The extension's half of the button's host contract.
 *
 * Runs immediately before bubble.js, in the same isolated world, so simply
 * assigning `window.__sushiHost` here is enough for the button to find it —
 * no page-world injection, and the page can neither see the button's wiring
 * nor pretend to be it.
 *
 * `always` is off: this is loaded into every tab the user opens, so the
 * button uses its own heuristic about what looks like a job page. In Sushi's
 * browser it is on, because there you arrived by asking for a job.
 */
window.__sushiHost = {
  always: false,

  send(payload) {
    return new Promise((resolve, reject) => {
      let settled = false;
      try {
        chrome.runtime.sendMessage({ kind: 'sushi-fill', payload }, (reply) => {
          settled = true;
          // A service worker that was asleep, or an extension that has just
          // been reloaded, answers with lastError rather than throwing.
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
            return;
          }
          if (!reply || !reply.ok) {
            reject(new Error((reply && reply.error) || 'Sushi did not answer'));
            return;
          }
          resolve(reply);
          // The filling happens in Sushi's own window, so this is the last
          // thing this page can honestly say about it.
          try {
            window.__sushiBubble.set('done', 'Sent to Sushi — filling in its window');
          } catch (e) { }
        });
      } catch (err) {
        reject(err);
      }
      setTimeout(() => {
        if (!settled) reject(new Error('Sushi did not answer'));
      }, 8000);
    });
  }
};
