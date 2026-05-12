"""Deterministic watermark renderer for evaluator reference frames.

Each frame carries four overlapping identification signals so that contestants
cannot fake the watermark via canvas overlays without actually decoding the
stream:

  1. Visible 5-digit zero-padded frame number block (top-left)
  2. HH:MM:SS.mmm timecode (top-right)
  3. Four fixed-RGB color blocks along the bottom strip
  4. DataMatrix code encoding the integer frame number (bottom-right)
"""

from __future__ import annotations

import argparse
import functools
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pylibdmtx.pylibdmtx import encode as dmtx_encode


# Fixed color block targets — analyzer.py validates against these exact values
# within a documented tolerance.
COLOR_BLOCKS = (
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 255),
)

# Background gradient base — fills the central area so SSIM has structure to
# compare against. Deterministic per (frame_number, codec).
BG_BASE = (24, 28, 40)


@dataclass(frozen=True)
class WatermarkLayout:
    """Pixel regions for each watermark element, scaled to the target frame size.

    `analyzer.py` MUST stay in sync with these proportions. We compute them
    from the frame dimensions so a single layout works for both 1920x1080 and
    2560x1440.
    """
    width: int
    height: int

    @property
    def frame_no_box(self) -> tuple[int, int, int, int]:
        # Top-left, ~20% width × ~12% height
        return (0, 0, int(self.width * 0.20), int(self.height * 0.12))

    @property
    def timecode_box(self) -> tuple[int, int, int, int]:
        # Top-right, ~40% width × ~12% height. The timecode is 12 characters
        # (`HH:MM:SS.mmm`); 25% width is not enough to render it legibly at
        # the box's height-derived font size, especially on 1920x1080.
        return (int(self.width * 0.60), 0, self.width, int(self.height * 0.12))

    @property
    def color_strip_box(self) -> tuple[int, int, int, int]:
        # Bottom 10% of the frame
        return (0, int(self.height * 0.90), self.width, self.height)

    @property
    def color_block_boxes(self) -> tuple[tuple[int, int, int, int], ...]:
        """One bbox per color block. Width = 25% of full width each."""
        top = int(self.height * 0.90)
        bottom = self.height
        step = self.width // 4
        return tuple(
            (i * step, top, (i + 1) * step, bottom) for i in range(4)
        )

    @property
    def datamatrix_box(self) -> tuple[int, int, int, int]:
        # Bottom-right square, occupying ~10% of the frame's shorter dimension.
        size = int(min(self.width, self.height) * 0.10)
        # Sit immediately above the color strip.
        top = int(self.height * 0.90) - size - 4
        left = self.width - size - 4
        return (left, top, left + size, top + size)


@functools.lru_cache(maxsize=64)
def _font(px_size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Best-effort TrueType font; falls back to PIL's bitmap font when no TTF
    is available on the host. We don't ship a TTF because all common Ubuntu
    installs have DejaVuSansMono.

    Cached: PIL's `truetype()` re-parses the font file each call, which is the
    hot path inside `_fit_font`'s binary search and adds ~10 ms × thousands
    of calls when generating 1650 frames.
    """
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, px_size)
    return ImageFont.load_default()


@functools.lru_cache(maxsize=128)
def _fit_font(text: str, max_w: int, max_h: int, padding: int = 16):
    """Pick the largest TrueType font size that fits `text` in the given box,
    leaving `padding` pixels of margin on each axis. Binary search between
    10px and max_h.

    Done this way instead of a fixed height-derived size because the timecode
    is wider than the frame number; using one font size for both either chops
    the timecode (current bug) or shrinks the frame number unnecessarily.

    Cached: with DejaVuSansMono all 5-digit frame numbers fit the same way and
    all 12-char timecodes fit the same way, so per-codec there are only ~2 fits
    that actually run. Without the cache, generating 1650 frames spends most
    of its time re-parsing the TTF inside this binary search.
    """
    target_w = max(1, max_w - padding)
    target_h = max(1, max_h - padding)
    lo, hi = 10, max_h
    best = lo
    while lo <= hi:
        mid = (lo + hi) // 2
        font = _font(mid)
        bbox = font.getbbox(text)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w <= target_w and h <= target_h:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return _font(best)


def _timecode(frame_number: int, fps: float) -> str:
    total_ms = int(round((frame_number / fps) * 1000.0))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _datamatrix_image(frame_number: int, size_px: int) -> Image.Image:
    encoded = dmtx_encode(str(frame_number).encode("ascii"))
    img = Image.frombytes("RGB", (encoded.width, encoded.height), encoded.pixels)
    # libdmtx generates a small image; scale up to the requested size with
    # nearest-neighbor so module edges stay sharp (critical for decode).
    return img.resize((size_px, size_px), Image.NEAREST)


def draw_watermark(frame: Image.Image, frame_number: int, fps: float) -> Image.Image:
    """Overlay all four watermark signals onto `frame` in place. Returns `frame`."""
    layout = WatermarkLayout(frame.width, frame.height)
    draw = ImageDraw.Draw(frame)

    # 1) Top-left frame number — white text on solid black, auto-sized to fit.
    box = layout.frame_no_box
    box_w, box_h = box[2] - box[0], box[3] - box[1]
    draw.rectangle(box, fill=(0, 0, 0))
    text = f"{frame_number:05d}"
    fn_font = _fit_font(text, box_w, box_h)
    draw.text((box[0] + 12, box[1] + 8), text, fill=(255, 255, 255), font=fn_font)

    # 2) Top-right timecode — white text on solid black, auto-sized to fit.
    box = layout.timecode_box
    box_w, box_h = box[2] - box[0], box[3] - box[1]
    draw.rectangle(box, fill=(0, 0, 0))
    tc = _timecode(frame_number, fps)
    tc_font = _fit_font(tc, box_w, box_h)
    draw.text((box[0] + 12, box[1] + 8), tc, fill=(255, 255, 255), font=tc_font)

    # 3) Bottom color strip — four solid blocks.
    for color, region in zip(COLOR_BLOCKS, layout.color_block_boxes):
        draw.rectangle(region, fill=color)

    # 4) Bottom-right DataMatrix.
    dm_box = layout.datamatrix_box
    dm_size = dm_box[2] - dm_box[0]
    # White surround for quiet zone, then paste the matrix on top.
    surround = (dm_box[0] - 4, dm_box[1] - 4, dm_box[2] + 4, dm_box[3] + 4)
    draw.rectangle(surround, fill=(255, 255, 255))
    dm = _datamatrix_image(frame_number, dm_size)
    frame.paste(dm, (dm_box[0], dm_box[1]))

    return frame


def _background(width: int, height: int, frame_number: int) -> Image.Image:
    """Deterministic non-uniform background so SSIM has signal to compare
    against. The pattern shifts per-frame, giving the analyzer a way to detect
    static-image cheats via SSIM divergence even if DataMatrix decodes.

    Vertical gradient × horizontal stripes that translate by frame number.
    Vectorised via numpy: a Python-level pixel loop on a 2560x1440 frame is
    ~3 s per frame, which would make a full 1650-frame regen take 80 minutes.
    """
    import numpy as np
    phase = frame_number * 7
    y_idx = np.arange(height, dtype=np.int32)
    x_idx = np.arange(width, dtype=np.int32)
    v = (y_idx * 200) // height                            # shape (H,)
    stripe = (((x_idx + phase) // 32) & 1).astype(bool)    # shape (W,)
    # Broadcast (H, W) gradient + stripe selector
    v_col = v[:, None]                                     # (H, 1)
    stripe_row = stripe[None, :]                           # (1, W)
    r = (BG_BASE[0] + v_col + np.where(stripe_row, 40, 0)) & 0xFF
    g = (BG_BASE[1] + (v_col // 2) + np.where(stripe_row, 0, 60)) & 0xFF
    b = (BG_BASE[2] + (v_col // 3) + np.where(stripe_row, 20, 30)) & 0xFF
    arr = np.stack([r, g, b], axis=-1).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def generate_frame(width: int, height: int, frame_number: int, fps: float) -> Image.Image:
    """Produce one fully-watermarked frame ready to be written or fed to ffmpeg."""
    base = _background(width, height, frame_number)
    return draw_watermark(base, frame_number, fps)


def write_sequence(out_dir: Path, width: int, height: int, fps: float, duration_s: float) -> int:
    """Write frame_NNNNN.png files into `out_dir`. Returns frame count."""
    out_dir.mkdir(parents=True, exist_ok=True)
    total = int(round(fps * duration_s))
    for n in range(total):
        img = generate_frame(width, height, n, fps)
        img.save(out_dir / f"frame_{n:05d}.png", "PNG", compress_level=1)
    return total


# ---- CLI ---------------------------------------------------------------------

def _cli() -> int:
    p = argparse.ArgumentParser(description="Generate watermarked reference frames.")
    p.add_argument("--codec", required=True, choices=("h264", "h265"))
    p.add_argument("--width", type=int, required=True)
    p.add_argument("--height", type=int, required=True)
    p.add_argument("--fps", type=float, required=True)
    p.add_argument("--duration", type=float, required=True, help="Seconds.")
    p.add_argument("--out", type=Path, required=True, help="Output directory.")
    args = p.parse_args()
    n = write_sequence(args.out, args.width, args.height, args.fps, args.duration)
    print(f"wrote {n} frames to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
