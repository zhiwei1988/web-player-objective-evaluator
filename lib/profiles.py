"""Single truth source for evaluator per-round parameters.

Every pipeline module (runner.py, analyzer.py, scorer.py, report.py),
every orchestration script (scripts/evaluator.sh, scripts/prepare_streams.sh,
scripts/health_check.sh), and the RTSP server config (rtsp_server/mediamtx.yml)
SHALL derive their per-round constants from PROFILES below — not from local
literals. Adding a new resolution profile is a one-spec addition here plus
matching MediaMTX path + reference stream regeneration.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileSpec:
    name: str                    # "2k" | "4k"
    width: int
    height: int
    fps: int
    bitrate: str                 # libx265 -b:v parameter, e.g. "4M" / "8M"
    rtsp_path: str               # MediaMTX path, e.g. "test/h265_2560_1440"
    stream_file: str             # path relative to repo root
    reference_dir: str           # path relative to repo root
    duration_s: int = 30
    cpu_sampled: bool = False    # True -> runner.py starts CPU sampler in this round


RTSP_PORT = 554
FRONTEND_PORT = 8080


PROFILES: dict[str, ProfileSpec] = {
    "2k": ProfileSpec(
        name="2k",
        width=2560,
        height=1440,
        fps=20,
        bitrate="4M",
        rtsp_path="test/h265_2560_1440",
        stream_file="streams/h265_2560_1440.mp4",
        reference_dir="reference/2k",
        cpu_sampled=False,
    ),
    "4k": ProfileSpec(
        name="4k",
        width=3840,
        height=2160,
        fps=20,
        bitrate="16M",
        rtsp_path="test/h265_3840_2160",
        stream_file="streams/h265_3840_2160.mp4",
        reference_dir="reference/4k",
        cpu_sampled=True,
    ),
}


def rtsp_url(profile: str, host: str = "127.0.0.1") -> str:
    """Compose the canonical RTSP URL for a profile.

    Profile name is looked up in PROFILES; passing an unknown name raises KeyError.
    """
    return f"rtsp://{host}:{RTSP_PORT}/{PROFILES[profile].rtsp_path}"
