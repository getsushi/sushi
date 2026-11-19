"""The Claude Code CLI on this machine, used as Sushi's model.

Claude Code is already installed and already signed in, which makes it the
one backend that needs no API key, no card and no signup. Two things have to
be bridged for the rest of the app to use it:

- **Sushi's own calls** — reading a CV, writing a letter, answering one
  question — go straight through :func:`ask`, which runs the CLI headlessly
  and returns the text.
- **browser-use speaks the OpenAI protocol and nothing else.** So the CLI is
  fronted by a loopback HTTP server that implements
  ``POST /v1/chat/completions``. Started on demand by :func:`ensure_running`
  rather than at launch: it costs nothing until something needs it.

What it costs, measured here rather than guessed, asking only for "OK":

    first call   $0.282   28,186 cache-creation tokens
    later calls  $0.0142  28,186 cache-read tokens, ~1.5s API + ~3s spawn

Every invocation carries Claude Code's own harness — twenty-eight thousand
tokens of system prompt and tooling — whatever you ask it. That is fine for
the handful of short calls Sushi makes directly. Across a browser agent's
twenty-odd steps it is roughly $0.36 an application and minutes of process
spawning, which is worse than a cheap hosted model on both counts. Use it
for the former; prefer a hosted model for the latter.

And the limit that matters for shipping: this needs Claude Code installed
and signed in, so every *user* would need their own. It unblocks
development. It does not solve distribution.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading

log = logging.getLogger(__name__)


class ClaudeCodeError(RuntimeError):
    """The CLI was missing, refused, or answered with something unusable."""


#: The OpenAI client insists on a key; the CLI ignores it entirely.
PLACEHOLDER_KEY = "claude-code"

#: Model names are ``claude-code:<alias>``; the alias is what the CLI takes
#: for ``--model``. Keeping the prefix means a glance at Settings says which
#: backend is in force, not just which model.
PREFIX = "claude-code:"
ALIASES = ("sonnet", "opus", "haiku")
DEFAULT_ALIAS = "sonnet"

#: Generous: a cold call pays to load the harness before it answers a word.
#: Still bounded, so a wedged CLI cannot hang a worker thread forever.
TIMEOUT = 300

_server_lock = threading.Lock()
_base_url: str | None = None


def available() -> bool:
    """Whether the Claude Code CLI is installed and on PATH."""
    return shutil.which("claude") is not None


def best_model(installed: list[str] | None = None) -> str:
    """The alias to prefer. Signature matches :mod:`sushi.local_llm`.

    The UI treats every keyless runtime the same way — list what is there,
    pick the best — so both modules must answer the same calls.
    """
    have = installed if installed is not None else models()
    for want in (f"{PREFIX}{a}" for a in ALIASES):
        if want in have:
            return want
    return have[0] if have else f"{PREFIX}{DEFAULT_ALIAS}"


def models() -> list[str]:
    return [f"{PREFIX}{a}" for a in ALIASES]


def _alias(model: str) -> str:
    """The CLI's model name, from whatever Sushi was configured with."""
    name = (model or "").strip()
    if name.startswith(PREFIX):
        name = name[len(PREFIX):]
    return name or DEFAULT_ALIAS


def _run(prompt: str, model: str = "", timeout: int = TIMEOUT) -> dict:
    """One headless call, returning the CLI's JSON envelope."""
    exe = shutil.which("claude")
    if not exe:
        raise ClaudeCodeError(
            "The Claude Code CLI is not installed, or not on PATH.")
    cmd = [exe, "-p", "--output-format", "json"]
    alias = _alias(model)
    if alias:
        cmd += ["--model", alias]
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCodeError(
            f"Claude Code did not answer within {timeout}s.") from exc
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        data = None
    if proc.returncode != 0 or (isinstance(data, dict) and data.get("is_error")):
        raise ClaudeCodeError(_failure(proc, data))
    if data is None:
        raise ClaudeCodeError(
            f"Claude Code returned something that was not JSON: "
            f"{proc.stdout[:200]!r}")
    return data


def _failure(proc: subprocess.CompletedProcess, data: dict | None) -> str:
    """The reason a call failed, in the CLI's own words.

    A failed call still prints its JSON envelope, with the reason in
    ``result`` at the very end — so the first few hundred characters of
    stdout are usage counters and the reason itself is cut off.
    """
    reason = ""
    if isinstance(data, dict):
        reason = str(data.get("result") or "").strip()
    if not reason:
        reason = (proc.stderr or proc.stdout or "").strip()[:300]
    if "authenticate" in reason.lower() or "/login" in reason:
        return (f"Claude Code is not signed in ({reason}). Run "
                f"'claude auth login' in a terminal, then try again.")
    return f"Claude Code failed: {reason or 'no output'}"


def ask(prompt: str, system: str = "", model: str = "") -> str:
    """One answer, as text.

    The system prompt is folded into the message: print mode has no separate
    system channel, and ``--append-system-prompt`` would only add to the
    harness we are already paying for.
    """
    full = f"{system.strip()}\n\n{prompt}" if system and system.strip() else prompt
    data = _run(full, model)
    text = (data.get("result") or "").strip()
    cost = data.get("total_cost_usd")
    if cost is not None:
        log.info("claude code: $%.4f, %sms", cost, data.get("duration_api_ms", "?"))
    if not text:
        raise ClaudeCodeError("Claude Code returned an empty answer.")
    return text


# --- the OpenAI-shaped front door -------------------------------------------

def _flatten(messages: list[dict]) -> tuple[str, str]:
    """Turn an OpenAI message list into (system, prompt).

    Roles are labelled rather than dropped: a multi-turn agent conversation
    collapsed into undifferentiated text loses who said what, and the model
    then cannot tell its own previous answers from the user's instructions.
    """
    system_parts, lines = [], []
    for msg in messages or []:
        role = (msg.get("role") or "user").lower()
        content = msg.get("content")
        if isinstance(content, list):
            # Multimodal content: keep the text, drop the images — the CLI
            # takes a prompt on stdin and nothing else.
            content = " ".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and part.get("type") == "text")
        content = (content or "").strip()
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
        elif role == "assistant":
            lines.append(f"Assistant: {content}")
        elif role == "tool":
            lines.append(f"Tool result: {content}")
        else:
            lines.append(f"User: {content}")
    return "\n\n".join(system_parts), "\n\n".join(lines)


def _completion(text: str, model: str) -> dict:
    """An OpenAI chat-completion envelope around one answer."""
    import time as _time

    return {
        "id": f"chatcmpl-claude-code-{int(_time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(_time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        # The CLI reports its own usage; callers only need the shape.
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _handler_class():
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError,
                    ConnectionAbortedError):
                # The caller gave up while the CLI was still thinking. A
                # browser-use step that times out does this every time, and
                # socketserver prints the whole traceback for it — twenty
                # lines of stack in the log for a client that simply left,
                # which reads as a crash in the shim and is not one.
                pass

        def do_GET(self):          # noqa: N802
            if self.path.rstrip("/").endswith("/models"):
                self._send({"object": "list", "data": [
                    {"id": m, "object": "model", "owned_by": "claude-code"}
                    for m in models()]})
                return
            self._send({"error": {"message": "not found"}}, 404)

        def do_POST(self):         # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._send({"error": {"message": "invalid JSON"}}, 400)
                return
            if not self.path.rstrip("/").endswith("/chat/completions"):
                self._send({"error": {"message": "not found"}}, 404)
                return

            model = payload.get("model") or best_model()
            system, prompt = _flatten(payload.get("messages") or [])
            if payload.get("response_format") or payload.get("tools"):
                # browser-use asks for JSON. Say so explicitly: the CLI is a
                # coding assistant and will otherwise wrap an answer in
                # explanation and fenced code.
                system = (system + "\n\nReply with JSON only. No prose, no "
                                   "code fences, no explanation.").strip()
            try:
                text = ask(prompt, system=system, model=model)
            except ClaudeCodeError as exc:
                self._send({"error": {"message": str(exc)}}, 502)
                return
            self._send(_completion(_unfence(text), model))

        def log_message(self, *_a):
            """Quiet: this is a private loopback server, not a web host."""

    return Handler


def _unfence(text: str) -> str:
    """Strip a ```json fence if the CLI wrapped the answer in one."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    t = t.split("\n", 1)[-1] if "\n" in t else t
    if t.rstrip().endswith("```"):
        t = t.rstrip()[:-3]
    return t.strip()


def ensure_running() -> str:
    """Start the loopback server if needed; return its base URL.

    Bound to 127.0.0.1 on an ephemeral port: it proxies to a signed-in CLI,
    so it must not be reachable from anywhere but this machine.
    """
    global _base_url
    with _server_lock:
        if _base_url:
            return _base_url
        if not available():
            raise ClaudeCodeError(
                "The Claude Code CLI is not installed, or not on PATH.")
        import socketserver

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        server = Server(("127.0.0.1", 0), _handler_class())
        threading.Thread(target=server.serve_forever, daemon=True,
                         name="sushi-claude-code").start()
        _base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        log.info("claude code: OpenAI-compatible front door at %s", _base_url)
        return _base_url


def base_url() -> str:
    """What to store as this runtime's endpoint: nothing.

    The other keyless runtime — Ollama — is spoken to over an
    OpenAI-compatible URL, so the settings page stores one. This one is
    routed natively in :mod:`sushi.llm`, and the loopback shim it puts up
    for browser-use is on an ephemeral port chosen at start-up: a URL
    written down today names a port that will not exist tomorrow.
    Whoever needs the shim calls :func:`ensure_running` when they need it.

    Both runtimes answer this, because the settings page asks blind — and
    asking for a ``BASE_URL`` attribute that only one of them had is what
    made choosing the recommended provider raise AttributeError.
    """
    return ""


def describe() -> str:
    """One line for the UI."""
    if not available():
        return ("Claude Code is not installed on this machine. Install it "
                "and sign in to use it without an API key.")
    return ("Claude Code on this machine, no API key needed. About 1.4p a "
            "call once warm; good for reading a CV and writing answers, "
            "slow and costly for driving the browser agent.")
