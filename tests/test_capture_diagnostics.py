from __future__ import annotations

import json

import pytest

import analyzer


def test_capture_diagnostics_from_timestamps_and_frames(tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    (shots / "timestamps.json").write_text(json.dumps({
        "timestamps": [10.0, 10.5, 11.0, 11.5],
        "target_fps": 25,
        "target_duration_s": 1.0,
    }))

    metrics = analyzer.CodecMetrics(
        total_shots=4,
        frame_numbers=[100, 101, 103, 103],
        duration=1.5,
    )

    analyzer.apply_capture_diagnostics("2k", shots, metrics)

    assert metrics.capture_sampling_fps == pytest.approx(4 / 1.5)
    assert metrics.target_capture_fps == 25
    assert metrics.target_capture_duration == 1.0
    assert metrics.capture_span_overrun_ratio == pytest.approx(1.5)
    assert metrics.frame_delta_histogram == {"0": 1, "1": 1, "2": 1}
    assert metrics.repeat_frame_rate == pytest.approx(1 / 3)
    assert metrics.dropped_or_skipped_frame_rate == pytest.approx(1 / 3)
    assert metrics.frame_progress_fps == pytest.approx(3 / 1.5)


def test_frame_delta_histogram_handles_loop_boundary(tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    (shots / "timestamps.json").write_text(json.dumps({
        "timestamps": [0.0, 0.04, 0.08, 0.12],
        "target_fps": 25,
        "target_duration_s": 30,
    }))

    metrics = analyzer.CodecMetrics(
        total_shots=4,
        frame_numbers=[748, 749, 0, 1],
        duration=0.12,
    )

    analyzer.apply_capture_diagnostics("2k", shots, metrics)

    assert metrics.frame_delta_histogram == {"1": 3}
    assert metrics.repeat_frame_rate == 0
    assert metrics.dropped_or_skipped_frame_rate == 0
    assert metrics.frame_progress_fps == pytest.approx(3 / 0.12)


def test_metrics_to_dict_keeps_existing_and_new_keys():
    metrics = analyzer.CodecMetrics(
        total_shots=2,
        watermark_recognized=2,
        color_blocks_passed=2,
        duration=1.0,
        unique_frame_count=2,
        measured_fps=2.0,
        watermark_recognition_rate=1.0,
        color_check_rate=1.0,
        mean_ssim=0.95,
        capture_sampling_fps=2.0,
        target_capture_fps=25.0,
        target_capture_duration=30.0,
        capture_span_overrun_ratio=1 / 30,
        frame_delta_histogram={"1": 1},
        repeat_frame_rate=0.0,
        dropped_or_skipped_frame_rate=0.0,
        frame_progress_fps=1.0,
    )

    out = analyzer.metrics_to_dict(metrics)

    for key in [
        "total_shots",
        "watermark_recognized",
        "color_blocks_passed",
        "duration",
        "unique_frame_count",
        "measured_fps",
        "capture_sampling_fps",
        "target_capture_fps",
        "target_capture_duration",
        "capture_span_overrun_ratio",
        "frame_delta_histogram",
        "repeat_frame_rate",
        "dropped_or_skipped_frame_rate",
        "frame_progress_fps",
    ]:
        assert key in out
