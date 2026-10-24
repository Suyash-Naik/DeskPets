"""Tunables and persisted settings for the Layla & Chico desktop pet.

Every threshold, interval, colour and frame-timing default lives here -- never inline in
widget code (CLAUDE.md, "Working conventions").

The file is organised into banner-delimited sections. Add new constants to the section they
belong to rather than to the module that consumes them.

Deliberately Qt-free below the ``SETTINGS PERSISTENCE`` banner, so settings can be unit-tested
without a ``QApplication``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, Mapping

log = logging.getLogger(__name__)


# =============================================================================
# PATHS & ATLAS
# =============================================================================

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
ASSETS_DIR: Final[Path] = PROJECT_ROOT / "assets"
ATLAS_JSON: Final[Path] = ASSETS_DIR / "layla-and-chico-pet-spritesheet.json"

#: ``None`` => resolve the PNG from the JSON's own ``image`` field, relative to the JSON's
#: directory. The BuildPlan prototype used a bare filename, which only worked if the process
#: happened to be launched from ``assets/``.
ATLAS_PNG: Final[Path | None] = None

SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset({1})

#: Verify the PNG against the JSON's ``image_sha256``. ~5 ms for 1.5 MB.
VERIFY_ATLAS_SHA256: Final[bool] = True

#: When True, a sha mismatch or a count mismatch is fatal instead of a warning. Left False so
#: art iteration is never blocked by a stale hash -- but the mismatch is always logged, because
#: a repacked PNG can invalidate every frame rect in the JSON.
STRICT_ATLAS: Final[bool] = False

#: Retain the source QImage after slicing so alpha hit-testing works. ~14 MB.
KEEP_ATLAS_IMAGE: Final[bool] = True

#: Compute per-frame opaque-pixel bounds (needs numpy; falls back to the full rect).
COMPUTE_CONTENT_BOUNDS: Final[bool] = True


# =============================================================================
# RENDERING
# =============================================================================

#: Integer logical upscale. Fractional scaling of pixel art is exactly what this design
#: avoids -- the art was already resampled 0.6x with a nearest filter by the atlas packer.
RENDER_SCALE: Final[int] = 1
MAX_RENDER_SCALE: Final[int] = 4

#: Never True for pixel art. Escape hatch only.
SMOOTH_SCALING: Final[bool] = False

WINDOW_START_MARGIN_PX: Final[int] = 24
WINDOW_START_CORNER: Final[str] = "bottom-right"

#: How much of the pet must remain on some screen while dragging.
MIN_VISIBLE_PX: Final[int] = 64

#: Re-assert always-on-top every N ms. 0 disables. Only helps recoverable cases (Explorer
#: restart, a UAC prompt); a fullscreen exclusive D3D game will still cover the pet, and that
#: is correct behaviour, not a bug to fight.
ALWAYS_ON_TOP_REASSERT_MS: Final[int] = 0


# =============================================================================
# ANIMATION
# =============================================================================

MIN_FRAME_MS: Final[int] = 16

#: Used only when a frame's ``duration_ms`` is absent or <= 0 in the atlas.
DEFAULT_FRAME_MS: Final[int] = 120

FRAME_DRIFT_COMPENSATION: Final[bool] = True

#: Qt's default CoarseTimer has ~15 ms granularity on Windows -- a ~17% error on a 90 ms
#: frame, which is visible. PreciseTimer uses a multimedia timer; negligible cost at ~8 fps.
PRECISE_ANIMATION_TIMER: Final[bool] = True

#: "hold" | "fallback". MUST stay "hold": StateMachine is the single owner of state, and it
#: reverts a non-looping row by letting its timed override expire. If the animator also
#: reverted, the two would race and the pet would ping-pong. See docs/PHASE1-CONTRACT.md 2.1.
NONLOOP_END_POLICY: Final[str] = "hold"

#: Pause the animation timer when the pet is hidden, so a tray-minimised pet costs nothing.
PAUSE_WHEN_HIDDEN: Final[bool] = True


# =============================================================================
# TELEMETRY
# =============================================================================

POLL_INTERVAL_MS: Final[int] = 1500          # BuildPlan says 1-2 s; 1.5 s is the midpoint
POLL_INTERVAL_MIN_MS: Final[int] = 500
POLL_INTERVAL_MAX_MS: Final[int] = 10_000

#: A short first tick so the card populates almost immediately. The reading is genuine, just
#: noisier than a full-interval sample.
FIRST_SAMPLE_DELAY_MS: Final[int] = 250

#: disk_usage() touches the filesystem, and the number barely moves. ~30 s cadence.
DISK_SAMPLE_EVERY_N_TICKS: Final[int] = 20
SYSTEM_DRIVE: Final[str] = "C:/"

SAMPLE_SLOW_WARN_MS: Final[int] = 250

#: After this many consecutive GPU provider errors, downgrade to the null provider
#: permanently. A driver that has started throwing will keep throwing, and we must not pay an
#: exception per tick or risk a hang inside the poll.
GPU_PROVIDER_MAX_CONSECUTIVE_ERRORS: Final[int] = 3

TELEMETRY_STOP_TIMEOUT_MS: Final[int] = 2000

# --- sustained-interaction detection (the signal behind `working-together`) ---------------
# WHAT THIS ACTUALLY MEASURES: whether the user is interacting with the machine at all. The
# only source is Windows' `GetLastInputInfo`, read through the platform seam, which reports
# how long ago the last input event of *any* kind arrived. A keystroke, a mouse move, a scroll
# and a trackpad graze are indistinguishable to it, so this is **sustained interaction**, never
# "typing" -- nothing here or downstream may be named as if it were.
#
# Deliberately not used, even though all three are hook-free and would give finer detail:
# `SetWindowsHookEx`/`WH_KEYBOARD_LL` (a keylogger by any reasonable definition),
# `GetAsyncKeyState` polling and Raw Input with `RIDEV_INPUTSINK` (both can reconstruct
# keystrokes, and both are flagged by AV heuristics). A desktop pet does not get to read input.

#: How often the telemetry worker re-reads the idle counter, on its own timer on the worker's
#: event loop. The 1500 ms `POLL_INTERVAL_MS` tick cannot measure input cadence: at that
#: resolution one stray mouse twitch and a solid 1.5 s of work look identical. 250 ms gives six
#: sub-samples per poll, which is enough to tell them apart and costs one cheap syscall each
#: (`GetLastInputInfo` reads a tick the kernel already maintains; it scans nothing).
INPUT_SAMPLE_INTERVAL_MS: Final[int] = 250

#: Slack in the per-sub-sample test "did any input arrive since the previous sub-sample?".
#: That test compares the idle reading against the **measured** elapsed time (a QElapsedTimer),
#: never against the nominal 250 ms: a timer that runs late -- and it will -- would otherwise
#: make real input look older than the gap and bias the activity ratio downward. The epsilon
#: only has to absorb quantisation: `GetTickCount64` and `LASTINPUTINFO.dwTime` are both ~10-16
#: ms ticks, so their difference can be out by about one tick in either direction.
INPUT_SAMPLE_EPSILON_S: Final[float] = 0.05


# =============================================================================
# THRESHOLDS
#
# Numbers come from CLAUDE.md's cleaned state table (20 / 60 / 80 / 85) and my own measurements on my PC
# =============================================================================

CPU_IDLE_MAX: Final[float] = 20.0
CPU_MODERATE_MIN: Final[float] = 1.0

#: Documented nominal upper edge of the moderate band; feeds copy, NOT a state gate. The spec
#: table leaves 60-80% undefined, so once the high-load rule declines, 60-80 falls through to
#: moderate. Adding a third gate at 60 would only create another boundary to flap at.
CPU_MODERATE_MAX: Final[float] = 60.0

CPU_HIGH: Final[float] = 80.0
RAM_HIGH: Final[float] = 55.0

#: Copy only -- NOT a state gate. Measured on the dev machine: RAM sits at ~70% while idle, so
#: BuildPlan's literal "CPU < 20% AND RAM < 60%" would make `idle-attentive` -- the DEFAULT
#: state -- unreachable. `idle-attentive` is the unconditional terminal default instead, and
#: this number only drives the card's subtitle wording.
RAM_BUSY_HINT: Final[float] = 60.0

#: CLAUDE.md says "high swap" without a number. 30% of committed swap is a defensible
#: "actually paging" line, but it is a guess and wants validating under real memory pressure.
SWAP_HIGH: Final[float] = 30.0

TEMP_HIGH_C: Final[float] = 80.0

IDLE_DOZY_S: Final[float] = 60.0          # 1 min, more often than spec says to see layla more often
IDLE_DEEP_SLEEP_S: Final[float] = 900.0    # 15 min -- the rows 3/4 tiebreak


# =============================================================================
# HYSTERESIS & DWELL
#
# Every numeric gate is a Schmitt band: enter high, exit lower. Without this the pet flaps
# between states every poll at a boundary. Bands stop oscillation from a noisy signal near one
# threshold; dwell stops churn from different rules winning on alternate ticks. Both needed.
# =============================================================================

BAND_CPU_HIGH: Final[tuple[float, float]] = (80.0, 70.0)      # 10 pt: CPU is the noisiest
BAND_RAM_HIGH: Final[tuple[float, float]] = (85.0, 80.0)      # 5 pt: RAM moves slowly
BAND_CPU_MODERATE: Final[tuple[float, float]] = (20.0, 14.0)  # 6 pt: guards idle/busy
BAND_SWAP_HIGH: Final[tuple[float, float]] = (30.0, 22.0)
BAND_TEMP_HIGH: Final[tuple[float, float]] = (80.0, 75.0)
# idle_seconds needs no band: it steps to ~0 on any input and otherwise rises monotonically.

#: ~3 animation loops (atlas rows run 1010-1500 ms), so no state is ever a flicker.
MIN_STATE_DWELL_MS: Final[int] = 4000

#: A higher-priority state ignores dwell, so a thermal/swap alarm or a sudden 95% spike is
#: never delayed. Only lateral and downward moves wait.
DWELL_BYPASS_ON_ESCALATION: Final[bool] = True

# --- `working-together`: sustained interaction, with its own asymmetric hysteresis
# Enter is a *ratio over a short window* rather than a single event, so one mouse twitch on the
# way past the desk cannot sit the pets down at their computer. Exit is a plain grace period on
# the idle counter -- "has anything happened recently" is already a one-sided question, so it
# needs no Schmitt band, only stickiness.

#: The trailing window the activity ratio is measured over: 1000 ms = four 250 ms sub-samples.
#: Short on purpose. Every millisecond of it is enter latency the user experiences directly as
#: "I started working and nothing happened".
INPUT_ACTIVITY_WINDOW_MS: Final[int] = 1000

#: Fraction of `INPUT_ACTIVITY_WINDOW_MS` that must contain input before the latch turns on.
#: At 0.75 that is three of four sub-samples, so the earliest possible enter is ~750 ms -- still
#: prompt enough that the pets sitting down reads as a consequence of the user sitting down.
#: A lone twitch scores 0.25 and is rejected. Was 0.5, which a drifting mouse sensor passed with
#: no hand on it: measured on the dev machine, the sensor emits a sub-pixel move every ~580 ms,
#: which lands in two of four sub-samples often enough to latch on every few seconds. Events that
#: far apart can never fill three of four, while real typing or mousing scores close to 1.0.
INPUT_ACTIVITY_ENTER_RATIO: Final[float] = 0.75

#: Milliseconds of *no input at all* before the latch turns off. Some stickiness is essential:
#: without it the pet would drop out of the state in the pause between two sentences. But the
#: number has to be read together with `MIN_STATE_DWELL_MS`, and 2200 + 4000 = 6200 ms of
#: "still working" after the user has walked away reads as broken, not as sticky.
#: The fix is NOT a shorter grace -- the grace is the part that stops the flicker -- but
#: `DWELL_BYPASS_ON_WORKING_EXIT` below. Total exit then lands at ~2.2-2.5 s.
INPUT_IDLE_GRACE_MS: Final[int] = 2200

#: Leaving `working-together` ignores `MIN_STATE_DWELL_MS`. Justified, not convenient: dwell
#: exists to stop animation churn, and the 2200 ms grace period already serves exactly that
#: purpose for this state -- it is longer than every row in the atlas, so nothing is cut short.
#: Applying both would only add dwell's 4 s to a wait that is already sufficient. Entering the
#: state bypasses dwell too, but for the ordinary reason: `PRIORITY` ranks it above
#: `idle-attentive`, so arriving from idle is an escalation.
DWELL_BYPASS_ON_WORKING_EXIT: Final[bool] = True

# --- ready-pleased edge detection ("CPU spike drops back to idle" is a transition, not a level)
READY_SPIKE_CPU: Final[float] = 60.0   # arms the latch
READY_CALM_CPU: Final[float] = 20.0    # fires it on the way down
READY_MIN_SPIKE_S: Final[float] = 8.0  # a 2-second blip is not a completed task
READY_CALM_SAMPLES: Final[int] = 2     # ~3 s: a dip between two build steps must not fire it
READY_PLEASED_HOLD_MS: Final[int] = 4500   # 3 loops of the 1500 ms row

# --- rows 1 vs 7 tiebreak (both trigger on CPU 20-60%)
# `running-playful-roll` is the resident moderate state; `running-busy-play` is a periodic
# one-shot flourish. Row 7 is loop=False in the atlas with a deliberate tail-only final pose,
# so residing in it would freeze the pet on a near-empty frame for minutes.
MODERATE_VARIATION_INTERVAL_MS: Final[int] = 25_000
MODERATE_VARIATION_JITTER_MS: Final[int] = 5_000

#: The same stroll, on a slower cadence, while the pet is calm -- so an idle desktop still
#: shows signs of life instead of looping one animation forever.
#:
#: Unlike the moderate-band schedule, this one is NOT reset when the pet briefly leaves
#: `idle-attentive`. On a five-minute timer a transient CPU blip would otherwise restart the
#: countdown, and on a normal desktop that happens often enough to starve the stroll
#: completely -- you would simply never see it. The elapsed wait therefore survives short
#: excursions and the flourish plays on the next calm sample after it comes due.
IDLE_VARIATION_INTERVAL_MS: Final[int] = 300_000   # 5 minutes

#: Set to 0 for exactly IDLE_VARIATION_INTERVAL_MS. The default spread keeps the cadence from
#: feeling metronomic, and stops two pets on one desktop from syncing up.
IDLE_VARIATION_JITTER_MS: Final[int] = 30_000

#: == atlas row 7 duration_ms: exactly one complete pass.
#:
#: Row 7 is the only flourish whose length is not a free choice. Layla walks OUT of frame and
#: the final pose is tail-only, so holding it longer shows an empty window and looping it
#: teleports her back to the start. It is structurally an exit gag, not a sustained presence.
MODERATE_BUSY_PLAY_HOLD_MS: Final[int] = 1160

#: 3 x row 1's 1340 ms cycle. Row 1 (Layla batting a ball) is the one Layla animation that
#: loops cleanly, so it is the only way to give her more than about a second on screen. Keep
#: this an exact multiple of the cycle so the flourish ends on a loop boundary.
ROLL_FLOURISH_HOLD_MS: Final[int] = 16080

#: 1 x row 8's 1500 ms cycle -- one complete wave.
WAVE_FLOURISH_HOLD_MS: Final[int] = 1500

#: Ambient flourishes, as ``(state, hold_ms, weight)``. Picked by weighted random choice each
#: time the schedule for that host state comes due.
#:
#: The pool is deliberately limited to rows that do not contradict the host state. The pet is
#: a system monitor first: every animation allowed to play at an arbitrary time is one you can
#: no longer read machine state from. Letting Chico's jump appear while the box is idle would
#: mean a jump no longer tells you the CPU is pegged, so the load-mapped rows
#: (`running-playful-jump`, `blocked-grumpy`, the sleep rows) are never drawn from here.
#:
#: Weights are relative, not percentages. The roll is weighted highest because it is the only
#: entry that puts Layla on screen for seconds rather than a fraction of one.
IDLE_FLOURISHES: Final[tuple[tuple[str, int, float], ...]] = (
    ("running-playful-roll", ROLL_FLOURISH_HOLD_MS, 6.0),
    ("running-busy-play", MODERATE_BUSY_PLAY_HOLD_MS, 3.0),
    ("ready-pleased", WAVE_FLOURISH_HOLD_MS, 1.0),
)

#: While already resident in the moderate band the pet is *showing* row 1, so the roll is not
#: a flourish there -- the stroll is the only thing that reads as a change.
MODERATE_FLOURISHES: Final[tuple[tuple[str, int, float], ...]] = (
    ("running-busy-play", MODERATE_BUSY_PLAY_HOLD_MS, 1.0),
)

#: Played when the user clicks the pet or finishes dragging it -- direct acknowledgement that
#: the pet noticed. ``None`` disables interaction greetings entirely.
#:
#: This is a state *name*, not a hard-coded row, so when a dedicated greeting animation lands
#: it is a one-line swap here rather than a code change. Today it borrows `ready-pleased`,
#: which also means "your long task just finished" -- a wave reads correctly both ways, but it
#: does mean a wave is no longer a unique "build done" signal.
INTERACTION_GREETING_STATE: Final[str | None] = "ready-pleased"

#: One full wave cycle. Clicking again while it plays extends it rather than restarting, so
#: repeated clicks keep the animation smooth instead of stuttering back to frame 0.
INTERACTION_GREETING_HOLD_MS: Final[int] = WAVE_FLOURISH_HOLD_MS

#: ``--demo``: how long each state is held while cycling through the atlas for visual inspection.
DEMO_HOLD_MS: Final[int] = 4000


# =============================================================================
# STATE COPY  (data-driven: a new sprite row needs one dict entry and no code change)
# =============================================================================


@dataclass(frozen=True, slots=True)
class StatePresentation:
    """Card copy and accent for one animation state.

    ``title`` and ``subtitle`` are format strings substituted against the keys produced by
    ``stats_card.presentation_context``: pet, cpu, ram, swap, gpu, vram, battery, temp,
    idle_min, disk. Unknown keys degrade to an em-dash rather than raising inside paintEvent.
    """

    title: str
    subtitle: str
    accent: str  # "#RRGGBB"
    glyph: str   # key into stats_card.GLYPH_PAINTERS


#: Clauses are separated with the same middle dot the card uses between a value and its unit,
#: rather than an ASCII hyphen -- at 13 px a hyphen reads as a stray minus sign.
STATE_PRESENTATION: Final[dict[str, StatePresentation]] = {
    "idle-attentive": StatePresentation(
        "Chico is on watch", "All quiet · CPU {cpu}%", "#6FD08C", "dot"),
    "running-playful-roll": StatePresentation(
        "Layla is rolling", "Moderate load · CPU {cpu}%", "#58A6FF", "spark"),
    "running-busy-play": StatePresentation(
        "Layla is strolling", "Keeping busy · CPU {cpu}%", "#58A6FF", "spark"),
    "running-playful-jump": StatePresentation(
        "Chico is going hard", "Heavy load · CPU {cpu}% · RAM {ram}%", "#F0883E", "bolt"),
    "waiting-dozy": StatePresentation(
        "Layla is dozing off", "Idle for {idle_min} min", "#8B949E", "zzz"),
    "waiting-deep-sleep": StatePresentation(
        "Chico and Layla are asleep", "Idle for {idle_min} min", "#6E7681", "zzz"),
    # Deliberately NOT ACCENT_DANGER. The card paints any metric over its own high threshold
    # in ACCENT_DANGER; if the state accent were the same red, every meter on this state would
    # be red -- including the ones that are fine -- and the card would cry wolf. A lighter red
    # keeps the state legible as "something is wrong" while leaving the danger red to mean
    # "this specific number is the problem".
    "blocked-grumpy": StatePresentation(
        "Something's wrong", "Memory pressure · swap {swap}%", "#FF7B72", "alert"),
    "needs-input-sad": StatePresentation(
        "Chico needs you", "Waiting on your input", "#D2A8FF", "alert"),
    "ready-pleased": StatePresentation(
        "All done", "The system is back to idle", "#4ADE80", "check"),
    # Copy stays honest about the signal: `GetLastInputInfo` knows the user is *at the
    # machine*, not that they are typing, so the subtitle says the former. Glyph is the
    # existing "spark" -- a new row does not get to add a glyph painter.
    "working-together": StatePresentation(
        "Heads down together", "You're at the machine · CPU {cpu}%", "#79C0FF", "spark"),
}

DEFAULT_PRESENTATION: Final[StatePresentation] = StatePresentation(
    "Chico and Layla", "Watching your system", "#8B949E", "dot")


# =============================================================================
# CARD PALETTE   (RGBA tuples; measured against assets/OpenAI_pets_windowreference.png)
# =============================================================================

#: #1E2024 at ~95%. The reference card sits over a soft wallpaper, where the 9% bleed of an
#: alpha-232 surface is imperceptible -- but a desktop pet sits over code editors and
#: terminals, and measured against high-contrast text 9% ghosts through legibly enough to
#: read the words underneath. 242 keeps the glassy edge while stopping that.
CARD_SURFACE: Final[tuple[int, int, int, int]] = (30, 32, 36, 242)
CARD_RIM: Final[tuple[int, int, int, int]] = (255, 255, 255, 18)        # faint 1px hairline
CARD_TITLE_COLOR: Final[tuple[int, int, int, int]] = (255, 255, 255, 242)
CARD_SUBTITLE_COLOR: Final[tuple[int, int, int, int]] = (255, 255, 255, 150)
CARD_LABEL_COLOR: Final[tuple[int, int, int, int]] = (255, 255, 255, 140)
CARD_VALUE_COLOR: Final[tuple[int, int, int, int]] = (255, 255, 255, 225)
CARD_METER_TRACK: Final[tuple[int, int, int, int]] = (255, 255, 255, 28)
CARD_DISABLED_COLOR: Final[tuple[int, int, int, int]] = (255, 255, 255, 70)

#: Used for any metric above its own high threshold, regardless of state accent, so a pegged
#: RAM stays visible while CPU drives the state. Two colours maximum on the card.
ACCENT_DANGER: Final[str] = "#F85149"


# =============================================================================
# CARD GEOMETRY / TYPOGRAPHY
# =============================================================================

CARD_WIDTH: Final[int] = 300
CARD_RADIUS: Final[int] = 20
CARD_PADDING_X: Final[int] = 16
CARD_PADDING_Y: Final[int] = 14

CARD_TITLE_PX: Final[int] = 15       # DemiBold
CARD_SUBTITLE_PX: Final[int] = 13
CARD_LABEL_PX: Final[int] = 12
CARD_VALUE_PX: Final[int] = 12       # DemiBold, tabular figures
CARD_FONT_FAMILIES: Final[tuple[str, ...]] = ("Segoe UI Variable Text", "Segoe UI", "Inter")

CARD_SECTION_GAP: Final[int] = 10
CARD_ROW_HEIGHT: Final[int] = 18
CARD_LABEL_COL_W: Final[int] = 40

#: Holds only the number (the unit is its own column), floored at the measured width of
#: "100%" so the meter track does not lengthen and shorten as CPU crosses 9 -> 10 -> 100%.
CARD_VALUE_COL_W: Final[int] = 40
CARD_METER_HEIGHT: Final[int] = 6
CARD_GLYPH_D: Final[int] = 28

CARD_GAP_PX: Final[int] = 12         # gap between pet and card
CARD_EDGE_MARGIN: Final[int] = 8

CARD_FADE_IN_MS: Final[int] = 120
CARD_FADE_OUT_MS: Final[int] = 160
CARD_HOVER_SHOW_DELAY_MS: Final[int] = 250   # no flicker when a cursor passes through
CARD_HOVER_HIDE_DELAY_MS: Final[int] = 700   # cancelled if the cursor enters the card itself

#: Draw a soft drop shadow around the card.
#:
#: **Not** ``QGraphicsDropShadowEffect`` -- on a ``WA_TranslucentBackground`` top-level that
#: effect forces an offscreen render of the whole window every repaint and composites
#: inconsistently on Windows. The card paints its own shadow instead: a stack of concentric
#: rounded rects of rising alpha, drawn just outside the surface path (and clipped to exclude
#: it) inside a transparent margin the widget reserves around the visible card. That is a few
#: dozen ``drawRoundedRect`` calls on a 300x100 card -- cheap, and identical on every backend.
#:
#: The margin is the reason the widget is bigger than the card: see ``StatsCard.shadow_margin``.
#: Everything the user sees or touches is inset by it.
CARD_SHADOW: Final[bool] = True

#: How far the shadow reaches past the card edge, in px -- the "blur radius". This is also the
#: transparent margin reserved on every side (plus the y-offset below).
CARD_SHADOW_RADIUS: Final[int] = 24

#: Downward displacement. Small: the reference's card sits just off the wallpaper, it is not
#: floating high above it.
CARD_SHADOW_Y_OFFSET: Final[int] = 5

#: Peak alpha, reached immediately outside the surface path and falling to 0 at
#: ``CARD_SHADOW_RADIUS``. Low on purpose -- the single most common way a hand-painted shadow
#: fails is reading as a grey halo on a light desktop, and that is an alpha problem.
CARD_SHADOW_ALPHA: Final[int] = 46

#: Shadow ink. Pure black goes muddy over saturated wallpaper; a hair of blue keeps it reading
#: as shade rather than as soot, which is also what the reference does.
CARD_SHADOW_COLOR: Final[tuple[int, int, int]] = (6, 8, 14)

#: Number of concentric rects across the falloff. At radius 24 this puts a band every ~1.3 px,
#: which is below the point where stepping is visible at either 100% or 150% scaling (checked
#: at 4x zoom on a light backdrop). More layers only cost draw calls.
CARD_SHADOW_STEPS: Final[int] = 18

#: Falloff exponent on the 0..1 distance from the card edge: alpha = peak * (1 - t) ** e.
#: ~2 is a quadratic ramp -- most of the ink hugs the card and the outer half is nearly
#: nothing, which is what makes a shadow read as soft rather than as an outline. Rendered over
#: light, mid-grey and dark backdrops at four tunings; below ~1.7 the contact under the card
#: goes washy, above ~2.2 the ink bunches into a visible dark rule along the edge.
CARD_SHADOW_FALLOFF: Final[float] = 1.9


# =============================================================================
# SETTINGS PERSISTENCE
#
# JSON, not QSettings. QSettings in PyQt6 silently returns the truthy string "false" if you
# forget `type=bool`, which is precisely the class of bug CLAUDE.md says this repo has already
# paid for once. JSON round-trips bool/int/float exactly and keeps this module Qt-free.
# =============================================================================

APP_NAME: Final[str] = "LC-pet-stat"
SETTINGS_SCHEMA_VERSION: Final[int] = 1
CONFIG_DIR_ENV: Final[str] = "LC_PET_STAT_CONFIG_DIR"


def config_dir() -> Path:
    """Per-user config directory. Overridable via ``$LC_PET_STAT_CONFIG_DIR`` (used by tests)."""
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / APP_NAME


def settings_path() -> Path:
    return config_dir() / "settings.json"


@dataclass(slots=True)
class Settings:
    """Persisted user state. Mutable by design -- one instance lives on the app object."""

    pos_x: int | None = None      # window frame top-left, logical px, virtual-desktop coords
    pos_y: int | None = None
    screen: str | None = None     # QScreen.name() hint, consulted only if pos is invalid
    click_through: bool = False
    always_on_top: bool = True
    card_visible: bool = False    # the *pinned* card
    pet_visible: bool = True
    scale: int = RENDER_SCALE

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "pos_x": self.pos_x,
            "pos_y": self.pos_y,
            "screen": self.screen,
            "click_through": self.click_through,
            "always_on_top": self.always_on_top,
            "card_visible": self.card_visible,
            "pet_visible": self.pet_visible,
            "scale": self.scale,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Settings":
        """Build from a mapping, ignoring unknown keys so a newer file never breaks an older
        build, and coercing junk to defaults rather than raising."""
        version = data.get("schema_version")
        if version is not None and version != SETTINGS_SCHEMA_VERSION:
            log.warning("settings schema_version %r != %d; using defaults where unreadable",
                        version, SETTINGS_SCHEMA_VERSION)

        def _opt_int(key: str) -> int | None:
            value = data.get(key)
            return int(value) if isinstance(value, (int, float)) else None

        def _bool(key: str, default: bool) -> bool:
            value = data.get(key, default)
            return value if isinstance(value, bool) else default

        screen = data.get("screen")
        scale = data.get("scale", RENDER_SCALE)
        return cls(
            pos_x=_opt_int("pos_x"),
            pos_y=_opt_int("pos_y"),
            screen=screen if isinstance(screen, str) else None,
            click_through=_bool("click_through", False),
            always_on_top=_bool("always_on_top", True),
            card_visible=_bool("card_visible", False),
            pet_visible=_bool("pet_visible", True),
            scale=int(scale) if isinstance(scale, (int, float)) else RENDER_SCALE,
        )

    def clamped(self) -> "Settings":
        """Return a copy with out-of-range values pulled back into bounds."""
        return replace(self, scale=max(1, min(MAX_RENDER_SCALE, int(self.scale))))


def load_settings(path: Path | None = None) -> Settings:
    """Read settings from disk. Never raises -- any failure yields defaults plus a log line."""
    target = path or settings_path()
    try:
        raw = target.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return Settings()
    except OSError as exc:
        log.warning("could not read %s (%s); using defaults", target, exc)
        return Settings()
    if not raw:
        return Settings()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Deliberately not renamed or deleted -- no surprise destruction of a user's file.
        log.warning("%s is not valid JSON (%s); using defaults", target, exc)
        return Settings()
    if not isinstance(data, Mapping):
        log.warning("%s does not contain a JSON object; using defaults", target)
        return Settings()
    return Settings.from_dict(data)


def save_settings(settings: Settings, path: Path | None = None) -> bool:
    """Write settings atomically, verifying by read-back. Returns False on failure.

    The read-back is CLAUDE.md's "validate generated files with the format's own tooling"
    rule. It costs microseconds on a sub-kilobyte file.
    """
    target = path or settings_path()
    payload = settings.to_dict()
    tmp = target.with_suffix(".json.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        written = json.loads(tmp.read_text(encoding="utf-8"))
        if written != payload:
            log.error("settings round-trip mismatch; not replacing %s", target)
            tmp.unlink(missing_ok=True)
            return False
        os.replace(tmp, target)
        return True
    except OSError as exc:
        log.warning("could not write %s (%s)", target, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
