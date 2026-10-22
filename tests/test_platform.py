"""`src/core/platform/` -- the OS seam.

Two halves. The abstract seam (`NullBackend`, the package-level functions, the "never raises"
contract) is portable and runs everywhere. The Windows implementation needs a real `HWND` and
is marked `windows`; on any other platform those tests skip with a reason, because the seam
exists precisely so that macOS and Linux can be ported later and a suite that *failed* there
would be telling a future porter that they broke something they have not written yet.

Every ex-style assertion reads the bit back through `tests/_win32.py`, an independent ctypes
implementation. See that module's docstring for why.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import pytest

from src.core import platform as seam
from src.core.platform.base import NullBackend, PlatformBackend

from . import _win32
from ._env import IS_WINDOWS, requires_windows


@pytest.fixture
def probe_window(qapp: object, qt_cleanup: Callable[[object], object]):
    """A plain, shown `QWidget` -- a real native window to apply styles to.

    Deliberately *not* a `PetWindow`: this module tests the seam, and using the app's own
    window would drag in its flag recipe and its `_apply_native_state` re-application, which are
    `test_pet_window.py`'s subject.
    """
    from PyQt6.QtWidgets import QApplication, QWidget

    widget = QWidget()
    widget.setWindowTitle("lc-pet-stat platform probe")
    widget.resize(200, 120)
    qt_cleanup(widget)
    widget.show()
    QApplication.processEvents()
    assert int(widget.winId()) != 0, "no native handle was created"
    return widget


# =============================================================================================
# The portable seam
# =============================================================================================


def test_backend_reports_itself_consistently() -> None:
    """`backend_name()`, `supports_click_through()` and `unsupported_reason()` must agree.

    A backend that claims support while carrying a reason, or refuses support without one,
    leaves the tray's disabled-checkbox tooltip empty -- the user then sees a greyed control
    with no explanation, which is worse than no control.
    """
    name = seam.backend_name()
    supported = seam.supports_click_through()
    reason = seam.unsupported_reason()

    assert isinstance(name, str) and name
    assert isinstance(supported, bool)
    if supported:
        assert reason == "", f"{name} supports click-through but gives reason {reason!r}"
    else:
        assert reason, f"{name} refuses click-through without saying why"


def test_the_windows_backend_is_selected_on_windows() -> None:
    """And, by implication, that a non-Windows run falls back rather than importing ctypes."""
    if IS_WINDOWS:
        assert seam.backend_name() == "windows"
        assert seam.supports_click_through() is True
    else:
        assert seam.backend_name() != "windows"


def test_null_backend_logs_once_and_returns_false(caplog: pytest.LogCaptureFixture) -> None:
    """The unported-OS path: never raises, never succeeds, and does not spam the log.

    "Logs once" is the part that matters. The pet re-applies its native state on every show,
    every flag change and every screen change, so a backend that logged per call would produce
    a line every few seconds forever on an unsupported platform.
    """
    backend = NullBackend("scratch reason")
    with caplog.at_level(logging.DEBUG, logger="src.core.platform.base"):
        results = [backend.set_click_through(1234, True) for _ in range(5)]

    assert results == [False] * 5
    mentions = [r for r in caplog.records if "scratch reason" in r.getMessage()]
    assert len(mentions) == 1, f"logged {len(mentions)} times over 5 calls"

    assert backend.get_idle_seconds() is None
    assert backend.unsupported_reason() == "scratch reason"
    assert backend.supports_click_through() is False


def test_always_on_top_is_a_documented_no_op_at_the_seam() -> None:
    """Contract 4.5: `PlatformBackend.set_always_on_top` defaults to `False`.

    Qt's `WindowStaysOnTopHint` already does the job, via `PetWindow.set_flag_override`. The
    seam method exists for a future platform that needs native help; returning `False` is the
    honest "I did not do anything" answer, and `PetWindow` does not depend on it.
    """
    assert PlatformBackend.set_always_on_top(seam.backend(), 0, True) is False


def test_get_idle_seconds_is_thread_safe() -> None:
    """The telemetry worker calls it from its own thread, six times a second.

    The Windows implementation is a `GetLastInputInfo` read into a stack struct with no shared
    state, so this is checking that it stays that way rather than growing a cached handle.
    """
    results: list[object] = []
    thread = threading.Thread(target=lambda: results.append(seam.get_idle_seconds()))
    thread.start()
    thread.join(timeout=5.0)

    assert len(results) == 1
    assert results[0] is None or isinstance(results[0], float)
    if isinstance(results[0], float):
        assert 0.0 <= results[0] < 1e6, f"implausible idle time {results[0]}"


# =============================================================================================
# Windows
# =============================================================================================


@requires_windows
@pytest.mark.windows
def test_click_through_is_applied_and_verified_by_an_independent_read(probe_window) -> None:
    """`set_click_through` returns True only when `WS_EX_TRANSPARENT` is really set.

    `WA_TransparentForMouseEvents` cannot do this job: it redirects events *inside* Qt's
    dispatcher, long after the OS has hit-tested the pixel and decided our window owns it.
    Using it alone would be worse than doing nothing -- the pet stops responding and the window
    underneath still never gets the click. Only `WS_EX_TRANSPARENT` makes the OS skip us.
    """
    hwnd = int(probe_window.winId())
    before = _win32.ex_style(hwnd)

    assert seam.set_click_through(probe_window, True) is True
    after = _win32.ex_style(hwnd)
    assert after & _win32.WS_EX_TRANSPARENT, (
        f"independent read shows {_win32.describe(after)}, was {_win32.describe(before)}")

    assert seam.set_click_through(probe_window, False) is True
    cleared = _win32.ex_style(hwnd)
    assert not (cleared & _win32.WS_EX_TRANSPARENT), _win32.describe(cleared)


@requires_windows
@pytest.mark.windows
def test_disabling_click_through_does_not_clear_ws_ex_layered(probe_window) -> None:
    """Qt may own `WS_EX_LAYERED` for the translucent surface; clearing it breaks rendering.

    The tempting implementation -- "remember the ex-style before, restore it after" -- takes
    this bit with it. Disable must clear exactly one bit and leave the rest alone.
    """
    hwnd = int(probe_window.winId())
    before_layered = bool(_win32.ex_style(hwnd) & _win32.WS_EX_LAYERED)

    seam.set_click_through(probe_window, True)
    seam.set_click_through(probe_window, False)

    after_layered = bool(_win32.ex_style(hwnd) & _win32.WS_EX_LAYERED)
    assert after_layered == before_layered, (
        f"WS_EX_LAYERED changed {before_layered} -> {after_layered}")


@requires_windows
@pytest.mark.windows
def test_click_through_toggles_are_idempotent(probe_window) -> None:
    """Setting the state it is already in succeeds and changes nothing.

    `PetApp` pushes the setting on startup, on every tray toggle and on tray double-click, so
    redundant calls are routine.
    """
    backend = seam.backend()
    hwnd = int(probe_window.winId())

    assert backend.set_click_through(hwnd, True) is True
    first = _win32.ex_style(hwnd)
    assert backend.set_click_through(hwnd, True) is True
    assert _win32.ex_style(hwnd) == first

    assert backend.set_click_through(hwnd, False) is True
    assert backend.set_click_through(hwnd, False) is True


@requires_windows
@pytest.mark.windows
@pytest.mark.parametrize("handle", [0, 0xDEADBEEF])
def test_an_invalid_handle_returns_false_and_never_raises(handle: int) -> None:
    """Contract 4.5: every OS failure is logged and returned as `False`.

    A raise here would come out of `showEvent` or a tray slot, where nothing is catching it.
    `0xDEADBEEF` also exercises the 64-bit marshalling: without `argtypes`, ctypes would pass
    an HWND as a 32-bit C int and silently target a different window or none at all.
    """
    assert seam.backend().set_click_through(handle, True) is False


@requires_windows
@pytest.mark.windows
def test_native_handle_is_read_fresh_every_time(probe_window) -> None:
    """`native_handle` must never cache -- `setWindowFlags` recreates the HWND.

    See `test_pet_window.py::test_regression_click_through_survives_a_flag_change` for the
    measured case where Windows recycled the *same* handle value into a recreated window, which
    is why a cache keyed on handle identity would look valid while being stale.
    """
    assert seam.native_handle(probe_window) == int(probe_window.winId())


@requires_windows
@pytest.mark.windows
def test_idle_seconds_is_a_plausible_float() -> None:
    """`GetLastInputInfo` via `GetTickCount64`, wrap-safe, never 0.0 on failure.

    `GetTickCount` (32-bit) wraps at 49.7 days, and `LASTINPUTINFO.dwTime` is a 32-bit tick, so
    the subtraction has to be masked. Failure must return `None`, never `0.0` -- `0.0` reads as
    "the user is active right now" and would permanently disable the sleep states.
    """
    idle = seam.get_idle_seconds()
    assert isinstance(idle, float), f"got {idle!r}"
    assert 0.0 <= idle < 1e6
