"""Read model credentials from a local env file so no key is typed in the UI.

The user keeps an ``.env_amm`` file with their provider details. We read it at
runtime only — the key is never written into the database, the code, or a log.
We reference it by file, following the project's secrets rule.

The file may be a clean ``KEY=VALUE`` env file, or a looser notepad of snippets;
both are handled. Recognised clean keys:

    OPENAI_API_KEY / AZURE_OPENAI_API_KEY / ANTHROPIC_API_KEY
    OPENAI_BASE_URL / AZURE_OPENAI_BASE_URL
    SUSHI_PROVIDER   (anthropic | openai)
    SUSHI_MODEL
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ENV_FILENAMES = (".env_amm", ".env")


def _search_dirs() -> list[Path]:
    """CWD, the package's repo, and every parent up to the drive root."""
    here = Path(__file__).resolve()
    dirs: list[Path] = [Path.cwd()]
    dirs += list(here.parents)
    seen: set[Path] = set()
    out: list[Path] = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def find_env_file() -> Path | None:
    for d in _search_dirs():
        for name in ENV_FILENAMES:
            p = d / name
            if p.is_file():
                return p
    return None


@dataclass
class ModelConfig:
    provider: str = ""      # openai | anthropic
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    source: str = ""        # path it came from, for display (never the value)
    label: str = ""         # the heading it sat under, e.g. "Groq"

    @property
    def ok(self) -> bool:
        return bool(self.api_key)


def _parse_clean(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().lstrip("export ").strip()
        val = val.strip().strip('"').strip("'")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) and val:
            out[key] = val
    return out


#: Key prefixes that identify a provider on sight. A Groq key says what it
#: is; an Azure one does not, which is why the label matters too.
_KEY_SIGNATURES = (
    ("gsk_", "Groq"),
    ("sk-ant", "Anthropic"),
    ("AIza", "Google Gemini"),
)

#: A heading in a notepad file: "for Groq", "For GPT5 Terra".
_HEADING = re.compile(r"(?mi)^\s*(?:for|using)\s+(\S.{0,40})$")


def _provider_for(label: str, key: str) -> str:
    """Which known provider this block is, by key prefix then by heading."""
    for prefix, name in _KEY_SIGNATURES:
        if key.startswith(prefix):
            return name
    low = (label or "").lower()
    from . import providers

    for preset in providers.PRESETS:
        # Only the key-taking presets can be what a key in a file means.
        # Without this, a block headed "for Claude" matches "Claude Code",
        # which holds its own credentials and wants nothing from the file.
        if preset.needs_key and preset.name.lower().split()[0] in low:
            return preset.name
    return ""


def _extract_one(chunk: str, label: str = "") -> ModelConfig:
    """Read one block of a notepad-style file."""
    cfg = ModelConfig()

    for m in re.finditer(r"(?:api[_ ]?key)\s*[:=]\s*([A-Za-z0-9_\-]{32,})", chunk, re.I):
        cfg.api_key = m.group(1).strip()
        break
    if not cfg.api_key:
        if m := re.search(r"\b([A-Za-z0-9]{60,})\b", chunk):
            cfg.api_key = m.group(1)
    if not cfg.api_key:
        return cfg

    # A provider we recognise brings its own endpoint, which is the whole
    # point: the file may show SDK code that never names a base URL at all.
    known = _provider_for(label, cfg.api_key)
    if known:
        from . import providers

        preset = providers.by_name(known)
        if preset:
            cfg.provider = preset.provider
            cfg.base_url = preset.base_url
            cfg.model = preset.model

    if m := re.search(r"https?://[^\s\"']+?/openai/v1", chunk, re.I):
        cfg.base_url = m.group(0).rstrip("/")
        cfg.provider = cfg.provider or "openai"
    elif not cfg.base_url:
        if m := re.search(r"https?://[^\s\"']+", chunk):
            cfg.base_url = m.group(0).rstrip("/")

    # An explicit model in the block beats the preset's default.
    if m := re.search(r"(?:deployment_name|model)\s*[=:]\s*[\"\']([^\"\']+)[\"\']", chunk):
        cfg.model = m.group(1).strip()
    elif not cfg.model:
        if m := re.search(r"\b(gpt[-\w./]+|claude[-\w.]+|llama[-\w./]+|o\d[-\w.]*)\b", chunk, re.I):
            cfg.model = m.group(1)

    if not cfg.provider:
        cfg.provider = "anthropic" if cfg.api_key.startswith("sk-ant") else "openai"
    cfg.label = label.strip()
    return cfg


def extract_all(text: str) -> list[ModelConfig]:
    """Every provider configured in the file, in the order they appear.

    A notepad accumulates: a key that stopped working is rarely deleted, a
    new one is pasted underneath it. Reading only the first block meant the
    oldest, deadest credential always won — and adding a working one changed
    nothing at all.
    """
    marks = [(m.start(), m.group(1)) for m in _HEADING.finditer(text)]
    if not marks:
        one = _extract_one(text)
        return [one] if one.api_key else []

    out: list[ModelConfig] = []
    # Anything before the first heading is its own unlabelled block.
    if marks[0][0] > 0:
        head = _extract_one(text[:marks[0][0]])
        if head.api_key:
            out.append(head)
    for i, (start, label) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        cfg = _extract_one(text[start:end], label)
        if cfg.api_key:
            out.append(cfg)
    return out


def _extract_loose(text: str) -> ModelConfig:
    """Best-effort extraction from a notepad-style file.

    When several are present the *last* wins: appending is how people add a
    replacement, so the newest entry is the one they meant.
    """
    found = extract_all(text)
    return found[-1] if found else ModelConfig()


def detect_model_config() -> ModelConfig:
    """Load the user's model config from the env file, if present."""
    p = find_env_file()
    if not p:
        return ModelConfig()
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ModelConfig()

    clean = _parse_clean(text)
    cfg = ModelConfig(source=str(p))

    # Clean keys win when present.
    key = (clean.get("OPENAI_API_KEY") or clean.get("AZURE_OPENAI_API_KEY")
           or clean.get("ANTHROPIC_API_KEY"))
    if key:
        cfg.api_key = key
        cfg.base_url = (clean.get("OPENAI_BASE_URL") or clean.get("AZURE_OPENAI_BASE_URL") or "")
        cfg.model = clean.get("SUSHI_MODEL", "")
        cfg.provider = clean.get("SUSHI_PROVIDER", "") or (
            "anthropic" if clean.get("ANTHROPIC_API_KEY") else "openai")
    else:
        loose = _extract_loose(text)
        loose.source = str(p)
        cfg = loose

    return cfg
