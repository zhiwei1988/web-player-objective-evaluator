## MODIFIED Requirements

### Requirement: Image Build Pipeline

`scripts/package.sh` SHALL produce a portable OCI image from already-built build-host assets without recompiling source code. It MUST precheck the presence and freshness of `third_party/install/bin/{ffmpeg,mediamtx,tesseract}`, `third_party/install/share/tessdata/eng.traineddata`, `.venv/bin/python`, `~/.cache/ms-playwright/chromium-*/`, `streams/h265_2560_1440.mp4`, `streams/h265_3840_2160.mp4`, and `reference/{2k,4k}/frame_*.png`, and SHALL early-die with a message naming the missing asset and the script (`setup.sh` / `build.sh` / `deploy.sh`) that produces it. The pipeline MUST stage `~/.cache/ms-playwright/` into the repo-local `./.playwright/` directory before invoking `docker build`, so the Dockerfile's `COPY .playwright` reads from build context. The Dockerfile MUST be multi-stage: the `builder` stage SHALL only `COPY` the staged assets (no recompilation, no apt install of build toolchain in builder); the `runtime` stage SHALL be `ubuntu:24.04` with apt-installed Chromium runtime libraries (at minimum `libnss3`, `libxkbcommon0`, `libdrm2`, `libxcomposite1`, `libxdamage1`, `libxrandr2`, `libgbm1`, `libpango-1.0-0`, `libcairo2`, `libasound2t64`, `libatk-bridge2.0-0`, `libatk1.0-0`, `libcups2`, `libxss1`, `libxshmfence1`, `fonts-liberation`, `unzip`, `lsof`, `procps`, `libcap2-bin`) and the staged repo assets. The Dockerfile SHALL apply `setcap cap_net_bind_service=+ep` to `/work/third_party/install/bin/mediamtx` in the runtime stage as a belt-and-suspenders measure (runtime privilege also requires `--cap-add=NET_BIND_SERVICE` from the host invocation). The container `WORKDIR` MUST be `/work`, the `ENTRYPOINT` MUST be `/work/scripts/evaluator.sh`, and `PLAYWRIGHT_BROWSERS_PATH` MUST be set to `/work/.playwright`. The image MUST be runnable with `--network none` for initialization paths that do not exercise RTSP, proving runtime independence from any external network.

#### Scenario: Build succeeds against fully-built workspace

- **WHEN** an organizer runs `scripts/package.sh` immediately after a successful `scripts/setup.sh`, `scripts/build.sh`, and `scripts/deploy.sh`
- **THEN** it produces `dist/evaluator-portable_<sha>.tar.zst`, `dist/evaluator-host.sh`, `dist/README.md`, `dist/SHA256SUMS`, and `dist/manifest.json`, and the loaded image initializes under `docker run --rm --network none --entrypoint /work/.venv/bin/python evaluator-portable:<sha> -c 'import playwright'` without error

#### Scenario: Build refuses with missing prerequisites

- **WHEN** any one of `third_party/install/bin/ffmpeg`, `~/.cache/ms-playwright/chromium-*/`, `streams/h265_2560_1440.mp4`, `streams/h265_3840_2160.mp4`, or `reference/2k/frame_00000.png` is absent
- **THEN** `scripts/package.sh` exits non-zero before invoking `docker build` and prints a message naming the missing asset and the upstream script that produces it

#### Scenario: Build does not require external network

- **WHEN** `scripts/package.sh` is invoked with all prerequisites present but the build host is offline
- **THEN** `docker build` completes (the Dockerfile's apt install in the runtime stage uses the build host's docker daemon configuration, which is the operator's responsibility, but `package.sh` itself MUST NOT call `curl`, `wget`, `playwright install`, `go get`, or any other network-dependent tool)

### Requirement: Target Host Operator Entry

`scripts/evaluator-host.sh <team_id> <submission_zip>` SHALL be the operator-facing entry on the target host and SHALL execute, in order: acquire `flock -n` on `/var/tmp/evaluator-host.lock` (exit 75 if already held, printing the holding PID); compute `ts=$(date +%Y%m%d_%H%M%S)`; create `results/<team_id>_<ts>/`; precheck that `docker info` succeeds, that `docker image inspect evaluator-portable:$(jq -r .image_sha256 manifest.json)` returns the image, and that TCP ports `8080` and `554` are not in use; extract the submission zip into `submissions/<team_id>/` (lifting a single top-level directory if present); `chmod +x` `start.sh` and any present `stop.sh`; export `RTSP_SERVER_HOST=127.0.0.1`, `RTSP_SERVER_PORT=554`, `FRONTEND_PORT=8080`; invoke `start.sh` via `setsid` and record the process group id; poll `curl -fsS 'http://127.0.0.1:8080/play?profile=2k&autoplay=1'` for up to 60 seconds; on success, invoke `docker run --rm --network host --cap-add=NET_BIND_SERVICE --user $(id -u):$(id -g) -v "$PWD/results/<team_id>_<ts>:/work/results/<team_id>_<ts>:rw" evaluator-portable:<sha> <team_id> <team_id>_<ts>`; on completion, run contestant `stop.sh` if present (timeout 10s), `kill -- -<pgid>` the contestant process group, `fuser -k 8080/tcp 554/tcp` as belt-and-braces, release the flock. A `trap cleanup EXIT INT TERM` MUST be installed before the contestant `start.sh` is invoked. The script SHALL accept an optional `--root` flag that omits the `--user $(id -u):$(id -g)` argument to `docker run`; the default behavior is to pass that flag so artifacts written to mounted volumes are owned by the invoking user. The `--cap-add=NET_BIND_SERVICE` flag SHALL always be passed (independent of `--root`) so MediaMTX inside the container can bind port `554`. The script SHALL print the contents of `score.json` to stdout on completion.

#### Scenario: Successful end-to-end run

- **WHEN** the target host has docker installed, the image loaded, ports `8080` and `554` free, and a contestant zip that satisfies the contestant runtime contract
- **THEN** `evaluator-host.sh` exits 0, `results/<team_id>_<ts>/score.json` exists and contains a valid `objective_total` (with `max_score=30` and per-profile `2k`/`4k` blocks), port `8080` is free after the run, and the loaded image and host script are both fingerprinted against `manifest.json` before the run started

#### Scenario: Image not loaded

- **WHEN** `scripts/evaluator-host.sh` is invoked while `docker image inspect evaluator-portable:<image_sha256>` fails
- **THEN** the script exits 1 before extracting the submission zip and prints a message instructing the operator to run `docker load < evaluator-portable_<sha>.tar.zst`

#### Scenario: Port occupied

- **WHEN** another process is already bound to port `8080` or `554` at script start
- **THEN** the script exits 1 before extracting the submission zip and prints the conflicting PID; it does NOT kill the conflicting process automatically

#### Scenario: Result file ownership

- **WHEN** the script completes with default `--user $(id -u):$(id -g)`
- **THEN** every file written under `results/<team_id>_<ts>/` is owned by the invoking user, allowing `rm` and `chmod` without `sudo`

#### Scenario: NET_BIND_SERVICE capability always passed

- **WHEN** `evaluator-host.sh` invokes `docker run` (with or without `--root`)
- **THEN** the command line contains `--cap-add=NET_BIND_SERVICE`; the script MUST NOT omit this flag, because without it MediaMTX inside the container fails to bind `:554` and the run aborts with an RTSP infrastructure error

### Requirement: Container Network Model

The target host SHALL invoke the portable OCI container with `--network host` so that the container and the host share a single Linux network namespace. The contestant frontend running on the host at port `8080` SHALL be reachable from inside the container via `http://127.0.0.1:8080`, and MediaMTX running inside the container on port `554` SHALL be reachable from the host (and from the contestant process) via `rtsp://127.0.0.1:554`. The image MUST NOT publish ports explicitly (no `-p 554:554`); explicit port publishing is incompatible with `--network host` and is reserved as an unsupported alternative. Inside the container, `runner.py` MUST continue to use `http://127.0.0.1:8080` exactly as in the non-containerized invocation; the image MUST NOT require any new environment variable for host discovery (no `host.docker.internal`, no `EVALUATOR_FRONTEND_HOST`).

#### Scenario: Localhost reachability is symmetric

- **WHEN** the container is running with `--network host --cap-add=NET_BIND_SERVICE` and MediaMTX is up
- **THEN** a host-side `ffprobe -rtsp_transport tcp rtsp://127.0.0.1:554/test/h265_2560_1440` succeeds and an in-container Playwright Chromium navigating `http://127.0.0.1:8080/play?profile=2k&autoplay=1` reaches the host contestant frontend

#### Scenario: runner.py source unchanged with respect to host discovery

- **WHEN** the change is implemented
- **THEN** `runner.py` contains no new references to `host.docker.internal` or any frontend-host environment variable; the only network-relevant strings in `runner.py` remain those that the pre-change version contained (the URL parameter change from `?codec=` to `?profile=` is in scope for this change and is not considered a host-discovery change)

### Requirement: Failure Score on Contestant Unavailable

When `scripts/evaluator-host.sh` polls the contestant readiness URL and the contestant frontend does not become reachable within 60 seconds, the script SHALL invoke a short docker run that calls the in-image `scorer.py` with `--failure-reason contestant_frontend_unavailable` to write a minimal `score.json` (with `max_score=30`) and `report.html` into the results directory, then exit with code 2. The score JSON written through this path MUST share schema with a normal scoring run (no jq-assembled JSON, no manual JSON construction in the host script). The contestant `stop.sh` and process-group cleanup MUST still execute before exit.

#### Scenario: Frontend never becomes ready

- **WHEN** the contestant `start.sh` runs but `http://127.0.0.1:8080/play?profile=2k&autoplay=1` is unreachable for 60 seconds
- **THEN** `evaluator-host.sh` exits 2, `score.json` exists in the results directory with `objective_total = 0`, `max_score = 30`, and `reason = "contestant_frontend_unavailable"`, and ports `8080` / `554` are free after cleanup

### Requirement: Build Host Local Shortcut

`scripts/evaluator-local.sh <team_id> <submission_zip>` SHALL be a build-host local-development shortcut that performs the same host-side orchestration as `evaluator-host.sh` (lock, precheck ports `8080` and `554`, extract submission, run contestant `start.sh`, poll readiness at `?profile=2k`, cleanup) but invokes `scripts/evaluator.sh` directly in the host venv instead of via `docker run`. It SHALL share usage, argument names, exit codes (0 / 1 / 2 / 75), and failure-score semantics with `evaluator-host.sh`. It SHALL NOT depend on docker being installed. Because the native path relies on `scripts/build.sh`'s setcap step to grant `cap_net_bind_service` to `third_party/install/bin/mediamtx`, this script SHALL NOT pass `--cap-add`-equivalent privileges.

#### Scenario: Equivalence with containerized run

- **WHEN** the same `reference.zip` is evaluated via `evaluator-local.sh` on the build host and via `evaluator-host.sh` on a target host with the same image sha
- **THEN** the two `score.json` files are equivalent in profile-level totals (identical `objective_total`, identical per-profile `2k`/`4k` subtotals, SSIM scores within float-rounding tolerance); CPU sub-score values MAY differ because the container path reports `gate_reason="container_mode_unsupported"` while the native path runs actual sampling

### Requirement: Target Host Prerequisites

The target host SHALL require only: x86_64 Linux with a kernel and glibc compatible with `ubuntu:24.04`; docker engine `>= 20.10` (or rootful podman of equivalent capability) that accepts `--cap-add=NET_BIND_SERVICE`; the `zstd` command-line tool (or, if `package.sh` was invoked with `--gzip`, the standard `gzip`); and whatever runtimes the contestant submission itself bundles. No apt install of evaluator-side toolchain, no Python interpreter on the host, no Playwright install, no submodule checkout. The target host MUST NOT be required to be online during evaluation. Unsupported target configurations include macOS, Windows, arm64, and rootless podman (rootless engines typically cannot grant `NET_BIND_SERVICE` to bind port `554`).

#### Scenario: Clean target host accepts the bundle

- **WHEN** dist files are copied to a clean Ubuntu 24.04 host whose only evaluator-related installations are `docker` and `zstd`
- **THEN** `docker load < image.tar` + `evaluator-host.sh team_ref reference.zip` runs to completion and produces a valid `score.json` with `max_score=30`

#### Scenario: Offline evaluation

- **WHEN** the target host has no outbound network connectivity at evaluation time
- **THEN** `evaluator-host.sh` completes evaluation against a locally-served contestant submission without errors attributable to network access

#### Scenario: Rootless engine rejected with clear error

- **WHEN** the target host is running rootless podman and the operator invokes `evaluator-host.sh`
- **THEN** the container startup fails (either at `--cap-add=NET_BIND_SERVICE` rejection or at MediaMTX bind time) and the wrapper writes a failure `score.json` with a `reason` field naming the binding privilege as the cause; the wrapper MUST NOT silently retry on a non-standard port

### Requirement: Portable Self-Test Mode

`scripts/test.sh --portable` SHALL exercise the entire bundle path end-to-end. It MUST run, in order: `package.sh` and verify the dist files; on a second machine identified by `EVAL_TARGET_HOST=user@host`, copy the dist files and run `evaluator-host.sh team_ref reference.zip`; on the build host run `evaluator-local.sh team_ref reference.zip` and diff its `score.json` against the target-host run (per-profile keys identical, numeric values within float tolerance, both reporting `max_score=30`); run a known-broken fixture on the target host and assert exit code 2 plus `reason="contestant_frontend_unavailable"` in `score.json`. The `--portable` mode SHALL NOT fall back to nested docker, and SHALL exit non-zero if `EVAL_TARGET_HOST` is unset.

#### Scenario: Portable self-test passes

- **WHEN** a developer runs `scripts/test.sh --portable` with `EVAL_TARGET_HOST` pointing to a healthy second Ubuntu 24.04 host
- **THEN** all four stages exit zero, score parity is confirmed at the per-profile level (CPU may differ by `container_mode_unsupported`), and the negative fixture exits 2 with the expected reason
