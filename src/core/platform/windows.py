"""Windows implementation of the platform seam: click-through and global idle time.

Everything here is ``ctypes`` against ``user32``/``kernel32``. There is no ``pywin32``
dependency -- the two calls we need are a handful of lines each, and adding a 10 MB binary
wheel to a desktop toy to avoid writing them is a bad trade.

**Importing this module is the availability probe.** If ``user32`` cannot be opened or the
entry points cannot be resolved, the import raises and ``__init__.py`` falls back to
``NullBackend`` with the reason attached. Nothing here is lazy, so the failure is loud, once,
at startup -- not on the first tray click.

Click-through is ``WS_EX_TRANSPARENT`` on the window's extended style. On a DWM-composited
Win10/11 desktop that bit alone takes the window out of hit-testing; see ``ADD_LAYERED`` for
the legacy case and why it is off.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from .base import PlatformBackend, WindowHandle

log = logging.getLogger(__name__)


# =============================================================================
# Tunables
# =============================================================================

#: Also set ``WS_EX_LAYERED`` when enabling click-through. **Default False, deliberately.**
#:
#: ``WS_EX_TRANSPARENT`` alone already removes a window from hit-testing under DWM
#: composition (every supported Windows 10/11 desktop). The layered bit is a pre-Vista
#: requirement that survives in a lot of copy-pasted advice.
#:
#: Turn it on only if pass-through is *observed* to fail. Note the trap if you do: MSDN is
#: explicit that a window which has just had ``WS_EX_LAYERED`` added is not displayed at all
#: until ``SetLayeredWindowAttributes`` or ``UpdateLayeredWindow`` is called -- so the code
#: below calls ``SetLayeredWindowAttributes`` immediately, and must keep doing so. Qt's
#: translucent surface may already own this bit, which is also why it is never *cleared*.
ADD_LAYERED: bool = False

#: Idle readings above this are treated as a clock artefact rather than a real idle period
#: (see ``_idle_seconds``). ~24.8 days; the wrap boundary of a signed 32-bit millisecond tick.
_IDLE_SANITY_LIMIT_S: float = (1 << 31) / 1000.0


# =============================================================================
# Win32 constants
# =============================================================================

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x0008_0000
WS_EX_TRANSPARENT = 0x0000_0020
LWA_ALPHA = 0x0000_0002

#: The extended style is a DWORD, but the Ptr accessors marshal it through a signed LONG_PTR.
_EXSTYLE_MASK = 0xFFFF_FFFF

ERROR_INVALID_WINDOW_HANDLE = 1400


# =============================================================================
# ctypes prototypes
#
# `argtypes`/`restype` on every function is MANDATORY, not tidiness. Without argtypes ctypes
# marshals a Python int as a 32-bit C `int`; an HWND on win64 routinely exceeds 32 bits, so
# the handle is silently truncated and the call targets a nonexistent -- or, far worse, a
# *different* -- window. Without restype ctypes assumes `int` and truncates the result the
# same way, which would corrupt the ex-style we read back and then write.
#
# `use_last_error=True` gives us a thread-local copy of GetLastError() that Python cannot
# clobber between the call and our check.
# =============================================================================

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

#: The ctypes spelling of Win32's ``LONG_PTR``: 32-bit on win32, 64-bit on win64.
LONG_PTR = ctypes.c_ssize_t

# SetWindowLongPtrW/GetWindowLongPtrW are only *exported* from a 64-bit user32. On 32-bit
# Windows they are preprocessor macros for the plain LONG versions, so the symbols do not
# exist and getattr returns None -- hence the fallback. (win-64 is the only supported target
# today; this costs two lines and removes a whole class of "works on my machine".)
_set_window_long = getattr(_user32, "SetWindowLongPtrW", None) or _user32.SetWindowLongW
_set_window_long.argtypes = (wintypes.HWND, ctypes.c_int, LONG_PTR)
_set_window_long.restype = LONG_PTR

_get_window_long = getattr(_user32, "GetWindowLongPtrW", None) or _user32.GetWindowLongW
_get_window_long.argtypes = (wintypes.HWND, ctypes.c_int)
_get_window_long.restype = LONG_PTR

_user32.IsWindow.argtypes = (wintypes.HWND,)
_user32.IsWindow.restype = wintypes.BOOL

_user32.SetLayeredWindowAttributes.argtypes = (
    wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD)
_user32.SetLayeredWindowAttributes.restype = wintypes.BOOL


class LASTINPUTINFO(ctypes.Structure):
    """``LASTINPUTINFO``: ``dwTime`` is a **32-bit** ``GetTickCount`` value, not 64-bit."""

    _fields_ = (("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD))


_user32.GetLastInputInfo.argtypes = (ctypes.POINTER(LASTINPUTINFO),)
_user32.GetLastInputInfo.restype = wintypes.BOOL

# GetTickCount64, not GetTickCount: the 32-bit version wraps to zero after 49.7 days of
# uptime, and a desktop machine reaching that is entirely ordinary.
_kernel32.GetTickCount64.argtypes = ()
_kernel32.GetTickCount64.restype = ctypes.c_ulonglong


# =============================================================================
# Thin wrappers
# =============================================================================


def _read_ex_style(hwnd: int) -> int:
    """Read ``GWL_EXSTYLE`` for ``hwnd``. Raises ``OSError`` on a genuine failure.

    ``GetWindowLongPtrW`` returns 0 both for "the extended style is zero" and for "the call
    failed" -- the values are indistinguishable. The documented disambiguation is to clear the
    thread's last-error before the call and inspect it only when the result is 0.

    Any thread.
    """
    ctypes.set_last_error(0)
    value = _get_window_long(hwnd, GWL_EXSTYLE)
    if value == 0:
        err = ctypes.get_last_error()
        if err:
            raise ctypes.WinError(err)
    return int(value) & _EXSTYLE_MASK


def _write_ex_style(hwnd: int, ex_style: int) -> None:
    """Write ``GWL_EXSTYLE``. Raises ``OSError`` on failure. Any thread.

    Same zero-is-ambiguous problem as the read: the return value is the *previous* style,
    which is legitimately 0 for a plain window.
    """
    ctypes.set_last_error(0)
    previous = _set_window_long(hwnd, GWL_EXSTYLE, ex_style & _EXSTYLE_MASK)
    if previous == 0:
        err = ctypes.get_last_error()
        if err:
            raise ctypes.WinError(err)


def _is_window(hwnd: int) -> bool:
    """Whether ``hwnd`` still names a live window. Any thread.

    Worth the extra syscall: Qt destroys and recreates the native window on every
    ``setWindowFlags``, so a stale handle is a realistic caller mistake, and checking here
    turns it into one clear log line instead of an opaque error 1400.
    """
    return hwnd != 0 and bool(_user32.IsWindow(hwnd))


def _idle_seconds() -> float | None:
    """Seconds since the last global keyboard/mouse input, or ``None`` if unavailable.

    Any thread -- ``GetLastInputInfo`` and ``GetTickCount64`` touch no per-thread state and
    take no locks we could contend on. This is what the telemetry worker calls once per tick.
    """
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    ctypes.set_last_error(0)
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        err = ctypes.get_last_error()
        # None, never 0.0. Zero would read as "the user just moved the mouse" and would
        # permanently hold the state machine out of waiting-dozy / waiting-deep-sleep.
        log.debug("GetLastInputInfo failed (error %d); idle time unknown", err)
        return None

    # dwTime is a 32-bit tick; GetTickCount64 is 64-bit. Truncate the latter to 32 bits so the
    # subtraction happens in the same modular arithmetic the OS used, then mask again so a
    # dwTime recorded just before a wrap still yields a small positive delta.
    now32 = int(_kernel32.GetTickCount64()) & 0xFFFF_FFFF
    delta_ms = (now32 - int(info.dwTime)) & 0xFFFF_FFFF
    seconds = delta_ms / 1000.0
    if seconds > _IDLE_SANITY_LIMIT_S:
        # Can only mean dwTime is ahead of the tick count (session/clock oddity), which the
        # mask turns into a ~49-day "idle" period. Unknown beats absurd.
        log.debug("implausible idle time %.0fs; reporting unknown", seconds)
        return None
    return seconds


# =============================================================================
# Backend
# =============================================================================


class WindowsBackend(PlatformBackend):
    """``WS_EX_TRANSPARENT`` click-through plus ``GetLastInputInfo`` idle time.

    Stateless and thread-safe: every call re-reads the window's current extended style from
    the OS rather than caching it, because Qt rewrites that style from scratch whenever it
    recreates the native window.
    """

    name = "windows"

    def supports_click_through(self) -> bool:
        """True -- reaching this class at all means the entry points resolved. Any thread."""
        return True

    def set_click_through(self, handle: WindowHandle, enabled: bool) -> bool:
        """Set or clear ``WS_EX_TRANSPARENT`` on ``handle`` and verify by read-back.

        Returns ``True`` only when the bit is *observed* to match the request afterwards. A
        no-op (the bit already has the requested value) short-circuits before any syscall and
        also returns ``True``: the caller asked for a state, and the window is in it.

        Never raises. Any thread; the GUI thread is the only real caller today.
        """
        try:
            hwnd = int(handle)
            if not _is_window(hwnd):
                log.warning("click-through: %#x is not a live window handle", hwnd)
                return False

            try:
                current = _read_ex_style(hwnd)

                if enabled:
                    desired = current | WS_EX_TRANSPARENT
                    if ADD_LAYERED:
                        desired |= WS_EX_LAYERED
                else:
                    # WS_EX_LAYERED is never cleared here: Qt sets it for the translucent
                    # sprite surface, and taking it away turns the pet into an opaque black
                    # box (or hides it outright).
                    desired = current & ~WS_EX_TRANSPARENT

                desired &= _EXSTYLE_MASK
                if desired == current:
                    log.debug("click-through already %s for %#x", enabled, hwnd)
                    return True

                _write_ex_style(hwnd, desired)

                if ADD_LAYERED and enabled and not (current & WS_EX_LAYERED):
                    # Mandatory companion call: a window that has only just acquired
                    # WS_EX_LAYERED is not painted at all until one of the layered-attribute
                    # setters runs. Fully opaque alpha -- Qt's own per-pixel alpha still
                    # applies through the composited surface.
                    if not _user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA):
                        raise ctypes.WinError(ctypes.get_last_error())

                observed = _read_ex_style(hwnd)
                applied = bool(observed & WS_EX_TRANSPARENT) == bool(enabled)
                if not applied:
                    log.error(
                        "click-through read-back mismatch for %#x: wanted %s, ex-style is %#010x",
                        hwnd, enabled, observed)
                    return False
                log.debug("click-through %s for %#x (ex-style %#010x -> %#010x)",
                          "on" if enabled else "off", hwnd, current, observed)
                return True
            except OSError as exc:
                log.warning("click-through %s failed for %#x: %s",
                            "enable" if enabled else "disable", hwnd, exc)
                return False
        except Exception:  # noqa: BLE001 -- the seam's contract is "never raises"
            log.exception("unexpected failure setting click-through (handle=%r)", handle)
            return False

    def get_idle_seconds(self) -> float | None:
        """Seconds since the last global user input, or ``None``.

        Called from the **telemetry worker thread** once per poll; safe from any thread.
        Never raises.
        """
        try:
            return _idle_seconds()
        except Exception:  # noqa: BLE001
            log.exception("unexpected failure reading idle time")
            return None
