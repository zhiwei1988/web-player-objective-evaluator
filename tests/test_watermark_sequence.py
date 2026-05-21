from __future__ import annotations

from PIL import Image

from lib import watermark


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
