"""Frame analyzer.

Reads per-codec screenshots, recognises the watermark frame number (DataMatrix
first, OCR fallback), looks up the matching reference PNG, runs SSIM after a
Lanczos resize, validates the four bottom color blocks, and emits a metrics
JSON.

Frame matching is keyed by the watermark number, NOT the screenshot index, so
the analyzer stays correct when the stream starts mid-clip or crosses the
30-second MP4 loop boundary.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image
from pylibdmtx.pylibdmtx import decode as dmtx_decode
from skimage.metrics import structural_similarity as ssim

from lib.profiles import PROFILES


# Must mirror lib/watermark.py:WatermarkLayout.
COLOR_TARGETS = (
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 255),
)
COLOR_TOLERANCE = 40  # Per-channel absolute tolerance. yuv420p + h264 +
# display path drifts pure primaries by up to ~40 (especially green which
# loses the most in chroma subsampling). Empirically 40 catches the legitimate
# rendering, 80+ would let a fake overlay pass.


@dataclass
class ShotMetric:
    shot_path: str
    frame_number: int | None
    ssim_score: float | None
    color_pass: bool
    note: str | None = None


@dataclass
class CodecMetrics:
    total_shots: int = 0
    watermark_recognized: int = 0
    color_blocks_passed: int = 0
    ssim_scores: list[float] = field(default_factory=list)
    frame_numbers: list[int | None] = field(default_factory=list)
    duration: float = 0.0
    unique_frame_count: int = 0
    measured_fps: float = 0.0
    watermark_recognition_rate: float = 0.0
    color_check_rate: float = 0.0
    mean_ssim: float = 0.0
    per_shot: list[dict] = field(default_factory=list)


# ---- Frame-number recognition ------------------------------------------------

def _decode_datamatrix(img: Image.Image) -> int | None:
    # Crop the bottom-right ~10% × 10% of the frame — that's where watermark.py
    # placed the DataMatrix. Cropping first speeds up decode and limits false
    # positives from any decorative QR-like content elsewhere in the screenshot.
    w, h = img.size
    side = int(min(w, h) * 0.18)
    crop = img.crop((w - side, int(h * 0.78), w, h)).convert("RGB")
    decoded = dmtx_decode(crop, max_count=1, timeout=400)
    if not decoded:
        return None
    try:
        return int(decoded[0].data.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return None


_DIGITS_RE = re.compile(r"\b(\d{4,6})\b")


def _ocr_frame_number(img: Image.Image) -> int | None:
    # Crop the top-left ~20% × 12% — matches WatermarkLayout.frame_no_box.
    w, h = img.size
    crop = img.crop((0, 0, int(w * 0.22), int(h * 0.14)))
    # Convert to grayscale + threshold so tesseract sees clean black/white.
    arr = np.asarray(crop.convert("L"))
    thr = (arr < 128).astype(np.uint8) * 255  # invert: digits become bright
    pil_thr = Image.fromarray(thr)
    try:
        text = subprocess.check_output(
            ["tesseract", "-", "-", "--psm", "7", "-c", "tessedit_char_whitelist=0123456789"],
            input=pil_thr.convert("RGB").tobytes("raw", "RGB"),
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    m = _DIGITS_RE.search(text.decode("ascii", errors="ignore"))
    return int(m.group(1)) if m else None


# Use pytesseract's image_to_string for a more robust path; subprocess above is
# the fallback if pytesseract import fails.
try:
    import pytesseract

    def _ocr_frame_number(img: Image.Image) -> int | None:  # noqa: F811
        w, h = img.size
        crop = img.crop((0, 0, int(w * 0.22), int(h * 0.14)))
        arr = np.asarray(crop.convert("L"))
        thr = (arr < 128).astype(np.uint8) * 255
        pil_thr = Image.fromarray(thr)
        try:
            text = pytesseract.image_to_string(
                pil_thr,
                config="--psm 7 -c tessedit_char_whitelist=0123456789",
            )
        except pytesseract.TesseractError:
            return None
        m = _DIGITS_RE.search(text)
        return int(m.group(1)) if m else None
except ImportError:
    pass


def recognize_frame_number(img: Image.Image) -> int | None:
    """DataMatrix first, OCR fallback. Returns None on both failures."""
    n = _decode_datamatrix(img)
    if n is not None:
        return n
    return _ocr_frame_number(img)


# ---- Color block check -------------------------------------------------------

def check_color_blocks(img: Image.Image) -> int:
    """Returns the number of color blocks (0..4) whose median RGB matches the
    target within COLOR_TOLERANCE per channel."""
    w, h = img.size
    top = int(h * 0.90)
    bottom = h
    step = w // 4
    arr = np.asarray(img.convert("RGB"))
    passed = 0
    for i, target in enumerate(COLOR_TARGETS):
        left = i * step
        right = (i + 1) * step
        # Median is robust to JPEG edges; sample center 60% of each block.
        cx0 = left + int(step * 0.20)
        cx1 = right - int(step * 0.20)
        cy0 = top + int((bottom - top) * 0.20)
        cy1 = bottom - int((bottom - top) * 0.20)
        block = arr[cy0:cy1, cx0:cx1]
        if block.size == 0:
            continue
        med = np.median(block.reshape(-1, 3), axis=0)
        diff = np.abs(med - np.asarray(target))
        if np.all(diff <= COLOR_TOLERANCE):
            passed += 1
    return passed


# ---- SSIM --------------------------------------------------------------------

SSIM_TARGET_W = 960  # downscale before SSIM; preserves "are these the same
                     # frame?" signal at ~10x the speed of full-res SSIM on the
                     # 2560x1440 H.265 frames.


def compute_ssim(shot: Image.Image, reference: Image.Image) -> float:
    """Downscale both images to a common low-res grayscale before SSIM. SSIM
    is robust to scale for our "is this the right reference frame" judgment
    (the watermark + gradient + color blocks remain dominant at 960px wide);
    keeping it full-res on H.265's 2560x1440 frames costs ~200 ms per shot and
    pushes a full analyzer run past the 3-minute mark per codec.
    """
    # Pick output size from whichever image is smaller (don't upscale).
    sw, sh = shot.size
    rw, rh = reference.size
    aspect = rw / rh
    target_w = min(SSIM_TARGET_W, sw, rw)
    target_h = max(1, int(round(target_w / aspect)))
    shot_s = shot.resize((target_w, target_h), Image.LANCZOS).convert("L")
    ref_s = reference.resize((target_w, target_h), Image.LANCZOS).convert("L")
    a = np.asarray(shot_s, dtype=np.float32)
    b = np.asarray(ref_s, dtype=np.float32)
    return float(ssim(a, b, data_range=255.0))


# ---- Main pipeline -----------------------------------------------------------

def analyze(profile: str, screenshots_dir: Path, reference_dir: Path) -> CodecMetrics:
    metrics = CodecMetrics()
    shots = sorted([
        *screenshots_dir.glob("shot_*.png"),
        *screenshots_dir.glob("shot_*.jpg"),
    ])
    metrics.total_shots = len(shots)

    # Load duration from runner.py's timestamps.json so unique-frame FPS is
    # divided by the actual capture span, not the wall clock of analysis.
    ts_file = screenshots_dir / "timestamps.json"
    if ts_file.exists():
        ts_data = json.loads(ts_file.read_text())
        ts_list = ts_data.get("timestamps") or []
        if len(ts_list) >= 2:
            metrics.duration = float(ts_list[-1] - ts_list[0])

    for shot_path in shots:
        try:
            shot = Image.open(shot_path)
        except Exception as exc:  # corrupted PNG, etc.
            metrics.per_shot.append({"shot": shot_path.name, "error": str(exc)})
            metrics.frame_numbers.append(None)
            continue

        fn = recognize_frame_number(shot)
        metrics.frame_numbers.append(fn)
        if fn is not None:
            metrics.watermark_recognized += 1

        passed = check_color_blocks(shot)
        if passed == 4:
            metrics.color_blocks_passed += 1

        ssim_score: float | None = None
        if fn is not None:
            ref_path = reference_dir / f"frame_{fn:05d}.png"
            if ref_path.exists():
                try:
                    ref = Image.open(ref_path)
                    ssim_score = compute_ssim(shot, ref)
                except Exception as exc:
                    metrics.per_shot.append(
                        {"shot": shot_path.name, "fn": fn, "ssim_error": str(exc)}
                    )

        if ssim_score is not None:
            metrics.ssim_scores.append(ssim_score)

        metrics.per_shot.append(
            {
                "shot": shot_path.name,
                "fn": fn,
                "color_blocks": passed,
                "ssim": ssim_score,
            }
        )

    # Aggregates.
    if metrics.total_shots:
        metrics.watermark_recognition_rate = metrics.watermark_recognized / metrics.total_shots
        metrics.color_check_rate = metrics.color_blocks_passed / metrics.total_shots
    if metrics.ssim_scores:
        metrics.mean_ssim = float(np.mean(metrics.ssim_scores))
    unique = {fn for fn in metrics.frame_numbers if fn is not None}
    metrics.unique_frame_count = len(unique)
    if metrics.duration > 0:
        metrics.measured_fps = metrics.unique_frame_count / metrics.duration

    return metrics


def metrics_to_dict(m: CodecMetrics) -> dict:
    return {
        "total_shots": m.total_shots,
        "watermark_recognized": m.watermark_recognized,
        "color_blocks_passed": m.color_blocks_passed,
        "ssim_scores": m.ssim_scores,
        "frame_numbers": m.frame_numbers,
        "duration": m.duration,
        "unique_frame_count": m.unique_frame_count,
        "measured_fps": m.measured_fps,
        "watermark_recognition_rate": m.watermark_recognition_rate,
        "color_check_rate": m.color_check_rate,
        "mean_ssim": m.mean_ssim,
        "per_shot": m.per_shot,
    }


def _cli() -> int:
    p = argparse.ArgumentParser(description="Analyze captured screenshots.")
    p.add_argument("--profile", required=True, choices=sorted(PROFILES.keys()),
                   help="Resolution profile key from lib.profiles.PROFILES.")
    p.add_argument("--screenshots", required=True, type=Path)
    p.add_argument("--reference", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()

    metrics = analyze(args.profile, args.screenshots, args.reference)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    metrics_dict = metrics_to_dict(metrics)
    # Forward the runner's CPU sample summary if it left one. analyzer.py
    # does no CPU computation itself — capture_meta.json is the source of
    # truth, scorer.py is the consumer.
    meta_file = args.screenshots / "capture_meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text())
            cpu = meta.get("cpu")
            if cpu is not None:
                metrics_dict["cpu"] = cpu
        except (OSError, json.JSONDecodeError):
            pass
    args.output.write_text(json.dumps(metrics_dict, indent=2))
    print(json.dumps({
        "profile": args.profile,
        "watermark_recognition_rate": metrics.watermark_recognition_rate,
        "color_check_rate": metrics.color_check_rate,
        "mean_ssim": metrics.mean_ssim,
        "measured_fps": metrics.measured_fps,
        "unique_frames": metrics.unique_frame_count,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
