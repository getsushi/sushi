"""Background work.

Everything slow - reading a CV, calling a model, scanning 1000 careers pages,
driving a browser - runs on a QThreadPool worker. The UI thread only ever
receives signals. Qt will crash if a widget is touched from another thread, so
workers return data and the UI does the updating.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger(__name__)

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot


class WorkerSignals(QObject):
    finished = pyqtSignal(object)      # the return value
    failed = pyqtSignal(str)           # a message fit to show a user
    progress = pyqtSignal(str)


class Worker(QRunnable):
    """Runs ``fn`` off the UI thread.

    If ``fn`` accepts an ``on_progress`` keyword it is given one that emits the
    progress signal, so long jobs can narrate themselves.
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self._cancelled = False
        # The pool must not delete this. A QRunnable is auto-deleted the
        # moment run() returns, and run() returns immediately after emitting
        # ``finished`` — which, being a cross-thread emit, is *queued* and
        # not delivered until the UI thread next runs. So the C++ half went
        # away, the Python wrapper followed it (nobody keeps a reference to
        # a worker), ``signals`` was destroyed with it, and the UI thread
        # then dispatched a queued call from a sender that no longer
        # existed: a native access violation inside the interpreter, in the
        # frame of whichever slot was being invoked. ``keep_alive`` below is
        # the other half of this; both are needed.
        self.setAutoDelete(False)

        import inspect
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            params = {}
        if "on_progress" in params:
            self.kwargs["on_progress"] = self._progress
        if "should_stop" in params:
            self.kwargs["should_stop"] = lambda: self._cancelled

    def keep_alive(self, holder: Any) -> None:
        """Hold a reference to this worker until it has reported.

        Called by whoever starts it. The reference is dropped on the event
        loop turn *after* the result slots have run, so a worker cannot be
        collected between emitting and being heard.
        """
        from PyQt6.QtCore import QTimer

        holder.add(self)

        def release(*_a: Any) -> None:
            QTimer.singleShot(0, lambda: holder.discard(self))

        # Connected last by construction: callers wire their own slots before
        # starting the worker, and Qt invokes direct connections in order.
        self.signals.finished.connect(release)
        self.signals.failed.connect(release)

    def _progress(self, text: str) -> None:
        """Narrate, but never take the job down with you.

        A scan runs for a minute across two dozen threads and emits progress
        the whole time. If the window closes — or the worker is dropped — in
        the middle of one, the C++ half of ``signals`` is destroyed and the
        next emit raises *inside the scan*, which PyQt reports as the scan
        having failed. The search did not fail; nobody was listening.
        """
        if self._cancelled:
            return
        try:
            self.signals.progress.emit(text)
        except (RuntimeError, AttributeError):
            # The receiver has gone; so has any reason to keep narrating.
            self._cancelled = True

    def cancel(self) -> None:
        self._cancelled = True

    @pyqtSlot()
    def run(self) -> None:
        name = getattr(self.fn, "__name__", repr(self.fn))
        log.info("worker start: %s", name)
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            # The traceback goes to the log; the user gets the message.
            log.exception("worker failed: %s", name)
            try:
                self.signals.failed.emit(f"{type(e).__name__}: {e}")
            except RuntimeError:          # the window it reports to is gone
                pass
            return
        log.info("worker done: %s", name)
        try:
            self.signals.finished.emit(result)
        except RuntimeError:              # the window it reports to is gone
            pass
