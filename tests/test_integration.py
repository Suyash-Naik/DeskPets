"""`src/main.py` -- the whole app, wired together and running.

`main.py` is "wiring only; no logic", which sounds like nothing to test. It is the opposite:
three of the five bugs PHASE1-LOG.md records under "found by running the code" were wiring
bugs, invisible in every unit test because each end behaved correctly on its own. A signal
connected to a slot with the opposite sense, or a push that never happens because the signal
only fires on a change, cannot be caught anywhere but here.

These tests construct the real `PetApp`: real atlas, real windows, real telemetry thread, real
tray. They are `slow` by nature and few by design -- the point of an integration test is the
seams, and anything that can be pinned down in `test_state_machine.py` with an injected clock
belongs there instead.

Settings are written to the temp `$LC_PET_STAT_CONFIG_DIR` from `conftest.py`; `PetApp` saves
on construction and on every toggle, so without that isolation this file would rewrite the
developer's real `settings.json` on every run.
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QGuiApplication, QMouseEvent
from PyQt6.QtWidgets import QApplication

from src import config
from src.core.state_machine import STATES
from src.main import PetApp

from ._env import requires_atlas

pytestmark = [requires_atlas, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def app(qapp: object, pump: Callable[[int], None]) -> Iterator[PetApp]:
    """A fully constructed, running `PetApp`, torn down completely.

    Teardown is not politeness: a surviving telemetry thread keeps polling for the rest of the
    session, and a surviving tray icon leaves a ghost in the notification area.
    """
    instance = PetApp(qapp)  # type: ignore[arg-type]
    available = QGuiApplication.primaryScreen().availableGeometry()
    instance._pet.move(available.left() + 300, available.top() + 250)
    pump(120)
    yield instance

    instance._telemetry.stop()
    if instance._tray is not None:
        instance._tray.shutdown()
    instance._pet.hide()
    instance._card.hide()
    QApplication.processEvents()
    # Each PetApp holds the decoded atlas (a ~14 MB QImage plus 74 pixmaps). Letting several
    # accumulate and then be collected at an arbitrary moment is a good way to stall the GUI
    # thread in the middle of a *later* test's wall-clock measurement, so they are released
    # here rather than whenever the collector gets round to it.
    del instance
    gc.collect()
    QApplication.processEvents()


def opaque_point(window) -> QPoint:
    """A widget-local point that is genuinely on the cat -- see `test_pet_window.py`."""
    centre = QPoint(window.width() // 2, window.height() // 2)
    if window.is_opaque_at(centre):
        return centre
    for radius in range(1, max(window.width(), window.height())):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                candidate = QPoint(centre.x() + dx, centre.y() + dy)
                if (0 <= candidate.x() < window.width()
                        and 0 <= candidate.y() < window.height()
                        and window.is_opaque_at(candidate)):
                    return candidate
    pytest.fail("no opaque pixel in the pet's current frame")


def send_mouse(window, kind: QEvent.Type, local: QPoint, *,
               button: Qt.MouseButton, buttons: Qt.MouseButton) -> None:
    event = QMouseEvent(kind, QPointF(local), QPointF(window.mapToGlobal(local)),
                        button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(window, event)


# =============================================================================================
# REGRESSIONS -- all three are wiring bugs
# =============================================================================================


def test_regression_the_pet_keeps_animating_while_it_is_visible(
        app: PetApp, pump: Callable[[int], None]) -> None:
    """Guards `visibility_changed` being connected straight to `set_paused`.

    PHASE1-LOG.md bug 2. The two have **opposite senses**: `visibility_changed(True)` means
    "shown", `set_paused(True)` means "stop animating". The contract's shorthand ("wire to
    `Animator.set_paused`") hid it, `main.py` connected them directly, and the pet froze the
    instant it became visible. The fix is an explicit inverting lambda.

    Caught originally by a smoke test that *counted frames*: 0 emitted in 2.2 s where ~15 were
    expected. Nothing raised, nothing logged -- the only symptom was a still image, which looks
    exactly like a pet that happens to be on a long frame.

    Both directions are asserted. Frames while shown is what the bug broke; frames *stopping*
    while hidden is what distinguishes correct wiring from `set_paused` never being called at
    all, which would also have made the first half pass.
    """
    frames: list[int] = []
    app._animator.frame_changed.connect(lambda *_: frames.append(1))

    assert app._pet.isVisible(), "the fixture expects a visible pet"
    frames.clear()
    pump(1500)
    while_visible = len(frames)
    assert while_visible >= 5, (
        f"{while_visible} frames in 1.5 s while visible -- the animator is paused. The atlas's "
        "slowest row still emits ~6 frames a second.")

    app._pet.hide()
    pump(100)
    frames.clear()
    pump(800)
    assert frames == [], (
        f"{len(frames)} frames emitted while hidden -- set_paused is never being called, so a "
        "tray-minimised pet still costs CPU")

    app._pet.show()
    pump(100)
    frames.clear()
    pump(800)
    assert frames, "the animator did not resume when the pet was shown again"


def test_regression_the_card_shows_the_real_state_from_the_first_moment(app: PetApp) -> None:
    """Guards the card sitting on `DEFAULT_PRESENTATION` until the first state *change*.

    PHASE1-LOG.md bug 3. `state_changed` fires only on an actual change, and the machine starts
    already in its default state -- so nothing ever pushed the initial state and the card showed
    the placeholder copy ("Chico and Layla / Watching your system") instead of the real one.
    `main.py` has to call `card.on_state_changed(machine.state)` explicitly after wiring.

    Found by looking at a rendered PNG, not by reading code: every signal was connected
    correctly and every component was individually right.
    """
    presentation = app._card._presentation
    assert presentation is not config.DEFAULT_PRESENTATION, (
        "the card is still on the placeholder copy -- the initial state was never pushed")
    assert presentation is config.STATE_PRESENTATION[app._machine.state], (
        f"the card shows copy for a different state than {app._machine.state!r}")


def test_regression_a_short_flourish_ends_on_time_not_on_the_next_poll(
        app: PetApp, pump: Callable[[int], None]) -> None:
    """Guards override expiry being noticed only on the next telemetry sample.

    An expired override used to be detected inside `evaluate()`, which only runs when a
    `Metrics` arrives -- every `POLL_INTERVAL_MS` (1500 ms). So a flourish shorter than the
    poll overstayed by up to a full interval. The stroll is the visible case: 1160 ms of
    animation followed by up to 1500 ms parked on row 7's deliberately tail-only final frame,
    i.e. an apparently empty window for longer than the animation itself ran.

    The fix is a single-shot `QTimer` started with the override's own duration, which is why
    this test needs a live event loop and cannot live in `test_state_machine.py`.

    **Telemetry is stopped first, and that is the whole design of the test.** With the poller
    running, "handed back on time" and "handed back on the next poll" are two timestamps
    ~500 ms apart, and any GUI-thread stall longer than that makes them indistinguishable --
    an earlier draft of this test failed intermittently for exactly that reason, with the
    handback landing on the next poll because a stall had delayed both. With the poller
    stopped, `evaluate()` is never called again, so a hand-back *at all* can only have come
    from the expiry timer. The bug does not merely delay this test, it makes it hang: the pet
    would stay on row 7's tail-only frame for the whole pump window.

    The 400 ms tolerance is therefore a sanity bound, not the discriminator.
    """
    machine = app._machine
    hold_ms = config.MODERATE_BUSY_PLAY_HOLD_MS
    assert hold_ms < config.POLL_INTERVAL_MS, (
        "this test is meaningless unless the flourish is shorter than the poll interval")

    app._telemetry.stop()
    pump(50)

    changes: list[tuple[float, str]] = []
    machine.state_changed.connect(lambda s: changes.append((time.monotonic(), s)))

    started = time.monotonic()
    machine.trigger_state("running-busy-play", hold_ms)
    pump(hold_ms + 1500)

    assert any(state == "running-busy-play" for _, state in changes), changes
    returns = [(t - started) * 1000.0 for t, state in changes
               if state != "running-busy-play" and t > started]
    assert returns, (
        "never left the flourish with the poller stopped -- the expiry timer did not hand the "
        "pet back, so it is parked on row 7's tail-only final frame until the next telemetry "
        f"sample. changes={[(round((t - started) * 1000), s) for t, s in changes]} "
        f"state={machine.state!r} last_natural={machine._last_natural!r} "
        f"override={machine._override_state!r} "
        f"overdue_by={(time.monotonic() - machine._override_until) * 1000:.0f}ms "
        f"timer_active={machine._expiry_timer.isActive()} "
        f"timer_remaining={machine._expiry_timer.remainingTime()}ms")

    lateness = returns[0] - hold_ms
    assert abs(lateness) < 400.0, (
        f"handed back {lateness:+.0f} ms off the {hold_ms} ms hold -- the timer fired, but far "
        "enough out that something stalled the GUI thread")


# =============================================================================================
# Construction and shutdown
# =============================================================================================


def test_the_app_constructs_and_all_the_parts_are_live(app: PetApp) -> None:
    """The wiring order in `main.py` is load-bearing in three places; this proves it survives."""
    assert app._pet.isVisible()
    assert app._animator.is_playing()
    assert app._telemetry.is_running()
    assert app._machine.state in config.STATE_PRESENTATION
    assert app._sheet.default_animation in app._sheet.names()
    assert QApplication.instance().quitOnLastWindowClosed() is False, (
        "hiding the pet from the tray would quit the whole app, tray icon and all")


def test_telemetry_reaches_both_the_state_machine_and_the_card(
        app: PetApp, pump: Callable[[int], None]) -> None:
    """One `metrics_ready` fans out to the machine, the card and the tray status line."""
    samples: list[object] = []
    app._telemetry.metrics_ready.connect(samples.append)
    pump(config.FIRST_SAMPLE_DELAY_MS + config.POLL_INTERVAL_MS + 500)

    assert samples, "no telemetry arrived in the GUI thread"
    assert app._telemetry.last_metrics is not None
    assert app._card._rows, "the card never built a row set from a real sample"


def test_the_machine_is_told_which_rows_the_atlas_actually_has(app: PetApp) -> None:
    """`set_available_states(sheet.names())` before telemetry starts.

    This is what makes an optional state inert instead of a wrong sprite. Without it the ladder
    can name a row the animator does not have, and the pet shows the previous animation while
    the card describes the new state over it.
    """
    for name in app._sheet.names():
        assert app._machine.is_available(name), name
    for name in STATES:
        if name not in set(app._sheet.names()):
            assert not app._machine.is_available(name), (
                f"{name} has no row in this atlas but the machine believes it is available")


def test_every_state_can_be_poked_through_paint_without_aborting(
        app: PetApp, pump: Callable[[int], None]) -> None:
    """The full-app version of the `qFatal` regression.

    `test_stats_card.py` paints each state into an offscreen image; this drives the *real* path
    -- tray poke -> state machine -> animator and card -> on-screen repaint -- for every state,
    with both windows mapped. A regression kills the process; there is no assertion that can
    catch `qFatal`.
    """
    app._card.set_pinned(True)
    pump(200)

    for state in STATES:
        app._machine.trigger_state(state, 200)
        QApplication.processEvents()
        app._card.repaint()
        app._pet.repaint()
        QApplication.processEvents()

    app._card.set_pinned(False)
    assert app._card._paint_failed_for is None


def test_settings_are_persisted_and_reloaded(app: PetApp, pump: Callable[[int], None]) -> None:
    """The app writes its position, and a fresh `load_settings()` reads it back.

    Uses the temp config dir from `conftest.py`, so the developer's real settings file is never
    touched -- see this module's docstring.
    """
    app._pet.move(app._pet.x() + 37, app._pet.y() + 23)
    pump(60)
    app._save()

    reloaded = config.load_settings()
    assert reloaded.pos_x == app._pet.current_position().x()
    assert reloaded.pos_y == app._pet.current_position().y()
    # And prove the isolation itself rather than trusting it: compute where settings would go
    # with the override removed, and check the app did not write there.
    #
    # (A "not under the user's home" check is the obvious form and is wrong on Windows --
    # pytest's tmp_path lives in %TEMP%, which is itself under the home directory.)
    override = os.environ.pop(config.CONFIG_DIR_ENV)
    try:
        production = config.settings_path()
    finally:
        os.environ[config.CONFIG_DIR_ENV] = override
    assert config.settings_path() != production, (
        f"the suite wrote to the real settings file at {production}")
    assert config.settings_path().parent == config.config_dir()


def test_a_clean_quit_stops_the_telemetry_thread(app: PetApp) -> None:
    """The quit sequence: save -> stop telemetry -> hide the tray icon -> quit.

    `QApplication.quit()` is deliberately not exercised (it would end the test session); what
    matters here is that the thread is joined rather than abandoned.
    """
    app._telemetry.stop()
    assert app._telemetry.is_running() is False
    app._telemetry.stop()  # the quit path calls it twice: aboutToQuit and _quit


# =============================================================================================
# Interaction greeting
# =============================================================================================


@pytest.mark.skipif(config.INTERACTION_GREETING_STATE is None,
                    reason="interaction greetings are disabled in config")
def test_clicking_the_pet_greets_without_moving_it(
        app: PetApp, pump: Callable[[int], None]) -> None:
    """A click is a click: the pet waves and the window does not move.

    Driven through synthesised `QMouseEvent`s so the alpha hit-test and the drag threshold are
    genuinely exercised -- calling `clicked.emit()` directly would test the connection and
    nothing else.
    """
    greeting = config.INTERACTION_GREETING_STATE
    pet = app._pet
    seen: list[str] = []
    app._machine.state_changed.connect(seen.append)

    point = opaque_point(pet)
    before = QPoint(pet.pos())
    send_mouse(pet, QEvent.Type.MouseButtonPress, point,
               button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
    send_mouse(pet, QEvent.Type.MouseButtonRelease, point,
               button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.NoButton)
    pump(200)

    assert greeting in seen, f"the click did not greet: {seen}"
    assert pet.pos() == before, "a click must not move the window"


@pytest.mark.skipif(config.INTERACTION_GREETING_STATE is None,
                    reason="interaction greetings are disabled in config")
def test_a_drag_greets_on_drop_and_not_mid_gesture(
        app: PetApp, pump: Callable[[int], None]) -> None:
    """`drag_finished` greets; `drag_started` does not.

    Greeting mid-drag would fire a wave the moment the pet is picked up, so the animation plays
    while the user is still moving it and is over before they let go.
    """
    greeting = config.INTERACTION_GREETING_STATE
    pet = app._pet
    seen: list[str] = []
    app._machine.state_changed.connect(seen.append)

    # Start from the resident state, so a greeting still in flight is not mistaken for this one.
    while app._machine.state == greeting:
        pump(200)
    seen.clear()

    point = opaque_point(pet)
    before = QPoint(pet.pos())
    send_mouse(pet, QEvent.Type.MouseButtonPress, point,
               button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
    for step in (20, 40, 60):
        send_mouse(pet, QEvent.Type.MouseMove, point + QPoint(step, step),
                   button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.LeftButton)
        QApplication.processEvents()
    during_drag = list(seen)

    send_mouse(pet, QEvent.Type.MouseButtonRelease, point + QPoint(60, 60),
               button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.NoButton)
    pump(200)

    assert pet.pos() != before, "the drag did not move the window"
    assert greeting not in during_drag, f"greeted mid-drag: {during_drag}"
    assert greeting in seen, f"the drop did not greet: {seen}"


@pytest.mark.skipif(config.INTERACTION_GREETING_STATE is None,
                    reason="interaction greetings are disabled in config")
def test_repeated_clicks_extend_the_greeting_rather_than_restarting_it(
        app: PetApp, pump: Callable[[int], None]) -> None:
    """Four rapid clicks give ONE transition into the greeting, not four.

    Restarting would stutter the wave back to frame 0 on every click, which reads as the pet
    glitching rather than as it being pleased to see you. Extending falls out of the override
    slot: re-installing the same state only pushes its deadline out, and `_commit` does not
    emit when the state is unchanged.
    """
    greeting = config.INTERACTION_GREETING_STATE
    pet = app._pet
    while app._machine.state == greeting:
        pump(200)

    seen: list[str] = []
    app._machine.state_changed.connect(seen.append)
    point = opaque_point(pet)
    for _ in range(4):
        send_mouse(pet, QEvent.Type.MouseButtonPress, point,
                   button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
        send_mouse(pet, QEvent.Type.MouseButtonRelease, point,
                   button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.NoButton)
        pump(120)

    greetings = [s for s in seen if s == greeting]
    assert len(greetings) == 1, f"{len(greetings)} greeting transitions from four clicks: {seen}"


@pytest.mark.skipif(config.INTERACTION_GREETING_STATE is None,
                    reason="interaction greetings are disabled in config")
def test_a_click_on_the_transparent_gutter_does_not_greet(
        app: PetApp, pump: Callable[[int], None]) -> None:
    """The alpha hit-test, end to end: a corner press reaches the desktop, not the pet."""
    greeting = config.INTERACTION_GREETING_STATE
    pet = app._pet
    while app._machine.state == greeting:
        pump(200)

    seen: list[str] = []
    app._machine.state_changed.connect(seen.append)
    corner = QPoint(2, 2)
    assert not pet.is_opaque_at(corner), "the test needs a transparent corner"

    send_mouse(pet, QEvent.Type.MouseButtonPress, corner,
               button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
    send_mouse(pet, QEvent.Type.MouseButtonRelease, corner,
               button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.NoButton)
    pump(200)

    assert greeting not in seen, f"a transparent-pixel click greeted: {seen}"


# =============================================================================================
# The command line
# =============================================================================================


def test_validate_runs_headless_and_exits_zero() -> None:
    """`--validate` must work with no GUI at all: it is the CI/pre-commit gate for the atlas.

    Run as a subprocess against the same interpreter, which is the only honest way to check an
    exit code -- and it also proves `validate_atlas` really is `QImage`-only, since a
    `QApplication` is never constructed on that path.
    """
    result = subprocess.run(
        [sys.executable, "-m", "src.main", "--validate"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "PASS" in result.stdout, result.stdout
