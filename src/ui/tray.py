"""System tray icon and menu.

The tray owns **no application state**. It emits intent and exposes ``set_*_checked``
setters so the app can push the truth back after applying (or failing to apply) a change.
That is what stops the classic feedback loop where a failed toggle leaves a checkbox lying
about reality.

It is also the escape hatch for click-through: once clicks pass through the pet, the pet
cannot be clicked to turn it back off, so this menu is the only way back. ``main.py``
enforces the matching precondition -- click-through may not be enabled unless a tray icon is
actually visible.

GUI thread only.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QSignalBlocker, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap, QRegion
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from .. import config
from ..core.state_machine import STATES

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..core.sprite_loader import SpriteSheet

log = logging.getLogger(__name__)

#: Windows asks for 16 px in the notification area and 32 px for the balloon and the overflow
#: flyout. Supplying both (plus the in-between sizes) beats letting Qt downscale a 192x208
#: frame to 16 px, which turns the cat into a smudge.
ICON_SIZES: tuple[int, ...] = (16, 24, 32, 48)

#: The tray icon is taken from this animation's first frame, so the icon and the pet match.
ICON_SOURCE_STATE: str = "idle-attentive"

#: Retry cadence for a tray that is not yet available (see install_tray).
TRAY_RETRY_MS: int = 2000


# ---------------------------------------------------------------------------
# Icon construction
# ---------------------------------------------------------------------------


def _trim_transparent(pixmap: QPixmap) -> QPixmap:
    """Crop a sprite frame to its opaque bounding box.

    Without this the cat occupies maybe 40% of a 192x208 cell -- most of which is the
    transparent gutter the art pipeline pads with -- and the tray icon reads as a smudge.
    """
    if pixmap.isNull():
        return pixmap
    mask = pixmap.mask()
    if mask.isNull():
        return pixmap
    rect = QRegion(mask).boundingRect()
    if rect.isEmpty():
        return pixmap
    return pixmap.copy(rect)


def _fallback_icon() -> QIcon:
    """A painted 'LC' badge, used when the atlas is unavailable.

    The tray must never be the thing that prevents startup -- without an icon there is no
    escape hatch from click-through, so this path matters more than it looks.
    """
    icon = QIcon()
    for size in ICON_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(config.DEFAULT_PRESENTATION.accent))
        radius = size * 0.28
        painter.drawRoundedRect(0, 0, size, size, radius, radius)
        if size >= 24:  # below this the glyph is illegible and the plain badge reads better
            font = QFont(config.CARD_FONT_FAMILIES[0])
            font.setPixelSize(int(size * 0.5))
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(20, 22, 26))
            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "LC")
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def build_tray_icon(sheet: "SpriteSheet | None" = None) -> QIcon:
    """Build the tray icon from the atlas, falling back to a painted badge.

    Never raises: any failure to read the atlas degrades to ``_fallback_icon()``.
    """
    if sheet is None:
        return _fallback_icon()
    try:
        frame = sheet.frame(ICON_SOURCE_STATE, 0)
    except Exception as exc:  # noqa: BLE001 - the tray must not be able to block startup
        log.warning("could not take tray icon from %r (%s); using fallback",
                    ICON_SOURCE_STATE, exc)
        return _fallback_icon()
    trimmed = _trim_transparent(frame)
    if trimmed.isNull():
        return _fallback_icon()
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(trimmed.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            # Smooth here, unlike the pet itself: the icon is a heavy downscale (>=4x), where
            # nearest-neighbour drops whole features rather than preserving crisp edges.
            Qt.TransformationMode.SmoothTransformation,
        ))
    return icon


# ---------------------------------------------------------------------------
# Tray
# ---------------------------------------------------------------------------


class TrayIcon(QSystemTrayIcon):
    """The notification-area icon and its menu.

    Emits intent; never decides. Call the ``set_*_checked`` methods to push applied state
    back, including after a failure -- the menu must reflect reality, not intent.
    """

    click_through_toggled = pyqtSignal(bool)
    pet_visibility_toggled = pyqtSignal(bool)
    card_visibility_toggled = pyqtSignal(bool)
    always_on_top_toggled = pyqtSignal(bool)
    reset_position_requested = pyqtSignal()
    poke_requested = pyqtSignal(str)
    quit_requested = pyqtSignal()
    activated_by_user = pyqtSignal()

    def __init__(self, icon: QIcon, parent: QObject | None = None) -> None:
        super().__init__(icon, parent)
        self.setToolTip(config.APP_NAME)

        # Strong references are mandatory. A QMenu or QAction referenced only by the menu it
        # was added to is a live GC hazard in PyQt and produces an intermittently empty menu.
        self._menu = QMenu()
        self._menu.setToolTipsVisible(True)  # so disabled-reason tooltips are readable

        self._act_click_through = self._add_checkable(
            "Click-through", self._on_click_through)
        self._act_pet_visible = self._add_checkable(
            "Show pet", self._on_pet_visible, checked=True)
        self._act_card_visible = self._add_checkable(
            "Show stats card", self._on_card_visible)
        self._act_always_on_top = self._add_checkable(
            "Always on top", self._on_always_on_top, checked=True)

        # "Poke the pets" is the manual trigger for needs-input-sad (which has no telemetry
        # signal) and doubles as the QA path for every animation row while the art is WIP --
        # blocked-grumpy in particular may never fire on a machine with no temperature source.
        self._poke_menu = self._menu.addMenu("Poke the pets")
        self._poke_group = QActionGroup(self)
        self._poke_group.setExclusive(False)
        # Keyed by state so set_poke_available() can grey out the rows this atlas lacks.
        self._poke_actions: dict[str, QAction] = {}
        for state in STATES:
            action = QAction(state, self)
            action.triggered.connect(lambda _checked, s=state: self.poke_requested.emit(s))
            self._poke_group.addAction(action)
            self._poke_menu.addAction(action)
            self._poke_actions[state] = action

        self._menu.addSeparator()
        self._act_reset = QAction("Reset position", self)
        self._act_reset.triggered.connect(self.reset_position_requested.emit)
        self._menu.addAction(self._act_reset)

        self._menu.addSeparator()
        self._act_quit = QAction("Quit", self)
        self._act_quit.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(self._act_quit)

        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)

    # -- construction helpers ------------------------------------------------

    def _add_checkable(self, text: str, slot, *, checked: bool = False) -> QAction:
        action = QAction(text, self)
        action.setCheckable(True)
        action.setChecked(checked)
        action.toggled.connect(slot)
        self._menu.addAction(action)
        return action

    # -- intent --------------------------------------------------------------

    def _on_click_through(self, on: bool) -> None:
        self.click_through_toggled.emit(on)

    def _on_pet_visible(self, on: bool) -> None:
        self.pet_visibility_toggled.emit(on)

    def _on_card_visible(self, on: bool) -> None:
        self.card_visibility_toggled.emit(on)

    def _on_always_on_top(self, on: bool) -> None:
        self.always_on_top_toggled.emit(on)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Single left click is deliberately ignored: on Windows the right-click context menu
        # is already wired via setContextMenu, and hijacking left-click makes the menu feel
        # unreliable. Double-click is the "give me my pet back" gesture.
        if reason is QSystemTrayIcon.ActivationReason.DoubleClick:
            self.activated_by_user.emit()

    # -- state push-back -----------------------------------------------------
    # Each wraps the QAction in a QSignalBlocker so pushing applied state back never
    # re-emits the intent signal and starts a loop.

    @staticmethod
    def _set_checked(action: QAction, on: bool) -> None:
        with QSignalBlocker(action):
            action.setChecked(on)

    def set_click_through_checked(self, on: bool) -> None:
        self._set_checked(self._act_click_through, on)
        self.setToolTip(f"{config.APP_NAME} - click-through ON" if on else config.APP_NAME)

    def set_pet_visible_checked(self, on: bool) -> None:
        self._set_checked(self._act_pet_visible, on)

    def set_card_visible_checked(self, on: bool) -> None:
        self._set_checked(self._act_card_visible, on)

    def set_always_on_top_checked(self, on: bool) -> None:
        self._set_checked(self._act_always_on_top, on)

    def set_click_through_available(self, available: bool, reason: str = "") -> None:
        """Enable or disable the click-through item, with a readable reason when disabled."""
        self._act_click_through.setEnabled(available)
        self._act_click_through.setToolTip("" if available else (reason or "Not available"))
        if not available:
            self._set_checked(self._act_click_through, False)

    def set_poke_available(self, names: Sequence[str]) -> None:
        """Grey out the poke entries whose animation this atlas does not have.

        The same shape as ``set_click_through_available``: the menu shows reality, never intent.
        A state can legitimately exist in ``STATES`` with no row yet -- its rules ship before
        its art -- and poking it would otherwise leave the animator holding the previous sprite
        while the card showed the missing state's copy over it. ``StateMachine.trigger_state``
        refuses such a name anyway; this is so the user can see why, rather than clicking a menu
        item that silently does nothing.
        """
        available = set(names)
        for state, action in self._poke_actions.items():
            ok = state in available
            action.setEnabled(ok)
            action.setToolTip("" if ok else "No animation for this state in the atlas yet")

    def set_status_text(self, text: str) -> None:
        """Set the hover tooltip, e.g. a one-line stats summary. Throttle to ~1 Hz."""
        self.setToolTip(text)

    def notify(self, title: str, body: str, msecs: int = 4000) -> None:
        """Show a balloon, if the platform supports one.

        Silently does nothing when balloons are suppressed (Focus Assist), which is
        acceptable because the checkbox and tooltip still convey the state.
        """
        if self.supportsMessages():
            self.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, msecs)

    def shutdown(self) -> None:
        """Hide the icon before the process exits.

        A process that exits without removing its icon leaves a ghost in the Windows
        notification area until the user happens to hover it.
        """
        self.hide()


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


def install_tray(parent: QObject, sheet: "SpriteSheet | None" = None,
                 retries: int = 5) -> TrayIcon | None:
    """Create and show the tray icon, retrying while the tray is unavailable.

    At login the app can legitimately start before (or alongside) Explorer, so
    ``isSystemTrayAvailable()`` may be False for a few seconds. Retries on a timer, then
    gives up and returns ``None``; the app still runs, but click-through must then stay
    permanently unavailable because there would be no way to turn it off again.

    Returns the tray immediately if one can be created now, otherwise ``None`` -- callers
    that want the retried instance should connect to the returned object's signals inside
    the ``on_ready`` callback pattern in main.py.
    """
    if not QSystemTrayIcon.isSystemTrayAvailable():
        if retries <= 0:
            log.warning("no system tray available; click-through will stay disabled")
            return None
        log.info("system tray not available yet; retrying in %d ms (%d left)",
                 TRAY_RETRY_MS, retries)
        return None
    tray = TrayIcon(build_tray_icon(sheet), parent)
    tray.show()
    return tray


def install_tray_async(parent: QObject, sheet: "SpriteSheet | None",
                       on_ready, retries: int = 5) -> None:
    """Install the tray, retrying on a timer, and hand it to ``on_ready`` when it appears.

    ``on_ready(tray_or_none)`` is called exactly once: with the tray, or with ``None`` once
    the retries are exhausted.
    """
    remaining = {"n": retries}

    def attempt() -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            tray = TrayIcon(build_tray_icon(sheet), parent)
            tray.show()
            on_ready(tray)
            return
        remaining["n"] -= 1
        if remaining["n"] <= 0:
            log.warning("no system tray after %d attempts; click-through will stay disabled",
                        retries)
            on_ready(None)
            return
        log.info("system tray not available yet; retrying in %d ms", TRAY_RETRY_MS)
        QTimer.singleShot(TRAY_RETRY_MS, attempt)

    attempt()
