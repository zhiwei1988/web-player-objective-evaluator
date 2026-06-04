"""Tests for the result.info renderer.

The renderer takes a score dict (as written by scorer.py to score.json) plus
runtime/run metadata and returns the line-oriented |result|/|score|/|runtime|/
|info|/|debug| string consumed by the contest platform.

Format spec: openspec/specs/evaluator/spec.md (Contest Platform Result Info).
Sample:      reference/result-sample.info.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import result_info


ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _full_2k_block(fps_points: float = 5) -> dict:
    return {
        "correctness_points": 5,
        "fps_points": fps_points,
        "total": 5 + fps_points,
        "measured_fps": 20.0,
        "expected_fps": 20.0,
        "watermark_recognition_rate": 1.0,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "fps_scoring_mode": "linear_absolute",
        "fps_linear_full_score": 5,
    }


def _full_4k_block(fps_points: float = 5) -> dict:
    return {
        "correctness_points": 5,
        "fps_points": fps_points,
        "total": 5 + fps_points,
        "measured_fps": 20.0,
        "expected_fps": 20.0,
        "watermark_recognition_rate": 1.0,
        "color_check_rate": 1.0,
        "mean_ssim": 0.93,
        "fps_scoring_mode": "linear_absolute",
        "fps_linear_full_score": 10,
    }


def _cpu_block(points: int = 10, gate_reason: str | None = None) -> dict:
    return {
        "points": points,
        "mean_percent": 3.2,
        "sample_count": 27,
        "sample_window_ms": 27000,
        "ncpu": 8,
        "normalization": "all_cores_total",
        "measured_on_profile": "2k",
        "gate_profile": "2k",
        "expected_fps": 20.0,
        "measured_fps": 20.0,
        "thresholds_used": {
            "gate_fps_ratio": 0.65,
            "full_percent": 5.0,
            "partial_start_percent": 6.0,
            "zero_percent": 20.0,
            "min_samples": 3,
            "sample_hz": 1.0,
        },
        "gated": gate_reason is not None,
        "gate_reason": gate_reason,
    }


def _full_score(
    *,
    objective_total: float = 30,
    two_k_fps_points: float = 5,
    four_k_fps_points: float = 5,
    cpu_points: int = 10,
    chromium_version: str = "Chromium 131.0.6778.85",
) -> dict:
    return {
        "max_score": 30,
        "objective_total": objective_total,
        "cpu": _cpu_block(points=cpu_points),
        "chromium_version": chromium_version,
        "2k": _full_2k_block(fps_points=two_k_fps_points),
        "4k": _full_4k_block(fps_points=four_k_fps_points),
    }


def _with_decode_path(block: dict, verdict: str, *, checks: dict | None = None,
                      evidence: list[str] | None = None) -> dict:
    block = dict(block)
    block["decode_path"] = {
        "verdict": verdict,
        "checks": checks or {},
        "evidence": evidence or [],
    }
    return block


def _failure_score(reason: str, *, chromium_version: str = "Chromium 131.0.6778.85") -> dict:
    """Mirror what scorer.build_score emits for a write_failure_score path."""
    return {
        "max_score": 30,
        "objective_total": 0,
        "cpu": _cpu_block(points=0, gate_reason="host_failure"),
        "chromium_version": chromium_version,
        "2k": None,
        "4k": None,
        "reason": reason,
    }


def _parse_fields(text: str) -> dict[str, str]:
    """Parse the line-oriented |key|value protocol into a dict.

    For multi-line fields (info, debug) the value is the concatenation of
    everything from the marker line until the next `|...|` marker or EOF.
    """
    fields: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    for line in text.split("\n"):
        if line.startswith("|") and line[1:].find("|") > 0:
            if current_key is not None:
                fields[current_key] = "\n".join(current_lines)
            head, _, rest = line[1:].partition("|")
            current_key = head
            current_lines = [rest]
        else:
            current_lines.append(line)
    if current_key is not None:
        fields[current_key] = "\n".join(current_lines)
    return fields


def _write_capture_statuses(
    run_dir: Path,
    *,
    two_k: dict | None = None,
    four_k: dict | None = None,
) -> None:
    statuses = {
        "2k": two_k or {"profile": "2k", "phase": "completed", "detail": {}},
        "4k": four_k or {"profile": "4k", "phase": "completed", "detail": {}},
    }
    for profile, status in statuses.items():
        status_dir = run_dir / f"{profile}_screenshots"
        status_dir.mkdir(parents=True, exist_ok=True)
        (status_dir / "capture_status.json").write_text(json.dumps(status))


# ---------------------------------------------------------------------------
# 1.1 Format (field order, |info| alone, multi-line info, |debug| last, no
#     explanatory comments from reference/result-sample.info)
# ---------------------------------------------------------------------------

def test_format_field_order():
    score = _full_score()
    text = result_info.render(score=score, runtime_ms=64231, run_dir="/results/team_ref_X")
    markers = [
        ln.split("|")[1]
        for ln in text.split("\n")
        if ln.startswith("|") and ln[1:].find("|") > 0
    ]
    assert markers == ["result", "score", "runtime", "info", "debug"]


def test_format_info_marker_is_alone_on_its_line():
    score = _full_score()
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r")
    info_lines = [ln for ln in text.split("\n") if ln.startswith("|info|")]
    assert info_lines == ["|info|"], "the |info| marker must appear alone on its line"


def test_format_info_is_multi_line_until_debug_marker():
    score = _full_score(four_k_fps_points=1.5, objective_total=21.5, cpu_points=5)
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r")
    lines = text.split("\n")
    info_idx = lines.index("|info|")
    debug_idx = next(i for i, ln in enumerate(lines) if ln.startswith("|debug|"))
    body = lines[info_idx + 1 : debug_idx]
    assert body[0].startswith("Objective Score:")
    assert any(ln.startswith("Breakdown:") for ln in body)
    assert any(ln.startswith("- ") for ln in body)
    # The block separates info from debug only via the |debug| marker.
    assert debug_idx > info_idx + 1


def test_format_debug_is_last_field():
    score = _full_score()
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r")
    lines = [ln for ln in text.split("\n") if ln.startswith("|") and ln[1:].find("|") > 0]
    assert lines[-1].startswith("|debug|")


def test_format_omits_reference_sample_explanatory_comments():
    """The trailing '---' comment block from reference/result-sample.info MUST
    NOT leak into our output."""
    score = _full_score()
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r")
    assert "---------------------" not in text
    assert "字段说明" not in text
    assert "出题者看的运行信息" not in text


def test_format_ends_with_newline():
    score = _full_score()
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r")
    assert text.endswith("\n")


# ---------------------------------------------------------------------------
# 1.2 Normal scoring info renders all five contestant-visible items
# ---------------------------------------------------------------------------

def test_info_lists_all_five_scoring_items_normal_run():
    score = _full_score(
        two_k_fps_points=4.25,
        four_k_fps_points=3.0,
        objective_total=19.25,
        cpu_points=3,
    )
    text = result_info.render(score=score, runtime_ms=42, run_dir="/r")
    fields = _parse_fields(text)
    info = fields["info"]

    assert "Objective Score: 19.25 / 30" in info
    assert "Breakdown:" in info
    assert "- 2K Correctness: 5 / 5" in info
    assert "- 2K FPS: 4.25 / 5" in info
    assert "- 4K Correctness: 5 / 5" in info
    assert "- 4K FPS: 3.00 / 10" in info
    assert "- CPU: 3.00 / 5" in info


def test_info_formats_cpu_scoring_item_with_two_decimal_places():
    score = _full_score(four_k_fps_points=1.5, objective_total=19.25, cpu_points=3)
    score["cpu"]["points"] = 3.25
    text = result_info.render(score=score, runtime_ms=42, run_dir="/r")
    fields = _parse_fields(text)

    assert "- CPU: 3.25 / 5" in fields["info"]


def test_info_appends_execution_feedback_after_scoring_items():
    score = _failure_score("contestant_frontend_unavailable")
    score["contestant_feedback"] = [
        "Frontend did not become reachable.",
        "npm ERR! missing script: start",
    ]
    text = result_info.render(score=score, runtime_ms=42, run_dir="/r", result_code=0)
    fields = _parse_fields(text)
    info = fields["info"]

    assert "- CPU: 0.00 / 5" in info
    assert "Runtime Metrics:" in info
    assert "Execution Feedback:" in info
    assert info.index("Runtime Metrics:") < info.index("Execution Feedback:")
    assert "- Frontend did not become reachable." in info
    assert "- npm ERR! missing script: start" in info


def test_info_appends_rendered_snapshot_links_when_present():
    score = _full_score()
    score["rendered_snapshots"] = [
        {
            "profile": "2k",
            "label": "2K",
            "filename": "2k-rendered.jpg",
            "url": "http://10.0.0.8:8090/2079591/2k-rendered.jpg",
        },
        {
            "profile": "4k",
            "label": "4K",
            "filename": "4k-rendered.jpg",
            "url": "http://10.0.0.8:8090/2079591/4k-rendered.jpg",
        },
    ]

    text = result_info.render(score=score, runtime_ms=42, run_dir="/r")
    fields = _parse_fields(text)
    info = fields["info"]

    assert "Rendered Snapshots:" in info
    assert "- 2K: http://10.0.0.8:8090/2079591/2k-rendered.jpg" in info
    assert "- 4K: http://10.0.0.8:8090/2079591/4k-rendered.jpg" in info
    assert info.index("Runtime Metrics:") < info.index("Rendered Snapshots:")


def test_info_omits_rendered_snapshots_when_metadata_missing():
    score = _full_score(four_k_fps_points=1.5, objective_total=19.5, cpu_points=3)
    score["contestant_feedback"] = ["Frontend did not become reachable."]

    text = result_info.render(score=score, runtime_ms=42, run_dir="/r")
    fields = _parse_fields(text)
    info = fields["info"]

    assert "Rendered Snapshots:" not in info
    assert "Objective Score: 19.5 / 30" in info
    assert "- CPU: 3.00 / 5" in info
    assert "Runtime Metrics:" in info
    assert "2k: measured_fps=20" in info
    assert "cpu: mean_percent=3.2" in info
    assert "Execution Feedback:" in info
    assert "- Frontend did not become reachable." in info


@pytest.mark.parametrize("feedback", [None, [], "", ["", "   "]])
def test_info_omits_execution_feedback_when_absent_or_empty(feedback):
    score = _full_score()
    if feedback is not None:
        score["contestant_feedback"] = feedback
    text = result_info.render(score=score, runtime_ms=42, run_dir="/r")
    fields = _parse_fields(text)
    assert "Execution Feedback:" not in fields["info"]


def test_info_always_emits_capture_status_for_successful_profiles(tmp_path):
    run_dir = tmp_path / "run"
    _write_capture_statuses(run_dir)

    text = result_info.render(score=_full_score(), runtime_ms=42, run_dir=run_dir)
    info = _parse_fields(text)["info"]

    assert "Capture Status:" in info
    assert info.splitlines()[info.splitlines().index("Capture Status:") + 1 :][:2] == [
        "- 2K: completed",
        "- 4K: completed",
    ]


def test_info_renders_capture_status_detail_as_deterministic_json(tmp_path):
    run_dir = tmp_path / "run"
    _write_capture_statuses(
        run_dir,
        four_k={
            "profile": "4k",
            "phase": "navigation_timeout",
            "detail": {
                "url": "http://127.0.0.1:8080/play",
                "timeout_s": 120,
            },
        },
    )

    text = result_info.render(score=_full_score(), runtime_ms=42, run_dir=run_dir)
    info = _parse_fields(text)["info"]

    assert '- 4K: navigation_timeout {"timeout_s": 120, "url": "http://127.0.0.1:8080/play"}' in info


def test_info_renders_capture_status_internal_phase_and_detail_unsanitized(tmp_path):
    run_dir = tmp_path / "run"
    _write_capture_statuses(
        run_dir,
        two_k={
            "profile": "2k",
            "phase": "forensics_installed",
            "detail": {
                "chromium_version": "Chromium 131.0.6778.85",
                "selector": "#player",
                "clip": {"x": 12, "y": 34, "width": 3840, "height": 2160},
            },
        },
    )

    text = result_info.render(score=_full_score(), runtime_ms=42, run_dir=run_dir)
    info = _parse_fields(text)["info"]

    assert "forensics_installed" in info
    assert "Chromium 131.0.6778.85" in info
    assert '"selector": "#player"' in info
    assert '"clip": {"height": 2160, "width": 3840, "x": 12, "y": 34}' in info


def test_info_renders_missing_capture_status_as_not_run(tmp_path):
    run_dir = tmp_path / "run"
    _write_capture_statuses(
        run_dir,
        two_k={"profile": "2k", "phase": "completed", "detail": {}},
    )
    (run_dir / "4k_screenshots" / "capture_status.json").unlink()

    text = result_info.render(score=_full_score(), runtime_ms=42, run_dir=run_dir)
    info = _parse_fields(text)["info"]

    assert "- 2K: completed" in info
    assert "- 4K: not run" in info

    text_without_run_dir = result_info.render(score=_full_score(), runtime_ms=42, run_dir=None)
    info_without_run_dir = _parse_fields(text_without_run_dir)["info"]
    assert "- 2K: not run" in info_without_run_dir
    assert "- 4K: not run" in info_without_run_dir


def test_capture_status_block_is_inside_info_field(tmp_path):
    run_dir = tmp_path / "run"
    _write_capture_statuses(run_dir)

    text = result_info.render(score=_full_score(), runtime_ms=42, run_dir=run_dir)

    assert text.index("Capture Status:") < text.index("|debug|")
    assert "Capture Status:" in _parse_fields(text)["info"]


# ---------------------------------------------------------------------------
# 1.3 Decode-path violation feedback
# ---------------------------------------------------------------------------

def test_info_surfaces_2k_decode_path_violation():
    score = _full_score(objective_total=0, cpu_points=0)
    score["2k"] = _with_decode_path(
        score["2k"],
        "violation",
        checks={
            "video_decoder_active": True,
            "video_decoder_codec": "FFmpegVideoDecoder",
            "sink_codecs": ["avc", "hevc"],
        },
        evidence=[
            "sink:websocket:hevc",
            "sink:appendBuffer:avc",
            "video_decoder:FFmpegVideoDecoder:frames=0:present=True",
        ],
    )

    text = result_info.render(score=score, runtime_ms=42, run_dir="/r")
    info = _parse_fields(text)["info"]

    assert "Decode Path Violations:" in info
    assert "- 2K: browser decode path received H.264/AVC instead of H.265" in info


def test_info_surfaces_each_decode_path_violation_once():
    score = _full_score(objective_total=0, cpu_points=0)
    score["2k"] = _with_decode_path(
        score["2k"],
        "violation",
        checks={"sink_codecs": ["avc"]},
        evidence=["sink:appendBuffer:avc"],
    )
    score["4k"] = _with_decode_path(
        score["4k"],
        "violation",
        checks={"video_decoder_active": True, "sink_codecs": []},
        evidence=["video_decoder:FFmpegVideoDecoder:frames=0:present=True"],
    )

    text = result_info.render(score=score, runtime_ms=42, run_dir="/r")
    info = _parse_fields(text)["info"]
    info_lines = info.splitlines()
    start = info_lines.index("Decode Path Violations:") + 1
    end = info_lines.index("", start)
    violation_lines = info_lines[start:end]

    assert "Decode Path Violations:" in info
    assert violation_lines == [
        "- 2K: browser decode path received H.264/AVC instead of H.265",
        "- 4K: browser video decoder was active on an HEVC-incapable host",
    ]


def test_info_omits_decode_path_section_for_non_violations():
    score = _full_score()
    score["2k"] = _with_decode_path(score["2k"], "ok", checks={"sink_codecs": ["hevc"]})
    score["4k"] = _with_decode_path(score["4k"], "inconclusive", checks={})

    text = result_info.render(score=score, runtime_ms=42, run_dir="/r")
    info = _parse_fields(text)["info"]

    assert "Decode Path Violations:" not in info


def test_decode_path_violation_info_is_sanitized():
    score = _full_score(chromium_version="Chromium 131.0.6778.85", objective_total=0, cpu_points=0)
    score["2k"] = _with_decode_path(
        score["2k"],
        "violation",
        checks={
            "video_decoder_active": True,
            "video_decoder_codec": "FFmpegVideoDecoder",
            "sink_codecs": ["jpeg"],
            "run_dir": "/root/workspace/web-player-objective-evaluator/results/team_x",
        },
        evidence=[
            "sink:appendBuffer:jpeg",
            "video_decoder:FFmpegVideoDecoder:frames=0:present=True",
            "/root/workspace/web-player-objective-evaluator/results/team_x",
        ],
    )

    text = result_info.render(score=score, runtime_ms=42, run_dir="/r")
    info = _parse_fields(text)["info"]

    assert "Decode Path Violations:" in info
    assert "- 2K:" in info
    assert "sink:appendBuffer" not in info
    assert "FFmpegVideoDecoder" not in info
    assert "Chromium 131.0.6778.85" not in info
    assert "/root/workspace" not in info
    assert "watermark" not in info
    assert "ssim" not in info
    assert "gate_fps_ratio" not in info


def test_execution_feedback_does_not_copy_internal_debug_fields_into_info():
    score = _failure_score("contestant_frontend_unavailable")
    score["contestant_feedback"] = ["Submission frontend did not become reachable."]
    text = result_info.render(
        score=score,
        runtime_ms=42,
        run_dir="/results/team_ref_20260522",
        result_code=0,
    )
    fields = _parse_fields(text)

    info = fields["info"]
    assert "Submission frontend did not become reachable." in info
    assert "/results/team_ref_20260522" not in info
    assert "Chromium 131.0.6778.85" not in info
    assert "gate_fps_ratio" not in info
    assert "contestant_frontend_unavailable" not in info


def test_score_field_drops_trailing_zeroes():
    score = _full_score(four_k_fps_points=1.5, objective_total=19.5)
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r")
    fields = _parse_fields(text)
    assert fields["score"] == "19.5"


def test_score_field_renders_integer_without_decimal():
    score = _full_score(four_k_fps_points=10, objective_total=30)
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r")
    fields = _parse_fields(text)
    assert fields["score"] == "30"


def test_runtime_is_integer_milliseconds():
    score = _full_score()
    text = result_info.render(score=score, runtime_ms=64231, run_dir="/r")
    fields = _parse_fields(text)
    assert fields["runtime"] == "64231"


def test_default_result_code_is_zero():
    score = _full_score()
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r")
    fields = _parse_fields(text)
    assert fields["result"] == "0"


# ---------------------------------------------------------------------------
# 1.3 Contestant-side zero-score failures
# ---------------------------------------------------------------------------

def test_contestant_failure_renders_zero_items_and_keeps_result_zero():
    score = _failure_score("contestant_frontend_unavailable")
    text = result_info.render(
        score=score,
        runtime_ms=12345,
        run_dir="/results/team_ref_20260522",
        result_code=0,
    )
    fields = _parse_fields(text)

    assert fields["result"] == "0"
    assert fields["score"] == "0"

    info = fields["info"]
    assert "Objective Score: 0 / 30" in info
    assert "- 2K Correctness: 0 / 5" in info
    assert "- 2K FPS: 0.00 / 5" in info
    assert "- 4K Correctness: 0 / 5" in info
    assert "- 4K FPS: 0.00 / 10" in info
    assert "- CPU: 0.00 / 5" in info


def test_contestant_failure_keeps_raw_reason_out_of_info_block():
    score = _failure_score("contestant_frontend_unavailable")
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r", result_code=0)
    fields = _parse_fields(text)
    assert "contestant_frontend_unavailable" not in fields["info"]
    assert "frontend" not in fields["info"].lower()


def test_contestant_failure_surfaces_raw_reason_in_debug_block():
    score = _failure_score("contestant_frontend_unavailable")
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r", result_code=0)
    fields = _parse_fields(text)
    assert "contestant_frontend_unavailable" in fields["debug"]


def test_memory_limit_failure_is_contestant_side_and_keeps_limit_out_of_info():
    score = _failure_score("contestant_memory_limit_exceeded")
    score["contestant_memory_limit"] = "10G"
    score["contestant_feedback"] = ["Submission exceeded evaluator memory limit of 10G."]

    assert result_info.classify_result_code(score["reason"]) == 0
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r", result_code=0)
    fields = _parse_fields(text)

    assert fields["result"] == "0"
    assert "Submission exceeded evaluator memory limit of 10G." in fields["info"]
    assert "contestant_memory_limit_exceeded" not in fields["info"]
    assert "contestant_memory_limit=10G" in fields["debug"]
    assert "contestant_memory_limit_exceeded" in fields["debug"]


# ---------------------------------------------------------------------------
# 1.4 Infrastructure/evaluator failures
# ---------------------------------------------------------------------------

def test_infrastructure_failure_marks_result_one():
    score = _failure_score("rtsp infrastructure failure (2k unreadable)")
    text = result_info.render(
        score=score,
        runtime_ms=1500,
        run_dir="/results/team_ref_20260522",
        result_code=1,
    )
    fields = _parse_fields(text)
    assert fields["result"] == "1"
    assert fields["score"] == "0"
    assert "rtsp infrastructure failure (2k unreadable)" in fields["debug"]


def test_infrastructure_failure_preserves_prior_score_when_provided():
    """If a trustworthy score block was already produced before the infra
    failure, render preserves objective_total rather than blanking it.
    The contract is that score follows objective_total verbatim."""
    score = _full_score(four_k_fps_points=4, objective_total=24, cpu_points=10)
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r", result_code=1)
    fields = _parse_fields(text)
    assert fields["result"] == "1"
    assert fields["score"] == "24"


def test_debug_includes_run_directory_and_chromium_version_when_present():
    score = _full_score(chromium_version="Chromium 131.0.6778.85")
    text = result_info.render(
        score=score,
        runtime_ms=1,
        run_dir="/results/team_ref_20260522_120000",
    )
    fields = _parse_fields(text)
    assert "/results/team_ref_20260522_120000" in fields["debug"]
    assert "Chromium 131.0.6778.85" in fields["debug"]


def test_debug_includes_per_profile_diagnostics_when_present():
    score = _full_score(four_k_fps_points=4, objective_total=29)
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r")
    fields = _parse_fields(text)
    # Per-profile diagnostics go in debug, never in info.
    assert "measured_fps" in fields["debug"]
    assert "watermark" in fields["debug"]
    # High-level runtime metrics are intentionally contestant-facing; detailed
    # recognition diagnostics stay in debug.
    assert "measured_fps" in fields["info"]
    assert "watermark" not in fields["info"]
    assert "ssim" not in fields["info"]


def test_result_info_does_not_dump_internal_layout_diagnostics():
    score = _full_score()
    score["2k"]["reason"] = "capture timeout after 120s"
    score["2k"]["layout_diagnostics"] = {
        "warnings": ["descendant canvas is larger than clipped host"],
        "host": {"computedStyle": {"overflow": "hidden"}},
    }

    text = result_info.render(score=score, runtime_ms=1, run_dir="/r")
    fields = _parse_fields(text)

    assert "capture timeout after 120s" in fields["debug"]
    assert "layout_diagnostics" not in fields["debug"]
    assert "computedStyle" not in fields["debug"]
    assert "descendant canvas is larger than clipped host" not in fields["info"]


def test_debug_includes_cpu_gate_diagnostics_when_gated():
    score = _full_score(cpu_points=0)
    score["cpu"]["gate_reason"] = "2k_fps_below_threshold"
    score["cpu"]["gated"] = True
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r")
    fields = _parse_fields(text)
    assert "2k_fps_below_threshold" in fields["debug"]
    # Not contestant-facing.
    assert "2k_fps_below_threshold" not in fields["info"]


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

def test_cli_writes_result_info(tmp_path):
    score = _full_score(four_k_fps_points=1.5, objective_total=19.5, cpu_points=3)
    score_path = tmp_path / "score.json"
    score_path.write_text(json.dumps(score))
    out_path = tmp_path / "result.info"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "result_info.py"),
            "--score-json", str(score_path),
            "--runtime-ms", "64231",
            "--run-dir", str(tmp_path / "results" / "team_ref_X"),
            "--output", str(out_path),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    content = out_path.read_text()
    fields = _parse_fields(content)
    assert fields["result"] == "0"
    assert fields["score"] == "19.5"
    assert fields["runtime"] == "64231"
    assert "Objective Score: 19.5 / 30" in fields["info"]
    assert "- CPU: 3.00 / 5" in fields["info"]


def test_cli_respects_explicit_result_code_one(tmp_path):
    score = _failure_score("rtsp infrastructure failure")
    score_path = tmp_path / "score.json"
    score_path.write_text(json.dumps(score))
    out_path = tmp_path / "result.info"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "result_info.py"),
            "--score-json", str(score_path),
            "--runtime-ms", "1500",
            "--run-dir", str(tmp_path / "results" / "team_ref_X"),
            "--output", str(out_path),
            "--result-code", "1",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    fields = _parse_fields(out_path.read_text())
    assert fields["result"] == "1"
    assert "rtsp infrastructure failure" in fields["debug"]
