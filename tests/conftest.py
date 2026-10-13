"""Shared fixtures.

Three things here are load-bearing and worth reading before the tests themselves:

* **One `QApplication` for the whole session.** Qt allows exactly one per process and will
  abort if a second is constructed, so it is session-scoped. The high-DPI rounding policy is
  set *before* it exists, exactly as `main.py` does -- setting it afterwards is silently
  ignored, and the pet window's size assertions would then be measuring a different Qt.

* **A temporary `$LC_PET_STAT_CONFIG_DIR`.** `config.config_dir()` reads the variable on every
  call rather than at import, so pointing it at a `tmp_path` before anything runs is enough to
  guarantee no test can read, write or delete the developer's real `settings.json`. This is
  autouse; there is no opt-out, deliberately.

* **`make_metrics`.** `Metrics` has twelve fields and every test needs three of them. The
  factory exists so that a test reads `make_metrics(cpu=95.0)` rather than eleven lines of
  irrelevant keyword arguments, which is the difference between a suite someone reviews and a
  suite someone scrolls past.

Nothing here calls `app.exec()`. A test that needs the event loop to turn uses the `pump`
fixture, which runs a nested `QEventLoop` for a bounded number of milliseconds and always
returns -- an `exec()` that never gets its `quit()` would hang the whole run.
"""

from __future__ import annotations

import os
import random
from collections.abc import Callable, Iterator

import pytest

from src import config
from src.core.telemetry import BatterySample, GpuSample, Metrics

# --- process-wide setup ----------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def isolated_config_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[object]:
    """Point settings persistence at a throwaway directory for the whole session.

    Autouse and session-scoped so it is in place before *any* test, fixture or module-level
    import can call `config.settings_path()`. `PetApp` writes settings on construction and on
    every tray toggle, so without this the suite would trample the developer's real file.
    """
    from pathlib import Path

    target: Path = tmp_path_factory.mktemp("lc-pet-stat-config")
    previous = os.environ.get(config.CONFIG_DIR_ENV)
    os.environ[config.CONFIG_DIR_ENV] = str(target)
    assert config.config_dir() == target, "the override did not take effect"
    try:
        yield target
    finally:
        if previous is None:
            os.environ.pop(config.CONFIG_DIR_ENV, None)
        else:
            os.environ[config.CONFIG_DIR_ENV] = previous


@pytest.fixture(scope="session")
def qapp() -> Iterator[object]:
    """The single `QApplication` for the session. **Every GUI test depends on this.**

    Mirrors `main.main()`'s required ordering: the rounding policy first, then the
    application, then `setQuitOnLastWindowClosed(False)` before any window exists.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QApplication

    existing = QApplication.instance()
    if existing is not None:  # pragma: no cover - only if a plugin made one first
        yield existing
        return

    # Must precede QApplication; ignored if set afterwards.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([])
    app.setApplicationName(config.APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    yield app
    # Deliberately no app.quit()/deleteLater(): tearing a QApplication down mid-interpreter
    # while widgets from other fixtures are still being collected is a reliable way to earn a
    # segfault at exit that looks like a test failure but is not one.


# --- event loop ------------------------------------------------------------------------------


@pytest.fixture
def pump(qapp: object) -> Callable[[int], None]:
    """`pump(ms)` -- run the real Qt event loop for `ms` milliseconds, then return.

    A bounded nested loop, never `app.exec()`: timers, queued cross-thread signals and paint
    events all get to run, and the call is guaranteed to come back.
    """
    from PyQt6.QtCore import QEventLoop, QTimer

    def _pump(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(max(0, int(ms)), loop.quit)
        loop.exec()

    return _pump


# --- assets ----------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def sheet(qapp: object) -> object:
    """The real atlas, loaded once. Session-scoped -- decoding 1920x1872 RGBA is not free.

    Depends on `qapp` because `load_spritesheet` builds `QPixmap`s, which is GUI-thread only.
    (`validate_atlas` is the `QImage`-only half and needs no application; tests that want the
    headless path call it directly.)
    """
    from src.core.sprite_loader import load_spritesheet

    return load_spritesheet()


@pytest.fixture(scope="session")
def atlas_json() -> dict:
    """The atlas JSON as raw data, for cross-checking the loader against its own input.

    Read independently of `sprite_loader` on purpose: a test that asks the loader what the
    loader parsed proves nothing.
    """
    import json

    return json.loads(config.ATLAS_JSON.read_text(encoding="utf-8-sig").strip())


# --- payload factory ---------------------------------------------------------------------------


@pytest.fixture
def make_metrics() -> Callable[..., Metrics]:
    """Factory for `Metrics` samples. Defaults describe a calm, quiet desktop.

    The defaults are chosen so that a bare `make_metrics()` lands in `idle-attentive`: low CPU,
    RAM at the ~67-70% this class of machine actually idles at (see the RAM regression test),
    no swap pressure, no GPU, no battery, and a user who touched the keyboard five seconds ago.
    Override only the field the test is about.
    """

    def _make(
        *,
        cpu: float = 5.0,
        ram: float = 67.0,
        swap: float = 3.0,
        disk: float | None = 37.0,
        temp: float | None = None,
        gpu: GpuSample | None = None,
        battery: BatterySample | None = None,
        idle: float | None = 5.0,
        ram_used: int = 11 * 2 ** 30,
        ram_total: int = 16 * 2 ** 30,
        timestamp: float = 0.0,
        input_ratio: float | None = None,
    ) -> Metrics:
        return Metrics(
            timestamp=timestamp,
            cpu_percent=cpu,
            ram_percent=ram,
            ram_used_bytes=ram_used,
            ram_total_bytes=ram_total,
            swap_percent=swap,
            disk_percent=disk,
            cpu_temp_c=temp,
            gpu=gpu,
            battery=battery,
            idle_seconds=idle,
            input_activity_ratio=input_ratio,
        )

    return _make


# --- state machine ------------------------------------------------------------------------------

#: Fixed so the weighted flourish draw is identical on every run. An unseeded suite that passes
#: nine runs in ten is worse than no suite: it teaches the reader to re-run rather than to look.
DEFAULT_RNG_SEED: int = 20260913


@pytest.fixture
def make_machine(qapp: object) -> Callable[..., object]:
    """Factory for a `StateMachine` with its flourish RNG pinned.

    `StateMachine._rng` is a private `random.Random` with no public seeding seam, so the
    factory reaches into it. That is a deliberate, documented exception and it lives *here*,
    once, rather than being repeated in every test that touches a flourish -- a reviewer should
    have to forgive it in one place.

    `available` narrows what the machine may emit, as `main.py` does from `sheet.names()`.
    Pass it explicitly when the test is about availability; leave it alone otherwise and the
    machine knows every name in `STATES`, which is the historical behaviour.
    """
    from src.core.state_machine import StateMachine

    def _make(*, seed: int = DEFAULT_RNG_SEED, available: object = None) -> StateMachine:
        machine = StateMachine()
        machine._rng = random.Random(seed)
        if available is not None:
            machine.set_available_states(list(available))
        return machine

    return _make


# --- Qt object lifetime ----------------------------------------------------------------------


@pytest.fixture
def qt_cleanup(qapp: object) -> Iterator[Callable[[object], object]]:
    """`keep(widget)` -- register a widget or service for teardown at the end of the test.

    Widgets are hidden and scheduled for deletion; `TelemetryService`s are stopped. Leaked
    windows stack up on the desktop and, worse, a leaked telemetry worker thread keeps polling
    psutil and adds noise to the shutdown-timing measurements in `test_telemetry.py`.
    """
    from PyQt6.QtWidgets import QApplication, QWidget

    from src.core.telemetry import TelemetryService

    registered: list[object] = []

    def keep(obj: object) -> object:
        registered.append(obj)
        return obj

    yield keep

    for obj in reversed(registered):
        try:
            if isinstance(obj, TelemetryService):
                obj.stop()
            elif isinstance(obj, QWidget):
                obj.hide()
                obj.deleteLater()
            elif hasattr(obj, "deleteLater"):
                obj.deleteLater()
        except Exception:  # pragma: no cover - teardown must never mask a real failure
            pass
    QApplication.processEvents()
