## MODIFIED Requirements

### Requirement: Workspace Layout

The evaluator's working tree IS the repository root (no `evaluator/` subdirectory). It SHALL contain at minimum: under `scripts/` — `setup.sh`, `build.sh`, `deploy.sh`, `test.sh`, `evaluator.sh`, `evaluator-host.sh`, `evaluator-local.sh`, `package.sh`, `prepare_streams.sh`, `start_rtsp.sh`, `health_check.sh`, `build_test_zips.sh`, `env.sh`, `teardown.sh`; at the root — `runner.py`, `analyzer.py`, `scorer.py`, `report.py`, `requirements.txt`, `Dockerfile`, `.dockerignore`; in `lib/` — `watermark.py`; in `rtsp_server/` — `mediamtx.yml`; plus `third_party/` (containing git submodules and, after `scripts/build.sh`, an `install/` prefix), `streams/`, `reference/h264/`, `reference/h265/`, `submissions/`, `results/`, `test_submissions/`, and `dist/` (produced by `scripts/package.sh`). The evaluator main body (`scripts/evaluator.sh` + `runner.py` + `analyzer.py` + `scorer.py` + `report.py`) MAY run either natively on the build host (driven by `scripts/evaluator-local.sh`) or inside the portable OCI container (driven by `scripts/evaluator-host.sh`). The target host operator workflow runs the containerized path only.

#### Scenario: Workspace exists after setup

- **WHEN** an organizer clones the repository and runs the documented setup steps
- **THEN** every path listed above exists, the shell scripts are executable, and both `./scripts/evaluator-host.sh` and `./scripts/evaluator-local.sh` (invoked with too few arguments) print a usage message naming `<team_id>` and `<submission_zip>`

#### Scenario: Submissions and results are isolated per run

- **WHEN** the evaluator runs for team `T` at timestamp `TS`
- **THEN** all submission files for that run live under `submissions/T/` and all artifacts live under `results/T_TS/`, with no cross-contamination from prior runs

---

### Requirement: Orchestration and Cleanup

`scripts/evaluator.sh <team_id> <results_subdir>` SHALL execute the evaluator main body in this order: start MediaMTX via `scripts/start_rtsp.sh` listening on port `8554`; health-check both `rtsp://127.0.0.1:8554/h264` and `rtsp://127.0.0.1:8554/h265` via `scripts/health_check.sh`; run the H.264 capture (30s) and the H.265 capture (30s) in fresh Playwright Chromium contexts via `runner.py`; analyze both screenshot directories with `analyzer.py`; score with `scorer.py` and generate `report.html` with `report.py`; terminate MediaMTX; print the final `score.json` to the original stdout. A cleanup function and shell traps MUST be defined before any step that could fail, so cleanup runs on any exit path. Host-side concerns (zip extraction, invocation of contestant `start.sh` / `stop.sh`, contestant process-group cleanup, single-instance lock, port `8080` / `8554` precheck) are NOT this script's responsibility; they belong to `scripts/evaluator-host.sh` (target host) and `scripts/evaluator-local.sh` (build host shortcut), which invoke this script after the host environment is prepared. MediaMTX SHALL be a per-invocation process owned by this script and SHALL NOT be assumed to exist as a host-resident daemon shared across runs.

#### Scenario: Successful in-body run

- **WHEN** invoked with a `team_id` and a `results_subdir` after the wrapping host script has confirmed that ports 8554 and 8080 are free and the contestant frontend is ready
- **THEN** the run completes, `score.json` is printed to stdout, and all artifact files (`h264_screenshots/`, `h265_screenshots/`, `h264_metrics.json`, `h265_metrics.json`, `score.json`, `report.html`, `evaluator.log`) are present under the results directory

#### Scenario: Mid-run failure

- **WHEN** the contestant process crashes after H.264 capture begins
- **THEN** the evaluator catches the failure via its trap, analyzes whatever screenshots were captured (counting missing or unrecognized frames as failures), still produces a `score.json` and `report.html` reflecting partial data, terminates MediaMTX, and exits with a code that allows the wrapping host script to perform port cleanup

#### Scenario: MediaMTX is owned by this script

- **WHEN** the script starts a run
- **THEN** it brings MediaMTX up and tears it down within its own lifetime; it MUST NOT assume a pre-existing host-resident MediaMTX

---

### Requirement: Lifecycle Scripts

The evaluator workspace SHALL provide eight idempotent lifecycle scripts under `scripts/`, in addition to the per-submission `scripts/evaluator.sh`:

- `scripts/setup.sh` — bootstrap the build host: install the apt toolchain (`build-essential`, `cmake`, `autoconf`, `automake`, `libtool`, `pkg-config`, `nasm`, `yasm`, `golang-go`, `python3-venv`, `lsof`, `unzip`), create the Python virtualenv at `.venv/` at the repo root, and run `git submodule update --init --recursive`. Build-host-only.
- `scripts/build.sh` — build every submodule into `third_party/install/` in dependency order (leptonica before tesseract, then libdmtx, x264, x265, ffmpeg, mediamtx), `pip install -r requirements.txt` into the venv, and `playwright install chromium` at the Playwright-pinned revision. A `--clean` flag SHALL force a from-scratch rebuild. Build-host-only.
- `scripts/deploy.sh` — bring the build host to a ready state for local development: invoke `scripts/prepare_streams.sh` if `streams/*.mp4` or `reference/<codec>/` are missing or older than `lib/watermark.py`; start MediaMTX via `scripts/start_rtsp.sh`; health-check both RTSP URLs via `ffprobe`. NOT used on target hosts (target hosts get MediaMTX from inside the portable OCI container).
- `scripts/package.sh` — produce the portable bundle (`dist/`). See the `portable-bundle` capability spec for the normative pipeline; the script SHALL be idempotent and SHALL refuse to run if `third_party/install/`, `.venv/`, `~/.cache/ms-playwright/chromium-*/`, `streams/`, or `reference/` are missing or stale.
- `scripts/evaluator-host.sh` — target host operator entry; see the `portable-bundle` capability spec for the normative contract.
- `scripts/evaluator-local.sh` — build host local-development shortcut sharing arguments and exit codes with `evaluator-host.sh` but invoking `scripts/evaluator.sh` natively (no docker). See the `portable-bundle` capability spec.
- `scripts/test.sh` — execute the automated validation suite against the bundled reference and negative-case submissions and exit non-zero if any expected score / failure-reason fails to match. The default mode SHALL auto-invoke `scripts/deploy.sh` if RTSP is not already up, drive cases through `scripts/evaluator-local.sh`, and register `scripts/teardown.sh` on EXIT/INT/TERM. A `--portable` mode SHALL additionally exercise `scripts/package.sh` and the full bundle path; see the `portable-bundle` capability spec.
- `scripts/teardown.sh` — opposite of `scripts/deploy.sh`: stop MediaMTX (via `rtsp_server/mediamtx.pid`, SIGTERM-then-SIGKILL) and free port `8080`. Does NOT touch any other port — internal contestant ports are the contestant's concern and are collected by `scripts/evaluator-host.sh`'s (or `scripts/evaluator-local.sh`'s) process-group kill.

Each script SHALL be safe to re-run, SHALL refuse to silently use system-wide tools when its own outputs exist, and SHALL print a clear final status line ("ready", "failed: <reason>", etc.).

#### Scenario: Cold-start to first local evaluation

- **WHEN** an organizer clones the repo onto a fresh Ubuntu 24.04 host and runs `setup.sh && build.sh && deploy.sh` in order
- **THEN** all three exit zero, `third_party/install/bin/{ffmpeg,mediamtx,tesseract}` exist and are executable, both watermarked MP4s and the reference PNG sequences are present, MediaMTX is listening on `:8554`, and `scripts/evaluator-local.sh` is ready to accept submissions

#### Scenario: Cold-start to bundle production

- **WHEN** an organizer runs `scripts/package.sh` after a successful `setup.sh && build.sh && deploy.sh`
- **THEN** `dist/{evaluator-portable_<sha>.tar.zst, evaluator-host.sh, README.md, SHA256SUMS, manifest.json}` exist and `sha256sum -c dist/SHA256SUMS` exits zero

#### Scenario: Idempotent re-runs

- **WHEN** any of `setup.sh`, `build.sh`, `deploy.sh`, or `package.sh` is run a second time with no relevant inputs changed
- **THEN** it completes quickly (no rebuild of up-to-date submodules, no regeneration of existing streams, no docker rebuild of unchanged layers), exits zero, and leaves the workspace in the same ready state

#### Scenario: Automated regression test (default mode)

- **WHEN** an organizer runs `scripts/test.sh` after a successful `scripts/deploy.sh`
- **THEN** it invokes `scripts/evaluator-local.sh` against each `test_submissions/*.zip`, compares each resulting `score.json` to the expected outcome, prints per-case PASS/FAIL, and exits non-zero if any case fails

#### Scenario: Portable bundle regression test (`--portable` mode)

- **WHEN** an organizer runs `scripts/test.sh --portable` after a successful `scripts/package.sh` and with `EVAL_TARGET_HOST=user@host` pointing to a reachable second Ubuntu 24.04 host
- **THEN** it exercises `scripts/package.sh`, runs `scripts/evaluator-host.sh` against `reference.zip` on the target host, diffs the resulting `score.json` against an `evaluator-local.sh` run on the build host, asserts exit code 2 plus `reason="contestant_frontend_unavailable"` for a negative fixture, prints per-stage PASS/FAIL, and exits non-zero if any stage fails

#### Scenario: Self-contained test session

- **WHEN** an organizer runs `scripts/test.sh` on a freshly built host (no prior `scripts/deploy.sh`)
- **THEN** it brings RTSP up via `scripts/deploy.sh`, runs all cases through `scripts/evaluator-local.sh`, and tears down via `scripts/teardown.sh` on exit (success, failure, or interrupt), leaving ports `8554` and `8080` free
