# LC-pet-stat — Layla & Chico

A frameless, always-on-top desktop pet for Windows. Two cats sit on your desktop and react to
live system load through state-driven sprite animations. Hover them for a translucent stats
card; drag them anywhere; steer them from the tray.

![Layla and Chico reacting to live system load](docs/pets_showcase.gif)

---

## What it does

A background worker samples CPU, RAM, swap, GPU, battery and idle time without blocking the
interface. Most readings update every 1.5 seconds, disk usage is cached on a slower cadence, and
input activity is sampled every 250 ms. A state machine uses those readings to pick one of ten
animations. Its priority ladder, hysteresis and minimum dwell time let the pet settle into a
state instead of flickering at a threshold boundary.

| State | Frames | Roughly when |
| --- | --- | --- |
| `idle-attentive` | 8 | the calm default — CPU below 20% |
| `running-playful-roll` | 8 | moderate load, CPU 20–60% |
| `running-busy-play` | 8 | moderate load (one-shot flourish, holds its last frame) |
| `running-playful-jump` | 10 | heavy load — CPU above 80% or RAM above 85% |
| `working-together` | 8 | sustained keyboard or mouse activity while the system is calm |
| `waiting-dozy` | 8 | no input for 1 minute |
| `waiting-deep-sleep` | 8 | no input for 15 minutes |
| `blocked-grumpy` | 8 | temperature above 80 °C, or heavy swap use |
| `ready-pleased` | 8 | a sustained CPU spike drops back to calm — also the greeting on click/drag |
| `needs-input-sad` | 8 | no telemetry trigger; reachable from the tray's **Poke the pets** |

Idle isn't static: every five minutes the pet plays a weighted random flourish (a roll, a busy
play, a wave) so a quiet desktop still has some life in it.

**Input-activity detection is deliberately non-invasive.** It uses `GetLastInputInfo`, the same
Win32 call screensavers use, to read the timestamp of the most recent keyboard or mouse event.
There is no keyboard hook, `GetAsyncKeyState` polling, raw input capture, or record of what the
user typed. The application only knows that some input occurred.

## Features

- **Frameless translucent overlay** — `FramelessWindowHint` + `WA_TranslucentBackground` +
  `WindowStaysOnTopHint`, with `Qt.Tool` so it stays out of the taskbar and Alt-Tab.
- **Drag to move** — left-click drag, with click-vs-drag separated by the platform's own
  `startDragDistance()`. Position persists across restarts, per screen.
- **Hover stats card** — a semi-transparent panel showing CPU and RAM, with GPU, temperature
  and battery information when the hardware provides it. Click the pet to pin it open.
- **Click-through mode** — the pet becomes decorative and clicks pass to whatever is beneath it
  (`WS_EX_TRANSPARENT`). Only ever enabled when the tray icon exists, so there is always a way
  back out.
- **System tray** — toggle click-through, pet visibility, always-on-top and the stats card;
  reset position; poke the pets into any state; quit.
- **Alpha-aware dragging** — a drag can begin only on an opaque sprite pixel, so the pets cannot
  be picked up by grabbing the empty transparent gutter around them.

## Requirements

- Windows 10 or 11
- [pixi](https://pixi.sh)

macOS and Linux are not supported yet. The platform-specific code sits behind a small seam
(`src/core/platform/`) with a working null backend, so adding them is a contained job rather
than a rewrite — but it hasn't been done or tested.

## Running it

```powershell
pixi install          # materialize the environment from pixi.lock
pixi run -e pets pets # start the pets in the lean runtime environment
```

Useful variants:

```powershell
pixi run -e pets pets --validate   # check the sprite atlas and exit, no GUI
pixi run -e pets pets --debug      # verbose logging
```

## Development

```powershell
pixi run -e dev test                    # full suite (~60 s)
pixi run -e dev test -m "not slow"      # fast subset, skips the wall-clock tests
pixi run check                          # frame QA gate over every art sequence
pixi run qa                             # frame QA notebook in JupyterLab
```

There are three pixi environments, and the split is intentional:

| Environment | Holds | Why |
| --- | --- | --- |
| `pets` | PyQt6, psutil, numpy, nvidia-ml-py | the deliberately lean runtime |
| `dev` | the above plus pytest | the test suite, kept out of the runtime surface |
| default | napari, JupyterLab, Pillow | the art pipeline, which the app never touches |

Notably **Pillow is not a runtime dependency**. The atlas is decoded and sliced with Qt's own
`QImage`, which makes a whole class of buffer-lifetime bug unrepresentable rather than merely
fixed. A test asserts it never creeps back in.

## Project layout

```
src/
  main.py               entry point and wiring — no behaviour of its own
  config.py             every tunable: thresholds, timings, copy, presentation
  core/
    sprite_loader.py    atlas PNG + JSON -> per-frame QPixmaps
    animator.py         drift-free per-frame playback
    state_machine.py    the telemetry -> state ladder
    telemetry.py        worker-thread metrics sampling
    platform/           OS-specific window tricks behind an abstract seam
  ui/
    pet_window.py       the overlay window, drag, hit testing
    stats_card.py       the hover card
    tray.py             tray icon and menu
assets/
  layla-and-chico-pet-spritesheet.png/.json   the only two files needed at runtime
  gen-image/                                  the art pipeline, one folder per sequence
docs/
  BuildPlan.md          the original spec
  PHASE1-CONTRACT.md    the interface contract the modules were built against
  PHASE1-LOG.md         what was built, what broke, and what was measured
tests/                  runtime and integration test suite
tools/frame_qa.py       the art QA gate
```

## Sprites

The atlas is 1920×2080 — ten rows of ten 192×208 frames, 82 frames in use. Per-frame timings
live in the sidecar JSON and are read verbatim, so an animation can hold one frame longer than
another without any code change.

Art is generated per sequence through the pipeline in
[`docs/codex-animation-pipeline-guide.md`](docs/codex-animation-pipeline-guide.md) and gated by
`pixi run check` before it ships. New states can be added to the state machine *before* their art
exists — an unavailable row is simply never emitted, and starts working the day it lands in the
atlas.

## Status

Phase 1 implements the Windows desktop pet, telemetry worker, state machine, animation player,
stats card and tray controls. Known gaps and measured limitations are tracked in
[`docs/PHASE1-LOG.md`](docs/PHASE1-LOG.md).

The broader Phase 2 goal is to learn how desktop applications can work with agentic AI. A
future version may add a small chat window that connects Layla and Chico to LLMs and carefully
scoped MCP tools, including a personal vault. This would let the pets answer questions, use
relevant application context and eventually help with useful tasks while keeping their actions
visible and under the user's control. This work is exploratory and has not been implemented
yet.

## License

Licensed under the [Apache License 2.0](LICENSE).

---

**Layla 🐾  and Chico 🐾  are real cats. Any resemblance to your system load is intentional 🐈‍⬛.**
