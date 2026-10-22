"""The cross-platform seam: everything OS-specific in this project lives behind it.

``src/core/platform/`` is the *only* package permitted to import ``ctypes``, touch
``sys.platform`` or name a Win32 symbol (docs/PHASE1-CONTRACT.md section 0). The rest of the
app talks to the package-level functions in ``__init__.py`` and never sees a backend object,
so porting the pet to another OS means adding one module here and one branch in the selector.

Two capabilities are behind the seam today:

``set_click_through``
    Make the pet window transparent to mouse input, so clicks land on whatever is underneath
    it. Every platform implements this differently and none of them do it through Qt.

``get_idle_seconds``
    Seconds since the last *user* input anywhere on the desktop -- not just in our window,
    which is what Qt could tell us. Drives the ``waiting-dozy`` / ``waiting-deep-sleep`` rows.

Failure policy, uniformly: **no method here ever raises.** An OS that cannot do a thing, or
an OS call that fails, is reported as ``False`` / ``None`` plus a log line. A desktop toy must
degrade quietly; a pet that cannot become click-through is a missing feature, not a crash.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


#: A native window handle as Qt hands it to us: ``int(QWidget.winId())``.
#:
#: On Windows this is an ``HWND``; on macOS an ``NSView*``; on X11 an ``xcb_window_t``. It is
#: deliberately a plain ``int`` rather than a ctypes type -- ctypes must not leak out of
#: ``windows.py``, and the callers only ever pass it straight back to us.
#:
#: It is **not stable for the lifetime of a widget**: ``setWindowFlags`` destroys and recreates
#: the native window, so the handle must be re-read from the widget on every use.
WindowHandle = int


class PlatformBackend(ABC):
    """One operating system's implementation of the seam.

    Thread affinity: implementations must be callable from **any** thread. The GUI thread
    drives ``set_click_through``; the telemetry worker thread calls ``get_idle_seconds`` once
    per poll. Backends therefore hold no Qt objects and no mutable shared state beyond
    idempotent logging guards.
    """

    #: Short identifier for logs and the tray tooltip. Matches the module name.
    name: str = "unknown"

    @abstractmethod
    def supports_click_through(self) -> bool:
        """Whether ``set_click_through`` can actually do anything on this machine.

        Callers use this to disable the tray toggle rather than to let it fail silently.
        Any thread.
        """

    @abstractmethod
    def set_click_through(self, handle: WindowHandle, enabled: bool) -> bool:
        """Turn mouse pass-through on or off for ``handle``.

        Returns ``True`` only when the change was applied **and verified by reading the state
        back from the OS** -- an optimistic ``True`` would let the tray checkbox claim a
        click-through pet that still swallows clicks, or worse, claim an interactive pet that
        does not respond. The tray reflects reality, not intent (contract section 5.3).

        Never raises. GUI thread in practice, but must be safe from any thread.
        """

    def set_always_on_top(self, handle: WindowHandle, enabled: bool) -> bool:
        """Native always-on-top. Default: unimplemented, returns ``False``.

        Qt owns this one. ``PetWindow.set_flag_override`` is the single mutation path for
        window flags (contract section 2.2), and a native ``SetWindowPos`` behind Qt's back
        would desync Qt's own flag bookkeeping -- the next ``setWindowFlags`` would silently
        undo it. The hook exists only for a platform where Qt's flag does not work.

        Any thread.
        """
        return False

    def get_idle_seconds(self) -> float | None:
        """Seconds since the last global user input, or ``None`` if unknowable.

        ``None`` is meaningfully different from ``0.0``: zero means "the user just typed", and
        a backend that reported zero on failure would pin the state machine out of its sleep
        rows forever. ``Band.holds(None, ...)`` is ``False``, so an unknown value simply never
        triggers a state.

        Called from the **telemetry worker thread**, once per poll. Must be thread-safe.
        """
        return None

    def unsupported_reason(self) -> str:
        """Human-readable reason click-through is unavailable; ``""`` when it is available.

        Shown as the tooltip on the disabled tray item. Any thread.
        """
        return ""


class NullBackend(PlatformBackend):
    """The do-nothing backend: every unsupported OS, and every failed backend import.

    Chosen so the app runs *everywhere*, minus click-through. It logs its reason **once**, not
    once per call -- the tray toggle is a checkbox a user can flick repeatedly, and a warning
    per flick would bury the log.
    """

    name = "null"

    def __init__(self, reason: str = "") -> None:
        self._reason = reason or "click-through is not supported on this platform"
        # Instance flag, not a module global: a test can build a fresh NullBackend and still
        # observe the first-time log.
        self._warned = False

    def _warn_once(self) -> None:
        """Emit the unsupported message the first time only. Any thread."""
        if not self._warned:
            self._warned = True
            log.warning("platform backend unavailable: %s", self._reason)

    def supports_click_through(self) -> bool:
        """Always False. Any thread."""
        return False

    def set_click_through(self, handle: WindowHandle, enabled: bool) -> bool:
        """Always False, after logging the reason once. Any thread."""
        self._warn_once()
        return False

    def unsupported_reason(self) -> str:
        """The reason captured at construction. Any thread."""
        return self._reason


# ---------------------------------------------------------------------------
# Porting notes -- deliberately NOT implemented (contract: Windows only in Phase 1).
#
# A future contributor should not have to rediscover which API to reach for. Neither of these
# is reachable through Qt; both need the native handle from `native_handle(widget)`.
#
# macOS -- src/core/platform/macos.py, via pyobjc (`pixi add --feature pets pyobjc-framework-Cocoa`):
#   `int(widget.winId())` is an NSView*, not an NSWindow. Wrap and walk up:
#       view = objc.objc_object(c_void_p=handle)
#       window = view.window()                     # NSWindow, or nil before the view is mapped
#       window.setIgnoresMouseEvents_(enabled)     # the click-through switch
#   Notes: `window()` is nil until the window is on screen, so apply it after show();
#   NSWindow calls are main-thread only, unlike this seam's Windows implementation.
#   Idle time is a separate API: CGEventSourceSecondsSinceLastEventType(
#       kCGEventSourceStateCombinedSessionState, kCGAnyInputEventType) -- already in seconds.
#
# Linux/X11 -- src/core/platform/x11.py, via the XFixes extension (python-xlib or ctypes):
#   Input pass-through is an empty *input shape*, not a property:
#       region = XFixesCreateRegion(display, None, 0)                   # empty region
#       XFixesSetWindowShapeRegion(display, win, ShapeInput, 0, 0, region)
#       XFixesDestroyRegion(display, region)
#   Restore interactivity by setting ShapeInput back to None (the whole window), not by
#   setting a full-size rectangle. Requires XFixes >= 4.0; check with XFixesQueryVersion.
#   Wayland has no equivalent -- NullBackend is the correct answer there, not a workaround.
#   Idle time: the XScreenSaver extension, XScreenSaverQueryInfo(...)->idle (milliseconds).
# ---------------------------------------------------------------------------
