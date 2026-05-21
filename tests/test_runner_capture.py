from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import runner


def test_default_capture_viewport_is_contract_size():
    assert runner.CAPTURE_VIEWPORT == {"width": 1280, "height": 720}


def test_clip_below_minimum_is_rejected():
    with pytest.raises(runner.PlayerClipTooSmall) as exc:
        runner.validate_player_clip({"x": 0, "y": 0, "width": 1279, "height": 720})

    assert "player-video below minimum size" in str(exc.value)


def test_timestamps_include_capture_metadata(tmp_path):
    result = runner.CaptureResult(
        success=True,
        target_fps=25.0,
        target_duration_s=30.0,
        capture_strategy="playwright",
        jpeg_quality=90,
        clip={"x": 0, "y": 0, "width": 1280, "height": 720},
    )
    result.timestamps.extend([1.0, 1.04])

    runner._write_timestamps(tmp_path, result)

    out = json.loads((tmp_path / "timestamps.json").read_text())
    assert out["target_fps"] == 25.0
    assert out["target_duration_s"] == 30.0
    assert out["capture_strategy"] == "playwright"
    assert out["jpeg_quality"] == 90
    assert out["clip"] == {"x": 0, "y": 0, "width": 1280, "height": 720}


def test_capture_meta_records_2k_cpu_profile(tmp_path):
    result = runner.CaptureResult(
        success=True,
        cpu_sample_result=SimpleNamespace(to_dict=lambda: {
            "mean_percent": 2.0,
            "sample_count": 4,
            "sample_window_ms": 30000,
            "ncpu": 8,
            "clk_tck": 100,
            "normalization": "all_cores_total",
            "pgid": 123,
            "extra_root_pid": 456,
            "sample_hz_used": 1.0,
            "exclude_chrome_gpu": True,
            "excluded_gpu_pids": [],
            "per_process_top": [],
        }),
        capture_started_at_epoch=1.0,
        capture_ended_at_epoch=31.0,
    )

    runner._write_capture_meta(tmp_path, "2k", result)

    out = json.loads((tmp_path / "capture_meta.json").read_text())
    assert out["profile"] == "2k"
    assert out["cpu"]["mean_percent"] == 2.0


def test_capture_meta_accepts_already_serialized_cpu_dict(tmp_path):
    result = runner.CaptureResult(
        success=True,
        cpu_sample_result={
            "mean_percent": 3.0,
            "sample_count": 4,
        },
        capture_started_at_epoch=1.0,
        capture_ended_at_epoch=31.0,
    )

    runner._write_capture_meta(tmp_path, "2k", result)

    out = json.loads((tmp_path / "capture_meta.json").read_text())
    assert out["cpu"] == {"mean_percent": 3.0, "sample_count": 4}


def test_capture_strategy_names_are_explicit():
    assert runner.CAPTURE_STRATEGY_PLAYWRIGHT == "playwright"
    assert runner.CAPTURE_STRATEGY_CDP == "cdp"
