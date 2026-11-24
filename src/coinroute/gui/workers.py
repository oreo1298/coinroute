"""Run long jobs (import, CSV export) on a worker thread so the window keeps responding."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot


class Cancelled(Exception):
    """Raised inside the job's progress callback once Cancel was pressed."""


class Worker(QObject):
    """Runs `job(progress)` on its own QThread.

    The job calls `progress(step: str, n: int)`; that is relayed to the GUI
    thread as the `progress` signal, and raises Cancelled after `cancel()`.
    Exactly one of finished / failed / cancelled is emitted at the end.
    """

    progress = Signal(str, int)
    finished = Signal(object)
    failed = Signal(str, str)  # message, traceback
    cancelled = Signal()

    def __init__(self, job: Callable[[Callable[[str, int], None]], object]):
        super().__init__()
        self._job = job
        self._cancel = threading.Event()
        self.qthread: QThread | None = None

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def _progress(self, step: str, n: int) -> None:
        if self._cancel.is_set():
            raise Cancelled
        self.progress.emit(step, n)

    @Slot()
    def run(self) -> None:
        try:
            result = self._job(self._progress)
        except Cancelled:
            self.cancelled.emit()
        except Exception as e:  # report to the GUI; the thread must not die silently
            self.failed.emit(str(e) or type(e).__name__, traceback.format_exc())
        else:
            if self._cancel.is_set() and result is None:
                self.cancelled.emit()
            else:
                self.finished.emit(result)

    def start(self) -> None:
        """Start on a new thread; the thread quits by itself when the job ends."""
        t = QThread()
        self.qthread = t
        self.moveToThread(t)
        t.started.connect(self.run)
        # Direct: quit from the worker thread at once. A queued quit would wait for the GUI thread,
        # which may itself be blocked in wait() inside a slot of these same signals.
        for sig in (self.finished, self.failed, self.cancelled):
            sig.connect(t.quit, Qt.ConnectionType.DirectConnection)
        t.start()

    def running(self) -> bool:
        return self.qthread is not None and self.qthread.isRunning()

    def wait(self, ms: int = 30000) -> bool:
        return self.qthread.wait(ms) if self.qthread else True
