"""Tests for scorer.score_fps linear per-profile scoring and audit fields."""

from __future__ import annotations

import pytest

import scorer


EXPECTED_FPS = scorer.EXPECTED_FPS


# 1.1 — expected FPS registry ---------------------------------------------------

def test_expected_fps_defaults_to_20_for_both_profiles():
    assert scorer.EXPECTED_FPS == {"2k": 20.0, "4k": 20.0}


# 1.2 — 2K linear absolute FPS scoring -----------------------------------------

def test_score_fps_2k_linear_absolute_half_fps_gets_half_points():
    assert scorer.score_fps(10.0, EXPECTED_FPS["2k"], "2k") == 2.5


def test_score_fps_2k_linear_absolute_caps_at_full_score():
    assert scorer.score_fps(25.0, EXPECTED_FPS["2k"], "2k") == 5.0


def test_score_fps_2k_linear_absolute_zero_fps_gets_zero_points():
    assert scorer.score_fps(0.0, EXPECTED_FPS["2k"], "2k") == 0.0



def test_score_fps_2k_linear_absolute_fractional_points():
    assert scorer.score_fps(17.0, EXPECTED_FPS["2k"], "2k") == 4.25


# 1.3 — 4K linear absolute FPS scoring -----------------------------------------

def test_score_fps_4k_linear_absolute_four_fps_gets_two_points():
    # 4K FPS is scored out of 10 (full score): 4/20 * 10 = 2.0.
    assert scorer.score_fps(4.0, EXPECTED_FPS["4k"], "4k") == 2.0


def test_score_fps_4k_linear_absolute_caps_at_full_score():
    assert scorer.score_fps(25.0, EXPECTED_FPS["4k"], "4k") == 10.0


def test_score_fps_4k_linear_absolute_fractional_points():
    assert scorer.score_fps(13.4, EXPECTED_FPS["4k"], "4k") == 6.7


# 1.4 — unknown profile fails loudly -------------------------------------------

def test_score_fps_unknown_profile_raises():
    with pytest.raises(KeyError):
        scorer.score_fps(20.0, 25.0, "8k")


# 1.5 — build_score surfaces the audit fields ----------------------------------

def _profile_metrics(measured_fps: float) -> dict:
    return {
        "watermark_recognition_rate": 1.0,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "measured_fps": measured_fps,
    }


def _profile_metrics_with_cpu(measured_fps: float, mean_cpu: float) -> dict:
    block = _profile_metrics(measured_fps)
    block["cpu"] = {
        "mean_percent": mean_cpu,
        "sample_count": 9,
        "sample_window_ms": 9000,
        "ncpu": 8,
        "clk_tck": 100,
        "normalization": "all_cores_total",
        "pgid": 12345,
        "sample_hz_used": 1.0,
    }
    return block


def test_build_score_emits_per_profile_audit_fields():
    out = scorer.build_score(
        {
            "2k": _profile_metrics_with_cpu(EXPECTED_FPS["2k"], 2.0),
            "4k": _profile_metrics(4.0),
        },
        chromium_version="test",
    )
    assert out["2k"]["fps_scoring_mode"] == "linear_absolute"
    assert out["2k"]["fps_linear_full_score"] == 5
    assert "fps_full_threshold_used" not in out["2k"]
    assert "fps_partial_threshold_used" not in out["2k"]
    assert out["2k"]["expected_fps"] == 20.0
    assert out["4k"]["fps_points"] == 2.0
    assert out["4k"]["fps_scoring_mode"] == "linear_absolute"
    assert out["4k"]["fps_linear_full_score"] == 10
    assert "fps_full_threshold_used" not in out["4k"]
    assert "fps_partial_threshold_used" not in out["4k"]
    assert out["4k"]["expected_fps"] == 20.0
