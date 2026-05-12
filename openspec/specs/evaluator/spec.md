# evaluator

## Purpose

Host-side automated scorer for the 30-point objective portion of the web plugin-free real-time media player challenge. Drives a known-good RTSP stream into each contestant submission, captures playback through Playwright Chromium, recognizes watermarked reference frames (DataMatrix + color blocks + SSIM), and produces a deterministic `score.json` plus internal `report.html` per run that organizers can defend against appeals. Internal use only.

## Requirements

### Requirement: Workspace Layout

The evaluator's working tree IS the repository root (no `evaluator/` subdirectory). It SHALL contain at minimum: under `scripts/` — `setup.sh`, `build.sh`, `deploy.sh`, `test.sh`, `evaluator.sh`, `prepare_streams.sh`, `start_rtsp.sh`, `health_check.sh`, `build_test_zips.sh`, `env.sh`; at the root — `runner.py`, `analyzer.py`, `scorer.py`, `report.py`, `requirements.txt`; in `lib/` — `watermark.py`; in `rtsp_server/` — `mediamtx.yml`; plus `third_party/` (containing git submodules and, after `scripts/build.sh`, an `install/` prefix), `streams/`, `reference/h264/`, `reference/h265/`, `submissions/`, `results/`, and `test_submissions/`. All evaluator components MUST run natively on the host (no container required).

#### Scenario: Workspace exists after setup

- **WHEN** an organizer clones the repository and runs the documented setup steps
- **THEN** every path listed above exists, the shell scripts are executable, and `./scripts/evaluator.sh` (invoked with too few arguments) prints a usage message naming `<team_id>` and `<submission_zip>`

#### Scenario: Submissions and results are isolated per run

- **WHEN** the evaluator runs for team `T` at timestamp `TS`
- **THEN** all submission files for that run live under `submissions/T/` and all artifacts live under `results/T_TS/`, with no cross-contamination from prior runs

### Requirement: Reference Stream Generation

`prepare_streams.sh` together with `lib/watermark.py` SHALL produce two watermarked MP4 files and matching PNG reference frame sequences before the contest. H.264: `1920x1080` at `30fps`, `30s` duration, `libx264 crf 18 yuv420p`, written to `streams/h264_watermarked.mp4` with frames under `reference/h264/frame_NNNNN.png`. H.265: `2560x1440` at `25fps`, `30s` duration, `libx265 hvc1 yuv420p` at `4 Mbps`, written to `streams/h265_watermarked.mp4` with frames under `reference/h265/frame_NNNNN.png`.

#### Scenario: Watermark content per frame

- **WHEN** any reference frame `N` is generated
- **THEN** the frame contains a high-contrast 5-digit zero-padded frame number block in the top-left, a `HH:MM:SS.mmm` timecode in the top-right, four solid color blocks `(255,0,0)`, `(0,255,0)`, `(0,0,255)`, `(255,255,255)` along the bottom, and a DataMatrix code in the bottom-right encoding the integer `N`

#### Scenario: Idempotent regeneration

- **WHEN** `prepare_streams.sh` is rerun on a host where outputs already exist
- **THEN** it overwrites the MP4 files and the reference frame directories deterministically so two runs from the same code produce byte-identical PNGs and equivalent MP4s for the same codec settings

### Requirement: Local RTSP Server

`rtsp_server/start_server.sh` SHALL start MediaMTX listening on port `8554` with RTSP forced over TCP, exposing `rtsp://localhost:8554/test/h264` and `rtsp://localhost:8554/test/h265`. The server SHALL serve the pre-generated MP4 files via `ffmpeg -re -stream_loop -1 -c:v copy` so no re-encoding occurs at runtime, and the streams SHALL be available on demand for every evaluation round.

#### Scenario: RTSP health check before contestant deploy

- **WHEN** the evaluator finishes starting the RTSP server
- **THEN** `ffprobe -rtsp_transport tcp rtsp://localhost:8554/test/h264` returns metadata within a short timeout

#### Scenario: RTSP failure classified as infrastructure

- **WHEN** the RTSP server fails to come up or the H.264 health check fails
- **THEN** the evaluator stops the run, records the failure as an organizer-side infrastructure problem in `evaluator.log`, and does NOT charge the contestant with an objective score of zero for that reason

### Requirement: Contestant Runtime Contract

Each contestant submission zip SHALL, after extraction into `submissions/<team_id>/`, provide an executable `start.sh` that launches whatever processes the submission needs; it MAY provide a `stop.sh` for cleanup. The evaluator SHALL export `RTSP_SERVER_HOST=127.0.0.1`, `RTSP_SERVER_PORT=8554`, and `FRONTEND_PORT=8080` before invoking `start.sh`. Internal contestant processes (relay backends, decode workers, etc.) MAY bind any other local port; the evaluator neither prescribes nor cleans those — the contestant's process group is SIGKILLed as a whole at cleanup. The frontend SHALL expose route `/play` accepting `codec` (`h264` or `h265`) and `autoplay=1`, render the video into `[data-testid="player-video"]` at an actual rendered size of at least `1280x720` with proportional (uncropped) display, set `window.__PLAYER_READY__ = true` after the first frame is rendered, and assign a human-readable string to `window.__PLAYER_ERROR__` on playback failure.

The canonical evaluation host is Ubuntu 24.04 with Google Chrome (pinned version recorded in every `score.json` / `report.html` as `chromium_version`). The runtime contract is the same for both codecs — `[data-testid="player-video"]` with the readiness signals above — and the evaluator does NOT prescribe a rendering strategy. Whether to use `<video>`, `<canvas>` with WebCodecs, a WASM decoder, or any other approach is the contestant's choice; the evaluator only screenshots the element.

#### Scenario: Missing start.sh

- **WHEN** the extracted submission does not contain `start.sh`
- **THEN** the evaluator fails the submission with a clear `missing start.sh` error, records it in `evaluator.log`, and skips capture rounds

#### Scenario: Optional stop.sh

- **WHEN** the submission provides `stop.sh`
- **THEN** the evaluator invokes it during cleanup; **WHEN** it is absent **THEN** the evaluator proceeds with its own port/process cleanup without error

#### Scenario: Frontend never becomes ready

- **WHEN** `http://localhost:8080/play?codec=h264&autoplay=1` is not reachable within 60 seconds of `start.sh` returning
- **THEN** the evaluator assigns objective score 0 for the submission, records `startup timeout` in `evaluator.log`, and proceeds to cleanup

### Requirement: Playwright Capture Runner

`runner.py` SHALL accept `--codec`, `--output`, `--duration`, and `--fps`; launch headless Chromium with `--disable-dev-shm-usage`, `--no-sandbox`, and `--autoplay-policy=no-user-gesture-required`; use a fresh browser context per codec at viewport `1920x1080`; navigate to `http://localhost:8080/play?codec=<codec>&autoplay=1`; wait up to 15 seconds for `window.__PLAYER_READY__ === true`; capture element-only screenshots of `[data-testid="player-video"]` (not full-page) at 30 Hz for 30 seconds; write screenshots as `shot_NNNNN.png` and a `timestamps.json` containing per-shot capture timestamps and any browser page errors. The runner MUST NOT use `networkidle` as a readiness condition.

#### Scenario: Round succeeds

- **WHEN** the contestant frontend signals readiness within 15 seconds and the player element is present
- **THEN** the runner produces `--duration * --fps` (within tolerance) screenshots in `--output/` and a `timestamps.json` with monotonically non-decreasing timestamps

#### Scenario: Readiness timeout

- **WHEN** `window.__PLAYER_READY__` is not `true` within 15 seconds
- **THEN** the runner reads `window.__PLAYER_ERROR__` if present, writes the reason and any captured browser errors into `timestamps.json`, exits non-zero, and the evaluator fails that codec round

#### Scenario: Missing player element

- **WHEN** `[data-testid="player-video"]` cannot be located after readiness
- **THEN** the runner fails the round with a clear `missing data-testid` error captured in `timestamps.json` and the evaluator log

### Requirement: Frame Analysis

`analyzer.py` SHALL accept `--codec`, `--screenshots`, `--reference`, and `--output`; iterate screenshot files in lexical order; convert each screenshot to an RGB array; extract the frame number using DataMatrix first and OCR as a fallback; locate the corresponding `reference/<codec>/frame_NNNNN.png`; resize the screenshot to the reference size before SSIM comparison; validate the four bottom color blocks against the fixed RGB targets within a documented tolerance; and emit a metrics JSON. Frame matching MUST be keyed by the watermark frame number, not the screenshot index, so the analyzer remains correct when the stream starts mid-clip or crosses the MP4 loop boundary.

#### Scenario: Metrics schema

- **WHEN** analysis of a codec completes
- **THEN** `<output>/h264_metrics.json` (or `h265_metrics.json`) contains: `total_shots`, `watermark_recognized`, `color_blocks_passed`, `ssim_scores`, `frame_numbers`, `duration`, `unique_frame_count`, `measured_fps`, `watermark_recognition_rate`, `color_check_rate`, and `mean_ssim`

#### Scenario: Unique-frame FPS

- **WHEN** a contestant feeds a single frozen image instead of live video
- **THEN** `unique_frame_count` is at or near 1 and `measured_fps` is computed from unique watermark frame numbers divided by capture duration, surfacing the cheat

#### Scenario: Loop-crossing stream

- **WHEN** the contestant playback crosses the 30-second MP4 loop boundary mid-capture
- **THEN** every recognized frame is still matched to the correct reference PNG by frame number, and SSIM remains high

### Requirement: Scoring

`scorer.py` SHALL accept `--h264`, `--h265`, `--output`, and `--report`; score each codec independently for up to 15 points (10 correctness + 5 FPS); and produce a `score.json` containing per-codec details, `objective_total`, and `max_score` of `30`. Correctness: full 10 when `watermark_recognition_rate >= 0.95` AND `color_check_rate >= 0.95` AND `mean_ssim >= 0.90`; partial 5 when `watermark_recognition_rate >= 0.80` AND `mean_ssim >= 0.75`; otherwise 0. FPS: expected `30` for H.264 and `25` for H.265; full 5 when `|measured_fps - expected| <= 1.0`; partial 3 when `<= 3.0`; otherwise 0.

#### Scenario: Per-codec totals

- **WHEN** scoring completes for a submission
- **THEN** `score.json` includes an H.264 block, an H.265 block, an `objective_total` equal to the sum of both codec subtotals, and `max_score = 30`, with the underlying metrics (rates, mean SSIM, measured FPS) preserved for audit

#### Scenario: Static-frame submission

- **WHEN** a submission renders a single static image so `measured_fps` is near 0
- **THEN** it receives 0 FPS points for that codec while correctness is scored on its own merits

#### Scenario: Fake-overlay submission

- **WHEN** a submission draws a fake canvas overlay that visually resembles the watermark but fails DataMatrix, color block, or SSIM checks
- **THEN** it cannot reach full correctness (10) and may receive 5 or 0 depending on which checks it passes

### Requirement: Report Generation

`report.py` SHALL generate an internal-only `report.html` per run, containing: a top summary with `objective_total` and per-codec subtotals; a gallery of suspicious screenshots (watermark recognition failures, color block failures, samples with SSIM `< 0.7`); a frame-number-over-time chart for each codec; an SSIM histogram for each codec; and relative-path links to the raw artifacts. The report MUST NOT be exposed to contestants by default.

#### Scenario: Report includes audit evidence

- **WHEN** scoring completes
- **THEN** `report.html` opens in a browser, renders all charts and galleries from local files only (no network calls), and links to `h264_screenshots/`, `h265_screenshots/`, both metrics JSONs, and `score.json`

#### Scenario: Empty failure gallery

- **WHEN** a submission passes every check
- **THEN** the report still renders, with the suspicious-screenshot gallery showing zero entries and a clear "no failures" message

### Requirement: Orchestration and Cleanup

`evaluator.sh <team_id> <submission_zip>` SHALL execute the run in this order: create `results/<team_id>_<timestamp>/` and `submissions/<team_id>/`; start logging to `results/<team_id>_<timestamp>/evaluator.log`; clean stale processes on port `8080`; ensure the RTSP server is up and health-check it; extract the submission zip; `chmod +x` contestant shell scripts; export the runtime env vars; invoke `start.sh` via `setsid`; poll the H.264 autoplay URL for up to 60 seconds; run the H.264 capture (30s); run the H.265 capture (30s) in a fresh context; analyze both directories; score and generate `report.html`; invoke contestant `stop.sh` if present; SIGKILL the contestant process group; clean residual processes on port `8080`; and print the final `score.json` to stdout. A cleanup function and shell traps MUST be defined before any step that could fail, so cleanup runs on any exit path.

#### Scenario: Successful end-to-end run

- **WHEN** the contestant submission satisfies the runtime contract
- **THEN** the run completes, `score.json` is printed to stdout, all listed result artifacts are present under `results/<team_id>_<timestamp>/`, and port `8080` is free after the run

#### Scenario: Mid-run failure

- **WHEN** the contestant process crashes after H.264 capture begins
- **THEN** the evaluator catches the failure, runs cleanup via trap, analyzes whatever screenshots were captured (counting missing or unrecognized frames as failures), still produces a `score.json` and `report.html` reflecting partial data, and still releases all evaluator-owned ports

#### Scenario: Leftover processes from previous submission

- **WHEN** a previous run left a process bound to port `8080`
- **THEN** the next invocation kills that process during pre-run cleanup before invoking the new `start.sh`

### Requirement: Result Artifacts

Each run SHALL produce, under `results/<team_id>_<timestamp>/`: `h264_screenshots/`, `h265_screenshots/`, `h264_metrics.json`, `h265_metrics.json`, `score.json`, `report.html`, and `evaluator.log`. The artifact set MUST be sufficient to reproduce or manually audit the score after the run finishes.

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
- **THEN** `unique_frame_count` collapses, `measured_fps` is near 0, and FPS points are 0 for that codec

#### Scenario: I-frame-only or repeated-frame playback

- **WHEN** a contestant only displays I-frames or repeats a small set of frames
- **THEN** `unique_frame_count` and `measured_fps` reflect the reduction, scoring partial or zero FPS points

#### Scenario: Fake canvas overlay

- **WHEN** a contestant draws a watermark-like overlay but the actual decoded video is missing or wrong
- **THEN** DataMatrix recognition, the four color block checks, and SSIM cannot all pass together, so full correctness (10) is unreachable

#### Scenario: Delayed or stale rendering

- **WHEN** the player shows old frames or stalls
- **THEN** `frame_numbers` in `timestamps.json` and the analyzer's frame-number-over-time series expose the gap, and the report flags the affected samples

#### Scenario: Missing `data-testid`

- **WHEN** the player element does not carry `data-testid="player-video"`
- **THEN** the round fails with a clear error and the evaluator does not silently capture the wrong element

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

The evaluator workspace SHALL provide five idempotent lifecycle scripts under `scripts/`, in addition to the per-submission `scripts/evaluator.sh`:

- `scripts/setup.sh` — bootstrap the host: install the apt toolchain (`build-essential`, `cmake`, `autoconf`, `automake`, `libtool`, `pkg-config`, `nasm`, `yasm`, `golang-go`, `python3-venv`, `lsof`, `unzip`), create the Python virtualenv at `.venv/` at the repo root, and run `git submodule update --init --recursive`.
- `scripts/build.sh` — build every submodule into `third_party/install/` in dependency order (leptonica before tesseract, then libdmtx, x264, x265, ffmpeg, mediamtx), `pip install -r requirements.txt` into the venv, and `playwright install chromium` at the Playwright-pinned revision. A `--clean` flag SHALL force a from-scratch rebuild.
- `scripts/deploy.sh` — bring the evaluator to a ready state: invoke `scripts/prepare_streams.sh` if `streams/*.mp4` or `reference/<codec>/` are missing or older than `lib/watermark.py`; start MediaMTX via `scripts/start_rtsp.sh`; health-check both RTSP URLs via `ffprobe`.
- `scripts/test.sh` — execute the automated validation suite against the bundled reference and negative-case submissions and exit non-zero if any expected score / failure-reason fails to match. The script SHALL auto-invoke `scripts/deploy.sh` if RTSP is not already up, and SHALL register `scripts/teardown.sh` on its EXIT/INT/TERM trap so the host is left clean.
- `scripts/teardown.sh` — opposite of `scripts/deploy.sh`: stop MediaMTX (via `rtsp_server/mediamtx.pid`, SIGTERM-then-SIGKILL) and free port `8080`. Does NOT touch any other port — internal contestant ports are the contestant's concern and are collected by `scripts/evaluator.sh`'s process-group kill.

Each script SHALL be safe to re-run, SHALL refuse to silently use system-wide tools when its own outputs exist, and SHALL print a clear final status line ("ready", "failed: <reason>", etc.).

#### Scenario: Cold-start to first evaluation

- **WHEN** an organizer clones the repo onto a fresh Ubuntu 24.04 host and runs `setup.sh && build.sh && deploy.sh` in order
- **THEN** all three exit zero, `third_party/install/bin/{ffmpeg,mediamtx,tesseract}` exist and are executable, both watermarked MP4s and the reference PNG sequences are present, MediaMTX is listening on `:8554`, and `evaluator.sh` is ready to accept submissions

#### Scenario: Idempotent re-runs

- **WHEN** any of `setup.sh`, `build.sh`, or `deploy.sh` is run a second time with no relevant inputs changed
- **THEN** it completes quickly (no rebuild of up-to-date submodules, no regeneration of existing streams), exits zero, and leaves the workspace in the same ready state

#### Scenario: Automated regression test

- **WHEN** an organizer runs `scripts/test.sh` after a successful `scripts/deploy.sh`
- **THEN** it invokes `scripts/evaluator.sh` against each `test_submissions/*.zip`, compares each resulting `score.json` to the expected outcome, prints per-case PASS/FAIL, and exits non-zero if any case fails

#### Scenario: Self-contained test session

- **WHEN** an organizer runs `scripts/test.sh` on a freshly built host (no prior `scripts/deploy.sh`)
- **THEN** it brings RTSP up via `scripts/deploy.sh`, runs all cases, and tears down via `scripts/teardown.sh` on exit (success, failure, or interrupt), leaving port `8554` and port `8080` free

### Requirement: Host Toolchain Documentation

Evaluator setup documentation SHALL list the host toolchain required by `scripts/setup.sh` (`build-essential`, `cmake`, `autoconf`, `automake`, `libtool`, `pkg-config`, `nasm`, `yasm`, `golang-go`, `python3-venv`, `lsof`, `unzip`), the pinned Python packages in `requirements.txt` (`playwright>=1.40`, `pillow>=10.0`, `numpy`, `scikit-image`, `pylibdmtx`, `pytesseract`), the list of submoduled open-source projects under `third_party/`, and the documented exception that Chromium is taken from the Playwright-bundled binary.

#### Scenario: Fresh Ubuntu 24.04 setup

- **WHEN** an organizer follows the documented setup steps (`setup.sh` → `build.sh` → `deploy.sh`) on a fresh Ubuntu 24.04 host that has none of `ffmpeg`, `mediamtx`, `tesseract`, `leptonica`, or `libdmtx` pre-installed system-wide
- **THEN** the evaluator runs end-to-end against the reference submission without missing-dependency errors and produces a complete `score.json` and `report.html`
