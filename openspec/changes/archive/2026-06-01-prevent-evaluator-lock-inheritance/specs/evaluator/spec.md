## MODIFIED Requirements

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
