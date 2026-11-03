"""Pomodoro: the timer, the card button, the done-hold and the tray label.

The timer takes an injected clock, so nothing here sleeps for real.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QImage, QMouseEvent

from src import config
from src.core.pomodoro import PomodoroTimer
from src.core.telemetry import Metrics
from src.ui.stats_card import StatsCard


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def timer(qapp: object, clock: FakeClock,
          qt_cleanup: Callable[[object], object]) -> PomodoroTimer:
    t = PomodoroTimer(duration_ms=60_000, clock=clock)
    qt_cleanup(t)
    return t


def test_default_duration_is_45_minutes() -> None:
    assert config.POMODORO_WORK_MS == 45 * 60 * 1000
    assert config.POMODORO_DONE_MS == 5 * 60 * 1000


def test_start_counts_down_and_finishes_once(timer: PomodoroTimer, clock: FakeClock) -> None:
    done: list[int] = []
    states: list[bool] = []
    timer.finished.connect(lambda: done.append(1))
    timer.state_changed.connect(states.append)

    timer.start()
    assert timer.running and timer.remaining_s() == 60
    clock.t += 59.5
    timer.poll()
    assert timer.running and not done
    clock.t += 1
    timer.poll()
    timer.poll()
    assert done == [1]
    assert not timer.running
    assert states == [True, False]


def test_stop_cancels_without_finishing(timer: PomodoroTimer, clock: FakeClock) -> None:
    done: list[int] = []
    timer.finished.connect(lambda: done.append(1))
    timer.start()
    timer.stop()
    clock.t += 1000
    timer.poll()
    assert not done and not timer.running
    timer.stop()  # idempotent


def test_toggle(timer: PomodoroTimer) -> None:
    timer.toggle()
    assert timer.running
    timer.toggle()
    assert not timer.running


# -- card -------------------------------------------------------------------------------


@pytest.fixture
def card(qapp: object, qt_cleanup: Callable[[object], object],
         make_metrics: Callable[..., Metrics]) -> StatsCard:
    widget = StatsCard()
    qt_cleanup(widget)
    widget.on_metrics(make_metrics())
    widget.on_state_changed("idle-attentive")
    return widget


def _press(widget: StatsCard, point: object) -> None:
    point = QPointF(point)  # card_rect().center() is a QPoint; QMouseEvent wants QPointF
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, point,
                     QPointF(widget.mapToGlobal(point.toPoint())),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    widget.mousePressEvent(ev)


def _paint(widget: StatsCard) -> None:
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    widget.render(image)


def test_click_on_button_toggles_timer_not_pin(card: StatsCard) -> None:
    fired: list[int] = []
    card.pomodoro_toggled.connect(lambda: fired.append(1))
    _press(card, card._play_rect().center())
    assert fired == [1]
    assert not card.is_pinned()


def test_click_elsewhere_still_pins(card: StatsCard) -> None:
    fired: list[int] = []
    card.pomodoro_toggled.connect(lambda: fired.append(1))
    _press(card, card.card_rect().center())
    assert not fired
    assert card.is_pinned()


@pytest.mark.parametrize("running", [False, True])
def test_paint_is_safe_in_every_timer_state(card: StatsCard, running: bool) -> None:
    card.set_pomodoro(running, 125)
    _paint(card)
    card.set_pomodoro(False, 0)
    card.on_state_changed(config.POMODORO_DONE_STATE)
    card.set_pomodoro_done(True)
    _paint(card)


def test_done_flag_clears_when_state_leaves_done(card: StatsCard) -> None:
    card.on_state_changed(config.POMODORO_DONE_STATE)
    card.set_pomodoro_done(True)
    card.on_state_changed("idle-attentive")
    assert card._pomo_done is False


def test_button_clear_of_glyph(card: StatsCard) -> None:
    header = card._header_rect()
    glyph_left = header.right() - config.CARD_GLYPH_D
    assert card._play_rect().right() < glyph_left


# -- state machine hold -----------------------------------------------------------------


def test_long_done_hold_survives_a_click_greeting(make_machine: Callable[..., object]) -> None:
    machine = make_machine()
    machine.trigger_state(config.POMODORO_DONE_STATE, config.POMODORO_DONE_MS)
    until = machine._override_until
    machine.trigger_state(config.POMODORO_DONE_STATE, config.INTERACTION_GREETING_HOLD_MS)
    assert machine._override_until == until
    assert machine.state == config.POMODORO_DONE_STATE


# -- tray / sound -----------------------------------------------------------------------


def test_play_ping_never_raises() -> None:
    from src.core import platform as seam
    assert seam.play_ping() in (True, False)
