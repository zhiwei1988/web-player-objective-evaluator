## Context

The current evaluator measures per-profile FPS from recognized unique frame numbers divided by the actual capture span. That is defensible for anti-cheat, but the capture span is now inflated by the evaluator itself: `page.screenshot(..., type="jpeg", quality=90)` takes roughly 50 ms per shot on observed 2K/4K runs, while a 25 fps source requires a 40 ms sampling interval. As a result, a healthy player can be capped by screenshot throughput near 19 shots/s before playback performance is the limiting factor.

The existing contestant contract already requires `[data-testid="player-video"]` to render at least `1280x720` proportionally and uncropped. The runner currently uses a `1920x1080` viewport and captures the rendered element clip at that size, which pays more compositor and JPEG encoding cost than the contract requires. The analyzer already downscales SSIM inputs to 960 px wide, so a 1280 px wide capture still preserves the watermark, color blocks, and SSIM signal.

## Goals / Non-Goals

**Goals:**

- Reduce steady-state screenshot cost enough that 750 requested shots for a 30 second 25 fps profile finishes much closer to the requested 30 second window.
- Preserve element-only capture so page background or unrelated DOM cannot satisfy correctness checks.
- Add audit metrics that separate capture throughput from unique-frame FPS.
- Keep static-frame, repeated-frame, and fake-overlay anti-cheat behavior intact.
- Keep the first implementation dependency-free beyond Playwright and Chrome already used by the evaluator.

**Non-Goals:**

- Do not change the contestant runtime contract beyond relying on its existing `1280x720` minimum.
- Do not immediately replace the scoring FPS formula with frame-progress or timestamp-derived alternatives.
- Do not require contestants to expose a special test hook, internal frame counter, or raw decoder buffer.
- Do not remove screenshot artifacts from run output.

## Decisions

### Default capture size becomes contract-sized

Use a fresh browser context with viewport `1280x720` for capture rounds and continue to compute one element clip from `[data-testid="player-video"]` after readiness. The runner shall verify the clip is at least `1280x720` before the capture loop. This aligns evaluator cost with the published minimum surface size instead of silently capturing a larger 1920x1080 element.

Alternative considered: keep `1920x1080` and lower JPEG quality. Lowering quality helps encode time, but still pushes the compositor and readback through more pixels than necessary. Size reduction is the cleaner first lever because it reduces readback, encode, disk, and analyzer decode cost together.

### Keep Playwright screenshot as the stable baseline, add CDP as an optional strategy

The default path remains Playwright `page.screenshot(clip=..., type="jpeg")` because it is stable and already integrated. Add a capture strategy switch so a benchmark or environment flag can select Chrome DevTools Protocol `Page.captureScreenshot` with the same clip and `optimizeForSpeed=true`. CDP can expose encoder options Playwright Python 1.49 does not, but it should earn its place through benchmark evidence before becoming default.

Alternative considered: switch directly to `Page.startScreencast`. Screencast is tempting because it pushes frames instead of polling screenshots, but it is experimental, viewport-scoped rather than element-scoped, and introduces ack/backpressure behavior. It is better as a later spike if smaller clip plus CDP capture is still insufficient.

### Add diagnostics before changing score semantics

Analyzer output should retain the existing `measured_fps` field and add capture diagnostics:

- `capture_sampling_fps = total_shots / duration`
- `target_capture_fps`
- `target_capture_duration`
- `capture_span_overrun_ratio`
- `frame_delta_histogram`
- `repeat_frame_rate`
- `dropped_or_skipped_frame_rate`
- `frame_progress_fps`

`frame_progress_fps` is useful audit evidence, but not yet a scoring replacement because a player that skips frames aggressively could advance watermark numbers without presenting smooth playback. Scoring stays conservative until diagnostics prove a better metric is cheat-resistant.

### Benchmark the evaluator itself

Add focused tests or benchmark scripts that run the capture path against a deterministic local fixture and assert the diagnostic fields are populated. Full 30 second throughput targets should be checked in a host-level benchmark path rather than brittle unit tests. Unit tests should cover frame-delta math and metrics schema; integration tests should verify recognition and scoring do not regress.

## Risks / Trade-offs

- Smaller viewport could expose submissions that hard-coded layouts for 1920x1080. Mitigation: the published contract only guarantees the evaluator requires at least `1280x720`; the runner will fail clearly if the element does not meet that minimum.
- CDP screenshot behavior may diverge from Playwright screenshot behavior. Mitigation: keep Playwright as default until CDP proves faster and equivalent on watermark recognition, color checks, and SSIM.
- New diagnostic FPS values can be misread as score fields. Mitigation: keep `score.json` FPS points tied to `measured_fps` unless a later spec explicitly changes scoring.
- Capture overrun may not disappear on overloaded hosts. Mitigation: record overrun and sampling FPS in metrics/report so organizer-side infrastructure limits are visible during appeals.

## Migration Plan

1. Add tests for capture diagnostic aggregation and frame-delta histogram behavior.
2. Change the runner default viewport to `1280x720`, record capture strategy metadata, and keep screenshot file naming unchanged.
3. Add analyzer diagnostics while preserving existing metrics keys.
4. Surface diagnostics in report output without changing score totals.
5. Run reference, static-frame, fake-overlay, and throughput benchmark checks before making CDP strategy default.

Rollback is straightforward: restore the runner viewport to `1920x1080` and keep diagnostics as harmless extra metrics, or disable the CDP strategy while retaining the baseline Playwright path.

## Open Questions

None. The first implementation should optimize capture cost and add diagnostics without changing scoring semantics.
