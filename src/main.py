"""Application entry point: construct the parts and wire them together.

Wiring only -- no behaviour lives here. Run with ``pixi run pets``.

Ordering in ``main()`` is load-bearing in three places, each marked in the code:
the high-DPI rounding policy must precede ``QApplication``; ``setQuitOnLastWindowClosed``
must precede the first window; and the pet must be shown before the card is first peeked,
so the card has an anchor and does not flash at (0, 0).
"""

from __future__ import annotations

import argparse
import logging
import sys

from PyQt6.QtCore import QObject, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication

from . import config
from .core import platform as platform_seam
from .core.animator import Animator
from .core.sprite_loader import SpriteLoadError, load_spritesheet, validate_atlas
from .core.state_machine import REQUIRED_STATES, StateMachine
from .core.telemetry import TelemetryService
from .ui.pet_window import PetWindow
from .ui.stats_card import StatsCard
from .ui.tray import TrayIcon, install_tray_async

log = logging.getLogger(__name__)


class PetApp(QObject):
    """Owns the components and mediates between them.

    Every tray toggle follows the same four steps: apply, read back what actually happened,
    push that truth to the tray, save. The read-back matters -- click-through can fail at the
    OS level, and a menu that shows intent rather than reality is how a user ends up with a
    pet they cannot interact with and no idea why.
    """

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._settings = config.load_settings().clamped()

        sheet = load_spritesheet()
        # REQUIRED_STATES, not STATES: an optional row (today, `working-together`, whose art is
        # still being drawn) must not be able to stop the app from starting. The machine is told
        # separately what the atlas actually has, and refuses to emit anything it lacks.
        sheet.require(REQUIRED_STATES)
        log.info("atlas: %d animations, %d frames, frame box %dx%d",
                 len(sheet), sheet.report.frame_count,
                 sheet.frame_box.width(), sheet.frame_box.height())

        self._sheet = sheet
        self._pet = PetWindow(sheet)
        self._animator = Animator(sheet)
        self._card = StatsCard()
        self._machine = StateMachine()
        self._telemetry = TelemetryService(interval_ms=config.POLL_INTERVAL_MS)
        self._tray: TrayIcon | None = None

        # Before anything can ask the machine for a state. Every route to a state name -- the
        # ladder, the flourish pools, trigger_state -- filters on this, so a row the atlas does
        # not have is unreachable rather than a warning-and-wrong-sprite at runtime. The rules
        # for such a state stay live and start working the day its row lands in the atlas.
        self._machine.set_available_states(sheet.names())

        self._wire()

        self._pet.set_render_scale(self._settings.scale)
        self._pet.restore_position(self._settings)
        self._card.set_anchor_offset(0)  # reserved for the control pill; see contract 2.8

        if self._settings.always_on_top is False:
            self._pet.set_always_on_top(False)

        # Show before the first peek so pet_moved has fired and the card has an anchor.
        if self._settings.pet_visible:
            self._pet.show()
        self._animator.play(sheet.default_animation)

        # state_changed only fires on an actual change, and the machine starts already in its
        # default state -- so without this push the card would sit on DEFAULT_PRESENTATION
        # ("Chico and Layla / Watching your system") until the first real transition.
        self._card.on_state_changed(self._machine.state)

        if self._settings.card_visible:
            self._card.set_pinned(True)

        # The tray may not exist yet at login; click-through stays refused until it does.
        install_tray_async(self, sheet, self._on_tray_ready)

        self._telemetry.start()

    # -- wiring --------------------------------------------------------------

    def _wire(self) -> None:
        self._animator.frame_changed.connect(self._pet.set_frame)
        self._machine.state_changed.connect(self._animator.set_state)
        self._machine.state_changed.connect(self._card.on_state_changed)
        self._telemetry.metrics_ready.connect(self._machine.on_metrics)
        # Separate from metrics_ready on purpose: this latch is sampled every 250 ms on the
        # telemetry thread, and gating `working-together` on the 1500 ms Metrics payload instead
        # would put 1.5 s of poll latency in front of a transition the user is watching for.
        self._telemetry.input_activity_changed.connect(
            self._machine.on_input_activity_changed)
        self._telemetry.metrics_ready.connect(self._card.on_metrics)
        self._telemetry.metrics_ready.connect(self._on_metrics)

        self._pet.pet_moved.connect(self._card.set_anchor)
        self._pet.hover_changed.connect(self._card.set_hovered)
        self._pet.clicked.connect(self._card.toggle_pinned)
        self._pet.clicked.connect(self._greet)
        self._pet.drag_finished.connect(self._greet)
        # NOT a direct connect: the senses are opposite. visibility_changed(True) means
        # "shown", set_paused(True) means "stop animating" -- wiring them together freezes the
        # pet the moment it becomes visible.
        self._pet.visibility_changed.connect(
            lambda visible: self._animator.set_paused(not visible))
        self._pet.drag_finished.connect(self._save)
        self._card.pinned_changed.connect(self._on_card_pinned)

        self._app.aboutToQuit.connect(self._telemetry.stop)
        self._app.aboutToQuit.connect(self._save)

    def _on_tray_ready(self, tray: TrayIcon | None) -> None:
        self._tray = tray
        if tray is None:
            # No tray means no escape hatch from click-through, so it must stay off. This is
            # the hard precondition: there is no code path that yields a click-through pet the
            # user cannot recover from.
            if self._settings.click_through:
                log.warning("click-through was enabled in settings but there is no tray icon "
                            "to turn it off again; forcing it off")
                self._settings.click_through = False
                self._save()
            return

        tray.click_through_toggled.connect(self._on_click_through)
        tray.pet_visibility_toggled.connect(self._on_pet_visible)
        tray.card_visibility_toggled.connect(self._card.set_pinned)
        tray.always_on_top_toggled.connect(self._on_always_on_top)
        tray.reset_position_requested.connect(self._on_reset_position)
        tray.poke_requested.connect(self._on_poke)
        tray.quit_requested.connect(self._quit)
        tray.activated_by_user.connect(self._on_tray_double_click)

        supported = platform_seam.supports_click_through()
        tray.set_click_through_available(supported, platform_seam.unsupported_reason())
        tray.set_pet_visible_checked(self._pet.isVisible())
        tray.set_always_on_top_checked(self._settings.always_on_top)
        tray.set_card_visible_checked(self._settings.card_visible)
        # "Poke the pets" is built from every name the machine knows, including ones whose art
        # has not shipped. Those must be visibly unavailable rather than a one-click route to a
        # missing sprite.
        tray.set_poke_available(self._sheet.names())

        if self._settings.click_through and supported:
            self._on_click_through(True)
        else:
            tray.set_click_through_checked(False)

    # -- tray slots ----------------------------------------------------------

    def _on_click_through(self, enabled: bool) -> None:
        if enabled and self._tray is None:
            log.warning("refusing click-through: no tray icon to turn it back off")
            return
        applied = self._pet.set_click_through(enabled)
        actual = self._pet.is_click_through()
        if enabled and not applied:
            log.warning("click-through could not be applied at the OS level")
        if self._tray is not None:
            self._tray.set_click_through_checked(actual)
            if actual:
                self._tray.notify(
                    "Click-through ON",
                    "Clicks now pass through Layla & Chico. "
                    "Use this tray icon to turn it back off.")
        self._settings.click_through = actual
        self._save()

    def _on_pet_visible(self, visible: bool) -> None:
        self._pet.setVisible(visible)
        if not visible:
            self._card.set_pinned(False)
        self._settings.pet_visible = visible
        if self._tray is not None:
            self._tray.set_pet_visible_checked(self._pet.isVisible())
        self._save()

    def _on_always_on_top(self, enabled: bool) -> None:
        self._pet.set_always_on_top(enabled)
        self._settings.always_on_top = enabled
        if self._tray is not None:
            self._tray.set_always_on_top_checked(enabled)
        self._save()

    def _on_reset_position(self) -> None:
        self._pet.reset_position()
        self._save()

    def _greet(self) -> None:
        """Acknowledge a click or a finished drag with a brief animation.

        Deliberately fires even under load: the user just touched the pet and an unresponsive
        pet reads as broken. The cost is that a greeting briefly covers whatever the telemetry
        would otherwise be showing -- including an alarm state -- for its hold duration.
        """
        state = config.INTERACTION_GREETING_STATE
        if state:
            self._machine.trigger_state(state, config.INTERACTION_GREETING_HOLD_MS)

    def _on_poke(self, state: str) -> None:
        # needs-input-sad has no telemetry trigger by design, and blocked-grumpy may never
        # fire on a machine with no temperature source -- this is how both get exercised.
        self._machine.trigger_state(state, 5000)

    def _on_tray_double_click(self) -> None:
        if not self._pet.isVisible():
            self._on_pet_visible(True)
        self._pet.raise_()
        if self._pet.is_click_through():
            # A second escape hatch, for one line.
            self._on_click_through(False)

    def _on_card_pinned(self, pinned: bool) -> None:
        self._settings.card_visible = pinned
        if self._tray is not None:
            self._tray.set_card_visible_checked(pinned)
        self._save()

    def _on_metrics(self, metrics: object) -> None:
        if self._tray is None:
            return
        cpu = getattr(metrics, "cpu_percent", None)
        ram = getattr(metrics, "ram_percent", None)
        if cpu is None or ram is None:
            return
        suffix = " - click-through ON" if self._pet.is_click_through() else ""
        self._tray.set_status_text(
            f"{config.APP_NAME}{suffix}\nCPU {cpu:.0f}%  RAM {ram:.0f}%")

    # -- lifecycle -----------------------------------------------------------

    def _save(self) -> None:
        pos = self._pet.current_position()
        self._settings.pos_x = pos.x()
        self._settings.pos_y = pos.y()
        screen = self._pet.screen()
        self._settings.screen = screen.name() if screen is not None else None
        config.save_settings(self._settings)

    def _quit(self) -> None:
        self._save()
        self._telemetry.stop()
        if self._tray is not None:
            self._tray.shutdown()
        self._app.quit()


def _configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pets", description="Layla & Chico desktop pet")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument("--validate", action="store_true",
                        help="validate the sprite atlas and exit, without a GUI")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    _configure_logging(args.debug)

    if args.validate:
        report = validate_atlas()
        print(report.render())
        return 0 if report.ok else 1

    # Must precede QApplication: PassThrough keeps the real fractional scale factor (1.25,
    # 1.5) instead of rounding it, so logical<->device geometry stays exact and the window
    # does not end up physically larger than intended on a 125% display.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    # Must precede any window: without it, hiding the pet from the tray closes the last
    # visible window and Qt quits the whole app, tray icon and all.
    app.setQuitOnLastWindowClosed(False)

    try:
        pet_app = PetApp(app)
    except SpriteLoadError as exc:
        log.error("%s", exc)
        return 1

    _ = pet_app  # keep a reference; the app object owns everything else
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
