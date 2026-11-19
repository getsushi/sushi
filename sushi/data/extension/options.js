/* The options page: where the app is, and whether it is answering. */

const $ = (id) => document.getElementById(id);
const said = (text) => { $('said').textContent = text; };

const load = async () => {
  const saved = await chrome.storage.local.get(['port', 'token']);
  $('port').value = saved.port || SUSHI_CONFIG.port || '';
  $('token').value = saved.token || SUSHI_CONFIG.token || '';
};

$('save').addEventListener('click', async () => {
  const port = parseInt($('port').value, 10);
  const token = $('token').value.trim();
  if (!port || !token) {
    said('Both a port and a token are needed.');
    return;
  }
  await chrome.storage.local.set({ port, token });
  said('Saved.');
});

$('test').addEventListener('click', () => {
  said('Asking…');
  chrome.runtime.sendMessage({ kind: 'sushi-ping' }, (reply) => {
    if (chrome.runtime.lastError) {
      said(chrome.runtime.lastError.message);
      return;
    }
    if (reply && reply.ok) {
      said('Sushi answered. The button will work on job pages.');
    } else {
      const why = (reply && reply.error) || 'no answer';
      said(/not running|fetch/i.test(why)
        ? 'No answer. Is Sushi open? It only listens while the window is.'
        : `No: ${why}`);
    }
  });
});

load();
