"""Tests for the level-0 gate: the 2K correctness AND fps sub-scores must both be
at full marks before the remaining profiles and the CPU sub-score are scored.

On gate failure the 2K block keeps its actual sub-scores, the 4K round is scored 0
with reason "skipped_gate_failed", and the CPU block is gated to 0 with gate_reason
"gate_failed". A decode-path violation on 2K keeps its more-specific CPU reason.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import scorer

EXPECTED = scorer.EXPECTED_FPS
ROOT = Path(__file__).resolve().parent.parent


def _2k(measured_fps: float, *, wm: float = 1.0, color: float = 1.0,
        ssim: float = 0.95, mean_cpu: float | None = None) -> dict:
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


def _4k(measured_fps: float) -> dict:
    return {
        "watermark_recognition_rate": 1.0,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "measured_fps": measured_fps,
    }


def _violation(metrics: dict) -> dict:
    metrics = dict(metrics)
    metrics["decode_forensics"] = {
        "verdict": "violation",
        "checks": {"video_decoder_active": True, "sink_codecs": []},
        "evidence": ["video_decoder:avc1.640028"],
    }
    return metrics


# --- 1.1 gate_passed unit cases ---------------------------------------------

def test_gate_passed_when_corr_and_fps_full():
    assert scorer.gate_passed(_2k(EXPECTED["2k"])) is True


def test_gate_fails_when_correctness_below_full():
    # watermark below the 0.95 full-mark band -> correctness != 5.
    assert scorer.gate_passed(_2k(EXPECTED["2k"], wm=0.80)) is False


def test_gate_fails_when_fps_below_full():
    # 0.84 ratio -> 2K fps falls to the partial band (3), not full.
    assert scorer.gate_passed(_2k(EXPECTED["2k"] * 0.84)) is False


def test_gate_fails_on_absent_or_empty_metrics():
    assert scorer.gate_passed(None) is False
    assert scorer.gate_passed({}) is False


def test_gate_fails_on_decode_path_violation_even_when_rates_full():
    assert scorer.gate_passed(_violation(_2k(EXPECTED["2k"]))) is False


# --- 1.2 build_score on gate failure ----------------------------------------

def test_build_score_gate_failure_keeps_2k_zeros_downstream():
    # 2K: full correctness (5) but partial fps (3) -> gate fails, 2K total 8.
    # Ratio 0.70 clears the CPU fps-gate (0.65) so the CPU block would otherwise
    # have scored — proving the gate_failed override (not 2k_fps_below_threshold).
    out = scorer.build_score(
        {"2k": _2k(EXPECTED["2k"] * 0.70, mean_cpu=2.0), "4k": _4k(EXPECTED["4k"])},
        chromium_version="t",
    )
    assert out["gate"]["passed"] is False
    assert out["gate"]["profile"] == "2k"
    assert out["2k"]["correctness_points"] == 5
    assert out["2k"]["fps_points"] == 3
    assert out["2k"]["total"] == 8
    assert out["4k"]["total"] == 0
    assert out["4k"]["reason"] == "skipped_gate_failed"
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "gate_failed"
    assert out["objective_total"] == 8


# --- 1.3 build_score on gate pass -------------------------------------------

def test_build_score_gate_pass_scores_downstream_normally():
    out = scorer.build_score(
        {"2k": _2k(EXPECTED["2k"], mean_cpu=2.0), "4k": _4k(EXPECTED["4k"])},
        chromium_version="t",
    )
    assert out["gate"]["passed"] is True
    assert out["gate"]["correctness_points"] == 5
    assert out["gate"]["fps_points"] == 5
    assert out["2k"]["total"] == 10
    assert out["4k"]["total"] == 15
    assert out["cpu"]["points"] == 5
    assert out["objective_total"] == out["2k"]["total"] + out["4k"]["total"] + out["cpu"]["points"]
    assert out["objective_total"] == 30


# --- 1.4 decode-path precedence over the gate -------------------------------

def test_build_score_2k_violation_keeps_decode_path_cpu_reason():
    out = scorer.build_score(
        {"2k": _violation(_2k(EXPECTED["2k"], mean_cpu=2.0)), "4k": _4k(EXPECTED["4k"])},
        chromium_version="t",
    )
    assert out["gate"]["passed"] is False
    assert out["2k"]["correctness_points"] == 0
    assert out["2k"]["fps_points"] == 0
    assert out["4k"]["total"] == 0
    # decode_path_violation is more specific than gate_failed and wins.
    assert out["cpu"]["gate_reason"] == "decode_path_violation"
    assert out["cpu"]["points"] == 0


def test_build_score_2k_below_cpu_floor_keeps_specific_cpu_reason():
    # Ratio 0.60 < CPU_GATE_FPS_RATIO (0.65): gate fails AND the CPU fps floor is
    # missed, so the more specific "2k_fps_below_threshold" wins over "gate_failed".
    out = scorer.build_score(
        {"2k": _2k(EXPECTED["2k"] * 0.60, mean_cpu=2.0), "4k": _4k(EXPECTED["4k"])},
        chromium_version="t",
    )
    assert out["gate"]["passed"] is False
    assert out["4k"]["total"] == 0
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gate_reason"] == "2k_fps_below_threshold"


# --- 1.5 short-circuit cannot change the score ------------------------------

def test_build_score_zeros_4k_even_when_4k_metrics_supplied():
    # Manual scorer run handed a perfectly good 4K alongside a non-perfect 2K:
    # build_score must still zero 4K and CPU, identical to the orchestrated skip.
    non_perfect_2k = _2k(EXPECTED["2k"], wm=0.80, mean_cpu=2.0)
    out = scorer.build_score(
        {"2k": non_perfect_2k, "4k": _4k(EXPECTED["4k"])},
        chromium_version="t",
    )
    assert out["gate"]["passed"] is False
    assert out["4k"]["total"] == 0
    assert out["4k"]["reason"] == "skipped_gate_failed"
    assert out["cpu"]["points"] == 0
    assert out["objective_total"] == out["2k"]["total"]


# --- 1.6 --gate-check CLI ----------------------------------------------------

def _run_gate_check(tmp_path: Path, metrics: dict) -> subprocess.CompletedProcess:
    metrics_file = tmp_path / "2k_metrics.json"
    metrics_file.write_text(json.dumps(metrics))
    out_file = tmp_path / "should_not_exist.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scorer.py"),
         "--gate-check", f"2k={metrics_file}",
         "--output", str(out_file)],
        capture_output=True, text=True,
    )
    assert not out_file.exists(), "--gate-check must not write an output file"
    return proc


def test_gate_check_cli_exit_zero_on_pass(tmp_path):
    proc = _run_gate_check(tmp_path, _2k(EXPECTED["2k"]))
    assert proc.returncode == 0


def test_gate_check_cli_exit_nonzero_on_fail(tmp_path):
    proc = _run_gate_check(tmp_path, _2k(EXPECTED["2k"] * 0.40))
    assert proc.returncode != 0
