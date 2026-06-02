# evaluator

## Purpose

Host-side automated scorer for the 30-point objective portion of the web plugin-free real-time media player challenge. Drives a known-good RTSP stream into each contestant submission, captures playback through Playwright Chromium, recognizes watermarked reference frames (DataMatrix + color blocks + SSIM), and produces a deterministic `score.json` plus internal `report.html` per run that organizers can defend against appeals. The score breakdown is 10 points for 2K (5 correctness + 5 FPS), 15 points for 4K (5 correctness + 10 FPS), plus a 5-point CPU sub-score derived from the contestant process tree's CPU usage sampled during the 2K capture round. Internal use only.
## Requirements
### Requirement: Workspace Layout

The evaluator's working tree IS the repository root (no `evaluator/` subdirectory). It SHALL contain at minimum: under `scripts/` — `setup.sh`, `build.sh`, `deploy.sh`, `test.sh`, `evaluator.sh`, `prepare_streams.sh`, `start_rtsp.sh`, `health_check.sh`, `build_test_zips.sh`, `env.sh`, `teardown.sh`, `_contestant_lifecycle.sh`; at the root — `runner.py`, `analyzer.py`, `scorer.py`, `report.py`, `requirements.txt`; in `lib/` — `watermark.py` and `profiles.py`; in `rtsp_server/` — `mediamtx.yml`; plus `third_party/` (containing git submodules and, after `scripts/build.sh`, an `install/` prefix), `streams/`, `reference/2k/`, `reference/4k/`, `results/`, `test_submissions/`. There SHALL NOT exist any of `scripts/evaluator-host.sh`, `scripts/evaluator-local.sh`, `scripts/package.sh`, `Dockerfile`, `.dockerignore`, `.playwright/`, or `dist/`. The evaluator main body (`scripts/evaluator.sh` + `runner.py` + `analyzer.py` + `scorer.py` + `report.py`) runs natively on the build host; `scripts/evaluator.sh` is the single operator-facing entry. Evaluation host = build host; there is no separate "target host" workflow.

#### Scenario: Workspace exists after setup

- **WHEN** an organizer clones the repository and runs the documented setup steps
- **THEN** every path listed above exists, the shell scripts are executable, `lib/profiles.py` exposes a `PROFILES` mapping with keys `"2k"` and `"4k"`, and `./scripts/evaluator.sh` (invoked with too few arguments) prints a usage message naming `<team_id>` and `<submission_zip>`

#### Scenario: Submission workspace is the zip directory and results are isolated per run

- **WHEN** the evaluator runs for team `T` at timestamp `TS` with submission zip `/uploads/T/submission.zip`
- **THEN** extracted submission files for that run live under `/uploads/T/`
- **THEN** all evaluator artifacts live under `results/T_TS/`
- **THEN** the evaluator does not create or require `submissions/T/` as a staging directory

#### Scenario: Legacy docker / bundle artifacts absent

- **WHEN** an organizer checks the repository working tree against the workspace layout
- **THEN** none of `Dockerfile`, `.dockerignore`, `scripts/package.sh`, `scripts/evaluator-host.sh`, `scripts/evaluator-local.sh`, `.playwright/`, or `dist/` exist; `.gitignore` does not name `dist/` or `.playwright/` (those entries are removed because the produced artifacts no longer exist)

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

### Requirement: Local RTSP Server

`scripts/start_rtsp.sh` SHALL start MediaMTX listening on TCP port `554` with RTSP forced over TCP, exposing `rtsp://127.0.0.1:554/test/h265_2560_1440` and `rtsp://127.0.0.1:554/test/h265_3840_2160`. The server SHALL serve the pre-generated MP4 files via `ffmpeg -re -stream_loop -1 -c:v copy` so no re-encoding occurs at runtime, and the streams SHALL be available on demand for every evaluation round. Because port `554` is below `1024`, the MediaMTX binary MUST hold `CAP_NET_BIND_SERVICE`: `scripts/build.sh` SHALL apply `setcap cap_net_bind_service=+ep` to `third_party/install/bin/mediamtx`. The evaluator MUST NOT silently fall back to a higher port when binding fails.

#### Scenario: RTSP health check before contestant deploy

- **WHEN** the evaluator finishes starting the RTSP server
- **THEN** `ffprobe -rtsp_transport tcp rtsp://127.0.0.1:554/test/h265_2560_1440` AND `ffprobe -rtsp_transport tcp rtsp://127.0.0.1:554/test/h265_3840_2160` each return metadata within a short timeout

#### Scenario: RTSP failure classified as infrastructure

- **WHEN** the RTSP server fails to come up, the 2K health check fails, or the 4K health check fails
- **THEN** the evaluator stops the run, records the failure as an organizer-side infrastructure problem in `evaluator.log`, and does NOT charge the contestant with an objective score of zero for that reason

#### Scenario: MediaMTX has CAP_NET_BIND_SERVICE

- **WHEN** MediaMTX is started after `scripts/build.sh` has applied `setcap cap_net_bind_service=+ep`
- **THEN** the process binds `0.0.0.0:554` without `EACCES` and the health check scenarios above pass; if the capability is missing, the bind fails loudly and the evaluator exits with an infrastructure error rather than retrying on a different port

### Requirement: Contestant Runtime Contract

Each contestant submission zip SHALL, after extraction into `dirname <submission_zip>`, provide an executable `start.sh` that launches whatever processes the submission needs; it MAY provide a `stop.sh` for cleanup. The evaluator SHALL export `RTSP_SERVER_HOST=127.0.0.1`, `RTSP_SERVER_PORT=554`, and `FRONTEND_PORT=8080` before invoking `start.sh`. Internal contestant processes (relay/transmux backends, demuxers, etc.) MAY bind any other local port; the evaluator neither prescribes nor cleans those — the contestant's process group is SIGKILLed as a whole at cleanup. The frontend SHALL expose route `/play` accepting `profile` (`2k` or `4k`) and `autoplay=1`, render the video into `[data-testid="player-video"]` at an actual rendered size of at least `1280x720` with proportional (uncropped) display, set `window.__PLAYER_READY__ = true` after the first frame is rendered, and assign a human-readable string to `window.__PLAYER_ERROR__` on playback failure. The RTSP source URLs that the contestant SHALL pull from are fixed: `rtsp://127.0.0.1:554/test/h265_2560_1440` for `profile=2k` and `rtsp://127.0.0.1:554/test/h265_3840_2160` for `profile=4k`.

The H.265 elementary stream SHALL be decoded **in the browser**. A contestant MAY relay, recontainerize, or transmux the HEVC stream server-side (e.g. RTSP→fMP4 keeping `hvc1`, with no re-encode) but SHALL NOT transcode it to another codec, nor decode it server-side and push decoded pixels (raw frames / images) to the page. The evaluator forensically inspects the codec of the stream entering the browser's decode path (see the Decode-Path Forensics requirement); a stream that is not H.265 at that boundary is a violation and zeros that profile's score.

The canonical evaluation host is Ubuntu 24.04 with Google Chrome (pinned version recorded in every `score.json` / `report.html` as `chromium_version`). The runtime contract is the same for both profiles — `[data-testid="player-video"]` with the readiness signals above. The evaluator does NOT prescribe a *rendering* strategy (`<canvas>`, `<video>`, OffscreenCanvas, WebGL, etc. are all the contestant's choice) provided the **decode** of H.265 happens in the browser. On the canonical host the browser cannot decode HEVC natively, so in practice the viable in-browser path is a contestant-supplied WASM (or, where supported, WebCodecs) HEVC decoder.

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

#### Scenario: In-browser H.265 decode is required

- **WHEN** a submission delivers the H.265 elementary stream into the page and decodes it in the browser (WASM/WebCodecs), rendering into `[data-testid="player-video"]`
- **THEN** the submission satisfies the contract and is scored on its measured metrics; **WHEN** instead the submission delivers a non-H.265 stream (server-side transcode or server-side decode) to the browser **THEN** the affected profile is a Decode-Path Forensics violation and scores 0 on correctness and FPS

### Requirement: Evaluator Entry Script

`scripts/evaluator.sh <team_id> <submission_zip>` SHALL be the single operator-facing entry for evaluating a contestant submission. The script SHALL execute, in order: acquire `flock -n /var/tmp/evaluator.lock` (exit `75 / EX_TEMPFAIL` if already held, printing the holding PID; MUST NOT block-wait or queue); keep that flock owned only by the evaluator parent process and MUST NOT allow the lock file descriptor to be inherited by contestant code, RTSP server processes, ffmpeg descendants, Playwright/Chromium descendants, analyzer/scorer helpers, or other child processes; compute `ts=$(date +%Y%m%d_%H%M%S)`; create `results/<team_id>_<ts>/`; precheck that TCP port `8080` is free (the script MUST NOT precheck `554` because it starts MediaMTX itself per run via `scripts/start_rtsp.sh`, reusing any already-bound instance); extract the submission zip non-interactively into `dirname <submission_zip>`, overwriting prior extracted files when present, and lift a single top-level directory if present; grant executable permission to all files under `dirname <submission_zip>` after extraction and lifting; export `RTSP_SERVER_HOST=127.0.0.1`, `RTSP_SERVER_PORT=554`, `FRONTEND_PORT=8080`; invoke `start.sh` from `dirname <submission_zip>` via `setsid` and record the process-group id; poll `curl -fsS 'http://127.0.0.1:8080/play?profile=2k&autoplay=1'` for up to 60 seconds; on success, drive the capture / analyze / score pipeline (per `Playwright Capture Runner`, `Frame Analysis`, `Scoring` Requirements) against each profile in `lib/profiles.py::PROFILES`; on completion, invoke contestant `stop.sh` if present (timeout 10s), `kill -- -<pgid>` the contestant process group, free port `8080` as belt-and-braces, tear down MediaMTX, release the flock. A `trap cleanup EXIT INT TERM` MUST be installed before the contestant `start.sh` is invoked. The script SHALL print the contents of `score.json` to stdout on completion. There SHALL NOT be any separate `scripts/evaluator-host.sh` or `scripts/evaluator-local.sh` wrapper; legacy filenames MUST NOT exist in the workspace.

Exit codes: `0` on a normal scoring run (regardless of contestant score), `2` when the contestant frontend never becomes ready (see Requirement `Failure Score on Contestant Unavailable`), `75 / EX_TEMPFAIL` on lock contention, `1` on any other startup failure (missing submission zip, port `8080` occupied, missing `start.sh`, etc.).

#### Scenario: Successful end-to-end run

- **WHEN** an organizer runs `scripts/evaluator.sh team_ref /uploads/team_ref/reference.zip` on a build-and-deploy-ready host with port `8080` free and a contestant zip that satisfies the contestant runtime contract
- **THEN** the script exits `0`, `results/team_ref_<ts>/score.json` exists and contains a valid `objective_total` with `max_score=30` and per-profile `2k` / `4k` / `cpu` blocks, port `8080` is free after the run, and the contents of `score.json` were printed to stdout
- **THEN** the contestant `start.sh` was invoked from `/uploads/team_ref/`

#### Scenario: Concurrent invocation refused

- **WHEN** one `scripts/evaluator.sh` invocation is mid-run and a second invocation starts on the same host
- **THEN** the second invocation exits `75` immediately and prints the first invocation's PID; the first invocation is not disturbed

#### Scenario: Lock auto-released on signal

- **WHEN** an `scripts/evaluator.sh` invocation is killed with SIGTERM during the contestant startup poll
- **THEN** the flock on `/var/tmp/evaluator.lock` is released by the kernel, and a fresh invocation can proceed immediately

#### Scenario: Lock not inherited by surviving contestant descendants

- **WHEN** an evaluator invocation starts contestant code that spawns a descendant process which survives after the evaluator parent exits
- **THEN** the surviving descendant does not hold `/var/tmp/evaluator.lock`
- **THEN** a fresh evaluator invocation can acquire the flock immediately

#### Scenario: Lock not inherited by evaluator helper descendants

- **WHEN** an evaluator invocation starts RTSP, capture, browser, analysis, or scoring helpers that spawn descendants which survive after the evaluator parent exits
- **THEN** those surviving descendants do not hold `/var/tmp/evaluator.lock`
- **THEN** a fresh evaluator invocation can acquire the flock immediately

#### Scenario: Port 8080 occupied at start

- **WHEN** another process is already bound to port `8080` at script start
- **THEN** the script exits `1` before extracting the submission zip and prints the conflicting PID; it does NOT kill the conflicting process automatically

#### Scenario: Legacy wrapper names removed

- **WHEN** any organizer or automated tool searches the repository for `scripts/evaluator-host.sh` or `scripts/evaluator-local.sh`
- **THEN** neither file exists; `scripts/evaluator.sh` is the only entry under `scripts/` that accepts `<team_id> <submission_zip>` arguments

#### Scenario: Extracted files are executable

- **WHEN** a submission zip contains non-executable regular files, including `start.sh`, `stop.sh`, helper scripts, or binaries
- **THEN** after extraction and any single-top-level-directory lifting, all files under `dirname <submission_zip>` have executable permission

#### Scenario: Repeated extraction is non-interactive

- **WHEN** an organizer evaluates a submission zip whose directory already contains files from a prior extraction
- **THEN** extraction overwrites those files without prompting on stdin
- **THEN** the evaluator proceeds to the normal lifecycle or contestant failure path without hanging on an `unzip` replacement question

### Requirement: Failure Score on Contestant Unavailable

When `scripts/evaluator.sh` polls the contestant readiness URL and the contestant frontend does not become reachable within 60 seconds, the script SHALL invoke `scorer.py --failure-reason contestant_frontend_unavailable --output <RUN_DIR>/score.json --report <RUN_DIR>/report.html` (with whatever additional flags scorer.py needs to honor the standard schema) so that `scorer.py` itself writes a `score.json` and `report.html` whose schema is identical to a normal scoring run (no shell-side `jq` or heredoc JSON construction). The script SHALL then exit with code `2`. Contestant `stop.sh` invocation and process-group cleanup MUST still execute before exit. The same path SHALL be used whenever the readiness poll fails for any reason attributable to the contestant frontend (missing `start.sh`, `start.sh` exiting non-zero, `__PLAYER_ERROR__` set with no recovery, etc.); other infrastructure failures (RTSP unavailable, MediaMTX bind failure) SHALL NOT use this code path.

#### Scenario: Frontend never becomes ready writes failure score

- **WHEN** the contestant `start.sh` runs but `http://127.0.0.1:8080/play?profile=2k&autoplay=1` is unreachable for 60 seconds
- **THEN** `scripts/evaluator.sh` exits `2`, `results/<team_id>_<ts>/score.json` exists with `objective_total = 0`, `max_score = 30`, and `reason = "contestant_frontend_unavailable"`, `report.html` exists with the same schema as a normal run, and ports `8080` / `554` are free after cleanup

#### Scenario: Missing start.sh writes failure score

- **WHEN** the extracted submission lacks `start.sh`
- **THEN** `scripts/evaluator.sh` exits `2` (NOT `1`, because the contestant—not the operator or infrastructure—is the cause), `score.json` contains `reason = "contestant_frontend_unavailable"` (or a more specific contestant-side reason if the implementation distinguishes them), and the failure path goes through `scorer.py`, not shell-assembled JSON

#### Scenario: Score JSON schema parity with success path

- **WHEN** a reviewer diffs the failure-mode `score.json` against a normal-run `score.json` (with `--ignore-numeric-values`)
- **THEN** the top-level key set is identical (no missing or extra keys on the failure path), including `objective_total`, `max_score`, per-profile blocks, `chromium_version`, and any other run-metadata fields the success path emits

### Requirement: Contestant Memory Limit

The evaluator SHALL enforce a hard memory limit on each contestant submission process tree. The default effective limit SHALL be `10G`; operators MAY override it with `EVALUATOR_CONTESTANT_MEMORY_MAX`, but the evaluator MUST NOT silently run a contestant submission without an effective hard memory limit on the canonical Ubuntu 24.04 host.

The limit SHALL apply to contestant `start.sh` and all descendant processes, including relay servers, transmuxers, decoders, Node/Python helpers, and any child process forked by the submission. The limit SHALL NOT apply to evaluator-owned processes such as MediaMTX, Playwright/Chromium, `runner.py`, `analyzer.py`, `scorer.py`, `report.py`, or cleanup helpers.

The evaluator SHALL use a cgroup/systemd memory boundary for enforcement. A per-process `ulimit` alone SHALL NOT satisfy this requirement.

#### Scenario: Default memory limit is applied

- **WHEN** `scripts/evaluator.sh` starts a contestant submission and `EVALUATOR_CONTESTANT_MEMORY_MAX` is not set
- **THEN** contestant `start.sh` and its descendants run under an effective hard memory limit of `10G`
- **THEN** the run artifacts record the effective contestant memory limit

#### Scenario: Environment override is applied

- **WHEN** an organizer runs `scripts/evaluator.sh` with `EVALUATOR_CONTESTANT_MEMORY_MAX=512M`
- **THEN** contestant `start.sh` and its descendants run under an effective hard memory limit of `512M`
- **THEN** the run artifacts record `512M` as the effective contestant memory limit

#### Scenario: Limit covers forked descendants

- **WHEN** contestant `start.sh` forks child worker processes that together allocate more than the effective memory limit
- **THEN** the contestant cgroup is stopped or killed without exhausting host memory
- **THEN** evaluator-owned processes remain outside that contestant memory limit and continue to cleanup and publish artifacts

#### Scenario: Evaluator helpers are not memory limited by contestant policy

- **WHEN** capture, analysis, scoring, report generation, MediaMTX, or Playwright/Chromium run during an evaluation
- **THEN** those evaluator-owned processes do not execute inside the contestant memory-limited cgroup

### Requirement: Contestant Memory Limiter Preflight

Before starting contestant code, the evaluator SHALL verify that the host can enforce the contestant memory limit. On the canonical Ubuntu 24.04 host, this preflight SHALL require cgroup v2 and a systemd transient unit mechanism capable of applying `MemoryMax` for the evaluator user. If the preflight fails, the evaluator SHALL fail loudly as an infrastructure failure before invoking contestant `start.sh`.

#### Scenario: Supported host passes preflight

- **WHEN** the evaluator user can create a transient systemd unit with `MemoryAccounting=yes`, `MemoryMax=<effective limit>`, `MemorySwapMax=0`, and `KillMode=control-group`
- **THEN** the evaluator proceeds to extract and start the contestant submission under that limit

#### Scenario: Unsupported host fails before contestant starts

- **WHEN** cgroup v2 is unavailable, `systemd-run --user` is unavailable, or a transient unit cannot apply `MemoryMax`
- **THEN** `scripts/evaluator.sh` exits with an infrastructure failure before invoking contestant `start.sh`
- **THEN** the evaluator does not run the submission without a hard memory limit

#### Scenario: Preflight result is logged

- **WHEN** the memory-limiter preflight passes or fails
- **THEN** `evaluator.log` records the effective memory limit and enough preflight detail for an organizer to diagnose host support

### Requirement: Contestant Memory Limit Failure Classification

When a contestant submission exceeds the effective memory limit, the evaluator SHALL classify the outcome as a contestant-side failure with reason `contestant_memory_limit_exceeded`. The evaluator SHALL still run normal cleanup, release the evaluator lock, stop the contestant unit or cgroup, free port `8080`, and publish schema-compatible `score.json`, `report.html`, and `result.info` artifacts whenever the scoring path can run.

Contestant-facing feedback for this failure SHALL be bounded and explicit, including the effective memory limit. The feedback SHALL be eligible for `result.info` because the failure is attributable to the contestant submission.

#### Scenario: Memory limit breach writes failure score

- **WHEN** contestant code exceeds the effective memory limit before or during evaluation
- **THEN** `score.json` exists with `objective_total = 0`, `max_score = 30`, and `reason = "contestant_memory_limit_exceeded"`
- **THEN** `score.json.contestant_feedback` includes a bounded message naming the effective memory limit
- **THEN** `report.html` and `result.info` are produced from the same score path

#### Scenario: Memory limit breach does not leave stale contestant processes

- **WHEN** contestant code is stopped because it exceeded the effective memory limit
- **THEN** cleanup stops the contestant cgroup or transient unit, kills the recorded contestant process group as a fallback, and frees port `8080`
- **THEN** a later evaluator invocation can acquire `/var/tmp/evaluator.lock` and start normally

#### Scenario: Memory limit breach during capture is preferred over generic timeout

- **WHEN** a capture round fails or times out after the contestant unit reports an OOM or memory-limit stop
- **THEN** the evaluator records `contestant_memory_limit_exceeded` rather than only a generic startup or capture timeout reason

### Requirement: Playwright Capture Runner

`runner.py` SHALL accept `--profile` (one of the keys in `lib/profiles.py::PROFILES`, i.e. `2k` or `4k`), `--output`, `--duration`, and `--fps`; launch headless Chromium with `--disable-dev-shm-usage`, `--no-sandbox`, `--autoplay-policy=no-user-gesture-required`, and `--enable-features=PlatformHEVCDecoderSupport`; use a fresh browser context per profile at viewport `1280x720`; navigate to `http://localhost:8080/play?profile=<profile>&autoplay=1`; wait up to 15 seconds for `window.__PLAYER_READY__ === true`; capture element-only screenshots of `[data-testid="player-video"]` (not full-page) at the requested rate for the requested duration; write screenshots as `shot_NNNNN.jpg` and a `timestamps.json` containing per-shot capture timestamps, target capture parameters, capture strategy metadata, and any browser page errors. The runner MUST NOT use `networkidle` as a readiness condition.

Before navigation, the runner SHALL inject decode-path forensic instrumentation (`add_init_script`) and SHALL open a CDP `Media` session for the page, and at capture end SHALL write a `decode_forensics.json` artifact alongside the screenshots recording the per-profile `decode_path_verdict` and its evidence (see the Decode-Path Forensics requirement). Forensic collection SHALL be best-effort and SHALL NOT cause the round to fail.

The runner SHALL precompute the element clip once after readiness and MUST verify that the clip is at least `1280x720`. The default capture strategy SHALL remain Playwright page screenshots with `clip`, `type="jpeg"`, and a documented JPEG quality. The runner MAY expose a non-default Chrome DevTools Protocol screenshot strategy for benchmarking or operator tuning, provided the selected strategy is recorded in `timestamps.json` and preserves element-only clipping.

#### Scenario: Round succeeds

- **WHEN** the contestant frontend signals readiness within 15 seconds and the player element is present with a clip of at least `1280x720`
- **THEN** the runner produces approximately `--duration × --fps` screenshots in `--output/`, a `timestamps.json` with monotonically non-decreasing timestamps, the requested `target_fps`, the requested `target_duration_s`, and the selected capture strategy, and a `decode_forensics.json` recording the profile's `decode_path_verdict`

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

#### Scenario: Forensic collection is non-fatal

- **WHEN** opening the CDP `Media` session or injecting the forensic instrumentation raises an error
- **THEN** the runner still completes the capture and writes screenshots, and `decode_forensics.json` records `verdict = "inconclusive"` rather than failing the round

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

`scorer.py` SHALL accept one or more `--metrics PROFILE=PATH` arguments (one per profile, e.g. `--metrics 2k=results/.../2k_metrics.json --metrics 4k=results/.../4k_metrics.json`), `--output`, and `--report`; score 2K for up to 10 points (5 correctness + 5 FPS), score 4K for up to 15 points (5 correctness + 10 FPS), compute an additional 0-5 point CPU sub-score based on contestant CPU usage measured during the **2K profile** round (see the Contestant CPU Usage Measurement requirement); and produce a `score.json` containing one block per profile (keys `2k`, `4k`), a top-level `cpu` block, a top-level `gate` block, `objective_total`, and `max_score` of `30`.

**Decode-path gate** (first, per profile, UNCHANGED): when the profile's `metrics.decode_forensics.verdict == "violation"`, that profile's `correctness_points` AND `fps_points` SHALL both be `0` regardless of measured rates or FPS, and the profile block SHALL include a `decode_path` sub-block (`verdict`, `checks`, `evidence`). When the violating profile is `2k`, the CPU block SHALL additionally be set to `points=0, gated=true, gate_reason="decode_path_violation"`. A verdict of `ok`, `inconclusive`, or an absent `decode_forensics` block SHALL NOT affect scoring (fail-open); an `inconclusive` verdict on any profile SHALL set top-level `score.json.review_required = true`.

**Correctness** (per profile): full 5 when `watermark_recognition_rate >= 0.95` AND `color_check_rate >= 0.95` AND `mean_ssim >= 0.90`; partial 2 when `watermark_recognition_rate >= 0.80` AND `mean_ssim >= 0.75`; otherwise 0. This applies to both 2K and 4K. 4K correctness SHALL remain scored even when 4K FPS is low — EXCEPT when that profile's decode-path verdict is `violation`.

**2K FPS** (threshold based): expected FPS SHALL come from `PROFILES["2k"].fps` and default to `20`. Full 5 when `measured_fps / expected_fps >= FPS_FULL_RATIO_BY_PROFILE["2k"]`; partial 3 at/above `FPS_PARTIAL_RATIO_BY_PROFILE["2k"]`; otherwise 0. Defaults `0.85` / `0.50`. The 2K block SHALL record `fps_full_threshold_used`, `fps_partial_threshold_used`, and `expected_fps`.

**4K FPS** (linear absolute): expected FPS SHALL come from `PROFILES["4k"].fps` and default to `20`. `score.json.4k.fps_points` SHALL equal `round(min(max(measured_fps, 0) / expected_fps, 1.0) * 10, 2)`. The 4K block SHALL record `fps_scoring_mode = "linear_absolute"`, `fps_linear_full_score = 10`, and `expected_fps`.

**CPU**: `scorer.score_cpu(...)` SHALL return an integer in `[0, 5]` with a nullable `gate_reason`, computed exactly as before (fps floor → `sampler_no_data` → full/zero/partial bands). The CPU block SHALL record `measured_on_profile = "2k"`, `gate_profile = "2k"`, `expected_fps`, `measured_fps`, `thresholds_used`, `mean_percent`, `sample_count`, and the existing audit fields.

**Level-0 gate conditions the total** (see the Level-0 Gate (Decode Correctness) requirement). The gate passes iff `2k.correctness_points == 5` AND `4k.correctness_points == 5`.

- **When the gate PASSES**, each profile's `total` SHALL equal `correctness_points + fps_points`, the CPU sub-score SHALL be scored normally, and `objective_total` SHALL equal `2k.total + 4k.total + cpu.points` (the full 30-point scheme).
- **When the gate FAILS**, level-1 SHALL NOT contribute: each profile's `total` SHALL equal its `correctness_points` alone (FPS still computed and recorded under `fps_points`, but excluded from `total`), the CPU block SHALL be set to `points=0, gated=true, gate_reason="gate_failed"` (UNLESS a more specific reason already applies — `decode_path_violation`, `2k_fps_below_threshold`, `sampler_no_data`, `2k_round_failed`, `container_mode_unsupported`, `host_failure` — which is retained), and `objective_total` SHALL equal `2k.correctness_points + 4k.correctness_points`.

`max_score` SHALL remain `30`. Because reaching `30` requires both full correctness (gate pass) and full level-1 performance, decode correctness alone caps the total at `≤ 10` ("level-0 has no full marks"). `objective_total` MAY be fractional (4K FPS is fractional) and SHALL be rounded consistently for display. No profile block SHALL carry `reason = "skipped_gate_failed"` (both profiles are always captured per the Evaluator Entry Script requirement).

#### Scenario: Per-profile totals and the gate block

- **WHEN** scoring completes for a submission
- **THEN** `score.json` includes a `2k` block, a `4k` block, a top-level `cpu` block, a top-level `gate` block, `max_score = 30`, and an `objective_total`; the underlying metrics (rates, mean SSIM, measured FPS, `fps_points`, CPU mean percent, sample count, thresholds/formulas) are preserved for audit; the keys `h264` / `h265` SHALL NOT appear

#### Scenario: Perfect run scores the full 30

- **WHEN** both profiles reach full correctness and full FPS and CPU is at full marks (`2k`: 5 + 5, `4k`: 5 + 10, `cpu`: 5)
- **THEN** `score.json.gate.passed = true`, `score.json.2k.total = 10`, `score.json.4k.total = 15`, `score.json.cpu.points = 5`, and `objective_total = 30`

#### Scenario: Gate passes but performance is poor

- **WHEN** both profiles reach full correctness (`gate.passed = true`) but `2k.fps_points = 0`, `4k.fps_points = 0`, and CPU scores `0`
- **THEN** `score.json.2k.total = 5`, `score.json.4k.total = 5`, `objective_total = 10`, and the FPS/CPU values are recorded — the gate is open, the level-1 points were simply not earned

#### Scenario: Gate fails — only correctness counts

- **WHEN** `2k.correctness_points = 5` but `4k.correctness_points = 2` (so the gate fails)
- **THEN** `score.json.gate.passed = false`, `score.json.2k.total = 5`, `score.json.4k.total = 2`, the `fps_points` fields are still recorded but excluded from the totals, `score.json.cpu.points = 0` with `cpu.gated = true` and `cpu.gate_reason = "gate_failed"`, and `objective_total = 7`

#### Scenario: Decode-path violation fails the gate and keeps decode-path precedence

- **WHEN** the 4K round's `decode_forensics.verdict == "violation"` (so `4k.correctness_points = 0` and the gate fails)
- **THEN** `objective_total = 2k.correctness_points` (4K contributes 0), level-1 is not scored, and a 2K `violation` (if present) keeps `cpu.gate_reason = "decode_path_violation"` rather than `"gate_failed"`

#### Scenario: OK or inconclusive verdict does not change scoring

- **WHEN** a profile's decode-path verdict is `ok`, `inconclusive`, or absent
- **THEN** that profile is scored on its measured metrics exactly as without forensics; an `inconclusive` verdict additionally sets `score.json.review_required = true`

#### Scenario: Fake-overlay submission

- **WHEN** a submission draws a fake canvas overlay that fails DataMatrix, color block, or SSIM checks
- **THEN** it cannot reach full correctness (5) on that profile, the gate fails, and level-1 is not scored

#### Scenario: Missing profile entry fails loudly

- **WHEN** `scorer.score_fps` is invoked with a profile name absent from `FPS_FULL_RATIO_BY_PROFILE` or `FPS_PARTIAL_RATIO_BY_PROFILE`
- **THEN** `score_fps` raises a clear error (e.g. `KeyError`) rather than silently falling back to a default ratio

### Requirement: Contestant CPU Usage Measurement

During the **2K profile** capture round, `runner.py` SHALL sample the contestant process group's CPU usage so that `scorer.py` can compute the CPU sub-score documented in the Scoring requirement. The sampler MUST run inside the same Python process as the Playwright capture loop (no sidecar daemon), MUST be active only while the steady-state capture loop is running (excluding `start.sh` warm-up, the readiness wait, and post-capture cleanup), and MUST use only the Python standard library (no new package dependency).

`runner.py` SHALL accept an optional `--contestant-pgid INT` argument. When provided AND `--profile` resolves to a `ProfileSpec` whose `cpu_sampled` flag is `True` (true for `2k` only, by default), the runner SHALL start a daemon sampler thread before the capture loop begins and stop it immediately after the loop ends. When `--contestant-pgid` is absent OR the active profile's `cpu_sampled` flag is `False`, the runner SHALL behave exactly as before sampling was introduced (no sampling, no `capture_meta.json` written), preserving backward compatibility for direct `_cli` invocations and the 4K round.

`runner.py` SHALL ALSO accept an optional `--cpu-sample-hz FLOAT` (debug-only; scheduled for retirement once a default is calibrated). When absent, the sampler SHALL use `_cpu_sampler.DEFAULT_SAMPLE_HZ`.

The sampler SHALL enumerate `/proc/[0-9]*/stat` on each tick and include any process belonging to **either** of two trees, with PID deduplication so a process matching both is counted once:

  1. **Contestant session tree** — every PID whose stat field-6 (`session`) equals the supplied PGID. This works because `scripts/_contestant_lifecycle.sh::clx_start_contestant` launches the contestant under `setsid`, so the session leader's PID equals the process group ID. Catches any server-side worker the contestant forks under its own session.

  2. **Playwright Chrome process tree** (optional) — every PID reachable from `extra_root_pid` via stat field-4 (`ppid`) descent, inclusive of the root. `runner.py` SHALL pluck `extra_root_pid` from Playwright's private API path `browser._impl_obj._connection._transport._proc.pid` after `chromium.launch(...)` and pass it to the sampler. On `AttributeError` (e.g. Playwright SDK bump changes the internal path) the runner SHALL fall back to `extra_root_pid=None` so sampling degrades to tree (1) alone and records that fact in audit fields. This tree exists because client-side-decode contestant designs (wasm / WebCodecs) run their decoder inside Chrome processes that are NOT in the contestant's PGID subtree; without this union, such contestants register near-zero CPU and bypass the sub-score entirely.

The sampler SHALL by default identify and **exclude** the Chrome GPU process from the union. A PID is classified as the Chrome GPU process when `/proc/<pid>/cmdline` (read as raw bytes) contains either `--gpu-preferences=` or `--type=gpu-process` as a substring; substring containment is required because Chrome rewrites its `/proc/<pid>/cmdline` into a single space-separated string via `prctl(PR_SET_MM_*)`, so the naive `split(b"\x00")` + `startswith(...)` approach silently fails to detect any Chrome subprocess type. Excluded PIDs SHALL be permanently dropped from the baseline, deltas, and per-PID attribution. The Sampler SHALL accept an `exclude_chrome_gpu: bool` parameter defaulting to `True`; the boolean is recorded in `capture_meta.json.cpu.exclude_chrome_gpu` and the excluded PID list in `capture_meta.json.cpu.excluded_gpu_pids` so audit can verify the filter applied to a given run.

The sampler SHALL accumulate `(utime + stime)` jiffies across all included processes, treat read errors on vanished PIDs as zero-delta (not an error), and use the first observation of a newly appeared PID as its baseline so historical CPU is not retroactively charged.

The sampler SHALL compute `mean_percent = Σ Δjiffies / (Δwall_seconds · ncpu · CLK_TCK) · 100`, where `ncpu = os.cpu_count()` and `CLK_TCK = os.sysconf("SC_CLK_TCK")`. The normalization basis SHALL be the total of all cores (per-core saturation = 100% ÷ ncpu).

When sampling completes, `runner.py` SHALL write `<screenshots_dir>/capture_meta.json` containing at least: `profile` (the active profile name, `"2k"`), `capture_started_at_epoch`, `capture_ended_at_epoch`, and a `cpu` sub-object with `mean_percent`, `sample_count`, `sample_window_ms`, `ncpu`, `clk_tck`, `normalization` (constant string `"all_cores_total"`), `pgid`, `extra_root_pid`, `sample_hz_used`, `exclude_chrome_gpu`, `excluded_gpu_pids`, and `per_process_top`. When the sampler collected fewer than `scorer.CPU_MIN_SAMPLES` samples or failed to start, `cpu` SHALL be `null` in `capture_meta.json` and the scoring pipeline SHALL treat this as `gate_reason="sampler_no_data"`.

`analyzer.py` SHALL pass the `cpu` sub-object through to `<output>/2k_metrics.json` verbatim when `<screenshots>/capture_meta.json` exists, performing no CPU-related computation of its own. When the file is absent or `cpu` is null, the analyzer SHALL omit the `cpu` field from `2k_metrics.json` (rather than fabricating zero values).

#### Scenario: Sampler activates only on 2K with a PGID

- **WHEN** `runner.py --profile 2k --contestant-pgid <P>` is invoked and the contestant frontend reaches readiness
- **THEN** the runner starts the sampler thread immediately before the steady-state capture loop, stops it immediately after the loop ends, writes `<screenshots_dir>/capture_meta.json` with `profile = "2k"` and a non-null `cpu` block containing `mean_percent`, `sample_count >= CPU_MIN_SAMPLES`, `sample_window_ms` approximately equal to the capture duration, `normalization="all_cores_total"`, `pgid=<P>`, and `sample_hz_used` equal to the configured rate

#### Scenario: Sampler is a no-op for 4K

- **WHEN** `runner.py --profile 4k --contestant-pgid <P>` is invoked
- **THEN** no sampler thread is started, no `capture_meta.json` is written, and the 4K metrics output contains no `cpu` field

#### Scenario: Missing PGID preserves backward compatibility

- **WHEN** `runner.py --profile 2k` is invoked without `--contestant-pgid`
- **THEN** no sampler thread is started, no `capture_meta.json` is written, `2k_metrics.json` contains no `cpu` field, and `score.json.cpu` reports `gate_reason="sampler_no_data"` with `points=0`

#### Scenario: PGID enumerates the full contestant subtree

- **WHEN** the contestant's `start.sh` forks additional processes (relay backend, decoder worker, browser tab) that inherit the session set by `setsid`
- **THEN** the sampler enumerates `/proc/[0-9]*/stat`, includes every process whose field-6 `session` matches the supplied PGID, and the reported `mean_percent` reflects CPU spent by the whole contestant subtree — not just the session leader

#### Scenario: Processes vanishing or appearing mid-capture do not corrupt the mean

- **WHEN** a contestant subprocess exits mid-capture, or a new subprocess is forked mid-capture
- **THEN** the vanished PID's last-observed jiffies remain in the running total without raising an error, the newly appeared PID's first observation establishes a baseline and only subsequent deltas are accumulated, and the final `mean_percent` is a valid normalized value (no negative numbers, no division by zero)

#### Scenario: Sampler debug rate is honored

- **WHEN** `runner.py --profile 2k --contestant-pgid <P> --cpu-sample-hz 5.0` is invoked
- **THEN** the sampler ticks at approximately 5 Hz, `capture_meta.json.cpu.sample_hz_used` is `5.0`, and `score.json.cpu.thresholds_used.sample_hz` is `5.0`

#### Scenario: Sampler also follows the Playwright Chrome subtree

- **WHEN** a contestant fans out raw H.265 NALUs and delegates decoding to a wasm worker inside the Chrome page
- **THEN** the sampler's union still picks up the renderer / browser / utility process CPU via `extra_root_pid` ppid descent, `capture_meta.json.cpu.extra_root_pid` records the driver PID it followed, and `mean_percent` reflects the contestant's effective rendering cost

#### Scenario: Chrome GPU process is excluded by default

- **WHEN** sampling enumerates the Chrome subtree on a host without a real GPU pipeline
- **THEN** the GPU process PID is excluded from the baseline, deltas, and per-PID attribution; `capture_meta.json.cpu.exclude_chrome_gpu` is `true` and the filtered PID appears in `excluded_gpu_pids`

#### Scenario: Chrome cmdline-rewrite layout is handled

- **WHEN** the sampler reads `/proc/<pid>/cmdline` for a Chrome subprocess that has rewritten its argv into a single space-separated string via `prctl(PR_SET_MM_*)` (the common runtime layout)
- **THEN** the GPU detection function still matches `--gpu-preferences=` via substring containment and correctly classifies the subprocess; a naive `cmdline.split(b"\x00")[i].startswith(b"--gpu-preferences=")` would silently miss every such subprocess and is explicitly NOT used

#### Scenario: Playwright private-API failure degrades safely

- **WHEN** a Playwright SDK upgrade renames or removes the path `browser._impl_obj._connection._transport._proc`
- **THEN** `runner.py` catches `AttributeError`, passes `extra_root_pid=None` to the sampler, and writes `capture_meta.json.cpu.extra_root_pid = null` so the audit field surfaces the regression; the sampler degrades to the contestant-session-only tree rather than raising

### Requirement: Report Generation

`report.py` SHALL generate an internal-only `report.html` per run, containing: a top summary with `objective_total` and per-profile subtotals (`2k` and `4k`); a gallery of suspicious screenshots (watermark recognition failures, color block failures, samples with SSIM `< 0.7`); a frame-number-over-time chart for each profile; an SSIM histogram for each profile; capture-throughput diagnostics for each profile; CPU scoring details identifying that CPU was measured on the 2K profile; the 4K linear absolute FPS formula and applied expected FPS; and relative-path links to the raw artifacts. The report MUST NOT be exposed to contestants by default.

#### Scenario: Report includes audit evidence

- **WHEN** scoring completes
- **THEN** `report.html` opens in a browser, renders all charts and galleries from local files only (no network calls), shows capture sampling FPS and capture overrun diagnostics for each completed profile, shows CPU measured on profile `2k`, shows the 4K linear FPS formula, and links to `2k_screenshots/`, `4k_screenshots/`, both metrics JSONs, and `score.json`

#### Scenario: Empty failure gallery

- **WHEN** a submission passes every check
- **THEN** the report still renders, with the suspicious-screenshot gallery showing zero entries and a clear "no failures" message

### Requirement: Orchestration and Cleanup

`scripts/evaluator.sh <team_id> <submission_zip>` SHALL execute the in-body pipeline portion of the evaluator (after host-side preparation succeeds per Requirement `Evaluator Entry Script`) in this order: start MediaMTX via `scripts/start_rtsp.sh` listening on port `554`; health-check both `rtsp://127.0.0.1:554/test/h265_2560_1440` and `rtsp://127.0.0.1:554/test/h265_3840_2160` via `scripts/health_check.sh`; run the 2K capture (30s) and the 4K capture (30s) in fresh Playwright Chromium contexts via `runner.py`, passing `--contestant-pgid` only for the profile whose `ProfileSpec.cpu_sampled` flag is true (2K by default); analyze both screenshot directories with `analyzer.py`; score with `scorer.py` (passing `--metrics 2k=... --metrics 4k=...`) and generate `report.html` with `report.py`; terminate MediaMTX; print the final `score.json` to the original stdout. A cleanup function and shell traps MUST be defined before any step that could fail, so cleanup runs on any exit path. Host-side concerns (zip extraction, invocation of contestant `start.sh` / `stop.sh`, contestant process-group cleanup, single-instance lock, port `8080` precheck) are owned by this same script under Requirement `Evaluator Entry Script`; the in-body pipeline described here SHALL run only after that preparation has succeeded. There SHALL NOT exist any separate wrapper script (`evaluator-host.sh`, `evaluator-local.sh`, or otherwise) that invokes this script; the script is the single entry. MediaMTX SHALL be a per-invocation process owned by this script and SHALL NOT be assumed to exist as a host-resident daemon shared across runs. The script SHALL iterate the profiles defined in `lib/profiles.py::PROFILES`; adding a new profile MUST NOT require new branches in this script.

#### Scenario: Successful in-body run

- **WHEN** the host-side preparation phase of `scripts/evaluator.sh` has confirmed that port `8080` is free, extracted the submission zip, started the contestant `start.sh`, and observed the contestant frontend become reachable
- **THEN** the in-body pipeline completes, `score.json` is printed to stdout, and all artifact files (`2k_screenshots/`, `4k_screenshots/`, `2k_metrics.json`, `4k_metrics.json`, `score.json`, `report.html`, `evaluator.log`) are present under the results directory

#### Scenario: CPU PGID follows the sampled profile

- **WHEN** `scripts/evaluator.sh` invokes `runner.py` for the default 2K and 4K profiles
- **THEN** it passes `--contestant-pgid` to the 2K invocation and does not pass it to the 4K invocation, because `PROFILES["2k"].cpu_sampled = True` and `PROFILES["4k"].cpu_sampled = False`

#### Scenario: Mid-run failure

- **WHEN** the contestant process crashes after 2K capture begins
- **THEN** the evaluator catches the failure via its trap, analyzes whatever screenshots were captured (counting missing or unrecognized frames as failures), still produces a `score.json` and `report.html` reflecting partial data, terminates MediaMTX, and the same script's cleanup trap kills the contestant process group, frees ports, releases the flock, and exits

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

### Requirement: Contest Platform Result Info

For every evaluator invocation that creates a run directory and produces `score.json`, the evaluator SHALL also produce a `result.info` file formatted after `reference/result-sample.info`. The evaluator SHALL write the audit copy to `results/<team_id>_<timestamp>/result.info` and SHALL copy the same bytes to `dirname <submission_zip>/result.info`.

`result.info` SHALL contain the fields `result`, `score`, `runtime`, `info`, and `debug` in that order:

```text
|result|0
|score|23.5
|runtime|64231
|info|
Objective Score: 23.5 / 30
Breakdown:
- 2K Correctness: 5 / 5
- 2K FPS: 5 / 5
- 4K Correctness: 5 / 5
- 4K FPS: 3.0 / 10
- CPU: 3 / 5
|debug|...
```

`score` SHALL equal `score.json.objective_total` formatted without unnecessary trailing zeroes. `runtime` SHALL be the evaluator wall-clock runtime in milliseconds for the current invocation. `info` SHALL be contestant-visible and SHALL contain the total objective score, the five scoring item point values (2K correctness, 2K FPS, 4K correctness, 4K FPS, and CPU), and, when available for contestant-side execution failures, a concise sanitized `Execution Feedback:` section. `debug` SHALL be organizer-facing and MAY contain multi-line diagnostics such as run directory, failure reason, per-profile metrics, CPU gate details, and Chromium version.

`result` SHALL be `0` when the evaluator produced a valid contestant result, including valid zero-score outcomes caused by the contestant submission. `result` SHALL be `1` when an evaluator, host, infrastructure, or publication failure makes the score untrustworthy.

The evaluator SHALL NOT place raw internal diagnostics in `info`. Contestant-visible execution feedback MUST be bounded and sanitized before it is written to `score.json` or `result.info`; it MUST NOT include run directories, host filesystem paths, Chromium version, scoring thresholds, measured FPS, SSIM, watermark recognition rate, color check rate, CPU mean percent, full Playwright stack traces, or organizer-only failure details.

#### Scenario: Successful run writes and publishes result info

- **WHEN** `scripts/evaluator.sh team_ref /uploads/team_ref.zip` completes a normal scoring run and `/uploads/` is writable
- **THEN** `results/team_ref_<timestamp>/result.info` exists
- **THEN** `/uploads/result.info` exists with identical contents
- **THEN** the `result.info` `score` field equals `score.json.objective_total`
- **THEN** the `info` block lists 2K correctness, 2K FPS, 4K correctness, 4K FPS, and CPU item scores
- **THEN** the `info` block does not contain an `Execution Feedback:` section unless contestant-side execution feedback was recorded

#### Scenario: Contestant failure remains a valid zero-score result

- **WHEN** the evaluator produces a contestant-side failure score, such as `contestant_frontend_unavailable`
- **THEN** `result.info` exists in the run directory and in `dirname <submission_zip>`
- **THEN** `|result|0` is written
- **THEN** `|score|0` is written
- **THEN** the `info` block shows `Objective Score: 0 / 30` and all five scoring items as `0 / <max>`
- **THEN** the `info` block includes an `Execution Feedback:` section when sanitized contestant feedback is available
- **THEN** the internal raw failure reason appears in `debug`

#### Scenario: Contestant startup log tail is surfaced safely

- **WHEN** the contestant frontend never becomes reachable and `contestant.log` contains output from the submitted `start.sh`
- **THEN** `score.json` records bounded sanitized contestant feedback derived from the startup failure and log tail
- **THEN** `result.info` includes that feedback under `Execution Feedback:` in the `info` block
- **THEN** the feedback is limited in size and excludes host paths, run directories, control characters, and organizer-only diagnostics

#### Scenario: Browser readiness failure is surfaced safely

- **WHEN** a profile capture fails because the page never sets `window.__PLAYER_READY__`, the player element is missing, or `window.__PLAYER_ERROR__` contains a contestant-facing error
- **THEN** `score.json` records bounded sanitized contestant feedback derived from the profile failure
- **THEN** `result.info` includes that feedback under `Execution Feedback:` in the `info` block
- **THEN** raw browser diagnostics and full Playwright errors remain available in `debug`, profile artifacts, or internal logs rather than being copied verbatim into `info`

#### Scenario: Infrastructure failure is marked untrusted

- **WHEN** the evaluator reaches a run directory but fails because of evaluator host or infrastructure problems, such as RTSP infrastructure failure
- **THEN** `result.info` exists in the run directory
- **THEN** `|result|1` is written
- **THEN** `|score|0` is written unless a trustworthy score was already produced
- **THEN** the failure reason appears in `debug`
- **THEN** the `info` block does not expose infrastructure, host, evaluator, or publication failure details as contestant feedback

#### Scenario: Info block is contestant-facing only

- **WHEN** a reviewer inspects `result.info`
- **THEN** the `info` block contains no measured FPS, SSIM, watermark recognition rate, color check rate, CPU mean percent, thresholds, run directory, Chromium version, full Playwright stack trace, or raw internal failure reason
- **THEN** those diagnostics, when available, are confined to `debug`, `score.json`, `report.html`, run logs, or profile artifacts
- **THEN** any `Execution Feedback:` lines in `info` are concise, sanitized messages intended for contestants

#### Scenario: Result info follows the sample field protocol

- **WHEN** `result.info` is parsed as line-oriented fields
- **THEN** `|result|`, `|score|`, and `|runtime|` are single-line fields
- **THEN** `|info|` appears alone on its line and its multi-line value continues until the `|debug|` marker
- **THEN** `|debug|` starts the final multi-line field
- **THEN** the explanatory comments from `reference/result-sample.info` are not included

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

#### Scenario: Server-side transcode to a browser-native codec

- **WHEN** a contestant's backend transcodes the fixed H.265 source to H.264 (or any non-H.265 codec) and the frontend plays it — typically in a `<video>` element that decodes natively
- **THEN** Decode-Path Forensics records `verdict = "violation"` for the affected profile (Check 1 detects a functioning `<video>` decoder on a host that cannot decode HEVC), and the Scoring decode-path gate zeros that profile's correctness and FPS (and CPU for `2k`)

#### Scenario: Server-side decode pushing pixels

- **WHEN** a contestant's backend decodes the H.265 server-side and pushes raw frames or JPEG images to the page rather than delivering the H.265 elementary stream
- **THEN** Decode-Path Forensics Check 2 classifies the decode-path bytes as non-H.265 and records `verdict = "violation"`, and the Scoring decode-path gate zeros that profile's correctness and FPS

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

- `scripts/setup.sh` — bootstrap the build host: install the apt toolchain (`build-essential`, `cmake`, `autoconf`, `automake`, `libtool`, `pkg-config`, `nasm`, `yasm`, `golang-go`, `python3-venv`, `lsof`, `unzip`, `libcap2-bin`), create the Python virtualenv at `.venv/` at the repo root, and run `git submodule update --init --recursive`.
- `scripts/build.sh` — build every submodule into `third_party/install/` in dependency order (leptonica before tesseract, then libdmtx, x264, x265, ffmpeg, mediamtx), `pip install -r requirements.txt` into the venv, `playwright install chromium` at the Playwright-pinned revision, and apply `sudo setcap cap_net_bind_service=+ep` to `third_party/install/bin/mediamtx` so the host-native MediaMTX can bind port `554`. A `--clean` flag SHALL force a from-scratch rebuild. The setcap step SHALL fail loudly (non-zero exit, explicit error message) when sudo is unavailable or the kernel does not support file capabilities; it MUST NOT silently fall back.
- `scripts/deploy.sh` — bring the host to a ready state for evaluation: invoke `scripts/prepare_streams.sh` if `streams/h265_*.mp4` or `reference/{2k,4k}/` are missing or older than `lib/watermark.py`. It SHALL NOT start MediaMTX or health-check RTSP — those are owned per-run by `scripts/evaluator.sh`. Operators who need RTSP up for ad-hoc `ffprobe` testing without invoking an evaluator run SHALL invoke `scripts/start_rtsp.sh` directly.
- `scripts/test.sh` — execute the automated validation suite against the bundled reference and negative-case submissions and exit non-zero if any expected score / failure-reason fails to match. The script SHALL auto-invoke `scripts/deploy.sh` if RTSP is not already up, copy each selected `test_submissions/*.zip` into a per-case isolated temporary upload directory before invoking `scripts/evaluator.sh`, drive cases through `scripts/evaluator.sh`, and register `scripts/teardown.sh` on EXIT/INT/TERM. The script SHALL NOT accept a `--portable` flag; legacy bundle-comparison mode is removed.
- `scripts/teardown.sh` — opposite of `scripts/deploy.sh`: stop MediaMTX (via `rtsp_server/mediamtx.pid`, SIGTERM-then-SIGKILL) and free port `8080`. Does NOT touch any other port — internal contestant ports are the contestant's concern and are collected by `scripts/evaluator.sh`'s process-group kill.

Each script SHALL be safe to re-run, SHALL refuse to silently use system-wide tools when its own outputs exist, and SHALL print a clear final status line ("ready", "failed: <reason>", etc.).

#### Scenario: Cold-start to first evaluation

- **WHEN** an organizer clones the repo onto a fresh Ubuntu 24.04 host and runs `setup.sh && build.sh && deploy.sh` in order
- **THEN** all three exit zero, `third_party/install/bin/{ffmpeg,mediamtx,tesseract}` exist and are executable, `getcap third_party/install/bin/mediamtx` reports `cap_net_bind_service+ep`, both watermarked MP4s (`h265_2560_1440.mp4` and `h265_3840_2160.mp4`) and the reference PNG sequences under `reference/2k/` and `reference/4k/` are present, no host-resident MediaMTX is running (deploy.sh does not start it), and `scripts/evaluator.sh` is ready to accept submissions (which will start its own MediaMTX per run)

#### Scenario: Idempotent re-runs

- **WHEN** any of `setup.sh`, `build.sh`, or `deploy.sh` is run a second time with no relevant inputs changed
- **THEN** it completes quickly (no rebuild of up-to-date submodules, no regeneration of existing streams), exits zero, and leaves the workspace in the same ready state

#### Scenario: setcap failure aborts the build

- **WHEN** `scripts/build.sh` reaches the setcap step but sudo is unavailable, or the kernel lacks `CAP_NET_BIND_SERVICE` support
- **THEN** `build.sh` exits non-zero with an explicit message naming `mediamtx` and the missing capability; it MUST NOT proceed past this point or attempt to use a different RTSP port

#### Scenario: Automated regression test

- **WHEN** an organizer runs `scripts/test.sh` after a successful `scripts/deploy.sh`
- **THEN** it evaluates each selected `test_submissions/*.zip` from a per-case isolated temporary upload directory, compares each resulting `score.json` to the expected outcome (with `max_score=30` and `2k` / `4k` / `cpu` block keys), prints per-case PASS/FAIL, and exits non-zero if any case fails

#### Scenario: Self-contained test session

- **WHEN** an organizer runs `scripts/test.sh` on a freshly built host (no prior `scripts/deploy.sh`)
- **THEN** it auto-invokes `scripts/deploy.sh` when watermarked streams are missing (to generate them), runs all cases through `scripts/evaluator.sh` (each of which starts and tears down its own MediaMTX per run), and runs `scripts/teardown.sh` on exit (success, failure, or interrupt) as a backstop, leaving ports `554` and `8080` free

#### Scenario: --portable flag rejected

- **WHEN** an organizer passes `scripts/test.sh --portable` (anywhere in argv)
- **THEN** the script exits non-zero with a usage message naming the supported flags; the `--portable` mode is removed and SHALL NOT silently fall back to default mode

### Requirement: Host Toolchain Documentation

Evaluator setup documentation SHALL list the host toolchain required by `scripts/setup.sh` (`build-essential`, `cmake`, `autoconf`, `automake`, `libtool`, `pkg-config`, `nasm`, `yasm`, `golang-go`, `python3-venv`, `lsof`, `unzip`, `libcap2-bin`), the pinned Python packages in `requirements.txt` (`playwright>=1.40`, `pillow>=10.0`, `numpy`, `scikit-image`, `pylibdmtx`, `pytesseract`), the list of submoduled open-source projects under `third_party/`, the documented exception that Chromium is taken from the Playwright-bundled binary, and the requirement that the host kernel supports `CAP_NET_BIND_SERVICE` file capabilities for MediaMTX to bind port `554` natively.

#### Scenario: Fresh Ubuntu 24.04 setup

- **WHEN** an organizer follows the documented setup steps (`setup.sh` → `build.sh` → `deploy.sh`) on a fresh Ubuntu 24.04 host that has none of `ffmpeg`, `mediamtx`, `tesseract`, `leptonica`, or `libdmtx` pre-installed system-wide
- **THEN** the evaluator runs end-to-end against the reference submission without missing-dependency errors and produces a complete `score.json` and `report.html` with `max_score=30`

### Requirement: Decode-Path Forensics

During each capture round, `runner.py` SHALL determine a per-profile `decode_path_verdict` ∈ {`ok`, `violation`, `inconclusive`} indicating whether the stream entering the browser's decode path is an H.265 (HEVC) elementary stream, and SHALL write it — with supporting evidence — to a `decode_forensics.json` artifact alongside the screenshots. `analyzer.py` SHALL pass this block through into `<profile>_metrics.json` under a `decode_forensics` key (the same pass-through pattern it uses for the runner's `cpu` block). The verdict SHALL be derived from two complementary checks:

- **Check 1 — active `<video>` decoder:** via the Chrome DevTools Protocol `Media` domain, the runner SHALL observe whether any media player has an active **video** decoder that has decoded one or more frames. Because the canonical host cannot decode HEVC through `<video>`/MSE/WebCodecs (`HOST_DECODES_HEVC = False`), a functioning `<video>` video decoder is itself proof that the input was re-encoded to a browser-decodable codec → `violation`. The self-reported codec string SHALL be recorded as evidence but SHALL NOT be the trigger. This rule is gated behind `HOST_DECODES_HEVC = False`; if the host policy ever enables hardware HEVC, Check 1 MUST instead inspect the codec string (flagging `avc1`, allowing `hvc1`/`hev1`).
- **Check 2 — wire-codec inspection:** instrumentation injected before contestant code (via `add_init_script`) SHALL sample bytes entering the decode path (`WebSocket`, `fetch`/`XHR` responses, `SourceBuffer.appendBuffer`, `VideoDecoder.configure`) and classify them. H.265-coded bytes (`hvc1`/`hev1` fMP4 sample entries, HEVC NAL unit types, or an HEVC `VideoDecoder` codec string) reaching a decode sink → confirms `ok`. Non-H.265 bytes reaching a decode/render sink (`avc1`, raw frames, JPEG) → `violation`. The byte classifier SHALL be a pure function so it is unit-testable without a browser.

Verdict aggregation per profile: `violation` if Check 1 fires OR Check 2 observed non-H.265 bytes into a sink; `ok` if Check 2 confirmed H.265 into a sink and nothing contradicts it; otherwise `inconclusive`. The forensic checks SHALL be best-effort: any error in the CDP session, the Media domain, or the injected binding SHALL degrade that check to "no signal" and SHALL NOT make the run fail or produce a `violation`. When `decode_forensics.json` is absent entirely (e.g. a pre-existing run), the scorer SHALL treat the verdict as `inconclusive`.

The policy is **fail-open**: only a positive `violation` deducts points (see the Scoring requirement). `ok` and `inconclusive` are scored on measured metrics; an `inconclusive` verdict on any profile SHALL set a top-level `review_required = true` in `score.json` and be flagged in `report.html` for manual review. The known evasion surface (dedicated Workers, WebTransport, obfuscation that `add_init_script` cannot reach) SHALL be documented; combined with fail-open, evasion yields false-negatives, never false-positives.

#### Scenario: Legal in-browser WASM decode is OK

- **WHEN** a contestant pulls the fixed H.265 RTSP source, relays/transmuxes the HEVC elementary stream into the page, and decodes it with an in-browser WASM (or WebCodecs) decoder rendering to `<canvas>` — creating no functioning `<video>` decoder
- **THEN** Check 1 produces no signal, Check 2 observes H.265-coded bytes into the decode sink, and `decode_forensics.json` records `verdict = "ok"` for that profile

#### Scenario: Server-side transcode to H.264 is a violation

- **WHEN** a contestant's backend transcodes the H.265 source to H.264 and the frontend plays it in a `<video>` element
- **THEN** the CDP `Media` domain reports a working video decoder with frames decoded, Check 1 fires, and `decode_forensics.json` records `verdict = "violation"` for that profile with the observed codec as evidence

#### Scenario: Server-side decode and push pixels is a violation

- **WHEN** a contestant's backend decodes the H.265 server-side and pushes raw frames or JPEG images to the page (no `<video>`, no H.265 on the wire)
- **THEN** Check 2 classifies the decode-path bytes as non-H.265 (raw/JPEG) and `decode_forensics.json` records `verdict = "violation"` for that profile

#### Scenario: Unobservable decode path is inconclusive and scored normally

- **WHEN** the decode path cannot be observed (e.g. bytes flow only through a dedicated Worker or a transport the injected instrumentation does not wrap) and no functioning `<video>` decoder is present
- **THEN** `decode_forensics.json` records `verdict = "inconclusive"`, the profile is scored on its measured metrics, and `score.json.review_required = true`

#### Scenario: Forensics never crashes the run

- **WHEN** the CDP `Media` session, the Media domain events, or the injected page binding raise an error
- **THEN** the affected check degrades to "no signal", the round still completes and produces screenshots, and the verdict is at worst `inconclusive` — never a `violation` caused by the error itself

### Requirement: Level-0 Gate (Decode Correctness)

Per-profile decode **correctness** SHALL act as a **level-0 gate** that conditions whether the "level-1" performance sub-scores (2K FPS, 4K FPS, and the CPU sub-score) contribute to `objective_total`. The gate is a scoring-stage decision only; it SHALL NOT skip the capture or analysis of any profile.

**Gate criterion.** `scorer.py` SHALL expose `gate_passed(profile_metrics) -> bool`, which returns `True` iff `score_correctness(...) == 5` for the 2K metrics AND `score_correctness(...) == 5` for the 4K metrics — i.e. full decode correctness (`watermark_recognition_rate >= 0.95` AND `color_check_rate >= 0.95` AND `mean_ssim >= 0.90`) on BOTH profiles, reusing the existing full-correctness band with NO new tunables. If either profile's metrics are absent or lack the inspected fields, or a decode-path `violation` has zeroed a profile's correctness, the gate SHALL be treated as FAILED.

**Effect of a passing gate.** The level-1 sub-scores SHALL be scored exactly as the Scoring requirement defines: each profile's `total = correctness_points + fps_points`, the CPU sub-score is computed normally, and `objective_total = 2k.total + 4k.total + cpu.points` (up to `30`).

**Effect of a failing gate.** Level-1 SHALL NOT contribute:

- each profile's `total` SHALL equal its `correctness_points` alone (its `fps_points` is still computed and recorded for audit, but excluded from `total` and from `objective_total`);
- the CPU block SHALL be set to `points = 0`, `gated = true`, `gate_reason = "gate_failed"` — UNLESS a more specific reason already applies (`decode_path_violation`, `2k_fps_below_threshold`, `sampler_no_data`, `2k_round_failed`, `container_mode_unsupported`, `host_failure`), which is retained;
- `objective_total` SHALL equal `2k.correctness_points + 4k.correctness_points`.

**Single source of the gate decision.** `build_score` SHALL derive the gate verdict from the supplied per-profile metrics and enforce the effects above regardless of caller, so a manual `scorer.py --metrics 2k=… --metrics 4k=…` run is self-consistent. `score.json` SHALL include a top-level `gate` block recording at least `{ "passed": <bool>, "2k_correctness_points": <int>, "4k_correctness_points": <int> }`.

**No capture short-circuit.** Both profiles SHALL always be captured and analyzed (the gate requires 4K correctness). `scorer.py` SHALL NOT provide a `--gate-check` capture-short-circuit mode, and `scripts/evaluator.sh` SHALL NOT gate downstream capture on the level-0 verdict.

#### Scenario: Full correctness on both profiles opens the gate

- **WHEN** `2k.correctness_points == 5` AND `4k.correctness_points == 5`
- **THEN** `score.json.gate.passed = true`, the 2K/4K FPS and CPU sub-scores are scored, and `objective_total = 2k.total + 4k.total + cpu.points`

#### Scenario: Sub-full correctness on either profile closes the gate

- **WHEN** `2k.correctness_points == 5` but `4k.correctness_points < 5` (or vice versa)
- **THEN** `score.json.gate.passed = false`, each profile's `total` equals its correctness only, `cpu.points = 0` with `cpu.gate_reason = "gate_failed"`, and `objective_total = 2k.correctness_points + 4k.correctness_points`

#### Scenario: FPS does not affect the gate

- **WHEN** both profiles reach full correctness but 2K FPS and 4K FPS are below their full-credit bands
- **THEN** `score.json.gate.passed = true` (FPS is not part of the criterion); the FPS points are scored on their own bands and added to the total

#### Scenario: Missing profile metrics fail the gate

- **WHEN** no `4k_metrics.json` was produced (the 4K round failed entirely)
- **THEN** `4k.correctness_points` is treated as `0`, `score.json.gate.passed = false`, level-1 is not scored, and `objective_total` reflects only the 2K correctness

#### Scenario: No --gate-check short-circuit exists

- **WHEN** `scripts/evaluator.sh` runs a submission
- **THEN** both the `2k` and `4k` rounds are captured and analyzed regardless of the 2K result, and no `scorer.py --gate-check` invocation drives capture skipping

### Requirement: Stage Execution Budgets

The evaluator SHALL enforce bounded wall-clock execution budgets for long-running evaluation stages after the run directory has been created. By default, each per-profile capture runner invocation SHALL have a 120 second budget, each per-profile analyzer invocation SHALL have a 240 second budget, scoring/report generation SHALL have a 60 second budget, and the evaluator body from run-directory creation through score publication SHALL have a 600 second budget. The effective budgets SHALL be configurable by environment variables and SHALL be recorded in run artifacts.

When a per-profile capture or analysis stage times out, the evaluator SHALL record a profile-specific reason, preserve any partial artifacts already written, continue to later stages or profiles when possible, and still produce a schema-compatible `score.json`, `report.html`, and `result.info` when the scoring path can run. A stage timeout SHALL NOT fabricate successful metrics. Existing scoring formulas SHALL remain unchanged; missing metrics and profile reasons SHALL be handled through the existing failure/profile-reason scoring path.

The evaluator SHALL write a run-level machine-readable stage timing artifact under `results/<team_id>_<ts>/` containing bounded records for major stages. Each record SHALL include at least stage name, profile when applicable, start epoch, end epoch when known, duration seconds when known, status (`running`, `success`, `failed`, `timeout`, or `skipped`), effective timeout seconds when applicable, exit code when available, and reason when available. For bounded stages, the evaluator SHALL write a `running` record before launching the stage process and SHALL write a final `success`, `failed`, or `timeout` record after the stage exits. A `running` record without a later final record for the same stage/profile indicates that the evaluator stopped before it could classify that stage.

#### Scenario: Normal run records successful stages

- **WHEN** a submission completes both profile captures, both analyses, and scoring inside the default budgets
- **THEN** the run directory contains a stage timing artifact with start and successful final records for 2K capture, 2K analysis, 4K capture, 4K analysis, scoring/report generation, and cleanup-relevant stages
- **THEN** `score.json` and `report.html` are produced using the existing scoring formulas

#### Scenario: Capture timeout is bounded and classified

- **WHEN** the 2K `runner.py` process exceeds the effective capture budget
- **THEN** the evaluator terminates that capture process, records the 2K profile reason as a capture timeout naming the budget, records a stage timing status of `timeout`, preserves any partial `2k_screenshots/` artifacts already written, and proceeds to the next profile when the host remains healthy
- **THEN** final scoring treats absent or incomplete 2K metrics as a failed 2K round rather than as successful playback

#### Scenario: Analyzer timeout does not block final score publication

- **WHEN** `analyzer.py` exceeds the effective analysis budget for a profile after screenshots were captured
- **THEN** the evaluator records an analysis timeout for that profile, leaves the raw screenshots and `timestamps.json` available for manual review, omits fabricated metrics for that profile, and continues toward final `score.json` publication

#### Scenario: Total evaluator timeout cleans up

- **WHEN** the evaluator body exceeds the effective total budget after creating a run directory
- **THEN** the evaluator runs its cleanup trap, terminates contestant and MediaMTX processes using the existing cleanup rules, releases the evaluator lock, and writes the clearest available failure artifacts without silently leaving the lock held

### Requirement: Capture Layout Diagnostics

`runner.py` SHALL write structured capture layout diagnostics into each profile's `timestamps.json`. After readiness, once the final capture clip and layout diagnostics have been computed, `runner.py` SHALL also immediately write the same diagnostic payload to `layout_diagnostics.json` in the profile screenshot directory before entering the steady-state screenshot loop. The diagnostics SHALL be collected at least after readiness and before the steady-state screenshot loop, and SHALL be collected on readiness timeout when the page is reachable. The diagnostics SHALL be bounded and SHALL NOT dump the full DOM.

The diagnostics SHALL include at least: navigated URL, browser viewport, device pixel ratio, page scroll offsets, `document.readyState`, `window.__PLAYER_READY__`, `window.__PLAYER_ERROR__`, the final capture clip, the `[data-testid="player-video"]` host element bounding box and client/scroll/offset dimensions, selected host computed styles relevant to clipping and scaling, and a bounded list of descendant `<canvas>` and `<video>` elements with their bounding boxes, client dimensions, intrinsic media/canvas dimensions, IDs, data-testid values, and selected computed styles.

The diagnostics SHALL include derived warnings when the observed layout suggests likely partial capture or misleading readiness, including at least: host clip smaller than the contract size, descendant media/canvas larger than the host while host overflow clips, transform applied to the host or media element, descendant media/canvas missing, and readiness true while the primary media/canvas has zero intrinsic dimensions.

When the computed capture clip extends beyond the browser viewport, the runner SHALL still attempt to capture the full clip rather than silently limiting the screenshot to the visible viewport. The diagnostics SHALL warn that the clip exceeds the viewport and that beyond-viewport capture may be slower. Capture timeout budgets remain responsible for bounding pathological large capture regions.

#### Scenario: Successful capture records player layout

- **WHEN** the contestant frontend reaches readiness and the player element is captured
- **THEN** `timestamps.json` contains `layout_diagnostics` with viewport, device pixel ratio, player host metrics, final clip, and a bounded list of descendant media/canvas metrics
- **THEN** `<profile>_screenshots/layout_diagnostics.json` contains the same diagnostic payload before the steady-state screenshot loop begins

#### Scenario: Oversized canvas inside clipped host is flagged

- **WHEN** `[data-testid="player-video"]` is `1280x720` but a descendant canvas is larger than the host and the host clips overflow
- **THEN** `timestamps.json.layout_diagnostics.warnings` contains a warning indicating that the screenshot may show only part of the rendered canvas

#### Scenario: Capture clip larger than viewport is captured beyond viewport

- **WHEN** the browser viewport is `1280x720` but `[data-testid="player-video"]` lays out to `2558x1438`
- **THEN** the runner captures a `2558x1438` screenshot when the browser supports beyond-viewport capture
- **THEN** `layout_diagnostics.warnings` contains a warning that the capture clip exceeds the browser viewport and may be slower

#### Scenario: Readiness timeout still records layout context

- **WHEN** the page loads but `window.__PLAYER_READY__` does not become `true` within the readiness window
- **THEN** `timestamps.json` records the startup timeout reason and includes whatever structured layout diagnostics can be collected from the page

#### Scenario: Diagnostics are bounded

- **WHEN** the contestant page contains many canvas or video elements
- **THEN** `runner.py` records only a fixed maximum number of descendant media/canvas entries and does not serialize arbitrary full DOM content

### Requirement: Runtime Diagnostics Surfacing

The evaluator SHALL surface stage timing, timeout, and capture layout diagnostics in organizer-facing artifacts without changing contestant-facing scoring semantics. `report.html` SHALL include a compact per-profile diagnostic summary and links to raw diagnostic artifacts. `scripts/diagnose_run.py` SHALL include stage timing and layout warning summaries when those artifacts are present, while continuing to handle older result directories where the new fields are absent. When `timestamps.json` is absent or lacks `layout_diagnostics`, organizer-facing diagnostics SHALL fall back to the standalone `<profile>_screenshots/layout_diagnostics.json` artifact when present.

`result.info` SHALL remain concise. It MAY include timeout/profile reasons and high-signal debug summaries, but SHALL NOT expose full layout diagnostics or internal browser logs as contestant-facing information.

#### Scenario: Report highlights timeout source

- **WHEN** a profile capture or analysis stage times out
- **THEN** `report.html` shows which profile and stage timed out, the effective timeout budget, and links to the available raw artifacts for that profile

#### Scenario: Report highlights likely partial capture

- **WHEN** `timestamps.json.layout_diagnostics.warnings` contains warnings about clipping, transform, zero intrinsic media size, or missing media/canvas elements
- **THEN** `report.html` displays a compact warning summary for that profile and links to the full `timestamps.json`

#### Scenario: Diagnose command remains backward compatible

- **WHEN** `scripts/diagnose_run.py` is run against an older result directory without stage timing or layout diagnostics
- **THEN** it still prints the existing score and FPS/capture-throughput diagnostics without failing
