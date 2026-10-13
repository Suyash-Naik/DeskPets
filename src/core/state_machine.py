"""Maps a :class:`~src.core.telemetry.Metrics` sample onto one of the atlas's animation states.

Design in one paragraph: every numeric gate is a Schmitt band rather than a bare comparison, so
a metric hovering on a threshold cannot flap the pet; a dwell timer stops two *different* rules
from winning on alternate ticks; and the ladder ends in an unconditional default so there is
always exactly one answer. ``evaluate()`` takes ``now`` as an argument, which is what lets the
whole thing be tested in microseconds instead of waiting out real minutes of idle time.

Thread model
------------
Everything here runs on the **GUI thread**. ``on_metrics`` is a queued slot fed by
``TelemetryService.metrics_ready``; no psutil call and no widget access happens in this module.

Purity of ``evaluate``
----------------------
``evaluate(m, now)`` performs no I/O, reads no clock, emits no signal and does not commit the
result -- feed it a scripted ``(Metrics, now)`` sequence and it is fully deterministic (modulo
the one-shot variation jitter, whose RNG lives on the instance). It *does* advance the
machine's own Schmitt latches and edge detectors, because a hysteresis trigger is stateful by
definition: that bookkeeping is the sample-to-sample memory, not a side effect on the world.
``on_metrics`` is the thin impure wrapper that supplies the clock, commits the answer and emits.

Availability
------------
``STATES`` is what the machine *can* name; the atlas decides what it *may* emit. ``main.py``
calls :meth:`StateMachine.set_available_states` with the rows the loaded atlas actually has,
and every route to a state name -- the ladder, the flourish pools and ``trigger_state`` --
filters on it. This is what lets a state ship before its art does: the rule is live, inert, and
starts working by itself the day the row lands, with no code change. Without the filter an
absent row reaches the animator, which logs a warning and holds the previous sprite while the
stats card happily shows the new state's copy over it.

The second input
----------------
``working-together`` is not driven by the 1500 ms ``Metrics`` payload. Its signal arrives on
:meth:`on_input_activity_changed`, a latched boolean the telemetry worker emits the moment its
own 250 ms sub-sampler decides interaction has started or stopped (see ``telemetry.py``).
Riding the poll would have added up to 1.5 s to a transition the user is watching for.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from src import config
from src.core.telemetry import Metrics

log = logging.getLogger(__name__)


STATES: Final[tuple[str, ...]] = (
    "idle-attentive",
    "running-playful-roll",
    "running-playful-jump",
    "waiting-deep-sleep",
    "waiting-dozy",
    "blocked-grumpy",
    "needs-input-sad",
    "running-busy-play",
    "ready-pleased",
    "working-together",
)

DEFAULT_STATE: Final[str] = "idle-attentive"

#: The state the sustained-interaction signal drives. Named once so the several places that
#: special-case it (the ladder, the dwell exemption) cannot drift apart.
WORKING_STATE: Final[str] = "working-together"

#: States the app runs fine without. An atlas missing one of these is not an error: the rule
#: that would emit it is skipped and the pet simply never shows it. Everything *not* listed
#: here is load-bearing, and :meth:`SpriteSheet.require` still fails loudly at startup for it --
#: a silent ``play()`` warning the first time the CPU spikes is much worse than a clear refusal
#: to start.
OPTIONAL_STATES: Final[frozenset[str]] = frozenset({WORKING_STATE})

#: What ``main.py`` hands to ``SpriteSheet.require()``. Derived, so adding an optional state is
#: one edit above rather than two lists to keep in sync.
REQUIRED_STATES: Final[tuple[str, ...]] = tuple(s for s in STATES if s not in OPTIONAL_STATES)

#: Lower number == more urgent. Used *only* to decide whether a pending transition may skip the
#: dwell timer (``config.DWELL_BYPASS_ON_ESCALATION``); the ladder order below is separate.
#:
#: The sleeping rows sit at the *bottom*, below ``idle-attentive``, on purpose. Dwell exists to
#: stop animation churn, but waking up is a responsiveness requirement: the instant the user
#: touches the keyboard, idle_seconds drops to ~0 and the pet must come awake on that very
#: sample rather than 4 s later. Ranking the sleep states lowest makes every *exit* from sleep
#: an escalation (bypassing dwell) while every *entry* still has to wait dwell out -- and an
#: entry has already waited five minutes, so the delay costs nothing.
#:
#: ``working-together`` is ranked immediately above ``idle-attentive`` and below every load
#: rule. Above idle so that sitting down at the machine is an *escalation* and enters without
#: waiting dwell out -- which is the whole point of a state that is supposed to appear when you
#: start working. Below the load rules because the user chose it that way: what the machine is
#: doing outranks what the human is doing, so a build at 95 % CPU still gets its own animation.
PRIORITY: Final[dict[str, int]] = {
    "blocked-grumpy": 0,
    "needs-input-sad": 1,
    "running-playful-jump": 2,
    "ready-pleased": 3,
    "running-busy-play": 4,
    "running-playful-roll": 5,
    "working-together": 6,
    "idle-attentive": 7,
    "waiting-dozy": 8,
    "waiting-deep-sleep": 9,
}


# =============================================================================
# HYSTERESIS
# =============================================================================


@dataclass(frozen=True, slots=True)
class Band:
    """A Schmitt trigger: cross ``enter`` to latch on, fall below ``exit`` to latch off.

    Pure and Qt-free; callable from any thread.
    """

    enter: float
    exit: float

    def holds(self, value: float | None, latched: bool) -> bool:
        """Whether the band holds for ``value``, given whether it was latched last sample."""
        # The one line that makes the absent Windows temperature safe: an unavailable metric
        # never satisfies a gate, so it can never trigger a state (contract 4.4).
        if value is None:
            return False
        return value >= (self.exit if latched else self.enter)

    @classmethod
    def of(cls, pair: tuple[float, float]) -> "Band":
        """Build from a ``config`` ``(enter, exit)`` tuple."""
        return cls(float(pair[0]), float(pair[1]))


BAND_CPU_HIGH: Final[Band] = Band.of(config.BAND_CPU_HIGH)
BAND_RAM_HIGH: Final[Band] = Band.of(config.BAND_RAM_HIGH)
BAND_CPU_MODERATE: Final[Band] = Band.of(config.BAND_CPU_MODERATE)
BAND_SWAP_HIGH: Final[Band] = Band.of(config.BAND_SWAP_HIGH)
BAND_TEMP_HIGH: Final[Band] = Band.of(config.BAND_TEMP_HIGH)


@dataclass(frozen=True, slots=True)
class _Gates:
    """The five band verdicts for one sample, computed before the ladder runs.

    Computed up front (never lazily inside the ladder) so that every band's latch advances on
    every sample. A band skipped by a short-circuiting ladder would keep a stale latch and then
    fire on the wrong side of its hysteresis when the ladder next reached it.
    """

    temp_high: bool
    swap_high: bool
    cpu_high: bool
    ram_high: bool
    cpu_moderate: bool


@dataclass(frozen=True, slots=True)
class Flourish:
    """A brief animation that interrupts a resident state, then hands it back."""

    state: str
    hold_ms: int
    weight: float


@dataclass(frozen=True, slots=True)
class VariationSpec:
    """How often a resident state is interrupted by a flourish, and which ones it may draw.

    A flourish is always a *timed override*, never a state the pet resides in. That is a hard
    requirement for row 7, which is ``loop: false`` and ends on a deliberate tail-only pose
    (contract 2.1), and it is the right shape for the looping rows too -- they are punctuation.
    """

    interval_ms: int
    jitter_ms: int
    #: Whether leaving the host state restarts the countdown. True for short cadences, where
    #: re-arming feels responsive. **False for long ones**: on a five-minute timer a transient
    #: CPU blip would restart the wait, and on a normal desktop that happens often enough to
    #: starve the flourish completely -- the user would simply never see it.
    rearm_on_reentry: bool
    pool: tuple[Flourish, ...]


def _pool(entries: tuple[tuple[str, int, float], ...]) -> tuple[Flourish, ...]:
    return tuple(Flourish(state, hold, weight) for state, hold, weight in entries)


#: Resident states that periodically play a flourish, how often, and from which pool.
VARIATION_HOSTS: Final[dict[str, VariationSpec]] = {
    "running-playful-roll": VariationSpec(
        config.MODERATE_VARIATION_INTERVAL_MS,
        config.MODERATE_VARIATION_JITTER_MS,
        rearm_on_reentry=True,
        pool=_pool(config.MODERATE_FLOURISHES),
    ),
    "idle-attentive": VariationSpec(
        config.IDLE_VARIATION_INTERVAL_MS,
        config.IDLE_VARIATION_JITTER_MS,
        rearm_on_reentry=False,
        pool=_pool(config.IDLE_FLOURISHES),
    ),
}


def _hottest(m: Metrics) -> float | None:
    """Highest known die temperature in the sample, or ``None`` if nothing reports one.

    CLAUDE.md's row 5 trigger is "CPU/GPU temp > 80 C", so either source may raise the alarm.

    CPU temperature stays ``None`` on Windows (``psutil.sensors_temperatures`` is Linux/BSD
    only). GPU temperature is live wherever NVML is -- ``nvidia-ml-py`` is in the ``pets``
    environment -- so on an NVIDIA machine the thermal half of ``blocked-grumpy`` genuinely
    fires. On a machine with no NVIDIA GPU both are ``None``, the band is inert by way of
    ``Band.holds(None, ...) -> False``, and high swap is the only working trigger.
    """
    candidates = [t for t in (m.cpu_temp_c, m.gpu.temp_c if m.gpu else None) if t is not None]
    return max(candidates) if candidates else None


# =============================================================================
# STATE MACHINE
# =============================================================================


class StateMachine(QObject):
    """Turns telemetry into an animation state name. **GUI thread only.**

    Emits ``state_changed`` on an actual change and never otherwise, so the animator is not
    asked to restart the same row every 1.5 s.
    """

    state_changed = pyqtSignal(str)  # the new state name

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state: str = DEFAULT_STATE
        #: ``None`` until the first committed change. It must NOT be seeded from
        #: ``time.monotonic()`` here: ``evaluate()`` takes an *injected* ``now``, so a caller on
        #: its own timeline (every test, and any future replay tool) would be comparing two
        #: unrelated clock domains and get an age of minus several days -- which reads as "still
        #: inside dwell" and silently pins the machine to its initial state forever.
        self._entered_at: float | None = None
        #: True when the current state was installed by a timed override. Such a state carries
        #: its own timer, so entering *and leaving* it must bypass dwell -- otherwise a
        #: ``running-busy-play`` override (1160 ms) would be pinned by the 4000 ms dwell and the
        #: pet would sit on that row's deliberately near-empty final frame (contract 2.1).
        self._state_from_override: bool = False
        #: Whether the last ``evaluate()`` answer came out of the override slot. Carried so that
        #: ``on_metrics`` can commit without re-deriving it (``_active_override`` mutates).
        self._last_from_override: bool = False

        # Timed override slot -- shared by ready-pleased, running-busy-play and trigger_state.
        # One slot, so a manual poke and an automatic flourish can never both be "active".
        self._override_state: str | None = None
        self._override_until: float = 0.0

        # Band latches, keyed by gate name.
        self._latched: dict[str, bool] = {
            "temp_high": False, "swap_high": False,
            "cpu_high": False, "ram_high": False, "cpu_moderate": False,
        }

        # ready-pleased edge detection: "a CPU spike drops back to idle" is a transition, not a
        # level, so it needs an arm/fire latch rather than a threshold.
        self._spike_since: float | None = None
        self._ready_armed: bool = False
        self._calm_samples: int = 0

        # rows 1 vs 7 tiebreak: a periodic one-shot flourish while resident in a host state.
        # Keyed by host state so the calm and busy cadences run independently -- dropping from
        # moderate back to idle must not restart the idle countdown.
        self._rng = random.Random()
        self._next_variation_at: dict[str, float] = {}

        #: The ladder's most recent answer, i.e. the state the pet would be in with no override
        #: active. Cached so an expiring flourish can hand control back without re-evaluating.
        self._last_natural: str = DEFAULT_STATE

        #: Whether the telemetry worker's sub-sampler currently says the user is interacting
        #: with the machine. Latched there, not here -- all the hysteresis lives on the worker
        #: so this is a plain boolean the ladder reads. False until told otherwise, so a build
        #: without the seam behaves exactly as it did before this state existed.
        self._input_active: bool = False

        #: Rows the loaded atlas actually has. Defaults to every name, so a bare
        #: ``StateMachine()`` (every test, every tool) behaves as it always did; ``main.py``
        #: narrows it to ``sheet.names()`` once the atlas is loaded.
        self._available: frozenset[str] = frozenset(STATES)

        #: The last sample and the gate verdicts it produced. Kept so an input-activity edge can
        #: re-run the ladder *without* re-running ``evaluate()`` -- see ``_reconsider``.
        self._last_metrics: Metrics | None = None
        self._last_gates: _Gates | None = None

        #: Fires when a timed override ends, so a flourish shorter than the poll interval does
        #: not overstay until the next telemetry sample. Single-shot, restarted per override.
        self._expiry_timer = QTimer(self)
        self._expiry_timer.setSingleShot(True)
        self._expiry_timer.timeout.connect(self._on_override_expired)

    # -- public surface -------------------------------------------------------------------

    @property
    def state(self) -> str:
        """The state currently shown. **GUI thread.**"""
        return self._state

    def set_available_states(self, names: Sequence[str]) -> None:
        """Restrict what the machine may emit to the rows the atlas actually has. **GUI thread.**

        Called once from ``main.py`` with ``sheet.names()``, before telemetry starts. A name in
        ``STATES`` but not in ``names`` becomes unreachable by every route: the ladder skips its
        rule, the flourish pools drop it, and ``trigger_state`` refuses it.

        ``DEFAULT_STATE`` missing would leave the ladder with no terminal answer, so it is
        forced back in and logged rather than allowed to produce an unplayable default. In
        practice it cannot happen -- ``REQUIRED_STATES`` covers it and ``require()`` has already
        thrown by this point.
        """
        available = frozenset(names)
        missing = [s for s in STATES if s not in available]
        if DEFAULT_STATE not in available:
            log.error("atlas has no %r row; forcing it available so the ladder has a terminal "
                      "answer -- the animator will warn on every frame", DEFAULT_STATE)
            available = available | {DEFAULT_STATE}
        self._available = available
        if missing:
            log.info("states unavailable in this atlas and therefore never emitted: %s",
                     ", ".join(sorted(missing)))

    def is_available(self, state: str) -> bool:
        """Whether ``state`` has a row in the loaded atlas. Any thread (read-only)."""
        return state in self._available

    @pyqtSlot(bool)
    def on_input_activity_changed(self, active: bool) -> None:
        """Latched "the user is interacting with the machine" edge. **GUI thread** (queued).

        Emitted by the telemetry worker's sub-sampler rather than carried on the 1500 ms
        ``Metrics`` payload, so entering and leaving ``working-together`` is prompt: riding the
        poll would have added up to a further 1.5 s to a transition the user is watching for.

        Re-decides immediately instead of waiting for the next sample -- via ``_reconsider``,
        which deliberately does **not** call ``evaluate()``; see that method.
        """
        active = bool(active)
        if active == self._input_active:
            return
        self._input_active = active
        log.debug("input activity -> %s", "active" if active else "idle")
        self._reconsider(time.monotonic())

    @pyqtSlot(object)
    def on_metrics(self, metrics: object) -> None:
        """Evaluate a fresh sample and emit on change. **GUI thread** (queued from telemetry).

        The thin impure wrapper around :meth:`evaluate`: it supplies the clock, commits the
        result and emits. All the logic lives in ``evaluate``.
        """
        if not isinstance(metrics, Metrics):
            log.error("StateMachine got %r, not Metrics; ignoring", type(metrics).__name__)
            return
        now = time.monotonic()
        state = self.evaluate(metrics, now)
        self._commit(state, now, from_override=self._last_from_override)

    @pyqtSlot(str, int)
    def trigger_state(self, state: str, duration_ms: int = 5000) -> None:
        """Force a state for ``duration_ms``, then let the ladder take over. **GUI thread.**

        The only route to ``needs-input-sad``, which has no automatic trigger, and the target of
        the tray's "Poke the pets" submenu. A manual poke is explicit user intent, so it takes
        effect on this call rather than on the next telemetry tick and ignores dwell.
        """
        if state not in STATES:
            # Logged and ignored, never raised: a typo in a menu wiring must not kill the app.
            log.warning("trigger_state(%r) ignored: not one of %s", state, ", ".join(STATES))
            return
        if state not in self._available:
            # The same refusal for a real name whose art has not landed. Without it the tray's
            # "Poke the pets" submenu is a one-click route to the exact failure the availability
            # filter exists to prevent: the animator warns and holds the previous sprite while
            # the card shows the new state's copy over it.
            log.warning("trigger_state(%r) ignored: no such row in the loaded atlas", state)
            return
        now = time.monotonic()
        self._install_override(state, max(1, int(duration_ms)), now)
        self._last_from_override = True
        self._commit(state, now, from_override=True)

    def evaluate(self, m: Metrics, now: float) -> str:
        """Decide which state ``m`` calls for at time ``now``. **Pure** -- see module docstring.

        Rule ladder, first match wins:
        timed override -> blocked-grumpy -> running-playful-jump -> waiting-deep-sleep ->
        waiting-dozy -> running-playful-roll -> working-together -> idle-attentive.

        A rule whose state is not in the loaded atlas is skipped, so the shape of the ladder
        depends on the art that shipped -- see the module docstring on availability.

        Callable from any thread in principle (it touches no Qt), but in practice only the GUI
        thread has a machine to call it on.
        """
        gates = self._advance_gates(m)
        self._advance_ready_latch(m, now)
        # Cached for _reconsider, which re-runs the ladder on an input-activity edge and must
        # not advance these latches a second time for one real sample.
        self._last_metrics = m
        self._last_gates = gates

        natural = self._ladder(gates, m)
        # Cached so _on_override_expired can hand the pet back without re-running evaluate()
        # (which would double-advance the latches -- see that method).
        self._last_natural = natural

        # rows 1 vs 7: running-playful-roll is the resident moderate state; running-busy-play is
        # only ever a timed override, because row 7 is loop=False and residing in it would
        # freeze the pet on a tail-only final pose for minutes (contract 2.1). The same stroll
        # also plays on a slower cadence while the pet is calm, so an idle desktop is not one
        # animation on loop forever.
        if natural in VARIATION_HOSTS:
            if self._active_override(now) is None and self._variation_due(natural, now):
                flourish = self._pick_flourish(natural)
                if flourish is not None:
                    self._install_override(flourish.state, flourish.hold_ms, now)
                self._schedule_variation(natural, now)
        for host, spec in VARIATION_HOSTS.items():
            if spec.rearm_on_reentry and host != natural:
                self._next_variation_at.pop(host, None)

        override = self._active_override(now)
        candidate = override if override is not None else natural
        self._last_from_override = override is not None
        return self._apply_dwell(candidate, now, from_override=self._last_from_override)

    # -- ladder ---------------------------------------------------------------------------

    def _advance_gates(self, m: Metrics) -> _Gates:
        """Advance every Schmitt latch for this sample and return the verdicts."""
        return _Gates(
            temp_high=self._gate("temp_high", BAND_TEMP_HIGH, _hottest(m)),
            swap_high=self._gate("swap_high", BAND_SWAP_HIGH, m.swap_percent),
            cpu_high=self._gate("cpu_high", BAND_CPU_HIGH, m.cpu_percent),
            ram_high=self._gate("ram_high", BAND_RAM_HIGH, m.ram_percent),
            cpu_moderate=self._gate("cpu_moderate", BAND_CPU_MODERATE, m.cpu_percent),
        )

    def _gate(self, key: str, band: Band, value: float | None) -> bool:
        held = band.holds(value, self._latched[key])
        self._latched[key] = held
        return held

    def _ladder(self, g: _Gates, m: Metrics) -> str:
        """The rule ladder minus the override slot. First match wins."""
        if g.temp_high or g.swap_high:
            return "blocked-grumpy"
        if g.cpu_high or g.ram_high:
            return "running-playful-jump"

        # rows 3 vs 4 tiebreak: dozy at 5 min, deep sleep at 15 min. Both additionally gated on
        # the machine being quiet -- a box at 90% while the user is away is not a sleeping pet.
        # idle_seconds is None when the platform seam cannot supply it; that must never be read
        # as 0.0, so the explicit None check comes first.
        idle = m.idle_seconds
        if idle is not None and not g.cpu_moderate:
            if idle >= config.IDLE_DEEP_SLEEP_S:
                return "waiting-deep-sleep"
            if idle >= config.IDLE_DOZY_S:
                return "waiting-dozy"

        if g.cpu_moderate:
            return "running-playful-roll"

        # Sustained interaction, below every load rule and above the calm default -- it fills
        # the gap where the pet would otherwise sit in idle-attentive while the user is very
        # obviously there. `_input_active` is a latch owned by the telemetry sub-sampler; this
        # is a plain read of it, with no threshold of its own.
        #
        # The availability test is what lets this rule ship before the art does: with no
        # `working-together` row in the atlas the rule is skipped entirely and the ladder falls
        # through to the default exactly as it did before, and the day the row lands it starts
        # working with no code change.
        if self._input_active and WORKING_STATE in self._available:
            return WORKING_STATE

        # Unconditional terminal default, and deliberately NOT gated on RAM. Measured on the dev
        # machine: RAM idles at ~70%, so BuildPlan's literal "CPU < 20% AND RAM < 60%" would make
        # the default state unreachable and the pet would never be calm. config.RAM_BUSY_HINT
        # carries that 60% number for the stats card's subtitle copy instead.
        return DEFAULT_STATE

    # -- ready-pleased edge detection -----------------------------------------------------

    def _advance_ready_latch(self, m: Metrics, now: float) -> None:
        """Arm on a sustained spike, fire on a sustained return to calm. Installs an override.

        Arming survives a mid-spike dip, because a build that pauses between two steps has not
        finished; firing needs ``READY_CALM_SAMPLES`` *consecutive* calm samples, so a brief
        trough between steps cannot claim the credit either.
        """
        cpu = m.cpu_percent

        if cpu >= config.READY_SPIKE_CPU:
            if self._spike_since is None:
                self._spike_since = now
            elif not self._ready_armed and (now - self._spike_since) >= config.READY_MIN_SPIKE_S:
                self._ready_armed = True
                log.debug("ready-pleased armed after %.1fs above %.0f%% CPU",
                          now - self._spike_since, config.READY_SPIKE_CPU)
        else:
            # The spike run is broken, but the arming is not: a 3 s dip mid-build must not force
            # the user to wait out another READY_MIN_SPIKE_S before the pet can look pleased.
            self._spike_since = None

        if cpu < config.READY_CALM_CPU:
            self._calm_samples += 1
        else:
            self._calm_samples = 0

        if self._ready_armed and self._calm_samples >= config.READY_CALM_SAMPLES:
            self._ready_armed = False
            self._calm_samples = 0
            self._spike_since = None
            self._install_override("ready-pleased", config.READY_PLEASED_HOLD_MS, now)
            log.debug("ready-pleased fired")

    # -- timed override slot --------------------------------------------------------------

    def _install_override(self, state: str, duration_ms: int, now: float) -> None:
        self._override_state = state
        self._override_until = now + duration_ms / 1000.0
        # Without this the slot is only noticed as expired on the NEXT telemetry sample, so a
        # flourish shorter than the poll interval overstays. The stroll is the visible case:
        # 1160 ms of animation followed by up to 1500 ms parked on its tail-only final frame,
        # i.e. an apparently empty window for longer than the animation itself ran.
        self._expiry_timer.start(max(1, int(duration_ms)))

    @pyqtSlot()
    def _on_override_expired(self) -> None:
        """Hand the pet back to its resident state the moment a flourish is over.

        Commits the ladder's last answer rather than re-running ``evaluate()``: re-evaluating
        against stale metrics would advance the Schmitt latches and the ready-pleased sample
        counter a second time for one real sample. **GUI thread.**
        """
        now = time.monotonic()
        if self._active_override(now) is not None:
            # The slot still holds a live override. Two ways to get here, and they must NOT be
            # treated the same:
            #
            #  - a longer override superseded this one (``_install_override`` restarted the
            #    timer, so the new deadline is already armed), or
            #  - THIS timer fired a hair early. QTimer's default CoarseTimer is documented to
            #    fire within ~5% either side of its interval, and ``_override_until`` is read
            #    from ``time.monotonic()`` a moment before ``start()``, so the margin here is
            #    thin: measured slack at fire time ranged from +0.3 ms to +21 ms on an 1160 ms
            #    hold, and went negative in roughly one run in four under test load.
            #
            # Simply returning handles the first case and silently breaks the second: the timer
            # is SINGLE-SHOT, so a dropped hand-back is never retried and the pet stays on the
            # flourish until the next telemetry sample -- reintroducing exactly the up-to-1500 ms
            # poll quantisation this timer exists to remove, on a row whose final pose is
            # deliberately near-empty. Re-arming for whatever is left is correct for both cases:
            # if a longer override really did supersede this one, this re-arms to that same
            # deadline, which is what is already scheduled.
            remaining_ms = (self._override_until - now) * 1000.0
            self._expiry_timer.start(max(1, int(remaining_ms) + 1))
            return
        # from_override=True: leaving a flourish must bypass dwell, or the pet is stranded on
        # it for the remainder of MIN_STATE_DWELL_MS (contract 2.1).
        self._commit(self._last_natural, now, from_override=True)

    def _active_override(self, now: float) -> str | None:
        """The override state if one is still live, else ``None`` (clearing an expired slot)."""
        if self._override_state is None:
            return None
        if now >= self._override_until:
            self._override_state = None
            return None
        return self._override_state

    # -- periodic stroll ------------------------------------------------------------------

    def _variation_due(self, host: str, now: float) -> bool:
        """Whether ``host``'s stroll is due, arming its schedule on first sight."""
        due_at = self._next_variation_at.get(host)
        if due_at is None:
            self._schedule_variation(host, now)
            return False
        return now >= due_at

    def _schedule_variation(self, host: str, now: float) -> None:
        """Next flourish in interval + [0, jitter), so two pets never sync up."""
        spec = VARIATION_HOSTS[host]
        delay_ms = spec.interval_ms + self._rng.uniform(0.0, float(spec.jitter_ms))
        self._next_variation_at[host] = now + delay_ms / 1000.0

    def _pick_flourish(self, host: str) -> Flourish | None:
        """Weighted random pick from ``host``'s pool, skipping anything the atlas lacks.

        Two filters, for two different failures. ``f.state in STATES`` drops a typo: the pool is
        user-editable config and a misspelling there must not be able to take the pet down.
        ``f.state in self._available`` drops a real state whose row is not in this atlas, which
        is how an optional state stays inert until its art ships.
        """
        pool = [f for f in VARIATION_HOSTS[host].pool
                if f.state in STATES and f.state in self._available]
        if not pool:
            return None
        return self._rng.choices(pool, weights=[f.weight for f in pool], k=1)[0]

    # -- dwell ----------------------------------------------------------------------------

    def _apply_dwell(self, candidate: str, now: float, *, from_override: bool) -> str:
        """Suppress a too-soon lateral or downward move; never delay an escalation."""
        if candidate == self._state:
            return candidate

        # An override carries its own timer; that timer *is* its dwell. Applying the 4 s dwell
        # on top would both delay entering a short flourish and strand the pet on it afterwards.
        if from_override or self._state_from_override:
            return candidate

        if self._entered_at is None:
            return candidate  # first move of the session; nothing to dwell on yet

        age_ms = (now - self._entered_at) * 1000.0
        if age_ms >= config.MIN_STATE_DWELL_MS:
            return candidate
        if config.DWELL_BYPASS_ON_ESCALATION and PRIORITY[candidate] < PRIORITY[self._state]:
            return candidate  # an alarm is never made to wait

        # Leaving working-together is the one de-escalation that does not wait. Its exit is
        # already gated on config.INPUT_IDLE_GRACE_MS of total silence, which serves dwell's
        # purpose (it is longer than any row in the atlas, so no animation is cut short) -- so
        # adding dwell's 4 s on top only strands the pet at its desk for ~6 s after the user
        # has walked away. See config.DWELL_BYPASS_ON_WORKING_EXIT for the arithmetic.
        if config.DWELL_BYPASS_ON_WORKING_EXIT and self._state == WORKING_STATE:
            return candidate
        return self._state

    # -- out-of-band re-decision ----------------------------------------------------------

    def _reconsider(self, now: float) -> None:
        """Re-run the ladder against the cached sample and commit. **GUI thread.**

        The path for an input that is not a telemetry sample. It deliberately does **not** call
        :meth:`evaluate`: that would advance every Schmitt latch and the ready-pleased sample
        counter a second time for one real sample, which is the same trap
        :meth:`_on_override_expired` documents. Only ``_ladder`` is re-run, from the cached
        gate verdicts, so the machine's memory advances exactly once per ``Metrics``.

        Flourish scheduling is skipped for the same reason: an input edge is not a tick, and a
        stroll must not be able to fire more often just because the user touched the mouse.
        """
        if self._last_gates is None or self._last_metrics is None:
            return  # no sample yet; the flag is recorded and the first tick will use it

        natural = self._ladder(self._last_gates, self._last_metrics)
        self._last_natural = natural
        override = self._active_override(now)
        candidate = override if override is not None else natural
        from_override = override is not None
        self._last_from_override = from_override
        state = self._apply_dwell(candidate, now, from_override=from_override)
        self._commit(state, now, from_override=from_override)

    # -- commit ---------------------------------------------------------------------------

    def _commit(self, state: str, now: float, *, from_override: bool | None = None) -> None:
        """Adopt ``state`` and emit if it actually changed. **GUI thread.**"""
        if from_override is None:
            from_override = self._last_from_override
        if state == self._state:
            self._state_from_override = bool(from_override)
            return
        previous = self._state
        self._state = state
        self._entered_at = now
        self._state_from_override = bool(from_override)
        log.debug("state %s -> %s", previous, state)
        self.state_changed.emit(state)
