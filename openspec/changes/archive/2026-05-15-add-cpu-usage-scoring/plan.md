# Add CPU Usage Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 10-point CPU usage sub-score (H.265 round only, contestant PGID, all-cores-total normalization, parameterized thresholds) to the evaluator's `score.json`, raising `max_score` from 30 to 40.

**Architecture:** Sampler is a daemon thread embedded in `runner.py`, gated on `--codec h265 --contestant-pgid <PGID>`. It enumerates `/proc/[0-9]*/stat` whose field-6 `session` equals the PGID (works because `clx_start_contestant` uses `setsid`, so SID = PGID = `CONTESTANT_PID`), and writes `<screenshots_dir>/capture_meta.json`. `analyzer.py` passes the `cpu` block through verbatim into `<codec>_metrics.json`. `scorer.py` adds `score_cpu()` with 5 ordered branches (gate / no-data / full / zero / partial-linear-clamp) and 5 named constants. All effective thresholds are echoed into `score.json.cpu.thresholds_used` for audit.

**Tech Stack:** Python 3.12 stdlib only for the sampler (`/proc/[0-9]*/stat`, `threading.Thread`, `threading.Event`, `os.cpu_count()`, `os.sysconf("SC_CLK_TCK")`); `pytest` for unit tests (new dependency); existing Playwright capture loop in `runner.py` is the host for the sampler lifecycle.

**Reference artifacts:**
- Delta spec: `openspec/changes/add-cpu-usage-scoring/specs/evaluator/spec.md`
- Design: `openspec/changes/add-cpu-usage-scoring/design.md`
- Brainstorm: `openspec/changes/add-cpu-usage-scoring/brainstorm.md`
- Task checklist: `openspec/changes/add-cpu-usage-scoring/tasks.md`

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `_cpu_sampler.py` | **CREATE** | Pure-stdlib `Sampler` class + `SampleResult` dataclass + `DEFAULT_SAMPLE_HZ` constant |
| `scorer.py` | MODIFY | Add 5 tunable constants + `score_cpu()` + extend `build_score()` + bump `max_score` to 40 + docstring |
| `runner.py` | MODIFY | Add `--contestant-pgid`/`--cpu-sample-hz` CLI flags + try/finally around sampler + write `capture_meta.json` |
| `analyzer.py` | MODIFY | Pass `cpu` sub-object from `capture_meta.json` into output metrics JSON |
| `report.py` | MODIFY | Render CPU summary block (no timeline) |
| `scripts/evaluator.sh` | MODIFY | Append `--contestant-pgid "$(cat …/contestant.pid)"` to the H.265 runner invocation only |
| `requirements.txt` | MODIFY | Add `pytest` |
| `tests/__init__.py` | CREATE (empty) | Marker for pytest test discovery |
| `tests/test_score_cpu.py` | **CREATE** | Table-driven `score_cpu` cases + gate-reason coverage + `thresholds_used` round-trip |
| `tests/test_cpu_sampler.py` | **CREATE** | Spin-CPU subprocess fixture + vanishing-PID edge case |
| `CLAUDE.md` | MODIFY | "30-point" → "40-point" and add a sentence about CPU evaluation scope |
| `openspec/specs/evaluator/spec.md::Purpose` | (not touched here) | Synced at `/opsx:archive` time per project CLAUDE.md rule |

---

## Task 1: Test scaffolding (failing tests for `score_cpu`)

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/test_score_cpu.py`

- [ ] **Step 1: Add `pytest` to `requirements.txt`**

Append after the existing pinned packages:

```
pytest==8.3.3
```

- [ ] **Step 2: Install pytest into the venv**

Run:
```bash
.venv/bin/pip install pytest==8.3.3
```
Expected: `Successfully installed pytest-8.3.3 …`

- [ ] **Step 3: Create empty `tests/__init__.py`**

```bash
mkdir -p tests && touch tests/__init__.py
```

- [ ] **Step 4: Write the failing test file `tests/test_score_cpu.py`**

```python
"""Table-driven tests for scorer.score_cpu and the cpu block in build_score."""

from __future__ import annotations

import pytest

import scorer


H265_EXPECTED = scorer.EXPECTED_FPS["h265"]


# Helpers --------------------------------------------------------------------

def fps_at(ratio: float) -> float:
    """Return a measured_h265_fps that yields measured/expected == ratio."""
    return H265_EXPECTED * ratio


# Score table covers the published mapping. -----------------------------------

@pytest.mark.parametrize(
    "mean_cpu, expected_points",
    [
        (0.0, 10),
        (4.99, 10),
        (5.0, 10),
        (5.5, 10),
        (6.0, 10),
        (7.0, 9),
        (8.0, 9),
        (9.0, 8),
        (10.0, 7),
        (11.0, 6),
        (12.0, 6),
        (13.0, 5),
        (14.0, 4),
        (15.0, 4),
        (16.0, 3),
        (17.0, 2),
        (18.0, 1),
        (19.0, 1),
        (20.0, 0),
        (20.001, 0),
        (99.0, 0),
    ],
)
def test_score_cpu_table(mean_cpu, expected_points):
    points, reason = scorer.score_cpu(
        mean_cpu_percent=mean_cpu,
        measured_h265_fps=fps_at(0.9),  # well above gate
    )
    assert points == expected_points
    assert reason is None


def test_score_cpu_gates_when_fps_below_threshold():
    # 0.24 < 0.25 → gate trips, returns (0, "h265_fps_below_threshold")
    points, reason = scorer.score_cpu(
        mean_cpu_percent=2.0,
        measured_h265_fps=fps_at(0.24),
    )
    assert points == 0
    assert reason == "h265_fps_below_threshold"


def test_score_cpu_gates_when_sampler_missing():
    points, reason = scorer.score_cpu(
        mean_cpu_percent=None,
        measured_h265_fps=fps_at(0.9),
    )
    assert points == 0
    assert reason == "sampler_no_data"


def test_score_cpu_gate_takes_precedence_over_value():
    # Even at 0% CPU, a failing fps round should not award CPU points.
    points, reason = scorer.score_cpu(
        mean_cpu_percent=0.0,
        measured_h265_fps=fps_at(0.10),
    )
    assert points == 0
    assert reason == "h265_fps_below_threshold"


# build_score wiring ---------------------------------------------------------

def _h264_full() -> dict:
    return {
        "watermark_recognition_rate": 1.0,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "measured_fps": 30.0,
    }


def _h265_full_with_cpu(mean_cpu: float | None) -> dict:
    block = {
        "watermark_recognition_rate": 1.0,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "measured_fps": 25.0,
    }
    if mean_cpu is not None:
        block["cpu"] = {
            "mean_percent": mean_cpu,
            "sample_count": 9,
            "sample_window_ms": 9000,
            "ncpu": 8,
            "clk_tck": 100,
            "normalization": "all_cores_total",
            "pgid": 12345,
            "sample_hz_used": 1.0,
        }
    return block


def test_build_score_max_score_is_40():
    out = scorer.build_score(
        _h264_full(), _h265_full_with_cpu(2.0), chromium_version="test",
    )
    assert out["max_score"] == 40


def test_build_score_objective_total_includes_cpu():
    out = scorer.build_score(
        _h264_full(), _h265_full_with_cpu(2.0), chromium_version="test",
    )
    assert out["objective_total"] == 15 + 15 + 10
    assert out["cpu"]["points"] == 10
    assert out["cpu"]["gated"] is False
    assert out["cpu"]["gate_reason"] is None
    assert out["cpu"]["measured_on_codec"] == "h265"


def test_build_score_records_thresholds_used():
    out = scorer.build_score(
        _h264_full(), _h265_full_with_cpu(2.0), chromium_version="test",
    )
    t = out["cpu"]["thresholds_used"]
    assert set(t.keys()) == {
        "gate_fps_ratio",
        "full_percent",
        "partial_start_percent",
        "zero_percent",
        "min_samples",
        "sample_hz",
    }
    assert t["gate_fps_ratio"] == scorer.CPU_GATE_H265_FPS_RATIO
    assert t["full_percent"] == scorer.CPU_FULL_THRESHOLD_PERCENT
    assert t["partial_start_percent"] == scorer.CPU_PARTIAL_START_PERCENT
    assert t["zero_percent"] == scorer.CPU_ZERO_THRESHOLD_PERCENT
    assert t["min_samples"] == scorer.CPU_MIN_SAMPLES
    assert t["sample_hz"] == 1.0  # the value in the fixture cpu block


def test_build_score_h265_round_failed_gates_cpu():
    out = scorer.build_score(
        _h264_full(), None, chromium_version="test",
    )
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "h265_round_failed"
    assert out["cpu"]["mean_percent"] is None


def test_build_score_sampler_no_data_gates_cpu():
    # h265 metrics present but cpu missing
    out = scorer.build_score(
        _h264_full(), _h265_full_with_cpu(None), chromium_version="test",
    )
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "sampler_no_data"


def test_build_score_container_mode_unsupported():
    out = scorer.build_score(
        _h264_full(),
        _h265_full_with_cpu(None),
        chromium_version="test",
        failure_reason=None,
        cpu_override_reason="container_mode_unsupported",
    )
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "container_mode_unsupported"
```

- [ ] **Step 5: Run the new tests and confirm they FAIL with `AttributeError` / `TypeError`**

Run:
```bash
.venv/bin/python -m pytest tests/test_score_cpu.py -x -q
```
Expected: failures citing `AttributeError: module 'scorer' has no attribute 'score_cpu'` (and similar for the new constants and the `cpu_override_reason` param). This proves the test scaffold is wired up.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt tests/__init__.py tests/test_score_cpu.py
git commit -m "test(scorer): add failing tests for score_cpu + 40-point build_score contract"
```

---

## Task 2: Test scaffolding (failing tests for `_cpu_sampler`)

**Files:**
- Create: `tests/test_cpu_sampler.py`

- [ ] **Step 1: Write `tests/test_cpu_sampler.py` with a spin-CPU subprocess fixture**

```python
"""Tests for _cpu_sampler.Sampler against a real spinning subprocess."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

import _cpu_sampler


SPIN_SCRIPT = (
    "import os, sys, time;"
    "os.setsid();"
    "sys.stdout.write(str(os.getpid()));"
    "sys.stdout.flush();"
    "x = 0\n"
    "while True:\n"
    "    x = (x + 1) % 1000000\n"
)


@pytest.fixture
def spin_pgid():
    """Spawn a Python subprocess that calls setsid() then spins a CPU.

    Yields the PGID (== child PID under setsid). Teardown sends SIGTERM then
    SIGKILL to the whole process group.
    """
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", SPIN_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    # First line of stdout is the child's PID, which equals its SID/PGID
    # because the child called setsid() before printing.
    pid_str = proc.stdout.readline().strip()
    assert pid_str, "spin helper failed to report its PID"
    pgid = int(pid_str)
    try:
        yield pgid
    finally:
        try:
            os.killpg(pgid, signal.SIGTERM)
            time.sleep(0.1)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=2)


def test_sampler_measures_single_core_spin(spin_pgid):
    """One pinned-core spin should land near 100/ncpu percent in all-cores-total."""
    sampler = _cpu_sampler.Sampler(pgid=spin_pgid, hz=4.0)
    sampler.start()
    time.sleep(2.0)
    result = sampler.stop()

    ncpu = os.cpu_count() or 1
    expected_pct = 100.0 / ncpu
    assert result.sample_count >= 4, f"got {result.sample_count} samples"
    assert result.mean_percent is not None
    assert result.pgid == spin_pgid
    assert result.normalization == "all_cores_total"
    # Wide tolerance — spinning Python BCE under interpreter overhead may dip.
    assert expected_pct - 3.0 <= result.mean_percent <= expected_pct + 3.0, (
        f"mean_percent={result.mean_percent}, expected≈{expected_pct}"
    )


def test_sampler_no_data_when_below_min_samples():
    """A very short window should report mean_percent=None instead of garbage."""
    sampler = _cpu_sampler.Sampler(pgid=os.getpid(), hz=0.5)
    sampler.start()
    time.sleep(0.05)  # too short for 2 ticks at 0.5 Hz
    result = sampler.stop()
    assert result.sample_count < _cpu_sampler.MIN_SAMPLES_FOR_MEAN
    assert result.mean_percent is None


def test_sampler_survives_vanishing_child(tmp_path):
    """Sampler must not crash when a PID disappears mid-sample."""
    # Spawn a short-lived child under our own session.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.3)"]
    )
    sampler = _cpu_sampler.Sampler(pgid=os.getpgid(0), hz=10.0)
    sampler.start()
    proc.wait()
    time.sleep(0.5)
    result = sampler.stop()
    # No assertion on mean — just that we got a clean stop without exception
    # and a non-negative value (or None).
    if result.mean_percent is not None:
        assert result.mean_percent >= 0.0


def test_sampler_skips_when_no_processes_match():
    """A bogus PGID with no matching processes yields no samples and no crash."""
    # 2^30 is well above any real PGID.
    sampler = _cpu_sampler.Sampler(pgid=2**30, hz=10.0)
    sampler.start()
    time.sleep(0.3)
    result = sampler.stop()
    assert result.mean_percent is None
```

- [ ] **Step 2: Run the new tests and confirm they FAIL with `ImportError`**

Run:
```bash
.venv/bin/python -m pytest tests/test_cpu_sampler.py -x -q
```
Expected: collection error — `ModuleNotFoundError: No module named '_cpu_sampler'`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cpu_sampler.py
git commit -m "test(cpu_sampler): add failing tests for /proc-based contestant CPU sampler"
```

---

## Task 3: Implement `_cpu_sampler.py`

**Files:**
- Create: `_cpu_sampler.py`

- [ ] **Step 1: Write the module skeleton with constants and dataclass**

Create `_cpu_sampler.py`:

```python
"""Contestant CPU usage sampler.

Background thread that periodically enumerates /proc/[0-9]*/stat for all
processes whose session ID (field 6 of stat) matches the contestant's PGID,
accumulates (utime + stime) jiffies, and reports a mean CPU percentage
normalized to the total of all cores (i.e., one core saturated == 100/ncpu %).

Precondition: the contestant is launched via setsid (see
scripts/_contestant_lifecycle.sh::clx_start_contestant), so SID == PGID ==
the recorded CONTESTANT_PID. Without that, the field-6 match would not
enumerate forked children.

Pure stdlib by design — no psutil, no pidstat — so the runtime requires no
new pip dependency.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SAMPLE_HZ: float = 1.0
"""Tunable: sampler tick rate (Hz). 1.0 gives ~10 samples over a 10s H.265
capture window — coarse but enough for a steady-state mean. Raise during
debugging via runner.py --cpu-sample-hz; fold back to a pure constant once
calibration is done."""


MIN_SAMPLES_FOR_MEAN: int = 3
"""Below this many ticks, the sampler refuses to compute a mean (returns None).
Independently echoed by scorer.CPU_MIN_SAMPLES."""


@dataclass
class SampleResult:
    mean_percent: float | None
    sample_count: int
    sample_window_ms: int
    ncpu: int
    clk_tck: int
    normalization: str
    pgid: int
    sample_hz_used: float

    def to_dict(self) -> dict:
        return {
            "mean_percent": self.mean_percent,
            "sample_count": self.sample_count,
            "sample_window_ms": self.sample_window_ms,
            "ncpu": self.ncpu,
            "clk_tck": self.clk_tck,
            "normalization": self.normalization,
            "pgid": self.pgid,
            "sample_hz_used": self.sample_hz_used,
        }
```

- [ ] **Step 2: Add the `/proc` enumeration helper**

Append to `_cpu_sampler.py`:

```python
def _read_stat(pid: int) -> tuple[int, int, int] | None:
    """Return (session, utime, stime) jiffies for `pid`, or None on any read
    error. We tolerate vanishing PIDs silently — they are expected mid-capture.

    /proc/<pid>/stat fields (1-indexed, per `man 5 proc`):
        1=pid, 2=comm (in parens, may contain spaces),
        3=state, 4=ppid, 5=pgrp, 6=session, ...
        14=utime, 15=stime
    """
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    # comm is between the first '(' and the LAST ')'. Slice it out then split
    # the rest on whitespace so a comm with spaces or parens does not shift
    # the indices.
    lparen = text.find("(")
    rparen = text.rfind(")")
    if lparen < 0 or rparen < 0:
        return None
    tail = text[rparen + 2:].split()
    if len(tail) < 13:
        return None
    # tail[0] is field 3 (state). Field 6 is tail[3]. utime is field 14 = tail[11],
    # stime is field 15 = tail[12].
    try:
        session = int(tail[3])
        utime = int(tail[11])
        stime = int(tail[12])
    except (IndexError, ValueError):
        return None
    return session, utime, stime


def _enumerate_pgid_pids(pgid: int) -> dict[int, int]:
    """Return {pid: utime+stime jiffies} for every PID whose session == pgid."""
    out: dict[int, int] = {}
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            stat = _read_stat(pid)
            if stat is None:
                continue
            session, utime, stime = stat
            if session == pgid:
                out[pid] = utime + stime
    except FileNotFoundError:
        pass
    return out
```

- [ ] **Step 3: Add the `Sampler` class**

Append to `_cpu_sampler.py`:

```python
class Sampler:
    """Daemon-thread CPU sampler bound to a single PGID.

    Lifecycle: ``s = Sampler(pgid, hz); s.start(); ...; result = s.stop()``.
    ``start`` is non-blocking. ``stop`` joins and returns a ``SampleResult``.

    Thread-safety: only ``start`` and ``stop`` are public; both are intended
    to be called from a single owning thread (runner.py's capture body).
    """

    def __init__(self, pgid: int, hz: float = DEFAULT_SAMPLE_HZ) -> None:
        if hz <= 0:
            raise ValueError(f"hz must be positive, got {hz}")
        self.pgid = pgid
        self.hz = hz
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ncpu = os.cpu_count() or 1
        self._clk_tck = os.sysconf("SC_CLK_TCK")
        # Tick accumulators — written only from the worker thread.
        self._t_start: float | None = None
        self._t_last: float | None = None
        self._sample_count = 0
        self._baseline: dict[int, int] = {}
        self._total_delta_jiffies = 0

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Sampler.start called twice")
        self._t_start = time.monotonic()
        self._baseline = _enumerate_pgid_pids(self.pgid)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        interval = 1.0 / self.hz
        while not self._stop.is_set():
            if self._stop.wait(timeout=interval):
                break
            self._tick()

    def _tick(self) -> None:
        now_pids = _enumerate_pgid_pids(self.pgid)
        # Sum deltas for PIDs known last round. New PIDs become baselines (no
        # delta credited this round — prevents historical CPU being charged).
        for pid, total in now_pids.items():
            prev = self._baseline.get(pid)
            if prev is None:
                continue
            delta = total - prev
            if delta > 0:
                self._total_delta_jiffies += delta
        # Vanished PIDs are simply dropped — their last contribution remains
        # in the running total because we already credited it on the tick
        # they were last seen.
        self._baseline = now_pids
        self._t_last = time.monotonic()
        self._sample_count += 1

    def stop(self) -> SampleResult:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        t_end = self._t_last or time.monotonic()
        t_start = self._t_start or t_end
        wall_s = max(0.0, t_end - t_start)
        mean: float | None
        if self._sample_count < MIN_SAMPLES_FOR_MEAN or wall_s <= 0:
            mean = None
        else:
            denom = wall_s * self._ncpu * self._clk_tck
            mean = (self._total_delta_jiffies / denom) * 100.0 if denom > 0 else None
        return SampleResult(
            mean_percent=mean,
            sample_count=self._sample_count,
            sample_window_ms=int(wall_s * 1000),
            ncpu=self._ncpu,
            clk_tck=self._clk_tck,
            normalization="all_cores_total",
            pgid=self.pgid,
            sample_hz_used=self.hz,
        )
```

- [ ] **Step 4: Run the sampler tests**

Run:
```bash
.venv/bin/python -m pytest tests/test_cpu_sampler.py -x -q
```
Expected: all 4 tests pass. If `test_sampler_measures_single_core_spin` flakes by 1–2 percentage points, that is acceptable noise — the assertion already allows ±3.

- [ ] **Step 5: Commit**

```bash
git add _cpu_sampler.py
git commit -m "feat(cpu_sampler): pure-stdlib /proc PGID sampler with daemon thread"
```

---

## Task 4: Implement `scorer.score_cpu` and extend `build_score`

**Files:**
- Modify: `scorer.py:1-187`

- [ ] **Step 1: Add tunable constants after `EXPECTED_FPS`**

In `scorer.py`, after line 16 (`EXPECTED_FPS = {…}`), insert:

```python

# CPU sub-score tunables. Module-level so calibration is a one-line change.
# Effective values applied to each run are echoed into score.json.cpu.thresholds_used.
CPU_GATE_H265_FPS_RATIO: float = 0.25
"""measured_h265_fps / expected_h265_fps below this → CPU score gated to 0.
Default mirrors score_fps's partial-credit threshold."""

CPU_FULL_THRESHOLD_PERCENT: float = 5.0
"""mean_cpu_percent at or below this → full 10 points."""

CPU_PARTIAL_START_PERCENT: float = 6.0
"""Anchor of the linear-decay partial-credit band; >5% to <6% clamps to 10."""

CPU_ZERO_THRESHOLD_PERCENT: float = 20.0
"""mean_cpu_percent above this → 0 points."""

CPU_MIN_SAMPLES: int = 3
"""Below this sample count, mean_cpu_percent is treated as missing."""
```

- [ ] **Step 2: Add `score_cpu` after `score_fps` (after current line 78)**

Insert:

```python


def score_cpu(
    mean_cpu_percent: float | None,
    measured_h265_fps: float,
    expected_h265_fps: float = EXPECTED_FPS["h265"],
    gate_fps_ratio: float = CPU_GATE_H265_FPS_RATIO,
) -> tuple[int, str | None]:
    """Map a measured CPU mean into 0-10 points with gating.

    Order of evaluation (first match wins):
        1. fps gate (round didn't really play) → 0, "h265_fps_below_threshold"
        2. mean missing/None → 0, "sampler_no_data"
        3. mean ≤ CPU_FULL_THRESHOLD_PERCENT → 10, None
        4. mean > CPU_ZERO_THRESHOLD_PERCENT  → 0, None
        5. partial band → linear decay anchored at PARTIAL_START / ZERO,
                          rounded, clamped to [0, 10]
    """
    if expected_h265_fps <= 0:
        return 0, "h265_fps_below_threshold"
    if measured_h265_fps / expected_h265_fps < gate_fps_ratio:
        return 0, "h265_fps_below_threshold"
    if mean_cpu_percent is None:
        return 0, "sampler_no_data"
    if mean_cpu_percent <= CPU_FULL_THRESHOLD_PERCENT:
        return 10, None
    if mean_cpu_percent > CPU_ZERO_THRESHOLD_PERCENT:
        return 0, None
    span = CPU_ZERO_THRESHOLD_PERCENT - CPU_PARTIAL_START_PERCENT
    raw = (CPU_ZERO_THRESHOLD_PERCENT - mean_cpu_percent) / span * 10.0
    return max(0, min(10, round(raw))), None
```

- [ ] **Step 3: Add a `_build_cpu_block` helper before `build_score` (before line 106)**

Insert:

```python


def _thresholds_used(sample_hz: float | None) -> dict:
    return {
        "gate_fps_ratio": CPU_GATE_H265_FPS_RATIO,
        "full_percent": CPU_FULL_THRESHOLD_PERCENT,
        "partial_start_percent": CPU_PARTIAL_START_PERCENT,
        "zero_percent": CPU_ZERO_THRESHOLD_PERCENT,
        "min_samples": CPU_MIN_SAMPLES,
        "sample_hz": sample_hz if sample_hz is not None else 0.0,
    }


def _build_cpu_block(
    h265_metrics: dict | None,
    cpu_override_reason: str | None,
) -> dict:
    """Assemble the score.json `cpu` sub-object.

    Precedence:
        1. cpu_override_reason (e.g. "container_mode_unsupported", "host_failure")
        2. h265_metrics is None                  → "h265_round_failed"
        3. h265_metrics["cpu"] missing/None      → "sampler_no_data"
        4. h265_metrics["cpu"]["sample_count"] < CPU_MIN_SAMPLES
                                                 → "sampler_no_data"
        5. normal scoring via score_cpu
    """
    block: dict = {
        "points": 0,
        "mean_percent": None,
        "sample_count": 0,
        "sample_window_ms": 0,
        "ncpu": None,
        "normalization": "all_cores_total",
        "measured_on_codec": "h265",
        "thresholds_used": _thresholds_used(None),
        "gated": True,
        "gate_reason": None,
    }
    if cpu_override_reason:
        block["gate_reason"] = cpu_override_reason
        return block
    if h265_metrics is None:
        block["gate_reason"] = "h265_round_failed"
        return block

    cpu_in = h265_metrics.get("cpu")
    measured_h265_fps = float(h265_metrics.get("measured_fps") or 0.0)
    if cpu_in is None:
        block["gate_reason"] = "sampler_no_data"
        # still record any data we have (none here)
        block["thresholds_used"] = _thresholds_used(None)
        # Apply gate logic anyway so a failing-fps round shows its real reason.
        points, reason = score_cpu(None, measured_h265_fps)
        block["points"] = points
        block["gate_reason"] = reason or "sampler_no_data"
        return block

    sample_count = int(cpu_in.get("sample_count") or 0)
    mean_pct = cpu_in.get("mean_percent")
    if isinstance(mean_pct, (int, float)) is False:
        mean_pct = None
    if sample_count < CPU_MIN_SAMPLES:
        mean_pct = None  # downgrade to no_data per gate order

    sample_hz = cpu_in.get("sample_hz_used")
    block["mean_percent"] = mean_pct
    block["sample_count"] = sample_count
    block["sample_window_ms"] = int(cpu_in.get("sample_window_ms") or 0)
    block["ncpu"] = cpu_in.get("ncpu")
    block["normalization"] = cpu_in.get("normalization") or "all_cores_total"
    block["thresholds_used"] = _thresholds_used(sample_hz)

    points, reason = score_cpu(mean_pct, measured_h265_fps)
    block["points"] = points
    block["gate_reason"] = reason
    block["gated"] = reason is not None
    return block
```

- [ ] **Step 4: Replace `build_score` body to include CPU and bump `max_score`**

Find the existing `build_score` (lines 106–140) and replace with:

```python
def build_score(
    h264_metrics: dict | None,
    h265_metrics: dict | None,
    chromium_version: str | None,
    failure_reason: str | None = None,
    per_round_reasons: dict | None = None,
    cpu_override_reason: str | None = None,
) -> dict:
    out: dict = {
        "max_score": 40,
        "objective_total": 0,
        "h264": None,
        "h265": None,
        "cpu": None,
        "chromium_version": chromium_version,
    }
    if failure_reason:
        out["reason"] = failure_reason

    if h264_metrics is not None:
        s = score_codec("h264", h264_metrics)
        out["h264"] = s.to_dict()
        out["objective_total"] += s.total
    if h265_metrics is not None:
        s = score_codec("h265", h265_metrics)
        out["h265"] = s.to_dict()
        out["objective_total"] += s.total

    if per_round_reasons:
        for codec, reason in per_round_reasons.items():
            if not reason:
                continue
            block = out.get(codec) or {}
            block["reason"] = reason
            out[codec] = block

    cpu_effective_override = cpu_override_reason
    if failure_reason and not cpu_effective_override:
        cpu_effective_override = "host_failure"

    cpu_block = _build_cpu_block(h265_metrics, cpu_effective_override)
    out["cpu"] = cpu_block
    out["objective_total"] += cpu_block["points"]
    return out
```

- [ ] **Step 5: Add `--cpu-override-reason` to `_cli` (so `scripts/evaluator-host.sh` can mark container mode)**

In `_cli` (around line 143), add an argparse entry **after** `--h265-reason`:

```python
    p.add_argument("--cpu-override-reason", type=str, default=None,
                   help="Force a gate_reason in the cpu block (used by the host "
                        "container wrapper to flag container_mode_unsupported).")
```

And in the `build_score(...)` call, pass through:

```python
    score = build_score(
        _load(args.h264),
        _load(args.h265),
        _load_chromium_version(args.install_prefix),
        failure_reason=args.failure_reason,
        per_round_reasons={"h264": args.h264_reason, "h265": args.h265_reason},
        cpu_override_reason=args.cpu_override_reason,
    )
```

- [ ] **Step 6: Update the module docstring**

Replace the first paragraph (lines 1–6) with:

```python
"""Score the analyzer's metrics into the final 40-point objective total.

10 correctness points + 5 FPS points per codec (two codecs = 30) plus a
0–10 CPU sub-score sampled during the H.265 round. Thresholds are
duplicated from openspec/specs/evaluator/spec.md and design.md; bumping one
without bumping the others is a regression.
"""
```

- [ ] **Step 7: Run scorer tests and confirm green**

Run:
```bash
.venv/bin/python -m pytest tests/test_score_cpu.py -x -q
```
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add scorer.py
git commit -m "feat(scorer): add CPU sub-score with 5 tunables and 40-point max_score"
```

---

## Task 5: Wire sampler into `runner.py`

**Files:**
- Modify: `runner.py:36-296`

- [ ] **Step 1: Extend `CaptureResult` dataclass to carry CPU output**

In `runner.py` after line 42 (inside `CaptureResult`), add:

```python
    cpu_sample_result: dict | None = None
    capture_started_at_epoch: float | None = None
    capture_ended_at_epoch: float | None = None
```

- [ ] **Step 2: Update `run_capture` signature and body to accept and use the sampler**

Change the `run_capture` signature (line 45) to:

```python
def run_capture(
    codec: str,
    output: Path,
    duration_s: float,
    fps: float,
    contestant_pgid: int | None = None,
    cpu_sample_hz: float | None = None,
) -> CaptureResult:
```

Inside `run_capture`, immediately **before** the steady-state capture loop (before line 222 `interval = 1.0 / fps`), insert:

```python
        import _cpu_sampler

        sampler: _cpu_sampler.Sampler | None = None
        if codec == "h265" and contestant_pgid is not None:
            sampler = _cpu_sampler.Sampler(
                pgid=contestant_pgid,
                hz=cpu_sample_hz if cpu_sample_hz else _cpu_sampler.DEFAULT_SAMPLE_HZ,
            )
            sampler.start()
        result.capture_started_at_epoch = time.time()
```

Wrap the existing capture loop (lines 226–247) in a `try` whose `finally` always stops the sampler and records the end timestamp. Replace the existing `try: for i in range(n_frames): ... except KeyboardInterrupt: ...` block with:

```python
        try:
            try:
                for i in range(n_frames):
                    now = time.monotonic()
                    if now < next_deadline:
                        time.sleep(next_deadline - now)
                    ts = time.time()
                    path = output / f"shot_{i:05d}.jpg"
                    try:
                        page.screenshot(path=str(path), clip=clip,
                                        type="jpeg", quality=90)
                    except PlaywrightError as exc:
                        result.browser_errors.append(f"screenshot {i}: {exc}")
                    result.timestamps.append(ts)
                    next_deadline += interval
            except KeyboardInterrupt:
                result.reason = "interrupted"
                browser.close()
                _write_timestamps(output, result)
                return result
        finally:
            result.capture_ended_at_epoch = time.time()
            if sampler is not None:
                sample_result = sampler.stop()
                result.cpu_sample_result = sample_result.to_dict()
            if codec == "h265" and (sampler is not None or contestant_pgid is not None):
                _write_capture_meta(output, codec, result)
```

- [ ] **Step 3: Add the `_write_capture_meta` helper**

After `_write_timestamps` (after line 268) add:

```python
def _write_capture_meta(output: Path, codec: str, result: CaptureResult) -> None:
    """Write capture_meta.json next to the screenshots.

    Only produced when sampling was requested. Absence is meaningful — the
    analyzer/scorer interprets it as 'sampler did not run'.
    """
    meta = {
        "codec": codec,
        "capture_started_at_epoch": result.capture_started_at_epoch,
        "capture_ended_at_epoch": result.capture_ended_at_epoch,
        "cpu": result.cpu_sample_result,  # None if sampler returned no usable data
    }
    (output / "capture_meta.json").write_text(json.dumps(meta, indent=2))
```

- [ ] **Step 4: Add the two new CLI flags to `_cli`**

In `_cli` (line 271+), after the `--fps` arg add:

```python
    p.add_argument("--contestant-pgid", type=int, default=None,
                   help="When set AND --codec is h265, sample the PGID's CPU.")
    p.add_argument("--cpu-sample-hz", type=float, default=None,
                   help="Sampler tick rate (debug-only, will be retired once calibrated).")
```

And pass them into `run_capture`:

```python
    result = run_capture(
        args.codec, args.output, args.duration, args.fps,
        contestant_pgid=args.contestant_pgid,
        cpu_sample_hz=args.cpu_sample_hz,
    )
```

- [ ] **Step 5: Smoke-import test**

Run:
```bash
.venv/bin/python -c "import runner; import _cpu_sampler; print('ok')"
```
Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add runner.py
git commit -m "feat(runner): embed cpu sampler in H.265 capture loop, write capture_meta.json"
```

---

## Task 6: Pass `cpu` block through `analyzer.py`

**Files:**
- Modify: `analyzer.py:198-285`

- [ ] **Step 1: Pass-through inside `_cli` after metrics are computed**

In `analyzer.py::_cli` (lines 287–306), **before** the `args.output.write_text(...)` line, insert:

```python
    metrics_dict = metrics_to_dict(metrics)
    meta_file = args.screenshots / "capture_meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text())
            cpu = meta.get("cpu")
            if cpu is not None:
                metrics_dict["cpu"] = cpu
        except (OSError, json.JSONDecodeError):
            pass
```

And change the existing write line to consume `metrics_dict` instead of re-calling `metrics_to_dict`:

```python
    args.output.write_text(json.dumps(metrics_dict, indent=2))
```

- [ ] **Step 2: Quick test — write a manual fixture and confirm pass-through**

Run:
```bash
mkdir -p /tmp/anaq/shots && \
echo '{"codec":"h265","cpu":{"mean_percent":7.5,"sample_count":9,"sample_window_ms":9000,"ncpu":8,"clk_tck":100,"normalization":"all_cores_total","pgid":111,"sample_hz_used":1.0}}' \
  > /tmp/anaq/shots/capture_meta.json && \
echo '{"success":true,"timestamps":[0.0,1.0]}' > /tmp/anaq/shots/timestamps.json && \
.venv/bin/python analyzer.py --codec h265 \
  --screenshots /tmp/anaq/shots \
  --reference reference/h265 \
  --output /tmp/anaq/h265_metrics.json && \
.venv/bin/python -c "import json; d=json.load(open('/tmp/anaq/h265_metrics.json')); assert d['cpu']['mean_percent']==7.5; print('passthrough ok')"
```
Expected: `passthrough ok`.

- [ ] **Step 3: Commit**

```bash
git add analyzer.py
git commit -m "feat(analyzer): pass capture_meta.json cpu block into h265_metrics.json"
```

---

## Task 7: Wire `--contestant-pgid` into `scripts/evaluator.sh`

**Files:**
- Modify: `scripts/evaluator.sh:132-148`

- [ ] **Step 1: Update `run_capture` shell function to optionally append `--contestant-pgid`**

Replace the existing `run_capture` shell function (lines 132–145) with:

```bash
run_capture() {
    local codec="$1" out="$2"
    log "running runner.py --codec ${codec}"

    local extra_args=()
    if [[ "${codec}" == "h265" && -f "${RUN_DIR}/contestant.pid" ]]; then
        local pgid
        pgid="$(cat "${RUN_DIR}/contestant.pid" 2>/dev/null || true)"
        if [[ -n "${pgid}" ]]; then
            extra_args+=(--contestant-pgid "${pgid}")
        fi
    fi

    if "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/runner.py" \
            --codec "${codec}" --output "${out}" --duration 30 --fps 30 \
            "${extra_args[@]}"; then
        return 0
    fi
    local reason
    reason="$(python3 -c "import json,sys;print(json.load(open('${out}/timestamps.json')).get('reason') or '')" 2>/dev/null || true)"
    log "  ${codec} runner failed: ${reason}"
    if [[ "${codec}" == "h264" ]]; then H264_REASON="${reason}"; else H265_REASON="${reason}"; fi
    return 1
}
```

- [ ] **Step 2: Manual smoke via `scripts/evaluator-local.sh`**

Run (against the bundled reference fixture; expect H.265 to fail playback on the canonical host, which exercises the `h265_fps_below_threshold` gate path):
```bash
./scripts/evaluator-local.sh test_eval test_submissions/reference.zip
```
Expected: exits 0; `results/test_eval_<ts>/score.json` contains `max_score:40`, `cpu.points:0`, `cpu.gate_reason:"h265_fps_below_threshold"`, `objective_total:15`.

If H.265 reference actually scores on this dev box (some hosts may have HEVC), CPU may report a real value — that's also acceptable for this smoke step.

- [ ] **Step 3: Commit**

```bash
git add scripts/evaluator.sh
git commit -m "feat(evaluator.sh): forward contestant PGID to H.265 runner for CPU sampling"
```

---

## Task 8: Render CPU block in `report.py`

**Files:**
- Modify: `report.py`

- [ ] **Step 1: Locate the `render_report` HTML body and add a CPU section**

Open `report.py` and find `render_report` (search for `def render_report`). Within the section that renders the summary header (after `objective_total` and per-codec subtotals), add a CPU block. The exact site depends on the template; insert markup like:

```python
    cpu = (score or {}).get("cpu") or {}
    cpu_block_html = (
        '<section class="cpu">'
        f'<h2>CPU sub-score: {html.escape(str(cpu.get("points", 0)))} / 10</h2>'
        '<dl>'
        f'<dt>mean_percent</dt><dd>{html.escape(str(cpu.get("mean_percent")))}</dd>'
        f'<dt>sample_count</dt><dd>{html.escape(str(cpu.get("sample_count")))}</dd>'
        f'<dt>sample_window_ms</dt><dd>{html.escape(str(cpu.get("sample_window_ms")))}</dd>'
        f'<dt>ncpu</dt><dd>{html.escape(str(cpu.get("ncpu")))}</dd>'
        f'<dt>normalization</dt><dd>{html.escape(str(cpu.get("normalization")))}</dd>'
        f'<dt>gated</dt><dd>{html.escape(str(cpu.get("gated")))}</dd>'
        f'<dt>gate_reason</dt><dd>{html.escape(str(cpu.get("gate_reason")))}</dd>'
        f'<dt>thresholds_used</dt><dd><pre>{html.escape(json.dumps(cpu.get("thresholds_used"), indent=2))}</pre></dd>'
        '</dl>'
        '</section>'
    )
```

Splice `cpu_block_html` into the assembled report body next to the other summary sections.

- [ ] **Step 2: Sanity check — render report from an existing run**

Run (replace `<run>` with the latest run dir from Task 7's smoke run):
```bash
.venv/bin/python -c "
import json
from pathlib import Path
import report
run = sorted(Path('results').iterdir())[-1]
score = json.loads((run / 'score.json').read_text())
h264 = json.loads((run / 'h264_metrics.json').read_text()) if (run / 'h264_metrics.json').exists() else None
h265 = json.loads((run / 'h265_metrics.json').read_text()) if (run / 'h265_metrics.json').exists() else None
report.render_report(score=score, h264_metrics=h264, h265_metrics=h265, output=run/'report.html', run_dir=run)
print('rendered', run / 'report.html')
"
```
Expected: the report opens locally and shows a "CPU sub-score: X / 10" section.

- [ ] **Step 3: Commit**

```bash
git add report.py
git commit -m "feat(report): render CPU sub-score block in evaluator HTML report"
```

---

## Task 9: Documentation sync

**Files:**
- Modify: `CLAUDE.md`
- (Already done) `scorer.py` docstring (Task 4 Step 6)

- [ ] **Step 1: Update `CLAUDE.md` Overview**

In the project `CLAUDE.md`, find the line containing "30-point objective portion" and change it to "40-point objective portion". Below the Overview paragraph, add:

```
A new 10-point CPU usage sub-score is sampled during the H.265 Playwright
capture window (contestant PGID, normalized to integer-machine all-cores
total). The sampler is host-native only — the container path (evaluator-host.sh)
emits cpu.gate_reason="container_mode_unsupported" until the packaging is
revisited.
```

- [ ] **Step 2: Confirm `openspec/specs/evaluator/spec.md::Purpose` is left for `/opsx:archive`**

Do NOT touch `openspec/specs/evaluator/spec.md` here. Project CLAUDE.md states this file's `Purpose` is rewritten at archive time. Verify the file still says "30-point" by `grep -n '30-point' openspec/specs/evaluator/spec.md`; expected: one match in the Purpose line.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE): bump objective total to 40 and note CPU sub-score scope"
```

---

## Task 10: End-to-end validation

**Files:** (no source changes — validation only)

- [ ] **Step 1: Run the full pytest suite**

Run:
```bash
.venv/bin/python -m pytest tests/ -q
```
Expected: all tests pass (Task 1 + 2 + 3 + 4 coverage).

- [ ] **Step 2: Run the regression suite via `scripts/test.sh`**

Run:
```bash
./scripts/test.sh
```
Expected: each fixture's pass/fail decision matches its expected outcome. For `reference.zip` the expected outcome is `total_ge:13`; with H.264 full (=15), H.265 zero (no HEVC on dev host), CPU zero (gated by fps), `objective_total = 15 ≥ 13` → PASS.

- [ ] **Step 3: Spot-check one `score.json` shape**

Run:
```bash
.venv/bin/python -c "
import json, sys
from pathlib import Path
score = json.loads(sorted(Path('results').glob('*/score.json'))[-1].read_text())
assert score['max_score'] == 40, score
assert 'cpu' in score, score
cpu = score['cpu']
for k in ('points', 'mean_percent', 'sample_count', 'sample_window_ms',
         'ncpu', 'normalization', 'measured_on_codec',
         'thresholds_used', 'gated', 'gate_reason'):
    assert k in cpu, f'missing {k}'
for k in ('gate_fps_ratio', 'full_percent', 'partial_start_percent',
         'zero_percent', 'min_samples', 'sample_hz'):
    assert k in cpu['thresholds_used'], f'thresholds missing {k}'
expected_total = (score['h264']['total'] if score['h264'] else 0) \
                 + (score['h265']['total'] if score['h265'] else 0) \
                 + cpu['points']
assert score['objective_total'] == expected_total, score
print('shape ok')
"
```
Expected: `shape ok`.

- [ ] **Step 4: Re-validate the OpenSpec change**

Run:
```bash
openspec validate add-cpu-usage-scoring
```
Expected: `Change 'add-cpu-usage-scoring' is valid`.

- [ ] **Step 5: Mark all tasks in `tasks.md` complete**

For each `- [ ]` checkbox in `openspec/changes/add-cpu-usage-scoring/tasks.md` whose work the steps above completed, change to `- [x]`. Verify no checkbox remains unchecked unless its task was intentionally deferred (e.g. a task is documented as out-of-scope in the change's design.md).

- [ ] **Step 6: Final commit**

```bash
git add openspec/changes/add-cpu-usage-scoring/tasks.md
git commit -m "chore(opsx): mark add-cpu-usage-scoring tasks complete"
```

---

## Self-Review Notes

- **Spec coverage map**:
  - Spec § "Contestant CPU Usage Measurement" → Tasks 2, 3, 5 (+ 7 wiring), 6 (analyzer pass-through)
  - Spec § "Scoring" MODIFIED → Tasks 1, 4
  - Spec § "Scoring" scenarios — full marks / partial / zero / gates — covered by `tests/test_score_cpu.py` (Task 1)
  - Spec § sampler enumerates subtree / vanishing PIDs — covered by `tests/test_cpu_sampler.py` (Task 2)
  - Spec § container path returns `container_mode_unsupported` — handled by `scorer._build_cpu_block` + the `--cpu-override-reason` CLI hook (Task 4 Step 5); the host-container wrapper passes it. The wrapper itself (`scripts/evaluator-host.sh`) is **out of scope** for this change because the user explicitly deferred container packaging — see brainstorm Open Question 1. The scorer code path is fully covered by `test_build_score_container_mode_unsupported`.
- **Skipped on purpose**:
  - CPU timeline in `report.html` — design.md Non-Goal.
  - End-to-end fixture exercising real CPU on H.265 — design.md Risk; relies on real submissions.
  - Touching `openspec/specs/evaluator/spec.md` directly — project CLAUDE.md rule defers `Purpose` to archive time.
- **Threshold names cross-check**: `CPU_GATE_H265_FPS_RATIO`, `CPU_FULL_THRESHOLD_PERCENT`, `CPU_PARTIAL_START_PERCENT`, `CPU_ZERO_THRESHOLD_PERCENT`, `CPU_MIN_SAMPLES` (scorer); `DEFAULT_SAMPLE_HZ`, `MIN_SAMPLES_FOR_MEAN` (sampler). The two min-samples constants are intentionally separate: the sampler refuses to compute a `mean_percent` below its own floor, the scorer additionally gates on `sample_count < CPU_MIN_SAMPLES` so a regression that lowers one without the other is caught by `test_build_score_sampler_no_data_gates_cpu`.
