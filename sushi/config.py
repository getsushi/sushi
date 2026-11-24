"""Paths and settings for Sushi.

Everything a user generates lives under their OS application-data directory,
not next to the code, so the app can be installed read-only and still work.
Secrets come from the environment or the user's keyring - never from a file we
commit, and never written into the database.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "Sushi"
APP_SLUG = "sushi"


def _data_root() -> Path:
    """Per-OS place for user data, following platform convention."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_SLUG


DATA_DIR = _data_root()
DB_PATH = DATA_DIR / "sushi.db"
CV_DIR = DATA_DIR / "cvs"
LETTERS_DIR = DATA_DIR / "letters"
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"
LOG_PATH = DATA_DIR / "sushi.log"

# The company corpus is shipped/cached data rather than user data: it is the
# same for everybody and expensive to rebuild, so it lives beside the code.
PACKAGE_DIR = Path(__file__).resolve().parent
CACHE_DIR = PACKAGE_DIR / "data"
COMPANIES_PATH = CACHE_DIR / "companies.jsonl"


# --- the user's workspace ---------------------------------------------------
#
# DATA_DIR is the app's own storage: a SQLite file, a browser profile, a cache.
# Nobody wants to go looking in AppData for the cover letter Sushi just wrote
# them, so anything a person might reasonably want to open, email or print
# lives in a plain folder under Documents instead — created on first run, and
# relocatable, because plenty of people keep Documents on OneDrive and plenty
# of others deliberately do not.

WORKSPACE_KEY = "workspace_dir"
DEFAULT_WORKSPACE = Path.home() / "Documents" / "Sushi"

#: Subfolders created inside the workspace, and what each is for.
WORKSPACE_TREE = {
    "cvs": "CVs",
    "letters": "Cover letters",
    "resumes": "Tailored resumes",
    "exports": "Exports",
}


def workspace() -> Path:
    """Where the user's documents go. Their choice if they made one."""
    try:
        from .storage import get_setting
        chosen = get_setting(WORKSPACE_KEY, "").strip()
    except Exception:
        # Called before the database exists (or while creating it) — the
        # default is always a valid answer.
        chosen = ""
    return Path(chosen) if chosen else DEFAULT_WORKSPACE


def ensure_workspace(root: Path | None = None) -> Path:
    """Create the workspace tree, and return the root.

    Never raises: an unwritable or disconnected location (a OneDrive folder
    that is not signed in, a removed drive) falls back to the default rather
    than taking the app down on launch.
    """
    root = Path(root) if root else workspace()
    try:
        for name in WORKSPACE_TREE.values():
            (root / name).mkdir(parents=True, exist_ok=True)
        return root
    except OSError:
        if root != DEFAULT_WORKSPACE:
            return ensure_workspace(DEFAULT_WORKSPACE)
        return root


def set_workspace(root: Path | str) -> Path:
    """Point the workspace somewhere else and build the tree there."""
    from .storage import set_setting
    root = Path(root)
    created = ensure_workspace(root)
    set_setting(WORKSPACE_KEY, str(created))
    return created


def letters_dir() -> Path:
    return ensure_workspace() / WORKSPACE_TREE["letters"]


def resumes_dir() -> Path:
    return ensure_workspace() / WORKSPACE_TREE["resumes"]


def cvs_dir() -> Path:
    return ensure_workspace() / WORKSPACE_TREE["cvs"]


def exports_dir() -> Path:
    return ensure_workspace() / WORKSPACE_TREE["exports"]


def ensure_dirs() -> None:
    for d in (DATA_DIR, CV_DIR, LETTERS_DIR, BROWSER_PROFILE_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
    ensure_workspace()


@dataclass(frozen=True)
class Settings:
    """Runtime configuration.

    ``provider`` selects which LLM backend the whole app uses; the user sets it
    once in Settings and every feature (CV parsing, matching, cover letters,
    form filling) follows it.
    """

    provider: str = "anthropic"     # anthropic | openai | azure | claude_code
    model: str = "claude-sonnet-5"
    #: Kept out of ``repr``. A dataclass prints every field by default, so
    #: one ``log.info("settings: %s", s)`` — or any traceback that happens
    #: to carry a Settings — would write the user's API key into a file they
    #: will cheerfully attach to a bug report. ``key_source`` is the thing
    #: worth printing, and it is deliberately a path or a word, never a
    #: value.
    api_key: str = field(default="", repr=False)
    azure_base_url: str = ""
    azure_deployment: str = ""
    openai_base_url: str = ""        # OpenAI-compatible endpoint (e.g. Azure /openai/v1)
    headless: bool = False          # users want to watch the first few applications
    autosubmit: bool = False        # never submit without an explicit opt-in
    key_source: str = ""            # where the key came from (file path / "settings"), never the value

    @property
    def llm_enabled(self) -> bool:
        if self.provider == "azure":
            return bool(self.api_key and self.azure_base_url and self.azure_deployment)
        if self.provider in CLI_PROVIDERS:
            # Credentials belong to the CLI, not to us. Asking for a key
            # here would be asking for one that does not exist.
            from .cli_agents import runtime_for
            return runtime_for(self.provider).available()
        return bool(self.api_key)


# Model defaults per provider. Claude models lead because the form-filling and
# CV-reasoning work benefits from the stronger instruction following.
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o",
    "azure": "",  # deployment name is user-specific
    "claude_code": "claude-code:sonnet",
    "codex": "codex:default",
    "opencode": "opencode:opencode/beam-free",
}

#: Providers that are a signed-in CLI on this machine rather than an API.
CLI_PROVIDERS = ("claude_code", "codex", "opencode")

ENV_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "claude_code": "",  # the CLI holds its own credentials
    "codex": "",
    "opencode": "",
}


def load_settings() -> Settings:
    """Resolve settings in priority order: explicit DB settings → ``.env_amm``
    file → process environment.

    The key is never persisted to the DB by this path; when it comes from the
    env file it is read fresh each run and only its *source* is recorded.
    """
    from .storage import get_setting  # local import: storage imports config
    from .env import detect_model_config

    env_cfg = detect_model_config()

    # Provider: explicit DB choice wins, else whatever the env file implies.
    provider = get_setting("provider", "") or env_cfg.provider or "anthropic"

    # Key: DB → env file → process env. Record source, not value.
    db_key = get_setting("api_key", "").strip()
    key, key_source = "", ""
    if provider in CLI_PROVIDERS:
        # There is no key to resolve, and no file to go looking in: the CLI
        # signs its own requests. Say so, so Settings can show where this is
        # coming from rather than "key from settings".
        from .cli_agents import runtime_for
        name = {"claude_code": "Claude Code", "codex": "Codex",
                "opencode": "OpenCode"}[provider]
        key, key_source = runtime_for(provider).PLACEHOLDER_KEY, f"{name} sign-in"
    elif db_key:
        key, key_source = db_key, "settings"
    elif env_cfg.ok:
        key, key_source = env_cfg.api_key, env_cfg.source or ".env file"
    else:
        proc = os.getenv(ENV_KEYS.get(provider, ""), "").strip()
        if proc:
            key, key_source = proc, "environment"

    model = (get_setting("model", "") or env_cfg.model
             or DEFAULT_MODELS.get(provider, ""))
    openai_base = (get_setting("openai_base_url", "")
                   or (env_cfg.base_url if provider == "openai" else "")
                   or os.getenv("OPENAI_BASE_URL", "")).rstrip("/")

    return Settings(
        provider=provider,
        model=model,
        api_key=key.strip(),
        azure_base_url=get_setting("azure_base_url", os.getenv("AZURE_OPENAI_BASE_URL", "")).rstrip("/"),
        azure_deployment=get_setting("azure_deployment", os.getenv("AZURE_OPENAI_DEPLOYMENT", "")),
        openai_base_url=openai_base,
        headless=get_setting("headless", "0") == "1",
        autosubmit=get_setting("autosubmit", "0") == "1",
        key_source=key_source,
    )
