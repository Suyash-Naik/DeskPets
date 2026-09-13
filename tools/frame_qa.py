"""Frame QA for animation sequences — the gate between pipeline step 2 and step 3.

Run this after extracting frames into ``02-extracted-frames`` and before generating
``03-qa-reference`` sheets, GIFs, or ``04-integration-notes``.

    pixi run check                      # every sequence
    pixi run check Layla-strolling      # one sequence
    pixi run python tools/frame_qa.py --json      # machine-readable

Read-only: nothing here writes into ``assets/``.

Exit codes: 0 clean or warnings only, 1 at least one BLOCK, 2 bad usage.

``tools/frame_qa.ipynb`` imports from this module, so the checks have one definition.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

# --- Tunables ----------------------------------------------------------------

CANVAS = (320, 320)        # expected registration canvas (w, h)
ALPHA_FLOOR = 16           # alpha above this counts as content
EDGE_MARGIN = 2            # px band at the canvas edge that should stay empty
BASELINE_TOLERANCE = 3     # px drift allowed between grounded frames
AIRBORNE_LIFT = 8          # px a frame must rise to read as genuinely airborne
SHADOW_LUMA = 45           # luma at or below this is shadow, not body
DUPE_THRESHOLD = 1.0       # mean abs RGBA delta below this = near-identical frames
CHROMA_MAX = 50            # residual green pixels tolerated before warning
SCALE_SPREAD = 0.35        # fractional content-height spread before warning

# The aligner's registration contract, per codex-animation-pipeline-guide.md step 5:
# shared 320x320 canvas, body centred on x=160, baseline at y=292 (measured 291 on disk).
ANCHOR_X = 160             # expected body centre, from the largest connected component
ANCHOR_BASELINE = 291      # expected bbox bottom (shadow included)
ANCHOR_X_TOLERANCE = 4
ANCHOR_BASELINE_TOLERANCE = 2

STAGE_CROPS = "02-extracted-frames"
STAGE_ALIGNED = "03-qa-reference"
ALIGNED_SUFFIX = "-aligned"

AIRBORNE_WORDS = ("airborne", "takeoff", "jump", "descent", "leap")

FRAME_RE = re.compile(r"^(?P<stem>.+?)-frame-(?P<idx>\d+)(?P<suffix>.*)\.png$", re.I)

BLOCK, WARN, INFO = "BLOCK", "WARN", "INFO"
_SEVERITY_ORDER = {BLOCK: 0, WARN: 1, INFO: 2}


# --- Repo layout -------------------------------------------------------------

def find_root(start: Path | None = None) -> Path:
    """Walk up until a directory containing ``assets/`` is found."""
    root = (start or Path.cwd()).resolve()
    while not (root / "assets").is_dir() and root != root.parent:
        root = root.parent
    return root


def gen_dir(root: Path | None = None) -> Path:
    return (root or find_root()) / "assets" / "gen-image"


def package_dir(sequence: str, revision: str | None = None, root: Path | None = None) -> Path:
    base = gen_dir(root) / sequence
    return base / revision if revision else base


def frame_dir(sequence: str, revision: str | None = None, root: Path | None = None) -> Path:
    """The step-02 folder. Kept as the name the rest of the pipeline uses."""
    return package_dir(sequence, revision, root) / STAGE_CROPS


def discover(root: Path | None = None) -> list[tuple[str, str | None]]:
    """Every (sequence, revision) pair that has an ``02-extracted-frames`` folder."""
    gen = gen_dir(root)
    found: list[tuple[str, str | None]] = []
    if not gen.is_dir():
        return found
    for seq_dir in sorted(p for p in gen.iterdir() if p.is_dir()):
        for rev in [None] + sorted(
            p.name for p in seq_dir.iterdir()
            if p.is_dir() and p.name.startswith("revision-")
        ):
            pkg = package_dir(seq_dir.name, rev, root)
            if (pkg / STAGE_CROPS).is_dir() or (pkg / STAGE_ALIGNED).is_dir():
                found.append((seq_dir.name, rev))
    return found


# --- Image helpers -----------------------------------------------------------

def load_rgba(path: Path) -> np.ndarray:
    """Frame as an (H, W, 4) uint8 array."""
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.uint8)


def content_bbox(rgba: np.ndarray, floor: int = ALPHA_FLOOR):
    """(left, top, right, bottom) of non-transparent pixels, or None if empty."""
    mask = rgba[..., 3] > floor
    if not mask.any():
        return None
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])


def body_bottom(rgba: np.ndarray, floor: int = ALPHA_FLOOR,
                shadow_luma: int = SHADOW_LUMA) -> int | None:
    """Lowest row of *body* pixels, ignoring the near-black drop shadow.

    A shadow stays planted on the ground even when the cat is mid-air, so the
    plain alpha bbox reports every frame as grounded. Excluding dark pixels is
    what makes an airborne frame measurable.
    """
    px = rgba.astype(np.int16)
    luma = 0.299 * px[..., 0] + 0.587 * px[..., 1] + 0.114 * px[..., 2]
    mask = (px[..., 3] > floor) & (luma > shadow_luma)
    if not mask.any():
        return None
    return int(np.flatnonzero(mask.any(axis=1))[-1])


def body_center_x(rgba: np.ndarray, floor: int = ALPHA_FLOOR) -> int | None:
    """Horizontal centre of the largest connected component.

    The guide anchors on the cat body and explicitly excludes detached effects
    (a ``?``, ``Z`` marks, tears). A plain bbox centre would be dragged sideways
    by those, so the biggest blob is what gets measured.
    """
    from scipy import ndimage

    mask = rgba[..., 3] > floor
    if not mask.any():
        return None
    labels, count = ndimage.label(mask)
    if count == 0:
        return None
    sizes = ndimage.sum(mask, labels, range(1, count + 1))
    body = labels == (int(np.argmax(sizes)) + 1)
    cols = np.flatnonzero(body.any(axis=0))
    return int((cols[0] + cols[-1]) // 2)


def load_spec(path: Path):
    """Tolerant ``sequence-spec.json`` reader. Returns ``(spec, note)``.

    Survives the two malformations seen in this repo: a UTF-8 BOM, and a literal
    backslash-n written after the closing brace.
    """
    if not path.exists():
        return None, "no sequence-spec.json"
    raw = path.read_text(encoding="utf-8-sig")
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        pass
    try:
        spec, end = json.JSONDecoder().raw_decode(raw.lstrip())
        trailing = raw.lstrip()[end:].strip()
        return spec, f"parsed, but malformed on disk — trailing {trailing!r}"
    except json.JSONDecodeError as exc:
        return None, f"unparseable: {exc}"


def pick_stem(sequence: str, stem_counts: Counter) -> str | None:
    """Which filename stem belongs to this sequence?

    Frame counts alone are not enough: a folder can hold more foreign files than
    its own. Score each stem by how many words it shares with the folder name,
    and fall back to frequency only to break a tie.
    """
    if not stem_counts:
        return None
    wanted = set(re.split(r"[-_\s]+", sequence.lower())) - {"and", "the"}

    def score(stem: str):
        words = set(re.split(r"[-_\s]+", stem.lower()))
        return (len(wanted & words), stem_counts[stem])

    return max(stem_counts, key=score)


# --- Results -----------------------------------------------------------------

@dataclass
class Finding:
    severity: str
    check: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity:<5}] {self.check:<14} {self.message}"


@dataclass
class Resolved:
    """What the folder actually contains, before any judgement."""
    sequence: str
    revision: str | None
    folder: Path
    stem: str | None
    frames: list[Path] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)
    crops: list[Path] = field(default_factory=list)
    crop_indices: list[int] = field(default_factory=list)
    layout: str = "none"          # aligned-03 | normalized-02 | none
    frames_dir: Path | None = None
    foreign: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)
    stems_seen: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.sequence}/{self.revision}" if self.revision else self.sequence


@dataclass
class Report:
    resolved: Resolved
    findings: list[Finding] = field(default_factory=list)
    baseline: int | None = None
    lift: dict[int, int] = field(default_factory=dict)
    centers: dict[int, int] = field(default_factory=dict)
    spec: dict | None = None

    def add(self, severity: str, check: str, message: str) -> None:
        self.findings.append(Finding(severity, check, message))

    @property
    def blocks(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == BLOCK]

    @property
    def warns(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == WARN]

    @property
    def verdict(self) -> str:
        if self.blocks:
            return "BLOCKED"
        return "PASS WITH WARNINGS" if self.warns else "PASS"

    def sort(self) -> None:
        self.findings.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], f.check))

    def to_dict(self) -> dict:
        return {
            "sequence": self.resolved.sequence,
            "revision": self.resolved.revision,
            "folder": str(self.resolved.folder),
            "stem": self.resolved.stem,
            "layout": self.resolved.layout,
            "frames_dir": str(self.resolved.frames_dir) if self.resolved.frames_dir else None,
            "frame_count": len(self.resolved.frames),
            "crop_count": len(self.resolved.crops),
            "baseline": self.baseline,
            "lift": self.lift,
            "verdict": self.verdict,
            "findings": [vars(f) for f in self.findings],
        }


# --- Resolve -----------------------------------------------------------------

def _indexed(paths, stem, want_canvas=True):
    """Map frame index -> path, keeping only files of this stem at the wanted size."""
    out: dict[int, Path] = {}
    for path in sorted(paths):
        m = FRAME_RE.match(path.name)
        if not m or (stem and m.group("stem") != stem):
            continue
        at_canvas = Image.open(path).size == CANVAS
        if at_canvas == want_canvas:
            out.setdefault(int(m.group("idx")), path)
    return out


def resolve(sequence: str, revision: str | None = None, stem: str | None = None,
            root: Path | None = None) -> Resolved:
    """Work out which files are this sequence's crops and which are its aligned frames.

    Two layouts exist in this repo:

    * ``aligned-03`` — the current pipeline. Step 02 holds tight crops; step 05
      writes registered frames to ``03-qa-reference`` with an ``-aligned`` suffix.
    * ``normalized-02`` — older sequences, where the registered frames sit in
      ``02-extracted-frames`` beside the crops.

    Either way registered frames are identified by CANVAS size, never by suffix.
    """
    pkg = package_dir(sequence, revision, root)
    crops_dir = pkg / STAGE_CROPS
    aligned_dir = pkg / STAGE_ALIGNED
    if not crops_dir.is_dir() and not aligned_dir.is_dir():
        raise FileNotFoundError(pkg)

    crop_pngs = sorted(crops_dir.glob("*.png")) if crops_dir.is_dir() else []
    parsed = [(p, FRAME_RE.match(p.name)) for p in crop_pngs]
    counts = Counter(m.group("stem") for _, m in parsed if m)
    dominant = stem or pick_stem(sequence, counts)

    # Step 05 output: <stem>-frame-NN-aligned.png at CANVAS, in 03-qa-reference.
    aligned_pngs = ([p for p in aligned_dir.glob("*.png") if ALIGNED_SUFFIX in p.stem]
                    if aligned_dir.is_dir() else [])
    if dominant is None and aligned_pngs:
        # Step 02 can be empty mid-run; fall back to naming the stem from step 03.
        dominant = pick_stem(sequence, Counter(
            m.group("stem") for m in (FRAME_RE.match(p.name) for p in aligned_pngs) if m))
    aligned = _indexed(aligned_pngs, dominant, want_canvas=True)

    if aligned:
        layout, frames_dir = "aligned-03", aligned_dir
        frames = aligned
    else:
        frames = _indexed(crop_pngs, dominant, want_canvas=True)   # legacy layout
        layout = "normalized-02" if frames else "none"
        frames_dir = crops_dir if frames else None

    crops = _indexed(crop_pngs, dominant, want_canvas=False)

    return Resolved(
        sequence=sequence,
        revision=revision,
        folder=crops_dir,
        stem=dominant,
        frames=[frames[i] for i in sorted(frames)],
        indices=sorted(frames),
        crops=[crops[i] for i in sorted(crops)],
        crop_indices=sorted(crops),
        layout=layout,
        frames_dir=frames_dir,
        foreign=sorted(p.name for p, m in parsed if m and m.group("stem") != dominant),
        other=sorted(p.name for p, m in parsed if not m),
        stems_seen=sorted(counts),
    )


# --- Checks ------------------------------------------------------------------

def check(res: Resolved) -> Report:
    """Run every check against a resolved sequence."""
    rep = Report(resolved=res)
    frames = [load_rgba(p) for p in res.frames]
    boxes = [content_bbox(f) for f in frames]
    bodies = [body_bottom(f) for f in frames]

    # 1. Frames present, registered, and contiguously numbered from 1.
    if not frames and res.crops:
        rep.add(BLOCK, "not-normalized",
                f"{len(res.crops)} tight crop(s) in {STAGE_CROPS}, but no "
                f"{CANVAS[0]}x{CANVAS[1]} frames in {STAGE_ALIGNED} — pipeline step 5 "
                "(normalize and align) has not been run")
    elif not frames:
        rep.add(BLOCK, "frame-set",
                f"no frames in {STAGE_CROPS} or {STAGE_ALIGNED} — the package is empty "
                "or the pipeline is mid-run")
    else:
        missing = sorted(set(range(1, max(res.indices) + 1)) - set(res.indices))
        if missing:
            rep.add(BLOCK, "frame-set", f"gap in numbering — missing frame(s) {missing}")
        if res.indices[0] != 1:
            rep.add(WARN, "frame-set", f"numbering starts at {res.indices[0]}, not 1")

    # 2. Files belonging to another sequence.
    if res.foreign:
        stems = sorted({FRAME_RE.match(f).group("stem") for f in res.foreign})
        rep.add(BLOCK, "contamination",
                f"{len(res.foreign)} file(s) from another sequence in "
                f"{res.folder.name} (prefix(es) {stems})")

    # 3. Canvas size — the aligned set must all be on the registration canvas.
    for idx, frame in zip(res.indices, frames):
        size = (frame.shape[1], frame.shape[0])
        if size != CANVAS:
            rep.add(BLOCK, "canvas",
                    f"frame {idx:02d} is {size[0]}x{size[1]}, expected "
                    f"{CANVAS[0]}x{CANVAS[1]}")

    # 4. Empty frames.
    for idx, box in zip(res.indices, boxes):
        if box is None:
            rep.add(BLOCK, "empty-frame", f"frame {idx:02d} is fully transparent")

    # 4. Background actually cut out.
    for idx, frame in zip(res.indices, frames):
        if frame[..., 3].min() == 255:
            rep.add(BLOCK, "alpha",
                    f"frame {idx:02d} has no transparent pixels — background not removed")

    # 5. Residual chroma-key green.
    for idx, frame in zip(res.indices, frames):
        r, g, b, a = (frame[..., i].astype(np.int16) for i in range(4))
        green = int(((g - r > 40) & (g - b > 40) & (a > ALPHA_FLOOR)).sum())
        if green > CHROMA_MAX:
            rep.add(WARN, "chroma", f"frame {idx:02d} has {green} green-ish pixels left")

    # 6. Neighbouring pose bleeding in at the canvas edge.
    for idx, frame in zip(res.indices, frames):
        a = frame[..., 3]
        edge = np.concatenate([
            a[:EDGE_MARGIN].ravel(), a[-EDGE_MARGIN:].ravel(),
            a[:, :EDGE_MARGIN].ravel(), a[:, -EDGE_MARGIN:].ravel(),
        ])
        if (edge > ALPHA_FLOOR).any():
            rep.add(WARN, "edge-bleed",
                    f"frame {idx:02d} has content touching the canvas edge — "
                    "possible neighbouring pose in the crop")

    # 7. Baseline registration, measured on the body with the shadow excluded.
    measured = [(i, y) for i, y in zip(res.indices, bodies) if y is not None]
    if measured:
        rep.baseline = max(y for _, y in measured)
        rep.lift = {i: rep.baseline - y for i, y in measured}
        grounded = [i for i, lift in rep.lift.items() if lift <= BASELINE_TOLERANCE]
        if grounded:
            spread = max(rep.lift[i] for i in grounded) - min(rep.lift[i] for i in grounded)
            if spread > BASELINE_TOLERANCE:
                rep.add(WARN, "baseline",
                        f"grounded frames vary by {spread}px at the baseline")

    # 8. Near-identical consecutive frames.
    for a_i, b_i, a, b in zip(res.indices, res.indices[1:], frames, frames[1:]):
        delta = float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())
        if delta < DUPE_THRESHOLD:
            rep.add(WARN, "duplicate",
                    f"frames {a_i:02d} and {b_i:02d} are near-identical (delta {delta:.2f})")

    # 9. Registration anchors, per guide step 5: body centre x=160, baseline y=292.
    if frames:
        for idx, frame in zip(res.indices, frames):
            cx = body_center_x(frame)
            if cx is None:
                continue
            rep.centers[idx] = cx
            if abs(cx - ANCHOR_X) > ANCHOR_X_TOLERANCE:
                rep.add(WARN, "anchor-x",
                        f"frame {idx:02d} body centre at x={cx}, expected "
                        f"{ANCHOR_X}±{ANCHOR_X_TOLERANCE}")
        for idx, box in zip(res.indices, boxes):
            if box and abs(box[3] - ANCHOR_BASELINE) > ANCHOR_BASELINE_TOLERANCE:
                rep.add(WARN, "anchor-baseline",
                        f"frame {idx:02d} sits at y={box[3]}, expected baseline "
                        f"{ANCHOR_BASELINE}±{ANCHOR_BASELINE_TOLERANCE}")

    # 10. Scale consistency.
    heights = [b[3] - b[1] for b in boxes if b]
    if heights and (max(heights) - min(heights)) / max(heights) > SCALE_SPREAD:
        rep.add(WARN, "scale",
                f"content height ranges {min(heights)}–{max(heights)}px — frames may "
                "not share one scale")

    _check_spec(res, rep)
    rep.sort()
    return rep


def _check_spec(res: Resolved, rep: Report) -> None:
    """Cross-check ``sequence-spec.json`` against what is on disk."""
    spec_path = res.folder.parent / "04-integration-notes" / "sequence-spec.json"
    spec, note = load_spec(spec_path)
    rep.spec = spec

    if spec is None and not spec_path.exists():
        # 04-integration-notes is written alongside 03-qa-reference, so at this
        # gate the spec legitimately may not exist yet.
        rep.add(WARN, "spec",
                "not written yet — expected if 04-integration-notes has not been generated")
        return
    if spec is None:
        rep.add(BLOCK, "spec", note)
        return
    if note:
        rep.add(WARN, "spec", note)

    declared = spec.get("frame_count")
    if declared is not None and declared != len(res.frames):
        rep.add(BLOCK, "spec",
                f"frame_count says {declared}, found {len(res.frames)} frames on disk")

    timing = spec.get("timing_ms") or []
    if timing and len(timing) != len(res.frames):
        rep.add(BLOCK, "spec",
                f"timing_ms has {len(timing)} entries for {len(res.frames)} frames")

    phases = spec.get("phases") or []
    if phases and len(phases) != len(res.frames):
        rep.add(WARN, "spec",
                f"phases has {len(phases)} entries for {len(res.frames)} frames")

    entries = spec.get("frames", [])
    for pos, entry in enumerate(entries, start=1):
        idx = entry.get("index", pos)
        rel = entry.get("file", "")
        target = res.folder.parent / rel
        m = FRAME_RE.match(Path(rel).name)
        if not rel:
            rep.add(WARN, "spec", f"frame {idx}: no file given")
        elif not target.exists():
            rep.add(BLOCK, "spec", f"frame {idx}: {rel} does not exist")
        elif m and res.stem and m.group("stem") != res.stem:
            rep.add(BLOCK, "spec",
                    f"frame {idx}: points at '{Path(rel).name}' — that is "
                    f"'{m.group('stem')}' art, not '{res.stem}'")

    # Frames the spec calls airborne should actually leave the baseline.
    for pos, entry in enumerate(entries, start=1):
        idx = entry.get("index", pos)
        phase = str(entry.get("phase", phases[pos - 1] if pos <= len(phases) else ""))
        claims_air = (entry.get("grounded") is False
                      or any(w in phase.lower() for w in AIRBORNE_WORDS))
        if claims_air and rep.lift.get(idx, 0) < AIRBORNE_LIFT:
            rep.add(WARN, "airborne",
                    f"frame {idx:02d} ('{phase}') sits {rep.lift.get(idx, 0)}px off the "
                    "baseline — the spec calls it airborne but it reads as grounded")


def review_stack(res: Resolved):
    """A uniform (N, H, W, 4) stack for viewing, plus what it actually is.

    Returns ``(stack, indices, registered)``. When the sequence has been normalized
    this is just the registered CANVAS frames, and positions are meaningful.

    When it has not, the tight crops are padded onto a common canvas so they can
    still be looked at. **That padding is not registration** — each crop is centred
    horizontally and sat on the bottom edge, which is a guess, not the pipeline's
    baseline. Judge cutout quality and pose from this; do not judge drift, lift, or
    alignment.
    """
    paths = res.frames if res.frames else res.crops
    indices = res.indices if res.frames else res.crop_indices
    registered = bool(res.frames)
    if not paths:
        return np.zeros((0, 1, 1, 4), dtype=np.uint8), [], registered

    images = [load_rgba(p) for p in paths]
    if registered:
        return np.stack(images), indices, True

    height = max(im.shape[0] for im in images)
    width = max(im.shape[1] for im in images)
    stack = np.zeros((len(images), height, width, 4), dtype=np.uint8)
    for slot, im in zip(stack, images):
        ih, iw = im.shape[:2]
        top = height - ih                    # sit on the bottom edge
        left = (width - iw) // 2             # centre horizontally
        slot[top:top + ih, left:left + iw] = im
    return stack, indices, False

def run(sequence: str, revision: str | None = None, stem: str | None = None,
        root: Path | None = None) -> Report:
    """Resolve then check, in one call."""
    return check(resolve(sequence, revision, stem, root))


# --- CLI ---------------------------------------------------------------------

def _print(rep: Report, verbose: bool = False) -> None:
    res = rep.resolved
    print(f"\n=== {res.label} ===")
    print(f"  folder : {res.folder}")
    print(f"  stem   : {res.stem}   (of {res.stems_seen})")
    where = f" from {res.frames_dir.name}" if res.frames_dir else ""
    print(f"  layout : {res.layout}")
    print(f"  frames : {len(res.frames)} at {CANVAS[0]}x{CANVAS[1]}{where}"
          f"{f', {len(res.crops)} crop(s)' if res.crops else ''}")
    if verbose and res.crops:
        sizes = sorted({Image.open(p).size for p in res.crops})
        print(f"  crops  : {sizes}")
    if verbose and rep.lift:
        print(f"  lift   : baseline y={rep.baseline}, {rep.lift}")
    if res.other:
        print(f"  other  : {res.other}")
    print()
    for finding in rep.findings:
        print(f"  {finding}")
    if not rep.findings:
        print("  no findings")
    print(f"\n  -> {rep.verdict}"
          f" ({len(rep.blocks)} block, {len(rep.warns)} warn)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="frame_qa",
        description="QA gate for 02-extracted-frames, run before generating "
                    "03-qa-reference and GIFs.")
    parser.add_argument("sequence", nargs="*",
                        help="sequence folder name(s); default is every sequence found")
    parser.add_argument("--revision", help="revision subfolder, e.g. revision-03-final")
    parser.add_argument("--stem", help="force the filename stem instead of inferring it")
    parser.add_argument("--root", type=Path, help="repo root (default: auto-detect)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="also show crop sizes and per-frame lift")
    args = parser.parse_args(argv)

    root = args.root or find_root()
    if not gen_dir(root).is_dir():
        print(f"no assets/gen-image under {root}", file=sys.stderr)
        return 2

    if args.sequence:
        targets = [(s, args.revision) for s in args.sequence]
    else:
        targets = discover(root)
        if not targets:
            print("no sequences with 02-extracted-frames found", file=sys.stderr)
            return 2

    reports = []
    for seq, rev in targets:
        try:
            reports.append(run(seq, rev, args.stem, root))
        except FileNotFoundError as exc:
            print(f"{seq}: no such folder — {exc}", file=sys.stderr)
            return 2

    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2))
    else:
        for rep in reports:
            _print(rep, args.verbose)
        if len(reports) > 1:
            print("\n" + "-" * 60)
            for rep in reports:
                print(f"  {rep.resolved.label:<28} {rep.verdict}")

    return 1 if any(r.blocks for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
