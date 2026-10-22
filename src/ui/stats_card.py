"""The translucent status card that peeks out from under the pet.

A sibling top-level, never a child of ``PetWindow``: a translucent child would be clipped to
the pet's 192x208 box, and the card is wider than the pet is.

Zero child widgets, by design
-----------------------------
Everything -- surface, rim, glyph, title, subtitle and every meter -- is drawn by one
``paintEvent``. Two measured reasons, neither of them stylistic:

* A Qt stylesheet ``border-radius`` **does not clip children**. A ``QLabel`` positioned near a
  rounded corner renders straight through the curve, which is exactly where this design's whole
  identity lives.
* Stylesheet corner antialiasing on a ``WA_TranslucentBackground`` top-level blends the curve
  against transparent *black*, leaving a dark fringe on the soft edge. ``QPainter`` with
  ``Antialiasing`` composites against the real backdrop instead.

Degradation policy (contract 5.2) -- the one rule worth reading twice
---------------------------------------------------------------------
* ``metrics.gpu is None`` / ``metrics.battery is None`` mean the capability is *structurally*
  absent on this machine. The row is **omitted entirely**. A permanently dead "GPU  n/a" line
  is visual debt the user stares at forever; emptiness reads as breakage, absence reads as
  design.
* ``metrics.gpu is not None`` but ``gpu.load_percent is None`` means the capability exists and
  *this sample* failed. The row **stays**, greyed, reading ``n/a``. It will come back, and a
  row that appears and disappears every few seconds is worse than a row that says so.
* Temperature is an **inline suffix** on its row (``84% . 62degC``), never a row of its own, so
  it costs no vertical space on the (Windows, Phase 1) machines where it is always ``None``.

On the dev machine the card is exactly two rows, CPU and RAM: ``sensors_temperatures`` does not
exist on Windows, ``sensors_battery()`` returns ``None`` on a desktop, and no GPU library is
installed. That two-row form is the *default* here, not a degraded edge case, and the visuals
are tuned at that size.

The widget is bigger than the card
---------------------------------
The card casts a hand-painted drop shadow (``config.CARD_SHADOW``), and a shadow needs
somewhere to land. So the widget reserves a **transparent margin** of
:meth:`StatsCard.shadow_margin` px on every side and the visible card occupies only
:meth:`StatsCard.card_rect` inside it. Three consequences, all handled here and all easy to
get wrong:

* ``sizeHint`` returns the *padded* size; the visible card is still exactly
  ``config.CARD_WIDTH`` wide.
* ``_reposition`` positions the **card**, not the widget -- it lays the card out against the
  anchor and the screen edges exactly as before, then moves the widget one margin up and left.
  Skipping that step would push the card a couple of dozen px off the pet.
* Hover is tested against ``card_rect``, not against the widget. The margin is transparent;
  a cursor in it is over the desktop as far as the user is concerned, so it must not count as
  entering the card. ``enterEvent`` alone cannot express that -- :meth:`mouseMoveEvent` does
  the real transition tracking.

Colour restraint: **two colours maximum**. The state accent, plus ``config.ACCENT_DANGER`` for
any metric over its own high threshold -- so a pegged RAM stays visible while CPU drives the
state. That restraint is most of why the reference image looks calm.

Thread model: **GUI thread only**, everything, without exception. ``on_metrics`` is a queued
slot fed by ``TelemetryService.metrics_ready``, which re-emits on the GUI thread.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Callable, Final, Mapping

from PyQt6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import QWidget

from src import config
from src.core.telemetry import Metrics
from src.ui._overlay import apply_overlay_flags

log = logging.getLogger(__name__)


# =============================================================================
# COPY FRAGMENTS
#
# Not in config.py's STATE COPY block on purpose: these are not per-state copy, they are the
# two sentinel tokens the renderer itself needs in order to never raise. Everything a *state*
# says lives in config.STATE_PRESENTATION.
# =============================================================================

#: Shown for a capability that exists but whose current sample failed.
NOT_AVAILABLE: Final[str] = "n/a"

#: Substituted for an unknown ``{placeholder}`` in user-edited copy. Degrading visually beats
#: raising inside ``paintEvent``, which in Qt means a traceback per repaint forever.
EM_DASH: Final[str] = "—"

DEGREE_SIGN: Final[str] = "°"

#: Leader on a row's inline unit, as in ``84% . 62degC`` (contract 5.2). It is prepended to
#: the unit rather than appended to the value, because the two are drawn as separate columns
#: -- see :class:`_Row`.
SUFFIX_SEP: Final[str] = "  ·  "

#: Only used to satisfy a ``{pet}`` placeholder; the shipped copy does not use one.
PET_NAMES: Final[str] = "Layla & Chico"

#: The widest string a percentage row can ever put in the number column. Measured, not
#: assumed: it is what keeps that column -- and so the meter track beside it -- one width
#: while the digits change. The value font uses tabular figures, so every digit is the same
#: advance and only the digit *count* can move this.
_WIDEST_VALUE: Final[str] = "100%"


# =============================================================================
# DERIVED LAYOUT
#
# No raw pixel literals below this banner -- every number is a config constant or an
# arithmetic consequence of one. Where a gap had no dedicated constant it is derived from
# CARD_SECTION_GAP rather than invented, so the card keeps one rhythm if that value is tuned.
# =============================================================================

#: Horizontal gap between the text block and the glyph, and between a row's columns.
_COL_GAP: Final[int] = config.CARD_SECTION_GAP

#: Breathing room between the title and the subtitle baseline blocks. Half the section gap:
#: the pair must read as one unit, distinct from the gap down to the meters.
_HEADER_LEADING: Final[int] = config.CARD_SECTION_GAP // 2

#: A meter at 1% must read as a dot rather than as nothing. One meter-height of width is
#: exactly a circle, which is the smallest mark that still looks deliberate.
_METER_MIN_FILL: Final[int] = config.CARD_METER_HEIGHT

#: Floor on the meter track. Six meter-heights is the shortest track on which the eye can
#: still judge a proportion; below it the bar stops being a bar. The value column is allowed
#: to eat the meter's width down to here and no further.
_MIN_METER_W: Final[int] = config.CARD_METER_HEIGHT * 6

#: Alpha for the glyph disc behind the accent-coloured mark. Low enough that the disc is a
#: halo rather than a second colour mass.
_GLYPH_DISC_ALPHA: Final[int] = 46
_GLYPH_RING_ALPHA: Final[int] = 92

#: How far the shadow's clip reaches back *under* the card, in px.
#:
#: The shadow is clipped to "everything outside the surface path" so it cannot tint the card
#: itself. Clipping exactly on that path leaves a hairline seam: Qt's clip edge is not
#: antialiased the way the surface's own edge is, so the two do not cancel along the corner
#: curves and a light thread appears between card and shadow. Letting the shadow run 1 px
#: under the surface hides the seam beneath opaque paint at no visual cost.
_SHADOW_CLIP_BITE: Final[float] = 1.0


class CardVisibility(enum.Enum):
    """What the card is currently doing. Callers need no timing knowledge beyond this."""

    HIDDEN = 0
    PEEKING = 1
    PINNED = 2


# =============================================================================
# GLYPHS
#
# Painted paths, not an icon font. A font would add an asset dependency plus a
# font-availability failure mode (a missing glyph renders as a tofu box in the one place the
# design cannot afford one), for six marks that are a dozen path segments each.
# =============================================================================


def _pen(colour: QColor, width: float) -> QPen:
    """A round-capped, round-joined stroke -- the soft language of the reference art."""
    pen = QPen(colour, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _draw_dot(painter: QPainter, box: QRectF, colour: QColor) -> None:
    """A calm filled disc: the resting state's mark.

    0.30, not 0.24: measured against the reference's tick, which fills roughly a third of its
    disc. At 0.24 the dot was a 6 px speck inside a 28 px halo and read as a rendering
    artefact rather than as a mark.
    """
    r = box.width() * 0.30
    centre = box.center()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(colour)
    painter.drawEllipse(centre, r, r)


def _draw_spark(painter: QPainter, box: QRectF, colour: QColor) -> None:
    """A four-point sparkle -- concave sides, so it reads as motion rather than as a star."""
    cx, cy = box.center().x(), box.center().y()
    rx, ry = box.width() * 0.46, box.height() * 0.46
    waist = 0.30  # how far the control points pull in; smaller == spikier
    path = QPainterPath()
    path.moveTo(cx, cy - ry)
    path.quadTo(cx + rx * waist, cy - ry * waist, cx + rx, cy)
    path.quadTo(cx + rx * waist, cy + ry * waist, cx, cy + ry)
    path.quadTo(cx - rx * waist, cy + ry * waist, cx - rx, cy)
    path.quadTo(cx - rx * waist, cy - ry * waist, cx, cy - ry)
    path.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(colour)
    painter.drawPath(path)


def _draw_bolt(painter: QPainter, box: QRectF, colour: QColor) -> None:
    """A lightning bolt for heavy load."""
    w, h = box.width(), box.height()
    x, y = box.left(), box.top()

    def pt(fx: float, fy: float) -> QPointF:
        # QPointF, not QPoint: QPainterPath has no integer overload, so a QPoint here is a
        # TypeError raised from inside paintEvent -- which PyQt6 turns into an abort().
        return QPointF(x + fx * w, y + fy * h)

    path = QPainterPath()
    path.moveTo(pt(0.60, 0.02))
    path.lineTo(pt(0.22, 0.56))
    path.lineTo(pt(0.46, 0.56))
    path.lineTo(pt(0.38, 0.98))
    path.lineTo(pt(0.78, 0.42))
    path.lineTo(pt(0.54, 0.42))
    path.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(colour)
    painter.drawPath(path)


def _draw_zzz(painter: QPainter, box: QRectF, colour: QColor) -> None:
    """Two Zs, the smaller trailing up and right -- sleep, without a font.

    Two, not three. Rendered and looked at: the mark box is ~46% of a 28 px disc, so three Zs
    put the smallest at ~3 px tall with a 1 px stroke and the trio overlapped into an
    illegible scribble. Two, sized 0.58 and 0.38 with a clear diagonal offset between them,
    still reads as "Zs" at that size -- which is the entire job of the glyph.
    """
    w, h = box.width(), box.height()
    x, y = box.left(), box.top()
    # (left, top, size, stroke) as fractions: bigger at the bottom-left, drifting up-right.
    letters = ((0.00, 0.42, 0.58, 0.15), (0.58, 0.00, 0.38, 0.13))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for fx, fy, fs, fw in letters:
        sw, sh = fs * w, fs * h
        lx, ly = x + fx * w, y + fy * h
        path = QPainterPath()
        path.moveTo(lx, ly)
        path.lineTo(lx + sw, ly)
        path.lineTo(lx, ly + sh)
        path.lineTo(lx + sw, ly + sh)
        painter.setPen(_pen(colour, max(1.2, fw * w)))
        painter.drawPath(path)


def _draw_alert(painter: QPainter, box: QRectF, colour: QColor) -> None:
    """An exclamation: a tapered bar over a dot. Legible at 14 px, which a triangle is not."""
    cx = box.center().x()
    w, h = box.width(), box.height()
    stroke = max(1.5, w * 0.16)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_pen(colour, stroke))
    painter.drawLine(QPointF(cx, box.top() + h * 0.08),
                     QPointF(cx, box.top() + h * 0.58))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(colour)
    painter.drawEllipse(QRectF(cx - stroke / 2, box.top() + h * 0.80, stroke, stroke))


def _draw_check(painter: QPainter, box: QRectF, colour: QColor) -> None:
    """The reference image's mark: a short-armed tick, optically centred (it sits low)."""
    w, h = box.width(), box.height()
    x, y = box.left(), box.top()
    path = QPainterPath()
    path.moveTo(x + 0.14 * w, y + 0.52 * h)
    path.lineTo(x + 0.40 * w, y + 0.76 * h)
    path.lineTo(x + 0.86 * w, y + 0.24 * h)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_pen(colour, max(1.6, w * 0.15)))
    painter.drawPath(path)


GLYPH_PAINTERS: Final[dict[str, Callable[[QPainter, QRectF, QColor], None]]] = {
    "dot": _draw_dot,
    "spark": _draw_spark,
    "bolt": _draw_bolt,
    "zzz": _draw_zzz,
    "alert": _draw_alert,
    "check": _draw_check,
}


# =============================================================================
# COPY SUBSTITUTION  (must never be able to raise inside paintEvent)
# =============================================================================


class _ForgivingContext(dict):
    """A format mapping whose unknown keys resolve to an em-dash instead of raising.

    ``config.STATE_PRESENTATION`` is user-editable copy. A typo there must degrade to a visible
    dash, not to a ``KeyError`` thrown from ``paintEvent`` -- which Qt would re-raise on every
    repaint, i.e. forever.
    """

    def __missing__(self, key: str) -> str:
        log.debug("state copy referenced unknown placeholder %r", key)
        return EM_DASH


def _fmt(value: float | None, suffix: str = "") -> str:
    """Whole-number rendering of an optional metric, or ``n/a``. Any thread."""
    if value is None:
        return NOT_AVAILABLE
    return f"{value:.0f}{suffix}"


def presentation_context(m: Metrics) -> Mapping[str, str]:
    """Pre-format every metric as a **string** for ``str.format_map``. Any thread.

    Strings throughout, deliberately: a missing GPU then yields ``"n/a"`` in the copy rather
    than a ``TypeError`` from formatting ``None``. The cost is that a numeric format spec in
    user copy (``{cpu:.1f}``) fails -- which :meth:`StatsCard._format` catches and renders as
    the raw template, still without raising.
    """
    gpu = m.gpu
    battery = m.battery
    temp = m.cpu_temp_c if m.cpu_temp_c is not None else (gpu.temp_c if gpu else None)
    idle_min = None if m.idle_seconds is None else m.idle_seconds / 60.0
    return _ForgivingContext(
        pet=PET_NAMES,
        cpu=_fmt(m.cpu_percent),
        ram=_fmt(m.ram_percent),
        swap=_fmt(m.swap_percent),
        disk=_fmt(m.disk_percent),
        temp=_fmt(temp),
        idle_min=_fmt(idle_min),
        gpu=_fmt(gpu.load_percent) if gpu else NOT_AVAILABLE,
        vram=_fmt(gpu.vram_percent) if gpu else NOT_AVAILABLE,
        battery=_fmt(battery.percent) if battery else NOT_AVAILABLE,
    )


def presentation_for(state: str) -> config.StatePresentation:
    """Copy + accent + glyph for ``state``, falling back rather than raising. Any thread."""
    presentation = config.STATE_PRESENTATION.get(state)
    if presentation is None:
        log.debug("no STATE_PRESENTATION entry for %r; using the default copy", state)
        return config.DEFAULT_PRESENTATION
    return presentation


# =============================================================================
# ROW MODEL
# =============================================================================


@dataclass(frozen=True, slots=True)
class _Row:
    """One metric line. ``key`` is what the visible-row frozenset is built from.

    ``value`` and ``suffix`` are separate strings on purpose. Rendered and compared: with the
    temperature concatenated into ``value``, the whole thing is right-aligned, so a row that
    has a suffix pushes its own percentage left and the percentages stop forming a column --
    ``94% . 62degC`` above a lone ``88%`` reads as ragged. Split, the numbers right-align in
    one fixed column and the units hang off to its right in the muted colour, which is both
    the calmer reading and the reference's own type hierarchy.
    """

    key: str
    label: str
    value: str
    suffix: str  # "" when the row has no inline unit; drawn dimmed, right of the number column
    fraction: float | None  # None => no meter fill (this sample failed)
    danger: bool

    @property
    def available(self) -> bool:
        return self.fraction is not None


def _build_rows(m: Metrics) -> tuple[_Row, ...]:
    """Decide which rows exist for this sample. GUI thread (called from ``on_metrics``).

    This function *is* the degradation policy; see the module docstring. CPU and RAM always
    exist because psutil always supplies them.
    """
    cpu_suffix = ""
    if m.cpu_temp_c is not None:
        # Inline, never its own row: on Windows this is always absent, and a row that only
        # exists on Linux would make the card two different shapes for no user benefit.
        cpu_suffix = f"{m.cpu_temp_c:.0f}{DEGREE_SIGN}C"

    rows: list[_Row] = [
        _Row("cpu", "CPU", f"{m.cpu_percent:.0f}%", cpu_suffix,
             m.cpu_percent / 100.0, m.cpu_percent >= config.CPU_HIGH),
        _Row("ram", "RAM", f"{m.ram_percent:.0f}%", "",
             m.ram_percent / 100.0, m.ram_percent >= config.RAM_HIGH),
    ]

    gpu = m.gpu
    if gpu is not None:  # structurally present -- the row exists even if this sample failed
        if gpu.load_percent is None:
            rows.append(_Row("gpu", "GPU", NOT_AVAILABLE, "", None, False))
        else:
            suffix = ""
            if gpu.temp_c is not None:
                suffix = f"{gpu.temp_c:.0f}{DEGREE_SIGN}C"
            # No dedicated GPU threshold exists in config; CPU_HIGH is this project's "a
            # processor is pegged" line and TEMP_HIGH_C is its thermal alarm, so the GPU row
            # borrows both rather than inventing numbers here (CLAUDE.md: thresholds in config).
            hot = gpu.temp_c is not None and gpu.temp_c >= config.TEMP_HIGH_C
            rows.append(_Row("gpu", "GPU", f"{gpu.load_percent:.0f}%", suffix,
                             gpu.load_percent / 100.0,
                             gpu.load_percent >= config.CPU_HIGH or hot))

    battery = m.battery
    if battery is not None:
        suffix = "AC" if battery.plugged else ""
        # Never danger-coloured: the rule is "over its own *high* threshold", and a high
        # battery is good news. A low-battery threshold would belong in config.py, not here.
        rows.append(_Row("batt", "BATT", f"{battery.percent:.0f}%", suffix,
                         battery.percent / 100.0, False))

    return tuple(rows)


# =============================================================================
# CARD
# =============================================================================


class StatsCard(QWidget):
    """The hover/pin telemetry card. **GUI thread only** -- construction, slots and all.

    Owns no child widgets and no layout; :meth:`paintEvent` draws the whole surface.
    """

    pinned_changed = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the card hidden. GUI thread; a ``QApplication`` must already exist."""
        super().__init__(parent)
        # accept_focus=False: the card must never steal the caret from whatever the user is
        # typing in. It has no keyboard affordances at all.
        apply_overlay_flags(self, accept_focus=False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setMouseTracking(True)

        self._metrics: Metrics = Metrics.placeholder()
        self._state: str = ""
        self._presentation: config.StatePresentation = config.DEFAULT_PRESENTATION
        self._rows: tuple[_Row, ...] = _build_rows(self._metrics)
        self._row_keys: frozenset[str] = frozenset(r.key for r in self._rows)

        self._anchor = QRect()
        self._anchor_offset = 0

        self._pinned = False
        self._hovered = False       # cursor is over the *pet*
        self._cursor_inside = False  # cursor is over the *card*

        #: State name whose paint has already been reported as failing; see ``paintEvent``.
        self._paint_failed_for: str | None = None

        self._title_font, self._subtitle_font, self._label_font, self._value_font = self._fonts()
        self._fm_title = QFontMetrics(self._title_font)
        self._fm_subtitle = QFontMetrics(self._subtitle_font)
        self._fm_label = QFontMetrics(self._label_font)
        self._fm_value = QFontMetrics(self._value_font)

        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.setInterval(config.CARD_HOVER_SHOW_DELAY_MS)
        self._show_timer.timeout.connect(self._on_show_timeout)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(config.CARD_HOVER_HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self._on_hide_timeout)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._on_fade_finished)
        self._fade_to_visible = False

        self.setWindowOpacity(0.0)
        self.resize(self.sizeHint())
        self.hide()

    # -- fonts ----------------------------------------------------------------------------

    def _fonts(self) -> tuple[QFont, QFont, QFont, QFont]:
        """Build the four fonts once. Sizes and families come from config."""

        def make(px: int, weight: QFont.Weight) -> QFont:
            font = QFont()
            font.setFamilies(list(config.CARD_FONT_FAMILIES))
            font.setPixelSize(px)
            font.setWeight(weight)
            return font

        title = make(config.CARD_TITLE_PX, QFont.Weight.DemiBold)
        subtitle = make(config.CARD_SUBTITLE_PX, QFont.Weight.Normal)
        label = make(config.CARD_LABEL_PX, QFont.Weight.Medium)
        value = make(config.CARD_VALUE_PX, QFont.Weight.DemiBold)
        # Tabular figures: proportional digits make a live percentage jitter sideways as it
        # changes, which is the single most distracting thing a always-on-top widget can do.
        _apply_tabular_figures(value)
        _apply_tabular_figures(subtitle)
        return title, subtitle, label, value

    # -- telemetry ------------------------------------------------------------------------

    @pyqtSlot(object)
    def on_metrics(self, metrics: object) -> None:
        """Adopt a fresh sample. **GUI thread** (queued from ``TelemetryService``).

        Resizes *only* when the set of visible rows changes. Resizing on every poll would make
        the card twitch at the telemetry interval even though nothing about its shape moved.
        """
        if not isinstance(metrics, Metrics):
            log.error("StatsCard got %r, not Metrics; ignoring", type(metrics).__name__)
            return
        self._metrics = metrics
        self._rows = _build_rows(metrics)
        keys = frozenset(r.key for r in self._rows)
        if keys != self._row_keys:
            log.debug("card row set changed %s -> %s", sorted(self._row_keys), sorted(keys))
            self._row_keys = keys
            self.resize(self.sizeHint())
            self._reposition()
        self.update()

    @pyqtSlot(str)
    def on_state_changed(self, state: str) -> None:
        """Adopt a new animation state's copy, accent and glyph. **GUI thread.**"""
        self._state = state
        self._presentation = presentation_for(state)
        self.update()

    # -- anchoring ------------------------------------------------------------------------

    @pyqtSlot(QRect)
    def set_anchor(self, anchor: QRect) -> None:
        """Follow the pet. **GUI thread**; ``anchor`` is global frame geometry.

        The move is deliberately *not* animated: an animated follow visibly lags the pet
        during a drag, which looks like a bug rather than like polish.
        """
        self._anchor = QRect(anchor)
        self._reposition()

    def set_anchor_offset(self, px: int) -> None:
        """Reserve ``px`` of extra clearance between pet and card. **GUI thread.**

        Phase 1 calls this with ``0``. It exists so the deferred control "pill" from the
        reference image can be inserted later without the card needing to know it is there.
        """
        self._anchor_offset = max(0, int(px))
        self._reposition()

    def _reposition(self) -> None:
        """Centre below the anchor, flipping above at the screen bottom. **GUI thread.**

        Every number below is about the **visible card**, never the widget: the shadow margin
        is transparent, so laying the widget out against the anchor would sit the card a
        margin's worth away from the pet and leave ``CARD_EDGE_MARGIN`` measuring from a
        boundary the user cannot see. The margin is subtracted once, at the final ``move``.
        """
        margin_px = self.shadow_margin()
        size = self.card_size()  # the *visible* card, already net of the shadow margin
        if size.isEmpty():
            hint = self.sizeHint()
            size = QSize(max(0, hint.width() - margin_px * 2),
                         max(0, hint.height() - margin_px * 2))

        anchor = self._anchor
        screen = None
        if not anchor.isNull():
            screen = QGuiApplication.screenAt(anchor.center())
        if screen is None:
            screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:  # pragma: no cover - no display at all
            return
        avail = screen.availableGeometry()
        if anchor.isNull():
            anchor = QRect(avail.center(), QSize(0, 0))

        margin = config.CARD_EDGE_MARGIN
        gap = config.CARD_GAP_PX + self._anchor_offset

        x = anchor.center().x() - size.width() // 2
        y = anchor.bottom() + gap

        # Flip above rather than clamp: a card clamped at the bottom would overlap the pet and
        # cover the thing the user is pointing at.
        if y + size.height() > avail.bottom() - margin:
            flipped = anchor.top() - gap - size.height()
            if flipped >= avail.top() + margin:
                y = flipped

        min_x = avail.left() + margin
        max_x = avail.right() + 1 - margin - size.width()
        min_y = avail.top() + margin
        max_y = avail.bottom() + 1 - margin - size.height()
        x = min(max(x, min_x), max_x) if max_x >= min_x else min_x
        y = min(max(y, min_y), max_y) if max_y >= min_y else min_y

        # (x, y) is where the *card* goes; the widget's origin is one margin further out.
        self.move(int(x) - margin_px, int(y) - margin_px)

    # -- visibility -----------------------------------------------------------------------

    def visibility_state(self) -> CardVisibility:
        """What the card is doing right now. **GUI thread.**"""
        if self._pinned:
            return CardVisibility.PINNED
        if self.isVisible() and self._fade_to_visible:
            return CardVisibility.PEEKING
        return CardVisibility.HIDDEN

    @pyqtSlot(bool)
    def set_hovered(self, hovered: bool) -> None:
        """The cursor entered/left the **pet**. **GUI thread.**

        Show is delayed by ``CARD_HOVER_SHOW_DELAY_MS`` so a cursor merely passing over the pet
        does not flash the card; hide is delayed by ``CARD_HOVER_HIDE_DELAY_MS`` so the user has
        time to move onto the card itself, where :meth:`enterEvent` cancels the hide outright.
        """
        self._hovered = hovered
        if hovered:
            self._hide_timer.stop()
            if self._pinned:
                return
            if self.isVisible():
                # Still mapped, but possibly mid fade-*out*: re-entering the pet during those
                # ~160 ms must pull the card back, not let it finish vanishing with no timer
                # left pending. Already fading in (or settled at full opacity) -> nothing to do.
                if not self._fade_to_visible:
                    self._fade_in()
                return
            self._show_timer.start()
        else:
            self._show_timer.stop()
            self._maybe_start_hide()

    @pyqtSlot(bool)
    def set_pinned(self, pinned: bool) -> None:
        """Pin (or unpin) the card. **GUI thread.** Emits ``pinned_changed`` only on a change.

        A pinned card ignores hover entirely -- that is the whole point of pinning it.
        """
        pinned = bool(pinned)
        if pinned == self._pinned:
            return
        self._pinned = pinned
        if pinned:
            self._show_timer.stop()
            self._hide_timer.stop()
            self._fade_in()
        else:
            # Unpinning while the cursor is still on the pet or the card demotes to a peek
            # rather than snapping the card away under the user's pointer.
            if self._hovered or self._cursor_inside:
                self._maybe_start_hide()
            else:
                self._fade_out()
        self.pinned_changed.emit(pinned)

    @pyqtSlot()
    def toggle_pinned(self) -> None:
        """Flip the pin. **GUI thread.** Wired to ``PetWindow.clicked`` and to the tray."""
        self.set_pinned(not self._pinned)

    def is_pinned(self) -> bool:
        """Whether the card is pinned. **GUI thread.**"""
        return self._pinned

    def _maybe_start_hide(self) -> None:
        if self._pinned or self._hovered or self._cursor_inside:
            return
        if not self.isVisible():
            return
        self._hide_timer.start()

    @pyqtSlot()
    def _on_show_timeout(self) -> None:
        if self._pinned or self._hovered:
            self._fade_in()

    @pyqtSlot()
    def _on_hide_timeout(self) -> None:
        if self._pinned or self._hovered or self._cursor_inside:
            return
        self._fade_out()

    def _fade_in(self) -> None:
        """Show and fade up. **GUI thread.** Safe to call while already visible."""
        self._fade.stop()
        self._fade_to_visible = True
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.resize(self.sizeHint())
            self._reposition()
            self.show()
        self._fade.setDuration(config.CARD_FADE_IN_MS)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(1.0)
        self._fade.start()

    def _fade_out(self) -> None:
        """Fade down, then genuinely ``hide()``. **GUI thread.**

        ``hide()`` runs from the animation's ``finished`` signal rather than immediately: a
        window left at zero opacity is still mapped and still takes hit-tests, so it would
        swallow clicks aimed at whatever is underneath it.
        """
        if not self.isVisible():
            self._fade_to_visible = False
            return
        self._fade.stop()
        self._fade_to_visible = False
        self._fade.setDuration(config.CARD_FADE_OUT_MS)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    @pyqtSlot()
    def _on_fade_finished(self) -> None:
        if not self._fade_to_visible:
            self.hide()
            self.setWindowOpacity(0.0)

    # -- events ---------------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        """Re-anchor on every show; the pet may have moved while we were hidden."""
        self._reposition()
        super().showEvent(event)

    def _set_cursor_inside(self, inside: bool) -> None:
        """Apply a card-region enter/leave transition. **GUI thread.** Idempotent.

        Entering is what makes hover actually usable: without it, moving the pointer off the
        pet and toward the card starts the hide timer, and reading the card dismisses it.
        """
        if inside == self._cursor_inside:
            return
        self._cursor_inside = inside
        if inside:
            self._hide_timer.stop()
            if self.isVisible() and not self._fade_to_visible:
                # The cursor reached the card after the fade-out had already begun. Stopping
                # the timer is not enough at that point -- the animation would still run to
                # zero and hide a window the user is actively pointing at.
                self._fade_in()
        else:
            self._maybe_start_hide()

    def enterEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        """Cursor entered the **widget**. **GUI thread.**

        Which is not the same as entering the card: the outer ``shadow_margin`` px are
        transparent, and the user reads them as desktop. Only a position actually inside
        :meth:`card_rect` counts -- otherwise crossing the shadow on the way past would hold
        the card open, and the card would feel like it had an invisible dead zone glued to it.
        """
        self._set_cursor_inside(self._in_card(event))
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        """Cursor left the widget entirely. **GUI thread.**"""
        self._set_cursor_inside(False)
        super().leaveEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        """Track the card-region boundary *within* the widget. **GUI thread.**

        Qt only sends enter/leave at the widget edge, so with a transparent margin around the
        card this is the only place the real boundary can be observed. Mouse tracking is on
        (see ``__init__``), so it fires with no button held.
        """
        self._set_cursor_inside(self._in_card(event))
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        """Left-click on the card toggles the pin. **GUI thread.**

        A press in the shadow margin is not a press on the card and must not pin it.
        """
        if event.button() == Qt.MouseButton.LeftButton and self._in_card(event):
            self.toggle_pinned()
            event.accept()
            return
        super().mousePressEvent(event)

    def _in_card(self, event) -> bool:  # noqa: ANN001 - Qt signature
        """Is this event's position over the visible card? **GUI thread.**

        Falls back to ``True`` if the event carries no usable position, so a Qt version that
        hands us a bare ``QEvent`` degrades to the old whole-widget behaviour rather than
        making the card unhoverable.
        """
        position = getattr(event, "position", None)
        if position is None:
            return True
        try:
            point = position()
        except TypeError:  # pragma: no cover - defensive; not a callable on this build
            return True
        return self.card_rect().contains(point.toPoint())

    # -- geometry -------------------------------------------------------------------------

    def shadow_margin(self) -> int:
        """Transparent px reserved on every side for the painted shadow. **Any thread.**

        Uniform, not per-edge. ``CARD_SHADOW_Y_OFFSET`` means the shadow needs
        ``radius + offset`` below and only ``radius - offset`` above, but a uniform margin
        keeps every ``centre - width // 2`` in this file honest, and the few px of unused
        transparency at the top cost nothing: the widget is click-tested against
        :meth:`card_rect`, so they are not even a dead hover zone.
        """
        if not config.CARD_SHADOW:
            return 0
        return max(0, int(config.CARD_SHADOW_RADIUS)) + max(0, int(config.CARD_SHADOW_Y_OFFSET))

    def card_size(self) -> QSize:
        """The size of the **visible** card -- ``size()`` minus the shadow margin. GUI thread."""
        margin = self.shadow_margin()
        size = self.size()
        if size.isEmpty():
            size = self.sizeHint()
        return QSize(max(0, size.width() - margin * 2), max(0, size.height() - margin * 2))

    def card_rect(self) -> QRect:
        """The **visible** card in widget-local coordinates. GUI thread.

        This -- not ``rect()`` -- is what the user sees, hovers and clicks.
        """
        margin = self.shadow_margin()
        size = self.card_size()
        return QRect(margin, margin, size.width(), size.height())

    def sizeHint(self) -> QSize:
        """Height is a pure function of the visible row count, plus the shadow margin.

        **GUI thread.** The *card* is always ``CARD_WIDTH`` wide; the extra is transparent
        padding for the shadow and is the only thing here that is not about the rows.
        """
        header = max(config.CARD_GLYPH_D,
                     self._fm_title.height() + _HEADER_LEADING + self._fm_subtitle.height())
        body = len(self._rows) * config.CARD_ROW_HEIGHT
        height = config.CARD_PADDING_Y * 2 + header + config.CARD_SECTION_GAP + body
        margin = self.shadow_margin() * 2
        return QSize(config.CARD_WIDTH + margin, height + margin)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # -- copy -----------------------------------------------------------------------------

    def _format(self, template: str) -> str:
        """Substitute metrics into one copy string. **Never raises.** GUI thread.

        ``_ForgivingContext`` covers unknown *keys*; this catches everything else a
        hand-edited template can do -- an unbalanced brace, a positional ``{0}``, a numeric
        format spec applied to our string values -- and falls back to the raw template, which
        is ugly but visible, and visible is debuggable.
        """
        try:
            return template.format_map(presentation_context(self._metrics))
        except Exception:
            log.warning("state copy %r could not be formatted; showing it verbatim",
                        template, exc_info=True)
            return template

    # -- painting -------------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        """Draw the entire card. **GUI thread.** No child widgets -- see the module docstring.

        Wrapped so that nothing raised by the drawing code can escape into Qt. This is not
        defensive decoration: PyQt6 does not merely print a traceback from a failed virtual
        reimplementation, it calls ``qFatal`` and **aborts the process**. A one-character
        mistake in a glyph path therefore kills the whole pet, silently, with no Python
        traceback -- which is exactly how it presented when this module's ``_draw_bolt``
        handed a ``QPoint`` to ``QPainterPath.moveTo``. A half-drawn card the user can still
        close from the tray beats a dead application every time.

        The log is *not* rate-limited to one line per state on purpose: ``log.exception``
        per repaint is noisy, but a paint failure is a genuine bug and must stay impossible
        to miss. ``_paint_failed_for`` only suppresses the repeat while the same state keeps
        failing.
        """
        try:
            self._paint(event)
        except Exception:
            if self._paint_failed_for != self._state:
                self._paint_failed_for = self._state
                log.exception("StatsCard.paintEvent failed for state %r; the card will render "
                              "incomplete rather than aborting the process", self._state)
        else:
            self._paint_failed_for = None

    def _paint(self, event) -> None:  # noqa: ANN001 - Qt signature
        """The actual drawing. Called only by :meth:`paintEvent`, which owns the safety net."""
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

            accent = QColor(self._presentation.accent)
            if not accent.isValid():  # a typo in user-edited copy must not black out the card
                log.warning("invalid accent %r for state %r; using the default",
                            self._presentation.accent, self._state)
                accent = QColor(config.DEFAULT_PRESENTATION.accent)

            # Everything below is laid out against the *card*, not the widget: the widget
            # carries a transparent shadow margin on every side.
            card = QRectF(self.card_rect())
            self._paint_shadow(painter, card)
            self._paint_surface(painter, card)

            inner = card.adjusted(
                config.CARD_PADDING_X, config.CARD_PADDING_Y,
                -config.CARD_PADDING_X, -config.CARD_PADDING_Y)

            header_h = max(config.CARD_GLYPH_D,
                           self._fm_title.height() + _HEADER_LEADING + self._fm_subtitle.height())
            header = QRectF(inner.left(), inner.top(), inner.width(), header_h)
            self._paint_header(painter, header, accent)

            body_top = header.bottom() + config.CARD_SECTION_GAP
            self._paint_rows(painter, QRectF(inner.left(), body_top, inner.width(),
                                             len(self._rows) * config.CARD_ROW_HEIGHT), accent)
        finally:
            # Explicit, not left to the QPainter destructor: on the exception path the live
            # painter would otherwise be kept alive by the traceback's frame reference until
            # the exception is cleared, and Qt warns about a painter still active on a widget
            # whose paint event has already returned.
            painter.end()

    def _paint_shadow(self, painter: QPainter, card: QRectF) -> None:
        """A soft drop shadow, painted by hand. **GUI thread**; called first, under everything.

        Not ``QGraphicsDropShadowEffect`` -- see ``config.CARD_SHADOW`` for why. The shape here
        is a stack of concentric rounded rects, largest and faintest first, each drawn over the
        last so the alphas *accumulate* toward the card. Three details earn their keep:

        * **The per-layer alpha is solved, not guessed.** Painting ``n`` layers of the same
          alpha gives an exponential ramp, which reads as a hard-edged blob with a long thin
          tail. Instead each layer targets a cumulative coverage ``C`` sampled from
          ``peak * (1 - t) ** CARD_SHADOW_FALLOFF``, and the alpha that gets there over what is
          already down is ``(C - covered) / (1 - covered)``. ``covered`` is then advanced using
          the *quantised* byte actually used, so 8-bit rounding cannot drift across 16 layers.
        * **It is clipped to outside the surface.** Otherwise 16 stacked layers would sit under
          a 91%-opaque card and show through as a muddied centre.
        * **The clip bites 1 px under the card** (``_SHADOW_CLIP_BITE``), because a clip edge
          exactly on the surface path does not antialias against the surface's own edge.

        Guarded end to end: this runs inside ``paintEvent``, where an exception aborts the
        process, and every value it reads is a user-tunable config constant.
        """
        if not config.CARD_SHADOW:
            return
        radius = float(max(0, int(config.CARD_SHADOW_RADIUS)))
        steps = max(1, int(config.CARD_SHADOW_STEPS))
        peak = max(0, min(255, int(config.CARD_SHADOW_ALPHA))) / 255.0
        if radius <= 0.0 or peak <= 0.0:
            return
        dy = float(config.CARD_SHADOW_Y_OFFSET)
        falloff = max(0.1, float(config.CARD_SHADOW_FALLOFF))

        bite = _SHADOW_CLIP_BITE
        hole = QPainterPath()
        hole.addRoundedRect(card.adjusted(bite, bite, -bite, -bite),
                            max(0.0, config.CARD_RADIUS - bite),
                            max(0.0, config.CARD_RADIUS - bite))
        clip = QPainterPath()
        clip.addRect(QRectF(self.rect()))

        painter.save()
        try:
            painter.setClipPath(clip.subtracted(hole))
            painter.setPen(Qt.PenStyle.NoPen)
            ink = QColor(*config.CARD_SHADOW_COLOR)
            covered = 0.0
            for i in range(steps):
                grow = radius * (1.0 - i / steps)          # outer edge of this band
                mid = 1.0 - (i + 0.5) / steps              # 0 at the card, 1 at full reach
                target = peak * (1.0 - mid) ** falloff
                if target <= covered:
                    continue
                alpha = int(round(((target - covered) / (1.0 - covered)) * 255.0))
                alpha = max(1, min(255, alpha))
                ink.setAlpha(alpha)
                painter.setBrush(ink)
                painter.drawRoundedRect(
                    card.adjusted(-grow, -grow + dy, grow, grow + dy),
                    config.CARD_RADIUS + grow, config.CARD_RADIUS + grow)
                covered += (alpha / 255.0) * (1.0 - covered)
        finally:
            painter.restore()

    def _paint_surface(self, painter: QPainter, card: QRectF) -> None:
        """The rounded translucent slab plus its hairline rim."""
        # Half-pixel inset so the 1 px rim lands on whole pixels instead of straddling two and
        # rendering as a 2 px smear at 50% alpha.
        rect = card.adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, config.CARD_RADIUS, config.CARD_RADIUS)
        painter.fillPath(path, QColor(*config.CARD_SURFACE))
        painter.strokePath(path, QPen(QColor(*config.CARD_RIM), 1.0))

    def _paint_header(self, painter: QPainter, box: QRectF, accent: QColor) -> None:
        """Bold title over a muted subtitle, with the status glyph right-aligned."""
        glyph_d = config.CARD_GLYPH_D
        glyph_box = QRectF(box.right() - glyph_d, box.center().y() - glyph_d / 2.0,
                           glyph_d, glyph_d)
        self._paint_glyph(painter, glyph_box, accent)

        text_w = box.width() - glyph_d - _COL_GAP
        title = self._format(self._presentation.title)
        subtitle = self._format(self._presentation.subtitle)

        block_h = self._fm_title.height() + _HEADER_LEADING + self._fm_subtitle.height()
        top = box.top() + (box.height() - block_h) / 2.0

        painter.setPen(QColor(*config.CARD_TITLE_COLOR))
        painter.setFont(self._title_font)
        painter.drawText(
            QRectF(box.left(), top, text_w, self._fm_title.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._fm_title.elidedText(title, Qt.TextElideMode.ElideRight, int(text_w)))

        painter.setPen(QColor(*config.CARD_SUBTITLE_COLOR))
        painter.setFont(self._subtitle_font)
        painter.drawText(
            QRectF(box.left(), top + self._fm_title.height() + _HEADER_LEADING,
                   text_w, self._fm_subtitle.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._fm_subtitle.elidedText(subtitle, Qt.TextElideMode.ElideRight, int(text_w)))

    def _paint_glyph(self, painter: QPainter, box: QRectF, accent: QColor) -> None:
        """A soft accent disc with the state's mark inside it."""
        disc = QColor(accent)
        disc.setAlpha(_GLYPH_DISC_ALPHA)
        ring = QColor(accent)
        ring.setAlpha(_GLYPH_RING_ALPHA)

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(disc)
        painter.drawEllipse(box)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ring, 1.0))
        painter.drawEllipse(box.adjusted(0.5, 0.5, -0.5, -0.5))

        paint = GLYPH_PAINTERS.get(self._presentation.glyph)
        if paint is None:
            log.debug("unknown glyph %r; falling back to 'dot'", self._presentation.glyph)
            paint = GLYPH_PAINTERS["dot"]
        # The mark occupies the middle ~46% of the disc, which is what keeps the disc reading
        # as a halo rather than as a button.
        inset = box.width() * 0.27
        paint(painter, box.adjusted(inset, inset, -inset, -inset), accent)
        painter.restore()

    def _paint_rows(self, painter: QPainter, box: QRectF, accent: QColor) -> None:
        """``label | meter | number | unit``, one line per visible row.

        The number column is right-aligned at a fixed x for *every* row, so the percentages
        form a column even when only some rows carry a unit; the unit hangs to its right in
        the label colour. The meter absorbs whatever width the two text columns need, because
        it is the only column that can shrink without losing information -- but never below
        ``_MIN_METER_W``, past which the unit is elided instead.
        """
        if not self._rows:
            return

        label_w = config.CARD_LABEL_COL_W
        suffixes = {r.key: (f"{SUFFIX_SEP}{r.suffix}" if r.suffix else "") for r in self._rows}
        # Floored at the widest percentage the column can ever hold, not merely at the widest
        # one it holds *now*: sized to the live values, the number column -- and therefore the
        # meter track -- would grow and shrink as CPU crossed 9 -> 10 -> 100%, which is the
        # same per-poll twitch the row-set frozenset exists to prevent, one column over.
        num_w = max(self._fm_value.horizontalAdvance(_WIDEST_VALUE),
                    max(self._fm_value.horizontalAdvance(r.value) for r in self._rows))
        suffix_w = max(self._fm_value.horizontalAdvance(s) for s in suffixes.values())

        # CARD_VALUE_COL_W is the floor on the *whole* value block, number plus unit -- not on
        # the number alone. Measured: a bare "94%" is ~27 px, so flooring the number column at
        # 52 and then hanging the unit off its right opened a 25 px hole between the meter and
        # the digits on every suffixed row. The block keeps the floor; the number column
        # absorbs the slack, so the digits still right-align on a single x.
        block_w = max(config.CARD_VALUE_COL_W, num_w + suffix_w)
        meter_w = box.width() - label_w - block_w - _COL_GAP * 2
        if meter_w < _MIN_METER_W:  # pathological copy/metrics only; keeps the meter a meter
            block_w = max(0.0, block_w - (_MIN_METER_W - meter_w))
            suffix_w = max(0.0, min(suffix_w, block_w - num_w))
            meter_w = box.width() - label_w - block_w - _COL_GAP * 2
        meter_x = box.left() + label_w + _COL_GAP
        num_x = box.right() - block_w
        num_w = block_w - suffix_w
        suffix_x = num_x + num_w

        danger = QColor(config.ACCENT_DANGER)
        label_colour = QColor(*config.CARD_LABEL_COLOR)
        value_colour = QColor(*config.CARD_VALUE_COLOR)
        disabled = QColor(*config.CARD_DISABLED_COLOR)

        for i, row in enumerate(self._rows):
            top = box.top() + i * config.CARD_ROW_HEIGHT
            line = QRectF(box.left(), top, box.width(), config.CARD_ROW_HEIGHT)

            painter.setFont(self._label_font)
            painter.setPen(disabled if not row.available else label_colour)
            painter.drawText(QRectF(line.left(), line.top(), label_w, line.height()),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             row.label)

            painter.setFont(self._value_font)
            painter.setPen(disabled if not row.available else value_colour)
            painter.drawText(QRectF(num_x, line.top(), num_w, line.height()),
                             int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                             row.value)

            suffix = suffixes[row.key]
            if suffix and suffix_w > 0:
                # Elide only when it genuinely does not fit: QFontMetrics lays text out in
                # 1/64ths and horizontalAdvance() rounds, so eliding unconditionally at the
                # measured advance costs the last character to a sub-pixel -- which is how
                # "62degC" first rendered as "62...".
                if self._fm_value.horizontalAdvance(suffix) > suffix_w:
                    suffix = self._fm_value.elidedText(suffix, Qt.TextElideMode.ElideRight,
                                                       int(suffix_w))
                painter.setPen(disabled if not row.available else label_colour)
                painter.drawText(
                    QRectF(suffix_x, line.top(), suffix_w, line.height()),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    suffix)

            if meter_w > 0:
                fill = danger if row.danger else accent
                self._paint_meter(painter, QRectF(meter_x,
                                                  line.center().y() - config.CARD_METER_HEIGHT / 2.0,
                                                  meter_w, config.CARD_METER_HEIGHT),
                                  row.fraction, fill)

    def _paint_meter(self, painter: QPainter, track: QRectF,
                     fraction: float | None, fill: QColor) -> None:
        """A rounded track with an accent fill; an empty track when the sample failed."""
        radius = track.height() / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(*config.CARD_METER_TRACK))
        painter.drawRoundedRect(track, radius, radius)

        if fraction is None or fraction <= 0.0:
            return
        clamped = min(1.0, float(fraction))
        # Clamp up as well as down: below one meter-height the fill degenerates into a sliver
        # that reads as a rendering artefact, so 1% is drawn as a deliberate dot instead.
        width = max(float(_METER_MIN_FILL), track.width() * clamped)
        width = min(width, track.width())
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(track.left(), track.top(), width, track.height()),
                                radius, radius)


def _apply_tabular_figures(font: QFont) -> bool:
    """Turn on the ``tnum`` OpenType feature if this Qt has the API. Any thread.

    ``QFont.setFeature`` landed in Qt 6.7 and we are pinned to 6.8.1, but the guard stays: the
    only cost of missing it is digits that shuffle sideways, which must never be an exception.
    """
    if not hasattr(font, "setFeature"):
        log.debug("QFont.setFeature unavailable; digits will not be tabular")
        return False
    # PyQt6 accepts either a plain 4-character tag or a QFont.Tag, depending on the build.
    # Both spellings are tried lazily, so constructing the Tag cannot escape the guard either.
    makers: tuple[Callable[[], object], ...] = (
        lambda: "tnum",
        lambda: QFont.Tag("tnum"),  # type: ignore[attr-defined]
    )
    for make_tag in makers:
        try:
            font.setFeature(make_tag(), 1)  # type: ignore[arg-type]
            return True
        except (AttributeError, TypeError, ValueError):
            continue
    log.debug("could not enable tabular figures on %s", font.family())
    return False
