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


def test_diagnose_run_reports_stage_timings_and_layout_warnings(tmp_path):
    run = tmp_path / "results" / "self_20260521_120000"
    shots = run / "2k_screenshots"
    shots.mkdir(parents=True)
    (run / "stage_timings.json").write_text(json.dumps({
        "stages": [
            {
                "stage": "capture",
                "profile": "2k",
                "status": "timeout",
                "duration_s": 1.0,
                "timeout_seconds": 1,
                "reason": "capture timeout after 1s",
            }
        ],
    }))
    (shots / "timestamps.json").write_text(json.dumps({
        "layout_diagnostics": {
            "warnings": ["descendant canvas is larger than clipped host"]
        }
    }))

    report = build_report(run, include_host=False)

    assert "Stage timings:" in report
    assert "capture[2k]: timeout" in report
    assert "capture timeout after 1s" in report
    assert "Layout warnings:" in report
    assert "2k: descendant canvas is larger than clipped host" in report


def test_diagnose_run_reports_contestant_memory_limit_metadata(tmp_path):
    run = tmp_path / "results" / "self_20260521_120000"
    run.mkdir(parents=True)
    (run / "score.json").write_text(json.dumps({
        "objective_total": 0,
        "reason": "contestant_memory_limit_exceeded",
        "contestant_memory_limit": "10G",
    }))
    (run / "stage_timings.json").write_text(json.dumps({
        "budgets": {"contestant_memory_max": "10G"},
        "stages": [],
    }))

    report = build_report(run, include_host=False)

    assert "contestant_memory_limit: 10G" in report
    assert "Contestant memory limit: 10G" in report


def test_diagnose_run_reports_unfinished_running_stage(tmp_path):
    run = tmp_path / "results" / "self_20260521_120000"
    run.mkdir(parents=True)
    (run / "stage_timings.json").write_text(json.dumps({
        "stages": [
            {
                "stage": "capture",
                "profile": "2k",
                "status": "running",
                "timeout_seconds": 120,
                "reason": "stage started; no final record",
            }
        ],
    }))

    report = build_report(run, include_host=False)

    assert "capture[2k]: running" in report
    assert "stage started; no final record" in report


def test_diagnose_run_without_new_diagnostics_is_backward_compatible(tmp_path):
    run = tmp_path / "results" / "self_20260521_120000"
    run.mkdir(parents=True)

    report = build_report(run, include_host=False)

    assert "No *_metrics.json files found." in report
    assert "Stage timings:" not in report
    assert "Layout warnings:" not in report
