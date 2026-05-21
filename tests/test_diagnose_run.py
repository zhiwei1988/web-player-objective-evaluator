from __future__ import annotations

import json

from scripts.diagnose_run import build_report


def test_diagnose_run_reports_capture_bottleneck(tmp_path):
    run = tmp_path / "results" / "self_20260521_120000"
    run.mkdir(parents=True)
    (run / "score.json").write_text(json.dumps({
        "objective_total": 12,
        "2k": {"measured_fps": 13.0, "fps_points": 2},
    }))
    (run / "2k_metrics.json").write_text(json.dumps({
        "measured_fps": 13.0,
        "capture_sampling_fps": 13.2,
        "target_capture_fps": 25,
        "duration": 56.8,
        "target_capture_duration": 30,
        "capture_span_overrun_ratio": 1.89,
        "unique_frame_count": 738,
        "total_shots": 750,
        "repeat_frame_rate": 0.02,
        "dropped_or_skipped_frame_rate": 0.01,
        "frame_delta_histogram": {"1": 720, "0": 15, "2": 5},
    }))

    report = build_report(run, include_host=False)

    assert "Run: " in report
    assert "2k" in report
    assert "measured_fps: 13.00" in report
    assert "capture_sampling_fps: 13.20" in report
    assert "Likely bottleneck: evaluator capture / host throughput" in report


def test_diagnose_run_reports_player_bottleneck(tmp_path):
    run = tmp_path / "results" / "self_20260521_120000"
    run.mkdir(parents=True)
    (run / "2k_metrics.json").write_text(json.dumps({
        "measured_fps": 13.0,
        "capture_sampling_fps": 24.8,
        "target_capture_fps": 25,
        "duration": 30.1,
        "target_capture_duration": 30,
        "capture_span_overrun_ratio": 1.0,
        "unique_frame_count": 391,
        "total_shots": 750,
        "repeat_frame_rate": 0.48,
        "dropped_or_skipped_frame_rate": 0.0,
        "frame_delta_histogram": {"0": 350, "1": 390},
    }))

    report = build_report(run, include_host=False)

    assert "Likely bottleneck: contestant playback / decode / rendering" in report
