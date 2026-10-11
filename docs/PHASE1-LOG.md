# Phase 1 build log — `desk-pet`

A record of what was built, what was decided and why, and what went wrong along the way.
Written for review, not as documentation — the durable interface spec lives in `docs/PHASE1-CONTRACT.md`, and the tunables are all in `src/config.py`.
This is committed as a example source for how agents were handled and tasks to build the plan were managed. 

---

## What was asked for

Build the Phase 1 base code for the desktop pet from the working prototype in `BuildPlan.md` §4, in a new pixi environment with a `pixi run pets` task. Three pillars, planned by three higher agents first:

1. Frameless, translucent, always-on-top surface.
2. Left-click drag, plus a tray-toggleable click-through mode.
3. A status card showing live telemetry, in the visual language of OpenAI's "Pets" feature.

Constraints carried throughout: clean, lean, modular; Windows now but macOS/Linux later; and the sprites are WIP, so nothing may hard-code what the art currently happens to be. Code generation was developed parallely with the animations and interactions. 

---

## The thing that changed the plan

Spritesheet and animations developed with pictures of my cats with ai-generation.

Closed these open topics in my plan: 

| open topic | Status after reading the real atlas |
| --- | --- |
| #1 "How to generate and packs the spritesheet?" | **Closed.** It is packed outside the repo; the JSON records `source_path_base`, per-frame source paths and sha256s |
| #2 "Frame size " | **Closed.** With adpative  |
| #3 "Frame count discussion" | **Closed.** The packer widened the grid to **10 columns** and kept all 10 jump frames. Row 2 really is 10. Each row should be fleixble depending on richness of gif animation |

Three further facts from the atlas drove design decisions:

- **Per-frame `x`/`y`/`width`/`height` are explicit** in `frame_data[]`. The loader reads them verbatim; it never computes `col * frame_width`. This is what makes a future ragged or repacked atlas a JSON-only change.
- **Per-frame `duration_ms`** is authored, running 90–260 ms. The prototype's fixed 120 ms tick destroys that phrasing. The atlas's own `compatibility.notes` says so explicitly.
- **Row 7 `running-busy-play` is `loop: false`** with a deliberate tail-only final pose (Layla walks out of frame). That single fact drove the rows-1-vs-7 tiebreak below. `running-busy-play` needs to be redone before phase 2 to loop in and interact better. 

---

## Decisions

### Defects fixed in the prototype

`BuildPlan.md` §4 was a sketch. These aspects were changed during development:

1. **Use-after-free in sprite loading.** `QImage(crop.tobytes(), w, h, Format_RGBA8888)` wraps a `bytes` temporary with no other reference — it is freed while the QImage still points at it. It appears to work only because CPython's allocator has not reused the block yet. *Fix:* drop Pillow from the runtime path entirely and use `QImage(path)` + `QImage.copy(rect)`, whose buffers are Qt-owned. This makes the defect **unrepresentable** rather than merely patched — a later edit cannot reintroduce it.
2. **Missing `bytesPerLine`** in the same call. Latent today (192 × 4 = 768 is already 4-aligned) and would detonate silently on any future atlas with an odd frame width. Gone with the same fix.
3. **`Qt.WindowType.SubWindow` replaced with `Qt.Tool` (`WS_EX_TOOLWINDOW`: no taskbar button, not in Alt-Tab). 2 agents designed to window plan and interaction plan flagged this independently however in my limited knowledge of qt window handing this was new.
4. **`QLabel` + `QVBoxLayout` silently adds 9 px margins** on all four sides, making the window 210×226 with the pet off-centre. Replaced with a direct `paintEvent`.
5. **`TelemetryThread.run` is `while True` with no stop flag** — the thread never exits and the process hangs on quit. Replaced with a worker object on a `QThread` driven by a `QTimer`, so shutdown is `quit()` + `wait()` against an idle event loop.
6. **`psutil.cpu_percent(interval=1)` blocks**, pinning the poll rate at 1 s regardless of any configured interval. Replaced with the non-blocking `interval=None` form plus a primed first call. Untested with heavier CPU overloads as writing this takes at max 10% CPU.
7. **Thresholds 30/75 contradict the spec table's 20/60/80/85, and there is no hysteresis** — the pet flaps between states at a boundary. Every gate is now a Schmitt band plus a minimum dwell, all numbers in `config.py`.
8. **Asset paths**: The PNG is now resolved relative to the JSON's own directory.

### Where the three plans disagreed

The planners worked in parallel against a shared skeleton, which surfaced seven genuine conflicts. All are recorded with rationale in `docs/PHASE1-CONTRACT.md` §2; the two that mattered most:

- **Who reverts a non-looping animation?** The rendering planner had the animator auto-fall back to idle (which is what the atlas's own note suggests). The telemetry planner had the animator *hold* the last frame because the state machine owns reversion. Both are defensible alone; together they race and the pet ping-pongs. **Resolved in favour of one owner:** the animator holds and emits `sequence_finished`; the state machine reverts by letting a timed override expire.
- **Click-through mechanism.** Settled definitively: `WA_TransparentForMouseEvents` cannot work across processes — it only redirects events *inside* Qt's dispatcher, and by then the OS has already hit-tested and decided our window owns the pixel. Using it alone would be worse than doing nothing: the pet stops responding *and* the window underneath still never gets the click. Only `WS_EX_TRANSPARENT` makes the OS skip the window.

### Findings that came from running things, not reading them

The telemetry planner probed the actual machine instead of trusting the spec, which caught a design bug that would have shipped:

- **This desktop idles at ~70.5 % RAM. Because I have a fun habit of using chrome** `BuildPlan.md` gates `idle-attentive` on `CPU < 20% AND RAM < 60%` — so the *default state would have been unreachable here*. Fixed by  making `idle-attentive` the unconditional terminal default and demoting the RAM figure to card subtitle copy (`config.RAM_BUSY_HINT`, which is explicitly commented as not a gate). Need to figure out an adaptive thershold in phase 2/ if difficult to do phase 3.
- `psutil.sensors_temperatures()` does not exist on Windows (confirms CLAUDE.md gap #4), `sensors_battery()` returns `None` on a desktop, and no GPU library was installed at the  time. So the card was designed against a two-row CPU/RAM default rather than treating the degraded case as an afterthought — which is what made adding the GPU row later a no-op. This provides a way to integrate additional telemetries in case base spec change for eg. multiple GPU **starry eyed**. Currently set up for only Nvidia need to see if amd can be included too. 
- **Later corrected during review:** "no GPU library installed" was a gap due to lacking `nvidia-ml-py`. This box has an RTX 3070, so `nvidia-ml-py` was added and GPU load, VRAM and  temperature are now live. Worth recording as evidence that the lazily-probed-provider design worked as intended and flexibly designed.
- `blocked-grumpy` is therefore thermally triggerable on this machine. On hardware without an NVIDIA GPU it remains swap-only, and measured swap here is 3.8 %, so it would rarely fire. Either way it is reachable from the tray's "Poke the pets" submenu, which doubles as the manual QA path for every animation row while the sprites are still WIP.

### Open questions answered

CLAUDE.md #5 asked for a tiebreak where two rows share a trigger. Both are now resolved semantically rather than arbitrarily:

- **Rows 1 vs 7** (both CPU 20–60 %): `running-playful-roll` is the resident state; `running-busy-play` is a periodic one-shot flourish every ~25 s ± 5 s jitter. Driven by row 7 being `loop: false` with a tail-only final pose — residing in it would freeze the pet on a near-empty frame for minutes. Splitting the CPU band was rejected because it reintroduces exactly the boundary-flapping the hysteresis work removes.
- **Rows 3 vs 4** (both user-idle > 5 min): split by depth — `waiting-dozy` at 5 min, `waiting-deep-sleep` at 15 min. The state names describe a progression, so mapping them to a progression in the driving variable is the reading the art implies. `idle_seconds` rises monotonically and steps to ~0 on any input, so neither needs a Schmitt band.

Both sleep states are additionally gated on CPU being low: a machine rendering at 90 % while the user is away should not show a sleeping pet. The pet represents the *machine*, not the human.

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

The heavy art-QA environment stays as `default`, so `pixi run check` and `pixi run qa` are unaffected. `pixi task list` shows `pets` as unambiguous, so plain `pixi run pets` resolves to
the right environment without `-e`.

Pillow is deliberately **not** in the runtime env — pyQT conflicts.

Platforms stayed `win-64`. Adding `osx-arm64`/`linux-64` would force re-solving the default environment (napari, JupyterLab) for those platforms too, which is a slow, large change for no
Phase 1 benefit. The *code* seams for macOS/Linux are real; the pixi change is one line when I actually wants to build there to have my friends be able to use the app.

---

## How it was built

Three planning agents first, one per pillar, working in parallel against a shared module skeleton. That was deliberate: independent plans surface disagreements that a single pass would paper over. It produced seven genuine conflicts, all recorded with rationale in `docs/PHASE1-CONTRACT.md` §2. Two planners independently identified the `SubWindow` defect, which is quite difficult to ignore should have been clarified in `Build-Plan.md`.

The contract was then written as the single authoritative spec, `config.py` was written and verified, and implementation fanned out in waves — each agent required to **run** its module against real data, not just write it. Every substantive bug below was found by execution and testing using the tests.

## Bugs found by running the code

Listed because they are the argument for the "run the artifact before calling it done" rule in CLAUDE.md. None of these would have been caught by reading because this is a gui heavy spec.

1. **`QPoint`/`QPointF` mismatch aborted the process.** `_draw_bolt` passed `QPoint` to `QPainterPath.moveTo`, which has only `QPointF` overloads. The `TypeError` inside `paintEvent` makes PyQt6 call `qFatal` — **exit code 9, no Python traceback**. Every entry into `running-playful-jump` (i.e. any heavy CPU load) would have killed the pet, with nothing in the log to explain it. `paintEvent` now has a `try/except` net so a future paint error logs instead of aborting silently.
2. **`visibility_changed` and `set_paused` have opposite senses.** The contract's shorthand "wire to `Animator.set_paused`" hid it, and `main.py` connected them directly — so the pets froze the instant it became visible. Caught because the smoke test counted frames: 0 emitted in 2.2 s where ~15 were expected. Now an explicit inverting lambda, with the trap documented at both ends.
3. **The card never received its initial state.** `state_changed` only fires on an actual change, and the machine starts already in its default state — so the card sat on the `DEFAULT_PRESENTATION` placeholder ("Chico and Layla / Watching your system") instead of the real copy. Caught by looking at a rendered PNG, not by reading code.
4. **Two clock domains in the dwell timer.** `StateMachine._entered_at` was seeded from `time.monotonic()` in `__init__`, but `evaluate()` takes an *injected* `now`. Any caller on its own timeline computes a state age of minus several days, reads it as "still inside dwell", and pins the pet to `idle-attentive` permanently. Invisible in production (both clocks agree); fatal to every test.
5. **The card vanished permanently if re-hovered mid-fade-out.** The visibility guard returned early on `isVisible()`, which is true *during* the fade; the animation then ran to zero and hid it with no timer pending.

## Measured results

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

1. **Pixel art is being stretched 1.5× on this display.** The machine runs at DPR 1.5 and the `PassThrough` rounding policy preserves that, so 192×208 nearest-neighbour art lands on non-integer device pixels — some source pixels are 1 device px wide, some 2. The fix is the device-pixel-snap path sketched in the rendering plan (scale once by an integer factor, set `devicePixelRatio`, blit 1:1) behind `config.DEVICE_PIXEL_SNAP`. Needs to be tested across displays.
2. **`ready-pleased` flashes `idle-attentive` for one ~1.5 s sample before firing.** The latch vs ladder heirarchy is tricky to balance to trigger events upon system changes.It is tunable via `READY_CALM_SAMPLES`.
3. ~~**`blocked-grumpy` originally would not fire* this was fixed with introducing `nvidia-ml-py`.
4. **`needs-input-sad` has no automatic trigger** — by design. There is no telemetry signal for "input needed" yet as it is the waiting animation for the phase 2 in my head. It is not documented anywhere here yet as it depends on a separate project that is under development. 
5. **No global hotkey as a second click-through escape hatch.** The hard precondition (no click-through without a visible tray icon) plus the tray double-click cover safety.`RegisterHotKey` is the real fix and is a Phase 2 item. Currently I dont see the need to click through them where needed. 
6. **macOS and Linux are structurally seamed but unimplemented.** `core/platform/base.py` carries comments naming the exact APIs a port needs (`NSWindow.setIgnoresMouseEvents_`; X11 `XFixesSetWindowShapeRegion` with an empty input region). Note for whoever does it: Wayland has no protocol for a normal client to position its own surface or stay on top, which breaks the pet's core interaction — that port needs `wlr-layer-shell`, not translation of the X11 path. These are things that may be implemented later.
7. **`SWAP_HIGH = 30.0` is a guess.**  "validating under real memory pressure" is a test that I am very afraid to perform on this machine as it is my only dev machine and is old.
8. **`Segoe UI Variable Text` is not installed here**, so the card is being judged on the`Segoe UI` fallback. What fonts do I want to integrate? Current look looks good.  
9. **Card surface opacity is a trade-off, tunable in one constant.** It started at the spec'd ~91 % (alpha 232). Captured over a real terminal, 9 % bleed of high-contrast text ghosted through legibly enough to *read the words underneath*. Raised to alpha 242 (~95 %), which reads as frosted glass. The reference card looks fine at 91 % because it sits over a soft wallpaper; a desktop pet sits over code editors. `CARD_SURFACE` is the dial.
10. **On a genuinely dark desktop the card's lift is carried almost entirely by the alpha-18 rim**, which is faint. A shadow cannot do the work there (dark ink on dark ground). lighter rim would fix it not implemented as shadows look better. 

## Card-widgets specifics 

- The card window is **358 px wide, not 300** — the extra 29 px per side is the transparent margin the drop shadow is painted into. Anything reading `card.width()` and expecting the *visible* card must use `card_rect()` instead. Nothing outside `stats_card.py` does today.
- The shadow is painted by hand, not `QGraphicsDropShadowEffect` (which is expensive on a translucent top-level and renders inconsistently on Windows). Eighteen concentric rounded rects, where each layer's alpha is *solved* so the accumulated coverage follows a smooth falloff curve — stacking layers of equal alpha instead gives an exponential ramp that looks like a hard blob with a thin tail. Clipped to outside the surface path, biting 1 px under the card so the clip edge and the surface edge do not leave a hairline seam.

## Built for the sprites changing

The art is WIP, so the code treats the atlas as data throughout and is flexibly built around it:

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

## Added after the Phase 1 baseline

Everything above describes the initial build. These landed afterwards, in review.

### GPU telemetry — a gap, not a machine fact

`nvidia-ml-py` had been deferred on the reasoning that it was "dead weight on a non-NVIDIA
machine". That reasoning was simply wrong here: the box has an **RTX 3070**. The provider was
degrading correctly; there was nothing to degrade *from*. One `pixi add` fixed it and nothing
else changed — the lazily-probed provider found it, the card grew a GPU row (174 → 192 px),
and the log line flipped from "no GPU provider" to "GPU provider ready".

A second thing fell out of it unprompted: **GPU temperature is now live**, and `_hottest()`
already took the max of CPU and GPU temperature — so `blocked-grumpy`'s thermal trigger, which
had been written off as permanently inert on Windows, genuinely fires. That partially closes
CLAUDE.md open question #4. CPU package temperature still needs LibreHardwareMonitor.

The degradation path was re-verified afterwards by blocking the `pynvml` import, because
adding the dependency would otherwise have quietly stopped exercising the branch that every
non-NVIDIA machine takes.

### Idle variety — and why the stroll could not simply be held longer

The pet was monotonous: `idle-attentive` is very nearly the only reachable state during normal
work, because a modern machine rarely sustains CPU ≥ 20 % while you type. The stroll was
1160 ms every five minutes — **0.4 % of the time**, and its last frame is `tail-exit`, with
Layla already out of shot.

The instinct is to hold it longer. That does not work: **row 7 ends with Layla off-screen**, so
holding shows an empty window and looping teleports her back. 1160 ms is the length of the art,
not a setting. The fix was to make **row 1** (Layla batting a ball, the one Layla animation
that loops cleanly) the main flourish at 3 loops ≈ 4 s, with the stroll demoted to an
occasional exit gag. Measured over a simulated 6 hours of calm: 68 flourishes, 66 % roll /
29 % stroll / 4 % wave.

Two things were deliberate:

- **Load-mapped rows are excluded from the pool.** The pet is a monitor first; every animation
  allowed to play at an arbitrary time is one you can no longer read machine state from. A
  jump during idle would mean a jump no longer tells you the CPU is pegged. There is a test
  asserting no load row ever appears in the idle rotation.
- **The idle schedule is NOT reset when the pet briefly leaves `idle-attentive`.** On a
  five-minute timer, a transient CPU blip would restart the countdown, and a normal desktop
  blips often enough to starve the flourish completely — you would simply never see it.
  Measured: with a blip every 4 minutes, 6 flourishes still occur in 32 minutes; under
  reset-on-exit it would be 0. The short moderate-band cadence *does* re-arm, because at 25 s
  that feels responsive rather than starved.

### A real bug found while doing it: poll-quantised expiry

Override expiry was only noticed on the *next* telemetry sample. With a 1500 ms poll and a
1160 ms stroll, Layla finished walking off and the window then sat on her empty tail-only frame
for up to another 1500 ms — longer than the animation itself ran. Fixed with a single-shot
timer; measured hand-back is now **+5 ms to +21 ms** past the hold instead of up to +1500.

### Click and drag greetings

Clicking the pet, or finishing a drag, plays a wave. Driven through real synthesised mouse
events in the tests, so the drag threshold and alpha hit-testing are genuinely exercised:
a click waves without moving the window, a drag waves on *drop* and not mid-drag (switching
animation while the window moves under the cursor reads as glitchy), four rapid clicks produce
**one** transition rather than stuttering back to frame 0, and a click on a transparent corner
does nothing at all.

The greeting fires even under load, deliberately — an unresponsive pet reads as broken. The
cost, documented at the call site: for 1.5 s it covers whatever telemetry would otherwise show,
including an alarm. `INTERACTION_GREETING_STATE` is a state *name* in config, so when dedicated
greeting art lands it is a one-line swap, and `None` disables greetings entirely.

### `working-together` — and an honest limit

A new state showing both cats at a computer, driven by whether the user is actively working.

**What it actually measures.** The only source is `GetLastInputInfo`, which reports how long
ago the last input event of *any* kind arrived. A keystroke, a mouse move and a scroll are
indistinguishable to it. So this is **sustained interaction**, never "typing", and nothing in
the code is named as if it were. Deliberately **not** used, though all three are hook-free and
would give finer detail: `SetWindowsHookEx`/`WH_KEYBOARD_LL` (a keylogger by any reasonable
definition), `GetAsyncKeyState` polling, and Raw Input with `RIDEV_INPUTSINK` (both can
reconstruct keystrokes, and both are flagged by AV heuristics). A desktop pet does not get to
read input.

**It ships before its art.** The atlas has no row for it yet. `STATES` is what the machine can
*name*; the atlas decides what it may *emit*. `main.py` calls `set_available_states(sheet.names())`
and four independent filters — the ladder rule, the flourish pool, `trigger_state`, and
`require(REQUIRED_STATES)` — make an absent row unreachable by every route. Without that,
adding a tenth name to `STATES` would make `require()` throw and **the app would refuse to
start**; worse, a leaked name reaches the animator, which logs a warning and holds the previous
sprite while the card shows the new state's copy over it. Verified both ways: with the row
absent it is never selected; inject the name and the same input selects it on the first sample.

**Latency had to beat the poll.** Riding the 1500 ms `Metrics` payload would have stacked poll
latency + sustained window + dwell into something like 5 s — "I start typing, the pets react"
at 5 s reads as broken. A separate 250 ms sub-sampler on the telemetry worker emits a latched
`input_activity_changed(bool)` instead. The sub-sampler compares the idle reading against
**measured** elapsed time (a `QElapsedTimer`), never the nominal 250 ms, because a timer that
runs late would otherwise make real input look older than the gap and bias the ratio downward.

Measured: enter **584 ms** on the real seam, exit **~2.3 s**, telemetry shutdown **2–9 ms**
(*faster* than the 15 ms baseline, which is the evidence the new timer is actually stopped).
Waking from *sleep* is poll-bound at ≤1500 ms rather than 584 ms, because the cached `Metrics`
still carries a stale `idle_seconds` until the next tick.

### Documentation corrected

`assets/generation-prompts.md` was rewritten against the shipped atlas. It had listed the
*planned* frame counts (6/8/8/4/5/8/6/6/6) against the real 8/8/10/8/8/8/8/8/8, and several
pose descriptions described art that was never made — `ready-pleased` as "both cats sit
attentive" when it is actually a wave, `needs-input-sad` as both cats when it is Chico alone.

It also now records that the shipped art breaks three clauses of its own art direction: the
original prompt forbids text, symbols and shadows, but the finished sequences use detached
effect glyphs (`?`, `Z`, tears) and a contact shadow under every pose that the packer measures
its baseline from. Regenerating under the original wording would produce art inconsistent with
the other rows.

Two claims were checked against the pixels rather than trusted. Unused trailing cells really
are fully transparent (16 of them). And `Chico-jump`'s airborne frames are worse than recorded:
CLAUDE.md says "within 3 px" of the baseline; every one of the ten frames has its bounding-box
bottom at **exactly `y=182`** — **0 px of lift**, including `airborne-peak`. There is no arc at
all.

Four contradictions were found across the existing docs and are recorded rather than silently
patched: the baseline is documented as 292 but enforced as 291; `codex-animation-pipeline-guide.md`
step 2 says effects must be attached while step 5 says detached; the QA gate's own docstring and
`assets/README.md` disagree about which step it runs at; and the "at most five visible colours"
cap is authoring intent that the *aligned* frames do not honour (~10–11k distinct RGBA values,
plausibly explained by a non-integer resample recorded in the Wave spec).

## Verify it yourself

```powershell
pixi run pets                                        # launch
pixi run -e pets python -m src.main --validate       # atlas check, no GUI, exit 1 on failure
pixi run -e pets python -m src.core.sprite_loader -v # detailed per-frame atlas report
pixi run pets --debug                                # verbose logging
```

