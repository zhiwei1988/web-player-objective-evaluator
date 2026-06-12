"""Tests for the decode-correctness level-0 gate.

Both profiles must earn full correctness before FPS and CPU points contribute
to the objective total. FPS is still computed for audit when the gate fails.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import scorer

EXPECTED = scorer.EXPECTED_FPS
ROOT = Path(__file__).resolve().parent.parent


def _2k(measured_fps: float, *, wm: float = 1.0, color: float = 1.0,
        ssim: float = 0.95) -> dict:
    return {
        "watermark_recognition_rate": wm,
        "color_check_rate": color,
        "mean_ssim": ssim,
        "measured_fps": measured_fps,
    }


def _4k(measured_fps: float, *, wm: float = 1.0, color: float = 1.0,
        ssim: float = 0.95, mean_cpu: float | None = None) -> dict:
    # The CPU sub-score is sampled during the 4K round, so the cpu sub-object
    # rides on the 4K profile metrics.
    block = {
        "watermark_recognition_rate": wm,
        "color_check_rate": color,
        "mean_ssim": ssim,
        "measured_fps": measured_fps,
    }
    if mean_cpu is not None:
        block["cpu"] = {
            "mean_percent": mean_cpu,
            "sample_count": 9,
            "sample_window_ms": 9000,
            "ncpu": 8,
            "clk_tck": 100,
            "normalization": "all_cores_total",
            "pgid": 12345,
            "sample_hz_used": 1.0,
        }
    return block


def _violation(metrics: dict) -> dict:
    metrics = dict(metrics)
    metrics["decode_forensics"] = {
        "verdict": "violation",
        "checks": {"video_decoder_active": True, "sink_codecs": []},
        "evidence": ["video_decoder:avc1.640028"],
    }
    return metrics


# --- gate_passed unit cases -------------------------------------------------

def test_gate_passed_when_both_profiles_have_full_correctness():
    assert scorer.gate_passed({
        "2k": _2k(0.0),
        "4k": _4k(0.0),
    }) is True


def test_gate_fails_when_either_profile_correctness_is_below_full():
    assert scorer.gate_passed({
        "2k": _2k(EXPECTED["2k"], wm=0.80),
        "4k": _4k(EXPECTED["4k"]),
    }) is False
    assert scorer.gate_passed({
        "2k": _2k(EXPECTED["2k"]),
        "4k": _4k(EXPECTED["4k"], wm=0.80),
    }) is False


def test_gate_fails_on_absent_or_empty_profile_metrics():
    assert scorer.gate_passed({}) is False
    assert scorer.gate_passed({"2k": _2k(EXPECTED["2k"])}) is False
    assert scorer.gate_passed({"2k": _2k(EXPECTED["2k"]), "4k": {}}) is False


def test_gate_verdict_does_not_depend_on_fps():
    assert scorer.gate_passed({
        "2k": _2k(0.0),
        "4k": _4k(0.0),
    }) is True


def test_gate_fails_on_decode_path_violation_even_when_rates_full():
    assert scorer.gate_passed({
        "2k": _2k(EXPECTED["2k"]),
        "4k": _violation(_4k(EXPECTED["4k"])),
    }) is False


# --- build_score on gate pass ----------------------------------------------

def test_build_score_gate_pass_scores_level_1_normally():
    out = scorer.build_score(
        {"2k": _2k(EXPECTED["2k"]), "4k": _4k(EXPECTED["4k"], mean_cpu=2.0)},
        chromium_version="t",
    )
    assert out["gate"] == {
        "passed": True,
        "2k_correctness_points": 5,
        "4k_correctness_points": 5,
    }
    assert out["2k"]["total"] == out["2k"]["correctness_points"] + out["2k"]["fps_points"]
    assert out["4k"]["total"] == out["4k"]["correctness_points"] + out["4k"]["fps_points"]
    assert out["cpu"]["points"] == 5
    assert out["objective_total"] == out["2k"]["total"] + out["4k"]["total"] + out["cpu"]["points"]


def test_build_score_perfect_run_scores_30():
    out = scorer.build_score(
        {"2k": _2k(EXPECTED["2k"]), "4k": _4k(EXPECTED["4k"], mean_cpu=2.0)},
        chromium_version="t",
    )
    assert out["2k"]["total"] == 10
    assert out["4k"]["total"] == 15
    assert out["cpu"]["points"] == 5
    assert out["objective_total"] == 30


def test_build_score_gate_pass_with_poor_performance_scores_correctness_10():
    out = scorer.build_score(
        {"2k": _2k(0.0), "4k": _4k(0.0, mean_cpu=2.0)},
        chromium_version="t",
    )
    assert out["gate"]["passed"] is True
    assert out["2k"]["fps_points"] == 0
    assert out["4k"]["fps_points"] == 0.0
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gate_reason"] == "4k_fps_below_threshold"
    assert out["objective_total"] == 10


# --- build_score on gate failure -------------------------------------------

def test_build_score_gate_failure_counts_correctness_only():
    out = scorer.build_score(
        {
            "2k": _2k(EXPECTED["2k"]),
            "4k": _4k(EXPECTED["4k"], wm=0.80, mean_cpu=2.0),
        },
        chromium_version="t",
    )
    assert out["gate"] == {
        "passed": False,
        "2k_correctness_points": 5,
        "4k_correctness_points": 2,
    }
    assert out["2k"]["fps_points"] == 5
    assert out["4k"]["fps_points"] == 10.0
    assert out["2k"]["total"] == 5
    assert out["4k"]["total"] == 2
    assert "reason" not in out["4k"]
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "gate_failed"
    assert out["objective_total"] == 7


def test_build_score_fps_never_closes_correctness_gate():
    out = scorer.build_score(
        {
            "2k": _2k(EXPECTED["2k"] * 0.70),
            "4k": _4k(EXPECTED["4k"] * 0.25, mean_cpu=2.0),
        },
        chromium_version="t",
    )
    assert out["gate"]["passed"] is True
    assert out["2k"]["total"] == 8.5
    assert out["4k"]["total"] == 7.5
    # The CPU sub-score now rides on the 4K round; a 0.25 FPS ratio there is
    # below the 0.65 CPU gate, so CPU is gated even though the correctness gate
    # stayed open. Low FPS still never closes the correctness gate.
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gate_reason"] == "4k_fps_below_threshold"
    assert out["objective_total"] == 16


def test_build_score_4k_violation_fails_gate_and_scores_zero_for_4k():
    out = scorer.build_score(
        {
            "2k": _2k(EXPECTED["2k"]),
            "4k": _violation(_4k(EXPECTED["4k"], mean_cpu=2.0)),
        },
        chromium_version="t",
    )
    assert out["gate"]["passed"] is False
    assert out["gate"]["4k_correctness_points"] == 0
    assert out["4k"]["correctness_points"] == 0
    assert out["4k"]["fps_points"] == 0
    assert out["4k"]["total"] == 0
    assert out["objective_total"] == 5


def test_build_score_4k_violation_keeps_decode_path_cpu_reason():
    # The CPU-sampled profile (4K) is the one whose decode-path violation must
    # override the CPU gate reason to decode_path_violation.
    out = scorer.build_score(
        {"2k": _2k(EXPECTED["2k"]), "4k": _violation(_4k(EXPECTED["4k"], mean_cpu=2.0))},
        chromium_version="t",
    )
    assert out["gate"]["passed"] is False
    assert out["4k"]["correctness_points"] == 0
    assert out["4k"]["fps_points"] == 0
    assert out["2k"]["total"] == 5
    assert out["cpu"]["gate_reason"] == "decode_path_violation"
    assert out["cpu"]["points"] == 0
    assert out["objective_total"] == 5


def test_build_score_4k_below_cpu_floor_keeps_specific_cpu_reason():
    out = scorer.build_score(
        {
            "2k": _2k(EXPECTED["2k"]),
            "4k": _4k(EXPECTED["4k"] * 0.60, wm=0.80, mean_cpu=2.0),
        },
        chromium_version="t",
    )
    assert out["gate"]["passed"] is False
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gate_reason"] == "4k_fps_below_threshold"


# --- execution short-circuit removal ---------------------------------------

def test_gate_check_cli_mode_is_removed():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scorer.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--gate-check" not in proc.stdout


def test_evaluator_has_no_gate_driven_capture_short_circuit():
    text = (ROOT / "scripts" / "evaluator.sh").read_text()
    assert "--gate-check" not in text
    assert "skipped_gate_failed" not in text
