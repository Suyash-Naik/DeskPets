# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

**LC-pet-stat** — a lightweight, frameless, always-on-top Windows desktop companion in Python.
Two cat pets, **Layla** and **Chico**, sit on the desktop and react to live system load
(CPU / RAM / swap / GPU / temperature / user idle time) through state-driven sprite animations.
Hovering them shows a semi-transparent stats card.

Spec history: `docs/BuildPlan.md` (original sketch, now superseded in places),
`docs/PHASE1-CONTRACT.md` (**authoritative interface contract** the modules were built against),
`docs/PHASE1-LOG.md` (what was built, what broke, what was measured), `docs/index.md`
(personal write-up), `tests/README.md` (test suite, regression index).

## Current state — Phase 1 implemented, Phase 2 not started

**Phase 1 is built and runnable.** `src/` holds ~6,800 lines; `tests/` holds ~4,800 lines.
Art production and atlas packing are finished: the atlas is in `assets/`.

Verified on 2026-10-24 (not just claimed):

| Check | Result |
| --- | --- |
| `pixi run -e pets python -m src.main --validate` | `PASS (0 block, 0 warn)` — 1920×2080, 82 frames in 10 animations |
| `pixi run -e dev test -m "not slow"` | 188 collected: **184 pass, 3 fail, 1 skip** (35 slow tests deselected, **not run**) |

The 3 failures are **config drift, not code bugs** — thresholds in `src/config.py` were tuned
after the tests were written, and the tests still assert the old numbers:

| Failing test | Asserts | `config.py` now has |
| --- | --- | --- |
| `test_config::test_thresholds_match_the_cleaned_spec_table_not_the_prototype` | `CPU_MODERATE_MIN == 20.0` | `1.0` |
| `test_state_machine::test_regression_idle_attentive_is_reachable_at_seventy_percent_ram` | `RAM_HIGH == 85.0` | `55.0` |
| `test_state_machine::test_sleep_states_split_by_depth_and_wake_instantly` | dozy at 5 min | `IDLE_DOZY_S = 60.0` (1 min) |

Note what those constants actually do: `RAM_HIGH = 55` only turns the **card's RAM meter**
red (`stats_card.py`); the real RAM state gate is `BAND_RAM_HIGH = (85, 80)`. `CPU_MODERATE_MIN`
is not read anywhere in `src/` — the moderate gate is `BAND_CPU_MODERATE = (20, 14)`. Either
update the tests or revert the constants; do not "fix" the tests by loosening them silently.

Uncommitted at time of writing: `src/config.py` (+`DEMO_HOLD_MS`) and `src/main.py` (the
`--demo` flag, below). The repo **is** `git init`-ed (27 commits, `main`).

## Tech stack

| Concern | Choice |
| --- | --- |
| Language | Python 3.14 (`>=3.14.7,<3.15`, pinned in `pixi.toml`) |
| GUI | PyQt6 `>=6.8.1,<7` — frameless, `WA_TranslucentBackground`, always-on-top, `Qt.Tool`, click-through via `WS_EX_TRANSPARENT` |
| Metrics | `psutil >=7.0.0` (CPU/RAM/swap/disk/battery), `nvidia-ml-py >=13.610.43` (NVIDIA GPU load/VRAM/temp) |
| Windows APIs | `ctypes` only, confined to `src/core/platform/windows.py` (`GetLastInputInfo`, ex-style get/set) |
| Sprites | Atlas PNG decoded and sliced with Qt's own `QImage` — **Pillow is not a runtime dependency** |
| Frame QA | `tools/frame_qa.py` (checks) + `tools/frame_qa.ipynb` (napari review) |
| Tests | pytest `>=9.1.1`, `pytest.ini`, marker `slow` / `windows` |
| Env | pixi + `pixi.toml`, `win-64` only |

PyObjC / macOS hooks from `BuildPlan.md` are **out of scope**.

## Environment

**Windows only.** macOS/Linux are not supported or tested; the platform seam exists so they can
be added later (see *Platform seam*). Environment manager is [pixi](https://pixi.sh) via
`pixi.toml` — not venv, bare pip, conda, or `pyproject.toml`. `pixi.lock` is committed.

Three environments, split on purpose (`no-default-feature = true` on `pets` and `dev` is
load-bearing — without it pixi folds napari/Jupyter/Pillow into both):

| Environment | Holds | Purpose |
| --- | --- | --- |
| `pets` | python, pyqt6, psutil, numpy, nvidia-ml-py | lean runtime |
| `dev` | the `pets` pins **mirrored** + pytest | test suite; `test_packaging.py` fails if the pins drift |
| default | napari, jupyterlab, ipywidgets, pillow, numpy, pyqt6 | art pipeline; the app never touches it |

`pets` and `dev` share one `solve-group`, so tests run against the versions the app ships.

Commands:

```powershell
pixi install                                   # materialize envs from pixi.lock
pixi run -e pets pets                          # launch the pets
pixi run -e pets pets --validate               # atlas check, no GUI, exit 1 on failure
pixi run -e pets pets --debug                  # verbose logging
pixi run -e pets pets --demo [SECONDS]         # cycle every available animation (default 4 s each)
pixi run -e pets python -m src.core.sprite_loader -v   # per-frame atlas report
pixi run -e dev test                           # full suite (~60 s)
pixi run -e dev test -m "not slow"             # fast subset (~6 s) — use while editing
pixi run -e dev test -m "not windows"          # skip tests needing a real HWND
pixi run check                                 # frame QA over every art sequence (exit 1 = blocked)
pixi run check <Sequence> [--json] [-v]
pixi run qa                                    # frame QA notebook in JupyterLab
```

**Do not run project code through bare `python` or `pip`.** `python` on PATH is a pixi global
env and `pip` targets a separate CPython 3.10; neither is the project env. Always `pixi run`.

## What is implemented in `src/`

```
src/
  main.py                 PetApp wiring + CLI (--debug, --validate, --demo). No behaviour of its own.
  config.py               every tunable + Settings persistence
  core/
    sprite_loader.py      atlas JSON+PNG -> validated SpriteSheet of QPixmap frames (890 lines)
    animator.py           drift-compensated per-frame QTimer player
    state_machine.py      metrics -> state: ladder, Schmitt bands, dwell, overrides, flourishes
    telemetry.py          worker-thread metrics sampling + 250 ms input sub-sampler
    platform/             OS seam: base.py (ABC + NullBackend), windows.py (ctypes), __init__.py
  ui/
    _overlay.py           shared frameless/translucent/on-top flag recipe
    pet_window.py         overlay window, alpha-aware drag, click-through, position persistence
    stats_card.py         hover/pinned card, hand-painted shadow, glyph painters (1,212 lines)
    tray.py               QSystemTrayIcon + menu, async install with retry
tests/                    13 modules, see tests/README.md
tools/frame_qa.py         art QA gate
```

### Behaviour as built

- **Atlas loader.** Reads per-frame `x/y/width/height` and `duration_ms` verbatim from JSON —
  never computes `col * frame_width`. Validates schema version (`{1}`), verifies sha256,
  warns/blocks via an `AtlasReport` (same verdict vocabulary as `frame_qa`), computes opaque
  content bounds with numpy, exposes `alpha_at()` for hit-testing, and `require()` as the one
  place that asserts which rows the app needs. `AssetsMissingError` / `AtlasValidationError`
  give one clear failure at startup.
- **Animator.** Honours authored per-frame durations (measured worst error 2.0 ms), compensates
  drift (−0.012 % over 3 loops), drops missed beats instead of machine-gunning after a stall,
  pauses when the window is hidden. Non-looping rows **hold** the last frame and emit
  `sequence_finished`; it never reverts on its own.
- **State machine.** Ten states. Priority ladder, first match wins: `blocked-grumpy`
  (temp or swap) → `running-playful-jump` (CPU or RAM high) → sleep rows (idle ≥ 60 s dozy /
  ≥ 900 s deep sleep, both gated on CPU not moderate) → `running-playful-roll` (CPU moderate)
  → `working-together` (sustained input) → `idle-attentive` (unconditional default).
  Every numeric gate is a **Schmitt band** (`config.BAND_*`: CPU-high 80/70, RAM-high 85/80,
  CPU-moderate 20/14, swap 30/22, temp 80/75) plus `MIN_STATE_DWELL_MS = 4000`; escalations
  (by `PRIORITY`) bypass dwell. Timed **overrides** (poke menu, greeting, flourishes) expire on
  a single-shot timer that re-arms if it fires early. `ready-pleased` fires from a latch:
  CPU ≥ 60 % for ≥ 8 s, then ≤ 20 % for 2 samples. **Idle flourishes** every ~5 min ± 30 s,
  weighted pool roll 6 / stroll 3 / wave 1 — load-mapped rows are deliberately excluded so the
  pet stays readable as a monitor. While in the moderate band, `running-busy-play` plays every
  ~25 s ± 5 s. The idle schedule is **not** reset when the pet briefly leaves idle.
- **Optional rows.** `OPTIONAL_STATES = {working-together}`. `main.py` calls
  `set_available_states(sheet.names())`; four independent filters make an absent row
  unreachable, so a state can ship before its art. `REQUIRED_STATES` is derived.
- **Telemetry.** `TelemetryService` owns a `QThread` + `_TelemetryWorker` driven by a `QTimer`
  (1500 ms default, clamped 500–10,000 ms). Non-blocking `cpu_percent(interval=None)`, primed.
  Emits a frozen `Metrics` (CPU, RAM, swap, disk every 20 ticks, temp, `GpuSample`,
  `BatterySample`, idle seconds). GPU and temperature providers are **lazily probed Protocols**
  with Null fallbacks; a GPU provider is dropped after 3 consecutive errors. A separate 250 ms
  sub-sampler on the same thread emits a latched `input_activity_changed(bool)` (enter at ≥ 75 %
  of a 1 s window, 2.2 s idle grace), measured against `QElapsedTimer`, not nominal ticks.
  `stop()` is `quit()` + `wait()`; measured 2–15 ms, second call a no-op.
- **Input detection is non-invasive by design.** `GetLastInputInfo` only. No keyboard hook,
  `GetAsyncKeyState` polling, or Raw Input. Do not add any.
- **Pet window.** 192×208 (the atlas frame box, read from JSON). Direct `paintEvent` (no
  `QLabel` margins), try/except net around paint. `set_flag_override()` is the **only** way
  to change window flags and re-applies native state after the HWND is recreated.
  Left-drag with click-vs-drag split by `startDragDistance()`; drag starts only on an opaque
  pixel; click pins the card and plays a greeting; drag greets on drop. Position persists per
  screen and is clamped/revalidated on monitor changes. Optional integer render scale.
- **Click-through.** `WS_EX_TRANSPARENT` via the Windows backend, only enabled when a tray icon
  exists, so there is always a way back. `WA_TransparentForMouseEvents` alone does not work
  across processes.
- **Stats card.** CPU and RAM always; GPU, VRAM, temperature, battery, disk rows appear only if
  the hardware provides them (structurally absent GPU omits the row; a failed sample keeps a
  greyed one). Per-state title/subtitle/accent/glyph from `config.STATE_PRESENTATION`
  (data-driven; unknown template keys degrade to an em-dash). Hover with 250 ms show / 700 ms
  hide delays, 120/160 ms fades, anchors below the pet and flips above at screen edges, can be
  pinned. Hand-painted 18-layer shadow in a 29 px transparent margin — use `card_rect()`, not
  `width()`, for the visible card. Surface alpha 242.
- **Tray.** Click-through, Show pet, Show stats card, Always on top (checkable);
  **Poke the pets** submenu (one entry per state, greyed if the atlas lacks the row);
  Reset position; Quit. Double-click is the "give me my pet back" gesture: shows the pet if hidden,
  raises it, and turns click-through off. Single left-click is ignored. Installed asynchronously with a 2 s retry; icon
  built from the idle sprite with a drawn fallback.
- **Settings.** `%APPDATA%\LC-pet-stat\settings.json` (override with
  `$LC_PET_STAT_CONFIG_DIR`). Atomic write with read-back verification, tolerant parse, unknown
  keys ignored, never deletes a bad file. Saved on drag-finish and on quit.
- **`--demo`.** Cycles every available state through the poke override, each held for
  `DEMO_HOLD_MS` (4 s) or the given seconds (minimum 500 ms). Uncommitted.
- **Platform seam.** No file outside `core/platform/` may contain `ctypes`, `sys.platform`, or
  `win32`; no file outside `telemetry.py` may call `psutil`; no file in `src/` may hard-code
  sprite geometry (`192`, `208`, `8`, `9`, `74`). `NullBackend` degrades non-Windows runs with
  a single warning.

### State and trigger summary (as implemented)

| Row | State | Frames | Trigger in code |
| --- | --- | ---: | --- |
| 0 | `idle-attentive` | 8 | default; plus idle flourish host |
| 1 | `running-playful-roll` | 8 | CPU moderate band; also the main idle flourish |
| 2 | `running-playful-jump` | 10 | CPU ≥ 80 (band 80/70) or RAM ≥ 85 (band 85/80) |
| 3 | `waiting-deep-sleep` | 8 | idle ≥ 900 s, CPU not moderate |
| 4 | `waiting-dozy` | 8 | idle ≥ 60 s, CPU not moderate |
| 5 | `blocked-grumpy` | 8 | temp ≥ 80 °C (GPU, via NVML) or swap ≥ 30 % |
| 6 | `needs-input-sad` | 8 | **no automatic trigger** — tray "Poke the pets" only |
| 7 | `running-busy-play` | 8 | one-shot flourish (`loop: false`, holds last frame) |
| 8 | `ready-pleased` | 8 | CPU spike (≥ 60 % for ≥ 8 s) then calm; also click/drag greeting |
| 9 | `working-together` | 8 | sustained keyboard/mouse activity, calm system |

## Asset pipeline

`assets/` conventions are unchanged and must be followed. Full process:
`docs/codex-animation-pipeline-guide.md`; check docs: `assets/README.md`. `AGENTS.md` holds the
current atlas contract and QA registry.

```
assets/
  layla-and-chico-pet-spritesheet.png/.json   the only two files the runtime needs
  gen-image/<Sequence>/                       one folder per animation sequence
    00-inputs/ 01-generated-sequence/ 02-extracted-frames/ 03-qa-reference/ 04-integration-notes/
  codex/                                      packing scripts, run-001/002, delivery-001, backups
  gen-gifs/                                   legacy 192×208 GIFs
```

- **Numbered stage folders**; never flatten. **Revisions additive**; never overwrite art.
- Registered frames are **320×320**; identify them by size, **never by filename suffix**.
- Atlas packing: one uniform 0.6 nearest-neighbour scale 320→192, padded (0,8) to 192×208.
  The atlas is **packed outside `src/`** by scripts in `assets/codex/scripts/`.
- Registration contract: x=160 on the largest 4-connected component, baseline y=291 (inclusive),
  one shared scale per sequence; airborne/rolling sequences keep vertical motion.
- `sequence-spec.json` is the source of truth for a sequence. QA GIFs are not production assets.
- Run `pixi run check` after registration and again once the spec exists.

### Sequence registry

Live version: `pixi run check`. Last recorded in `AGENTS.md` (2026-09-14):

| Sequence | Row | Layout | QA |
| --- | --- | --- | --- |
| `Chico-alert` | 0 | aligned-03 | pass |
| `Layla-ball-rolling` | 1 | aligned-03 | pass with warnings (0 block, 8 warn) |
| `Chico-jump` | 2 | normalized-02 | warn — limited lift, anchor-x, malformed trailing escape in spec |
| `Chico-layla-nap` | 3 | aligned-03 | pass |
| `Layla-sleeping` | 4 | normalized-02 | warn — anchor-x, content height |
| `Layla-chico-mad` | 5 | normalized-02 | pass |
| `Chico-sad` | 6 | aligned-03 | warn — baseline |
| `Layla-strolling` | 7 | aligned-03 | warn — edge frames 6–8; atlas uses corrected extraction in `assets/codex/run-002/derived/` |
| `Layla-Chico-Wave` | 8 | aligned-03 | pass |
| `Layla-Chico-Working` | 9 | aligned-03 | pass; visual deviations recorded separately |

## Known gaps (carry forward, do not silently fix)

1. **3 failing fast tests** from config drift — see *Current state*.
2. **`Chico-jump` has 0 px of lift.** Every frame's bbox bottom is at exactly y=182 in the atlas;
   the arc was erased by per-frame bottom pinning. A fix needs a per-frame `draw_offset` in the
   atlas JSON applied through `Frame.offset` — not a row-2 special case.
3. **`needs-input-sad` has no trigger.** Reachable only from the tray. Reserved for Phase 2.
4. **CPU temperature has no Windows source.** `psutil.sensors_temperatures()` does not exist
   on Windows. `blocked-grumpy` fires on **GPU** temp (NVML) or swap only. Needs
   LibreHardwareMonitor. Non-NVIDIA GPUs are unsupported.
5. **`SWAP_HIGH = 30` is a guess**, unvalidated under real memory pressure.
6. **Pixel art is stretched 1.5×** on a DPR-1.5 display (non-integer device pixels). The
   `DEVICE_PIXEL_SNAP` path described in the log is **not implemented**.
7. **`ready-pleased` flashes `idle-attentive` for one ~1.5 s sample** before firing.
8. **No global hotkey** as a second click-through escape hatch (`RegisterHotKey`, Phase 2).
9. **`running-busy-play` ends with Layla out of frame** and must stay one-shot; the log wants it
   redrawn to loop in before Phase 2.
10. **Working row:** spec says 1800 ms total, timings sum to 1300 ms; art exceeds the original
    palette cap; frame 5 has a detached 101-px tail fragment.
11. **Idle-attentive depends on RAM not gating it** (box idles ~70 % RAM). An adaptive threshold is
    a Phase 2/3 item.
12. **Not tested:** visual appearance, multi-monitor, real sustained load, long-running
    behaviour, sleep/resume, `working-together` end-to-end latency, 35 slow tests unrun here.
13. **macOS/Linux** structurally seamed, unimplemented. Wayland needs `wlr-layer-shell`.
14. **Historical scripts** under `assets/codex/scripts/` reference old paths — inspect before
    rerunning; they do not safely rebuild the current atlas.
15. `Segoe UI Variable Text` is not installed on the dev box; the card is judged on `Segoe UI`.

## Phase 2 (not implemented)

Exploratory only: a small chat window connecting Layla and Chico to LLMs with carefully scoped
MCP tools (including a personal vault), keeping actions visible and under user control. Nothing
exists in the repo for this yet. Do not describe it as built.

## Defects fixed from the BuildPlan §4 prototype (all done — see `docs/PHASE1-LOG.md`)

`QImage` use-after-free and missing `bytesPerLine` (removed Pillow from runtime) · `SubWindow`
→ `Qt.Tool` · `QLabel` 9 px margins · `while True` telemetry thread · blocking
`cpu_percent(interval=1)` · thresholds 30/75 vs table and no hysteresis · bare-filename asset
paths. Each has a named regression test in `tests/README.md`'s index.

## Generated files — validate before handing them over

If a format has a validator or builder library, use it; do not hand-author serialized output.
Content that parses is not content that conforms. Round-trip everything: write, re-read with the
real parser, assert it matches (`config.save_settings` does this).

- **Notebooks**: `nbformat.v4.new_notebook()` / `new_code_cell()`, finish with
  `nbformat.validate()`. (A hand-written notebook once lacked the cell `id`s nbformat ≥ 4.5 needs.)
- **JSON** (`sequence-spec.json`, atlas): UTF-8, no BOM, no trailing text.
- **Run the artifact before calling it done.** Every substantive defect in this repo — including
  the exit-code-9 `QPoint`/`QPointF` crash and the inverted pause wiring — surfaced from
  execution, not from reading.

## Working conventions

- Run everything through `pixi run`; never `pip install` into global interpreters.
- Windows only; no macOS/Linux branches unless asked.
- Thresholds, intervals and timings live in `config.py` / the atlas JSON, never inline in widgets.
- Telemetry and I/O stay on the worker thread; **never touch widgets or call psutil across
  threads**. Signals are `snake_case`; modules start with `from __future__ import annotations`;
  public classes/functions docstring which thread may call them; frozen+slots dataclasses.
- Tests inject the clock (`StateMachine.evaluate(m, now)`) and seed the flourish RNG; the user's
  real settings file must never be touched (`$LC_PET_STAT_CONFIG_DIR`).
- Don't regenerate or overwrite art; add a new `revision-NN/`. Back up before any atlas update.
- Run `pixi run check` after extracting frames and before generating a QA sheet or GIF.
- When a sequence lands, update the registry here and in `AGENTS.md`.
