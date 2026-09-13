# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

**LC-pet-stat** — a lightweight, frameless, always-on-top desktop companion in Python.
Two cat pets, **Layla** and **Chico**, sit on the desktop and react to live system load
(CPU / RAM / GPU) through state-driven sprite animations. Hovering them shows a
semi-transparent stats card.

Authoritative spec: `BuildPlan.md`. Read it before any implementation work.

## Current state — Phase 0, animation production

**There is no source code yet.** The repo contains only `BuildPlan.md` and `assets/`.
`src/`, `requirements.txt`, and `README.md` described in the plan do not exist.

Active work is **art**: building the per-state animation sequences one at a time. Six folders
exist and four have extracted frames, against the nine rows the state machine needs — see the
sequence registry below, and run `tools/frame_qa.ipynb` for the live state.

`assets/layla-and-chico-pet-spritesheet.png` and `.json` are **incoming, not yet in the
repo**. Every one of them is what `sprite_loader.py` consumes, so nothing downstream can be
run end-to-end until they land. Treat their absence as expected, not as a bug to work
around — do not invent a placeholder atlas or stub the loader against one.

Useful work that does *not* block on the spritesheet: `src/` scaffolding,
`config.py` thresholds, `telemetry.py` (fully testable on its own), and the tray/window
shell. Everything frame-related waits.

## Tech stack

| Concern | Choice |
| --- | --- |
| Language | Python 3.14 (pinned in `pixi.toml`; plan said 3.10+) |
| GUI | PyQt6 6.8.1 (frameless, `WA_TranslucentBackground`, always-on-top, click-through) |
| Metrics | `psutil` (CPU/RAM/disk), `gputil` / `pynvml` (NVIDIA GPU) — GPU libs not installed |
| Images | Pillow — slices spritesheets into per-frame `QPixmap`s |
| Frame QA | `tools/frame_qa.py` (checks) + `frame_qa.ipynb` (napari review) |
| Env | pixi + `pixi.toml`, `win-64` only |

`BuildPlan.md` lists PyObjC for macOS hooks. **Out of scope** — the app targets Windows.

## Environment

**Target platform is Windows.** macOS/Linux are not supported and not tested; wil be added upon later development.

**Environment manager is [pixi](https://pixi.sh), configured in `pixi.toml`** — not venv,
not bare pip, not conda, and *not* `pyproject.toml`. `requirements.txt` in the plan's tree
is superseded; if it ever exists it is only a generated export.

Live config (`pixi.toml`, workspace `LC-pet-stat`, `win-64`, channel `conda-forge`):

| Dependency | Pin | Note |
| --- | --- | --- |
| `python` | `>=3.14.7,<3.15` | resolves the version question — pinned high |
| `pyqt6` | `>=6.8.1,<7` | Qt6, matching `BuildPlan.md`'s API spellings |
| `napari` | `>=0.9.1,<0.10` | frame QA only; binds to PyQt6 via `qtpy` |
| `jupyterlab` | `>=4.6.3,<5` | runs `tools/frame_qa.ipynb` |
| `ipywidgets` | `>=8.1.9,<9` | sequence picker in the QA notebook |

Also available transitively: `numpy`, `pillow`, `psutil`, `imageio`, `ipykernel`,
`nbformat`. **Not** present: `matplotlib`, `pynvml`/`gputil`.

`pixi.lock` is committed — it is what makes the environment reproducible. `.gitignore` and
`.gitattributes` are in place (`.pixi/` ignored, `pixi.lock` marked binary/generated), but
**the repo is not `git init`-ed yet** — there is no history to commit against.

Commands (pixi **0.72.0**):

```powershell
pixi install              # materialize the env from pixi.lock into .pixi/
pixi run check            # frame QA on every sequence (exit 1 = blocked)
pixi run qa               # frame QA notebook in JupyterLab
pixi run python -m src.main
pixi add <pkg>            # conda-forge dependency
pixi add --pypi <pkg>     # PyPI dependency
pixi shell                # interactive shell in the project env
```

`[tasks]` defines `check` and `qa`. There is no `start` task yet — nothing to launch.

**Do not run project code through bare `python`.** `python` on PATH is a pixi *global*
env (`C:\Users\Suyash\.pixi\envs\python\python.exe`, currently 3.14.7) and is unrelated to
this project. `pip` on PATH is more misleading still — it targets a separate CPython 3.10
install at `C:\Users\Suyash\AppData\Local\Programs\Python\Python310\`. So `pip install X`
followed by `python -c "import X"` fails, because they are two different interpreters and
neither is the project env. Always go through `pixi run`.

## Target structure (from BuildPlan.md — not yet created)

```
desktop-pet/
├── assets/
│   ├── layla-and-chico-pet-spritesheet.png
│   └── layla-and-chico-pet-spritesheet.json
├── src/
│   ├── __init__.py
│   ├── main.py                # App entry point & SystemTray setup
│   ├── ui/
│   │   ├── pet_window.py      # Frameless overlay window & drag logic
│   │   └── stats_card.py      # Telemetry hover tooltip card
│   ├── core/
│   │   ├── sprite_loader.py   # Slices JSON/PNG into QPixmap frames
│   │   ├── animator.py        # QTimer-driven frame player & state switcher
│   │   └── telemetry.py       # Background thread monitoring CPU/RAM/GPU
│   └── config.py              # Threshold settings and update intervals
├── requirements.txt
└── README.md
```

Deviations from that tree as written:

- The repo root is `LC-pet-stat/`, not `desktop-pet/`.
- `requirements.txt` is replaced by `pyproject.toml` + `pixi.lock` (see **Environment**).
- The real `assets/` also holds the whole art pipeline below the two spritesheet files.

## Animation state machine

Nine states, one spritesheet row each. The names are the contract between the loader, the
animator, and the telemetry mapper — use them verbatim.

| Row | State | Frames | Trigger |
| --- | --- | --- | --- |
| 0 | `idle-attentive` | 6 | CPU < 20% and RAM < 60% |
| 1 | `running-playful-roll` | 8 | CPU 20–60% |
| 2 | `running-playful-jump` | 8 | CPU > 80% or RAM > 85% |
| 3 | `waiting-deep-sleep` | 4 | user idle > 5 min |
| 4 | `waiting-dozy` | 5 | user idle > 5 min |
| 5 | `blocked-grumpy` | 8 | CPU/GPU temp > 80 °C, or high swap |
| 6 | `needs-input-sad` | 6 | input needed / prompt active |
| 7 | `running-busy-play` | 6 | CPU 20–60% |
| 8 | `ready-pleased` | 6 | CPU spike drops back to idle |

Telemetry runs on a background `QThread` at a 1–2 s interval and emits into the Qt main
thread via signal. **Never call psutil or touch widgets across threads directly.**

## Asset pipeline

`assets/` is the one part of the project with established conventions. Follow them.

```
assets/
  gen-image/                     # generated character art (~1144x1375 sources)
  gen-image/<Sequence>/          # one folder per animation sequence
    00-inputs/                   # source reference art
    01-generated-sequence/       # the wide generated strip
    02-extracted-frames/         # per-frame PNGs
    03-qa-reference/             # labeled contact sheet + timing GIF
    04-integration-notes/        # README.md + sequence-spec.json
    revision-NN/                 # a re-run; same numbered subfolders
  gen-gifs/                      # legacy 192x208 GIFs (running-right, waiting, waving)
  red-image/                     # empty
```

Rules the existing work follows, and new work must too:

- **Numbered stage folders.** Never flatten them; each stage is separately reviewable.
- **Revisions are additive.** A re-run goes in a new `revision-NN/` (then `revision-NN-final/`
  once approved). Earlier revisions are kept, not overwritten. `Chico-jump` currently holds
  the original, `revision-02`, `revision-03`, and `revision-03-final`.
- **Two frame variants per frame.** `frame-NN.png` is the tight crop (varying size, e.g.
  196×229); `frame-NN-normalized.png` is registered onto a shared **320×320** canvas at one
  shared scale. *Only the normalized frames are integration-ready.* Grounded frames share a
  bottom baseline; airborne frames rise within the canvas without rescaling.
- **`sequence-spec.json` is the source of truth** for a sequence: `frame_count`, per-frame
  `timing_ms`, phase names, canvas size, `grounded` flags. Read it rather than inferring
  from filenames.
- **Non-destructive.** A new sequence never modifies existing atlases or installed pets;
  integration is an explicit, separate step.
- QA GIF previews are timing references, **not** production assets.

Canonical example to imitate: `assets/gen-image/Chico-jump/`
(10 frames, 320×320, crouch → wiggle ×3 → takeoff → airborne → descent → land).

### Frame QA gate

Process spec: `codex-animation-pipeline-guide.md`. Short version and the check's own docs:
`assets/README.md`.

**Normalization happens in step 4, and its output lands in `03-qa-reference/` as
`*-aligned.png` — not in `02-extracted-frames/`.** Run the gate after that, before any
step-6/7 artefact (contact sheet, GIF, spec, README) is generated.

| | |
| --- | --- |
| `pixi run check` | every sequence, text report, **exit 1 if anything is blocked** |
| `pixi run check <Sequence>` | just that one |
| `... --json` / `-v` | machine-readable / crop sizes, per-frame lift and centres |
| `pixi run qa` | `tools/frame_qa.ipynb` — pick one sequence, then scrub it in napari |

The notebook renders a sequence **whether or not it has been aligned**. Unregistered crops
are padded onto a common canvas so they can still be viewed — the cell says so loudly and
suppresses the baseline overlay, because padding is not registration. A blocked verdict
never stops you looking at the art.

Both are read-only; neither writes into `assets/`. Exit codes: 0 clean or warnings only,
1 at least one BLOCK, 2 bad usage — so `pixi run check` can gate a generation script.

**Registration contract** (guide step 5, verified honoured on disk): canvas 320×320, body
centre `x=160` measured on the **largest connected component** so detached effects (`?`,
`Z`, tears) cannot drag it, baseline `y≈292` (291 in practice) taken from the bbox bottom
with the shadow included, one shared scale.

What it checks: frame-number continuity · foreign files · crops present but never aligned ·
empty frames · canvas size · background actually cut out · residual chroma green · content
touching the canvas edge · body centre and baseline against the anchor · near-identical
consecutive frames · scale consistency · and a cross-check that `sequence-spec.json` parses,
counts match, every `frames[].file` exists and belongs to this sequence, and frames the spec
calls airborne actually leave the baseline.

Severity: a **missing** `sequence-spec.json` is a WARN (step 7 writes it, after this gate);
a spec that is present but broken or contradicts the frames on disk is a BLOCK.

**Two package layouts**, both auto-detected and named in the report: `aligned-03` (current)
and `normalized-02` (older sequences, registered frames beside the crops in step 02).
Registered frames are found by 320×320 size, never by suffix.

Tunables are module constants at the top of `frame_qa.py`. The notebook `reload()`s the
module, so edits land without restarting the kernel.

### Sequence registry

Generated from disk by `pixi run check` — that command is the live version of this table.
Seven packages; six aligned.

| Sequence folder | Target row | Layout | QA verdict |
| --- | --- | --- | --- |
| `Chico-alert/` | 6 · `needs-input-sad` | aligned-03 | **pass** |
| `Chico-layla-nap/` | 3/4 · `waiting-*` | aligned-03 | **pass** |
| `Layla-chico-mad/` | 5 · `blocked-grumpy` | normalized-02 | **pass** |
| `Layla-strolling/` | 1/7 · `running-*` | aligned-03 | warn — edge contact, frames 6–8 |
| `Layla-sleeping/` | 3/4 · `waiting-*` | normalized-02 | warn — anchor-x drift, scale spread |
| `Chico-jump/` | 2 · `running-playful-jump` | normalized-02 | warn — anchor-x, airborne frames don't lift |
| `Chico-sad/` | 6 · `needs-input-sad` | none | mid-run when last checked |
| — | rows 0, 8 | — | not started |

`Chico-jump` has been flattened — `revision-NN/` subfolders are gone and the
`revision-03-final` content now sits at the sequence root.

**Filename suffixes differ per sequence** — `-aligned`, `-normalized`, `-raw`, `-unnormal`,
or none at all. Identify registered frames **by 320×320 size, never by suffix**.
`frame_qa.py` does this, and picks the owning stem by matching the folder name rather than
by file count, because a package can hold more foreign files than its own.

## Open questions / known gaps

Flag these rather than silently guessing:

1. **Who packs the spritesheet?** The atlas is incoming, but nothing in the repo turns
   per-sequence 320×320 frames into the 8×9 / 192×208 / 1536×1872 atlas the loader expects.
   If it is produced by hand outside the repo, say so and the packer question closes; if it
   should be scripted, that tool is unwritten.
2. **Frame size conflict.** Every sequence frame is **320×320**; the atlas spec says
   **192×208** (as do the legacy GIFs). Whoever builds the atlas has to rescale, and 320 → 192
   is a non-integer 0.6× — it will soften pixel edges unless the art is redrawn to fit.
3. **Frame count conflict.** `chico-jump` final is **10 frames**; row 2
   (`running-playful-jump`) is specced as **8**. `Layla-chico-mad` is 8 and row 5 wants 8, so
   that one lines up. Retime or drop frames per the sequence README's guidance — don't
   truncate blindly.
4. **Temperature triggers do not work on Windows.** `psutil.sensors_temperatures()` is
   Linux/BSD only — the attribute does not exist on Windows. Since Windows is now the only
   target, row 5 `blocked-grumpy` needs a different source (`pynvml` gives GPU temp;
   LibreHardwareMonitor for CPU) or a different trigger entirely — high swap alone is the
   cheap fallback.
5. **Rows 1 and 7 share a trigger, and so do rows 3 and 4.** Needs a tiebreak — alternate,
   randomize, or split the CPU band.
6. **`Chico-jump`'s airborne frames don't leave the ground.** The spec labels frames 6–9
   takeoff / airborne-jump / airborne-peak / descent, but every frame's body sits within
   3px of the same baseline. Either the art never shows the arc, or normalization
   re-registered the airborne frames down onto it — which is exactly what the sequence
   README says must *not* happen. Confirm visually in napari before this row ships.
7. **`Chico-alert` is contaminated.** 16 files from `chico-jump` and a stray
   `chico-alert-unnormal-frame-01.png` sit in its `02-extracted-frames/`. Same pattern as
   the one already cleared out of `Layla-chico-mad` — the generation step appears to copy
   the previous sequence's folder.
8. `BuildPlan.md`'s markdown is mangled — the state table is flattened into a prose run-on
   and it carries stray LaTeX (`$192 \times 208$`). The table above is the cleaned reading.

## Prototype code caveats

`BuildPlan.md` §4 is a sketch, not a reference implementation. Do not copy it verbatim;
these are real defects to fix when lifting it into `src/`:

- `QImage(crop.tobytes(), w, h, Format_RGBA8888)` omits `bytesPerLine` and keeps no
  reference to the buffer — the bytes are freed while the QImage still points at them. Keep
  the `bytes` object alive and pass the stride, or use `PIL.ImageQt`.
- `TelemetryThread.run` is `while True` with no stop flag; the thread never exits cleanly.
- `psutil.cpu_percent(interval=1)` blocks, so the poll rate is pinned at 1 s regardless of
  any configured interval.
- Thresholds in the code (30 / 75) contradict the plan's table (20 / 60 / 80 / 85), and
  there is **no hysteresis** — the pet will flap between states at a boundary. Add a dwell
  time or Schmitt-trigger band, and keep the numbers in `config.py`.
- Asset paths are bare filenames; they must resolve relative to `assets/`.
- No system tray, no stats card, no click-through toggle yet — all three are required.

## Generated files — validate before handing them over

A lesson from this repo, learned the expensive way. It applies to anything written in a
format that has a spec.

**If the format has a validator or a builder library, use it. Do not hand-author the
serialized form and eyeball it.** Content that parses is not content that conforms —
structurally valid JSON can still violate the schema wrapped around it, and the failure is
silent until some other tool chokes on it.

The case that prompted this: `tools/frame_qa.ipynb` was first written as raw JSON via a
plain file write. It parsed, it ran, it looked finished. But it declared
`nbformat_minor: 5`, and nbformat ≥ 4.5 **requires a unique `id` on every cell** — which
hand-written JSON had no reason to include. Two consequences, neither visible on inspection:

- Jupyter would have silently assigned random ids on first save, churning the file on every
  open.
- Cell-addressed editing (`NotebookEdit`) targets cells *by id*, so every later edit would
  have had to rewrite the entire notebook.

One `nbformat.validate()` call would have caught it at generation time. The fix — stable,
meaningful ids per cell — cost minutes after the fact; it would have cost nothing up front.

So, concretely:

- **Notebooks**: build with `nbformat.v4.new_notebook()` / `new_code_cell()` rather than
  literal JSON, and always finish with `nbformat.validate()`. Both are in the pixi env.
- **`sequence-spec.json`**: plain UTF-8, no BOM, no trailing text. The two malformed specs
  in `assets/` are this same class of bug — written without a read-back — and they are why
  `frame_qa.ipynb` needs a tolerant loader at all.
- **Anything else with a schema**: round-trip it. Write, re-read with the real parser,
  assert it matches.

And the general form: **run the artifact before calling it done.** Every substantive defect
found in this repo's tooling surfaced from executing it against real data, never from
reading it back.

## Working conventions

- Run everything through `pixi run`. Never `pip install` into the global interpreters.
- Run `pixi run check` after extracting frames, before generating a QA sheet or GIF.
- Windows-only. No macOS/Linux branches unless asked.
- Keep thresholds, intervals, and frame timings in `config.py` / `sequence-spec.json`,
  never inline in widget code.
- GUI work stays non-blocking; anything that polls or does I/O belongs on a worker thread.
- Don't regenerate or overwrite art. Add a new `revision-NN/` folder instead.
- When a sequence lands, update the **Sequence registry** table above.
- Validate generated files with the format's own tooling before handing them over.
