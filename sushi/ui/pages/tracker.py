"""Tracker — the post-application pipeline (Applied → Offer) + CSV export."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QScrollArea,
                             QVBoxLayout, QWidget)

from ... import storage
from ...extras import (PIPELINE_STAGES, STAGE_LABELS, export_tracker_csv,
                       get_stage, set_stage)
from .. import theme
from ..apptable import ApplicationsTable
from ..components import (ElidedLabel, button, divider, faint, h3,
                          icon_button, label, muted, page_title)
from .base import Page, scroll_page


class TrackerPage(Page):
    title = "Tracker"
    assistant_key = "tracker"

    def build(self) -> None:
        host, lay, scroll = scroll_page()
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.addWidget(scroll)

        head = QHBoxLayout()
        head.addWidget(page_title("Applications", over="NEWEST FIRST"), 1)
        head.addWidget(button("Export CSV", "Chip", self._export, icon="download"), 0,
                       Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)

        # Said precisely, because the board and the table below count
        # different things. The board holds what has *reached a stage*; the
        # sidebar badge counts every application. With thirty-three
        # applications and no stages, this page said "nothing in the
        # pipeline" under a badge reading 33 and showed nothing at all.
        self.empty = muted(
            "Nothing has a stage yet. Everything you have applied to is in "
            "the table below.")
        lay.addWidget(self.empty)

        # The five stages divide the width between them rather than each
        # taking a fixed 238px: at 238 the board was 1,240px wide inside a
        # 788px well, so two of the five stages were off the right edge and
        # the page opened on a horizontal scrollbar. The scroll area stays
        # for windows too narrow even for the minimums.
        self.board_host = QWidget()
        self.board_host.setStyleSheet("background: transparent;")
        self.board = QHBoxLayout(self.board_host)
        self.board.setContentsMargins(0, 0, 0, theme.GAP_SM)
        self.board.setSpacing(theme.GAP_SM)

        self.board_scroll = QScrollArea()
        self.board_scroll.setWidgetResizable(True)
        self.board_scroll.setWidget(self.board_host)
        self.board_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.board_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.board_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.board_scroll)
        # The stage board (Applied, Ghosted, Interviewing, Rejected, Offer)
        # is not part of the new design: the page is the table, as in the
        # wireframe. Built but never shown, so nothing that refers to it breaks.
        self.board_scroll.setVisible(False)
        self.empty.setVisible(False)
        # The same widget the Dashboard shows. This is the page a user comes
        # to for "what have I applied to", so it has to answer that whether
        # or not anything has been given a stage.
        self.applications = ApplicationsTable(title="",
            empty_text="Nothing applied to yet. Anything Sushi fills, or that "
                       "you send by hand, appears here.")
        self.applications.open_requested.connect(self.app.show_role_details)
        lay.addWidget(self.applications)
        lay.addStretch(1)

    def on_show(self) -> None:
        self._render()
        self.applications.set_tab(self.applications.active_tab)

    def _render(self) -> None:
        while self.board.count():
            item = self.board.takeAt(0)
            if item and item.widget():
                widget = item.widget()
                widget.setParent(None)
                widget.deleteLater()

        apps = storage.list_applications()
        tracked = [a for a in apps if a.status == "submitted" or get_stage(a.id or 0, a.status)]
        by_stage: dict[str, list] = {s: [] for s in PIPELINE_STAGES}
        for a in tracked:
            by_stage.setdefault(get_stage(a.id or 0, a.status) or "applied", []).append(a)

        # An empty board is five tall grey rectangles saying "—", which looks
        # like a rendering failure rather than an empty pipeline.
        self.empty.setVisible(False)
        self.board_scroll.setVisible(False)
        if not tracked:
            return

        tallest = max(len(v) for v in by_stage.values())
        for stage in PIPELINE_STAGES:
            items = by_stage.get(stage, [])
            col = QFrame()
            col.setObjectName("Sunken")
            col.setMinimumWidth(self._COL_MIN)
            cl = QVBoxLayout(col)
            cl.setContentsMargins(theme.GAP_SM, theme.GAP_SM,
                                  theme.GAP_SM, theme.GAP_SM)
            cl.setSpacing(theme.GAP_XS)
            header = QHBoxLayout()
            header.addWidget(h3(STAGE_LABELS[stage]))
            header.addStretch()
            header.addWidget(label(str(len(items)), "Faint"))
            cl.addLayout(header)
            if not items:
                cl.addWidget(faint("—"))
            for a in items:
                cl.addWidget(self._app_card(a))
            cl.addStretch()
            # Columns are as tall as the fullest one, not as tall as the page.
            col.setFixedHeight(self._COL_CHROME + tallest * self._CARD_PITCH)
            self.board.addWidget(col, 1)
        self.board_scroll.setFixedHeight(
            self._COL_CHROME + tallest * self._CARD_PITCH + 14)

    #: A column narrower than this cannot show a company name, so below it
    #: the board scrolls sideways instead of squeezing further.
    _COL_MIN = 140
    _CARD_H = 74
    _CARD_PITCH = _CARD_H + 6
    _COL_CHROME = 64

    def _app_card(self, a) -> QWidget:
        """One application, on the stage it has reached.

        The stage used to be a full-width combo box *inside the column named
        after that stage* — so every card on the board spelled out, in a
        control as wide as the card, the one thing its position already said.
        Five of those per screen is what made the columns 238px wide and
        pushed two stages off the edge. The chevron opens the same list of
        stages; it just does not recite the current one.
        """
        c = QFrame(); c.setObjectName("Card")
        c.setFixedHeight(self._CARD_H)
        v = QVBoxLayout(c)
        v.setContentsMargins(theme.GAP_SM, theme.GAP_XS, theme.GAP_XS, theme.GAP_XS)
        v.setSpacing(0)

        top = QHBoxLayout()
        top.setSpacing(2)
        company = ElidedLabel(a.role.company)
        company.setStyleSheet(
            f"background:transparent;color:{theme.INK};"
            f"font-size:{theme.SIZE_SMALL}px;font-weight:600;")
        company.setToolTip(a.role.company)
        top.addWidget(company, 1)
        move = icon_button(
            "chevron-down", "Move to another stage",
            size=24, icon_size=14)
        move.clicked.connect(lambda _=False, w=move, app_id=a.id:
                             self._stage_menu(w, app_id))
        top.addWidget(move, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(top)

        role = ElidedLabel(a.role.title)
        role.setStyleSheet(
            f"background:transparent;color:{theme.FAINT};"
            f"font-size:{theme.SIZE_SMALL}px;")
        role.setToolTip(a.role.title)
        v.addWidget(role)
        v.addStretch(1)
        return c

    def _stage_menu(self, anchor: QWidget, app_id: int) -> None:
        """The five stages, under the card's own chevron."""
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        for stage in PIPELINE_STAGES:
            menu.addAction(STAGE_LABELS[stage],
                           lambda s=stage: self._move(app_id, s))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _move(self, app_id: int, stage: str) -> None:
        set_stage(app_id, stage)
        self._render()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export tracker", "tracker.csv", "CSV (*.csv)")
        if not path:
            return
        Path(path).write_text(export_tracker_csv(), encoding="utf-8")
        self.app.set_status(f"Exported to {Path(path).name}")
