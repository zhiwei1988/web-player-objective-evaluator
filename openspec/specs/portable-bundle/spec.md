# portable-bundle

## Purpose

Package the already-built evaluator (third_party install prefix, Python venv, Playwright Chromium / Google Chrome, watermarked streams and reference frames, source code) into a self-contained OCI image plus a thin host wrapper, so that any docker-equipped Ubuntu 24.04 server can evaluate one contestant submission with `docker load < image.tar.zst && ./evaluator-host.sh <team_id> <zip>` — no apt install, no source compile, no outbound network at evaluation time. Distribution is a 5- or 6-file `dist/` produced by `scripts/package.sh` on the build host; the bundle is sha-locked through a `manifest.json` so the wrapper refuses to operate against an image whose digest does not match.

## Requirements

### Requirement: Image Build Pipeline

`scripts/package.sh` SHALL produce a portable OCI image from already-built build-host assets without recompiling source code. It MUST precheck the presence and freshness of `third_party/install/bin/{ffmpeg,mediamtx,tesseract}`, `third_party/install/share/tessdata/eng.traineddata`, `.venv/bin/python`, `~/.cache/ms-playwright/chromium-*/`, `streams/{h264,h265}_watermarked.mp4`, and `reference/{h264,h265}/frame_*.png`, and SHALL early-die with a message naming the missing asset and the script (`setup.sh` / `build.sh` / `deploy.sh`) that produces it. The pipeline MUST stage `~/.cache/ms-playwright/` into the repo-local `./.playwright/` directory before invoking `docker build`, so the Dockerfile's `COPY .playwright` reads from build context. The Dockerfile MUST be multi-stage: the `builder` stage SHALL only `COPY` the staged assets (no recompilation, no apt install of build toolchain in builder); the `runtime` stage SHALL be `ubuntu:24.04` with apt-installed Chromium runtime libraries (at minimum `libnss3`, `libxkbcommon0`, `libdrm2`, `libxcomposite1`, `libxdamage1`, `libxrandr2`, `libgbm1`, `libpango-1.0-0`, `libcairo2`, `libasound2t64`, `libatk-bridge2.0-0`, `libatk1.0-0`, `libcups2`, `libxss1`, `libxshmfence1`, `fonts-liberation`, `unzip`, `lsof`, `procps`) and the staged repo assets. The container `WORKDIR` MUST be `/work`, the `ENTRYPOINT` MUST be `/work/scripts/evaluator.sh`, and `PLAYWRIGHT_BROWSERS_PATH` MUST be set to `/work/.playwright`. The image MUST be runnable with `--network none` for initialization paths that do not exercise RTSP, proving runtime independence from any external network.

#### Scenario: Build succeeds against fully-built workspace

- **WHEN** an organizer runs `scripts/package.sh` immediately after a successful `scripts/setup.sh`, `scripts/build.sh`, and `scripts/deploy.sh`
- **THEN** it produces `dist/evaluator-portable_<sha>.tar.zst`, `dist/evaluator-host.sh`, `dist/README.md`, `dist/SHA256SUMS`, and `dist/manifest.json`, and the loaded image initializes under `docker run --rm --network none --entrypoint /work/.venv/bin/python evaluator-portable:<sha> -c 'import playwright'` without error

#### Scenario: Build refuses with missing prerequisites

- **WHEN** any one of `third_party/install/bin/ffmpeg`, `~/.cache/ms-playwright/chromium-*/`, `streams/h264_watermarked.mp4`, or `reference/h264/frame_00000.png` is absent
- **THEN** `scripts/package.sh` exits non-zero before invoking `docker build` and prints a message naming the missing asset and the upstream script that produces it

#### Scenario: Build does not require external network

- **WHEN** `scripts/package.sh` is invoked with all prerequisites present but the build host is offline
- **THEN** `docker build` completes (the Dockerfile's apt install in the runtime stage uses the build host's docker daemon configuration, which is the operator's responsibility, but `package.sh` itself MUST NOT call `curl`, `wget`, `playwright install`, `go get`, or any other network-dependent tool)

### Requirement: Dist Bundle Layout

The `dist/` directory produced by `scripts/package.sh` SHALL contain exactly six files: `evaluator-portable_<sha>.tar.zst` (the zstd-compressed `docker save` archive), `evaluator-host.sh` (the target host operator entry, copied from `scripts/`), `_contestant_lifecycle.sh` (the host-side helper functions sourced by `evaluator-host.sh`), `README.md` (target host deployment instructions in Chinese), `SHA256SUMS` (sha256 of all five sibling files in the BSD-style format consumed by `sha256sum -c`), and `manifest.json`. The `<sha>` in the image filename SHALL be the first 12 hex chars of the git HEAD commit. `manifest.json` SHALL contain exactly these six fields and no more: `git_sha` (full 40-char), `build_timestamp` (ISO 8601 UTC), `submodule_status` (object mapping each submodule path to its pinned sha), `playwright_chromium_version` (string from `third_party/install/playwright_chromium.version`), `image_sha256` (full sha256 of the loaded docker image, as reported by `docker image inspect`), and `image_size_bytes` (integer, the compressed tarball size).

#### Scenario: Bundle integrity verification

- **WHEN** an operator runs `sha256sum -c SHA256SUMS` in a directory containing the dist files
- **THEN** all five entries report `OK`

#### Scenario: Manifest field set is closed

- **WHEN** a reviewer parses `manifest.json` after a successful build
- **THEN** it contains exactly `git_sha`, `build_timestamp`, `submodule_status`, `playwright_chromium_version`, `image_sha256`, and `image_size_bytes`, with no additional or missing fields

### Requirement: Target Host Operator Entry

`scripts/evaluator-host.sh <team_id> <submission_zip>` SHALL be the operator-facing entry on the target host and SHALL execute, in order: acquire `flock -n` on `/var/tmp/evaluator-host.lock` (exit 75 if already held, printing the holding PID); compute `ts=$(date +%Y%m%d_%H%M%S)`; create `results/<team_id>_<ts>/`; precheck that `docker info` succeeds, that `docker image inspect evaluator-portable:$(jq -r .image_sha256 manifest.json)` returns the image, and that TCP ports 8080 and 8554 are not in use; extract the submission zip into `submissions/<team_id>/` (lifting a single top-level directory if present); `chmod +x` `start.sh` and any present `stop.sh`; export `RTSP_SERVER_HOST=127.0.0.1`, `RTSP_SERVER_PORT=8554`, `FRONTEND_PORT=8080`; invoke `start.sh` via `setsid` and record the process group id; poll `curl -fsS http://127.0.0.1:8080/play?codec=h264&autoplay=1` for up to 60 seconds; on success, invoke `docker run --rm --network host --user $(id -u):$(id -g) -v "$PWD/results/<team_id>_<ts>:/work/results/<team_id>_<ts>:rw" evaluator-portable:<sha> <team_id> <team_id>_<ts>`; on completion, run contestant `stop.sh` if present (timeout 10s), `kill -- -<pgid>` the contestant process group, `fuser -k 8080/tcp 8554/tcp` as belt-and-braces, release the flock. A `trap cleanup EXIT INT TERM` MUST be installed before the contestant `start.sh` is invoked. The script SHALL accept an optional `--root` flag that omits the `--user $(id -u):$(id -g)` argument to `docker run`; the default behavior is to pass that flag so artifacts written to mounted volumes are owned by the invoking user. The script SHALL print the contents of `score.json` to stdout on completion.

#### Scenario: Successful end-to-end run

- **WHEN** the target host has docker installed, the image loaded, ports 8080 and 8554 free, and a contestant zip that satisfies the contestant runtime contract
- **THEN** `evaluator-host.sh` exits 0, `results/<team_id>_<ts>/score.json` exists and contains a valid `objective_total`, port 8080 is free after the run, and the loaded image and host script are both fingerprinted against `manifest.json` before the run started

#### Scenario: Image not loaded

- **WHEN** `scripts/evaluator-host.sh` is invoked while `docker image inspect evaluator-portable:<image_sha256>` fails
- **THEN** the script exits 1 before extracting the submission zip and prints a message instructing the operator to run `docker load < evaluator-portable_<sha>.tar.zst`

#### Scenario: Port occupied

- **WHEN** another process is already bound to port 8080 or 8554 at script start
- **THEN** the script exits 1 before extracting the submission zip and prints the conflicting PID; it does NOT kill the conflicting process automatically

#### Scenario: Result file ownership

- **WHEN** the script completes with default `--user $(id -u):$(id -g)`
- **THEN** every file written under `results/<team_id>_<ts>/` is owned by the invoking user, allowing `rm` and `chmod` without `sudo`

### Requirement: Container Network Model

The target host SHALL invoke the portable OCI container with `--network host` so that the container and the host share a single Linux network namespace. The contestant frontend running on the host at port 8080 SHALL be reachable from inside the container via `http://127.0.0.1:8080`, and MediaMTX running inside the container on port 8554 SHALL be reachable from the host (and from the contestant process) via `rtsp://127.0.0.1:8554`. The image MUST NOT publish ports explicitly (no `-p 8554:8554`); explicit port publishing is incompatible with `--network host` and is reserved as an unsupported alternative. Inside the container, `runner.py` MUST continue to use `http://127.0.0.1:8080` exactly as in the non-containerized invocation; the image MUST NOT require any new environment variable for host discovery (no `host.docker.internal`, no `EVALUATOR_FRONTEND_HOST`).

#### Scenario: Localhost reachability is symmetric

- **WHEN** the container is running with `--network host` and MediaMTX is up
- **THEN** a host-side `ffprobe -rtsp_transport tcp rtsp://127.0.0.1:8554/h264` succeeds and an in-container Playwright Chromium navigating `http://127.0.0.1:8080/play?codec=h264&autoplay=1` reaches the host contestant frontend

#### Scenario: runner.py source unchanged

- **WHEN** the change is implemented
- **THEN** `runner.py` contains no new references to `host.docker.internal` or any frontend-host environment variable; the only network-relevant strings in `runner.py` remain those that the pre-change version contained

### Requirement: Image Versioning and Verification

`scripts/evaluator-host.sh` SHALL read `image_sha256` from a `manifest.json` co-located with itself and SHALL use that value (and no fallback) when invoking `docker image inspect` and `docker run`. The script MUST refuse to operate against `evaluator-portable:latest` or any tag other than the one matching `image_sha256`. The script itself is NOT version-locked to the manifest, so it MAY be hot-fixed independently of the image as long as its observable contract is preserved.

#### Scenario: Manifest sha lock honored

- **WHEN** two image versions are loaded on the same target host and the operator runs `evaluator-host.sh` with the `manifest.json` corresponding to one of them
- **THEN** the run uses the image whose sha matches `manifest.json`, regardless of which image is most recently loaded or tagged `:latest`

#### Scenario: Tampered or missing manifest

- **WHEN** `manifest.json` is absent or `image_sha256` does not match any loaded image
- **THEN** the script exits 1 before extracting the submission zip and prints a message naming the expected sha

### Requirement: Concurrent Run Mutex

`scripts/evaluator-host.sh` SHALL enforce single-instance execution on a given target host by acquiring `flock -n /var/tmp/evaluator-host.lock` at startup. The lock file path is fixed; no fallback chain is provided (the target host is assumed single-operator and not multi-user-shared). On lock acquisition failure the script SHALL exit with code 75 (`EX_TEMPFAIL`) and print the PID of the lock holder. The script SHALL NOT block-wait or queue.

#### Scenario: Second invocation refuses

- **WHEN** one `evaluator-host.sh` invocation is mid-run and another invocation starts
- **THEN** the second invocation exits 75 immediately and prints the first invocation's PID

#### Scenario: Lock auto-released on signal

- **WHEN** an `evaluator-host.sh` invocation is killed with SIGTERM during the contestant startup poll
- **THEN** the flock is released by the kernel, and a fresh invocation can proceed immediately

### Requirement: Failure Score on Contestant Unavailable

When `scripts/evaluator-host.sh` polls the H.264 autoplay URL and the contestant frontend does not become reachable within 60 seconds, the script SHALL invoke a short docker run that calls the in-image `scorer.py` with `--failure-reason contestant_frontend_unavailable` to write a minimal `score.json` and `report.html` into the results directory, then exit with code 2. The score JSON written through this path MUST share schema with a normal scoring run (no jq-assembled JSON, no manual JSON construction in the host script). The contestant `stop.sh` and process-group cleanup MUST still execute before exit.

#### Scenario: Frontend never becomes ready

- **WHEN** the contestant `start.sh` runs but `http://127.0.0.1:8080/play?codec=h264&autoplay=1` is unreachable for 60 seconds
- **THEN** `evaluator-host.sh` exits 2, `score.json` exists in the results directory with `objective_total = 0` and `reason = "contestant_frontend_unavailable"`, and port 8080 is free after cleanup

### Requirement: Build Host Local Shortcut

`scripts/evaluator-local.sh <team_id> <submission_zip>` SHALL be a build-host local-development shortcut that performs the same host-side orchestration as `evaluator-host.sh` (lock, precheck ports, extract submission, run contestant `start.sh`, poll readiness, cleanup) but invokes `scripts/evaluator.sh` directly in the host venv instead of via `docker run`. It SHALL share usage, argument names, exit codes (0 / 1 / 2 / 75), and failure-score semantics with `evaluator-host.sh`. It SHALL NOT depend on docker being installed.

#### Scenario: Equivalence with containerized run

- **WHEN** the same `reference.zip` is evaluated via `evaluator-local.sh` on the build host and via `evaluator-host.sh` on a target host with the same image sha
- **THEN** the two `score.json` files are equivalent (identical `objective_total`, identical per-codec subtotals, SSIM scores within float-rounding tolerance)

### Requirement: Target Host Prerequisites

The target host SHALL require only: x86_64 Linux with a kernel and glibc compatible with `ubuntu:24.04`; docker engine `>= 20.10` (or rootful podman of equivalent capability); the `zstd` command-line tool (or, if `package.sh` was invoked with `--gzip`, the standard `gzip`); and whatever runtimes the contestant submission itself bundles. No apt install of evaluator-side toolchain, no Python interpreter on the host, no Playwright install, no submodule checkout. The target host MUST NOT be required to be online during evaluation. Unsupported target configurations include macOS, Windows, arm64, and rootless podman.

#### Scenario: Clean target host accepts the bundle

- **WHEN** dist files are copied to a clean Ubuntu 24.04 host whose only evaluator-related installations are `docker` and `zstd`
- **THEN** `docker load < image.tar` + `evaluator-host.sh team_ref reference.zip` runs to completion and produces a valid `score.json`

#### Scenario: Offline evaluation

- **WHEN** the target host has no outbound network connectivity at evaluation time
- **THEN** `evaluator-host.sh` completes evaluation against a locally-served contestant submission without errors attributable to network access

### Requirement: Portable Self-Test Mode

`scripts/test.sh --portable` SHALL exercise the entire bundle path end-to-end. It MUST run, in order: `package.sh` and verify the dist files; on a second machine identified by `EVAL_TARGET_HOST=user@host`, copy the dist files and run `evaluator-host.sh team_ref reference.zip`; on the build host run `evaluator-local.sh team_ref reference.zip` and diff its `score.json` against the target-host run (keys identical, numeric values within float tolerance); run a known-broken fixture on the target host and assert exit code 2 plus `reason="contestant_frontend_unavailable"` in `score.json`. The `--portable` mode SHALL NOT fall back to nested docker, and SHALL exit non-zero if `EVAL_TARGET_HOST` is unset.

#### Scenario: Portable self-test passes

- **WHEN** a developer runs `scripts/test.sh --portable` with `EVAL_TARGET_HOST` pointing to a healthy second Ubuntu 24.04 host
- **THEN** all four stages exit zero, score parity is confirmed, and the negative fixture exits 2 with the expected reason
