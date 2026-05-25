## MODIFIED Requirements

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

### Requirement: Contestant Runtime Contract

Each contestant submission zip SHALL, after extraction into `dirname <submission_zip>`, provide an executable `start.sh` that launches whatever processes the submission needs; it MAY provide a `stop.sh` for cleanup. The evaluator SHALL export `RTSP_SERVER_HOST=127.0.0.1`, `RTSP_SERVER_PORT=554`, and `FRONTEND_PORT=8080` before invoking `start.sh`. Internal contestant processes (relay backends, decode workers, etc.) MAY bind any other local port; the evaluator neither prescribes nor cleans those — the contestant's process group is SIGKILLed as a whole at cleanup. The frontend SHALL expose route `/play` accepting `profile` (`2k` or `4k`) and `autoplay=1`, render the video into `[data-testid="player-video"]` at an actual rendered size of at least `1280x720` with proportional (uncropped) display, set `window.__PLAYER_READY__ = true` after the first frame is rendered, and assign a human-readable string to `window.__PLAYER_ERROR__` on playback failure. The RTSP source URLs that the contestant SHALL pull from are fixed: `rtsp://127.0.0.1:554/test/h265_2560_1440` for `profile=2k` and `rtsp://127.0.0.1:554/test/h265_3840_2160` for `profile=4k`.

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

### Requirement: Evaluator Entry Script

`scripts/evaluator.sh <team_id> <submission_zip>` SHALL be the single operator-facing entry for evaluating a contestant submission. The script SHALL execute, in order: acquire `flock -n /var/tmp/evaluator.lock` (exit `75 / EX_TEMPFAIL` if already held, printing the holding PID; MUST NOT block-wait or queue); compute `ts=$(date +%Y%m%d_%H%M%S)`; create `results/<team_id>_<ts>/`; precheck that TCP port `8080` is free (the script MUST NOT precheck `554` because it starts MediaMTX itself per run via `scripts/start_rtsp.sh`, reusing any already-bound instance); extract the submission zip into `dirname <submission_zip>`, lifting a single top-level directory if present; grant executable permission to all files under `dirname <submission_zip>` after extraction and lifting; export `RTSP_SERVER_HOST=127.0.0.1`, `RTSP_SERVER_PORT=554`, `FRONTEND_PORT=8080`; invoke `start.sh` from `dirname <submission_zip>` via `setsid` and record the process-group id; poll `curl -fsS 'http://127.0.0.1:8080/play?profile=2k&autoplay=1'` for up to 60 seconds; on success, drive the capture / analyze / score pipeline (per `Playwright Capture Runner`, `Frame Analysis`, `Scoring` Requirements) against each profile in `lib/profiles.py::PROFILES`; on completion, invoke contestant `stop.sh` if present (timeout 10s), `kill -- -<pgid>` the contestant process group, free port `8080` as belt-and-braces, tear down MediaMTX, release the flock. A `trap cleanup EXIT INT TERM` MUST be installed before the contestant `start.sh` is invoked. The script SHALL print the contents of `score.json` to stdout on completion. There SHALL NOT be any separate `scripts/evaluator-host.sh` or `scripts/evaluator-local.sh` wrapper; legacy filenames MUST NOT exist in the workspace.

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

#### Scenario: Port 8080 occupied at start

- **WHEN** another process is already bound to port `8080` at script start
- **THEN** the script exits `1` before extracting the submission zip and prints the conflicting PID; it does NOT kill the conflicting process automatically

#### Scenario: Legacy wrapper names removed

- **WHEN** any organizer or automated tool searches the repository for `scripts/evaluator-host.sh` or `scripts/evaluator-local.sh`
- **THEN** neither file exists; `scripts/evaluator.sh` is the only entry under `scripts/` that accepts `<team_id> <submission_zip>` arguments

#### Scenario: Extracted files are executable

- **WHEN** a submission zip contains non-executable regular files, including `start.sh`, `stop.sh`, helper scripts, or binaries
- **THEN** after extraction and any single-top-level-directory lifting, all files under `dirname <submission_zip>` have executable permission

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
- 4K FPS: 1.5 / 5
- CPU: 7 / 10
|debug|...
```

`score` SHALL equal `score.json.objective_total` formatted without unnecessary trailing zeroes. `runtime` SHALL be the evaluator wall-clock runtime in milliseconds for the current invocation. `info` SHALL be contestant-visible and SHALL contain only the total objective score and the five scoring item point values: 2K correctness, 2K FPS, 4K correctness, 4K FPS, and CPU. `debug` SHALL be organizer-facing and MAY contain multi-line diagnostics such as run directory, failure reason, per-profile metrics, CPU gate details, and Chromium version.

`result` SHALL be `0` when the evaluator produced a valid contestant result, including valid zero-score outcomes caused by the contestant submission. `result` SHALL be `1` when an evaluator, host, infrastructure, or publication failure makes the score untrustworthy.

#### Scenario: Successful run writes and publishes result info

- **WHEN** `scripts/evaluator.sh team_ref /uploads/team_ref.zip` completes a normal scoring run and `/uploads/` is writable
- **THEN** `results/team_ref_<timestamp>/result.info` exists
- **THEN** `/uploads/result.info` exists with identical contents
- **THEN** the `result.info` `score` field equals `score.json.objective_total`
- **THEN** the `info` block lists exactly 2K correctness, 2K FPS, 4K correctness, 4K FPS, and CPU item scores

#### Scenario: Contestant failure remains a valid zero-score result

- **WHEN** the evaluator produces a contestant-side failure score, such as `contestant_frontend_unavailable`
- **THEN** `result.info` exists in the run directory and in `dirname <submission_zip>`
- **THEN** `|result|0` is written
- **THEN** `|score|0` is written
- **THEN** the `info` block shows `Objective Score: 0 / 30` and all five scoring items as `0 / <max>`
- **THEN** the contestant-visible `info` block does not include internal failure reasons
- **THEN** the internal failure reason appears in `debug`

#### Scenario: Infrastructure failure is marked untrusted

- **WHEN** the evaluator reaches a run directory but fails because of evaluator host or infrastructure problems, such as RTSP infrastructure failure
- **THEN** `result.info` exists in the run directory
- **THEN** `|result|1` is written
- **THEN** `|score|0` is written unless a trustworthy score was already produced
- **THEN** the failure reason appears in `debug`

#### Scenario: Info block is contestant-facing only

- **WHEN** a reviewer inspects `result.info`
- **THEN** the `info` block contains no measured FPS, SSIM, watermark recognition rate, color check rate, CPU mean percent, thresholds, run directory, Chromium version, or raw failure reason
- **THEN** those diagnostics, when available, are confined to `debug`, `score.json`, or `report.html`

#### Scenario: Result info follows the sample field protocol

- **WHEN** `result.info` is parsed as line-oriented fields
- **THEN** `|result|`, `|score|`, and `|runtime|` are single-line fields
- **THEN** `|info|` appears alone on its line and its multi-line value continues until the `|debug|` marker
- **THEN** `|debug|` starts the final multi-line field
- **THEN** the explanatory comments from `reference/result-sample.info` are not included
