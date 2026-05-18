# evaluator

## Purpose

Host-side automated scorer for the 30-point objective portion of the web plugin-free real-time media player challenge. Drives a known-good RTSP stream into each contestant submission, captures playback through Playwright Chromium, recognizes watermarked reference frames (DataMatrix + color blocks + SSIM), and produces a deterministic `score.json` plus internal `report.html` per run that organizers can defend against appeals. The score breakdown is 10 points per H.265 profile (5 correctness + 5 FPS, at 2K and 4K resolutions) plus a 10-point CPU sub-score derived from the contestant process tree's CPU usage sampled during the 4K capture round. Internal use only.

## Requirements

### Requirement: Workspace Layout

The evaluator's working tree IS the repository root (no `evaluator/` subdirectory). It SHALL contain at minimum: under `scripts/` — `setup.sh`, `build.sh`, `deploy.sh`, `test.sh`, `evaluator.sh`, `evaluator-host.sh`, `evaluator-local.sh`, `package.sh`, `prepare_streams.sh`, `start_rtsp.sh`, `health_check.sh`, `build_test_zips.sh`, `env.sh`, `teardown.sh`; at the root — `runner.py`, `analyzer.py`, `scorer.py`, `report.py`, `requirements.txt`, `Dockerfile`, `.dockerignore`; in `lib/` — `watermark.py` and `profiles.py`; in `rtsp_server/` — `mediamtx.yml`; plus `third_party/` (containing git submodules and, after `scripts/build.sh`, an `install/` prefix), `streams/`, `reference/2k/`, `reference/4k/`, `submissions/`, `results/`, `test_submissions/`, and `dist/` (produced by `scripts/package.sh`). The evaluator main body (`scripts/evaluator.sh` + `runner.py` + `analyzer.py` + `scorer.py` + `report.py`) MAY run either natively on the build host (driven by `scripts/evaluator-local.sh`) or inside the portable OCI container (driven by `scripts/evaluator-host.sh`). The target host operator workflow runs the containerized path only.

#### Scenario: Workspace exists after setup

- **WHEN** an organizer clones the repository and runs the documented setup steps
- **THEN** every path listed above exists, the shell scripts are executable, `lib/profiles.py` exposes a `PROFILES` mapping with keys `"2k"` and `"4k"`, and both `./scripts/evaluator-host.sh` and `./scripts/evaluator-local.sh` (invoked with too few arguments) print a usage message naming `<team_id>` and `<submission_zip>`

#### Scenario: Submissions and results are isolated per run

- **WHEN** the evaluator runs for team `T` at timestamp `TS`
- **THEN** all submission files for that run live under `submissions/T/` and all artifacts live under `results/T_TS/`, with no cross-contamination from prior runs

### Requirement: Reference Stream Generation

`prepare_streams.sh` together with `lib/watermark.py` SHALL produce two watermarked MP4 files and matching PNG reference frame sequences before the contest, one per profile defined in `lib/profiles.py::PROFILES`. The **2K profile** SHALL produce `streams/h265_2560_1440.mp4` (`2560x1440`, `25fps`, `30s` duration, `libx265 hvc1 yuv420p` at `4 Mbps`, GOP `50`, `scenecut=0`) with frames under `reference/2k/frame_NNNNN.png`. The **4K profile** SHALL produce `streams/h265_3840_2160.mp4` (`3840x2160`, `25fps`, `30s` duration, `libx265 hvc1 yuv420p` at `8 Mbps`, GOP `50`, `scenecut=0`) with frames under `reference/4k/frame_NNNNN.png`. All ProfileSpec-derived parameters (resolution, fps, bitrate, duration, output paths) SHALL come from `PROFILES` rather than from script-local literals. `prepare_streams.sh` SHALL delete legacy `streams/h264_watermarked.mp4`, `streams/h265_watermarked.mp4`, `reference/h264/`, and `reference/h265/` when present, before regenerating, so the two layouts do not coexist.

#### Scenario: Watermark content per frame

- **WHEN** any reference frame `N` is generated for any profile
- **THEN** the frame contains a high-contrast 5-digit zero-padded frame number block in the top-left, a `HH:MM:SS.mmm` timecode in the top-right, four solid color blocks `(255,0,0)`, `(0,255,0)`, `(0,0,255)`, `(255,255,255)` along the bottom, and a DataMatrix code in the bottom-right encoding the integer `N`

#### Scenario: Idempotent regeneration

- **WHEN** `prepare_streams.sh` is rerun on a host where outputs already exist
- **THEN** it overwrites the MP4 files and the reference frame directories deterministically so two runs from the same code produce byte-identical PNGs and equivalent MP4s for the same profile settings

#### Scenario: Legacy assets removed

- **WHEN** `prepare_streams.sh` is run on a host where `streams/h264_watermarked.mp4`, `streams/h265_watermarked.mp4`, `reference/h264/`, or `reference/h265/` exists from a prior layout
- **THEN** those legacy paths are removed before new generation begins, leaving only the 2K and 4K profile outputs

### Requirement: Local RTSP Server

`scripts/start_rtsp.sh` SHALL start MediaMTX listening on TCP port `554` with RTSP forced over TCP, exposing `rtsp://127.0.0.1:554/test/h265_2560_1440` and `rtsp://127.0.0.1:554/test/h265_3840_2160`. The server SHALL serve the pre-generated MP4 files via `ffmpeg -re -stream_loop -1 -c:v copy` so no re-encoding occurs at runtime, and the streams SHALL be available on demand for every evaluation round. Because port `554` is below `1024`, the MediaMTX binary or container MUST hold `CAP_NET_BIND_SERVICE`: on build hosts `scripts/build.sh` SHALL apply `setcap cap_net_bind_service=+ep` to `third_party/install/bin/mediamtx`; in the container path `scripts/evaluator-host.sh` SHALL pass `--cap-add=NET_BIND_SERVICE` to `docker run`. The evaluator MUST NOT silently fall back to a higher port when binding fails.

#### Scenario: RTSP health check before contestant deploy

- **WHEN** the evaluator finishes starting the RTSP server
- **THEN** `ffprobe -rtsp_transport tcp rtsp://127.0.0.1:554/test/h265_2560_1440` AND `ffprobe -rtsp_transport tcp rtsp://127.0.0.1:554/test/h265_3840_2160` each return metadata within a short timeout

#### Scenario: RTSP failure classified as infrastructure

- **WHEN** the RTSP server fails to come up, the 2K health check fails, or the 4K health check fails
- **THEN** the evaluator stops the run, records the failure as an organizer-side infrastructure problem in `evaluator.log`, and does NOT charge the contestant with an objective score of zero for that reason

#### Scenario: MediaMTX has CAP_NET_BIND_SERVICE

- **WHEN** MediaMTX is started under either the host-native path (after `scripts/build.sh`) or the containerized path (with `docker run --cap-add=NET_BIND_SERVICE`)
- **THEN** the process binds `0.0.0.0:554` without `EACCES` and the health check scenarios above pass; if the capability is missing, the bind fails loudly and the evaluator exits with an infrastructure error rather than retrying on a different port

### Requirement: Contestant Runtime Contract

Each contestant submission zip SHALL, after extraction into `submissions/<team_id>/`, provide an executable `start.sh` that launches whatever processes the submission needs; it MAY provide a `stop.sh` for cleanup. The evaluator SHALL export `RTSP_SERVER_HOST=127.0.0.1`, `RTSP_SERVER_PORT=554`, and `FRONTEND_PORT=8080` before invoking `start.sh`. Internal contestant processes (relay backends, decode workers, etc.) MAY bind any other local port; the evaluator neither prescribes nor cleans those — the contestant's process group is SIGKILLed as a whole at cleanup. The frontend SHALL expose route `/play` accepting `profile` (`2k` or `4k`) and `autoplay=1`, render the video into `[data-testid="player-video"]` at an actual rendered size of at least `1280x720` with proportional (uncropped) display, set `window.__PLAYER_READY__ = true` after the first frame is rendered, and assign a human-readable string to `window.__PLAYER_ERROR__` on playback failure. The RTSP source URLs that the contestant SHALL pull from are fixed: `rtsp://127.0.0.1:554/test/h265_2560_1440` for `profile=2k` and `rtsp://127.0.0.1:554/test/h265_3840_2160` for `profile=4k`.

The canonical evaluation host is Ubuntu 24.04 with Google Chrome (pinned version recorded in every `score.json` / `report.html` as `chromium_version`). The runtime contract is the same for both profiles — `[data-testid="player-video"]` with the readiness signals above — and the evaluator does NOT prescribe a rendering strategy. Whether to use `<video>`, `<canvas>` with WebCodecs, a WASM decoder, or any other approach is the contestant's choice; the evaluator only screenshots the element.

#### Scenario: Missing start.sh

- **WHEN** the extracted submission does not contain `start.sh`
- **THEN** the evaluator fails the submission with a clear `missing start.sh` error, records it in `evaluator.log`, and skips capture rounds

#### Scenario: Optional stop.sh

- **WHEN** the submission provides `stop.sh`
- **THEN** the evaluator invokes it during cleanup; **WHEN** it is absent **THEN** the evaluator proceeds with its own port/process cleanup without error

#### Scenario: Frontend never becomes ready

- **WHEN** `http://localhost:8080/play?profile=2k&autoplay=1` is not reachable within 60 seconds of `start.sh` returning
- **THEN** the evaluator assigns objective score 0 for the submission, records `startup timeout` in `evaluator.log`, and proceeds to cleanup

#### Scenario: Profile parameter routes to the correct stream

- **WHEN** the contestant frontend is opened at `http://localhost:8080/play?profile=4k&autoplay=1`
- **THEN** the contestant SHALL pull from `rtsp://127.0.0.1:554/test/h265_3840_2160` and render its decoded output into `[data-testid="player-video"]`; opening with `profile=2k` SHALL similarly route to `rtsp://127.0.0.1:554/test/h265_2560_1440`

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

### Requirement: Scoring

`scorer.py` SHALL accept one or more `--metrics PROFILE=PATH` arguments (one per profile, e.g. `--metrics 2k=results/.../2k_metrics.json --metrics 4k=results/.../4k_metrics.json`), `--output`, and `--report`; score each profile independently for up to 10 points (5 correctness + 5 FPS); compute an additional 0–10 point CPU sub-score based on contestant CPU usage measured during the **4K profile** round (see the Contestant CPU Usage Measurement requirement); and produce a `score.json` containing one block per profile (keys `2k`, `4k`), a top-level `cpu` block, `objective_total`, and `max_score` of `30`.

**Correctness** (rescaled): full 5 when `watermark_recognition_rate >= 0.95` AND `color_check_rate >= 0.95` AND `mean_ssim >= 0.90`; partial 2 when `watermark_recognition_rate >= 0.80` AND `mean_ssim >= 0.75`; otherwise 0.

**FPS** (per-profile full- and partial-credit ratios): expected `25` for both profiles. Full 5 when `measured_fps / expected_fps >= FPS_FULL_RATIO_BY_PROFILE[profile]`; partial 3 when `measured_fps / expected_fps >= FPS_PARTIAL_RATIO_BY_PROFILE[profile]`; otherwise 0. Both are module-level `dict[str, float]` in `scorer.py` with default values `FPS_FULL_RATIO_BY_PROFILE = {"2k": 0.85, "4k": 0.65}` and `FPS_PARTIAL_RATIO_BY_PROFILE = {"2k": 0.50, "4k": 0.40}`. Each dict SHALL contain an entry for every key in `lib/profiles.py::PROFILES`, and for every profile the partial ratio SHALL be strictly less than the full ratio; scoring SHALL fail loudly if a profile is missing or the invariant is violated (no silent fallback). Each per-profile block in `score.json` SHALL include `fps_full_threshold_used` and `fps_partial_threshold_used` recording the ratios applied to that profile so an audit can verify the score.

**CPU** (gating on 4K): `scorer.score_cpu(mean_cpu_percent, measured_4k_fps, expected_4k_fps)` SHALL return an integer in `[0, 10]` together with a nullable `gate_reason` string, evaluated in this order:

1. Gate: if `measured_4k_fps / expected_4k_fps < CPU_GATE_FPS_RATIO` (default `0.65`, aligned with `FPS_FULL_RATIO_BY_PROFILE["4k"]`), return `(0, "4k_fps_below_threshold")`. A submission that does not reach full 4K FPS credit cannot earn any CPU points. The two ratios are independent module constants and MAY drift apart deliberately in a future change.
2. If `mean_cpu_percent` is unavailable (analyzer omitted the field, or `capture_meta.json.cpu` was null), return `(0, "sampler_no_data")`.
3. If `mean_cpu_percent <= CPU_FULL_THRESHOLD_PERCENT` (default `5.0`), return `(10, None)`.
4. If `mean_cpu_percent > CPU_ZERO_THRESHOLD_PERCENT` (default `20.0`), return `(0, None)`.
5. Otherwise return `(round((CPU_ZERO_THRESHOLD_PERCENT - mean_cpu_percent) / (CPU_ZERO_THRESHOLD_PERCENT - CPU_PARTIAL_START_PERCENT) * 10), None)`, clamped to `[0, 10]`. `CPU_PARTIAL_START_PERCENT` defaults to `6.0`.

The six tunables (`CPU_GATE_FPS_RATIO`, `CPU_FULL_THRESHOLD_PERCENT`, `CPU_PARTIAL_START_PERCENT`, `CPU_ZERO_THRESHOLD_PERCENT`, `CPU_MIN_SAMPLES`, `_cpu_sampler.DEFAULT_SAMPLE_HZ`) SHALL be exposed as named module-level constants and the actual values applied to each run SHALL be recorded under `score.json.cpu.thresholds_used` so a contestant audit can verify which thresholds produced the score.

When the 4K round fails entirely (no `4k_metrics.json` produced, or it lacks the fields the gate inspects), `scorer.py` SHALL still emit a complete `score.json` with `cpu.points=0`, `cpu.gated=true`, `cpu.gate_reason="4k_round_failed"`, and `cpu.mean_percent=null`. When the host-side wrapper fails before the evaluator main body runs, the existing failure `score.json` path SHALL include `cpu.gated=true`, `cpu.gate_reason="host_failure"`, `cpu.points=0`.

`objective_total` SHALL equal `2k.total + 4k.total + cpu.points` and SHALL NOT exceed `max_score` (`30`).

#### Scenario: Per-profile totals

- **WHEN** scoring completes for a submission
- **THEN** `score.json` includes a `2k` block, a `4k` block, a top-level `cpu` block, an `objective_total` equal to `2k.total + 4k.total + cpu.points`, and `max_score = 30`, with the underlying metrics (rates, mean SSIM, measured FPS, CPU mean percent, sample count, thresholds applied) preserved for audit; the keys `h264` and `h265` SHALL NOT appear

#### Scenario: Static-frame submission

- **WHEN** a submission renders a single static image so `measured_fps` is near 0 for both profiles
- **THEN** each profile receives 0 FPS points; correctness is scored on its own merits; the CPU gate trips on `measured_4k_fps / expected_4k_fps < CPU_GATE_FPS_RATIO` so `cpu.points=0` and `cpu.gate_reason="4k_fps_below_threshold"`

#### Scenario: Fake-overlay submission

- **WHEN** a submission draws a fake canvas overlay that visually resembles the watermark but fails DataMatrix, color block, or SSIM checks
- **THEN** it cannot reach full correctness (5) per profile and may receive 2 or 0 depending on which checks it passes

#### Scenario: 2K FPS full credit requires 85% of expected

- **WHEN** a submission's 2K round produces `measured_fps / expected_fps >= 0.85` (e.g. `22.20` fps against expected `25` fps = `88.8%`)
- **THEN** `score.json.2k.fps_points = 5`, `score.json.2k.fps_full_threshold_used = 0.85`, and `score.json.2k.fps_partial_threshold_used = 0.50`

#### Scenario: 2K FPS in the partial band receives 3 points

- **WHEN** a submission's 2K round produces `0.50 <= measured_fps / expected_fps < 0.85` (e.g. `15` fps / `25` fps = `60%`)
- **THEN** `score.json.2k.fps_points = 3`, `score.json.2k.fps_full_threshold_used = 0.85`, and `score.json.2k.fps_partial_threshold_used = 0.50`

#### Scenario: 2K FPS below the partial floor receives 0

- **WHEN** a submission's 2K round produces `measured_fps / expected_fps < 0.50` (e.g. `10` fps / `25` fps = `40%`)
- **THEN** `score.json.2k.fps_points = 0` and `score.json.2k.fps_partial_threshold_used = 0.50`

#### Scenario: 4K FPS full credit requires 65% of expected

- **WHEN** a submission's 4K round produces `measured_fps / expected_fps >= 0.65` (e.g. `17.29` fps against expected `25` fps = `69.2%`)
- **THEN** `score.json.4k.fps_points = 5`, `score.json.4k.fps_full_threshold_used = 0.65`, and `score.json.4k.fps_partial_threshold_used = 0.40`

#### Scenario: 4K FPS in the partial band receives 3 points

- **WHEN** a submission's 4K round produces `0.40 <= measured_fps / expected_fps < 0.65` (e.g. `12` fps / `25` fps = `48%`)
- **THEN** `score.json.4k.fps_points = 3`, `score.json.4k.fps_full_threshold_used = 0.65`, and `score.json.4k.fps_partial_threshold_used = 0.40`

#### Scenario: 4K FPS below the partial floor receives 0

- **WHEN** a submission's 4K round produces `measured_fps / expected_fps < 0.40` (e.g. `8` fps / `25` fps = `32%`)
- **THEN** `score.json.4k.fps_points = 0` and `score.json.4k.fps_partial_threshold_used = 0.40`

#### Scenario: CPU gate trips when 4K does not earn full FPS credit

- **WHEN** the 4K round produces `measured_fps / expected_fps = 0.50` (above the 0.40 4K partial-FPS bar, below the 0.65 CPU gate)
- **THEN** `score.json.4k.fps_points = 3` AND `score.json.cpu.points = 0` AND `score.json.cpu.gated = true` AND `score.json.cpu.gate_reason = "4k_fps_below_threshold"`, regardless of the measured `mean_cpu_percent`

#### Scenario: Missing profile entry fails loudly

- **WHEN** `scorer.score_fps` is invoked with a profile name absent from `FPS_FULL_RATIO_BY_PROFILE` or `FPS_PARTIAL_RATIO_BY_PROFILE`
- **THEN** `score_fps` raises a clear error (e.g. `KeyError`) rather than silently falling back to a default ratio, ensuring adding a new profile in `PROFILES` cannot bypass a scoring-policy decision

#### Scenario: Partial-below-full invariant is enforced at import time

- **WHEN** `scorer.py` is imported with a profile whose `FPS_PARTIAL_RATIO_BY_PROFILE[profile] >= FPS_FULL_RATIO_BY_PROFILE[profile]`
- **THEN** import fails with a clear assertion-style error naming the offending profile, so a configuration typo cannot collapse the partial band silently

#### Scenario: CPU full marks at low usage

- **WHEN** the 4K round produces `measured_fps / expected_fps >= CPU_GATE_FPS_RATIO` (passes the gate) AND `mean_cpu_percent <= 5.0`
- **THEN** `cpu.points = 10`, `cpu.gated = false`, `cpu.gate_reason = null`, and `cpu.thresholds_used` records every threshold that produced this outcome

#### Scenario: CPU partial credit in the proportional band

- **WHEN** the 4K round produces `measured_fps / expected_fps >= CPU_GATE_FPS_RATIO` (passes the gate) AND `mean_cpu_percent` falls within `(5.0, 20.0]`
- **THEN** `cpu.points = round((20 - mean_cpu_percent) / 14 * 10)` clamped to `[0, 10]`, `cpu.gated = false`, and the formula matches the published score table at the integer percent breakpoints (`6→10`, `7→9`, `10→7`, `13→5`, `15→4`, `17→2`, `19→1`, `20→0`)

#### Scenario: CPU zero when usage exceeds the upper limit

- **WHEN** the 4K round produces `measured_fps / expected_fps >= CPU_GATE_FPS_RATIO` (passes the gate) AND `mean_cpu_percent > 20.0`
- **THEN** `cpu.points = 0`, `cpu.gated = false`, `cpu.gate_reason = null`, and `cpu.mean_percent` is recorded as-measured for audit (not clipped)

#### Scenario: CPU gated when 4K fps round fails

- **WHEN** the 4K round produces `measured_fps / expected_fps < CPU_GATE_FPS_RATIO` (default `0.65`, i.e. `< 16.25` fps against the expected `25` fps)
- **THEN** `cpu.points = 0`, `cpu.gated = true`, `cpu.gate_reason = "4k_fps_below_threshold"`, and `cpu.mean_percent` still records the measured value (or `null` if sampling failed) for diagnostic purposes

#### Scenario: CPU gated when sampler produced no data

- **WHEN** the 4K capture round ran but `capture_meta.json` is missing OR `capture_meta.json.cpu` is null OR fewer than `CPU_MIN_SAMPLES` samples were collected
- **THEN** `cpu.points = 0`, `cpu.gated = true`, `cpu.gate_reason = "sampler_no_data"`, and `cpu.mean_percent = null`

#### Scenario: CPU gated when 4K round itself failed

- **WHEN** no `4k_metrics.json` was produced (player error, readiness timeout, missing element, etc.)
- **THEN** `score.json` is still emitted with `cpu.points = 0`, `cpu.gated = true`, `cpu.gate_reason = "4k_round_failed"`, and `cpu.mean_percent = null`, alongside whatever `4k.reason` the runner recorded

#### Scenario: Thresholds-used audit field

- **WHEN** any `score.json` is produced
- **THEN** `score.json.cpu.thresholds_used` contains exactly the six keys `gate_fps_ratio`, `full_percent`, `partial_start_percent`, `zero_percent`, `min_samples`, `sample_hz`, each set to the value actually applied to that run, so an organizer can replay the score from the metrics alone

### Requirement: Contestant CPU Usage Measurement

During the **4K profile** capture round, `runner.py` SHALL sample the contestant process group's CPU usage so that `scorer.py` can compute the CPU sub-score documented in the Scoring requirement. The sampler MUST run inside the same Python process as the Playwright capture loop (no sidecar daemon), MUST be active only while the steady-state capture loop is running (excluding `start.sh` warm-up, the readiness wait, and post-capture cleanup), and MUST use only the Python standard library (no new package dependency).

`runner.py` SHALL accept an optional `--contestant-pgid INT` argument. When provided AND `--profile` resolves to a `ProfileSpec` whose `cpu_sampled` flag is `True` (true for `4k` only, by default), the runner SHALL start a daemon sampler thread before the capture loop begins and stop it immediately after the loop ends. When `--contestant-pgid` is absent OR the active profile's `cpu_sampled` flag is `False`, the runner SHALL behave exactly as before sampling was introduced (no sampling, no `capture_meta.json` written), preserving backward compatibility for direct `_cli` invocations and the 2K round.

`runner.py` SHALL ALSO accept an optional `--cpu-sample-hz FLOAT` (debug-only; scheduled for retirement once a default is calibrated). When absent, the sampler SHALL use `_cpu_sampler.DEFAULT_SAMPLE_HZ` (1.0 Hz at introduction).

The sampler SHALL enumerate `/proc/[0-9]*/stat` on each tick and include any process belonging to **either** of two trees, with PID deduplication so a process matching both is counted once:

  1. **Contestant session tree** — every PID whose stat field-6 (`session`) equals the supplied PGID. This works because `scripts/_contestant_lifecycle.sh::clx_start_contestant` launches the contestant under `setsid`, so the session leader's PID equals the process group ID. Catches any server-side worker the contestant forks under its own session.

  2. **Playwright Chrome process tree** (optional) — every PID reachable from `extra_root_pid` via stat field-4 (`ppid`) descent, inclusive of the root. `runner.py` SHALL pluck `extra_root_pid` from Playwright's private API path `browser._impl_obj._connection._transport._proc.pid` after `chromium.launch(...)` and pass it to the sampler. On `AttributeError` (e.g. Playwright SDK bump changes the internal path) the runner SHALL fall back to `extra_root_pid=None` so sampling degrades to tree (1) alone and records that fact in audit fields. This tree exists because client-side-decode contestant designs (wasm / WebCodecs) run their decoder inside Chrome processes that are NOT in the contestant's PGID subtree; without this union, such contestants register near-zero CPU and bypass the sub-score entirely.

The sampler SHALL by default identify and **exclude** the Chrome GPU process from the union. A PID is classified as the Chrome GPU process when `/proc/<pid>/cmdline` (read as raw bytes) contains either `--gpu-preferences=` or `--type=gpu-process` as a substring; substring containment is required because Chrome rewrites its `/proc/<pid>/cmdline` into a single space-separated string via `prctl(PR_SET_MM_*)`, so the naive `split(b"\x00")` + `startswith(...)` approach silently fails to detect any Chrome subprocess type. Excluded PIDs SHALL be permanently dropped from the baseline, deltas, and per-PID attribution. The Sampler SHALL accept an `exclude_chrome_gpu: bool` parameter defaulting to `True`; the boolean is recorded in `capture_meta.json.cpu.exclude_chrome_gpu` and the excluded PID list in `capture_meta.json.cpu.excluded_gpu_pids` so audit can verify the filter applied to a given run.

The sampler SHALL accumulate `(utime + stime)` jiffies across all included processes, treat read errors on vanished PIDs as zero-delta (not an error), and use the first observation of a newly appeared PID as its baseline so historical CPU is not retroactively charged.

The sampler SHALL compute `mean_percent = Σ Δjiffies / (Δwall_seconds · ncpu · CLK_TCK) · 100`, where `ncpu = os.cpu_count()` and `CLK_TCK = os.sysconf("SC_CLK_TCK")`. The normalization basis SHALL be the total of all cores (per-core saturation = 100% ÷ ncpu).

When sampling completes, `runner.py` SHALL write `<screenshots_dir>/capture_meta.json` containing at least: `profile` (the active profile name, e.g. `"4k"`), `capture_started_at_epoch`, `capture_ended_at_epoch`, and a `cpu` sub-object with `mean_percent`, `sample_count`, `sample_window_ms`, `ncpu`, `clk_tck`, `normalization` (constant string `"all_cores_total"`), `pgid`, `extra_root_pid`, `sample_hz_used`, `exclude_chrome_gpu`, `excluded_gpu_pids`, and `per_process_top`. When the sampler collected fewer than `scorer.CPU_MIN_SAMPLES` samples or failed to start, `cpu` SHALL be `null` in `capture_meta.json` and the scoring pipeline SHALL treat this as `gate_reason="sampler_no_data"`.

`analyzer.py` SHALL pass the `cpu` sub-object through to `<output>/4k_metrics.json` verbatim when `<screenshots>/capture_meta.json` exists, performing no CPU-related computation of its own. When the file is absent or `cpu` is null, the analyzer SHALL omit the `cpu` field from `4k_metrics.json` (rather than fabricating zero values).

The CPU measurement pipeline is host-native only. Container-based execution paths (`scripts/evaluator-host.sh` and the portable OCI bundle) cannot read the contestant's host `/proc` from inside the container's PID namespace; in those paths the scoring pipeline SHALL omit sampling and report `gate_reason="container_mode_unsupported"`, `points=0` in `score.json.cpu`.

#### Scenario: Sampler activates only on 4K with a PGID

- **WHEN** `runner.py --profile 4k --contestant-pgid <P>` is invoked and the contestant frontend reaches readiness
- **THEN** the runner starts the sampler thread immediately before the steady-state capture loop, stops it immediately after the loop ends, writes `<screenshots_dir>/capture_meta.json` with a non-null `cpu` block containing `mean_percent`, `sample_count >= CPU_MIN_SAMPLES`, `sample_window_ms` approximately equal to the capture duration, `normalization="all_cores_total"`, `pgid=<P>`, and `sample_hz_used` equal to the configured rate

#### Scenario: Sampler is a no-op for 2K

- **WHEN** `runner.py --profile 2k --contestant-pgid <P>` is invoked
- **THEN** no sampler thread is started, no `capture_meta.json` is written, and the 2K metrics output is identical to a run invoked without `--contestant-pgid`

#### Scenario: Missing PGID preserves backward compatibility

- **WHEN** `runner.py --profile 4k` is invoked without `--contestant-pgid`
- **THEN** no sampler thread is started, no `capture_meta.json` is written, `4k_metrics.json` contains no `cpu` field, and `score.json.cpu` reports `gate_reason="sampler_no_data"` with `points=0`

#### Scenario: PGID enumerates the full contestant subtree

- **WHEN** the contestant's `start.sh` forks additional processes (relay backend, decoder worker, browser tab) that inherit the session set by `setsid`
- **THEN** the sampler enumerates `/proc/[0-9]*/stat`, includes every process whose field-6 `session` matches the supplied PGID, and the reported `mean_percent` reflects CPU spent by the whole contestant subtree — not just the session leader

#### Scenario: Processes vanishing or appearing mid-capture do not corrupt the mean

- **WHEN** a contestant subprocess exits mid-capture, or a new subprocess is forked mid-capture
- **THEN** the vanished PID's last-observed jiffies remain in the running total without raising an error, the newly appeared PID's first observation establishes a baseline and only subsequent deltas are accumulated, and the final `mean_percent` is a valid normalized value (no negative numbers, no division by zero)

#### Scenario: Container path reports unsupported without crashing

- **WHEN** the evaluator is invoked via `scripts/evaluator-host.sh` (container path) and the container cannot enumerate the host contestant's PIDs
- **THEN** the pipeline completes without raising and writes `score.json` containing a `cpu` block with `points=0`, `gated=true`, `gate_reason="container_mode_unsupported"`, and `mean_percent=null`

#### Scenario: Sampler debug rate is honored

- **WHEN** `runner.py --profile 4k --contestant-pgid <P> --cpu-sample-hz 5.0` is invoked
- **THEN** the sampler ticks at approximately 5 Hz, `capture_meta.json.cpu.sample_hz_used` is `5.0`, and `score.json.cpu.thresholds_used.sample_hz` is `5.0`

#### Scenario: Sampler also follows the Playwright Chrome subtree

- **WHEN** a contestant fans out raw H.265 NALUs and delegates decoding to a wasm worker inside the Chrome page (no decode in the contestant's own session tree)
- **THEN** the sampler's union still picks up the renderer / browser / utility process CPU via `extra_root_pid` ppid descent, `capture_meta.json.cpu.extra_root_pid` records the driver PID it followed, and `mean_percent` reflects the contestant's effective rendering cost — not the near-zero value the contestant's own PGID would report in isolation

#### Scenario: Chrome GPU process is excluded by default

- **WHEN** sampling enumerates the Chrome subtree on a host without a real GPU pipeline (default `--ozone-platform=headless` run, SwiftShader doing software rasterisation)
- **THEN** the GPU process PID — identified via `--gpu-preferences=` / `--type=gpu-process` substring match against its `/proc/<pid>/cmdline` — is excluded from the baseline, deltas, and per-PID attribution; `capture_meta.json.cpu.exclude_chrome_gpu` is `true` and the filtered PID appears in `excluded_gpu_pids`; `mean_percent` reflects only renderer + browser + utility + contestant work, not SwiftShader software rasterisation noise

#### Scenario: Chrome cmdline-rewrite layout is handled

- **WHEN** the sampler reads `/proc/<pid>/cmdline` for a Chrome subprocess that has rewritten its argv into a single space-separated string via `prctl(PR_SET_MM_*)` (the common runtime layout)
- **THEN** the GPU detection function still matches `--gpu-preferences=` via substring containment and correctly classifies the subprocess; a naive `cmdline.split(b"\x00")[i].startswith(b"--gpu-preferences=")` would silently miss every such subprocess and is explicitly NOT used

#### Scenario: Playwright private-API failure degrades safely

- **WHEN** a Playwright SDK upgrade renames or removes the path `browser._impl_obj._connection._transport._proc`
- **THEN** `runner.py` catches `AttributeError`, passes `extra_root_pid=None` to the sampler, and writes `capture_meta.json.cpu.extra_root_pid = null` so the audit field surfaces the regression; the sampler degrades to the contestant-session-only tree rather than raising

### Requirement: Report Generation

`report.py` SHALL generate an internal-only `report.html` per run, containing: a top summary with `objective_total` and per-profile subtotals (`2k` and `4k`); a gallery of suspicious screenshots (watermark recognition failures, color block failures, samples with SSIM `< 0.7`); a frame-number-over-time chart for each profile; an SSIM histogram for each profile; capture-throughput diagnostics for each profile; and relative-path links to the raw artifacts. The report MUST NOT be exposed to contestants by default.

#### Scenario: Report includes audit evidence

- **WHEN** scoring completes
- **THEN** `report.html` opens in a browser, renders all charts and galleries from local files only (no network calls), shows capture sampling FPS and capture overrun diagnostics for each completed profile, and links to `2k_screenshots/`, `4k_screenshots/`, both metrics JSONs, and `score.json`

#### Scenario: Empty failure gallery

- **WHEN** a submission passes every check
- **THEN** the report still renders, with the suspicious-screenshot gallery showing zero entries and a clear "no failures" message

### Requirement: Orchestration and Cleanup

`scripts/evaluator.sh <team_id> <results_subdir>` SHALL execute the evaluator main body in this order: start MediaMTX via `scripts/start_rtsp.sh` listening on port `554`; health-check both `rtsp://127.0.0.1:554/test/h265_2560_1440` and `rtsp://127.0.0.1:554/test/h265_3840_2160` via `scripts/health_check.sh`; run the 2K capture (30s) and the 4K capture (30s) in fresh Playwright Chromium contexts via `runner.py` (passing `--contestant-pgid` only for the 4K invocation, per the Contestant CPU Usage Measurement requirement); analyze both screenshot directories with `analyzer.py`; score with `scorer.py` (passing `--metrics 2k=... --metrics 4k=...`) and generate `report.html` with `report.py`; terminate MediaMTX; print the final `score.json` to the original stdout. A cleanup function and shell traps MUST be defined before any step that could fail, so cleanup runs on any exit path. Host-side concerns (zip extraction, invocation of contestant `start.sh` / `stop.sh`, contestant process-group cleanup, single-instance lock, port `8080` / `554` precheck) are NOT this script's responsibility; they belong to `scripts/evaluator-host.sh` (target host) and `scripts/evaluator-local.sh` (build host shortcut), which invoke this script after the host environment is prepared. MediaMTX SHALL be a per-invocation process owned by this script and SHALL NOT be assumed to exist as a host-resident daemon shared across runs. The script SHALL iterate the profiles defined in `lib/profiles.py::PROFILES`; adding a new profile MUST NOT require new branches in this script.

#### Scenario: Successful in-body run

- **WHEN** invoked with a `team_id` and a `results_subdir` after the wrapping host script has confirmed that ports `554` and `8080` are free and the contestant frontend is ready
- **THEN** the run completes, `score.json` is printed to stdout, and all artifact files (`2k_screenshots/`, `4k_screenshots/`, `2k_metrics.json`, `4k_metrics.json`, `score.json`, `report.html`, `evaluator.log`) are present under the results directory

#### Scenario: Mid-run failure

- **WHEN** the contestant process crashes after 2K capture begins
- **THEN** the evaluator catches the failure via its trap, analyzes whatever screenshots were captured (counting missing or unrecognized frames as failures), still produces a `score.json` and `report.html` reflecting partial data, terminates MediaMTX, and exits with a code that allows the wrapping host script to perform port cleanup

#### Scenario: MediaMTX is owned by this script

- **WHEN** the script starts a run
- **THEN** it brings MediaMTX up and tears it down within its own lifetime; it MUST NOT assume a pre-existing host-resident MediaMTX

### Requirement: Result Artifacts

Each run SHALL produce, under `results/<team_id>_<timestamp>/`: `2k_screenshots/`, `4k_screenshots/`, `2k_metrics.json`, `4k_metrics.json`, `score.json`, `report.html`, and `evaluator.log`. The artifact set MUST be sufficient to reproduce or manually audit the score after the run finishes.

#### Scenario: Artifacts present on success

- **WHEN** a run completes successfully
- **THEN** all seven artifact entries above exist in the run directory

#### Scenario: Artifacts present on failure

- **WHEN** a run fails (e.g., startup timeout, RTSP infrastructure failure, missing `start.sh`)
- **THEN** at least `evaluator.log` and a `score.json` describing the failure are present, even if screenshot directories or metrics are empty

### Requirement: Anti-Cheating Behaviors

The evaluator SHALL detect or neutralize the cheating strategies listed below, and SHALL classify infrastructure problems separately from contestant failures.

#### Scenario: Static image playback

- **WHEN** a contestant renders one frozen image instead of decoding the stream
- **THEN** `unique_frame_count` collapses, `measured_fps` is near 0 for the affected profile, and FPS points are 0 for that profile

#### Scenario: I-frame-only or repeated-frame playback

- **WHEN** a contestant only displays I-frames or repeats a small set of frames
- **THEN** `unique_frame_count` and `measured_fps` reflect the reduction, scoring partial or zero FPS points

#### Scenario: Fake canvas overlay

- **WHEN** a contestant draws a watermark-like overlay but the actual decoded video is missing or wrong
- **THEN** DataMatrix recognition, the four color block checks, and SSIM cannot all pass together, so full correctness (5) per profile is unreachable

#### Scenario: Delayed or stale rendering

- **WHEN** the player shows old frames or stalls
- **THEN** `frame_numbers` in `timestamps.json` and the analyzer's frame-number-over-time series expose the gap, and the report flags the affected samples

#### Scenario: Missing `data-testid`

- **WHEN** the player element does not carry `data-testid="player-video"`
- **THEN** the round fails with a clear error and the evaluator does not silently capture the wrong element

#### Scenario: 2K-only or 4K-only decoder (resolution-floored cheat)

- **WHEN** a contestant only supports one resolution and renders the other as static / black / overlay
- **THEN** the unsupported profile collapses `unique_frame_count` and SSIM independently of the supported profile, scoring 0 on both correctness and FPS for the failing profile while the passing profile is unaffected; if 4K is the failing profile, the CPU gate trips and `cpu.gate_reason="4k_fps_below_threshold"`

### Requirement: Source-Built Dependencies via Submodules

All open-source C/C++/Go runtime dependencies SHALL be managed as git submodules under `third_party/<name>/` and built from source: at minimum `ffmpeg`, `mediamtx`, `tesseract`, `leptonica`, and `libdmtx`, plus `x264` and `x265` (ffmpeg's H.264/H.265 encoders). Each submodule SHALL be pinned to a specific upstream commit (never a branch tip). Build outputs SHALL install into `third_party/install/{bin,lib,include}`, and the evaluator runtime (`scripts/evaluator.sh`, `runner.py`, `analyzer.py`, `scripts/start_rtsp.sh`) SHALL prepend that prefix to `PATH`, `LD_LIBRARY_PATH`, and `PKG_CONFIG_PATH` (via `scripts/env.sh`) so it uses those binaries instead of anything in `/usr/bin` or `/usr/lib`. The evaluator MUST NOT depend on `apt`-installed copies of those libraries. Python packages remain pip-installed from pinned versions in `requirements.txt`, and the Playwright-bundled Chromium remains the canonical browser binary; both are documented exceptions.

#### Scenario: Submodules are pinned and audit-traceable

- **WHEN** an organizer runs `git submodule status` from the repo root
- **THEN** every entry under `third_party/` reports a fixed SHA, and `.gitmodules` records the upstream URL for each, so any audit can reproduce the exact toolchain used for a given score

#### Scenario: Evaluator uses source-built binaries

- **WHEN** the evaluator runs after a successful `build.sh`
- **THEN** `which ffmpeg`, `which mediamtx`, and `which tesseract` (executed inside the evaluator's environment) all resolve under `third_party/install/bin/`, not under `/usr/bin`

#### Scenario: Missing source build fails loudly

- **WHEN** `evaluator.sh` is invoked but `third_party/install/bin/ffmpeg` (or `mediamtx`) does not exist
- **THEN** the run aborts immediately with an explicit message directing the operator to run `build.sh`, and does NOT silently fall back to a system binary

### Requirement: Lifecycle Scripts

The evaluator workspace SHALL provide eight idempotent lifecycle scripts under `scripts/`, in addition to the per-submission `scripts/evaluator.sh`:

- `scripts/setup.sh` — bootstrap the build host: install the apt toolchain (`build-essential`, `cmake`, `autoconf`, `automake`, `libtool`, `pkg-config`, `nasm`, `yasm`, `golang-go`, `python3-venv`, `lsof`, `unzip`, `libcap2-bin`), create the Python virtualenv at `.venv/` at the repo root, and run `git submodule update --init --recursive`. Build-host-only.
- `scripts/build.sh` — build every submodule into `third_party/install/` in dependency order (leptonica before tesseract, then libdmtx, x264, x265, ffmpeg, mediamtx), `pip install -r requirements.txt` into the venv, `playwright install chromium` at the Playwright-pinned revision, and apply `sudo setcap cap_net_bind_service=+ep` to `third_party/install/bin/mediamtx` so the host-native MediaMTX can bind port `554`. A `--clean` flag SHALL force a from-scratch rebuild. The setcap step SHALL fail loudly (non-zero exit, explicit error message) when sudo is unavailable or the kernel does not support file capabilities; it MUST NOT silently fall back. Build-host-only.
- `scripts/deploy.sh` — bring the build host to a ready state for local development: invoke `scripts/prepare_streams.sh` if `streams/h265_*.mp4` or `reference/{2k,4k}/` are missing or older than `lib/watermark.py`. It SHALL NOT start MediaMTX or health-check RTSP — those are owned per-run by `scripts/evaluator.sh`. Operators who need RTSP up for ad-hoc `ffprobe` testing without invoking an evaluator run SHALL invoke `scripts/start_rtsp.sh` directly. NOT used on target hosts.
- `scripts/package.sh` — produce the portable bundle (`dist/`). See the `portable-bundle` capability spec for the normative pipeline.
- `scripts/evaluator-host.sh` — target host operator entry; see the `portable-bundle` capability spec for the normative contract.
- `scripts/evaluator-local.sh` — build host local-development shortcut sharing arguments and exit codes with `evaluator-host.sh` but invoking `scripts/evaluator.sh` natively (no docker). See the `portable-bundle` capability spec.
- `scripts/test.sh` — execute the automated validation suite against the bundled reference and negative-case submissions and exit non-zero if any expected score / failure-reason fails to match. The default mode SHALL auto-invoke `scripts/deploy.sh` if RTSP is not already up, drive cases through `scripts/evaluator-local.sh`, and register `scripts/teardown.sh` on EXIT/INT/TERM. A `--portable` mode SHALL additionally exercise `scripts/package.sh` and the full bundle path; see the `portable-bundle` capability spec.
- `scripts/teardown.sh` — opposite of `scripts/deploy.sh`: stop MediaMTX (via `rtsp_server/mediamtx.pid`, SIGTERM-then-SIGKILL) and free port `8080`. Does NOT touch any other port — internal contestant ports are the contestant's concern and are collected by `scripts/evaluator-host.sh`'s (or `scripts/evaluator-local.sh`'s) process-group kill.

Each script SHALL be safe to re-run, SHALL refuse to silently use system-wide tools when its own outputs exist, and SHALL print a clear final status line ("ready", "failed: <reason>", etc.).

#### Scenario: Cold-start to first local evaluation

- **WHEN** an organizer clones the repo onto a fresh Ubuntu 24.04 host and runs `setup.sh && build.sh && deploy.sh` in order
- **THEN** all three exit zero, `third_party/install/bin/{ffmpeg,mediamtx,tesseract}` exist and are executable, `getcap third_party/install/bin/mediamtx` reports `cap_net_bind_service+ep`, both watermarked MP4s (`h265_2560_1440.mp4` and `h265_3840_2160.mp4`) and the reference PNG sequences under `reference/2k/` and `reference/4k/` are present, no host-resident MediaMTX is running (deploy.sh does not start it), and `scripts/evaluator-local.sh` is ready to accept submissions (which will start its own MediaMTX per run)

#### Scenario: Cold-start to bundle production

- **WHEN** an organizer runs `scripts/package.sh` after a successful `setup.sh && build.sh && deploy.sh`
- **THEN** `dist/{evaluator-portable_<sha>.tar.zst, evaluator-host.sh, README.md, SHA256SUMS, manifest.json}` exist and `sha256sum -c dist/SHA256SUMS` exits zero

#### Scenario: Idempotent re-runs

- **WHEN** any of `setup.sh`, `build.sh`, `deploy.sh`, or `package.sh` is run a second time with no relevant inputs changed
- **THEN** it completes quickly (no rebuild of up-to-date submodules, no regeneration of existing streams, no docker rebuild of unchanged layers), exits zero, and leaves the workspace in the same ready state

#### Scenario: setcap failure aborts the build

- **WHEN** `scripts/build.sh` reaches the setcap step but sudo is unavailable, or the kernel lacks `CAP_NET_BIND_SERVICE` support
- **THEN** `build.sh` exits non-zero with an explicit message naming `mediamtx` and the missing capability; it MUST NOT proceed past this point or attempt to use a different RTSP port

#### Scenario: Automated regression test (default mode)

- **WHEN** an organizer runs `scripts/test.sh` after a successful `scripts/deploy.sh`
- **THEN** it invokes `scripts/evaluator-local.sh` against each `test_submissions/*.zip`, compares each resulting `score.json` to the expected outcome (with `max_score=30` and `2k`/`4k`/`cpu` block keys), prints per-case PASS/FAIL, and exits non-zero if any case fails

#### Scenario: Portable bundle regression test (`--portable` mode)

- **WHEN** an organizer runs `scripts/test.sh --portable` after a successful `scripts/package.sh` and with `EVAL_TARGET_HOST=user@host` pointing to a reachable second Ubuntu 24.04 host
- **THEN** it exercises `scripts/package.sh`, runs `scripts/evaluator-host.sh` against `reference.zip` on the target host, diffs the resulting `score.json` against an `evaluator-local.sh` run on the build host, asserts exit code 2 plus `reason="contestant_frontend_unavailable"` for a negative fixture, prints per-stage PASS/FAIL, and exits non-zero if any stage fails

#### Scenario: Self-contained test session

- **WHEN** an organizer runs `scripts/test.sh` on a freshly built host (no prior `scripts/deploy.sh`)
- **THEN** it auto-invokes `scripts/deploy.sh` when watermarked streams are missing (to generate them), runs all cases through `scripts/evaluator-local.sh` (each of which starts and tears down its own MediaMTX per run), and runs `scripts/teardown.sh` on exit (success, failure, or interrupt) as a backstop, leaving ports `554` and `8080` free

### Requirement: Host Toolchain Documentation

Evaluator setup documentation SHALL list the host toolchain required by `scripts/setup.sh` (`build-essential`, `cmake`, `autoconf`, `automake`, `libtool`, `pkg-config`, `nasm`, `yasm`, `golang-go`, `python3-venv`, `lsof`, `unzip`, `libcap2-bin`), the pinned Python packages in `requirements.txt` (`playwright>=1.40`, `pillow>=10.0`, `numpy`, `scikit-image`, `pylibdmtx`, `pytesseract`), the list of submoduled open-source projects under `third_party/`, the documented exception that Chromium is taken from the Playwright-bundled binary, and the requirement that the host kernel supports `CAP_NET_BIND_SERVICE` file capabilities for MediaMTX to bind port `554` natively.

#### Scenario: Fresh Ubuntu 24.04 setup

- **WHEN** an organizer follows the documented setup steps (`setup.sh` → `build.sh` → `deploy.sh`) on a fresh Ubuntu 24.04 host that has none of `ffmpeg`, `mediamtx`, `tesseract`, `leptonica`, or `libdmtx` pre-installed system-wide
- **THEN** the evaluator runs end-to-end against the reference submission without missing-dependency errors and produces a complete `score.json` and `report.html` with `max_score=30`
