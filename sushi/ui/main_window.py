"""The Sushi window — the earlier design's shell.

The earlier design's chrome is deliberately thin: a 64px header carrying the brand, a
disclosure button and nothing else; a drawer on the left holding every
destination; and the whole screen below given to one centred column. Sushi had
its navigation across the top, which is where this differs most — the nav is
now the sidebar, and the header keeps only the toggle, the brand, the current
location and the model pill.

The assistant rail moved from the left to the right. With navigation on the
left it was two rails competing for the same edge, and the earlier design already reserves
the right-hand gutter for the thing that talks back.

The window is still the controller: pages call into it for shared state and
background work, and it owns the role actions so every page behaves the same.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import logging
import time

from PyQt6.QtCore import Qt, QThreadPool, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QColor, QDesktopServices, QKeySequence, QPainter,
                         QShortcut)
from PyQt6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel,
                             QProgressBar, QSizePolicy, QStackedWidget,
                             QVBoxLayout, QWidget)

from .. import learning, storage
from ..assistant import AssistantContext, parse_pairs
from ..config import DATA_DIR, ensure_dirs, load_settings
from ..cv import SUPPORTED, build_profile, extract_text
from ..extras import (RESUME_MODE_WORDS, autoapply_preview,
                      load_apply_settings, load_autoapply, note_applied)
from ..letters import render_pdf, write_letter
from ..llm import LLM
from ..models import Profile, Role
from . import dialogs, icons, theme
from .assistant_panel import AssistantPanel
from .brand import SushiMark
from .components import ElidedLabel, button, icon_button, label, muted
from .controls import WindowButtons, WindowDrag
from .locations import LOCATIONS_CHOSEN
from .pages.alljobs import AllJobsPage
from .pages.auto_apply import AutoApplyPage
from .pages.dashboard import DashboardPage
from .pages.manual import ManualPage
from .pages.profile import ProfilePage
from .pages.settings import SettingsPage
from .pages.tracker import TrackerPage
from .sidebar import Sidebar
from .workers import Worker


log = logging.getLogger(__name__)


def _log_lines() -> int:
    """How much has been logged this session — the console strip's counter."""
    from .. import diagnostics
    path = diagnostics.LOG_FILE
    if path is None or not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


class TitleBar(WindowDrag, QFrame):
    """The 64px header — also what you grab to move the window."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window_host = window


class HeaderGlow(QWidget):
    """The warm hairline under the header.

    A flat 1px rule would separate the header from the page just as well and
    look like every other border in the app. This is the one place worth a
    light source: the accent at a low alpha, with a short falloff beneath
    it. It reads as the header sitting slightly above the page rather than
    as a line drawn between two panes.

    The line used to fade to nothing at both ends — brightest in the middle,
    gone by either edge. At the width of a title bar that is not a light
    source, it is a line that stops: the quarter nearest each corner sits
    under alpha 60 and vanishes against the paper, so the header looked
    separated in the middle of the window and unfinished at the sides, and
    the brand and the window buttons — which live at the two ends — were the
    two things it never reached. It is even now, across the full width.
    """

    #: The line itself, then the glow that bleeds down from it.
    _LINE = 1
    _FALL = 7
    #: The line's own alpha, and the glow's at the first row under it.
    _ALPHA = 150
    _GLOW = 44

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(self._LINE + self._FALL)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event) -> None:  # noqa: N802
        w = self.width()
        if w <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.BG))

        accent = QColor(theme.ACCENT)

        def wash(alpha: int) -> QColor:
            return QColor(accent.red(), accent.green(), accent.blue(), alpha)

        p.fillRect(0, 0, w, self._LINE, wash(self._ALPHA))
        # The bleed: the same colour again, weaker each row down. The glow
        # is the vertical falloff now and only that, which is what it was
        # doing anyway — the horizontal fade was costing the line its ends
        # and buying nothing the falloff was not already providing.
        for i in range(self._FALL):
            alpha = int(self._GLOW * (1 - i / self._FALL) ** 2)
            if alpha <= 0:
                break
            p.fillRect(0, self._LINE + i, w, 1, wash(alpha))
        p.end()


def _pretty_report(message: str) -> str:
    """The agent's report, as something a person reads.

    It arrives as "STATUS: filled / UNANSWERED: ... / GUESSED: q = a", which
    is a format for parsing, and it was shown exactly like that.
    """
    import re

    heads = {"unanswered": "Left for you:",
             "guessed": "Sushi worked these out, check them:",
             "notes": "Notes:"}
    out: list[str] = []
    section = ""
    for raw in (message or "").splitlines():
        line = raw.strip(" -*\t")
        if not line:
            continue
        m = re.match(r"^(status|unanswered|guessed|notes?)\s*:\s*(.*)$", line, re.I)
        if m:
            section = m.group(1).lower()
            if section == "note":
                section = "notes"
            rest = m.group(2).strip()
            if section == "status":
                continue
            out.append("\n" + heads[section])
            if rest and rest.lower() not in ("none", "n/a", "-"):
                out.append("• " + rest.replace(" = ", " → "))
            continue
        if section == "status":
            continue
        out.append(("• " if section else "") + line.replace(" = ", " → "))
    text = "\n".join(out).strip()
    return text or (message or "").strip()


def _keep_open_questions(result) -> None:
    """Remember what a run could not answer, for the Profile page."""
    from .. import learning
    try:
        learning.note_unanswered(getattr(result, "unanswered", None) or [])
        learning.note_guesses(getattr(result, "guessed", None) or [])
        learning.note_declined(getattr(result, "declined", None) or [])
    except Exception:                                   # noqa: BLE001
        log.debug("could not keep the open questions", exc_info=True)


class MainWindow(QWidget):
    profile_changed = pyqtSignal()
    matches_changed = pyqtSignal()
    apps_changed = pyqtSignal()
    scan_state_changed = pyqtSignal(bool)
    #: running, and a line describing what it is doing
    autoapply_changed = pyqtSignal(bool, str)
    #: The whole run, re-emitted whenever any row of it changes.
    autoapply_rows_changed = pyqtSignal(list)
    #: Roles found part-way through a scan, emitted from the scan's thread.
    scan_found = pyqtSignal(list)
    #: (boards read, boards due) during a refresh, from the scan's thread.
    scan_progress = pyqtSignal(int, int)
    #: (url, page title) when the Fill button on a page is pressed. Emitted
    #: from the browser loop's thread or the bridge's, so it is a signal
    #: rather than a call: Qt hands it to the UI thread, which is the only
    #: one allowed to open a dialog or touch a widget.
    fill_requested = pyqtSignal(str, str)

    def __init__(self) -> None:
        super().__init__()
        ensure_dirs()
        self.setWindowTitle("Sushi · your job search, handled")
        self.setStyleSheet(theme.STYLESHEET)
        self.setAcceptDrops(True)

        # The earlier design draws its own title bar, so the system one goes. The resize
        # borders it takes with it are put back in nativeEvent below, and the
        # corners are rounded by DWM in _round_corners.
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self._size_to_screen(1420, 920)

        self.pool = QThreadPool.globalInstance()
        self._profile: Profile | None = storage.load_profile()
        # An install from before the CV library had one CV in a setting and
        # one profile beside it. Both are real; adopting them means nobody
        # has to choose their own CV again to get a library with it in.
        storage.adopt_legacy_cv(self._profile)
        self._matches: list[Role] = []
        self._pages: dict[str, QWidget] = {}
        self._current = "Home"
        self._scan_worker = None

        # Onboarding and the app proper are siblings, so "Restart onboarding"
        # is a stack switch rather than a second window to keep in sync.
        self.root_stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.root_stack)

        self.shell = self._build_shell()
        self.root_stack.addWidget(self.shell)

        self._wire_signals()
        self._listen_for_fills()
        # Parse the company corpus off the UI thread now, so the first open
        # of Settings (which counts it) is not half a second of frozen window.
        import threading
        from .. import discovery
        threading.Thread(target=discovery.load_companies, daemon=True,
                         name="sushi-warm-corpus").start()
        try:
            n = storage.reset_interrupted()
            if n:
                log.info("startup: %d fill(s) left half-done were put back in the queue", n)
        except Exception as exc:                       # noqa: BLE001
            log.info("startup: could not tidy interrupted fills: %s", exc)
        self._load_feed()
        self.navigate("Home")
        self._check_model()
        self._refresh_identity()

        QShortcut(QKeySequence("Ctrl+B"), self, activated=self._toggle_sidebar)

        # A CV already read is setup done, however the wizard was left. The
        # flag was only written on its last screen, so anyone who attached a
        # CV and then closed the window got the whole wizard again on every
        # start.
        if storage.get_setting("onboarded", "") != "1" and self._profile:
            storage.set_setting("onboarded", "1")
        if storage.get_setting("onboarded", "") != "1":
            self.restart_onboarding()
        else:
            # Let the window paint first; a scan started inside __init__ makes
            # the app look frozen for the second it takes to spin up.
            QTimer.singleShot(400, self.maybe_autoscan)
            QTimer.singleShot(600, self.rescore_if_stale)

        # "Refreshed daily" was only true of a window that got closed and
        # reopened: the autoscan ran at startup and never again, so a machine
        # left on for a week served a week-old table. An hour is the cadence
        # of the *check*; `maybe_autoscan` does nothing until a board is
        # actually due, which is still once a day per board.
        self._daily_timer = QTimer(self)
        self._daily_timer.setInterval(60 * 60 * 1000)
        self._daily_timer.timeout.connect(self.maybe_autoscan)
        self._daily_timer.start()

    # --- window geometry and chrome -------------------------------------------

    #: How close to an edge counts as a grab for resizing, in logical pixels.
    #: Windows' own borders are about this wide, and a frameless window with
    #: rounded corners needs the generous end of the range: the visible edge
    #: curves away from the true rectangle, so the first few pixels a user
    #: aims at near a corner are outside the window entirely.
    _RESIZE_MARGIN = 8

    def _size_to_screen(self, width: int, height: int) -> None:
        """Fit the window to the screen it opens on, then centre it.

        A hard-coded size taller than the usable area opens with the bottom of
        the window — status bar, primary actions — below the screen edge, which
        on a 1366x768 laptop is most of a page.
        """
        from PyQt6.QtWidgets import QApplication

        self.setMinimumSize(980, 640)
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(width, height)
            return
        area = screen.availableGeometry()
        w = min(width, max(980, area.width() - 48))
        h = min(height, max(640, area.height() - 48))
        self.resize(w, h)
        self.move(area.x() + (area.width() - w) // 2,
                  area.y() + max(0, (area.height() - h) // 2))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._round_corners()
        self._fit_rails()

    # Below this the sidebar and the assistant together leave the content well
    # too narrow for a two-card grid, so one of them stands down.
    _NARROW = 1180
    _VERY_NARROW = 1000

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_rails()

    def _fit_rails(self) -> None:
        """Collapse the rails on a narrow window — unless the user said otherwise.

        The earlier design's drawer does the same thing in a media query. The ``_rails_pinned``
        flag is what stops this from fighting someone who deliberately opened
        the sidebar on a small screen: once they touch a toggle, the window
        stops having opinions.
        """
        if getattr(self, "_rails_pinned", False) or not hasattr(self, "sidebar"):
            return
        w = self.width()
        self.sidebar.set_collapsed(w < self._NARROW)
        # Narrow only ever *takes* the assistant away. It used to put it back
        # on any resize over the threshold, which with the panel now closed by
        # default would have had it reappear the first time the window moved.
        if w < self._VERY_NARROW:
            self._set_assistant_visible(False, remember=False)
        elif self._assistant_wanted:
            self._set_assistant_visible(True, remember=False)

    def _round_corners(self) -> None:
        """Ask DWM for Windows 11's rounded corners.

        A frameless window is a bare rectangle otherwise, which next to every
        other window on the desktop reads as broken rather than minimal. Fails
        quietly everywhere this attribute does not exist.
        """
        try:
            import ctypes
            import ctypes.wintypes

            preference = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.wintypes.HWND(int(self.winId())),
                ctypes.c_uint(33),  # DWMWA_WINDOW_CORNER_PREFERENCE
                ctypes.byref(preference), ctypes.sizeof(preference))
        except Exception:
            pass

    def toggle_maximise(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self.win_btns.set_maximised(self.isMaximized())

    def drag_window_by(self, dx: int, dy: int) -> None:
        """Apply a drag the title bar measured.

        A maximised window restores first, as dragging one does everywhere else
        on the desktop — otherwise the drag silently does nothing and the
        window looks stuck.
        """
        if self.isMaximized():
            self.showNormal()
            self.win_btns.set_maximised(False)
            return
        pos = self.pos()
        self.move(pos.x() + dx, pos.y() + dy)

    def nativeEvent(self, event_type, message):  # noqa: N802
        """Give the frameless window its eight resize grips back.

        FramelessWindowHint removes the non-client area, and with it every
        resize border. Answering WM_NCHITTEST ourselves puts them back, and
        Windows does the resizing — so it behaves like a real window rather
        than like an app imitating one.

        Only the borders are claimed. The draggable region is deliberately not
        reported as HTCAPTION: the top bar is full of things you click, and a
        caption hit test would swallow those clicks before they arrive.

        **Never call ``super().nativeEvent()`` here.** Under PyQt6 that is an
        immediate access violation — the base implementation wants to write
        through a ``qintptr*`` the binding does not hand it — and the window
        dies the instant it is shown. ``(False, 0)`` means "not handled, carry
        on", which is what the base call would have returned anyway. (PySide6
        does not have this problem, which is why the original of this code
        could make the call.)
        """
        unhandled = (False, 0)
        try:
            kind = bytes(event_type)
        except Exception:
            return unhandled
        if kind != b"windows_generic_MSG":
            return unhandled

        try:
            import ctypes
            import ctypes.wintypes

            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message != 0x0084:  # WM_NCHITTEST
                return unhandled
            if self.isMaximized() or self.isFullScreen():
                return unhandled

            # lParam packs two *signed* 16-bit screen coordinates. Masking
            # alone gives 65000-odd for anything left of or above the origin,
            # which is every window on a monitor placed to the left.
            x = ctypes.c_short(msg.lParam & 0xFFFF).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value

            # Windows sends physical pixels; Qt's geometry is logical. Bring
            # the *point* into Qt's space rather than pushing the rectangle
            # into Windows' — one division instead of four multiplications,
            # and the margin then means the same number of visible pixels at
            # every display scale. Scaled up, the old form shrank the grab
            # target rather than growing it, which is why the edges could be
            # unusable on one machine and fine on another.
            ratio = self.devicePixelRatioF() or 1.0
            lx, ly = x / ratio, y / ratio

            rect = self.frameGeometry()
            margin = self._RESIZE_MARGIN
            left, top = rect.left(), rect.top()
            right, bottom = rect.right(), rect.bottom()

            on_left = left <= lx < left + margin
            on_right = right - margin < lx <= right
            on_top = top <= ly < top + margin
            on_bottom = bottom - margin < ly <= bottom

            # Corners first: they overlap the edges and are the smaller target.
            if on_top and on_left:
                return True, 13     # HTTOPLEFT
            if on_top and on_right:
                return True, 14     # HTTOPRIGHT
            if on_bottom and on_left:
                return True, 16     # HTBOTTOMLEFT
            if on_bottom and on_right:
                return True, 17     # HTBOTTOMRIGHT
            if on_left:
                return True, 10     # HTLEFT
            if on_right:
                return True, 11     # HTRIGHT
            if on_top:
                return True, 12     # HTTOP
            if on_bottom:
                return True, 15     # HTBOTTOM
        except Exception:
            # A hit test that raises makes the window unusable, so fall back to
            # Qt's answer rather than let it propagate.
            pass

        return unhandled

    # --- shell ----------------------------------------------------------------

    def _build_shell(self) -> QWidget:
        shell = QWidget()
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.navigate.connect(self.navigate)
        self.sidebar.toggle_requested.connect(self._toggle_sidebar)

        # The sidebar runs the full height of the window, top to bottom, as
        # the wireframe's does. The title bar (window buttons, assistant) sits
        # over the content column only.
        frame = QHBoxLayout()
        frame.setContentsMargins(0, 0, 0, 0)
        frame.setSpacing(0)
        outer.addLayout(frame, 1)
        frame.addWidget(self.sidebar)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        frame.addLayout(right, 1)
        right.addWidget(self._topbar())

        root = QHBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        right.addLayout(root, 1)

        centre = QVBoxLayout()
        centre.setContentsMargins(0, 0, 0, 0)
        centre.setSpacing(0)

        self.stack = QStackedWidget()
        from .pages.home import HomePage
        for cls in (HomePage, AutoApplyPage, ManualPage, DashboardPage, AllJobsPage,
                    TrackerPage, ProfilePage, SettingsPage):
            page = cls(self)
            page.ensure_built()
            self._pages[page.title] = page
            self.stack.addWidget(page)
        centre.addWidget(self.stack, 1)
        # What Sushi is doing, and how far the job search has got, along the
        # bottom of the content column only: the sidebar keeps its full height.
        centre.addWidget(self._status_line())

        cw = QWidget()
        cw.setLayout(centre)
        root.addWidget(cw, 1)

        self.assistant = AssistantPanel(self.assistant_context, self._run_action,
                                        on_close=self._toggle_assistant)
        # Closed until asked for: the sparkles button top right, Help in the
        # sidebar, or anything that calls `ask_assistant`.
        self.assistant.setVisible(False)
        root.addWidget(self.assistant)

        # No strip along the bottom: its log button and busy bar sit at the
        # right of the status line now, and the window ends at the content.
        return shell

    def _topbar(self) -> QWidget:
        # 64px tall with 16px of padding, as in the design — and the window's
        # drag handle, since there is no system title bar to grab.
        bar = TitleBar(self)
        bar.setObjectName("TopBar")
        bar.setFixedHeight(40)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)

        # The show/hide button moved into the sidebar. Kept, hidden, for
        # anything that still refers to it.
        self.toggle_btn = icon_button("panel-left", "Hide or show the sidebar  (Ctrl+B)",
                                      self._toggle_sidebar)
        self.toggle_btn.setVisible(False)

        # The brand, in the one place every app puts it: the title bar. It
        # stood in the sidebar header instead, which meant it disappeared when
        # the sidebar collapsed to a rail — and the bar it left behind opened
        # on the page name and nothing else.
        # The brand lives at the head of the sidebar now, as the wireframe
        # has it. The mark is kept (hidden) for anything that refers to it.
        self.brand_mark = SushiMark(22)
        self.brand_mark.setVisible(False)

        # No page name next to the brand. Every page opens with its own title
        # in 28px type an inch below, so the crumb was the same word twice —
        # and a breadcrumb with one level is not telling anyone where they are.
        # No "N left today" here either. It is the hero's second figure on the
        # Dashboard and the headline on Insane mode, and a number repeated in
        # the chrome of every page is one more thing to read past.
        lay.addStretch(1)

        # The way out of a run. Sushi leaves each filled form on screen on
        # purpose, which is right — and until now the only way to be finished
        # with them was to find the Chromium window yourself and close it, or
        # to quit Sushi. So the chrome carries the end of the job as well as
        # the start of it, and only while there is one to end.
        self.browsers_btn = button("", "Chip", self.close_browsers, icon="x")
        self.browsers_btn.setVisible(False)
        lay.addWidget(self.browsers_btn)

        # No model name in the bar. Which deployment is answering is a setting,
        # not a thing to read on every screen — it lives in Settings → Model,
        # and the status line already says whether one is configured at all.
        lay.addWidget(self._segment())

        self.win_btns = WindowButtons(self.showMinimized, self.toggle_maximise,
                                      self.close)
        lay.addWidget(self.win_btns)
        return bar

    def _segment(self) -> QWidget:
        """The title bar's grouped toggles.

        The design shows three slots. Two of them are wired to things that
        exist — the assistant and Insane mode. The third in the mockup is a
        light/dark switch, which needs a second palette and a rebuild of every
        widget that captured a colour at construction; it is not here rather
        than here and dead.
        """
        seg = QFrame()
        seg.setObjectName("Segment")
        seg.setFixedHeight(32)
        row = QHBoxLayout(seg)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(3)

        # Off at startup, because the panel is: a lit button over a closed
        # panel is the chrome contradicting the window.
        self.assistant_btn = icon_button(
            "sparkles", "Ask Sushi for help", self._toggle_assistant,
            size=26, icon_size=16, kind="SegBtn", colour=theme.MUTED)
        row.addWidget(self.assistant_btn)

        self.insane_btn = icon_button(
            "flame", "Arm or disarm Insane mode", self._toggle_insane,
            size=26, icon_size=16, kind="SegBtn", colour=theme.MUTED)
        row.addWidget(self.insane_btn)
        return seg

    def _toggle_insane(self) -> None:
        """Arm or disarm from the title bar, and tell the page about it."""
        from ..extras import load_autoapply, save_autoapply

        cfg = load_autoapply()
        if not cfg.enabled and self.applies_myself():
            self.set_status(self._insane_blocked_note())
            return
        cfg.enabled = not cfg.enabled
        save_autoapply(cfg)
        page = self._pages.get("Insane mode")
        if page is not None and hasattr(page, "arm_btn"):
            page._loading = True
            page.arm_btn.setChecked(cfg.enabled)
            page._loading = False
            if hasattr(page, "cfg"):
                page.cfg = cfg
                page._render_console()
        self.set_status(
            "Insane mode armed." if cfg.enabled else
            # What it does, not a promise about the future. The old wording
            # — "Nothing will be sent on its own" — was a claim about an
            # application that was already running, and with autosubmit on
            # it was not true of that one.
            "Insane mode disarmed. Stopping what is open; nothing new starts.")
        self.refresh_chrome()
        if cfg.enabled:
            self.start_autoapply()
        else:
            self.stop_autoapply("Disarmed.")

    def _set_model(self, name: str) -> None:
        storage.set_setting("model", name)
        self._check_model()
        self._refresh_identity()
        self.set_status(f"Model set to {name}.")

    def _status_line(self) -> QWidget:
        """The wireframe's live status line: a gold dot and what Sushi is
        doing this second, across the top of every page."""
        bar = QFrame()
        bar.setObjectName("StatusLine")
        bar.setStyleSheet(
            f"QFrame#StatusLine{{background:{theme.SURFACE};"
            f"border-top:1px solid {theme.LINE_2};}}")
        bar.setFixedHeight(40)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(24, 0, 24, 0)
        lay.setSpacing(10)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{theme.ACCENT};border-radius:4px;")
        lay.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        # The dot breathes while Sushi is working (searching, reading,
        # filling) and holds still when it is idle.
        self._status_dot = dot
        self._dot_phase = False
        self._dot_timer = QTimer(self)
        self._dot_timer.timeout.connect(self._pulse_dot)
        self._dot_timer.start(550)
        # Elided and never asking for width: a long progress line used to set
        # the window's minimum and push every page past its right edge.
        self.status = ElidedLabel("")
        self.status.setStyleSheet(f"color:{theme.INK};font-size:{theme.SIZE_SMALL + 1}px;"
                                  "background:transparent;")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.status, 1)
        # The job search's own bar: boards read out of boards to read.
        self.scan_bar = QProgressBar()
        self.scan_bar.setFixedSize(180, 4)
        self.scan_bar.setTextVisible(False)
        self.scan_bar.setVisible(False)
        self.scan_bar.setToolTip("Searching job boards")
        lay.addWidget(self.scan_bar, 0, Qt.AlignmentFlag.AlignVCenter)
        self.scan_progress.connect(self._show_scan_progress)
        self.today_lbl = label("", "Faint", wrap=False)
        lay.addWidget(self.today_lbl)
        tail = self._console_bar()
        tail.setStyleSheet("QFrame#ConsoleBar{background:transparent;border:none;}")
        tail.layout().setContentsMargins(0, 0, 0, 0)
        lay.addWidget(tail)
        return bar

    def _show_scan_progress(self, done: int, total: int) -> None:
        if total <= 0:
            return
        self.scan_bar.setRange(0, total)
        self.scan_bar.setValue(min(done, total))
        self.scan_bar.setToolTip(f"Searching job boards: {done:,} of {total:,}")
        self.scan_bar.setVisible(done < total)

    def _pulse_dot(self) -> None:
        if not self.scanning() and self.scan_bar.isVisible():
            self.scan_bar.setVisible(False)
        working = (self.progress.isVisible() if hasattr(self, "progress") else False)             or self.scanning() or getattr(self, "_apply_worker", None) is not None
        self._dot_phase = (not self._dot_phase) if working else False
        colour = theme.ACCENT_LINE if self._dot_phase else theme.ACCENT
        self._status_dot.setStyleSheet(f"background:{colour};border-radius:4px;")

    def _console_bar(self) -> QWidget:
        """The strip along the very bottom: how much has been logged, and a
        way into it. Replaces a status line nobody could act on."""
        bar = QFrame()
        bar.setObjectName("ConsoleBar")
        bar.setFixedHeight(30)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(22, 0, 14, 0)
        lay.setSpacing(8)
        bar.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        # The status text moved to the live line under the title bar.
        lay.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(150)
        # A thin line, not two coral bricks: without a fixed height the
        # busy bar filled the status bar and Windows drew its chunks blocky.
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        self.log_btn = button("", "Link", self._open_log, icon="info", icon_size=15)
        self.log_btn.setToolTip("Open the log")
        lay.addWidget(self.log_btn)
        self.log_count = label("0", "Mono", wrap=False)
        lay.addWidget(self.log_count)
        return bar

    def _open_log(self) -> None:
        from .. import diagnostics
        if diagnostics.LOG_FILE is None:
            self.error("Logging is not running in this session.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(diagnostics.LOG_FILE.parent)))

    #: Whether the user has asked for the assistant this session. The panel
    #: starts closed — it is help, and help that is already open is a third
    #: of the window spent on a question nobody asked — so this is what tells
    #: a resize whether putting it back is restoring their choice or
    #: overriding it.
    _assistant_wanted = False

    def _set_assistant_visible(self, shown: bool, remember: bool = True) -> None:
        if remember:
            self._assistant_wanted = shown
        # isHidden, not isVisible: a child of a window Qt has not mapped yet
        # reports invisible even when nothing has hidden it, which turned the
        # first toggle of a session into a no-op.
        if (not self.assistant.isHidden()) == shown:
            return
        self.assistant.setVisible(shown)
        self.assistant_btn.setObjectName("IconBtnOn" if shown else "IconBtn")
        self.assistant_btn.setIcon(icons.icon(
            "sparkles", 18, theme.ACCENT if shown else theme.MUTED))
        self.assistant_btn.style().unpolish(self.assistant_btn)
        self.assistant_btn.style().polish(self.assistant_btn)

    def _toggle_assistant(self) -> None:
        self._rails_pinned = True   # the user has an opinion; stop auto-fitting
        self._set_assistant_visible(not self._assistant_wanted)

    def _toggle_sidebar(self) -> None:
        self._rails_pinned = True
        self.sidebar.toggle()

    # --- discovery ------------------------------------------------------------
    #
    # Finding jobs used to be a page with a button on it, which meant the feed
    # was empty until you went and asked. It runs on its own now: once there
    # is a profile, the first scan starts by itself and the Dashboard fills
    # while you are reading the rest of the app.

    #: Don't re-scan more often than this on a plain app start.
    #: How old the local job database may get before a refresh starts on
    #: its own. Daily: a refresh reads every board that has vacancies, and
    #: boards do not turn over faster than that.
    _RESCAN_AFTER = 60 * 60 * 24

    def scan_page(self):
        """Where scan progress is shown. The QA harness drives this."""
        return self._pages["Dashboard"]

    def scanning(self) -> bool:
        return getattr(self, "_scan_worker", None) is not None

    def _load_feed(self) -> None:
        """Fill the feed from the local job database. No network.

        This is what the database is for. The feed used to start empty
        every single time and fill minutes later as a sweep ran; now the
        jobs are already on disk and showing them is a query.
        """
        from .. import jobsdb

        if not self._profile:
            return
        try:
            from ..extras import dealt_with
            roles = jobsdb.search(self._profile, min_score=self._feed_bar(),
                                  limit=400, exclude=dealt_with())
        except Exception as exc:          # a cache must never block startup
            log.info("feed: could not read the job database: %s", exc)
            return
        if roles:
            self._matches = roles
            self.matches_changed.emit()
            log.info("feed: %d role(s) from the local database", len(roles))

    @staticmethod
    def _feed_bar() -> int:
        """The lowest score worth showing.

        Six, because the scores bunch: across 37,433 real vacancies, 16,417
        scored 2 (a seniority or location point and nothing else) and the
        next real group was 1,272 at 6. Eight sat above that group and left
        a feed of forty-seven where there were more than a thousand worth
        seeing.

        It is now the only bar in the app. Insane mode used to have a second
        one on top of it and no longer does, so what this returns decides
        both what you are shown and what may be applied to on your behalf —
        raise it and the feature gets stricter; it is not only cosmetic.
        """
        return 6

    def rescore_if_stale(self) -> None:
        """Re-score at startup if the table disagrees with today's scorer.

        ``set_profile`` catches "your targets changed", which was the only
        way a row could go stale while the app was running. It is not the
        only way a row goes stale between runs: the fingerprint also carries
        the matcher's own revision, so a release that fixes scoring arrives
        with a table full of answers from the version that was wrong — and
        nothing asked it to think again, because the profile had not moved.
        That is how a corrected location rule could ship and the feed still
        show the jobs the old one picked.
        """
        from .. import jobsdb

        if not self._profile:
            return
        if jobsdb.stale_count(self._profile, limit=1):
            self.set_status("Re-scoring your jobs against the current matcher…")
            self.rescore_feed()

    #: The re-score in flight, and whether the profile moved again while it
    #: was running. One at a time: a pass rewrites every row in a table of a
    #: million, and two of them racing is two writers doing a million UPDATEs
    #: each against rows the other is also rewriting — for an answer only the
    #: last one to finish gets to keep. Switching between two resumes is two
    #: clicks, so this is now easy to ask for by accident.
    _rescore_worker: Worker | None = None
    _rescore_again = False

    def rescore_feed(self) -> None:
        """Re-score the table against the current profile, then reload.

        Changing your targets costs a pass over rows already on disk —
        about thirty seconds for a million of them — instead of another
        forty-minute sweep of fifteen thousand boards. That trade is the
        reason the table is worth 300MB.
        """
        from .. import jobsdb

        if not self._profile:
            return
        if self._rescore_worker is not None:
            # Coalesced rather than queued. A second pass would score
            # against whatever the profile is when it starts, so one more
            # at the end covers every change made during this one.
            self._rescore_again = True
            return
        profile = self._profile
        # One live rescore at a time. Two running against different profiles
        # (the old targets and the just-edited ones) each saw the other's
        # rows as stale and re-scored them back — round and round, which is
        # how "Re-scored 1,520,000 job(s)" came out of a 300,000-row table.
        self._rescore_gen = getattr(self, "_rescore_gen", 0) + 1
        gen = self._rescore_gen

        def stale() -> bool:
            return gen != self._rescore_gen

        def job(on_progress=None):
            return jobsdb.rescore(profile, on_progress=on_progress,
                                  should_stop=stale)

        def again() -> None:
            self._rescore_worker = None
            if self._rescore_again:
                self._rescore_again = False
                QTimer.singleShot(0, self.rescore_feed)

        def done(n) -> None:
            again()
            if stale():
                return
            if n:
                self.set_status(f"Re-scored {n:,} job(s) against your profile.")
            self._load_feed()

        def failed(msg: str) -> None:
            # Not a dialog. This is housekeeping that runs itself off the
            # back of a profile change — the user pressed "switch resume",
            # not "re-score" — and a modal box with a raw exception in it
            # over the page they are reading is the wrong way to report
            # that some rows kept yesterday's score. The detail goes to the
            # log, where it is diagnosable.
            again()
            log.warning("re-score failed: %s", msg)
            self.set_status(
                "Some jobs could not be re-scored against this profile, so "
                "their match may be out of date. Press Refresh on All jobs "
                "to try again.")
            self._load_feed()

        self._rescore_worker = self.run(job, on_done=done, on_failed=failed)

    # --- reading the CV, from wherever it is asked for ------------------------

    def read_cv(self, *, only_if_changed: bool = False, then=None) -> bool:
        """Read the active CV into a profile. Says whether a read started.

        On the window rather than on the Profile page, because two screens
        now ask for it: the Read my CV button, and Refresh on All jobs.
        (Onboarding keeps its own copy — it has a progress bar and a retry
        of its own to drive, and none of that belongs here.)

        ``then`` runs once the profile has landed, or straight away when
        there is nothing to read, so a caller can chain a scan behind it
        without caring which happened.
        """
        def carry_on() -> None:
            if then:
                then()

        found = storage.active_resume()
        if found is None:
            self.set_status("No CV attached. Profile → Add resume.")
            carry_on()
            return False
        if not found.exists():
            # The path is remembered; the file may have been moved or
            # deleted since. Saying so beats a worker failing into the log.
            self.set_status(f"{found.name} is not where it was. "
                            f"Attach it again in Profile.")
            carry_on()
            return False
        if only_if_changed and not found.changed():
            carry_on()
            return False
        settings = load_settings()
        if not settings.llm_enabled:
            self.set_status("Your CV has changed, and reading it needs a "
                            "model. Add one in Settings."
                            if only_if_changed else
                            "No model configured. Add credentials in Settings.")
            carry_on()
            return False

        path = found.path
        rid = found.id
        self.busy(True, f"Reading {found.name}…")

        def job():
            return build_profile(LLM(settings), extract_text(path))

        def done(profile: Profile) -> None:
            # A re-read replaces everything the CV knows, and must not
            # replace the one thing it does not: where you are willing to
            # work is an answer you gave this app, not something on the file.
            was = storage.resume(rid)
            prior = (was.profile if was else None) or self._profile
            chosen = storage.get_setting(LOCATIONS_CHOSEN, "") == "1"
            if prior is not None and (chosen or (prior.workplace or "any") != "any"):
                profile.workplace = prior.workplace
            if prior is not None and chosen and prior.target_locations:
                profile.target_locations = prior.target_locations
            # Typed under Application defaults, so the user's: a CV that does
            # not mention salary or sponsorship must not blank them.
            if prior is not None:
                for key in ("work_authorisation", "requires_sponsorship",
                            "salary_expectation"):
                    if not getattr(profile, key, "") and getattr(prior, key, ""):
                        setattr(profile, key, getattr(prior, key))
            storage.record_cv_read(path, profile)
            self.set_profile(profile)
            self.busy(False, f"Profile rebuilt from {Path(path).name}.")
            carry_on()

        def failed(msg: str) -> None:
            self.busy(False, "")
            self.error(f"{found.name} would not read: {msg}")
            carry_on()

        self.run(job, on_done=done, on_failed=failed)
        return True

    def refresh_all(self) -> None:
        """What the Refresh button on All jobs means.

        Two things, in the order they matter. Your CV decides half of every
        score, so if it has changed on disk since Sushi read it there is no
        point looking for jobs against the old one — it is read first, the
        feed is re-scored off the new profile, and only then does the sweep
        of the boards start. If the CV has not moved, this is the sweep and
        nothing else.

        ``read_cv`` calls ``then`` exactly once either way — after the read,
        or straight away when there is nothing to read — so the scan is
        started from one place rather than from two branches that could both
        fire.
        """
        self.read_cv(only_if_changed=True, then=self.start_scan)

    def maybe_autoscan(self) -> None:
        """Start a scan if the feed is stale, quietly doing nothing if not.

        ``SUSHI_NO_AUTOSCAN`` exists for the QA harnesses: a screenshot run
        otherwise spends a minute reading a hundred and sixty real careers
        pages before it can photograph a page that does not depend on them.
        """
        import os

        from .. import jobsdb
        from ..discovery import boards

        if os.environ.get("SUSHI_NO_AUTOSCAN"):
            return
        if self.scanning() or not self._profile:
            return
        if self._restart_timer is not None and self._restart_timer.isActive():
            # Where to look has just moved and the restart is a moment away.
            # Starting here would mean a pool of fourteen threads reading
            # boards under the old queue for most of a second, to be stopped
            # by the restart that is already on its way.
            return

        # Judged on the database, not on the clock. The old rule was "there
        # are matches and a scan ran recently, so do nothing" — which left a
        # build that had crashed eight seconds in sitting at 76 of 15,654
        # boards for the rest of the day, with nine matches on screen and no
        # sign that 99.5% of the corpus had never been read. A refresh only
        # counts as done when it has worked through everything due.
        try:
            if jobsdb.stats().fresh and not jobsdb.due_boards(boards()):
                return
        except Exception as exc:        # a cache problem must not block the app
            log.info("autoscan: could not read the job database: %s", exc)
        self.start_scan()

    # The slice-and-resume sweep is gone. It existed because a pass had to
    # be short enough to show results before the user gave up, and the next
    # pass had to start where the last stopped. jobsdb.refresh walks every
    # due board itself, writes each one as it is read — so an interrupted
    # run has kept its work — and streams new jobs to the feed as they land.

    def start_scan(self, sweep: bool = True) -> None:
        """Look for roles, and keep going until the whole corpus is read.

        One pass reads a slice of the corpus. With fifteen thousand boards
        in it, a single pass covers under a tenth of the companies Sushi
        knows about — so stopping there means most employers are never
        looked at, and the obvious question ("shouldn't it keep reading?")
        has the obvious answer.

        A scan is now a *refresh of the local job database*, not a search
        that throws its results away. It reads the boards that are due —
        daily for the ones with vacancies, weekly for the two in five that
        are empty — and writes every job it finds to disk. What the user
        sees is unchanged, except that it is faster, survives restarts, and
        the next launch starts full instead of empty.

        Measured on this corpus: ~41 minutes for all 15,589 boards, about
        1.06 million jobs, ~333MB. It is interruptible at any point and
        each board is recorded as it is read, so stopping loses nothing.
        """
        if self.scanning():
            self._sweep = self._sweep or sweep
            return
        if not self._profile:
            self.set_status("Add your CV in Profile and Sushi will start looking.")
            return
        from .. import jobsdb
        from ..discovery import boards, corpus_size

        self._sweep = sweep
        self._sweep_read = 0
        all_boards = boards()
        due = len(jobsdb.due_boards(all_boards))
        log.info("refresh starting for %s: %d of %d boards due",
                 self._profile.full_name or "(unnamed)", due, len(all_boards))
        if not due:
            self.busy(False, f"Up to date · {jobsdb.stats().jobs:,} jobs on file.")
            self._sweep = False
            return
        profile = self._profile
        corpus_size()                      # warms the cached corpus count

        self._scan_started = time.time()

        # Closing the window mid-scan deletes the C++ side of these signals
        # while the refresh threads are still emitting. Stop quietly instead
        # of raising "wrapped C/C++ object has been deleted" from every one.
        gone = {"closed": False}

        def emit(signal, *values) -> None:
            if gone["closed"]:
                return
            try:
                signal.emit(*values)
            except RuntimeError:
                gone["closed"] = True

        def job(on_progress=None, should_stop=None):
            return jobsdb.refresh(
                all_boards, profile=profile, on_progress=on_progress,
                # Straight to the feed as they are stored, so a first run —
                # when there is nothing on disk yet — fills as it goes.
                on_jobs=lambda roles: emit(self.scan_found, list(roles)),
                on_tick=lambda d, t: emit(self.scan_progress, d, t),
                should_stop=lambda: gone["closed"] or bool(
                    should_stop and should_stop()))

        self.busy(True, self._sweep_note() if sweep else "Refreshing jobs…")
        self.scan_state_changed.emit(True)
        # ``on_failed``, so a sweep that dies does not also raise a modal
        # box with a raw exception in it over whatever page you are reading.
        # A background refresh of fifteen thousand careers pages is not
        # something the user asked for at that moment.
        self._scan_worker = self.run(job, on_done=self._refresh_done,
                                     on_failed=self._scan_failed)

    #: Set while a full sweep of the corpus is in progress.
    _sweep = False
    _sweep_start: int | None = None
    _sweep_read = 0
    #: When the running refresh began, so a page can turn boards-per-second
    #: into minutes-remaining. None when nothing is running.
    _scan_started: float | None = None

    def sweeping(self) -> bool:
        return self._sweep

    def scan_started_at(self) -> float | None:
        return self._scan_started

    def sweep_progress(self) -> tuple[int, int]:
        """Boards on file, and how many there are in total."""
        from .. import jobsdb
        from ..discovery import corpus_size

        total = corpus_size()
        try:
            return jobsdb.stats().boards_read, total
        except Exception:
            return 0, total

    def _sweep_note(self) -> str:
        read, total = self.sweep_progress()
        if not total:
            return "Looking for roles…"
        return (f"Reading every company Sushi knows: {read:,} of {total:,} "
                f"careers pages on file.")

    #: Set while a restart is waiting for the running refresh to let go.
    _restart_pending = False
    #: Built on the first restart, so a burst of chip clicks is one restart.
    _restart_timer: QTimer | None = None
    #: The answer the coalesced restart is leaving behind, so a user who
    #: clicks away and clicks back does not pay for a sweep to end up where
    #: it already was.
    _restart_from: str | None = None

    def restart_sweep(self, was: str | None = None) -> None:
        """Where to look changed: read the corpus again, from the top.

        A re-score answers "these rows were judged against your old answer"
        and nothing else, and that is not the whole of what changed. The
        refresh reads boards on a schedule — daily for the ones with
        vacancies, weekly for the empty ones — so a sweep stopped at 560 of
        15,918 would simply carry on at 561 and leave the other 15,358
        unread for a day under an answer the user has just replaced. Worse,
        the refresh in flight is holding the *old* profile and scoring every
        row it writes against it, so rows would keep arriving stale behind
        the re-score that was meant to fix them.

        So the whole queue goes back on the table, the rotation goes back to
        the top of the corpus, and the refresh is restarted carrying the new
        answer. Nothing already fetched is thrown away — see
        ``jobsdb.restart_boards``.

        Coalesced, because this is wired to a row of chips: picking EMEA,
        then Germany, then "remote only" is three saves in two seconds and
        one change of mind. ``was`` is the answer the first of those clicks
        moved off; if the last one lands back on it, there is nothing to do.
        """
        if self._restart_timer is None:
            self._restart_timer = QTimer(self)
            self._restart_timer.setSingleShot(True)
            self._restart_timer.timeout.connect(self._restart_sweep_now)
        if not self._restart_timer.isActive():
            self._restart_from = was
        self._restart_timer.start(900)

    def _restart_sweep_now(self) -> None:
        from .. import jobsdb
        from ..discovery import reset_scan_cursor

        was, self._restart_from = self._restart_from, None
        if was is not None and jobsdb.where_rev(self._profile) == was:
            return
        jobsdb.restart_boards()
        reset_scan_cursor()
        if self.scanning():
            # Cancelled rather than abandoned: the worker writes each board
            # as it reads it, so stopping costs nothing, and _refresh_done
            # starts the new one once the old has actually let go. The flag
            # goes on *after* the stop, which clears it — so pressing stop
            # yourself is the end of it, and this is not.
            self.stop_scan()
            self._restart_pending = True
            self.set_status("Where you want to work changed — starting again.")
            return
        self.rescore_feed()
        # Through maybe_autoscan rather than start_scan: the queue is full
        # again so it will start, and it is the one door that honours
        # SUSHI_NO_AUTOSCAN — a QA run that touches this setting must not be
        # sent off to read fifteen thousand real careers pages.
        self.maybe_autoscan()

    def stop_scan(self) -> None:
        self._sweep = False
        self._sweep_start = None
        self._scan_started = None
        # An explicit stop outranks a queued restart: otherwise the one
        # thing the button is for — making it stop — would be undone a
        # second later by a setting the user changed before pressing it.
        self._restart_pending = False
        worker = getattr(self, "_scan_worker", None)
        if worker is not None:
            worker.cancel()
            self.set_status("Stopping the search…")

    def _refresh_done(self, st) -> None:
        """One refresh finished. There is no next slice to start."""
        import time

        self._scan_worker = None
        self._sweep = False
        self._sweep_start = None
        self._scan_started = None
        if self._restart_pending:
            # Straight back out again under the new answer. The pill is only
            # told the scan stopped if the next one declines to start, since
            # as far as the user is concerned it has not stopped — it has
            # gone back to the beginning.
            self._restart_pending = False
            self.rescore_feed()
            self.maybe_autoscan()
            if not self.scanning():
                self.busy(False, "")
                self.scan_state_changed.emit(False)
            return
        storage.set_setting("last_scan_at", str(time.time()))
        log.info("refresh done: %s jobs on file from %s boards",
                 getattr(st, "jobs", 0), getattr(st, "boards_read", 0))
        self._load_feed()
        # A standing order has been waiting for exactly this.
        self.autoapply_recheck()
        total = getattr(st, "jobs", 0)
        read, boards_total = self.sweep_progress()
        self.busy(False, (
            f"{total:,} jobs on file from {read:,} of {boards_total:,} "
            f"careers pages. {len(self._matches)} match your profile."))
        self.scan_state_changed.emit(False)

    #: Arrivals waiting to go into the feed, and the timer that drains them.
    _pending_roles: list

    def _scan_found(self, roles: list) -> None:
        """Take what the refresh has just found. On the UI thread.

        Buffered rather than applied straight away. The refresh reads
        fourteen boards at a time and hands over each one as it lands, so
        this is called many times a second, and every call used to re-sort
        the feed and schedule a rebuild of the page. Collecting them and
        draining once gives the same result for a fraction of the work.
        """
        if not roles:
            return
        if not hasattr(self, "_pending_roles"):
            self._pending_roles = []
        self._pending_roles.extend(roles)
        if not self._drain_timer.isActive():
            self._drain_timer.start(1500)

    def _drain_found(self) -> None:
        """Fold everything that arrived during the window into the feed."""
        roles, self._pending_roles = getattr(self, "_pending_roles", []), []
        if roles and self.add_matches(roles):
            # A standing order may have been waiting for one of these.
            self.autoapply_recheck()

    def _scan_failed(self, msg: str) -> None:
        log.warning("refresh failed: %s", msg)
        self._scan_worker = None
        self._sweep = False
        self._sweep_start = None
        self._scan_started = None
        if self._restart_pending:
            self._restart_pending = False
            self.rescore_feed()
            self.maybe_autoscan()
            if self.scanning():
                return
        self.busy(False, "")
        # Said, rather than only tidied up after. Every board already read
        # is on disk, so this costs the rest of the sweep and nothing else
        # — which is the part worth telling someone.
        self.set_status("The job search stopped early. What it had already "
                        "read is on file; press Refresh to carry on.")
        self.scan_state_changed.emit(False)

    def _wire_signals(self) -> None:
        # The trailing half of the rebuild throttle in _on_state_changed.
        self._last_refresh = -1e9
        self._refresh_pending = QTimer(self)
        self._refresh_pending.setSingleShot(True)
        self._refresh_pending.timeout.connect(self._refresh_now)

        self.profile_changed.connect(self._on_state_changed)
        self.matches_changed.connect(self._on_state_changed)
        self.apps_changed.connect(self._on_state_changed)
        # A form the user finished and sent by hand: show it as sent.
        try:
            from .. import autofill as _af
            _af.on_user_submitted.append(lambda _url: self.apps_changed.emit())
        except Exception:                               # noqa: BLE001
            pass
        # Report notes the agent filed as answers ("written from the CV…")
        # are cleared before any form can be filled with them.
        try:
            from .. import learning as _lr
            gone = _lr.purge_report_answers()
            if gone:
                log.info("learning: cleared %d report note(s) banked as answers", gone)
        except Exception:                               # noqa: BLE001
            log.debug("could not purge report notes", exc_info=True)
        # Queued, because it is emitted from the scan's worker thread.
        self.scan_found.connect(self._scan_found)
        self._pending_roles = []
        self._drain_timer = QTimer(self)
        self._drain_timer.setSingleShot(True)
        self._drain_timer.timeout.connect(self._drain_found)

    def _refresh_now(self) -> None:
        """The deferred rebuild, once a burst of signals has stopped."""
        self._last_refresh = time.monotonic() * 1000
        current = self.stack.currentWidget()
        if hasattr(current, "on_show"):
            current.on_show()
        self._refresh_identity()

    # --- onboarding -----------------------------------------------------------

    def restart_onboarding(self) -> None:
        """Show the first-run flow over the app, building it on demand."""
        from .onboarding import Onboarding

        flow = Onboarding(self)
        flow.finished.connect(lambda: self._close_onboarding(flow))
        self.root_stack.addWidget(flow)
        self.root_stack.setCurrentWidget(flow)

    def _close_onboarding(self, flow: QWidget) -> None:
        self.root_stack.setCurrentWidget(self.shell)
        self.root_stack.removeWidget(flow)
        flow.deleteLater()
        self._check_model()
        self._refresh_identity()
        self.navigate(self._current)
        QTimer.singleShot(400, self.maybe_autoscan)

    # --- controller API (used by pages) ---------------------------------------

    @property
    def profile(self) -> Profile | None:
        return self._profile

    def set_profile(self, p: Profile) -> None:
        """Save the profile and bring the feed into line with it.

        What you are looking for is half of every score, so changing it
        makes every row on file stale. That used to mean another sweep;
        now it is a pass over the table — about thirty seconds for a
        million rows — and it runs itself rather than waiting for you to
        notice the feed is answering your old question.
        """
        from .. import jobsdb

        was = jobsdb.profile_rev(self._profile)
        # Where to look is the one answer a re-score cannot fully serve: see
        # restart_sweep. Only a profile that was already there counts as
        # having moved — the first one to arrive has nothing to restart.
        had, was_where = self._profile is not None, jobsdb.where_rev(self._profile)
        self._profile = p
        storage.save_profile(p)
        # And against the CV it came from. Without this, editing your target
        # titles and then switching resume and back would put the edit back
        # the way it was: the library would hand over the profile as it stood
        # when the file was read.
        storage.bank_active_profile(p)
        self.profile_changed.emit()
        if had and jobsdb.where_rev(p) != was_where:
            self.restart_sweep(was_where)
        elif jobsdb.profile_rev(p) != was:
            self.rescore_feed()
        # A profile arriving is the starting gun for the first search, and
        # nothing else was firing it. The autoscan runs when onboarding
        # closes — but on a first run there is usually no profile at that
        # moment: the CV is read on the Profile page, or dropped on the
        # window, or read a minute later when a model is finally configured.
        # So the one run that matters most, the first, never began: an empty
        # All jobs, "Never scanned", and nothing happening.
        QTimer.singleShot(0, self.maybe_autoscan)

    @property
    def matches(self) -> list[Role]:
        return self._matches

    def match_total(self) -> int:
        """How many jobs are above the bar. Counted, not listed."""
        from .. import jobsdb

        if not self._profile:
            return 0
        try:
            return jobsdb.count(min_score=self._feed_bar())
        except Exception:
            return len(self._matches)

    def open_match_total(self) -> int:
        """Jobs above the bar that you have not already dealt with.

        The number every surface in the app should quote. Three of them were
        quoting three different ones: the sidebar badge said 497 (every job
        above the bar), All jobs said 484 (minus the settled), and the
        Dashboard said "the best 4 of 497" beside a "See all 497" that opened
        the page saying 484.
        """
        return max(0, self.match_total()
                   - self._settled_count(storage.counts_by_status()))

    def all_matches(self, limit: int = 4000) -> list[Role]:
        """Every job above the bar, straight from the database.

        ``matches`` is the in-memory feed: capped, and whatever the last
        scan streamed in. The All jobs page wants the actual answer, which
        is a query — so it asks for one rather than reading a cache of a
        cache.
        """
        from .. import jobsdb

        if not self._profile:
            return []
        try:
            return jobsdb.search(self._profile, min_score=self._feed_bar(),
                                 limit=limit)
        except Exception as exc:
            log.info("all jobs: could not read the job database: %s", exc)
            return list(self._matches)

    def set_matches(self, roles: list[Role]) -> None:
        self._matches = roles
        self.matches_changed.emit()

    #: The feed is capped so it cannot grow without bound across a long
    #: session. Well past what anyone scrolls, and the worst ones go first.
    _MAX_MATCHES = 400

    def add_matches(self, roles: list[Role]) -> int:
        """Merge a scan's results into the feed. Returns how many are new.

        Each scan now reads a *different* slice of the corpus, so replacing
        the feed would throw away everything the last one found and the
        number of jobs on screen would never grow — it would just change.
        Merging is what makes a second search worth running.
        """
        have = {r.url or r.key: r for r in self._matches}
        new = 0
        for role in roles:
            key = role.url or role.key
            existing = have.get(key)
            if existing is None:
                have[key] = role
                new += 1
            elif role.score > existing.score:
                # A later pass scored it better — keep the better reading.
                have[key] = role
        # Dealt-with jobs leave before the cap, or they hold slots that
        # newly fetched ones need.
        from ..extras import dealt_with
        gone = dealt_with()
        merged = sorted((r for r in have.values() if r.url not in gone),
                        key=lambda r: -r.score)
        self._matches = merged[:self._MAX_MATCHES]
        self.matches_changed.emit()
        return new

    def settings(self):
        return load_settings()

    #: The wireframe's tab names, and the pages behind them.
    _TAB_PAGE = {"Matches": "All jobs", "Applications": "Tracker"}
    #: Which tab lights up for each page, including ones with no tab.
    _PAGE_TAB = {"All jobs": "Matches", "Tracker": "Applications",
                 "Dashboard": "Home", "Insane mode": "Home",
                 "Manual apply": "Matches", "Settings": ""}

    def navigate(self, name: str) -> None:
        # Help is a nav row that is not a destination: it opens the assistant
        # on the right and leaves the page where it was. Handled here rather
        # than wired separately in the sidebar, because the sidebar's one
        # output is a name and this is the place that knows what names mean.
        if name == "Help":
            self._set_assistant_visible(True)
            self.sidebar.set_active(self._current)
            return
        name = self._TAB_PAGE.get(name, name)
        if name not in self._pages:
            return
        self._current = name
        page = self._pages[name]
        self.stack.setCurrentWidget(page)
        self.sidebar.set_active(self._PAGE_TAB.get(name, name))
        self.assistant.set_page(getattr(page, "assistant_key", "dashboard"))
        page.on_show()

    def set_status(self, msg: str) -> None:
        self.status.setText(msg)

    def status_text(self) -> str:
        """What the status bar currently says. Used by the QA sweep."""
        return self.status.text()

    def busy(self, on: bool, msg: str = "") -> None:
        self.progress.setVisible(on)
        if msg:
            self.status.setText(msg)

    def error(self, msg: str) -> None:
        self.busy(False)
        dialogs.tell(self, "That didn't work.", body=msg, button_text="Close")

    #: Workers that have been started and have not reported yet. Nothing
    #: else refers to them, and without this they are collected while their
    #: queued result is still in flight. See ``Worker.keep_alive``.
    _inflight: set

    def start_worker(self, w: Worker) -> Worker:
        """Start a worker and keep it alive until it has been heard."""
        if not hasattr(self, "_inflight"):
            self._inflight = set()
        w.keep_alive(self._inflight)
        self.pool.start(w)
        return w

    def run(self, fn, *args, on_done=None, on_progress=None, on_failed=None,
            **kwargs) -> Worker:
        """Start work off the UI thread.

        ``on_failed`` *replaces* the default error dialog rather than adding
        to it. Callers used to connect their own handler to the returned
        worker, which left both wired — so one failure produced the caller's
        considered message *and* a modal box with the raw exception text in
        it, which is what the user actually read.
        """
        w = Worker(fn, *args, **kwargs)
        if on_done:
            w.signals.finished.connect(on_done)
        w.signals.failed.connect(on_failed or self.error)
        w.signals.progress.connect(on_progress or self.set_status)
        return self.start_worker(w)

    def ask_assistant(self, text: str) -> None:
        self._set_assistant_visible(True)
        self.assistant._ask(text)

    def assistant_context(self) -> AssistantContext:
        return AssistantContext(profile=self._profile, matches=self._matches,
                                app_counts=storage.counts_by_status(),
                                known=learning.prompt_block(24, clip=180))

    # --- role actions (shared by Dashboard/Browse) ----------------------------

    def save_role(self, role: Role) -> None:
        storage.upsert_role(role, status="matched")
        self.set_status(f"Saved {role.title} at {role.company}.")

    def pass_role(self, role: Role) -> None:
        # upsert_role deliberately keeps an existing row's status so a rescan
        # never undoes something you acted on — which means it cannot be used
        # to *set* one. Passing on a role you had already saved left it sitting
        # in the feed marked "matched". Passing is an explicit decision, so it
        # is written explicitly.
        app_id = storage.upsert_role(role, status="skipped")
        storage.set_status(app_id, "skipped")
        self._matches = [r for r in self._matches if r.key != role.key]
        self.apps_changed.emit()
        self.matches_changed.emit()
        self.set_status(f"Passed on {role.title}.")

    def show_role_details(self, role: Role) -> None:
        """Open the drawer over the feed.

        This used to be a modal dialog, which took the window hostage for
        something you do while scanning: open a posting, decide, close, open
        the next. The drawer leaves the grid in place behind it.
        """
        self.details_overlay().open_role(role)

    def details_overlay(self):
        """The one drawer, built on first use."""
        overlay = getattr(self, "_details", None)
        if overlay is None:
            from .details import DetailsOverlay

            overlay = DetailsOverlay(self)
            panel = overlay.panel
            panel.apply_clicked.connect(self.apply_to_role)
            panel.pass_clicked.connect(self.pass_role)
            panel.save_clicked.connect(self.save_role)
            panel.open_clicked.connect(
                lambda r: QDesktopServices.openUrl(QUrl(r.url)))
            self._details = overlay
        return overlay

    @staticmethod
    def applies_myself() -> bool:
        """The user picked "I apply myself" in Settings."""
        from ..extras import chosen_apply_mode
        return chosen_apply_mode() == "self"

    def _insane_allowed(self) -> bool:
        # Read through this module's load_settings, as every other check
        # here is, so the model check is the very one Insane mode always had.
        return not self.applies_myself() and load_settings().llm_enabled

    def _insane_blocked_note(self) -> str:
        if self.applies_myself():
            return ("Insane mode is off because you apply yourself. Switch to "
                    "“Sushi applies” in Settings → Apply settings.")
        return "Insane mode needs a model. Add one in Settings."

    def mark_submitted(self, role) -> None:
        """The user says they sent it. Recorded exactly as a detected hand
        submission is, so the row leaves "Needs you" everywhere."""
        from .. import autofill
        autofill._mark_sent(role.url)
        self.apps_changed.emit()
        self.set_status(f"Marked {role.title} at {role.company} as submitted.")

    def open_role_links(self, roles: list) -> int:
        """The "I apply myself" path: open each posting in the user's own
        browser and do nothing more."""
        opened = 0
        for role in roles:
            if role and role.url and QDesktopServices.openUrl(QUrl(role.url)):
                opened += 1
        if opened:
            self.set_status(f"Opened {opened} job(s) in your browser.")
        return opened

    def apply_to_role(self, role: Role, watch: bool = False) -> None:
        if self.applies_myself():
            self.open_role_links([role])
            return
        if not self._profile:
            self.error("Build your profile first (Profile → Read my CV).")
            return
        s = load_settings()
        asettings = load_apply_settings()
        submit = s.autosubmit
        # The role is the subject, what happens next is one line, and the
        # button says the verb. The same question used to be a paragraph with
        # blank lines in it and a Yes to press.
        what = ("It will fill the form and send it — submitted applications "
                "cannot be taken back."
                if submit else
                "It opens a browser, fills the form, and stops there for you "
                "to check before anything is sent.")
        resume = RESUME_MODE_WORDS.get(asettings.resume_optimization, "")
        if not dialogs.ask(
                self, "Apply with Sushi?",
                subject=role.title,
                sub=f"{role.company}" + (f" · {role.location}" if role.location else ""),
                body=what + (f"\n\nResume: {resume}." if resume else ""),
                confirm="Submit it" if submit else "Fill it in",
                danger=submit):
            return
        self.begin_apply(role, watch=watch, announce=True)

    # --- the Fill button on a page -----------------------------------------

    def _listen_for_fills(self) -> None:
        """Take presses of the in-page button, from either host.

        The button is on the page, and the page is in a browser — either the
        one Sushi drives or the user's own with the extension loaded. Both
        arrive here, because from this point on it is the same application
        Manual apply would have started.
        """
        self.fill_requested.connect(self._fill_from_page)
        try:
            from .. import autofill
            autofill.set_fill_handler(
                lambda url, title: self.fill_requested.emit(url, title))
        except Exception as exc:
            log.info("fill button: not listening (%s)", exc)
        # The same press, arriving from the user's own Chrome instead. Only
        # started if the extension has actually been generated: an app that
        # has never been asked for it has no business holding a port open.
        try:
            from .. import bridge
            if (DATA_DIR / "extension").is_dir():
                bridge.start(lambda url, title:
                             self.fill_requested.emit(url, title))
        except Exception as exc:
            log.info("bridge: not started (%s)", exc)

    def _fill_from_page(self, url: str, title: str) -> None:
        """Somebody pressed Fill on a job page. Read it, then apply to it.

        Reading is a fetch and a model call, so it happens on a worker; the
        button is told what is going on throughout, because the page is what
        the user is looking at and a pill that says "Filling it in…" for
        fifteen seconds with nothing behind it is how a feature gets a
        reputation for not working.
        """
        from .. import autofill
        from ..extras import role_from_url

        if not url:
            return
        if not self._profile:
            autofill.set_bubble_state("error", "Sushi needs your CV first")
            self.set_status("Press Fill, and Sushi needs your CV first. "
                            "Profile → Read my CV.")
            return
        log.info("fill button: %s", url[:120])
        self.set_status(f"Reading {title or url[:60]}…")
        autofill.set_bubble_state("busy", "Reading the job…")
        profile = self._profile

        def read() -> Role:
            return role_from_url(url, profile)

        def ready(role: Role) -> None:
            storage.upsert_role(role, status="matched")
            self.apps_changed.emit()
            autofill.set_bubble_state("busy", "Filling it in…")
            self.begin_apply(role, watch=True, announce=False,
                             on_finished=self._filled_from_page)

        def failed(_msg: str) -> None:
            autofill.set_bubble_state("error", "Sushi could not read that page")
            self.set_status("That page would not read. Open Manual apply and "
                            "paste the description in yourself.")

        self.run(read, on_done=ready, on_failed=failed)

    def _filled_from_page(self, status: str, result) -> None:
        """Report back onto the button, which is where the user is looking."""
        from .. import autofill

        if result is None:
            autofill.set_bubble_state("error", "That did not reach the form")
            return
        _keep_open_questions(result)
        left = len(getattr(result, "unanswered", None) or [])
        if left:
            autofill.set_bubble_state(
                "done", f"Filled · {left} question(s) for you in Sushi")
        else:
            autofill.set_bubble_state("done", "Filled — check it before you send")

    def begin_apply(self, role: Role, watch: bool = False,
                    announce: bool = True, on_finished=None,
                    on_step=None, track: bool = False) -> None:
        """Fill one application. No dialog — the caller has already asked.

        Split out of :meth:`apply_to_role` so a batch can use it: Insane mode
        applies to a queue of roles, and a confirmation box per role would
        make "runs while you are elsewhere" mean "runs while you sit here
        pressing Yes".
        """
        if not self._profile:
            # Told, not swallowed. Returning quietly left a batch caller
            # waiting for a callback that was never going to come, so Insane
            # mode sat on "armed" with an empty queue behind it.
            self.set_status("Needs your CV first. Profile → Read my CV.")
            if on_finished:
                on_finished("failed", None)
            return
        s = load_settings()
        asettings = load_apply_settings()

        app_id = storage.upsert_role(role, status="filling")
        # upsert keeps an existing row's status, so a saved job stayed
        # "matched" all through its fill. Set it.
        storage.set_status(app_id, "filling")
        self.apps_changed.emit()
        self.busy(True, f"Applying to {role.company}…")
        cv_path = storage.get_setting("cv_path", "")
        # Re-read: a correction saved from a form ("Save changes to Sushi")
        # updates the stored profile, and this copy would not have it.
        fresh = storage.load_profile()
        if fresh is not None:
            self._profile = fresh
        profile = self._profile
        auto_cl = asettings.auto_cover_letter

        from ..autofill import (AutofillError, fill_application, fill_directly,
                                resume_rule)

        fill_mode = getattr(asettings, "fill_mode", "auto")
        explorative = bool(getattr(asettings, "explorative", False))

        def make_letter() -> str:
            """Write one, reusing anything already written for this role.

            Called only once the form has been read and found to have a
            cover-letter field. Writing one for every application spent a
            model call on the majority that never ask for one.
            """
            from ..letters import existing_letter

            found = existing_letter(role)
            if found:
                log.info("apply: reusing %s", found.name)
                return str(found)
            if not auto_cl:
                return ""
            path, text = write_letter(LLM(s), profile, role)
            render_pdf(text, path.with_suffix(".pdf"), author=profile.full_name)
            return str(path.with_suffix(".pdf"))

        def job(on_progress=None, should_stop=None):
            letter_path = ""
            # The tab the first pass filled, if there was one. A form is
            # state in the page: whoever goes second has to be given this
            # tab, or they open a second copy of the form with nothing in
            # it and the half-filled one is stranded beside it.
            tab = ""
            run_settings = replace(s, headless=False) if watch else s
            if fill_mode in ("direct", "auto"):
                # The lookup first, always. It fills the identity half of a
                # form in seconds for nothing, and leaves the agent — if it
                # runs at all — a much smaller job on a much smaller page.
                #
                # Then, before the agent is reached for at all, the cheap
                # model path: one short call per question rather than ten
                # thousand tokens per step. "auto" writes the free text;
                # Explorative answers everything left and flags it.
                result = fill_directly(role, profile, run_settings,
                                       cv_path=cv_path, letter_path=letter_path,
                                       on_step=on_progress,
                                       on_letter_needed=make_letter,
                                       explorative=explorative,
                                       compose=fill_mode == "auto",
                                       tone=resume_rule(
                                           asettings.resume_optimization),
                                       # In "auto" this pass is the first of
                                       # two, and the page must not say the
                                       # form is ready for review while the
                                       # agent is still to come. See the
                                       # badge at the end of _run_direct.
                                       may_hand_over=fill_mode == "auto",
                                       should_stop=should_stop)
                if fill_mode == "direct" or not [
                        q for q in result.unanswered
                        if q not in result.declined]:
                    return result
                tab = result.tab
                if on_progress:
                    on_progress(f"{len(result.unanswered)} question(s) left, "
                                f"asking the model…")
            if auto_cl and not letter_path:
                try:
                    letter_path = make_letter()
                except Exception:
                    letter_path = ""
            try:
                return fill_application(
                    role, profile, run_settings, cv_path=cv_path,
                    letter_path=letter_path,
                    resume_mode=asettings.resume_optimization,
                    explorative=explorative, on_step=on_progress,
                    continue_in=tab, should_stop=should_stop)
            except AutofillError as exc:
                # The agent could not run at all — most often because the
                # model plan is too small for it, which no form is small
                # enough to fix. Saying so and stopping leaves the user
                # with an empty browser and nothing filled, when the path
                # that works is sitting right here.
                log.info("apply: the agent could not run (%s); "
                         "falling back to the lookup", exc)
                if on_progress:
                    on_progress("The agent could not run, so Sushi is filling what it "
                                "knows instead…")
                result = fill_directly(
                    role, profile, run_settings, cv_path=cv_path,
                    letter_path=letter_path, on_step=on_progress,
                    on_letter_needed=make_letter, explorative=explorative,
                    compose=not explorative,
                    tone=resume_rule(asettings.resume_optimization),
                    # Same tab again: the agent never got as far as the page,
                    # so what the first pass filled is still sitting in it.
                    continue_in=tab, should_stop=should_stop)
                return replace(
                    result,
                    message=f"{exc}\n\nSushi filled the form the other way "
                            f"instead. {result.message}")

        def done(result):
            if track:
                self._apply_worker = None
            self.busy(False, "")
            if result.submitted:
                status = "submitted"
            elif result.ok or result.left_open:
                status = "needs_review"
            elif (result.message or "").startswith("Stopped"):
                # You stopped it: nothing is wrong with the job. "failed"
                # took it off every feed for good.
                status = "matched"
            else:
                status = "failed"
            note = ("Needs: " + "; ".join(result.unanswered)) if result.unanswered else ""
            _keep_open_questions(result)
            # Recorded on the application, not only reported once. An
            # application sent with guessed answers in it is a thing you may
            # want to find again a week later, and the tracker row is the
            # only place that survives the dialog being dismissed.
            guessed = getattr(result, "guessed", None)
            if guessed:
                note = (f"{len(guessed)} answer(s) guessed: "
                        + "; ".join(q for q, _ in guessed) + ". " + note).strip()
            if result.left_open:
                note = ("Browser left open to finish. " + note).strip()
            storage.set_status(app_id, status, note)
            if result.submitted:
                note_applied(1)
            self.apps_changed.emit()
            # A browser may have just been left open, or just been closed
            # because the form went. Either way the title bar's way out of
            # the run is now wrong until something repaints it.
            self.refresh_chrome()
            if announce:
                # The headline is what happened; the report is what it did.
                # They used to be one block of text with the outcome buried
                # three lines into it.
                dialogs.tell(
                    self,
                    "Submitted." if result.submitted else
                    "Filled in — your turn." if result.left_open else "Done.",
                    subject=role.title,
                    sub=role.company,
                    body=(("The browser is still open, so you can check it, "
                           "finish anything Sushi could not fill, and send it "
                           "yourself.\n\n" if result.left_open else "")
                          + _pretty_report(result.message or "")[:1500]).strip(),
                    button_text="Close")
            if on_finished:
                on_finished(status, result)

        def failed(msg):
            if track:
                self._apply_worker = None
            storage.set_status(app_id, "failed", msg[:200])
            self.apps_changed.emit()
            self.refresh_chrome()
            if announce:
                self.error(msg)
            else:
                self.set_status(f"{role.company}: {msg[:120]}")
            if on_finished:
                on_finished("failed", None)

        w = Worker(job)
        if track:
            # Held so disarming can reach it. Only for the batch: a single
            # apply somebody started by hand is not Insane mode's to stop.
            self._apply_worker = w
        w.signals.finished.connect(done)
        w.signals.failed.connect(failed)
        w.signals.progress.connect(self.set_status)
        if on_step:
            # The same progress the status bar gets, for a caller that wants
            # to show it somewhere the user is actually looking.
            w.signals.progress.connect(on_step)
        self.start_worker(w)

    # --- Insane mode: the part that actually applies -------------------------
    #
    # Arming used to set a flag, light a badge and change a word on a button.
    # Nothing read that flag except the things painting it, so the console
    # said ARMED and RUNNING over a feature that had no runner at all.
    #
    # It applies one role at a time rather than all of them at once, for a
    # reason that is not just politeness: every session shares one Chromium
    # user-data directory — which is what keeps you logged into Greenhouse
    # between applications — and Chromium allows exactly one process per
    # directory. Fifteen at once is not a busier version of this; it is a
    # broken one.

    _autoapply_queue: list[Role] = []
    _autoapply_running = False      # an application is being filled right now
    _autoapply_armed = False        # the standing order is in force
    _autoapply_done = 0
    #: One entry per role in this run: ``{key, company, title, score, state,
    #: detail, url}``. The page renders it as a table, so a run that is doing
    #: nothing has to say which role it is stuck on and why — the console
    #: caption alone could say "armed and waiting" over any of a dozen
    #: different situations, which is how this came to look broken.
    _autoapply_rows: list[dict] = []

    def autoapplying(self) -> bool:
        """Armed — which is what the button reflects, running or waiting."""
        return self._autoapply_armed

    def autoapply_filling(self) -> bool:
        """Actually filling something right now."""
        return self._autoapply_running

    def autoapply_rows(self) -> list[dict]:
        """The current run, for a page that has just been opened."""
        return list(self._autoapply_rows)

    def _rows_emit(self) -> None:
        self.autoapply_rows_changed.emit(list(self._autoapply_rows))

    def _row_update(self, key: str, **changes) -> None:
        for row in self._autoapply_rows:
            if row["key"] == key:
                row.update(changes)
                break
        self._rows_emit()

    def _rows_blocked(self, reason: str) -> None:
        """One row saying why nothing is going to happen.

        An empty table under an armed button is the same dead end as the
        caption was: it is doing nothing, and there is no way to tell whether
        that means "nothing qualifies", "no model" or "it crashed".
        """
        self._autoapply_rows = [{
            "key": "\x00blocked", "company": "", "title": reason,
            "score": 0, "state": "blocked", "detail": "", "url": "",
        }]
        self._rows_emit()

    def start_autoapply(self) -> None:
        """Arm it. Applies to whatever qualifies now, and keeps standing by.

        The first version treated arming as a one-shot: if nothing cleared the
        quality bar at that instant it printed a line and gave up, while the
        button went on saying RUNNING. With the bar on "Only the best" and a
        feed where nothing scored 15, that meant arming the headline feature
        did nothing at all and said nothing about why.

        Armed is a *standing order* now. Nothing to do yet is a normal state,
        not a failure: it waits, and the moment a search turns up a role it
        has not dealt with, it starts on it. The bar itself is gone — the
        queue is the feed in match order, down to the daily cap.
        """
        cfg = load_autoapply()
        if not self._profile:
            self.set_status("Insane mode needs your CV first. Profile → Read my CV.")
            self.autoapply_changed.emit(False, "Needs your CV first.")
            self._rows_blocked("Needs your CV first. Profile → Read my CV.")
            return
        if not self._insane_allowed():
            self.set_status(self._insane_blocked_note())
            self.autoapply_changed.emit(False, self._insane_blocked_note())
            self._rows_blocked(self._insane_blocked_note())
            return

        self._autoapply_armed = True
        if self._autoapply_running:
            return
        # The last run was stopped but its form is still winding down. A
        # second run started now opened its own Chrome on the same profile:
        # Chrome refused to start twice, and the two runs closed each
        # other's browser mid-fill. Wait for the old one to let go.
        if self._apply_worker is not None:
            self.autoapply_changed.emit(
                True, "Finishing the last form first, then starting…")
            QTimer.singleShot(1500, lambda: self._autoapply_armed
                              and self.start_autoapply())
            return

        # A queue someone built by hand is not a draft to be improved on.
        # Arming again — from the title bar, from a scan arriving, from
        # opening the page — used to rebuild the list from the quality bar
        # and throw away whatever had been chosen deliberately.
        if self._autoapply_queue:
            note = f"{len(self._autoapply_queue)} already queued, carrying on."
            self.autoapply_changed.emit(True, note)
            if not self._autoapply_running:
                self._autoapply_running = True
                self._autoapply_next()
            return

        picks = autoapply_preview(cfg, self._matches)
        if not picks:
            note = self._waiting_note(cfg)
            self.autoapply_changed.emit(True, note)
            self._rows_blocked(note)
            return

        # The queue goes on screen before the first browser opens. Watching a
        # list of fifteen roles appear and then tick over one by one is the
        # whole of what was missing: pressing the button used to produce a
        # changed caption and nothing else for the next minute.
        self._autoapply_queue = list(picks)
        self._autoapply_rows = [{
            "key": r.key, "company": r.company, "title": r.title,
            "score": r.score, "state": "queued", "detail": "", "url": r.url,
        } for r in picks]
        self._rows_emit()
        self._autoapply_running = True
        self._autoapply_done = 0
        log.info("insane mode: starting on %d role(s)", len(picks))
        self.autoapply_changed.emit(True, f"Starting on {len(picks)} application(s)…")
        self._autoapply_next()

    def autoapply_these(self, roles: list[Role]) -> int:
        """Arm Insane mode on exactly these roles. Returns how many queued.

        The difference from :meth:`start_autoapply` is only where the list
        comes from. Arming normally means "work through whatever clears the
        bar"; this means "work through the eleven I ticked", which is the
        same machine with the picking done by a person instead of a score.

        Everything else holds: the same guards, the same daily cap, the same
        table on the Insane mode page, the same one-at-a-time runner — one
        Chromium profile allows one process, however the queue was chosen.
        """
        from ..extras import applied_today, load_autoapply, save_autoapply

        roles = [r for r in roles if r]
        if not roles:
            return 0
        if not self._profile:
            self.set_status("Insane mode needs your CV first. Profile → Read my CV.")
            return 0
        if not self._insane_allowed():
            self.set_status(self._insane_blocked_note())
            return 0

        cfg = load_autoapply()
        queued_already = len(self._autoapply_queue)
        room = max(0, cfg.daily_cap - applied_today() - queued_already)
        if room <= 0:
            self.set_status(
                f"Today's cap of {cfg.daily_cap} is spent or already queued. "
                f"Raise it on the Insane mode page.")
            return 0
        picks, dropped = roles[:room], max(0, len(roles) - room)

        # Armed, and written down as armed — the page reads the saved flag,
        # so a run started from here must light the same badge as the big
        # button does. Two ways in, one state.
        cfg.enabled = True
        save_autoapply(cfg)
        self._autoapply_armed = True

        known = {r["key"] for r in self._autoapply_rows}
        self._autoapply_queue += [r for r in picks if r.key not in known]
        self._autoapply_rows += [{
            "key": r.key, "company": r.company, "title": r.title,
            "score": r.score, "state": "queued", "detail": "", "url": r.url,
        } for r in picks if r.key not in known]

        # Claimed *before* anything is emitted. ``_rows_emit`` and
        # ``refresh_chrome`` both run slots, and one of them reaches
        # ``autoapply_recheck`` — which, seeing an armed window that is not
        # yet running, called ``start_autoapply`` and replaced this
        # hand-picked queue with the score-picked one. The roles you ticked
        # were dropped between ticking them and the page opening.
        was_running, self._autoapply_running = self._autoapply_running, True
        self._rows_emit()
        self.refresh_chrome()

        note = f"Queued {len(picks)} application(s)"
        if dropped:
            note += f"; {dropped} over today's cap of {cfg.daily_cap}"
        self.set_status(note + ".")
        self.autoapply_changed.emit(True, note + "…")
        log.info("insane mode: queued %d hand-picked role(s)", len(picks))

        if not was_running:
            self._autoapply_next()
        return len(picks)

    def _waiting_note(self, cfg=None) -> str:
        """Why nothing is happening, in terms the user can act on.

        There is no quality bar to lower any more, so the dead end this used
        to explain — "nothing clears the bar", with no way to see what the
        bar was or what the feed held — cannot arise. What is left are the
        two honest reasons for an armed run doing nothing: the cap is spent,
        or there is nothing on the feed it has not already dealt with.
        """
        from ..extras import applied_today, load_autoapply, still_open
        cfg = cfg or load_autoapply()
        if applied_today() >= cfg.daily_cap:
            return (f"Today's cap of {cfg.daily_cap} is spent. Armed again "
                    f"tomorrow, or raise the cap below.")
        if not self._matches:
            return "Armed. Waiting for the first job search to finish…"
        ready = len(still_open(self._matches))
        if ready:
            return f"Armed · {ready} role(s) ready to start, best first."
        return (f"Armed, and every one of your {len(self._matches)} matches "
                f"has been applied to or passed on. Leave this armed and "
                f"Sushi will start the moment a search turns up a new one.")

    def autoapply_recheck(self) -> None:
        """New matches arrived — act on them if armed and idle."""
        if self._autoapply_armed and not self._autoapply_running:
            self.start_autoapply()

    #: The batch's current application, while one is running. Only ever set
    #: for Insane mode: an apply somebody started by hand is not this
    #: button's to stop.
    _apply_worker = None

    def stop_autoapply(self, note: str = "") -> None:
        """Disarm. The queue is dropped and the open application is stopped.

        It used to let the one in flight run to the end, on the grounds that
        killing a half-filled form mid-type leaves partial answers in front
        of an employer. But a half-filled form left open is this app's
        *normal* ending — it is what every run that needs review looks like
        — and the thing that rationale was protecting had a much worse
        failure on the other side of it: with autosubmit on, an application
        could still be sent, seconds after the user pressed stop and was
        told "nothing will be sent on its own".

        So the run is stopped. Not killed: the agent is asked to stop and
        does so at the top of its next step, the lookup between one page and
        the next — points where nothing is half-typed and the page is
        settled. What was filled stays on screen in the open browser.
        """
        had = bool(self._autoapply_queue)
        self._autoapply_armed = False
        worker = self._apply_worker
        if worker is not None:
            # The flag the run polls. It is read between steps, so this
            # returns long before the application does.
            worker.cancel()
        dropped = {r.key for r in self._autoapply_queue}
        self._autoapply_queue = []
        # What was filled stays on the table; what never started says so,
        # rather than sitting on "Queued" for the rest of the session.
        if dropped:
            for row in self._autoapply_rows:
                if row["key"] in dropped:
                    row["state"] = "skipped"
                    row["detail"] = "Stopped before this one."
            self._rows_emit()
        if self._autoapply_running or note:
            self._autoapply_running = False
            self.autoapply_changed.emit(
                False, note or ("Stopped." if had else "Finished."))

    def _run_summary(self) -> None:
        from collections import Counter
        states = Counter(r.get("state") for r in self._autoapply_rows)
        lines = []
        if states.get("submitted"):
            lines.append(f"• {states['submitted']} submitted")
        if states.get("needs_review"):
            lines.append(f"• {states['needs_review']} filled and open in the "
                         f"browser for you to check and submit")
        if states.get("failed"):
            lines.append(f"• {states['failed']} could not be filled")
        if states.get("skipped"):
            lines.append(f"• {states['skipped']} skipped")
        body = "\n".join(lines) + (
            "\n\nSubmit the open forms first if you want them sent. "
            "Finishing closes every browser Sushi opened.")
        if dialogs.ask(self, "Insane mode is done",
                       subject=f"{len(self._autoapply_rows)} job(s) worked through",
                       body=body, confirm="Finish & close browsers",
                       cancel="Keep them open"):
            self.finish_autoapply_run()

    def finish_autoapply_run(self) -> None:
        """End the run: disarm, close Sushi's browsers, clear the list.

        Closing happens off the UI thread — each browser gets up to ten
        seconds to shut — and answers typed into open forms are saved first,
        by close_open_sessions itself.
        """
        from .. import autofill
        from ..extras import load_autoapply, save_autoapply
        cfg = load_autoapply()
        if cfg.enabled:
            cfg.enabled = False
            save_autoapply(cfg)
        self.stop_autoapply("Finished.")
        self._autoapply_rows = []
        self._rows_emit()
        self.refresh_chrome()
        self.set_status("Closing Sushi's browsers…")
        self.run(autofill.close_open_sessions,
                 on_done=lambda _r=None: self.set_status(
                     "Insane mode finished. Browsers closed."))

    def _autoapply_next(self) -> None:
        if not self._autoapply_running:
            return
        if not self._autoapply_armed:
            self._autoapply_running = False
            return
        if not self._autoapply_queue:
            done, self._autoapply_done = self._autoapply_done, 0
            self._autoapply_running = False
            # Still armed: the next search may turn up more.
            note = (f"{done} application(s) filled and waiting for you to "
                    f"review and submit. ") if done else ""
            self.autoapply_changed.emit(
                self._autoapply_armed, note + self._waiting_note()
                if self._autoapply_armed else note.strip())
            self.refresh_chrome()
            if done:
                # The run has an end, and says so. Before, the last form was
                # filled and nothing happened: browsers open, Insane mode
                # still armed, no word that the batch was over.
                QTimer.singleShot(300, self._run_summary)
            return

        role = self._autoapply_queue.pop(0)

        # Taken down since the feed saw it. Skipping costs one indexed
        # lookup and saves a browser launch, a model call and one of a
        # fifteen-a-day cap spent on an advert for a job that is gone —
        # which is what the closure tracking was built for and, until now,
        # nothing actually consulted.
        from .. import jobsdb
        try:
            gone = jobsdb.known_closed(role)
        except Exception:
            gone = False
        if gone:
            log.info("insane mode: skipping %s at %s, no longer on the board",
                     role.title, role.company)
            self._row_update(role.key, state="skipped",
                             detail="No longer on the board, so it was skipped.")
            # Recorded, so the next run does not pick it and skip it again.
            try:
                aid = storage.upsert_role(role, status="skipped")
                storage.set_status(aid, "skipped", "No longer on the board")
            except Exception as exc:                   # noqa: BLE001
                log.info("insane mode: could not mark closed job: %s", exc)
            QTimer.singleShot(0, self._autoapply_next)
            return

        left = len(self._autoapply_queue)
        self.autoapply_changed.emit(
            True, f"Filling {role.title} at {role.company}"
                  + (f" · {left} more after this" if left else ""))
        self._row_update(role.key, state="filling", detail="Opening the form…")

        def finished(status: str, result) -> None:
            self._autoapply_done += 1
            _keep_open_questions(result)
            guessed = len(getattr(result, "guessed", None) or []) if result else 0
            if result is None:
                detail = "Did not get as far as the form."
            elif status == "matched":
                detail = "Stopped. Back in the list for next time."
            elif getattr(result, "unanswered", None):
                detail = f"{len(result.unanswered)} question(s) need you."
            elif status == "submitted":
                detail = "Submitted."
            elif guessed:
                detail = (f"Filled, with {guessed} answer(s) guessed. Check the "
                          f"amber fields before you submit.")
            else:
                detail = "Filled. Review and submit in the browser."
            self._row_update(role.key, state=status, detail=detail)
            self.refresh_chrome()
            # Re-entered through the event loop rather than called directly:
            # this is a signal handler, and starting the next application
            # inside it nests the whole chain one frame deeper each time.
            QTimer.singleShot(400, self._autoapply_next)

        # Visible, always. The browser being on screen is the entire point of
        # the design the user asked for: it fills, you check it, you submit.
        self.begin_apply(role, watch=True, announce=False, on_finished=finished,
                         track=True,
                         on_step=lambda text, k=role.key:
                             self._row_update(k, detail=text[:120]))

    def _run_action(self, action: str, arg: str) -> None:
        """Execute an action proposed by the assistant."""
        if action == "apply_top":
            self.navigate("Dashboard")
            self._pages["Dashboard"]._apply_top5()
        elif action == "open_tracker":
            self.navigate("Tracker")
        elif action == "show_applications":
            self.navigate("Dashboard")
        elif action == "rescan":
            self.navigate("Dashboard")
            self.start_scan()
        elif action == "tailor_resume":
            self.navigate("Profile")
            self._profile_sub("Resume")
        elif action == "write_cover_letter":
            self.navigate("Profile")
            self._profile_sub("Cover Letter")
        elif action == "manual_apply":
            self.navigate("Manual apply")
        elif action in ("set_targets", "set_locations"):
            self._set_preferences(action, arg)
        elif action == "undo_preferences":
            self.undo_preferences()
        elif action == "remember_answer":
            self.remember_answers(arg)
        elif action == "undo_remember":
            self.undo_remember()
        else:
            log.warning("assistant proposed an action nothing handles: %r", action)
            self.set_status(f"I don't know how to {action.replace('_', ' ')} yet.")

    def _profile_sub(self, name: str) -> None:
        """Land on the right tab of Profile, not merely on the page.

        "Write my cover letter" opening the resume builder is the kind of
        near-miss that reads as the action having done nothing.
        """
        page = self._pages.get("Profile")
        if page is not None and hasattr(page, "_set_sub"):
            try:
                page._set_sub(name)
            except Exception:
                log.debug("profile has no sub-page %r", name)

    #: The last preference change, so it can be put back. One deep: the useful
    #: undo is always the thing you just said, and a stack of them would need a
    #: history UI nobody asked for.
    _pref_undo: tuple[str, list[str]] | None = None

    def _set_preferences(self, action: str, arg: str) -> None:
        """Let the conversation steer what Sushi looks for.

        This is what replaced lanes. Saying "I want analytics roles in Bristol"
        is the whole configuration, and it lands on the profile the matcher
        already reads — so a rescan straight afterwards actually uses it.
        """
        values = [v.strip() for v in (arg or "").split(",") if v.strip()]
        if not values or not self._profile:
            self.set_status("Tell me the roles you want and I will look for those.")
            return
        field = "target_titles" if action == "set_targets" else "target_locations"
        if field == "target_locations":
            # Said out loud counts as chosen: re-reading the CV must not
            # quietly put back the places it guessed.
            storage.set_setting(LOCATIONS_CHOSEN, "1")
        self._pref_undo = (field, list(getattr(self._profile, field, []) or []))
        setattr(self._profile, field, values)
        self.set_profile(self._profile)
        what = "Looking for" if action == "set_targets" else "Searching in"
        self.set_status(f"{what}: {', '.join(values)}. Searching again…")
        self.start_scan()

    def undo_preferences(self) -> None:
        """Put back what the last preference change replaced."""
        if not self._pref_undo or not self._profile:
            self.set_status("Nothing to put back.")
            return
        field, old = self._pref_undo
        self._pref_undo = None
        setattr(self._profile, field, old)
        self.set_profile(self._profile)
        self.set_status(
            f"Back to: {', '.join(old)}. Searching again…" if old
            else "Cleared. Searching on your CV alone again.")
        self.start_scan()

    def last_preference_change(self) -> tuple[str, list[str]] | None:
        """What the last change replaced, if anything. For the chat's Undo."""
        return self._pref_undo

    #: What the last ``remember_answer`` overwrote: [(question, answer before,
    #: source before)], with an empty answer where nothing was there. Same
    #: depth as the preference undo, for the same reason.
    _answer_undo: list[tuple[str, str, str]] | None = None

    def remember_answers(self, arg: str) -> None:
        """Bank what the user just told the assistant about themselves.

        The assistant used to answer "notice period isn't something Sushi
        tracks" and point at a settings page that has no such field. It is
        tracked — it is exactly what the answer bank is for — so a fact stated
        in the chat now lands in the same store the form-filler reads, as a
        ``human`` answer, which outranks anything the agent guessed.

        The gates are the bank's own: a password or a demographic question is
        refused here as it is everywhere else, and the refusal is reported
        rather than swallowed.
        """
        from ..autofill import learn_demographics

        pairs = parse_pairs(arg)
        if not pairs:
            self.set_status("Tell me the fact and I will put it in your answers.")
            return

        allow_demo = learn_demographics()
        undo: list[tuple[str, str, str]] = []
        saved: list[str] = []
        refused = 0
        for question, answer in pairs:
            if not learning.learnable(question, answer,
                                      include_demographics=allow_demo):
                refused += 1
                continue
            undo.append((question, learning.recall_exact(question),
                         learning.source_of(question)))
            if learning.remember(question, answer, source="human"):
                saved.append(question)

        self._answer_undo = undo or None
        self.refresh_learning_view()
        if saved:
            self.set_status(f"Noted. {'; '.join(saved)} saved to your answers. "
                            f"Forms that ask will be filled with it.")
        elif refused:
            self.set_status(
                "Sushi will not store that one: passwords and demographic "
                "answers stay out of the bank. Answer it in the form itself.")
        else:
            self.set_status("Already had that one, so nothing changed.")

    def undo_remember(self) -> None:
        """Put the answer bank back to what it said before the last chat turn."""
        if not self._answer_undo:
            self.set_status("Nothing to put back.")
            return
        for question, answer, source in self._answer_undo:
            # Forget first even when something is going back: the bank refuses
            # to let a guess overwrite a human answer, which is right
            # everywhere except here, where the human answer is the one being
            # taken away.
            learning.forget(question)
            if answer:
                learning.remember(question, answer, source=source or "agent")
        self._answer_undo = None
        self.refresh_learning_view()
        self.set_status("Put your answers back the way they were.")

    def refresh_learning_view(self) -> None:
        """Re-draw Settings' answer list, if that is what is on screen.

        Writing to the bank from the chat while the list of it is open and
        unchanged is the kind of thing that makes a feature look like it
        silently failed.
        """
        page = self._pages.get("Settings")
        if page is not None and hasattr(page, "_load_learning"):
            try:
                page._load_learning()
            except Exception:
                log.debug("settings page could not reload the answer bank")

    # --- misc -----------------------------------------------------------------

    #: Shortest gap between two full page rebuilds, in milliseconds.
    _REFRESH_GAP = 400

    def _on_state_changed(self) -> None:
        """Rebuild the current page — at most this often.

        ``on_show`` rebuilds a page from scratch: the dashboard throws away
        two hundred cards and table rows and builds two hundred more. Three
        signals feed this, and during an Insane mode run all three fire
        repeatedly — a status per application, a batch of matches per scan —
        so the app spent a run destroying and recreating a few hundred
        widgets every couple of seconds, for a page nobody was necessarily
        looking at.

        The first signal is still honoured immediately, so nothing feels
        delayed and a test that emits once and looks straight after still
        sees the change. A burst collapses into one trailing rebuild.
        """
        now = time.monotonic() * 1000
        since = now - getattr(self, "_last_refresh", -1e9)
        if since < self._REFRESH_GAP:
            self._refresh_pending.start(int(self._REFRESH_GAP - since))
            return
        self._refresh_pending.stop()
        self._last_refresh = now
        current = self.stack.currentWidget()
        if hasattr(current, "on_show"):
            current.on_show()
        self._refresh_identity()

    def refresh_chrome(self) -> None:
        """Re-read the sidebar's footer and badges. For pages that change state
        without going through one of the three signals."""
        self._refresh_identity()

    # --- finishing with the browser -------------------------------------------

    @staticmethod
    def open_browsers() -> int:
        """How many browser windows Sushi is holding open for review."""
        try:
            from .. import autofill
            return autofill.open_browsers()
        except Exception:
            return 0

    def close_browsers(self) -> None:
        """End the run: shut the browsers Sushi left open.

        Asked first, and not only out of politeness. A form that is filled
        but not submitted lives in the page — close the window and it is
        gone, and refilling it costs another model pass. The tracker keeps
        the row either way, so what is actually lost is the typing, which is
        exactly what somebody pressing a button labelled "close" may not have
        in mind.
        """
        n = self.open_browsers()
        if not n:
            self.set_status("No browser windows are open.")
            self.refresh_chrome()
            return
        waiting = storage.counts_by_status().get("needs_review", 0)
        body = ("Anything Sushi filled but you have not submitted is still "
                "only in the page, and closing the window loses it. "
                "Submitted applications are safe, and every row stays on "
                "the Tracker.")
        if waiting:
            body = (f"{waiting} application(s) are marked as needing you. "
                    + body)
        if not dialogs.ask(
                self, "Close the browser?",
                subject=f"{n} window{'s' if n > 1 else ''} open",
                sub="Sushi left them up so you could review and submit.",
                body=body, confirm="Close it" if n == 1 else "Close them all",
                danger=True):
            return
        try:
            from .. import autofill
            autofill.close_open_sessions()
        except Exception as exc:
            log.info("could not close the open browsers: %s", exc)
            self.error("Could not close the browser. Close its window yourself.")
            return
        self.set_status("Closed. What you submitted is on the Tracker.")
        self.refresh_chrome()
        # The banner that offered this is now describing browsers that are
        # gone, so it is told directly rather than left to the next repaint.
        page = self._pages.get("Insane mode")
        if page is not None and hasattr(page, "_render_finished"):
            page._render_finished()

    def _refresh_identity(self) -> None:
        """Keep the sidebar's footer and counts honest about current state."""
        s = load_settings()
        p = self._profile
        self.sidebar.set_identity(
            p.full_name if p else "",
            "local" if s.llm_enabled else "No model yet")
        counts = storage.counts_by_status()
        # No count on the Dashboard. It used to show len(self._matches),
        # which is the in-memory feed — capped at 400 — so the sidebar read
        # "Dashboard 400" directly above "All jobs 3,508" and the 400 was an
        # implementation detail wearing the clothes of a fact. The Dashboard
        # is a shortlist of four; the number that means something lives on
        # the page that holds all of them.
        n = self.open_match_total()
        self.sidebar.set_count("Matches", f"{n:,}" if n else "")
        self.sidebar.set_count("Applications", str(sum(counts.values())) if counts else "")
        needs = counts.get("needs_review", 0)
        self.sidebar.set_count("Home", str(needs) if needs else "")

        open_browsers = self.open_browsers()
        if hasattr(self, "browsers_btn"):
            self.browsers_btn.setText(
                "Close browser" if open_browsers == 1
                else f"Close {open_browsers} browsers")
            self.browsers_btn.setToolTip(
                "Finish with the filled forms and shut the browser Sushi "
                "left open. Anything not submitted is lost.")
            self.browsers_btn.setVisible(bool(open_browsers))

        cfg = load_autoapply()
        if hasattr(self, "today_lbl"):
            from ..extras import applied_today
            self.today_lbl.setText(
                f"{applied_today()} of {cfg.daily_cap} today" if cfg.enabled else "")
        self.sidebar.set_mode("Insane Mode · on" if cfg.enabled else "Manual")
        self.insane_btn.setObjectName("SegBtnOn" if cfg.enabled else "SegBtn")
        self.insane_btn.setIcon(icons.icon(
            "flame", 16, theme.REC if cfg.enabled else theme.MUTED))
        self.insane_btn.style().unpolish(self.insane_btn)
        self.insane_btn.style().polish(self.insane_btn)
        if hasattr(self, "log_count"):
            self.log_count.setText(str(_log_lines()))

    @staticmethod
    def _settled_count(counts: dict[str, int]) -> int:
        """How many jobs All jobs drops because they are already dealt with.

        The page filters on :data:`sushi.extras.OFF_THE_LIST`; this counts
        the same set, so the badge and the page agree. Not ``SETTLED``:
        that includes the filled-but-unsubmitted, which the page now keeps
        and tints rather than hiding — counting them here would put the
        badge six below the number the page prints.
        """
        from ..extras import OFF_THE_LIST

        return sum(n for status, n in counts.items() if status in OFF_THE_LIST)

    def _check_model(self) -> None:
        s = load_settings()
        if hasattr(self, "assistant"):
            self.assistant.refresh_model()
        if s.llm_enabled:
            src = Path(s.key_source).name if "\\" in s.key_source or "/" in s.key_source else s.key_source
            self.set_status(f"Ready · key from {src}.")
        else:
            self.set_status("No model set up yet. Choose one in Settings → Model.")

    # --- drag & drop a CV anywhere --------------------------------------------

    def closeEvent(self, e) -> None:  # noqa: N802
        """Shut the background work down before the interpreter goes away.

        A scan is twenty-four threads deep in ``ThreadPoolExecutor``. Closing
        the window without cancelling it leaves them submitting work into a
        pool that is being torn down, which surfaces as "cannot schedule new
        futures after interpreter shutdown" on the way out — harmless, but it
        is a traceback the user did not cause and should never see.
        """
        try:
            self.stop_scan()
        except Exception:
            pass
        try:
            from .. import autofill
            # Unregistered first: the browser loop outlives this window by a
            # moment, and a click landing in that gap would emit a signal on
            # a widget that is being torn down.
            autofill.set_fill_handler(None)
            autofill.close_open_sessions()
        except Exception:
            pass
        try:
            from .. import bridge
            bridge.stop()
        except Exception:
            pass
        super().closeEvent(e)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.suffix.lower() in SUPPORTED:
                self.navigate("Profile")
                # Navigating builds the page, so there is one to hand it to.
                # It stores the path and reads it, the same as choosing the
                # file there — a drop should not be the one route that leaves
                # you a button to press.
                page = self._pages.get("Profile")
                if page is not None:
                    page.use_cv(str(p))
                else:
                    storage.set_active_resume(storage.add_resume(str(p)))
                self.set_status(f"Loaded {p.name}.")
                return
        self.error("That file type isn't supported. Use PDF, DOCX, TXT or MD.")


class RoleDialog(QDialog):
    def __init__(self, parent: MainWindow, role: Role) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{role.title} · {role.company}")
        self.setStyleSheet(theme.STYLESHEET)
        self.resize(560, 460)
        self._parent = parent
        self._role = role

        v = QVBoxLayout(self)
        v.setContentsMargins(26, 24, 26, 22)
        v.setSpacing(12)
        v.addWidget(label(role.title, "H2"))
        v.addWidget(label(f"{role.company} · {role.location or 'Remote'}", "Muted"))
        if role.reasons:
            v.addWidget(label("Why it fits: " + "; ".join(role.reasons), "Faint"))
        if role.description:
            from PyQt6.QtWidgets import QTextBrowser
            tb = QTextBrowser()
            # The document keeps its own default font, which the stylesheet's
            # font-family never reaches — a job description came out in a
            # serif face nothing else in the app uses.
            tb.setFont(theme.body_font(15))
            tb.setPlainText(role.description[:4000])
            v.addWidget(tb, 1)
        else:
            v.addStretch()

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(button("Open job page", "Chip",
                             lambda: QDesktopServices.openUrl(QUrl(role.url))))
        row.addWidget(button("Write cover letter", "Chip", self._letter))
        row.addStretch()
        row.addWidget(button("Apply", "Primary", self._apply))
        v.addLayout(row)

    def _apply(self) -> None:
        self.accept()
        self._parent.apply_to_role(self._role)

    def _letter(self) -> None:
        p = self._parent.profile
        if not p:
            self._parent.error("Build your profile first.")
            return
        s = load_settings()
        self._parent.busy(True, "Writing cover letter…")

        def job():
            path, text = write_letter(LLM(s), p, self._role)
            render_pdf(text, path.with_suffix(".pdf"), author=p.full_name)
            return path.with_suffix(".pdf")

        def done(path):
            self._parent.busy(False, f"Saved {Path(path).name}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

        self._parent.run(job, on_done=done)
