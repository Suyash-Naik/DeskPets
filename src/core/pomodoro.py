"""Pomodoro timer: a monotonic deadline plus a display ticker.

The deadline is a ``time.monotonic()`` value, so a stalled event loop or a slow tick cannot make
the timer drift; the 1 s ticker exists only to refresh the countdown and to notice expiry.
``finished`` fires exactly once per run. The timer is not persisted across restarts.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .. import config

log = logging.getLogger(__name__)


class PomodoroTimer(QObject):
    """One countdown. **GUI thread only.**

    Signals: ``state_changed(bool)`` on start/stop/finish, ``ticked(int)`` with whole seconds
    remaining (also emitted once on start), ``finished()`` when the deadline passes.
    """

    state_changed = pyqtSignal(bool)
    ticked = pyqtSignal(int)
    finished = pyqtSignal()

    def __init__(self, parent: QObject | None = None, *,
                 duration_ms: int | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(parent)
        self._duration_s = (config.POMODORO_WORK_MS if duration_ms is None else duration_ms) / 1000
        self._clock = clock
        self._deadline: float | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(config.POMODORO_TICK_MS)
        self._timer.timeout.connect(self.poll)

    @property
    def running(self) -> bool:
        """Whether a countdown is active. GUI thread."""
        return self._deadline is not None

    def remaining_s(self) -> int:
        """Whole seconds left (rounded up); 0 when idle. GUI thread."""
        if self._deadline is None:
            return 0
        return max(0, math.ceil(self._deadline - self._clock()))

    def start(self) -> None:
        """Begin a fresh countdown; restarts if already running. GUI thread."""
        was_running = self.running
        self._deadline = self._clock() + self._duration_s
        self._timer.start()
        if not was_running:
            self.state_changed.emit(True)
        self.ticked.emit(self.remaining_s())

    def stop(self) -> None:
        """Cancel without firing ``finished``. No-op when idle. GUI thread."""
        if self._deadline is None:
            return
        self._deadline = None
        self._timer.stop()
        self.state_changed.emit(False)

    def toggle(self) -> None:
        """Start if idle, stop if running. GUI thread."""
        if self.running:
            self.stop()
        else:
            self.start()

    def poll(self) -> None:
        """Check the deadline. Driven by the ticker; public so tests can drive it. GUI thread."""
        if self._deadline is None:
            return
        if self._clock() >= self._deadline:
            self._deadline = None
            self._timer.stop()
            self.state_changed.emit(False)
            self.ticked.emit(0)
            log.info("pomodoro finished")
            self.finished.emit()
        else:
            self.ticked.emit(self.remaining_s())
