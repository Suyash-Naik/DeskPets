# Codex pet animation pipeline guide

Working status verified 2026-09-14. This document lives in `docs/`; paths below are relative to the repository root unless stated otherwise.

## Current deliverables

The desktop atlas exists at `assets/layla-and-chico-pet-spritesheet.png` with its matching JSON. It is **1920×2080**, a **10-column × 10-row** grid of **192×208** cells, containing **82 used frames**. The original eight-column, nine-row sketch in `BuildPlan.md` is historical; consume the current JSON geometry, row names, counts and timing.

Row 9 is named **`working-together`**, not `running-typing`. Row 1, `running-playful-roll`, now contains the updated pink-purple yarn-ball animation. All other atlas rows were preserved pixel-for-pixel during that row replacement. See [AGENTS.md](AGENTS.md) for the full current row registry.

Desktop and Codex app packages are separate:
- The assets-root pair is the current desktop deliverable.
- `assets/codex/delivery-001/codex-app/` contains two previously generated Codex **v1** packages, with their own fixed nine-row format and `pet.json`. These were staged, not installed, and later desktop updates did not rebuild them.
- Earlier desktop outputs under `assets/codex/delivery-001/` and `run-001/`, `run-002/` are historical, not the current atlas.

## Runtime and local tools

Use the project Pixi environment for Python, Pillow, NumPy and QA. Never use global Python or pip. The default environment contains the art tools; `pixi.toml` also declares a `pets` environment and task for the planned desktop runtime. A `src/` directory is not currently present.

From the repository root:

```powershell
pixi run check Layla-ball-rolling
pixi run check --json
pixi run qa
```

The local adaptation is `assets/codex/.agents/skills/hatch-pet-local/SKILL.md`. It uses the verified Pixi environment instead of requiring the unavailable `load_workspace_dependencies` tool. The original installed skill was not modified.

Helpers and run artifacts are under `assets/codex/`. Scripts there record specific workflow stages; inspect their input paths, output assumptions and overwrite behavior before reuse. Some still reference the former typing directory or the earlier nine-sequence inventory. They are not all generic rebuild commands for today's tree.

## Package layout

`assets/gen-image/<Sequence>/` contains:
- `00-inputs/`: original references; preserve filenames and bytes.
- `01-generated-sequence/`: generated green strips, cleaned/despilled images, reports.
- `02-extracted-frames/`: canonical tight crops used by the checker.
- `03-qa-reference/`: registered `*-aligned.png` frames, review sheets and previews.
- `04-integration-notes/`: `sequence-spec.json`, README and integration preview.

The requested `02-exported-frames/` path was used for yarn crop review during this conversation. The current canonical yarn crops are in `02-extracted-frames/`. Do not infer current production paths from an earlier export location.

Keep revisions additive. When the user authorizes replacing current derived files or updating an atlas, preserve exact copies first in a named backup. Never delete original art, inputs, earlier revisions or installed pets as cleanup.

## Generation and identity

Use the built-in image-generation tool for new raster artwork, with inspected identity references attached. Re-extract an approved replacement strip when the user asks for crops; do not needlessly regenerate its art.

Layla is the black, slim cat with pointed upright triangular ears, pale paw socks/chest markings and amber-gold eyes. Chico is lighter charcoal grey, stockier, with folded rounded ears, full cheeks and amber-gold eyes. In paired wave/working poses, Layla stays viewer-left and Chico viewer-right.

Request exactly eight equal, separated horizontal slots on pure `#00FF00`, crisp sparse pixel art, consistent scale and generous empty margins. Preserve an existing approved sequence's actual frame count; Chico-jump has ten frames. Specify every beat and loop closure. No labels, frame numbers, text or visible grid.

Prop allowances are sequence-specific:
- Working together: one small grey laptop, touched by both cats; the pair and machine should form a connected shape. Small attached contact shadows were explicitly requested. All poses stay seated and grounded.
- Yarn play: one pink-purple yarn ball. It may float separately; Layla remains the largest connected component. Preserve ball position relative to the cat. The laptop paragraph in the yarn request was carried over from the typing prompt and was not applied.

Do not generalize these exceptions to new props or scenery. Palette, exact extents and choreography constraints are visual requirements; a generated draft or passing structural check alone does not certify them.

## Cleanup and extraction

1. Inspect current source filenames and pixels; user replacement may change the filename stem.
2. Preserve the green source. Create a separate transparent RGBA derivative using Euclidean distance from the key, recording thresholds.
3. Apply deterministic edge despill with the actual key; save the processed image and report. Preserve alpha and clear hidden RGB under transparent pixels. A report's `ok` refers to its processing scope, not all visual requirements.
4. Start with proportional slot boundaries. Inspect for clipped bodies or neighboring fragments. If the actual generated poses cross nominal slots, use measured empty gaps or component bounds instead; record the chosen boundaries.
5. Extract each complete cat and its associated prop with modest transparent padding. Preserve relative positions and source order. Do not recenter the ball independently.

The updated yarn source is 2172 pixels wide. Its corrected boundaries were `[0, 291, 559, 820, 1105, 1353, 1626, 1879, 2172]`. These are specific to that source, not a universal grid. Equal-width slicing clipped several poses. The earlier strolling strip had a similar extraction problem; its last tail-only exit pose is intentional.

## Registration and motion

Use 320×320 canvases and **one shared scale across a sequence**. Anchor x=160 on the midpoint of the largest 4-connected component's horizontal bounds. Do not anchor on a detached ball, glyph, or whole-image bbox.

For grounded sequences, use a common baseline near y=292 (inclusive last pixel y=291). For an airborne/rolling sequence, preserve meaningful vertical coordinates from the original strip or documented offsets. Do not pin every frame's bottom to the baseline and flatten its motion. Keep the ball and effects in their original relationship to the cat.

The latest yarn integration applies one shared coordinate transform and retains source vertical offsets. Its checker warnings about varying bottoms and content heights are recorded; the script does not independently rescale poses. A changing silhouette height is not proof of a changing scale.

The desktop packing transform is uniform nearest-neighbor 320×320 → 192×192, padded at (0,8) into 192×208. This avoids stretching and preserves registration, though non-integer sampling cannot preserve every source pixel. Paste RGBA without using the sprite as its own alpha mask.

## Approval and QA

Generate registered review frames immediately after extraction. Run the project gate before final review artifacts, then inspect the actual images. Missing specs may be warnings during initial preparation; rerun the gate after writing the completed spec.

Respect approval already given in the conversation. A request to proceed to QA authorizes the necessary cleanup, alignment and review outputs. A request to regenerate remaining steps and update the atlas authorizes that integration, with backups; do not ask for the same approval again. If approval is still absent, show the aligned review before final integration.

The checker is read-only:
- Exit 0: clean or warnings only.
- Exit 1: at least one BLOCK.
- Exit 2: invalid usage/setup.
- `PASS WITH WARNINGS` is not a warning-free pass.

It checks registration, naming, counts, alpha, edge contact, residual green, near-duplicates, and selected spec consistency. Its baseline heuristic assumes grounded frames; its scale heuristic compares visible heights. Review intentional airborne motion rather than suppress warnings or distort art to satisfy those heuristics. Do not weaken checks to claim success.

The checker does not validate every contract: for example, the current working spec's total duration mismatch and the desktop atlas's stale provenance paths are not caught by a per-sequence PASS.

## Current sequence-specific results

### Working together

Current folder: `assets/gen-image/Layla-Chico-Working/`. It was generated and integrated as `Layla-Chico-Typing`; image filenames still use `layla-chico-typing`.

Eight registered frames pass the project checker. The reviewed source has accepted/reported visual deviations: more than five colours, imperfect requested microgestures, 276–280 px silhouette width, and a 101-pixel detached tail fragment in frame 5. All cat anchors are x=160 and grounded baselines y=291. Do not label this strict original visual-contract compliance.

The integrated loop is 1300 ms: `180,140,140,140,160,180,160,200`. The current renamed source spec declares `duration_ms: 1800` despite those timings summing to 1300; record this as a current inconsistency, not a newly approved timing. The assets-root atlas still stores `Layla-Chico-Typing` source/provenance paths. Its pixels exist, but those paths do not match the renamed directory.

### Yarn-ball rolling

Current source stem: `layla-ball-yarn-airborne`. Corrected canonical crops, aligned frames, labeled sheet, GIFs, README and spec are present. The desktop `running-playful-roll` row is updated.

Timing: `180,140,140,150,180,180,150,220` ms (1340 ms total). All eight frame/prop relationships are preserved.

The requested command `pixi run check Layla-ball-rolling` completed with **exit 0, 0 blockers, 8 warnings**: seven baseline warnings and one content-height/scale warning. It is a pass with warnings, not zero findings. Current full-sequence QA also exits 0.

## Final artifacts and integration

After authorization and gate review:
- Build transparent and labeled contact sheets from aligned frames.
- Create looping GIF previews with a consistent palette and verified frame count, order, dimensions and durations. APNG can retain exact RGBA pixels. GIFs are review references, not production frame sources.
- Write plain UTF-8 JSON, with no BOM or trailing literal escapes. Include frame paths, order, timing, phases, loop behavior, scale and registration. Do not assert grounded/airborne states contrary to the actual frames.
- Always include `04-integration-notes/README.md`; distinguish observed gestures from intended prompt beats.
- Reopen every serialized artifact and validate it using the appropriate library.
- For atlas replacement, check manifest/image agreement, source paths, used/unused cells, frame rectangles, pixel equality of unchanged rows and source hashes. Update the PNG and JSON together after validation.
- Root atlas `source_path_base` is `.`; revision copies need their own relative base. Moving a package requires auditing provenance separately from pixel geometry.

Backups from this conversation:
- `assets/codex/desktop-atlas-001/previous/`: before working-together was added.
- `assets/codex/yarn-integration-001/previous/`: prior yarn QA/integration files and desktop atlas.
- `assets/codex/yarn-integration-001/`: latest yarn integration validation and atlas snapshot.
- `assets/codex/run-002/derived/Layla-strolling/`: corrected strolling extraction used in the desktop atlas.

Final handoff must state the exact updated files, QA exit code, remaining warnings, backup location and whether an atlas or installed pet changed. Do not claim live application testing from file-format validation.
