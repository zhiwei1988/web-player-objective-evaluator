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
        (0.0, 5.0),
        (10.0, 5.0),
        (12.0, 5.0),
        (13.0, 5.0),
        (14.0, 4.76),
        (15.0, 4.52),
        (16.0, 4.29),
        (17.0, 4.05),
        (18.0, 3.81),
        (19.0, 3.57),
        (20.0, 3.33),
        (21.0, 3.10),
        (22.0, 2.86),
        (23.0, 2.62),
        (24.0, 2.38),
        (25.0, 2.14),
        (26.0, 1.90),
        (27.0, 1.67),
        (28.0, 1.43),
        (29.0, 1.19),
        (30.0, 0.95),
        (31.0, 0.71),
        (32.0, 0.48),
        (33.0, 0.24),
        (34.0, 0.0),
        (34.001, 0.0),
        (99.0, 0.0),
    ],
)
def test_score_cpu_table(mean_cpu, expected_points):
    points, reason = scorer.score_cpu(
        mean_cpu_percent=mean_cpu,
        measured_fps=fps_at(0.9),
    )
    assert points == expected_points
    assert reason is None


def test_score_cpu_gates_when_fps_below_threshold():
    # Intentionally pins gate_fps_ratio=0.25 to verify the gating mechanism at
    # an arbitrary boundary, decoupled from the current module default.
    points, reason = scorer.score_cpu(
        mean_cpu_percent=2.0,
        measured_fps=fps_at(0.24),
        gate_fps_ratio=0.25,
    )
    assert points == 0.0
    assert reason == "4k_fps_below_threshold"


def test_score_cpu_default_gate_trips_just_below_08():
    # At the module default (0.8), 0.79 ratio must gate.
    points, reason = scorer.score_cpu(
        mean_cpu_percent=2.0,
        measured_fps=fps_at(0.79),
    )
    assert points == 0.0
    assert reason == "4k_fps_below_threshold"


def test_score_cpu_default_gate_passes_just_above_08():
    # 0.81 ratio passes the gate and the cpu scorer awards full marks at <=12% mean.
    points, reason = scorer.score_cpu(
        mean_cpu_percent=2.0,
        measured_fps=fps_at(0.81),
    )
    assert points == 5.0
    assert reason is None


def test_score_cpu_default_gate_trips_when_only_partial_fps_credit():
    # 4K ratio 0.50 earns partial linear FPS credit but is still below the
    # 0.8 CPU gate, so CPU must be gated to 0 regardless of measured CPU.
    points, reason = scorer.score_cpu(
        mean_cpu_percent=0.0,
        measured_fps=fps_at(0.50),
    )
    assert points == 0.0
    assert reason == "4k_fps_below_threshold"


def test_score_cpu_gates_when_sampler_missing():
    points, reason = scorer.score_cpu(
        mean_cpu_percent=None,
        measured_fps=fps_at(0.9),
    )
    assert points == 0.0
    assert reason == "sampler_no_data"


def test_score_cpu_gate_takes_precedence_over_value():
    points, reason = scorer.score_cpu(
        mean_cpu_percent=0.0,
        measured_fps=fps_at(0.10),
        gate_fps_ratio=0.25,
    )
    assert points == 0.0
    assert reason == "4k_fps_below_threshold"


# build_score wiring ---------------------------------------------------------

def _profile_full() -> dict:
    return {
        "watermark_recognition_rate": 1.0,
        "color_check_rate": 1.0,
        "mean_ssim": 0.95,
        "measured_fps": 20.0,
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
    # 2k: 5 correctness + 5 fps = 10; 4k: 5 correctness + 10 fps = 15; CPU 5 -> 30
    assert out["objective_total"] == 30
    assert out["2k"]["total"] == 10
    assert out["4k"]["total"] == 15
    assert out["cpu"]["points"] == 5.0
    assert out["cpu"]["gated"] is False
    assert out["cpu"]["gate_reason"] is None
    assert out["cpu"]["measured_on_profile"] == "4k"
    assert out["cpu"]["gate_profile"] == "4k"
    assert out["cpu"]["expected_fps"] == 20.0
    assert out["cpu"]["measured_fps"] == 20.0


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
    assert out["cpu"]["points"] == 0.0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "4k_round_failed"
    assert out["cpu"]["mean_percent"] is None


def test_build_score_sampler_no_data_gates_cpu():
    # 4k metrics present but cpu missing
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _profile_full()},
        chromium_version="test",
    )
    assert out["cpu"]["points"] == 0.0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "sampler_no_data"


def test_build_score_container_mode_unsupported():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _profile_full()},
        chromium_version="test",
        failure_reason=None,
        cpu_override_reason="container_mode_unsupported",
    )
    assert out["cpu"]["points"] == 0.0
    assert out["cpu"]["gated"] is True
    assert out["cpu"]["gate_reason"] == "container_mode_unsupported"


def test_build_score_no_h26x_keys():
    out = scorer.build_score(
        {"2k": _profile_full(), "4k": _4k_full_with_cpu(2.0)},
        chromium_version="test",
    )
    assert "h264" not in out
    assert "h265" not in out
