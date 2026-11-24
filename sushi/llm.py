"""One LLM interface over Anthropic, OpenAI and Azure OpenAI.

The app asks for text or for JSON; which vendor answers is a user setting. The
rest of the codebase never imports a vendor SDK directly, so adding a provider
means editing this file only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .claude_code import ClaudeCodeError
from .config import Settings


#: Status codes that mean "your credentials", not "your request".
_AUTH_CODES = ("401", "403", "invalid_api_key", "authenticationerror",
               "invalid subscription key", "access denied")


def explain(exc: Exception, settings: Any = None) -> str:
    """Turn a vendor SDK exception into something worth showing a person.

    The raw text is a JSON dump of someone else's error envelope. A 401 in
    particular is not a bug in whatever the user just clicked — it means
    nothing model-backed will work until the credentials are fixed — and
    saying so once, plainly, beats showing the same wall of JSON from the
    cover-letter button, the CV reader, the chat and the form filler.
    """
    raw = f"{type(exc).__name__}: {exc}"
    low = raw.lower()
    if any(code in low for code in _AUTH_CODES):
        where = ""
        if settings is not None:
            src = getattr(settings, "key_source", "")
            name = Path(src).name if src else ""
            where = f" (currently from {name})" if name else ""
        return (
            "Your model credentials were refused (401). Nothing that needs "
            "the model will work until this is fixed: reading a CV, writing a "
            "letter, filling a form, the chat. Check the key and the "
            f"endpoint in Settings → Model{where}. If the key was rotated or "
            "the deployment moved, paste the new one there.")
    if "429" in low or "rate limit" in low:
        return ("The model is rate-limiting us. Wait a moment and try again; "
                "if it keeps happening, your deployment's quota is the limit.")
    if "timeout" in low or "timed out" in low:
        return "The model did not answer in time. Try again."
    return raw


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._client: Any = None

    @property
    def enabled(self) -> bool:
        return self.s.llm_enabled

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        p = self.s.provider
        try:
            if p in ("claude_code", "codex", "opencode"):
                from .cli_agents import runtime_for
                self._client = runtime_for(p)
            elif p == "anthropic":
                from anthropic import Anthropic
                self._client = Anthropic(api_key=self.s.api_key)
            elif p == "openai":
                from openai import OpenAI
                kwargs: dict[str, Any] = {"api_key": self.s.api_key}
                if self.s.openai_base_url:  # OpenAI-compatible endpoint (e.g. Azure /openai/v1)
                    kwargs["base_url"] = self.s.openai_base_url
                self._client = OpenAI(**kwargs)
            elif p == "azure":
                from openai import AzureOpenAI
                self._client = AzureOpenAI(
                    api_key=self.s.api_key,
                    azure_endpoint=self.s.azure_base_url,
                    api_version="2024-10-21",
                )
            else:
                raise LLMError(f"Unknown provider {p!r}")
        except ImportError as e:
            raise LLMError(f"The {p} SDK is not installed: {e}") from e
        return self._client

    #: The quick model, for short answers where waiting is the cost: a form
    #: question answered in a word or a number does not need the big one,
    #: and on the CLI it came back in seconds rather than half a minute.
    FAST = {"claude_code": "claude-code:haiku", "anthropic": "claude-haiku-5-5",
            "opencode": "opencode:opencode/beam-free"}

    def fast_model(self) -> str:
        """The quick-answer model: the user's choice, else the default.

        "same" means always the main model (returns "")."""
        from . import storage
        chosen = storage.get_setting("fast_model", "").strip()
        if chosen == "same":
            return ""
        # Only a model this provider can run. The setting outlives a switch
        # of provider, and an OpenCode name sent to Claude Code (or to the
        # Anthropic API) failed every quick answer.
        prefix = {"claude_code": "claude-code:", "codex": "codex:",
                  "opencode": "opencode:"}.get(self.s.provider)
        known = ("claude-code:", "codex:", "opencode:")
        fits = (chosen.startswith(prefix) if prefix
                else not chosen.startswith(known))
        return (chosen if chosen and fits else "") or self.FAST.get(self.s.provider, "")

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 4000,
                 temperature: float = 0.2, fast: bool = False) -> str:
        """Plain text completion."""
        if not self.enabled:
            raise LLMError("No API key configured. Open Settings and add one.")
        client = self._get_client()
        try:
            if self.s.provider in ("claude_code", "codex", "opencode"):
                # The CLI takes one prompt and prints one answer; there is no
                # temperature to pass and no budget to set, so both are
                # dropped rather than faked.
                quick = self.fast_model() if fast else None
                return client.ask(prompt, system=system or "You are a careful assistant.",
                                  model=quick or self.s.model)

            if self.s.provider == "anthropic":
                # No temperature: the current SDK and models reject it
                # ("unexpected keyword argument 'temperature'"), which failed
                # every Anthropic call.
                resp = client.messages.create(
                    model=(self.fast_model() or self.s.model) if fast else self.s.model,
                    max_tokens=max_tokens,
                    system=system or "You are a careful assistant.",
                    messages=[{"role": "user", "content": prompt}],
                )
                return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

            model = self.s.azure_deployment if self.s.provider == "azure" else self.s.model
            msgs = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": prompt}]
            return self._openai_chat(client, model, msgs, max_tokens, temperature)
        except LLMError:
            raise
        except ClaudeCodeError as e:
            # Already written for a person — the CLI's own diagnosis beats
            # anything `explain` could make of a RuntimeError.
            raise LLMError(str(e)) from e
        except Exception as e:  # vendor SDKs raise their own hierarchies
            raise LLMError(explain(e, self.s)) from e

    #: Floor for a reasoning model's budget. A request whose whole allowance
    #: goes on reasoning comes back with empty content and no error at all.
    _REASONING_FLOOR = 3000

    @staticmethod
    def _openai_chat(client: Any, model: str, msgs: list, max_tokens: int,
                     temperature: float) -> str:
        """Chat completion that adapts to parameter differences across models.

        Newer models (e.g. gpt-5.x/gpt-6 on Azure's v1 endpoint) require
        ``max_completion_tokens`` and reject a non-default ``temperature``; older
        ones only accept ``max_tokens``. We try the modern shape first and drop
        offending params on a 400 rather than hard-failing.

        Reasoning models fail a second, quieter way. The token budget covers
        *reasoning* as well as output, so a request for 200 tokens can spend
        all 200 thinking and return ``content=""`` with ``finish_reason
        "length"`` and a 200 OK — measured exactly that on gpt-6-astra. The
        caller then shows the user an empty cover letter and no error. So an
        empty-for-length answer is retried once on a much larger budget, and
        an empty answer that survives that is raised rather than returned.
        """
        def call(extra: dict) -> tuple[str, str]:
            resp = client.chat.completions.create(model=model, messages=msgs, **extra)
            choice = resp.choices[0]
            return (choice.message.content or ""), (getattr(choice, "finish_reason", "") or "")

        budget_key = "max_completion_tokens"
        attempts = [
            {"max_completion_tokens": max_tokens, "temperature": temperature},
            {"max_completion_tokens": max_tokens},          # drop temperature
            {"max_tokens": max_tokens, "temperature": temperature},
            {"max_tokens": max_tokens},
        ]
        last: Exception | None = None
        for extra in attempts:
            try:
                text, finish = call(extra)
            except Exception as e:  # noqa: BLE001 - inspect message to decide retry
                last = e
                msg = str(e).lower()
                if "unsupported" in msg or "max_tokens" in msg or "max_completion_tokens" in msg \
                        or "temperature" in msg or "invalid_request" in msg:
                    continue
                # Not a parameter mismatch we can retry around — explain it.
                raise LLMError(explain(e)) from e

            if text.strip():
                return text

            budget_key = "max_completion_tokens" if "max_completion_tokens" in extra else "max_tokens"
            bigger = max(LLM._REASONING_FLOOR, max_tokens * 6)
            if finish == "length" and extra.get(budget_key, 0) < bigger:
                retry = dict(extra)
                retry[budget_key] = bigger
                try:
                    text, finish = call(retry)
                except Exception as e:  # noqa: BLE001
                    last = e
                    continue
                if text.strip():
                    return text
            raise LLMError(
                f"The model returned nothing (finish_reason={finish or 'unknown'}). "
                f"On a reasoning model the token budget covers its thinking too, so "
                f"try again, or raise the limit for this request.")
        raise LLMError(explain(last) if last else
                       "The model did not answer.") from last

    def complete_json(self, prompt: str, *, system: str = "", max_tokens: int = 4000,
                      fast: bool = False) -> Any:
        """Completion that must parse as JSON.

        Models wrap JSON in prose or fences often enough that extracting the
        outermost object is worth doing rather than failing the whole parse.
        """
        system = (system or "") + "\nRespond with valid JSON only. No prose, no code fences."
        raw = self.complete(prompt, system=system, max_tokens=max_tokens,
                            temperature=0, fast=fast)
        return parse_json(raw)


def parse_json(raw: str) -> Any:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost {...} or [...] in the response.
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = text.find(open_c), text.rfind(close_c)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"Model did not return JSON: {text[:200]}")
