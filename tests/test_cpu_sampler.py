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
    "import os, sys, time\n"
    "os.setsid()\n"
    "sys.stdout.write(str(os.getpid()))\n"
    "sys.stdout.write('\\n')\n"
    "sys.stdout.flush()\n"
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
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


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


def test_sampler_survives_vanishing_child():
    """Sampler must not crash when a PID disappears mid-sample."""
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
