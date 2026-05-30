"""Tests for scorer.score_fps per-profile thresholds and the audit fields they emit."""

from __future__ import annotations

import pytest

import scorer
from lib.profiles import PROFILES


EXPECTED_FPS = scorer.EXPECTED_FPS


def fps_at(profile: str, ratio: float) -> float:
    """Return a measured fps that yields measured/expected == ratio for the profile."""
    return EXPECTED_FPS[profile] * ratio


# 1.1 — 2K threshold cutoffs ----------------------------------------------------

def test_expected_fps_defaults_to_20_for_both_profiles():
    assert scorer.EXPECTED_FPS == {"2k": 20.0, "4k": 20.0}


def test_score_fps_2k_full_at_boundary():
    assert scorer.score_fps(fps_at("2k", 0.85), EXPECTED_FPS["2k"], "2k") == 5


def test_score_fps_2k_just_below_full_falls_to_partial():
    assert scorer.score_fps(fps_at("2k", 0.84), EXPECTED_FPS["2k"], "2k") == 3


def test_score_fps_2k_partial_at_boundary():
    assert scorer.score_fps(fps_at("2k", 0.50), EXPECTED_FPS["2k"], "2k") == 3


def test_score_fps_2k_just_below_partial_is_zero():
    assert scorer.score_fps(fps_at("2k", 0.49), EXPECTED_FPS["2k"], "2k") == 0


# 1.2 — 4K linear absolute FPS scoring -----------------------------------------

def test_score_fps_4k_linear_absolute_four_fps_gets_two_points():
    # 4K FPS is scored out of 10 (full score): 4/20 * 10 = 2.0.
    assert scorer.score_fps(4.0, EXPECTED_FPS["4k"], "4k") == 2.0


def test_score_fps_4k_linear_absolute_caps_at_full_score():
    assert scorer.score_fps(25.0, EXPECTED_FPS["4k"], "4k") == 10.0


def test_score_fps_4k_linear_absolute_fractional_points():
    assert scorer.score_fps(13.4, EXPECTED_FPS["4k"], "4k") == 6.7


# 1.3 — unknown profile fails loudly -------------------------------------------

def test_score_fps_unknown_profile_raises():
    with pytest.raises(KeyError):
        scorer.score_fps(20.0, 25.0, "8k")


# 1.4 — threshold dicts only govern threshold-scored profiles ------------------

def test_fps_threshold_dicts_cover_threshold_scored_profiles():
    assert set(scorer.FPS_FULL_RATIO_BY_PROFILE) == {"2k"}
    assert set(scorer.FPS_PARTIAL_RATIO_BY_PROFILE) == {"2k"}
    assert "4k" in PROFILES


# 1.5 — partial strictly below full per profile --------------------------------

@pytest.mark.parametrize("profile", list(scorer.FPS_FULL_RATIO_BY_PROFILE.keys()))
def test_partial_strictly_below_full(profile):
    assert scorer.FPS_PARTIAL_RATIO_BY_PROFILE[profile] < scorer.FPS_FULL_RATIO_BY_PROFILE[profile]


# 1.6 — build_score surfaces the audit fields ----------------------------------

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
    assert out["2k"]["fps_full_threshold_used"] == scorer.FPS_FULL_RATIO_BY_PROFILE["2k"]
    assert out["2k"]["fps_partial_threshold_used"] == scorer.FPS_PARTIAL_RATIO_BY_PROFILE["2k"]
    assert out["2k"]["expected_fps"] == 20.0
    assert out["4k"]["fps_points"] == 2.0
    assert out["4k"]["fps_scoring_mode"] == "linear_absolute"
    assert out["4k"]["fps_linear_full_score"] == 10
    assert out["4k"]["expected_fps"] == 20.0
