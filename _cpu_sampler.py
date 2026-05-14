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
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    lparen = text.find("(")
    rparen = text.rfind(")")
    if lparen < 0 or rparen < 0:
        return None
    # tail[0] is field 3 (state). Field 6 is tail[3]. utime is field 14 = tail[11],
    # stime is field 15 = tail[12].
    tail = text[rparen + 2:].split()
    if len(tail) < 13:
        return None
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
        entries = os.listdir("/proc")
    except FileNotFoundError:
        return out
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        stat = _read_stat(pid)
        if stat is None:
            continue
        session, utime, stime = stat
        if session == pgid:
            out[pid] = utime + stime
    return out


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
        # Vanished PIDs are simply dropped — their last contribution remains
        # in the running total because we already credited it on the tick
        # they were last seen.
        for pid, total in now_pids.items():
            prev = self._baseline.get(pid)
            if prev is None:
                continue
            delta = total - prev
            if delta > 0:
                self._total_delta_jiffies += delta
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
