"""Launch Sushi the way it looks to somebody who has just installed it.

``main.py`` opens the app against whatever the user has accumulated: a CV, a
profile, a scored jobs database, an answer bank, a company corpus that has
grown by a couple of thousand boards since it shipped. That is the right
behaviour for the real app and the wrong one for judging the first run — you
cannot see the onboarding flow, the empty states, or how long the first scan
takes, because none of it happens twice.

So this launcher runs the *same* ``main.main()``, with every store Sushi
writes to pointed at a throwaway sandbox:

    <sandbox>/appdata/sushi/    the database, jobs.db, logos, the browser
                               profile, the log — normally under LOCALAPPDATA
    <sandbox>/Documents/Sushi/  the workspace: CVs, letters, resumes, exports
    <sandbox>/corpus/           the company corpus, seeded with the version
                               committed to this repo, which is what a new
                               install actually ships with

Nothing outside the sandbox is written, moved or deleted, so a clean run can
be done at any time without disturbing the real one. Playwright's browsers are
the single deliberate exception: they live under LOCALAPPDATA too, they are
hundreds of megabytes, and re-downloading them has nothing to do with being a
new user — so that path is pinned to the real install before the redirect.

The model is the other thing not worth re-doing. A clean database has no
provider in it, so the app would stop at "add a model" and want a key pasted
in before it could read so much as a CV — every single run. The Claude Code
CLI is already on this machine and already signed in, it is the backend the
app recommends first on a machine that has it, and its credentials live in
the home directory rather than anywhere this script redirects. So the fresh
database is handed that preset before the window opens: same seam the model
picker uses, ``providers.apply_preset``, so the app cannot tell the
difference between this and a user choosing Claude Code on the model step.

    python main_refreshed.py                    a new sandbox, kept on disk
    python main_refreshed.py --dir runs/first   use (and reuse) one place
    python main_refreshed.py --dir runs/first --reset   wipe it first
    python main_refreshed.py --empty-corpus     no companies to scan at all
    python main_refreshed.py --no-model         don't configure anything: see
                                                the model step a stranger gets
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Captured before anything is redirected: both Playwright's browsers and one
#: of the places the Claude Code CLI installs to are under the real one.
REAL_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")

#: Where the Claude Code CLI puts itself when it is not already on PATH —
#: the native installer first, then the package managers people install it
#: with. Launched from a shortcut rather than a shell, PATH is often the
#: shorter system one, and "not on PATH" would otherwise read as "not
#: installed" and send the fresh run back to asking for a key.
CLAUDE_DIRS = (
    Path.home() / ".local" / "bin",             # native installer
    Path.home() / ".claude" / "local",          # older local installer
    Path.home() / "AppData" / "Roaming" / "npm",
    Path(REAL_LOCALAPPDATA) / "Programs" / "claude" if REAL_LOCALAPPDATA else None,
    Path.home() / ".bun" / "bin",
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
)

#: Written at the top of every sandbox this script creates. ``--reset`` refuses
#: to delete a directory without one, because the flag takes an arbitrary path
#: from the command line and a typo there should not cost somebody a folder.
MARKER = ".sushi-sandbox"

#: Paths in :mod:`sushi.config` that must end up inside the sandbox. Checked
#: before the window opens, so a constant that escapes the redirect is a loud
#: failure rather than a quiet write into the real data directory.
SANDBOXED = ("DATA_DIR", "DB_PATH", "CV_DIR", "LETTERS_DIR",
             "BROWSER_PROFILE_DIR", "LOG_PATH", "CACHE_DIR",
             "COMPANIES_PATH", "DEFAULT_WORKSPACE")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="main_refreshed.py",
        description="Run Sushi as a first-time user, against a clean sandbox.")
    p.add_argument("--dir", metavar="PATH", default="",
                   help="sandbox to use (reused as-is if it already exists); "
                        "default is a new one under the system temp directory")
    p.add_argument("--reset", action="store_true",
                   help="empty the sandbox named by --dir before starting")
    p.add_argument("--no-model", action="store_true",
                   help="configure no backend at all, and hide the .env file "
                        "and any key in the environment, so the first run "
                        "asks for a model the way a stranger's would")
    p.add_argument("--empty-corpus", action="store_true",
                   help="start with no companies at all, rather than the "
                        "corpus a new install ships with")
    return p.parse_args(argv)


def _sandbox(args: argparse.Namespace) -> Path:
    """Return the sandbox root, created and marked."""
    if not args.dir:
        root = Path(tempfile.mkdtemp(
            prefix=f"sushi-fresh-{time.strftime('%Y%m%d-%H%M%S')}-"))
    else:
        root = Path(args.dir).expanduser().resolve()
        if args.reset and root.exists():
            if not (root / MARKER).exists():
                raise SystemExit(
                    f"refusing to reset {root}: it was not created by this "
                    f"script (no {MARKER} file). Delete it yourself if that "
                    "is really what you meant.")
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text(
        f"Sushi clean-run sandbox, created {time.strftime('%Y-%m-%d %H:%M')}\n",
        encoding="utf-8")
    return root


def _redirect(root: Path):
    """Point every store at *root*, and return the patched config module.

    Must run before anything else imports ``sushi``: most modules bind the
    paths they use at import time (``jobsdb.JOBS_DB_PATH``, ``storage.DB_PATH``
    and so on), so patching afterwards would catch some of them and not others.
    """
    data = root / "appdata"

    # Playwright keeps its browsers under LOCALAPPDATA. Pin it to the real one
    # before the override, or the first application attempt downloads Chromium.
    if REAL_LOCALAPPDATA:
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                              str(Path(REAL_LOCALAPPDATA) / "ms-playwright"))

    os.environ["LOCALAPPDATA"] = str(data)      # Windows
    os.environ["XDG_DATA_HOME"] = str(data)     # Linux

    from sushi import config

    # macOS resolves the data root from the home directory rather than from an
    # environment variable, so on that platform the constants are still the
    # real ones here. Rewriting by prefix rather than by name means a path
    # constant added to config later is redirected too, instead of silently
    # becoming the one leak in an otherwise clean run.
    old, new = config.DATA_DIR, data / config.APP_SLUG
    if old != new:
        for name, value in list(vars(config).items()):
            if isinstance(value, Path) and (value == old or old in value.parents):
                setattr(config, name, new / value.relative_to(old))

    # The workspace is the user's own folder, normally ~/Documents/Sushi. Only
    # the default needs moving: a fresh database has no workspace setting, and
    # `workspace()` reads this global when there is none.
    config.DEFAULT_WORKSPACE = root / "Documents" / "Sushi"

    # The corpus ships beside the code and is written back to as scans harvest
    # new boards, which is exactly the growth a clean run should not inherit.
    config.CACHE_DIR = root / "corpus"
    config.COMPANIES_PATH = config.CACHE_DIR / "companies.jsonl"

    for name in SANDBOXED:
        value = getattr(config, name)
        if root not in Path(value).parents:
            raise SystemExit(f"config.{name} escaped the sandbox: {value}")
    return config


def _seed_corpus(config, *, empty: bool) -> str:
    """Give the sandbox the corpus a new install would have, and describe it."""
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if empty:
        return "empty — discovery starts with nothing to scan"

    shipped = subprocess.run(
        ["git", "show", "HEAD:sushi/data/companies.jsonl"],
        cwd=REPO, capture_output=True)
    if shipped.returncode == 0 and shipped.stdout:
        config.COMPANIES_PATH.write_bytes(shipped.stdout)
        return "{:,} companies, as committed".format(_lines(shipped.stdout))

    # No git, or no such file in HEAD. The working copy is the only corpus
    # left to offer; say so, because it carries whatever previous runs added.
    source = config.PACKAGE_DIR / "data" / "companies.jsonl"
    if not source.is_file():
        return "empty — no corpus found to copy"
    shutil.copyfile(source, config.COMPANIES_PATH)
    return ("{:,} companies, copied from the working tree (git could not "
            "supply the committed one)".format(_lines(source.read_bytes())))


def _lines(blob: bytes) -> int:
    return blob.count(b"\n")


def _hide_key() -> None:
    """Make the machine look like one that has never had a key on it.

    ``sushi.env`` searches the working directory and every parent for an
    ``.env_amm``, which on a development checkout it will certainly find, and
    ``load_settings`` falls back to the process environment after that.
    """
    from sushi import config, env
    env.find_env_file = lambda: None
    for var in config.ENV_KEYS.values():
        if var:
            os.environ.pop(var, None)


def _find_claude() -> Path | None:
    """The Claude Code CLI on this machine, wherever it was installed."""
    on_path = shutil.which("claude")
    if on_path:
        return Path(on_path)
    for d in CLAUDE_DIRS:
        if d is None or not d.is_dir():
            continue
        # .exe and .cmd first: on Windows the extensionless file beside them
        # is the shell script, which `subprocess.run` cannot execute.
        for name in ("claude.exe", "claude.cmd", "claude.bat", "claude"):
            exe = d / name
            if exe.is_file():
                return exe
    return None


def _use_claude_code() -> str:
    """Point the fresh install at the Claude Code CLI, and say what happened.

    Written with ``providers.apply_preset`` rather than four ``set_setting``
    calls so this stays correct if the preset changes: it is the same function
    the model picker calls when a user chooses Claude Code themselves.
    """
    exe = _find_claude()
    if exe is None:
        return ("Claude Code not found — the first run will ask for a model "
                "(looked in: PATH, "
                + ", ".join(str(d) for d in CLAUDE_DIRS if d) + ")")

    # `claude_code.available()` asks PATH and nothing else, and everything
    # downstream — the model picker's "installed?" check, `llm_enabled`, the
    # loopback shim — goes through it. Putting the directory on PATH is
    # therefore what makes the whole chain work, not just this one call.
    if not shutil.which("claude"):
        os.environ["PATH"] = str(exe.parent) + os.pathsep + os.environ.get("PATH", "")

    from sushi import claude_code, providers
    preset = providers.by_name("Claude Code")
    if preset is None or not claude_code.available():   # renamed, or a shim
        return f"found {exe}, but Sushi could not use it — it will ask instead"
    providers.apply_preset(preset)
    return f"Claude Code at {exe} ({preset.model}), no key needed"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    root = _sandbox(args)
    config = _redirect(root)
    corpus = _seed_corpus(config, empty=args.empty_corpus)
    if args.no_model:
        _hide_key()
        backend = "none (--no-model) — the first run asks for one"
    else:
        backend = _use_claude_code()

    print(f"Sushi, fresh install\n  sandbox:   {root}")
    print(f"  app data:  {config.DATA_DIR}")
    print(f"  workspace: {config.DEFAULT_WORKSPACE}")
    print(f"  corpus:    {corpus}")
    print(f"  model:     {backend}")
    print(f"  log:       {config.LOG_PATH}")
    print("Delete the sandbox when you are done with it.\n", flush=True)

    # The real entry point, unchanged: this launcher is about where the data
    # goes, not about what the app does with it.
    import main as launcher
    return launcher.main()


if __name__ == "__main__":
    raise SystemExit(main())
