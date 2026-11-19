"""Shared base for pages.

A ``Page`` holds a reference to the controller (the main window), which exposes
shared state and helpers so pages stay thin and decoupled.
"""
from __future__ import annotations

from typing import Protocol

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget


class Controller(Protocol):
    """What pages may call on the main window."""

    def run(self, fn, *args, on_done=None, on_progress=None, **kwargs): ...
    def navigate(self, name: str) -> None: ...
    def set_status(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...
    def busy(self, on: bool, msg: str = "") -> None: ...


class Page(QWidget):
    #: nav title; set per subclass
    title: str = "Page"
    #: assistant context key
    assistant_key: str = "dashboard"

    def __init__(self, app) -> None:  # app: MainWindow (Controller)
        super().__init__()
        self.app = app
        self._built = False

    def ensure_built(self) -> None:
        if not self._built:
            self.build()
            self._built = True

    def build(self) -> None:  # override
        raise NotImplementedError

    def on_show(self) -> None:
        """Called each time the page becomes visible; override to refresh."""


def scroll_page() -> tuple[QWidget, QVBoxLayout, QScrollArea]:
    """A vertically scrolling content area with comfortable padding.

    One margin and one spacing for every page in the app, off the scale in
    :mod:`sushi.ui.theme`. They were 28/24 — not square, for no reason — and
    each page then chose its own gaps on top, so no two pages began at the
    same y or breathed at the same rhythm.
    """
    from .. import theme

    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(theme.PAGE_PAD, theme.PAGE_PAD,
                           theme.PAGE_PAD, theme.PAGE_PAD)
    lay.setSpacing(theme.GAP_MD)
    lay.setAlignment(Qt.AlignmentFlag.AlignTop)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(host)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    # Nothing in this UI is wider than its column — a horizontal bar here only
    # ever means something refused to shrink, and hiding it makes that show up
    # as reflowed text rather than as content pushed off the right edge.
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return host, lay, scroll
