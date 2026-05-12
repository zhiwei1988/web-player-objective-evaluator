"""Playwright capture runner.

Launches headless Chromium against http://localhost:8080/play?codec=<codec>&autoplay=1,
waits for window.__PLAYER_READY__, and screenshots the [data-testid="player-video"]
element at the requested rate. Element-only screenshots — never full-page — so that
contestants cannot pass color-block checks by relying on the page background.

Exit codes:
  0 success
  2 readiness timeout (window.__PLAYER_READY__ never became true within 15s)
  3 missing data-testid element
  4 playwright launched but page failed to load
  5 other / unknown
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)


READY_TIMEOUT_S = 15
PLAYER_SELECTOR = '[data-testid="player-video"]'


@dataclass
class CaptureResult:
    success: bool
    reason: str | None = None
    timestamps: list[float] = field(default_factory=list)
    browser_errors: list[str] = field(default_factory=list)
    chromium_version: str | None = None


def run_capture(codec: str, output: Path, duration_s: float, fps: float) -> CaptureResult:
    output.mkdir(parents=True, exist_ok=True)
    result = CaptureResult(success=False)
    url = f"http://localhost:8080/play?codec={codec}&autoplay=1"

    with sync_playwright() as p:
        # Fresh browser per codec — never reuse contexts to avoid state bleed.
        # We use channel='chrome' (Google Chrome) instead of the Playwright-
        # bundled Chromium because the open-source Chromium Headless Shell that
        # Playwright ships does not include the proprietary H.264/H.265
        # decoders — a <video> tag against our reference streams fails with
        # DEMUXER_ERROR_NO_SUPPORTED_STREAMS. Google Chrome (and Microsoft
        # Edge) license the codecs and play them out-of-the-box. If Chrome is
        # not installed we fall back to Chromium so the script still runs
        # against contestants that ship their own JS-decode path
        # (WebCodecs / MSE).
        launch_args = dict(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--autoplay-policy=no-user-gesture-required",
                # HEVC/H.265 decoding in Chrome on Linux is feature-gated.
                # Without the explicit flag, h265 <video> playback errors with
                # DEMUXER_ERROR_NO_SUPPORTED_STREAMS even on full Chrome.
                "--enable-features=PlatformHEVCDecoderSupport",
            ],
        )
        browser = None
        try:
            browser = p.chromium.launch(channel="chrome", **launch_args)
        except PlaywrightError as exc:
            chrome_err = str(exc)
            try:
                browser = p.chromium.launch(**launch_args)
            except PlaywrightError as exc2:
                result.reason = (
                    f"browser launch failed: chrome={chrome_err}; chromium={exc2}"
                )
                return result

        result.chromium_version = browser.version
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        page.on("pageerror", lambda exc: result.browser_errors.append(f"pageerror: {exc}"))
        page.on(
            "console",
            lambda msg: result.browser_errors.append(f"console.{msg.type}: {msg.text}")
            if msg.type in ("error", "warning")
            else None,
        )

        try:
            # NEVER networkidle — streaming apps keep network busy forever.
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightTimeout:
            result.reason = "navigation timeout"
            browser.close()
            return result
        except PlaywrightError as exc:
            result.reason = f"navigation failed: {exc}"
            browser.close()
            return result

        # Wait for the contract handshake.
        try:
            page.wait_for_function(
                "window.__PLAYER_READY__ === true",
                timeout=READY_TIMEOUT_S * 1000,
            )
        except PlaywrightTimeout:
            err = page.evaluate("window.__PLAYER_ERROR__ || null")
            result.reason = f"startup timeout (__PLAYER_ERROR__={err})"
            _write_timestamps(output, result)
            browser.close()
            return result

        # Element must exist after readiness — never silently capture something else.
        try:
            locator = page.locator(PLAYER_SELECTOR)
            locator.wait_for(state="visible", timeout=5000)
            bbox = locator.bounding_box()
            if not bbox:
                raise PlaywrightTimeout("bounding_box returned None")
        except PlaywrightTimeout:
            result.reason = "missing data-testid=player-video"
            _write_timestamps(output, result)
            browser.close()
            return result

        # Precompute the clip rect ONCE. page.screenshot(clip=...) is ~3x faster
        # than locator.screenshot() per call because it skips the per-shot
        # bounding-box round-trip. At 30 Hz target this turns a 70-second
        # capture into ~30 seconds (i.e., back on schedule).
        clip = {
            "x": int(bbox["x"]),
            "y": int(bbox["y"]),
            "width": int(bbox["width"]),
            "height": int(bbox["height"]),
        }

        # Capture loop. JPEG quality=90 is visually indistinguishable from PNG
        # for our watermarked test pattern but encodes ~3x faster, which is
        # what gets us to the spec's 30 Hz target on commodity hardware. SSIM
        # against the reference PNG is computed on grayscale and quality=90
        # has no measurable impact at that level; DataMatrix and color blocks
        # are unaffected.
        interval = 1.0 / fps
        n_frames = int(round(duration_s * fps))
        t0 = time.monotonic()
        next_deadline = t0
        try:
            for i in range(n_frames):
                # Pace ourselves; if we fall behind, capture anyway — better to
                # under-sample than to lie about FPS.
                now = time.monotonic()
                if now < next_deadline:
                    time.sleep(next_deadline - now)
                ts = time.time()
                path = output / f"shot_{i:05d}.jpg"
                try:
                    page.screenshot(path=str(path), clip=clip,
                                    type="jpeg", quality=90)
                except PlaywrightError as exc:
                    result.browser_errors.append(f"screenshot {i}: {exc}")
                    # Continue — analyzer will see the gap.
                result.timestamps.append(ts)
                next_deadline += interval
        except KeyboardInterrupt:
            result.reason = "interrupted"
            browser.close()
            _write_timestamps(output, result)
            return result

        browser.close()

    result.success = True
    _write_timestamps(output, result)
    return result


def _write_timestamps(output: Path, result: CaptureResult) -> None:
    (output / "timestamps.json").write_text(
        json.dumps(
            {
                "success": result.success,
                "reason": result.reason,
                "timestamps": result.timestamps,
                "browser_errors": result.browser_errors,
                "chromium_version": result.chromium_version,
            },
            indent=2,
        )
    )


def _cli() -> int:
    p = argparse.ArgumentParser(description="Playwright capture runner.")
    p.add_argument("--codec", required=True, choices=("h264", "h265"))
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--duration", required=True, type=float, help="Seconds.")
    p.add_argument("--fps", required=True, type=float)
    args = p.parse_args()

    result = run_capture(args.codec, args.output, args.duration, args.fps)
    print(json.dumps({"success": result.success, "reason": result.reason}))
    if result.success:
        return 0
    if not result.reason:
        return 5
    if "startup timeout" in result.reason:
        return 2
    if "missing data-testid" in result.reason:
        return 3
    if "navigation" in result.reason:
        return 4
    return 5


if __name__ == "__main__":
    raise SystemExit(_cli())
