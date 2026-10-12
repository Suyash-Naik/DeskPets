# Tests

A pytest suite for `src/`. It replaces roughly fifty throwaway verification scripts written during Phase 1 development, which lived in a session temp directory and were neither runnable nor reviewable by anyone else.

Every test that guards a specific historical bug says so in its docstring — what broke, how it manifested, and why the assertion is shaped the way it is. A lot of the development was done with human in the loop so some inconcistencies may arise due to them. These tests are designed as a safegaurd to ensure the code runs smoothly before full implementation. The suite is therefore also the record of what went wrong during Phase 1, which is the same argument `PHASE1-LOG.md` makes for CLAUDE.md's "run the artifact before calling it done" rule.

---

## Running it

```powershell
pixi run test                       # everything: 222 tests, ~60 s
pixi run test -m "not slow"         # the fast subset: 187 tests, ~5 s. Use this while editing.
pixi run test tests/test_state_machine.py          # one module
pixi run test -k hysteresis                        # one test, by name fragment
pixi run test -v                                   # names rather than dots
pixi run test -m "not windows"                     # skip everything needing a real HWND
```

`pixi run test` runs in the **`dev`** environment, which is `pets` plus pytest. Running
`pytest` some other way (a global install, the `default` art-QA environment) may work but is
not what any of this is written against — and `test_sprite_loader.py` will fail on purpose if
Pillow is importable, because the `pets` runtime is supposed to be lean.

The task sets `PYTHONDONTWRITEBYTECODE=1` and `pytest.ini` passes `-p no:cacheprovider`, so a
run leaves no `__pycache__/` or `.pytest_cache/` behind.

### The `slow` marker

Anything that measures real wall-clock time or spins a live Qt event loop. 35 tests,
and essentially all of the 60 s. `-m "not slow"` gives a genuinely useful five-second default
run — config, the state machine (which runs on an injected clock), the sprite loader, the
overlay recipe, the card's row logic, and every structural invariant.

Tolerances for the slow tests are derived from the measurements in `PHASE1-LOG.md`, with an
order of magnitude of headroom, and each one says in its docstring what it discriminates
against:

| Measured in PHASE1-LOG.md | Asserted here |
| --- | --- |
| animator per-frame error, worst 2.0 ms on 90–260 ms frames | 25 ms |
| animation drift over 3 loops, −0.4 ms (−0.012 %) | 40 ms |
| sub-40 ms gaps after a 1.2 s event-loop stall: zero | zero, exactly |
| telemetry clean shutdown 15.03 ms (2000 ms budget) | 250 ms |
| second `stop()` 0.003 ms | 50 ms |
| poll interval 1508 / 1525 ms vs a 1500 ms target | ±40 % of the interval |
| override expiry, bug delayed it up to 1500 ms | 200 ms on an 1160 ms hold |

---

## What the environment has to provide

All of it lives in **`tests/_env.py`**. Read that file rather than hunting for `skipif`
decorators. In short, three things are gated:

| Gate | Why it cannot be faked |
| --- | --- |
| `requires_windows` / `pytest.mark.windows` | a real Win32 `HWND`, `user32`, `WS_EX_TRANSPARENT`, `GetLastInputInfo` |
| `skip_without_tray()` | the Windows notification area (`QSystemTrayIcon.isSystemTrayAvailable()`) |
| `requires_atlas` | `assets/layla-and-chico-pet-spritesheet.{json,png}` on disk |

Everything gated **skips with a reason; nothing fails.** The platform seam exists so macOS and
Linux can be ported later, and a suite that went red there would be telling a future porter
they had broken something they have not written yet.

Explicitly **not** assumed, even though the development machine has all four:

- **an NVIDIA GPU** — the GPU row, the VRAM figures and the thermal trigger are all driven from
  synthetic `GpuSample` objects, and the no-GPU degradation path is reached by *blocking the
  `pynvml` import* rather than by finding hardware without one. Both branches run everywhere.
- **no battery** — same approach; a synthetic `BatterySample` exercises the row on a desktop.
- **a device pixel ratio of 1.5** — no test asserts a device-pixel measurement.
- **`Segoe UI Variable Text`** — it is not installed on the dev box either, so the card is
  measured *relatively* (this layout is one row taller than that one) and never against the
  absolute 174 px / 192 px heights recorded in `PHASE1-LOG.md`.

On the development machine the whole suite runs with **zero skips**. That is worth knowing when
reading a CI result from elsewhere: skips there are expected, failures are not.

## Determinism

- **The flourish RNG is seeded.** `StateMachine._rng` is private with no public seeding seam, so
  the `make_machine` fixture in `conftest.py` replaces it with a `random.Random(DEFAULT_RNG_SEED)`.
  That reach-in happens in exactly one place, deliberately, so a reviewer has to forgive it once
  rather than fifteen times. An unseeded suite that passes nine runs in ten is worse than no
  suite: it teaches people to re-run instead of to look.
- **The clock is injected.** `StateMachine.evaluate(m, now)` takes its time as an argument, so a
  fifteen-minute idle timeout or a six-hour flourish cadence is a loop over floats. No test
  waits out a real timeout.
- **No test depends on execution order.** Widgets, animators and telemetry services are created
  per test and torn down by fixtures; module-level state that has to be patched
  (`config.STATE_PRESENTATION`, `config.FRAME_DRIFT_COMPENSATION`, the platform seam) is patched
  with `monkeypatch`, never assigned and restored by hand, so a mid-test failure cannot leak.
- **The user's real settings file is never touched.** A session-scoped autouse fixture points
  `$LC_PET_STAT_CONFIG_DIR` at a temp directory before anything else runs, and
  `test_integration.py` asserts that the path actually written to is not the production one.

---

## What is NOT covered

This matters more than the list of what is. An inflated coverage claim is worse than an honest
gap.

**The visual appearance of the card and the pet.** Rendering is checked *structurally* — the
paint completes without aborting, the correct set of rows exists, the card grows by exactly one
row's height when a row appears, it anchors below the pet and flips above it at the screen
edge. Nothing here knows whether the result is *good*: not the shadow falloff, not the surface
opacity over a bright terminal, not whether the rim carries enough lift on a dark desktop, not
the colour of anything, not the type hierarchy, not whether the pet's sprite is the right
sprite. Those were judged from rendered PNGs by eye during development and there is no
automated substitute. `PHASE1-LOG.md`'s "Known gaps" 8, 9 and 10 are all in this category and
all still open.

**macOS and Linux.** `src/core/platform/` is structurally seamed for them and completely
unimplemented, so the only thing tested is that a non-Windows run gets `NullBackend` and
degrades rather than crashing. Nothing exercises `NSWindow.setIgnoresMouseEvents_` or X11's
`XFixesSetWindowShapeRegion`, because neither exists yet. Note for whoever ports it: Wayland
has no protocol for a normal client to position its own surface or stay on top, which breaks
the pet's core interaction — that port needs `wlr-layer-shell`, not a translation of the X11
path.

**Multi-monitor behaviour.** `virtual_bounds()`, `clamp_to_desktop()` and `is_position_valid()`
are tested against whatever screens the running machine happens to have, and the
"restore onto a monitor that no longer exists" path is exercised only with an obviously invalid
coordinate. Dragging across a seam, a pet on a secondary display, per-monitor DPI changes, and
a monitor being *unplugged while the app runs* (`screenRemoved` → `revalidate_position`) are all
untested. Qt offers no way to synthesise a screen.

**Anything needing real sustained load.** Every threshold test feeds synthetic `Metrics`. The
suite never actually pegs a CPU, fills RAM, forces swap, or heats a GPU, so what is verified is
that *the rules respond correctly to numbers* — not that the numbers psutil reports under real
pressure are the ones the thresholds were designed for. `config.SWAP_HIGH = 30.0` in particular
is documented as a guess and is still unvalidated.

**The `working-together` state's end-to-end latency.** The state machine's side is tested
thoroughly with an injected clock and an explicitly-set availability list. The telemetry-side
sub-sampler — the 250 ms `GetLastInputInfo` loop, the activity ratio, the enter/exit latch — is
not driven end to end here; the scratch `check_working.py` measured it (~584 ms enter, ~2.3 s
exit) by faking the idle source, and that measurement has not been carried over because it is
sensitive to timer scheduling in a way the tolerances could not honestly absorb.

**Long-running behaviour.** Nothing runs for more than a few seconds. Timer drift over hours,
memory growth, the atlas `QImage` being retained for the life of the process, and behaviour
across a system sleep/resume are all unobserved.

**`--debug` and the log output.** Only `--validate`'s exit code is checked.

---

## Layout

```
tests/
  README.md              this file
  __init__.py            makes tests a package, so `from ._env import ...` works
  _env.py                EVERY environment assumption, and the skip markers
  _win32.py              an independent ctypes read-back of a window's ex-style
  conftest.py            QApplication, temp config dir, loaded SpriteSheet, Metrics factory
  test_config.py         tunables + the settings round-trip and its tolerant parsing
  test_sprite_loader.py  atlas validation, slicing, corrupt-atlas BLOCK handling
  test_animator.py       frame timing, drift, non-loop hold, pause/resume
  test_telemetry.py      shutdown, poll cadence, threading model, degradation
  test_state_machine.py  the rule ladder, hysteresis, dwell, flourishes  (the largest module)
  test_platform.py       the OS seam; the Windows half is marked and skippable
  test_overlay.py        the shared frameless/translucent/on-top flag recipe
  test_pet_window.py     geometry, rendering, click vs drag, native click-through state
  test_stats_card.py     row-set policy, copy degradation, anchoring, visibility
  test_tray.py           the menu telling the truth; icon construction
  test_integration.py    the real PetApp: wiring, greeting, --validate
  test_packaging.py      pixi.toml's environment contract (not a source module)
```

---

## Regression index

Each row is a defect that was actually hit during Phase 1 and the test that now guards it.
Source: `PHASE1-LOG.md` ("Defects found in the prototype", "Bugs found by running the code")
and the later work on flourishes, the stroll and optional rows.

| The bug | Guarded by |
| --- | --- |
| `QImage(bytes, …)` use-after-free + missing `bytesPerLine` | `test_sprite_loader.py::test_regression_frames_are_valid_qt_owned_pixmaps` |
| Pillow creeping back into the runtime path | `test_sprite_loader.py::test_regression_pillow_is_not_a_runtime_dependency`, `test_packaging.py::test_regression_runtime_environments_exclude_the_art_qa_stack` |
| `Qt.WindowType.SubWindow` on a top-level (and the masked-comparison trap) | `test_overlay.py::test_regression_the_window_type_is_tool_not_subwindow` |
| `QLabel` + `QVBoxLayout` 9 px margins → a 210×226 window | `test_pet_window.py::test_regression_the_window_is_exactly_the_atlas_frame_box` |
| `while True` telemetry thread that never exits | `test_telemetry.py::test_regression_stop_completes_well_inside_its_budget`, `…::test_regression_a_second_stop_is_a_no_op` |
| Blocking `psutil.cpu_percent(interval=1)` pinning the poll rate | `test_telemetry.py::test_regression_polling_is_not_pinned_by_a_blocking_cpu_percent`, `…::test_regression_cpu_percent_is_primed_so_readings_are_real` |
| No hysteresis — the pet flaps at a threshold | `test_state_machine.py::test_regression_hysteresis_absorbs_an_oscillation_at_the_threshold` |
| HWND recycled on `setWindowFlags`, losing `WS_EX_TRANSPARENT` | `test_pet_window.py::test_regression_click_through_survives_a_flag_change`, `…::test_click_through_survives_ten_toggles` |
| `QPoint`/`QPointF` in a glyph painter → `qFatal`, exit code 9 | `test_stats_card.py::test_regression_every_state_paints_without_aborting`, `test_integration.py::test_every_state_can_be_poked_through_paint_without_aborting` |
| `visibility_changed` wired straight to `set_paused` (opposite senses) | `test_integration.py::test_regression_the_pet_keeps_animating_while_it_is_visible` |
| The card never received its initial state | `test_integration.py::test_regression_the_card_shows_the_real_state_from_the_first_moment` |
| Two clock domains in the dwell timer | `test_state_machine.py::test_regression_dwell_uses_only_the_injected_clock` |
| Override expiry quantised to the 1500 ms poll | `test_integration.py::test_regression_a_short_flourish_ends_on_time_not_on_the_next_poll` |
| CPU blips restarting the 5-minute stroll countdown | `test_state_machine.py::test_regression_cpu_blips_do_not_starve_the_idle_stroll` |
| `idle-attentive` unreachable because the box idles at ~70 % RAM | `test_state_machine.py::test_regression_idle_attentive_is_reachable_at_seventy_percent_ram` |
| GPU structurally absent vs sample-failed | `test_stats_card.py::test_regression_a_structurally_absent_gpu_omits_the_row`, `…_a_failed_gpu_sample_keeps_a_greyed_row`, `…_the_two_gpu_absences_give_different_card_heights` |
| An absent optional row reaching the animator | `test_state_machine.py::test_regression_an_absent_optional_row_is_unreachable_by_every_route` |
| The card vanishing permanently if re-hovered mid-fade-out | `test_stats_card.py::test_regression_re_hovering_during_the_fade_out_restores_the_card` |
| **A single-shot expiry timer firing a hair early and dropping the hand-back entirely** — found by this suite, and fixed in `src/core/state_machine.py`; see below | `test_state_machine.py::test_regression_an_early_expiry_timer_re_arms_instead_of_dropping_the_handback` |

### One defect this suite found, and the source change it caused

`src/core/state_machine.py::_on_override_expired` is the only file under `src/` that this work
modified. It used to read:

```python
if self._active_override(now) is not None:
    return  # superseded by a longer override in the meantime
```

That guard cannot distinguish "a longer override superseded me" from "**I fired a fraction of a
millisecond early**". `QTimer`'s default `CoarseTimer` is documented to fire within ~5% either
side of its interval, and `_override_until` is read from `time.monotonic()` a moment *before*
`start()` is called, so the margin is thin.

`PHASE1-LOG.md` records the hand-back as "**+5 ms to +21 ms** past the hold". That measurement
is real but was taken on a quiet run. Re-measured inside a real `PetApp` here, the slack at
fire time ranged from **+0.3 ms** to +21 ms — and under the load of a full test session it went
negative.

Because the timer is single-shot, an early fire dropped the hand-back permanently — the pet
stayed on the flourish until the next telemetry sample, reintroducing the exact up-to-1500 ms
poll quantisation the timer was added to remove. On row 7 (`running-busy-play`), whose final
pose is deliberately tail-only with Layla walked out of frame, the visible symptom is an
apparently **empty pet window** for longer than the animation ran.

It reproduced in roughly **one run in four**. The fix re-arms the timer for the remaining time
instead of returning, which is correct for both of the cases the old guard conflated.

---

## Corrections to the scratch scripts these tests replace

Three assertions were carried over and found to be wrong. They are recorded here because a
suite that quietly "fixed" them would be hiding the more useful information.

1. **`check_config.py` asserted `len(config.STATE_PRESENTATION) == 9`.** Stale the moment
   `working-together` became a tenth state. Pinning a count was the wrong shape of assertion;
   `test_config.py` asserts `set(STATE_PRESENTATION) == set(STATES)` instead, which cannot go
   stale.

2. **`functional.py` asserted the card's anchoring against `card.x()` / `card.y()`.** The widget
   reserves a 29 px transparent margin on every side for its hand-painted shadow, and
   `_reposition` lays out the *visible* card, subtracting the margin once at the final `move()`.
   Those assertions are off by exactly one margin and fail against correct code.
   `test_stats_card.py` has a `visible_rect()` helper and asserts through `card_rect()` — which
   is the accessor `PHASE1-LOG.md` already warns is the one that means what it says.

3. **A tray tooltip cleared with `setToolTip("")` reads back as the action's own text.** Qt
   substitutes it. A test written as `assert action.toolTip() == ""` fails against correct
   code; the honest assertion is "no *reason* is being shown".

Two further notes, neither a correction:

- `check_stroll.py` already documents its own fix — counting *samples* where it meant
  *transitions into a flourish*, which made a 4–9 band meaningless against an unseeded RNG.
  That discipline is preserved verbatim in `flourish_starts()`.
- `test_telemetry.py` deliberately weakens one scratch assertion and says so: "sample[1]'s CPU
  is not identically 0.0" becomes "at least one sample after the first reports non-zero CPU".
  A genuinely idle machine can legitimately report 0.0 for one 500 ms window, and the weaker
  form still fails outright if the priming call is removed.
