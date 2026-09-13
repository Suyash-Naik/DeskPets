# Codex pet animation pipeline guide

This guide records the agreed workflow for Layla and Chico animation sequences. It is a working checklist for future runs and is intentionally stored as a new file at the project root. Existing project files are not edited by this guide.

## Current project conventions

Animation packages live under:

`D:\LC-pet-stat\assets\gen-image\<sequence-name>`

Each package uses these stages:

- `00-inputs`: user-provided reference art. Never overwrite or delete it.
- `01-generated-sequence`: generated source strips, green-screen source, cleaned source, despilled source, and generation reports.
- `02-extracted-frames`: frame cutouts and the step-02 preview sheet.
- `03-qa-reference`: normalized/aligned frames, labeled contact sheet, QA sheet, and QA GIF.
- `04-integration-notes`: integration GIF, `sequence-spec.json`, and `README.md`.

## Current work already produced

### Layla strolling

Input: `assets\gen-image\Layla-strolling\00-inputs\laylaRecline-frame.png`

Generated and extracted:

- `01-generated-sequence\layla-strolling-sequence-green-source.png`
- `01-generated-sequence\layla-strolling-sequence-clean.png`
- `01-generated-sequence\layla-strolling-sequence-clean-despilled.png`
- `02-extracted-frames\layla-strolling-frame-01.png` through `-08.png`
- `02-extracted-frames\layla-strolling-extracted-preview.png`

After approval, normalization, alignment, QA, GIF, spec, and README were completed.

### Chico and Layla nap

Input: `assets\gen-image\Chico-layla-nap\00-inputs\layla-and-chico-waiting-sleep-source.png`

Generated and extracted for current verification:

- `01-generated-sequence\chico-layla-nap-sequence-green-source.png`
- `01-generated-sequence\chico-layla-nap-sequence-clean.png`
- `01-generated-sequence\chico-layla-nap-sequence-clean-despilled.png`
- `01-generated-sequence\chico-layla-nap-despill-report.json`
- `02-extracted-frames\chico-layla-nap-frame-01.png` through `-08.png`
- `02-extracted-frames\chico-layla-nap-extracted-preview.png`

Status: user verification is pending. Do not generate final QA/GIF/spec yet, but do perform normalization/alignment now under the updated rule below.

### Chico sad

Input: `assets\gen-image\Chico-sad\00-input\chico-pet-generated-source.png`

Generated and extracted for current verification:

- `01-generated-sequence\chico-sad-sequence-green-source.png`
- `01-generated-sequence\chico-sad-sequence-clean.png`
- `01-generated-sequence\chico-sad-sequence-clean-despilled.png`
- `01-generated-sequence\chico-sad-despill-report.json`
- `02-extracted-frames\chico-sad-frame-01.png` through `-08.png`
- `02-extracted-frames\chico-sad-extracted-preview.png`

Status: user verification is pending. Do not generate final QA/GIF/spec yet, but do perform normalization/alignment now under the updated rule below.

## Updated approval rule

The user reviews the extracted sequence before final packaging. However, normalization and body alignment should happen immediately after extraction so the user can review the frames at their intended shared scale and registration.

The stages are therefore:

1. Generate source strip.
2. Clean chroma background and run deterministic despill.
3. Extract step-02 frame cutouts.
4. Immediately normalize and align derived review frames.
5. Show the user the normalized/aligned frame preview and wait for approval.
6. After approval, generate QA artifacts, GIF previews, integration spec, and README.

Do not generate the final GIF or claim the sequence is integration-ready before approval.

## Detailed pipeline

### 1. Inspect inputs first

- List every file in `00-inputs`.
- View each reference image before generating.
- Preserve the exact input filenames and bytes.
- Record which image defines identity, palette, pose, markings, or expression.
- Do not edit any existing file in the package or project root without explicit user approval.

### 2. Generate one coherent source strip

Use the built-in image-generation tool for raster generation.

Prompt requirements:

- State the exact cat identities and distinguishing traits.
- Use the provided input as the canonical visual reference.
- Request one horizontal strip with exactly eight separated slots.
- Request a uniform pure green `#00FF00` background for extraction.
- Require equal slots, complete silhouettes, consistent pixel-art style, scale, and baseline.
- Describe every frame beat in left-to-right order.
- Avoid labels, text, scenery, grid lines, shadows, glow, or unrelated objects.
- Effects such as `Z` marks or a question mark must be small, opaque, state-relevant, and attached/kept within the same frame slot.

Save the selected output under `01-generated-sequence` and preserve the original generated output in the Codex generated-images area.

### 3. Chroma cleanup

- Convert the green source to RGBA using Euclidean distance from pure green.
- Save a separate clean source; never replace the original green source.
- Run `despill_chroma_edges.py` with `--chroma-key '#00FF00'`.
- Save the despilled output and JSON report separately.
- The report must indicate `ok: true`.
- If QA shows green edge contamination, run the deterministic despill/correction on derived frames only and rebuild derived artifacts; preserve all source generations.

### 4. Extract step-02 frames

- Slice the source strip using proportional slot boundaries, not guessed fixed widths.
- Extract exactly eight frames in source order.
- Crop transparent margins with modest padding so no neighboring pose is included.
- Use descriptive canonical names such as `chico-sad-frame-01.png` or `chico-layla-nap-frame-01.png`.
- Create an extracted preview sheet in `02-extracted-frames` for inspection.
- Do not create final QA/GIF/spec at this stage.

### 5. Normalize and align immediately

This is now performed before the user verification step.

- Read only the canonical frame names for the review sequence.
- Use a shared `320x320` registration canvas.
- Find the largest connected visible component representing the cat body.
- Exclude detached effects such as `?`, `Z`, tears, or other marks from the alignment anchor.
- Use a shared scale derived from the maximum body bounding box; do not independently resize frames in a way that causes size popping.
- Place each frame using a shared body-center and baseline anchor, normally body center `x=160` and baseline near `y=292`.
- Preserve effect positions relative to the frame, but do not let effects determine the anchor.
- Save derived aligned files under `03-qa-reference` with explicit `-aligned.png` suffixes.
- Never overwrite the user’s step-02 source frames during normalization.

The review preview should show the normalized/aligned frames, preferably labeled with frame number and phase. If a frame is clipped, contains a neighboring pose, changes apparent scale, or has a bad anchor, stop and repair the derived output before asking for approval.

### 6. Approval gate

Ask the user to verify the normalized/aligned preview. Do not proceed to final GIF or integration artifacts until the user approves.

If the user requests changes:

- Keep all old generated sources and prior intermediates.
- Create a clearly named revision folder or versioned derived file.
- Do not silently replace a source generation.
- Re-run naming, hash, geometry, and visual checks after revisions.

### 7. Final QA and GIF generation after approval

After approval:

- Build `03-qa-reference/<name>-reference-sequence.png` from aligned frames.
- Build a labeled QA sheet with frame number and phase.
- Build `03-qa-reference/<name>-preview.gif`.
- Copy/rebuild the integration GIF at `04-integration-notes/<name>-preview.gif`.
- Verify GIF frame count, canvas size, order, and timing with Pillow.
- Inspect the labeled sheet visually.
- Confirm no green fringe, clipped silhouette, slot overlap, detached forbidden effects, or baseline/scale popping.

### 8. Integration spec and README

Always create both files in `04-integration-notes`:

- `sequence-spec.json`: sequence id, frame count, timing, source frame order, phase names, source paths, aligned paths, canvas, scale, anchor, and baseline.
- `README.md`: purpose, frame-by-frame sequence, timing, registration details, artifact paths, and preservation note.

The README is mandatory and must never be omitted.

## Naming and duplicate checks

Before generating derived outputs:

- Check all filenames in `02-extracted-frames`.
- Never let a prior sequence name such as `chico-jump` survive in a new sequence package.
- Compare candidate duplicates with SHA-256 before deletion.
- Delete only files proven byte-identical to a canonical file and explicitly within the requested cleanup scope.
- Preserve distinct raw crops by renaming them with a clear suffix such as `-raw.png` or `-alt.png`.
- After cleanup, verify that every spec path exists and that no stale names remain.

This naming check is required before QA sheets, GIFs, or specs are generated.

## Preservation and safety rules

- Never delete generated sources or user inputs.
- Never edit existing project-root files without asking first and stating the exact proposed edit.
- New guides, reports, and sequence artifacts may be created in their requested folders.
- If a requested destination already contains a file, inspect it first and avoid overwriting unless the user explicitly requests replacement.
- Keep all generation intermediates when the user has asked for preservation.
- Report the exact files created, replaced, renamed, or removed.

## Final handoff checklist

Before reporting completion, verify:

- Input reference preserved.
- Generated source strip preserved.
- Clean and despilled sources present.
- Eight canonical extracted frames present.
- Normalized/aligned preview present.
- User approval status is clear.
- QA sheet and labeled sheet present if approved.
- GIF exists and has the expected frame count/timing if approved.
- `sequence-spec.json` exists and every referenced path exists.
- `README.md` exists in step 04.
- No stale sequence names remain.
- No unrelated pet sheet or installed pet was changed.
