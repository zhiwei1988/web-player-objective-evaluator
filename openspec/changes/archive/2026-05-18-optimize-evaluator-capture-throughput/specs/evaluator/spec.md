## ADDED Requirements

### Requirement: Capture Throughput Diagnostics

The evaluator SHALL make its own capture throughput visible in each profile metrics output so organizer-side screenshot/compositor bottlenecks can be distinguished from contestant playback bottlenecks. The diagnostics SHALL be derived from `timestamps.json` and recognized frame numbers, and SHALL NOT change FPS scoring unless the Scoring requirement is explicitly modified in a later change.

#### Scenario: Capture diagnostics are emitted

- **WHEN** analysis of a profile completes with at least two screenshot timestamps
- **THEN** `<output>/<profile>_metrics.json` contains `capture_sampling_fps`, `target_capture_fps`, `target_capture_duration`, `capture_span_overrun_ratio`, `frame_delta_histogram`, `repeat_frame_rate`, `dropped_or_skipped_frame_rate`, and `frame_progress_fps`

#### Scenario: Capture diagnostics separate evaluator overrun from playback

- **WHEN** the runner requested 750 shots over 30 seconds but the actual capture span is 39 seconds
- **THEN** `capture_sampling_fps` reflects `750 / 39`, `capture_span_overrun_ratio` is greater than `1.0`, and `measured_fps` remains computed from `unique_frame_count / duration`

#### Scenario: Diagnostics do not award score

- **WHEN** `frame_progress_fps` is higher than `measured_fps` because screenshots skipped intermediate watermark numbers
- **THEN** profile FPS points are still computed from `measured_fps` under the Scoring requirement, and the additional fields are audit evidence only

### Requirement: Capture Throughput Benchmark

The repository SHALL include a focused benchmark or regression path that measures evaluator capture throughput against a deterministic local fixture without changing contestant scoring. The benchmark SHALL report actual capture span, shot count, average shot interval, and p50/p90/p99 inter-shot intervals for each capture strategy under test.

#### Scenario: Benchmark reports screenshot throughput

- **WHEN** the capture throughput benchmark is run against a ready local fixture
- **THEN** it reports shot count, actual capture duration, capture sampling FPS, average inter-shot interval, p50 inter-shot interval, p90 inter-shot interval, and p99 inter-shot interval

#### Scenario: Benchmark protects recognition stability

- **WHEN** a capture strategy is benchmarked against the reference fixture
- **THEN** the benchmark or its companion analysis verifies watermark recognition rate, color check rate, and mean SSIM stay within the existing full-correctness thresholds

## MODIFIED Requirements

### Requirement: Playwright Capture Runner

`runner.py` SHALL accept `--profile` (one of the keys in `lib/profiles.py::PROFILES`, i.e. `2k` or `4k`), `--output`, `--duration`, and `--fps`; launch headless Chromium with `--disable-dev-shm-usage`, `--no-sandbox`, `--autoplay-policy=no-user-gesture-required`, and `--enable-features=PlatformHEVCDecoderSupport`; use a fresh browser context per profile at viewport `1280x720`; navigate to `http://localhost:8080/play?profile=<profile>&autoplay=1`; wait up to 15 seconds for `window.__PLAYER_READY__ === true`; capture element-only screenshots of `[data-testid="player-video"]` (not full-page) at the requested rate for the requested duration; write screenshots as `shot_NNNNN.jpg` and a `timestamps.json` containing per-shot capture timestamps, target capture parameters, capture strategy metadata, and any browser page errors. The runner MUST NOT use `networkidle` as a readiness condition.

The runner SHALL precompute the element clip once after readiness and MUST verify that the clip is at least `1280x720`. The default capture strategy SHALL remain Playwright page screenshots with `clip`, `type="jpeg"`, and a documented JPEG quality. The runner MAY expose a non-default Chrome DevTools Protocol screenshot strategy for benchmarking or operator tuning, provided the selected strategy is recorded in `timestamps.json` and preserves element-only clipping.

#### Scenario: Round succeeds

- **WHEN** the contestant frontend signals readiness within 15 seconds and the player element is present with a clip of at least `1280x720`
- **THEN** the runner produces approximately `--duration × --fps` screenshots in `--output/` and a `timestamps.json` with monotonically non-decreasing timestamps, the requested `target_fps`, the requested `target_duration_s`, and the selected capture strategy

#### Scenario: Element below minimum capture size

- **WHEN** `[data-testid="player-video"]` is visible after readiness but its clip is smaller than `1280x720`
- **THEN** the runner fails the round with a clear `player-video below minimum size` error captured in `timestamps.json` and the evaluator log

#### Scenario: Readiness timeout

- **WHEN** `window.__PLAYER_READY__` is not `true` within 15 seconds
- **THEN** the runner reads `window.__PLAYER_ERROR__` if present, writes the reason and any captured browser errors into `timestamps.json`, exits non-zero, and the evaluator fails that profile round

#### Scenario: Missing player element

- **WHEN** `[data-testid="player-video"]` cannot be located after readiness
- **THEN** the runner fails the round with a clear `missing data-testid` error captured in `timestamps.json` and the evaluator log

#### Scenario: Profile selects the right URL

- **WHEN** `runner.py --profile 4k ...` is invoked
- **THEN** the navigated URL is exactly `http://localhost:8080/play?profile=4k&autoplay=1`; the runner SHALL NOT emit `?codec=` query parameters under any flag combination

#### Scenario: CDP strategy is auditable when enabled

- **WHEN** the non-default Chrome DevTools Protocol screenshot strategy is selected
- **THEN** the runner captures the same element clip, records that strategy and its relevant options in `timestamps.json`, and still writes screenshots using the same `shot_NNNNN.jpg` naming convention

### Requirement: Frame Analysis

`analyzer.py` SHALL accept `--profile`, `--screenshots`, `--reference`, and `--output`; iterate screenshot files in lexical order; convert each screenshot to an RGB array; extract the frame number using DataMatrix first and OCR as a fallback; locate the corresponding `<reference>/frame_NNNNN.png` (where `<reference>` is the profile's reference directory, e.g. `reference/2k/` or `reference/4k/`); resize the screenshot to the reference size before SSIM comparison; validate the four bottom color blocks against the fixed RGB targets within a documented tolerance; compute capture-throughput diagnostics from `timestamps.json` and recognized frame numbers; and emit a metrics JSON at `<output>/<profile>_metrics.json`. Frame matching MUST be keyed by the watermark frame number, not the screenshot index, so the analyzer remains correct when the stream starts mid-clip or crosses the MP4 loop boundary.

#### Scenario: Metrics schema

- **WHEN** analysis of a profile completes
- **THEN** `<output>/<profile>_metrics.json` (one of `2k_metrics.json` or `4k_metrics.json`) contains: `total_shots`, `watermark_recognized`, `color_blocks_passed`, `ssim_scores`, `frame_numbers`, `duration`, `unique_frame_count`, `measured_fps`, `watermark_recognition_rate`, `color_check_rate`, `mean_ssim`, `capture_sampling_fps`, `target_capture_fps`, `target_capture_duration`, `capture_span_overrun_ratio`, `frame_delta_histogram`, `repeat_frame_rate`, `dropped_or_skipped_frame_rate`, and `frame_progress_fps`

#### Scenario: Unique-frame FPS

- **WHEN** a contestant feeds a single frozen image instead of live video
- **THEN** `unique_frame_count` is at or near 1 and `measured_fps` is computed from unique watermark frame numbers divided by capture duration, surfacing the cheat

#### Scenario: Loop-crossing stream

- **WHEN** the contestant playback crosses the 30-second MP4 loop boundary mid-capture
- **THEN** every recognized frame is still matched to the correct reference PNG by frame number, SSIM remains high, and frame-delta diagnostics account for the loop boundary without treating it as a normal large forward skip

#### Scenario: Repeated-frame diagnostics

- **WHEN** consecutive recognized screenshots carry the same watermark frame number
- **THEN** the analyzer counts those samples in `frame_delta_histogram` under delta `0` and includes them in `repeat_frame_rate`

#### Scenario: Skipped-frame diagnostics

- **WHEN** consecutive recognized screenshots advance by more than one watermark frame number within the same stream loop
- **THEN** the analyzer counts those samples in `frame_delta_histogram` under the observed positive delta and includes them in `dropped_or_skipped_frame_rate`

### Requirement: Report Generation

`report.py` SHALL generate an internal-only `report.html` per run, containing: a top summary with `objective_total` and per-profile subtotals (`2k` and `4k`); a gallery of suspicious screenshots (watermark recognition failures, color block failures, samples with SSIM `< 0.7`); a frame-number-over-time chart for each profile; an SSIM histogram for each profile; capture-throughput diagnostics for each profile; and relative-path links to the raw artifacts. The report MUST NOT be exposed to contestants by default.

#### Scenario: Report includes audit evidence

- **WHEN** scoring completes
- **THEN** `report.html` opens in a browser, renders all charts and galleries from local files only (no network calls), shows capture sampling FPS and capture overrun diagnostics for each completed profile, and links to `2k_screenshots/`, `4k_screenshots/`, both metrics JSONs, and `score.json`

#### Scenario: Empty failure gallery

- **WHEN** a submission passes every check
- **THEN** the report still renders, with the suspicious-screenshot gallery showing zero entries and a clear "no failures" message
