"""report.py surfaces the decode-path verdict per profile and the top-level
review-required banner."""

from __future__ import annotations

import report
import scorer


def _metrics(verdict: str) -> dict:
    return {
        "watermark_recognition_rate": 1.0,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "measured_fps": 20.0,
        "decode_forensics": {"verdict": verdict, "checks": {}, "evidence": ["video_decoder:avc1.640028"]},
    }


def test_report_renders_decode_path_violation(tmp_path):
    m = _metrics("violation")
    score = scorer.build_score({"2k": None, "4k": m}, chromium_version="t")
    out = tmp_path / "report.html"
    report.render_report(score=score, profile_metrics={"2k": None, "4k": m}, output=out, run_dir=tmp_path)
    body = out.read_text()
    assert "decode path" in body
    assert "violation" in body


def test_report_renders_review_required_banner(tmp_path):
    m = _metrics("inconclusive")
    score = scorer.build_score({"2k": None, "4k": m}, chromium_version="t")
    out = tmp_path / "report.html"
    report.render_report(score=score, profile_metrics={"2k": None, "4k": m}, output=out, run_dir=tmp_path)
    assert "Review required" in out.read_text()


def test_report_no_review_banner_when_clean(tmp_path):
    m = _metrics("ok")
    score = scorer.build_score({"2k": None, "4k": m}, chromium_version="t")
    out = tmp_path / "report.html"
    report.render_report(score=score, profile_metrics={"2k": None, "4k": m}, output=out, run_dir=tmp_path)
    assert "Review required" not in out.read_text()
