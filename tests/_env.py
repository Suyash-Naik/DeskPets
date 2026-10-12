"""Every assumption this suite makes about the machine it is running on, in one file.

Read this first if a test skips. A suite that only passes on the box it was written on is not
a suite, so anything below that is *not* true everywhere is expressed as a `pytest.mark.skipif`
with a reason string, never as a bare assertion.

The list is deliberately short. Most hardware variation is handled by **constructing synthetic
samples** instead of skipping -- `Metrics(gpu=GpuSample(...))` exercises the GPU card row on a
machine with no GPU at all, which is both more portable and more deterministic than asking the
real driver. Skips are reserved for the three things that genuinely cannot be faked:

1. a real Win32 ``HWND`` and ``user32`` (``WS_EX_TRANSPARENT``, ``GetLastInputInfo``),
2. the Windows notification area (``QSystemTrayIcon.isSystemTrayAvailable()``),
3. the sprite atlas being on disk.

Explicitly **not** assumed, though the dev machine has all four: an NVIDIA GPU, a device pixel
ratio of 1.5, the absence of a battery, and the `Segoe UI Variable Text` font. Tests that would
otherwise depend on those assert *relative* facts (this card is taller than that one) rather
than absolute pixel counts.
"""

from __future__ import annotations

import os
import sys

import pytest

from src import config

# --- platform ------------------------------------------------------------------------------

IS_WINDOWS: bool = sys.platform == "win32"

#: Windows is the only implemented platform seam (`src/core/platform/windows.py`); macOS and
#: Linux are structurally seamed but unimplemented, so their backend is `NullBackend`. Tests
#: that assert a real ex-style bit, or a working `get_idle_seconds`, are Windows-only.
requires_windows = pytest.mark.skipif(
    not IS_WINDOWS,
    reason=f"needs the Win32 platform backend; sys.platform is {sys.platform!r}",
)


# --- assets --------------------------------------------------------------------------------

ATLAS_PRESENT: bool = config.ATLAS_JSON.exists()

#: CLAUDE.md once described the atlas as "incoming, not yet in the repo". It has landed, but a
#: shallow clone or a sparse checkout could still be missing it, and "the art is not here" is a
#: much better failure message than forty red loader tests.
requires_atlas = pytest.mark.skipif(
    not ATLAS_PRESENT,
    reason=f"sprite atlas not on disk at {config.ATLAS_JSON}",
)


# --- desktop shell -------------------------------------------------------------------------


def _tray_available() -> bool:
    """Whether the desktop has a notification area. Needs a live QApplication."""
    try:
        from PyQt6.QtWidgets import QSystemTrayIcon
    except ImportError:  # pragma: no cover - PyQt6 is a hard dependency of the env
        return False
    try:
        return bool(QSystemTrayIcon.isSystemTrayAvailable())
    except Exception:  # pragma: no cover - defensive; Qt should never raise here
        return False


#: At login the app can legitimately start before Explorer, which is why `install_tray` retries
#: at all -- and in a headless CI container there is no notification area ever. Evaluated
#: lazily inside the test, because it needs a QApplication to answer honestly.
def skip_without_tray() -> None:
    """Call from inside a test that needs a real tray icon."""
    if not _tray_available():
        pytest.skip("no system notification area on this desktop "
                    "(QSystemTrayIcon.isSystemTrayAvailable() is False)")


# --- informational -------------------------------------------------------------------------
# Not gates. Printed by `-v` runs and quoted in failure messages so that a number that *did*
# depend on the machine is obvious in the report rather than mysterious.


def describe() -> str:
    """One-line summary of the environment, for failure messages and `pytest -s`."""
    bits = [f"platform={sys.platform}", f"atlas={'yes' if ATLAS_PRESENT else 'no'}"]
    if os.environ.get(config.CONFIG_DIR_ENV):
        bits.append("config-dir=overridden")
    return "  ".join(bits)
