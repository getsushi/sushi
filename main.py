"""Launch Sushi."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _use_project_venv() -> None:
    """Re-launch under the project's .venv if one exists and we're not in it.

    ``python main.py`` with the system interpreter otherwise starts fine and
    only fails at Apply, with ``No module named 'browser_use'``.
    """
    here = Path(__file__).resolve().parent
    venv_py = here / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if venv_py.exists() and Path(sys.prefix).resolve() != (here / ".venv").resolve():
        import subprocess
        sys.exit(subprocess.call([str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]]))


_use_project_venv()

from PyQt6.QtWidgets import QApplication  # noqa: E402

from sushi import apply_qt_stack_size
from sushi.config import LOG_PATH, ensure_dirs
from sushi.ui.main_window import MainWindow


def main() -> int:
    ensure_dirs()
    # Before any thread exists: the scorer runs out of C stack on the default
    # one and takes the process with it. See sushi.THREAD_STACK_BYTES.
    apply_qt_stack_size()
    app = QApplication(sys.argv)
    app.setApplicationName("Sushi")
    from sushi.ui.brand import app_icon
    app.setWindowIcon(app_icon())
    if sys.platform == "win32":
        # Its own taskbar group, so Windows shows Sushi's icon, not Python's.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Sushi.App")
        except Exception:
            pass

    # Before anything else that can fail. PyQt aborts the process on an
    # unhandled exception in a slot, and with no console attached that looks
    # like the window simply closing — so logging has to be in place before
    # the first widget exists.
    from sushi import diagnostics
    diagnostics.install(LOG_PATH, on_error=_report)

    # Register the display face if it is bundled, then rebuild the stylesheet
    # against whatever actually loaded. Body type is the platform's own sans,
    # as in the Typing Avo design, so there is nothing to register for it.
    from sushi.ui import theme
    from sushi.ui.fonts import DISPLAY, load_fonts
    loaded = load_fonts()
    theme.refresh_stylesheet(display=loaded.get("display", DISPLAY),
                            body=loaded.get("body", ""))
    # Popups are top-level windows and take their colours from here, not from
    # the window's stylesheet — without this they arrive in Windows' dark
    # palette. See theme.apply_palette.
    theme.apply_palette(app)

    # The splash goes up *after* the stylesheet and before the window, which
    # is the slow part: MainWindow builds every page in its constructor.
    from sushi.ui.splash import Splash
    splash = Splash()
    splash.show()
    splash.step("Loading your profile")

    # Browsers stranded by a previous run hold the one profile directory
    # Chromium allows, so every application would fail to start until they
    # are cleared. By definition nothing in this session owns one yet.
    splash.step("Tidying up")
    try:
        from sushi.autofill import close_orphaned_browsers
        closed = close_orphaned_browsers()
        if closed:
            import logging
            logging.getLogger("sushi").info(
                "closed %d browser(s) left over from a previous run", closed)
    except Exception:
        pass

    win = MainWindow()
    splash.step("Ready")
    splash.finish(win)
    return app.exec()


def _report(title: str, detail: str) -> None:
    """Show an unhandled exception rather than dying behind the user's back."""
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from sushi import diagnostics

    if QApplication.instance() is None:
        return
    box = QMessageBox()
    box.setWindowTitle("Sushi hit a problem")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(title)
    box.setInformativeText(
        "Sushi is still running — whatever you were doing may not have "
        f"finished.\n\nThe full details are in:\n{diagnostics.LOG_FILE}")
    box.setDetailedText(detail)
    box.exec()


if __name__ == "__main__":
    raise SystemExit(main())
