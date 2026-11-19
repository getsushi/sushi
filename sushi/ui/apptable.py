"""Every application, with where it got to — one widget, used twice.

The Dashboard shows this as a summary under the feed. The Tracker needs it
too, and for a sharper reason: the Tracker's board only holds applications
that have *reached a stage*, so a user with thirty-three applications and no
stages saw "Nothing in the pipeline yet" under a sidebar badge reading 33.
The board was right, the badge was right, and the page was useless.

Rather than a second copy that drifts from the first, both pages embed this.
"""
from __future__ import annotations

import datetime as _dt
import os

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QTableWidget, QVBoxLayout, QWidget)

from .. import storage
from ..models import Role
from .components import (ROW_HEIGHT, button, company_cell, h2, muted,
                         status_cell, text_cell, tune_table)

#: Tab name -> the statuses it shows. ``None`` means everything.
STATUS_TABS: list[tuple[str, set[str] | None]] = [
    ("All", None),
    ("Submitted", {"submitted"}),
    ("In flight", {"queued", "filling"}),
    ("Needs you", {"needs_review"}),
    ("Failed", {"failed"}),
    ("Skipped", {"skipped"}),
]


def when(stamp: str) -> str:
    """A date a person reads, not one a database stores.

    "2026-10-19" is ten characters of which three matter, and it was
    costing enough width to elide the role beside it.
    """
    if not stamp:
        return "—"
    try:
        d = _dt.datetime.strptime(stamp.split(" ")[0], "%Y-%m-%d").date()
    except ValueError:
        return stamp.split(" ")[0]
    today = _dt.date.today()
    if d == today:
        return "Today"
    if (today - d).days == 1:
        return "Yesterday"
    if d.year == today.year:
        return d.strftime("%-d %b") if os.name != "nt" else d.strftime("%#d %b")
    return d.strftime("%b %Y")


class ApplicationsTable(QWidget):
    """The applications table and its status tabs."""

    #: The role behind a pressed Open button.
    open_requested = pyqtSignal(object)

    def __init__(self, title: str = "All applications",
                 empty_text: str = "") -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        if title:
            lay.addWidget(h2(title))

        self.tab_row = QHBoxLayout()
        self.tab_row.setSpacing(6)
        self._tab_buttons: dict[str, object] = {}
        for name, _ in STATUS_TABS:
            b = button(name, "Chip", lambda n=name: self.set_tab(n))
            self._tab_buttons[name] = b
            self.tab_row.addWidget(b)
        self.tab_row.addStretch()
        holder = QWidget()
        holder.setLayout(self.tab_row)
        lay.addWidget(holder)

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("AppTable")
        self.table.setHorizontalHeaderLabels(
            ["COMPANY", "ROLE", "STATUS", "WHEN", ""])
        # WHEN gets 122, not 96. Every cell carries 18px of padding each
        # side, so 96 left about 60px for a mono date — enough for "Today"
        # and not for "19 Oct", which is why this column has been showing
        # "Yest…" and "19 O…" since it was built. STATUS and the Open link
        # are likewise as wide as their longest content and no wider; the
        # company and the role divide the rest.
        tune_table(self.table, widths={2: 160, 3: 112, 4: 96},
                   weights={0: 1.0, 1: 1.5})
        lay.addWidget(self.table)

        self.empty = muted(empty_text or
                           "Nothing here yet. Apply to something and it "
                           "appears in this table, with where it got to.")
        lay.addWidget(self.empty)
        self._active_tab = "All"

    # --- state ---------------------------------------------------------------

    @property
    def active_tab(self) -> str:
        return self._active_tab

    def row_count(self) -> int:
        return self.table.rowCount()

    def set_tab(self, name: str) -> None:
        self._active_tab = name
        for n, b in self._tab_buttons.items():
            b.setObjectName("ChipActive" if n == name else "Chip")
            b.style().unpolish(b)
            b.style().polish(b)
        self.refresh()

    def refresh(self) -> None:
        """Re-read the applications and paint the current tab."""
        statuses = next((s for t, s in STATUS_TABS if t == self._active_tab),
                        None)
        apps = storage.list_applications()
        if statuses is not None:
            apps = [a for a in apps if a.status in statuses]

        t = self.table
        t.setRowCount(0)
        for i, a in enumerate(apps):
            r = t.rowCount()
            t.insertRow(r)
            t.setRowHeight(r, ROW_HEIGHT)
            t.setCellWidget(r, 0, company_cell(a.role.company, i))
            t.setCellWidget(r, 1, text_cell(a.role.title))
            t.setCellWidget(r, 2, status_cell(a.status))
            t.setCellWidget(r, 3, text_cell(when(a.updated_at),
                                            muted_text=True, mono=True))
            t.setCellWidget(r, 4, self._open_cell(a.role))

        # Height follows the content. Left at its default the table was a
        # 260px box with its own scrollbar inside the page's scrollbar —
        # four rows visible, two nested scrollbars, and half a screen of
        # nothing underneath. Sized to its rows there is one scrollbar, the
        # page's, and the table uses the room it has.
        self._fit_height(len(apps))
        t.setVisible(bool(apps))
        self.empty.setVisible(not apps)

    #: Beyond this the table scrolls internally rather than growing. Forty
    #: rows is already two screens; past that a widget tall enough to hold
    #: every row costs more than it gives.
    MAX_GROWN_ROWS = 40

    def _fit_height(self, rows: int) -> None:
        shown = min(max(rows, 1), self.MAX_GROWN_ROWS)
        header = self.table.horizontalHeader().height()
        self.table.setMinimumHeight(0)
        self.table.setMaximumHeight(16_777_215)
        self.table.setFixedHeight(header + shown * ROW_HEIGHT + 4)

    def _open_cell(self, role: Role) -> QWidget:
        holder = QWidget()
        holder.setObjectName("ActionCell")
        holder.setStyleSheet("QWidget#ActionCell{background:transparent;}")
        hb = QHBoxLayout(holder)
        hb.setContentsMargins(10, 0, 14, 0)
        hb.addStretch()
        hb.addWidget(button("Open", "Link",
                            lambda _=False, r=role: self.open_requested.emit(r)))
        return holder
