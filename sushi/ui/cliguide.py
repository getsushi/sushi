"""Setting up Claude Code, Codex, OpenCode or Ollama, for someone who never has.

One button, *Set it up for me*, does the whole thing and says what it is
doing as it goes:

- **Install** — Node.js too when the tool needs it, through the system's own
  installer (winget on Windows, Homebrew on a Mac); then the tool.
- **Sign in** (coding agents) — opens the tool's own sign-in, because that
  is a browser hand-off only the tool can drive, then watches for it to
  finish and carries on by itself.
- **Download a model** (Ollama) — the one that fits this PC, chosen from its
  memory, graphics card and free disk, downloaded in the app with a bar.
- **Test** — one real request. A tick here is the only proof the chain works.

Every step can still be done by hand from the same rows, for when automatic
cannot work (no winget, a locked-down work laptop).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QProgressBar,
                             QVBoxLayout, QWidget)

from .. import setup_agent
from . import theme
from .components import button, card, faint

NODE_URL = "https://nodejs.org/en/download"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class _Bus(QObject):
    """Carries results from worker threads back to the UI thread."""
    done = pyqtSignal(str, bool, str)       # step, ok, message
    line = pyqtSignal(str, str)             # step, live text
    progress = pyqtSignal(float, str)       # download fraction, words
    hw = pyqtSignal(object, object)         # Hardware, Pick


def open_terminal(cmd: str) -> None:
    """A visible terminal window running ``cmd``, left open afterwards."""
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/c", "start", "Sushi sign-in", "cmd", "/k", cmd])
    elif sys.platform == "darwin":
        subprocess.Popen(["osascript", "-e",
                          f'tell application "Terminal" to do script "{cmd}"'])
    else:
        for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
            if shutil.which(term):
                subprocess.Popen([term, "-e", f"bash -c '{cmd}; exec bash'"])
                return


class CliGuide(QWidget):
    """The steps for one runtime (claude_code / local_llm module or a CliAgent)."""

    ready = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("CliGuide")
        self.setStyleSheet("QWidget#CliGuide{background:transparent;}")
        self._rt = None
        self._auto = False          # running the whole chain by itself
        self._busy = False
        self._pick = None
        self._bus = _Bus()
        self._bus.done.connect(self._finished)
        self._bus.line.connect(lambda k, t: self._tick(k, "busy", t))
        self._bus.progress.connect(self._on_progress)
        self._bus.hw.connect(self._on_hardware)
        self._watch = QTimer(self)
        self._watch.setInterval(2500)
        self._watch.timeout.connect(self._check_signed_in)
        self._watch_left = 0

        box = card("Sunken", margins=(16, 14, 16, 14), spacing=10)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)
        lay = box.layout()

        head = QHBoxLayout()
        head.setSpacing(10)
        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        self.title = QLabel("")
        self.title.setStyleSheet(f"color:{theme.INK};font-weight:600;"
                                 f"font-size:{theme.SIZE_BODY}px;background:transparent;")
        tcol.addWidget(self.title)
        self.subtitle = faint("")
        self.subtitle.setWordWrap(True)
        tcol.addWidget(self.subtitle)
        head.addLayout(tcol, 1)
        self.auto_btn = button("Set it up for me", "Primary", self._run_all)
        head.addWidget(self.auto_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)

        # Ollama only: what this PC can run, and the model to download.
        self.hw_row = QWidget()
        self.hw_row.setStyleSheet("background:transparent;")
        hl = QVBoxLayout(self.hw_row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        self.hw_text = faint("Checking this PC…")
        self.hw_text.setWordWrap(True)
        hl.addWidget(self.hw_text)
        mrow = QHBoxLayout()
        mrow.setSpacing(8)
        lbl = faint("Model to download")
        lbl.setWordWrap(False)
        mrow.addWidget(lbl)
        self.pull_box = QComboBox()
        self.pull_box.setEditable(True)
        self.pull_box.setMinimumWidth(220)
        mrow.addWidget(self.pull_box, 1)
        hl.addLayout(mrow)
        lay.addWidget(self.hw_row)

        self.rows: dict[str, tuple[QLabel, QLabel, QWidget]] = {}
        self.install_btn = button("Install", "Chip", self._install)
        self.copy_btn = button("Copy command", "Chip", self._copy)
        self._row("install", "Install", [self.copy_btn, self.install_btn])
        self.login_btn = button("Open sign-in", "Chip", self._login)
        self._row("login", "Sign in", [self.login_btn])
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.hide()
        lay.addWidget(self.rows["install"][2])
        lay.addWidget(self.rows["login"][2])
        lay.addWidget(self.bar)
        self.test_btn = button("Test it", "Chip", self._test)
        self._row("test", "Check it works", [self.test_btn])
        lay.addWidget(self.rows["test"][2])
        self.hide()

    def _row(self, key: str, title: str, actions: list[QWidget]) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        mark = QLabel("○")
        mark.setFixedWidth(16)
        mark.setStyleSheet(f"color:{theme.FAINT};background:transparent;font-size:14px;")
        h.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        head = QLabel(title)
        head.setStyleSheet(f"color:{theme.INK};font-weight:600;background:transparent;")
        text = faint("")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(head)
        col.addWidget(text)
        h.addLayout(col, 1)
        for a in actions:
            h.addWidget(a, 0, Qt.AlignmentFlag.AlignTop)
        self.rows[key] = (mark, text, w)
        self._heads = getattr(self, "_heads", {})
        self._heads[key] = head
        return w

    # --- state ---------------------------------------------------------------

    def _provider(self) -> str:
        return {"Claude Code": "claude_code", "Codex": "codex",
                "OpenCode": "opencode", "Ollama": "ollama"}.get(self._rt.NAME, "")

    def _is_ollama(self) -> bool:
        return self._rt is not None and self._provider() == "ollama"

    def show_for(self, runtime) -> None:
        """Show the steps for this runtime, or hide for a non-CLI option."""
        if self._busy and runtime is self._rt:
            return
        self._rt = runtime if runtime is not None and hasattr(runtime, "LOGIN") else None
        self._watch.stop()
        self._auto = False
        if self._rt is None:
            self.hide()
            return
        if self._state()[0] == "ready":
            self.hide()
            return
        self.title.setText(f"Set up {self._rt.NAME}")
        self.subtitle.setText(
            "Sushi installs it, downloads a model that fits this PC and tests it. "
            "Free, and nothing leaves your computer." if self._is_ollama() else
            "Sushi installs it for you, then opens its sign-in. Sign in in your "
            "browser and Sushi carries on by itself.")
        self.hw_row.setVisible(self._is_ollama())
        if self._is_ollama() and self._pick is None:
            threading.Thread(target=self._read_hardware, daemon=True).start()
        self.refresh()
        self.show()

    def _read_hardware(self) -> None:
        hw = setup_agent.hardware()
        self._bus.hw.emit(hw, setup_agent.recommend(hw))

    def _on_hardware(self, hw, pick) -> None:
        self._pick = pick
        self.hw_text.setText(f"This PC: {hw.summary()}.\n"
                             + (f"Recommended: {pick.model} ({pick.size_gb:g} GB download). "
                                f"{pick.speed}" if pick.fits else f"⚠ {pick.speed}"))
        self.hw_text.setStyleSheet(f"color:{theme.WARN if not pick.fits else theme.FAINT};"
                                   "background:transparent;")
        self.pull_box.blockSignals(True)
        self.pull_box.clear()
        options = [pick.model] + [t[0] for t in setup_agent._TIERS if t[0] != pick.model]
        self.pull_box.addItems(options)
        self.pull_box.blockSignals(False)

    def _state(self) -> tuple[str, str]:
        from ..cli_agents import status
        return status(self._provider())

    def _tick(self, key: str, state: str, text: str) -> None:
        mark, label, _w = self.rows[key]
        glyph, colour = {"ok": ("✓", theme.ACCENT), "todo": ("○", theme.FAINT),
                         "busy": ("…", theme.MUTED), "bad": ("!", theme.WARN)}[state]
        mark.setText(glyph)
        mark.setStyleSheet(f"color:{colour};background:transparent;font-size:14px;"
                           f"font-weight:700;")
        label.setText(_ANSI.sub("", text))

    def _installed(self) -> bool:
        rt = self._rt
        return bool(getattr(rt, "installed", rt.available)())

    def refresh(self) -> None:
        rt = self._rt
        if rt is None or self._busy:
            return
        installed = self._installed()
        steps, problem = setup_agent.install_plan(self._provider())
        if installed:
            self._tick("install", "ok", "Installed.")
        elif problem:
            self._tick("install", "bad", problem)
        else:
            what = " then ".join(s.label.replace("Installing ", "").split(" (")[0] for s in steps)
            self._tick("install", "todo", f"Sushi installs {what} for you. Takes a minute or two.")
        self.install_btn.setVisible(not installed and not problem)
        self.copy_btn.setVisible(not installed)

        self._heads["login"].setText("Download a model" if self._is_ollama() else "Sign in")
        self.login_btn.setText("Download" if self._is_ollama() else "Open sign-in")
        state = self._state()[0]
        if installed and state == "ready":
            self._tick("login", "ok", "Done.")
        elif self._is_ollama():
            self._tick("login", "todo", "Downloads the model above, inside Sushi.")
        else:
            self._tick("login", "todo", f"Opens “{rt.LOGIN}”. Sign in in your browser; "
                                        "Sushi notices when you have.")
        self.login_btn.setEnabled(installed)
        self._tick("test", "todo", "Sends one tiny request through it.")
        self.test_btn.setEnabled(installed)
        self.auto_btn.setEnabled(not problem or installed)
        self.auto_btn.setText("Set it up for me")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for b in (self.auto_btn, self.install_btn, self.login_btn, self.test_btn):
            b.setEnabled(not busy)
        if busy:
            self.auto_btn.setText("Setting up…")

    # --- the whole chain ------------------------------------------------------

    def _run_all(self) -> None:
        self._auto = True
        if not self._installed():
            self._install()
        else:
            self._after_install()

    def _after_install(self) -> None:
        if self._is_ollama():
            from .. import local_llm
            want = self.pull_box.currentText().strip()
            have = local_llm.models()
            if want and any(m == want or m.split(":")[0] == want for m in have):
                self._tick("login", "ok", f"{want} is already downloaded.")
                self._test()
            else:
                self._login()
        elif self._state()[0] == "ready":
            self._tick("login", "ok", "Already signed in.")
            self._test()
        else:
            self._login()

    # --- actions -------------------------------------------------------------

    def _copy(self) -> None:
        steps, _p = setup_agent.install_plan(self._provider())
        cmd = " && ".join(s.cmd for s in steps) or self._rt.INSTALL
        QGuiApplication.clipboard().setText(cmd)
        self._tick("install", "todo", f"Copied. Paste it into a terminal:  {cmd}")

    def _install(self) -> None:
        self._set_busy(True)
        self._tick("install", "busy", "Starting…")
        prov = self._provider()

        def work():
            ok, msg = setup_agent.install(prov, lambda t: self._bus.line.emit("install", t))
            self._bus.done.emit("install", ok, msg)
        threading.Thread(target=work, daemon=True).start()

    def _login(self) -> None:
        if self._is_ollama():
            return self._download()
        try:
            open_terminal(self._rt.LOGIN)
        except Exception as exc:                        # noqa: BLE001
            self._tick("login", "bad", f"Could not open a window ({exc}). "
                                       f"Run “{self._rt.LOGIN}” yourself.")
            return
        self._tick("login", "busy", "A window opened with the sign-in. Finish it in "
                                    "your browser; Sushi is watching for it.")
        self._watch_left = 120          # five minutes at 2.5 s
        self._watch.start()

    def _check_signed_in(self) -> None:
        self._watch_left -= 1
        if self._state()[0] == "ready":
            self._watch.stop()
            self._tick("login", "ok", "Signed in.")
            self._test()
        elif self._watch_left <= 0:
            self._watch.stop()
            self._tick("login", "todo", "Not signed in yet. Finish it, then press Test it.")

    def _download(self) -> None:
        model = self.pull_box.currentText().strip() or "qwen2.5:7b"
        self._set_busy(True)
        self.bar.setValue(0)
        self.bar.show()
        self._tick("login", "busy", f"Starting the download of {model}…")

        def work():
            ok, msg = setup_agent.pull(model, lambda f, t: self._bus.progress.emit(f, t))
            self._bus.done.emit("download", ok, msg if not ok else model)
        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, frac: float, words: str) -> None:
        self.bar.setValue(int(frac * 1000))
        self._tick("login", "busy", words)

    def _test(self) -> None:
        self._set_busy(True)
        self._tick("test", "busy", "Asking it to say OK… up to a minute.")
        rt = self._rt
        model = self.pull_box.currentText().strip() if self._is_ollama() else ""

        def work():
            try:
                out = rt.ask("Reply with exactly the word OK.", model=model or rt.best_model())
                self._bus.done.emit("test", "ok" in out.lower(), out[:80])
            except Exception as exc:                    # noqa: BLE001
                self._bus.done.emit("test", False, str(exc)[:200])
        threading.Thread(target=work, daemon=True).start()

    def _finished(self, step: str, ok: bool, msg: str) -> None:
        self._set_busy(False)
        if step == "install":
            if ok:
                self.refresh()
                self._tick("install", "ok", "Installed.")
                if self._auto:
                    self._after_install()
                return
            self.refresh()
            self._tick("install", "bad", f"Could not install: {msg}")
            self._auto = False
        elif step == "download":
            self.bar.hide()
            if ok:
                self._tick("login", "ok", f"{msg} downloaded.")
                self.downloaded = msg
                if self._auto:
                    self._test()
                else:
                    self.refresh()
                    self._tick("login", "ok", f"{msg} downloaded.")
            else:
                self._tick("login", "bad", msg)
                self._auto = False
        elif step == "test":
            self._auto = False
            if ok:
                self._tick("test", "ok", "Working. Sushi switched to it.")
                self.auto_btn.setText("Done")
                self.auto_btn.setEnabled(False)
                self.ready.emit()
            else:
                self._tick("test", "bad", f"Not yet: {msg}")
