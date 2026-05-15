"""Contestant CPU usage sampler.

Background thread that periodically enumerates /proc/[0-9]*/stat for processes
in TWO trees and sums their (utime + stime) jiffies:

  1. Contestant session tree — every PID whose stat field-6 (session) matches
     the contestant's PGID. Catches server-side workers the contestant forks
     under its own setsid'ed session (relay backends, decode workers, etc.).

  2. Browser process tree (optional `extra_root_pid`) — every PID reachable
     from `extra_root_pid` via ppid descent. Catches the Playwright driver
     + Chrome main + Chrome renderer/GPU/utility workers, where wasm /
     WebCodecs decoding actually runs for client-side-decode contestant
     designs (the dominant pattern: server fanout, browser decodes).

The union is normalized to the total of all cores — one saturated core
reads as 100/ncpu %.

Precondition for tree 1: contestant is launched via setsid (see
scripts/_contestant_lifecycle.sh::clx_start_contestant), so
SID == PGID == the recorded CONTESTANT_PID.

Precondition for tree 2: caller passes a PID whose subtree contains the
work to attribute. For Playwright runs, the natural choice is
`browser._impl_obj._connection._transport._proc.pid` (the driver process —
parent of Chrome). The driver/Chrome subtree counts against the contestant
as their effective rendering cost; this includes a small fixed overhead
from Playwright's control-channel work that we accept as evaluator noise.

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
    extra_root_pid: int | None
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
            "extra_root_pid": self.extra_root_pid,
            "sample_hz_used": self.sample_hz_used,
        }


def _read_stat(pid: int) -> tuple[int, int, int, int] | None:
    """Return (session, ppid, utime, stime) jiffies for `pid`, or None on any
    read error. We tolerate vanishing PIDs silently — they are expected
    mid-capture.

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
    # tail[0] = field 3 (state). Indices after that:
    #   field  4 (ppid)    = tail[1]
    #   field  6 (session) = tail[3]
    #   field 14 (utime)   = tail[11]
    #   field 15 (stime)   = tail[12]
    tail = text[rparen + 2:].split()
    if len(tail) < 13:
        return None
    try:
        ppid = int(tail[1])
        session = int(tail[3])
        utime = int(tail[11])
        stime = int(tail[12])
    except (IndexError, ValueError):
        return None
    return session, ppid, utime, stime


def _enumerate_targets(pgid: int, extra_root_pid: int | None) -> dict[int, int]:
    """Return {pid: utime+stime jiffies} for the union of two trees:

      Tree 1 — Contestant session: every PID whose stat field-6 (session)
               equals `pgid`. Catches anything the contestant fork'ed under
               its own setsid'ed session.

      Tree 2 — Browser process tree (optional): every PID reachable from
               `extra_root_pid` via ppid descent. Catches the Playwright
               driver + Chrome main + Chrome renderer/GPU/utility workers,
               where wasm / WebCodecs decoding actually runs for
               client-side-decode contestant designs.

    PIDs in both trees are counted once. Vanished PIDs are silently dropped.
    """
    info: dict[int, tuple[int, int, int]] = {}  # pid -> (ppid, session, jiffies)
    try:
        entries = os.listdir("/proc")
    except FileNotFoundError:
        return {}
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        stat = _read_stat(pid)
        if stat is None:
            continue
        session, ppid, utime, stime = stat
        info[pid] = (ppid, session, utime + stime)

    selected: dict[int, int] = {}

    # Tree 1: session == pgid
    for pid, (_, session, total) in info.items():
        if session == pgid:
            selected[pid] = total

    # Tree 2: ppid descendants of extra_root_pid (inclusive)
    if extra_root_pid is not None:
        children: dict[int, list[int]] = {}
        for pid, (ppid, _, _) in info.items():
            children.setdefault(ppid, []).append(pid)
        stack = [extra_root_pid]
        while stack:
            cur = stack.pop()
            if cur in selected:
                continue
            cur_info = info.get(cur)
            if cur_info is not None:
                selected[cur] = cur_info[2]
            stack.extend(children.get(cur, []))

    return selected


class Sampler:
    """Daemon-thread CPU sampler bound to a single PGID.

    Lifecycle: ``s = Sampler(pgid, hz); s.start(); ...; result = s.stop()``.
    ``start`` is non-blocking. ``stop`` joins and returns a ``SampleResult``.

    Thread-safety: only ``start`` and ``stop`` are public; both are intended
    to be called from a single owning thread (runner.py's capture body).
    """

    def __init__(
        self,
        pgid: int,
        hz: float = DEFAULT_SAMPLE_HZ,
        extra_root_pid: int | None = None,
    ) -> None:
        if hz <= 0:
            raise ValueError(f"hz must be positive, got {hz}")
        self.pgid = pgid
        self.extra_root_pid = extra_root_pid
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
        self._baseline = _enumerate_targets(self.pgid, self.extra_root_pid)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        interval = 1.0 / self.hz
        while not self._stop.is_set():
            if self._stop.wait(timeout=interval):
                break
            self._tick()

    def _tick(self) -> None:
        now_pids = _enumerate_targets(self.pgid, self.extra_root_pid)
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
            extra_root_pid=self.extra_root_pid,
            sample_hz_used=self.hz,
        )
