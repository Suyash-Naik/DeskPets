# Layla & Chico — waving sequence, step-2 generation prompt

For Codex's image-generation tool. Conforms to `codex-animation-pipeline-guide.md` step 2.

**Canonical references** (in `00-inputs/`, read all three before generating):

| File | Use it for |
| --- | --- |
| `exec-ee47f232-47ed-4e03-a048-55d695b3ec1e.png` | **Pose, style, palette, seating, wave arc.** A 4-frame strip of this exact pair. The generated strip must look like it came from the same sheet. |
| `PXL_20260525_160356946.jpg` | **Layla's identity** — photo. Black, pointed upright ears, amber eyes. |
| `exec-4160199c-f1ef-42ea-a618-32042da8de50.png` | **Chico's identity** — pixel portrait. Charcoal Scottish Fold, folded ears, amber eyes. |

---

## Prompt

> Pixel-art animation strip of two seated cats, in the exact style, palette and proportions of the supplied 4-frame reference strip.
>
> **The two cats, left to right in every frame:**
>
> **Layla** sits on the **left**. A near-black short-haired cat with **pointed, upright
> triangular ears**. Large amber-gold eyes (`#edb22b`), small dark nose, fine white
> whiskers, white toe tufts on her front paws, and a long tail curling around her left
> side. Body reads as flat black (`#000000`) with sparse cool dark-grey shading
> (`#404050`, `#505050`) and a lighter grey highlight (`#909090`) on the flank and tail.
>
> **Chico** sits on the **right**, shoulder to shoulder with her, slightly overlapping.
> A charcoal-grey **Scottish Fold** — his ears are **small, rounded and folded flat
> against his skull, never pointed**. This is his single most important identifying
> trait. Same amber-gold eyes (`#edb22b`), lighter grey muzzle and chest, white
> whiskers, white toe tufts, thick tail. He is visibly **lighter** than Layla: more
> mid-grey (`#504050`, `#505050`) over the same black base.
>
> **Both cats are cheerful and playful throughout.** Happy upturned mouths, softly
> squinted or wide bright eyes, ears perked and attentive, relaxed friendly posture.
> This is a warm, welcoming greeting — not the neutral expression in the reference art.
> Keep the cheer in the eyes and mouth; do not add blush marks, sparkles, hearts, or
> any floating effect.
>
> **One single horizontal strip, exactly eight equally sized slots, left to right, with
> generous empty green gaps between slots** so no frame touches its neighbour. Both cats
> stay seated in the same screen positions in all eight frames, at one shared scale, on
> one shared ground baseline. Only the waving paw, head tilt and expression change
> between frames — bodies, tails and seating do not drift, resize, or swap sides.
>
> **Frame beats, left to right:**
>
> 1. Both seated at rest, facing forward, cheerful closed smiles, front paws down.
> 2. Layla lifts her outer (screen-left) front paw to chest height, beginning a wave.
>    Chico stays at rest, watching her, smiling.
> 3. Layla's paw is raised high beside her head, pads facing forward, her head tilted
>    happily toward Chico, eyes in a warm squint. Chico still at rest, smiling.
> 4. Layla's paw drops back toward her chest as Chico lifts his outer (screen-right)
>    front paw to chest height, starting his own wave. Both look pleased.
> 5. Chico's paw is raised high beside his head, pads facing forward, his head tilted
>    happily toward Layla. Layla is back at rest, looking up at him, smiling brightly.
> 6. Chico's raised paw tips outward in the second beat of his wave, a friendly waggle.
>    Layla at rest, cheerful.
> 7. Chico lowers his paw toward his chest. Both settling, still smiling.
> 8. Both seated at rest again, identical to frame 1, so the loop closes cleanly.
>
> **Background:** a single uniform pure green `#00FF00` fill behind everything, edge to
> edge, for chroma extraction. Nothing else in the background.
>
> **Do not include:** frame numbers, labels, text, captions, borders, grid lines, panel
> dividers, scenery, props, floors, drop shadows, glow, motion lines, gradients, or any
> object other than the two cats. No cropped or clipped silhouettes — every frame shows
> both cats complete, well inside its slot.
>
> Target aspect roughly 8:1 — around 4344 × 724 px, matching the reference's 543 px per
> frame at 724 px tall.

---

## Checks before moving to step 3

- Eight slots, equal width, clean green gaps, no pose bleeding between slots.
- Chico's ears are **folded** in all eight frames. Pointed ears on him is the most
  likely failure and means regenerate.
- Layla is the darker cat and stays on the left in all eight.
- Frame 8 matches frame 1 closely enough to loop.
- Expressions read cheerful, not neutral — the references will pull toward neutral.
- Background is a flat `#00FF00`, not a gradient or a near-green.

Then: chroma cleanup + despill (step 2), extract eight crops to `02-extracted-frames/`
(step 3), normalize and align to `03-qa-reference/*-aligned.png` (step 4), and run
`pixi run check Layla-Chico-Wave` before generating any QA sheet, GIF or spec.
