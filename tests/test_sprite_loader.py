"""`src/core/sprite_loader.py` -- atlas validation and slicing.

Two jobs. The first is that every frame the app will ever draw is a real, correctly sized,
Qt-owned `QPixmap` -- which is where the BuildPlan prototype's use-after-free lived. The second
is that a *broken* atlas produces a clear BLOCK report rather than a crash or, worse, a pet
drawn from garbage rects.

Everything here is driven off the atlas JSON read independently in the `atlas_json` fixture,
never off the loader's own parse, so a test that agrees with the loader is agreeing with the
JSON and not with itself. Nothing hard-codes 192, 208, 8, 9 or 74 -- the contract forbids those
literals in `src/`, and a test that pins them would undo that the first time the art changes.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint

from src import config
from src.core import sprite_loader as sl
from src.core.state_machine import REQUIRED_STATES

from ._env import requires_atlas

pytestmark = requires_atlas

REPO_ROOT = Path(__file__).resolve().parent.parent


# =============================================================================================
# REGRESSION -- the prototype's Pillow use-after-free
# =============================================================================================


def test_regression_frames_are_valid_qt_owned_pixmaps(sheet, atlas_json) -> None:
    """Guards the BuildPlan prototype's `QImage(crop.tobytes(), w, h, Format_RGBA8888)`.

    Two defects in one call (PHASE1-LOG.md defects 1 and 2):

    * **Use-after-free.** The `bytes` temporary had no other reference, so it was freed while
      the `QImage` still pointed at it. It appeared to work only because CPython's allocator
      had not yet reused the block -- which is exactly the failure mode that ships.
    * **Missing `bytesPerLine`.** Latent on today's atlas (192 x 4 = 768 is already 4-aligned)
      and would have detonated silently on any future atlas with an odd frame width.

    The fix made both *unrepresentable* rather than merely fixed: `QImage(str(path))` +
    `QImage.copy(rect)`, whose buffers Qt owns. What a test can observe is the consequence --
    every pixmap is non-null, is exactly the declared cell size, and still holds real pixel
    data after the loader has returned and any temporary would long since have been collected.
    """
    cell = (atlas_json["frame_width"], atlas_json["frame_height"])
    bad: list[str] = []
    for name in sheet.names():
        for frame in sheet[name]:
            if frame.pixmap.isNull():
                bad.append(f"{name}[{frame.index}] is a null pixmap")
            elif (frame.pixmap.width(), frame.pixmap.height()) != cell:
                bad.append(f"{name}[{frame.index}] is "
                           f"{frame.pixmap.width()}x{frame.pixmap.height()}, want {cell}")
    assert not bad, bad

    # And the pixels survived: a frame that had been freed under Qt reads back as uniform
    # garbage or zeroes. Sampling one frame's opaque body proves the buffer is still live.
    image = sheet[sheet.default_animation][0].pixmap.toImage()
    distinct = {image.pixel(x, y)
                for y in range(0, image.height(), 5)
                for x in range(0, image.width(), 5)}
    assert len(distinct) > 4, "frame pixels look uniform -- the backing buffer may be dead"


def test_regression_pillow_is_not_a_runtime_dependency() -> None:
    """Contract 2.7: Pillow is imported nowhere in `src/` and is absent from the `pets` env.

    This is the structural half of the fix above. The strongest guarantee is the environment
    itself -- `pixi.toml`'s `[feature.pets.dependencies]` has no Pillow, so a runtime import
    would be an ImportError on a user's machine rather than a silent revival of the defect.
    The source scan guards the other direction: someone adding the dependency back because a
    module started importing it.

    The scan walks the AST rather than grepping for the string "PIL", because `config.py`,
    `pixi.toml` and this very file all mention Pillow in prose and a substring match would
    flag every one of them.

    `tests/test_packaging.py` guards the environment half of this in `pixi.toml`.
    """
    assert importlib.util.find_spec("PIL") is None, (
        "Pillow is importable in the environment running these tests. Run the suite with "
        "`pixi run test` (the lean `dev` environment); if that is what you did, the "
        "environment definition in pixi.toml has regressed -- see test_packaging.py.")

    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0].lower()
                if root in {"pil", "pillow", "imageqt"}:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports {name}")
    assert not offenders, offenders


# =============================================================================================
# The atlas, as loaded
# =============================================================================================


def test_the_real_atlas_validates_clean() -> None:
    """`validate_atlas()` on the shipped art: no BLOCKs, no WARNs.

    Runs the `QImage`-only path with no `QApplication` in the signature, which is what makes
    `python -m src.main --validate` work headless.
    """
    report = sl.validate_atlas()
    assert report.ok, report.render()
    assert report.blocks == ()
    assert report.warns == (), report.render()
    assert report.frame_count > 0
    assert "PASS" in report.render()


def test_every_required_state_is_in_the_atlas(sheet) -> None:
    """`REQUIRED_STATES` all have rows, so `sheet.require()` cannot throw at startup.

    Derived from `state_machine.REQUIRED_STATES` rather than a copy of the name list --
    `check_working.py`'s predecessor kept its own nine-name tuple, which silently stopped
    tracking the module the day an optional state was added.
    """
    sheet.require(list(REQUIRED_STATES))
    assert set(REQUIRED_STATES) <= set(sheet.names())


def test_frame_counts_and_loop_flags_come_from_the_json(sheet, atlas_json) -> None:
    """Counts and loop flags are read verbatim, never inferred from a grid position."""
    expected_counts = {row["name"]: len(row["frame_data"]) for row in atlas_json["rows"]}
    assert {name: len(sheet[name]) for name in sheet.names()} == expected_counts

    expected_loops = {row["name"]: bool(row["loop"]) for row in atlas_json["rows"]}
    assert {name: sheet[name].loop for name in sheet.names()} == expected_loops

    total = sum(len(sheet[name]) for name in sheet.names())
    assert total == atlas_json["frame_count"]


def test_per_frame_durations_are_authored_not_constant(sheet, atlas_json) -> None:
    """Frame timing comes from the atlas's own `duration_ms`, which is not a fixed tick.

    The prototype's 120 ms constant destroys the phrasing the art was drawn with; the atlas's
    `compatibility.notes` says so in as many words.
    """
    expected = {row["name"]: [f["duration_ms"] for f in row["frame_data"]]
                for row in atlas_json["rows"]}
    actual = {name: [f.duration_ms for f in sheet[name]] for name in sheet.names()}
    assert actual == expected

    default_row = actual[sheet.default_animation]
    assert len(set(default_row)) > 1, (
        f"{sheet.default_animation} has a constant frame time {default_row[0]} ms -- either the "
        "art really is evenly timed, or the loader is ignoring duration_ms")

    for name in sheet.names():
        assert sheet[name].duration_ms == sum(f.duration_ms for f in sheet[name])


def test_frame_rects_are_read_verbatim_from_the_json(sheet, atlas_json) -> None:
    """`source_rect` is the JSON's x/y/w/h, never `col * frame_width`.

    This is what makes a future ragged or repacked atlas a JSON-only change.
    """
    for row in atlas_json["rows"]:
        animation = sheet[row["name"]]
        for spec, frame in zip(row["frame_data"], animation, strict=True):
            rect = frame.source_rect
            assert (rect.x(), rect.y(), rect.width(), rect.height()) == (
                spec["x"], spec["y"], spec["width"], spec["height"])


def test_frame_offsets_are_zero_on_a_uniform_atlas(sheet) -> None:
    """`Frame.offset` is bottom-centred within the animation box; (0,0) for today's uniform art.

    It exists so a ragged or re-cropped atlas renders correctly with no code change -- and so
    that the jump's missing vertical lift has somewhere to land when the art gains it.
    """
    offsets = {(f.offset.x(), f.offset.y()) for n in sheet.names() for f in sheet[n]}
    assert offsets == {(0, 0)}


# --- sequence protocol --------------------------------------------------------------------------


def test_frame_at_wraps_but_getitem_does_not(sheet) -> None:
    """`frame_at` is modulo (the animator's accessor); `__getitem__` raises (contract 4.1)."""
    animation = sheet[sheet.default_animation]
    n = len(animation)
    assert animation.frame_at(n).index == 0
    assert animation.frame_at(n + 3).index == 3
    assert animation.frame_at(-1).index == n - 1
    with pytest.raises(IndexError):
        animation[n]
    assert len(list(animation)) == n


def test_unknown_name_raises_a_keyerror_that_lists_what_exists(sheet) -> None:
    """A KeyError whose message is just the bad key is useless while the art is WIP."""
    with pytest.raises(KeyError) as excinfo:
        sheet["not-a-state"]
    assert sheet.default_animation in str(excinfo.value)


def test_require_names_exactly_what_is_missing(sheet) -> None:
    """One clear error listing the absent rows -- the single place the app asserts its needs."""
    with pytest.raises(sl.AtlasValidationError) as excinfo:
        sheet.require([sheet.default_animation, "ghost-pet", "second-ghost"])
    message = str(excinfo.value)
    assert "ghost-pet" in message and "second-ghost" in message


def test_animations_mapping_is_read_only(sheet) -> None:
    """Nothing downstream may mutate the loaded atlas out from under the animator."""
    with pytest.raises(TypeError):
        sheet.animations["x"] = None  # type: ignore[index]


# --- alpha hit-testing ---------------------------------------------------------------------------


def test_alpha_at_distinguishes_the_pet_from_its_transparent_gutter(sheet) -> None:
    """Hit-testing is by alpha, not by rect.

    Measured in PHASE1-LOG.md: every frame has a ~25 px fully transparent gutter below the
    pet's feet, so a rect hit-test makes the pet feel like it has an invisible pedestal. The
    corner must read 0 and the content centre must read opaque.
    """
    name = sheet.default_animation
    assert sheet.alpha_at(name, 0, QPoint(0, 0)) == 0

    content = sheet[name][0].content_rect
    centre = QPoint(content.x() + content.width() // 2,
                    content.y() + content.height() // 2)
    assert sheet.alpha_at(name, 0, centre) > 0

    # Out of range and unknown names are 0, never an exception -- this runs inside mousePress.
    assert sheet.alpha_at(name, 0, QPoint(10_000, 0)) == 0
    assert sheet.alpha_at("no-such-animation", 0, centre) == 0


def test_content_rect_is_a_real_sub_rect_not_the_whole_cell(sheet, atlas_json) -> None:
    """`COMPUTE_CONTENT_BOUNDS` actually ran -- otherwise every bbox is the full cell.

    If numpy were missing the loader falls back to the full rect, which is correct but would
    make the gutter test above pass vacuously. This is the canary for that fallback.
    """
    cell = (atlas_json["frame_width"], atlas_json["frame_height"])
    full_cell_frames = [f"{n}[{f.index}]" for n in sheet.names() for f in sheet[n]
                        if (f.content_rect.width(), f.content_rect.height()) == cell]
    assert not full_cell_frames, (
        f"{len(full_cell_frames)} frames report the full cell as their content bounds; "
        "the numpy alpha-bbox scan did not run")


# =============================================================================================
# Corrupt atlases
# =============================================================================================


@pytest.fixture
def corrupt_atlas_dir(tmp_path: Path) -> Path:
    """A scratch directory for hand-damaged copies of the atlas JSON.

    Only the JSON is copied; every variant points back at the real PNG (via `_png()`), so these
    tests are about metadata damage rather than image decoding.

    A thin alias for `tmp_path`, kept because the name is what makes the call sites read
    correctly -- `_write(corrupt_atlas_dir, ...)` says what the directory is *for*.
    """
    return tmp_path


def _write(directory: Path, name: str, data: object) -> Path:
    path = directory / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _png() -> Path:
    return config.ATLAS_JSON.with_suffix(".png")


def test_frame_rects_off_the_image_are_blocks(corrupt_atlas_dir: Path, atlas_json) -> None:
    """Rects that fall outside the PNG are a BLOCK, and `load_spritesheet` refuses to load.

    This is the failure a stale JSON against a repacked PNG produces. Loading anyway would
    slice the pet out of whatever pixels happened to be there.
    """
    damaged = json.loads(json.dumps(atlas_json))
    damaged["rows"][0]["frame_data"][2]["x"] = 9000                     # off the right edge
    damaged["rows"][1]["frame_data"][0]["y"] = atlas_json["height"] - 10  # off the bottom
    path = _write(corrupt_atlas_dir, "off-image.json", damaged)

    report = sl.validate_atlas(path, png_path=_png())
    assert not report.ok
    assert len(report.blocks) == 2
    assert {issue.check for issue in report.blocks} == {"frame-bounds"}

    with pytest.raises(sl.AtlasValidationError):
        sl.load_spritesheet(path, png_path=_png())


def test_stale_sha_and_frame_count_are_warnings_not_blocks(
        corrupt_atlas_dir: Path, atlas_json) -> None:
    """Art iteration must not be blocked by a hash the packer forgot to refresh.

    `STRICT_ATLAS` is False by default precisely so a mid-iteration atlas still runs -- but the
    mismatch is always reported, because a repacked PNG can invalidate every rect in the JSON.
    """
    stale = json.loads(json.dumps(atlas_json))
    stale["image_sha256"] = "0" * 64
    stale["frame_count"] = 99
    path = _write(corrupt_atlas_dir, "stale.json", stale)

    report = sl.validate_atlas(path, png_path=_png())
    assert report.ok, "a stale hash must not block"
    assert len(report.warns) == 2, [(i.severity, i.check) for i in report.issues]


def test_strict_mode_promotes_those_warnings_to_a_refusal(
        corrupt_atlas_dir: Path, atlas_json) -> None:
    """`strict=True` is the ship-it setting: the same file that warns now refuses."""
    stale = json.loads(json.dumps(atlas_json))
    stale["image_sha256"] = "0" * 64
    stale["frame_count"] = 99
    path = _write(corrupt_atlas_dir, "stale-strict.json", stale)

    with pytest.raises(sl.AtlasValidationError):
        sl.load_spritesheet(path, png_path=_png(), strict=True)


def test_bom_and_trailing_whitespace_still_parse(corrupt_atlas_dir: Path, atlas_json) -> None:
    """The two malformed-spec faults this repo has already paid for once (CLAUDE.md).

    A BOM plus trailing newlines is what a hand-written or Notepad-saved JSON looks like. It is
    not corruption and must not be treated as such.
    """
    path = corrupt_atlas_dir / "bom.json"
    path.write_bytes("﻿".encode("utf-8")
                     + json.dumps(atlas_json).encode("utf-8") + b"\n\n")
    report = sl.validate_atlas(path, png_path=_png())
    assert report.ok
    assert report.frame_count == atlas_json["frame_count"]


def test_unparseable_json_raises_atlas_validation_error(corrupt_atlas_dir: Path) -> None:
    """Damaged beyond parsing is a typed error, not a raw `JSONDecodeError` out of startup."""
    path = corrupt_atlas_dir / "junk.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(sl.AtlasValidationError):
        sl.validate_atlas(path)


def test_a_missing_atlas_raises_assets_missing_error(corrupt_atlas_dir: Path) -> None:
    """Distinct from a *broken* atlas: "the art is not here" needs its own message."""
    with pytest.raises(sl.AssetsMissingError):
        sl.validate_atlas(corrupt_atlas_dir / "nope.json")


def test_error_types_form_the_documented_hierarchy() -> None:
    """Contract 4.1: both failure modes are catchable as one `SpriteLoadError` in `main.py`."""
    assert issubclass(sl.AssetsMissingError, sl.SpriteLoadError)
    assert issubclass(sl.AtlasValidationError, sl.SpriteLoadError)
    assert issubclass(sl.SpriteLoadError, RuntimeError)
