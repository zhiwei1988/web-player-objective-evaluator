"""Tests for scorer.score_fps per-profile thresholds and the audit fields they emit."""

from __future__ import annotations

import pytest

import scorer
from lib.profiles import PROFILES


EXPECTED_FPS = scorer.EXPECTED_FPS


def fps_at(profile: str, ratio: float) -> float:
    """Return a measured fps that yields measured/expected == ratio for the profile."""
    return EXPECTED_FPS[profile] * ratio


# 1.1 — per-profile full-credit cutoff -----------------------------------------

@pytest.mark.parametrize("profile, full_ratio", [("2k", 0.85), ("4k", 0.65)])
def test_score_fps_full_at_boundary(profile, full_ratio):
    assert scorer.score_fps(fps_at(profile, full_ratio), EXPECTED_FPS[profile], profile) == 5


@pytest.mark.parametrize("profile, full_ratio", [("2k", 0.85), ("4k", 0.65)])
def test_score_fps_full_just_above_boundary(profile, full_ratio):
    assert scorer.score_fps(fps_at(profile, full_ratio + 0.01), EXPECTED_FPS[profile], profile) == 5


@pytest.mark.parametrize("profile, full_ratio", [("2k", 0.85), ("4k", 0.65)])
def test_score_fps_just_below_full_falls_to_partial(profile, full_ratio):
    assert scorer.score_fps(fps_at(profile, full_ratio - 0.01), EXPECTED_FPS[profile], profile) == 3


# 1.2 — per-profile partial-credit cutoff --------------------------------------

@pytest.mark.parametrize("profile, partial_ratio", [("2k", 0.50), ("4k", 0.40)])
def test_score_fps_partial_at_boundary(profile, partial_ratio):
    assert scorer.score_fps(fps_at(profile, partial_ratio), EXPECTED_FPS[profile], profile) == 3


@pytest.mark.parametrize("profile, partial_ratio", [("2k", 0.50), ("4k", 0.40)])
def test_score_fps_partial_just_above_boundary(profile, partial_ratio):
    assert scorer.score_fps(fps_at(profile, partial_ratio + 0.01), EXPECTED_FPS[profile], profile) == 3


@pytest.mark.parametrize("profile, partial_ratio", [("2k", 0.50), ("4k", 0.40)])
def test_score_fps_just_below_partial_is_zero(profile, partial_ratio):
    assert scorer.score_fps(fps_at(profile, partial_ratio - 0.01), EXPECTED_FPS[profile], profile) == 0


# 1.3 — unknown profile fails loudly -------------------------------------------

def test_score_fps_unknown_profile_raises():
    with pytest.raises(KeyError):
        scorer.score_fps(20.0, 25.0, "8k")


# 1.4 — dicts cover every profile in PROFILES ----------------------------------

def test_fps_threshold_dicts_cover_all_profiles():
    profiles = set(PROFILES)
    assert set(scorer.FPS_FULL_RATIO_BY_PROFILE) == profiles
    assert set(scorer.FPS_PARTIAL_RATIO_BY_PROFILE) == profiles


# 1.5 — partial strictly below full per profile --------------------------------

@pytest.mark.parametrize("profile", list(PROFILES.keys()))
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
            "2k": _profile_metrics(EXPECTED_FPS["2k"]),
            "4k": _profile_metrics_with_cpu(EXPECTED_FPS["4k"], 2.0),
        },
        chromium_version="test",
    )
    assert out["2k"]["fps_full_threshold_used"] == scorer.FPS_FULL_RATIO_BY_PROFILE["2k"]
    assert out["2k"]["fps_partial_threshold_used"] == scorer.FPS_PARTIAL_RATIO_BY_PROFILE["2k"]
    assert out["4k"]["fps_full_threshold_used"] == scorer.FPS_FULL_RATIO_BY_PROFILE["4k"]
    assert out["4k"]["fps_partial_threshold_used"] == scorer.FPS_PARTIAL_RATIO_BY_PROFILE["4k"]
