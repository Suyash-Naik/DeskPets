"""Background system-metrics sampling for the Layla & Chico desktop pet.

One worker ``QObject`` lives on a plain ``QThread`` and is driven by a ``QTimer`` on that
thread's own event loop. ``TelemetryService`` is the only thing the rest of the app touches;
the single thing that crosses the thread boundary is an immutable :class:`Metrics`, delivered
over an automatic (therefore queued) signal connection.

Why *not* a ``QThread`` subclass
--------------------------------
A ``QThread`` subclass lives in the thread that *created* it, so its ``pyqtSlot``s execute in
the creating thread. Putting ``psutil`` calls in such a slot silently reintroduces exactly the
cross-thread hazard CLAUDE.md warns about, while looking correct. The worker-object idiom keeps
affinity honest: everything from :meth:`_TelemetryWorker.prime` onwards genuinely runs on the
worker thread, and an ``assert`` in the tick proves it under ``-O``-less Python.

MODULE-GLOBAL INVARIANT -- nothing else in this process may call ``psutil.cpu_percent()``
-----------------------------------------------------------------------------------------
``psutil.cpu_percent(interval=None)`` is *stateful*: it returns the utilisation measured over
the window since **the previous call by anyone in this process**, and that reference point is
shared process-wide. A second caller (a debug print, a stats widget, a test helper) silently
truncates our window and makes our numbers meaningless -- with no error anywhere. Read
``TelemetryService.last_metrics`` instead. The blocking ``interval=1`` form is likewise banned:
it pins the poll rate at one second no matter what ``config.POLL_INTERVAL_MS`` says, which is
the prototype defect this module exists to remove.

Structural gaps on Windows (all expected, none are bugs)
-------------------------------------------------------
* ``psutil.sensors_temperatures`` **does not exist** on Windows -- the attribute is absent, not
  empty. ``Metrics.cpu_temp_c`` is therefore always ``None`` in Phase 1.
  Future sources, in preference order:
    - ``pynvml`` -> GPU die temperature. Arrives free with the GPU dependency
      (``pixi add --feature pets nvidia-ml-py``); already surfaced as ``GpuSample.temp_c``.
    - LibreHardwareMonitor (its shared-memory/HTTP bridge) -> real CPU package temperature.
  **Rejected: WMI ``MSAcpi_ThermalZoneTemperature``.** It is usually a motherboard ACPI zone,
  not the CPU package -- it reads tens of degrees low and barely moves under load. A
  plausible-looking wrong number is strictly worse than ``None``, because ``None`` is handled
  (``Band.holds(None, ...) -> False``) whereas a wrong number silently disables the
  ``blocked-grumpy`` thermal rule while appearing to work.
* ``psutil.sensors_battery()`` returns ``None`` on a desktop. Probed once; ``Metrics.battery``
  then stays ``None`` for the life of the service.
* Neither ``pynvml`` nor ``GPUtil`` is installed by default, so ``Metrics.gpu`` is ``None``.

``None`` in every one of those fields means *structurally unavailable*, which the stats card
renders by omitting the row entirely (contract 5.2).

The input sub-sampler -- what it does and does not know
------------------------------------------------------
A second, faster ``QTimer`` on the *same* worker event loop re-reads the platform seam's
``get_idle_seconds()`` every ``config.INPUT_SAMPLE_INTERVAL_MS`` and latches a boolean:
"the user is interacting with this machine". It exists because the 1500 ms poll cannot measure
input cadence -- at that resolution a single stray mouse movement and a solid 1.5 s of work are
the same reading.

**It detects sustained interaction, not typing.** The only source is Windows'
``GetLastInputInfo`` (through the platform seam), which reports how long ago the last input
event of *any* kind arrived. A keystroke, a mouse move, a scroll wheel and a trackpad graze are
indistinguishable to it, and nothing in this module may be named or documented as if they were.
That is the point, not a limitation to work around: the alternatives that *could* tell them
apart -- ``SetWindowsHookEx``/``WH_KEYBOARD_LL``, ``GetAsyncKeyState`` polling, Raw Input with
``RIDEV_INPUTSINK`` -- are keyloggers by any reasonable reading and trip AV heuristics, and are
deliberately not used.

Two details that are easy to get wrong:

* The per-sub-sample test is ``idle_now < measured_elapsed + epsilon``, where ``measured_elapsed``
  comes from a ``QElapsedTimer``, **not** from the nominal interval. A timer that runs late --
  and on a loaded desktop it will -- makes real input look older than a hard-coded 250 ms gap,
  which under-counts activity and biases the ratio downward exactly when the machine is busy.
* ``get_idle_seconds()`` returning ``None`` counts as *not active*, never as "input happened".
  Same principle as ``Band.holds(None, ...) -> False`` in the state machine: an unavailable
  signal must make a state unreachable, not permanently on.

The latch is published on its own signal, ``input_activity_changed``, rather than only on the
``Metrics`` payload. Riding the 1500 ms poll would have added up to 1.5 s to a transition the
user is watching for; the ratio still travels on ``Metrics.input_activity_ratio`` for the card.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Final, Protocol

import psutil
from PyQt6.QtCore import (
    Q_ARG,
    QCoreApplication,
    QElapsedTimer,
    QMetaObject,
    QObject,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)

from src import config

log = logging.getLogger(__name__)

# The idle-time seam (contract 4.5) is written in parallel and may not be on disk yet. Guarding
# the import keeps this module loadable and testable on its own; idle_seconds is simply None
# until the seam lands, which the state machine already treats as "cannot supply it".
try:
    from src.core import platform as platform_seam
except ImportError:  # pragma: no cover - transient, only while core/platform/ is unwritten
    platform_seam = None  # type: ignore[assignment]
    log.info("src.core.platform not importable yet; idle_seconds will be None")


# =============================================================================
# SAMPLE PAYLOADS
# =============================================================================


@dataclass(frozen=True, slots=True)
class GpuSample:
    """One GPU reading. Any field may be ``None`` if the driver did not supply it."""

    name: str
    load_percent: float | None
    vram_used_bytes: int | None
    vram_total_bytes: int | None
    vram_percent: float | None
    temp_c: float | None


@dataclass(frozen=True, slots=True)
class BatterySample:
    """One battery reading. Absent entirely (``Metrics.battery is None``) on a desktop."""

    percent: float
    plugged: bool
    secs_left: int | None


@dataclass(frozen=True, slots=True)
class Metrics:
    """An immutable snapshot of system load.

    This is the *only* object that crosses the telemetry thread boundary. Frozen + slots so it
    cannot be mutated by a receiver on the GUI thread after the worker has moved on.

    ``None`` always means "structurally unavailable on this machine", never "zero".
    """

    timestamp: float  # time.monotonic(), taken on the worker thread
    cpu_percent: float
    ram_percent: float
    ram_used_bytes: int
    ram_total_bytes: int
    swap_percent: float
    disk_percent: float | None
    cpu_temp_c: float | None  # ALWAYS None on Windows in Phase 1 -- see module docstring
    gpu: GpuSample | None
    battery: BatterySample | None
    idle_seconds: float | None
    #: Fraction of the trailing ``config.INPUT_ACTIVITY_WINDOW_MS`` that contained user input,
    #: in [0, 1]; ``None`` when the platform seam cannot supply idle time at all. Informational
    #: only -- the state machine is driven by the latched ``input_activity_changed`` signal, not
    #: by this number, so that entering the state is not gated on the 1500 ms poll.
    #:
    #: Defaulted because it is an addition to contract 4.3's field list: every existing caller
    #: that builds a ``Metrics`` by keyword keeps working unchanged.
    input_activity_ratio: float | None = None

    @classmethod
    def placeholder(cls) -> "Metrics":
        """A zeroed sample for painting the card before the first real tick arrives.

        Callable from any thread.
        """
        return cls(
            timestamp=time.monotonic(),
            cpu_percent=0.0,
            ram_percent=0.0,
            ram_used_bytes=0,
            ram_total_bytes=0,
            swap_percent=0.0,
            disk_percent=None,
            cpu_temp_c=None,
            gpu=None,
            battery=None,
            idle_seconds=None,
            input_activity_ratio=None,
        )


# =============================================================================
# PROVIDER SEAMS  (lazily probed on the worker thread, never at import time)
# =============================================================================


class GpuProvider(Protocol):
    """A source of GPU telemetry. Implementations must not raise from ``close()``."""

    name: str

    def sample(self) -> GpuSample | None: ...

    def close(self) -> None: ...


class TempProvider(Protocol):
    """A source of CPU package temperature in degrees Celsius, or ``None`` if unknown."""

    name: str

    def sample(self) -> float | None: ...

    def close(self) -> None: ...


class NullGpuProvider:
    """No GPU telemetry available. Costs nothing per tick and never raises."""

    name = "none"

    def sample(self) -> GpuSample | None:
        return None

    def close(self) -> None:
        return None


class NullTempProvider:
    """No temperature source. ``None`` means unknown -- we never synthesise a number."""

    name = "none"

    def sample(self) -> float | None:
        return None

    def close(self) -> None:
        return None


class _NvmlGpuProvider:
    """NVIDIA GPU telemetry via ``pynvml`` / ``nvidia-ml-py``. Worker thread only."""

    name = "pynvml"

    def __init__(self, nvml: Any, handle: Any, device_name: str) -> None:
        self._nvml = nvml
        self._handle = handle
        self._device_name = device_name

    def sample(self) -> GpuSample | None:
        nvml = self._nvml
        util = nvml.nvmlDeviceGetUtilizationRates(self._handle)
        mem = nvml.nvmlDeviceGetMemoryInfo(self._handle)
        try:
            temp = float(nvml.nvmlDeviceGetTemperature(self._handle, nvml.NVML_TEMPERATURE_GPU))
        except Exception:  # a card without a readable sensor must not kill the whole sample
            temp = None
        used = int(mem.used)
        total = int(mem.total)
        return GpuSample(
            name=self._device_name,
            load_percent=float(util.gpu),
            vram_used_bytes=used,
            vram_total_bytes=total,
            vram_percent=(100.0 * used / total) if total else None,
            temp_c=temp,
        )

    def close(self) -> None:
        try:
            self._nvml.nvmlShutdown()
        except Exception:  # pragma: no cover - shutdown failures are not actionable
            pass


class _PsutilTempProvider:
    """CPU temperature via ``psutil.sensors_temperatures()``.

    Only constructed where the attribute exists at all -- i.e. never on Windows. Kept so that a
    future Linux port needs no new code path.
    """

    name = "psutil"

    #: Coretemp/k10temp first; these are CPU package sensors rather than board zones.
    _PREFERRED: Final[tuple[str, ...]] = ("coretemp", "k10temp", "cpu_thermal", "acpitz")

    def sample(self) -> float | None:
        readings = psutil.sensors_temperatures()  # type: ignore[attr-defined]
        for key in self._PREFERRED:
            entries = readings.get(key) or ()
            for entry in entries:
                if entry.current is not None:
                    return float(entry.current)
        return None

    def close(self) -> None:
        return None


def _probe_gpu_provider() -> GpuProvider:
    """Build a GPU provider, or a null one. **Worker thread only** (called from ``prime``).

    ``pynvml`` is imported here rather than at module level so that an absent or broken NVIDIA
    driver costs the app nothing at startup and cannot stop ``import src.core.telemetry``.
    """
    try:
        import pynvml  # type: ignore[import-not-found]
    except ImportError:
        log.info("no GPU provider: pynvml/nvidia-ml-py is not installed; GPU row will be hidden")
        return NullGpuProvider()

    try:
        pynvml.nvmlInit()
        if pynvml.nvmlDeviceGetCount() < 1:
            pynvml.nvmlShutdown()
            log.info("no GPU provider: NVML initialised but reports zero devices")
            return NullGpuProvider()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        raw = pynvml.nvmlDeviceGetName(handle)
        device_name = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    except Exception as exc:
        log.info("no GPU provider: NVML probe failed (%s)", exc)
        return NullGpuProvider()

    log.info("GPU provider ready: %s", device_name)
    return _NvmlGpuProvider(pynvml, handle, device_name)


def _probe_temp_provider() -> TempProvider:
    """Build a temperature provider, or a null one. **Worker thread only.**

    On Windows ``psutil.sensors_temperatures`` does not exist as an attribute, so this returns
    the null provider and ``cpu_temp_c`` stays ``None`` forever. That is the designed outcome,
    not a degradation to work around -- see the module docstring on why WMI is not a substitute.
    """
    if not hasattr(psutil, "sensors_temperatures"):
        log.info(
            "no temperature provider: psutil.sensors_temperatures is unavailable on this "
            "platform; cpu_temp_c stays None and the thermal rule stays disabled"
        )
        return NullTempProvider()
    try:
        provider = _PsutilTempProvider()
        provider.sample()
    except Exception as exc:  # pragma: no cover - not reachable on the Windows target
        log.info("no temperature provider: psutil sensor probe failed (%s)", exc)
        return NullTempProvider()
    return provider


# =============================================================================
# WORKER
# =============================================================================


def _clamp_interval(ms: int) -> int:
    """Pull a requested poll interval into ``config``'s supported range. Any thread."""
    return max(config.POLL_INTERVAL_MIN_MS, min(config.POLL_INTERVAL_MAX_MS, int(ms)))


class _TelemetryWorker(QObject):
    """Samples psutil on the telemetry thread. Never touches a widget.

    Every method below ``prime`` runs on the worker thread. Nothing here may be called
    directly from the GUI thread -- use ``QMetaObject.invokeMethod``, which is what
    :class:`TelemetryService` does.
    """

    metrics_ready = pyqtSignal(object)  # payload: Metrics
    input_activity_changed = pyqtSignal(bool)  # latched "the user is interacting"

    def __init__(self, interval_ms: int) -> None:
        """Constructed on the GUI thread; owns nothing thread-affine until ``prime`` runs."""
        super().__init__()
        self._interval_ms = _clamp_interval(interval_ms)
        self._timer: QTimer | None = None
        self._stopped = False

        # -- input sub-sampler (see the module docstring). All of it is worker-thread state.
        self._input_timer: QTimer | None = None
        #: Measures the real gap between two sub-samples. The nominal interval is not good
        #: enough -- see the module docstring.
        self._input_clock = QElapsedTimer()
        #: Trailing (measured_gap_seconds, had_input) pairs covering roughly
        #: ``config.INPUT_ACTIVITY_WINDOW_MS``. Time-weighted rather than sample-counted so a
        #: late timer contributes its real share instead of one vote like any other.
        self._input_window: deque[tuple[float, bool]] = deque()
        self._input_active = False
        self._input_ratio: float | None = None

        self._gpu: GpuProvider = NullGpuProvider()
        self._temp: TempProvider = NullTempProvider()
        self._gpu_errors = 0

        self._battery_supported = True
        self._idle_supported = platform_seam is not None
        self._disk_percent: float | None = None
        self._disk_countdown = 0

    # -- lifecycle ------------------------------------------------------------------------

    @pyqtSlot()
    def prime(self) -> None:
        """Set up the providers and the poll timer. **Worker thread** (via ``thread.started``).

        Makes one discarded ``cpu_percent()`` call: the first call in a process has no previous
        reference point and unconditionally returns 0.0, which would otherwise show up as a
        fake idle sample and, worse, arm nothing / disarm the state machine's spike latch.
        """
        self._assert_worker_thread()
        if self._stopped:
            return

        psutil.cpu_percent(interval=None)  # discarded: establishes the shared reference point
        self._gpu = _probe_gpu_provider()
        self._temp = _probe_temp_provider()

        battery = self._read_battery()
        if battery is None:
            # Probed once: a machine does not grow a battery at runtime. Saves a syscall/tick.
            self._battery_supported = False
            log.info("no battery present; the battery row will be hidden")

        timer = QTimer(self)
        timer.setInterval(self._interval_ms)
        timer.timeout.connect(self._tick)
        self._timer = timer

        self._start_input_sampler()

        # A short first tick so the card populates almost immediately. The reading is genuine,
        # just measured over a shorter window than the configured interval.
        QTimer.singleShot(config.FIRST_SAMPLE_DELAY_MS, self._begin_polling)

    @pyqtSlot()
    def _begin_polling(self) -> None:
        """First real sample, then start the repeating timer. **Worker thread.**"""
        if self._stopped or self._timer is None:
            return
        self._tick()
        if not self._stopped and self._timer is not None:
            self._timer.start()

    @pyqtSlot()
    def stop(self) -> None:
        """Halt polling and release providers. **Worker thread** (BlockingQueuedConnection).

        Running on the worker thread matters: ``QTimer`` cannot be stopped from another thread,
        and ``deleteLater`` posted from here is flushed by ``QThread::exec`` as it unwinds.
        """
        self._assert_worker_thread()
        self._stopped = True
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        # The sub-sampler fires six times as often as the poll timer, so leaving it running is
        # the obvious way to regress a shutdown that currently measures ~15 ms. Stopped and
        # deleted on exactly the same terms as the poll timer, and on the worker thread, which
        # is the only thread allowed to stop a QTimer that lives here.
        self._stop_input_sampler()
        self._gpu.close()
        self._temp.close()
        self._gpu = NullGpuProvider()
        self._temp = NullTempProvider()

    @pyqtSlot(int)
    def set_interval(self, ms: int) -> None:
        """Retime the poller. **Worker thread** (QueuedConnection).

        Retimes the *metrics* poll only. The input sub-sampler is deliberately independent of
        it: its cadence is a property of how fast human input has to be resolved, not of how
        often the user wants CPU numbers, and slowing it to match a 10 s poll would make
        ``working-together`` useless. Do not "fix" it to follow this interval.
        """
        self._interval_ms = _clamp_interval(ms)
        if self._timer is not None:
            self._timer.setInterval(self._interval_ms)

    # -- sampling -------------------------------------------------------------------------

    @pyqtSlot()
    def _tick(self) -> None:
        """One poll. **Worker thread.** A bad sample logs and skips; it never kills the timer."""
        if __debug__:
            self._assert_worker_thread()
        if self._stopped:
            return
        started = time.monotonic()
        try:
            metrics = self._sample()
        except Exception:
            # Deliberately broad: psutil raises OSError/AccessDenied variants that differ per
            # Windows build, and one unreadable counter must not stop the pet from animating.
            log.error("telemetry sample failed; skipping this tick", exc_info=True)
            return
        elapsed_ms = (time.monotonic() - started) * 1000.0
        if elapsed_ms > config.SAMPLE_SLOW_WARN_MS:
            log.warning("telemetry sample took %.0f ms (> %d ms)",
                        elapsed_ms, config.SAMPLE_SLOW_WARN_MS)
        self.metrics_ready.emit(metrics)

    def _sample(self) -> Metrics:
        """Read every counter once. **Worker thread.**"""
        cpu = float(psutil.cpu_percent(interval=None))  # non-blocking form, always
        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()

        return Metrics(
            timestamp=time.monotonic(),
            cpu_percent=cpu,
            ram_percent=float(vm.percent),
            ram_used_bytes=int(vm.used),
            ram_total_bytes=int(vm.total),
            swap_percent=float(sm.percent),
            disk_percent=self._read_disk(),
            cpu_temp_c=self._read_temp(),
            gpu=self._read_gpu(),
            battery=self._read_battery() if self._battery_supported else None,
            idle_seconds=self._read_idle(),
            input_activity_ratio=self._input_ratio,
        )

    def _read_disk(self) -> float | None:
        """Cached disk usage. **Worker thread.**

        ``disk_usage()`` touches the filesystem and the number barely moves, so it is refreshed
        once per ``DISK_SAMPLE_EVERY_N_TICKS`` and served from cache in between.
        """
        if self._disk_countdown > 0:
            self._disk_countdown -= 1
            return self._disk_percent
        self._disk_countdown = max(0, config.DISK_SAMPLE_EVERY_N_TICKS - 1)
        try:
            self._disk_percent = float(psutil.disk_usage(config.SYSTEM_DRIVE).percent)
        except OSError as exc:
            log.warning("disk usage for %s unavailable (%s)", config.SYSTEM_DRIVE, exc)
            self._disk_percent = None
        return self._disk_percent

    def _read_temp(self) -> float | None:
        """CPU package temperature, or ``None``. **Worker thread.** Never synthesised."""
        try:
            return self._temp.sample()
        except Exception as exc:
            log.warning("temperature provider %r failed (%s); downgrading to null",
                        self._temp.name, exc)
            self._temp = NullTempProvider()
            return None

    def _read_gpu(self) -> GpuSample | None:
        """GPU sample, or ``None``. **Worker thread.**

        After ``config.GPU_PROVIDER_MAX_CONSECUTIVE_ERRORS`` consecutive failures the provider
        is replaced permanently: a driver that has started throwing keeps throwing, and we will
        not pay an exception (or risk a hang) inside every poll for the rest of the session.
        """
        if isinstance(self._gpu, NullGpuProvider):
            return None
        try:
            sample = self._gpu.sample()
        except Exception as exc:
            self._gpu_errors += 1
            log.warning("GPU provider %r failed (%d/%d): %s", self._gpu.name, self._gpu_errors,
                        config.GPU_PROVIDER_MAX_CONSECUTIVE_ERRORS, exc)
            if self._gpu_errors >= config.GPU_PROVIDER_MAX_CONSECUTIVE_ERRORS:
                log.error("GPU provider %r disabled permanently after %d consecutive errors",
                          self._gpu.name, self._gpu_errors)
                self._gpu.close()
                self._gpu = NullGpuProvider()
            return None
        self._gpu_errors = 0
        return sample

    def _read_battery(self) -> BatterySample | None:
        """Battery sample, or ``None`` when no battery exists. **Worker thread.**"""
        getter = getattr(psutil, "sensors_battery", None)
        if getter is None:
            return None
        try:
            raw = getter()
        except Exception as exc:
            log.warning("battery read failed (%s); hiding the battery row", exc)
            self._battery_supported = False
            return None
        if raw is None:
            return None
        secs = raw.secsleft
        if secs is None or secs < 0:  # POWER_TIME_UNKNOWN / UNLIMITED are negative sentinels
            secs = None
        return BatterySample(percent=float(raw.percent), plugged=bool(raw.power_plugged),
                             secs_left=secs)

    def _read_idle(self) -> float | None:
        """Seconds since the last user input, or ``None``. **Worker thread.**

        ``None`` is meaningful: the state machine refuses to enter a sleeping state on it. It
        must never be coerced to ``0.0``, which would read as "user is active" and permanently
        disable rows 3 and 4.
        """
        if not self._idle_supported or platform_seam is None:
            return None
        try:
            return platform_seam.get_idle_seconds()
        except Exception as exc:
            log.warning("idle-time seam failed (%s); disabling the sleep states", exc)
            self._idle_supported = False
            return None

    # -- input sub-sampler ----------------------------------------------------------------

    def _start_input_sampler(self) -> None:
        """Arm the fast idle-time sub-sampler. **Worker thread** (called from ``prime``).

        Does nothing when the platform seam is absent, so on an unported OS the state driven by
        this signal is simply unreachable rather than wrongly always-on.
        """
        if not self._idle_supported:
            log.info("no idle-time seam; sustained-interaction detection is disabled")
            return
        timer = QTimer(self)
        timer.setInterval(max(1, int(config.INPUT_SAMPLE_INTERVAL_MS)))
        timer.timeout.connect(self._input_subsample)
        self._input_timer = timer
        self._input_clock.start()
        timer.start()

    def _stop_input_sampler(self) -> None:
        """Halt and release the sub-sampler. **Worker thread.** Idempotent."""
        if self._input_timer is not None:
            self._input_timer.stop()
            self._input_timer.deleteLater()
            self._input_timer = None
        self._input_window.clear()

    @pyqtSlot()
    def _input_subsample(self) -> None:
        """One idle-time sub-sample; latch and emit on an edge. **Worker thread.**

        Cheap by construction: one ``GetLastInputInfo`` read, a bounded deque of about four
        entries, and no allocation beyond that. It emits only on a change of the latch, never
        per sub-sample.
        """
        if self._stopped:
            return

        # restart() returns the measured gap and re-zeros in one call, so no gap is lost
        # between two sub-samples even if this one runs late.
        elapsed_s = self._input_clock.restart() / 1000.0
        idle = self._read_idle()

        if idle is None:
            # Unknown is not "active" -- see the module docstring. If the seam has failed for
            # good, _read_idle has already latched that; stop paying for the timer.
            self._input_window.clear()
            self._input_ratio = None
            self._set_input_active(False)
            if not self._idle_supported:
                log.info("idle-time seam is gone; stopping the input sub-sampler")
                self._stop_input_sampler()
            return

        # The one comparison the whole feature rests on. `elapsed_s` is measured, never the
        # nominal interval: a late timer would otherwise make genuine input look older than the
        # gap and silently under-count it. The epsilon absorbs tick quantisation only.
        had_input = idle < (elapsed_s + config.INPUT_SAMPLE_EPSILON_S)

        window_s = config.INPUT_ACTIVITY_WINDOW_MS / 1000.0
        self._input_window.append((elapsed_s, had_input))
        total = sum(gap for gap, _ in self._input_window)
        # Keep the shortest suffix that still spans the window, so the ratio is measured over
        # `window_s` of real time rather than over a fixed number of samples.
        while len(self._input_window) > 1 and (total - self._input_window[0][0]) >= window_s:
            total -= self._input_window.popleft()[0]

        active_s = sum(gap for gap, hit in self._input_window if hit)
        ratio = (active_s / total) if total > 0.0 else 0.0
        self._input_ratio = ratio

        if self._input_active:
            # Exit on plain silence. A ratio would be the wrong shape here: "nothing at all has
            # happened for a while" is already one-sided, and reading it straight off the idle
            # counter makes the grace period exactly as long as config says it is.
            if idle >= config.INPUT_IDLE_GRACE_MS / 1000.0:
                self._set_input_active(False)
        else:
            # Enter only on a *full* window, so the very first sub-sample after start-up cannot
            # latch on, and only above the ratio, so one twitch in passing cannot either.
            if total >= window_s and ratio >= config.INPUT_ACTIVITY_ENTER_RATIO:
                self._set_input_active(True)

    def _set_input_active(self, active: bool) -> None:
        """Emit on an edge only. **Worker thread.**"""
        if active == self._input_active:
            return
        self._input_active = active
        log.debug("sustained interaction -> %s (ratio %.2f)",
                  "active" if active else "idle", self._input_ratio or 0.0)
        self.input_activity_changed.emit(active)

    # -- guards ---------------------------------------------------------------------------

    def _assert_worker_thread(self) -> None:
        """Fail loudly if worker code ever executes on the GUI thread. Debug builds only."""
        if not __debug__:  # pragma: no cover
            return
        app = QCoreApplication.instance()
        if app is None:
            return
        assert QThread.currentThread() is not app.thread(), (
            "telemetry worker code ran on the GUI thread -- the QThread-subclass defect is back"
        )


# =============================================================================
# SERVICE  (the only public surface)
# =============================================================================


class TelemetryService(QObject):
    """Owns the telemetry thread and re-emits its samples on the GUI thread.

    Construct, connect, ``start()`` and ``stop()`` from the **GUI thread only**.
    ``metrics_ready`` is emitted on the GUI thread, so receivers may touch widgets freely.
    """

    metrics_ready = pyqtSignal(object)  # payload: Metrics
    #: Latched "the user is interacting with this machine", from the worker's own fast
    #: sub-sampler. Separate from ``metrics_ready`` on purpose: gating the state transition on
    #: the 1500 ms payload would have made entering it feel broken. See the module docstring --
    #: this is sustained *interaction*, not typing.
    input_activity_changed = pyqtSignal(bool)

    def __init__(self, parent: QObject | None = None,
                 interval_ms: int = config.POLL_INTERVAL_MS) -> None:
        super().__init__(parent)
        self._interval_ms = _clamp_interval(interval_ms)
        self._thread: QThread | None = None
        self._worker: _TelemetryWorker | None = None
        self._last: Metrics | None = None
        self._input_active: bool = False

    # -- lifecycle ------------------------------------------------------------------------

    def start(self) -> None:
        """Spin up the worker thread. Idempotent. **GUI thread.**"""
        if self._thread is not None:
            log.debug("TelemetryService.start() ignored; already running")
            return

        worker = _TelemetryWorker(self._interval_ms)
        thread = QThread()
        thread.setObjectName("lc-pet-telemetry")
        worker.moveToThread(thread)

        # Automatic connection + differing thread affinity == queued, so the payload is handed
        # over through the GUI thread's event loop rather than shared live.
        worker.metrics_ready.connect(self._on_worker_metrics)
        worker.input_activity_changed.connect(self._on_worker_input_activity)
        thread.started.connect(worker.prime)
        thread.finished.connect(worker.deleteLater)

        self._thread = thread
        self._worker = worker
        thread.start()
        log.info("telemetry started at %d ms", self._interval_ms)

    def stop(self) -> None:
        """Shut the worker thread down cleanly. Idempotent -- a second call is a no-op.

        **GUI thread.** Sequence is: block until the worker has stopped its own timer, quit its
        event loop, then join with a bounded wait. This is the fix for the prototype's
        ``while True`` run loop, which had no way to exit at all.
        """
        thread = self._thread
        worker = self._worker
        if thread is None:
            return  # idempotent: already stopped, or never started
        self._thread = None
        self._worker = None

        if worker is not None and thread.isRunning():
            # Blocking so the timer is definitely dead before we ask the loop to quit;
            # the worker's stop() runs on the worker thread, where the timer actually lives.
            QMetaObject.invokeMethod(worker, "stop", Qt.ConnectionType.BlockingQueuedConnection)

        thread.quit()
        if not thread.wait(config.TELEMETRY_STOP_TIMEOUT_MS):
            log.error("telemetry thread did not exit within %d ms; terminating",
                      config.TELEMETRY_STOP_TIMEOUT_MS)
            thread.terminate()
            thread.wait(config.TELEMETRY_STOP_TIMEOUT_MS)
        else:
            log.info("telemetry stopped")

    # -- configuration --------------------------------------------------------------------

    def set_interval(self, ms: int) -> None:
        """Change the poll interval, live if running. **GUI thread.**"""
        self._interval_ms = _clamp_interval(ms)
        worker = self._worker
        if worker is not None:
            QMetaObject.invokeMethod(worker, "set_interval", Qt.ConnectionType.QueuedConnection,
                                     Q_ARG(int, self._interval_ms))

    def interval_ms(self) -> int:
        """The clamped poll interval currently in effect. **GUI thread.**"""
        return self._interval_ms

    def is_running(self) -> bool:
        """Whether the worker thread is up. **GUI thread.**"""
        return self._thread is not None

    @property
    def input_active(self) -> bool:
        """Whether the worker currently reports sustained interaction. **GUI thread.**"""
        return self._input_active

    @property
    def last_metrics(self) -> Metrics | None:
        """The most recent sample, or ``None`` before the first tick. **GUI thread.**

        Read this instead of calling ``psutil.cpu_percent()`` yourself -- see the module
        docstring's invariant.
        """
        return self._last

    # -- internals ------------------------------------------------------------------------

    @pyqtSlot(object)
    def _on_worker_metrics(self, metrics: object) -> None:
        """Re-emit a worker sample. **GUI thread** (queued from the worker)."""
        if not isinstance(metrics, Metrics):  # pragma: no cover - defensive
            log.error("telemetry received %r, not Metrics; dropping", type(metrics).__name__)
            return
        self._last = metrics
        self.metrics_ready.emit(metrics)

    @pyqtSlot(bool)
    def _on_worker_input_activity(self, active: bool) -> None:
        """Re-emit the worker's interaction latch. **GUI thread** (queued from the worker)."""
        self._input_active = bool(active)
        self.input_activity_changed.emit(self._input_active)
