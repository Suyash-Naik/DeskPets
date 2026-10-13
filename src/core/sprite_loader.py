"""Packed sprite atlas -> validated ``QPixmap`` frames.

The atlas is a PNG plus a sidecar JSON (``assets/layla-and-chico-pet-spritesheet.*``).
Every frame rect is read **verbatim** from ``rows[].frame_data[]`` -- never computed as
``column * frame_width``. Row 2 already carries ten frames where every other row carries
eight, so a grid assumption would silently shear the sheet; and a future ragged atlas (tight
crops packed by size) would break a grid reader completely while this one keeps working.

Two entry points, with different threading rules:

``validate_atlas()``   ``QImage`` only -- no ``QPixmap``, no ``QApplication``. Safe from any
                       thread and from a headless process, which is what makes the ``__main__``
                       CLI and ``main.py --validate`` work without a GUI.
``load_spritesheet()`` builds ``QPixmap``s. **GUI thread only**, and only after a
                       ``QGuiApplication`` exists.

Decoding is Qt's own: ``QImage(str(path))`` then ``QImage.copy(QRect)``. Pillow is *not*
imported here and is not in the ``pets`` environment (see ``pixi.toml``). That is deliberate --
the BuildPlan prototype did ``QImage(crop.tobytes(), w, h, Format_RGBA8888)``, which wraps a
``bytes`` temporary that is freed while the QImage still points at it (use-after-free) and
omits ``bytesPerLine`` (wrong stride on any padded scanline). Slicing an image Qt owns makes
both defects unrepresentable rather than merely fixed. Do not "optimise" Pillow back in.

The atlas's own ``transform`` block records that the art was downscaled 0.6x with a
``"nearest"`` filter when it was packed. That is the justification for nearest-neighbour at
render time (``config.SMOOTH_SCALING`` False): smoothing now would blur a resample the pipeline
deliberately kept hard.

CLI exit codes mirror ``tools/frame_qa.py``: 0 clean or warnings only, 1 at least one BLOCK,
2 bad usage / missing assets. The BLOCK / WARN vocabulary is the same on purpose.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Iterator, Mapping, Sequence

from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtGui import QGuiApplication, QImage, QPixmap

from .. import config

log = logging.getLogger(__name__)

# numpy is present in the `pets` environment and is used for a single vectorised pass over the
# atlas alpha plane. It is guarded anyway: content bounds are an optimisation for hit-testing
# and tray-icon trimming, never a correctness requirement -- without numpy every frame simply
# reports its full rect as its content rect.
try:  # pragma: no cover - depends on the environment this runs in
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None  # type: ignore[assignment]

#: Same severity vocabulary as ``tools/frame_qa.py`` so one grep finds every gate in the repo.
BLOCK: Final[str] = "BLOCK"
WARN: Final[str] = "WARN"
_SEVERITY_ORDER: Final[dict[str, int]] = {BLOCK: 0, WARN: 1}

_SHA_CHUNK: Final[int] = 1 << 20


# =============================================================================
# ERRORS
# =============================================================================


class SpriteLoadError(RuntimeError):
    """Base class for every atlas failure. Callers may catch just this one."""


class AssetsMissingError(SpriteLoadError):
    """The JSON or the PNG is not on disk -- a packaging problem, not a content problem."""


class AtlasValidationError(SpriteLoadError):
    """The atlas exists but is unusable: unparseable, or it produced BLOCK findings."""


# =============================================================================
# VALUE TYPES
# =============================================================================


@dataclass(frozen=True, slots=True)
class Frame:
    """One frame of one animation.

    ``offset`` is where the frame should be drawn inside its animation's ``box``: horizontally
    centred, sitting on the box's bottom edge. With today's uniform atlas every frame fills the
    box, so every offset is ``(0, 0)`` -- that is expected, not a bug. It exists so that a
    future atlas packed from tight crops renders correctly with no change in ``pet_window``.

    ``content_rect`` is the opaque-pixel bounding box in **frame-local** coordinates, or the
    full frame rect when bounds could not be computed (no numpy, or
    ``config.COMPUTE_CONTENT_BOUNDS`` off). Read-only value type, safe to hold from any thread;
    the ``QPixmap`` inside may only be *painted* on the GUI thread.
    """

    index: int
    pixmap: QPixmap
    source_rect: QRect
    duration_ms: int
    phase: str
    offset: QPoint
    content_rect: QRect


@dataclass(frozen=True, slots=True)
class Animation:
    """One atlas row: an ordered, timed sequence of frames.

    ``box`` is the largest frame width/height in the row -- the surface this animation needs.
    Immutable; readable from any thread.
    """

    name: str
    row: int
    sequence: str
    loop: bool
    duration_ms: int
    frames: tuple[Frame, ...]
    box: QSize

    def __len__(self) -> int:
        return len(self.frames)

    def __iter__(self) -> Iterator[Frame]:
        return iter(self.frames)

    def __getitem__(self, index: int) -> Frame:
        """Strict indexing -- raises ``IndexError`` past the end. Use ``frame_at`` to wrap."""
        return self.frames[index]

    def frame_at(self, index: int) -> Frame:
        """Indexing that wraps modulo ``len(self)``. The animator's hot path."""
        if not self.frames:
            raise IndexError(f"animation {self.name!r} has no frames")
        return self.frames[index % len(self.frames)]


@dataclass(frozen=True, slots=True)
class AtlasIssue:
    """One validation finding. ``severity`` is ``"BLOCK"`` or ``"WARN"``."""

    severity: str
    check: str
    message: str

    def __str__(self) -> str:  # mirrors frame_qa.Finding.__str__
        return f"[{self.severity:<5}] {self.check:<16} {self.message}"


@dataclass(frozen=True, slots=True)
class AtlasReport:
    """The verdict on one atlas. Produced headlessly; cheap to log or serialise.

    ``frame_counts`` is additive to the contract's field list: the CLI's ``-v`` mode needs
    per-animation counts, and re-parsing the JSON to recover them would be silly.
    """

    json_path: Path
    png_path: Path
    image_size: QSize
    frame_count: int
    animation_names: tuple[str, ...]
    issues: tuple[AtlasIssue, ...]
    frame_counts: tuple[tuple[str, int], ...] = ()

    @property
    def ok(self) -> bool:
        """True when nothing is blocked. Warnings do not make an atlas unusable."""
        return not self.blocks

    @property
    def blocks(self) -> tuple[AtlasIssue, ...]:
        return tuple(i for i in self.issues if i.severity == BLOCK)

    @property
    def warns(self) -> tuple[AtlasIssue, ...]:
        return tuple(i for i in self.issues if i.severity == WARN)

    @property
    def verdict(self) -> str:
        if self.blocks:
            return "BLOCKED"
        return "PASS WITH WARNINGS" if self.warns else "PASS"

    def render(self, verbose: bool = False) -> str:
        """A human-readable report block, styled like ``tools/frame_qa.py``'s ``_print``."""
        size = f"{self.image_size.width()}x{self.image_size.height()}"
        lines = [
            f"=== {self.json_path.stem} ===",
            f"  json   : {self.json_path}",
            f"  png    : {self.png_path}",
            f"  image  : {size}",
            f"  frames : {self.frame_count} in {len(self.animation_names)} animation(s)",
        ]
        if verbose:
            for name, count in self.frame_counts:
                lines.append(f"           {name:<24} {count} frame(s)")
        lines.append("")
        lines.extend(f"  {issue}" for issue in self.issues)
        if not self.issues:
            lines.append("  no findings")
        lines.append("")
        lines.append(
            f"  -> {self.verdict} ({len(self.blocks)} block, {len(self.warns)} warn)")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form for ``--json`` (and for pasting into a bug report)."""
        return {
            "json_path": str(self.json_path),
            "png_path": str(self.png_path),
            "image_size": [self.image_size.width(), self.image_size.height()],
            "frame_count": self.frame_count,
            "animation_names": list(self.animation_names),
            "frame_counts": {name: count for name, count in self.frame_counts},
            "verdict": self.verdict,
            "ok": self.ok,
            "issues": [
                {"severity": i.severity, "check": i.check, "message": i.message}
                for i in self.issues
            ],
        }


# =============================================================================
# INTERNAL PLAN
#
# Validation's by-product, reused by the loader, so one load parses the JSON, hashes the PNG
# and scans the alpha plane exactly once instead of twice.
# =============================================================================


@dataclass(frozen=True, slots=True)
class _FramePlan:
    index: int
    rect: QRect
    duration_ms: int
    phase: str
    content_rect: QRect


@dataclass(frozen=True, slots=True)
class _RowPlan:
    name: str
    row: int
    sequence: str
    loop: bool
    duration_ms: int
    frames: tuple[_FramePlan, ...]


@dataclass
class _Collector:
    """Accumulates findings during a validation pass."""

    strict: bool
    issues: list[AtlasIssue] = field(default_factory=list)

    def block(self, check: str, message: str) -> None:
        self.issues.append(AtlasIssue(BLOCK, check, message))

    def warn(self, check: str, message: str) -> None:
        self.issues.append(AtlasIssue(WARN, check, message))

    def soft(self, check: str, message: str) -> None:
        """WARN normally, BLOCK under ``config.STRICT_ATLAS``.

        Used for the sha256 and declared-count cross-checks: a stale hash must not block art
        iteration, but it is always reported, because a repacked PNG invalidates every rect in
        the JSON.
        """
        self.issues.append(AtlasIssue(BLOCK if self.strict else WARN, check, message))

    def sorted_issues(self) -> tuple[AtlasIssue, ...]:
        return tuple(sorted(self.issues, key=lambda i: (_SEVERITY_ORDER[i.severity], i.check)))


# =============================================================================
# I/O HELPERS
# =============================================================================


def _read_atlas_json(json_path: Path) -> Mapping[str, Any]:
    """Parse the sidecar JSON. Any thread.

    ``utf-8-sig`` + ``.strip()`` is not defensiveness for its own sake: this repo has already
    shipped ``sequence-spec.json`` files with a BOM / trailing newline that a plain
    ``json.loads`` refused (CLAUDE.md, "Generated files"). The same tolerance belongs here.
    """
    json_path = Path(json_path)
    try:
        raw = json_path.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError as exc:
        raise AssetsMissingError(f"atlas JSON not found: {json_path}") from exc
    except OSError as exc:
        raise AssetsMissingError(f"cannot read atlas JSON {json_path}: {exc}") from exc
    if not raw:
        raise AtlasValidationError(f"atlas JSON is empty: {json_path}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AtlasValidationError(f"atlas JSON is not valid JSON ({json_path}): {exc}") from exc
    if not isinstance(data, Mapping):
        raise AtlasValidationError(f"atlas JSON is not a JSON object: {json_path}")
    return data


def _resolve_png(data: Mapping[str, Any], json_path: Path, png_path: Path | None) -> Path:
    """Locate the PNG **relative to the JSON's own directory**.

    The BuildPlan prototype used a bare filename, so it only worked when the process happened
    to be launched from ``assets/``. An explicit argument wins, then ``config.ATLAS_PNG``, then
    the JSON's ``image`` field, then the JSON's own stem.
    """
    if png_path is not None:
        return Path(png_path)
    if config.ATLAS_PNG is not None:
        return Path(config.ATLAS_PNG)
    image = data.get("image")
    if isinstance(image, str) and image.strip():
        return (json_path.parent / image.strip()).resolve()
    return json_path.with_suffix(".png")


def _load_image(png_path: Path) -> QImage:
    """Decode the atlas with Qt's own decoder and normalise its format once. Any thread.

    One conversion to ``Format_ARGB32_Premultiplied`` up front means every later
    ``copy()`` / ``fromImage()`` is a straight blit with no per-frame format conversion, and it
    gives the alpha scan a known four-byte layout.
    """
    png_path = Path(png_path)
    if not png_path.is_file():
        raise AssetsMissingError(f"atlas PNG not found: {png_path}")
    image = QImage(str(png_path))
    if image.isNull():
        raise AtlasValidationError(f"atlas PNG could not be decoded by Qt: {png_path}")
    if image.format() is not QImage.Format.Format_ARGB32_Premultiplied:
        image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():
        raise AtlasValidationError(f"atlas PNG could not be converted to ARGB32: {png_path}")
    return image


def _sha256(path: Path) -> str | None:
    """Hash the PNG in 1 MiB chunks. ~5 ms for this atlas; None if it became unreadable."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_SHA_CHUNK), b""):
                digest.update(chunk)
    except OSError as exc:  # pragma: no cover - Qt has already opened this file successfully
        log.warning("could not hash %s: %s", path, exc)
        return None
    return digest.hexdigest()


def _alpha_plane(image: QImage):
    """The atlas alpha channel as an ``(H, W)`` uint8 array, or ``None``.

    One vectorised pass over the whole sheet; per-frame bounds are then slices of this array,
    which beats walking every frame image separately. The array is copied out of Qt's buffer so
    it never aliases an image that may be released later.
    """
    if _np is None or not config.COMPUTE_CONTENT_BOUNDS:
        return None
    bits = image.constBits()
    if bits is None:  # pragma: no cover - only for a null image, already excluded
        return None
    try:
        bits.setsize(image.sizeInBytes())
        flat = _np.frombuffer(memoryview(bits), dtype=_np.uint8)
        rows = flat.reshape(image.height(), image.bytesPerLine())
        # ARGB32 is four bytes per pixel; on a little-endian machine the alpha byte is last.
        # bytesPerLine() can exceed width*4 because Qt pads scanlines, hence the explicit stop.
        offset = 3 if sys.byteorder == "little" else 0
        return _np.array(rows[:, offset:image.width() * 4:4])
    except (ValueError, TypeError) as exc:  # pragma: no cover
        log.warning("alpha plane unavailable (%s); falling back to full frame rects", exc)
        return None


def _content_rect(alpha, rect: QRect) -> QRect:
    """Opaque-pixel bbox of ``rect``, in frame-local coordinates. Empty if nothing is opaque."""
    if alpha is None:
        return QRect(0, 0, rect.width(), rect.height())
    sub = alpha[rect.y():rect.y() + rect.height(), rect.x():rect.x() + rect.width()]
    mask = sub > 0
    if not mask.any():
        return QRect()
    cols = _np.flatnonzero(mask.any(axis=0))
    rows = _np.flatnonzero(mask.any(axis=1))
    left, right = int(cols[0]), int(cols[-1])
    top, bottom = int(rows[0]), int(rows[-1])
    return QRect(left, top, right - left + 1, bottom - top + 1)


# =============================================================================
# VALIDATION
# =============================================================================


def _int(value: Any) -> int | None:
    """Int coercion that rejects bools and strings and accepts integral floats."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _is_list(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _normalise_duration(raw: Any, fallback: int | None, where: str,
                        issues: _Collector) -> int:
    """Per-frame duration with the config-driven floor applied. Never returns <= 0."""
    value = _int(raw)
    if value is None or value <= 0:
        value = fallback if (fallback is not None and fallback > 0) else config.DEFAULT_FRAME_MS
        issues.warn("timing", f"{where}: duration_ms missing or <= 0; using {value} ms")
    if value < config.MIN_FRAME_MS:
        issues.warn("timing",
                    f"{where}: duration_ms {value} below MIN_FRAME_MS "
                    f"{config.MIN_FRAME_MS}; clamped")
        value = config.MIN_FRAME_MS
    return value


def _validate(data: Mapping[str, Any], image: QImage, json_path: Path, png_path: Path,
              strict: bool) -> tuple[AtlasReport, tuple[_RowPlan, ...]]:
    """The whole gate in one pass. Returns the report and the plan the loader consumes.

    Order is deliberate: cheap structural checks first, so a truncated or mis-schemaed file
    reports its real cause instead of one bounds failure per frame. Any thread -- ``QImage``
    only, no ``QPixmap``.
    """
    issues = _Collector(strict=strict)
    img_w, img_h = image.width(), image.height()

    # --- schema -------------------------------------------------------------
    schema = _int(data.get("schema_version"))
    if schema is None:
        issues.block("schema", "schema_version is missing or not an integer")
    elif schema not in config.SUPPORTED_SCHEMA_VERSIONS:
        issues.block("schema",
                     f"schema_version {schema} is not in "
                     f"{sorted(config.SUPPORTED_SCHEMA_VERSIONS)}")

    # --- declared image size vs the decoded PNG -----------------------------
    declared_w, declared_h = _int(data.get("width")), _int(data.get("height"))
    if declared_w is not None and declared_h is not None:
        if (declared_w, declared_h) != (img_w, img_h):
            issues.soft("image-size",
                        f"JSON says {declared_w}x{declared_h}, PNG is {img_w}x{img_h} "
                        "-- every frame rect is suspect")

    # --- sha256 -------------------------------------------------------------
    expected_sha = data.get("image_sha256")
    if config.VERIFY_ATLAS_SHA256 and isinstance(expected_sha, str) and expected_sha:
        actual = _sha256(png_path)
        if actual is not None and actual.lower() != expected_sha.lower():
            issues.soft("sha256",
                        f"{png_path.name} hashes to {actual[:12]}..., JSON expects "
                        f"{expected_sha[:12]}... -- the PNG was repacked without repacking the "
                        "JSON, so the frame rects may no longer line up with the art")

    # --- rows ---------------------------------------------------------------
    rows: Any = data.get("rows")
    if not _is_list(rows) or not rows:
        issues.block("rows", "'rows' is missing, empty, or not a list")
        rows = []

    alpha = _alpha_plane(image)
    plans: list[_RowPlan] = []
    seen_names: dict[str, int] = {}
    total_frames = 0

    for position, row in enumerate(rows):
        if not isinstance(row, Mapping):
            issues.block("rows", f"rows[{position}] is not an object")
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            issues.block("rows", f"rows[{position}] has no usable 'name'")
            continue
        name = name.strip()
        row_index = _int(row.get("row"))
        if row_index is None:
            row_index = position
            issues.warn("rows", f"{name}: 'row' missing; using list position {position}")
        if name in seen_names:
            issues.block("rows",
                         f"duplicate animation name {name!r} (rows {seen_names[name]} "
                         f"and {row_index})")
            continue
        seen_names[name] = row_index

        frame_data = row.get("frame_data")
        if not _is_list(frame_data):
            issues.block("frame-data", f"{name}: 'frame_data' is missing or not a list")
            continue
        declared_frames = _int(row.get("frames"))
        if declared_frames is not None and declared_frames != len(frame_data):
            issues.block("frame-count",
                         f"{name}: 'frames' says {declared_frames} but frame_data has "
                         f"{len(frame_data)} entries")

        timing = row.get("timing_ms")
        timing_list = list(timing) if _is_list(timing) else []
        if timing_list and len(timing_list) != len(frame_data):
            issues.warn("timing",
                        f"{name}: timing_ms has {len(timing_list)} entries for "
                        f"{len(frame_data)} frames")

        frames: list[_FramePlan] = []
        for i, entry in enumerate(frame_data):
            where = f"{name}[{i}]"
            if not isinstance(entry, Mapping):
                issues.block("frame-data", f"{where}: not an object")
                continue
            x, y = _int(entry.get("x")), _int(entry.get("y"))
            w, h = _int(entry.get("width")), _int(entry.get("height"))
            if x is None or y is None or w is None or h is None:
                issues.block("frame-rect",
                             f"{where}: x/y/width/height missing or not integers "
                             f"({entry.get('x')!r}, {entry.get('y')!r}, "
                             f"{entry.get('width')!r}, {entry.get('height')!r})")
                continue
            if w <= 0 or h <= 0:
                issues.block("frame-rect", f"{where}: non-positive size {w}x{h}")
                continue
            # Bounds-checked against the PNG that was actually decoded, not the declared size.
            # QImage.copy() of an out-of-range rect quietly returns transparent padding instead
            # of failing, so an unchecked bad rect becomes an invisible pet rather than an
            # error -- exactly the class of fault that is hardest to diagnose later.
            if x < 0 or y < 0 or x + w > img_w or y + h > img_h:
                issues.block("frame-bounds",
                             f"{where}: rect ({x},{y},{w},{h}) falls outside the "
                             f"{img_w}x{img_h} atlas")
                continue
            rect = QRect(x, y, w, h)
            fallback = _int(timing_list[i]) if i < len(timing_list) else None
            duration = _normalise_duration(entry.get("duration_ms"), fallback, where, issues)
            raw_phase = entry.get("phase")
            phase = raw_phase.strip() if isinstance(raw_phase, str) else ""
            content = _content_rect(alpha, rect)
            if content.isEmpty():
                issues.warn("empty-frame",
                            f"{where}: no opaque pixels in rect ({x},{y},{w},{h})")
                content = QRect(0, 0, w, h)
            frames.append(_FramePlan(i, rect, duration, phase, content))

        if not frames:
            issues.block("frame-data", f"{name}: no usable frames")
            continue

        total_frames += len(frames)
        loop = row.get("loop")
        if not isinstance(loop, bool):
            issues.warn("loop", f"{name}: 'loop' missing or not a bool; assuming True")
            loop = True
        sequence = row.get("sequence")
        sequence = sequence if isinstance(sequence, str) else ""
        summed = sum(f.duration_ms for f in frames)
        declared_duration = _int(row.get("duration_ms"))
        if declared_duration is not None and declared_duration != summed:
            issues.warn("duration",
                        f"{name}: duration_ms {declared_duration} != sum of frame durations "
                        f"{summed}; using {summed}")
        plans.append(_RowPlan(name, row_index, sequence, loop, summed, tuple(frames)))

    # --- cross-checks against the top-level counts --------------------------
    declared_rows = _int(data.get("row_count"))
    if declared_rows is not None and declared_rows != len(plans):
        issues.soft("row-count",
                    f"row_count says {declared_rows}, {len(plans)} usable row(s) parsed")
    declared_total = _int(data.get("frame_count"))
    if declared_total is not None and declared_total != total_frames:
        issues.soft("frame-count",
                    f"frame_count says {declared_total}, {total_frames} frame(s) parsed")

    default = data.get("default_animation")
    if not isinstance(default, str) or default not in seen_names:
        if plans:
            issues.warn("default",
                        f"default_animation {default!r} is not a row in this atlas; "
                        f"falling back to {plans[0].name!r}")
        else:
            issues.block("default", "default_animation is unusable and there are no rows")

    report = AtlasReport(
        json_path=Path(json_path),
        png_path=Path(png_path),
        image_size=QSize(img_w, img_h),
        frame_count=total_frames,
        animation_names=tuple(p.name for p in plans),
        issues=issues.sorted_issues(),
        frame_counts=tuple((p.name, len(p.frames)) for p in plans),
    )
    return report, tuple(plans)


# =============================================================================
# THE SHEET
# =============================================================================


class SpriteSheet:
    """Every animation in the atlas, already sliced into ``QPixmap``s.

    Built on the **GUI thread** by ``load_spritesheet``. Once built it is effectively
    immutable, so other threads may read its metadata; painting its pixmaps stays GUI-thread
    work like any other ``QPixmap`` use.
    """

    __slots__ = ("_animations", "_default", "_frame_box", "_report", "_source_image")

    def __init__(self, animations: Mapping[str, Animation], default_animation: str,
                 frame_box: QSize, report: AtlasReport,
                 source_image: QImage | None = None) -> None:
        # A read-only proxy, so nothing can mutate the sheet out from under the animator.
        self._animations: Mapping[str, Animation] = MappingProxyType(dict(animations))
        self._default = default_animation
        self._frame_box = frame_box
        self._report = report
        self._source_image = source_image

    # -- contract attributes -------------------------------------------------
    @property
    def animations(self) -> Mapping[str, Animation]:
        return self._animations

    @property
    def default_animation(self) -> str:
        return self._default

    @property
    def frame_box(self) -> QSize:
        """The surface every animation fits inside -- the window size ``pet_window`` needs."""
        return self._frame_box

    @property
    def report(self) -> AtlasReport:
        return self._report

    @property
    def source_image(self) -> QImage | None:
        """The whole atlas, retained when ``config.KEEP_ATLAS_IMAGE`` -- ``alpha_at`` needs it."""
        return self._source_image

    # -- mapping protocol ----------------------------------------------------
    def __contains__(self, name: object) -> bool:
        return name in self._animations

    def __getitem__(self, name: str) -> Animation:
        try:
            return self._animations[name]
        except KeyError:
            raise KeyError(
                f"no animation {name!r} in this atlas; known names: {self.names()}") from None

    def __len__(self) -> int:
        return len(self._animations)

    def __iter__(self) -> Iterator[str]:
        return iter(self._animations)

    def names(self) -> tuple[str, ...]:
        return tuple(self._animations)

    def get(self, name: str, default: Animation | None = None) -> Animation | None:
        return self._animations.get(name, default)

    def require(self, names: Sequence[str]) -> None:
        """Assert the atlas covers every state the state machine can emit.

        Called once from ``main.py`` against ``state_machine.STATES``. Failing loudly at
        startup beats a silent ``play()`` warning the first time the CPU spikes.
        """
        missing = [n for n in names if n not in self._animations]
        if missing:
            raise AtlasValidationError(
                f"atlas is missing required animation(s): {missing}; it has {self.names()}")

    # -- convenience ---------------------------------------------------------
    def frame(self, name: str, index: int) -> QPixmap:
        """One frame's pixmap, wrapping on ``index``. Used by ``tray.py`` for the icon."""
        return self[name].frame_at(index).pixmap

    def alpha_at(self, animation: str, frame_index: int, point: QPoint) -> int:
        """Alpha (0-255) of one **frame-local** pixel, for alpha hit-testing.

        Returns 0 for an unknown animation, an out-of-range point, or when the atlas image was
        not retained (``config.KEEP_ATLAS_IMAGE`` False). "Not opaque" is the safe answer: at
        worst that pixel stops being draggable. Called from mouse events, so GUI thread in
        practice, though nothing here requires it.
        """
        image = self._source_image
        if image is None:
            return 0
        anim = self.get(animation)
        if anim is None or not anim.frames:
            return 0
        frame = anim.frame_at(frame_index)
        if not (0 <= point.x() < frame.source_rect.width()
                and 0 <= point.y() < frame.source_rect.height()):
            return 0
        x = frame.source_rect.x() + point.x()
        y = frame.source_rect.y() + point.y()
        if not image.rect().contains(x, y):
            return 0
        return image.pixelColor(x, y).alpha()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"<SpriteSheet {len(self)} animation(s), "
                f"{self._report.frame_count} frame(s), default={self._default!r}>")


# =============================================================================
# PUBLIC ENTRY POINTS
# =============================================================================


def validate_atlas(json_path: Path | None = None,
                   png_path: Path | None = None) -> AtlasReport:
    """Validate the atlas without building a single ``QPixmap``.

    **Any thread, no ``QApplication`` required** -- ``QImage`` is a plain raster type. That is
    what lets the ``__main__`` CLI and ``main.py --validate`` run headless.

    Raises ``AssetsMissingError`` when the JSON or PNG is absent and ``AtlasValidationError``
    when the JSON cannot be parsed at all; every other fault is a finding in the report.
    """
    resolved_json = Path(json_path) if json_path is not None else config.ATLAS_JSON
    data = _read_atlas_json(resolved_json)
    resolved_png = _resolve_png(data, resolved_json, png_path)
    image = _load_image(resolved_png)
    report, _ = _validate(data, image, resolved_json, resolved_png, bool(config.STRICT_ATLAS))
    return report


def load_spritesheet(json_path: Path | None = None,
                     png_path: Path | None = None,
                     *, strict: bool | None = None) -> SpriteSheet:
    """Validate, then slice the atlas into per-frame ``QPixmap``s.

    **GUI thread only**, and only after a ``QGuiApplication`` exists -- a ``QPixmap`` lives in
    the window system's memory and Qt aborts the process if one is constructed too early.

    ``strict`` defaults to ``config.STRICT_ATLAS``; it promotes the soft cross-checks (sha256,
    declared counts) to BLOCK. Any BLOCK raises ``AtlasValidationError``: a blocked atlas is one
    whose rects cannot be trusted, and a pet drawn from it would be silently wrong, which is
    worse than not starting.
    """
    if QGuiApplication.instance() is None:
        raise SpriteLoadError(
            "load_spritesheet() needs a QGuiApplication (a QPixmap cannot exist without one); "
            "use validate_atlas() for headless checks")

    resolved_json = Path(json_path) if json_path is not None else config.ATLAS_JSON
    data = _read_atlas_json(resolved_json)
    resolved_png = _resolve_png(data, resolved_json, png_path)
    image = _load_image(resolved_png)
    effective_strict = bool(config.STRICT_ATLAS) if strict is None else bool(strict)
    report, plans = _validate(data, image, resolved_json, resolved_png, effective_strict)

    for issue in report.warns:
        log.warning("atlas %s: %s", issue.check, issue.message)
    if not report.ok:
        for issue in report.blocks:
            log.error("atlas %s: %s", issue.check, issue.message)
        raise AtlasValidationError(
            f"atlas validation failed for {resolved_json}\n{report.render()}")

    animations: dict[str, Animation] = {}
    for plan in plans:
        box = QSize(max(f.rect.width() for f in plan.frames),
                    max(f.rect.height() for f in plan.frames))
        frames = tuple(
            Frame(
                index=f.index,
                # copy() on an image Qt owns: no borrowed buffer to outlive, no manual stride.
                pixmap=QPixmap.fromImage(image.copy(f.rect)),
                source_rect=QRect(f.rect),
                duration_ms=f.duration_ms,
                phase=f.phase,
                # Bottom-centred inside the row's box, so a shorter frame still stands on the
                # same ground line. Uniform atlas today => always (0, 0).
                offset=QPoint((box.width() - f.rect.width()) // 2,
                              box.height() - f.rect.height()),
                content_rect=QRect(f.content_rect),
            )
            for f in plan.frames
        )
        null = [f.index for f in frames if f.pixmap.isNull()]
        if null:
            raise AtlasValidationError(
                f"{plan.name}: QPixmap.fromImage returned null for frame(s) {null}")
        animations[plan.name] = Animation(
            name=plan.name, row=plan.row, sequence=plan.sequence, loop=plan.loop,
            duration_ms=plan.duration_ms, frames=frames, box=box)

    default = data.get("default_animation")
    if not isinstance(default, str) or default not in animations:
        default = next(iter(animations))

    # The declared frame size is a floor, not the truth: a ragged atlas could hold a frame
    # larger than the nominal cell, and the window must still contain it.
    declared_w = _int(data.get("frame_width")) or 0
    declared_h = _int(data.get("frame_height")) or 0
    frame_box = QSize(max(declared_w, *(a.box.width() for a in animations.values())),
                      max(declared_h, *(a.box.height() for a in animations.values())))

    sheet = SpriteSheet(
        animations=animations,
        default_animation=default,
        frame_box=frame_box,
        report=report,
        source_image=image if config.KEEP_ATLAS_IMAGE else None,
    )
    log.info("loaded %d animation(s), %d frame(s) from %s",
             len(sheet), report.frame_count, resolved_png.name)
    return sheet


# =============================================================================
# CLI   (mirrors tools/frame_qa.py: --json, -v, exit 0 / 1 / 2)
# =============================================================================


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sprite_loader",
        description="Validate the packed sprite atlas the desktop pet loads at startup. "
                    "Headless: no QApplication, no window, no QPixmap.")
    parser.add_argument("atlas", nargs="?", type=Path,
                        help=f"atlas JSON (default: {config.ATLAS_JSON})")
    parser.add_argument("--png", type=Path,
                        help="override the PNG (default: the JSON's 'image' field, resolved "
                             "next to the JSON)")
    parser.add_argument("--strict", action="store_true",
                        help="promote sha256 / declared-count mismatches to BLOCK")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="also list per-animation frame counts and log at DEBUG")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)-8s %(name)s: %(message)s")

    json_path = Path(args.atlas) if args.atlas else config.ATLAS_JSON
    strict = args.strict or bool(config.STRICT_ATLAS)
    try:
        data = _read_atlas_json(json_path)
        png_path = _resolve_png(data, json_path, args.png)
        image = _load_image(png_path)
        report, _ = _validate(data, image, json_path, png_path, strict)
    except AssetsMissingError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    except AtlasValidationError as exc:
        print(f"[{BLOCK}] {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print()
        print(report.render(verbose=args.verbose))

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
