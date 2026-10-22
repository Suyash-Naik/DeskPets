# AGENTS.md

CODEX_AGENT — guidance for Codex in LC-pet-stat.

Before animation or atlas work, also read docs/codex-animation-pipeline-guide.md.

## Project and current status

LC-pet-stat is a planned lightweight, frameless, always-on-top Windows desktop companion. Layla and Chico react to CPU/RAM/GPU activity and show a hover stats card.

Read [BuildPlan.md](BuildPlan.md) for product architecture and [the animation pipeline guide](docs/codex-animation-pipeline-guide.md) for current art/integration procedure. The plan's atlas dimensions and frame counts predate current deliverables. Actual serialized atlas metadata is authoritative for slicing and timing; documented inconsistencies still need explicit resolution.

Art production and atlas integration are complete through the working-together and updated yarn sequences. The assets-root PNG/JSON **exist**. A `src/` directory is not currently present; the runtime is not verified end-to-end. The repository now has `.git/`; do not repeat earlier claims that it is not initialized or assume lockfile tracking status without checking Git.

## Environment

Currently windows only, expanded to linux and macos upon further development.  Use Pixi and `pixi.toml`/`pixi.lock`, not global Python, global pip, a new venv, or `pyproject.toml` as an alternate environment definition. Use `pixi run` for project Python commands.

Current default art/QA dependencies:

| Dependency | Constraint |
| --- | --- |
| Python | >=3.14.7,<3.15 |
| PyQt6 | >=6.8.1,<7 |
| napari | >=0.9.1,<0.10 |
| JupyterLab | >=4.6.3,<5 |
| ipywidgets | >=8.1.9,<9 |
| Pillow | >=12.3.0,<13 |
| NumPy | >=2.5.3,<3 |

Pillow and NumPy are now explicit dependencies. The declared `pets` feature also includes Python, PyQt6, NumPy, psutil >=7.0.0 and nvidia-ml-py >=13.610.43,<14, and defines `python -m src.main` as the `pets` task. This declaration is not evidence that the absent application source runs. Inspect Pixi's effective environment before claiming dependency isolation.

```powershell
pixi install
pixi run check
pixi run check Layla-ball-rolling
pixi run check --json
pixi run qa
pixi run -e pets pets  # declared runtime task; requires src.main
```

The local skill `assets/codex/.agents/skills/hatch-pet-local/SKILL.md` adapts packaging to Pixi. The unavailable `load_workspace_dependencies` tool is not a Python dependency to install. The globally installed hatch-pet skill remains unchanged.

## Desktop atlas contract

Files:
- `assets/layla-and-chico-pet-spritesheet.png`
- `assets/layla-and-chico-pet-spritesheet.json`

Current dimensions: **1920×2080**, **10×10 grid**, **192×208 cells**, **82 used frames**. Registered sources are 320×320. Packing uses one 0.6 scale to 192×192 and vertical padding (0,8). All ten jump frames are retained. Never truncate to the old eight-frame plan blindly.

The loader contract includes `rows[].name`, integer `rows[].frames`, per-frame rectangles and `timing_ms`. Read counts, geometry and durations from JSON. JSON timing is not enacted merely by exporting it: the application must consume it. The prototype's fixed 120 ms timer does not reproduce source timing.

| Row | State | Frames | Current source folder | Intended use |
| --- | --- | ---: | --- | --- |
| 0 | idle-attentive | 8 | Chico-alert | Idle |
| 1 | running-playful-roll | 8 | Layla-ball-rolling | Moderate work / yarn play |
| 2 | running-playful-jump | 10 | Chico-jump | High workload |
| 3 | waiting-deep-sleep | 8 | Chico-layla-nap | User inactivity |
| 4 | waiting-dozy | 8 | Layla-sleeping | User inactivity |
| 5 | blocked-grumpy | 8 | Layla-chico-mad | Warning / blocked |
| 6 | needs-input-sad | 8 | Chico-sad | Input needed |
| 7 | running-busy-play | 8 | Layla-strolling | Moderate work |
| 8 | ready-pleased | 8 | Layla-Chico-Wave | Completion |
| 9 | working-together | 8 | Layla-Chico-Working | Optional working animation |

Keep these names stable. `working-together` is the user-requested name; it was initially drafted as `running-typing`. Selecting the new row requires state-mapper integration, not just an atlas update. Row 7 is a one-shot stroll ending in an intentional tail-exit pose; respect its `loop: false`.

Historical planned CPU thresholds remain 20/60/80 percent and RAM 60/85 percent. They are design targets, not tested code. Resolve shared triggers (rows 1/7, 3/4) and selection of row 9 explicitly in runtime work.

## Separate Codex app pets

`assets/codex/delivery-001/codex-app/` contains `layla-and-chico-generated` and `layla-and-chico-sleepy` packages. These are supported **v1** conversions: 1536×1872, 8×9, fixed Codex row semantics/counts/timing, no v2 pointer gaze. Each includes the requested long PNG/JSON names and the discoverable `pet.json`.

They are separate from the desktop atlas, were not installed during this work, and were not refreshed by subsequent desktop row updates. Do not call a custom desktop atlas Codex-compatible by renaming its manifest. Do not overwrite existing installed pets when merely generating art.

## Asset workflow

Use the numbered stages in the pipeline guide. The canonical checker input is `02-extracted-frames/`; new normalized outputs belong in `03-qa-reference/*-aligned.png`. Older packages use `normalized-02`. Determine registered frames by 320×320 dimensions and ownership, not filename suffix alone.

Preserve source identity:
- Layla: black, pointed upright ears, slim, pale paw/chest markings, amber eyes.
- Chico: lighter charcoal, folded ears, round cheeks, stockier, amber eyes.
- Paired working/wave poses: Layla viewer-left, Chico viewer-right.

Use built-in image generation for new artwork. Use deterministic extraction/registration for approved strips. Preserve references, raw outputs, previous crops, and reports. Source replacement by the user may change a filename stem; inspect the current directory rather than reusing obsolete paths.

A generated equal-slot request is not proof of equal-slot output. The yarn and strolling strips required measured pose-gap boundaries to avoid clipping. Keep yarn with its corresponding pose; it may be detached but must not become the cat alignment anchor.

Anchor horizontally at x=160 on the largest 4-connected component and use one shared scale. Grounded sequences share baseline y=291 inclusive. Airborne/rolling sequences must preserve meaningful vertical motion; per-frame bottom pinning can erase it. Document the shared vertical transform and review baseline warnings in that context.

The working laptop/contact-shadow and floating yarn exceptions are specific user authorizations. They do not permit arbitrary scenery or props in other sequences. For working-together, the main pair and laptop are connected; frame 5 also has a small detached tail fragment.

## Current QA registry

Checked with `pixi run check --json` on 2026-09-14; command exits 0. A PASS here covers the checker's scope, not every original visual constraint.

| Sequence | Layout | Current project QA |
| --- | --- | --- |
| Chico-alert | aligned-03 | PASS |
| Chico-jump | normalized-02 | Warnings: limited lift, horizontal anchors, trailing literal escape in spec |
| Chico-layla-nap | aligned-03 | PASS |
| Chico-sad | aligned-03 | Baseline warnings |
| Layla-ball-rolling | aligned-03 | PASS WITH WARNINGS: 0 blockers, 8 warnings |
| Layla-chico-mad | normalized-02 | PASS |
| Layla-Chico-Wave | aligned-03 | PASS |
| Layla-Chico-Working | aligned-03 | PASS; visual deviations recorded separately |
| Layla-sleeping | normalized-02 | Horizontal anchor / content-height warnings |
| Layla-strolling | aligned-03 | Source-package edge warnings; atlas uses corrected derived extraction |

The exact requested `pixi run check Layla-ball-rolling` also exited 0: seven baseline warnings and one content-height warning. Shared source vertical coordinates and one scale were retained. Do not report it as warning-free or flatten poses to silence warnings.

Gate behavior:
- 0 = clean or warnings only; 1 = BLOCK; 2 = bad usage/setup.
- Missing spec during preparation is a warning; malformed or mismatched present specs can block.
- Run after registration and again with completed metadata.
- Inspect visual warnings and preview motion; do not weaken thresholds to manufacture a pass.
- Verify image/manifest links and duration sums separately; the gate is not a complete atlas or timing validator.

## Current inconsistencies and limitations

These are the working state observed on disk, not requests to silently alter unrelated files:

1. The working folder is now `Layla-Chico-Working` and its sequence id is `layla-chico-working`, but files retain `layla-chico-typing` stems. Root atlas row 9 still refers to the former `Layla-Chico-Typing` folder in provenance/source paths.
2. The current working spec declares 1800 ms total; its eight timings sum to 1300 ms. The integrated atlas loop from this conversation is 1300 ms. Resolve intentionally before a timing change.
3. Working art exceeds the original five-colour cap and exact choreography/extent requirements. Reviewed width varies 276–280 px; frame 5 has a 101-pixel detached tail fragment. Project QA PASS does not certify the strict original visual contract.
4. Chico-jump retains ten frames but only about 0–3 px measured body lift; it also has a malformed trailing literal escape in its source spec. Do not cite it as a verified ideal airborne-registration example.
5. Source strolling frames 6–8 still have edge warnings. The desktop atlas uses corrected extraction under `assets/codex/run-002/derived/Layla-strolling/`; preserve that distinction.
6. Historical scripts contain old typing paths, fixed source inventories or one-off completion steps. Inspect before rerunning; do not assume a historical builder safely rebuilds the current atlas.
7. Current no-blocker QA does not mean missing provenance paths, pose semantics or duration totals have been validated.

## Preservation and integration

No deletion was authorized in this conversation. Keep old sources and revisions. A user request to update an atlas authorizes updating the corresponding current PNG/JSON after backup and validation; do not request the same permission again. Generation alone does not authorize installation or integration.

Current backups and evidence:
- `assets/codex/desktop-atlas-001/previous/`: before adding working-together.
- `assets/codex/yarn-integration-001/previous/`: before yarn QA/metadata/atlas replacement.
- `assets/codex/yarn-integration-001/validation.json`: latest yarn integration results.
- `assets/codex/scripts/`: deterministic helpers.
- `assets/codex/delivery-001/`: earlier separate Codex and desktop deliverables.

When updating a row, verify other rows remain pixel-identical, source art is unchanged, unused cells are transparent, source paths resolve, and hashes agree. Publish the PNG/JSON pair consistently. The root JSON uses `source_path_base: "."`; copies in nested revision folders need appropriate relative bases.

## Future runtime work

`BuildPlan.md` is a prototype sketch, not production code:
- Use owned image buffers or QImage decoding/copying; avoid QImage referencing temporary Python bytes without a safe lifetime/stride.
- Provide thread cancellation and clean shutdown. Never touch widgets from telemetry workers.
- Keep GUI work non-blocking; configure polling rather than accidentally pinning it with a blocking one-second call.
- Put thresholds/timing in configuration or sequence metadata and add hysteresis/dwell time.
- Resolve assets relative to their actual location and read current geometry.
- Implement tray, stats card and click-through deliberately; their existence has not been verified.
- Windows temperature telemetry needs a supported provider; do not assume psutil exposes Linux-style sensor APIs.
- GPU dependency declarations do not prove GPU availability or sensor behavior.

## Validation and working conventions

- Use format libraries/builders where available and reopen outputs with their real parsers.
- JSON: explicit UTF-8 for both reading and writing, no BOM, no trailing text; verify count/timing/path/hash invariants.
- GIF: validate all frames, dimensions, duration and order; use as a reference, not an atlas source.
- Notebooks: use nbformat builders, unique cell IDs and `nbformat.validate`.
- PNG: verify RGBA, geometry, nonempty used cells, empty unused cells and round-trip pixels.
- Keep approval provenance and warnings explicit. Do not convert source approval into a claim of exact prompt compliance.
- Update this registry when a sequence lands, using current disk evidence and scoped QA.
- For documentation-only requests, update only the named documents; record discrepancies without repairing assets or runtime code.
