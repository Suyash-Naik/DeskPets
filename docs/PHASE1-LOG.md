# Phase 1 build log — `desk-pet`

A record of what was built, what was decided and why, and what went wrong along the way.
Written for review, not as documentation — the durable interface spec lives in
`docs/PHASE1-CONTRACT.md`, and the tunables are all in `src/config.py`.

---

## What was asked for

Build the Phase 1 base code for the desktop pet from the working prototype in `BuildPlan.md`
§4, in a new pixi environment with a `pixi run pets` task. Three pillars, planned by three
agents first:

1. Frameless, translucent, always-on-top surface.
2. Left-click drag, plus a tray-toggleable click-through mode.
3. A status card showing live telemetry, in the visual language of OpenAI's "Pets" feature.

Constraints carried throughout: clean, lean, modular; Windows now but macOS/Linux later;
and the sprites are WIP, so nothing may hard-code what the art currently happens to be.

---

## The thing that changed the plan

`CLAUDE.md` states the spritesheet is "incoming, not yet in the repo" and instructs treating
its absence as expected. **That is stale — the atlas had landed.** Both
`assets/layla-and-chico-pet-spritesheet.png` (1920×1872 RGBA) and its `.json` were on disk.

That mattered a lot, because the shipped atlas is materially richer than `BuildPlan.md`
describes, and it closes three of CLAUDE.md's eight open questions outright:

| CLAUDE.md open question | Status after reading the real atlas |
| --- | --- |
| #1 "Who packs the spritesheet?" | **Closed.** It is packed outside the repo; the JSON records `source_path_base`, per-frame source paths and sha256s. No in-repo packer is needed. |
| #2 "Frame size conflict: 320 vs 192×208" | **Closed.** Top-level `transform` records `scale: 0.6`, `filter: "nearest"`, `offset: [0, 8]` — the rescale already happened, with a nearest filter. This is also the hard justification for nearest-neighbour at render time. |
| #3 "Frame count conflict: jump is 10, row specced 8" | **Closed.** The packer widened the grid to **10 columns** and kept all 10 jump frames. Row 2 really is 10. |

Three further facts from the atlas drove design decisions:

- **Per-frame `x`/`y`/`width`/`height` are explicit** in `frame_data[]`. The loader reads them
  verbatim; it never computes `col * frame_width`. This is what makes a future ragged or
  repacked atlas a JSON-only change.
- **Per-frame `duration_ms`** is authored, running 90–260 ms. The prototype's fixed 120 ms
  tick destroys that phrasing. The atlas's own `compatibility.notes` says so explicitly.
- **Row 7 `running-busy-play` is `loop: false`** with a deliberate tail-only final pose
  (Layla walks out of frame). That single fact drove the rows-1-vs-7 tiebreak below.

---

## Decisions

### Defects found in the prototype

`BuildPlan.md` §4 is a sketch. These are real bugs in it, all fixed rather than carried over:

1. **Use-after-free in sprite loading.** `QImage(crop.tobytes(), w, h, Format_RGBA8888)` wraps
   a `bytes` temporary with no other reference — it is freed while the QImage still points at
   it. It appears to work only because CPython's allocator has not reused the block yet.
   *Fix:* drop Pillow from the runtime path entirely and use `QImage(path)` +
   `QImage.copy(rect)`, whose buffers are Qt-owned. This makes the defect **unrepresentable**
   rather than merely patched — a later edit cannot reintroduce it.
2. **Missing `bytesPerLine`** in the same call. Latent today (192 × 4 = 768 is already
   4-aligned) and would detonate silently on any future atlas with an odd frame width. Gone
   with the same fix.
3. **`Qt.WindowType.SubWindow` is wrong.** It is an MDI child type; on a parentless top-level
   it is an unsupported configuration and gives none of the taskbar exclusion presumably
   wanted. Replaced with `Qt.Tool` (`WS_EX_TOOLWINDOW`: no taskbar button, not in Alt-Tab).
   Both the window planner and the interaction planner flagged this independently.
4. **`QLabel` + `QVBoxLayout` silently adds 9 px margins** on all four sides, making the window
   210×226 with the pet off-centre. Replaced with a direct `paintEvent`.
5. **`TelemetryThread.run` is `while True` with no stop flag** — the thread never exits and the
   process hangs on quit. Replaced with a worker object on a `QThread` driven by a `QTimer`,
   so shutdown is `quit()` + `wait()` against an idle event loop.
6. **`psutil.cpu_percent(interval=1)` blocks**, pinning the poll rate at 1 s regardless of any
   configured interval. Replaced with the non-blocking `interval=None` form plus a primed
   first call.
7. **Thresholds 30/75 contradict the spec table's 20/60/80/85, and there is no hysteresis** —
   the pet flaps between states at a boundary. Every gate is now a Schmitt band plus a minimum
   dwell, all numbers in `config.py`.
8. **Asset paths are bare filenames**, which only resolve if the process happens to start in
   `assets/`. The PNG is now resolved relative to the JSON's own directory.

### Where the three plans disagreed

The planners worked in parallel against a shared skeleton, which surfaced seven genuine
conflicts. All are recorded with rationale in `docs/PHASE1-CONTRACT.md` §2; the two that
mattered most:

- **Who reverts a non-looping animation?** The rendering planner had the animator auto-fall
  back to idle (which is what the atlas's own note suggests). The telemetry planner had the
  animator *hold* the last frame because the state machine owns reversion. Both are defensible
  alone; together they race and the pet ping-pongs. **Resolved in favour of one owner:** the
  animator holds and emits `sequence_finished`; the state machine reverts by letting a timed
  override expire.
- **Click-through mechanism.** Settled definitively: `WA_TransparentForMouseEvents` cannot
  work across processes — it only redirects events *inside* Qt's dispatcher, and by then the OS
  has already hit-tested and decided our window owns the pixel. Using it alone would be worse
  than doing nothing: the pet stops responding *and* the window underneath still never gets the
  click. Only `WS_EX_TRANSPARENT` makes the OS skip the window.

### Findings that came from running things, not reading them

The telemetry planner probed the actual machine instead of trusting the spec, which caught a
design bug that would have shipped:

- **This desktop idles at ~70.5 % RAM.** `BuildPlan.md` gates `idle-attentive` on
  `CPU < 20% AND RAM < 60%` — so the *default state would have been unreachable here*. Fixed by
  making `idle-attentive` the unconditional terminal default and demoting the RAM figure to
  card subtitle copy (`config.RAM_BUSY_HINT`, which is explicitly commented as not a gate).
- `psutil.sensors_temperatures()` does not exist on Windows (confirms CLAUDE.md gap #4),
  `sensors_battery()` returns `None` on a desktop, and no GPU library was installed at the
  time. So the card was designed against a two-row CPU/RAM default rather than treating the
  degraded case as an afterthought — which is what made adding the GPU row later a no-op.
- **Later corrected during review:** "no GPU library installed" was a gap, not a fact of the
  machine. This box has an RTX 3070, so `nvidia-ml-py` was added and GPU load, VRAM and
  temperature are now live. The provider seam meant nothing else changed — the card grew a row
  and the thermal trigger woke up on its own. Worth recording as evidence that the
  lazily-probed-provider design paid for itself.
- `blocked-grumpy` is therefore thermally triggerable on this machine. On hardware without an
  NVIDIA GPU it remains swap-only, and measured swap here is 3.8 %, so it would rarely fire.
  Either way it is reachable from the tray's "Poke the pets" submenu, which doubles as the
  manual QA path for every animation row while the sprites are still WIP.

### Open questions answered

CLAUDE.md #5 asked for a tiebreak where two rows share a trigger. Both are now resolved
semantically rather than arbitrarily:

- **Rows 1 vs 7** (both CPU 20–60 %): `running-playful-roll` is the resident state;
  `running-busy-play` is a periodic one-shot flourish every ~25 s ± 5 s jitter. Driven by row
  7 being `loop: false` with a tail-only final pose — residing in it would freeze the pet on a
  near-empty frame for minutes. Splitting the CPU band was rejected because it reintroduces
  exactly the boundary-flapping the hysteresis work removes.
- **Rows 3 vs 4** (both user-idle > 5 min): split by depth — `waiting-dozy` at 5 min,
  `waiting-deep-sleep` at 15 min. The state names describe a progression, so mapping them to a
  progression in the driving variable is the reading the art implies. `idle_seconds` rises
  monotonically and steps to ~0 on any input, so neither needs a Schmitt band.

Both sleep states are additionally gated on CPU being low: a machine rendering at 90 % while
the user is away should not show a sleeping pet. The pet represents the *machine*, not the
human.

---

## Environment

A new pixi **feature** rather than a change to the default environment:

```toml
[feature.pets.dependencies]   # PyQt6 + psutil + numpy + nvidia-ml-py.
                              # No napari, no Jupyter, no Pillow.
[feature.pets.tasks]
pets = { cmd = "python -m src.main", description = "Run the Layla & Chico desktop pet" }
[environments]
pets = { features = ["pets"], solve-group = "pets" }
```

The heavy art-QA environment stays as `default`, so `pixi run check` and `pixi run qa` are
unaffected. `pixi task list` shows `pets` as unambiguous, so plain `pixi run pets` resolves to
the right environment without `-e`.

Pillow is deliberately **not** in the runtime env — see defect 1 above.

Platforms stayed `win-64`. Adding `osx-arm64`/`linux-64` would force re-solving the default
environment (napari, JupyterLab) for those platforms too, which is a slow, large change for no
Phase 1 benefit. The *code* seams for macOS/Linux are real; the pixi change is one line when
someone actually wants to build there.

---

## How it was built

Three planning agents first, one per pillar, working in parallel against a shared module
skeleton. That was deliberate: independent plans surface disagreements that a single pass
would paper over. It produced seven genuine conflicts, all recorded with rationale in
`docs/PHASE1-CONTRACT.md` §2. Two planners independently identified the `SubWindow` defect,
which is decent evidence it is real.

The contract was then written as the single authoritative spec, `config.py` was written and
verified, and implementation fanned out in waves — each agent required to **run** its module
against real data, not just write it. Every substantive bug below was found by execution.

## Bugs found by running the code

Listed because they are the argument for the "run the artifact before calling it done" rule
in CLAUDE.md. None of these would have been caught by reading.

1. **`QPoint`/`QPointF` mismatch aborted the process.** `_draw_bolt` passed `QPoint` to
   `QPainterPath.moveTo`, which has only `QPointF` overloads. The `TypeError` inside
   `paintEvent` makes PyQt6 call `qFatal` — **exit code 9, no Python traceback**. Every entry
   into `running-playful-jump` (i.e. any heavy CPU load) would have killed the pet, with
   nothing in the log to explain it. `paintEvent` now has a `try/except` net so a future paint
   error logs instead of aborting silently.
2. **`visibility_changed` and `set_paused` have opposite senses.** The contract's shorthand
   "wire to `Animator.set_paused`" hid it, and `main.py` connected them directly — so the pet
   froze the instant it became visible. Caught because the smoke test counted frames:
   0 emitted in 2.2 s where ~15 were expected. Now an explicit inverting lambda, with the
   trap documented at both ends.
3. **The card never received its initial state.** `state_changed` only fires on an actual
   change, and the machine starts already in its default state — so the card sat on the
   `DEFAULT_PRESENTATION` placeholder ("Chico and Layla / Watching your system") instead of
   the real copy. Caught by looking at a rendered PNG, not by reading code.
4. **Two clock domains in the dwell timer.** `StateMachine._entered_at` was seeded from
   `time.monotonic()` in `__init__`, but `evaluate()` takes an *injected* `now`. Any caller on
   its own timeline computes a state age of minus several days, reads it as "still inside
   dwell", and pins the pet to `idle-attentive` permanently. Invisible in production (both
   clocks agree); fatal to every test. Now unset until the first committed change.
5. **The card vanished permanently if re-hovered mid-fade-out.** The visibility guard returned
   early on `isVisible()`, which is true *during* the fade; the animation then ran to zero and
   hid it with no timer pending.

## Measured results

Not assertions that it works — numbers from running it.

| What | Measured |
| --- | --- |
| Atlas validation | `PASS (0 block, 0 warn)`, 74 frames in 9 animations, sha matches |
| Frame timing vs the atlas's authored `[150,110,120,110,180,120,110,220]` ms | `150.6 / 109.6 / 120.4 / 109.1 / 180.1 / 120.9 / 109.0 / 221.0` — worst error **2.0 ms** |
| Animation drift over 3 loops | **−0.4 ms (−0.012 %)**; with compensation off, +16.4 ms over 6 loops |
| Event loop stalled 1.2 s | **zero** sub-40 ms gaps — missed beats dropped, not machine-gunned |
| Telemetry clean shutdown | **15.03 ms** against a 2000 ms budget; second `stop()` 0.003 ms |
| Poll interval | 1508 / 1525 ms against a 1500 ms target |
| Pet window size | **192×208** — the QLabel-margin regression (210×226) is gone |
| Click-through across a flag change | ex-style `0x800a8 → 0x800a0 → 0x800a8`, `WS_EX_TRANSPARENT` never lost across 10 toggles |
| Alpha hit-testing | opaque bbox `(31, 73, 129, 110)` in a 192×208 cell — a **25 px** transparent gutter below the feet |
| State machine | CPU 78→82→78→82 produces **1** transition, not 4 |
| Degradation | `battery` and `cpu_temp_c` `None`, one INFO line each, no crash |
| GPU (after adding `nvidia-ml-py`) | RTX 3070, 4 % load, 3.59/8.00 GiB VRAM, 47 °C; card grows a GPU row, height 174 → 192 px |
| Full-app smoke | 55 frames, 5 metric samples, all 9 states cycled through paint with no abort |

## Known gaps and judgement calls

Things left undone on purpose, so they are decisions rather than oversights.

1. **Pixel art is being stretched 1.5× on this display.** The machine runs at DPR 1.5 and the
   `PassThrough` rounding policy preserves that, so 192×208 nearest-neighbour art lands on
   non-integer device pixels — some source pixels are 1 device px wide, some 2. The fix is the
   device-pixel-snap path sketched in the rendering plan (scale once by an integer factor, set
   `devicePixelRatio`, blit 1:1) behind `config.DEVICE_PIXEL_SNAP`. Not done because it wants
   a look on a real 150 % monitor to judge whether it is worth the per-frame cache.
2. **`ready-pleased` flashes `idle-attentive` for one ~1.5 s sample before firing.** The latch
   needs two consecutive calm samples while the rule ladder already answers on the first.
   Suppressing it means inventing a rule the contract does not have; it is tunable via
   `READY_CALM_SAMPLES`.
3. ~~**`blocked-grumpy` may never fire on this machine.**~~ **Resolved during review.**
   `nvidia-ml-py` is now in the `pets` environment, so GPU load, VRAM and **temperature** are
   live (RTX 3070, reading 47 °C at idle). `_hottest()` already took the max of CPU and GPU
   temperature, so the thermal half of the row-5 trigger fires for real on NVIDIA hardware —
   this partially closes CLAUDE.md open question #4. CPU temperature is still `None` on
   Windows; LibreHardwareMonitor remains the route to it. On a non-NVIDIA machine the band
   stays inert via `Band.holds(None, ...) -> False` and swap is the only trigger, so the
   degradation path still matters and is still exercised.
4. **`needs-input-sad` has no automatic trigger** — by design. There is no telemetry signal for
   "input needed"; detecting it properly means UI Automation, which is a feature, not a
   threshold. A pet that looks sad for no discoverable reason is worse than one that never
   does.
5. **No global hotkey as a second click-through escape hatch.** The hard precondition (no
   click-through without a visible tray icon) plus the tray double-click cover safety.
   `RegisterHotKey` is the real fix and is a Phase 2 item.
6. **macOS and Linux are structurally seamed but unimplemented.** `core/platform/base.py`
   carries comments naming the exact APIs a port needs (`NSWindow.setIgnoresMouseEvents_`;
   X11 `XFixesSetWindowShapeRegion` with an empty input region). Note for whoever does it:
   Wayland has no protocol for a normal client to position its own surface or stay on top,
   which breaks the pet's core interaction — that port needs `wlr-layer-shell`, not a
   translation of the X11 path.
7. **`SWAP_HIGH = 30.0` is a guess.** CLAUDE.md says "high swap" without a number. It wants
   validating under real memory pressure.
8. **`Segoe UI Variable Text` is not installed here**, so the card is being judged on the
   `Segoe UI` fallback.
9. **Card surface opacity is a trade-off, tunable in one constant.** It started at the spec'd
   ~91 % (alpha 232). Captured over a real terminal, 9 % bleed of high-contrast text ghosted
   through legibly enough to *read the words underneath*. Raised to alpha 242 (~95 %), which
   reads as frosted glass. The reference card looks fine at 91 % because it sits over a soft
   wallpaper; a desktop pet sits over code editors. `CARD_SURFACE` is the dial.
10. **On a genuinely dark desktop the card's lift is carried almost entirely by the alpha-18
    rim**, which is faint. A shadow cannot do the work there (dark ink on dark ground). A
    lighter rim would fix it; left alone as a separate call.

## Two things worth knowing about the card widget

- The card window is **358 px wide, not 300** — the extra 29 px per side is the transparent
  margin the drop shadow is painted into. Anything reading `card.width()` and expecting the
  *visible* card must use `card_rect()` instead. Nothing outside `stats_card.py` does today.
- The shadow is painted by hand, not `QGraphicsDropShadowEffect` (which is expensive on a
  translucent top-level and renders inconsistently on Windows). Eighteen concentric rounded
  rects, where each layer's alpha is *solved* so the accumulated coverage follows a smooth
  falloff curve — stacking layers of equal alpha instead gives an exponential ramp that looks
  like a hard blob with a thin tail. Clipped to outside the surface path, biting 1 px under
  the card so the clip edge and the surface edge do not leave a hairline seam.

## Built for the sprites changing

The art is WIP, so the code treats the atlas as data throughout:

- Frame rects, counts, durations, loop flags, the default animation and the window size all
  come from the JSON at runtime. There is no `192`, `208`, `8`, `9` or `74` as sprite geometry
  anywhere in `src/`, and no `STATES = [...]` list driving the loader.
- Adding a tenth row is a JSON change plus one `config.STATE_PRESENTATION` entry for its card
  copy. No code edit.
- `Frame.offset` is computed bottom-centred within a per-animation box. Every offset is `(0,0)`
  for today's uniform atlas — it exists so a ragged or re-cropped atlas renders correctly with
  no change.
- `SpriteSheet.require()` is the single place the app asserts which names it needs, so a
  missing row produces one clear error listing exactly what the atlas lacks.
- The one thing that would need a schema change: **real vertical lift for the jump**.
  CLAUDE.md's open question 6 is confirmed — `Chico-jump`'s airborne frames sit on the same
  baseline because alignment pinned every bbox bottom to it. Restoring the arc needs a
  per-frame `draw_offset` in the atlas JSON, which `Frame.offset` is the natural place to
  apply. It must not be hacked in as a row-2 special case.

## Verify it yourself

```powershell
pixi run pets                                        # launch
pixi run -e pets python -m src.main --validate       # atlas check, no GUI, exit 1 on failure
pixi run -e pets python -m src.core.sprite_loader -v # detailed per-frame atlas report
pixi run pets --debug                                # verbose logging
```

