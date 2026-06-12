from __future__ import annotations

import numpy as np
from PIL import Image

from lib import watermark


def test_background_is_deterministic_per_frame_number():
    # Same frame number must yield byte-identical pixels so reference PNGs are
    # reproducible across regenerations (the analyzer matches by frame number).
    a = np.asarray(watermark._background(640, 360, 123))
    b = np.asarray(watermark._background(640, 360, 123))
    assert np.array_equal(a, b)


def test_background_is_high_entropy_and_varies_per_frame():
    # The hardened background must carry real per-frame-varying detail so the
    # 4K encode fills toward its bitrate target instead of collapsing to a
    # near-lossless ~8 Mbps. A smooth gradient+stripes background changes only
    # slightly frame-to-frame; the tiled random field changes substantially.
    bg_n = np.asarray(watermark._background(640, 360, 200)).astype(np.int16)
    bg_next = np.asarray(watermark._background(640, 360, 201)).astype(np.int16)
    mad = float(np.mean(np.abs(bg_n - bg_next)))
    assert mad > 30.0, f"frame-to-frame background change too small: MAD={mad:.1f}"
    # And the background within a frame is high-variance (not a smooth gradient).
    assert float(bg_n.std()) > 40.0


def test_watermark_signals_decode_on_top_of_high_entropy_background():
    from pylibdmtx.pylibdmtx import decode

    frame = watermark.generate_frame(1280, 720, 4242, fps=20)
    layout = watermark.WatermarkLayout(1280, 720)

    # DataMatrix still decodes to the frame number.
    side = int(min(1280, 720) * 0.18)
    crop = frame.crop((1280 - side, int(720 * 0.78), 1280, 720))
    res = decode(crop, max_count=1, timeout=2000)
    assert res and res[0].data.decode() == "4242"

    # Frame-number box stays solid black, color blocks stay their fixed RGB.
    arr = np.asarray(frame)
    fn = layout.frame_no_box
    corner = arr[fn[1] + 2, fn[0] + 2]
    assert tuple(int(c) for c in corner) == (0, 0, 0)
    for color, region in zip(watermark.COLOR_BLOCKS, layout.color_block_boxes):
        cx = (region[0] + region[2]) // 2
        cy = (region[1] + region[3]) // 2
        assert tuple(int(c) for c in arr[cy, cx]) == color


def test_write_sequence_removes_stale_frames(tmp_path, monkeypatch):
    def tiny_frame(width: int, height: int, frame_number: int, fps: float) -> Image.Image:
        return Image.new("RGB", (1, 1), (frame_number, 0, 0))

    monkeypatch.setattr(watermark, "generate_frame", tiny_frame)
    stale = tmp_path / "frame_00002.png"
    stale.write_bytes(b"stale")

    count = watermark.write_sequence(tmp_path, width=1, height=1, fps=2, duration_s=1)

    assert count == 2
    assert (tmp_path / "frame_00000.png").exists()
    assert (tmp_path / "frame_00001.png").exists()
    assert not stale.exists()
