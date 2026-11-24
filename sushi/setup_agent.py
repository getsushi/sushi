"""Getting a model working on a machine that has nothing.

The setup guide used to stop at "install Node.js" with a download link, and
at "run this in a terminal". Most people this app is for have never opened
one. So this module does the work itself, and reports as it goes:

- :func:`install_plan` — every command needed to get a coding agent or
  Ollama onto *this* machine, Node.js included when the tool needs it.
- :func:`run_plan` — runs them, streaming each line back for the UI.
- :func:`refresh_path` — an installer edits PATH in the registry, not in
  the process that ran it, so without this a fresh install is invisible
  until Sushi is restarted ("I installed it and it still says missing").
  It also matters for a packaged macOS app, which starts from Finder with a
  PATH that holds none of Homebrew, npm or ~/.local/bin.
- :func:`hardware` and :func:`recommend` — what this machine can run
  locally, and the model that fits it with room to spare.
- :func:`pull` — downloads an Ollama model through its own API, with
  progress, so nobody has to watch a terminal for 5 GB.
- :func:`check_key` — one cheap request to prove an API key works before it
  is saved, instead of finding out on the first application.
"""
from __future__ import annotations

import json
import re
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

WIN = sys.platform == "win32"
MAC = sys.platform == "darwin"
_NO_WINDOW = 0x08000000 if WIN else 0          # CREATE_NO_WINDOW
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


# --- PATH ---------------------------------------------------------------------

def _extra_dirs() -> list[str]:
    home = Path.home()
    if WIN:
        appdata = os.environ.get("APPDATA", str(home / "AppData" / "Roaming"))
        local = os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))
        return [str(Path(appdata) / "npm"), str(Path(local) / "Programs" / "Ollama"),
                str(home / ".local" / "bin"), r"C:\Program Files\nodejs",
                str(Path(local) / "Microsoft" / "WindowsApps")]
    return ["/opt/homebrew/bin", "/usr/local/bin", str(home / ".local" / "bin"),
            str(home / ".opencode" / "bin"), str(home / ".npm-global" / "bin"),
            "/Applications/Ollama.app/Contents/Resources"]


def refresh_path() -> None:
    """Make tools installed since start-up findable, without a restart."""
    parts = os.environ.get("PATH", "").split(os.pathsep)
    fresh: list[str] = []
    if WIN:
        try:
            import winreg
            for root, key in ((winreg.HKEY_LOCAL_MACHINE,
                               r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                              (winreg.HKEY_CURRENT_USER, "Environment")):
                try:
                    with winreg.OpenKey(root, key) as k:
                        val, _ = winreg.QueryValueEx(k, "Path")
                    fresh += os.path.expandvars(val).split(os.pathsep)
                except OSError:
                    pass
        except ImportError:
            pass
    fresh += _extra_dirs()
    for d in fresh:
        if d and d not in parts and os.path.isdir(d):
            parts.append(d)
    os.environ["PATH"] = os.pathsep.join(p for p in parts if p)


def has(cmd: str) -> bool:
    return bool(shutil.which(cmd))


# --- install plans -------------------------------------------------------------

@dataclass
class Step:
    label: str        # what the user reads: "Installing Node.js"
    cmd: str          # run through the shell
    timeout: int = 900


_WINGET = "winget install -e --silent --accept-source-agreements --accept-package-agreements --id "

_NPM_PKG = {"claude_code": "@anthropic-ai/claude-code", "codex": "@openai/codex",
            "opencode": "opencode-ai"}
_EXE = {"claude_code": "claude", "codex": "codex", "opencode": "opencode", "ollama": "ollama"}
_NAME = {"claude_code": "Claude Code", "codex": "Codex", "opencode": "OpenCode",
         "ollama": "Ollama"}


def _node_step() -> Step | None:
    if has("node") and has("npm"):
        return None
    if WIN and has("winget"):
        return Step("Installing Node.js (needed by the installer)",
                    _WINGET + "OpenJS.NodeJS.LTS")
    if MAC and has("brew"):
        return Step("Installing Node.js (needed by the installer)", "brew install node")
    return None


def install_plan(provider: str) -> tuple[list[Step], str]:
    """The commands that put ``provider`` on this machine.

    Returns (steps, problem). ``problem`` is a sentence when it cannot be
    done automatically here, and says what to do instead.
    """
    name = _NAME.get(provider, provider)
    if provider == "ollama":
        if WIN:
            if has("winget"):
                return [Step("Installing Ollama", _WINGET + "Ollama.Ollama")], ""
            return [], "Windows' installer tool (winget) is missing. Download Ollama from ollama.com."
        if MAC:
            if has("brew"):
                return [Step("Installing Ollama", "brew install --cask ollama")], ""
            return [Step("Downloading Ollama",
                          "curl -fsSL -o /tmp/Ollama.zip https://ollama.com/download/Ollama-darwin.zip"
                          " && ditto -x -k /tmp/Ollama.zip ~/Applications"
                          " && open ~/Applications/Ollama.app")], ""
        return [], "Run “curl -fsSL https://ollama.com/install.sh | sh” in a terminal (it asks for your password)."

    steps: list[Step] = []
    # Claude Code and OpenCode have their own installers: no Node needed.
    if provider == "claude_code":
        if WIN:
            steps.append(Step("Installing Claude Code",
                              'powershell -NoProfile -ExecutionPolicy Bypass -Command '
                              '"irm https://claude.ai/install.ps1 | iex"'))
        else:
            steps.append(Step("Installing Claude Code",
                              "curl -fsSL https://claude.ai/install.sh | bash"))
        return steps, ""
    if provider == "opencode" and not WIN:
        return [Step("Installing OpenCode", "curl -fsSL https://opencode.ai/install | bash")], ""

    node = _node_step()
    if node is None and not (has("node") and has("npm")):
        where = "nodejs.org" if not WIN else "nodejs.org (or install “App Installer” from the Microsoft Store)"
        return [], f"{name} needs Node.js, which Sushi cannot install here. Get the LTS version from {where}, then try again."
    if node:
        steps.append(node)
    steps.append(Step(f"Installing {name}", f"npm install -g {_NPM_PKG[provider]}"))
    return steps, ""


def run_plan(steps: list[Step], on_line: Callable[[str], None]) -> tuple[bool, str]:
    """Run each step in order; stop at the first failure.

    ``on_line`` gets the step label, then the last line of output as it
    arrives, for a one-line live status. Returns (ok, message).
    """
    for step in steps:
        on_line(step.label + "…")
        log.info("setup: %s: %s", step.label, step.cmd)
        try:
            proc = subprocess.Popen(step.cmd, shell=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    text=True, encoding="utf-8", errors="replace",
                                    creationflags=_NO_WINDOW)
        except OSError as exc:
            return False, f"{step.label} failed: {exc}"
        last, start = "", time.time()
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = _ANSI.sub("", raw).strip()
            if line and not set(line) <= set("-\\|/ █▒░"):
                last = line
                on_line(f"{step.label}… {line[:90]}")
            if time.time() - start > step.timeout:
                proc.kill()
                return False, f"{step.label} took too long."
        code = proc.wait()
        refresh_path()
        # winget says "already installed" with a non-zero code; that is fine.
        if code != 0 and "already installed" not in last.lower() \
                and "no available upgrade" not in last.lower():
            log.warning("setup step failed (%s): %s", code, last)
            return False, f"{step.label} failed: {last[:160] or f'exit code {code}'}"
    refresh_path()
    return True, "Installed."


def install(provider: str, on_line: Callable[[str], None]) -> tuple[bool, str]:
    steps, problem = install_plan(provider)
    if problem:
        return False, problem
    ok, msg = run_plan(steps, on_line)
    if ok and not has(_EXE.get(provider, provider)):
        return False, ("Installed, but Sushi cannot see it yet. Close and reopen "
                       "Sushi, then pick it again.")
    if ok and provider == "ollama":
        start_ollama()
    return ok, msg


# --- what this machine can run ------------------------------------------------

@dataclass
class Hardware:
    ram_gb: float
    vram_gb: float          # dedicated GPU memory; 0 if none found
    gpu: str
    apple_silicon: bool
    disk_free_gb: float

    def summary(self) -> str:
        bits = [f"{self.ram_gb:.0f} GB memory"]
        if self.apple_silicon:
            bits.append("Apple Silicon")
        elif self.gpu:
            bits.append(f"{self.gpu}" + (f" ({self.vram_gb:.0f} GB)" if self.vram_gb else ""))
        else:
            bits.append("no graphics card found")
        bits.append(f"{self.disk_free_gb:.0f} GB free disk")
        return " · ".join(bits)


def _ram_gb() -> float:
    try:
        if WIN:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 2**30
        if MAC:
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
            return int(out.stdout.strip()) / 2**30
        with open("/proc/meminfo") as f:
            return int(f.readline().split()[1]) / 2**20
    except Exception:                                   # noqa: BLE001
        return 0.0


def _gpu() -> tuple[str, float]:
    """(name, VRAM GB) of an NVIDIA card via nvidia-smi; else a name only."""
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run([smi, "--query-gpu=name,memory.total",
                                  "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=8,
                                 creationflags=_NO_WINDOW).stdout.strip().splitlines()
            if out:
                name, mem = out[0].rsplit(",", 1)
                return name.strip(), float(mem) / 1024
        except Exception:                               # noqa: BLE001
            pass
    if WIN:
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_VideoController | Select-Object -First 1 -ExpandProperty Name)"],
                capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW).stdout.strip()
            return out, 0.0
        except Exception:                               # noqa: BLE001
            pass
    return "", 0.0


def hardware() -> Hardware:
    gpu, vram = _gpu()
    models_dir = Path.home()
    try:
        free = shutil.disk_usage(models_dir).free / 2**30
    except OSError:
        free = 0.0
    apple = MAC and platform.machine() == "arm64"
    return Hardware(round(_ram_gb(), 1), round(vram, 1), gpu, apple, round(free, 1))


@dataclass
class Pick:
    model: str
    size_gb: float
    speed: str        # what to expect, said plainly
    fits: bool        # False: it will run, but badly


#: Largest first. Sizes are Ollama's download sizes.
_TIERS = [
    ("qwen2.5:14b", 9.0, 12, 32),     # needs VRAM >= 12, or unified/RAM >= 32
    ("qwen2.5:7b", 4.7, 6, 16),
    ("qwen2.5:3b", 1.9, 3, 8),
    ("llama3.2:1b", 1.3, 0, 0),
]


def recommend(hw: Hardware) -> Pick:
    """The biggest model that fits comfortably, leaving room for the browser."""
    for model, size, need_vram, need_ram in _TIERS:
        if hw.disk_free_gb and hw.disk_free_gb < size + 2:
            continue
        if hw.vram_gb >= need_vram and need_vram:
            return Pick(model, size, "Fast on your graphics card.", True)
        if hw.apple_silicon and hw.ram_gb >= need_ram and need_ram:
            return Pick(model, size, "Fast on Apple Silicon.", True)
        if not hw.vram_gb and not hw.apple_silicon and hw.ram_gb >= need_ram + 8 and need_ram:
            return Pick(model, size, "Runs on the processor: works, but each answer "
                                     "takes several seconds.", True)
    last = _TIERS[-1]
    if hw.disk_free_gb and hw.disk_free_gb < last[1] + 2:
        return Pick(last[0], last[1],
                    f"Only {hw.disk_free_gb:.1f} GB of disk is free; the smallest model needs "
                    f"{last[1] + 2:.1f} GB. Free some space, or use a free cloud "
                    "option (Groq, Gemini) instead.", False)
    ok = hw.ram_gb >= 8
    return Pick(last[0], last[1],
                "Small and quick, but less accurate on long forms." if ok else
                "This PC has little memory; a free cloud option (Groq, Gemini) will work better.",
                ok)


# --- Ollama: start and download -------------------------------------------------

def ollama_serving() -> bool:
    from . import local_llm
    try:
        with urllib.request.urlopen(f"{local_llm.HOST}/api/version", timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


def start_ollama(wait: float = 15) -> bool:
    """Start Ollama's server if it is installed and not running."""
    if ollama_serving():
        return True
    refresh_path()
    exe = shutil.which("ollama")
    if not exe:
        return False
    try:
        kw = {"creationflags": _NO_WINDOW | 0x00000008} if WIN else {"start_new_session": True}
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, **kw)
    except OSError as exc:
        log.info("could not start ollama: %s", exc)
        return False
    end = time.time() + wait
    while time.time() < end:
        if ollama_serving():
            return True
        time.sleep(0.5)
    return False


def pull(model: str, on_progress: Callable[[float, str], None]) -> tuple[bool, str]:
    """Download ``model`` through Ollama, reporting (fraction 0..1, words)."""
    from . import local_llm
    if not start_ollama():
        return False, "Ollama is installed but not starting. Open the Ollama app once, then try again."
    req = urllib.request.Request(f"{local_llm.HOST}/api/pull",
                                 data=json.dumps({"model": model, "stream": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    parts: dict[str, tuple[int, int]] = {}
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            for raw in resp:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if ev.get("error"):
                    return False, ev["error"]
                if ev.get("digest") and ev.get("total"):
                    parts[ev["digest"]] = (ev.get("completed", 0), ev["total"])
                done = sum(c for c, _ in parts.values())
                total = sum(t for _, t in parts.values()) or 1
                status = ev.get("status", "")
                if status == "success":
                    on_progress(1.0, "Downloaded.")
                    return True, "Downloaded."
                if parts:
                    on_progress(done / total,
                                f"Downloading {model}: {done / 2**30:.1f} of {total / 2**30:.1f} GB")
                else:
                    on_progress(0.0, status.capitalize() or "Starting…")
    except (urllib.error.URLError, OSError) as exc:
        return False, f"Download stopped: {exc}"
    return model in local_llm.models(), "Downloaded."


# --- your own API key -------------------------------------------------------------

def check_key(provider: str, base_url: str, key: str) -> tuple[bool, str]:
    """One cheap request (list models) to prove a key works. Never logs it."""
    if provider == "anthropic":
        req = urllib.request.Request("https://api.anthropic.com/v1/models",
                                     headers={"x-api-key": key,
                                              "anthropic-version": "2023-06-01"})
    else:
        base = (base_url or "https://api.openai.com/v1").rstrip("/")
        req = urllib.request.Request(f"{base}/models",
                                     headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True, "Key works."
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, "That key was refused. Check it was copied in full."
        if exc.code == 429:
            return True, "Key works (rate-limited right now)."
        return False, f"The provider answered {exc.code}."
    except (urllib.error.URLError, OSError):
        return False, "Could not reach the provider. Check the internet connection."
