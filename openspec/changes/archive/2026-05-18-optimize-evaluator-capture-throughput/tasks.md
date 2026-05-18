## 1. Metrics Tests

- [x] 1.1 Add failing unit tests for capture diagnostic aggregation from synthetic `timestamps.json` and frame-number sequences
- [x] 1.2 Add failing unit tests for `frame_delta_histogram`, `repeat_frame_rate`, `dropped_or_skipped_frame_rate`, and loop-boundary handling
- [x] 1.3 Add failing schema/regression tests confirming existing metrics keys remain present while new diagnostic keys are emitted

## 2. Runner Capture Strategy

- [x] 2.1 Add failing tests or a small runner-level fixture for rejecting `[data-testid="player-video"]` clips below `1280x720`
- [x] 2.2 Change the default browser context viewport from `1920x1080` to `1280x720`
- [x] 2.3 Record `target_fps`, `target_duration_s`, capture strategy name, JPEG quality, and clip dimensions in `timestamps.json`
- [x] 2.4 Keep the default Playwright screenshot strategy element-only and compatible with existing `shot_NNNNN.jpg` artifacts
- [x] 2.5 Add a non-default CDP `Page.captureScreenshot` strategy path with equivalent element clipping and auditable strategy metadata

## 3. Analyzer Diagnostics

- [x] 3.1 Implement diagnostic extraction from `timestamps.json`, including target capture parameters and actual capture span
- [x] 3.2 Implement frame-delta histogram computation while handling the 30-second stream loop boundary
- [x] 3.3 Emit `capture_sampling_fps`, `target_capture_fps`, `target_capture_duration`, `capture_span_overrun_ratio`, `frame_delta_histogram`, `repeat_frame_rate`, `dropped_or_skipped_frame_rate`, and `frame_progress_fps`
- [x] 3.4 Preserve existing `measured_fps = unique_frame_count / duration` scoring input unchanged

## 4. Report And Audit Output

- [x] 4.1 Add failing report tests or snapshot checks for rendering capture diagnostics from metrics JSON
- [x] 4.2 Surface per-profile capture sampling FPS, capture overrun ratio, repeat-frame rate, skipped-frame rate, and frame-progress FPS in `report.html`
- [x] 4.3 Ensure report generation remains local-only and still links to screenshot directories, metrics JSONs, and `score.json`

## 5. Benchmark Path

- [x] 5.1 Add a capture-throughput benchmark command or script that runs against a deterministic local fixture
- [x] 5.2 Report shot count, actual capture duration, capture sampling FPS, average inter-shot interval, and p50/p90/p99 intervals
- [x] 5.3 Verify benchmark analysis preserves full-correctness thresholds for watermark recognition, color checks, and mean SSIM
- [x] 5.4 Document how to compare Playwright and CDP capture strategies without changing scoring

## 6. Regression Verification

- [x] 6.1 Run targeted unit tests for analyzer diagnostics, runner metadata, and report rendering
- [x] 6.2 Run evaluator fixture tests for reference, static-frame, and fake-overlay submissions
- [x] 6.3 Run or document a host throughput benchmark comparing old baseline, 1280x720 Playwright capture, and optional CDP capture
- [x] 6.4 Run `openspec validate optimize-evaluator-capture-throughput --strict`
