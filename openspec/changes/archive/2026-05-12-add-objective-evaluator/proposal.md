## Why

The web plugin-free real-time media player challenge needs a host-side automated evaluator that can score the 30-point objective portion of each contestant submission reproducibly and resistantly to cheating. Without it, organizers cannot scale judging, cannot guarantee that two runs of the same submission produce the same score, and cannot defend a score against contestant appeals with concrete evidence.

This change introduces that evaluator: a project-root tooling workspace that drives a known-good RTSP stream into the contestant package, captures playback through Playwright, recognizes watermarked reference frames, and produces a score JSON plus audit report. It is for organizer/internal use only and is not exposed to contestants.

## What Changes

- Establish the evaluator at the repository root (no `evaluator/` subdirectory): Python modules and `lib/`, `rtsp_server/`, `third_party/`, `streams/`, `reference/`, `submissions/`, `results/`, `test_submissions/` directories sit at the top level; all shell scripts live under `scripts/`.
- Add five lifecycle scripts in `scripts/` with non-overlapping responsibilities: `setup.sh` (one-time host bootstrap: apt build toolchain, Python venv, `git submodule update --init --recursive`), `build.sh` (compile all third-party submodules to `third_party/install/`, install Python deps into the venv, install Playwright Chromium), `deploy.sh` (generate watermarked streams if missing, start RTSP server, verify stream health), `evaluator.sh` (per-submission main entry), and `test.sh` (run the automated validation suite against the reference and negative-case submissions). All MUST be idempotent.
- Manage every open-source C/C++/Go dependency as a git submodule under `third_party/<name>/` and build from source: `ffmpeg`, `mediamtx`, `tesseract`, `leptonica` (tesseract's image-IO dep), `libdmtx`, plus `x264` and `x265` (ffmpeg's H.264/H.265 encoders). No `apt install` of these libraries on the evaluator host. Build artifacts install under `third_party/install/{bin,lib,include}`; `scripts/env.sh` extends `PATH`, `LD_LIBRARY_PATH`, and `PKG_CONFIG_PATH` so the evaluator uses those binaries, not anything system-wide.
- Python packages remain pip-installed from pinned versions in `requirements.txt` (pip+sdist is already a source-build path). Chromium is the documented exception: we pin the Playwright-bundled Chromium revision instead of building Chromium from source.
- Add `scripts/evaluator.sh` as the main per-submission entry point invoked as `./scripts/evaluator.sh <team_id> <submission_zip>`.
- Add `scripts/prepare_streams.sh` plus `lib/watermark.py` to pre-generate watermarked H.264 (`1920x1080@30fps`, libx264 crf 18, 30s) and H.265 (`2560x1440@25fps`, libx265 4Mbps, 30s) MP4 files and PNG reference frame sequences.
- Embed in every reference frame: 5-digit frame-number block (top-left), `HH:MM:SS.mmm` timecode (top-right), four fixed RGB color blocks `(255,0,0)/(0,255,0)/(0,0,255)/(255,255,255)` at the bottom, and a DataMatrix code (bottom-right) encoding the frame number.
- Add `rtsp_server/mediamtx.yml` plus `scripts/start_rtsp.sh` and `scripts/health_check.sh` that publish `rtsp://localhost:8554/test/h264` and `rtsp://localhost:8554/test/h265` over RTSP-TCP, using `ffmpeg -re -stream_loop -1 -c:v copy` to loop without re-encoding.
- Define the contestant runtime contract: `start.sh`, optional `stop.sh`, env vars `RTSP_SERVER_HOST` / `RTSP_SERVER_PORT` / `FRONTEND_PORT`, frontend route `/play?codec=<codec>&autoplay=1`, render element `[data-testid="player-video"]` rendered at least `1280x720` and proportional, and window signals `__PLAYER_READY__` / `__PLAYER_ERROR__`. Internal contestant processes (relay backends, decode workers, etc.) are not in the contract — they can use any port that doesn't collide with the evaluator's reserved set.
- Add `runner.py` Playwright runner that launches headless Chromium per codec with `--autoplay-policy=no-user-gesture-required`, waits up to 15s for `__PLAYER_READY__`, samples element-only screenshots at 30 Hz for 30 seconds, and writes `shot_NNNNN.png` plus `timestamps.json`.
- Add `analyzer.py` that reads screenshots, extracts the frame number via DataMatrix (with OCR fallback), looks up the matching reference frame, runs SSIM after resizing, validates the four bottom color blocks within tolerance, and computes recognition rate, color rate, mean SSIM, unique-frame count, and measured FPS — frame-matching keyed by watermark frame number, not screenshot index.
- Add `scorer.py` and `report.py` that score each codec out of 15 (10 correctness + 5 FPS) for a total of 30, write `score.json`, and render an internal-only `report.html` with summary, suspicious-screenshot gallery, frame-number-over-time chart, SSIM histogram, and links to raw artifacts.
- Implement orchestration in `scripts/evaluator.sh`: create `results/<team_id>_<timestamp>/`, log to `evaluator.log`, clean ports `8080/8081/9000` and stale processes, start RTSP server, ffprobe-verify stream health, extract submission, export env vars, start contestant `start.sh`, poll H.264 autoplay URL up to 60s, run H.264 then H.265 capture in fresh contexts, analyze + score + report, run contestant `stop.sh`, clean again, print score JSON. Cleanup function + traps defined before any step that can fail.
- Add dependency setup documentation: host-only apt packages limited to compiler/toolchain (`build-essential`, `cmake`, `autoconf`, `automake`, `libtool`, `pkg-config`, `nasm`, `yasm`, `golang-go`, `python3-venv`, `lsof`, `unzip`); Python packages pinned in `requirements.txt` (`playwright>=1.40`, `pillow>=10.0`, `numpy`, `scikit-image`, `pylibdmtx`, `pytesseract`); all other open-source dependencies built from git-submoduled sources via `scripts/build.sh`.
- Add validation tasks: reference contestant submission that should score near full marks; negative submissions (static frame, I-frame-only / repeated frame, fake canvas overlay, missing `start.sh`, frontend never ready, missing `data-testid`) that should each produce the expected reduced score and a clear failure reason.

## Capabilities

### New Capabilities

- `evaluator`: Host-side objective evaluator for the web real-time media player challenge — covers stream generation, RTSP serving, contestant runtime contract, Playwright capture, image-based analysis, scoring thresholds, report generation, cleanup/isolation, and anti-cheating behaviors.

### Modified Capabilities

<!-- none — this is the first capability in the repo -->

## Impact

- The entire repo is the evaluator. New top-level entries: Python modules (`runner.py`, `analyzer.py`, `scorer.py`, `report.py`), `lib/`, `scripts/`, `rtsp_server/`, `third_party/`, generated dirs (`streams/`, `reference/`, `submissions/`, `results/`), and `test_submissions/`.
- New git submodules under `third_party/`: `ffmpeg`, `mediamtx`, `tesseract`, `leptonica`, `libdmtx`, `x264`, `x265`. Each pinned to a known-good upstream commit. `third_party/install/` is git-ignored and produced by `scripts/build.sh`.
- Host requirements on the evaluator machine: Ubuntu 24.04, a working C/C++ toolchain (gcc, make, cmake, autoconf, automake, libtool, pkg-config, nasm, yasm), a Go toolchain (for MediaMTX), Python 3 with venv, and the host utilities `lsof` and `unzip`. The Playwright-managed Chromium is downloaded by `scripts/build.sh` at a pinned revision. The evaluator MUST NOT depend on system-wide installations of ffmpeg, mediamtx, tesseract, leptonica, libdmtx, x264, or x265 — it uses only the binaries under `third_party/install/`.
- Fixed local ports during a run: `8554` (RTSP, evaluator-owned) and `8080` (contestant HTTP frontend, contract). The evaluator clears `8080` before and after every run; contestants are free to use any other port internally and are responsible for their own process trees (the evaluator does process-group SIGTERM/SIGKILL on cleanup as a backstop).
- Disk footprint per submission: one screenshot directory per codec (≈900 PNGs each at 30 Hz × 30s), two metrics JSONs, one score JSON, one HTML report, and one log file under `results/<team_id>_<timestamp>/`.
- No production code paths, no contestant-facing surfaces, and no networked services beyond `localhost` are affected.
