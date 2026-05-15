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
    exclude_chrome_gpu: bool
    excluded_gpu_pids: list[int]
    per_process_top: list[dict]  # diagnostic: top jiffies-burners

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
            "exclude_chrome_gpu": self.exclude_chrome_gpu,
            "excluded_gpu_pids": self.excluded_gpu_pids,
            "per_process_top": self.per_process_top,
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


def _cmdline_is_chrome_gpu(cmdline: bytes) -> bool:
    """Detect a Chrome GPU process from its /proc/<pid>/cmdline bytes.

    Caveat: chrome subprocesses rewrite /proc/<pid>/cmdline into a SINGLE
    space-separated string (argv elements are no longer NUL-separated after
    prctl(PR_SET_MM_*) shenanigans), so a NUL-split-then-startswith approach
    silently fails. Substring containment is the only reliable marker.
    """
    if not cmdline:
        return False
    return b"--gpu-preferences=" in cmdline or b"--type=gpu-process" in cmdline


def _read_proc_diagnostic(pid: int) -> tuple[str, int, int, bool]:
    """Return (label, ppid, threads, is_chrome_gpu) for a PID. Best-effort.

    Chrome zeroes its argv hash early so --type=renderer / utility is mostly
    NOT recoverable from /proc/<pid>/cmdline once the process settles. The
    GPU process however carries --gpu-preferences=... long enough to be
    detected on a fresh observation — that's enough to mark it and exclude
    it from sampling (see Sampler `exclude_chrome_gpu`). On Linux headless
    Chrome without a real GPU acceleration path, the GPU process runs
    SwiftShader on the CPU and would otherwise dominate the union total,
    not because contestant code is heavy but because software rasterisation
    is.

    label is `argv[0]` basename when cmdline is readable, otherwise `comm`.
    ppid and threads come from /proc/<pid>/status.
    """
    label = "?"
    ppid = 0
    threads = 0
    is_chrome_gpu = False
    cmdline = b""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        if cmdline.strip(b"\x00"):
            argv0 = cmdline.split(b"\x00", 1)[0]
            label = argv0.decode("utf-8", "replace").rsplit("/", 1)[-1]
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        pass
    is_chrome_gpu = _cmdline_is_chrome_gpu(cmdline)
    if label in ("?", ""):
        try:
            label = Path(f"/proc/{pid}/comm").read_text().strip() or "?"
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            pass
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        for line in status.splitlines():
            if line.startswith("PPid:"):
                ppid = int(line.split()[1])
            elif line.startswith("Threads:"):
                threads = int(line.split()[1])
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError, ValueError):
        pass
    return label, ppid, threads, is_chrome_gpu


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
        exclude_chrome_gpu: bool = True,
    ) -> None:
        if hz <= 0:
            raise ValueError(f"hz must be positive, got {hz}")
        self.pgid = pgid
        self.extra_root_pid = extra_root_pid
        self.hz = hz
        # Headless Chrome's GPU process runs SwiftShader software rasterisation
        # when no real GPU pipeline is wired; on this evaluator's --ozone-platform=
        # headless config it can dominate the union (84% in observed runs) without
        # representing any contestant work. Filter it out by default; flip to False
        # if you ever switch the runner to a real-GPU Chrome config.
        self.exclude_chrome_gpu = exclude_chrome_gpu
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ncpu = os.cpu_count() or 1
        self._clk_tck = os.sysconf("SC_CLK_TCK")
        self._t_start: float | None = None
        self._t_last: float | None = None
        self._sample_count = 0
        self._baseline: dict[int, int] = {}
        self._total_delta_jiffies = 0
        # Per-PID attribution for diagnostic dump. PID → cumulative δjiffies.
        self._per_pid_delta: dict[int, int] = {}
        # PID → (label, ppid, threads, is_chrome_gpu) at first observation.
        self._pid_diag: dict[int, tuple[str, int, int, bool]] = {}
        # PIDs identified as chrome GPU process; permanently excluded from
        # baseline / deltas / per_pid_delta when exclude_chrome_gpu is True.
        self._excluded_gpu_pids: set[int] = set()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Sampler.start called twice")
        self._t_start = time.monotonic()
        raw_baseline = _enumerate_targets(self.pgid, self.extra_root_pid)
        # Diagnose every baseline PID while it is alive (cmdline is most
        # readable here; chrome subprocesses zero their argv soon after).
        # Mid-run new PIDs get diagnosed on first observation in _tick. Re-
        # read threads at stop time for top consumers since renderer thread
        # counts can change as wasm workers spin up.
        for pid in raw_baseline:
            diag = _read_proc_diagnostic(pid)
            self._pid_diag[pid] = diag
            if self.exclude_chrome_gpu and diag[3]:  # is_chrome_gpu
                self._excluded_gpu_pids.add(pid)
        # Filtered baseline drops any chrome GPU process so deltas, total
        # jiffies, and per_pid_delta never count its SwiftShader work.
        self._baseline = {
            pid: total for pid, total in raw_baseline.items()
            if pid not in self._excluded_gpu_pids
        }
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        interval = 1.0 / self.hz
        while not self._stop.is_set():
            if self._stop.wait(timeout=interval):
                break
            self._tick()

    def _tick(self) -> None:
        raw_pids = _enumerate_targets(self.pgid, self.extra_root_pid)
        # Diagnose new PIDs first so we can decide on exclusion before they
        # contribute any delta. New chrome GPU processes (rare mid-run, but
        # possible on a process crash + respawn) get filtered out from this
        # tick on.
        for pid in raw_pids:
            if pid not in self._pid_diag:
                diag = _read_proc_diagnostic(pid)
                self._pid_diag[pid] = diag
                if self.exclude_chrome_gpu and diag[3]:
                    self._excluded_gpu_pids.add(pid)
        now_pids = {
            pid: total for pid, total in raw_pids.items()
            if pid not in self._excluded_gpu_pids
        }
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
                self._per_pid_delta[pid] = self._per_pid_delta.get(pid, 0) + delta
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
        # Diagnostic: top jiffies-burners. Sorted desc, capped at 10. We
        # re-read threads at stop time (vs first-observation time) for the
        # top consumers — Chrome renderers spin up wasm workers mid-capture,
        # and the late thread count is more informative.
        top_pairs = sorted(
            self._per_pid_delta.items(), key=lambda kv: kv[1], reverse=True
        )[:10]
        total = self._total_delta_jiffies or 1
        per_process_top = []
        for pid, jiffies in top_pairs:
            label, ppid, threads_init, _ = self._pid_diag.get(pid, ("?", 0, 0, False))
            _, _, threads_now, _ = _read_proc_diagnostic(pid)
            per_process_top.append({
                "pid": pid,
                "label": label,
                "ppid": ppid,
                "threads": threads_now or threads_init,
                "cpu_jiffies": jiffies,
                "percent_of_union": round(jiffies / total * 100.0, 2),
            })
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
            exclude_chrome_gpu=self.exclude_chrome_gpu,
            excluded_gpu_pids=sorted(self._excluded_gpu_pids),
            per_process_top=per_process_top,
        )
