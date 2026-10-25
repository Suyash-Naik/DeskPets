# AGENTS.md

CODEX_AGENT — guidance for Codex in LC-pet-stat.

Before animation or atlas work, also read docs/codex-animation-pipeline-guide.md.

## Project and current status

LC-pet-stat is a lightweight, frameless, always-on-top Windows desktop companion. Layla and Chico react to CPU/RAM/swap/GPU/temperature/user-idle activity through ten sprite states and show a hover stats card. **Phase 1 is implemented.** Phase 2 (LLM chat + scoped MCP tools) is exploratory and not started.

Read [docs/PHASE1-CONTRACT.md](docs/PHASE1-CONTRACT.md) (authoritative interface contract), [docs/PHASE1-LOG.md](docs/PHASE1-LOG.md) (decisions, defects, measurements), `tests/README.md` (suite and regression index) and [the animation pipeline guide](docs/codex-animation-pipeline-guide.md) (art/integration procedure). `docs/BuildPlan.md` is the original sketch; its atlas dimensions and frame counts are historical. Actual serialized atlas metadata is authoritative for slicing and timing.

Art production and atlas integration are complete through the working-together and updated yarn sequences. The assets-root PNG/JSON exist and validate. The repository is under Git (27 commits on `main`); `src/config.py` and `src/main.py` have uncommitted changes (the `--demo` flag). Check Git rather than assuming tracking status of the lockfile.

Verified 2026-10-24: `--validate` is `PASS (0 block, 0 warn)` (1920×2080, 82 frames, 10 animations). `pixi run -e dev test -m "not slow"`: 188 collected, **184 pass, 3 fail, 1 skip**; the 35 slow tests were not run. The 3 failures are config drift (tests assert `CPU_MODERATE_MIN == 20`, `RAM_HIGH == 85`, dozy at 5 min; `config.py` now has `1.0`, `55.0`, `IDLE_DOZY_S = 60`). `RAM_HIGH` only colours the card's RAM meter; the state gate is `BAND_RAM_HIGH = (85, 80)`. Resolve by updating the tests or reverting the constants deliberately.

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

Pixi has three environments. `pets` (runtime): Python, PyQt6, NumPy, psutil >=7.0.0, nvidia-ml-py >=13.610.43,<14 — no Pillow, napari or Jupyter. `dev`: the same pins mirrored plus pytest >=9.1.1. Default: the art/QA stack above. `pets` and `dev` use `no-default-feature = true` (otherwise the default feature leaks Pillow and napari into them) and share a solve-group. `tests/test_packaging.py` asserts all of this; **Pillow must never become a runtime dependency**.

```powershell
pixi install
pixi run -e pets pets                  # launch
pixi run -e pets pets --validate       # atlas check, no GUI
pixi run -e pets pets --debug
pixi run -e pets pets --demo [SECONDS] # cycle every available animation (default 4 s)
pixi run -e dev test                   # full suite (~60 s)
pixi run -e dev test -m "not slow"     # fast subset (~6 s)
pixi run check
pixi run check Layla-ball-rolling
pixi run check --json
pixi run qa
```

## Runtime as implemented (`src/`)

~6,800 lines across `main.py`, `config.py`, `core/` (`sprite_loader`, `animator`, `state_machine`, `telemetry`, `platform/`) and `ui/` (`_overlay`, `pet_window`, `stats_card`, `tray`). ~4,800 lines of tests in 13 modules. Full per-module behaviour is in `CLAUDE.md`; the contract an agent must not break:

- **Threading.** Telemetry runs on a `QThread` worker driven by a `QTimer` (1500 ms, clamped 500–10,000). Only `telemetry.py` calls psutil. Widgets are touched only on the main thread via signals. `stop()` is `quit()` + `wait()` (measured 2–15 ms). A separate 250 ms sub-sampler emits a latched `input_activity_changed(bool)` from `GetLastInputInfo`.
- **Privacy.** Input detection is `GetLastInputInfo` only. No keyboard hook, `GetAsyncKeyState` polling or Raw Input — do not add one.
- **Platform seam.** `ctypes`, `sys.platform` and `win32` live only in `src/core/platform/`; `NullBackend` degrades other platforms. No sprite geometry literals (`192`, `208`, `8`, `9`, `74`) anywhere in `src/`.
- **Atlas as data.** The loader reads frame rects, counts, durations, loop flags and default animation from JSON, verifies sha256, builds `QPixmap`s with Qt-owned `QImage.copy()` (no Pillow, so the prototype's use-after-free cannot recur) and fails once, clearly, via `SpriteSheet.require()`.
- **State machine.** Ladder, first match wins: `blocked-grumpy` (temp ≥ 80 °C or swap ≥ 30 %) → `running-playful-jump` (CPU ≥ 80 or RAM ≥ 85) → sleep rows (idle ≥ 60 s `waiting-dozy`, ≥ 900 s `waiting-deep-sleep`, both only when CPU is not moderate) → `running-playful-roll` (CPU moderate band) → `working-together` (sustained input, optional row) → `idle-attentive` (unconditional default, deliberately not gated on RAM). Every gate is a Schmitt band in `config.BAND_*` plus `MIN_STATE_DWELL_MS = 4000`; escalations bypass dwell. Timed overrides (poke, greeting, flourishes) hand back via a single-shot timer that re-arms if it fires early. `ready-pleased` fires after CPU ≥ 60 % for ≥ 8 s then ≤ 20 % for 2 samples.
- **Tiebreaks (resolved).** Rows 1 vs 7: roll is resident, the stroll is a one-shot flourish (~25 s ± 5 s in the moderate band). Rows 3 vs 4: split by idle depth. Idle flourish every ~5 min ± 30 s, weighted roll 6 / stroll 3 / wave 1; load-mapped rows are excluded so state stays readable; the schedule is not reset when the pet briefly leaves idle.
- **One owner of reversion.** The animator holds a non-looping row's last frame and emits `sequence_finished`; the state machine alone reverts. Never add an animator-side fallback.
- **Optional rows.** `OPTIONAL_STATES = {working-together}`; `set_available_states()` makes an absent row unreachable by every route, so a state can ship before its art.
- **Window.** `PetWindow` is the atlas frame box (192×208), direct `paintEvent`, `Qt.Tool` (not `SubWindow`), alpha-aware drag, click pins the card and plays a greeting (`INTERACTION_GREETING_STATE = ready-pleased`), position persisted per screen and revalidated on monitor change. `set_flag_override()` is the only way to change flags. Click-through is `WS_EX_TRANSPARENT`, enabled only while a tray icon exists.
- **Card and tray.** `StatsCard` shows CPU/RAM always and GPU/VRAM/temp/battery/disk when available, with per-state copy from `config.STATE_PRESENTATION`, hover/pin, fades, edge-flip anchoring and a hand-painted shadow (use `card_rect()` for the visible card). Tray: Click-through, Show pet, Show stats card, Always on top, **Poke the pets** (one entry per state), Reset position, Quit; double-click restores the pet and disables click-through.
- **Settings.** `%APPDATA%\LC-pet-stat\settings.json` (`$LC_PET_STAT_CONFIG_DIR` overrides). Atomic write with read-back, tolerant parse, never deletes a bad file; saved on drag-finish and quit.
- **Not implemented:** automatic trigger for `needs-input-sad` (tray only); CPU temperature on Windows (GPU temp via NVML only; non-NVIDIA unsupported); device-pixel snapping (1.5× DPR stretches the art); global hotkey; real jump arc (`draw_offset`); macOS/Linux; any Phase 2 chat/LLM/MCP code.

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

The thresholds above are now implemented in `src/config.py` and `state_machine.py` (see *Runtime as implemented*). The shared triggers (rows 1/7, 3/4) and the selection of row 9 are resolved there. Only the CPU and RAM *high* gates and the 20 % idle edge follow the original 20/60/80 and 60/85 design numbers; the sleep thresholds (60 s / 900 s) were tuned from the planned 5 min.

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

## Runtime rules to preserve, and remaining work

The prototype defects in `BuildPlan.md` §4 are fixed and each has a regression test (`tests/README.md` index). Preserve:
- Qt-owned image buffers; no Pillow in the runtime path.
- Clean thread shutdown; never touch widgets from the telemetry worker; never call psutil outside `telemetry.py`.
- Non-blocking polling; thresholds and timings in `config.py` or the atlas JSON, with hysteresis and dwell.
- Assets resolved relative to the JSON's directory; geometry read from JSON.
- The platform seam and the no-keylogging input rule.

Remaining work: reconcile the 3 drifted tests; validate thresholds (especially `SWAP_HIGH = 30`) under real load; Windows CPU temperature provider (LibreHardwareMonitor); non-NVIDIA GPUs; device-pixel snapping; a real trigger for `needs-input-sad`; redraw `running-busy-play` so it loops in; restore the jump arc; run and report the 35 slow tests; then Phase 2. GPU dependency declarations do not prove GPU availability — the no-GPU path is tested by blocking the `pynvml` import.

## Validation and working conventions

- Use format libraries/builders where available and reopen outputs with their real parsers.
- JSON: explicit UTF-8 for both reading and writing, no BOM, no trailing text; verify count/timing/path/hash invariants.
- GIF: validate all frames, dimensions, duration and order; use as a reference, not an atlas source.
- Notebooks: use nbformat builders, unique cell IDs and `nbformat.validate`.
- PNG: verify RGBA, geometry, nonempty used cells, empty unused cells and round-trip pixels.
- Keep approval provenance and warnings explicit. Do not convert source approval into a claim of exact prompt compliance.
- Update this registry when a sequence lands, using current disk evidence and scoped QA.
- For documentation-only requests, update only the named documents; record discrepancies without repairing assets or runtime code.
