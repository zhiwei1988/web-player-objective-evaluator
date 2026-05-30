"""Tests for the scorer's decode-path gate: a `violation` verdict zeros the
profile's correctness + FPS (and CPU for 2k); `ok`/`inconclusive`/absent are
fail-open and leave scoring unchanged (inconclusive sets review_required).
"""

from __future__ import annotations

import scorer

EXPECTED = scorer.EXPECTED_FPS


def _clean_metrics(measured_fps: float) -> dict:
    return {
        "watermark_recognition_rate": 1.0,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "measured_fps": measured_fps,
    }


def _with_cpu(metrics: dict, mean_cpu: float) -> dict:
    metrics = dict(metrics)
    metrics["cpu"] = {
        "mean_percent": mean_cpu,
        "sample_count": 9,
        "sample_window_ms": 9000,
        "ncpu": 8,
        "normalization": "all_cores_total",
        "sample_hz_used": 1.0,
    }
    return metrics


def _forensics(metrics: dict, verdict: str) -> dict:
    metrics = dict(metrics)
    metrics["decode_forensics"] = {
        "verdict": verdict,
        "checks": {"video_decoder_active": verdict == "violation", "sink_codecs": []},
        "evidence": ["test"],
    }
    return metrics


def test_violation_zeros_4k_correctness_and_fps():
    out = scorer.build_score(
        {"2k": None, "4k": _forensics(_clean_metrics(EXPECTED["4k"]), "violation")},
        chromium_version="t",
    )
    assert out["4k"]["correctness_points"] == 0
    assert out["4k"]["fps_points"] == 0
    assert out["4k"]["total"] == 0
    assert out["4k"]["decode_path"]["verdict"] == "violation"


def test_violation_on_2k_also_zeros_cpu():
    # 2k metrics that would otherwise earn full FPS + full CPU.
    m2k = _forensics(_with_cpu(_clean_metrics(EXPECTED["2k"]), 2.0), "violation")
    out = scorer.build_score({"2k": m2k, "4k": None}, chromium_version="t")
    assert out["2k"]["correctness_points"] == 0
    assert out["2k"]["fps_points"] == 0
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "decode_path_violation"


def test_violation_excludes_profile_from_objective_total():
    out = scorer.build_score(
        {"2k": None, "4k": _forensics(_clean_metrics(EXPECTED["4k"]), "violation")},
        chromium_version="t",
    )
    # 4k contributed 0; with 2k absent and cpu gated, objective_total is 0.
    assert out["objective_total"] == 0


def test_ok_verdict_scores_normally_and_records_block():
    out = scorer.build_score(
        {"2k": None, "4k": _forensics(_clean_metrics(EXPECTED["4k"]), "ok")},
        chromium_version="t",
    )
    assert out["4k"]["correctness_points"] == 5
    assert out["4k"]["fps_points"] == 10.0
    assert out["4k"]["decode_path"]["verdict"] == "ok"
    assert out.get("review_required") in (False, None)


def test_inconclusive_scores_normally_but_flags_review():
    out = scorer.build_score(
        {"2k": None, "4k": _forensics(_clean_metrics(EXPECTED["4k"]), "inconclusive")},
        chromium_version="t",
    )
    assert out["4k"]["correctness_points"] == 5
    assert out["4k"]["fps_points"] == 10.0
    assert out["review_required"] is True


def test_absent_forensics_is_backward_compatible():
    out = scorer.build_score(
        {"2k": None, "4k": _clean_metrics(EXPECTED["4k"])},
        chromium_version="t",
    )
    assert out["4k"]["correctness_points"] == 5
    assert "decode_path" not in out["4k"]
    assert out.get("review_required") in (False, None)
