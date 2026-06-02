#!/usr/bin/env python3
"""Print a focused diagnostic report for an evaluator result directory.

The report is meant to compare the same submission across hosts. It separates
evaluator capture throughput from contestant playback throughput, then appends
host facts that often explain FPS differences.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
METRIC_KEYS = [
    "measured_fps",
    "capture_sampling_fps",
    "target_capture_fps",
    "duration",
    "target_capture_duration",
    "capture_span_overrun_ratio",
    "unique_frame_count",
    "total_shots",
    "repeat_frame_rate",
    "dropped_or_skipped_frame_rate",
    "frame_delta_histogram",
]


def find_latest_run(root: Path = ROOT, team_id: str | None = None) -> Path:
    results = root / "results"
    pattern = f"{team_id}_*" if team_id else "*"
    candidates = [p for p in results.glob(pattern) if p.is_dir()]
    if not candidates:
        hint = f" for team_id={team_id}" if team_id else ""
        raise FileNotFoundError(f"no result directories found under {results}{hint}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def fmt_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def diagnose_profile(metrics: dict[str, Any]) -> str:
    target = float(metrics.get("target_capture_fps") or 25.0)
    measured = float(metrics.get("measured_fps") or 0.0)
    capture = float(metrics.get("capture_sampling_fps") or 0.0)
    repeat = float(metrics.get("repeat_frame_rate") or 0.0)
    skipped = float(metrics.get("dropped_or_skipped_frame_rate") or 0.0)

    if target <= 0:
        return "Likely bottleneck: unknown (missing target_capture_fps)"

    capture_ratio = capture / target
    measured_ratio = measured / target

    if capture_ratio < 0.85 and capture <= max(measured * 1.25, 1.0):
        return "Likely bottleneck: evaluator capture / host throughput"
    if capture_ratio >= 0.90 and measured_ratio < 0.80:
        if repeat >= 0.20:
            return "Likely bottleneck: contestant playback / decode / rendering (many repeated frames)"
        if skipped >= 0.20:
            return "Likely bottleneck: contestant playback / decode / rendering (many skipped frames)"
        return "Likely bottleneck: contestant playback / decode / rendering"
    if capture_ratio < 0.90 and measured_ratio < 0.80:
        return "Likely bottleneck: mixed or inconclusive (capture and playback are both below target)"
    return "Likely bottleneck: none obvious from FPS metrics"


def profile_sections(run_dir: Path) -> list[str]:
    sections: list[str] = []
    for metrics_path in sorted(run_dir.glob("*_metrics.json")):
        profile = metrics_path.name.removesuffix("_metrics.json")
        metrics = load_json(metrics_path)
        lines = [f"Profile: {profile}"]
        for key in METRIC_KEYS:
            if key in metrics:
                lines.append(f"  {key}: {fmt_value(metrics[key])}")
        lines.append(f"  {diagnose_profile(metrics)}")
        sections.append("\n".join(lines))
    return sections


def stage_timing_section(run_dir: Path) -> str | None:
    path = run_dir / "stage_timings.json"
    if not path.exists():
        return None
    try:
        data = load_json(path)
    except Exception:
        return None
    lines = ["Stage timings:"]
    found = False
    budgets = data.get("budgets") if isinstance(data.get("budgets"), dict) else {}
    if isinstance(budgets, dict) and budgets.get("contestant_memory_max"):
        lines.append(f"  Contestant memory limit: {budgets['contestant_memory_max']}")
        found = True
    for record in data.get("stages") or []:
        if not isinstance(record, dict):
            continue
        stage = record.get("stage") or "?"
        profile = record.get("profile")
        label = f"{stage}[{profile}]" if profile else str(stage)
        status = record.get("status") or "?"
        duration = record.get("duration_s")
        timeout = record.get("timeout_seconds")
        reason = record.get("reason")
        parts = [f"  {label}: {status}"]
        if isinstance(duration, (int, float)):
            parts.append(f"duration={duration:.2f}s")
        if timeout not in (None, ""):
            parts.append(f"timeout={timeout}s")
        if reason:
            parts.append(str(reason))
        lines.append(" ".join(parts))
        found = True
    return "\n".join(lines) if found else None


def layout_warning_section(run_dir: Path) -> str | None:
    lines = ["Layout warnings:"]
    found = False
    for ts_path in sorted(run_dir.glob("*_screenshots/timestamps.json")):
        profile = ts_path.parent.name.removesuffix("_screenshots")
        try:
            data = load_json(ts_path)
        except Exception:
            continue
        layout = data.get("layout_diagnostics") or {}
        warnings = layout.get("warnings") or []
        for warning in warnings:
            lines.append(f"  {profile}: {warning}")
            found = True
    return "\n".join(lines) if found else None


def score_section(run_dir: Path) -> str | None:
    score_path = run_dir / "score.json"
    if not score_path.exists():
        return None
    score = load_json(score_path)
    lines = ["Score:"]
    if "objective_total" in score:
        lines.append(f"  objective_total: {fmt_value(score['objective_total'])}")
    if score.get("reason"):
        lines.append(f"  reason: {score['reason']}")
    if score.get("contestant_memory_limit"):
        lines.append(f"  contestant_memory_limit: {score['contestant_memory_limit']}")
    for profile in sorted(k for k, v in score.items() if isinstance(v, dict)):
        block = score[profile]
        if "measured_fps" in block or "fps_points" in block:
            lines.append(
                f"  {profile}: measured_fps={fmt_value(block.get('measured_fps'))} "
                f"fps_points={fmt_value(block.get('fps_points'))}"
            )
    return "\n".join(lines)


def run_command(args: list[str], cwd: Path = ROOT) -> str:
    if shutil.which(args[0]) is None:
        return f"$ {' '.join(args)}\n  skipped: command not found"
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"$ {' '.join(args)}\n  timed out"
    out = completed.stdout.strip() or "(no output)"
    return f"$ {' '.join(args)}\n{out}"


def governor_summary() -> str:
    cpufreq = Path("/sys/devices/system/cpu")
    governors = set()
    for path in cpufreq.glob("cpu[0-9]*/cpufreq/scaling_governor"):
        try:
            governors.add(path.read_text().strip())
        except OSError:
            pass
    if not governors:
        return "CPU governor: unavailable"
    return f"CPU governor: {', '.join(sorted(governors))}"


def host_section(root: Path = ROOT) -> str:
    commands = [
        ["lscpu"],
        ["free", "-h"],
        ["df", "-h", str(root)],
        ["uptime"],
        ["google-chrome", "--version"],
        ["chromium", "--version"],
        ["ss", "-lntp", "sport = :8080"],
        ["ss", "-lntp", "sport = :554"],
    ]
    lines = [
        "Host:",
        f"Python: {sys.version.split()[0]} ({platform.platform()})",
        f"Machine: {platform.machine()}",
        governor_summary(),
    ]
    lines.extend(run_command(cmd, cwd=root) for cmd in commands)
    return "\n\n".join(lines)


def build_report(run_dir: Path, include_host: bool = True, root: Path = ROOT) -> str:
    run_dir = run_dir.resolve()
    sections = [f"Run: {run_dir}"]
    score = score_section(run_dir)
    if score:
        sections.append(score)
    profiles = profile_sections(run_dir)
    if profiles:
        sections.extend(profiles)
    else:
        sections.append("No *_metrics.json files found.")
    stage_timings = stage_timing_section(run_dir)
    if stage_timings:
        sections.append(stage_timings)
    layout_warnings = layout_warning_section(run_dir)
    if layout_warnings:
        sections.append(layout_warnings)
    if include_host:
        sections.append(host_section(root))
    return "\n\n".join(sections)


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose evaluator FPS differences from a result directory."
    )
    parser.add_argument(
        "run_dir",
        nargs="?",
        type=Path,
        help="Result directory to inspect. Defaults to the latest results/* directory.",
    )
    parser.add_argument(
        "--team-id",
        help="When run_dir is omitted, choose the latest results/<team_id>_* directory.",
    )
    parser.add_argument(
        "--no-host",
        action="store_true",
        help="Only print score and metrics; skip host/environment commands.",
    )
    args = parser.parse_args()

    try:
        run_dir = args.run_dir or find_latest_run(ROOT, args.team_id)
        print(build_report(run_dir, include_host=not args.no_host, root=ROOT))
    except Exception as exc:
        print(f"diagnose_run.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
