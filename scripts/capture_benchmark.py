#!/usr/bin/env python3
"""Summarize evaluator capture throughput from an existing fixture run.

This command intentionally does not score contestants. It reads artifacts from
an already-captured deterministic fixture and reports the evaluator's own
sampling throughput so capture strategies can be compared.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean, median


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(round((pct / 100.0) * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[idx]


def summarize_capture(screenshots_dir: Path, metrics_path: Path | None = None) -> dict:
    ts_file = screenshots_dir / "timestamps.json"
    ts_data = json.loads(ts_file.read_text())
    timestamps = [float(v) for v in ts_data.get("timestamps") or []]
    intervals_ms = [
        (b - a) * 1000.0
        for a, b in zip(timestamps, timestamps[1:])
    ]
    duration = (timestamps[-1] - timestamps[0]) if len(timestamps) >= 2 else 0.0
    shot_count = len(timestamps)
    out = {
        "screenshots_dir": str(screenshots_dir),
        "profile": ts_data.get("profile"),
        "capture_strategy": ts_data.get("capture_strategy"),
        "target_fps": ts_data.get("target_fps"),
        "target_duration_s": ts_data.get("target_duration_s"),
        "shot_count": shot_count,
        "actual_capture_duration_s": duration,
        "capture_sampling_fps": shot_count / duration if duration > 0 else 0.0,
        "avg_interval_ms": mean(intervals_ms) if intervals_ms else 0.0,
        "p50_interval_ms": median(intervals_ms) if intervals_ms else 0.0,
        "p90_interval_ms": _percentile(intervals_ms, 90),
        "p99_interval_ms": _percentile(intervals_ms, 99),
        "recognition_stability": None,
    }

    if metrics_path is not None:
        metrics = json.loads(metrics_path.read_text())
        wm = float(metrics.get("watermark_recognition_rate") or 0.0)
        color = float(metrics.get("color_check_rate") or 0.0)
        ssim = float(metrics.get("mean_ssim") or 0.0)
        out["recognition_stability"] = {
            "watermark_recognition_rate": wm,
            "color_check_rate": color,
            "mean_ssim": ssim,
            "preserves_full_correctness_thresholds": (
                wm >= 0.95 and color >= 0.95 and ssim >= 0.90
            ),
        }

    return out


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Summarize capture throughput artifacts.")
    parser.add_argument("--screenshots", required=True, type=Path,
                        help="Directory containing timestamps.json from runner.py.")
    parser.add_argument("--metrics", type=Path, default=None,
                        help="Optional profile metrics JSON for recognition stability checks.")
    parser.add_argument("--output", type=Path, default=None,
                        help="Optional path to write the benchmark JSON.")
    args = parser.parse_args()

    summary = summarize_capture(args.screenshots, args.metrics)
    text = json.dumps(summary, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
