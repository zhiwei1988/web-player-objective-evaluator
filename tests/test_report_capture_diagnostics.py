from __future__ import annotations

import json

import report


def _profile_score(measured_fps: float = 16.0) -> dict:
    return {
        "correctness_points": 5,
        "fps_points": 5,
        "total": 10,
        "measured_fps": measured_fps,
        "expected_fps": 20.0,
        "watermark_recognition_rate": 1.0,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "fps_scoring_mode": "linear_absolute",
        "fps_linear_full_score": 5,
    }


def _metrics() -> dict:
    return {
        "ssim_scores": [0.95, 0.96],
        "frame_numbers": [1, 2],
        "per_shot": [],
        "capture_sampling_fps": 19.2,
        "capture_span_overrun_ratio": 1.3,
        "repeat_frame_rate": 0.1,
        "dropped_or_skipped_frame_rate": 0.2,
        "frame_progress_fps": 24.0,
    }


def test_report_renders_capture_diagnostics_and_local_links(tmp_path):
    score = {
        "objective_total": 30,
        "max_score": 30,
        "chromium_version": "test",
        "2k": _profile_score(),
        "4k": _profile_score(14.0),
        "cpu": {
            "points": 5,
            "gated": False,
            "gate_reason": None,
            "measured_on_profile": "4k",
            "gate_profile": "4k",
            "expected_fps": 20.0,
            "measured_fps": 18.0,
            "thresholds_used": {},
        },
    }
    (tmp_path / "score.json").write_text(json.dumps(score))
    out = tmp_path / "report.html"

    report.render_report(
        score=score,
        profile_metrics={"2k": _metrics(), "4k": _metrics()},
        output=out,
        run_dir=tmp_path,
    )

    html = out.read_text()
    assert "capture sampling fps" in html
    assert "capture overrun" in html
    assert "repeat frame rate" in html
    assert "skipped frame rate" in html
    assert "frame progress fps" in html
    assert "2k_metrics.json" in html
    assert "4k_screenshots/" in html
    assert "measured on profile" in html
    assert "<td>4k</td>" in html  # CPU sub-score is now sampled on the 4K round
    assert "linear" in html
    assert 'src="http://' not in html
    assert 'src="https://' not in html
    assert 'href="http://' not in html
    assert 'href="https://' not in html


def test_report_renders_stage_timeout_and_layout_warnings(tmp_path):
    score = {
        "objective_total": 0,
        "max_score": 30,
        "chromium_version": "test",
        "2k": {"reason": "capture timeout after 120s"},
        "4k": _profile_score(14.0),
        "cpu": {"points": 0, "gated": True, "gate_reason": "2k_round_failed"},
    }
    (tmp_path / "stage_timings.json").write_text(json.dumps({
        "budgets": {"capture_timeout_seconds": 120},
        "stages": [
            {
                "stage": "capture",
                "profile": "2k",
                "status": "timeout",
                "timeout_seconds": 120,
                "reason": "capture timeout after 120s",
            }
        ],
    }))
    shots = tmp_path / "4k_screenshots"
    shots.mkdir()
    (shots / "timestamps.json").write_text(json.dumps({
        "layout_diagnostics": {
            "warnings": ["descendant canvas is larger than clipped host"]
        }
    }))
    out = tmp_path / "report.html"

    report.render_report(
        score=score,
        profile_metrics={"2k": None, "4k": _metrics()},
        output=out,
        run_dir=tmp_path,
    )

    html = out.read_text()
    assert "Stage diagnostics" in html
    assert "capture timeout after 120s" in html
    assert "Layout diagnostics" in html
    assert "descendant canvas is larger than clipped host" in html
    assert "4k_screenshots/timestamps.json" in html


def test_report_shows_effective_bandwidth_limit(tmp_path):
    score = {
        "objective_total": 30,
        "max_score": 30,
        "chromium_version": "test",
        "2k": _profile_score(),
        "4k": _profile_score(14.0),
        "cpu": {"points": 5, "gated": False, "gate_reason": None,
                "measured_on_profile": "4k", "gate_profile": "4k"},
    }
    (tmp_path / "score.json").write_text(json.dumps(score))
    (tmp_path / "stage_timings.json").write_text(json.dumps({
        "budgets": {
            "capture_timeout_seconds": 120,
            "contestant_memory_max": "10G",
            "contestant_bandwidth_max": "100mbit",
        },
        "stages": [],
    }))
    out = tmp_path / "report.html"
    report.render_report(
        score=score,
        profile_metrics={"2k": _metrics(), "4k": _metrics()},
        output=out,
        run_dir=tmp_path,
    )
    html = out.read_text()
    assert "contestant bandwidth limit" in html
    assert "100mbit" in html


def test_report_reads_standalone_layout_diagnostics_when_timestamps_missing(tmp_path):
    score = {
        "objective_total": 0,
        "max_score": 30,
        "chromium_version": "test",
        "2k": {"reason": "capture interrupted"},
        "4k": None,
        "cpu": {"points": 0, "gated": True, "gate_reason": "2k_round_failed"},
    }
    shots = tmp_path / "2k_screenshots"
    shots.mkdir()
    (shots / "layout_diagnostics.json").write_text(json.dumps({
        "warnings": ["canvas element is larger than clipped host"],
    }))
    out = tmp_path / "report.html"

    report.render_report(
        score=score,
        profile_metrics={"2k": None, "4k": None},
        output=out,
        run_dir=tmp_path,
    )

    html = out.read_text()
    assert "canvas element is larger than clipped host" in html
    assert "2k_screenshots/layout_diagnostics.json" in html


def test_report_renders_capture_status(tmp_path):
    score = {
        "objective_total": 0,
        "max_score": 30,
        "chromium_version": "test",
        "2k": {"reason": "capture timeout after 120s"},
        "4k": None,
        "cpu": {"points": 0, "gated": True, "gate_reason": "2k_round_failed"},
    }
    shots = tmp_path / "2k_screenshots"
    shots.mkdir()
    (shots / "capture_status.json").write_text(json.dumps({
        "profile": "2k",
        "phase": "navigating",
        "detail": {"url": "http://localhost:8080/play?profile=2k&autoplay=1"},
    }))
    out = tmp_path / "report.html"

    report.render_report(
        score=score,
        profile_metrics={"2k": None, "4k": None},
        output=out,
        run_dir=tmp_path,
    )

    html = out.read_text()
    assert "Capture status" in html
    assert "navigating" in html
    assert "2k_screenshots/capture_status.json" in html


def test_report_renders_contestant_memory_limit_metadata(tmp_path):
    score = {
        "objective_total": 0,
        "max_score": 30,
        "chromium_version": "test",
        "reason": "contestant_memory_limit_exceeded",
        "contestant_memory_limit": "10G",
        "2k": None,
        "4k": None,
        "cpu": {"points": 0, "gated": True, "gate_reason": "host_failure"},
    }
    (tmp_path / "stage_timings.json").write_text(json.dumps({
        "budgets": {"contestant_memory_max": "10G"},
        "stages": [],
    }))
    out = tmp_path / "report.html"

    report.render_report(
        score=score,
        profile_metrics={"2k": None, "4k": None},
        output=out,
        run_dir=tmp_path,
    )

    html = out.read_text()
    assert "contestant memory limit" in html
    assert "10G" in html
    assert "contestant_memory_limit_exceeded" in html


def test_report_renders_unfinished_running_stage(tmp_path):
    score = {
        "objective_total": 0,
        "max_score": 30,
        "chromium_version": "test",
        "2k": {"reason": "capture interrupted"},
        "4k": None,
        "cpu": {"points": 0, "gated": True, "gate_reason": "2k_round_failed"},
    }
    (tmp_path / "stage_timings.json").write_text(json.dumps({
        "budgets": {"capture_timeout_seconds": 120},
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
    out = tmp_path / "report.html"

    report.render_report(
        score=score,
        profile_metrics={"2k": None, "4k": None},
        output=out,
        run_dir=tmp_path,
    )

    html = out.read_text()
    assert "capture[2k]" in html
    assert '<span class="warn">running</span>' in html
    assert "stage started; no final record" in html
