"""Backend selection and the public face of the platform seam.

Callers import *these functions* and never a backend object::

    from src.core import platform

    if platform.supports_click_through():
        ok = platform.set_click_through(self, True)

Selection happens exactly once, at import. There is no runtime re-probe: the OS does not
change under a running process, and a lazy probe would move an import failure onto the first
tray click, where it is least useful. A backend that fails to import degrades to
``NullBackend`` carrying the reason -- the app must still start.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from .base import NullBackend, PlatformBackend, WindowHandle

if TYPE_CHECKING:
    # Type-checking only. This package must stay importable with no QApplication -- and, on a
    # build without PyQt6 at all, so that `validate_atlas`-style headless tooling still runs.
    from PyQt6.QtWidgets import QWidget

log = logging.getLogger(__name__)

__all__ = [
    "PlatformBackend",
    "NullBackend",
    "WindowHandle",
    "backend",
    "backend_name",
    "supports_click_through",
    "unsupported_reason",
    "native_handle",
    "set_click_through",
    "set_always_on_top",
    "get_idle_seconds",
]


def _select_backend() -> PlatformBackend:
    """Pick the backend for this OS. Import time, GUI thread, exactly once."""
    if sys.platform == "win32":
        try:
            from .windows import WindowsBackend
        except Exception as exc:  # noqa: BLE001 -- a missing export must not stop the app
            # Realistically: a 32-bit user32 without the Ptr exports, a locked-down image
            # where ctypes cannot open the DLL, or a Wine/ReactOS stub.
            log.warning("Windows platform backend unavailable (%s); click-through disabled",
                        exc)
            return NullBackend(f"Windows backend failed to load: {exc}")
        return WindowsBackend()

    return NullBackend(
        f"click-through and idle detection are not implemented for sys.platform="
        f"{sys.platform!r} (Phase 1 targets Windows only; see base.py for the porting notes)")


_BACKEND: PlatformBackend = _select_backend()
log.debug("platform backend: %s", _BACKEND.name)


def backend() -> PlatformBackend:
    """The selected backend. Escape hatch for tests and diagnostics. Any thread."""
    return _BACKEND


def backend_name() -> str:
    """``"windows"`` or ``"null"``. For logs and the tray tooltip. Any thread."""
    return _BACKEND.name


def supports_click_through() -> bool:
    """Whether click-through can work here. Any thread."""
    return _BACKEND.supports_click_through()


def unsupported_reason() -> str:
    """Why click-through is unavailable, or ``""`` if it is available. Any thread."""
    return _BACKEND.unsupported_reason()


def native_handle(widget: QWidget) -> WindowHandle:
    """Return ``int(widget.winId())`` -- re-read every call, **never cached**.

    ``widget`` is duck-typed: anything exposing ``winId()`` works, and nothing here does an
    ``isinstance`` check, which is what keeps this module free of a runtime Qt import.

    Caching would be a real bug, not a missed optimisation. ``setWindowFlags`` destroys the
    native window and Qt creates a fresh one with a different HWND, so a cached handle would
    address a dead window -- or one Windows has since recycled. ``winId()`` also *creates* the
    native window as a side effect if it does not exist yet.

    Returns 0 if the handle cannot be obtained; the backends reject 0. **GUI thread only** --
    ``winId()`` is a Qt widget call.
    """
    try:
        return int(widget.winId())
    except Exception:  # noqa: BLE001
        log.exception("could not read a native handle from %r", widget)
        return 0


def set_click_through(widget: QWidget, enabled: bool) -> bool:
    """Make ``widget`` transparent to mouse input (or interactive again).

    ``True`` means applied **and verified by read-back**; ``False`` means it did not happen,
    and the caller must show the toggle as off (contract section 5.3: the tray reflects
    reality, not intent). Never raises. **GUI thread only** (it reads ``winId()``).
    """
    handle = native_handle(widget)
    if not handle:
        return False
    return _BACKEND.set_click_through(handle, enabled)


def set_always_on_top(widget: QWidget, enabled: bool) -> bool:
    """Native always-on-top. Returns ``False`` on every backend today, by design.

    Qt's ``WindowStaysOnTopHint`` via ``PetWindow.set_flag_override`` is the supported path
    (contract section 2.2); doing it natively as well would desync Qt's flag bookkeeping.
    Kept so a platform that needs it has somewhere to put it. **GUI thread only.**
    """
    handle = native_handle(widget)
    if not handle:
        return False
    return _BACKEND.set_always_on_top(handle, enabled)


def get_idle_seconds() -> float | None:
    """Seconds since the last global user input, or ``None`` when unknowable.

    ``None`` is not ``0.0``: zero means "the user just typed". Consumers must treat the two
    differently or the sleep states become unreachable.

    Takes no widget and touches no Qt object, so it is safe to call from the **telemetry
    worker thread** -- which is the only caller, once per poll. Never raises.
    """
    return _BACKEND.get_idle_seconds()
