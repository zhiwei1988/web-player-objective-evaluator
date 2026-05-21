## MODIFIED Requirements

### Requirement: Reference Stream Generation

`prepare_streams.sh` together with `lib/watermark.py` SHALL produce two watermarked MP4 files and matching PNG reference frame sequences before the contest, one per profile defined in `lib/profiles.py::PROFILES`. The **2K profile** SHALL produce `streams/h265_2560_1440.mp4` (`2560x1440`, `20fps`, `30s` duration, `libx265 hvc1 yuv420p` at `4 Mbps`, GOP `40`, `scenecut=0`) with frames under `reference/2k/frame_NNNNN.png`. The **4K profile** SHALL produce `streams/h265_3840_2160.mp4` (`3840x2160`, `20fps`, `30s` duration, `libx265 hvc1 yuv420p` at `8 Mbps`, GOP `40`, `scenecut=0`) with frames under `reference/4k/frame_NNNNN.png`. Each profile SHALL produce `fps * duration_s` reference PNG frames, i.e. `600` frames for the default 20fps/30s profiles. All ProfileSpec-derived parameters (resolution, fps, bitrate, duration, output paths) SHALL come from `PROFILES` rather than from script-local literals. `prepare_streams.sh` SHALL delete legacy `streams/h264_watermarked.mp4`, `streams/h265_watermarked.mp4`, `reference/h264/`, and `reference/h265/` when present, before regenerating, so the two layouts do not coexist.

#### Scenario: Watermark content per frame

- **WHEN** any reference frame `N` is generated for any profile
- **THEN** the frame contains a high-contrast 5-digit zero-padded frame number block in the top-left, a `HH:MM:SS.mmm` timecode in the top-right, four solid color blocks `(255,0,0)`, `(0,255,0)`, `(0,0,255)`, `(255,255,255)` along the bottom, and a DataMatrix code in the bottom-right encoding the integer `N`

#### Scenario: Idempotent regeneration

- **WHEN** `prepare_streams.sh` is rerun on a host where outputs already exist
- **THEN** it overwrites the MP4 files and the reference frame directories deterministically so two runs from the same code produce byte-identical PNGs and equivalent MP4s for the same profile settings

#### Scenario: Legacy assets removed

- **WHEN** `prepare_streams.sh` is run on a host where `streams/h264_watermarked.mp4`, `streams/h265_watermarked.mp4`, `reference/h264/`, or `reference/h265/` exists from a prior layout
- **THEN** those legacy paths are removed before new generation begins, leaving only the 2K and 4K profile outputs

#### Scenario: Profile frame count follows 20fps source

- **WHEN** streams are generated with the default `PROFILES`
- **THEN** `reference/2k/` and `reference/4k/` each contain 600 numbered frames, and both generated MP4 streams report `20fps`

### Requirement: Scoring

`scorer.py` SHALL accept one or more `--metrics PROFILE=PATH` arguments (one per profile, e.g. `--metrics 2k=results/.../2k_metrics.json --metrics 4k=results/.../4k_metrics.json`), `--output`, and `--report`; score each profile independently for up to 10 points (5 correctness + 5 FPS); compute an additional 0-10 point CPU sub-score based on contestant CPU usage measured during the **2K profile** round (see the Contestant CPU Usage Measurement requirement); and produce a `score.json` containing one block per profile (keys `2k`, `4k`), a top-level `cpu` block, `objective_total`, and `max_score` of `30`.

**Correctness** (rescaled): full 5 when `watermark_recognition_rate >= 0.95` AND `color_check_rate >= 0.95` AND `mean_ssim >= 0.90`; partial 2 when `watermark_recognition_rate >= 0.80` AND `mean_ssim >= 0.75`; otherwise 0. This applies to both 2K and 4K. 4K correctness SHALL remain scored even when 4K FPS is low.

**2K FPS** (threshold based): expected FPS SHALL come from `PROFILES["2k"].fps` and default to `20`. Full 5 when `measured_fps / expected_fps >= FPS_FULL_RATIO_BY_PROFILE["2k"]`; partial 3 when `measured_fps / expected_fps >= FPS_PARTIAL_RATIO_BY_PROFILE["2k"]`; otherwise 0. The default values SHALL remain `FPS_FULL_RATIO_BY_PROFILE["2k"] = 0.85` and `FPS_PARTIAL_RATIO_BY_PROFILE["2k"] = 0.50`. The 2K block in `score.json` SHALL include `fps_full_threshold_used`, `fps_partial_threshold_used`, and `expected_fps`.

**4K FPS** (linear absolute score): expected FPS SHALL come from `PROFILES["4k"].fps` and default to `20`. `score.json.4k.fps_points` SHALL equal `round(min(max(measured_fps, 0) / expected_fps, 1.0) * 5, 2)`. A 4K run measured at `4fps` against the default `20fps` expected value SHALL earn `1.0` FPS point. The 4K block in `score.json` SHALL include `fps_scoring_mode = "linear_absolute"`, `fps_linear_full_score = 5`, and `expected_fps`. 4K threshold-band fields MAY be omitted or set to `null`, but the report MUST make the linear formula clear.

**CPU** (gating on 2K): `scorer.score_cpu(mean_cpu_percent, measured_cpu_profile_fps, expected_cpu_profile_fps)` SHALL return an integer in `[0, 10]` together with a nullable `gate_reason` string, evaluated in this order:

1. Gate: if `measured_cpu_profile_fps / expected_cpu_profile_fps < CPU_GATE_FPS_RATIO` (default `0.65`), return `(0, "2k_fps_below_threshold")`. A submission that does not reach the minimum 2K playback throughput cannot earn any CPU points.
2. If `mean_cpu_percent` is unavailable (analyzer omitted the field, or `capture_meta.json.cpu` was null), return `(0, "sampler_no_data")`.
3. If `mean_cpu_percent <= CPU_FULL_THRESHOLD_PERCENT` (default `5.0`), return `(10, None)`.
4. If `mean_cpu_percent > CPU_ZERO_THRESHOLD_PERCENT` (default `20.0`), return `(0, None)`.
5. Otherwise return `(round((CPU_ZERO_THRESHOLD_PERCENT - mean_cpu_percent) / (CPU_ZERO_THRESHOLD_PERCENT - CPU_PARTIAL_START_PERCENT) * 10), None)`, clamped to `[0, 10]`. `CPU_PARTIAL_START_PERCENT` defaults to `6.0`.

The CPU block SHALL include `measured_on_profile = "2k"`, `gate_profile = "2k"`, `expected_fps`, `measured_fps`, `thresholds_used`, `mean_percent`, `sample_count`, and the existing audit fields. When the 2K round fails entirely (no `2k_metrics.json` produced, or it lacks the fields the gate inspects), `scorer.py` SHALL still emit a complete `score.json` with `cpu.points=0`, `cpu.gated=true`, `cpu.gate_reason="2k_round_failed"`, and `cpu.mean_percent=null`. When the host-side wrapper fails before the evaluator main body runs, the existing failure `score.json` path SHALL include `cpu.gated=true`, `cpu.gate_reason="host_failure"`, `cpu.points=0`.

`objective_total` SHALL equal `2k.total + 4k.total + cpu.points` and SHALL NOT exceed `max_score` (`30`). Because 4K FPS points can be fractional, `objective_total` MAY be fractional and SHALL be rounded consistently for JSON/report display.

#### Scenario: Per-profile totals

- **WHEN** scoring completes for a submission
- **THEN** `score.json` includes a `2k` block, a `4k` block, a top-level `cpu` block, an `objective_total` equal to `2k.total + 4k.total + cpu.points`, and `max_score = 30`, with the underlying metrics (rates, mean SSIM, measured FPS, CPU mean percent, sample count, thresholds/formulas applied) preserved for audit; the keys `h264` and `h265` SHALL NOT appear

#### Scenario: 2K FPS full credit uses 20fps expected value

- **WHEN** a submission's 2K round produces `measured_fps / expected_fps >= 0.85` (e.g. `17.2` fps against expected `20` fps = `86%`)
- **THEN** `score.json.2k.fps_points = 5`, `score.json.2k.expected_fps = 20`, `score.json.2k.fps_full_threshold_used = 0.85`, and `score.json.2k.fps_partial_threshold_used = 0.50`

#### Scenario: 2K FPS in the partial band receives 3 points

- **WHEN** a submission's 2K round produces `0.50 <= measured_fps / expected_fps < 0.85` (e.g. `12` fps / `20` fps = `60%`)
- **THEN** `score.json.2k.fps_points = 3`, `score.json.2k.expected_fps = 20`, `score.json.2k.fps_full_threshold_used = 0.85`, and `score.json.2k.fps_partial_threshold_used = 0.50`

#### Scenario: 2K FPS below the partial floor receives 0

- **WHEN** a submission's 2K round produces `measured_fps / expected_fps < 0.50` (e.g. `8` fps / `20` fps = `40%`)
- **THEN** `score.json.2k.fps_points = 0`, `score.json.2k.expected_fps = 20`, and `score.json.2k.fps_partial_threshold_used = 0.50`

#### Scenario: 4K correctness is still scored

- **WHEN** the 4K round produces low FPS but passes watermark, color block, and SSIM thresholds
- **THEN** the 4K block can still receive 5 correctness points, and only the 4K FPS points are reduced by the linear formula

#### Scenario: 4K FPS is scored linearly by absolute FPS

- **WHEN** a submission's 4K round produces `measured_fps = 4.0` and `expected_fps = 20`
- **THEN** `score.json.4k.fps_points = 1.0`, `score.json.4k.fps_scoring_mode = "linear_absolute"`, `score.json.4k.expected_fps = 20`, and `score.json.4k.fps_linear_full_score = 5`

#### Scenario: 4K FPS linear score is capped

- **WHEN** a submission's 4K round produces `measured_fps >= 20`
- **THEN** `score.json.4k.fps_points = 5.0` and the value does not exceed 5 even if measured FPS is higher than the source FPS

#### Scenario: CPU gate trips when 2K does not meet the throughput gate

- **WHEN** the 2K round produces `measured_fps / expected_fps = 0.50` and `CPU_GATE_FPS_RATIO = 0.65`
- **THEN** `score.json.cpu.points = 0`, `score.json.cpu.gated = true`, `score.json.cpu.gate_reason = "2k_fps_below_threshold"`, `score.json.cpu.measured_on_profile = "2k"`, and the measured `mean_cpu_percent` is diagnostic only

#### Scenario: CPU full marks at low usage

- **WHEN** the 2K round produces `measured_fps / expected_fps >= CPU_GATE_FPS_RATIO` and `mean_cpu_percent <= 5.0`
- **THEN** `cpu.points = 10`, `cpu.gated = false`, `cpu.gate_reason = null`, `cpu.measured_on_profile = "2k"`, and `cpu.thresholds_used` records every threshold that produced this outcome

#### Scenario: CPU partial credit in the proportional band

- **WHEN** the 2K round produces `measured_fps / expected_fps >= CPU_GATE_FPS_RATIO` and `mean_cpu_percent` falls within `(5.0, 20.0]`
- **THEN** `cpu.points = round((20 - mean_cpu_percent) / 14 * 10)` clamped to `[0, 10]`, `cpu.gated = false`, and the formula matches the published score table at the integer percent breakpoints (`6->10`, `7->9`, `10->7`, `13->5`, `15->4`, `17->2`, `19->1`, `20->0`)

#### Scenario: CPU gated when sampler produced no data

- **WHEN** the 2K capture round ran but `capture_meta.json` is missing OR `capture_meta.json.cpu` is null OR fewer than `CPU_MIN_SAMPLES` samples were collected
- **THEN** `cpu.points = 0`, `cpu.gated = true`, `cpu.gate_reason = "sampler_no_data"`, `cpu.mean_percent = null`, and `cpu.measured_on_profile = "2k"`

#### Scenario: CPU gated when sampled round itself failed

- **WHEN** no `2k_metrics.json` was produced
- **THEN** `score.json` is still emitted with `cpu.points = 0`, `cpu.gated = true`, `cpu.gate_reason = "2k_round_failed"`, and `cpu.mean_percent = null`, alongside whatever `2k.reason` the runner recorded

#### Scenario: Thresholds-used audit field

- **WHEN** any `score.json` is produced
- **THEN** `score.json.cpu.thresholds_used` contains exactly the six keys `gate_fps_ratio`, `full_percent`, `partial_start_percent`, `zero_percent`, `min_samples`, `sample_hz`, each set to the value actually applied to that run, so an organizer can replay the score from the metrics alone

### Requirement: Contestant CPU Usage Measurement

During the **2K profile** capture round, `runner.py` SHALL sample the contestant process group's CPU usage so that `scorer.py` can compute the CPU sub-score documented in the Scoring requirement. The sampler MUST run inside the same Python process as the Playwright capture loop (no sidecar daemon), MUST be active only while the steady-state capture loop is running (excluding `start.sh` warm-up, the readiness wait, and post-capture cleanup), and MUST use only the Python standard library (no new package dependency).

`runner.py` SHALL accept an optional `--contestant-pgid INT` argument. When provided AND `--profile` resolves to a `ProfileSpec` whose `cpu_sampled` flag is `True` (true for `2k` only, by default), the runner SHALL start a daemon sampler thread before the capture loop begins and stop it immediately after the loop ends. When `--contestant-pgid` is absent OR the active profile's `cpu_sampled` flag is `False`, the runner SHALL behave exactly as before sampling was introduced (no sampling, no `capture_meta.json` written), preserving backward compatibility for direct `_cli` invocations and the 4K round.

`runner.py` SHALL ALSO accept an optional `--cpu-sample-hz FLOAT` (debug-only; scheduled for retirement once a default is calibrated). When absent, the sampler SHALL use `_cpu_sampler.DEFAULT_SAMPLE_HZ`.

The sampler SHALL enumerate `/proc/[0-9]*/stat` on each tick and include any process belonging to either the contestant session tree or the Playwright Chrome process tree, with PID deduplication so a process matching both is counted once. The Chrome process tree SHALL be rooted at the Playwright driver PID discovered from `browser._impl_obj._connection._transport._proc.pid` when available, and SHALL degrade to contestant-session-only sampling when that private API path is unavailable. The sampler SHALL by default exclude the Chrome GPU process using the existing `--gpu-preferences=` / `--type=gpu-process` cmdline substring detection and SHALL record the excluded PIDs in `capture_meta.json`.

The sampler SHALL accumulate `(utime + stime)` jiffies across all included processes, treat read errors on vanished PIDs as zero-delta, and use the first observation of a newly appeared PID as its baseline. It SHALL compute `mean_percent = sum(delta_jiffies) / (delta_wall_seconds * ncpu * CLK_TCK) * 100`, where `ncpu = os.cpu_count()` and `CLK_TCK = os.sysconf("SC_CLK_TCK")`.

When sampling completes, `runner.py` SHALL write `<screenshots_dir>/capture_meta.json` containing at least: `profile` (the active profile name, `"2k"`), `capture_started_at_epoch`, `capture_ended_at_epoch`, and a `cpu` sub-object with `mean_percent`, `sample_count`, `sample_window_ms`, `ncpu`, `clk_tck`, `normalization` (constant string `"all_cores_total"`), `pgid`, `extra_root_pid`, `sample_hz_used`, `exclude_chrome_gpu`, `excluded_gpu_pids`, and `per_process_top`. When the sampler collected fewer than `scorer.CPU_MIN_SAMPLES` samples or failed to start, `cpu` SHALL be `null` in `capture_meta.json` and the scoring pipeline SHALL treat this as `gate_reason="sampler_no_data"`.

`analyzer.py` SHALL pass the `cpu` sub-object through to `<output>/2k_metrics.json` verbatim when `<screenshots>/capture_meta.json` exists, performing no CPU-related computation of its own. When the file is absent or `cpu` is null, the analyzer SHALL omit the `cpu` field from `2k_metrics.json` rather than fabricating zero values.

#### Scenario: Sampler activates only on 2K with a PGID

- **WHEN** `runner.py --profile 2k --contestant-pgid <P>` is invoked and the contestant frontend reaches readiness
- **THEN** the runner starts the sampler thread immediately before the steady-state capture loop, stops it immediately after the loop ends, writes `<screenshots_dir>/capture_meta.json` with `profile = "2k"` and a non-null `cpu` block containing `mean_percent`, `sample_count >= CPU_MIN_SAMPLES`, `sample_window_ms` approximately equal to the capture duration, `normalization="all_cores_total"`, `pgid=<P>`, and `sample_hz_used` equal to the configured rate

#### Scenario: Sampler is a no-op for 4K

- **WHEN** `runner.py --profile 4k --contestant-pgid <P>` is invoked
- **THEN** no sampler thread is started, no `capture_meta.json` is written, and the 4K metrics output contains no `cpu` field

#### Scenario: Missing PGID preserves backward compatibility

- **WHEN** `runner.py --profile 2k` is invoked without `--contestant-pgid`
- **THEN** no sampler thread is started, no `capture_meta.json` is written, `2k_metrics.json` contains no `cpu` field, and `score.json.cpu` reports `gate_reason="sampler_no_data"` with `points=0`

#### Scenario: Sampler also follows the Playwright Chrome subtree

- **WHEN** a contestant fans out raw H.265 NALUs and delegates decoding to a wasm worker inside the Chrome page
- **THEN** the sampler's union still picks up the renderer / browser / utility process CPU via `extra_root_pid` ppid descent, `capture_meta.json.cpu.extra_root_pid` records the driver PID it followed, and `mean_percent` reflects the contestant's effective rendering cost

#### Scenario: Chrome GPU process is excluded by default

- **WHEN** sampling enumerates the Chrome subtree on a host without a real GPU pipeline
- **THEN** the GPU process PID is excluded from the baseline, deltas, and per-PID attribution; `capture_meta.json.cpu.exclude_chrome_gpu` is `true` and the filtered PID appears in `excluded_gpu_pids`

### Requirement: Orchestration and Cleanup

`scripts/evaluator.sh <team_id> <submission_zip>` SHALL execute the in-body pipeline portion of the evaluator (after host-side preparation succeeds per Requirement `Evaluator Entry Script`) in this order: start MediaMTX via `scripts/start_rtsp.sh` listening on port `554`; health-check both `rtsp://127.0.0.1:554/test/h265_2560_1440` and `rtsp://127.0.0.1:554/test/h265_3840_2160` via `scripts/health_check.sh`; run the 2K capture (30s) and the 4K capture (30s) in fresh Playwright Chromium contexts via `runner.py`, passing `--contestant-pgid` only for the profile whose `ProfileSpec.cpu_sampled` flag is true (2K by default); analyze both screenshot directories with `analyzer.py`; score with `scorer.py` (passing `--metrics 2k=... --metrics 4k=...`) and generate `report.html` with `report.py`; terminate MediaMTX; print the final `score.json` to the original stdout. A cleanup function and shell traps MUST be defined before any step that could fail, so cleanup runs on any exit path. Host-side concerns (zip extraction, invocation of contestant `start.sh` / `stop.sh`, contestant process-group cleanup, single-instance lock, port `8080` precheck) are owned by this same script under Requirement `Evaluator Entry Script`; the in-body pipeline described here SHALL run only after that preparation has succeeded. There SHALL NOT exist any separate wrapper script (`evaluator-host.sh`, `evaluator-local.sh`, or otherwise) that invokes this script; the script is the single entry. MediaMTX SHALL be a per-invocation process owned by this script and SHALL NOT be assumed to exist as a host-resident daemon shared across runs. The script SHALL iterate the profiles defined in `lib/profiles.py::PROFILES`; adding a new profile MUST NOT require new branches in this script.

#### Scenario: Successful in-body run

- **WHEN** the host-side preparation phase of `scripts/evaluator.sh` has confirmed that port `8080` is free, extracted the submission zip, started the contestant `start.sh`, and observed the contestant frontend become reachable
- **THEN** the in-body pipeline completes, `score.json` is printed to stdout, and all artifact files (`2k_screenshots/`, `4k_screenshots/`, `2k_metrics.json`, `4k_metrics.json`, `score.json`, `report.html`, `evaluator.log`) are present under the results directory

#### Scenario: CPU PGID follows the sampled profile

- **WHEN** `scripts/evaluator.sh` invokes `runner.py` for the default 2K and 4K profiles
- **THEN** it passes `--contestant-pgid` to the 2K invocation and does not pass it to the 4K invocation, because `PROFILES["2k"].cpu_sampled = True` and `PROFILES["4k"].cpu_sampled = False`

#### Scenario: MediaMTX is owned by this script

- **WHEN** the script starts a run
- **THEN** it brings MediaMTX up and tears it down within its own lifetime; it MUST NOT assume a pre-existing host-resident MediaMTX

### Requirement: Report Generation

`report.py` SHALL generate an internal-only `report.html` per run, containing: a top summary with `objective_total` and per-profile subtotals (`2k` and `4k`); a gallery of suspicious screenshots (watermark recognition failures, color block failures, samples with SSIM `< 0.7`); a frame-number-over-time chart for each profile; an SSIM histogram for each profile; capture-throughput diagnostics for each profile; CPU scoring details identifying that CPU was measured on the 2K profile; the 4K linear absolute FPS formula and applied expected FPS; and relative-path links to the raw artifacts. The report MUST NOT be exposed to contestants by default.

#### Scenario: Report includes audit evidence

- **WHEN** scoring completes
- **THEN** `report.html` opens in a browser, renders all charts and galleries from local files only (no network calls), shows capture sampling FPS and capture overrun diagnostics for each completed profile, shows CPU measured on profile `2k`, shows the 4K linear FPS formula, and links to `2k_screenshots/`, `4k_screenshots/`, both metrics JSONs, and `score.json`

#### Scenario: Empty failure gallery

- **WHEN** a submission passes every check
- **THEN** the report still renders, with the suspicious-screenshot gallery showing zero entries and a clear "no failures" message

### Requirement: Anti-Cheating Behaviors

The evaluator SHALL detect or neutralize the cheating strategies listed below, and SHALL classify infrastructure problems separately from contestant failures.

#### Scenario: Static image playback

- **WHEN** a contestant renders one frozen image instead of decoding the stream
- **THEN** `unique_frame_count` collapses, `measured_fps` is near 0 for the affected profile, and FPS points are 0 for 2K while 4K receives only the linear FPS value implied by its near-zero measured FPS

#### Scenario: I-frame-only or repeated-frame playback

- **WHEN** a contestant only displays I-frames or repeats a small set of frames
- **THEN** `unique_frame_count` and `measured_fps` reflect the reduction, scoring partial or zero FPS points according to the 2K threshold policy and the 4K linear FPS policy

#### Scenario: Fake canvas overlay

- **WHEN** a contestant draws a watermark-like overlay but the actual decoded video is missing or wrong
- **THEN** DataMatrix recognition, the four color block checks, and SSIM cannot all pass together, so full correctness (5) per profile is unreachable

#### Scenario: Delayed or stale rendering

- **WHEN** the player shows old frames or stalls
- **THEN** `frame_numbers` in `timestamps.json` and the analyzer's frame-number-over-time series expose the gap, and the report flags the affected samples

#### Scenario: Missing `data-testid`

- **WHEN** the player element does not carry `data-testid="player-video"`
- **THEN** the round fails with a clear error and the evaluator does not silently capture the wrong element

#### Scenario: 2K-only or 4K-only decoder

- **WHEN** a contestant only supports one resolution and renders the other as static / black / overlay
- **THEN** the unsupported profile collapses `unique_frame_count` and SSIM independently of the supported profile, scoring low or zero on correctness/FPS for the failing profile while the passing profile is unaffected; if 2K is the failing profile, the CPU gate trips and `cpu.gate_reason="2k_fps_below_threshold"`
