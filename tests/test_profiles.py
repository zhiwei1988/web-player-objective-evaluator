"""Sanity test for lib.profiles — the single truth source for evaluator
per-round parameters."""

from __future__ import annotations

from lib.profiles import PROFILES, ProfileSpec, RTSP_PORT, FRONTEND_PORT, rtsp_url


def test_profiles_has_2k_and_4k():
    assert set(PROFILES.keys()) == {"2k", "4k"}


def test_profile_2k_values():
    spec = PROFILES["2k"]
    assert isinstance(spec, ProfileSpec)
    assert spec.name == "2k"
    assert spec.width == 2560
    assert spec.height == 1440
    assert spec.fps == 20
    assert spec.bitrate == "4M"
    assert spec.rtsp_path == "test/h265_2560_1440"
    assert spec.stream_file == "streams/h265_2560_1440.mp4"
    assert spec.reference_dir == "reference/2k"
    assert spec.duration_s == 30
    assert spec.cpu_sampled is True


def test_profile_4k_values():
    spec = PROFILES["4k"]
    assert spec.width == 3840
    assert spec.height == 2160
    assert spec.fps == 20
    assert spec.bitrate == "8M"
    assert spec.rtsp_path == "test/h265_3840_2160"
    assert spec.stream_file == "streams/h265_3840_2160.mp4"
    assert spec.reference_dir == "reference/4k"
    assert spec.duration_s == 30
    assert spec.cpu_sampled is False


def test_only_2k_is_cpu_sampled():
    sampled = [name for name, s in PROFILES.items() if s.cpu_sampled]
    assert sampled == ["2k"]


def test_rtsp_constants():
    assert RTSP_PORT == 554
    assert FRONTEND_PORT == 8080


def test_rtsp_url_helper():
    assert rtsp_url("2k") == "rtsp://127.0.0.1:554/test/h265_2560_1440"
    assert rtsp_url("4k") == "rtsp://127.0.0.1:554/test/h265_3840_2160"
    assert rtsp_url("2k", host="10.0.0.1") == "rtsp://10.0.0.1:554/test/h265_2560_1440"
