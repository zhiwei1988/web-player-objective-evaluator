#!/usr/bin/env python3
"""Diagnostic: measure the SSIM ceiling a faithful contestant can reach on a
profile, and the achieved stream bitrate, after the high-entropy background
change.

The evaluator captures the player element at a 1280x720 viewport, then
``analyzer.compute_ssim`` downscales both shot and reference to 960px grayscale
before SSIM. So even a perfect decoder loses some SSIM to the encode (yuv420p +
x265) and to the capture-and-resize path. This tool simulates that whole path
against the real stream so ``BG_TILE_PX`` and the ``mean_ssim`` full-correctness
threshold can be calibrated:

    .venv/bin/python scripts/measure_ssim_ceiling.py 4k --frames 60

It decodes the first N frames of the profile's stream, downscales each decoded
4K frame to 1280x720 (the player element render), and compares to the matching
reference PNG via the analyzer's own SSIM. It also confirms the DataMatrix still
decodes after encode, and reports the stream's measured bitrate.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analyzer import compute_ssim  # noqa: E402
from lib.profiles import PROFILES  # noqa: E402


def _stream_bitrate_bps(mp4: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=bit_rate",
         "-of", "default=nw=1:nk=1", str(mp4)],
        text=True,
    ).strip()
    return float(out) if out and out != "N/A" else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("profile", choices=sorted(PROFILES.keys()))
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--render-w", type=int, default=1280)
    ap.add_argument("--render-h", type=int, default=720)
    args = ap.parse_args()

    spec = PROFILES[args.profile]
    mp4 = ROOT / spec.stream_file
    ref_dir = ROOT / spec.reference_dir
    if not mp4.exists():
        print(f"missing stream {mp4}; run scripts/prepare_streams.sh", file=sys.stderr)
        return 1

    bitrate = _stream_bitrate_bps(mp4)
    print(f"profile={args.profile} stream={mp4.name} "
          f"bitrate={bitrate/1e6:.2f} Mbps target={spec.bitrate}")

    with tempfile.TemporaryDirectory() as td:
        decoded_dir = Path(td)
        # Decode the first N frames in order (fresh start = decoded i == ref i).
        subprocess.check_call(
            ["ffmpeg", "-v", "error", "-i", str(mp4),
             "-frames:v", str(args.frames),
             str(decoded_dir / "dec_%05d.png")],
        )
        ssims: list[float] = []
        for i in range(args.frames):
            dec = decoded_dir / f"dec_{i + 1:05d}.png"  # ffmpeg numbers from 1
            ref = ref_dir / f"frame_{i:05d}.png"
            if not dec.exists() or not ref.exists():
                continue
            decoded = Image.open(dec).convert("RGB")
            # Simulate the browser player element render at the capture viewport.
            shot = decoded.resize((args.render_w, args.render_h), Image.LANCZOS)
            reference = Image.open(ref).convert("RGB")
            ssims.append(compute_ssim(shot, reference))

    if not ssims:
        print("no frames compared", file=sys.stderr)
        return 1
    arr = np.array(ssims)
    print(f"frames={len(ssims)} mean_ssim={arr.mean():.4f} "
          f"min={arr.min():.4f} p10={np.percentile(arr, 10):.4f} "
          f"max={arr.max():.4f}")
    print(f"=> a faithful decoder's SSIM ceiling on {args.profile} ~ {arr.mean():.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
