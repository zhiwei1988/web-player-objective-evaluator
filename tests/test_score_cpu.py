"""Table-driven tests for scorer.score_cpu and the cpu block in build_score."""

from __future__ import annotations

import pytest

import scorer


EXPECTED_4K = scorer.EXPECTED_FPS["4k"]


# Helpers --------------------------------------------------------------------

def fps_at(ratio: float) -> float:
    """Return a measured 4K fps that yields measured/expected == ratio."""
    return EXPECTED_4K * ratio


# Score table covers the published mapping. -----------------------------------

@pytest.mark.parametrize(
    "mean_cpu, expected_points",
    [
        (0.0, 10),
        (4.99, 10),
        (5.0, 10),
        (5.5, 10),
        (6.0, 10),
        (7.0, 9),
        (8.0, 9),
        (9.0, 8),
        (10.0, 7),
        (11.0, 6),
        (12.0, 6),
        (13.0, 5),
        (14.0, 4),
        (15.0, 4),
        (16.0, 3),
        (17.0, 2),
        (18.0, 1),
        (19.0, 1),
        (20.0, 0),
        (20.001, 0),
        (99.0, 0),
    ],
)
def test_score_cpu_table(mean_cpu, expected_points):
    points, reason = scorer.score_cpu(
        mean_cpu_percent=mean_cpu,
        measured_4k_fps=fps_at(0.9),
    )
    assert points == expected_points
    assert reason is None


def test_score_cpu_gates_when_fps_below_threshold():
    points, reason = scorer.score_cpu(
        mean_cpu_percent=2.0,
        measured_4k_fps=fps_at(0.24),
        gate_fps_ratio=0.25,
    )
    assert points == 0
    assert reason == "4k_fps_below_threshold"


def test_score_cpu_gates_when_sampler_missing():
    points, reason = scorer.score_cpu(
        mean_cpu_percent=None,
        measured_4k_fps=fps_at(0.9),
    )
    assert points == 0
    assert reason == "sampler_no_data"


def test_score_cpu_gate_takes_precedence_over_value():
    points, reason = scorer.score_cpu(
        mean_cpu_percent=0.0,
        measured_4k_fps=fps_at(0.10),
        gate_fps_ratio=0.25,
    )
    assert points == 0
    assert reason == "4k_fps_below_threshold"


# build_score wiring ---------------------------------------------------------

def _profile_full() -> dict:
    return {
        "watermark_recognition_rate": 1.0,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "measured_fps": 25.0,
    }


def _4k_full_with_cpu(mean_cpu: float | None) -> dict:
    block = _profile_full()
    if mean_cpu is not None:
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


def test_build_score_max_score_is_30():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _4k_full_with_cpu(2.0)},
        chromium_version="test",
    )
    assert out["max_score"] == 30


def test_build_score_objective_total_includes_cpu():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _4k_full_with_cpu(2.0)},
        chromium_version="test",
    )
    # 5 correctness + 5 fps per profile = 10; plus 10 CPU = 30
    assert out["objective_total"] == 10 + 10 + 10
    assert out["2k"]["total"] == 10
    assert out["4k"]["total"] == 10
    assert out["cpu"]["points"] == 10
    assert out["cpu"]["gated"] is False
    assert out["cpu"]["gate_reason"] is None
    assert out["cpu"]["measured_on_profile"] == "4k"


def test_build_score_records_thresholds_used():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _4k_full_with_cpu(2.0)},
        chromium_version="test",
    )
    t = out["cpu"]["thresholds_used"]
    assert set(t.keys()) == {
        "gate_fps_ratio",
        "full_percent",
        "partial_start_percent",
        "zero_percent",
        "min_samples",
        "sample_hz",
    }
    assert t["gate_fps_ratio"] == scorer.CPU_GATE_FPS_RATIO
    assert t["full_percent"] == scorer.CPU_FULL_THRESHOLD_PERCENT
    assert t["partial_start_percent"] == scorer.CPU_PARTIAL_START_PERCENT
    assert t["zero_percent"] == scorer.CPU_ZERO_THRESHOLD_PERCENT
    assert t["min_samples"] == scorer.CPU_MIN_SAMPLES
    assert t["sample_hz"] == 1.0  # value in the fixture cpu block


def test_build_score_4k_round_failed_gates_cpu():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": None},
        chromium_version="test",
    )
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "4k_round_failed"
    assert out["cpu"]["mean_percent"] is None


def test_build_score_sampler_no_data_gates_cpu():
    # 4k metrics present but cpu missing
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _4k_full_with_cpu(None)},
        chromium_version="test",
    )
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "sampler_no_data"


def test_build_score_container_mode_unsupported():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _4k_full_with_cpu(None)},
        chromium_version="test",
        failure_reason=None,
        cpu_override_reason="container_mode_unsupported",
    )
    assert out["cpu"]["points"] == 0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "container_mode_unsupported"


def test_build_score_no_h26x_keys():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _4k_full_with_cpu(2.0)},
        chromium_version="test",
    )
    assert "h264" not in out
    assert "h265" not in out
