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

from lib import decode_forensics as _forensics
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
MAX_LAYOUT_MEDIA_ELEMENTS = 8


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
    decode_forensics: dict | None = None
    contestant_feedback: list[str] = field(default_factory=list)
    layout_diagnostics: dict | None = None


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
    collector = _forensics.ForensicsCollector()
    result.target_fps = fps
    result.target_duration_s = duration_s
    result.capture_strategy = _resolve_capture_strategy(capture_strategy)
    result.jpeg_quality = DEFAULT_JPEG_QUALITY
    url = f"http://localhost:{FRONTEND_PORT}/play?profile={profile}&autoplay=1"
    _write_capture_status(output, profile=profile, phase="runner_started", detail={"url": url})

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
        _write_capture_status(output, profile=profile, phase="browser_launching", detail={"channel": "chrome"})
        try:
            browser = p.chromium.launch(channel="chrome", **launch_args)
        except PlaywrightError as exc:
            chrome_err = str(exc)
            _write_capture_status(output, profile=profile, phase="browser_launching", detail={"channel": "chromium"})
            try:
                browser = p.chromium.launch(**launch_args)
            except PlaywrightError as exc2:
                result.reason = (
                    f"browser launch failed: chrome={chrome_err}; chromium={exc2}"
                )
                _write_capture_status(output, profile=profile, phase="browser_launch_failed", detail={"reason": result.reason})
                _write_timestamps(output, result)
                return result

        result.chromium_version = browser.version
        _write_capture_status(output, profile=profile, phase="browser_launched", detail={"chromium_version": result.chromium_version})
        context = browser.new_context(viewport=CAPTURE_VIEWPORT)
        _write_capture_status(output, profile=profile, phase="context_created", detail={"viewport": CAPTURE_VIEWPORT})
        page = context.new_page()
        _write_capture_status(output, profile=profile, phase="page_created")

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

        # Decode-path forensics (best-effort, installed before navigation).
        _install_forensics(context, page, collector)
        _write_capture_status(output, profile=profile, phase="forensics_installed")

        try:
            # NEVER networkidle — streaming apps keep network busy forever.
            _write_capture_status(output, profile=profile, phase="navigating", detail={"url": url})
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightTimeout:
            result.reason = "navigation timeout"
            _write_capture_status(output, profile=profile, phase="navigation_timeout", detail={"url": url})
            _write_timestamps(output, result)
            browser.close()
            return result
        except PlaywrightError as exc:
            result.reason = f"navigation failed: {exc}"
            _write_capture_status(output, profile=profile, phase="navigation_failed", detail={"url": url, "reason": result.reason})
            _write_timestamps(output, result)
            browser.close()
            return result

        # Wait for the contract handshake.
        try:
            _write_capture_status(output, profile=profile, phase="waiting_ready", detail={"timeout_s": READY_TIMEOUT_S})
            page.wait_for_function(
                "window.__PLAYER_READY__ === true",
                timeout=READY_TIMEOUT_S * 1000,
            )
        except PlaywrightTimeout:
            _write_capture_status(output, profile=profile, phase="readiness_timeout", detail={"timeout_s": READY_TIMEOUT_S})
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
            result.layout_diagnostics = _collect_layout_diagnostics(page, None)
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
        _write_capture_status(output, profile=profile, phase="ready")

        # Element must exist after readiness — never silently capture something else.
        try:
            _write_capture_status(output, profile=profile, phase="player_visible_wait", detail={"selector": PLAYER_SELECTOR, "timeout_ms": 5000})
            locator = page.locator(PLAYER_SELECTOR)
            locator.wait_for(state="visible", timeout=5000)
            bbox = locator.bounding_box()
            if not bbox:
                raise PlaywrightTimeout("bounding_box returned None")
        except PlaywrightTimeout:
            result.reason = "missing data-testid=player-video"
            _write_capture_status(output, profile=profile, phase="player_missing", detail={"selector": PLAYER_SELECTOR})
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
            _write_capture_status(output, profile=profile, phase="clip_invalid", detail={"clip": clip, "reason": result.reason})
            _write_timestamps(output, result)
            browser.close()
            return result
        result.clip = clip
        _write_capture_status(output, profile=profile, phase="clip_computed", detail={"clip": clip})
        result.layout_diagnostics = _collect_layout_diagnostics(page, clip)
        _write_layout_diagnostics(output, result)
        _write_capture_status(output, profile=profile, phase="layout_diagnostics_written", detail={"path": "layout_diagnostics.json"})

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
        _write_capture_status(output, profile=profile, phase="screenshot_loop_started", detail={"target_frames": int(round(duration_s * fps)), "fps": fps, "duration_s": duration_s})

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
                _write_capture_status(output, profile=profile, phase="interrupted")
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
            try:
                result.decode_forensics = collector.result()
            except Exception as exc:  # collector is defensive, but never fail here
                result.decode_forensics = {
                    "verdict": _forensics.VERDICT_INCONCLUSIVE,
                    "checks": {},
                    "evidence": [],
                    "errors": [f"collector: {exc}"],
                }
            _write_decode_forensics(output, result)
            _write_capture_status(output, profile=profile, phase="screenshot_loop_finished", detail={"captured_timestamps": len(result.timestamps)})

        browser.close()

    result.success = True
    _write_capture_status(output, profile=profile, phase="completed", detail={"captured_timestamps": len(result.timestamps)})
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


def _box_dict(value: dict | None) -> dict | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, float] = {}
    for key in ("x", "y", "width", "height", "top", "right", "bottom", "left"):
        raw = value.get(key)
        if isinstance(raw, (int, float)):
            out[key] = float(raw)
    return out


def _dim_dict(value: dict | None) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for key in ("width", "height"):
        raw = value.get(key)
        if isinstance(raw, (int, float)):
            out[key] = float(raw)
    return out


def _style_subset(value: dict | None) -> dict:
    if not isinstance(value, dict):
        return {}
    keys = (
        "display",
        "visibility",
        "position",
        "overflow",
        "overflowX",
        "overflowY",
        "objectFit",
        "transform",
        "width",
        "height",
    )
    return {key: str(value.get(key) or "") for key in keys if key in value}


def _normalize_layout_diagnostics(raw: dict | None, clip: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    media_in = raw.get("media") if isinstance(raw.get("media"), list) else []
    media = []
    for item in media_in[:MAX_LAYOUT_MEDIA_ELEMENTS]:
        if not isinstance(item, dict):
            continue
        media.append({
            "tag": str(item.get("tag") or "").lower(),
            "id": str(item.get("id") or ""),
            "testid": str(item.get("testid") or ""),
            "bbox": _box_dict(item.get("bbox")),
            "client": _dim_dict(item.get("client")),
            "intrinsic": _dim_dict(item.get("intrinsic")),
            "computedStyle": _style_subset(item.get("computedStyle")),
        })

    host_in = raw.get("host") if isinstance(raw.get("host"), dict) else {}
    diagnostics = {
        "url": str(raw.get("url") or ""),
        "viewport": _dim_dict(raw.get("viewport")),
        "devicePixelRatio": raw.get("devicePixelRatio"),
        "scroll": raw.get("scroll") if isinstance(raw.get("scroll"), dict) else {},
        "documentReadyState": str(raw.get("documentReadyState") or ""),
        "ready": raw.get("ready"),
        "error": raw.get("error"),
        "clip": clip,
        "host": {
            "bbox": _box_dict(host_in.get("bbox")),
            "client": _dim_dict(host_in.get("client")),
            "scroll": _dim_dict(host_in.get("scroll")),
            "offset": _dim_dict(host_in.get("offset")),
            "computedStyle": _style_subset(host_in.get("computedStyle")),
        },
        "media": media,
        "warnings": [],
    }
    warnings = _derive_layout_warnings(diagnostics)
    if len(media_in) > MAX_LAYOUT_MEDIA_ELEMENTS:
        warnings.append(
            f"truncated media diagnostics to {MAX_LAYOUT_MEDIA_ELEMENTS} of {len(media_in)} elements"
        )
    diagnostics["warnings"] = warnings
    return diagnostics


def _overflow_clips(style: dict) -> bool:
    vals = [style.get("overflow"), style.get("overflowX"), style.get("overflowY")]
    return any(v in {"hidden", "clip", "scroll", "auto"} for v in vals if v)


def _transform_applied(style: dict) -> bool:
    value = str(style.get("transform") or "")
    return bool(value and value != "none")


def _derive_layout_warnings(diagnostics: dict) -> list[str]:
    warnings: list[str] = []
    clip = diagnostics.get("clip") or {}
    viewport = diagnostics.get("viewport") or {}
    host = diagnostics.get("host") or {}
    host_box = host.get("bbox") or {}
    host_style = host.get("computedStyle") or {}
    media = diagnostics.get("media") or []

    clip_w = float(clip.get("width") or 0)
    clip_h = float(clip.get("height") or 0)
    if clip and (clip_w < MIN_PLAYER_WIDTH or clip_h < MIN_PLAYER_HEIGHT):
        warnings.append(
            f"host clip below contract size ({clip_w:g}x{clip_h:g} < {MIN_PLAYER_WIDTH}x{MIN_PLAYER_HEIGHT})"
        )
    if _clip_exceeds_viewport(clip, viewport):
        viewport_w = float(viewport.get("width") or 0)
        viewport_h = float(viewport.get("height") or 0)
        clip_x = float(clip.get("x") or 0)
        clip_y = float(clip.get("y") or 0)
        warnings.append(
            f"capture clip exceeds browser viewport ({clip_w:g}x{clip_h:g} at x={clip_x:g},y={clip_y:g} > {viewport_w:g}x{viewport_h:g}); beyond-viewport capture may be slower"
        )
    if _transform_applied(host_style):
        warnings.append("player host has CSS transform applied")
    if not media:
        warnings.append("no descendant canvas/video elements found")

    host_w = float(host_box.get("width") or 0)
    host_h = float(host_box.get("height") or 0)
    host_clips = _overflow_clips(host_style)
    for item in media:
        tag = item.get("tag") or "media"
        style = item.get("computedStyle") or {}
        if _transform_applied(style):
            warnings.append(f"{tag} element has CSS transform applied")
        intrinsic = item.get("intrinsic") or {}
        if diagnostics.get("ready") is True and (
            float(intrinsic.get("width") or 0) <= 0
            or float(intrinsic.get("height") or 0) <= 0
        ):
            warnings.append(f"{tag} element has zero intrinsic dimensions after readiness")
        box = item.get("bbox") or {}
        media_w = float(box.get("width") or (item.get("client") or {}).get("width") or 0)
        media_h = float(box.get("height") or (item.get("client") or {}).get("height") or 0)
        if host_clips and host_w > 0 and host_h > 0 and (
            media_w > host_w + 1 or media_h > host_h + 1
        ):
            warnings.append(f"{tag} element is larger than clipped host; screenshot may show only part of it")
    return warnings


def _collect_layout_diagnostics(page, clip: dict | None) -> dict | None:
    try:
        raw = page.evaluate(
            f"""() => {{
                const maxMedia = {MAX_LAYOUT_MEDIA_ELEMENTS + 1};
                const box = (el) => {{
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return {{
                        x: r.x, y: r.y, width: r.width, height: r.height,
                        top: r.top, right: r.right, bottom: r.bottom, left: r.left,
                    }};
                }};
                const style = (el) => {{
                    if (!el) return {{}};
                    const s = getComputedStyle(el);
                    return {{
                        display: s.display,
                        visibility: s.visibility,
                        position: s.position,
                        overflow: s.overflow,
                        overflowX: s.overflowX,
                        overflowY: s.overflowY,
                        objectFit: s.objectFit,
                        transform: s.transform,
                        width: s.width,
                        height: s.height,
                    }};
                }};
                const dims = (el, kind) => {{
                    if (!el) return {{}};
                    if (kind === 'client') return {{width: el.clientWidth || 0, height: el.clientHeight || 0}};
                    if (kind === 'scroll') return {{width: el.scrollWidth || 0, height: el.scrollHeight || 0}};
                    if (kind === 'offset') return {{width: el.offsetWidth || 0, height: el.offsetHeight || 0}};
                    return {{}};
                }};
                const el = document.querySelector('[data-testid="player-video"]');
                const media = Array.from((el || document).querySelectorAll('canvas,video')).slice(0, maxMedia).map((m) => {{
                    const tag = m.tagName.toLowerCase();
                    return {{
                        tag,
                        id: m.id || '',
                        testid: m.getAttribute('data-testid') || '',
                        bbox: box(m),
                        client: dims(m, 'client'),
                        intrinsic: tag === 'canvas'
                            ? {{width: m.width || 0, height: m.height || 0}}
                            : {{width: m.videoWidth || 0, height: m.videoHeight || 0}},
                        computedStyle: style(m),
                    }};
                }});
                return {{
                    url: location.href,
                    viewport: {{width: window.innerWidth, height: window.innerHeight}},
                    devicePixelRatio: window.devicePixelRatio,
                    scroll: {{x: window.scrollX, y: window.scrollY}},
                    documentReadyState: document.readyState,
                    ready: window.__PLAYER_READY__ ?? null,
                    error: window.__PLAYER_ERROR__ ?? null,
                    host: el && {{
                        bbox: box(el),
                        client: dims(el, 'client'),
                        scroll: dims(el, 'scroll'),
                        offset: dims(el, 'offset'),
                        computedStyle: style(el),
                    }},
                    media,
                }};
            }}"""
        )
    except PlaywrightError:
        return None
    return _normalize_layout_diagnostics(raw, clip)


def _clip_exceeds_viewport(clip: dict | None, viewport: dict | None) -> bool:
    if not clip or not viewport:
        return False
    try:
        x = float(clip.get("x") or 0)
        y = float(clip.get("y") or 0)
        width = float(clip.get("width") or 0)
        height = float(clip.get("height") or 0)
        viewport_width = float(viewport.get("width") or 0)
        viewport_height = float(viewport.get("height") or 0)
    except (TypeError, ValueError):
        return False
    if viewport_width <= 0 or viewport_height <= 0:
        return False
    return x < 0 or y < 0 or x + width > viewport_width or y + height > viewport_height


def _capture_cdp_screenshot(
    *,
    page,
    path: Path,
    clip: dict,
    jpeg_quality: int,
) -> None:
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
                "captureBeyondViewport": True,
                "optimizeForSpeed": True,
            },
        )
        path.write_bytes(base64.b64decode(res["data"]))
    finally:
        session.detach()


def _capture_screenshot(
    *,
    page,
    path: Path,
    clip: dict,
    strategy: str,
    jpeg_quality: int,
) -> None:
    if strategy == CAPTURE_STRATEGY_PLAYWRIGHT:
        viewport = getattr(page, "viewport_size", None)
        if _clip_exceeds_viewport(clip, viewport):
            _capture_cdp_screenshot(
                page=page,
                path=path,
                clip=clip,
                jpeg_quality=jpeg_quality,
            )
            return
        page.screenshot(
            path=str(path),
            clip=clip,
            type="jpeg",
            quality=jpeg_quality,
        )
        return

    if strategy == CAPTURE_STRATEGY_CDP:
        _capture_cdp_screenshot(
            page=page,
            path=path,
            clip=clip,
            jpeg_quality=jpeg_quality,
        )
        return

    raise ValueError(f"unknown capture strategy {strategy!r}")


# Pre-load instrumentation (Check 2): wrap the entry points where bytes reach a
# decode sink and report a small hex sample back to Python. Defensive throughout
# — any failure is swallowed so a contestant page can't be broken by forensics.
_FORENSIC_INIT_JS = r"""
(() => {
  try {
    const cap = {n: 0};
    const report = (obj) => {
      try { if (cap.n++ < 200 && window.__forensicReport) window.__forensicReport(JSON.stringify(obj)); }
      catch (e) {}
    };
    const u8 = (buf) => {
      try {
        if (buf instanceof ArrayBuffer) return new Uint8Array(buf);
        if (ArrayBuffer.isView(buf)) return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
      } catch (e) {}
      return null;
    };
    const hex = (buf, max) => {
      const a = u8(buf); if (!a) return '';
      const n = Math.min(a.length, max || 256); let s = '';
      for (let i = 0; i < n; i++) s += a[i].toString(16).padStart(2, '0');
      return s;
    };
    const sample = (sink, buf) => { const h = hex(buf, 256); if (h) report({sink, hex: h}); };

    // WebSocket messages.
    const RealWS = window.WebSocket;
    if (RealWS) {
      const WS = function (...args) {
        const ws = new RealWS(...args);
        try {
          ws.addEventListener('message', (ev) => {
            try {
              const d = ev.data;
              if (d instanceof ArrayBuffer) sample('websocket', d);
              else if (d && d.arrayBuffer) d.arrayBuffer().then((b) => sample('websocket', b)).catch(() => {});
            } catch (e) {}
          });
        } catch (e) {}
        return ws;
      };
      WS.prototype = RealWS.prototype;
      ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach((k) => { try { WS[k] = RealWS[k]; } catch (e) {} });
      window.WebSocket = WS;
    }

    // fetch responses (sample the first few only).
    const realFetch = window.fetch;
    if (realFetch) {
      let fcount = 0;
      window.fetch = function (...args) {
        return realFetch.apply(this, args).then((resp) => {
          try {
            if (fcount++ < 8 && resp && resp.clone) {
              resp.clone().arrayBuffer().then((b) => sample('fetch', b)).catch(() => {});
            }
          } catch (e) {}
          return resp;
        });
      };
    }

    // MSE appendBuffer — the cleanest <video> feed signal.
    if (window.SourceBuffer && SourceBuffer.prototype && SourceBuffer.prototype.appendBuffer) {
      const realAppend = SourceBuffer.prototype.appendBuffer;
      SourceBuffer.prototype.appendBuffer = function (data) {
        try { sample('appendBuffer', data); } catch (e) {}
        return realAppend.call(this, data);
      };
    }

    // WebCodecs VideoDecoder.configure — codec string directly.
    if (window.VideoDecoder && VideoDecoder.prototype && VideoDecoder.prototype.configure) {
      const realConf = VideoDecoder.prototype.configure;
      VideoDecoder.prototype.configure = function (cfg) {
        try { if (cfg && cfg.codec) report({sink: 'videodecoder', codec: cfg.codec}); } catch (e) {}
        return realConf.call(this, cfg);
      };
    }
  } catch (e) {}
})();
"""


def _ingest_forensic_report(collector: "_forensics.ForensicsCollector", payload: str) -> None:
    """Python sink for window.__forensicReport — classify a JS-sampled buffer."""
    try:
        obj = json.loads(payload)
    except (TypeError, ValueError):
        return
    sink = obj.get("sink", "?")
    if "codec" in obj:
        collector.note_sink_codec_string(obj["codec"], sink=sink)
        return
    hexstr = obj.get("hex")
    if hexstr:
        try:
            data = bytes.fromhex(hexstr)
        except ValueError:
            return
        collector.note_sink_bytes(data, sink=sink)


def _ingest_media_props(collector: "_forensics.ForensicsCollector", state: dict, params: dict) -> None:
    """CDP Media.playerPropertiesChanged handler (Check 1)."""
    try:
        for prop in params.get("properties", []) or []:
            name = prop.get("name", "") or ""
            value = prop.get("value")
            # kVideoDecoderName is set when Chrome initializes a video decoder —
            # which only happens for a codec it can decode (never HEVC on this
            # host). That alone is the Check-1 violation signal.
            if name == "kVideoDecoderName" and value and str(value).strip():
                collector.note_video_decoder(codec=str(value), present=True)
            elif name == "kVideoTracks" and value:
                state["codec"] = value
            elif "FramesDecoded" in name:  # alt signal on builds that surface it
                try:
                    frames = int(value)
                except (TypeError, ValueError):
                    continue
                if frames > 0:
                    collector.note_video_decoder(codec=state.get("codec"), frames_decoded=frames)
    except Exception as exc:  # CDP payloads are untrusted — never let one break capture
        collector.note_error(f"media_props: {exc}")


def _install_forensics(context, page, collector: "_forensics.ForensicsCollector"):
    """Wire Check 1 (CDP Media) + Check 2 (init-script instrumentation). Best
    effort: every step is independently guarded so a failure only costs signal,
    degrading the verdict to inconclusive (fail-open) — never failing the round."""
    try:
        page.expose_function("__forensicReport", lambda payload: _ingest_forensic_report(collector, payload))
    except PlaywrightError as exc:
        collector.note_error(f"expose_function: {exc}")
    try:
        context.add_init_script(_FORENSIC_INIT_JS)
    except PlaywrightError as exc:
        collector.note_error(f"add_init_script: {exc}")
    try:
        media = context.new_cdp_session(page)
        media.send("Media.enable")
        state: dict = {"codec": None}
        media.on("Media.playerPropertiesChanged", lambda params: _ingest_media_props(collector, state, params))
    except PlaywrightError as exc:
        collector.note_error(f"cdp_media: {exc}")


def _write_decode_forensics(output: Path, result: CaptureResult) -> None:
    """Write decode_forensics.json next to the screenshots. analyzer.py forwards
    it into `<profile>_metrics.json`; scorer.py consumes the verdict (fail-open
    when absent)."""
    forensics = result.decode_forensics
    if forensics is None:
        return
    (output / "decode_forensics.json").write_text(json.dumps(forensics, indent=2))


def _write_capture_meta(output: Path, profile: str, result: CaptureResult) -> None:
    """Write capture_meta.json next to the screenshots.

    Only produced when sampling was requested. Absence is meaningful — the
    analyzer/scorer interprets it as 'sampler did not run'.
    """
    cpu = result.cpu_sample_result
    if cpu is not None and hasattr(cpu, "to_dict"):
        cpu = cpu.to_dict()
    meta = {
        "profile": profile,
        "capture_started_at_epoch": result.capture_started_at_epoch,
        "capture_ended_at_epoch": result.capture_ended_at_epoch,
        "cpu": cpu,
    }
    (output / "capture_meta.json").write_text(json.dumps(meta, indent=2))


def _write_layout_diagnostics(output: Path, result: CaptureResult) -> None:
    diagnostics = result.layout_diagnostics
    if diagnostics is None:
        return
    path = output / "layout_diagnostics.json"
    tmp = output / ".layout_diagnostics.json.tmp"
    tmp.write_text(json.dumps(diagnostics, indent=2))
    tmp.replace(path)


def _write_capture_status(
    output: Path,
    *,
    profile: str,
    phase: str,
    detail: dict | None = None,
) -> None:
    payload = {
        "profile": profile,
        "phase": phase,
        "updated_at_epoch": time.time(),
    }
    if detail:
        payload["detail"] = detail
    path = output / "capture_status.json"
    tmp = output / ".capture_status.json.tmp"
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def _write_timestamps(output: Path, result: CaptureResult) -> None:
    contestant_feedback = _contestant_feedback_from_result(result)
    payload = {
        "success": result.success,
        "reason": result.reason,
        "timestamps": result.timestamps,
        "target_fps": result.target_fps,
        "target_duration_s": result.target_duration_s,
        "capture_strategy": result.capture_strategy,
        "jpeg_quality": result.jpeg_quality,
        "clip": result.clip,
        "contestant_feedback": contestant_feedback,
        "browser_errors": result.browser_errors,
        "chromium_version": result.chromium_version,
    }
    if result.layout_diagnostics is not None:
        payload["layout_diagnostics"] = result.layout_diagnostics
    (output / "timestamps.json").write_text(json.dumps(payload, indent=2))


def _contestant_feedback_from_result(result: CaptureResult) -> list[str]:
    if result.contestant_feedback:
        return list(result.contestant_feedback)
    if result.success or not result.reason:
        return []
    reason = str(result.reason)
    contestant_patterns = (
        "startup timeout",
        "missing data-testid",
        "player-video below minimum size",
        "navigation timeout",
        "navigation failed",
    )
    if any(pattern in reason for pattern in contestant_patterns):
        return [reason]
    return []


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
