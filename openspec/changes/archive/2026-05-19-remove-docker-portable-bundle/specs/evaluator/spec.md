## ADDED Requirements

### Requirement: Evaluator Entry Script

`scripts/evaluator.sh <team_id> <submission_zip>` SHALL be the single operator-facing entry for evaluating a contestant submission. The script SHALL execute, in order: acquire `flock -n /var/tmp/evaluator.lock` (exit `75 / EX_TEMPFAIL` if already held, printing the holding PID; MUST NOT block-wait or queue); compute `ts=$(date +%Y%m%d_%H%M%S)`; create `results/<team_id>_<ts>/`; precheck that TCP port `8080` is free (the script MUST NOT precheck `554` because it starts MediaMTX itself per run via `scripts/start_rtsp.sh`, reusing any already-bound instance); extract the submission zip into `submissions/<team_id>/`, lifting a single top-level directory if present; `chmod +x` `start.sh` and any present `stop.sh`; export `RTSP_SERVER_HOST=127.0.0.1`, `RTSP_SERVER_PORT=554`, `FRONTEND_PORT=8080`; invoke `start.sh` via `setsid` and record the process-group id; poll `curl -fsS 'http://127.0.0.1:8080/play?profile=2k&autoplay=1'` for up to 60 seconds; on success, drive the capture / analyze / score pipeline (per `Playwright Capture Runner`, `Frame Analysis`, `Scoring` Requirements) against each profile in `lib/profiles.py::PROFILES`; on completion, invoke contestant `stop.sh` if present (timeout 10s), `kill -- -<pgid>` the contestant process group, free port `8080` as belt-and-braces, tear down MediaMTX, release the flock. A `trap cleanup EXIT INT TERM` MUST be installed before the contestant `start.sh` is invoked. The script SHALL print the contents of `score.json` to stdout on completion. There SHALL NOT be any separate `scripts/evaluator-host.sh` or `scripts/evaluator-local.sh` wrapper; legacy filenames MUST NOT exist in the workspace.

Exit codes: `0` on a normal scoring run (regardless of contestant score), `2` when the contestant frontend never becomes ready (see Requirement `Failure Score on Contestant Unavailable`), `75 / EX_TEMPFAIL` on lock contention, `1` on any other startup failure (missing submission zip, port `8080` occupied, missing `start.sh`, etc.).

#### Scenario: Successful end-to-end run

- **WHEN** an organizer runs `scripts/evaluator.sh team_ref reference.zip` on a build-and-deploy-ready host with port `8080` free and a contestant zip that satisfies the contestant runtime contract
- **THEN** the script exits `0`, `results/team_ref_<ts>/score.json` exists and contains a valid `objective_total` with `max_score=30` and per-profile `2k` / `4k` / `cpu` blocks, port `8080` is free after the run, and the contents of `score.json` were printed to stdout

#### Scenario: Concurrent invocation refused

- **WHEN** one `scripts/evaluator.sh` invocation is mid-run and a second invocation starts on the same host
- **THEN** the second invocation exits `75` immediately and prints the first invocation's PID; the first invocation is not disturbed

#### Scenario: Lock auto-released on signal

- **WHEN** an `scripts/evaluator.sh` invocation is killed with SIGTERM during the contestant startup poll
- **THEN** the flock on `/var/tmp/evaluator.lock` is released by the kernel, and a fresh invocation can proceed immediately

#### Scenario: Port 8080 occupied at start

- **WHEN** another process is already bound to port `8080` at script start
- **THEN** the script exits `1` before extracting the submission zip and prints the conflicting PID; it does NOT kill the conflicting process automatically

#### Scenario: Legacy wrapper names removed

- **WHEN** any organizer or automated tool searches the repository for `scripts/evaluator-host.sh` or `scripts/evaluator-local.sh`
- **THEN** neither file exists; `scripts/evaluator.sh` is the only entry under `scripts/` that accepts `<team_id> <submission_zip>` arguments

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

## MODIFIED Requirements

### Requirement: Workspace Layout

The evaluator's working tree IS the repository root (no `evaluator/` subdirectory). It SHALL contain at minimum: under `scripts/` — `setup.sh`, `build.sh`, `deploy.sh`, `test.sh`, `evaluator.sh`, `prepare_streams.sh`, `start_rtsp.sh`, `health_check.sh`, `build_test_zips.sh`, `env.sh`, `teardown.sh`, `_contestant_lifecycle.sh`; at the root — `runner.py`, `analyzer.py`, `scorer.py`, `report.py`, `requirements.txt`; in `lib/` — `watermark.py` and `profiles.py`; in `rtsp_server/` — `mediamtx.yml`; plus `third_party/` (containing git submodules and, after `scripts/build.sh`, an `install/` prefix), `streams/`, `reference/2k/`, `reference/4k/`, `submissions/`, `results/`, `test_submissions/`. There SHALL NOT exist any of `scripts/evaluator-host.sh`, `scripts/evaluator-local.sh`, `scripts/package.sh`, `Dockerfile`, `.dockerignore`, `.playwright/`, or `dist/`. The evaluator main body (`scripts/evaluator.sh` + `runner.py` + `analyzer.py` + `scorer.py` + `report.py`) runs natively on the build host; `scripts/evaluator.sh` is the single operator-facing entry. Evaluation host = build host; there is no separate "target host" workflow.

#### Scenario: Workspace exists after setup

- **WHEN** an organizer clones the repository and runs the documented setup steps
- **THEN** every path listed above exists, the shell scripts are executable, `lib/profiles.py` exposes a `PROFILES` mapping with keys `"2k"` and `"4k"`, and `./scripts/evaluator.sh` (invoked with too few arguments) prints a usage message naming `<team_id>` and `<submission_zip>`

#### Scenario: Submissions and results are isolated per run

- **WHEN** the evaluator runs for team `T` at timestamp `TS`
- **THEN** all submission files for that run live under `submissions/T/` and all artifacts live under `results/T_TS/`, with no cross-contamination from prior runs

#### Scenario: Legacy docker / bundle artifacts absent

- **WHEN** an organizer checks the repository working tree against the workspace layout
- **THEN** none of `Dockerfile`, `.dockerignore`, `scripts/package.sh`, `scripts/evaluator-host.sh`, `scripts/evaluator-local.sh`, `.playwright/`, or `dist/` exist; `.gitignore` does not name `dist/` or `.playwright/` (those entries are removed because the produced artifacts no longer exist)

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

### Requirement: Orchestration and Cleanup

`scripts/evaluator.sh <team_id> <submission_zip>` SHALL execute the in-body pipeline portion of the evaluator (after host-side preparation succeeds per Requirement `Evaluator Entry Script`) in this order: start MediaMTX via `scripts/start_rtsp.sh` listening on port `554`; health-check both `rtsp://127.0.0.1:554/test/h265_2560_1440` and `rtsp://127.0.0.1:554/test/h265_3840_2160` via `scripts/health_check.sh`; run the 2K capture (30s) and the 4K capture (30s) in fresh Playwright Chromium contexts via `runner.py` (passing `--contestant-pgid` only for the 4K invocation, per the Contestant CPU Usage Measurement requirement); analyze both screenshot directories with `analyzer.py`; score with `scorer.py` (passing `--metrics 2k=... --metrics 4k=...`) and generate `report.html` with `report.py`; terminate MediaMTX; print the final `score.json` to the original stdout. A cleanup function and shell traps MUST be defined before any step that could fail, so cleanup runs on any exit path. Host-side concerns (zip extraction, invocation of contestant `start.sh` / `stop.sh`, contestant process-group cleanup, single-instance lock, port `8080` precheck) are owned by this same script under Requirement `Evaluator Entry Script`; the in-body pipeline described here SHALL run only after that preparation has succeeded. There SHALL NOT exist any separate wrapper script (`evaluator-host.sh`, `evaluator-local.sh`, or otherwise) that invokes this script; the script is the single entry. MediaMTX SHALL be a per-invocation process owned by this script and SHALL NOT be assumed to exist as a host-resident daemon shared across runs. The script SHALL iterate the profiles defined in `lib/profiles.py::PROFILES`; adding a new profile MUST NOT require new branches in this script.

#### Scenario: Successful in-body run

- **WHEN** the host-side preparation phase of `scripts/evaluator.sh` has confirmed that port `8080` is free, extracted the submission zip, started the contestant `start.sh`, and observed the contestant frontend become reachable
- **THEN** the in-body pipeline completes, `score.json` is printed to stdout, and all artifact files (`2k_screenshots/`, `4k_screenshots/`, `2k_metrics.json`, `4k_metrics.json`, `score.json`, `report.html`, `evaluator.log`) are present under the results directory

#### Scenario: Mid-run failure

- **WHEN** the contestant process crashes after 2K capture begins
- **THEN** the evaluator catches the failure via its trap, analyzes whatever screenshots were captured (counting missing or unrecognized frames as failures), still produces a `score.json` and `report.html` reflecting partial data, terminates MediaMTX, and the same script's cleanup trap kills the contestant process group, frees ports, releases the flock, and exits

#### Scenario: MediaMTX is owned by this script

- **WHEN** the script starts a run
- **THEN** it brings MediaMTX up and tears it down within its own lifetime; it MUST NOT assume a pre-existing host-resident MediaMTX

### Requirement: Lifecycle Scripts

The evaluator workspace SHALL provide five idempotent lifecycle scripts under `scripts/`, in addition to the per-submission `scripts/evaluator.sh`:

- `scripts/setup.sh` — bootstrap the build host: install the apt toolchain (`build-essential`, `cmake`, `autoconf`, `automake`, `libtool`, `pkg-config`, `nasm`, `yasm`, `golang-go`, `python3-venv`, `lsof`, `unzip`, `libcap2-bin`), create the Python virtualenv at `.venv/` at the repo root, and run `git submodule update --init --recursive`.
- `scripts/build.sh` — build every submodule into `third_party/install/` in dependency order (leptonica before tesseract, then libdmtx, x264, x265, ffmpeg, mediamtx), `pip install -r requirements.txt` into the venv, `playwright install chromium` at the Playwright-pinned revision, and apply `sudo setcap cap_net_bind_service=+ep` to `third_party/install/bin/mediamtx` so the host-native MediaMTX can bind port `554`. A `--clean` flag SHALL force a from-scratch rebuild. The setcap step SHALL fail loudly (non-zero exit, explicit error message) when sudo is unavailable or the kernel does not support file capabilities; it MUST NOT silently fall back.
- `scripts/deploy.sh` — bring the host to a ready state for evaluation: invoke `scripts/prepare_streams.sh` if `streams/h265_*.mp4` or `reference/{2k,4k}/` are missing or older than `lib/watermark.py`. It SHALL NOT start MediaMTX or health-check RTSP — those are owned per-run by `scripts/evaluator.sh`. Operators who need RTSP up for ad-hoc `ffprobe` testing without invoking an evaluator run SHALL invoke `scripts/start_rtsp.sh` directly.
- `scripts/test.sh` — execute the automated validation suite against the bundled reference and negative-case submissions and exit non-zero if any expected score / failure-reason fails to match. The script SHALL auto-invoke `scripts/deploy.sh` if RTSP is not already up, drive cases through `scripts/evaluator.sh`, and register `scripts/teardown.sh` on EXIT/INT/TERM. The script SHALL NOT accept a `--portable` flag; legacy bundle-comparison mode is removed.
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
- **THEN** it invokes `scripts/evaluator.sh` against each `test_submissions/*.zip`, compares each resulting `score.json` to the expected outcome (with `max_score=30` and `2k` / `4k` / `cpu` block keys), prints per-case PASS/FAIL, and exits non-zero if any case fails

#### Scenario: Self-contained test session

- **WHEN** an organizer runs `scripts/test.sh` on a freshly built host (no prior `scripts/deploy.sh`)
- **THEN** it auto-invokes `scripts/deploy.sh` when watermarked streams are missing (to generate them), runs all cases through `scripts/evaluator.sh` (each of which starts and tears down its own MediaMTX per run), and runs `scripts/teardown.sh` on exit (success, failure, or interrupt) as a backstop, leaving ports `554` and `8080` free

#### Scenario: --portable flag rejected

- **WHEN** an organizer passes `scripts/test.sh --portable` (anywhere in argv)
- **THEN** the script exits non-zero with a usage message naming the supported flags; the `--portable` mode is removed and SHALL NOT silently fall back to default mode
