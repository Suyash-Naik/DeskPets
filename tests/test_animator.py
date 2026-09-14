"""`src/core/animator.py` -- the QTimer frame player.

Everything about timing has to be measured against a real event loop, so most of this file is `slow`. Tolerances come from PHASE1-LOG.md's measured numbers:

| Measured | Asserted here |
| --- | --- |
| per-frame error, worst 2.0 ms on 90-260 ms frames | 25 ms |
| drift over 3 loops, -0.4 ms (-0.012%) | 40 ms |
| sub-40 ms gaps after a 1.2 s stall: zero | zero, exactly |

The 25 ms and 40 ms bounds are an order of magnitude above what was measured, and are set where they are for a reason: Qt's default `CoarseTimer` has ~15 ms granularity on Windows, so a regression from `PreciseTimer` back to the default lands *inside* 25 ms per frame but accumulates well past 40 ms over three loops. The pair discriminates; neither alone would.

A test that asserted 3 ms would fail on a loaded CI box and teach the reader to re-run.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import pytest
from PyQt6.QtCore import Qt

from src import config
from src.core.animator import Animator

from ._env import requires_atlas

pytestmark = requires_atlas


@pytest.fixture
def animator(sheet, qt_cleanup: Callable[[object], object]) -> Animator:
    """A fresh `Animator` per test, stopped at teardown.

    Fresh rather than shared: the animator carries a frame index, a pending deferred switch and
    a live timer, and a test that inherited any of those from its predecessor would depend on
    execution order.
    """
    instance = Animator(sheet)
    qt_cleanup(instance)
    yield instance
    instance.stop()


@pytest.fixture
def recorder(animator: Animator):
    """Timestamped `frame_changed` capture: `(perf_counter, index, state)` per emission."""
    frames: list[tuple[float, int, str]] = []
    animator.frame_changed.connect(
        lambda _pixmap, _offset: frames.append(
            (time.perf_counter(), animator.current_index(), animator.current_state())))
    return frames


# =============================================================================================
# Timing -- the reason the animator exists at all
# =============================================================================================


@pytest.mark.slow
def test_per_frame_timing_follows_the_atlas_not_a_fixed_tick(
        animator: Animator, sheet, recorder: list, pump: Callable[[int], None]) -> None:
    """Each frame is held for its own authored `duration_ms`.

    The atlas authors 90-260 ms per frame, and `compatibility.notes` says so explicitly. The
    prototype's fixed 120 ms tick throws that phrasing away -- and the difference is not subtle:
    on `idle-attentive` the longest hold is roughly twice the shortest.

    Asserted three ways, weakest first: every gap is close to its authored value; a long frame
    really is longer than a short one; and the longest authored hold is the longest observed
    gap. The last two survive even if the absolute tolerance ever has to be loosened.
    """
    name = sheet.default_animation
    authored = [frame.duration_ms for frame in sheet[name].frames]
    n = len(authored)

    animator.play(name)
    pump(3 * sum(authored) + 300)
    animator.stop()

    indices = [index for _, index, _ in recorder]
    assert indices[:n] == list(range(n)), indices[:n]
    assert indices[n] == 0, "the loop did not wrap"

    gaps = [(recorder[i + 1][0] - recorder[i][0]) * 1000.0 for i in range(len(recorder) - 1)]
    worst = 0.0
    means: list[float] = []
    for k, expected in enumerate(authored):
        observed = [gaps[k + n * loop] for loop in range(3) if k + n * loop < len(gaps)]
        means.append(sum(observed) / len(observed))
        worst = max(worst, max(abs(o - expected) for o in observed))

    assert worst <= 25.0, (
        f"worst per-frame error {worst:.1f} ms (measured baseline 2.0 ms). Observed means "
        f"{[round(m, 1) for m in means]} against authored {authored}")

    longest_authored = authored.index(max(authored))
    assert means.index(max(means)) == longest_authored, (
        f"the longest authored frame ({max(authored)} ms at index {longest_authored}) is not "
        f"the longest observed: means {[round(m, 1) for m in means]}")


@pytest.mark.slow
def test_drift_compensation_keeps_a_loop_on_its_nominal_length(
        animator: Animator, sheet, recorder: list, pump: Callable[[int], None]) -> None:
    """Three loops land within 40 ms of nominal -- absolute deadlines, not relative delays.

    A single-shot timer rescheduled by `duration_ms` accumulates each timeout's lateness; one
    rescheduled against an absolute deadline on a `QElapsedTimer` does not. Measured: -0.4 ms
    over three loops with compensation on, +16.4 ms over six with it off.

    40 ms is also the discriminator described in the module docstring -- a `CoarseTimer`
    regression hides inside the per-frame bound but not inside this one.
    """
    name = sheet.default_animation
    loop_ms = sheet[name].duration_ms
    n = len(sheet[name])

    animator.play(name)
    pump(3 * loop_ms + 300)
    animator.stop()

    assert len(recorder) > 3 * n, f"only {len(recorder)} emissions in 3 loops of {n} frames"
    observed = (recorder[3 * n][0] - recorder[0][0]) * 1000.0
    drift = observed - 3 * loop_ms
    assert abs(drift) <= 40.0, (
        f"drift {drift:+.1f} ms over {3 * loop_ms} ms (measured baseline -0.4 ms)")


@pytest.mark.slow
def test_a_stalled_event_loop_drops_beats_rather_than_machine_gunning(
        animator: Animator, sheet, pump: Callable[[int], None]) -> None:
    """After a 1.2 s block, missed frames are dropped -- not replayed back to back.

    A naive catch-up ("we are ten frames behind, emit ten frames") turns any hitch in the host
    application into a visible burst of animation. Measured: zero sub-40 ms gaps across a
    deliberate 1.2 s stall, where the shortest authored frame is 90 ms.
    """
    gaps: list[float] = []
    previous = [0.0]

    def on_frame(_pixmap: object, _offset: object) -> None:
        now = time.perf_counter()
        if previous[0]:
            gaps.append((now - previous[0]) * 1000.0)
        previous[0] = now

    animator.frame_changed.connect(on_frame)
    animator.play(sheet.default_animation)
    pump(400)
    time.sleep(1.2)          # block the event loop hard: ~10 frames' worth
    pump(600)
    animator.stop()

    shortest = min(f.duration_ms for f in sheet[sheet.default_animation].frames)
    bursts = [round(gap, 1) for gap in gaps if gap < 40.0]
    assert not bursts, (
        f"{len(bursts)} gaps under 40 ms after the stall ({bursts}); the shortest authored "
        f"frame is {shortest} ms, so these are catch-up emissions")


def test_the_timer_is_single_shot_and_precise(animator: Animator) -> None:
    """Contract 4.2: `PreciseTimer`, rescheduled per frame.

    Qt's default `CoarseTimer` has ~15 ms granularity on Windows -- a ~17% error on a 90 ms
    frame, which is visible. Checked structurally as well as by the timing tests above, because
    the structural check says *why* a drift failure happened.
    """
    assert animator._timer.isSingleShot() is True
    if config.PRECISE_ANIMATION_TIMER:
        assert animator._timer.timerType() is Qt.TimerType.PreciseTimer


# =============================================================================================
# State switching
# =============================================================================================


def test_switching_state_emits_the_first_frame_synchronously(
        animator: Animator, sheet, recorder: list) -> None:
    """`play()` emits frame 0 inside the call, not on the next timeout.

    Waiting for the timer would leave the *old* sprite on screen for up to 260 ms after the
    state changed -- which reads as the pet being slow to react to load rather than as a
    rendering detail.
    """
    switches: list[tuple[str, str]] = []
    animator.state_changed.connect(lambda old, new: switches.append((old, new)))

    animator.play(sheet.default_animation)
    recorder.clear()
    switches.clear()

    target = next(n for n in sheet.names() if n != sheet.default_animation)
    animator.play(target)

    assert len(recorder) == 1, f"{len(recorder)} emissions inside play()"
    assert recorder[0][1] == 0
    assert recorder[0][2] == target
    assert switches == [(sheet.default_animation, target)]


def test_play_with_an_unknown_name_returns_false_and_never_raises(
        animator: Animator, sheet, caplog: pytest.LogCaptureFixture) -> None:
    """A telemetry mapper emitting a state the atlas lacks must not crash the pet.

    This is the second half of the availability story: the state machine filters unknown rows
    out, and if one ever gets through anyway the animator declines it and keeps playing. The
    current animation must be *untouched* -- not stopped, not reset.
    """
    animator.play(sheet.default_animation)
    before_state = animator.current_state()
    before_playing = animator.is_playing()

    with caplog.at_level(logging.WARNING, logger="src.core.animator"):
        result = animator.play("no-such-state")

    assert result is False
    assert animator.current_state() == before_state
    assert animator.is_playing() == before_playing
    assert any("no-such-state" in record.getMessage() for record in caplog.records)


def test_replaying_the_current_state_does_not_restart_it(
        animator: Animator, sheet, pump: Callable[[int], None]) -> None:
    """A repeated telemetry tick naming the same state must not snap back to frame 0.

    `StateMachine` only emits on a change, so this should not happen in the app -- but
    `play()` is public and the tray's poke menu can reach it, and a stutter back to frame 0
    every 1.5 s would be a very confusing symptom to chase.
    """
    name = sheet.default_animation
    animator.play(name)
    pump(sheet[name].frames[0].duration_ms + 60)
    index = animator.current_index()
    assert index != 0, "the test needs the animation to have advanced first"

    assert animator.play(name) is True
    assert animator.current_index() == index


def test_restart_forces_a_rewind(animator: Animator, sheet, pump: Callable[[int], None]) -> None:
    """`restart=True` is the explicit opt-in to the behaviour the test above forbids."""
    name = sheet.default_animation
    animator.play(name)
    pump(sheet[name].frames[0].duration_ms + 60)
    assert animator.current_index() != 0

    animator.play(name, restart=True)
    assert animator.current_index() == 0


@pytest.mark.slow
def test_defer_to_loop_end_waits_for_the_loop_boundary(
        animator: Animator, sheet, pump: Callable[[int], None]) -> None:
    """A deferred switch lands on the wrap, not mid-cycle.

    The escape hatch for a switch that would otherwise cut an animation off in the middle of a
    gesture. Not used by the state machine today -- state changes are deliberately immediate --
    but it is public API and the only way to notice it has broken is to exercise it.
    """
    name = sheet.default_animation
    loop_ms = sheet[name].duration_ms
    target = next(n for n in sheet.names() if n != name)

    animator.play(name, restart=True)
    pump(300)
    assert animator.play(target, defer_to_loop_end=True) is True
    assert animator.current_state() == name, "applied immediately instead of deferring"

    pump(loop_ms + 200)
    assert animator.current_state() == target


# =============================================================================================
# Non-looping rows
# =============================================================================================


@pytest.mark.slow
def test_a_non_looping_row_plays_once_then_holds_its_last_frame(
        animator: Animator, sheet, recorder: list, pump: Callable[[int], None]) -> None:
    """Contract 2.1: the animator HOLDS; `StateMachine` owns reversion.

    Row 7 (`running-busy-play`) is `loop: false` with a deliberate tail-only final pose -- Layla
    walks out of frame. The animator must play it exactly once, emit `sequence_finished`, stop
    its timer and leave that frame up.

    The consequence the implementer must not "fix": bypass the state machine and the pet freezes
    on a near-empty frame. That is intended, and `sequence_finished` is the hook for a future
    owner. Two owners reverting the same state is how the pet ping-pongs.
    """
    non_looping = [n for n in sheet.names() if not sheet[n].loop]
    if not non_looping:
        pytest.skip("this atlas has no loop:false row to exercise the hold policy")
    name = non_looping[0]
    animation = sheet[name]

    finished: list[str] = []
    animator.sequence_finished.connect(finished.append)

    animator.play(name)
    pump(animation.duration_ms + 300)

    emitted_indices = [index for _, index, _ in recorder]
    assert emitted_indices == list(range(len(animation))), emitted_indices
    assert finished == [name], finished
    assert animator.is_playing() is False
    assert animator.current_index() == len(animation) - 1

    count_at_end = len(recorder)
    pump(600)
    assert len(recorder) == count_at_end, "still emitting frames while holding"
    assert animator.current_state() == name, "the animator reverted on its own -- contract 2.1"


@pytest.mark.slow
def test_a_sequence_finished_slot_may_call_play_without_re_entrancy_trouble(
        animator: Animator, sheet, pump: Callable[[int], None]) -> None:
    """`sequence_finished` is emitted *before* the hold, and the hold is skipped if a slot acted.

    This is the hook contract 2.1 promises a future owner. If the hold were applied regardless,
    a slot calling `play()` would have its new animation immediately overwritten by the frozen
    tail frame of the old one.
    """
    non_looping = [n for n in sheet.names() if not sheet[n].loop]
    if not non_looping:
        pytest.skip("this atlas has no loop:false row to exercise the hold policy")
    name = non_looping[0]

    animator.sequence_finished.connect(lambda _n: animator.play(sheet.default_animation))
    animator.play(name)
    pump(sheet[name].duration_ms + 400)

    assert animator.current_state() == sheet.default_animation
    assert animator.is_playing() is True


# =============================================================================================
# Pause / resume
# =============================================================================================


@pytest.mark.slow
def test_pause_freezes_the_frame_and_resume_continues_from_it(
        animator: Animator, sheet, recorder: list, pump: Callable[[int], None]) -> None:
    """A hidden pet costs nothing, and reappears where it left off rather than at frame 0.

    Resuming from the frozen frame is the part worth asserting: restarting the row would make
    every tray show/hide visibly snap the animation back to its first pose.
    """
    name = sheet.default_animation
    n = len(sheet[name])
    animator.play(name, restart=True)
    pump(250)

    animator.set_paused(True)
    frozen = animator.current_index()
    recorder.clear()
    pump(700)

    assert recorder == [], f"{len(recorder)} emissions while paused"
    assert animator.is_playing() is False
    assert animator.current_index() == frozen

    animator.set_paused(False)
    pump(700)
    assert recorder, "no emissions after resuming"
    assert recorder[0][1] == (frozen + 1) % n, (
        f"resumed at index {recorder[0][1]}, expected {(frozen + 1) % n} -- it skipped ahead")


def test_set_state_is_the_slot_the_state_machine_connects_to(
        animator: Animator, sheet) -> None:
    """`state_changed(str) -> set_state(str)`: the one wire between the two modules."""
    target = next(n for n in sheet.names() if n != sheet.default_animation)
    animator.play(sheet.default_animation)
    animator.set_state(target)
    assert animator.current_state() == target

    animator.set_state("not-a-row")            # must not raise out of a queued slot
    assert animator.current_state() == target
