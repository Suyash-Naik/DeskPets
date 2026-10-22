"""`src/ui/_overlay.py` -- the shared frameless / translucent / always-on-top recipe.

Small module, disproportionately important: both top-levels go through it, and every one of its
settings has a failure mode that only shows up as "the window looks slightly wrong" rather than
as an exception. The `SubWindow` regression below is the headline, but the attribute assertions
matter for the same reason -- `WA_OpaquePaintEvent`, for instance, would leave ghost pixels of
the previous sprite frame and read as an art bug.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

from src.ui._overlay import BASE_FLAGS, apply_overlay_flags

W = Qt.WindowType
A = Qt.WidgetAttribute


@pytest.fixture
def widget(qapp: object, qt_cleanup: Callable[[object], object]) -> QWidget:
    return qt_cleanup(QWidget())  # type: ignore[return-value]


# =============================================================================================
# REGRESSION
# =============================================================================================


def test_regression_the_window_type_is_tool_not_subwindow(widget: QWidget) -> None:
    """Guards `Qt.WindowType.SubWindow` from the BuildPlan prototype.

    `SubWindow` is an **MDI child** type. On a parentless top-level it is an unsupported
    configuration, and it gives none of the taskbar exclusion it was presumably chosen for.
    `Qt.Tool` is the correct one: `WS_EX_TOOLWINDOW`, so no taskbar button and no Alt-Tab entry.
    Two independent planners flagged it, which is decent evidence it is real.

    **The trap, and the reason this test is written the way it is:** window *types* share the
    low byte of the flag word behind `WindowType_Mask`. `SubWindow` is 0x12 and `Tool` is 0x0b,
    so they overlap -- `flags & Qt.WindowType.SubWindow` is truthy on a correctly configured
    `Tool` window, and a test written that way passes on both the bug and the fix. The masked
    comparison is the only meaningful form. Window *hints* (Frameless, StaysOnTop, ...) are real
    bit flags and may be tested with `&` as normal; the test below does exactly that.
    """
    apply_overlay_flags(widget, accept_focus=True)
    window_type = widget.windowFlags() & W.WindowType_Mask

    assert window_type == W.Tool, f"masked window type is {window_type!r}"
    assert window_type != W.SubWindow

    # Demonstrate the trap rather than merely describing it: a bare `&` against SubWindow is
    # truthy here, on a window that is unambiguously a Tool.
    assert widget.windowFlags() & W.SubWindow, (
        "if this ever stops being true the low-byte overlap has changed and the masked "
        "comparison above may no longer be the one that matters")


# =============================================================================================
# The recipe
# =============================================================================================


def test_base_flags_are_the_documented_four() -> None:
    """Contract 2.3 spells out the recipe; this is it, verbatim.

    Needs no widget and no `QApplication` -- `BASE_FLAGS` is a module constant, and asserting it
    against the four hints it is composed from is what catches someone "tidying" the expression.
    """
    assert int(BASE_FLAGS) == int(
        W.FramelessWindowHint | W.WindowStaysOnTopHint | W.Tool | W.NoDropShadowWindowHint)


def test_hints_are_applied_and_the_return_value_matches(widget: QWidget) -> None:
    """`apply_overlay_flags` returns exactly the flag set it applied.

    `PetWindow.set_flag_override` recomposes `BASE_FLAGS | overrides` from that return value, so
    a discrepancy would silently drop a hint on the first flag change rather than at startup.
    """
    returned = apply_overlay_flags(widget, accept_focus=True)
    flags = widget.windowFlags()

    for hint in (W.FramelessWindowHint, W.WindowStaysOnTopHint, W.NoDropShadowWindowHint):
        assert flags & hint, f"{hint.name} not applied"
    assert int(returned) == int(flags)


def test_focus_is_refused_by_default_and_granted_on_request(qapp: object,
                                                            qt_cleanup) -> None:
    """The card wants `WindowDoesNotAcceptFocus`; the pet does not, because it has a menu.

    The default is the card's, so that forgetting the keyword yields the safer window.
    """
    card = qt_cleanup(QWidget())
    card_flags = apply_overlay_flags(card, accept_focus=False)
    assert card_flags & W.WindowDoesNotAcceptFocus
    assert int(card_flags) == int(BASE_FLAGS | W.WindowDoesNotAcceptFocus)

    defaulted = qt_cleanup(QWidget())
    assert apply_overlay_flags(defaulted) & W.WindowDoesNotAcceptFocus, (
        "the no-argument default must be the safer one")

    pet = qt_cleanup(QWidget())
    pet_flags = apply_overlay_flags(pet, accept_focus=True)
    assert not (pet_flags & W.WindowDoesNotAcceptFocus)


def test_translucency_attributes_are_set_and_opaque_paint_is_not(widget: QWidget) -> None:
    """Three attributes on, one deliberately off.

    `WA_OpaquePaintEvent` is the one that is off on purpose: it suppresses the per-paint erase,
    which on a sprite that changes shape every frame leaves ghost pixels of the previous pose.
    That reads as an art bug, not a rendering flag, and is correspondingly hard to trace.
    """
    apply_overlay_flags(widget, accept_focus=True)
    assert widget.testAttribute(A.WA_TranslucentBackground)
    assert widget.testAttribute(A.WA_NoSystemBackground)
    assert widget.testAttribute(A.WA_ShowWithoutActivating)
    assert not widget.testAttribute(A.WA_OpaquePaintEvent)
    assert not widget.autoFillBackground()


def test_flags_and_position_survive_the_first_show(widget: QWidget) -> None:
    """The recipe must be applied *before* the first `show()`.

    `WA_TranslucentBackground` set after the native window exists forces Qt to destroy and
    recreate it, losing position and z-order -- which is why `apply_overlay_flags` warns when
    called late rather than quietly doing the wrong thing.
    """
    returned = apply_overlay_flags(widget, accept_focus=True)
    widget.resize(192, 208)
    widget.move(80, 80)
    widget.show()
    QApplication.processEvents()

    assert widget.isVisible()
    assert widget.testAttribute(A.WA_WState_Created), "no native window was created"
    assert int(widget.windowFlags()) == int(returned)
    assert (widget.x(), widget.y()) == (80, 80)


def test_a_late_call_warns_instead_of_raising(widget: QWidget,
                                              caplog: pytest.LogCaptureFixture) -> None:
    """Calling it after `show()` is a mistake, but not one worth killing the app over."""
    import logging

    apply_overlay_flags(widget, accept_focus=True)
    widget.show()
    QApplication.processEvents()

    with caplog.at_level(logging.WARNING, logger="src.ui._overlay"):
        apply_overlay_flags(widget)

    assert caplog.records, "a late call should warn"
