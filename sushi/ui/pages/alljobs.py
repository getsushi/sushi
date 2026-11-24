"""All jobs — every vacancy that matches, not just the pick of them.

The Dashboard shows four. That is deliberate: it is a morning glance, and a
wall of cards is not a glance. But four out of twelve hundred is a sample,
and the other eleven hundred and ninety-six were unreachable — the grid was
capped and nothing said so, which is part of why a feed of nine looked
identical to a feed of fifteen hundred.

So the feed is a shortlist and this is the list. A table rather than cards:
at this length the eye reads down a column, and a thousand cards is a
thousand widgets for a page nobody scrolls to the bottom of.

Three things make a list this long usable, and they are all here:

- **Filters**, because searching for a word you have not thought of yet is
  not a way to narrow three and a half thousand rows.
- **Tick boxes and one button**, because applying to eleven roles should
  not be eleven separate decisions to press the same button.
- **Rows that leave**, because a job you have applied to is not a job you
  are choosing between any more. It goes to the Tracker, which is where you
  look for it afterwards.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QRectF, Qt, QThreadPool, QTimer, QUrl
from PyQt6.QtGui import (QAction, QActionGroup, QColor, QDesktopServices,
                         QPainter)
from PyQt6.QtWidgets import (QCheckBox, QHBoxLayout, QMenu, QProgressBar,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from ... import storage
from .. import dialogs, theme
from ..components import (ROW_HEIGHT, ElidedLabel, FlowLayout, SearchField,
                          button, company_cell, faint, icon_button, label, muted,
                          page_title, text_cell, tune_table)

#: Taller than other tables: each match carries its "Why:" line, as in the
#: wireframe.
MATCH_ROW = 72


def _role_cell(role) -> QWidget:
    """The role, and why it matched, the way the wireframe's Matches list
    reads: title on top, "Why: …" under it."""
    host = QWidget()
    host.setObjectName("TextCell")
    host.setStyleSheet("QWidget#TextCell{background:transparent;}")
    v = QVBoxLayout(host)
    v.setContentsMargins(theme.CELL_PAD, 8, theme.CELL_PAD, 8)
    v.setSpacing(2)
    title = ElidedLabel(role.title)
    title.setStyleSheet(f"background:transparent;color:{theme.INK};"
                        f"font-size:{theme.SIZE_SMALL + 1}px;font-weight:600;")
    v.addWidget(title)
    why = ", ".join(r for r in (role.reasons or [])[:3] if r)
    if why:
        w = ElidedLabel(f"Why: {why}")
        w.setToolTip(", ".join(role.reasons))
        w.setStyleSheet(f"background:transparent;color:{theme.MUTED};"
                        f"font-size:{theme.SIZE_SMALL}px;")
        v.addWidget(w)
    return host


def _match_cell(pct: int) -> QWidget:
    host = text_cell(f"{pct}%")
    for lbl in host.findChildren(ElidedLabel):
        lbl.setStyleSheet(f"background:transparent;color:{theme.ACCENT};"
                          f"font-size:{theme.SIZE_BODY}px;font-weight:600;")
    return host
from .base import Page, scroll_page
from ..workers import Worker

#: One definition of "already dealt with", shared with the Dashboard and
#: with the sidebar badge — see ``extras``. They used to be two, and
#: disagreed.
from ...extras import (IN_FLIGHT, NEEDS_YOU,  # noqa: E402,F401
                       OFF_THE_LIST as GONE)

#: The wash on a row being worked, in the same amber Sushi outlines a
#: field it is filling with. Deep enough to find at a glance down a long
#: table — #FFF6E0 was correct and invisible.
TINT_WORKING = "#FAEDC8"

#: And the wash on one that is waiting for you. The accent rather than the
#: amber, because these two rows mean opposite things — amber is Sushi
#: working, coral is Sushi stopped and wanting you — and a long table where
#: both read as "in progress" is the distinction lost.
TINT_NEEDS_YOU = "#F8DCD0"

DAY = 86400.0
#: Date buckets. Measured from the board's posting date where it publishes
#: one (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee), and
#: from when Sushi first saw the vacancy otherwise.
AGES = [("Any time", 0.0), ("Last 24 hours", 1.0), ("Last 3 days", 3.0),
        ("Last week", 7.0), ("Last month", 30.0)]

MATCHES = [("Any match", 0), ("60%+", 60), ("70%+", 70), ("80%+", 80),
           ("90%+", 90)]

#: How many distinct values a "pick from the list" filter offers. Past this
#: the menu is longer than the page and the search box is the better tool.
MENU_LIMIT = 25


class FilterChip(QWidget):
    """One filter: a chip that opens a menu and says what it is holding.

    ``single`` chips are a choice (a date range); the others are a set (any
    of these locations). Either way the chip shows its own state, because a
    filter you cannot see is a list that is wrong for no visible reason.
    """

    def __init__(self, name: str, on_change, single: bool = False,
                 icon: str = "") -> None:
        super().__init__()
        self._name = name
        self._single = single
        self._on_change = on_change
        self._icon = icon
        self.chosen: set[str] = set()
        self._options: list[str] = []

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.btn = button(name, "Chip", self._open, icon=icon or "chevron-down")
        row.addWidget(self.btn)

    def set_options(self, options: list[str]) -> None:
        """Offer these, keeping any choice that still exists."""
        self._options = list(options)
        kept = self.chosen & set(options)
        if kept != self.chosen:
            self.chosen = kept
        self._paint()

    def clear(self) -> None:
        if self.chosen:
            self.chosen = set()
            self._paint()

    def _paint(self) -> None:
        if not self.chosen:
            self.btn.setText(self._name)
        elif self._single or len(self.chosen) == 1:
            self.btn.setText(f"{self._name}: {next(iter(self.chosen))[:22]}")
        else:
            self.btn.setText(f"{self._name} · {len(self.chosen)}")
        self.btn.setObjectName("ChipActive" if self.chosen else "Chip")
        self.btn.style().unpolish(self.btn)
        self.btn.style().polish(self.btn)

    def _open(self) -> None:
        menu = QMenu(self)
        if self._single:
            group = QActionGroup(menu)
            group.setExclusive(True)
        for option in self._options:
            act = QAction(option, menu)
            act.setCheckable(True)
            act.setChecked(option in self.chosen)
            if self._single:
                group.addAction(act)
            act.triggered.connect(
                lambda checked, o=option: self._pick(o, checked))
            menu.addAction(act)
        if self.chosen:
            menu.addSeparator()
            reset = QAction(f"Clear {self._name.lower()}", menu)
            reset.triggered.connect(self._reset)
            menu.addAction(reset)
        # popup, not exec. exec spins a nested event loop and does not
        # return until the menu is dismissed — which freezes anything
        # driving the app that is not a pair of hands, and is a nested
        # loop inside a signal handler even when there are.
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(self.btn.mapToGlobal(self.btn.rect().bottomLeft()))

    def _pick(self, option: str, checked: bool) -> None:
        if self._single:
            self.chosen = {option} if checked else set()
        elif checked:
            self.chosen.add(option)
        else:
            self.chosen.discard(option)
        self._paint()
        self._on_change()

    def _reset(self) -> None:
        self.clear()
        self._on_change()


class AllJobsPage(Page):
    title = "All jobs"
    assistant_key = "dashboard"

    #: Rows drawn at once. The query is instant at any size; the widgets are
    #: not, and nobody reads past a few hundred — the search narrows faster
    #: than scrolling does.
    PAGE_SIZE = 200
    #: Rows built before the page first paints, then per idle tick after.
    _FIRST_BATCH = 20
    _BATCH = 15

    def build(self) -> None:
        host, lay, scroll = scroll_page()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        #: Keys of the roles ticked. Kept across renders on purpose: a
        #: filter narrowing the list must not quietly unpick what you had
        #: already chosen out of it.
        self._selected: set[str] = set()
        self._shown: list = []

        head = QHBoxLayout()
        head.addWidget(page_title("Matches", over="BEST FIRST"), 1)
        # Manual apply, from the older layout: any job, from a pasted link.
        head.addWidget(button("Paste a job link", "", lambda: self.app.navigate("Manual apply"),
                              icon="external-link"), 0, Qt.AlignmentFlag.AlignBottom)
        lay.addLayout(head)

        #: The jobs above the bar, as last read from the database. Filters
        #: and the search narrow *this*; only a data change re-reads it,
        #: and that read runs off the UI thread. ``None`` = never loaded.
        self._pool_roles: list | None = None
        self._loading = False
        self._reload_again = False
        self._render_gen = 0
        self._workers: set = set()

        self.search = SearchField("Search by title, company or keyword…")
        # Debounced: each keystroke used to redo the whole page.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._render)
        self.search.edit.textChanged.connect(lambda _t: self._search_timer.start())
        # One redraw for a burst of updates: while Sushi applies, every
        # status change used to redraw this whole table three times.
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(120)
        self._render_timer.timeout.connect(self._render)
        lay.addWidget(self.search)

        # --- filters ---------------------------------------------------------
        bar_host = QWidget()
        bar_host.setObjectName("Filters")
        bar_host.setStyleSheet("QWidget#Filters{background:transparent;}")
        bar = FlowLayout(bar_host, spacing=6)
        self.f_date = FilterChip("Date", self._render, single=True, icon="clock")
        self.f_place = FilterChip("Location", self._render, icon="compass")
        self.f_work = FilterChip("Workplace", self._render)
        self.f_company = FilterChip("Company", self._render)
        self.f_match = FilterChip("Match", self._render, single=True)
        self.f_date.set_options([name for name, _ in AGES])
        self.f_match.set_options([name for name, _ in MATCHES])
        self.f_work.set_options(["Remote", "Hybrid", "On site"])
        self.filters = [self.f_date, self.f_place, self.f_work,
                        self.f_company, self.f_match]
        for chip in self.filters:
            bar.addWidget(chip)
        self.saved_btn = button("Saved", "Chip", self._toggle_saved,
                                icon="bookmark")
        self._saved_only = False
        bar.addWidget(self.saved_btn)
        self.clear_btn = button("Clear filters", "Link", self._clear_filters)
        bar.addWidget(self.clear_btn)
        lay.addWidget(bar_host)

        # --- count, freshness, refresh ---------------------------------------
        head = QHBoxLayout()
        head.setSpacing(theme.GAP_SM)
        self.count_lbl = muted("")
        head.addWidget(self.count_lbl, 1)
        # One line. Wrapped, "Updated 4 hours ago" broke after "hours" and
        # the second word sat below the row, dragging the bar's height with
        # it and leaving the Look again button off-centre against it.
        self.updated_lbl = label("", "Faint", wrap=False)
        head.addWidget(self.updated_lbl, 0, Qt.AlignmentFlag.AlignRight)
        # "Look again" said what it did to the boards and nothing about the
        # other half of a score. Your CV decides the rest of it, and editing
        # it left this list answering the old question with no way to ask
        # again short of going to Profile and pressing Read my CV. So:
        # Refresh, and it means the whole feed — the CV first if the file has
        # changed since Sushi read it, then the boards.
        self.refresh_btn = button("Refresh", "Chip",
                                  lambda: self.app.refresh_all(), icon="refresh")
        self.refresh_btn.setToolTip(
            "Re-read your CV if you have changed it, re-score the list "
            "against it, then look for new jobs.")
        head.addWidget(self.refresh_btn, 0, Qt.AlignmentFlag.AlignRight)
        lay.addLayout(head)

        # --- the selection bar, which only exists when something is ticked ---
        self.pick_row = QWidget()
        self.pick_row.setObjectName("PickBar")
        self.pick_row.setStyleSheet(
            f"QWidget#PickBar{{background:{theme.SURFACE_2};"
            f"border:1px solid {theme.LINE_2};border-radius:12px;}}")
        pick = QHBoxLayout(self.pick_row)
        pick.setContentsMargins(16, 10, 12, 10)
        pick.setSpacing(8)
        self.pick_lbl = muted("")
        pick.addWidget(self.pick_lbl, 1)
        pick.addWidget(button("Select all shown", "Link", self._select_shown))
        pick.addWidget(button("Clear", "Link", self._clear_selection))
        self.apply_sel_btn = button("Apply to selected", "Primary",
                                    self._apply_selected, icon="zap")
        pick.addWidget(self.apply_sel_btn)
        self.pick_row.setVisible(False)
        lay.addWidget(self.pick_row)

        # Above the table, not instead of it. The first jobs arrive minutes
        # into a build that takes the better part of an hour, and a bar that
        # disappears the moment the first row lands leaves the page looking
        # finished when it is five per cent done.
        lay.addWidget(self._building_block())

        self.table = QTableWidget(0, 6)
        self.table.setObjectName("AppTable")
        self.table.setHorizontalHeaderLabels(
            ["", "COMPANY", "ROLE", "WHERE", "MATCH", ""])
        # Three columns are a fixed size because their content is: a tick
        # box, a percentage, and one button. The three that hold language
        # share what is left, with the role taking the largest part because
        # it is what the row is actually about.
        tune_table(self.table,
                   widths={0: 44, 4: 92, 5: 190},
                   weights={1: 1.2, 2: 2.0, 3: 1.1})
        # Opening a job is what clicking its row means. The Details button
        # that used to say so is gone; this is where the behaviour went.
        self.table.setMouseTracking(True)
        self.table.cellClicked.connect(self._row_clicked)
        lay.addWidget(self.table)

        self.skeleton = SkeletonRows(8)
        self.skeleton.setVisible(False)
        lay.addWidget(self.skeleton)

        self.empty = muted("Building your job database…")
        lay.addWidget(self.empty)

        self.more_lbl = faint("")
        lay.addWidget(self.more_lbl)
        lay.addStretch(1)

        # A row that has been applied to leaves this list, so the list has
        # to hear about it happening.
        self.app.apps_changed.connect(self._render_timer.start)
        self.app.scan_progress.connect(self._scan_tick)
        self.app.scan_state_changed.connect(lambda _on: self._paint_building())

    # --- the first build ------------------------------------------------------

    def _building_block(self) -> QWidget:
        """A bar and an estimate for as long as the search is running.

        On a first run this page is empty until boards have been read, and
        "Building your job database…" on its own gives no way to tell a
        minute from an afternoon — so the app looks broken, and the honest
        answer ("about twenty minutes") is unavailable at the one moment it
        is worth anything.
        """
        self.building = QWidget()
        v = QVBoxLayout(self.building)
        v.setContentsMargins(0, 2, 0, 0)
        v.setSpacing(6)
        self.build_bar = QProgressBar()
        self.build_bar.setRange(0, 100)
        self.build_bar.setValue(0)
        self.build_bar.setTextVisible(False)
        self.build_bar.setFixedHeight(6)
        self.build_bar.setMaximumWidth(420)
        v.addWidget(self.build_bar)
        self.build_lbl = faint("")
        v.addWidget(self.build_lbl)
        self.building.setVisible(False)
        return self.building

    def _scan_tick(self, done: int, total: int) -> None:
        # Timed from the first board rather than from the start of the run.
        # A refresh reads the global feeds before it touches a board, and
        # folding those twenty seconds into the average made the first
        # estimate half an hour too pessimistic.
        if done == 0 or done < self._build_done:
            self._build_t0 = time.time()
        self._build_done, self._build_total = done, total
        # Thirteen boards a second on this corpus, and the line only ever
        # says whole counts and whole minutes: redrawing it on every one of
        # them is twelve relayouts a second to show the same sentence.
        now = time.time()
        if done and now - self._build_painted < 0.5:
            return
        self._build_painted = now
        self._paint_building()
        self.updated_lbl.setText("Searching now")

    #: Boards read and boards due in the running refresh, as last reported.
    _build_done = 0
    _build_total = 0
    _build_painted = 0.0
    #: When this run's first board was reported, for the estimate.
    _build_t0 = 0.0

    def _paint_building(self) -> None:
        building = self.app.scanning()
        self.building.setVisible(building)

        # The empty line answers "why is this list empty", which has three
        # different answers. It used to give the first one always — so a
        # filter that matched nothing, on a full database, said "building".
        nothing = self.table.rowCount() == 0
        self.empty.setVisible(nothing)
        if nothing:
            if self.active_filters() or self.search.text().strip():
                self.empty.setText("Nothing matches those filters.")
            elif building:
                self.empty.setText("Building your job database…")
            else:
                self.empty.setText("No jobs on file yet. Press Refresh "
                                   "to start the first search.")
        if not building:
            return
        done, total = self._build_done, self._build_total
        if not total:
            self.build_bar.setValue(0)
            self.build_lbl.setText(
                "Working out which company job boards need reading…")
            return
        self.build_bar.setValue(int(100 * done / total))
        self.build_lbl.setText(
            f"Reading {total:,} company job boards, {done:,} done"
            f"{self._eta_note(done, total)}. Jobs appear here as they are "
            f"found; you can leave this running.")

    def _eta_note(self, done: int, total: int) -> str:
        """An ", about 20 minutes left", once there is enough to say so.

        Averaged over the whole run rather than the last few boards. The
        rate swings by an order of magnitude board to board — one company
        has four vacancies and the next has nine hundred — and an estimate
        that jumps between "2 minutes" and "an hour" is worse than none.
        """
        started = self._build_t0 or self.app.scan_started_at()
        if not started or done < 30 or done >= total:
            return ""
        left = (total - done) * (time.time() - started) / done
        if left < 90:
            return ", under a minute left"
        minutes = round(left / 60)
        if minutes < 60:
            return f", about {minutes} minute{'s' if minutes != 1 else ''} left"
        # Halves, in words. "1.4 hours" is a measurement; nobody waiting for
        # something says it back to themselves that way.
        halves = round(left / 1800) / 2
        whole = int(halves)
        word = {0.5: "half an hour", 1: "an hour", 1.5: "an hour and a half"}
        return ", about " + word.get(halves, f"{whole}½ hours" if halves % 1
                                      else f"{whole} hours") + " left"

    # --- lifecycle -----------------------------------------------------------

    def on_show(self) -> None:
        # Draw what we have at once, then refresh it in the background.
        if self._pool_roles is not None:
            self._render_timer.start()
        self._reload()

    def _reload(self) -> None:
        """Re-read the jobs from the database on a worker thread."""
        if self._loading:
            self._reload_again = True
            return
        self._loading = True
        if self._pool_roles is None:
            self.table.setVisible(False)
            self.empty.setText("Loading your jobs…")
            self.empty.setVisible(True)
            self.skeleton.setVisible(True)

        def done(roles) -> None:
            self._loading = False
            self._pool_roles = roles or []
            self.skeleton.setVisible(False)
            self._render_timer.start()
            if self._reload_again:
                self._reload_again = False
                self._reload()

        def failed(_msg) -> None:
            self._loading = False
            self.skeleton.setVisible(False)
            if self._pool_roles is None:
                self._pool_roles = []
            self._render()

        worker = Worker(self.app.all_matches)
        worker.signals.finished.connect(done)
        worker.signals.failed.connect(failed)
        worker.keep_alive(self._workers)
        QThreadPool.globalInstance().start(worker)

    # --- the filters ---------------------------------------------------------

    def _toggle_saved(self) -> None:
        self._saved_only = not self._saved_only
        self.saved_btn.setObjectName("ChipActive" if self._saved_only else "Chip")
        self.saved_btn.style().unpolish(self.saved_btn)
        self.saved_btn.style().polish(self.saved_btn)
        self._render()

    def _clear_filters(self) -> None:
        for chip in self.filters:
            chip.clear()
        if self._saved_only:
            self._toggle_saved()
        else:
            self._render()

    def active_filters(self) -> int:
        """How many filters are doing something. For the tests and the UI."""
        return sum(1 for c in self.filters if c.chosen) + int(self._saved_only)

    @staticmethod
    def _statuses() -> dict[str, str]:
        """url → status for everything Sushi has a record of."""
        try:
            return storage.status_by_url()
        except Exception:                               # noqa: BLE001
            return {}

    def _matches(self) -> tuple[list, dict[str, str]]:
        """Everything above the bar, narrowed by the search and the filters."""
        from ..details import workplace

        roles = list(self._pool_roles or [])
        status = self._statuses()

        # Gone first, so the filter menus below are built from what is
        # actually on the page rather than from jobs you have dealt with.
        roles = [r for r in roles if status.get(r.url, "") not in GONE]

        q = self.search.text().strip().lower()
        if q:
            roles = [r for r in roles
                     if q in r.title.lower() or q in r.company.lower()
                     or q in (r.location or "").lower()
                     or any(q in s.lower() for s in r.reasons)]

        # The menus offer what is there, not a fixed list that mostly
        # matches nothing. Places are offered by country rather than by the
        # string each board happened to write: the raw values spend the
        # menu's twenty-five slots on "Remote - US", "Remote, US", "US
        # Remote", "Remote - United States" and "United States" — five ways
        # of saying one country — and a place with a couple of hundred jobs
        # never reaches the list at all.
        self.f_place.set_options(_common_places(roles))
        self.f_company.set_options(_common(r.company for r in roles))

        if self.f_date.chosen:
            days = dict(AGES).get(next(iter(self.f_date.chosen)), 0.0)
            if days:
                cutoff = time.time() - days * DAY
                # A job with no date on it is kept: it is older than the
                # table's date column, not newer than the filter.
                roles = [r for r in roles
                         if not r.first_seen or r.first_seen >= cutoff]
        if self.f_match.chosen:
            floor = dict(MATCHES).get(next(iter(self.f_match.chosen)), 0)
            roles = [r for r in roles if (r.match or 0) >= floor]
        if self.f_place.chosen:
            roles = [r for r in roles
                     if self.f_place.chosen & _places_of(r.location)]
        if self.f_company.chosen:
            roles = [r for r in roles if r.company in self.f_company.chosen]
        if self.f_work.chosen:
            roles = [r for r in roles if workplace(r) in self.f_work.chosen]
        if self._saved_only:
            roles = [r for r in roles if status.get(r.url, "") == "matched"]
        return roles, status

    # --- selection -----------------------------------------------------------

    def _select_shown(self) -> None:
        self._selected |= {r.key for r in self._shown}
        self._render()

    def _clear_selection(self) -> None:
        self._selected.clear()
        self._render()

    def _toggle(self, role, on: bool) -> None:
        if on:
            self._selected.add(role.key)
        else:
            self._selected.discard(role.key)
        self._paint_pick_bar()

    def _paint_pick_bar(self) -> None:
        n = len(self._selected)
        self.pick_row.setVisible(bool(n))
        self.pick_lbl.setText(f"{n} job(s) selected")
        verb = "Open" if self.app.applies_myself() else "Apply to"
        self.apply_sel_btn.setText(f"{verb} {n} selected" if n else f"{verb} selected")

    def _apply_selected(self) -> None:
        """Hand the ticked roles to Insane mode and let it work through them.

        Not a loop of Apply presses. One browser profile allows one Chromium,
        so eleven applications at once is not a faster version of this — it
        is a broken one. Insane mode is already the thing that does them one
        at a time with a queue on screen, so this arms it on your list
        instead of on the quality bar.
        """
        picked = [r for r in self._shown if r.key in self._selected]
        if not picked:
            self.app.set_status("Tick some jobs first.")
            return
        if self.app.applies_myself():
            # "I apply myself": open them, nothing more.
            def open_them():
                self.app.open_role_links(picked)
                self._selected.clear()
                self._render()
            if len(picked) > 10:
                dialogs.confirm(self, "Open these jobs?", open_them,
                                subject=f"{len(picked)} tabs",
                                body="Each job opens in its own tab in your browser.",
                                confirm=f"Open {len(picked)}")
            else:
                open_them()
            return
        queued = self.app.autoapply_these(picked)
        if not queued:
            return      # autoapply_these has already said why
        self._selected.clear()
        self.app.navigate("Insane mode")
        self._render()

    # --- drawing -------------------------------------------------------------

    _sushi_applies = True

    def _render(self) -> None:
        self._sushi_applies = not self.app.applies_myself()
        roles, status = self._matches()
        shown = roles[:self.PAGE_SIZE]
        self._shown = shown
        # A row that has left the list cannot stay selected.
        self._selected &= {r.key for r in shown}

        t = self.table
        # Take the old cell widgets out by hand. ``setRowCount(0)`` detaches
        # them but does not destroy them promptly, so a redraw leaves a full
        # set of orphans alive and still connected to ``_toggle`` and
        # ``show_role_details`` — eight Details buttons for four rows, half
        # of them holding a role that has since left the list. Nothing a
        # user can click, but a leak, and anything walking the widget tree
        # finds them.
        for row in range(t.rowCount()):
            for col in range(t.columnCount()):
                old = t.cellWidget(row, col)
                if old is not None:
                    t.removeCellWidget(row, col)
                    old.setParent(None)
                    old.deleteLater()
        t.setRowCount(0)
        t.setRowCount(len(shown))
        for r in range(len(shown)):
            t.setRowHeight(r, MATCH_ROW)

        # Rows are real widgets and cost ~3ms each to build. The first screen
        # is built now; the rest follow in small batches between events, so
        # the window keeps responding while they land. A newer render
        # bumps the generation and the stale batches stop.
        self._render_gen += 1
        gen = self._render_gen

        def fill(start: int, stop: int) -> None:
            if gen != self._render_gen:
                return
            for r in range(start, min(stop, len(shown))):
                role = shown[r]
                state = status.get(role.url, "")
                t.setCellWidget(r, 0, self._tick(role, state))
                t.setCellWidget(r, 1, company_cell(role.company, r))
                t.setCellWidget(r, 2, _role_cell(role))
                t.setCellWidget(r, 3, text_cell(role.location or "—",
                                                muted_text=True))
                t.setCellWidget(r, 4, _match_cell(role.match))
                t.setCellWidget(r, 5, self._actions(role, state))
                if state in IN_FLIGHT:
                    _tint_row(t, r, TINT_WORKING)
                elif state == NEEDS_YOU:
                    _tint_row(t, r, TINT_NEEDS_YOU)
            if stop < len(shown):
                QTimer.singleShot(0, lambda: fill(stop, stop + self._BATCH))

        fill(0, self._FIRST_BATCH)

        header = t.horizontalHeader().height()
        t.setFixedHeight(header + max(1, len(shown)) * MATCH_ROW + 4)
        t.setVisible(bool(shown))
        self._paint_building()
        self._paint_pick_bar()
        self._paint_counts(len(roles), len(shown),
                           sum(1 for r in roles
                               if status.get(r.url, "") == NEEDS_YOU))

    def _paint_counts(self, total: int, drawn: int, needs_you: int = 0) -> None:
        filtered = self.active_filters() or self.search.text().strip()
        self.count_lbl.setText(
            "" if not total else
            f"{total:,} job(s) {'match these filters' if filtered else 'match your profile'}"
            + (f", showing the first {drawn}" if total > drawn else "")
            # Counted over the whole list rather than the drawn page: the
            # tint only helps for a row you can see, and on a feed of four
            # hundred the filled ones are not reliably in the first two
            # hundred.
            + (f" · {needs_you} filled and waiting for you to submit"
               if needs_you else ""))
        self.more_lbl.setText(
            "" if total <= drawn else
            f"{total - drawn:,} more. Narrow them with the filters above.")
        # "Never scanned" beside a bar that is visibly scanning is the page
        # contradicting itself: the stamp is only written when a refresh
        # finishes, which on a first run is half an hour away.
        self.updated_lbl.setText(
            "Searching now" if self.app.scanning() else _freshness())

    #: The tick box as the stylesheet draws it: an 18px indicator inside a
    #: 1px border.
    _TICK = 20

    def _tick(self, role, state: str) -> QWidget:
        holder = QWidget()
        holder.setObjectName("TickCell")
        holder.setStyleSheet("QWidget#TickCell{background:transparent;}")
        hb = QHBoxLayout(holder)
        # No margin of its own. ``QTableView::item`` already pads every cell
        # by 9px, so a 44px column hands this widget 26px — and 14 more on
        # the left, plus the 10px of label spacing an empty QCheckBox still
        # reserves, left the 20px indicator about 12px to draw in. It was
        # painted anyway, clipped down its left edge, on every row.
        hb.setContentsMargins(0, 0, 0, 0)
        box = QCheckBox()
        box.setFixedSize(self._TICK, self._TICK)
        box.setChecked(role.key in self._selected)
        # Already on its way, or already filled: there is nothing to choose
        # about it. Ticking a filled application into a bulk apply would
        # fill it a second time and spend a second day's cap doing it.
        box.setEnabled(state not in IN_FLIGHT and state != NEEDS_YOU)
        box.toggled.connect(lambda on, r=role: self._toggle(r, on))
        hb.addWidget(box, 0, Qt.AlignmentFlag.AlignCenter)
        return holder

    #: Columns whose own widget is the point of the click. The tick box picks
    #: the row for a bulk apply and Apply starts one — neither of them means
    #: "show me this job", so a click that lands in those columns is not a
    #: request to open it.
    _OWN_CLICK = {0, 5}

    def _row_clicked(self, row: int, column: int) -> None:
        if column in self._OWN_CLICK or row >= len(self._shown):
            return
        self.app.show_role_details(self._shown[row])

    def _actions(self, role, state: str) -> QWidget:
        holder = QWidget()
        holder.setObjectName("ActionCell")
        holder.setStyleSheet("QWidget#ActionCell{background:transparent;}")
        hb = QHBoxLayout(holder)
        hb.setContentsMargins(8, 0, 12, 0)
        hb.setSpacing(6)
        hb.addStretch()
        if state in IN_FLIGHT:
            # The row says what is happening to it instead of offering to
            # start it again.
            word = faint("Queued" if state == "queued" else "Applying…")
            word.setStyleSheet(
                f"background:transparent;color:{theme.WARN};"
                f"font-size:{theme.SIZE_SMALL}px;font-weight:600;")
            hb.addWidget(word)
            return holder
        if state == NEEDS_YOU:
            # Open, not Apply — it is filled already. The same word and the
            # same action as the Tracker's "Needs you" tab, because it is
            # the same job in the same state and two names for one move is
            # how a user ends up pressing Apply on a finished form.
            go = button("Submit", "Chip",
                        lambda _=False, r=role: QDesktopServices.openUrl(
                            QUrl(r.url)))
            go.setToolTip("Filled and waiting. Opens the form to check and "
                          "submit.")
            # Never squeezed: beside the tick it was being cut to "ubmi".
            go.setMinimumWidth(go.sizeHint().width())
            hb.addWidget(go)
            # For the site whose confirmation page Sushi did not recognise:
            # one click and the row stops saying "Needs you".
            hb.addWidget(icon_button(
                "check", "I've submitted it: mark this job as sent",
                lambda r=role: self.app.mark_submitted(r), size=28, icon_size=16))
            return holder
        # No "Details" button beside Apply any more. Clicking the row opens
        # the job — which is what a row of a job list does everywhere else,
        # and was already true of the cards on the Dashboard — so the button
        # was a second way to do the thing the row does, charged against the
        # width that was eliding every role on the page to "Senior Data An…".
        # Read once per render, not per row: it touches settings storage.
        auto = self._sushi_applies
        skip = button("Skip", "Link", lambda _=False, r=role: self.app.pass_role(r))
        skip.setToolTip("Not for you. It leaves the list and is not applied to.")
        hb.addWidget(skip)
        go = button("Apply" if auto else "Open", "Primary",
                    lambda _=False, r=role: self.app.apply_to_role(r))
        if not auto:
            go.setToolTip("Opens the job in your browser. You apply yourself.")
        hb.addWidget(go)
        return holder


class SkeletonRows(QWidget):
    """Grey placeholder rows, shown while the job list is first read."""

    def __init__(self, rows: int = 8) -> None:
        super().__init__()
        self._rows = rows
        self.setFixedHeight(rows * ROW_HEIGHT)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.LINE))
        w = self.width()
        for i in range(self._rows):
            y = i * ROW_HEIGHT + ROW_HEIGHT / 2 - 6
            p.drawRoundedRect(QRectF(16, y - 4, 20, 20), 5, 5)
            p.drawRoundedRect(QRectF(52, y, w * 0.18, 12), 6, 6)
            p.drawRoundedRect(QRectF(w * 0.30, y, w * (0.25 + 0.08 * (i % 3)), 12), 6, 6)
            p.drawRoundedRect(QRectF(w * 0.75, y, w * 0.12, 12), 6, 6)
        p.end()


def _tint_row(table: QTableWidget, row: int, colour: str) -> None:
    """Wash a row. The cells hold widgets, so the tint goes behind them."""
    for col in range(table.columnCount()):
        item = QTableWidgetItem()
        item.setBackground(QColor(colour))
        table.setItem(row, col, item)


def _common(values, limit: int = MENU_LIMIT) -> list[str]:
    """The most frequent distinct values, for a pick-from-the-list menu."""
    from collections import Counter

    counted = Counter(v.strip() for v in values if v and v.strip())
    return [v for v, _ in counted.most_common(limit)]


#: What "Remote" and friends are called in the Location menu. A posting that
#: names no country is still somewhere you can filter to — it is the whole
#: of what a remote-from-anywhere search returns — so it gets a name rather
#: than being left out of the list.
_ANYWHERE_OPTION = "Anywhere / remote"


def _places_of(location: str) -> set[str]:
    """The countries and regions a posting's location names.

    The label, not the code: the menu is read by a person, and ``PK`` is
    not a place anyone recognises at a glance.
    """
    from ...discovery import countries_in_place, place_label

    codes = countries_in_place(location)
    return {place_label(c) for c in codes} if codes else {_ANYWHERE_OPTION}


def _common_places(roles, limit: int = MENU_LIMIT) -> list[str]:
    """The countries most of these jobs are in, commonest first."""
    from collections import Counter

    counted: Counter = Counter()
    for r in roles:
        counted.update(_places_of(r.location))
    return [v for v, _ in counted.most_common(limit)]


def _freshness() -> str:
    """How long ago the last scan finished, in words."""
    try:
        when = float(storage.get_setting("last_scan_at", "") or 0)
    except (TypeError, ValueError):
        when = 0.0
    if not when:
        return "Never scanned"
    gap = max(0.0, time.time() - when)
    if gap < 90:
        return "Updated just now"
    if gap < 3600:
        return f"Updated {int(gap // 60)}m ago"
    if gap < DAY:
        return f"Updated {int(gap // 3600)}h ago"
    return f"Updated {int(gap // DAY)}d ago"
