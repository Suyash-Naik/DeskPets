"""Frame player: walks one :class:`~src.core.sprite_loader.Animation` in real time.

**GUI thread only.** Every method here touches a ``QTimer`` and emits ``QPixmap``s straight into widget slots; calling any of it from the telemetry thread is a defect. ``StateMachine``
lives on the GUI thread too, so its ``state_changed`` -> :meth:`Animator.set_state` connection is a direct call, not a queued one.

No crossfading -- switching state is immediate and synchronous (contract 2.8).
"""

from __future__ import annotations

import logging
from typing import Final

from PyQt6.QtCore import QElapsedTimer, QObject, QPoint, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPixmap

from .. import config
from .sprite_loader import Animation, SpriteSheet

log = logging.getLogger(__name__)

#: How far past a frame's deadline the scheduler is still willing to compensate, in whole
#: frames. Inside this slack the next delay is simply shortened, which is what keeps a sequence
#: drift-free. Beyond it the schedule is abandoned and re-based on the clock, because the only
#: way to honour it would be to emit several frames back to back.
#:
#: Deliberately a module constant rather than a ``config`` entry: it is an implementation
#: detail of this scheduler, not something a user would tune. Everything that is tunable
#: (``MIN_FRAME_MS``, ``FRAME_DRIFT_COMPENSATION``, ``PRECISE_ANIMATION_TIMER``,
#: ``NONLOOP_END_POLICY``, ``PAUSE_WHEN_HIDDEN``) already lives in ``config``.
_RESYNC_SLIP_FRAMES: Final[int] = 1

_POLICY_HOLD: Final[str] = "hold"
_POLICY_FALLBACK: Final[str] = "fallback"


class Animator(QObject):
    """Plays atlas rows, one at a time, at the timings the atlas authored.

    **GUI thread only** -- construct it, call it and connect to it from the GUI thread.

    Signals
    -------
    ``frame_changed(QPixmap, QPoint)``
        The frame to draw and where to draw it inside the animation's box. Wired to
        ``PetWindow.set_frame``. Emitted on every frame *and* synchronously from :meth:`play`
        when the state changes, so the outgoing sprite never lingers.
    ``state_changed(str, str)``
        ``(previous, new)``. Emitted only on an actual change of animation, matching
        ``StateMachine.state_changed``'s convention; a restart of the same row is not a change.
    ``sequence_finished(str)``
        A ``loop: false`` row has shown its last frame for that frame's full duration. Emitted
        **before** the end policy is applied. ``StateMachine`` owns reversion (contract 2.1);
        this signal exists so a future owner can hook the end without the animator growing an
        opinion about what comes next.
    """

    frame_changed = pyqtSignal(QPixmap, QPoint)
    state_changed = pyqtSignal(str, str)
    sequence_finished = pyqtSignal(str)

    def __init__(self, sheet: SpriteSheet, parent: QObject | None = None) -> None:
        """Build a player over ``sheet``. Nothing plays until :meth:`play` or :meth:`start`.

        GUI thread only.
        """
        super().__init__(parent)
        self._sheet = sheet
        self._current: Animation | None = None
        self._index: int = 0
        self._paused: bool = False
        self._finished: bool = False          # a non-looping row is holding its last frame
        self._pending: str | None = None      # a switch deferred to the next loop end

        # Bumped on every applied switch, and captured across a signal emission to detect a
        # slot that re-entered play(). See _finish_sequence.
        self._generation: int = 0

        self._clock = QElapsedTimer()
        self._clock.start()
        self._deadline_ms: int = 0            # absolute, measured on _clock

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)       # re-armed per frame; never a repeating tick
        self._timer.setTimerType(
            Qt.TimerType.PreciseTimer if config.PRECISE_ANIMATION_TIMER
            else Qt.TimerType.CoarseTimer)
        self._timer.timeout.connect(self._on_timeout)

        if config.NONLOOP_END_POLICY not in (_POLICY_HOLD, _POLICY_FALLBACK):
            log.warning("NONLOOP_END_POLICY %r is neither %r nor %r; holding the last frame",
                        config.NONLOOP_END_POLICY, _POLICY_HOLD, _POLICY_FALLBACK)

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def play(self, name: str, *, restart: bool = False,
             defer_to_loop_end: bool = False) -> bool:
        """Switch to animation ``name``. Returns ``True`` if the request was accepted.

        **Never raises.** An unknown name logs a warning and returns ``False``: a telemetry
        mapper emitting a state this atlas lacks must not take the pet down with it, and
        whatever is on screen keeps playing.

        ``name == current`` with ``restart`` False is a no-op that returns ``True``. That case
        is the common one -- telemetry re-asserts the same state every poll -- and restarting
        on each tick would reset the pet to frame 0 mid-pounce, forever.

        ``restart=True`` forces the sequence back to frame 0 even when it is already current.

        ``defer_to_loop_end=True`` stashes the request and applies it when the current
        animation wraps (or when a non-looping one ends), so a jump is not cut in half. It is
        applied immediately when nothing is playing, when the timer is not running, or when a
        non-looping row is already holding -- in those cases the wrap would never come. Only
        one request is stashed; a later one replaces it.

        GUI thread only.
        """
        animation = self._sheet.get(name)
        if animation is None:
            log.warning("play(%r): no such animation in this atlas; known: %s",
                        name, self._sheet.names())
            return False
        if not animation.frames:  # pragma: no cover - the loader blocks an empty row
            log.warning("play(%r): animation has no frames", name)
            return False

        if self._current is not None and self._current.name == name and not restart:
            return True

        if (defer_to_loop_end and self._current is not None
                and not self._finished and self._timer.isActive()):
            self._pending = name
            log.debug("play(%r) deferred to the end of %r", name, self._current.name)
            return True

        self._apply(animation)
        return True

    @pyqtSlot(str)
    def set_state(self, name: str) -> None:
        """Slot for ``StateMachine.state_changed``. A thin wrapper over :meth:`play`.

        GUI thread only (the state machine lives there, so the connection is direct).
        """
        self.play(name)

    def current_state(self) -> str:
        """Name of the animation on screen, or ``""`` before the first :meth:`play`."""
        return self._current.name if self._current is not None else ""

    def current_index(self) -> int:
        """Index of the frame last emitted. ``0`` before the first :meth:`play`."""
        return self._index

    def is_playing(self) -> bool:
        """True while the frame timer is armed.

        False when paused, stopped, holding the last frame of a non-looping row, or sitting on
        a single-frame looping row -- there is nothing to advance to.
        """
        return self._timer.isActive()

    def set_paused(self, paused: bool) -> None:
        """Freeze or resume playback. Wired to ``PetWindow.visibility_changed``.

        A hidden pet must burn no CPU, so pausing stops the timer outright. Resuming re-bases
        the deadline on *now* instead of replaying the time spent hidden, so the pet picks up
        from the frame it froze on rather than skipping ahead. Idempotent.

        Honours ``config.PAUSE_WHEN_HIDDEN``: when that is False the call is ignored, which
        keeps the visibility wiring in ``main.py`` unconditional.

        GUI thread only.
        """
        if not config.PAUSE_WHEN_HIDDEN:
            return
        if paused == self._paused:
            return
        self._paused = paused
        if paused:
            self._timer.stop()
            log.debug("paused on %s[%d]", self.current_state(), self._index)
            return
        if self._current is None or self._finished:
            return
        self._arm(self._current.frames[self._index].duration_ms, advance=False)

    def stop(self) -> None:
        """Halt playback, leaving the current frame on screen. Idempotent. GUI thread only."""
        self._timer.stop()

    def start(self) -> None:
        """Begin or resume playback; plays the atlas default if nothing has played yet.

        Does not clear a :meth:`set_paused` pause -- that is the visibility owner's state, and
        overriding it here would restart a hidden pet's timer. GUI thread only.
        """
        if self._current is None:
            self.play(self._sheet.default_animation)
            return
        if self._paused or self._finished or self._timer.isActive():
            return
        self._arm(self._current.frames[self._index].duration_ms, advance=False)

    # =========================================================================
    # INTERNALS
    # =========================================================================

    def _apply(self, animation: Animation) -> None:
        """Install ``animation`` at frame 0 and emit that frame **synchronously**.

        Waiting for the next timeout would leave the outgoing sprite on screen for the
        remainder of its frame -- up to 260 ms on the slowest row -- which reads as lag on a
        state change that is meant to feel instant.
        """
        previous = self.current_state()
        self._current = animation
        self._index = 0
        self._finished = False
        self._pending = None
        self._generation += 1

        frame = animation.frames[0]
        # frame_changed first, state_changed second, deliberately: a slot on state_changed may
        # call play() again, and if it does, *its* frame must be the last one emitted. Emitting
        # state_changed first would let our frame overwrite the re-entrant one.
        self.frame_changed.emit(frame.pixmap, frame.offset)
        if previous != animation.name:
            self.state_changed.emit(previous, animation.name)

        # A re-entrant play() from either slot has already installed and armed something else;
        # arming here would schedule this now-stale frame on top of it.
        if self._current is not animation:
            return
        if self._needs_timer(animation):
            self._arm(frame.duration_ms, advance=False)

    @staticmethod
    def _needs_timer(animation: Animation) -> bool:
        """False for a single-frame looping row: re-emitting one pixmap forever is pure waste.

        A single-frame *non*-looping row still gets a timer, because its end has to be reported
        through ``sequence_finished`` once its duration is up.
        """
        return len(animation.frames) > 1 or not animation.loop

    def _arm(self, duration_ms: int, *, advance: bool) -> None:
        """Re-arm the single-shot timer for ``duration_ms``.

        ``advance=True`` extends the existing absolute deadline (the drift-free path, used
        after every frame); ``advance=False`` re-bases it on the clock (a fresh state, a
        resume). ``config.FRAME_DRIFT_COMPENSATION`` False reverts to naive rescheduling, which
        is useful only for demonstrating the drift this exists to remove.
        """
        if self._paused:
            return
        # The loader already floors every duration, but this is the hot path and a zero here
        # would spin the event loop.
        duration = max(config.MIN_FRAME_MS, int(duration_ms))
        now = self._clock.elapsed()

        if advance and config.FRAME_DRIFT_COMPENSATION:
            self._deadline_ms += duration
            slip = now - self._deadline_ms
            if slip > duration * _RESYNC_SLIP_FRAMES:
                log.debug("%s is %d ms behind its schedule; re-syncing the deadline",
                          self.current_state(), slip)
                self._deadline_ms = now + duration
        else:
            self._deadline_ms = now + duration

        self._timer.start(max(0, self._deadline_ms - now))

    def _on_timeout(self) -> None:
        """One frame's time is up: advance, wrap, or end. GUI thread (the event loop)."""
        animation = self._current
        if animation is None or self._paused:  # pragma: no cover - defensive
            return

        if self._index >= len(animation.frames) - 1:
            # The end of a pass: the one point at which a deferred switch may land.
            pending = self._pending
            if pending is not None:
                self._pending = None
                deferred = self._sheet.get(pending)
                if deferred is not None:
                    self._apply(deferred)
                    return
                # Only reachable if the sheet changed under us; play() already vetted the name.
                log.warning("deferred animation %r is no longer in the atlas", pending)
            if not animation.loop:
                self._finish_sequence(animation)
                return
            next_index = 0
        else:
            next_index = self._index + 1

        self._index = next_index
        frame = animation.frames[next_index]
        self.frame_changed.emit(frame.pixmap, frame.offset)
        # A frame_changed slot may have switched state re-entrantly (a tray poke, say); if it
        # did, it owns the timer now.
        if self._current is not animation:
            return
        self._arm(frame.duration_ms, advance=True)

    def _finish_sequence(self, animation: Animation) -> None:
        """A ``loop: false`` row has run its course. Emit, then apply the end policy.

        ``config.NONLOOP_END_POLICY`` is ``"hold"`` and must stay that way: ``StateMachine`` is
        the single owner of state and reverts row 7 by letting its timed override expire. An
        animator that also reverted would race it and the pet would ping-pong (contract 2.1).
        The consequence -- a non-looping row freezing on its last frame if the state machine is
        bypassed -- is intended, not a bug to fix here.
        """
        self._finished = True
        self._timer.stop()

        generation = self._generation
        self.sequence_finished.emit(animation.name)
        if self._generation != generation:
            # A slot called play(): it has installed and armed something else already, and
            # applying the end policy on top of that would fight the new owner.
            return

        if config.NONLOOP_END_POLICY == _POLICY_FALLBACK:
            fallback = self._sheet.default_animation
            if fallback == animation.name:
                log.warning("NONLOOP_END_POLICY is %r but %r is the default animation; holding",
                            _POLICY_FALLBACK, animation.name)
                return
            target = self._sheet.get(fallback)
            if target is None:  # pragma: no cover - the loader guarantees the default exists
                log.warning("fallback animation %r is not in the atlas; holding", fallback)
                return
            self._apply(target)
            return

        log.debug("%s finished; holding frame %d", animation.name, self._index)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"<Animator {self.current_state()!r}[{self._index}] "
                f"playing={self.is_playing()} paused={self._paused} "
                f"finished={self._finished}>")
