"""report.py distinguishes a failed correctness gate from unearned performance."""

from __future__ import annotations

import report
import scorer

EXPECTED = scorer.EXPECTED_FPS


def _2k(measured_fps: float, *, wm: float = 1.0) -> dict:
    return {
        "watermark_recognition_rate": wm,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "measured_fps": measured_fps,
        "cpu": {
            "mean_percent": 2.0,
            "sample_count": 9,
            "sample_window_ms": 9000,
            "ncpu": 8,
            "normalization": "all_cores_total",
            "sample_hz_used": 1.0,
        },
    }


def _4k(measured_fps: float, *, wm: float = 1.0) -> dict:
    return {
        "watermark_recognition_rate": wm,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "measured_fps": measured_fps,
    }


def _render(tmp_path, metrics: dict) -> str:
    score = scorer.build_score(metrics, chromium_version="t")
    out = tmp_path / "report.html"
    report.render_report(score=score, profile_metrics=metrics, output=out, run_dir=tmp_path)
    return out.read_text()


def test_report_renders_correctness_gate_failed_banner_and_unscored_level_1(tmp_path):
    body = _render(tmp_path, {
        "2k": _2k(EXPECTED["2k"]),
        "4k": _4k(EXPECTED["4k"], wm=0.80),
    })
    assert "Level-0 gate failed" in body
    assert "level-0 gate (decode correctness)" in body
    assert "2K correctness 5/5" in body
    assert "4K correctness 2/5" in body
    assert "level-1 FPS and CPU points are not scored" in body
    assert "not scored: level-0 gate failed" in body
    assert "gate_failed" in body
    assert "skipped_gate_failed" not in body


def test_report_gate_pass_with_poor_performance_shows_unearned_points_normally(tmp_path):
    body = _render(tmp_path, {
        "2k": _2k(0.0),
        "4k": _4k(0.0),
    })
    assert "Level-0 gate failed" not in body
    assert "level-0 gate (decode correctness)" in body
    assert "passed" in body
    assert "not scored: level-0 gate failed" not in body
    assert "<tr><th>fps</th><td>0/5</td></tr>" in body
