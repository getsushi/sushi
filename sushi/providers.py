"""Where the model runs, and what each option costs to apply with.

The measurement that reframes this: filling one application costs roughly
320,000 input tokens, because a browser agent re-reads the page on every one
of its ~25 steps. The *model* you point that at therefore matters more than
anything else in the app.

    frontier model    $3.35 per application     $50/day at 15/day
    claude haiku      $0.34                     $5.13/day
    gpt-4o-mini       $0.051                    $0.76/day
    gemini flash      $0.034                    $0.51/day

A hundredfold spread, for a job that is mostly "read this label, type that
value". Running a frontier model here is like chartering a plane to post a
letter — and it is why a perfectly healthy subscription can be empty by the
evening.

So Sushi should not ask the user to supply a frontier key. It should offer
the cheap and free options first, and name the cost of each, because the
cost is not obvious and the default is otherwise whatever the user happens
to have lying around.

Every preset below is OpenAI-protocol-compatible, which is why they need no
new client code: only a base URL, a key and a model name change.

The free tiers are the ones worth starting on, and the ones most likely to
be out of date: providers change limits, rename models and withdraw free
access without notice. Treat the notes here as a starting point and the
signup link as the authority — which is why each preset carries one.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    name: str
    provider: str            # the Sushi provider path to use
    base_url: str            # "" means the provider's own default
    model: str
    signup: str              # where a key comes from
    free: str                # what you get without paying
    cost_note: str           # per application, measured against ~320k tokens
    key_env: str = "OPENAI_API_KEY"
    needs_key: bool = True
    #: For the keyless options, the module that knows whether this machine
    #: can actually serve it and which models it has. Both
    #: :mod:`sushi.local_llm` and :mod:`sushi.claude_code` answer the same
    #: four questions, so the settings page can treat them alike.
    runtime: str = ""

    @property
    def is_free(self) -> bool:
        """Whether someone can get going on this without paying anything."""
        return not self.free.lower().startswith("no free")


def runtime_module(name: str):
    """The module behind a keyless preset, or ``None``."""
    from . import claude_code, local_llm
    from .cli_agents import AGENTS
    return {"ollama": local_llm, "claude_code": claude_code, **AGENTS}.get(name)


#: Ordered by what to recommend first: free and fast, then cheap, then the
#: ones that only make sense if you already pay for them.
PRESETS: list[Preset] = [
    Preset(
        # First, because for anyone who has it this is the best option on the
        # list by a distance: a frontier model, no key, no card, no new
        # account — and the strongest instruction-following of anything here,
        # which is what form-filling actually turns on.
        name="Claude Code",
        provider="claude_code",
        base_url="",                    # supplied by the local shim
        model="claude-code:sonnet",
        signup="https://claude.com/code",
        free="Uses the Claude Code subscription already on this machine.",
        cost_note="No extra cost. Spends your Claude Code usage limits "
                  "instead. Text-only: no page screenshots.",
        needs_key=False,
        runtime="claude_code",
    ),
    Preset(
        name="Codex",
        provider="codex",
        base_url="",
        model="codex:default",
        signup="https://developers.openai.com/codex/cli",
        free="Uses the ChatGPT plan you sign in to Codex with.",
        cost_note="No extra cost. Spends your ChatGPT plan's Codex limits.",
        needs_key=False,
        runtime="codex",
    ),
    Preset(
        name="OpenCode",
        provider="opencode",
        base_url="",
        model="opencode:opencode/beam-free",
        signup="https://opencode.ai",
        free="Free models included, or any provider you sign in to.",
        cost_note="Free on OpenCode's free models. Others bill your own "
                  "provider account.",
        needs_key=False,
        runtime="opencode",
    ),
    Preset(
        name="Groq",
        provider="openai",
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile",
        signup="https://console.groq.com/keys",
        free="Free tier with daily limits, no card required.",
        cost_note="Free within the tier; among the fastest available.",
    ),
    Preset(
        name="Google Gemini",
        provider="openai",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.0-flash",
        signup="https://aistudio.google.com/apikey",
        free="Free tier with rate limits, no card required.",
        cost_note="~$0.03 per application once past the free tier.",
    ),
    # Cerebras, GitHub Models and OpenRouter sat here. Pulled: nine options
    # is a decision rather than a list, and these were the three whose free
    # tier is hardest to explain in a line — a token that has to be minted
    # with the right scope, a per-day cap, a ':free' suffix that has to stay
    # on the model name. A preset nobody picks is just length on the step
    # that decides whether the app works at all.
    Preset(
        name="OpenAI",
        provider="openai",
        base_url="",
        model="gpt-4o-mini",
        signup="https://platform.openai.com/api-keys",
        free="No free tier.",
        cost_note="~$0.05 per application on gpt-4o-mini.",
    ),
    Preset(
        name="Anthropic",
        provider="anthropic",
        base_url="",
        model="claude-haiku-4-5-20251001",
        signup="https://console.anthropic.com/settings/keys",
        free="No free tier.",
        cost_note="~$0.34 per application on Haiku.",
        key_env="ANTHROPIC_API_KEY",
    ),
    Preset(
        name="Local (Ollama)",
        provider="openai",
        base_url="http://127.0.0.1:11434/v1",
        model="",                       # filled in from what is installed
        signup="https://ollama.com/download",
        free="Free forever. Nothing leaves this machine.",
        cost_note="Free. Needs a GPU to be comfortable; slow on CPU alone.",
        needs_key=False,
        runtime="ollama",
    ),
]


def by_name(name: str) -> Preset | None:
    for p in PRESETS:
        if p.name.lower() == (name or "").lower():
            return p
    return None


def recommended() -> Preset:
    """What to suggest to someone who has not chosen.

    Whatever this machine can already serve without a signup, best first:
    a Claude Code subscription beats a local 7B, and either beats asking
    somebody to go and make an account. Otherwise the best free-tier cloud
    option.
    """
    for preset in PRESETS:
        mod = runtime_module(preset.runtime)
        if mod is not None and mod.available():
            return preset
    return next(p for p in PRESETS if p.needs_key)


def apply_preset(preset: Preset, api_key: str = "", model: str = "") -> None:
    """Point Sushi at this provider. The key is stored, never logged."""
    from . import storage

    chosen_model = model or preset.model
    runtime = runtime_module(preset.runtime)
    base_url = preset.base_url
    if runtime is not None:
        chosen_model = chosen_model or runtime.best_model()
        # A keyless runtime owns its own endpoint. Asked as a question
        # rather than read as an attribute: Claude Code has no fixed URL to
        # expose — it is routed natively and its shim takes an ephemeral
        # port — and reading ``.BASE_URL`` off it raised AttributeError, so
        # choosing the provider this app *recommends* on a machine with the
        # CLI installed failed outright.
        base_url = runtime.base_url()

    storage.set_setting("provider", preset.provider)
    storage.set_setting("model", chosen_model)
    storage.set_setting("openai_base_url", base_url)
    if runtime is not None:
        storage.set_setting("api_key", runtime.PLACEHOLDER_KEY)
    elif api_key:
        storage.set_setting("api_key", api_key)


def cost_table() -> list[tuple[str, str]]:
    """Name and cost note for every preset, for the UI."""
    return [(p.name, p.cost_note) for p in PRESETS]
