from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import runner
from lib import decode_forensics as _forensics


def test_default_capture_viewport_is_contract_size():
    assert runner.CAPTURE_VIEWPORT == {"width": 1280, "height": 720}


# ---- decode-path forensics glue (no browser) ---------------------------------

def test_write_decode_forensics_roundtrip(tmp_path):
    res = runner.CaptureResult(success=True)
    res.decode_forensics = {"verdict": "violation", "checks": {}, "evidence": ["x"], "errors": []}
    runner._write_decode_forensics(tmp_path, res)
    out = json.loads((tmp_path / "decode_forensics.json").read_text())
    assert out["verdict"] == "violation"


def test_write_decode_forensics_noop_when_none(tmp_path):
    res = runner.CaptureResult(success=True)
    runner._write_decode_forensics(tmp_path, res)
    assert not (tmp_path / "decode_forensics.json").exists()


def test_ingest_forensic_report_hevc_bytes_is_ok():
    c = _forensics.ForensicsCollector()
    runner._ingest_forensic_report(
        c, json.dumps({"sink": "appendBuffer", "hex": b"\x00\x00\x00\x01\x40\x01".hex()})
    )
    assert c.result()["verdict"] == "ok"


def test_ingest_forensic_report_codec_string_is_violation():
    c = _forensics.ForensicsCollector()
    runner._ingest_forensic_report(c, json.dumps({"sink": "videodecoder", "codec": "avc1.640028"}))
    assert c.result()["verdict"] == "violation"


def test_ingest_media_props_frames_decoded_triggers_violation():
    c = _forensics.ForensicsCollector()
    runner._ingest_media_props(c, {"codec": None}, {"properties": [{"name": "kFramesDecoded", "value": "7"}]})
    assert c.result()["verdict"] == "violation"


def test_ingest_media_props_video_decoder_name_triggers_violation():
    # The real Check-1 signal on the eval host's Chrome: a <video> decoder was
    # initialized (only happens for a browser-decodable, i.e. non-HEVC, codec).
    c = _forensics.ForensicsCollector()
    runner._ingest_media_props(
        c, {"codec": None}, {"properties": [{"name": "kVideoDecoderName", "value": "FFmpegVideoDecoder"}]}
    )
    res = c.result()
    assert res["verdict"] == "violation"
    assert res["checks"]["video_decoder_codec"] == "FFmpegVideoDecoder"


def test_ingest_media_props_empty_decoder_name_is_not_a_signal():
    c = _forensics.ForensicsCollector()
    runner._ingest_media_props(c, {"codec": None}, {"properties": [{"name": "kVideoDecoderName", "value": ""}]})
    assert c.result()["verdict"] == "inconclusive"


def test_ingest_handles_garbage_without_raising():
    c = _forensics.ForensicsCollector()
    runner._ingest_forensic_report(c, "not json")
    runner._ingest_media_props(c, {"codec": None}, {"bogus": True})
    assert c.result()["verdict"] == "inconclusive"


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


def test_timestamps_include_layout_diagnostics(tmp_path):
    result = runner.CaptureResult(
        success=True,
        target_fps=20.0,
        target_duration_s=30.0,
        capture_strategy="playwright",
        jpeg_quality=90,
        clip={"x": 0, "y": 0, "width": 1280, "height": 720},
    )
    result.layout_diagnostics = {
        "url": "http://localhost:8080/play?profile=2k&autoplay=1",
        "viewport": {"width": 1280, "height": 720},
        "devicePixelRatio": 1,
        "ready": True,
        "error": None,
        "clip": result.clip,
        "host": {"bbox": {"x": 0, "y": 0, "width": 1280, "height": 720}},
        "media": [],
        "warnings": ["no descendant canvas/video elements found"],
    }

    runner._write_timestamps(tmp_path, result)

    out = json.loads((tmp_path / "timestamps.json").read_text())
    assert out["layout_diagnostics"]["viewport"] == {"width": 1280, "height": 720}
    assert out["layout_diagnostics"]["warnings"] == ["no descendant canvas/video elements found"]


def test_layout_warning_for_oversized_canvas_in_clipped_host():
    diagnostics = {
        "ready": True,
        "host": {
            "bbox": {"width": 1280, "height": 720},
            "client": {"width": 1280, "height": 720},
            "scroll": {"width": 2560, "height": 1440},
            "computedStyle": {"overflow": "hidden", "overflowX": "hidden", "overflowY": "hidden"},
        },
        "media": [
            {
                "tag": "canvas",
                "bbox": {"width": 2560, "height": 1440},
                "client": {"width": 2560, "height": 1440},
                "intrinsic": {"width": 2560, "height": 1440},
                "computedStyle": {"transform": "none"},
            }
        ],
        "clip": {"width": 1280, "height": 720},
    }

    warnings = runner._derive_layout_warnings(diagnostics)

    assert any("larger than clipped host" in warning for warning in warnings)


def test_layout_warnings_for_transform_missing_media_and_zero_intrinsic_size():
    diagnostics = {
        "ready": True,
        "host": {
            "bbox": {"width": 1280, "height": 720},
            "computedStyle": {"overflow": "visible", "transform": "scale(2)"},
        },
        "media": [
            {
                "tag": "video",
                "bbox": {"width": 1280, "height": 720},
                "intrinsic": {"width": 0, "height": 0},
                "computedStyle": {"transform": "none"},
            }
        ],
        "clip": {"width": 1280, "height": 720},
    }

    warnings = runner._derive_layout_warnings(diagnostics)
    missing_warnings = runner._derive_layout_warnings({
        "ready": True,
        "host": {"bbox": {"width": 1280, "height": 720}, "computedStyle": {}},
        "media": [],
        "clip": {"width": 1280, "height": 720},
    })

    assert any("transform" in warning for warning in warnings)
    assert any("zero intrinsic" in warning for warning in warnings)
    assert any("no descendant canvas/video" in warning for warning in missing_warnings)


def test_collect_layout_diagnostics_is_bounded_for_many_media_elements():
    raw = {
        "url": "http://localhost:8080/play",
        "viewport": {"width": 1280, "height": 720},
        "devicePixelRatio": 1,
        "ready": True,
        "error": None,
        "host": {"bbox": {"width": 1280, "height": 720}, "computedStyle": {}},
        "media": [
            {"tag": "canvas", "bbox": {"width": 1, "height": 1}, "intrinsic": {"width": 1, "height": 1}}
            for _ in range(runner.MAX_LAYOUT_MEDIA_ELEMENTS + 3)
        ],
    }

    diagnostics = runner._normalize_layout_diagnostics(raw, {"x": 0, "y": 0, "width": 1280, "height": 720})

    assert len(diagnostics["media"]) == runner.MAX_LAYOUT_MEDIA_ELEMENTS
    assert any("truncated media diagnostics" in warning for warning in diagnostics["warnings"])


def test_timestamps_include_contestant_feedback_for_capture_reason(tmp_path):
    result = runner.CaptureResult(
        success=False,
        reason="missing data-testid=player-video",
    )

    runner._write_timestamps(tmp_path, result)

    out = json.loads((tmp_path / "timestamps.json").read_text())
    assert out["contestant_feedback"] == ["missing data-testid=player-video"]


def test_timestamps_include_player_error_feedback_without_full_diagnostics(tmp_path):
    result = runner.CaptureResult(
        success=False,
        reason="startup timeout (__PLAYER_ERROR__=decoder failed)",
    )
    result.browser_errors.append('diagnostic: {"url":"http://localhost:8080/play","error":"decoder failed"}')
    result.browser_errors.append("pageerror: Error: full stack trace")

    runner._write_timestamps(tmp_path, result)

    out = json.loads((tmp_path / "timestamps.json").read_text())
    assert "startup timeout (__PLAYER_ERROR__=decoder failed)" in out["contestant_feedback"]
    assert not any("diagnostic:" in line for line in out["contestant_feedback"])
    assert not any("full stack trace" in line for line in out["contestant_feedback"])


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
