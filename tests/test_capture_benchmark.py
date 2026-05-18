from __future__ import annotations

import json

import pytest

from scripts.capture_benchmark import summarize_capture


def test_capture_benchmark_reports_intervals_and_stability(tmp_path):
    screenshots = tmp_path / "2k_screenshots"
    screenshots.mkdir()
    (screenshots / "timestamps.json").write_text(json.dumps({
        "profile": "2k",
        "capture_strategy": "playwright",
        "target_fps": 25,
        "target_duration_s": 30,
        "timestamps": [0.0, 0.04, 0.09, 0.15],
    }))
    metrics = tmp_path / "2k_metrics.json"
    metrics.write_text(json.dumps({
        "watermark_recognition_rate": 0.99,
        "color_check_rate": 0.98,
        "mean_ssim": 0.95,
    }))

    out = summarize_capture(screenshots, metrics)

    assert out["shot_count"] == 4
    assert out["actual_capture_duration_s"] == 0.15
    assert out["capture_sampling_fps"] == pytest.approx(4 / 0.15)
    assert out["avg_interval_ms"] == pytest.approx(50.0)
    assert out["p50_interval_ms"] == pytest.approx(50.0)
    assert out["p90_interval_ms"] == pytest.approx(60.0)
    assert out["p99_interval_ms"] == pytest.approx(60.0)
    assert out["recognition_stability"]["preserves_full_correctness_thresholds"] is True
