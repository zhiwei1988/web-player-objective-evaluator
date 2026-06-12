#!/usr/bin/env python3
"""Print hardware and evaluator-host facts for cross-machine comparison.

Run on the canonical competition machine before calibrating CPU thresholds or
comparing score.json.cpu.mean_percent across hosts.

    .venv/bin/python scripts/print_host_specs.py
    .venv/bin/python scripts/print_host_specs.py --json > host_specs.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _read_kv_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            if "=" not in line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"')
    except OSError:
        pass
    return out


def _proc_meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                out[parts[0].rstrip(":")] = int(parts[1])
    except OSError:
        pass
    return out


def _cpu_model() -> str | None:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def _cpu_governors() -> list[str]:
    governors: set[str] = set()
    for path in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor"):
        try:
            governors.add(path.read_text().strip())
        except OSError:
            pass
    return sorted(governors)


def _lscpu_fields() -> dict[str, str]:
    if shutil.which("lscpu") is None:
        return {}
    try:
        completed = subprocess.run(
            ["lscpu"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    out: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def _file_cap(path: str) -> str | None:
    if shutil.which("getcap") is None or not Path(path).is_file():
        return None
    try:
        completed = subprocess.run(
            ["getcap", path],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = completed.stdout.strip()
    return text or None


def _cgroup_v2_available() -> bool:
    try:
        mounts = Path("/proc/self/mountinfo").read_text()
    except OSError:
        return False
    return "cgroup2" in mounts


def _playwright_chromium_version(root: Path) -> str | None:
    version_file = root / "third_party/install/playwright_chromium.version"
    try:
        text = version_file.read_text().strip()
    except OSError:
        return None
    return text or None


def _disk_usage(path: Path) -> dict[str, str] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    gib = 1024**3
    return {
        "path": str(path),
        "total_gib": f"{usage.total / gib:.1f}",
        "used_gib": f"{usage.used / gib:.1f}",
        "free_gib": f"{usage.free / gib:.1f}",
    }


def collect_specs(root: Path = ROOT) -> dict[str, Any]:
    os_release = _read_kv_file(Path("/etc/os-release"))
    meminfo = _proc_meminfo()
    lscpu = _lscpu_fields()
    clk_tck = os.sysconf("SC_CLK_TCK")
    ncpu = os.cpu_count()

    mem_total_kib = meminfo.get("MemTotal")
    swap_total_kib = meminfo.get("SwapTotal")

    specs: dict[str, Any] = {
        "hostname": platform.node(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "kernel": platform.release(),
        "os": {
            "name": os_release.get("NAME"),
            "version": os_release.get("VERSION"),
            "id": os_release.get("ID"),
            "version_id": os_release.get("VERSION_ID"),
        },
        "cpu": {
            "model_name": _cpu_model(),
            "ncpu_os_cpu_count": ncpu,
            "cpu_online": lscpu.get("On-line CPU(s) list"),
            "socket_count": lscpu.get("Socket(s)"),
            "core_per_socket": lscpu.get("Core(s) per socket"),
            "thread_per_core": lscpu.get("Thread(s) per core"),
            "architecture": lscpu.get("Architecture") or platform.machine(),
            "cpu_op_modes": lscpu.get("CPU op-mode(s)"),
            "governors": _cpu_governors(),
        },
        "cpu_sampler": {
            "ncpu": ncpu,
            "clk_tck": clk_tck,
            "normalization": "all_cores_total",
            "one_core_saturated_percent": round(100.0 / ncpu, 4) if ncpu else None,
        },
        "memory": {
            "mem_total_kib": mem_total_kib,
            "mem_total_gib": round(mem_total_kib / (1024**2), 2) if mem_total_kib else None,
            "swap_total_kib": swap_total_kib,
            "swap_total_gib": round(swap_total_kib / (1024**2), 2) if swap_total_kib else None,
        },
        "disk": _disk_usage(root),
        "python": {
            "version": sys.version.split()[0],
            "executable": sys.executable,
        },
        "evaluator": {
            "repo_root": str(root),
            "contestant_memory_max": os.environ.get(
                "EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE"
            )
            or os.environ.get("EVALUATOR_CONTESTANT_MEMORY_MAX")
            or "10G",
            "contestant_bandwidth_max": os.environ.get(
                "EVALUATOR_CONTESTANT_BANDWIDTH_MAX_EFFECTIVE"
            )
            or os.environ.get("EVALUATOR_CONTESTANT_BANDWIDTH_MAX")
            or "100mbit",
            "cgroup_v2_available": _cgroup_v2_available(),
            "tc_getcap": _file_cap(shutil.which("tc") or "/usr/sbin/tc"),
            "nft_getcap": _file_cap(shutil.which("nft") or "/usr/sbin/nft"),
            "playwright_chromium_version": _playwright_chromium_version(root),
            "venv_present": (root / ".venv/bin/python").is_file(),
            "third_party_install_present": (root / "third_party/install/bin/ffmpeg").is_file(),
        },
    }
    return specs


def _format_human(specs: dict[str, Any]) -> str:
    cpu = specs["cpu"]
    sampler = specs["cpu_sampler"]
    mem = specs["memory"]
    ev = specs["evaluator"]
    os_info = specs["os"]
    lines = [
        "Evaluator host specs",
        "====================",
        f"Hostname:     {specs['hostname']}",
        f"OS:           {os_info.get('name')} {os_info.get('version') or os_info.get('version_id')}",
        f"Kernel:       {specs['kernel']}",
        f"Machine:      {specs['machine']}",
        "",
        "CPU",
        "----",
        f"Model:        {cpu.get('model_name') or 'unknown'}",
        f"ncpu:         {sampler.get('ncpu')}  (os.cpu_count — used by CPU sampler)",
        f"Online CPUs:  {cpu.get('cpu_online') or 'unknown'}",
        f"Sockets:      {cpu.get('socket_count') or 'unknown'}",
        f"Cores/socket: {cpu.get('core_per_socket') or 'unknown'}",
        f"Threads/core: {cpu.get('thread_per_core') or 'unknown'}",
        f"CLK_TCK:      {sampler.get('clk_tck')}  (SC_CLK_TCK — CPU sampler jiffies)",
        f"Governor(s):  {', '.join(cpu.get('governors') or []) or 'unavailable'}",
        f"1-core load:  {sampler.get('one_core_saturated_percent')}% of all-cores-total",
        "",
        "Memory",
        "------",
        f"RAM total:    {mem.get('mem_total_gib')} GiB",
        f"Swap total:   {mem.get('swap_total_gib')} GiB",
    ]
    disk = specs.get("disk")
    if disk:
        lines.extend(
            [
                "",
                "Disk (repo root)",
                "----------------",
                f"Path:         {disk['path']}",
                f"Free:         {disk['free_gib']} GiB / {disk['total_gib']} GiB",
            ]
        )
    lines.extend(
        [
            "",
            "Evaluator runtime",
            "-----------------",
            f"Repo root:    {ev['repo_root']}",
            f"Python:       {specs['python']['version']}",
            f"Memory limit: {ev['contestant_memory_max']}",
            f"Bandwidth:    {ev['contestant_bandwidth_max']}",
            f"cgroup v2:    {ev['cgroup_v2_available']}",
            f"tc getcap:    {ev['tc_getcap'] or 'not set / unavailable'}",
            f"nft getcap:   {ev['nft_getcap'] or 'not set / unavailable'}",
            f"venv:         {ev['venv_present']}",
            f"third_party:  {ev['third_party_install_present']}",
        ]
    )
    chromium = ev.get("playwright_chromium_version")
    if chromium:
        chromium_one_line = re.sub(r"\s+", " ", chromium)
        lines.append(f"Chromium:     {chromium_one_line}")
    return "\n".join(lines)


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Print hardware and evaluator-host facts for the competition machine."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a human report.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Evaluator repository root (default: parent of scripts/).",
    )
    args = parser.parse_args()

    specs = collect_specs(args.root.resolve())
    if args.json:
        print(json.dumps(specs, indent=2, sort_keys=True))
    else:
        print(_format_human(specs))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
