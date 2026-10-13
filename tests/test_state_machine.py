"""`src/core/state_machine.py` -- metrics in, animation state out.

Almost everything here runs on an **injected clock**. `evaluate(m, now)` reads no clock, emits
no signal and commits nothing, so a fifteen-minute idle timeout or a six-hour flourish cadence
is a for-loop over floats rather than a six-hour test. That property is the single reason this
module is cheaply testable, and `test_regression_dwell_uses_only_the_injected_clock` exists to
keep it true.

The two things that would otherwise make this suite non-deterministic are both pinned:

* the flourish RNG, via the `make_machine` fixture (see `conftest.DEFAULT_RNG_SEED`);
* the clock, by never calling `time.monotonic()` in a timeline test.

`evaluate()` does not commit its own answer -- `on_metrics` is the impure wrapper that does --
so the `drive()` helper below commits explicitly, exactly as `on_metrics` would. Forgetting
that is how a trace comes out looking like the dwell timer is broken when it is not.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

import pytest

from src import config
from src.core.state_machine import (DEFAULT_STATE, OPTIONAL_STATES, PRIORITY, REQUIRED_STATES,
                                    STATES, WORKING_STATE, Band, StateMachine)

TICK = config.POLL_INTERVAL_MS / 1000.0


# --- helpers ---------------------------------------------------------------------------------


def drive(machine: StateMachine, samples: Sequence[object], *,
          t0: float = 1000.0, step: float = TICK) -> list[tuple[float, object, str]]:
    """Feed `samples` at `step`-second intervals, committing each answer as `on_metrics` does.

    Returns `(t, metrics, state)` per sample. `t0` is arbitrary and deliberately nowhere near
    `time.monotonic()` -- the machine must not care.
    """
    trace: list[tuple[float, object, str]] = []
    t = t0
    for metrics in samples:
        state = machine.evaluate(metrics, t)
        machine._commit(state, t, from_override=machine._last_from_override)
        trace.append((t, metrics, state))
        t += step
    return trace


def states(trace: Sequence[tuple[float, object, str]]) -> list[str]:
    return [state for _, _, state in trace]


def transitions(trace: Sequence[tuple[float, object, str]], *, start: str | None = None) -> int:
    """Number of times the state actually changed across the trace."""
    previous = start
    count = 0
    for state in states(trace):
        if previous is not None and state != previous:
            count += 1
        previous = state
    return count


def flourish_starts(trace: Sequence[tuple[float, object, str]], host: str,
                    *, only_cpu: float | None = None) -> list[float]:
    """Times at which a flourish *began*: `host` -> anything else.

    Counting transitions rather than samples is not a detail. A flourish drawn from the idle
    pool is 1160, 1500 or 4020 ms long, i.e. one to three 1500 ms samples, so counting samples
    turns "five flourishes" into any number between five and fifteen depending on the draw.
    `check_stroll.py` records getting this wrong and fixing it; the discipline is preserved.

    `only_cpu` restricts to samples driven by that CPU value, so a deliberate one-sample load
    blip is never miscounted as a flourish.
    """
    starts: list[float] = []
    previous = host
    for t, metrics, state in trace:
        if previous == host and state != host:
            if only_cpu is None or metrics.cpu_percent == only_cpu:
                starts.append(t)
        previous = state
    return starts


# =============================================================================================
# REGRESSIONS
# =============================================================================================


def test_regression_hysteresis_absorbs_an_oscillation_at_the_threshold(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """CPU 78 -> 82 -> 78 -> 82 produces exactly ONE transition, not four.

    PHASE1-LOG.md defect 7: the prototype compared bare values against a bare threshold, so a
    metric hovering on 80% flapped the pet every poll. Every gate is now a `Band(enter, exit)`
    Schmitt trigger -- enter at 80, stay until 70 -- so the second 78 does not fall out of the
    band and the fourth sample changes nothing.

    The timeline is kept under `MODERATE_VARIATION_INTERVAL_MS` (25 s) on purpose: a flourish
    interleaving would add transitions that have nothing to do with hysteresis.
    """
    machine = make_machine()
    # Settle into the resident moderate state first, so dwell is satisfied and the transition
    # being counted is the real one.
    drive(machine, [make_metrics(cpu=78.0)] * 4, t0=0.0, step=2.0)
    settled = machine.state
    assert settled == "running-playful-roll", settled

    trace = drive(machine, [make_metrics(cpu=cpu) for cpu in (78.0, 82.0, 78.0, 82.0)],
                  t0=8.0, step=2.0)
    assert transitions(trace, start=settled) == 1, states(trace)
    assert machine.state == "running-playful-jump"

    # And the counterfactual: a bare comparison would have toggled on two of the four samples.
    band = Band.of(config.BAND_CPU_HIGH)
    naive = [value >= band.enter for value in (78.0, 82.0, 78.0, 82.0)]
    assert naive == [False, True, False, True], "the oscillation really does cross the threshold"


def test_regression_idle_attentive_is_reachable_at_seventy_percent_ram(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """RAM at 70.5% with a quiet CPU still gives `idle-attentive`.

    `BuildPlan.md` gates the default state on `CPU < 20% AND RAM < 60%`. The dev machine idles
    at ~70.5% RAM, so that literal reading makes the *default state unreachable* -- the pet
    would never once be calm. `idle-attentive` is the unconditional terminal default instead,
    and `config.RAM_BUSY_HINT` carries the 60% figure for the card's subtitle copy only.

    Found by probing the machine rather than by reading the spec, which is why it is here.
    """
    machine = make_machine()
    trace = drive(machine, [make_metrics(cpu=5.0, ram=70.5)] * 3, t0=0.0, step=5.0)
    assert states(trace)[-1] == DEFAULT_STATE
    assert config.RAM_BUSY_HINT == 60.0, "the number survives as copy"
    assert config.RAM_HIGH == 85.0, "and the real RAM gate is far above idle"


def test_regression_dwell_uses_only_the_injected_clock(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """The dwell timer must not mix `time.monotonic()` with `evaluate()`'s injected `now`.

    PHASE1-LOG.md bug 4. `_entered_at` used to be seeded from `time.monotonic()` in
    `__init__`, while `evaluate()` takes `now` as an argument. Any caller on its own timeline
    -- every test here, and any future replay tool -- then computed a state age of *minus
    several days*, read that as "still inside dwell", and pinned the pet to `idle-attentive`
    forever. Invisible in production, because there both clocks agree; fatal to every test.

    Two assertions: the structural one (`_entered_at` is unset until the first real change)
    and the behavioural one (a timeline starting at t=0.0 transitions normally).
    """
    machine = make_machine()
    assert machine._entered_at is None, (
        "_entered_at was seeded at construction; a caller on an injected clock will compute a "
        "nonsensical state age")

    # t0=0.0 is roughly a fortnight behind time.monotonic() on a typical uptime.
    trace = drive(machine, [make_metrics(cpu=95.0, ram=50.0)] * 2, t0=0.0, step=TICK)
    assert states(trace)[-1] == "running-playful-jump"
    assert machine._entered_at == pytest.approx(TICK, abs=1e-9) or machine._entered_at == 0.0


def test_regression_cpu_blips_do_not_starve_the_idle_stroll(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """A transient load blip every four minutes must not reset the five-minute stroll timer.

    The idle flourish is on a 300 s +/- 30 s cadence. If leaving `idle-attentive` re-armed that
    countdown, then on a normal desktop -- where something briefly touches the CPU every couple
    of minutes -- the wait would restart before it ever elapsed and the stroll would simply
    never be seen. `VariationSpec.rearm_on_reentry=False` for the idle host is the fix; this is
    what it is for.
    """
    machine = make_machine()
    pattern: list[object] = []
    for _ in range(8):
        pattern.extend([make_metrics(cpu=5.0)] * int(4 * 60 / TICK))  # 4 min calm
        pattern.append(make_metrics(cpu=35.0))                        # one moderate sample
    trace = drive(machine, pattern)

    strolls = flourish_starts(trace, DEFAULT_STATE, only_cpu=5.0)
    assert len(strolls) >= 4, (
        f"only {len(strolls)} strolls in ~32 min of (interrupted) calm -- the blips starved "
        "the cadence, which is exactly what rearm_on_reentry=False exists to prevent")


def test_regression_an_absent_optional_row_is_unreachable_by_every_route(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """With no `working-together` row in the atlas, nothing may ever select it.

    Three routes, three filters, and all three have to hold or the animator logs a warning and
    holds the previous sprite while the stats card cheerfully shows the missing state's copy
    over it -- a wrong pet that looks deliberate.

    Availability is set explicitly here rather than taken from the real atlas: the whole point
    of an optional state is that its art lands one day, and a test keyed on today's atlas would
    start passing vacuously the moment it does.
    """
    available = [s for s in STATES if s != WORKING_STATE]
    machine = make_machine(available=available)
    machine.on_input_activity_changed(True)

    # 1. the ladder
    trace = drive(machine, [make_metrics(cpu=5.0, idle=0.1)] * 40)
    assert set(states(trace)) == {DEFAULT_STATE}, sorted(set(states(trace)))

    # 2. trigger_state -- the tray's "Poke the pets" submenu
    machine.trigger_state(WORKING_STATE, 5000)
    assert machine.state != WORKING_STATE

    # 3. the flourish pools, over three simulated hours of calm
    quiet = make_machine(available=available)
    long_trace = drive(quiet, [make_metrics(cpu=5.0)] * int(3 * 3600 / TICK))
    assert WORKING_STATE not in set(states(long_trace))


def test_regression_an_early_expiry_timer_re_arms_instead_of_dropping_the_handback(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """A flourish must still end on time when its `QTimer` fires a hair early.

    **Found by this suite, not by the original session.** `_on_override_expired` used to read
    `if self._active_override(now) is not None: return`, treating "the slot is still live" as
    "a longer override superseded me". But `QTimer`'s default `CoarseTimer` is documented to
    fire within ~5% either side of its interval, and `_override_until` is taken from
    `time.monotonic()` a moment *before* `start()` -- so the margin is thin. Measured inside a
    real `PetApp` on an 1160 ms hold, the slack at fire time ranged from **+0.3 ms** to +21 ms,
    and went negative in roughly one run in four under test load.

    Because the timer is single-shot, a dropped hand-back was never retried: the pet stayed on
    the flourish until the next telemetry sample. That is exactly the up-to-1500 ms poll
    quantisation the timer was added to remove, on row 7, whose final pose is deliberately
    near-empty -- so the visible symptom is an apparently blank pet window for longer than the
    animation itself ran. `test_integration.py` caught it as an intermittent failure; it is
    pinned deterministically here.

    Simulated rather than waited for: calling the slot while the override is still live *is* an
    early fire, and is the only way to test this without racing the very timer under test.
    """
    machine = make_machine()
    machine.on_metrics(make_metrics(cpu=5.0))
    natural = machine.state
    machine.trigger_state("running-busy-play", 1160)
    assert machine.state == "running-busy-play"

    # The early fire: the slot runs while `now` is still short of `_override_until`.
    machine._expiry_timer.stop()
    machine._on_override_expired()

    assert machine.state == "running-busy-play", "handing back early would cut the flourish short"
    assert machine._expiry_timer.isActive(), (
        "the expiry timer was not re-armed after an early fire -- the hand-back is now lost "
        "until the next telemetry poll")
    assert 0 < machine._expiry_timer.remainingTime() <= 1160

    # And once the deadline really has passed, the same slot hands back as normal.
    machine._override_until = time.monotonic() - 0.001
    machine._on_override_expired()
    assert machine.state == natural


def test_flourish_pools_respect_availability(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """The same availability filter, proved against a row that *is* in a pool.

    The test above cannot exercise `_pick_flourish`'s filter, because `working-together` is not
    in any pool. Removing `running-playful-roll` -- the heaviest-weighted idle flourish -- does.
    """
    available = [s for s in STATES if s != "running-playful-roll"]
    machine = make_machine(available=available)
    trace = drive(machine, [make_metrics(cpu=5.0)] * int(2 * 3600 / TICK))
    seen = set(states(trace))
    assert "running-playful-roll" not in seen, sorted(seen)
    assert seen - {DEFAULT_STATE}, "the remaining pool entries should still play"


# =============================================================================================
# The rule ladder
# =============================================================================================


def test_missing_metrics_never_trigger_a_state(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """`Band.holds(None, latched) -> False` -- the line that makes the absent temperature safe.

    `cpu_temp_c` is always `None` on Windows. If a missing metric read as 0, or worse kept a
    latch alive, `blocked-grumpy` would be either permanently off or permanently on. Both
    latched and unlatched are checked: the latched case is the one a naive implementation gets
    wrong.
    """
    band = Band.of(config.BAND_TEMP_HIGH)
    assert band.holds(None, False) is False
    assert band.holds(None, True) is False

    machine = make_machine()
    trace = drive(machine,
                  [make_metrics(cpu=cpu, temp=None) for cpu in (5.0, 50.0, 99.0, 5.0)] * 3,
                  t0=0.0, step=5.0)
    assert "blocked-grumpy" not in set(states(trace))


def test_high_swap_alone_does_trigger_blocked_grumpy(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """On hardware with no temperature source, swap is the only working row-5 trigger.

    Worth an explicit test because on the dev machine (RTX 3070, NVML live) the thermal half
    fires and the swap half is never exercised -- and the swap half is what every non-NVIDIA
    user gets.
    """
    machine = make_machine()
    trace = drive(machine, [make_metrics(cpu=5.0, swap=55.0)] * 2, t0=0.0, step=5.0)
    assert states(trace)[-1] == "blocked-grumpy"


def test_gpu_temperature_also_raises_the_thermal_alarm(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """`_hottest()` takes the max of CPU and GPU temperature, so either source may alarm.

    Driven from a synthetic `GpuSample` rather than real hardware, so it runs on a machine with
    no GPU at all.
    """
    from src.core.telemetry import GpuSample

    hot = GpuSample("synthetic", 40.0, 1, 2, 50.0, 92.0)
    machine = make_machine()
    trace = drive(machine, [make_metrics(cpu=5.0, gpu=hot)] * 2, t0=0.0, step=5.0)
    assert states(trace)[-1] == "blocked-grumpy"


def test_ram_above_its_gate_triggers_the_heavy_load_row(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """RAM >= 85% is a heavy-load trigger in its own right, independent of CPU."""
    machine = make_machine()
    trace = drive(machine, [make_metrics(cpu=5.0, ram=88.0)] * 2, t0=0.0, step=5.0)
    assert states(trace)[-1] == "running-playful-jump"


def test_sleep_states_split_by_depth_and_wake_instantly(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """Rows 3 vs 4 tiebreak: dozy at 5 min, deep sleep at 15 min, awake on the next sample.

    Waking is the interesting half. The state age when the user touches the keyboard is
    typically far inside `MIN_STATE_DWELL_MS`, so waking on that very sample is only possible
    because the sleep rows are ranked *below* `idle-attentive` in `PRIORITY`, making every exit
    from sleep an escalation that bypasses dwell.
    """
    machine = make_machine()
    seen: dict[int, str] = {}
    t = 0.0
    for idle in (0, 60, 299, 301, 600, 899, 901, 1200):
        t += 10.0
        state = machine.evaluate(make_metrics(cpu=3.0, idle=float(idle)), t)
        machine._commit(state, t, from_override=machine._last_from_override)
        seen[idle] = state

    assert seen[299] == DEFAULT_STATE
    assert seen[301] == "waiting-dozy"
    assert seen[899] == "waiting-dozy"
    assert seen[901] == "waiting-deep-sleep"

    # 0.1 s after entering deep sleep -- two orders of magnitude inside the 4 s dwell.
    woke = machine.evaluate(make_metrics(cpu=3.0, idle=0.0), t + 0.1)
    assert woke == DEFAULT_STATE, (
        f"state age was 0.1 s against a {config.MIN_STATE_DWELL_MS} ms dwell; waking must be "
        "an escalation")


def test_sleep_states_are_gated_on_the_machine_being_quiet(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """A box rendering at 90% while the user is away is not a sleeping pet.

    The pet represents the machine, not the human.
    """
    machine = make_machine()
    trace = drive(machine, [make_metrics(cpu=90.0, idle=2000.0)] * 4, t0=0.0, step=5.0)
    assert not any(s.startswith("waiting-") for s in states(trace)), sorted(set(states(trace)))


def test_idle_seconds_none_never_yields_a_sleep_state(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """`None` from the platform seam means "unknown", never "the user has been away for 0 s".

    The inverse mistake -- returning `0.0` on failure -- would read as "user is active" and
    permanently disable the sleep states; this one would put the pet to sleep immediately.
    """
    machine = make_machine()
    trace = drive(machine, [make_metrics(cpu=2.0, idle=None)] * 20, t0=0.0, step=120.0)
    assert not any(s.startswith("waiting-") for s in states(trace))


def test_needs_input_sad_has_no_automatic_trigger(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """Reachable only via `trigger_state`, by design (contract 4.4).

    There is no telemetry signal for "input needed"; a pet that looks sad for no discoverable
    reason is worse than one that never does.
    """
    machine = make_machine()
    extremes = [
        make_metrics(cpu=0.0, ram=10.0, swap=0.0, idle=None),
        make_metrics(cpu=100.0, ram=99.0, swap=99.0, idle=0.0),
        make_metrics(cpu=50.0, ram=50.0, swap=0.0, idle=1000.0),
    ]
    trace = drive(machine, extremes, t0=0.0, step=10.0)
    assert "needs-input-sad" not in set(states(trace))

    machine.trigger_state("needs-input-sad", 3000)
    assert machine.state == "needs-input-sad"


def test_trigger_state_ignores_a_name_that_is_not_a_state(
        make_machine: Callable[..., StateMachine]) -> None:
    """A typo in a menu wiring is logged and dropped, never raised."""
    machine = make_machine()
    before = machine.state
    machine.trigger_state("not-a-real-state")
    assert machine.state == before


# =============================================================================================
# ready-pleased edge detection
# =============================================================================================


def test_ready_pleased_needs_a_sustained_spike_and_a_sustained_calm(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """"A CPU spike drops back to idle" is a transition, so it needs an arm/fire latch.

    Two failure modes the latch has to avoid, both checked here:

    * a 3 s dip between two build steps must not claim the credit (firing needs
      `READY_CALM_SAMPLES` *consecutive* calm samples);
    * that same dip must not disarm the latch either, or the user waits out another
      `READY_MIN_SPIKE_S` before the pet can look pleased.
    """
    machine = make_machine()
    script = ([make_metrics(cpu=5.0)] * 2          # baseline
              + [make_metrics(cpu=90.0)] * 8       # 12 s spike -- arms
              + [make_metrics(cpu=35.0)] * 2)      # a dip between build steps
    trace = drive(machine, script, t0=0.0, step=TICK)
    assert "ready-pleased" not in set(states(trace))
    assert machine._ready_armed is True, "the dip must not disarm the latch"

    after = drive(machine, [make_metrics(cpu=90.0)] * 4 + [make_metrics(cpu=5.0)] * 8,
                  t0=100.0, step=TICK)
    seq = states(after)
    assert "ready-pleased" in seq, seq
    assert machine._ready_armed is False, "firing disarms"

    episodes = sum(1 for previous, current in zip(["<start>"] + seq, seq)
                   if current == "ready-pleased" and previous != "ready-pleased")
    assert episodes == 1, f"{episodes} episodes; {seq}"


# =============================================================================================
# Dwell
# =============================================================================================


def test_dwell_suppresses_a_lateral_move_but_never_an_escalation(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """Dwell stops churn between rules; it must never delay an alarm.

    Three steps on one timeline: a downward move 500 ms in is suppressed, an escalation 600 ms
    in is not, and the same downward move 5 s in goes through.
    """
    machine = make_machine()
    drive(machine, [make_metrics(cpu=95.0, ram=50.0)], t0=0.0, step=0.0)
    assert machine.state == "running-playful-jump"

    lateral = machine.evaluate(make_metrics(cpu=5.0), 0.5)
    assert lateral == "running-playful-jump", (
        f"a downward move 500 ms into a {config.MIN_STATE_DWELL_MS} ms dwell must be held")
    machine._commit(lateral, 0.5, from_override=machine._last_from_override)

    escalation = machine.evaluate(make_metrics(cpu=5.0, swap=55.0), 0.6)
    assert escalation == "blocked-grumpy", (
        f"PRIORITY {PRIORITY['blocked-grumpy']} < {PRIORITY['running-playful-jump']}, so the "
        "alarm bypasses dwell")

    later = make_machine()
    drive(later, [make_metrics(cpu=95.0, ram=50.0)], t0=0.0, step=0.0)
    assert later.evaluate(make_metrics(cpu=5.0), 5.0) == DEFAULT_STATE


# =============================================================================================
# Flourishes: rotation, weighting, cadence
# =============================================================================================


def test_idle_flourish_rotation_covers_the_pool_and_respects_the_weights(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """Six simulated hours of calm: all three idle flourishes appear, the roll dominating.

    The weights are 6 / 3 / 1, so over ~70 draws all three should be represented and
    `running-playful-roll` should clearly lead. The assertion is *ordering*, not a proportion:
    a chi-squared test on 70 samples would be theatre, and pinning exact counts would make the
    test a hash of the seed rather than a statement about the design.

    The seed is fixed (`conftest.DEFAULT_RNG_SEED`) so this is the same 70 draws every run.
    """
    import collections

    from src.core.state_machine import VARIATION_HOSTS

    machine = make_machine()
    trace = drive(machine, [make_metrics(cpu=5.0)] * int(6 * 3600 / TICK))
    flourishes = [s for s in states(trace) if s != DEFAULT_STATE]
    mix = collections.Counter(flourishes)

    pool_names = {f.state for f in VARIATION_HOSTS[DEFAULT_STATE].pool}
    assert set(mix) == pool_names, f"drew {sorted(mix)}, pool is {sorted(pool_names)}"
    assert mix["running-playful-roll"] > mix["ready-pleased"], dict(mix)


def test_load_mapped_rows_never_appear_as_an_ambient_flourish(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """The pet is a system monitor first.

    Every animation allowed to play at an arbitrary time is one you can no longer read machine
    state from: if Chico's jump could show up while the box is idle, a jump would stop meaning
    "the CPU is pegged". The load-mapped rows are therefore excluded from the ambient pools.
    """
    machine = make_machine()
    trace = drive(machine, [make_metrics(cpu=5.0)] * int(6 * 3600 / TICK))
    seen = set(states(trace))
    forbidden = {"running-playful-jump", "blocked-grumpy",
                 "waiting-dozy", "waiting-deep-sleep", "needs-input-sad"}
    assert not (seen & forbidden), sorted(seen & forbidden)


def test_every_flourish_hands_the_pet_back_to_its_host_state(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """A flourish is punctuation: host -> flourish -> host, never flourish -> flourish.

    This is what makes a `loop: false` row safe to use. Row 7 ends on a deliberate tail-only
    pose with Layla walked out of frame; residing in it, or chaining into another flourish
    without passing through the host, would park the pet on an empty window.
    """
    machine = make_machine()
    trace = drive(machine, [make_metrics(cpu=5.0)] * int(4 * 3600 / TICK))
    seq = states(trace)
    chained = [(a, b) for a, b in zip(seq, seq[1:])
               if a != DEFAULT_STATE and b != DEFAULT_STATE and a != b]
    assert not chained, chained


def test_idle_stroll_cadence_matches_its_configured_interval(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """30 minutes of calm gives roughly five strolls, ~300-330 s apart.

    Bounds are deliberately wider than the seeded run needs (4-9 rather than 5-6, 300-340 s
    rather than the exact jitter draw). With the RNG pinned they could be tightened to equality;
    leaving them loose keeps the test a statement about the *cadence* rather than about the
    seed, so re-seeding does not turn into re-baselining.
    """
    machine = make_machine()
    trace = drive(machine, [make_metrics(cpu=5.0)] * int(30 * 60 / TICK))
    strolls = flourish_starts(trace, DEFAULT_STATE)
    assert 4 <= len(strolls) <= 9, f"{len(strolls)} strolls in 30 min: {strolls}"

    gaps = [round(b - a, 1) for a, b in zip(strolls, strolls[1:])]
    lower = config.IDLE_VARIATION_INTERVAL_MS / 1000.0
    upper = (config.IDLE_VARIATION_INTERVAL_MS + config.IDLE_VARIATION_JITTER_MS) / 1000.0 + 10
    assert all(lower <= gap <= upper for gap in gaps), f"gaps {gaps} outside [{lower}, {upper}]"


def test_moderate_cadence_is_faster_and_runs_independently(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """The busy-band stroll is on a ~25 s cadence, and does not share the idle countdown."""
    machine = make_machine()
    trace = drive(machine, [make_metrics(cpu=35.0)] * int(3 * 60 / TICK))
    starts = flourish_starts(trace, "running-playful-roll")
    gaps = [round(b - a, 1) for a, b in zip(starts, starts[1:])]
    lower = config.MODERATE_VARIATION_INTERVAL_MS / 1000.0
    upper = (config.MODERATE_VARIATION_INTERVAL_MS
             + config.MODERATE_VARIATION_JITTER_MS) / 1000.0 + TICK
    assert starts, "no moderate flourish in 3 minutes of load"
    assert all(lower <= gap <= upper for gap in gaps), f"gaps {gaps} outside [{lower}, {upper}]"


def test_running_busy_play_is_only_ever_a_timed_override(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """Row 7 is never resided in -- contract 2.1, and the reason for the rows 1/7 tiebreak.

    Two minutes of steady moderate load: the pet's resident state is `running-playful-roll`
    throughout, with row 7 appearing only as brief bursts that end back on row 1.
    """
    machine = make_machine()
    trace = drive(machine, [make_metrics(cpu=40.0)] * 80)
    seq = states(trace)
    busy_samples = [s for s in seq if s == "running-busy-play"]
    episodes = sum(1 for previous, current in zip(["<start>"] + seq, seq)
                   if current == "running-busy-play" and previous != "running-busy-play")
    assert episodes > 0, "the tiebreak flourish never fired"
    assert len(busy_samples) <= episodes * 2, (
        f"{len(busy_samples)} samples across {episodes} episodes -- row 7 is being resided in")
    assert seq[-1] == "running-playful-roll"


def test_no_stroll_while_the_pet_is_asleep(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """The idle cadence belongs to `idle-attentive`, not to "the desktop is quiet"."""
    machine = make_machine()
    drive(machine, [make_metrics(cpu=5.0)] * 2)
    trace = drive(machine, [make_metrics(cpu=5.0, idle=1000.0)] * int(20 * 60 / TICK))
    assert "running-busy-play" not in set(states(trace))


# =============================================================================================
# working-together
# =============================================================================================


def test_working_together_is_selected_once_its_row_exists(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """The same calm sample that gives `idle-attentive` gives `working-together` when active.

    No warm-up: the latch is owned by the telemetry sub-sampler, so by the time the state
    machine sees it the hysteresis has already happened and the ladder is a plain read.
    """
    machine = make_machine(available=STATES)
    machine.on_input_activity_changed(True)
    trace = drive(machine, [make_metrics(cpu=5.0, idle=0.1)] * 10)
    assert states(trace)[0] == WORKING_STATE, "selected on the very first sample"

    quiet = make_machine(available=STATES)
    quiet.on_input_activity_changed(False)
    assert WORKING_STATE not in set(states(
        drive(quiet, [make_metrics(cpu=5.0, idle=0.1)] * 10)))


@pytest.mark.parametrize(
    ("label", "sample_kwargs", "expected"),
    [
        ("heavy CPU", dict(cpu=95.0, ram=50.0, idle=0.1), {"running-playful-jump"}),
        ("high swap", dict(cpu=5.0, swap=60.0, idle=0.1), {"blocked-grumpy"}),
        ("moderate CPU", dict(cpu=35.0, idle=0.1),
         {"running-playful-roll", "running-busy-play"}),
    ],
)
def test_load_rules_outrank_working_together(
        make_machine: Callable[..., StateMachine], make_metrics,
        label: str, sample_kwargs: dict, expected: set[str]) -> None:
    """What the machine is doing outranks what the human is doing.

    A build at 95% CPU still gets its own animation even though the user is plainly at the
    keyboard -- the pet is a system monitor, and `working-together` fills the gap where it would
    otherwise sit in `idle-attentive`, not the gap where it has something to report.
    """
    machine = make_machine(available=STATES)
    machine.on_input_activity_changed(True)
    trace = drive(machine, [make_metrics(**sample_kwargs)] * 8)
    assert set(states(trace)) <= expected, (label, sorted(set(states(trace))))


def test_leaving_working_together_bypasses_dwell(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """An alarm half a second into `working-together` is not made to wait.

    `DWELL_BYPASS_ON_WORKING_EXIT` exists because the state already has a 2200 ms idle grace
    period serving dwell's purpose; stacking the 4 s dwell on top would strand the pet at its
    desk for ~6 s after the user walked away.
    """
    machine = make_machine(available=STATES)
    machine.on_input_activity_changed(True)
    t = 1000.0
    machine._commit(machine.evaluate(make_metrics(cpu=5.0, idle=0.1), t), t,
                    from_override=machine._last_from_override)
    assert machine.state == WORKING_STATE

    escalated = machine.evaluate(make_metrics(cpu=95.0, ram=50.0, idle=0.1), t + 0.5)
    assert escalated == "running-playful-jump"


def test_continuous_interaction_never_reads_as_sleep(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """20 minutes of interaction: `idle_seconds` stays ~0, so the sleep rows stay unreachable."""
    machine = make_machine(available=STATES)
    machine.on_input_activity_changed(True)
    trace = drive(machine, [make_metrics(cpu=5.0, idle=0.1)] * int(20 * 60 / TICK))
    assert not any(s.startswith("waiting-") for s in states(trace))


# =============================================================================================
# Structural invariants
# =============================================================================================


def test_priority_covers_every_state() -> None:
    """A state with no `PRIORITY` entry raises a KeyError inside `_apply_dwell` -- at runtime."""
    assert set(PRIORITY) == set(STATES)


def test_working_together_is_ranked_between_idle_and_the_load_rules() -> None:
    """Above `idle-attentive` so sitting down is an escalation; below every load rule."""
    assert PRIORITY[WORKING_STATE] < PRIORITY[DEFAULT_STATE]
    assert PRIORITY[WORKING_STATE] > PRIORITY["running-playful-roll"]
    assert PRIORITY[WORKING_STATE] > PRIORITY["running-playful-jump"]


def test_sleep_states_rank_below_idle_attentive() -> None:
    """So that every *exit* from sleep is an escalation and waking is never delayed.

    Counter-intuitive -- a sleeping pet feels like a low-priority state, and it is; the ranking
    is about exit responsiveness, not importance.
    """
    assert PRIORITY["waiting-dozy"] > PRIORITY[DEFAULT_STATE]
    assert PRIORITY["waiting-deep-sleep"] > PRIORITY[DEFAULT_STATE]


def test_required_states_is_derived_from_states_minus_optional() -> None:
    """One edit adds an optional state, not two lists to keep in sync."""
    assert set(REQUIRED_STATES) == set(STATES) - set(OPTIONAL_STATES)
    assert DEFAULT_STATE in REQUIRED_STATES


def test_state_changed_is_emitted_only_on_a_real_change(
        make_machine: Callable[..., StateMachine], make_metrics) -> None:
    """Twenty identical samples must not ask the animator to restart the same row twenty times."""
    machine = make_machine()
    emitted: list[str] = []
    machine.state_changed.connect(emitted.append)
    for i in range(20):
        machine.on_metrics(make_metrics(cpu=95.0, ram=50.0, timestamp=float(i)))
    assert emitted == ["running-playful-jump"], emitted


def test_on_metrics_ignores_a_payload_that_is_not_metrics(
        make_machine: Callable[..., StateMachine]) -> None:
    """The slot is `pyqtSlot(object)`, so a mis-wired signal must degrade, not crash the GUI."""
    machine = make_machine()
    before = machine.state
    machine.on_metrics("not a Metrics")
    machine.on_metrics(None)
    assert machine.state == before
