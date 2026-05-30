"""Score the analyzer's metrics into the final 30-point objective total.

2K: 5 correctness + 5 FPS, 4K: 5 correctness + 10 FPS, plus a
0–5 CPU sub-score sampled during the 2K profile capture window. Thresholds
are duplicated from openspec/specs/evaluator/spec.md and design.md; bumping
one without bumping the others is a regression.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from lib.decode_forensics import VERDICT_INCONCLUSIVE, VERDICT_VIOLATION
from lib.profiles import PROFILES


EXPECTED_FPS: dict[str, float] = {name: float(spec.fps) for name, spec in PROFILES.items()}


# Level-0 gate. Both profiles must earn full decode-correctness marks before
# performance points contribute to the objective total. The gate reuses the
# published score_correctness full-mark band; there is no separate threshold.
GATE_PROFILES = ("2k", "4k")
GATE_CPU_REASON = "gate_failed"
"""cpu `gate_reason` when the CPU sub-score is voided by a failed level-0 gate."""


# FPS scoring tunables. Per-profile so 2K (easier) can be held to a stricter
# bar than 4K. See openspec/specs/evaluator/spec.md — Scoring requirement.
FPS_FULL_RATIO_BY_PROFILE: dict[str, float] = {"2k": 0.85}
"""measured_fps / expected_fps at or above this → full 5 FPS points."""

FPS_PARTIAL_RATIO_BY_PROFILE: dict[str, float] = {"2k": 0.50}
"""measured_fps / expected_fps at or above this (but below the full ratio)
→ partial 3 FPS points. Below this → 0 FPS points."""


def _validate_fps_thresholds() -> None:
    """Enforce at import time: every profile has both thresholds and partial < full.

    Using a raise (not assert) so this still fires under `python -O`. Adding a
    new profile in lib/profiles.PROFILES MUST also add entries to both dicts —
    no silent fallback ratio.
    """
    threshold_profiles = {"2k"}
    missing_full = threshold_profiles - set(FPS_FULL_RATIO_BY_PROFILE)
    missing_partial = threshold_profiles - set(FPS_PARTIAL_RATIO_BY_PROFILE)
    if missing_full or missing_partial:
        raise RuntimeError(
            f"FPS scoring dicts incomplete: missing from FPS_FULL_RATIO_BY_PROFILE={sorted(missing_full)}, "
            f"missing from FPS_PARTIAL_RATIO_BY_PROFILE={sorted(missing_partial)}"
        )
    unknown = (set(FPS_FULL_RATIO_BY_PROFILE) | set(FPS_PARTIAL_RATIO_BY_PROFILE)) - set(PROFILES)
    if unknown:
        raise RuntimeError(f"FPS scoring dicts name unknown profiles: {sorted(unknown)}")
    for p in threshold_profiles:
        full = FPS_FULL_RATIO_BY_PROFILE[p]
        partial = FPS_PARTIAL_RATIO_BY_PROFILE[p]
        if partial >= full:
            raise RuntimeError(
                f"FPS_PARTIAL_RATIO_BY_PROFILE[{p!r}]={partial} must be < FPS_FULL_RATIO_BY_PROFILE[{p!r}]={full}"
            )


_validate_fps_thresholds()


# CPU sub-score tunables. Module-level so calibration is a one-line change.
# Effective values applied to each run are echoed into score.json.cpu.thresholds_used.
CPU_GATE_FPS_RATIO: float = 0.65
"""measured sampled-profile fps / expected fps below this -> CPU score gated to 0."""

CPU_FULL_THRESHOLD_PERCENT: float = 5.0
"""mean_cpu_percent at or below this → full 10 points."""

CPU_PARTIAL_START_PERCENT: float = 6.0
"""Anchor of the linear-decay partial-credit band; >5% to <6% clamps to 10."""

CPU_ZERO_THRESHOLD_PERCENT: float = 20.0
"""mean_cpu_percent above this → 0 points."""

CPU_MIN_SAMPLES: int = 3
"""Below this sample count, mean_cpu_percent is treated as missing."""


@dataclass
class ProfileScore:
    profile: str
    correctness_points: int
    fps_points: float
    measured_fps: float
    expected_fps: float
    watermark_recognition_rate: float
    color_check_rate: float
    mean_ssim: float
    fps_full_threshold_used: float
    fps_partial_threshold_used: float

    @property
    def total(self) -> float:
        return self.correctness_points + self.fps_points

    def to_dict(self) -> dict:
        out = {
            "correctness_points": self.correctness_points,
            "fps_points": self.fps_points,
            "total": self.total,
            "measured_fps": self.measured_fps,
            "expected_fps": self.expected_fps,
            "watermark_recognition_rate": self.watermark_recognition_rate,
            "color_check_rate": self.color_check_rate,
            "mean_ssim": self.mean_ssim,
        }
        if self.profile in FPS_FULL_RATIO_BY_PROFILE:
            out["fps_full_threshold_used"] = self.fps_full_threshold_used
            out["fps_partial_threshold_used"] = self.fps_partial_threshold_used
        if self.profile == "4k":
            out["fps_scoring_mode"] = "linear_absolute"
            out["fps_linear_full_score"] = 10
            out["fps_full_threshold_used"] = None
            out["fps_partial_threshold_used"] = None
        return out


def score_correctness(rate_wm: float, rate_color: float, mean_ssim: float) -> int:
    """Correctness band, max 5 (was 10 before the codec→profile switch).

    Full 5: watermark and color rates ≥0.95 AND mean SSIM ≥0.90.
    Partial 2: watermark ≥0.80 AND mean SSIM ≥0.75 (color may be soft).
    Zero: anything below.
    """
    if rate_wm >= 0.95 and rate_color >= 0.95 and mean_ssim >= 0.90:
        return 5
    if rate_wm >= 0.80 and mean_ssim >= 0.75:
        return 2
    return 0


def score_fps(measured: float, expected: float, profile: str) -> float:
    """Score the unique-frame FPS metric, with per-profile thresholds.

    The full-credit and partial-credit ratios are looked up from
    FPS_FULL_RATIO_BY_PROFILE / FPS_PARTIAL_RATIO_BY_PROFILE — an unknown
    profile raises KeyError (no silent fallback).

    Full (5): measured / expected ≥ FPS_FULL_RATIO_BY_PROFILE[profile].
    Partial (3): measured / expected ≥ FPS_PARTIAL_RATIO_BY_PROFILE[profile].
    Zero (0): below the partial ratio.
    """
    if profile not in PROFILES:
        raise KeyError(profile)
    if expected <= 0:
        return 0.0
    if profile == "4k":
        return round(min(max(measured, 0.0) / expected, 1.0) * 10.0, 2)
    full_ratio = FPS_FULL_RATIO_BY_PROFILE[profile]
    partial_ratio = FPS_PARTIAL_RATIO_BY_PROFILE[profile]
    ratio = measured / expected
    if ratio >= full_ratio:
        return 5
    if ratio >= partial_ratio:
        return 3
    return 0


def _gate_correctness_points(metrics: dict | None) -> int:
    if not metrics:
        return 0
    forensics = metrics.get("decode_forensics") or {}
    if forensics.get("verdict") == VERDICT_VIOLATION:
        return 0
    rate_wm = float(metrics.get("watermark_recognition_rate") or 0.0)
    rate_color = float(metrics.get("color_check_rate") or 0.0)
    mean_ssim = float(metrics.get("mean_ssim") or 0.0)
    return score_correctness(rate_wm, rate_color, mean_ssim)


def gate_passed(profile_metrics: dict[str, dict | None]) -> bool:
    """Return whether both profiles earned full decode-correctness marks.

    Missing/incomplete metrics and decode-path violations fail closed. FPS is
    deliberately not inspected: it is a level-1 performance score.
    """
    return all(
        _gate_correctness_points(profile_metrics.get(profile)) == 5
        for profile in GATE_PROFILES
    )


CPU_PROFILE = next((name for name, spec in PROFILES.items() if spec.cpu_sampled), "2k")


def score_cpu(
    mean_cpu_percent: float | None,
    measured_fps: float,
    expected_fps: float | None = None,
    gate_fps_ratio: float = CPU_GATE_FPS_RATIO,
) -> tuple[int, str | None]:
    """Map a measured CPU mean into 0-5 points with gating.

    Evaluation order (first match wins):
        1. fps gate (sampled round didn't really play) -> 0, "<profile>_fps_below_threshold"
        2. mean missing/None → 0, "sampler_no_data"
        3. mean ≤ CPU_FULL_THRESHOLD_PERCENT → 5, None
        4. mean > CPU_ZERO_THRESHOLD_PERCENT  → 0, None
        5. partial band → linear decay anchored at PARTIAL_START / ZERO,
                          rounded, clamped to [0, 5]
    """
    expected = expected_fps if expected_fps is not None else EXPECTED_FPS[CPU_PROFILE]
    fps_gate_reason = f"{CPU_PROFILE}_fps_below_threshold"
    if expected <= 0:
        return 0, fps_gate_reason
    if measured_fps / expected < gate_fps_ratio:
        return 0, fps_gate_reason
    if mean_cpu_percent is None:
        return 0, "sampler_no_data"
    if mean_cpu_percent <= CPU_FULL_THRESHOLD_PERCENT:
        return 5, None
    if mean_cpu_percent > CPU_ZERO_THRESHOLD_PERCENT:
        return 0, None
    span = CPU_ZERO_THRESHOLD_PERCENT - CPU_PARTIAL_START_PERCENT
    raw = (CPU_ZERO_THRESHOLD_PERCENT - mean_cpu_percent) / span * 5.0
    return max(0, min(5, round(raw))), None


def _thresholds_used(sample_hz: float | None) -> dict:
    return {
        "gate_fps_ratio": CPU_GATE_FPS_RATIO,
        "full_percent": CPU_FULL_THRESHOLD_PERCENT,
        "partial_start_percent": CPU_PARTIAL_START_PERCENT,
        "zero_percent": CPU_ZERO_THRESHOLD_PERCENT,
        "min_samples": CPU_MIN_SAMPLES,
        "sample_hz": sample_hz if sample_hz is not None else 0.0,
    }


def _build_cpu_block(
    profile_metrics: dict,
    cpu_override_reason: str | None,
) -> dict:
    """Assemble the score.json `cpu` sub-object.

    Precedence:
        1. cpu_override_reason (e.g. "container_mode_unsupported", "host_failure")
        2. profile_metrics[CPU_PROFILE] is None           → "<profile>_round_failed"
        3. profile_metrics[CPU_PROFILE]["cpu"] missing    → "sampler_no_data"
        4. sample_count < CPU_MIN_SAMPLES                 → "sampler_no_data"
        5. normal scoring via score_cpu
    """
    block: dict = {
        "points": 0,
        "mean_percent": None,
        "sample_count": 0,
        "sample_window_ms": 0,
        "ncpu": None,
        "normalization": "all_cores_total",
        "measured_on_profile": CPU_PROFILE,
        "gate_profile": CPU_PROFILE,
        "expected_fps": EXPECTED_FPS[CPU_PROFILE],
        "measured_fps": 0.0,
        "thresholds_used": _thresholds_used(None),
        "gated": True,
        "gate_reason": None,
    }
    if cpu_override_reason:
        block["gate_reason"] = cpu_override_reason
        return block

    sampled_metrics = profile_metrics.get(CPU_PROFILE)
    if sampled_metrics is None:
        block["gate_reason"] = f"{CPU_PROFILE}_round_failed"
        return block

    cpu_in = sampled_metrics.get("cpu")
    measured_fps = float(sampled_metrics.get("measured_fps") or 0.0)
    block["measured_fps"] = measured_fps
    if cpu_in is None:
        block["gate_reason"] = "sampler_no_data"
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

    if mean_pct is None:
        block["gate_reason"] = "sampler_no_data"
        return block

    points, reason = score_cpu(mean_pct, measured_fps)
    block["points"] = points
    block["gate_reason"] = reason
    block["gated"] = reason is not None
    return block


def score_profile(profile: str, metrics: dict) -> ProfileScore:
    rate_wm = float(metrics.get("watermark_recognition_rate") or 0.0)
    rate_color = float(metrics.get("color_check_rate") or 0.0)
    mean_ssim = float(metrics.get("mean_ssim") or 0.0)
    measured_fps = float(metrics.get("measured_fps") or 0.0)
    expected_fps = EXPECTED_FPS[profile]
    return ProfileScore(
        profile=profile,
        correctness_points=score_correctness(rate_wm, rate_color, mean_ssim),
        fps_points=score_fps(measured_fps, expected_fps, profile),
        measured_fps=measured_fps,
        expected_fps=expected_fps,
        watermark_recognition_rate=rate_wm,
        color_check_rate=rate_color,
        mean_ssim=mean_ssim,
        fps_full_threshold_used=FPS_FULL_RATIO_BY_PROFILE.get(profile, 0.0),
        fps_partial_threshold_used=FPS_PARTIAL_RATIO_BY_PROFILE.get(profile, 0.0),
    )


def _load_chromium_version(install_prefix: Path | None) -> str | None:
    if not install_prefix:
        return None
    f = install_prefix / "playwright_chromium.version"
    return f.read_text().strip() if f.exists() else None


def build_score(
    profile_metrics: dict[str, dict | None],
    chromium_version: str | None,
    failure_reason: str | None = None,
    per_round_reasons: dict | None = None,
    cpu_override_reason: str | None = None,
) -> dict:
    out: dict = {
        "max_score": 30,
        "objective_total": 0,
        "cpu": None,
        "chromium_version": chromium_version,
    }
    for profile in PROFILES:
        out[profile] = None

    if failure_reason:
        out["reason"] = failure_reason

    review_required = False
    cpu_decode_override: str | None = None

    # Pass 1: score every profile block, applying the per-profile decode-path gate.
    for profile in PROFILES:
        metrics = profile_metrics.get(profile)
        if metrics is None:
            continue
        s = score_profile(profile, metrics)
        pd = s.to_dict()

        # Decode-path gate (fail-open): only a positive `violation` deducts.
        forensics = metrics.get("decode_forensics") or {}
        verdict = forensics.get("verdict")
        if verdict:
            pd["decode_path"] = {
                "verdict": verdict,
                "checks": forensics.get("checks"),
                "evidence": forensics.get("evidence"),
            }
        if verdict == VERDICT_VIOLATION:
            pd["correctness_points"] = 0
            pd["fps_points"] = 0
            pd["total"] = 0
            if profile == CPU_PROFILE:
                cpu_decode_override = "decode_path_violation"
        elif verdict == VERDICT_INCONCLUSIVE:
            review_required = True

        out[profile] = pd

    out["review_required"] = review_required

    # Level-0 gate: both profiles must earn full decode correctness before the
    # level-1 FPS and CPU sub-scores contribute to the objective total.
    gate_pass = gate_passed(profile_metrics)
    out["gate"] = {
        "passed": gate_pass,
        "2k_correctness_points": (out.get("2k") or {}).get("correctness_points", 0),
        "4k_correctness_points": (out.get("4k") or {}).get("correctness_points", 0),
    }

    # On gate failure level-1 points are audit-only: preserve fps_points while
    # excluding them from each profile subtotal and from objective_total.
    if not gate_pass:
        for profile in PROFILES:
            block = out.get(profile)
            if block:
                block["total"] = block.get("correctness_points", 0)

    if per_round_reasons:
        for profile, reason in per_round_reasons.items():
            if not reason:
                continue
            block = out.get(profile) or {}
            block["reason"] = reason
            out[profile] = block

    cpu_effective_override = cpu_override_reason
    if not cpu_effective_override and cpu_decode_override:
        cpu_effective_override = cpu_decode_override
    if failure_reason and not cpu_effective_override:
        cpu_effective_override = "host_failure"

    cpu_block = _build_cpu_block(profile_metrics, cpu_effective_override)
    # A failed gate voids the CPU sub-score — but only when the CPU block would
    # otherwise have scored. A more specific reason already set by
    # _build_cpu_block (decode_path_violation, 2k_round_failed, sampler_no_data,
    # container_mode_unsupported, host_failure) is more informative and wins.
    if not gate_pass and not cpu_block.get("gated"):
        cpu_block["points"] = 0
        cpu_block["gated"] = True
        cpu_block["gate_reason"] = GATE_CPU_REASON
    out["cpu"] = cpu_block

    objective_total = 0
    for profile in PROFILES:
        block = out.get(profile)
        if block:
            objective_total += block.get("total", 0)
    objective_total += cpu_block["points"]
    out["objective_total"] = objective_total
    return out


def _parse_kv(items: list[str], label: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"{label}: expected PROFILE=VALUE, got: {item!r}")
        k, v = item.split("=", 1)
        out[k] = v
    return out


def _cli() -> int:
    p = argparse.ArgumentParser(description="Score analyzer metrics into score.json.")
    p.add_argument("--metrics", action="append", default=[],
                   metavar="PROFILE=PATH",
                   help="Per-profile metrics JSON (repeat once per profile).")
    p.add_argument("--output", type=Path, required=False)
    p.add_argument("--report", type=Path, required=False)
    p.add_argument("--install-prefix", type=Path, required=False,
                   help="Path to third_party/install/ for Chromium version pickup.")
    p.add_argument("--failure-reason", type=str, default=None)
    p.add_argument("--profile-reason", action="append", default=[],
                   metavar="PROFILE=REASON",
                   help="Per-profile failure reason (repeat once per profile).")
    p.add_argument("--cpu-override-reason", type=str, default=None,
                   help="Force a gate_reason in the cpu block (used by the host "
                        "container wrapper to flag container_mode_unsupported).")
    args = p.parse_args()

    if args.output is None:
        raise SystemExit("--output is required")

    metrics_paths = _parse_kv(args.metrics, "--metrics")
    profile_metrics: dict[str, dict | None] = {prof: None for prof in PROFILES}
    for profile, path_str in metrics_paths.items():
        if profile not in PROFILES:
            raise SystemExit(f"--metrics: unknown profile {profile!r}; "
                             f"valid: {sorted(PROFILES.keys())}")
        path = Path(path_str)
        profile_metrics[profile] = json.loads(path.read_text()) if path.exists() else None

    per_round_reasons = _parse_kv(args.profile_reason, "--profile-reason")

    score = build_score(
        profile_metrics,
        _load_chromium_version(args.install_prefix),
        failure_reason=args.failure_reason,
        per_round_reasons=per_round_reasons,
        cpu_override_reason=args.cpu_override_reason,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(score, indent=2))

    if args.report:
        from report import render_report
        render_report(
            score=score,
            profile_metrics=profile_metrics,
            output=args.report,
            run_dir=args.output.parent,
        )

    print(json.dumps(score))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
