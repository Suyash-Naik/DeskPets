# Layla and Chico animation generation prompts

Mode: image generation with local photo and prior sprite references, followed by
deterministic nearest-neighbor palette reduction and sprite-sheet assembly.

**Shared art direction:** standalone tiny sparse Codex `/pet`-style pixel art; crisp square
pixels; strong silhouette; transparent background; at most five visible colors; no UI,
scenery, clothing, or borders. Layla is black with pointed ears. Chico is charcoal gray with
folded ears, round cheeks, and amber-gold eyes.

> **Two constraints were relaxed during production, and new work must match what shipped, not
> the original wording.** The first draft of this direction said "no text, symbols … or
> shadows". The finished sequences use **detached effect glyphs** (Chico's `?`, the sleep
> `Z`/`Zzz`, Chico's tears) and each pose sits on a **contact shadow**, which the packer's
> baseline is measured from. Regenerating a sequence under the original no-symbols,
> no-shadows prompt will produce art that does not sit consistently with the other eight rows.

---

## Shipped sheet

`layla-and-chico-pet-spritesheet.png` + `.json` — **1920×1872, 10 columns × 9 rows, 192×208
cells, 74 frames.** The grid was widened to 10 columns to preserve every finalized jump
frame. Rows shorter than 10 leave their trailing cells fully transparent.

Frames were produced on a **320×320** canvas and packed at **0.6× with a nearest filter**
into a 192×192 area offset 8 px down inside each 192×208 cell. Because that resample has
already happened, the runtime draws frames at native size with smoothing off; any new source
art must come in at 320×320 so it survives the same path.

Per-frame `x`/`y`/`width`/`height` and `duration_ms` are all explicit in the JSON. The app
reads them verbatim — it never computes a cell from a row/column index — so **a re-pack with
different counts, cell sizes, or timings needs no code change.**

---

## Sheet rows

Every row is 8 frames except the jump, which is 10. All loop except the stroll.

| Row | State name (contract) | Frames | Loop | Cycle | Source sequence |
| --- | --- | --- | --- | --- | --- |
| 0 | `idle-attentive` | 8 | yes | 1120 ms | `Chico-alert` |
| 1 | `running-playful-roll` | 8 | yes | 1340 ms | `Layla-ball-rolling` |
| 2 | `running-playful-jump` | 10 | yes | 1190 ms | `Chico-jump` |
| 3 | `waiting-deep-sleep` | 8 | yes | 1320 ms | `Chico-layla-nap` |
| 4 | `waiting-dozy` | 8 | yes | 1260 ms | `Layla-sleeping` |
| 5 | `blocked-grumpy` | 8 | yes | 1010 ms | `Layla-chico-mad` |
| 6 | `needs-input-sad` | 8 | yes | 1430 ms | `Chico-sad` |
| 7 | `running-busy-play` | 8 | **no** | 1160 ms | `Layla-strolling` |
| 8 | `ready-pleased` | 8 | yes | 1500 ms | `Layla-Chico-Wave` |

State names are the contract between the loader, the animator and the telemetry mapper. Use
them verbatim.

**Row 7 is deliberately non-looping.** Layla walks out of frame and the final pose is
tail-only. It is played once as a brief flourish and the pet then returns to its resident
state; it is never a state the pet sits in. Do not "fix" the empty-looking last frame.

---

## What each row actually shows

Corrected against the shipped frames. Phase names are the ones recorded in the JSON.

**Row 0 — `idle-attentive` (Chico alone).** Chico looks around, curious. `neutral-alert →
turn-screen-left → look-screen-left → return-center → question-mark → turn-screen-right →
look-screen-right → settle-curious`. The `?` is a detached glyph above his head on frame 4.
The closing pose holds 220 ms, which is what gives the loop its beat.

**Row 1 — `running-playful-roll` (Layla alone, with a ball).** She stalks and bats a ball,
then rolls with it: `crouch-watch → reach → bat → pounce → side-roll → back-roll → pop-up →
playful-crouch`. The ball is part of the sequence, not scenery.

**Row 2 — `running-playful-jump` (Chico alone).** A wind-up then an arc:
`crouched-ready → compressed-crouch → tail-wiggle-left → tail-wiggle-right →
tail-wiggle-center → takeoff → airborne-jump → airborne-peak → descent-paws-reaching →
landing-all-four`. The three tail-wiggle frames are the anticipation beat and are why this
row needed ten cells.

**Row 3 — `waiting-deep-sleep` (both cats).** They settle into a cuddle and fall asleep:
`close-awake → lean-together → paws-settle → bodies-touch → begin-dozing →
cheek-to-cheek-with-Z → tighter-dreamy-cuddle → deep-sleep-cuddle-with-Zs`. Sleep `Z`s are
detached glyphs on the last three frames.

**Row 4 — `waiting-dozy` (Layla alone).** Lighter sleep, with a head lift:
`sleep-neutral → sleep-with-zzs → head-lift-start → head-lift → head-lift-pause →
head-lower → nearly-asleep → sleep-neutral-with-zzs`. Reads as drowsy rather than out cold —
the app plays this at 5 minutes idle and row 3 at 15.

**Row 5 — `blocked-grumpy` (both cats).** Annoyed head tilts, not back-to-back:
`neutral-grumpy → tilt-screen-left → center → tilt-screen-right → center →
tilt-screen-left-strong → center-annoyed → settle-neutral`. Comically narrowed eyes; harmless,
never distressed. Shortest cycle on the sheet at 1010 ms, which suits the irritated read.

**Row 6 — `needs-input-sad` (Chico alone).** A full emotional arc that recovers:
`alert → ears-lower → sad-gaze → sad-pause → tears → recovering → hopeful → soft-blink`.
Tears are detached glyphs on frame 4. It ends hopeful, not miserable — the pet is asking, not
suffering.

**Row 7 — `running-busy-play` (Layla alone).** She gets up and walks off:
`seated-alert → standing → front-leg-stretch → stretch-finish → first-step → walking →
near-exit → tail-exit`. Re-extracted from its original strip to remove neighboring fragments.

**Row 8 — `ready-pleased` (both cats, waving).** They take turns waving:
`Rest/greeting → Layla lifts paw → Layla high wave → Chico lifts; Layla rests →
Chico high wave → Chico outward waggle → Both settled → Rest/loop close`. Longest cycle at
1500 ms.

---

## Rules for a new or regenerated sequence

Anything dropped into this sheet has to satisfy the pipeline, not just look right in
isolation. Full process spec is `../codex-animation-pipeline-guide.md`; the short version:

- **Numbered stage folders** under `gen-image/<Sequence>/`: `00-inputs`,
  `01-generated-sequence`, `02-extracted-frames`, `03-qa-reference`, `04-integration-notes`.
  Never flatten them.
- **Revisions are additive.** A re-run goes in a new `revision-NN/`; earlier revisions are
  kept, never overwritten.
- **Registration contract:** 320×320 canvas, body centre `x=160` measured on the **largest
  connected component** — so a detached `?`, `Z` or tear cannot drag the centre — and baseline
  `y≈292` from the bbox bottom **with the contact shadow included**, at one shared scale.
- **Airborne frames must genuinely leave the baseline.** Alignment pins the bbox bottom, so a
  jump whose arc was never drawn will be silently flattened onto the ground.
- **Run the gate:** `pixi run check <Sequence>` after extracting and aligning, before any
  contact sheet, GIF or spec is generated. Exit 1 means blocked.
- **`sequence-spec.json` is the source of truth** for frame count, per-frame `timing_ms`,
  phase names, canvas size and `grounded` flags. Plain UTF-8, no BOM, no trailing text.
- Effects and symbols are allowed (see the note at the top) but must be **detached** from the
  body silhouette so registration stays stable.
- QA GIF previews are timing references, not production assets.

---

## Adding a tenth row

The app is built for this. Adding an emotional state takes:

1. A new sequence through the pipeline above, packed into the atlas as a new row with its own
   `name`, `frames`, `loop`, `timing_ms` and `frame_data[]`.
2. One entry in `src/config.py`'s `STATE_PRESENTATION` giving the status card's title,
   subtitle, accent colour and glyph for that state.
3. A trigger — either a rule in `src/core/state_machine.py`, or nothing at all if it is meant
   to be poked manually from the tray like `needs-input-sad`.

No loader, animator or window code changes. Frame counts, cell sizes and timings are read
from the JSON, so they are free to differ from every existing row.

---

## Known art issue

**`Chico-jump`'s airborne frames do not leave the ground.** The spec labels frames 5–8
takeoff / airborne-jump / airborne-peak / descent, but there is **no vertical movement at
all**: measured against the packed atlas, all ten frames have their opaque bounding box
bottom at exactly `y=182` within the cell — a lift of **0 px** on every frame, including
`airborne-peak`. (CLAUDE.md records this as "within 3 px"; the true figure is zero.)
Alignment registered the airborne poses back down onto the baseline.

The runtime cannot compensate: it draws each frame bottom-centred within the cell, and there
is no per-frame vertical offset in schema v1. Restoring the arc needs **either** re-aligned
art that keeps the lift, **or** a `draw_offset` / `lift_px` field added per frame in a schema
v2, which the loader would apply as part of the existing frame offset. It must not be
special-cased for row 2 in code.
