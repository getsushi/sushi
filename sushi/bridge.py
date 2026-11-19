"""The door the Chrome extension knocks on.

The button in :mod:`sushi.bubble` works inside the browser Sushi drives
because Python is already on the other end of a CDP connection. In the
user's *own* Chrome there is no such connection, so the extension needs
somewhere to send a click: a small HTTP server on the loopback interface,
started with the window and stopped with it.

Three things make that safe enough to leave running:

- **It only listens to this machine.** Bound to 127.0.0.1, so nothing off
  the box can reach it at all.
- **It needs a token.** Any page you visit can POST to 127.0.0.1 — browsers
  stop it *reading* the answer, not sending the request — so without a
  secret, a hostile page could make Sushi open an application form of its
  choosing and type the user's details into it. The token is generated here,
  written into the generated extension, and never shown in a page.
- **It does one thing.** The only route that acts takes a URL and hands it
  to the app, which then asks the same questions it would have asked if the
  URL had been pasted into Manual apply. There is no route that reads
  anything back out.
"""
from __future__ import annotations

import json
import logging
import secrets
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

#: Settings keys. The port is remembered so a rebuild of the extension and a
#: restart of the app agree about where to knock.
TOKEN_KEY = "bridge_token"
PORT_KEY = "bridge_port"

#: First choice, then a few neighbours if something else already has it.
DEFAULT_PORT = 8765
PORT_TRIES = 8

#: The extension's sources, minus the button itself, which is copied in from
#: ``data/bubble.js`` so there is one of it rather than two.
SOURCE_DIR = Path(__file__).resolve().parent / "data" / "extension"

_server: ThreadingHTTPServer | None = None
_thread: threading.Thread | None = None
_on_fill: Callable[[str, str], None] | None = None


def token() -> str:
    """This machine's bridge token, generated on first use."""
    from . import storage

    existing = storage.get_setting(TOKEN_KEY, "")
    if existing:
        return existing
    fresh = secrets.token_urlsafe(24)
    storage.set_setting(TOKEN_KEY, fresh)
    return fresh


def new_token() -> str:
    """Throw the current token away and issue another.

    For the one case that matters: the extension folder has been copied
    somewhere it should not have been, so the old token has to stop working.
    The extension must be rebuilt afterwards.
    """
    from . import storage

    storage.set_setting(TOKEN_KEY, "")
    return token()


def port() -> int:
    """The port the bridge is on, or the one it will try first."""
    from . import storage

    try:
        return int(storage.get_setting(PORT_KEY, "") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def running() -> bool:
    return _server is not None


class _Handler(BaseHTTPRequestHandler):
    # The default logs every request to stderr, which in a GUI app means a
    # console nobody is reading, or no console at all.
    def log_message(self, fmt: str, *args) -> None:
        log.debug("bridge: " + fmt, *args)

    def _reply(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        # The extension has host permissions for this origin, so this is
        # belt and braces — but a preflight that fails is an opaque error in
        # a service worker nobody can see, and this costs one line.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Sushi-Token")
        self.end_headers()
        self.wfile.write(raw)

    def _authorised(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-Sushi-Token", ""), token())

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._reply(204, {})

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/ping":
            self._reply(404, {"error": "no such route"})
            return
        if not self._authorised():
            self._reply(403, {"error": "bad token"})
            return
        self._reply(200, {"ok": True, "app": "sushi"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/fill":
            self._reply(404, {"error": "no such route"})
            return
        if not self._authorised():
            # Deliberately the same answer whether the token is missing or
            # wrong: a page probing for this learns nothing either way.
            self._reply(403, {"error": "bad token"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (TypeError, ValueError):
            self._reply(400, {"error": "unreadable request"})
            return
        url = str(payload.get("url") or "")
        if not url.startswith(("http://", "https://")):
            self._reply(400, {"error": "not a link"})
            return
        handler = _on_fill
        if handler is None:
            self._reply(503, {"error": "Sushi is not ready"})
            return
        try:
            handler(url, str(payload.get("title") or ""))
        except Exception as exc:
            log.warning("bridge: handler failed: %s", exc)
            self._reply(500, {"error": "Sushi could not take that"})
            return
        self._reply(200, {"ok": True})


def start(on_fill: Callable[[str, str], None]) -> int:
    """Start listening. Returns the port, or 0 if it could not.

    Never raises: the app works without this, and a port conflict is not a
    reason to refuse to open.
    """
    global _server, _thread, _on_fill

    _on_fill = on_fill
    if _server is not None:
        return _server.server_address[1]

    from . import storage

    first = port()
    for candidate in range(first, first + PORT_TRIES):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), _Handler)
        except OSError:
            continue
        server.daemon_threads = True
        _server = server
        _thread = threading.Thread(target=server.serve_forever,
                                   name="sushi-bridge", daemon=True)
        _thread.start()
        storage.set_setting(PORT_KEY, str(candidate))
        log.info("bridge: listening on 127.0.0.1:%d", candidate)
        return candidate
    log.warning("bridge: no free port in %d-%d", first, first + PORT_TRIES - 1)
    return 0


def stop() -> None:
    global _server, _thread, _on_fill

    _on_fill = None
    server, _server = _server, None
    _thread = None
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass


def build_extension(dest: Path | None = None) -> Path:
    """Write a ready-to-load extension, with this machine's token in it.

    Generated rather than committed, for two reasons: the token belongs to
    this install and nowhere else, and ``bubble.js`` has to be *copied* in
    rather than kept as a second copy that drifts from the one the app's own
    browser uses.
    """
    from .config import DATA_DIR
    from . import bubble

    out = Path(dest) if dest else (DATA_DIR / "extension")
    out.mkdir(parents=True, exist_ok=True)

    for name in ("manifest.json", "host.js", "background.js",
                 "options.html", "options.js"):
        shutil.copyfile(SOURCE_DIR / name, out / name)
    shutil.copyfile(bubble.ASSET, out / "bubble.js")

    (out / "config.js").write_text(
        "// Written by Sushi for this machine. Not for sharing: the token\n"
        "// below is what stops any page you visit from driving the app.\n"
        f"const SUSHI_CONFIG = {json.dumps({'port': port(), 'token': token()}, indent=2)};\n",
        encoding="utf-8")
    log.info("bridge: extension written to %s", out)
    return out
