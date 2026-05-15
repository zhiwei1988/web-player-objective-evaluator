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


def test_sampler_counts_extra_root_pid_subtree():
    """A spinner reached only via ppid descent (extra_root_pid) is counted.

    Set pgid to a bogus value so the session-match path can't hit; the only
    way the spinner gets counted is through extra_root_pid → ppid descent.
    """
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c",
         "x=0\nwhile True:\n    x=(x+1)%1000000\n"]
    )
    try:
        sampler = _cpu_sampler.Sampler(
            pgid=2**30,                 # no session match
            extra_root_pid=proc.pid,    # the spinner itself
            hz=4.0,
        )
        sampler.start()
        time.sleep(2.0)
        result = sampler.stop()
        ncpu = os.cpu_count() or 1
        expected_pct = 100.0 / ncpu
        assert result.extra_root_pid == proc.pid
        assert result.mean_percent is not None
        # Spinner pinned to one core; allow generous low-side slack for warmup.
        assert result.mean_percent > expected_pct - 3.0, (
            f"mean={result.mean_percent} expected≈{expected_pct}"
        )
    finally:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def test_sampler_dedupes_pids_in_both_trees():
    """A PID that satisfies BOTH the session match AND the ppid descent path
    must be counted once (not double-counted)."""
    # Our own PID. It matches our session AND it is itself the root of its
    # own ppid subtree.
    sampler = _cpu_sampler.Sampler(
        pgid=os.getpgid(0),
        extra_root_pid=os.getpid(),
        hz=10.0,
    )
    sampler.start()
    time.sleep(0.5)
    result = sampler.stop()
    # We can't assert the exact value (depends on host load), but it must be
    # bounded — double counting could push 1-core spin to 2-core readings.
    if result.mean_percent is not None:
        ncpu = os.cpu_count() or 1
        # Sanity ceiling: even a heavily loaded pytest worker shouldn't
        # exceed 1/ncpu of all-cores-total during a 0.5s window.
        assert result.mean_percent < 100.0 / ncpu * 1.5
