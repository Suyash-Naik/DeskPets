"""`src/ui/pet_window.py` -- the pet surface: rendering, geometry, input, native state.

Mouse behaviour is driven through real synthesised `QMouseEvent`s posted at the widget, not by
calling `mousePressEvent` directly with a hand-built object. That matters: the drag threshold is
read from `QApplication.startDragDistance()` and compared against *global* coordinates, and the
press is gated on alpha hit-testing of the *local* position, so an event with only one of the
two populated would exercise half the logic and pass.

The opaque press point is found by scanning the current frame's alpha rather than assumed. A
hard-coded "the middle of the window" is exactly the kind of thing that silently starts landing
in the transparent gutter when the art changes, turning every input test into a no-op that
still passes.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PyQt6.QtGui import QEnterEvent, QGuiApplication, QMouseEvent
from PyQt6.QtWidgets import QApplication

from src import config
from src.ui.pet_window import (PetWindow, clamp_to_desktop, default_position, is_position_valid,
                               virtual_bounds)

from . import _win32
from ._env import requires_atlas, requires_windows

pytestmark = requires_atlas


# --- fixtures and helpers ----------------------------------------------------------------------


@pytest.fixture
def pet(sheet, qt_cleanup: Callable[[object], object], pump: Callable[[int], None]) -> PetWindow:
    """A shown `PetWindow` on the primary screen, hidden and deleted at teardown.

    Shown rather than merely constructed: half of what this module tests -- the native ex-style,
    `pet_moved` from `showEvent`, hit-testing against a painted frame -- does not exist until
    the window has been mapped.
    """
    window = PetWindow(sheet)
    qt_cleanup(window)
    available = QGuiApplication.primaryScreen().availableGeometry()
    window.move(available.left() + 200, available.top() + 200)
    window.show()
    pump(60)
    return window


def opaque_point(window: PetWindow) -> QPoint:
    """A widget-local point that is genuinely part of the pet.

    Scans outward from the centre rather than trusting it: the pet does not fill its cell, and
    a point in the transparent gutter would make every press silently `ignore()`d -- which is
    correct behaviour, and would make the drag tests pass while testing nothing.
    """
    centre = QPoint(window.width() // 2, window.height() // 2)
    if window.is_opaque_at(centre):
        return centre
    for radius in range(1, max(window.width(), window.height())):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                candidate = QPoint(centre.x() + dx, centre.y() + dy)
                if (0 <= candidate.x() < window.width()
                        and 0 <= candidate.y() < window.height()
                        and window.is_opaque_at(candidate)):
                    return candidate
    pytest.fail("no opaque pixel anywhere in the frame -- the pet did not render")


def send_mouse(window: PetWindow, kind: QEvent.Type, local: QPoint, *,
               button: Qt.MouseButton, buttons: Qt.MouseButton) -> QMouseEvent:
    """Post a real `QMouseEvent` with both local and global positions populated."""
    event = QMouseEvent(kind, QPointF(local), QPointF(window.mapToGlobal(local)),
                        button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(window, event)
    return event


def gesture(window: PetWindow, origin: QPoint, dx: int, dy: int,
            pump: Callable[[int], None]) -> tuple[QPoint, QPoint]:
    """Press at `origin`, move by (dx, dy), release. Returns the window position before/after."""
    before = QPoint(window.frameGeometry().topLeft())
    send_mouse(window, QEvent.Type.MouseButtonPress, origin,
               button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
    send_mouse(window, QEvent.Type.MouseMove, origin + QPoint(dx, dy),
               button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.LeftButton)
    send_mouse(window, QEvent.Type.MouseButtonRelease, origin + QPoint(dx, dy),
               button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.NoButton)
    pump(30)
    return before, QPoint(window.frameGeometry().topLeft())


# =============================================================================================
# REGRESSIONS
# =============================================================================================


def test_regression_the_window_is_exactly_the_atlas_frame_box(pet: PetWindow, sheet) -> None:
    """Guards `QLabel` + `QVBoxLayout` and its invisible 9 px content margins.

    PHASE1-LOG.md defect 4: the prototype put a `QLabel` in a `QVBoxLayout`, and the style's
    default content margins silently inflated the window to 210x226 with the pet off-centre in
    it. Nothing errors; the pet is just wrong by 18 px in each axis, and the translucent
    background makes that invisible until you measure it.

    The fix is a direct `paintEvent` with no child widget and no layout. Asserted against the
    atlas's own frame box, never against the literal 192x208 -- the contract forbids sprite
    dimensions as literals in `src/`, and a test that pinned them would have to be edited the
    first time the art is repacked. The margin signature (`box + 18`) is checked explicitly so
    that a failure names the actual historical bug rather than just a size mismatch.
    """
    box = QSize(sheet.frame_box)
    expected = QSize(box.width() * pet.render_scale(), box.height() * pet.render_scale())

    assert pet.size() == expected, (
        f"{pet.size().width()}x{pet.size().height()} vs frame box "
        f"{expected.width()}x{expected.height()}")
    assert pet.size() != QSize(box.width() + 18, box.height() + 18), (
        "the window is the frame box plus 9 px on all four sides -- the QLabel/QVBoxLayout "
        "content margins are back")
    assert pet.layout() is None, "a layout will reintroduce the margins"
    assert not pet.findChildren(object, options=Qt.FindChildOption.FindDirectChildrenOnly), (
        "the pet surface must have no child widgets")


@requires_windows
@pytest.mark.windows
def test_regression_click_through_survives_a_flag_change(
        pet: PetWindow, pump: Callable[[int], None]) -> None:
    """`WS_EX_TRANSPARENT` must survive `set_always_on_top(False)` then `(True)`.

    `setWindowFlags` destroys the native window and Qt recomputes the ex-style from scratch, so
    the click-through bit is silently lost on every flag change unless `_apply_native_state()`
    re-applies it afterwards. Contract 2.2 makes `set_flag_override` the only mutation path for
    exactly this reason.

    **And the measured detail that makes the obvious optimisation wrong:** after
    `hide(); setWindowFlag(...); show()` the handle came back as the *identical* value
    `0x9318ac` while the ex-style had been rebuilt from `0x100` to `0x0` -- Windows recycled the
    handle value into the recreated window. So the re-apply is unconditional: a cache keyed on
    "has the handle changed?" would look valid while being stale, and would never show up in
    testing. This test therefore does not care whether the handle changed; it only checks the
    bit.

    Read back through `tests/_win32.py`, an independent ctypes implementation -- asking
    `pet.is_click_through()` would only be asking the module what it believes about itself.
    """
    assert pet.set_click_through(True) is True
    assert pet.is_click_through() is True
    before = _win32.ex_style(int(pet.winId()))
    assert before & _win32.WS_EX_TRANSPARENT, _win32.describe(before)

    pet.set_always_on_top(False)
    pump(60)
    after_off = _win32.ex_style(int(pet.winId()))
    assert after_off & _win32.WS_EX_TRANSPARENT, (
        f"lost across set_always_on_top(False): {_win32.describe(before)} -> "
        f"{_win32.describe(after_off)}")

    pet.set_always_on_top(True)
    pump(60)
    after_on = _win32.ex_style(int(pet.winId()))
    assert after_on & _win32.WS_EX_TRANSPARENT, (
        f"lost across set_always_on_top(True): {_win32.describe(after_off)} -> "
        f"{_win32.describe(after_on)}")

    assert pet.is_click_through() is True
    assert pet.window_flags() & Qt.WindowType.WindowStaysOnTopHint
    assert pet.window_flags() & Qt.WindowType.WindowType_Mask == Qt.WindowType.Tool

    pet.set_click_through(False)
    pump(30)
    assert not (_win32.ex_style(int(pet.winId())) & _win32.WS_EX_TRANSPARENT)


@requires_windows
@pytest.mark.windows
def test_click_through_survives_ten_toggles(pet: PetWindow, pump: Callable[[int], None]) -> None:
    """The measured case: ten enable/disable cycles, the bit correct every time.

    One toggle can pass by luck of timing; ten cannot. PHASE1-LOG.md records
    `0x800a8 -> 0x800a0 -> 0x800a8` with `WS_EX_TRANSPARENT` never lost across ten toggles.
    """
    for i in range(10):
        assert pet.set_click_through(True) is True, f"enable {i}"
        assert _win32.ex_style(int(pet.winId())) & _win32.WS_EX_TRANSPARENT, f"enable {i}"
        assert pet.set_click_through(False) is True, f"disable {i}"
        assert not (_win32.ex_style(int(pet.winId())) & _win32.WS_EX_TRANSPARENT), f"disable {i}"
    pump(20)


# =============================================================================================
# Rendering
# =============================================================================================


def test_the_first_frame_is_primed_before_the_animator_ticks(pet: PetWindow) -> None:
    """Without priming, the very first click on a freshly shown pet falls through.

    The window would be blank for one event-loop turn, and blank means `is_opaque_at` reports
    transparent everywhere -- so `mousePressEvent` ignores the press and the user's first
    interaction silently does nothing.
    """
    assert pet.is_opaque_at(opaque_point(pet))


def test_set_frame_repaints_without_raising(pet: PetWindow, sheet,
                                            pump: Callable[[int], None]) -> None:
    """The slot the animator calls ~12 times a second. Cheap, and must never throw.

    `grab()` forces a real `paintEvent`; a uniform grab would mean nothing was drawn.
    """
    animation = sheet[sheet.default_animation]
    frame = animation.frames[min(2, len(animation) - 1)]
    pet.set_frame(frame.pixmap, frame.offset)
    pet.repaint()
    pump(20)

    image = pet.grab().toImage()
    distinct = {image.pixel(x, y)
                for y in range(0, image.height(), 7)
                for x in range(0, image.width(), 7)}
    assert len(distinct) > 1, "the grabbed surface is uniform -- nothing was painted"


def test_render_scale_is_an_integer_and_resizes_the_window(pet: PetWindow, sheet) -> None:
    """Contract 2.5: integer upscale only, clamped to `MAX_RENDER_SCALE`."""
    box = QSize(sheet.frame_box)
    pet.set_render_scale(2)
    assert pet.render_scale() == 2
    assert pet.size() == QSize(box.width() * 2, box.height() * 2)

    pet.set_render_scale(99)
    assert pet.render_scale() == config.MAX_RENDER_SCALE
    pet.set_render_scale(0)
    assert pet.render_scale() == 1
    assert pet.size() == QSize(box.width(), box.height())


def test_frame_box_reports_the_unscaled_atlas_box(pet: PetWindow, sheet) -> None:
    """`frame_box()` is the atlas's, independent of render scale; `size()` is the scaled one."""
    pet.set_render_scale(2)
    assert pet.frame_box() == sheet.frame_box
    pet.set_render_scale(1)


# =============================================================================================
# Hit-testing
# =============================================================================================


def test_alpha_hit_testing_distinguishes_the_pet_from_its_gutter(pet: PetWindow) -> None:
    """Hit-testing is by alpha, not by rect.

    Measured: the opaque bbox is `(31, 73, 129, 110)` inside the cell, i.e. a ~25 px fully
    transparent gutter below the feet and 20-45 px at the sides. Rect hit-testing would give
    the pet an invisible pedestal that swallows clicks aimed at the desktop behind it.
    """
    assert pet.alpha_at_local(QPoint(0, 0)) == 0
    assert not pet.is_opaque_at(QPoint(1, 1))
    assert pet.is_opaque_at(opaque_point(pet))

    # Out of range must be 0, not an exception -- this runs inside mousePressEvent.
    assert pet.alpha_at_local(QPoint(-5, -5)) == 0
    assert pet.alpha_at_local(QPoint(10_000, 10_000)) == 0


def test_a_press_on_a_transparent_pixel_is_declined(pet: PetWindow) -> None:
    """`ignore()`, not swallowed: the user is aiming at whatever is underneath the window."""
    event = send_mouse(pet, QEvent.Type.MouseButtonPress, QPoint(1, 1),
                       button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
    assert not event.isAccepted()
    assert pet._press_origin is None, "a declined press must not arm the drag state"


# =============================================================================================
# Click vs drag
# =============================================================================================


def test_a_sub_threshold_gesture_is_a_click_not_a_drag(
        pet: PetWindow, pump: Callable[[int], None]) -> None:
    """Contract 2.6: distance only, via `QApplication.startDragDistance()`.

    No time limit, deliberately -- a slow, deliberate click is still a click, and a time limit
    makes the pet feel broken when you pause mid-press.
    """
    clicks: list[int] = []
    drags: list[int] = []
    pet.clicked.connect(lambda: clicks.append(1))
    pet.drag_started.connect(lambda: drags.append(1))

    before, after = gesture(pet, opaque_point(pet), 1, 1, pump)

    assert before == after, f"a 1 px gesture moved the window {before} -> {after}"
    assert len(clicks) == 1
    assert not drags


def test_a_supra_threshold_gesture_moves_the_window_and_emits_drag_signals(
        pet: PetWindow, pump: Callable[[int], None]) -> None:
    """Past the platform threshold it is a drag: the window follows and `clicked` stays silent.

    The window is moved by the delta from a `_press_origin` captured once at press, never by
    accumulating per-move deltas -- which is what stops a long drag from drifting away from the
    cursor.
    """
    clicks: list[int] = []
    started: list[int] = []
    finished: list[int] = []
    moved: list[QRect] = []
    pet.clicked.connect(lambda: clicks.append(1))
    pet.drag_started.connect(lambda: started.append(1))
    pet.drag_finished.connect(lambda: finished.append(1))
    pet.pet_moved.connect(lambda rect: moved.append(QRect(rect)))

    distance = QApplication.startDragDistance() + 40
    before, after = gesture(pet, opaque_point(pet), distance, distance, pump)

    assert after == before + QPoint(distance, distance), f"{before} -> {after}"
    assert started == [1]
    assert finished == [1]
    assert not clicks
    assert moved and moved[-1] == pet.frameGeometry(), (
        "pet_moved must carry the global frame geometry the card anchors to")


def test_a_press_with_a_second_button_held_still_drags(
        pet: PetWindow, pump: Callable[[int], None]) -> None:
    """`event.buttons() & LeftButton`, never `== LeftButton`.

    The equality form is False the moment a second button -- or, on some platforms, a modifier
    -- is held, which silently kills the drag mid-gesture with no error anywhere.
    """
    origin = opaque_point(pet)
    both = Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton
    before = QPoint(pet.frameGeometry().topLeft())

    send_mouse(pet, QEvent.Type.MouseButtonPress, origin,
               button=Qt.MouseButton.LeftButton, buttons=both)
    distance = QApplication.startDragDistance() + 30
    send_mouse(pet, QEvent.Type.MouseMove, origin + QPoint(distance, 0),
               button=Qt.MouseButton.NoButton, buttons=both)
    send_mouse(pet, QEvent.Type.MouseButtonRelease, origin + QPoint(distance, 0),
               button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.RightButton)
    pump(30)

    assert pet.frameGeometry().topLeft() == before + QPoint(distance, 0)


# =============================================================================================
# Hover and interactivity
# =============================================================================================


def test_hover_is_reported_when_interactive_and_suppressed_when_click_through(
        pet: PetWindow, pump: Callable[[int], None]) -> None:
    """`hover_changed(False)` unconditionally while click-through is on.

    The OS stops routing the cursor to us, so a latched `True` would leave the card stuck open
    with nothing able to retract it. Enabling click-through must therefore *retract* an existing
    hover, not merely stop new ones.
    """
    point = opaque_point(pet)
    hovers: list[bool] = []
    pet.hover_changed.connect(hovers.append)

    pet.set_click_through(False)
    pump(20)
    hovers.clear()
    QApplication.sendEvent(pet, QEnterEvent(
        QPointF(point), QPointF(point), QPointF(pet.mapToGlobal(point))))
    pump(20)
    assert hovers == [True], hovers

    hovers.clear()
    if pet.set_click_through(True):
        pump(20)
        assert hovers == [False], f"enabling click-through must retract the hover: {hovers}"
        pet.set_click_through(False)
    else:
        pytest.skip("click-through is unsupported on this platform")


def test_is_interactive_tracks_click_through(pet: PetWindow, pump: Callable[[int], None]) -> None:
    """"Interactive" is the inverse of click-through, and drives whether presses are read."""
    pet.set_click_through(False)
    assert pet.is_interactive() is True
    if not pet.set_click_through(True):
        pytest.skip("click-through is unsupported on this platform")
    pump(20)
    assert pet.is_interactive() is False

    event = send_mouse(pet, QEvent.Type.MouseButtonPress, opaque_point(pet),
                       button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
    assert not event.isAccepted(), "a click-through pet must not consume presses"
    pet.set_click_through(False)


# =============================================================================================
# Flags
# =============================================================================================


def test_set_flag_override_preserves_geometry_and_visibility(
        pet: PetWindow, pump: Callable[[int], None]) -> None:
    """Contract 2.2: capture geometry and visibility, recompose, restore.

    `setWindowFlags` on a visible window hides it and resets its position, so without the
    capture/restore the pet would teleport to the top-left corner and vanish every time the
    tray's "Always on top" was toggled.
    """
    geometry = QRect(pet.geometry())
    visible = pet.isVisible()

    pet.set_flag_override(Qt.WindowType.WindowStaysOnTopHint, False)
    pump(60)
    assert pet.geometry() == geometry, f"{geometry} -> {pet.geometry()}"
    assert pet.isVisible() == visible

    pet.set_flag_override(Qt.WindowType.WindowStaysOnTopHint, True)
    pump(60)
    assert pet.geometry() == geometry
    assert pet.isVisible() == visible
    assert pet.window_flags() & Qt.WindowType.WindowStaysOnTopHint


def test_overrides_compose_rather_than_clobber(pet: PetWindow, pump: Callable[[int], None]) -> None:
    """Two overrides at once: setting the second must not discard the first.

    `_flag_overrides` is a dict replayed over `BASE_FLAGS` on every change, which is what makes
    this true. A naive implementation that composed from the *current* flags would work for one
    override and drift after two.
    """
    pet.set_flag_override(Qt.WindowType.WindowStaysOnTopHint, False)
    pump(40)
    pet.set_flag_override(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    pump(40)

    flags = pet.window_flags()
    assert not (flags & Qt.WindowType.WindowStaysOnTopHint), "the first override was lost"
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    assert flags & Qt.WindowType.WindowType_Mask == Qt.WindowType.Tool

    pet.set_flag_override(Qt.WindowType.WindowDoesNotAcceptFocus, False)
    pet.set_flag_override(Qt.WindowType.WindowStaysOnTopHint, True)
    pump(40)


# =============================================================================================
# Geometry helpers -- pure, no window needed
# =============================================================================================


def test_clamp_to_desktop_keeps_the_pet_partly_on_screen(sheet) -> None:
    """The loose clamp, used *during* a drag: permits crossing monitor seams, refuses escape.

    `MIN_VISIBLE_PX` of the pet must remain on some screen, so a drag cannot lose it entirely.
    """
    box = QSize(sheet.frame_box)
    bounds = virtual_bounds()
    needed = (min(config.MIN_VISIBLE_PX, box.width()), min(config.MIN_VISIBLE_PX, box.height()))

    for extreme in (QPoint(99_999, 99_999), QPoint(-99_999, -99_999)):
        clamped = clamp_to_desktop(extreme, box)
        overlap = QRect(clamped, box).intersected(bounds)
        assert overlap.width() >= needed[0] and overlap.height() >= needed[1], (
            f"{extreme} clamped to {clamped}, overlap {overlap.width()}x{overlap.height()}")

    interior = QPoint(bounds.left() + 300, bounds.top() + 300)
    assert clamp_to_desktop(interior, box) == interior, "an interior point must be left alone"


def test_is_position_valid_is_stricter_than_the_clamp(sheet) -> None:
    """The strict test, used when *restoring*: a pet never comes back on a vanished monitor.

    Two different questions deserve two different functions. During a drag the user is actively
    holding the pet and may reasonably hang it off an edge; on restore, a saved position from a
    monitor that no longer exists must be rejected outright and the default used instead.
    """
    box = QSize(sheet.frame_box)
    assert not is_position_valid(QPoint(-99_999, -99_999), box)

    primary = QGuiApplication.primaryScreen().availableGeometry()
    assert is_position_valid(QPoint(primary.left() + 100, primary.top() + 100), box)


def test_default_position_lands_inside_the_primary_screen(sheet) -> None:
    """Fully inside `availableGeometry`, so the first run never starts under the taskbar."""
    box = QSize(sheet.frame_box)
    primary = QGuiApplication.primaryScreen().availableGeometry()
    assert primary.contains(QRect(default_position(box), box))


def test_restore_position_falls_back_when_the_saved_point_is_gone(
        pet: PetWindow, sheet, pump: Callable[[int], None]) -> None:
    """A position from a disconnected monitor is replaced by the default, not clamped to it."""
    pet.restore_position(config.Settings(pos_x=-99_999, pos_y=-99_999))
    pump(40)
    assert pet.frameGeometry().topLeft() == default_position(pet.size())

    primary = QGuiApplication.primaryScreen().availableGeometry()
    good = config.Settings(pos_x=primary.left() + 150, pos_y=primary.top() + 150)
    pet.restore_position(good)
    pump(40)
    assert pet.frameGeometry().topLeft() == QPoint(good.pos_x, good.pos_y)


# =============================================================================================
# Signals
# =============================================================================================


def test_pet_moved_fires_on_show_as_well_as_on_move(
        pet: PetWindow, pump: Callable[[int], None]) -> None:
    """Contract 2.4: one signal, from `moveEvent` *and* once from `showEvent`.

    The `showEvent` emission is what gives the card an anchor before the first hover -- without
    it the card's first peek flashes at (0, 0).
    """
    emissions: list[QRect] = []
    pet.pet_moved.connect(lambda rect: emissions.append(QRect(rect)))

    pet.hide()
    pump(40)
    emissions.clear()
    pet.show()
    pump(80)

    assert emissions, "no pet_moved on show()"
    assert emissions[-1] == pet.frameGeometry()


def test_visibility_changed_reports_shown_not_paused(
        pet: PetWindow, pump: Callable[[int], None]) -> None:
    """`visibility_changed(True)` means SHOWN. See the integration test for why this matters.

    The sense is the opposite of `Animator.set_paused(True)`, which means *stop*. Connecting the
    two directly freezes the pet the instant it becomes visible -- PHASE1-LOG.md bug 2.
    """
    seen: list[bool] = []
    pet.visibility_changed.connect(seen.append)

    pet.hide()
    pump(40)
    pet.show()
    pump(40)

    assert seen[-2:] == [False, True], seen
