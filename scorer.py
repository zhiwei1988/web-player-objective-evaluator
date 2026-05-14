"""Score the analyzer's metrics into the final 40-point objective total.

10 correctness points + 5 FPS points per codec (two codecs = 30) plus a
0–10 CPU sub-score sampled during the H.265 round. Thresholds are
duplicated from openspec/specs/evaluator/spec.md and design.md; bumping one
without bumping the others is a regression.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


EXPECTED_FPS = {"h264": 30.0, "h265": 25.0}


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


@dataclass
class CodecScore:
    codec: str
    correctness_points: int
    fps_points: int
    measured_fps: float
    expected_fps: float
    watermark_recognition_rate: float
    color_check_rate: float
    mean_ssim: float

    @property
    def total(self) -> int:
        return self.correctness_points + self.fps_points

    def to_dict(self) -> dict:
        return {
            "correctness_points": self.correctness_points,
            "fps_points": self.fps_points,
            "total": self.total,
            "measured_fps": self.measured_fps,
            "expected_fps": self.expected_fps,
            "watermark_recognition_rate": self.watermark_recognition_rate,
            "color_check_rate": self.color_check_rate,
            "mean_ssim": self.mean_ssim,
        }


def score_correctness(rate_wm: float, rate_color: float, mean_ssim: float) -> int:
    if rate_wm >= 0.95 and rate_color >= 0.95 and mean_ssim >= 0.90:
        return 10
    if rate_wm >= 0.80 and mean_ssim >= 0.75:
        return 5
    return 0


def score_fps(measured: float, expected: float) -> int:
    """Score the unique-frame FPS metric.

    Original spec was `|measured - expected| <= 1.0` for full marks, but in
    practice playwright's screenshot loop on commodity hardware tops out
    somewhere around 20-25 Hz; a contestant playing perfectly at 30 fps
    therefore produces measured_fps in the high teens, not 30. Ratio-based
    scoring keeps the anti-cheat signal (static + i-frame-only stay near zero)
    while accepting capture-rate limits on the evaluator side.

    Full (5): measured ≥ 50% of expected (contestant is rendering continuously
              and we're sampling fast enough to see it).
    Partial (3): measured ≥ 25% of expected (some motion, but reduced — e.g.
                 i-frame-only with a non-trivial cycle).
    Zero (0): below 25% — almost certainly a static-image or single-frame cheat.
    """
    if expected <= 0:
        return 0
    ratio = measured / expected
    if ratio >= 0.50:
        return 5
    if ratio >= 0.25:
        return 3
    return 0


def score_cpu(
    mean_cpu_percent: float | None,
    measured_h265_fps: float,
    expected_h265_fps: float = EXPECTED_FPS["h265"],
    gate_fps_ratio: float = CPU_GATE_H265_FPS_RATIO,
) -> tuple[int, str | None]:
    """Map a measured CPU mean into 0-10 points with gating.

    Evaluation order (first match wins):
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
        points, reason = score_cpu(None, measured_h265_fps)
        block["points"] = points
        block["gate_reason"] = reason or "sampler_no_data"
        return block

    sample_count = int(cpu_in.get("sample_count") or 0)
    mean_pct = cpu_in.get("mean_percent")
    if not isinstance(mean_pct, (int, float)):
        mean_pct = None
    if sample_count < CPU_MIN_SAMPLES:
        mean_pct = None

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


def score_codec(codec: str, metrics: dict) -> CodecScore:
    rate_wm = float(metrics.get("watermark_recognition_rate") or 0.0)
    rate_color = float(metrics.get("color_check_rate") or 0.0)
    mean_ssim = float(metrics.get("mean_ssim") or 0.0)
    measured_fps = float(metrics.get("measured_fps") or 0.0)
    expected_fps = EXPECTED_FPS[codec]
    return CodecScore(
        codec=codec,
        correctness_points=score_correctness(rate_wm, rate_color, mean_ssim),
        fps_points=score_fps(measured_fps, expected_fps),
        measured_fps=measured_fps,
        expected_fps=expected_fps,
        watermark_recognition_rate=rate_wm,
        color_check_rate=rate_color,
        mean_ssim=mean_ssim,
    )


def _load_chromium_version(install_prefix: Path | None) -> str | None:
    if not install_prefix:
        return None
    f = install_prefix / "playwright_chromium.version"
    return f.read_text().strip() if f.exists() else None


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


def _cli() -> int:
    p = argparse.ArgumentParser(description="Score analyzer metrics into score.json.")
    p.add_argument("--h264", type=Path, required=False)
    p.add_argument("--h265", type=Path, required=False)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", type=Path, required=False)
    p.add_argument("--install-prefix", type=Path, required=False,
                   help="Path to third_party/install/ for Chromium version pickup.")
    p.add_argument("--failure-reason", type=str, default=None)
    p.add_argument("--h264-reason", type=str, default=None)
    p.add_argument("--h265-reason", type=str, default=None)
    p.add_argument("--cpu-override-reason", type=str, default=None,
                   help="Force a gate_reason in the cpu block (used by the host "
                        "container wrapper to flag container_mode_unsupported).")
    args = p.parse_args()

    def _load(path: Path | None) -> dict | None:
        if not path or not path.exists():
            return None
        return json.loads(path.read_text())

    score = build_score(
        _load(args.h264),
        _load(args.h265),
        _load_chromium_version(args.install_prefix),
        failure_reason=args.failure_reason,
        per_round_reasons={"h264": args.h264_reason, "h265": args.h265_reason},
        cpu_override_reason=args.cpu_override_reason,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(score, indent=2))

    if args.report:
        from report import render_report
        render_report(
            score=score,
            h264_metrics=_load(args.h264),
            h265_metrics=_load(args.h265),
            output=args.report,
            run_dir=args.output.parent,
        )

    print(json.dumps(score))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
