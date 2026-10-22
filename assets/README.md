# Assets — animation pipeline and QA gate

Source art and animation sequences for the Layla & Chico desktop pet.

Authoritative process spec:
[`../docs/codex-animation-pipeline-guide.md`](../docs/codex-animation-pipeline-guide.md).
This README is the short operational reference. The guide carries the full history,
sequence-specific exceptions, approval rules, and integration procedure.

## Current desktop atlas

The current desktop deliverable is the assets-root pair:

- `layla-and-chico-pet-spritesheet.png`
- `layla-and-chico-pet-spritesheet.json`

It is a **1920×2080**, **10×10** atlas with **192×208** cells and **82 used frames**.
The JSON is authoritative for row names, frame counts, frame rectangles, loop behavior, and
per-frame timing. Historical plans describing an 8×9 atlas or fixed frame counts are obsolete.

| Row | State | Frames | Loop | Duration | Current source package |
| ---: | --- | ---: | :---: | ---: | --- |
| 0 | `idle-attentive` | 8 | yes | 1120 ms | `Chico-alert` |
| 1 | `running-playful-roll` | 8 | yes | 1340 ms | `Layla-ball-rolling` |
| 2 | `running-playful-jump` | 10 | yes | 1190 ms | `Chico-jump` |
| 3 | `waiting-deep-sleep` | 8 | yes | 1320 ms | `Chico-layla-nap` |
| 4 | `waiting-dozy` | 8 | yes | 1260 ms | `Layla-sleeping` |
| 5 | `blocked-grumpy` | 8 | yes | 1010 ms | `Layla-chico-mad` |
| 6 | `needs-input-sad` | 8 | yes | 1430 ms | `Chico-sad` |
| 7 | `running-busy-play` | 8 | no | 1160 ms | `Layla-strolling` |
| 8 | `ready-pleased` | 8 | yes | 1500 ms | `Layla-Chico-Wave` |
| 9 | `working-together` | 8 | yes | 1300 ms | `Layla-Chico-Working` |

Row 7 is a one-shot stroll whose tail-exit ending is intentional. Row 2 keeps all ten jump
frames. Row 9's current folder and sequence id use “Working,” but the root atlas still contains
older `Layla-Chico-Typing` provenance paths; see **Known inconsistencies** below.

## Layout

```
assets/
  layla-and-chico-pet-spritesheet.png/.json   current desktop atlas pair
  gen-image/<Sequence>/      one package per animation sequence
    00-inputs/               user reference art — never overwrite or delete
    01-generated-sequence/   green source strip, cleaned, despilled, despill report
    02-extracted-frames/     tight frame cutouts + extracted preview sheet
    03-qa-reference/         *-aligned.png (320x320), contact sheet, QA sheet, GIF
    04-integration-notes/    sequence-spec.json, README.md, integration GIF
  codex/                     deterministic helpers, run evidence, backups, deliveries
    scripts/                 stage-specific helper scripts; inspect before reuse
    desktop-atlas-001/       backup from adding working-together
    yarn-integration-001/    latest yarn integration evidence and previous atlas
    delivery-001/            earlier desktop outputs and separate Codex app packages
  gen-gifs/                  legacy 192x208 GIFs, pre-pipeline
  ref-image/                 visual references
```

A re-run goes in a new `revision-NN/` inside the package, with the same numbered
subfolders. Earlier revisions, input art, raw generation output, crops, reports, and backups
are kept rather than overwritten or deleted.

The packages under `assets/codex/delivery-001/codex-app/` are separate Codex **v1** pet
conversions. They are not the desktop atlas, were not installed by this project, and were not
refreshed by later desktop-row updates.

## The stages

| Step | Produces | Folder |
| --- | --- | --- |
| 1 | one horizontal strip on `#00FF00`, preserving the intended frame count | `01-generated-sequence/` |
| 2 | chroma cleanup + deterministic despill (`ok: true`) | `01-generated-sequence/` |
| 3 | complete tight crops using verified pose boundaries | `02-extracted-frames/` |
| 4 | **normalize and align** onto the shared canvas | `03-qa-reference/*-aligned.png` |
| 5 | **← run the QA gate here**, then review the actual motion | — |
| 6 | contact sheet, labelled QA sheet, preview GIF | `03-qa-reference/` |
| 7 | `sequence-spec.json` + `README.md` | `04-integration-notes/` |
| 8 | rerun the gate and validate metadata, links, durations, and hashes | — |

Most generated sequences have eight frames, but the approved jump has ten. An equal-slot
generation request does not prove the output has equal visual slots: the yarn and strolling
strips required boundaries measured from the empty gaps between poses because proportional
slicing clipped bodies and pulled in neighbouring fragments. Inspect each current source
rather than applying an old sequence's boundaries or filename assumptions.

Normalization happens before final integration review so the preview shows the intended scale,
registration, and motion. Approval to generate art does not by itself authorize installation
or atlas integration. When an atlas update is authorized, preserve the previous PNG/JSON pair
and validate both replacements together.

## Registration contract

Aligned frames must satisfy all of:

| Property | Value |
| --- | --- |
| Canvas | `320 x 320` |
| Body centre | `x = 160` — largest connected component, **excluding** detached effects |
| Grounded baseline | inclusive last pixel `y = 291` (approximately baseline 292) |
| Scale | one shared scale across the sequence — no size popping |

Detached effects (`?`, `Z`, tears) keep their position relative to the frame but never
determine the anchor. A bbox centre would be dragged sideways by them; the check measures
the largest four-connected component instead. Props such as the yarn ball keep their position
relative to the cat and must not be independently centered.

Grounded sequences share one baseline. Airborne and rolling sequences preserve meaningful
source vertical motion with one documented shared transform; bottom-pinning every frame can
erase the very motion the animation is meant to show. Checker baseline and content-height
warnings must therefore be reviewed in the context of the sequence rather than “fixed” by
flattening the poses.

Desktop packing uses one nearest-neighbour `0.6` scale from 320×320 to 192×192, then places the
result at `(0, 8)` inside each 192×208 cell. The sprite itself must not be reused as a paste
mask because that applies alpha twice.

## The QA gate

`tools/frame_qa.py` — run it after step 4, before generating anything in step 6 or 7.

```powershell
pixi run check                    # every sequence
pixi run check Layla-ball-rolling # one sequence
pixi run check --json             # machine-readable
pixi run check -v                 # + crop sizes, per-frame lift and centres
pixi run qa                       # notebook: pick a sequence, scrub it in napari
```

Read-only — it never writes into `assets/`.

**Exit codes:** `0` clean or warnings only · `1` at least one BLOCK · `2` bad usage.
So it gates a generation step directly:

```powershell
pixi run check Layla-strolling; if ($?) { ...generate QA sheet and GIF... }
```

### What it checks

Packaging: frame-number continuity · files left over from another sequence · crops present
but never aligned · empty frames · canvas size.

Cutout: background actually cut out · residual chroma-key green · content touching the
canvas edge (a neighbouring pose caught in the crop).

Registration: body centre against `x=160` · baseline against `y=291` · one shared scale ·
near-identical consecutive frames.

Spec: `sequence-spec.json` parses · counts match the frames on disk · every `frames[].file`
exists and belongs to this sequence · selected grounded/airborne declarations agree with the
registered frames.

The gate does not prove every visual requirement or validate the complete desktop atlas. Check
duration sums, provenance paths, manifest/image links, hashes, unused-cell transparency, and
pixel identity of untouched rows separately during integration.

### Severity

- **BLOCK** — do not generate QA artefacts. Wrong canvas, missing frames, foreign files,
  un-run alignment, or a spec that contradicts what is on disk.
- **WARN** — look before proceeding. Anchor drift, edge contact, scale spread, residual
  green, a spec not written yet (expected at this gate, since step 7 comes later).

Thresholds are module constants at the top of `tools/frame_qa.py` (`CANVAS`, `ANCHOR_X`,
`ANCHOR_BASELINE`, `ALPHA_FLOOR`, `SHADOW_LUMA`, …).

### Two package layouts

Both are recognised automatically, and the report names which one it found:

- `aligned-03` — current pipeline. Crops in step 02, aligned frames in step 03.
- `normalized-02` — older sequences, where registered frames sit in `02-extracted-frames`
  beside the crops.

Registered frames are identified by their `320x320` size **and package ownership**, never by
filename suffix alone. Suffixes differ across sequences (`-aligned`, `-normalized`, `-raw`,
`-unnormal`, or none), and old packages may contain foreign files from an earlier run.

## Current QA registry

The latest recorded full run was `pixi run check --json` on **2026-09-14** and exited `0`.
Exit `0` includes warnings; it does not mean every sequence is warning-free.

| Sequence | Layout | Recorded project QA |
| --- | --- | --- |
| `Chico-alert` | `aligned-03` | PASS |
| `Chico-jump` | `normalized-02` | warnings: limited lift, horizontal anchors, malformed trailing literal escape in its spec |
| `Chico-layla-nap` | `aligned-03` | PASS |
| `Chico-sad` | `aligned-03` | baseline warnings |
| `Layla-ball-rolling` | `aligned-03` | PASS WITH WARNINGS: 0 blockers, 8 warnings |
| `Layla-chico-mad` | `normalized-02` | PASS |
| `Layla-Chico-Wave` | `aligned-03` | PASS |
| `Layla-Chico-Working` | `aligned-03` | PASS; known visual deviations remain |
| `Layla-sleeping` | `normalized-02` | horizontal-anchor and content-height warnings |
| `Layla-strolling` | `aligned-03` | source-package edge warnings; atlas uses the corrected derived extraction |

The exact `pixi run check Layla-ball-rolling` command also exited `0` with seven baseline
warnings and one content-height warning. The shared source vertical coordinates and one scale
were retained; do not flatten the poses merely to silence those warnings.

## Integration and preservation

When a row is updated, back up the current atlas PNG and JSON before publishing the replacement pair. Verify that:

- every other row remains pixel-identical;
- the source artwork is unchanged;
- all unused atlas cells are transparent;
- provenance paths resolve from the metadata's `source_path_base`;
- frame counts, rectangles, durations, and hashes agree; and
- the PNG and JSON are published together.

The root atlas metadata uses `source_path_base: "."`. Copies stored in nested revision folders need a relative base appropriate to their new location.

Useful preserved evidence includes:

- `codex/desktop-atlas-001/previous/` — the atlas before `working-together` was added;
- `codex/yarn-integration-001/previous/` — the atlas before the yarn row was updated;
- `codex/yarn-integration-001/validation.json` — the latest yarn integration validation;
- `codex/run-002/derived/Layla-strolling/` — the corrected strolling extraction used by the desktop atlas; and
- `codex/scripts/` — deterministic extraction, registration, and packaging helpers.

## Known inconsistencies

These are recorded here so they are not mistaken for validated behavior or silently repaired during unrelated asset work:

1. The working folder is now `Layla-Chico-Working` and its sequence ID is `layla-chico-working`, but its files retain `layla-chico-typing` stems. The root atlas row still names the former `Layla-Chico-Typing` folder in its provenance.
2. The working sequence spec declares 1800 ms total, while its eight frame timings and the integrated atlas row total 1300 ms. Resolve this intentionally before changing its timing.
3. The working artwork exceeds the original five-colour target and exact extent requirements. Reviewed width varies from 276 to 280 pixels, and frame 5 includes a detached 101-pixel tail fragment. A checker PASS does not certify those original visual constraints.
4. `Chico-jump` retains all ten frames but shows only about 0–3 pixels of measured body lift. Its source spec also contains a malformed trailing literal escape.
5. Source strolling frames 6–8 still have edge warnings. The desktop atlas uses the corrected extraction in `codex/run-002/derived/Layla-strolling/`.
6. Some historical scripts contain old typing paths, fixed source inventories, or one-off completion steps. Inspect them before rerunning them against the current atlas.

## Gotchas

- Do not run the checker while image generation or frame extraction is still writing files.
- Keep `sequence-spec.json` and other JSON files as plain UTF-8 without a BOM or trailing text. Reopen generated files with their real parsers.
- A replacement source may use a different filename stem. Inspect the current folder instead of reusing an older path.
- Checker exit code 0 includes warnings. Read the report and preview the motion rather than treating it as warning-free.
- A QA PASS covers the checker's rules. It does not prove exact prompt compliance, pose meaning, valid provenance, or correct atlas timing.
- Use GIFs for motion review only; do not use them as atlas sources.
- Preserve source strips, earlier registered frames, reports, review images, and backups. Do not delete prior evidence during an update.
- Grounded sequences use a shared baseline. Airborne and rolling sequences need a shared vertical transform that preserves intentional lift or arcs; per-frame bottom pinning can erase that motion.
