"""The two keyless runtimes, and what they do when nothing is serving.

Sushi can run with no API key at all: through Ollama on the user's own
machine, or through a Claude Code subscription via a local shim. Both are
optional, and for most people at least one of them is simply absent — so
the behaviour that matters most is the one on the unhappy path. An
``available()`` that raises instead of returning False takes the Settings
page down with it, and a ``best_model()`` that guesses a model the machine
does not have configures Sushi to call something that cannot answer.

The settings page treats both alike — list what is there, pick the best —
so this also checks they really do answer the same four questions with the
same shapes. That is an interface, and nothing enforced it.

    python qa_runtimes.py            # offline behaviour
    python qa_runtimes.py --live     # also calls whichever is installed
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sushi-runtimes-"))
os.environ["LOCALAPPDATA"] = str(_TMP)
os.environ["XDG_DATA_HOME"] = str(_TMP)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

LIVE = "--live" in sys.argv
RESULTS: list[tuple[str, str, str]] = []


def check(name: str, fn, *, live: bool = False) -> None:
    if live and not LIVE:
        RESULTS.append(("SKIP", name, "needs --live"))
        print(f"  skip  {name}")
        return
    try:
        RESULTS.append(("PASS", name, str(fn() or "")[:150]))
        print(f"  ok    {name}")
    except Exception as e:
        RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=2)


def main() -> int:
    from sushi import claude_code, local_llm, providers

    runtimes = {"local_llm": local_llm, "claude_code": claude_code}

    def t_same_interface():
        """Both answer the same questions, because the UI asks them blind."""
        for name, mod in runtimes.items():
            for attr in ("available", "models", "best_model", "describe",
                         "base_url"):
                assert callable(getattr(mod, attr, None)), \
                    f"{name} does not answer {attr}()"
            # Asked, not read off the module. Only one of them has a fixed
            # URL; requiring the attribute is what let a missing BASE_URL
            # reach apply_preset and raise there instead of here.
            assert isinstance(mod.base_url(), str), \
                f"{name}.base_url() returned {mod.base_url()!r}"
            assert isinstance(getattr(mod, "PLACEHOLDER_KEY", None), str), \
                f"{name} has no placeholder key, so apply_preset cannot store one"
            assert mod.PLACEHOLDER_KEY, f"{name}'s placeholder is empty"
        return f"{len(runtimes)} runtimes, same four questions"
    check("runtimes: both present the same interface", t_same_interface)

    def t_available_never_raises():
        """Absent is a normal answer, and must not be an exception.

        This runs on the Settings page while it is being drawn. A runtime
        that raises when its service is missing takes the page down for
        everyone who has not installed it — which is most people.
        """
        for name, mod in runtimes.items():
            got = mod.available()
            assert isinstance(got, bool), f"{name}.available() returned {got!r}"
            listed = mod.models()
            assert isinstance(listed, list), f"{name}.models() returned {listed!r}"
            assert all(isinstance(m, str) for m in listed), listed
            text = mod.describe()
            assert isinstance(text, str) and text, f"{name}.describe() said nothing"
        here = [n for n, m in runtimes.items() if m.available()]
        return f"answered without raising; present here: {here or 'neither'}"
    check("runtimes: asking whether they exist never raises",
          t_available_never_raises)

    def t_best_model_with_nothing_installed():
        """With no models, say so — do not invent one.

        Ollama with nothing pulled must return "", because configuring
        Sushi to call a model that is not there fails later, in the middle
        of an application, instead of now in Settings where it can be
        fixed.
        """
        assert local_llm.best_model(installed=[]) == "", \
            "a model was invented for a machine with none installed"
        # Claude Code is different on purpose: its aliases always exist if
        # the CLI does, so it falls back to its default rather than "".
        fallback = claude_code.best_model(installed=[])
        assert fallback.startswith(claude_code.PREFIX), fallback
        return f"ollama yields nothing; claude code falls back to {fallback}"
    check("runtimes: nothing installed means no model, not a guess",
          t_best_model_with_nothing_installed)

    def t_best_model_prefers_the_capable_one():
        """Given a choice, pick the best — exact match first, then family."""
        have = ["llama3.2:1b", "qwen2.5:7b-instruct-q5_K_M", "tinyllama:latest"]
        got = local_llm.best_model(installed=have)
        assert got in have, got
        assert got != "tinyllama:latest", \
            f"picked the weakest model available: {got}"
        # A tagged build is recognised as the family it belongs to.
        assert local_llm.best_model(installed=["qwen2.5:7b-instruct-q5_K_M"]) \
            == "qwen2.5:7b-instruct-q5_K_M"
        # Something unrecognised is still usable rather than refused.
        assert local_llm.best_model(installed=["mystery:3b"]) == "mystery:3b"
        return f"chose {got} from {len(have)}"
    check("runtimes: the most capable installed model wins",
          t_best_model_prefers_the_capable_one)

    def t_alias_normalisation():
        """However the model was stored, the CLI gets a name it knows."""
        default = claude_code.DEFAULT_ALIAS
        assert claude_code._alias("") == default
        assert claude_code._alias(claude_code.PREFIX + "opus") == "opus"
        assert claude_code._alias("opus") == "opus"
        assert claude_code._alias("  ") == default, \
            "whitespace resolved to an empty model name"
        return f"prefix stripped, blank falls back to {default}"
    check("claude code: stored model names resolve to CLI aliases",
          t_alias_normalisation)

    def t_presets_point_at_real_runtimes():
        """Every keyless preset names a runtime that actually exists."""
        keyless = [p for p in providers.PRESETS if not p.needs_key]
        assert keyless, "no keyless option is offered at all"
        for preset in keyless:
            mod = providers.runtime_module(preset.runtime)
            assert mod is not None, \
                f"{preset.name} names runtime {preset.runtime!r}, which is nothing"
            assert mod in runtimes.values(), preset.runtime
        return f"{len(keyless)} keyless preset(s), each wired to a real runtime"
    check("providers: keyless presets resolve to a runtime",
          t_presets_point_at_real_runtimes)

    def t_apply_preset_uses_the_runtime_endpoint():
        """A keyless preset configures the shim, not a cloud endpoint.

        It must also store the placeholder rather than leaving the previous
        provider's key in place — otherwise switching to Ollama keeps
        sending the old key to a server that never asked for one.
        """
        from sushi import storage

        keyless = next(p for p in providers.PRESETS if not p.needs_key)
        mod = providers.runtime_module(keyless.runtime)
        storage.set_setting("api_key", "sk-left-over-from-before")
        providers.apply_preset(keyless)
        assert storage.get_setting("openai_base_url") == mod.base_url(), \
            storage.get_setting("openai_base_url")
        assert storage.get_setting("api_key") == mod.PLACEHOLDER_KEY, \
            "a real key survived the switch to a keyless runtime"
        assert storage.get_setting("provider") == keyless.provider
        return (f"{keyless.name} -> {mod.base_url() or 'routed natively'}"
                f" with a placeholder key")
    check("providers: choosing a keyless runtime clears the old key",
          t_apply_preset_uses_the_runtime_endpoint)

    def t_live_roundtrip():
        """Whichever runtime is installed here actually answers."""
        answered = []
        for name, mod in runtimes.items():
            if not mod.available():
                continue
            if name == "claude_code":
                text = claude_code.ask("Reply with the single word: ready")
            else:
                model = mod.best_model()
                assert model, "available but no model to use"
                from sushi.llm import LLM
                text = LLM(mod.settings_for(model)).ask(
                    "Reply with the single word: ready")
            assert text and text.strip(), f"{name} answered with nothing"
            answered.append(f"{name}: {text.strip()[:30]!r}")
        if not answered:
            return "neither runtime is installed on this machine"
        return "; ".join(answered)
    check("runtimes: the installed one answers a real prompt",
          t_live_roundtrip, live=True)

    print()
    width = max(len(n) for _, n, _ in RESULTS) + 2
    for status, name, detail in RESULTS:
        mark = {"PASS": "ok  ", "FAIL": "FAIL", "SKIP": "skip"}[status]
        print(f"  {mark}  {name:<{width}} {detail}")
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    skipped = sum(1 for s, _, _ in RESULTS if s == "SKIP")
    print(f"\n{len(RESULTS) - failed - skipped} passed, {failed} failed, "
          f"{skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
