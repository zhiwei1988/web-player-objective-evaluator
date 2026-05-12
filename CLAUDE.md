<!-- last synced: 2026-05-11 -->

## Overview

Host-side automated scorer for the 30-point objective portion of a web plugin-free real-time media player challenge. Generates watermarked H.264/H.265 RTSP streams, drives each contestant submission through Playwright Chromium, compares decoded frames against watermarked references, and emits `score.json` + internal `report.html`. Built with Python 3.12 (Playwright, Pillow, NumPy, scikit-image, pylibdmtx, pytesseract) plus source-built C/C++/Go toolchain (ffmpeg, mediamtx, tesseract, leptonica, libdmtx, x264, x265) on Ubuntu 24.04. Organizer-internal only.

## Structure

```
.
├── runner.py / analyzer.py / scorer.py / report.py   # Python pipeline modules at root
├── lib/watermark.py                                  # reference-frame generator (CLI)
├── scripts/
│   ├── setup.sh build.sh deploy.sh evaluator.sh test.sh teardown.sh   # 6-script lifecycle + self-test
│   ├── prepare_streams.sh start_rtsp.sh health_check.sh    # internal helpers
│   ├── build_test_zips.sh                                  # rebuilds test_submissions/*.zip from src/
│   └── env.sh                                              # shared shell env (paths, venv)
├── rtsp_server/mediamtx.yml          # config only; binary lives at third_party/install/bin/mediamtx
├── third_party/                      # 7 git submodules, all source-built into third_party/install/
├── reference/{h264,h265}/            # generated reference PNGs (git-ignored)
├── streams/                          # generated watermarked mp4 (git-ignored)
├── submissions/                      # per-run contestant staging (git-ignored)
├── results/<team>_<ts>/              # per-run artifacts (git-ignored)
├── test_submissions/                 # src/ bundled, *.zip generated
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

1. `scripts/deploy.sh` → `lib/watermark.py` generates `reference/{codec}/frame_NNNNN.png` and mp4 in `streams/`; `scripts/prepare_streams.sh` muxes via ffmpeg; MediaMTX serves them on `rtsp://127.0.0.1:8554/{h264,h265}`.
2. `scripts/evaluator.sh <team_id> <zip>` unpacks submission to `submissions/<team_id>/`, exports `RTSP_SERVER_HOST/PORT`, `FRONTEND_PORT=8080`, `BACKEND_PORT=8081`, runs `start.sh`.
3. `runner.py` (Playwright Chromium) opens contestant frontend per codec → captures screenshots into `results/<run>/{h264,h265}_screenshots/`.
4. `analyzer.py` reads screenshots + matching reference PNGs → emits `{codec}_metrics.json` (per-frame DataMatrix/OCR ID, color-block correctness, SSIM, measured fps).
5. `scorer.py` consumes both metrics jsons + submodule install prefix → writes `score.json` (30-pt breakdown + toolchain fingerprint).
6. `report.py` consumes the same metrics + run dir → writes `report.html` (internal only).
7. `scripts/evaluator.sh` calls contestant `stop.sh`, frees ports, logs to `evaluator.log`.

## Entry Points

| Entry | File | Description |
|-------|------|-------------|
| Per-contestant run | `scripts/evaluator.sh` | Full lifecycle for one submission zip; orchestrates runner/analyzer/scorer/report |
| Stream prep | `scripts/deploy.sh` | Generates streams (if stale), starts MediaMTX, RTSP health-check |
| Capture CLI | `runner.py` (`_cli`) | Standalone Playwright capture for one codec |
| Metrics CLI | `analyzer.py` (`_cli`) | Standalone scoring of an existing screenshots dir |
| Score CLI | `scorer.py` (`_cli`) | Combine codec metrics → `score.json` |
| Watermark CLI | `python -m lib.watermark` | Generate reference frame sequence |
| Self-test | `scripts/test.sh` | Runs reference + negative fixtures end-to-end |

## Dev Commands

| Command | Purpose |
|---------|---------|
| `./scripts/setup.sh` | Once per host: apt toolchain, Python venv, `git submodule update --init --recursive` |
| `./scripts/build.sh` (`--clean` to force) | Build all submodules into `third_party/install/`, pip install, `playwright install chromium` |
| `./scripts/deploy.sh` | Regenerate streams if missing, start MediaMTX, RTSP health-check |
| `./scripts/evaluator.sh <team_id> <submission.zip>` | Score one contestant; writes `results/<team>_<ts>/` |
| `./scripts/test.sh` | Bundled self-test suite (always run after evaluator changes; auto deploys + tears down) |
| `./scripts/teardown.sh` | Stop MediaMTX and free ports — opposite of `deploy.sh` |
| `.venv/bin/python <module>.py --help` | Each pipeline stage is independently runnable |
| `git submodule status` | Source of truth for which toolchain commit produced any given score |

## Conventions

- **Source-build rule**: do NOT use apt-installed `ffmpeg`/`mediamtx`/`tesseract`/`leptonica`/`libdmtx`/`x264`/`x265`. All consumed from pinned submodules → `third_party/install/`. Documented exceptions: Python wheels (pip) and Chromium (Playwright-pinned revision).
- **Idempotent scripts**: `setup.sh`/`build.sh`/`deploy.sh`/`evaluator.sh` are all safe to re-run. Do not introduce stateful first-run-only branches.
- **Pipeline stages stay independently runnable**: every Python module has `_cli()` + `if __name__ == "__main__"`. New stages MUST preserve this — `scripts/evaluator.sh` invokes them as separate `python module.py` subprocesses, not as imports.
- **Contestant contract is frozen**: `start.sh`/`stop.sh` at zip root; env vars `RTSP_SERVER_HOST/PORT`, `FRONTEND_PORT=8080`; frontend at `/play?codec=<h264|h265>&autoplay=1` rendering into `[data-testid="player-video"]` (≥1280x720) with `window.__PLAYER_READY__` / `window.__PLAYER_ERROR__` signals. Whatever the contestant runs internally (relay backends, decode workers, etc.) is their business; the framework does NOT export a backend-port hint and does NOT clean any port other than 8080/8554.
- **Canonical evaluation host**: Ubuntu 24.04 + Google Chrome, no hardware HEVC decoder. Chrome on this config reports `supported: false` for HEVC in both `<video>` and `WebCodecs`. The bundled `reference.zip` self-test uses `<video>` only and therefore fails the H.265 round on this host; `scripts/test.sh` gates the reference at `total_ge:13` (H.264 full = 15, H.265 = 0). The evaluator itself is codec-agnostic — it just screenshots `[data-testid="player-video"]` — so contestants are free to choose any rendering path. **Do not** add contestant-facing prescriptions about "what they should do" to spec/README/contestant docs; the contract is the element + readiness signals only.
- **Ports 8554 (RTSP) and 8080 (contestant frontend)** are the evaluator's reserved set and must be free before a run; `scripts/evaluator.sh` and `scripts/teardown.sh` clean only these. Playwright drives Chrome over its own internal CDP channel — no fixed remote-debugging port is exposed.
- **Reports are internal**. Contestants only ever see `score.json`. Anything in `report.html` is free to be diagnostic / suspicious-frame-revealing.
- **Generated dirs are git-ignored**: `streams/`, `reference/`, `submissions/`, `results/`, `third_party/install/`, `test_submissions/*.zip`. Don't commit run artifacts.
- **Path discipline**: every shell script computes `SCRIPT_DIR` (= `scripts/`) then `ROOT_DIR="${SCRIPT_DIR}/.."` and sources `scripts/env.sh`. Don't hardcode `evaluator/` — that subdirectory was removed in the May 11 layout flatten.
- **`openspec/`** holds the OpenSpec workflow state (changes/, specs/). Treat as orthogonal to runtime code; use `/opsx:*` skills, not direct edits.
