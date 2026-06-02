from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from PIL import Image
from playwright.sync_api import Error as PlaywrightError, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

import runner
from lib import decode_forensics as _forensics


def test_default_capture_viewport_is_contract_size():
    assert runner.CAPTURE_VIEWPORT == {"width": 1280, "height": 720}


# ---- decode-path forensics glue (no browser) ---------------------------------

def test_write_decode_forensics_roundtrip(tmp_path):
    res = runner.CaptureResult(success=True)
    res.decode_forensics = {"verdict": "violation", "checks": {}, "evidence": ["x"], "errors": []}
    runner._write_decode_forensics(tmp_path, res)
    out = json.loads((tmp_path / "decode_forensics.json").read_text())
    assert out["verdict"] == "violation"


def test_write_decode_forensics_noop_when_none(tmp_path):
    res = runner.CaptureResult(success=True)
    runner._write_decode_forensics(tmp_path, res)
    assert not (tmp_path / "decode_forensics.json").exists()


def test_ingest_forensic_report_hevc_bytes_is_ok():
    c = _forensics.ForensicsCollector()
    runner._ingest_forensic_report(
        c, json.dumps({"sink": "appendBuffer", "hex": b"\x00\x00\x00\x01\x40\x01".hex()})
    )
    assert c.result()["verdict"] == "ok"


def test_ingest_forensic_report_codec_string_is_violation():
    c = _forensics.ForensicsCollector()
    runner._ingest_forensic_report(c, json.dumps({"sink": "videodecoder", "codec": "avc1.640028"}))
    assert c.result()["verdict"] == "violation"


def test_ingest_media_props_frames_decoded_triggers_violation():
    c = _forensics.ForensicsCollector()
    runner._ingest_media_props(c, {"codec": None}, {"properties": [{"name": "kFramesDecoded", "value": "7"}]})
    assert c.result()["verdict"] == "violation"


def test_ingest_media_props_video_decoder_name_triggers_violation():
    # The real Check-1 signal on the eval host's Chrome: a <video> decoder was
    # initialized (only happens for a browser-decodable, i.e. non-HEVC, codec).
    c = _forensics.ForensicsCollector()
    runner._ingest_media_props(
        c, {"codec": None}, {"properties": [{"name": "kVideoDecoderName", "value": "FFmpegVideoDecoder"}]}
    )
    res = c.result()
    assert res["verdict"] == "violation"
    assert res["checks"]["video_decoder_codec"] == "FFmpegVideoDecoder"


def test_ingest_media_props_empty_decoder_name_is_not_a_signal():
    c = _forensics.ForensicsCollector()
    runner._ingest_media_props(c, {"codec": None}, {"properties": [{"name": "kVideoDecoderName", "value": ""}]})
    assert c.result()["verdict"] == "inconclusive"


def test_ingest_handles_garbage_without_raising():
    c = _forensics.ForensicsCollector()
    runner._ingest_forensic_report(c, "not json")
    runner._ingest_media_props(c, {"codec": None}, {"bogus": True})
    assert c.result()["verdict"] == "inconclusive"


def test_clip_below_minimum_is_rejected():
    with pytest.raises(runner.PlayerClipTooSmall) as exc:
        runner.validate_player_clip({"x": 0, "y": 0, "width": 1279, "height": 720})

    assert "player-video below minimum size" in str(exc.value)


def test_timestamps_include_capture_metadata(tmp_path):
    result = runner.CaptureResult(
        success=True,
        target_fps=25.0,
        target_duration_s=30.0,
        capture_strategy="playwright",
        jpeg_quality=90,
        clip={"x": 0, "y": 0, "width": 1280, "height": 720},
    )
    result.timestamps.extend([1.0, 1.04])

    runner._write_timestamps(tmp_path, result)

    out = json.loads((tmp_path / "timestamps.json").read_text())
    assert out["target_fps"] == 25.0
    assert out["target_duration_s"] == 30.0
    assert out["capture_strategy"] == "playwright"
    assert out["jpeg_quality"] == 90
    assert out["clip"] == {"x": 0, "y": 0, "width": 1280, "height": 720}


def test_timestamps_include_layout_diagnostics(tmp_path):
    result = runner.CaptureResult(
        success=True,
        target_fps=20.0,
        target_duration_s=30.0,
        capture_strategy="playwright",
        jpeg_quality=90,
        clip={"x": 0, "y": 0, "width": 1280, "height": 720},
    )
    result.layout_diagnostics = {
        "url": "http://localhost:8080/play?profile=2k&autoplay=1",
        "viewport": {"width": 1280, "height": 720},
        "devicePixelRatio": 1,
        "ready": True,
        "error": None,
        "clip": result.clip,
        "host": {"bbox": {"x": 0, "y": 0, "width": 1280, "height": 720}},
        "media": [],
        "warnings": ["no descendant canvas/video elements found"],
    }

    runner._write_timestamps(tmp_path, result)

    out = json.loads((tmp_path / "timestamps.json").read_text())
    assert out["layout_diagnostics"]["viewport"] == {"width": 1280, "height": 720}
    assert out["layout_diagnostics"]["warnings"] == ["no descendant canvas/video elements found"]


def test_write_layout_diagnostics_writes_standalone_file(tmp_path):
    result = runner.CaptureResult(
        success=True,
        clip={"x": 0, "y": 0, "width": 1280, "height": 720},
    )
    result.layout_diagnostics = {
        "clip": result.clip,
        "host": {"bbox": {"width": 1280, "height": 720}},
        "warnings": ["canvas element is larger than clipped host"],
    }

    runner._write_layout_diagnostics(tmp_path, result)

    out = json.loads((tmp_path / "layout_diagnostics.json").read_text())
    assert out["clip"]["width"] == 1280
    assert out["host"]["bbox"]["height"] == 720
    assert out["warnings"] == ["canvas element is larger than clipped host"]


def test_write_layout_diagnostics_noop_when_absent(tmp_path):
    result = runner.CaptureResult(success=True)

    runner._write_layout_diagnostics(tmp_path, result)

    assert not (tmp_path / "layout_diagnostics.json").exists()


def test_write_capture_status_overwrites_phase_atomically(tmp_path):
    runner._write_capture_status(tmp_path, profile="2k", phase="browser_launching")
    runner._write_capture_status(tmp_path, profile="2k", phase="navigating", detail={"url": "http://localhost:8080/play"})

    out = json.loads((tmp_path / "capture_status.json").read_text())
    assert out["profile"] == "2k"
    assert out["phase"] == "navigating"
    assert out["detail"] == {"url": "http://localhost:8080/play"}
    assert isinstance(out["updated_at_epoch"], float)
    assert not (tmp_path / ".capture_status.json.tmp").exists()


class _FakeMediaSession:
    def send(self, method):
        assert method == "Media.enable"

    def on(self, event, callback):
        assert event == "Media.playerPropertiesChanged"


class _FakeBrowserPage:
    def __init__(self, goto_error=None):
        self.goto_error = goto_error
        self.handlers = {}

    def on(self, event, callback):
        self.handlers[event] = callback

    def expose_function(self, name, callback):
        assert name == "__forensicReport"

    def goto(self, url, wait_until, timeout):
        assert wait_until == "domcontentloaded"
        assert timeout == 30_000
        if self.goto_error:
            raise self.goto_error


class _FakeBrowserContext:
    def __init__(self, page):
        self.page = page

    def new_page(self):
        return self.page

    def add_init_script(self, script):
        assert script

    def new_cdp_session(self, page):
        return _FakeMediaSession()


class _FakeBrowser:
    version = "fake-chrome"

    def __init__(self, page):
        self.page = page
        self.closed = False

    def new_context(self, viewport):
        assert viewport == runner.CAPTURE_VIEWPORT
        return _FakeBrowserContext(self.page)

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, page):
        self.page = page

    def launch(self, **kwargs):
        return _FakeBrowser(self.page)


class _FakePlaywright:
    def __init__(self, page):
        self.chromium = _FakeChromium(page)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_run_capture_navigation_timeout_writes_artifacts(tmp_path, monkeypatch):
    page = _FakeBrowserPage(goto_error=PlaywrightTimeout("navigation timed out"))
    monkeypatch.setattr(runner, "sync_playwright", lambda: _FakePlaywright(page))

    result = runner.run_capture("2k", tmp_path, duration_s=30, fps=20)

    assert result.success is False
    assert result.reason == "navigation timeout"
    status = json.loads((tmp_path / "capture_status.json").read_text())
    assert status["phase"] == "navigation_timeout"
    timestamps = json.loads((tmp_path / "timestamps.json").read_text())
    assert timestamps["reason"] == "navigation timeout"
    assert timestamps["contestant_feedback"] == ["navigation timeout"]


def test_layout_warning_for_oversized_canvas_in_clipped_host():
    diagnostics = {
        "ready": True,
        "host": {
            "bbox": {"width": 1280, "height": 720},
            "client": {"width": 1280, "height": 720},
            "scroll": {"width": 2560, "height": 1440},
            "computedStyle": {"overflow": "hidden", "overflowX": "hidden", "overflowY": "hidden"},
        },
        "media": [
            {
                "tag": "canvas",
                "bbox": {"width": 2560, "height": 1440},
                "client": {"width": 2560, "height": 1440},
                "intrinsic": {"width": 2560, "height": 1440},
                "computedStyle": {"transform": "none"},
            }
        ],
        "clip": {"width": 1280, "height": 720},
    }

    warnings = runner._derive_layout_warnings(diagnostics)

    assert any("larger than clipped host" in warning for warning in warnings)


def test_layout_warning_for_clip_exceeding_viewport():
    diagnostics = {
        "viewport": {"width": 1280, "height": 720},
        "clip": {"x": 1, "y": 1, "width": 2558, "height": 1438},
        "host": {"bbox": {"width": 2558, "height": 1438}, "computedStyle": {}},
        "media": [{"tag": "canvas", "bbox": {"width": 2558, "height": 1438}, "intrinsic": {"width": 2558, "height": 1438}}],
    }

    warnings = runner._derive_layout_warnings(diagnostics)

    assert any("capture clip exceeds browser viewport" in warning for warning in warnings)


def test_layout_warnings_for_transform_missing_media_and_zero_intrinsic_size():
    diagnostics = {
        "ready": True,
        "host": {
            "bbox": {"width": 1280, "height": 720},
            "computedStyle": {"overflow": "visible", "transform": "scale(2)"},
        },
        "media": [
            {
                "tag": "video",
                "bbox": {"width": 1280, "height": 720},
                "intrinsic": {"width": 0, "height": 0},
                "computedStyle": {"transform": "none"},
            }
        ],
        "clip": {"width": 1280, "height": 720},
    }

    warnings = runner._derive_layout_warnings(diagnostics)
    missing_warnings = runner._derive_layout_warnings({
        "ready": True,
        "host": {"bbox": {"width": 1280, "height": 720}, "computedStyle": {}},
        "media": [],
        "clip": {"width": 1280, "height": 720},
    })

    assert any("transform" in warning for warning in warnings)
    assert any("zero intrinsic" in warning for warning in warnings)
    assert any("no descendant canvas/video" in warning for warning in missing_warnings)


def test_collect_layout_diagnostics_is_bounded_for_many_media_elements():
    raw = {
        "url": "http://localhost:8080/play",
        "viewport": {"width": 1280, "height": 720},
        "devicePixelRatio": 1,
        "ready": True,
        "error": None,
        "host": {"bbox": {"width": 1280, "height": 720}, "computedStyle": {}},
        "media": [
            {"tag": "canvas", "bbox": {"width": 1, "height": 1}, "intrinsic": {"width": 1, "height": 1}}
            for _ in range(runner.MAX_LAYOUT_MEDIA_ELEMENTS + 3)
        ],
    }

    diagnostics = runner._normalize_layout_diagnostics(raw, {"x": 0, "y": 0, "width": 1280, "height": 720})

    assert len(diagnostics["media"]) == runner.MAX_LAYOUT_MEDIA_ELEMENTS
    assert any("truncated media diagnostics" in warning for warning in diagnostics["warnings"])


def test_timestamps_include_contestant_feedback_for_capture_reason(tmp_path):
    result = runner.CaptureResult(
        success=False,
        reason="missing data-testid=player-video",
    )

    runner._write_timestamps(tmp_path, result)

    out = json.loads((tmp_path / "timestamps.json").read_text())
    assert out["contestant_feedback"] == ["missing data-testid=player-video"]


def test_timestamps_include_player_error_feedback_without_full_diagnostics(tmp_path):
    result = runner.CaptureResult(
        success=False,
        reason="startup timeout (__PLAYER_ERROR__=decoder failed)",
    )
    result.browser_errors.append('diagnostic: {"url":"http://localhost:8080/play","error":"decoder failed"}')
    result.browser_errors.append("pageerror: Error: full stack trace")

    runner._write_timestamps(tmp_path, result)

    out = json.loads((tmp_path / "timestamps.json").read_text())
    assert "startup timeout (__PLAYER_ERROR__=decoder failed)" in out["contestant_feedback"]
    assert not any("diagnostic:" in line for line in out["contestant_feedback"])
    assert not any("full stack trace" in line for line in out["contestant_feedback"])


def test_capture_meta_records_2k_cpu_profile(tmp_path):
    result = runner.CaptureResult(
        success=True,
        cpu_sample_result=SimpleNamespace(to_dict=lambda: {
            "mean_percent": 2.0,
            "sample_count": 4,
            "sample_window_ms": 30000,
            "ncpu": 8,
            "clk_tck": 100,
            "normalization": "all_cores_total",
            "pgid": 123,
            "extra_root_pid": 456,
            "sample_hz_used": 1.0,
            "exclude_chrome_gpu": True,
            "excluded_gpu_pids": [],
            "per_process_top": [],
        }),
        capture_started_at_epoch=1.0,
        capture_ended_at_epoch=31.0,
    )

    runner._write_capture_meta(tmp_path, "2k", result)

    out = json.loads((tmp_path / "capture_meta.json").read_text())
    assert out["profile"] == "2k"
    assert out["cpu"]["mean_percent"] == 2.0


def test_capture_meta_accepts_already_serialized_cpu_dict(tmp_path):
    result = runner.CaptureResult(
        success=True,
        cpu_sample_result={
            "mean_percent": 3.0,
            "sample_count": 4,
        },
        capture_started_at_epoch=1.0,
        capture_ended_at_epoch=31.0,
    )

    runner._write_capture_meta(tmp_path, "2k", result)

    out = json.loads((tmp_path / "capture_meta.json").read_text())
    assert out["cpu"] == {"mean_percent": 3.0, "sample_count": 4}


def test_capture_strategy_names_are_explicit():
    assert runner.CAPTURE_STRATEGY_PLAYWRIGHT == "playwright"
    assert runner.CAPTURE_STRATEGY_CDP == "cdp"


class _FakeSession:
    def __init__(self):
        self.sent = []
        self.detached = False

    def send(self, method, params):
        self.sent.append((method, params))
        return {"data": "AQID"}

    def detach(self):
        self.detached = True


class _FakeContext:
    def __init__(self, session):
        self.session = session

    def new_cdp_session(self, page):
        return self.session


class _FakePage:
    def __init__(self, viewport):
        self.viewport_size = viewport
        self.session = _FakeSession()
        self.context = _FakeContext(self.session)
        self.playwright_screenshots = []

    def screenshot(self, **kwargs):
        self.playwright_screenshots.append(kwargs)


def test_playwright_strategy_uses_cdp_beyond_viewport_for_oversized_clip(tmp_path):
    page = _FakePage({"width": 1280, "height": 720})
    path = tmp_path / "shot.jpg"

    runner._capture_screenshot(
        page=page,
        path=path,
        clip={"x": 1, "y": 1, "width": 2558, "height": 1438},
        strategy=runner.CAPTURE_STRATEGY_PLAYWRIGHT,
        jpeg_quality=90,
    )

    assert page.playwright_screenshots == []
    method, params = page.session.sent[0]
    assert method == "Page.captureScreenshot"
    assert params["captureBeyondViewport"] is True
    assert params["clip"]["width"] == 2558.0
    assert path.read_bytes() == b"\x01\x02\x03"


def test_playwright_strategy_keeps_playwright_for_clip_inside_viewport(tmp_path):
    page = _FakePage({"width": 1280, "height": 720})
    path = tmp_path / "shot.jpg"

    runner._capture_screenshot(
        page=page,
        path=path,
        clip={"x": 0, "y": 0, "width": 1280, "height": 720},
        strategy=runner.CAPTURE_STRATEGY_PLAYWRIGHT,
        jpeg_quality=90,
    )

    assert len(page.playwright_screenshots) == 1
    assert page.session.sent == []


def test_cdp_strategy_captures_beyond_viewport(tmp_path):
    page = _FakePage({"width": 1280, "height": 720})
    path = tmp_path / "shot.jpg"

    runner._capture_screenshot(
        page=page,
        path=path,
        clip={"x": 0, "y": 0, "width": 1280, "height": 720},
        strategy=runner.CAPTURE_STRATEGY_CDP,
        jpeg_quality=90,
    )

    assert page.session.sent[0][1]["captureBeyondViewport"] is True


def test_real_browser_capture_can_exceed_viewport(tmp_path):
    path = tmp_path / "shot.jpg"
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True, args=["--no-sandbox"])
        except PlaywrightError:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()
        page.set_content(
            '<html><body style="margin:0">'
            '<div data-testid="player-video" '
            'style="width:2558px;height:1438px;background:linear-gradient(135deg, red, blue)">'
            '</div></body></html>'
        )

        runner._capture_screenshot(
            page=page,
            path=path,
            clip={"x": 0, "y": 0, "width": 2558, "height": 1438},
            strategy=runner.CAPTURE_STRATEGY_PLAYWRIGHT,
            jpeg_quality=90,
        )
        browser.close()

    assert Image.open(path).size == (2558, 1438)
