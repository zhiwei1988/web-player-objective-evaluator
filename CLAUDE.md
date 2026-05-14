<!-- last synced: 2026-05-14 -->

## Overview

Host-side automated scorer for the 40-point objective portion of a web plugin-free real-time media player challenge. Generates watermarked H.264/H.265 RTSP streams, drives each contestant submission through Playwright Chromium, compares decoded frames against watermarked references, and emits `score.json` + internal `report.html`. Built with Python 3.12 (Playwright, Pillow, NumPy, scikit-image, pylibdmtx, pytesseract) plus source-built C/C++/Go toolchain (ffmpeg, mediamtx, tesseract, leptonica, libdmtx, x264, x265) on Ubuntu 24.04. Organizer-internal only.

Scoring breakdown: H.264 round 15 pts (10 correctness + 5 fps), H.265 round 15 pts (10 correctness + 5 fps), plus a 10-pt contestant **CPU usage sub-score** sampled during the H.265 capture window only. CPU is measured on the contestant's PGID process group (set via `setsid` in the host wrapper) and normalized to integer machine all-cores total (one saturated core ≈ 100/ncpu percent). The CPU sub-score requires H.265 fps to clear `score_fps`'s partial-credit threshold (default `measured/expected ≥ 0.25`) — otherwise it's gated to 0. All six tunables live as named constants in `scorer.py` / `_cpu_sampler.py`; the values actually applied to each run are echoed into `score.json.cpu.thresholds_used`. Sampling is **host-native only** (runs in `runner.py` against host `/proc`); `evaluator-host.sh` container path currently lacks a host-/proc bridge, so CPU samples won't appear under that path until the packaging revisit lands.

Two execution modes:
- **Native build host**: clone repo, run `setup.sh → build.sh → deploy.sh`, then `scripts/evaluator-local.sh <team_id> <submission.zip>`. Used for development and self-test.
- **Portable OCI bundle**: on the build host, run `scripts/package.sh` to produce `dist/` (a `docker save` archive + host wrapper + manifest). Copy `dist/` to any docker-equipped Ubuntu 24.04 host and run `./evaluator-host.sh <team_id> <submission.zip>`. Target host needs only docker + zstd.

## Structure

```
.
├── runner.py / analyzer.py / scorer.py / report.py   # Python pipeline modules at root
├── lib/watermark.py                                  # reference-frame generator (CLI)
├── Dockerfile / .dockerignore        # multi-stage portable image (COPY-only builder)
├── scripts/
│   ├── setup.sh build.sh deploy.sh test.sh teardown.sh   # build-host lifecycle
│   ├── evaluator.sh                                       # evaluator main body (container or native; called by wrappers)
│   ├── evaluator-host.sh                                  # target-host operator entry (uses docker)
│   ├── evaluator-local.sh                                 # build-host dev shortcut (no docker)
│   ├── _contestant_lifecycle.sh                           # shared helper sourced by both wrappers
│   ├── package.sh                                         # produces dist/ portable bundle
│   ├── prepare_streams.sh start_rtsp.sh health_check.sh   # internal helpers
│   ├── build_test_zips.sh                                 # rebuilds test_submissions/*.zip from src/
│   └── env.sh                                             # shared shell env (paths, venv)
├── rtsp_server/mediamtx.yml          # config only; binary lives at third_party/install/bin/mediamtx
├── third_party/                      # 7 git submodules, all source-built into third_party/install/
├── reference/{h264,h265}/            # generated reference PNGs (git-ignored)
├── streams/                          # generated watermarked mp4 (git-ignored)
├── submissions/                      # per-run contestant staging (git-ignored)
├── results/<team>_<ts>/              # per-run artifacts (git-ignored)
├── test_submissions/                 # src/ bundled, *.zip generated
├── .playwright/                      # rsync'd from ~/.cache/ms-playwright/ for docker build (git-ignored)
├── dist/                             # portable bundle output (git-ignored)
├── requirements.txt                  # pinned: playwright 1.49, pillow 10.4, numpy 1.26.4, scikit-image 0.24, pylibdmtx, pytesseract
└── openspec/                         # OpenSpec change/spec workflow (specs/, changes/, config.yaml)
```

## Key Abstractions

| Name | Location | Role |
|------|----------|------|
| `WatermarkLayout` / `draw_watermark` | `lib/watermark.py` | Per-frame overlay: timecode text, DataMatrix barcode encoding frame number, color-block ring; deterministic from `(frame_number, fps)` |
| `CaptureResult` / `run_capture` | `runner.py` | Playwright session: navigates contestant frontend `/play?codec=…&autoplay=1`, waits for `window.__PLAYER_READY__`, screenshots `[data-testid="player-video"]` at target fps |
| `ShotMetric` / `CodecMetrics` / `analyze` | `analyzer.py` | Per-shot: `_decode_datamatrix` → `_ocr_frame_number` fallback, `check_color_blocks`, `compute_ssim` against reference |
| `CodecScore` / `score_correctness` / `score_fps` / `build_score` | `scorer.py` | Maps `(watermark_rate, color_rate, mean_ssim, measured_fps)` to integer points; records Chromium build string from `playwright` install |
| `render_report` | `report.py` | Builds standalone HTML with embedded base64 thumbs, frame-number trace SVG, SSIM histogram, suspicious-frame gallery |

## Data Flow

Per-run pipeline (driven by `evaluator-local.sh` natively, or `evaluator-host.sh` inside the OCI container):

1. `scripts/deploy.sh` → `lib/watermark.py` generates `reference/{codec}/frame_NNNNN.png` and mp4 in `streams/`; `scripts/prepare_streams.sh` muxes via ffmpeg. (Build-host one-time step. The portable bundle bakes these into the image so target hosts skip this.)
2. **Host-side wrapper** (`evaluator-host.sh` or `evaluator-local.sh`): acquire flock `/var/tmp/evaluator-host.lock`, precheck ports (8080 always; 8554 only for `-host.sh`), unzip submission to `submissions/<team_id>/`, export `RTSP_SERVER_HOST=127.0.0.1 RTSP_SERVER_PORT=8554 FRONTEND_PORT=8080`, `setsid ./start.sh` on the host, poll `http://127.0.0.1:8080/play?codec=h264&autoplay=1` up to 60 s.
3. **Evaluator main body** (`scripts/evaluator.sh <team_id> <results_subdir>`, called by the wrapper; runs inside the container for `-host.sh` path): start MediaMTX via `start_rtsp.sh` (per-run; killed at exit), `health_check.sh` both codecs.
4. `runner.py` (Playwright Chromium / Google Chrome via `channel="chrome"`) opens contestant frontend per codec → captures screenshots into `results/<run>/{h264,h265}_screenshots/`.
5. `analyzer.py` reads screenshots + matching reference PNGs → emits `{codec}_metrics.json` (per-frame DataMatrix/OCR ID, color-block correctness, SSIM, measured fps).
6. `scorer.py` consumes both metrics jsons + submodule install prefix → writes `score.json` (30-pt breakdown + toolchain fingerprint).
7. `report.py` consumes the same metrics + run dir → writes `report.html` (internal only).
8. **Host-side cleanup**: contestant `stop.sh` (with 10 s timeout), `kill -- -<pgid>`, `fuser -k 8080/tcp 8554/tcp`, release flock. Always emits a `score.json` even on host-side failures (extract / readiness / docker), via either local `.venv/bin/python scorer.py --failure-reason …` or a short `docker run … scorer.py --failure-reason …`.

Portable bundle path: `scripts/package.sh` snapshots build-host assets into an OCI image (`evaluator-portable:<git-sha>`), saves as `dist/evaluator-portable_<sha>.tar.zst`, and emits `dist/manifest.json` + `dist/SHA256SUMS` + `dist/README.md` + `dist/evaluator-host.sh` + `dist/_contestant_lifecycle.sh`. Target hosts `docker load < image.tar.zst` then run `evaluator-host.sh`. Inside the container the data flow above (steps 3–7) runs unchanged; MediaMTX is now per-container-run (no host-resident daemon).

## Entry Points

| Entry | File | Description |
|-------|------|-------------|
| Target-host operator (containerized) | `scripts/evaluator-host.sh` | `<team_id> <submission.zip> [--root] [--manifest <path>]`; reads `manifest.json`'s `image_sha256`, runs evaluator inside the loaded OCI image via `docker run --network host` |
| Build-host dev (native) | `scripts/evaluator-local.sh` | `<team_id> <submission.zip>`; same UX as `evaluator-host.sh` but bypasses docker, invokes in-tree `scripts/evaluator.sh` directly |
| Evaluator main body | `scripts/evaluator.sh` | `<team_id> <results_subdir>`; MediaMTX + runner × 2 + analyzer × 2 + scorer + report. Called by the wrappers above; do not invoke directly for full evaluation |
| Stream prep / build-host deploy | `scripts/deploy.sh` | Generates streams (if stale), starts MediaMTX, RTSP health-check. Build-host one-time step |
| Portable bundle build | `scripts/package.sh` | `[--gzip]`; emits `dist/` (image + host wrapper + manifest + SHA256SUMS + README) |
| Capture CLI | `runner.py` (`_cli`) | Standalone Playwright capture for one codec |
| Metrics CLI | `analyzer.py` (`_cli`) | Standalone scoring of an existing screenshots dir |
| Score CLI | `scorer.py` (`_cli`) | Combine codec metrics → `score.json` |
| Watermark CLI | `python -m lib.watermark` | Generate reference frame sequence |
| Self-test | `scripts/test.sh [--portable]` | Default mode: 7 fixtures via `evaluator-local.sh`. `--portable` mode: end-to-end against `EVAL_TARGET_HOST` |

## Dev Commands

Build host (development + bundle production):

| Command | Purpose |
|---------|---------|
| `./scripts/setup.sh` | Once per host: apt toolchain, Python venv, `git submodule update --init --recursive` |
| `./scripts/build.sh` (`--clean` to force) | Build all submodules into `third_party/install/`, pip install, `playwright install chromium` |
| `./scripts/deploy.sh` | Regenerate streams if missing, start MediaMTX, RTSP health-check |
| `./scripts/evaluator-local.sh <team_id> <submission.zip>` | Native (no docker) scoring shortcut; writes `results/<team>_<ts>/` |
| `./scripts/package.sh [--gzip]` | Produce `dist/` portable bundle. Requires docker on build host (`sg docker -c ...` if uid hasn't been re-logged into the `docker` group) |
| `./scripts/test.sh` | Default-mode self-test (7 fixtures via `evaluator-local.sh`; auto deploys + tears down) |
| `./scripts/test.sh --portable` | Full bundle e2e against `$EVAL_TARGET_HOST`. Requires that target host to have docker + zstd + an SSH key configured; intended for verification, not routine development |
| `./scripts/teardown.sh` | Stop host-resident MediaMTX and free ports — opposite of `deploy.sh` |

Target host (operator, after `scp dist/* <target>:.` and `docker load`):

| Command | Purpose |
|---------|---------|
| `./evaluator-host.sh <team_id> <submission.zip>` | Score one contestant via the loaded OCI image; writes `results/<team>_<ts>/` relative to PWD |
| `./evaluator-host.sh --root <team_id> <submission.zip>` | Same, but run container as root (default is `--user $(id -u):$(id -g)`); escape hatch |

Diagnostic:

| Command | Purpose |
|---------|---------|
| `.venv/bin/python <module>.py --help` | Each pipeline stage is independently runnable |
| `git submodule status` | Source of truth for which toolchain commit produced any given score |
| `jq .image_sha256 dist/manifest.json` | sha-locked image identifier; matched against the loaded docker image by `evaluator-host.sh` at startup |

## Conventions

- **Source-build rule**: do NOT use apt-installed `ffmpeg`/`mediamtx`/`tesseract`/`leptonica`/`libdmtx`/`x264`/`x265`. All consumed from pinned submodules → `third_party/install/`. Documented exceptions: Python wheels (pip) and Chromium (Playwright-pinned revision). Inside the OCI image, the source-built `third_party/install/lib` is registered with `ldconfig` via `/etc/ld.so.conf.d/evaluator.conf` so `ctypes.util.find_library` (used by pylibdmtx) finds it without needing gcc/ld in the runtime stage.
- **Idempotent scripts**: `setup.sh`/`build.sh`/`deploy.sh`/`package.sh`/`evaluator-host.sh`/`evaluator-local.sh`/`evaluator.sh` are all safe to re-run. `evaluator-host.sh` and `evaluator-local.sh` are also flock-mutex'd via `/var/tmp/evaluator-host.lock` so concurrent runs on the same host fail-fast with exit 75 rather than corrupting each other's port state.
- **Pipeline stages stay independently runnable**: every Python module has `_cli()` + `if __name__ == "__main__"`. New stages MUST preserve this — `scripts/evaluator.sh` invokes them as separate `python module.py` subprocesses, not as imports.
- **Contestant contract is frozen**: `start.sh`/`stop.sh` at zip root; env vars `RTSP_SERVER_HOST/PORT`, `FRONTEND_PORT=8080`; frontend at `/play?codec=<h264|h265>&autoplay=1` rendering into `[data-testid="player-video"]` (≥1280x720) with `window.__PLAYER_READY__` / `window.__PLAYER_ERROR__` signals. Whatever the contestant runs internally (relay backends, decode workers, etc.) is their business; the framework does NOT export a backend-port hint and does NOT clean any port other than 8080/8554. **The contestant runs on the host** (native process group), not inside the container, even when scored via `evaluator-host.sh`; the container only carries the evaluator's MediaMTX + Playwright + Python pipeline.
- **Canonical evaluation host**: Ubuntu 24.04 + Google Chrome, no hardware HEVC decoder. Chrome on this config reports `supported: false` for HEVC in both `<video>` and `WebCodecs`. The bundled `reference.zip` self-test uses `<video>` only and therefore fails the H.265 round on this host; `scripts/test.sh` gates the reference at `total_ge:13` (H.264 full = 15, H.265 = 0). The evaluator itself is codec-agnostic — it just screenshots `[data-testid="player-video"]` — so contestants are free to choose any rendering path. **Do not** add contestant-facing prescriptions about "what they should do" to spec/README/contestant docs; the contract is the element + readiness signals only.
- **Ports 8554 (RTSP) and 8080 (contestant frontend)** are the evaluator's reserved set and must be free before a run; the wrappers and `scripts/teardown.sh` clean only these. With `--network host` the container's MediaMTX and the host's contestant frontend share the same loopback, so `127.0.0.1:8080` / `127.0.0.1:8554` references in `runner.py` and the contestant work unchanged. Playwright drives Chrome over its own internal CDP channel — no fixed remote-debugging port is exposed.
- **MediaMTX lifecycle**: per-run, owned by `scripts/evaluator.sh`. Start at evaluator-body entry, kill on every exit path via cleanup trap. No longer a host-resident daemon shared across runs (the previous "deploy.sh starts MediaMTX; evaluator.sh inherits" pattern is gone). `scripts/deploy.sh`'s `start_rtsp.sh` is now only needed for build-host development conveniences (interactive testing); it's no-op effectively because the next `evaluator-local.sh` invocation will replace its instance.
- **Reports are internal**. Contestants only ever see `score.json`. Anything in `report.html` is free to be diagnostic / suspicious-frame-revealing.
- **Portable bundle target-host prerequisites**: docker engine ≥ 20.10 (or rootful podman of equivalent capability) and `zstd`. Nothing else — no apt install of evaluator toolchain, no system Python, no Playwright install, no submodule checkout, no internet at evaluation time. Unsupported configurations: macOS / Windows hosts, arm64, rootless podman. The image is sha-locked: `evaluator-host.sh` refuses to run unless the loaded image matches `manifest.json`'s `image_sha256` exactly (no `latest` fallback).
- **Build-host docker prerequisites**: docker engine ≥ 20.10 with daemon proxy configured if outbound access is gated. `scripts/package.sh` uses `docker build --network host` so apt/curl inside RUN steps can reach host-bound proxies on 127.0.0.1; and the image's runtime stage `apt install`s Google Chrome from `dl.google.com` (needed because `runner.py` launches via `channel="chrome"` for H.264/H.265 codec licensing — the Playwright-bundled Chromium Headless Shell lacks the proprietary codec build).
- **Generated dirs are git-ignored**: `streams/`, `reference/`, `submissions/`, `results/`, `third_party/install/`, `test_submissions/*.zip`, `.playwright/`, `dist/`. Don't commit run artifacts or bundle outputs.
- **Path discipline**: every shell script computes `SCRIPT_DIR` (= `scripts/`) then `ROOT_DIR="${SCRIPT_DIR}/.."` and sources `scripts/env.sh`. **Exception**: `scripts/evaluator-host.sh` sets `ROOT_DIR="${PWD}"` so target-host operators can `cd` into any working directory and have `submissions/` and `results/` created there, decoupled from where the script binary lives. Don't hardcode `evaluator/` — that subdirectory was removed in the May 11 layout flatten.
- **`openspec/`** holds the OpenSpec workflow state (changes/, specs/). Treat as orthogonal to runtime code; use `/opsx:*` skills, not direct edits.
