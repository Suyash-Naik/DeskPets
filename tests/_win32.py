"""An **independent** ctypes read-back of a window's extended style.

Deliberately a second, separate implementation of the same Win32 call that
`src/core/platform/windows.py` makes. A test that asked the module under test what the module
under test had written would pass just as happily if it wrote nothing at all -- the whole value
of the click-through regression test is that the bit is read by code that shares nothing with
the code that set it.

Kept minimal and literal: the constants are spelled out, `argtypes`/`restype` are declared (an
HWND on win64 exceeds 32 bits and ctypes truncates it silently without them), and the
zero-return ambiguity is resolved with `use_last_error` exactly as the real module does -- for
the same reason, not by copying it.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000

if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    # SetWindowLongPtrW/GetWindowLongPtrW are only *exported* on 64-bit user32; on 32-bit they
    # are macros, hence the fallback.
    _get_long = getattr(_user32, "GetWindowLongPtrW", None) or _user32.GetWindowLongW
    _get_long.argtypes = (wintypes.HWND, ctypes.c_int)
    _get_long.restype = ctypes.c_ssize_t
else:  # pragma: no cover - the tests that use this are skipped off Windows
    _get_long = None


def ex_style(hwnd: int) -> int:
    """Read `GWL_EXSTYLE` for `hwnd`. Raises `OSError` if the call genuinely failed.

    `GetWindowLongPtrW` returns 0 both for "the ex-style is zero" and for failure, so the
    last-error dance is the only way to tell a valid zero from a bad handle.
    """
    if _get_long is None:  # pragma: no cover
        raise RuntimeError("ex_style() is Windows-only")
    ctypes.set_last_error(0)
    value = _get_long(wintypes.HWND(hwnd), GWL_EXSTYLE)
    if value == 0:
        err = ctypes.get_last_error()
        if err:
            raise OSError(f"GetWindowLongPtrW failed for hwnd {hwnd:#x}: error {err}")
    return int(value) & 0xFFFFFFFF


def describe(value: int) -> str:
    """Human-readable ex-style, for failure messages."""
    bits = []
    for name, mask in (("TRANSPARENT", WS_EX_TRANSPARENT),
                       ("TOOLWINDOW", WS_EX_TOOLWINDOW),
                       ("LAYERED", WS_EX_LAYERED)):
        if value & mask:
            bits.append(name)
    return f"{value:#010x}" + (f" [{' '.join(bits)}]" if bits else "")
