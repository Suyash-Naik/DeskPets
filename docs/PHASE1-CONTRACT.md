# Phase 1 — Interface Contract

**This file is authoritative.** Three planning agents designed the three pillars independently;
where their plans disagreed, the decision below wins. Implementers must follow this document
over anything in their own brief.

Read `CLAUDE.md` first for repo conventions, then this.

---

## 0. Module ownership

```
src/
  __init__.py
  main.py                     # wiring only; no logic
  config.py                   # ALL tunables + Settings persistence  (written by the integrator)
  core/
    __init__.py
    sprite_loader.py          # atlas -> QPixmap frames
    animator.py               # QTimer frame player
    telemetry.py              # background metrics thread
    state_machine.py          # metrics -> state name
    platform/
      __init__.py             # backend selection
      base.py                 # abstract seam + NullBackend
      windows.py              # ctypes implementation
  ui/
    _overlay.py               # shared frameless/translucent/on-top recipe
    __init__.py
    pet_window.py             # the pet surface (render + input)
    stats_card.py             # the status card
    tray.py                   # QSystemTrayIcon + menu
```

No file outside `core/platform/` may contain `ctypes`, `sys.platform`, or `win32`.
No file outside `telemetry.py` may call `psutil`.
No file in `src/` may contain the literals `192`, `208`, `8`, `9`, `74` as sprite geometry —
all of it comes from the atlas JSON at runtime.

---

## 1. Conventions

- **Signals are `snake_case`.** (Planner 2 proposed camelCase; overruled for PEP 8 consistency.)
- Type hints everywhere; `from __future__ import annotations` at the top of every module.
- `logging.getLogger(__name__)` per module. No `print` outside a `__main__` block.
- Every public class/function gets a docstring naming *which thread* it may be called from.
- Dataclasses are `frozen=True, slots=True` unless they are mutable settings.

---

## 2. Resolved conflicts (decisions, with the losing option noted)

### 2.1 Row 7 `running-busy-play` is `loop: false` — who reverts?

**DECISION: the animator HOLDS the final frame. `StateMachine` owns all reversion.**

- `config.NONLOOP_END_POLICY = "hold"`.
- `Animator` emits `sequence_finished(name)` when a non-looping animation reaches its last
  frame, then stops its timer and leaves the frame on screen.
- `StateMachine` installs `running-busy-play` only ever as a **timed override** lasting
  `MODERATE_BUSY_PLAY_HOLD_MS` (= the atlas row's own `duration_ms`), after which its ladder
  re-evaluates and moves the pet back to `running-playful-roll` or wherever load now says.
- *Rejected:* the animator auto-falling back to `default_animation`. It is what the atlas's
  `compatibility.notes` literally suggests, but with the state machine also driving state you
  get two owners racing, and the pet ping-pongs. One owner, always.
- **Consequence the implementer must not "fix":** if the state machine is ever bypassed, a
  non-looping row will freeze on its last frame. That is intended. `sequence_finished` exists
  so a future owner can hook it.

### 2.2 Window flags

**DECISION: `PetWindow.set_flag_override(flag, enabled)` is the ONLY way to change flags.**

It must: capture geometry + visibility -> recompose `BASE_FLAGS | overrides` ->
`setWindowFlags` -> restore geometry -> `show()` if it was visible ->
**call `self._apply_native_state()`** (which re-applies the click-through ex-style, because
`setWindowFlags` destroys the HWND and Qt recomputes the ex-style from scratch).

`set_always_on_top(enabled)` is a thin wrapper over `set_flag_override`.
Nothing anywhere calls `setWindowFlags` / `setWindowFlag` directly except this one method.

### 2.3 Shared overlay recipe

**DECISION: `src/ui/_overlay.py` owns the flag recipe. Both windows use it.**

```python
BASE_FLAGS = (Qt.WindowType.FramelessWindowHint
              | Qt.WindowType.WindowStaysOnTopHint
              | Qt.WindowType.Tool
              | Qt.WindowType.NoDropShadowWindowHint)

def apply_overlay_flags(widget: QWidget, *, accept_focus: bool = False) -> Qt.WindowType:
    """Apply the frameless/translucent/always-on-top recipe. Returns the flag set applied.

    Must be called BEFORE the first show() -- WA_TranslucentBackground set after the native
    window exists forces Qt to destroy and recreate it, losing position and z-order.
    accept_focus=False adds WindowDoesNotAcceptFocus (the card wants this; the pet does not,
    because it needs a context menu).
    """
```

It sets: `WA_TranslucentBackground`, `WA_NoSystemBackground`, `WA_ShowWithoutActivating`,
`setAutoFillBackground(False)`. It does **not** set `WA_OpaquePaintEvent` (that suppresses the
per-paint erase and leaves ghost pixels from the previous sprite frame).

`Qt.WindowType.SubWindow` from the BuildPlan prototype is a **defect** — it is an MDI child
type, meaningless on a parentless top-level, and it does not give taskbar exclusion.
`Qt.Tool` is correct (`WS_EX_TOOLWINDOW`: no taskbar button, not in Alt-Tab).

**Trap when asserting on window type (measured):** window *types* share the low byte of the
flag word, so `flags & Qt.WindowType.Tool` is a meaningless test — `SubWindow` (0x12) and
`Tool` (0x0b) overlap, and `flags & SubWindow` is truthy on a `Tool` window. Always compare
`flags & Qt.WindowType.WindowType_Mask == Qt.WindowType.Tool`. This applies to
`set_flag_override` and to any test written against it. Window *hints* (Frameless,
StaysOnTop, …) are real bit flags and may be tested with `&` as normal.

### 2.4 Position signal

**DECISION: one signal.** `PetWindow.pet_moved = pyqtSignal(QRect)` — the pet's
**frame geometry in global coordinates**, emitted from `moveEvent` and once from `showEvent`.
The card anchors to it; the settings layer persists from it.
*Rejected:* separate `moved(QPoint)` / `dragFinished(QPoint)` position signals.
`drag_finished` survives but carries no position — it is purely a "persist now" nudge.

### 2.5 Render scale is an `int`

`Settings.scale: int = 1`, clamped to `1..MAX_RENDER_SCALE`. Fractional scaling of pixel art
is the exact thing this design avoids. *Rejected:* Planner 2's `scale: float`.

### 2.6 Click vs drag disambiguation

**DECISION: distance only, via `QApplication.startDragDistance()`** (the platform's own value,
10 px on Windows). No time limit. *Rejected:* Planner 3's `<4px AND <400ms` — a slow,
deliberate click is still a click, and a time limit makes it feel broken.

### 2.7 Pillow is not a runtime dependency

The atlas is decoded with `QImage(str(path))` and sliced with `QImage.copy(QRect)`.
Pillow is **not imported anywhere in `src/`**. It stays a pipeline/QA dependency.
This makes the BuildPlan use-after-free defect unrepresentable rather than merely fixed.

### 2.8 Deferred out of Phase 1

- Global hotkey as a second click-through escape hatch (the tray precondition in §5.3 covers
  safety). Note it in the log as a Phase 2 item.
- The control "pill" from the OpenAI reference image. `StatsCard.set_anchor_offset(px)` exists
  and is called with `0`; the pill can land later without touching the card.
- Crossfades between animations. Switching is immediate.
- ~~`nvidia-ml-py` / GPU metrics.~~ **Shipped.** Added during review; GPU load, VRAM and
  temperature are live on NVIDIA hardware. Nothing outside `pixi.toml` changed — the lazily
  probed provider picked it up, the card grew a GPU row, and `_hottest()` made the row-5
  thermal trigger live. The degradation path still matters on non-NVIDIA machines and is
  still exercised there.

---

## 3. `config.py`

Written by the integrator, **already complete**. Implementers import from it and must not add
constants to their own modules. If a value is missing, add it to `config.py` in the correct
banner section rather than inlining it.

Sections, in order: `PATHS & ATLAS`, `RENDERING`, `ANIMATION`, `TELEMETRY`, `THRESHOLDS`,
`HYSTERESIS & DWELL`, `STATE COPY`, `CARD PALETTE`, `CARD GEOMETRY`, `SETTINGS PERSISTENCE`.

---

## 4. Core interfaces (exact; implementers must match these signatures)

### 4.1 `core/sprite_loader.py`

```python
class SpriteLoadError(RuntimeError): ...
class AssetsMissingError(SpriteLoadError): ...
class AtlasValidationError(SpriteLoadError): ...

@dataclass(frozen=True, slots=True)
class Frame:
    index: int
    pixmap: QPixmap
    source_rect: QRect
    duration_ms: int
    phase: str
    offset: QPoint          # bottom-centred within Animation.box; (0,0) for today's atlas
    content_rect: QRect     # opaque-pixel bbox, frame-local; full rect if unknown

@dataclass(frozen=True, slots=True)
class Animation:
    name: str
    row: int
    sequence: str
    loop: bool
    duration_ms: int
    frames: tuple[Frame, ...]
    box: QSize
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[Frame]: ...
    def __getitem__(self, index: int) -> Frame: ...     # no wrapping; raises IndexError
    def frame_at(self, index: int) -> Frame: ...        # wraps modulo len()

@dataclass(frozen=True, slots=True)
class AtlasIssue:
    severity: str           # "BLOCK" | "WARN"   (same vocabulary as tools/frame_qa.py)
    check: str
    message: str

@dataclass(frozen=True, slots=True)
class AtlasReport:
    json_path: Path
    png_path: Path
    image_size: QSize
    frame_count: int
    animation_names: tuple[str, ...]
    issues: tuple[AtlasIssue, ...]
    @property
    def ok(self) -> bool: ...
    def render(self) -> str: ...

class SpriteSheet:
    animations: Mapping[str, Animation]
    default_animation: str
    frame_box: QSize
    report: AtlasReport
    source_image: QImage | None

    def __contains__(self, name: str) -> bool: ...
    def __getitem__(self, name: str) -> Animation: ...   # KeyError lists known names
    def __len__(self) -> int: ...
    def names(self) -> tuple[str, ...]: ...
    def get(self, name: str, default: Animation | None = None) -> Animation | None: ...
    def require(self, names: Sequence[str]) -> None: ...
    def frame(self, name: str, index: int) -> QPixmap: ...   # convenience for tray.py
    def alpha_at(self, animation: str, frame_index: int, point: QPoint) -> int: ...

def validate_atlas(json_path: Path | None = None,
                   png_path: Path | None = None) -> AtlasReport: ...
def load_spritesheet(json_path: Path | None = None,
                     png_path: Path | None = None,
                     *, strict: bool | None = None) -> SpriteSheet: ...
```

`validate_atlas` is `QImage`-only and needs **no** `QApplication` (so the `__main__` CLI works
headless). `load_spritesheet` builds `QPixmap`s and is **GUI-thread only**.

### 4.2 `core/animator.py`

```python
class Animator(QObject):
    frame_changed     = pyqtSignal(QPixmap, QPoint)   # pixmap, draw offset
    state_changed     = pyqtSignal(str, str)          # previous, new
    sequence_finished = pyqtSignal(str)               # a loop=False row hit its last frame

    def __init__(self, sheet: SpriteSheet, parent: QObject | None = None) -> None: ...
    def play(self, name: str, *, restart: bool = False,
             defer_to_loop_end: bool = False) -> bool: ...
    @pyqtSlot(str)
    def set_state(self, name: str) -> None: ...       # slot for StateMachine.state_changed
    def current_state(self) -> str: ...
    def current_index(self) -> int: ...
    def is_playing(self) -> bool: ...
    def set_paused(self, paused: bool) -> None: ...
    def stop(self) -> None: ...
    def start(self) -> None: ...
```

- `play()` returns `False` + logs a warning for an unknown name; it must never raise. A
  telemetry mapper emitting a state the atlas lacks must not crash the pet.
- Timing: single-shot `QTimer` rescheduled per frame against an **absolute deadline** on a
  `QElapsedTimer`, so per-frame `duration_ms` is honoured with no drift. Use
  `Qt.TimerType.PreciseTimer` — the default `CoarseTimer` has ~15 ms granularity on Windows,
  a ~17 % error on a 90 ms frame.
- Switching state is immediate and synchronous: reset index to 0, emit `frame_changed` right
  away (do **not** wait for the next timeout, or the old sprite lingers up to 260 ms).
- Non-loop end: emit `sequence_finished` **before** applying the hold, and guard re-entrancy
  (if a slot already called `play()`, skip the hold).

### 4.3 `core/telemetry.py`

Threading model: **worker object moved to a plain `QThread`, driven by a `QTimer` inside the
worker's own event loop.** Not a `QThread` subclass — a subclass's slots run in the *creating*
thread, which silently reintroduces cross-thread psutil calls.

```python
@dataclass(frozen=True, slots=True)
class GpuSample:
    name: str
    load_percent: float | None
    vram_used_bytes: int | None
    vram_total_bytes: int | None
    vram_percent: float | None
    temp_c: float | None

@dataclass(frozen=True, slots=True)
class BatterySample:
    percent: float
    plugged: bool
    secs_left: int | None

@dataclass(frozen=True, slots=True)
class Metrics:
    timestamp: float                 # time.monotonic()
    cpu_percent: float
    ram_percent: float
    ram_used_bytes: int
    ram_total_bytes: int
    swap_percent: float
    disk_percent: float | None
    cpu_temp_c: float | None         # ALWAYS None on Windows in Phase 1
    gpu: GpuSample | None            # None = structurally unavailable
    battery: BatterySample | None    # None = no battery present
    idle_seconds: float | None       # None = platform seam cannot supply it
    @classmethod
    def placeholder(cls) -> "Metrics": ...

class TelemetryService(QObject):
    metrics_ready = pyqtSignal(object)     # payload: Metrics
    def __init__(self, parent=None, interval_ms: int = config.POLL_INTERVAL_MS) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...            # idempotent
    def set_interval(self, ms: int) -> None: ...
    @property
    def last_metrics(self) -> Metrics | None: ...
```

Mandatory fixes to the prototype:
- **No `while True`.** A `QTimer` on the worker's event loop. `stop()` does
  `invokeMethod(worker, "stop", BlockingQueuedConnection)` -> `thread.quit()` ->
  `thread.wait(config.TELEMETRY_STOP_TIMEOUT_MS)`.
- **`psutil.cpu_percent(interval=None)`**, always. `prime()` makes one discarded call first
  (the first call has no reference point and returns 0.0). The poll rate comes solely from the
  timer. Document the module-global invariant: *nothing else in this process may call
  `cpu_percent()`* — a second caller corrupts the shared reference point.
- One bad `sample()` logs at ERROR and skips that tick; it must not kill the poller.
- GPU + temperature are **lazily probed providers** behind Protocols, created once inside
  `prime()` on the worker thread. `pynvml` is imported inside the probe, never at module level.
  Never substitute a synthetic temperature — `None` means unknown.

### 4.4 `core/state_machine.py`

```python
STATES: Final[tuple[str, ...]] = (
    "idle-attentive", "running-playful-roll", "running-playful-jump",
    "waiting-deep-sleep", "waiting-dozy", "blocked-grumpy",
    "needs-input-sad", "running-busy-play", "ready-pleased",
)

class StateMachine(QObject):
    state_changed = pyqtSignal(str)        # emitted ONLY on an actual change
    def __init__(self, parent: QObject | None = None) -> None: ...
    @pyqtSlot(object)
    def on_metrics(self, metrics: Metrics) -> None: ...
    @pyqtSlot(str, int)
    def trigger_state(self, state: str, duration_ms: int = 5000) -> None: ...
    @property
    def state(self) -> str: ...
    def evaluate(self, m: Metrics, now: float) -> str: ...   # PURE; `now` injected for tests
```

- Rule ladder, first match wins: timed override -> `blocked-grumpy` -> `running-playful-jump`
  -> `waiting-deep-sleep` -> `waiting-dozy` -> moderate (`running-playful-roll`) ->
  `idle-attentive` (unconditional terminal default).
- **`idle-attentive` is the terminal default and is NOT gated on RAM.** Measured: this machine
  idles at 70.5 % RAM, so BuildPlan's literal `RAM < 60%` would make the default state
  unreachable. `RAM_BUSY_HINT` feeds the card's subtitle copy instead. Comment this in code.
- Hysteresis: every numeric gate is a `Band(enter, exit)` Schmitt trigger.
  `Band.holds(None, latched) -> False` — a missing metric never triggers a state, which is
  exactly what makes the absent Windows temperature safe.
- Dwell: `MIN_STATE_DWELL_MS` suppresses lateral/downward moves, bypassed when
  `PRIORITY[candidate] < PRIORITY[current]` so alarms are never delayed.
- Rows 1 vs 7 tiebreak: `running-playful-roll` is the resident moderate state;
  `running-busy-play` is a periodic one-shot override (see §2.1).
- Rows 3 vs 4 tiebreak: `waiting-dozy` at 5 min, `waiting-deep-sleep` at 15 min. Both
  additionally gated on `not BAND_CPU_MODERATE.holds(cpu)` — a machine at 90 % while the user
  is away should not show a sleeping pet.
- `ready-pleased`: edge-detected via an arm/fire latch, installed as a timed override.
- `needs-input-sad`: **no automatic trigger.** Reachable only via `trigger_state`.

### 4.5 `core/platform/`

```python
# base.py
WindowHandle = int      # int(QWidget.winId())

class PlatformBackend(ABC):
    name: str = "unknown"
    @abstractmethod
    def supports_click_through(self) -> bool: ...
    @abstractmethod
    def set_click_through(self, handle: WindowHandle, enabled: bool) -> bool: ...
    def set_always_on_top(self, handle: WindowHandle, enabled: bool) -> bool: return False
    def get_idle_seconds(self) -> float | None: return None
    def unsupported_reason(self) -> str: return ""

class NullBackend(PlatformBackend): ...     # logs ONCE, never raises, returns False

# __init__.py  -- callers never see backend objects
def backend() -> PlatformBackend: ...
def backend_name() -> str: ...
def supports_click_through() -> bool: ...
def unsupported_reason() -> str: ...
def native_handle(widget) -> WindowHandle: ...          # int(widget.winId()), NEVER cached
def set_click_through(widget, enabled: bool) -> bool: ...
def set_always_on_top(widget, enabled: bool) -> bool: ...
def get_idle_seconds() -> float | None: ...             # thread-safe; telemetry calls it
```

`set_click_through` returns `True` only if applied **and verified by read-back**. It must never
raise — every OS failure is logged and returned as `False`.

Windows specifics that implementers get wrong:
- `argtypes`/`restype` are **mandatory**. Without them ctypes marshals Python ints as 32-bit
  C `int`; an HWND on win64 can exceed 32 bits and gets silently truncated, targeting a
  nonexistent or *different* window. `LONG_PTR = ctypes.c_ssize_t`.
- `SetWindowLongPtrW`/`GetWindowLongPtrW` are only *exported* on 64-bit user32 — on 32-bit they
  are macros, so `getattr(..., None) or _user32.SetWindowLongW` fallback is required.
- `GetWindowLongPtrW` returns 0 both for "ex-style is zero" and for failure. Open the DLL with
  `use_last_error=True`, `ctypes.set_last_error(0)` before the call, check
  `ctypes.get_last_error()` when the result is 0.
- Never clear `WS_EX_LAYERED` on disable — Qt may own that bit for the translucent surface.
- `ADD_LAYERED` is a module constant, default **False**. `WS_EX_TRANSPARENT` alone already
  removes the window from hit-testing on DWM-composited Win10/11. Only set it `True` if
  pass-through is observed to fail — and if it is set, `SetLayeredWindowAttributes` **must** be
  called immediately after, or the window becomes invisible (MSDN: a window that has just had
  `WS_EX_LAYERED` added is not displayed until then).
- `get_idle_seconds`: `GetTickCount64` (not `GetTickCount`, which wraps at 49.7 days);
  `LASTINPUTINFO.dwTime` is a 32-bit tick so the subtraction must be wrap-safe
  (`& 0xFFFFFFFF`); `GetLastInputInfo` returning 0 means failure -> return `None`, **never**
  `0.0` (which would read as "user is active" and permanently disable the sleep states).

---

## 5. UI interfaces

### 5.1 `ui/pet_window.py`

```python
class PetWindow(QWidget):
    pet_moved            = pyqtSignal(QRect)   # global frame geometry; moveEvent + showEvent
    clicked              = pyqtSignal()        # press+release below the drag threshold
    drag_started         = pyqtSignal()
    drag_finished        = pyqtSignal()        # "persist now" nudge; carries no position
    hover_changed        = pyqtSignal(bool)    # False unconditionally while click-through is on
    interactivity_changed = pyqtSignal(bool)   # False = click-through just came on
    screen_changed       = pyqtSignal()
    visibility_changed   = pyqtSignal(bool)    # True == shown. NOTE: inverted vs set_paused

    def __init__(self, sheet: SpriteSheet, parent: QWidget | None = None) -> None: ...

    # rendering
    @pyqtSlot(QPixmap, QPoint)
    def set_frame(self, pixmap: QPixmap, offset: QPoint = QPoint(0, 0)) -> None: ...
    def render_scale(self) -> int: ...
    def set_render_scale(self, scale: int) -> None: ...
    def frame_box(self) -> QSize: ...

    # flags -- the ONLY mutation path
    def set_flag_override(self, flag: Qt.WindowType, enabled: bool) -> None: ...
    def window_flags(self) -> Qt.WindowType: ...
    def set_always_on_top(self, enabled: bool) -> None: ...

    # click-through
    def set_click_through(self, enabled: bool) -> bool: ...   # True == applied AND verified
    def is_click_through(self) -> bool: ...
    def is_interactive(self) -> bool: ...

    # geometry
    def restore_position(self, settings: Settings) -> None: ...
    def reset_position(self) -> None: ...
    def revalidate_position(self) -> None: ...
    def current_position(self) -> QPoint: ...

    # hit-testing
    def alpha_at_local(self, pos: QPoint) -> int: ...
    def is_opaque_at(self, pos: QPoint, threshold: int = 16) -> bool: ...
```

- **No `QLabel`, no layout.** A custom `paintEvent` drawing the pixmap directly. BuildPlan's
  `QLabel` + `QVBoxLayout` silently adds the style's 9 px content margins on all four sides,
  making the window 210x226 with the pet off-centre. Real defect.
- `paintEvent` sets `SmoothPixmapTransform` to `config.SMOOTH_SCALING` (False) — nearest
  neighbour. The atlas's `transform.filter` is `"nearest"` and the art was already resampled
  0.6x; smoothing now would blur a resample the pipeline deliberately kept hard.
- Drag anchors to `_press_origin` captured once at press, never accumulated deltas (no drift).
  Threshold is `QApplication.startDragDistance()`. Use `event.buttons() & LeftButton` for the
  mask, not `==` (which breaks when a modifier or second button is held).
- `_apply_native_state()` is the single place the ex-style is written; it re-reads
  `int(self.winId())` every time. Called from `showEvent` (via `QTimer.singleShot(0, ...)`, so
  Qt has finished mapping), from `set_flag_override`, and from `screenChanged`.
- **MEASURED, and it contradicts the obvious assumption: re-apply UNCONDITIONALLY.** Do not
  gate the re-apply on the handle having changed, and do not cache the handle or the ex-style.
  Observed on this machine: after `hide(); setWindowFlag(...); show()`, `winId()` returned the
  *identical* value `0x9318ac` while the ex-style had been rebuilt from `0x100` to `0x0` —
  Windows recycled the handle value into the recreated window. A cache keyed on handle identity
  would therefore look valid while being stale, and would not show up in testing. Logging the
  handle is fine as a diagnostic; branching on it is not.
- Hit-testing uses alpha, not the rect: every frame has a ~20-25 px fully transparent gutter
  below the pet's feet, so rect hit-testing feels wrong. `mousePressEvent` ignores presses
  where `not is_opaque_at(pos)`.
- Module-level pure helpers (unit-testable with no window): `virtual_bounds()`,
  `default_position(size, screen=None)`, `clamp_to_desktop(pos, size)` (loose — used *during*
  a drag, permits crossing monitor seams), `is_position_valid(pos, size)` (strict — used when
  *restoring*, so a pet never comes back on a monitor that no longer exists).

### 5.2 `ui/stats_card.py`

```python
class CardVisibility(enum.Enum):
    HIDDEN = 0; PEEKING = 1; PINNED = 2

class StatsCard(QWidget):
    pinned_changed = pyqtSignal(bool)
    def __init__(self, parent: QWidget | None = None) -> None: ...
    @pyqtSlot(object)
    def on_metrics(self, metrics: Metrics) -> None: ...
    @pyqtSlot(str)
    def on_state_changed(self, state: str) -> None: ...
    @pyqtSlot(QRect)
    def set_anchor(self, anchor: QRect) -> None: ...
    def set_anchor_offset(self, px: int) -> None: ...
    @pyqtSlot(bool)
    def set_hovered(self, hovered: bool) -> None: ...
    @pyqtSlot(bool)
    def set_pinned(self, pinned: bool) -> None: ...
    @pyqtSlot()
    def toggle_pinned(self) -> None: ...
    def visibility_state(self) -> CardVisibility: ...
```

- A **sibling top-level**, not a child of `PetWindow` (a translucent child would be clipped to
  the pet's 192x208 box). Uses `apply_overlay_flags(self, accept_focus=False)`.
- **Zero child widgets.** One `paintEvent` draws surface, glyph, all text, all meters.
  Rationale: a Qt stylesheet `border-radius` does not clip children, and its corner
  antialiasing on a translucent top-level blends against transparent black, giving a dark
  fringe on exactly the soft edge the design depends on. `QPainter` + `QPainterPath` with
  `Antialiasing` composites correctly.
- Degradation policy: **omit** the row when the capability is structurally absent
  (`metrics.gpu is None`, `metrics.battery is None`); **keep it greyed with `n/a`** when the
  capability exists but this sample failed. Emptiness reads as breakage; absence reads as
  design. Temperature is an inline suffix on the CPU row (`84% · 62°C`), never its own row, so
  it costs no space when absent.
- **On the dev machine the card is exactly two rows, CPU and RAM.** Tune the visuals at that
  size — it is the default there, not a degraded case.
- Relayout + `resize(sizeHint())` only when the *set* of visible rows changes (tracked as a
  frozenset), or the card twitches every poll.
- Visibility: hover-to-peek (250 ms show delay, 700 ms hide delay, hide timer cancelled if the
  cursor enters the card itself so it can be read), click-to-pin, tray-to-pin. Fade via
  `QPropertyAnimation` on `windowOpacity`; call `hide()` from the fade-out's `finished` signal
  so it is genuinely hidden, not a 0-opacity ghost still taking hit-tests.
- Copy comes from `config.STATE_PRESENTATION` via `str.format_map` over a mapping that returns
  an em-dash for unknown keys, so a typo in user-edited copy degrades instead of crashing the
  paint. Unknown state names fall back to `DEFAULT_PRESENTATION`, never raise.
- Two colours maximum on the card: the state accent, plus `ACCENT_DANGER` for a metric over its
  own high threshold. That restraint is most of why the reference looks calm.

### 5.3 `ui/tray.py`

```python
class TrayIcon(QSystemTrayIcon):
    click_through_toggled    = pyqtSignal(bool)
    pet_visibility_toggled   = pyqtSignal(bool)
    card_visibility_toggled  = pyqtSignal(bool)
    always_on_top_toggled    = pyqtSignal(bool)
    reset_position_requested = pyqtSignal()
    poke_requested           = pyqtSignal(str)     # a state name, for manual QA
    quit_requested           = pyqtSignal()
    activated_by_user        = pyqtSignal()        # double-click

    def __init__(self, icon: QIcon, parent: QObject | None = None) -> None: ...
    def set_click_through_checked(self, on: bool) -> None: ...
    def set_pet_visible_checked(self, on: bool) -> None: ...
    def set_card_visible_checked(self, on: bool) -> None: ...
    def set_always_on_top_checked(self, on: bool) -> None: ...
    def set_click_through_available(self, available: bool, reason: str = "") -> None: ...
    def set_status_text(self, text: str) -> None: ...
    def notify(self, title: str, body: str, msecs: int = 4000) -> None: ...
    def shutdown(self) -> None: ...

def build_tray_icon(sheet: SpriteSheet | None = None) -> QIcon: ...
def install_tray(parent: QObject, sheet=None, retries: int = 5) -> TrayIcon | None: ...
```

Menu:
```
[x] Click-through            (checkable; disabled + tooltip when unsupported)
[x] Show pet                 (checkable)
[ ] Show stats card          (checkable)
[x] Always on top            (checkable)
    Poke the pets         >  (submenu: one item per STATES entry -> poke_requested)
    ---
    Reset position
    ---
    Quit
```

- Every `set_*_checked` wraps the `QAction` in `QSignalBlocker` so pushing state back never
  re-emits. The tray reflects **reality, not intent** — if a toggle fails, the checkbox
  follows the failure.
- Keep strong references: `self._menu`, and each `QAction` on `self`. A `QMenu`/`QAction`
  referenced only by the menu is a live GC hazard in PyQt and yields an intermittently empty
  menu. `self._menu.setToolTipsVisible(True)` so disabled-reason tooltips are readable.
- **Click-through hard precondition:** `MainApp` refuses to enable it when the tray is absent
  or hidden, and forces a restored `click_through: true` back to `false` at startup with a log
  line. There must be no code path producing a click-through pet with no escape hatch. On
  enable, `notify(...)` fires a balloon naming the tray as the way back, and the tray tooltip
  becomes `"LC-pet-stat — click-through ON"`.
- Double-click (`ActivationReason.DoubleClick`) shows/raises the pet **and turns click-through
  off** — a second escape hatch for one line. `Trigger` (single left click) is ignored;
  hijacking it makes the right-click menu feel unreliable.
- Icon: frame 0 of `idle-attentive` from the atlas, alpha-trimmed
  (`QRegion(pixmap.mask()).boundingRect()`), then `addPixmap` at 16/24/32/48 — Windows picks
  16 for the tray and 32 for the balloon, and letting Qt downscale a 192x208 frame to 16 gives
  mush. Falls back to a painted "LC" badge if the atlas is unavailable; the tray must never
  prevent startup.
- `install_tray` retries `isSystemTrayAvailable()` on a 2 s `QTimer` up to `retries` times —
  at login the app can legitimately start before Explorer — then logs a warning and returns
  `None`. The app still runs, with click-through permanently unavailable.

### 5.4 `main.py`

Wiring only. Required order:

```python
QGuiApplication.setHighDpiScaleFactorRoundingPolicy(PassThrough)   # BEFORE QApplication
app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)                               # BEFORE any window

settings  = load_settings().clamped()
sheet     = load_spritesheet()
sheet.require(STATES)
pet       = PetWindow(sheet)
animator  = Animator(sheet)
card      = StatsCard()
machine   = StateMachine()
telemetry = TelemetryService(interval_ms=config.POLL_INTERVAL_MS)
tray      = install_tray(app_obj, sheet=sheet)

animator.frame_changed.connect(pet.set_frame)
machine.state_changed.connect(animator.set_state)
machine.state_changed.connect(card.on_state_changed)
telemetry.metrics_ready.connect(machine.on_metrics)
telemetry.metrics_ready.connect(card.on_metrics)
pet.pet_moved.connect(card.set_anchor)
pet.hover_changed.connect(card.set_hovered)
pet.clicked.connect(card.toggle_pinned)
# NOT a direct connect -- the senses are opposite (measured: a direct connect freezes the pet
# the instant it becomes visible). visibility_changed(True) == shown; set_paused(True) == stop.
pet.visibility_changed.connect(lambda visible: animator.set_paused(not visible))
app.aboutToQuit.connect(telemetry.stop)
app.aboutToQuit.connect(lambda: save_settings(settings))

pet.restore_position(settings)
pet.show()                      # emits pet_moved -> card anchors correctly before first peek
animator.play(sheet.default_animation)
card.on_state_changed(machine.state)   # state_changed only fires on a CHANGE, and the machine
                                       # starts in its default state -- without this push the
                                       # card sits on DEFAULT_PRESENTATION until the first
                                       # real transition.
telemetry.start()
```

`--debug` flag: verbose logging. `--validate`: run `validate_atlas()`, print the report, exit
without a GUI.

Quit sequence on `quit_requested`: save settings -> `telemetry.stop()` -> `tray.shutdown()`
(explicitly `hide()` the icon, or Windows leaves a ghost in the notification area until the
user hovers it) -> `QApplication.quit()`.

---

## 6. Verification each implementer must do

CLAUDE.md's rule: **run the artifact before calling it done.** Reading it back is not enough.

- Every module must at minimum import cleanly:
  `pixi run -e pets python -c "import src.<module>"`.
- `sprite_loader.py` ships a `__main__` CLI: `pixi run -e pets python -m src.core.sprite_loader`
  must print a clean report against the real atlas and exit 0.
- `state_machine.py` and `config.py` are pure Python — exercise them from a `__main__` or a
  scratch script, not by inspection.
- `windows.py` must be validated against a real HWND (apply, read back, confirm the bit) before
  it is wired to the pet.
