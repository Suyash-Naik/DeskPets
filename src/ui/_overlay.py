"""The one frameless / translucent / always-on-top window recipe.

Both top-levels -- the pet surface and the stats card -- are the same kind of object: a
parentless, shaped, click-anywhere overlay that must not appear in the taskbar or Alt-Tab. The
recipe lives here once so the two cannot drift apart, and so the "why" below is written down
in exactly one place.

Private module (leading underscore): it is an implementation detail of ``src/ui``, not part of
the app's surface. GUI thread only -- everything here touches ``QWidget`` state.
"""

from __future__ import annotations

import logging
from typing import Final

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

log = logging.getLogger(__name__)

#: The shared flag set. Composed once so ``PetWindow.set_flag_override`` can recompose
#: ``BASE_FLAGS | overrides`` from a known base rather than mutating whatever is current.
#:
#: ``Qt.Tool`` is the load-bearing one. The BuildPlan prototype used
#: ``Qt.WindowType.SubWindow``, which is a **defect**: SubWindow is an MDI child type and is
#: meaningless on a parentless top-level, so it neither excludes the window from the taskbar
#: nor changes its z-behaviour. ``Qt.Tool`` maps to ``WS_EX_TOOLWINDOW`` on Windows -- no
#: taskbar button, not in the Alt-Tab list -- which is what a desktop pet actually wants.
#:
#: ``NoDropShadowWindowHint`` suppresses the DWM shadow that would otherwise be cast around the
#: window's *rectangle*, not around the sprite, drawing a hard grey box behind a pet whose own
#: edges are soft.
BASE_FLAGS: Final[Qt.WindowType] = (
    Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.WindowStaysOnTopHint
    | Qt.WindowType.Tool
    | Qt.WindowType.NoDropShadowWindowHint
)


def apply_overlay_flags(widget: QWidget, *, accept_focus: bool = False) -> Qt.WindowType:
    """Apply the frameless/translucent/always-on-top recipe. Returns the flag set applied.

    Must be called BEFORE the first show() -- WA_TranslucentBackground set after the native
    window exists forces Qt to destroy and recreate it, losing position and z-order.
    accept_focus=False adds WindowDoesNotAcceptFocus (the card wants this; the pet does not,
    because it needs a context menu).

    GUI thread only.

    Notes for whoever edits this next:

    * ``WA_NoSystemBackground`` stops the platform from painting the window's own background
      before the ``paintEvent`` runs; without it a translucent surface can flash opaque for one
      frame while it is being mapped.
    * ``WA_ShowWithoutActivating`` keeps ``show()`` from stealing focus from whatever the user
      is typing in. A pet that pulls focus on every unhide is intolerable.
    * ``WA_OpaquePaintEvent`` is deliberately **not** set. It tells Qt to skip erasing the
      widget before each paint, which on a sprite that changes shape every frame leaves ghost
      pixels from the previous frame's silhouette.
    * After construction, ``PetWindow.set_flag_override`` is the only place allowed to change
      flags again (contract 2.2) -- it recomposes from ``BASE_FLAGS`` and re-applies the native
      ex-style, because ``setWindowFlags`` destroys the HWND and Qt recomputes the ex-style
      from scratch.
    """
    if widget.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
        # Not fatal -- Qt will simply recreate the native window -- but the widget will lose its
        # position and z-order, and the cause is invisible at the call site. Say so loudly.
        log.warning("apply_overlay_flags(%s) called after the native window already existed; "
                    "position and z-order will be reset", type(widget).__name__)

    flags = BASE_FLAGS
    if not accept_focus:
        flags |= Qt.WindowType.WindowDoesNotAcceptFocus

    widget.setWindowFlags(flags)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    widget.setAutoFillBackground(False)
    return flags
