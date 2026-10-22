"""`src/core/telemetry.py` -- the background metrics thread.

Most of this module can only be tested by actually running it, so most of this file is marked
`slow`. The fast part is what can be reasoned about without a clock: the `Metrics` payload's
shape, the interval clamp, and the provider degradation path -- which is exercised by *blocking
the pynvml import* rather than by finding a machine without an NVIDIA card, so it runs
everywhere and always takes the branch it means to.

Tolerances for the timing tests are derived from PHASE1-LOG.md's measured numbers, with an
order of magnitude of headroom:

| Measured | Asserted here |
| --- | --- |
| clean shutdown 15.03 ms (budget 2000 ms) | < 250 ms, i.e. 16x the measurement |
| second `stop()` 0.003 ms | < 50 ms |
| poll interval 1508 / 1525 ms vs 1500 ms target (~1.5% error) | +/- 40% of the interval |

The poll test runs at `POLL_INTERVAL_MIN_MS` rather than the shipped 1500 ms. That is not a
shortcut: the bug it guards pinned the rate at a *floor* of 1000 ms, so a 500 ms target
discriminates far more sharply than a 1500 ms one, and the whole test takes two seconds.
"""

from __future__ import annotations

import builtins
import logging
import threading
import time
from collections.abc import Callable

import pytest

from src import config
from src.core import telemetry as tel
from src.core.telemetry import BatterySample, GpuSample, Metrics, TelemetryService


@pytest.fixture
def service(qt_cleanup: Callable[[object], object]) -> Callable[..., TelemetryService]:
    """Factory for a `TelemetryService` that is always stopped at the end of the test.

    A leaked worker keeps polling psutil for the rest of the session and adds jitter to every
    later shutdown measurement, so this is a correctness fixture, not politeness.
    """

    def _make(interval_ms: int = config.POLL_INTERVAL_MS) -> TelemetryService:
        return qt_cleanup(TelemetryService(interval_ms=interval_ms))  # type: ignore[return-value]

    return _make


# =============================================================================================
# REGRESSIONS
# =============================================================================================


@pytest.mark.slow
def test_regression_stop_completes_well_inside_its_budget(
        service: Callable[..., TelemetryService], pump: Callable[[int], None]) -> None:
    """Guards `TelemetryThread.run` being `while True` with no stop flag.

    PHASE1-LOG.md defect 5: the prototype's thread had no exit path at all, so the process hung
    on quit. The fix is a worker object on a plain `QThread` driven by a `QTimer` on the
    worker's own event loop -- shutdown is then `invokeMethod(stop, Blocking)` + `quit()` +
    `wait()` against an idle loop, which is nearly instantaneous.

    Measured at 15.03 ms against a 2000 ms budget. Asserted at 250 ms: a regression to a
    busy-wait shows up as a full `TELEMETRY_STOP_TIMEOUT_MS` timeout, which is 8x this bound,
    so the test discriminates cleanly without being tight.
    """
    svc = service()
    svc.start()
    pump(1200)          # let it prime, probe providers and take a sample or two
    assert svc.is_running() is True

    started = time.perf_counter()
    svc.stop()
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert svc.is_running() is False
    assert elapsed_ms < 250.0, (
        f"stop() took {elapsed_ms:.1f} ms; budget is {config.TELEMETRY_STOP_TIMEOUT_MS} ms and "
        "the measured baseline is ~15 ms")


@pytest.mark.slow
def test_regression_a_second_stop_is_a_no_op(
        service: Callable[..., TelemetryService], pump: Callable[[int], None]) -> None:
    """`stop()` is idempotent (contract 4.3), so quit paths may call it more than once.

    `main.py` wires `aboutToQuit -> telemetry.stop` *and* calls it explicitly in `_quit`, so
    the double call is the normal path, not a defensive hypothetical.
    """
    svc = service()
    svc.start()
    pump(800)
    svc.stop()

    started = time.perf_counter()
    svc.stop()
    second_ms = (time.perf_counter() - started) * 1000.0
    svc.stop()  # and a third, for good measure

    assert second_ms < 50.0, f"second stop() took {second_ms:.3f} ms; measured baseline 0.003 ms"
    assert svc.is_running() is False


@pytest.mark.slow
def test_regression_polling_is_not_pinned_by_a_blocking_cpu_percent(
        service: Callable[..., TelemetryService], pump: Callable[[int], None]) -> None:
    """Guards `psutil.cpu_percent(interval=1)`, which blocks for a second inside the poll.

    PHASE1-LOG.md defect 6: the blocking form pins the poll rate at >= 1 s no matter what
    interval is configured, so `POLL_INTERVAL_MS` becomes decorative. The fix is the
    non-blocking `interval=None` form plus one discarded priming call, with the rate coming
    solely from the timer.

    Run at `POLL_INTERVAL_MIN_MS` (500 ms) precisely because the old bug's floor was 1000 ms:
    at 500 ms a regression doubles every gap and is unmissable, where at 1500 ms it would
    barely show. The first gap is skipped -- `FIRST_SAMPLE_DELAY_MS` makes it deliberately
    short so the card populates quickly.
    """
    interval = config.POLL_INTERVAL_MIN_MS
    svc = service(interval_ms=interval)
    stamps: list[float] = []
    samples: list[Metrics] = []
    svc.metrics_ready.connect(lambda m: (stamps.append(time.perf_counter()), samples.append(m)))

    svc.start()
    pump(int(config.FIRST_SAMPLE_DELAY_MS + interval * 5.5))
    svc.stop()

    assert len(stamps) >= 4, f"only {len(stamps)} samples in ~3 s at a {interval} ms interval"
    gaps = [(b - a) * 1000.0 for a, b in zip(stamps[1:], stamps[2:])]
    median = sorted(gaps)[len(gaps) // 2]
    assert interval * 0.6 <= median <= interval * 1.4, (
        f"median inter-sample gap {median:.0f} ms against a {interval} ms interval; gaps={gaps}. "
        "A gap pinned near 1000 ms means the blocking cpu_percent(interval=1) is back.")


@pytest.mark.slow
def test_regression_cpu_percent_is_primed_so_readings_are_real(
        service: Callable[..., TelemetryService], pump: Callable[[int], None]) -> None:
    """The first `cpu_percent()` call in a process returns 0.0 and must be discarded.

    Without `prime()`'s throwaway call the very first sample reads a fake 0.0% -- which is not
    merely cosmetic: it feeds the state machine's `ready-pleased` spike latch and its calm-sample
    counter, so a fake idle sample silently corrupts an edge detector.

    The honest form of the assertion is "at least one sample after the first reports real CPU
    use", not "sample[1] is exactly non-zero": a genuinely idle machine can legitimately report
    0.0 for one 500 ms window, and a test that flakes once a fortnight teaches people to re-run
    rather than to look. Over five windows with a test runner working, zero everywhere means
    the priming call is gone.
    """
    svc = service(interval_ms=config.POLL_INTERVAL_MIN_MS)
    samples: list[Metrics] = []
    svc.metrics_ready.connect(samples.append)
    svc.start()
    pump(config.FIRST_SAMPLE_DELAY_MS + config.POLL_INTERVAL_MIN_MS * 5)
    svc.stop()

    assert len(samples) >= 3
    later = samples[1:]
    assert any(s.cpu_percent > 0.0 for s in later), (
        f"every sample after the first read 0.0% CPU: {[s.cpu_percent for s in later]}")
    assert all(0.0 <= s.cpu_percent <= 100.0 for s in samples)


# =============================================================================================
# Threading model
# =============================================================================================


@pytest.mark.slow
def test_metrics_arrive_on_the_gui_thread(
        service: Callable[..., TelemetryService], pump: Callable[[int], None]) -> None:
    """Contract 4.3: a worker object moved to a plain `QThread`, never a `QThread` subclass.

    A `QThread` subclass's slots run in the *creating* thread, which silently reintroduces
    cross-thread psutil calls and cross-thread widget access. The observable consequence of
    getting it right is that `metrics_ready` is delivered on the GUI thread -- so that is what
    is asserted, rather than the class hierarchy.
    """
    main_thread = threading.get_ident()
    receiving: set[int] = set()

    svc = service(interval_ms=config.POLL_INTERVAL_MIN_MS)
    svc.metrics_ready.connect(lambda _m: receiving.add(threading.get_ident()))
    svc.start()
    pump(config.FIRST_SAMPLE_DELAY_MS + config.POLL_INTERVAL_MIN_MS * 2)
    svc.stop()

    assert receiving == {main_thread}, f"delivered on {receiving}, GUI thread is {main_thread}"


@pytest.mark.slow
def test_start_is_idempotent_and_restart_after_stop_works(
        service: Callable[..., TelemetryService], pump: Callable[[int], None]) -> None:
    """Two `start()`s must not give two worker threads, and a stopped service must restart."""
    svc = service(interval_ms=config.POLL_INTERVAL_MIN_MS)
    first: list[Metrics] = []
    svc.metrics_ready.connect(first.append)

    svc.start()
    svc.start()
    pump(config.FIRST_SAMPLE_DELAY_MS + config.POLL_INTERVAL_MIN_MS * 3)
    svc.stop()
    count_one_worker = len(first)
    assert 2 <= count_one_worker <= 6, (
        f"{count_one_worker} samples in ~2 s at {config.POLL_INTERVAL_MIN_MS} ms -- more than "
        "one worker is polling")

    first.clear()
    svc.start()
    pump(config.FIRST_SAMPLE_DELAY_MS + config.POLL_INTERVAL_MIN_MS * 2)
    svc.stop()
    assert first, "the service did not restart after stop()"


@pytest.mark.slow
def test_last_metrics_tracks_the_most_recent_sample(
        service: Callable[..., TelemetryService], pump: Callable[[int], None]) -> None:
    """`last_metrics` is what the card paints from before the first tick of a fresh poll."""
    svc = service(interval_ms=config.POLL_INTERVAL_MIN_MS)
    samples: list[Metrics] = []
    svc.metrics_ready.connect(samples.append)
    svc.start()
    pump(config.FIRST_SAMPLE_DELAY_MS + config.POLL_INTERVAL_MIN_MS * 2)
    svc.stop()

    assert samples
    assert svc.last_metrics is samples[-1]
    assert all(a.timestamp < b.timestamp for a, b in zip(samples, samples[1:]))


@pytest.mark.slow
def test_a_real_sample_is_plausible(
        service: Callable[..., TelemetryService], pump: Callable[[int], None]) -> None:
    """One end-to-end sanity pass over the actual numbers this machine reports.

    Deliberately loose. Anything tighter is an assertion about the developer's hardware, and
    those belong in tests/_env.py as skips rather than in the body of a test.
    """
    svc = service(interval_ms=config.POLL_INTERVAL_MIN_MS)
    samples: list[Metrics] = []
    svc.metrics_ready.connect(samples.append)
    svc.start()
    pump(config.FIRST_SAMPLE_DELAY_MS + config.POLL_INTERVAL_MIN_MS * 3)
    svc.stop()

    latest = samples[-1]
    assert 0.0 < latest.ram_percent <= 100.0
    assert latest.ram_total_bytes >= 2 ** 30, "less than 1 GiB of RAM is not a real reading"
    assert 0 < latest.ram_used_bytes <= latest.ram_total_bytes
    assert 0.0 <= latest.swap_percent <= 100.0
    assert latest.disk_percent is None or 0.0 <= latest.disk_percent <= 100.0
    assert latest.idle_seconds is None or latest.idle_seconds >= 0.0


# =============================================================================================
# Degradation
# =============================================================================================


def test_gpu_provider_degrades_to_null_when_pynvml_is_absent(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The no-NVIDIA path, forced rather than found.

    The dev machine has an RTX 3070, so this branch would otherwise never be taken here -- and
    it is the branch every AMD/Intel user gets. Blocking the import inside `_probe_gpu_provider`
    is enough because the module imports `pynvml` lazily, inside the probe, precisely so an
    absent or broken driver cannot affect start-up.
    """
    real_import = builtins.__import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name == "pynvml":
            raise ImportError("simulated: no pynvml on this machine")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    provider = tel._probe_gpu_provider()
    monkeypatch.undo()

    assert isinstance(provider, tel.NullGpuProvider)
    assert provider.sample() is None
    provider.close()  # must not raise


def test_null_providers_never_raise() -> None:
    """Contract: a provider's `close()` must be safe to call, repeatedly, on any path."""
    gpu = tel.NullGpuProvider()
    temp = tel.NullTempProvider()
    assert gpu.sample() is None
    assert temp.sample() is None
    gpu.close()
    gpu.close()
    temp.close()
    temp.close()


def test_temperature_is_never_synthesised() -> None:
    """`None` means unknown. Substituting a plausible number would make the alarm a fiction.

    `psutil.sensors_temperatures` does not exist as an attribute on Windows, so the probe
    returns the null provider and `cpu_temp_c` stays `None` forever -- the designed outcome,
    which is why the thermal band is written to treat `None` as "does not hold".
    """
    provider = tel._probe_temp_provider()
    sample = provider.sample()
    assert sample is None or isinstance(sample, float)
    provider.close()


# =============================================================================================
# Payload and interval plumbing
# =============================================================================================


def test_metrics_is_immutable(make_metrics: Callable[..., Metrics]) -> None:
    """Frozen + slots: the one object crossing the thread boundary cannot be edited after."""
    metrics = make_metrics()
    with pytest.raises(Exception):
        metrics.cpu_percent = 99.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        metrics.brand_new_field = 1  # type: ignore[attr-defined]


def test_metrics_placeholder_is_all_unknowns_not_all_zeroes() -> None:
    """The pre-first-tick sample. Optional fields are `None`, so the card omits their rows."""
    placeholder = Metrics.placeholder()
    assert placeholder.cpu_percent == 0.0
    assert placeholder.disk_percent is None
    assert placeholder.cpu_temp_c is None
    assert placeholder.gpu is None
    assert placeholder.battery is None
    assert placeholder.idle_seconds is None


def test_input_activity_ratio_is_an_optional_field() -> None:
    """Added after contract 4.3 was written, so it must default.

    Every existing caller builds `Metrics` by keyword; a required field here would have been a
    breaking change to a dozen construction sites (including this suite's own factory).
    """
    metrics = Metrics(
        timestamp=0.0, cpu_percent=1.0, ram_percent=2.0, ram_used_bytes=1, ram_total_bytes=2,
        swap_percent=0.0, disk_percent=None, cpu_temp_c=None, gpu=None, battery=None,
        idle_seconds=None)
    assert metrics.input_activity_ratio is None


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (50, config.POLL_INTERVAL_MIN_MS),
        (99_999, config.POLL_INTERVAL_MAX_MS),
        (600, 600),
        (config.POLL_INTERVAL_MIN_MS, config.POLL_INTERVAL_MIN_MS),
        (config.POLL_INTERVAL_MAX_MS, config.POLL_INTERVAL_MAX_MS),
    ],
)
def test_interval_is_clamped_into_the_supported_range(
        service: Callable[..., TelemetryService], requested: int, expected: int) -> None:
    """A poll every 50 ms would busy-spin psutil; one every 100 s is not telemetry."""
    svc = service()
    svc.set_interval(requested)
    assert svc.interval_ms() == expected


@pytest.mark.slow
def test_a_failing_sample_logs_and_skips_rather_than_killing_the_poller(
        service: Callable[..., TelemetryService], pump: Callable[[int], None],
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Contract 4.3: one bad `sample()` logs at ERROR and skips that tick, forever if need be.

    psutil raises OSError and AccessDenied variants that differ per Windows build; one
    unreadable counter must not stop the pet from animating for the rest of the session.

    Driven through the real service rather than by poking `_tick()` directly, because
    `_assert_worker_thread` -- rightly -- refuses to let worker code run on the GUI thread, and
    defeating that guard to test the code beneath it would defeat the guard. Breaking
    `psutil.virtual_memory` reaches `_sample` on the worker thread exactly as a real counter
    failure would; `prime()` uses `cpu_percent` and `sensors_battery`, which stay intact, so
    start-up still succeeds and it is genuinely the poll that fails.

    The "does not kill the poller" half is the interesting one: several ERRORs over several
    intervals prove the timer went on firing after the first failure.
    """
    def broken(*_args: object, **_kwargs: object) -> object:
        raise OSError("simulated counter failure")

    monkeypatch.setattr(tel.psutil, "virtual_memory", broken)

    svc = service(interval_ms=config.POLL_INTERVAL_MIN_MS)
    emitted: list[object] = []
    svc.metrics_ready.connect(emitted.append)

    with caplog.at_level(logging.ERROR, logger="src.core.telemetry"):
        svc.start()
        pump(config.FIRST_SAMPLE_DELAY_MS + config.POLL_INTERVAL_MIN_MS * 3)
        svc.stop()

    assert emitted == [], "a failed sample must not emit a Metrics"
    failures = [r for r in caplog.records if "skipping this tick" in r.getMessage()]
    assert len(failures) >= 2, (
        f"{len(failures)} logged failures -- the poll timer stopped after the first one")
    assert svc.last_metrics is None


def test_gpu_and_battery_samples_are_plain_immutable_records() -> None:
    """Both are frozen dataclasses whose fields may each independently be `None`.

    `GpuSample(name=..., everything else None)` is the "the card is there but this sample
    failed" shape the stats card has to tell apart from `Metrics.gpu is None`.
    """
    gpu = GpuSample("RTX 3070", None, None, None, None, None)
    assert gpu.name == "RTX 3070"
    assert gpu.load_percent is None
    with pytest.raises(Exception):
        gpu.name = "other"  # type: ignore[misc]

    battery = BatterySample(55.0, True, 3600)
    assert battery.plugged is True
    with pytest.raises(Exception):
        battery.percent = 10.0  # type: ignore[misc]
