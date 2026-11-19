"""Running the model on this machine instead of someone else's.

Every model-backed feature stops the moment a cloud key lapses — which it
will, because keys get rotated, credit runs out, and a subscription that
worked at lunchtime can be refused by the evening. A local runtime removes
that whole category of failure: no key, no quota, no bill, and the job
descriptions and answers never leave the machine.

Ollama is the one this targets, because it exposes an OpenAI-compatible
endpoint — so Sushi's existing client, and browser-use's, both talk to it
with nothing changed but a base URL.

What local costs you is speed, and the honest numbers are not what you would
guess. The first call after a model is idle pays for loading several
gigabytes into VRAM and can take a minute; every call after that, on a
mid-range GPU, is faster than most cloud endpoints. Measured on an RTX 3090:
0.5 tok/s cold, 135 tok/s warm. So the thing worth optimising is *keeping it
warm*, not avoiding it.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

HOST = "http://127.0.0.1:11434"
BASE_URL = f"{HOST}/v1"
#: Ollama ignores the key entirely, but the OpenAI client insists on one.
PLACEHOLDER_KEY = "ollama"

#: Preferred first. Judged on instruction-following and JSON reliability at a
#: size that fits a consumer GPU — not on leaderboard scores. A model that
#: cannot be relied on to return the JSON it was asked for is no use here
#: whatever else it can do.
PREFERRED = (
    "qwen2.5:14b", "qwen2.5:7b", "qwen3:8b", "llama3.1:8b",
    "mistral-nemo", "llama3.2:3b", "qwen2.5:3b", "llama3.2:1b",
)


def available() -> bool:
    """Is a local runtime answering right now?"""
    return bool(models())


def models() -> list[str]:
    """Models this machine has already pulled. Empty if nothing is serving."""
    try:
        with urllib.request.urlopen(f"{HOST}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return []
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]


def best_model(installed: list[str] | None = None) -> str:
    """The most capable installed model, or "" if there are none.

    Exact matches first, then a prefix match so "qwen2.5:7b-instruct-q5" is
    recognised as the qwen2.5:7b it is.
    """
    have = installed if installed is not None else models()
    if not have:
        return ""
    for want in PREFERRED:
        for got in have:
            if got == want:
                return got
    for want in PREFERRED:
        stem = want.split(":")[0]
        for got in have:
            if got.split(":")[0] == stem:
                return got
    return have[0]


def loaded() -> set[str]:
    """Models currently resident in memory — the ones that answer instantly."""
    try:
        with urllib.request.urlopen(f"{HOST}/api/ps", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return set()
    return {m.get("name", "") for m in data.get("models", [])}


def warm(model: str, timeout: int = 180) -> bool:
    """Load a model into memory so the next real call is not the slow one.

    Worth doing on the way into an apply rather than during it: a cold load
    in the middle of filling a form looks like the app has hung, and the
    same load done a moment earlier is invisible.
    """
    if not model or model in loaded():
        return True
    body = json.dumps({"model": model, "messages": [], "keep_alive": "30m"}).encode()
    req = urllib.request.Request(
        f"{HOST}/api/chat", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            log.info("local model %s warmed", model)
            return True
    except Exception as exc:                        # noqa: BLE001
        log.info("could not warm %s: %s", model, exc)
        return False


def settings_for(model: str = ""):
    """Settings that point Sushi's existing client at the local runtime.

    No new provider: Ollama speaks the OpenAI protocol, so this is the
    ordinary OpenAI path with a different base URL.
    """
    from dataclasses import replace

    from .config import load_settings

    chosen = model or best_model()
    return replace(
        load_settings(),
        provider="openai",
        api_key=PLACEHOLDER_KEY,
        openai_base_url=BASE_URL,
        model=chosen,
        key_source="local (Ollama)",
    )


def base_url() -> str:
    """Ollama's OpenAI-compatible endpoint. Fixed, and on this machine."""
    return BASE_URL


def describe() -> str:
    """One line for the UI about what is available locally."""
    have = models()
    if not have:
        return ("No local runtime detected. Install Ollama and pull a model "
                "(ollama pull qwen2.5:7b) to run without an API key.")
    pick = best_model(have)
    warm_now = pick in loaded()
    return (f"{len(have)} local model(s) available, using {pick}"
            + (" (loaded, answers immediately)." if warm_now
               else " (first call will pause while it loads)."))
