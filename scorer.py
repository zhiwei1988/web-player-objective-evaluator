"""Score the analyzer's metrics into the final 30-point objective total.

10 correctness points + 5 FPS points per codec, two codecs, max 30. Thresholds
are duplicated from openspec/specs/evaluator/spec.md and design.md; bumping one
without bumping the others is a regression.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


EXPECTED_FPS = {"h264": 30.0, "h265": 25.0}


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
) -> dict:
    out: dict = {
        "max_score": 30,
        "objective_total": 0,
        "h264": None,
        "h265": None,
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
