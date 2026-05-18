"""Playwright capture runner.

Launches headless Chromium against http://localhost:8080/play?profile=<profile>&autoplay=1,
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
import base64
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from lib.profiles import PROFILES, FRONTEND_PORT


READY_TIMEOUT_S = 15
PLAYER_SELECTOR = '[data-testid="player-video"]'
MIN_PLAYER_WIDTH = 1280
MIN_PLAYER_HEIGHT = 720
CAPTURE_VIEWPORT = {"width": MIN_PLAYER_WIDTH, "height": MIN_PLAYER_HEIGHT}
DEFAULT_JPEG_QUALITY = 90
CAPTURE_STRATEGY_PLAYWRIGHT = "playwright"
CAPTURE_STRATEGY_CDP = "cdp"
CAPTURE_STRATEGIES = (CAPTURE_STRATEGY_PLAYWRIGHT, CAPTURE_STRATEGY_CDP)


class PlayerClipTooSmall(ValueError):
    pass


@dataclass
class CaptureResult:
    success: bool
    reason: str | None = None
    timestamps: list[float] = field(default_factory=list)
    browser_errors: list[str] = field(default_factory=list)
    chromium_version: str | None = None
    cpu_sample_result: dict | None = None
    capture_started_at_epoch: float | None = None
    capture_ended_at_epoch: float | None = None
    target_fps: float | None = None
    target_duration_s: float | None = None
    capture_strategy: str = CAPTURE_STRATEGY_PLAYWRIGHT
    jpeg_quality: int = DEFAULT_JPEG_QUALITY
    clip: dict | None = None


def run_capture(
    profile: str,
    output: Path,
    duration_s: float,
    fps: float,
    contestant_pgid: int | None = None,
    cpu_sample_hz: float | None = None,
    capture_strategy: str | None = None,
) -> CaptureResult:
    output.mkdir(parents=True, exist_ok=True)
    spec = PROFILES[profile]
    result = CaptureResult(success=False)
    result.target_fps = fps
    result.target_duration_s = duration_s
    result.capture_strategy = _resolve_capture_strategy(capture_strategy)
    result.jpeg_quality = DEFAULT_JPEG_QUALITY
    url = f"http://localhost:{FRONTEND_PORT}/play?profile={profile}&autoplay=1"

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
        context = browser.new_context(viewport=CAPTURE_VIEWPORT)
        page = context.new_page()

        page.on("pageerror", lambda exc: result.browser_errors.append(f"pageerror: {exc}"))
        page.on(
            "console",
            lambda msg: result.browser_errors.append(f"console.{msg.type}: {msg.text}"),
        )
        page.on(
            "requestfailed",
            lambda req: result.browser_errors.append(
                f"requestfailed: {req.method} {req.url} -> {req.failure}"
            ),
        )
        page.on(
            "response",
            lambda resp: result.browser_errors.append(
                f"response: {resp.status} {resp.url}"
            )
            if resp.status >= 400
            else None,
        )
        page.on(
            "worker",
            lambda w: result.browser_errors.append(f"worker.created: {w.url}"),
        )
        page.on("crash", lambda _p: result.browser_errors.append("page.crash"))

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
            # Full diagnostic snapshot — we cannot guess from a single error string
            # why the player never fired __PLAYER_READY__.
            try:
                diag = page.evaluate(
                    """() => {
                        const el = document.querySelector('[data-testid="player-video"]');
                        const canvas = document.getElementById('contest-canvas');
                        const video = document.getElementById('contest-video');
                        const errBox = document.getElementById('contest-error');
                        const bbox = el ? el.getBoundingClientRect() : null;
                        return {
                            url: location.href,
                            ready: window.__PLAYER_READY__,
                            error: window.__PLAYER_ERROR__ ?? null,
                            crossOriginIsolated: self.crossOriginIsolated,
                            hasSAB: typeof SharedArrayBuffer !== 'undefined',
                            hasWebCodecs: typeof VideoDecoder !== 'undefined',
                            hasWebGL: (() => {
                                try {
                                    const c = document.createElement('canvas');
                                    return !!(c.getContext('webgl2') || c.getContext('webgl'));
                                } catch (e) { return 'threw:' + e; }
                            })(),
                            hostExists: !!el,
                            hostBBox: bbox && {
                                x: bbox.x, y: bbox.y, w: bbox.width, h: bbox.height,
                            },
                            canvasSize: canvas && { w: canvas.width, h: canvas.height,
                                                   cw: canvas.clientWidth, ch: canvas.clientHeight },
                            videoState: video && {
                                readyState: video.readyState,
                                paused: video.paused,
                                currentTime: video.currentTime,
                                error: video.error && video.error.code,
                                src: video.currentSrc || video.src,
                            },
                            errorBox: errBox && errBox.textContent,
                            docReadyState: document.readyState,
                            hidden: document.hidden,
                        };
                    }"""
                )
                result.browser_errors.append(f"diagnostic: {json.dumps(diag)}")
            except PlaywrightError as exc:
                result.browser_errors.append(f"diagnostic_failed: {exc}")
            # Capture a screenshot of the *page* (not just the host element) for
            # eyeball debugging — element clip may not exist if host never laid
            # out.
            try:
                page.screenshot(path=str(output / "_timeout_page.png"), full_page=False)
            except PlaywrightError as exc:
                result.browser_errors.append(f"screenshot_failed: {exc}")
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
        try:
            validate_player_clip(clip)
        except PlayerClipTooSmall as exc:
            result.reason = str(exc)
            result.clip = clip
            _write_timestamps(output, result)
            browser.close()
            return result
        result.clip = clip

        # Capture loop. JPEG quality=90 is visually indistinguishable from PNG
        # for our watermarked test pattern but encodes ~3x faster, which is
        # what gets us to the spec's 30 Hz target on commodity hardware. SSIM
        # against the reference PNG is computed on grayscale and quality=90
        # has no measurable impact at that level; DataMatrix and color blocks
        # are unaffected.
        import _cpu_sampler

        sampler: _cpu_sampler.Sampler | None = None
        if spec.cpu_sampled and contestant_pgid is not None:
            # Pluck the Playwright driver PID so the sampler can also follow
            # the Chrome subtree (where wasm / WebCodecs decode actually runs
            # for client-side-decode contestant designs). The contestant's
            # session tree alone would miss this work entirely.
            #
            # Playwright Python doesn't expose the driver process on the
            # public surface; the private path has been stable across the
            # 1.4x series. Fall back to None on AttributeError so an SDK
            # bump can't break sampling silently — capture_meta records
            # extra_root_pid=null in that case for audit.
            driver_pid: int | None = None
            try:
                driver_pid = browser._impl_obj._connection._transport._proc.pid
            except AttributeError:
                pass
            sampler = _cpu_sampler.Sampler(
                pgid=contestant_pgid,
                hz=cpu_sample_hz if cpu_sample_hz else _cpu_sampler.DEFAULT_SAMPLE_HZ,
                extra_root_pid=driver_pid,
            )
            sampler.start()
        result.capture_started_at_epoch = time.time()

        interval = 1.0 / fps
        n_frames = int(round(duration_s * fps))
        t0 = time.monotonic()
        next_deadline = t0
        try:
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
                        _capture_screenshot(
                            page=page,
                            path=path,
                            clip=clip,
                            strategy=result.capture_strategy,
                            jpeg_quality=result.jpeg_quality,
                        )
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
        finally:
            result.capture_ended_at_epoch = time.time()
            if sampler is not None:
                sample_result = sampler.stop()
                result.cpu_sample_result = sample_result.to_dict()
            if spec.cpu_sampled and contestant_pgid is not None:
                _write_capture_meta(output, profile, result)

        browser.close()

    result.success = True
    _write_timestamps(output, result)
    return result


def _resolve_capture_strategy(value: str | None) -> str:
    strategy = value or os.environ.get("EVALUATOR_CAPTURE_STRATEGY") or CAPTURE_STRATEGY_PLAYWRIGHT
    if strategy not in CAPTURE_STRATEGIES:
        raise ValueError(
            f"unknown capture strategy {strategy!r}; expected one of {CAPTURE_STRATEGIES}"
        )
    return strategy


def validate_player_clip(clip: dict) -> None:
    width = int(clip.get("width") or 0)
    height = int(clip.get("height") or 0)
    if width < MIN_PLAYER_WIDTH or height < MIN_PLAYER_HEIGHT:
        raise PlayerClipTooSmall(
            "player-video below minimum size "
            f"({width}x{height} < {MIN_PLAYER_WIDTH}x{MIN_PLAYER_HEIGHT})"
        )


def _capture_screenshot(
    *,
    page,
    path: Path,
    clip: dict,
    strategy: str,
    jpeg_quality: int,
) -> None:
    if strategy == CAPTURE_STRATEGY_PLAYWRIGHT:
        page.screenshot(
            path=str(path),
            clip=clip,
            type="jpeg",
            quality=jpeg_quality,
        )
        return

    if strategy == CAPTURE_STRATEGY_CDP:
        session = page.context.new_cdp_session(page)
        try:
            res = session.send(
                "Page.captureScreenshot",
                {
                    "format": "jpeg",
                    "quality": jpeg_quality,
                    "clip": {
                        "x": float(clip["x"]),
                        "y": float(clip["y"]),
                        "width": float(clip["width"]),
                        "height": float(clip["height"]),
                        "scale": 1,
                    },
                    "fromSurface": True,
                    "captureBeyondViewport": False,
                    "optimizeForSpeed": True,
                },
            )
            path.write_bytes(base64.b64decode(res["data"]))
        finally:
            session.detach()
        return

    raise ValueError(f"unknown capture strategy {strategy!r}")


def _write_capture_meta(output: Path, profile: str, result: CaptureResult) -> None:
    """Write capture_meta.json next to the screenshots.

    Only produced when sampling was requested. Absence is meaningful — the
    analyzer/scorer interprets it as 'sampler did not run'.
    """
    meta = {
        "profile": profile,
        "capture_started_at_epoch": result.capture_started_at_epoch,
        "capture_ended_at_epoch": result.capture_ended_at_epoch,
        "cpu": result.cpu_sample_result,
    }
    (output / "capture_meta.json").write_text(json.dumps(meta, indent=2))


def _write_timestamps(output: Path, result: CaptureResult) -> None:
    (output / "timestamps.json").write_text(
        json.dumps(
            {
                "success": result.success,
                "reason": result.reason,
                "timestamps": result.timestamps,
                "target_fps": result.target_fps,
                "target_duration_s": result.target_duration_s,
                "capture_strategy": result.capture_strategy,
                "jpeg_quality": result.jpeg_quality,
                "clip": result.clip,
                "browser_errors": result.browser_errors,
                "chromium_version": result.chromium_version,
            },
            indent=2,
        )
    )


def _cli() -> int:
    p = argparse.ArgumentParser(description="Playwright capture runner.")
    p.add_argument("--profile", required=True, choices=sorted(PROFILES.keys()),
                   help="Resolution profile key from lib.profiles.PROFILES.")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--duration", required=True, type=float, help="Seconds.")
    p.add_argument("--fps", required=True, type=float)
    p.add_argument("--contestant-pgid", type=int, default=None,
                   help="When set AND the active profile has cpu_sampled=True "
                        "(currently 4k only), sample the PGID's CPU.")
    p.add_argument("--cpu-sample-hz", type=float, default=None,
                   help="Sampler tick rate (debug-only, will be retired once calibrated).")
    p.add_argument("--capture-strategy", choices=CAPTURE_STRATEGIES, default=None,
                   help="Screenshot strategy. Defaults to playwright; cdp is for throughput benchmarking.")
    args = p.parse_args()

    result = run_capture(
        args.profile, args.output, args.duration, args.fps,
        contestant_pgid=args.contestant_pgid,
        cpu_sample_hz=args.cpu_sample_hz,
        capture_strategy=args.capture_strategy,
    )
    print(json.dumps({"success": result.success, "reason": result.reason}))
    if result.success:
        return 0
    if not result.reason:
        return 5
    if "startup timeout" in result.reason:
        return 2
    if "missing data-testid" in result.reason:
        return 3
    if "player-video below minimum size" in result.reason:
        return 3
    if "navigation" in result.reason:
        return 4
    return 5


if __name__ == "__main__":
    raise SystemExit(_cli())
