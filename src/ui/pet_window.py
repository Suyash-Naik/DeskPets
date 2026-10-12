"""The pet surface: the thing on screen, and every mouse gesture that lands on it.

This is the most interface-dense module in the project -- it is simultaneously the render
target for ``Animator.frame_changed``, the drag handle, the hover source for the stats card,
and the Qt-side owner of the native click-through ex-style. The contract for all of it is
``docs/PHASE1-CONTRACT.md`` section 5.1.

Four decisions in here look like style and are not:

**No ``QLabel``, no layout.** A custom ``paintEvent`` draws the pixmap directly. The BuildPlan
prototype's ``QLabel`` inside a ``QVBoxLayout`` silently inherits the style's 9 px content
margins on all four sides, which turns a 192x208 atlas frame into a 210x226 window with the
pet off-centre and a 9 px dead border that still eats clicks. Measured, not theorised.

**Nearest-neighbour, always** (``config.SMOOTH_SCALING`` is False). The atlas records
``transform.filter: "nearest"`` -- the art was already resampled 0.6x with a hard filter when
it was packed. Smoothing at render time would blur a resample the pipeline deliberately kept
crisp.

**Hit-testing is by alpha, not by rect.** Every frame carries a ~20-25 px fully transparent
gutter below the pet's feet and 20-45 px at the sides. Rect hit-testing would let the user
drag the pet by empty air and would swallow clicks meant for the icon underneath, so
``mousePressEvent`` ignores any press over a transparent pixel and lets it fall through.

**The native ex-style is re-applied unconditionally**, never gated on the window handle having
changed. See ``_apply_native_state`` for the measurement that forces this.

GUI thread only -- every public method here touches ``QWidget`` state. ``set_frame`` is a slot
for a signal emitted on the GUI thread by ``Animator``; it is not safe to call directly from
the telemetry worker.
"""

from __future__ import annotations

import logging
from typing import Final

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import (
    QEnterEvent,
    QHideEvent,
    QImage,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QScreen,
    QShowEvent,
)
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication, QWidget

from .. import config
from ..core import platform
from ..core.sprite_loader import SpriteSheet
from ._overlay import BASE_FLAGS, apply_overlay_flags

log = logging.getLogger(__name__)

__all__ = [
    "PetWindow",
    "virtual_bounds",
    "default_position",
    "clamp_to_desktop",
    "is_position_valid",
    "screen_named",
]

#: Fallback desktop rectangle when Qt reports no screens at all (headless CI, a session with
#: every display asleep). Only ever used to keep the pure helpers total -- never seen by a user.
_NO_SCREEN_FALLBACK: Final[QRect] = QRect(0, 0, 1920, 1080)


# =============================================================================
# PURE GEOMETRY HELPERS
#
# Module-level and window-free on purpose (contract 5.1): they are the part of this file with
# real edge cases -- monitor seams, a display unplugged while the pet sits on it, a negative
# virtual-desktop origin when the secondary monitor is to the left of the primary -- and they
# must be unit-testable without constructing a PetWindow.
#
# They need a QGuiApplication (they read QScreen), but no window and no PetWindow instance.
# =============================================================================


def virtual_bounds() -> QRect:
    """Bounding rectangle of the whole virtual desktop, in global logical pixels.

    Full ``QScreen.geometry()``, not ``availableGeometry()``: a desktop pet is allowed to stand
    in front of the taskbar, and clamping it out of that strip during a drag would feel like
    the window fighting the cursor. ``availableGeometry`` is used for *placement*
    (``default_position``) and for *validity* (``is_position_valid``) instead, where staying
    clear of the taskbar is what the user wants.

    Note this is the bounding box, so on an L-shaped multi-monitor arrangement it includes
    dead space between the screens. That is deliberate for the loose drag clamp; the strict
    check is per-screen.

    GUI thread. Requires a ``QGuiApplication``; returns a fallback rect if there are no
    screens, so callers never have to handle an invalid ``QRect``.
    """
    screens = QGuiApplication.screens()
    if not screens:
        log.warning("QGuiApplication reports no screens; using the fallback desktop rect")
        return QRect(_NO_SCREEN_FALLBACK)

    rect = QRect(screens[0].geometry())
    for screen in screens[1:]:
        rect = rect.united(screen.geometry())
    return rect


def screen_named(name: str | None) -> QScreen | None:
    """The ``QScreen`` whose ``name()`` matches, or ``None``.

    A persisted screen name is a *hint*, not an identity: display names are not stable across
    driver updates or a change of cable, so every caller must cope with ``None``. GUI thread.
    """
    if not name:
        return None
    for screen in QGuiApplication.screens():
        if screen.name() == name:
            return screen
    return None


def default_position(size: QSize, screen: QScreen | None = None) -> QPoint:
    """Where a pet with no saved position goes: a corner of ``screen``'s work area.

    Corner and margin come from ``config.WINDOW_START_CORNER`` /
    ``config.WINDOW_START_MARGIN_PX``. ``availableGeometry`` (not ``geometry``) so the initial
    placement never lands underneath the taskbar -- a first-run pet hidden behind the taskbar
    reads as "the app did not start".

    GUI thread. Falls back to the primary screen, then to the fallback rect.
    """
    target = screen or QGuiApplication.primaryScreen()
    area = QRect(target.availableGeometry()) if target is not None else QRect(_NO_SCREEN_FALLBACK)

    margin = config.WINDOW_START_MARGIN_PX
    corner = config.WINDOW_START_CORNER
    if corner not in ("top-left", "top-right", "bottom-left", "bottom-right"):
        log.warning("config.WINDOW_START_CORNER=%r is not a recognised corner; "
                    "using 'bottom-right'", corner)
        corner = "bottom-right"

    vertical, _, horizontal = corner.partition("-")
    # right()/bottom() are the *last pixel inside* in Qt, hence the +1 before subtracting.
    x = (area.left() + margin) if horizontal == "left" else (area.right() + 1 - margin - size.width())
    y = (area.top() + margin) if vertical == "top" else (area.bottom() + 1 - margin - size.height())

    # A pet larger than the work area (absurd scale on a small screen) would otherwise be
    # placed at a negative offset from the screen origin and look like it had gone missing.
    x = max(area.left(), x)
    y = max(area.top(), y)
    return QPoint(x, y)


def clamp_to_desktop(pos: QPoint, size: QSize) -> QPoint:
    """**Loose** clamp, used *during* a drag. Keeps ``MIN_VISIBLE_PX`` on the virtual desktop.

    Loose means two things, both deliberate:

    * it tests against the *bounding box* of all screens, so the pet may be dragged across a
      monitor seam and may hang over the dead space of a non-rectangular arrangement. A strict
      per-screen clamp there would make the window stutter or jump sideways exactly at the
      seam, which is the moment the user is paying most attention to it;
    * it permits the pet to hang partly off an outer edge, down to ``MIN_VISIBLE_PX`` of
      remaining window. Half a cat peeking off the edge of the screen is a feature.

    The strict counterpart is ``is_position_valid``, used when *restoring* a position.

    Pure apart from reading ``QScreen``. GUI thread; no window needed.
    """
    bounds = virtual_bounds()
    # A window smaller than MIN_VISIBLE_PX must not be clamped to more than it has, or the
    # bounds invert and the pet is pinned to a corner.
    keep_w = min(config.MIN_VISIBLE_PX, size.width())
    keep_h = min(config.MIN_VISIBLE_PX, size.height())

    min_x = bounds.left() - (size.width() - keep_w)
    max_x = bounds.right() + 1 - keep_w
    min_y = bounds.top() - (size.height() - keep_h)
    max_y = bounds.bottom() + 1 - keep_h

    return QPoint(max(min_x, min(pos.x(), max_x)),
                  max(min_y, min(pos.y(), max_y)))


def is_position_valid(pos: QPoint, size: QSize) -> bool:
    """**Strict** check, used when *restoring* a saved position.

    ``True`` only when the window rectangle overlaps the work area of one **single real
    screen** by at least ``MIN_VISIBLE_PX`` in both directions. Per-screen, not against the
    bounding box, because the bounding box includes dead space: a pet saved on a second
    monitor that has since been unplugged would test as "inside the virtual desktop" and come
    back invisible, which is indistinguishable from the app failing to start.

    Pure apart from reading ``QScreen``. GUI thread; no window needed.
    """
    need_w = min(config.MIN_VISIBLE_PX, size.width())
    need_h = min(config.MIN_VISIBLE_PX, size.height())
    rect = QRect(pos, size)
    for screen in QGuiApplication.screens():
        overlap = rect.intersected(screen.availableGeometry())
        if overlap.width() >= need_w and overlap.height() >= need_h:
            return True
    return False


# =============================================================================
# THE WINDOW
# =============================================================================


class PetWindow(QWidget):
    """The frameless, translucent, always-on-top sprite surface.

    Construct on the **GUI thread**, after a ``QApplication`` exists and after
    ``load_spritesheet()`` has produced the sheet. Every method below is GUI thread only.

    Signals (contract 5.1; ``snake_case`` per contract 1):

    ``pet_moved(QRect)``
        Global frame geometry. Emitted from ``moveEvent`` and once from ``showEvent`` so the
        card can anchor itself before the first hover. One signal, not two -- contract 2.4.
    ``clicked()``
        A press and release that never exceeded the drag threshold.
    ``drag_started()`` / ``drag_finished()``
        ``drag_finished`` is a "persist now" nudge and carries no position; read it from
        ``current_position()`` or from the last ``pet_moved``.
    ``hover_changed(bool)``
        ``False`` unconditionally while click-through is on.
    ``interactivity_changed(bool)``
        ``False`` means click-through has just come on.
    ``screen_changed()``
        The window moved to a different ``QScreen``.
    ``visibility_changed(bool)``
        Wire to ``Animator.set_paused`` -- a hidden pet should cost nothing.
    """

    pet_moved = pyqtSignal(QRect)
    clicked = pyqtSignal()
    drag_started = pyqtSignal()
    drag_finished = pyqtSignal()
    hover_changed = pyqtSignal(bool)
    interactivity_changed = pyqtSignal(bool)
    screen_changed = pyqtSignal()
    visibility_changed = pyqtSignal(bool)

    def __init__(self, sheet: SpriteSheet, parent: QWidget | None = None) -> None:
        """Build the surface and size it to the atlas frame box. GUI thread only."""
        super().__init__(parent)
        self._sheet = sheet

        # The *unscaled* frame box, straight from the atlas. No sprite dimension is ever a
        # literal in src/ (contract 0).
        self._frame_box = QSize(sheet.frame_box)
        self._scale = max(1, min(config.MAX_RENDER_SCALE, int(config.RENDER_SCALE)))

        self._pixmap: QPixmap | None = None
        self._offset = QPoint(0, 0)
        # Lazy QImage view of the current pixmap, for alpha hit-testing. Keyed on cacheKey so a
        # frame change invalidates it without anyone having to remember to.
        self._alpha_image: QImage | None = None
        self._alpha_key: int = -1

        # flag -> desired state. Insertion-ordered, replayed over BASE_FLAGS on every change,
        # so overrides compose instead of one clobbering the last.
        self._flag_overrides: dict[Qt.WindowType, bool] = {}

        self._click_through = False
        self._hovered = False

        # Drag state. _press_origin is captured ONCE at press and never updated, so the drag
        # cannot accumulate rounding drift the way a per-move delta chain does.
        self._press_origin: QPoint | None = None
        self._press_window_pos: QPoint | None = None
        self._dragging = False

        self._window_handle_connected: object | None = None

        # BEFORE the first show(): WA_TranslucentBackground applied afterwards forces Qt to
        # destroy and recreate the native window, losing position and z-order.
        # accept_focus=True -- unlike the card, the pet may need to own a context menu.
        apply_overlay_flags(self, accept_focus=True)
        self.setObjectName("PetWindow")
        self._sync_size()

        # Two connections, no polling. screenAdded is deliberately NOT connected: gaining a
        # monitor cannot invalidate a position, it can only add places that are valid.
        QGuiApplication.instance().screenRemoved.connect(self.revalidate_position)
        QGuiApplication.instance().primaryScreenChanged.connect(self.revalidate_position)

        self._prime_first_frame()

    # -- construction helpers ------------------------------------------------

    def _prime_first_frame(self) -> None:
        """Paint the atlas's default frame before the animator's first tick. GUI thread.

        Without this the window is empty for one event-loop turn, and -- more importantly --
        ``is_opaque_at`` would report everything transparent, so the very first click on a
        freshly shown pet would fall through.
        """
        animation = self._sheet.get(self._sheet.default_animation)
        if animation is None or not animation.frames:
            log.warning("atlas default animation %r is unusable; the pet starts blank",
                        self._sheet.default_animation)
            return
        frame = animation.frames[0]
        self.set_frame(frame.pixmap, frame.offset)

    def _sync_size(self) -> None:
        """Resize to ``frame_box * scale``. GUI thread.

        ``setFixedSize`` rather than ``resize``: the window is exactly the sprite box and has
        no layout, so there is no legitimate reason for anything to resize it. Fixing the size
        also makes the 192x208-vs-210x226 layout-margin regression impossible to reintroduce.
        """
        self.setFixedSize(self._frame_box.width() * self._scale,
                          self._frame_box.height() * self._scale)

    # =====================================================================
    # RENDERING
    # =====================================================================

    @pyqtSlot(QPixmap, QPoint)
    @pyqtSlot(QPixmap)
    def set_frame(self, pixmap: QPixmap, offset: QPoint = QPoint(0, 0)) -> None:
        """Show one frame. Slot for ``Animator.frame_changed``. **GUI thread only.**

        ``offset`` is the frame's position inside the animation box, in *unscaled* atlas
        pixels; it is ``(0, 0)`` for today's uniform atlas and non-zero only for a future
        tight-packed one.

        Cheap by design -- this runs up to ~12 times a second. It stores, invalidates the alpha
        cache and schedules a repaint; nothing here decodes or converts.
        """
        self._pixmap = pixmap
        self._offset = QPoint(offset)
        # Drop the alpha view; _alpha_source() rebuilds it on the next hit-test, which is
        # roughly once per user click rather than once per frame.
        self._alpha_image = None
        self._alpha_key = -1
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        """Draw the current frame. GUI thread (Qt calls it).

        No layout and no child widget, so the pixmap lands at exactly ``offset * scale`` with
        no style-injected margin. ``WA_OpaquePaintEvent`` is deliberately not set upstream, so
        Qt clears the translucent surface for us and the previous frame's silhouette cannot
        ghost through.
        """
        pixmap = self._pixmap
        if pixmap is None or pixmap.isNull():
            return

        painter = QPainter(self)
        # False for pixel art: the atlas was packed with a "nearest" filter at 0.6x and the
        # hard edges are the intended look. This is the one render hint that matters here.
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, config.SMOOTH_SCALING)
        target = QRect(self._offset.x() * self._scale,
                       self._offset.y() * self._scale,
                       pixmap.width() * self._scale,
                       pixmap.height() * self._scale)
        painter.drawPixmap(target, pixmap)
        painter.end()

    def render_scale(self) -> int:
        """Current integer upscale factor. GUI thread."""
        return self._scale

    def set_render_scale(self, scale: int) -> None:
        """Change the integer upscale factor, clamped to ``1..MAX_RENDER_SCALE``. GUI thread.

        Integer only -- contract 2.5. Fractional scaling of pixel art is the exact thing this
        design avoids, so this takes an ``int`` and coerces rather than accepting a float.
        """
        scale = max(1, min(config.MAX_RENDER_SCALE, int(scale)))
        if scale == self._scale:
            return
        log.debug("render scale %d -> %d", self._scale, scale)
        self._scale = scale
        self._sync_size()
        # Growing the window can push it off the edge it was tucked into.
        self.move(clamp_to_desktop(self.frameGeometry().topLeft(), self.size()))
        self.update()
        # resizeEvent does not emit pet_moved (contract 2.4 names moveEvent + showEvent), but
        # the card's anchor rect just changed, so nudge it here explicitly.
        self._emit_moved()

    def frame_box(self) -> QSize:
        """The **unscaled** atlas frame box. GUI thread (value type; safe anywhere)."""
        return QSize(self._frame_box)

    # =====================================================================
    # WINDOW FLAGS -- the only mutation path (contract 2.2)
    # =====================================================================

    def _compose_flags(self) -> Qt.WindowType:
        """``BASE_FLAGS`` with every recorded override replayed over it. GUI thread.

        Composed from the shared base every time rather than mutated in place, so two
        overrides cannot fight and a stale bit cannot survive. Done in ``int`` space: it is
        unambiguous regardless of how PyQt6 defines complement on its ``enum.Flag`` types.
        """
        value = int(BASE_FLAGS)
        for flag, enabled in self._flag_overrides.items():
            if enabled:
                value |= int(flag)
            else:
                value &= ~int(flag)
        return Qt.WindowType(value)

    def set_flag_override(self, flag: Qt.WindowType, enabled: bool) -> None:
        """The **only** supported way to change this window's flags. GUI thread only.

        Nothing else anywhere may call ``setWindowFlags`` / ``setWindowFlag`` on the pet
        (contract 2.2), because changing flags destroys the native window and everything
        native that was hung off it -- most importantly the click-through ex-style -- has to be
        put back afterwards. That "afterwards" is what this method exists to guarantee.

        Sequence: capture geometry and visibility -> recompose from ``BASE_FLAGS`` ->
        ``setWindowFlags`` -> restore geometry -> ``show()`` if it was visible ->
        ``_apply_native_state()``.

        Note when asserting on the result: window *types* share the low byte of the flag word,
        so ``flags & Qt.WindowType.Tool`` is meaningless (``SubWindow`` 0x12 overlaps ``Tool``
        0x0b and the naive test passes on either). Compare
        ``flags & Qt.WindowType.WindowType_Mask == Qt.WindowType.Tool``. Hints -- Frameless,
        StaysOnTop -- are real single bits and test normally.
        """
        self._flag_overrides[flag] = bool(enabled)
        flags = self._compose_flags()

        was_visible = self.isVisible()
        geometry = self.geometry()

        if flags == self.windowFlags():
            log.debug("set_flag_override(%s, %s): flags already %#x; re-applying native state",
                      flag, enabled, int(flags))
            # No HWND was destroyed, so nothing *should* need restoring -- but re-applying is
            # free and the doctrine in _apply_native_state is "unconditional, never gated".
            self._apply_native_state()
            return

        log.debug("set_flag_override(%s, %s): %#x -> %#x (visible=%s)",
                  flag, enabled, int(self.windowFlags()), int(flags), was_visible)
        self.setWindowFlags(flags)
        self.setGeometry(geometry)
        if was_visible:
            # WA_ShowWithoutActivating (set by apply_overlay_flags) keeps this from stealing
            # focus from whatever the user is typing in.
            self.show()
        self._apply_native_state()

    def window_flags(self) -> Qt.WindowType:
        """The live flag set as Qt holds it. GUI thread."""
        return self.windowFlags()

    def set_always_on_top(self, enabled: bool) -> None:
        """Thin wrapper over ``set_flag_override``. GUI thread only."""
        self.set_flag_override(Qt.WindowType.WindowStaysOnTopHint, enabled)

    # =====================================================================
    # CLICK-THROUGH
    # =====================================================================

    def _apply_native_state(self) -> None:
        """Write the native ex-style for the current handle. The ONLY place it is written.

        GUI thread only (it reads ``winId()``). Called from ``showEvent`` via
        ``QTimer.singleShot(0, ...)`` so Qt has finished mapping the window, from
        ``set_flag_override``, and from the ``QWindow.screenChanged`` handler.

        **Re-applies unconditionally, and this contradicts the obvious assumption.** It is
        tempting to skip the work when ``winId()`` has not changed, on the reasoning that an
        unchanged handle means an unchanged window. Measured on this machine and recorded in
        contract 5.1: after ``hide(); setWindowFlag(...); show()``, ``winId()`` returned the
        *identical* value ``0x9318ac`` while the ex-style had been rebuilt from ``0x100`` to
        ``0x0`` -- Windows recycled the handle value into the recreated window. A cache keyed
        on handle identity therefore looks valid while being stale, silently leaves the pet
        interactive when the tray says click-through is on, and would not show up in testing.
        So: never gate on the handle, never cache the handle, never cache the ex-style. The
        handle is logged as a diagnostic only; nothing branches on it.
        """
        if not self.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
            # winId() would *create* the native window as a side effect. For a pet that has
            # never been shown that is premature; showEvent will call us once it exists.
            log.debug("native state deferred: no native window yet")
            return

        handle = platform.native_handle(self)
        log.debug("re-applying native state (handle=%#x, click_through=%s)",
                  handle, self._click_through)

        ok = platform.set_click_through(self, self._click_through)
        if self._click_through and not ok:
            # The window was recreated and we could not put the bit back. Reality wins: drop
            # the flag so the tray follows the failure rather than claiming a pass-through pet
            # that still swallows clicks (contract 5.3).
            log.warning("could not re-apply click-through after a window change (%s); "
                        "the pet is interactive again",
                        platform.unsupported_reason() or "backend refused")
            self._click_through = False
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.interactivity_changed.emit(True)

    def set_click_through(self, enabled: bool) -> bool:
        """Make the pet transparent to mouse input. GUI thread only.

        Returns ``True`` only when the change was applied **and verified by read-back** by the
        platform backend. ``False`` means it did not happen and the caller must show the
        toggle as off -- the tray reflects reality, not intent (contract 5.3).
        """
        enabled = bool(enabled)

        if enabled and self._dragging:
            # A drag in flight would otherwise keep moving the window on mouse events that the
            # OS is about to stop delivering, leaving _dragging latched on forever.
            self._cancel_drag()

        ok = platform.set_click_through(self, enabled)

        # Qt-side attribute, set alongside the native call. To be explicit: this attribute is
        # NOT what makes pass-through work. It only affects Qt's *internal* event routing --
        # the OS still hit-tests this window and still hands it the click, Qt just forwards it
        # to the widget underneath *in this process*, of which there is none. WS_EX_TRANSPARENT
        # (core/platform/windows.py) is the real mechanism. It is kept in sync so Qt's view and
        # the OS's view of the window never disagree; it is tied to `enabled and ok` rather
        # than to `enabled` so a backend that refused leaves a genuinely interactive pet
        # instead of one that is inert in Qt while still blocking the desktop beneath it.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled and ok)

        was = self._click_through
        # Never latch on failure.
        self._click_through = enabled and ok
        if enabled and not ok:
            log.warning("click-through could not be enabled (%s)",
                        platform.unsupported_reason() or "backend refused")

        if self._click_through != was:
            if self._click_through and self._hovered:
                # Leaving hover "on" would strand the card open with no way to dismiss it,
                # because no leaveEvent is coming.
                self._hovered = False
                self.hover_changed.emit(False)
            self.interactivity_changed.emit(not self._click_through)

        return ok

    def is_click_through(self) -> bool:
        """Whether pass-through is currently active *and verified*. GUI thread."""
        return self._click_through

    def is_interactive(self) -> bool:
        """``not is_click_through()`` -- reads better at the call sites. GUI thread."""
        return not self._click_through

    # =====================================================================
    # GEOMETRY
    # =====================================================================

    def restore_position(self, settings: config.Settings) -> None:
        """Place the pet from persisted settings, or at the default. GUI thread only.

        Uses the **strict** validity check: a saved position that no longer lands on a real
        screen is discarded rather than clamped, because clamping a position from a monitor
        that has been unplugged produces a plausible-looking but wrong corner. ``settings.screen``
        is consulted only as a hint for where the default should go.
        """
        size = self.size()
        if settings.pos_x is not None and settings.pos_y is not None:
            saved = QPoint(settings.pos_x, settings.pos_y)
            if is_position_valid(saved, size):
                self.move(saved)
                return
            log.info("saved pet position (%d, %d) is no longer on any screen; "
                     "falling back to the default corner", saved.x(), saved.y())

        self.move(default_position(size, screen_named(settings.screen)))

    def reset_position(self) -> None:
        """Move to the configured start corner of the primary screen. GUI thread only."""
        self.move(default_position(self.size()))

    @pyqtSlot()
    def revalidate_position(self) -> None:
        """Re-home the pet if its current position has stopped being valid. GUI thread only.

        Slot for ``QGuiApplication.screenRemoved`` and ``primaryScreenChanged`` -- two
        connections, no polling. ``screenAdded`` is deliberately not connected: gaining a
        monitor can only add valid places, never invalidate the current one.

        Both signals carry a ``QScreen`` argument that this slot ignores; the question is
        never "which screen changed" but "is where we are still somewhere".
        """
        if is_position_valid(self.frameGeometry().topLeft(), self.size()):
            return
        pos = self.frameGeometry().topLeft()
        log.info("pet position (%d, %d) became invalid after a display change; resetting",
                 pos.x(), pos.y())
        self.reset_position()

    def current_position(self) -> QPoint:
        """Top-left of the frame geometry, global coordinates. GUI thread.

        Frameless, so this equals ``pos()`` -- but it is read from ``frameGeometry`` so that
        what is persisted matches what ``pet_moved`` carries.
        """
        return self.frameGeometry().topLeft()

    def _emit_moved(self) -> None:
        """Publish the anchor rect the card and the settings layer both read. GUI thread."""
        self.pet_moved.emit(QRect(self.frameGeometry()))

    # =====================================================================
    # HIT-TESTING (alpha, not rect)
    # =====================================================================

    def _alpha_source(self) -> QImage | None:
        """A ``QImage`` view of the current frame, built lazily and cached. GUI thread.

        ``QPixmap`` cannot be sampled per pixel, so a conversion is unavoidable somewhere.
        Doing it here, on the first hit-test after a frame change, costs one conversion per
        user gesture; doing it in ``set_frame`` would cost one per animation frame, ~12 a
        second, forever, for a value almost never read.

        ``SpriteSheet.alpha_at`` measures the same thing from the retained atlas image, but it
        is keyed by *(animation name, frame index)* and ``set_frame``'s contract signature
        carries neither -- the window is handed a bare pixmap. Sampling the pixmap we actually
        painted is also strictly more truthful: it is the thing on screen.
        """
        pixmap = self._pixmap
        if pixmap is None or pixmap.isNull():
            return None
        if self._alpha_image is None or self._alpha_key != pixmap.cacheKey():
            self._alpha_image = pixmap.toImage()
            self._alpha_key = pixmap.cacheKey()
        return self._alpha_image

    def alpha_at_local(self, pos: QPoint) -> int:
        """Alpha (0-255) of the sprite pixel under a **widget-local** point. GUI thread.

        Returns 0 outside the sprite, for an unpainted window, or if the pixmap cannot be
        sampled. "Transparent" is the safe answer everywhere: at worst that pixel stops being
        draggable.
        """
        pixmap = self._pixmap
        if pixmap is None or pixmap.isNull():
            return 0

        # Undo the render scale, then the frame's offset within the box. Floor division is
        # correct for negative coordinates too (a point above/left of the widget).
        x = pos.x() // self._scale - self._offset.x()
        y = pos.y() // self._scale - self._offset.y()
        if not (0 <= x < pixmap.width() and 0 <= y < pixmap.height()):
            return 0

        image = self._alpha_source()
        if image is None or image.isNull():
            return 0
        return image.pixelColor(x, y).alpha()

    def is_opaque_at(self, pos: QPoint, threshold: int = 16) -> bool:
        """Whether a widget-local point is solid enough to count as "the pet". GUI thread.

        The default threshold of 16/255 ignores the near-invisible antialiased fringe the 0.6x
        resample left around the silhouette; grabbing the pet by a 6 %-opaque pixel would feel
        like grabbing air.
        """
        return self.alpha_at_local(pos) >= threshold

    # =====================================================================
    # MOUSE INPUT
    # =====================================================================

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """Begin a potential drag, or decline the press entirely. GUI thread (Qt calls it).

        Presses over a transparent pixel are ``ignore()``d rather than swallowed: every frame
        has a ~20-25 px transparent gutter below the pet's feet and 20-45 px at the sides, and
        a press there is the user aiming at whatever is underneath the window, not at the pet.
        """
        # `& LeftButton`, never `== LeftButton`: the latter is False the moment a second
        # button or (on some platforms) a modifier is held, which silently kills the drag.
        if not self.is_interactive() or not (event.buttons() & Qt.MouseButton.LeftButton):
            event.ignore()
            return

        pos = event.position().toPoint()
        if not self.is_opaque_at(pos):
            event.ignore()
            return

        # Captured ONCE. Every later move is measured against this origin, so the drag cannot
        # accumulate the rounding drift a chain of per-move deltas would.
        self._press_origin = event.globalPosition().toPoint()
        self._press_window_pos = self.frameGeometry().topLeft()
        self._dragging = False
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """Promote the press to a drag once it passes the threshold, then move. GUI thread."""
        if self._press_origin is None or self._press_window_pos is None:
            event.ignore()
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            event.ignore()
            return

        delta = event.globalPosition().toPoint() - self._press_origin

        if not self._dragging:
            # The platform's own value (10 px on Windows), so the pet's click-vs-drag feel
            # matches every other draggable thing in the OS. Distance only -- no time limit;
            # a slow, deliberate click is still a click (contract 2.6).
            if delta.manhattanLength() < QApplication.startDragDistance():
                event.accept()
                return
            self._dragging = True
            self.drag_started.emit()

        # Loose clamp: the pet may cross a monitor seam and hang off an outer edge rather than
        # fight the cursor.
        self.move(clamp_to_desktop(self._press_window_pos + delta, self.size()))
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """End the gesture: ``drag_finished`` if it moved, ``clicked`` if it did not."""
        if self._press_origin is None or event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return

        was_dragging = self._dragging
        self._press_origin = None
        self._press_window_pos = None
        self._dragging = False

        if was_dragging:
            self.drag_finished.emit()
        else:
            self.clicked.emit()
        event.accept()

    def _cancel_drag(self) -> None:
        """Abandon an in-flight drag, leaving the window where it is. GUI thread.

        Used when click-through is switched on mid-drag: the OS is about to stop delivering
        mouse events to this window, so no release is coming and the state would latch.
        ``drag_finished`` is still emitted -- the position did change, and the settings layer
        needs its "persist now" nudge.
        """
        if self._press_origin is None:
            return
        was_dragging = self._dragging
        self._press_origin = None
        self._press_window_pos = None
        self._dragging = False
        log.debug("drag cancelled (click-through enabled mid-gesture)")
        if was_dragging:
            self.drag_finished.emit()

    # =====================================================================
    # HOVER
    # =====================================================================

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 - Qt override
        """Report hover to the card. GUI thread (Qt calls it).

        Guarded on ``is_interactive()`` as defence in depth. On Windows the
        ``WS_EX_TRANSPARENT`` window never receives the enter at all, so the guard looks
        redundant -- but under the ``NullBackend`` (any non-Windows platform) Qt *will* still
        deliver hover, and the pet must not pop its card while it is nominally pass-through.
        """
        super().enterEvent(event)
        if not self.is_interactive():
            return
        if not self._hovered:
            self._hovered = True
            self.hover_changed.emit(True)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        """Clear hover. GUI thread.

        Deliberately *not* guarded on ``is_interactive()``: the guard belongs on the edge that
        asserts hover, never on the edge that retracts it. A leave that arrived after
        click-through came on must still be allowed to clear a stale ``True``, or the card
        stays open with nothing able to dismiss it.
        """
        super().leaveEvent(event)
        if self._hovered:
            self._hovered = False
            self.hover_changed.emit(False)

    # =====================================================================
    # QT LIFECYCLE
    # =====================================================================

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override
        """Wire up the native window and publish the first anchor rect. GUI thread."""
        super().showEvent(event)
        self._connect_window_handle()
        # Deferred by one event-loop turn: at showEvent time Qt has not finished mapping the
        # window, and the handle we would read now is not necessarily the final one.
        QTimer.singleShot(0, self._apply_native_state)
        # Contract 2.4: pet_moved comes from moveEvent and once from showEvent, so the card
        # has an anchor before the first hover rather than after the first move.
        self._emit_moved()
        self.visibility_changed.emit(True)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 - Qt override
        """Tell the animator it can stop. GUI thread."""
        super().hideEvent(event)
        # Hover cannot survive a hide, and no leaveEvent is guaranteed.
        if self._hovered:
            self._hovered = False
            self.hover_changed.emit(False)
        self.visibility_changed.emit(False)

    def moveEvent(self, event: QMoveEvent) -> None:  # noqa: N802 - Qt override
        """Publish the new anchor rect. GUI thread."""
        super().moveEvent(event)
        self._emit_moved()

    def _connect_window_handle(self) -> None:
        """(Re)connect to the current ``QWindow``'s ``screenChanged``. GUI thread.

        ``setWindowFlags`` destroys the ``QWindow`` along with the native handle, so the
        connection has to be remade against the new object. Identity-compared rather than
        connection-counted because the old object is gone, not merely disconnected.
        """
        handle = self.windowHandle()
        if handle is None or handle is self._window_handle_connected:
            return
        self._window_handle_connected = handle
        handle.screenChanged.connect(self._on_screen_changed)

    @pyqtSlot(QScreen)
    def _on_screen_changed(self, screen: QScreen) -> None:
        """The window moved to another display. GUI thread.

        Re-applies the native state because a cross-screen move on Windows can involve a
        DPI change, which Qt handles by recreating the native window -- and a recreated window
        has a freshly computed ex-style with our click-through bit gone.

        It deliberately does **not** revalidate the position: arriving on a new screen means
        the position is valid by construction, and running the strict check here would fight a
        drag that is crossing a monitor seam at exactly the wrong moment.
        """
        log.debug("pet moved to screen %r", screen.name() if screen else None)
        self._apply_native_state()
        self.screen_changed.emit()
