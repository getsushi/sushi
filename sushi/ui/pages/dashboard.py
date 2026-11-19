"""Dashboard — the match feed and the applications table (Tsenta's home)."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from ... import storage
from ...extras import (IN_FLIGHT, SETTLED, applied_today, load_autoapply,
                       still_open)
from .. import theme
from ..apptable import ApplicationsTable, when
from ..components import (ROW_HEIGHT, FlowLayout, JobCard, SearchField, Stat,
                          button, divider, faint, h2, muted, page_title)
from ..starfield import GridPanel, transparent
from .base import Page, scroll_page

#: The hero's figures. The earlier design's display scale tops out at 28 for a page title;
#: this is the one band on the Dashboard whose whole content is three
#: numbers, and it is read from across the desk rather than at it.
HERO_STAT = 44


def _hero_rule() -> QWidget:
    """A hairline between two figures, in the panel's own white."""
    rule = QWidget()
    rule.setObjectName("HeroRule")
    rule.setFixedWidth(1)
    rule.setFixedHeight(52)
    rule.setStyleSheet(
        "QWidget#HeroRule{background:rgba(247,248,239,0.22);}")
    return rule


class DashboardPage(Page):
    title = "Dashboard"
    assistant_key = "dashboard"

    def build(self) -> None:
        host, lay, scroll = scroll_page()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.opener = page_title("Good to see you.", over="DASHBOARD")
        lay.addWidget(self.opener)

        self.search = SearchField("Search your matches by title, company or keyword…")
        self.search.edit.textChanged.connect(self._render_matches)
        lay.addWidget(self.search)

        lay.addWidget(self._hero())

        # top matches header + actions
        head = QHBoxLayout()
        head.setSpacing(theme.GAP_SM)
        head.addWidget(h2("Top job matches"))
        # How many there actually are. The grid is capped, so without this
        # the page said "9" when it meant "the nine it had room to draw",
        # and a feed of fifteen hundred looked identical to a feed of nine.
        self.match_count = faint("")
        head.addWidget(self.match_count)
        head.addStretch()
        self.see_all_btn = button("See all", "Chip",
                                  lambda: self.app.navigate("All jobs"),
                                  icon="arrow-right")
        self.see_all_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        head.addWidget(self.see_all_btn)
        self.scan_btn = button("Look again", "Chip", self._toggle_scan, icon="refresh")
        head.addWidget(self.scan_btn)
        # The one primary action on this screen. It used to be here *and* on
        # the hero panel, eighty pixels apart, which reads as two different
        # things until you have pressed one of them.
        self.apply_all_btn = button(f"Apply to top {self._FEED_CARDS}",
                                    "ChipPrimary", self._apply_top5)
        head.addWidget(self.apply_all_btn)
        lay.addLayout(head)

        self.scan_lbl = faint("")
        lay.addWidget(self.scan_lbl)

        self.matches_host = QWidget()
        self.matches_flow = FlowLayout(self.matches_host, spacing=theme.GAP_SM)
        lay.addWidget(self.matches_host)
        # Cards are sized to the row, so a resize that changes how many fit
        # redraws them rather than leaving one stranded on a row of its own.
        self._cols = 0
        self.matches_host.installEventFilter(self)

        self.empty_matches = muted("Looking for your first matches…")
        lay.addWidget(self.empty_matches)

        lay.addWidget(divider())

        # applications — the same widget the Tracker uses, so the two can
        # never drift apart.
        self.applications = ApplicationsTable()
        self.applications.open_requested.connect(self.app.show_role_details)
        lay.addWidget(self.applications)
        # Kept as names because this page and its tests have always reached
        # for them; they now point into the shared widget.
        self.app_table = self.applications.table
        self.app_empty = self.applications.empty
        self._tab_buttons = self.applications._tab_buttons
        self.app.scan_state_changed.connect(self._scan_state)
        self._scan_state(self.app.scanning())

    # --- discovery ------------------------------------------------------------

    def _toggle_scan(self) -> None:
        if self.app.scanning():
            self.app.stop_scan()
        else:
            self.app.start_scan()

    def _scan_state(self, running: bool) -> None:
        """Reflect the window's scan, which may have started without us."""
        self.scan_btn.setText("Stop" if running else "Look again")
        self.scan_lbl.setText("Searching careers pages…" if running else "")
        self.scan_lbl.setVisible(running)

    # The mockup's `show_empty_state` link is gone. It was a developer
    # affordance for checking the empty copy without emptying your own feed,
    # and it shipped into the dashboard with a snake_case identifier for a
    # label, sitting beside Stop and Apply to top 5 as though it were
    # something a user should press. The empty state is covered by qa_shots.

    # --- hero ---------------------------------------------------------------

    def _hero(self) -> QWidget:
        """Where you stand, in three numbers and nothing else.

        This was a sentence — "15 applications left in today's cap. Insane
        mode is on standby." — which is two numbers and a state wearing
        eleven words. Numbers can be read at a glance; a sentence has to be
        read.

        What has since gone is everything that was not a number. A white
        pill opening Insane mode, which the sidebar and the title bar both
        already offer, and a line under it repeating a state those two also
        show — a button and a caption sharing a band with three figures, so
        the eye had four things to sort before it could read any of them.
        Three figures, evenly spread, divided by a hairline: The earlier design's metric
        row, which is the one thing this panel was always trying to be.
        """
        panel = GridPanel(padding=(theme.CARD_PAD, theme.GAP_LG,
                                   theme.CARD_PAD, theme.GAP_LG))
        panel.body.setSpacing(0)
        # Content height. Left to grow, the panel took a share of the page's
        # spare space and its two lines drifted to opposite ends of it.
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        row = transparent(QWidget())
        hb = QHBoxLayout(row)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(theme.GAP_LG)

        self.stat_matches = Stat("—", "matches", on_dark=True, size=HERO_STAT)
        self.stat_left = Stat("—", "left today", on_dark=True, size=HERO_STAT)
        self.stat_sent = Stat("—", "sent today", on_dark=True, size=HERO_STAT)
        for i, s in enumerate((self.stat_matches, self.stat_left,
                               self.stat_sent)):
            if i:
                hb.addWidget(_hero_rule(), 0, Qt.AlignmentFlag.AlignVCenter)
            # Equal shares, so the three of them hold the width the button
            # used to take rather than huddling at the left of an empty band.
            hb.addWidget(s, 1, Qt.AlignmentFlag.AlignVCenter)

        panel.body.addWidget(row)
        return panel

    # --- lifecycle ----------------------------------------------------------

    def on_show(self) -> None:
        self._render_banner()
        self._render_matches()
        self._set_tab(self._active_tab)

    # --- rendering ----------------------------------------------------------

    def _render_banner(self) -> None:
        cfg = load_autoapply()
        used = applied_today()
        p = self.app.profile
        first = (p.full_name.split()[0] if p and p.full_name else "")
        self.opener.title_label.setText(
            f"Good to see you, {first}." if first else "Good to see you.")
        self.stat_matches.set_value(f"{self.app.open_match_total():,}")
        self.stat_left.set_value(f"{max(0, cfg.daily_cap - used):,}")
        self.stat_sent.set_value(f"{used:,}")

    #: How many cards the feed draws. The Dashboard is a morning glance,
    #: not the list — a wall of cards is not a glance, and with a full job
    #: database there are a few thousand above the bar. Four, and "See all"
    #: goes to the page that holds the rest.
    _FEED_CARDS = 4

    #: Narrowest a card may get before a column is dropped.
    _CARD_MIN = 280

    def _grid(self) -> tuple[int, int]:
        """Columns that fit, and the card width that fills the row exactly.

        Fixed 300px cards left a gap down the right of the feed, and four
        of them in a three-wide row left the fourth on its own.
        """
        sp = theme.GAP_SM
        w = max(self.matches_host.width(), self._CARD_MIN)
        cols = max(1, (w + sp) // (self._CARD_MIN + sp))
        # A few pixels short: a row that is exactly full wraps on rounding.
        return cols, (w - (cols - 1) * sp) // cols - 2

    def _card_count(self, cols: int) -> int:
        """Whole rows only: about four cards, never a lone one."""
        rows = max(1, -(-self._FEED_CARDS // cols))
        if cols >= 3:
            rows = 1
        return cols * rows

    def eventFilter(self, obj, ev):  # noqa: N802
        from PyQt6.QtCore import QEvent
        if obj is self.matches_host and ev.type() == QEvent.Type.Resize:
            cols, width = self._grid()
            if cols != self._cols:
                self._render_matches()
            else:
                for i in range(self.matches_flow.count()):
                    item = self.matches_flow.itemAt(i)
                    if item and item.widget():
                        item.widget().setFixedWidth(width)
        return super().eventFilter(obj, ev)

    def _render_matches(self) -> None:
        q = self.search.text().strip().lower()
        # A job you have applied to is on the Tracker, and the next best one
        # takes its card. Without this the feed kept offering it, Apply and
        # all — four cards that never changed however many you sent, and no
        # way to tell a fresh match from one you dealt with yesterday.
        matches = still_open(self.app.matches)
        if q:
            matches = [r for r in matches
                       if q in r.title.lower() or q in r.company.lower()
                       or any(q in s.lower() for s in r.reasons)]
        # clear
        while self.matches_flow.count():
            item = self.matches_flow.takeAt(0)
            if item and item.widget():
                widget = item.widget()
                widget.setParent(None)
                widget.deleteLater()
        self._cols, card_w = self._grid()
        top = matches[:self._card_count(self._cols)]
        self.apply_all_btn.setText(f"Apply to top {len(top)}" if top else "Apply to top")
        self.empty_matches.setVisible(not top)
        if not top:
            # "Looking…" is only true while a search is running. Otherwise
            # it sat there for ever over an empty feed, saying nothing about
            # what to do.
            self.empty_matches.setText(
                "Looking for your first matches…" if self.app.scanning() else
                "No matches yet. Press Look again on All jobs to search, or "
                "widen your target roles and locations in Profile.")
        # The true total, not the length of the in-memory feed: that is
        # capped, so the Dashboard said "of 400" while All jobs said 2,121.
        # ``open_match_total`` and not ``match_total``, so "See all 497" does
        # not open a page headed 484.
        total = (len(matches) if q
                 else max(len(matches), self.app.open_match_total()))
        self.match_count.setText(
            "" if not total else
            (f"{total:,} above the bar" if total <= len(top) else
             f"the best {len(top)} of {total:,}"))
        self.see_all_btn.setText(
            f"See all {total:,}" if total > len(top) else "See all")
        self.see_all_btn.setVisible(total > len(top))
        for i, role in enumerate(top):
            c = JobCard(role, tint_index=i)
            c.setFixedWidth(card_w)
            c.apply_clicked.connect(self.app.apply_to_role)
            c.pass_clicked.connect(self.app.pass_role)
            c.details_clicked.connect(self.app.show_role_details)
            c.save_clicked.connect(self.app.save_role)
            self.matches_flow.addWidget(c)

    @property
    def _active_tab(self) -> str:
        return self.applications.active_tab

    def _set_tab(self, name: str) -> None:
        self.applications.set_tab(name)

    #: Already dealt with — queueing these again would either duplicate an
    #: application or undo a decision the user already made.
    _SETTLED = SETTLED | IN_FLIGHT

    def _apply_top5(self) -> None:
        """Queue the best matches that are not already in flight.

        ``upsert_role`` keeps an existing row's status, so the first version of
        this looked like it worked and quietly queued nothing whenever the top
        matches had been seen before. Now the already-handled ones are counted
        and reported rather than silently skipped.
        """
        existing = storage.status_by_url()
        queued, skipped = 0, 0
        # The cards on screen, which is what the button names.
        for role in still_open(self.app.matches)[:self._card_count(self._cols or 1)]:
            if existing.get(role.url) in self._SETTLED:
                skipped += 1
                continue
            app_id = storage.upsert_role(role, status="queued")
            storage.set_status(app_id, "queued")
            queued += 1

        self.app.apps_changed.emit()
        if queued:
            note = f" ({skipped} already handled)" if skipped else ""
            self.app.set_status(
                f"Queued {queued} application(s){note}. Review them below or in the Tracker.")
        elif skipped:
            self.app.set_status(
                f"Nothing new to queue. Your top {skipped} matches are already in flight.")
        else:
            self.app.set_status("No matches to queue yet. Run a scan in Browse jobs.")
        self._set_tab("In flight")

    # --- helpers ------------------------------------------------------------

    #: Date formatting and the table's own tuning moved into
    #: :mod:`sushi.ui.apptable` when the Tracker started using the same
    #: widget. Kept here as names because both are referenced elsewhere.
    _ROW_HEIGHT = ROW_HEIGHT
    _when = staticmethod(when)
