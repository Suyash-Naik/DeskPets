"""`src/ui/tray.py` -- the notification-area icon and its menu.

The tray is the only escape hatch from click-through, which makes it a safety component rather
than a convenience one: `MainApp` refuses to enable click-through at all when the tray is
absent. So the tests here are mostly about the menu telling the truth -- a checkbox that shows
*intent* rather than *reality* is how a user ends up with a pet they cannot interact with and no
idea why.

Constructing a real `TrayIcon` needs a notification area, so those tests call
`skip_without_tray()`. Icon construction and the `STATES` coverage of the poke menu do not, and
run everywhere.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PyQt6.QtCore import QObject
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QSystemTrayIcon

from src.core.state_machine import STATES
from src.ui import tray as tray_module
from src.ui.tray import TrayIcon, build_tray_icon, install_tray_async

from ._env import requires_atlas, skip_without_tray


@pytest.fixture
def tray(qapp: object, sheet, qt_cleanup: Callable[[object], object]) -> TrayIcon:
    """A real `TrayIcon`, hidden at teardown so the suite leaves no ghost in the tray."""
    skip_without_tray()
    icon = TrayIcon(build_tray_icon(sheet), None)
    qt_cleanup(icon)
    yield icon
    icon.shutdown()


# =============================================================================================
# The icon
# =============================================================================================


@requires_atlas
def test_the_icon_is_built_from_the_atlas_at_several_sizes(sheet) -> None:
    """Windows picks 16 px for the tray and 32 for the balloon.

    Letting Qt downscale a 192x208 frame to 16 px in one step gives mush, so the icon carries
    pre-rendered pixmaps at 16/24/32/48 from an alpha-trimmed frame 0.
    """
    icon = build_tray_icon(sheet)
    assert not icon.isNull()

    sizes = {(size.width(), size.height()) for size in icon.availableSizes()}
    assert len(sizes) >= 2, f"only one size baked in: {sizes}"
    for wanted in (16, 32):
        assert any(w >= wanted for w, _ in sizes), f"nothing at or above {wanted} px: {sizes}"

    trimmed = icon.pixmap(32, 32)
    assert not trimmed.isNull()


def test_the_icon_falls_back_when_the_atlas_is_unavailable() -> None:
    """The tray must never prevent startup.

    A painted "LC" badge is a worse icon than the cat, and infinitely better than an exception
    on the path that also builds the only way to turn click-through off.
    """
    icon = build_tray_icon(None)
    assert isinstance(icon, QIcon)
    assert not icon.isNull()


# =============================================================================================
# The menu reflects reality
# =============================================================================================


def test_pushing_state_back_does_not_re_emit(tray: TrayIcon) -> None:
    """Every `set_*_checked` wraps the action in a `QSignalBlocker`.

    Without it, `MainApp`'s apply -> read back -> push cycle re-enters itself: pushing the
    applied value toggles the action, which emits, which applies again. The loop is only broken
    by the state happening to converge, which it does not when the apply *failed* -- exactly the
    case the read-back exists for.
    """
    emissions: list[bool] = []
    tray.click_through_toggled.connect(emissions.append)
    tray.pet_visibility_toggled.connect(emissions.append)
    tray.card_visibility_toggled.connect(emissions.append)
    tray.always_on_top_toggled.connect(emissions.append)

    tray.set_click_through_checked(True)
    tray.set_click_through_checked(False)
    tray.set_pet_visible_checked(False)
    tray.set_card_visible_checked(True)
    tray.set_always_on_top_checked(False)

    assert emissions == [], f"pushing state back re-emitted {emissions}"


def test_a_toggle_the_user_flips_does_emit(tray: TrayIcon) -> None:
    """The counterpart: the blocker must not have deafened the real user path."""
    emissions: list[bool] = []
    tray.click_through_toggled.connect(emissions.append)

    tray._act_click_through.setChecked(True)     # what a click on the menu item does
    assert emissions == [True]


def test_an_unavailable_click_through_is_disabled_with_a_readable_reason(
        tray: TrayIcon) -> None:
    """A greyed control with no tooltip is worse than no control.

    Disabling must also force the checkbox off: an unsupported platform showing a ticked
    "Click-through" would claim the pet is in a state it cannot be in.
    """
    tray.set_click_through_available(False, "no Win32 backend on this platform")
    assert tray._act_click_through.isEnabled() is False
    assert "Win32" in tray._act_click_through.toolTip()
    assert tray._act_click_through.isChecked() is False

    tray.set_click_through_available(True)
    assert tray._act_click_through.isEnabled() is True
    # Qt substitutes the action's own text when the tooltip is set to "", so the assertion is
    # "no reason is being shown", not "the string is empty". (A test written as `== ""` fails
    # against correct code -- it reads Qt's fallback as a leftover reason.)
    assert tray._act_click_through.toolTip() == tray._act_click_through.text()


def test_tooltips_are_visible_on_the_menu(tray: TrayIcon) -> None:
    """`setToolTipsVisible(True)`, or the disabled-reason tooltip above is never shown.

    Qt menus suppress action tooltips by default, so the reason string would be written and
    never read.
    """
    assert tray.contextMenu().toolTipsVisible() is True


def test_the_menu_and_its_actions_are_strongly_referenced(tray: TrayIcon) -> None:
    """A `QMenu`/`QAction` referenced only by the menu is a live GC hazard in PyQt.

    The symptom is an intermittently empty context menu -- which, for the component that is the
    escape hatch from click-through, is a trap rather than a cosmetic bug.
    """
    assert tray._menu is not None
    assert tray.contextMenu() is tray._menu
    for attribute in ("_act_click_through", "_act_pet_visible", "_act_card_visible",
                      "_act_always_on_top", "_act_reset", "_act_quit"):
        assert getattr(tray, attribute) is not None, attribute
    assert tray._poke_menu is not None


# =============================================================================================
# Poke menu
# =============================================================================================


def test_the_poke_menu_has_an_entry_for_every_state(tray: TrayIcon) -> None:
    """It is the manual QA path for every animation row while the art is WIP.

    `needs-input-sad` has no telemetry trigger at all, and `blocked-grumpy` may never fire on a
    machine with no temperature source -- the poke menu is the only way either is ever seen.
    """
    assert set(tray._poke_actions) == set(STATES)


def test_poking_emits_the_state_name(tray: TrayIcon) -> None:
    """`poke_requested(str)` carries the name; the state machine decides whether to honour it."""
    emitted: list[str] = []
    tray.poke_requested.connect(emitted.append)
    tray._poke_actions["needs-input-sad"].trigger()
    assert emitted == ["needs-input-sad"]


@requires_atlas
def test_states_without_art_are_greyed_out_in_the_poke_menu(tray: TrayIcon, sheet) -> None:
    """The same "reality, not intent" rule, applied to a state whose row has not landed.

    `StateMachine.trigger_state` refuses an unavailable name anyway, so an enabled menu item
    would be a control that silently does nothing. Greying it says why.
    """
    tray.set_poke_available(sheet.names())

    missing = [state for state in STATES if state not in set(sheet.names())]
    for state in sheet.names():
        if state in tray._poke_actions:
            assert tray._poke_actions[state].isEnabled() is True, state
    for state in missing:
        action = tray._poke_actions[state]
        assert action.isEnabled() is False, f"{state} has no row but is pokeable"
        assert action.toolTip(), f"{state} is greyed with no explanation"

    if not missing:
        pytest.skip("this atlas has a row for every state, so nothing is greyed to check")


# =============================================================================================
# Activation and installation
# =============================================================================================


def test_a_double_click_is_the_second_escape_hatch(tray: TrayIcon) -> None:
    """`DoubleClick` raises the pet and turns click-through off; `Trigger` is ignored.

    Hijacking the single left click makes the right-click menu feel unreliable, which matters
    more than usual here -- that menu is the primary escape hatch.
    """
    activations: list[int] = []
    tray.activated_by_user.connect(lambda: activations.append(1))

    tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    assert activations == [], "a single click must not activate"

    tray.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
    assert activations == [1]


def test_install_tray_async_always_calls_back_exactly_once(
        qapp: object, monkeypatch: pytest.MonkeyPatch, pump: Callable[[int], None]) -> None:
    """The no-tray path: retries, gives up, and hands `None` to the callback.

    The app must still run without a tray -- with click-through permanently unavailable -- so
    the callback firing is what keeps `PetApp.__init__` from waiting forever on a tray that is
    never coming. Retries are collapsed to a short interval so the test does not sit through
    5 x 2 s of real waiting.
    """
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    monkeypatch.setattr(tray_module, "TRAY_RETRY_MS", 5)

    results: list[object] = []
    install_tray_async(QObject(), None, results.append, retries=3)
    pump(200)

    assert results == [None], results
