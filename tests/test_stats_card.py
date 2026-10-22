"""`src/ui/stats_card.py` -- the hover status card.

**What this file does not test: how the card looks.** Rendering is checked *structurally* --
the paint runs without aborting, the right set of rows exists, the card grows by one row's
height when a row appears, it anchors where it should. Whether it is beautiful, whether the
shadow falls off pleasantly, whether the text is legible over a terminal: none of that is
asserted anywhere, and it cannot be. See `tests/README.md`.

That distinction is why the degradation tests below compare *heights relative to each other*
rather than to the 174 px and 192 px recorded in PHASE1-LOG.md. Those numbers are true of one
machine with one font fallback (`Segoe UI Variable Text` is not installed on the dev box, so
the card is being judged on `Segoe UI`), and pinning them would make this file a hardware
report rather than a test.

GPU and battery are supplied as synthetic samples throughout. That is deliberate and is what
makes the whole degradation matrix runnable on any machine, including the branch a developer
with an NVIDIA card can never otherwise reach.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QGuiApplication, QImage

from src import config
from src.core.state_machine import STATES
from src.core.telemetry import BatterySample, GpuSample, Metrics
from src.ui import stats_card as sc
from src.ui.stats_card import CardVisibility, StatsCard


@pytest.fixture
def card(qapp: object, qt_cleanup: Callable[[object], object],
         make_metrics: Callable[..., Metrics]) -> StatsCard:
    """A `StatsCard` with one sample already delivered, so it has a row set to reason about."""
    widget = StatsCard()
    qt_cleanup(widget)
    widget.on_metrics(make_metrics())
    widget.on_state_changed("idle-attentive")
    return widget


def force_paint(widget: StatsCard) -> None:
    """Drive `paintEvent` onto an offscreen image.

    Rendering into a `QImage` rather than waiting for the compositor means an exception inside
    the paint surfaces here, synchronously, instead of on some later event-loop turn.
    """
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    widget.render(image)


def visible_rect(widget: StatsCard) -> QRect:
    """The card the user actually sees, in global coordinates.

    **Not** `widget.geometry()`. The widget reserves `shadow_margin()` transparent px on every
    side for the hand-painted shadow, so its frame is 58 px wider and taller than the card and
    sits 29 px outside it. `_reposition` lays out the *visible* card against the anchor and
    subtracts the margin once at the final `move()`, so every anchoring assertion has to add it
    back or it is measuring a boundary nobody can see.

    (The scratch `functional.py` this file replaces asserted on `widget.x()`/`.y()` directly and
    would fail here by exactly one margin. See tests/README.md.)
    """
    rect = widget.card_rect()
    return QRect(widget.x() + rect.x(), widget.y() + rect.y(), rect.width(), rect.height())


GPU_LIVE = GpuSample("RTX 3070", 14.0, 3 * 2 ** 30, 8 * 2 ** 30, 44.9, 48.0)
GPU_FAILED = GpuSample("RTX 3070", None, None, None, None, None)
BATTERY = BatterySample(72.0, False, 5400)


# =============================================================================================
# REGRESSIONS
# =============================================================================================


@pytest.mark.parametrize("state", STATES)
def test_regression_every_state_paints_without_aborting(
        card: StatsCard, state: str, make_metrics: Callable[..., Metrics]) -> None:
    """Guards the `QPoint`/`QPointF` mismatch that killed the process with `qFatal`.

    PHASE1-LOG.md bug 1: `_draw_bolt` passed a `QPoint` to `QPainterPath.moveTo`, which has
    only `QPointF` overloads. A `TypeError` raised inside `paintEvent` makes PyQt6 call
    `qFatal` -- **exit code 9, no Python traceback, nothing in the log**. Every entry into
    `running-playful-jump`, i.e. any heavy CPU load, would have killed the pet silently.

    **How a regression manifests here:** not as a failing assertion but as the whole pytest
    process dying mid-run. There is no `try` that can catch `qFatal`. A run that ends with
    "INTERNALERROR" or simply stops partway through this parametrisation is this bug.
    `paintEvent` now also carries its own `try/except` net, so `_paint_failed_for` is the
    softer signal that something raised and was caught.

    Parametrised over `STATES` rather than over the atlas's rows: card copy is atlas-independent
    (`working-together` has a presentation entry with no art yet), and the glyph painters are
    what is under test. `running-playful-jump` -- the bolt -- is in the list, which is the point.
    """
    card.on_state_changed(state)
    for metrics in (make_metrics(),
                    make_metrics(cpu=100.0, ram=100.0, temp=88.0, gpu=GPU_LIVE, battery=BATTERY),
                    make_metrics(cpu=0.0, ram=0.0, gpu=GPU_FAILED)):
        card.on_metrics(metrics)
        card.resize(card.sizeHint())
        force_paint(card)
    assert card._paint_failed_for is None, f"paintEvent raised for {state!r}"


def test_regression_a_structurally_absent_gpu_omits_the_row(
        card: StatsCard, make_metrics: Callable[..., Metrics]) -> None:
    """`metrics.gpu is None` means "no GPU on this machine" -- the row must not exist at all.

    The degradation policy in one sentence: **emptiness reads as breakage; absence reads as
    design.** A permanently blank GPU row on an Intel laptop looks like the app is broken; no
    GPU row at all looks like the card knows what machine it is on.
    """
    card.on_metrics(make_metrics(gpu=None))
    assert "gpu" not in card._row_keys, sorted(card._row_keys)

    card.on_metrics(make_metrics(battery=None))
    assert "batt" not in card._row_keys, sorted(card._row_keys)


def test_regression_a_failed_gpu_sample_keeps_a_greyed_row(
        card: StatsCard, make_metrics: Callable[..., Metrics]) -> None:
    """`GpuSample` present but every field `None` means "the card is there, this sample failed".

    The row stays, greyed, reading `n/a`, with no meter fill -- because the capability exists
    and silently dropping the row would make the card change shape every time a driver hiccups.

    This is the distinction the card has to get right, and it is invisible from the outside:
    both cases show "no GPU number", and only the layout tells them apart.
    """
    card.on_metrics(make_metrics(gpu=GPU_FAILED))
    rows = {row.key: row for row in card._rows}

    assert "gpu" in rows, "a failed sample must not remove the row"
    assert rows["gpu"].value == sc.NOT_AVAILABLE
    assert rows["gpu"].available is False, "the row must be greyed"
    assert rows["gpu"].fraction is None, "a failed sample must not draw a meter fill"


def test_regression_the_two_gpu_absences_give_different_card_heights(
        card: StatsCard, make_metrics: Callable[..., Metrics]) -> None:
    """The observable consequence of the policy above: absent shrinks the card, failed does not.

    Measured relatively, never against the 174 -> 192 px in PHASE1-LOG.md -- those depend on
    the installed font. What must hold on any machine is `absent < live == failed`.
    """
    def height_for(gpu: GpuSample | None) -> int:
        card.on_metrics(make_metrics(gpu=gpu))
        card.resize(card.sizeHint())
        return card.card_rect().height()

    absent = height_for(None)
    live = height_for(GPU_LIVE)
    failed = height_for(GPU_FAILED)

    assert live > absent, f"the GPU row added no height: {absent} -> {live}"
    assert failed == live, (
        f"a failed sample changed the card's height ({live} -> {failed}); it must keep the row")
    assert live - absent == config.CARD_ROW_HEIGHT, (
        f"the GPU row is worth {live - absent} px, not one CARD_ROW_HEIGHT")


def test_regression_temperature_is_an_inline_suffix_not_its_own_row(
        card: StatsCard, make_metrics: Callable[..., Metrics]) -> None:
    """`84% . 62degC` on the CPU row, so the absent Windows temperature costs no space.

    A dedicated temperature row would make the card two different shapes on two platforms for
    no user benefit -- and on Windows, where `cpu_temp_c` is always `None`, it would be a row
    that exists only to be empty.
    """
    card.on_metrics(make_metrics(temp=None))
    assert {row.key: row for row in card._rows}["cpu"].suffix == ""
    shape_without = card._row_keys

    card.on_metrics(make_metrics(temp=62.0))
    rows = {row.key: row for row in card._rows}
    assert rows["cpu"].suffix == f"62{sc.DEGREE_SIGN}C"
    assert card._row_keys == shape_without, "the temperature added a row"


@pytest.mark.slow
def test_regression_re_hovering_during_the_fade_out_restores_the_card(
        card: StatsCard, pump: Callable[[int], None]) -> None:
    """PHASE1-LOG.md bug 5: the card vanished permanently if re-hovered mid-fade-out.

    The visibility guard returned early on `isVisible()`, which is still true *during* the
    fade; the animation then ran to zero opacity and hid the widget with no timer pending and
    nothing able to bring it back. The card was gone until the app restarted.
    """
    card.set_hovered(True)
    pump(config.CARD_HOVER_SHOW_DELAY_MS + config.CARD_FADE_IN_MS + 150)
    assert card.isVisible()

    card.set_hovered(False)
    pump(config.CARD_HOVER_HIDE_DELAY_MS + int(config.CARD_FADE_OUT_MS * 0.4))
    card.set_hovered(True)                       # caught mid-fade
    pump(config.CARD_FADE_OUT_MS + config.CARD_FADE_IN_MS + 300)

    assert card.isVisible(), "the card did not come back"
    assert card.windowOpacity() > 0.9, f"came back as a ghost at {card.windowOpacity():.2f}"
    card.set_hovered(False)
    pump(config.CARD_HOVER_HIDE_DELAY_MS + config.CARD_FADE_OUT_MS + 200)


# =============================================================================================
# Copy
# =============================================================================================


def test_unknown_state_falls_back_instead_of_raising(card: StatsCard) -> None:
    """A state name with no copy entry gets `DEFAULT_PRESENTATION`, never a KeyError.

    This runs on the path to `paintEvent`, where a raise is fatal (see the `qFatal` regression).
    """
    card.on_state_changed("this-state-does-not-exist")
    assert card._presentation is config.DEFAULT_PRESENTATION
    assert sc.presentation_for("nope") is config.DEFAULT_PRESENTATION
    force_paint(card)
    assert card._paint_failed_for is None


def test_an_unknown_placeholder_degrades_to_an_em_dash(
        card: StatsCard, monkeypatch: pytest.MonkeyPatch) -> None:
    """Copy is `str.format_map` over a forgiving mapping, so a typo degrades rather than crashes.

    `STATE_PRESENTATION` is user-editable data. A mistyped `{cpu}` must cost a dash on the card,
    not the process.

    Patched with `monkeypatch.setitem` rather than assigned and deleted by hand: other tests
    iterate this dict, and a mid-test failure that leaked a `__bogus__` state into it would
    break them in a way that looks unrelated.
    """
    bogus = config.StatePresentation("Hi {nonexistent}", "{alsomissing} here", "#6FD08C", "dot")
    monkeypatch.setitem(config.STATE_PRESENTATION, "__bogus__", bogus)

    card.on_state_changed("__bogus__")
    assert card._format(card._presentation.title) == f"Hi {sc.EM_DASH}"
    force_paint(card)
    assert card._paint_failed_for is None


@pytest.mark.parametrize(
    "template",
    ["unbalanced {brace", "positional {0}", "numeric {cpu:.1f}", "{cpu!z}", "{}"],
)
def test_malformed_copy_templates_degrade(card: StatsCard, template: str) -> None:
    """Every way `format_map` itself can reject a string, not just missing keys.

    `{0}` and `{}` raise IndexError, an unbalanced brace raises ValueError, `{cpu!z}` an
    unknown conversion. The forgiving *mapping* handles none of those -- only a `try` around
    the whole call does.
    """
    assert isinstance(card._format(template), str)


def test_an_invalid_accent_or_unknown_glyph_does_not_kill_the_paint(
        card: StatsCard, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other two user-editable fields in a `StatePresentation`.

    `QColor("not-a-colour")` is invalid rather than an exception, and an unknown glyph key has
    to fall back to a painter that exists.
    """
    monkeypatch.setitem(config.STATE_PRESENTATION, "__bogus2__",
                        config.StatePresentation("T", "S", "not-a-colour", "no-such-glyph"))
    card.on_state_changed("__bogus2__")
    force_paint(card)
    assert card._paint_failed_for is None


def test_every_shipped_glyph_has_a_painter() -> None:
    """A copy entry naming a glyph that does not exist silently draws the fallback dot."""
    for state, presentation in config.STATE_PRESENTATION.items():
        assert presentation.glyph in sc.GLYPH_PAINTERS, (
            f"{state!r} uses glyph {presentation.glyph!r}, which has no painter")


def test_on_metrics_ignores_a_payload_that_is_not_metrics(card: StatsCard) -> None:
    """A mis-wired signal must not take the card's paint down with it."""
    before = card._row_keys
    card.on_metrics("not a Metrics")
    card.on_metrics(None)
    assert card._row_keys == before


# =============================================================================================
# Layout stability
# =============================================================================================


def test_size_hint_is_stable_across_samples_of_the_same_shape(
        card: StatsCard, make_metrics: Callable[..., Metrics]) -> None:
    """Relayout only when the *set* of visible rows changes, or the card twitches every poll.

    The height must not move as CPU crosses 9 -> 10 -> 100%, which is why the value column has
    a floor at the measured width of "100%".
    """
    card.on_metrics(make_metrics(cpu=10.0, ram=50.0))
    baseline = card.sizeHint()
    for cpu, ram in ((99.0, 84.0), (0.0, 100.0), (7.0, 7.0), (100.0, 100.0), (9.0, 9.0)):
        card.on_metrics(make_metrics(cpu=cpu, ram=ram))
        assert card.sizeHint() == baseline, f"size changed at cpu={cpu} ram={ram}"


def test_size_hint_grows_and_shrinks_with_the_row_set(
        card: StatsCard, make_metrics: Callable[..., Metrics]) -> None:
    """One row's worth per row, in both directions."""
    card.on_metrics(make_metrics())
    two_rows = card.sizeHint().height()

    card.on_metrics(make_metrics(gpu=GPU_LIVE))
    assert card.sizeHint().height() == two_rows + config.CARD_ROW_HEIGHT

    card.on_metrics(make_metrics(gpu=GPU_LIVE, battery=BATTERY))
    assert card.sizeHint().height() == two_rows + 2 * config.CARD_ROW_HEIGHT

    card.on_metrics(make_metrics())
    assert card.sizeHint().height() == two_rows


def test_the_widget_is_wider_than_the_visible_card(card: StatsCard) -> None:
    """The shadow is painted into a transparent margin the widget reserves around itself.

    Recorded in PHASE1-LOG.md because it is a trap: the widget is 358 px wide where the card is
    300. Anything reading `card.width()` and expecting the *visible* card is wrong by 58 px --
    `card_rect()` is the accessor that means what it says.
    """
    card.resize(card.sizeHint())
    margin = card.shadow_margin()
    assert card.width() == card.card_size().width() + 2 * margin
    assert card.card_rect().width() == config.CARD_WIDTH
    assert card.card_rect().topLeft().x() == margin


# =============================================================================================
# Anchoring
# =============================================================================================


def test_the_card_sits_below_and_centred_on_the_pet_in_open_space(card: StatsCard) -> None:
    """The default placement, which is what the reference image shows."""
    available = QGuiApplication.primaryScreen().availableGeometry()
    card.resize(card.sizeHint())
    anchor = QRect(available.center().x() - 96, available.top() + 200, 192, 208)
    card.set_anchor(anchor)

    seen = visible_rect(card)
    assert seen.top() >= anchor.bottom() + config.CARD_GAP_PX - 1, f"{seen} vs anchor {anchor}"
    assert abs(seen.center().x() - anchor.center().x()) <= 1


def test_the_card_flips_above_the_pet_near_the_bottom_edge(card: StatsCard) -> None:
    """Rather than being clamped into the taskbar or hanging off the screen."""
    available = QGuiApplication.primaryScreen().availableGeometry()
    card.resize(card.sizeHint())
    anchor = QRect(available.center().x() - 96, available.bottom() - 208, 192, 208)
    card.set_anchor(anchor)

    seen = visible_rect(card)
    assert seen.bottom() <= anchor.top() - config.CARD_GAP_PX + 1, f"{seen} vs anchor {anchor}"


@pytest.mark.parametrize("side", ["left", "right"])
def test_the_card_is_clamped_inside_the_available_geometry(card: StatsCard, side: str) -> None:
    """A pet dragged to a screen edge must not push its card off the screen."""
    available = QGuiApplication.primaryScreen().availableGeometry()
    card.resize(card.sizeHint())
    margin = config.CARD_EDGE_MARGIN

    if side == "left":
        card.set_anchor(QRect(available.left(), available.top() + 200, 192, 208))
        seen = visible_rect(card)
        assert seen.left() == available.left() + margin, seen
    else:
        card.set_anchor(QRect(available.right() - 192, available.top() + 200, 192, 208))
        seen = visible_rect(card)
        assert seen.right() == available.right() - margin, seen

    assert available.contains(seen), f"{seen} escaped {available}"


def test_set_anchor_offset_reserves_extra_clearance(card: StatsCard) -> None:
    """Contract 2.8: the hook the deferred control "pill" will need, called with 0 today."""
    available = QGuiApplication.primaryScreen().availableGeometry()
    card.resize(card.sizeHint())
    anchor = QRect(available.center().x() - 96, available.top() + 200, 192, 208)

    card.set_anchor_offset(0)
    card.set_anchor(anchor)
    plain = card.y()

    card.set_anchor_offset(40)
    card.set_anchor(anchor)
    assert card.y() == plain + 40

    card.set_anchor_offset(0)


# =============================================================================================
# Visibility
# =============================================================================================


@pytest.mark.slow
def test_hover_to_peek_respects_the_show_and_hide_delays(
        card: StatsCard, pump: Callable[[int], None]) -> None:
    """250 ms before showing, 700 ms before hiding.

    The show delay stops a cursor merely passing over the pet from flashing the card; the hide
    delay is what makes the card readable, since you have to cross the gap to reach it.
    """
    card.hide()
    pump(30)
    assert card.visibility_state() is CardVisibility.HIDDEN

    card.set_hovered(True)
    pump(int(config.CARD_HOVER_SHOW_DELAY_MS * 0.4))
    assert not card.isVisible(), "shown before the show delay elapsed"

    pump(config.CARD_HOVER_SHOW_DELAY_MS + config.CARD_FADE_IN_MS + 150)
    assert card.isVisible()
    assert card.visibility_state() is CardVisibility.PEEKING

    card.set_hovered(False)
    pump(int(config.CARD_HOVER_HIDE_DELAY_MS * 0.4))
    assert card.isVisible(), "hidden before the hide delay elapsed"

    # The assertion that carries the meaning is the one above -- that it does NOT hide early.
    # This one only has to establish that it hides *eventually*, so it waits rather than
    # budgeting a fixed window. A fixed budget was flaky at ~1 run in 5: the nominal cost is
    # 700 ms of hide timer plus a 160 ms fade, but under a loaded event loop each of those can
    # start late, and the old 250 ms of headroom did not always absorb it. Waiting keeps the
    # test's real subject (no early hide) exact while making the outcome deterministic; a card
    # that genuinely never hides still fails, just via the timeout.
    deadline_ms = config.CARD_HOVER_HIDE_DELAY_MS + config.CARD_FADE_OUT_MS + 3000
    waited = 0
    while card.isVisible() and waited < deadline_ms:
        pump(50)
        waited += 50
    assert not card.isVisible(), f"still visible {waited} ms after un-hover"


@pytest.mark.slow
def test_the_cursor_entering_the_card_cancels_the_pending_hide(
        card: StatsCard, pump: Callable[[int], None]) -> None:
    """Otherwise the card is unreadable: it would fade as you moved to look at it."""
    card.set_hovered(True)
    pump(config.CARD_HOVER_SHOW_DELAY_MS + config.CARD_FADE_IN_MS + 150)
    card.set_hovered(False)
    pump(int(config.CARD_HOVER_HIDE_DELAY_MS * 0.3))

    card.enterEvent(None)
    pump(config.CARD_HOVER_HIDE_DELAY_MS + config.CARD_FADE_OUT_MS + 250)
    assert card.isVisible()

    card.leaveEvent(None)
    pump(config.CARD_HOVER_HIDE_DELAY_MS + config.CARD_FADE_OUT_MS + 250)
    assert not card.isVisible()


@pytest.mark.slow
def test_pinning_holds_the_card_open_regardless_of_hover(
        card: StatsCard, pump: Callable[[int], None]) -> None:
    """Click-to-pin and tray-to-pin, and `pinned_changed` emitted only on a real change."""
    emitted: list[bool] = []
    card.pinned_changed.connect(emitted.append)

    card.set_pinned(True)
    pump(config.CARD_FADE_IN_MS + 150)
    assert card.isVisible()
    assert card.visibility_state() is CardVisibility.PINNED
    assert emitted == [True]

    card.set_pinned(True)
    assert emitted == [True], "pinning an already-pinned card re-emitted"

    card.set_hovered(False)
    pump(config.CARD_HOVER_HIDE_DELAY_MS + config.CARD_FADE_OUT_MS + 200)
    assert card.isVisible(), "a pinned card must ignore un-hover"

    card.toggle_pinned()
    assert card.is_pinned() is False
    assert emitted == [True, False]
    pump(config.CARD_FADE_OUT_MS + 250)
    assert not card.isVisible()


@pytest.mark.slow
def test_unpinning_while_hovered_demotes_to_a_peek(
        card: StatsCard, pump: Callable[[int], None]) -> None:
    """The card should not snap away under a cursor that is still on the pet."""
    card.set_pinned(True)
    pump(config.CARD_FADE_IN_MS + 150)
    card.set_hovered(True)
    card.set_pinned(False)
    pump(config.CARD_FADE_OUT_MS + 150)
    assert card.isVisible()

    card.set_hovered(False)
    pump(config.CARD_HOVER_HIDE_DELAY_MS + config.CARD_FADE_OUT_MS + 300)
    assert not card.isVisible()
