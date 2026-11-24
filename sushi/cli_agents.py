"""Coding-agent CLIs the user already has, used as Sushi's model.

Claude Code was the first (see :mod:`sushi.claude_code`). The same idea
works for any agent CLI that is installed and signed in: no key to paste, no
card, the user's own subscription. This module adds two more:

- **Codex** (OpenAI's CLI, ``codex exec``), signed in with a ChatGPT plan.
- **OpenCode** (``opencode run``), which fronts dozens of providers, some of
  them free (``opencode/beam-free`` at the time of writing).

Each is a :class:`CliAgent`, which answers the same questions the settings
page and the LLM layer ask of :mod:`sushi.claude_code` — ``available``,
``models``, ``best_model``, ``ask``, ``ensure_running``, ``base_url``,
``describe`` — so anything written against one works with all three.

The prompt always goes in on stdin. Passed as an argument, a CV plus a form
is over Windows' 32,767-character command line and the call fails before it
starts.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)

TIMEOUT = 300


class CliAgentError(RuntimeError):
    """The CLI was missing, not signed in, or answered with nothing usable."""


@dataclass
class CliAgent:
    provider: str            # the Settings.provider value
    name: str                # what the user sees
    exe: str                 # the command on PATH
    prefix: str              # model names are "<prefix><model>"
    model_list: list[str]
    install: str             # the one command that installs it
    login: str               # the one command that signs it in
    site: str
    build: Callable[["CliAgent", str, str], tuple[list[str], str]] = None  # type: ignore[assignment]
    parse: Callable[[subprocess.CompletedProcess, str], str] = None        # type: ignore[assignment]
    PLACEHOLDER_KEY: str = field(default="")

    def __post_init__(self) -> None:
        self.PLACEHOLDER_KEY = self.provider
        # The names the setup guide reads, matching sushi.claude_code's.
        self.NAME, self.INSTALL, self.LOGIN, self.SITE = (
            self.name, self.install, self.login, self.site)
        self._lock = threading.Lock()
        self._base_url: str | None = None

    # --- the questions every runtime answers ---------------------------------

    def path(self) -> str:
        return shutil.which(self.exe) or ""

    def available(self) -> bool:
        return bool(self.path())

    def models(self) -> list[str]:
        """The tool's real catalogue where it has one, else the fallbacks."""
        real = real_models(self.provider)
        return real or [f"{self.prefix}{m}" for m in self.model_list]

    def best_model(self, installed: list[str] | None = None) -> str:
        have = installed if installed is not None else self.models()
        return have[0] if have else f"{self.prefix}{self.model_list[0]}"

    def base_url(self) -> str:
        return ""        # routed natively; the shim's port is chosen at start-up

    def describe(self) -> str:
        if not self.available():
            return (f"{self.name} is not installed here. Install it with "
                    f"“{self.install}”, sign in with “{self.login}”, then "
                    f"reopen Sushi.")
        return (f"{self.name} on this machine, no API key needed. It uses "
                f"your own {self.name} sign-in and limits.")

    def _model(self, model: str) -> str:
        m = (model or "").strip()
        if m.startswith(self.prefix):
            m = m[len(self.prefix):]
        return m or self.model_list[0]

    # --- calling it ----------------------------------------------------------

    def ask(self, prompt: str, system: str = "", model: str = "") -> str:
        exe = self.path()
        if not exe:
            raise CliAgentError(self.describe())
        full = f"{system.strip()}\n\n{prompt}" if system and system.strip() else prompt
        cmd, out_file = self.build(self, exe, self._model(model))
        try:
            proc = subprocess.run(
                cmd, input=full, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=TIMEOUT,
                # Off any project: an agent started inside a repository
                # reads it as context, and may try to edit it.
                cwd=tempfile.gettempdir())
        except subprocess.TimeoutExpired as exc:
            if out_file:
                try:
                    os.unlink(out_file)
                except OSError:
                    pass
            raise CliAgentError(f"{self.name} did not answer within {TIMEOUT}s.") from exc
        try:
            text = self.parse(proc, out_file).strip()
        finally:
            if out_file:
                try:
                    os.unlink(out_file)
                except OSError:
                    pass
        if not text:
            reason = (proc.stderr or proc.stdout or "").strip()[-300:]
            if any(w in reason.lower() for w in ("login", "auth", "sign in", "unauthor")):
                raise CliAgentError(
                    f"{self.name} is not signed in. Run “{self.login}” in a "
                    f"terminal, then try again.")
            raise CliAgentError(f"{self.name} failed: {reason or 'no answer'}")
        return text

    # --- the OpenAI-shaped front door, for the browser agent -----------------

    def ensure_running(self) -> str:
        from . import claude_code
        with self._lock:
            if self._base_url:
                return self._base_url
            if not self.available():
                raise CliAgentError(self.describe())
            import socketserver
            handler = claude_code.handler_for(self.ask, self.models,
                                              self.best_model, self.provider)

            class Server(socketserver.ThreadingTCPServer):
                daemon_threads = True
                allow_reuse_address = True

            server = Server(("127.0.0.1", 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True,
                             name=f"sushi-{self.provider}").start()
            self._base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
            log.info("%s: OpenAI-compatible front door at %s",
                     self.provider, self._base_url)
            return self._base_url


# --- Codex ------------------------------------------------------------------

def _codex_build(agent: CliAgent, exe: str, model: str) -> tuple[list[str], str]:
    fd, out = tempfile.mkstemp(prefix="sushi-codex-", suffix=".txt")
    os.close(fd)
    cmd = [exe, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
           "--color", "never", "--output-last-message", out]
    if model and model != "default":
        cmd += ["-m", model]
    from .claude_code import reasoning_effort
    effort = {"max": "high", "xhigh": "high"}.get(reasoning_effort(), reasoning_effort())
    if effort:
        cmd += ["-c", f"model_reasoning_effort={effort}"]
    cmd.append("-")          # the prompt, from stdin
    return cmd, out


def _codex_parse(proc: subprocess.CompletedProcess, out_file: str) -> str:
    try:
        with open(out_file, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


CODEX = CliAgent(
    provider="codex", name="Codex", exe="codex", prefix="codex:",
    model_list=["default", "gpt-5-codex", "gpt-5"],
    install="npm install -g @openai/codex", login="codex login",
    site="https://developers.openai.com/codex/cli",
    build=_codex_build, parse=_codex_parse)


# --- OpenCode ---------------------------------------------------------------

def _opencode_build(agent: CliAgent, exe: str, model: str) -> tuple[list[str], str]:
    cmd = [exe, "run", "--format", "json"]
    if model and model != "default":
        cmd += ["-m", model]
    return cmd, ""


def _opencode_parse(proc: subprocess.CompletedProcess, _out: str) -> str:
    """Join the text parts of the JSON event stream; raise on its error."""
    texts, error = [], ""
    for line in (proc.stdout or "").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "text":
            texts.append(str((ev.get("part") or {}).get("text") or ""))
        elif ev.get("type") == "error":
            error = str((ev.get("error") or {}).get("message") or "error")
    if not texts and error:
        first = error.split("\n", 1)[0][:200]
        raise CliAgentError(f"OpenCode: {first}")
    return "".join(texts)


OPENCODE = CliAgent(
    provider="opencode", name="OpenCode", exe="opencode", prefix="opencode:",
    model_list=["opencode/beam-free", "default", "opencode/claude-sonnet-5"],
    install="npm install -g opencode-ai", login="opencode auth login",
    site="https://opencode.ai",
    build=_opencode_build, parse=_opencode_parse)


AGENTS = {"codex": CODEX, "opencode": OPENCODE}


def runtime_for(provider: str):
    """The keyless CLI behind a provider: Claude Code, Codex, OpenCode."""
    if provider == "claude_code":
        from . import claude_code
        return claude_code
    return AGENTS.get(provider)


# --- what is really there: models and readiness ------------------------------
#
# The lists above are fallbacks. Each tool is asked for its real catalogue
# where it has one, cached for the session (asking costs a process launch):
#
# - OpenCode: ``opencode models``, its whole catalogue, free ones first.
# - Codex: its own cache of the models the signed-in account can use
#   (~/.codex/models_cache.json), the ones it lists, in its order.
# - Claude Code: has no list command; its --help names the aliases, which
#   always point at the latest of each family.
from pathlib import Path as _Path

_MODEL_CACHE: dict[str, list[str]] = {}


def _opencode_models() -> list[str]:
    exe = shutil.which("opencode")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "models"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    names = [l.strip() for l in out.splitlines() if "/" in l and " " not in l.strip()]
    free = sorted((n for n in names if "free" in n.lower()),
                  key=lambda n: (not n.startswith("opencode/"), n))
    return free + [n for n in names if n not in free]


def _codex_models() -> list[str]:
    cache = _Path.home() / ".codex" / "models_cache.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = [m for m in data.get("models") or []
            if isinstance(m, dict) and m.get("slug") and m.get("visibility") != "hide"]
    rows.sort(key=lambda m: m.get("priority") or 99)
    return [m["slug"] for m in rows]


def _claude_models() -> list[str]:
    import re
    exe = shutil.which("claude")
    aliases: list[str] = []
    if exe:
        try:
            out = subprocess.run([exe, "--help"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=20,
                                 stdin=subprocess.DEVNULL).stdout
            m = re.search(r"--model <model>(.{0,400})", out, re.S)
            aliases = re.findall(r"'([a-z][a-z0-9-]*)'", m.group(1)) if m else []
        except (subprocess.TimeoutExpired, OSError):
            pass
    order = ["sonnet", "opus", "fable", "haiku"]
    found = list(dict.fromkeys(aliases + ["haiku"]))
    found.sort(key=lambda a: order.index(a) if a in order else len(order))
    return found


def real_models(provider: str, refresh: bool = False) -> list[str]:
    """The models this tool really offers here, prefixed for Sushi."""
    if refresh or provider not in _MODEL_CACHE:
        fetch = {"opencode": _opencode_models, "codex": _codex_models,
                 "claude_code": _claude_models}.get(provider)
        _MODEL_CACHE[provider] = fetch() if fetch else []
    prefix = {"opencode": "opencode:", "codex": "codex:",
              "claude_code": "claude-code:"}.get(provider, "")
    return [f"{prefix}{m}" for m in _MODEL_CACHE[provider]]


def _signed_in(provider: str) -> bool:
    """Whether the tool holds a sign-in. Existence only; never read."""
    home = _Path.home()
    if provider == "claude_code":
        import sys as _sys
        if _sys.platform == "darwin":
            return True      # kept in the Keychain, not a file; "Test it" checks
        return (home / ".claude" / ".credentials.json").exists() or bool(
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
    if provider == "codex":
        return (home / ".codex" / "auth.json").exists() or bool(os.environ.get("OPENAI_API_KEY"))
    return True          # OpenCode's free models need no account


def status(provider: str) -> tuple[str, str]:
    """(state, words): "ready", "signin", "missing" or "nomodel"."""
    if provider == "ollama":
        from . import local_llm
        if not local_llm.installed():
            return "missing", "Not installed on this PC yet."
        if not local_llm.models():
            from . import setup_agent
            if local_llm.models_on_disk():
                threading.Thread(target=setup_agent.start_ollama, daemon=True).start()
                return "nomodel", "Installed with a model; starting Ollama…"
            return "nomodel", "Installed. Download a model to use it."
        return "ready", f"Ready · {len(local_llm.models())} model(s) on this PC."
    rt = runtime_for(provider)
    if rt is None:
        return "", ""
    if not rt.available():
        return "missing", "Not installed on this PC yet."
    if not _signed_in(provider):
        return "signin", "Installed. Sign in to use it."
    n = len(real_models(provider))
    head = ("Installed · free models ready" if provider == "opencode"
            else "Installed and signed in")
    return "ready", head + (f" · {n} models" if n else "") + "."
