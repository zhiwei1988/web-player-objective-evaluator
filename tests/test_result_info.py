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

def _full_2k_block() -> dict:
    return {
        "correctness_points": 5,
        "fps_points": 5,
        "total": 10,
        "measured_fps": 20.0,
        "expected_fps": 20.0,
        "watermark_recognition_rate": 1.0,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "fps_full_threshold_used": 0.85,
        "fps_partial_threshold_used": 0.5,
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
        "fps_linear_full_score": 5,
        "fps_full_threshold_used": None,
        "fps_partial_threshold_used": None,
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
    four_k_fps_points: float = 5,
    cpu_points: int = 10,
    chromium_version: str = "Chromium 131.0.6778.85",
) -> dict:
    return {
        "max_score": 30,
        "objective_total": objective_total,
        "cpu": _cpu_block(points=cpu_points),
        "chromium_version": chromium_version,
        "2k": _full_2k_block(),
        "4k": _full_4k_block(fps_points=four_k_fps_points),
    }


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
    score = _full_score(four_k_fps_points=1.5, objective_total=23.5, cpu_points=7)
    text = result_info.render(score=score, runtime_ms=42, run_dir="/r")
    fields = _parse_fields(text)
    info = fields["info"]

    assert "Objective Score: 23.5 / 30" in info
    assert "Breakdown:" in info
    assert "- 2K Correctness: 5 / 5" in info
    assert "- 2K FPS: 5 / 5" in info
    assert "- 4K Correctness: 5 / 5" in info
    assert "- 4K FPS: 1.5 / 5" in info
    assert "- CPU: 7 / 10" in info


def test_score_field_drops_trailing_zeroes():
    score = _full_score(four_k_fps_points=1.5, objective_total=23.5)
    text = result_info.render(score=score, runtime_ms=1, run_dir="/r")
    fields = _parse_fields(text)
    assert fields["score"] == "23.5"


def test_score_field_renders_integer_without_decimal():
    score = _full_score(four_k_fps_points=5, objective_total=30)
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
    assert "- 2K FPS: 0 / 5" in info
    assert "- 4K Correctness: 0 / 5" in info
    assert "- 4K FPS: 0 / 5" in info
    assert "- CPU: 0 / 10" in info


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
    # And do not leak into info.
    assert "measured_fps" not in fields["info"]
    assert "watermark" not in fields["info"]
    assert "ssim" not in fields["info"]


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
    score = _full_score(four_k_fps_points=1.5, objective_total=23.5, cpu_points=7)
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
    assert fields["score"] == "23.5"
    assert fields["runtime"] == "64231"
    assert "Objective Score: 23.5 / 30" in fields["info"]
    assert "- CPU: 7 / 10" in fields["info"]


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
