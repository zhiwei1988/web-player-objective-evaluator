"""report.py surfaces the level-0 gate verdict: a banner + summary row on failure,
and the skipped-profile / voided-CPU reasons."""

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
    }


def _4k(measured_fps: float) -> dict:
    return {
        "watermark_recognition_rate": 1.0,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "measured_fps": measured_fps,
    }


def test_report_renders_gate_failed_banner_and_reasons(tmp_path):
    # Full correctness but partial fps -> gate fails.
    metrics = {"2k": _2k(EXPECTED["2k"] * 0.70), "4k": _4k(EXPECTED["4k"])}
    score = scorer.build_score(metrics, chromium_version="t")
    out = tmp_path / "report.html"
    report.render_report(score=score, profile_metrics=metrics, output=out, run_dir=tmp_path)
    body = out.read_text()
    assert "Level-0 gate failed" in body
    assert "level-0 gate (2k)" in body
    assert "skipped_gate_failed" in body  # the 4K reason row
    assert "gate_failed" in body          # the voided CPU gate_reason


def test_report_no_gate_banner_when_passed(tmp_path):
    metrics = {"2k": _2k(EXPECTED["2k"]), "4k": _4k(EXPECTED["4k"])}
    score = scorer.build_score(metrics, chromium_version="t")
    out = tmp_path / "report.html"
    report.render_report(score=score, profile_metrics=metrics, output=out, run_dir=tmp_path)
    body = out.read_text()
    assert "Level-0 gate failed" not in body
    assert "level-0 gate (2k)" in body  # summary row still present
    assert "passed" in body
