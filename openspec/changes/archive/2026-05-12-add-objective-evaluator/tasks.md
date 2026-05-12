## 1. Workspace Scaffold

- [x] 1.1 Create the directory tree at the repository root: `scripts/`, `lib/`, `rtsp_server/`, `third_party/`, `streams/`, `reference/h264/`, `reference/h265/`, `submissions/`, `results/`, `test_submissions/`, with `.gitkeep` files where appropriate so the tree survives an empty clone
- [x] 1.2 Add `requirements.txt` pinning `playwright>=1.40`, `pillow>=10.0`, `numpy`, `scikit-image`, `pylibdmtx`, `pytesseract` to specific versions known to work on Ubuntu 24.04
- [x] 1.3 Add `README.md` documenting the host toolchain prerequisites, the lifecycle scripts under `scripts/` (`setup.sh` → `build.sh` → `deploy.sh` → `evaluator.sh`), the submodule layout, and the Chromium / Python-package exceptions to the source-build rule
- [x] 1.4 Add `.gitignore` excluding `.venv/`, `third_party/install/`, all `third_party/*/build/` and `third_party/*/*.o`-style artifacts (case-by-case per submodule), `streams/*.mp4`, `reference/*/frame_*.png`, `submissions/*/`, `results/*/`, and `test_submissions/*.zip`

## 2. Third-Party Submodules

- [x] 2.1 Add git submodules under `third_party/`, each pinned to a specific tag or release commit: `ffmpeg` (upstream `git://source.ffmpeg.org/ffmpeg.git`), `mediamtx` (`https://github.com/bluenviron/mediamtx.git`), `tesseract` (`https://github.com/tesseract-ocr/tesseract.git`), `leptonica` (`https://github.com/DanBloomberg/leptonica.git`), `libdmtx` (`https://github.com/dmtx/libdmtx.git`). Also added `x264` and `x265` because they are transitive deps of ffmpeg's H.264/H.265 encoding and the same reproducibility argument applies.
- [x] 2.2 Verify `.gitmodules` records the URL and `git submodule status` lists the pinned SHAs for all five; document the chosen tag/commit per submodule in `README.md` so a reviewer can map a score back to a known toolchain
- [x] 2.3 Confirm `git submodule update --init --recursive` from a clean clone fetches all submodules without errors — **needs runtime verification on a clean clone**

## 3. Lifecycle Script: `setup.sh`

- [x] 3.1 Implement `scripts/setup.sh` that `apt install`s `build-essential cmake autoconf automake libtool pkg-config nasm yasm golang-go python3-venv lsof unzip` (with `sudo` and a friendly error if not root-capable)
- [x] 3.2 Create the Python venv at `.venv/` (at the repo root) if it does not exist (idempotent)
- [x] 3.3 Run `git submodule update --init --recursive` from the repo root
- [x] 3.4 Print a single final status line ("setup ok" or "setup failed: <reason>") and exit accordingly

## 4. Lifecycle Script: `build.sh`

- [x] 4.1 Implement `scripts/build.sh` argument parsing for an optional `--clean` flag that wipes `third_party/install/` and each submodule's build directory before rebuilding
- [x] 4.2 Build `leptonica` first via its autotools chain into `third_party/install/`; pass `--prefix=$(pwd)/third_party/install`
- [x] 4.3 Build `tesseract` against the just-installed leptonica (`PKG_CONFIG_PATH` extended to `third_party/install/lib/pkgconfig`); install into the same prefix
- [x] 4.4 Build `libdmtx` via its autotools chain; install into the same prefix
- [x] 4.5 Build `ffmpeg` with `./configure --prefix=... --enable-gpl --enable-libx264 --enable-libx265 --disable-doc --disable-network-fetching-flags`; document every enabled flag inline in `build.sh` so it is auditable. Also added `build_x264` and `build_x265` ahead of ffmpeg so the H.264/H.265 encoders come from source too.
- [x] 4.6 Build `mediamtx` with `go build` (using the Go toolchain installed by `setup.sh`); copy the resulting binary into `third_party/install/bin/mediamtx`
- [x] 4.7 Activate the venv and run `pip install -r requirements.txt` (with `--require-hashes` if a hash file is provided)
- [x] 4.8 Run `python -m playwright install chromium` to fetch the Playwright-pinned Chromium revision; record that revision into `third_party/install/playwright_chromium.version` for later report inclusion
- [x] 4.9 Add idempotency checks: skip any step whose output is newer than its inputs unless `--clean` is given; final status line "build ok" or "build failed: <step>"

## 5. Lifecycle Script: `deploy.sh`

- [x] 5.1 Implement `scripts/deploy.sh` to source a small helper that exports `PATH`, `LD_LIBRARY_PATH`, `PKG_CONFIG_PATH` to prepend `third_party/install/` and activate the venv (`scripts/env.sh`)
- [x] 5.2 If `streams/h264_watermarked.mp4` or `streams/h265_watermarked.mp4` is missing or older than `lib/watermark.py`, invoke `prepare_streams.sh` (otherwise skip)
- [x] 5.3 Start MediaMTX via `rtsp_server/start_server.sh`; record its PID for later cleanup
- [x] 5.4 Run the RTSP health check on both H.264 and H.265 URLs with bounded timeouts; on failure, kill MediaMTX, print "deploy failed: rtsp <reason>", and exit non-zero
- [x] 5.5 Print "deploy ok: ready for submissions" and exit zero

## 6. Lifecycle Script: `test.sh`

- [x] 6.1 Implement `scripts/test.sh` that asserts `deploy.sh` has succeeded (RTSP reachable) and exits with a clear message if not
- [x] 6.2 Iterate over every `test_submissions/*.zip`; for each, invoke `evaluator.sh <case_name> test_submissions/<case>.zip` and capture the resulting `score.json`
- [x] 6.3 Compare each `score.json` (and the captured failure reason where applicable) against the per-case expected outcome encoded in the script: reference submission → `objective_total >= 28`; static_frame → 0 FPS points per codec; iframe_only → reduced FPS points; fake_overlay → correctness < 10; missing_start → `reason: missing start.sh`; never_ready → `reason: startup timeout`; missing_testid → `reason: missing data-testid`
- [x] 6.4 Print a per-case PASS/FAIL line; exit non-zero if any case fails

## 7. Watermarked Reference Streams

- [x] 7.1 Implement `lib/watermark.py` with a `draw_watermark(image, frame_number, total_seconds)` function that overlays: 5-digit zero-padded frame number block (top-left), `HH:MM:SS.mmm` timecode (top-right), four color blocks `(255,0,0)/(0,255,0)/(0,0,255)/(255,255,255)` along the bottom, and a DataMatrix code in the bottom-right encoding the integer frame number
- [x] 7.2 Implement a frame-generation entry point in `lib/watermark.py` that, given codec/resolution/fps/duration, writes the full PNG sequence under `reference/<codec>/frame_NNNNN.png` deterministically (no timestamp-based randomness, fixed PRNG seeds if any are used)
- [x] 7.3 Implement `scripts/prepare_streams.sh` that activates the venv, prepends `third_party/install/` to `PATH`/`LD_LIBRARY_PATH`, calls the Python frame generator for each codec, then runs the source-built `ffmpeg` to encode `streams/h264_watermarked.mp4` (`libx264 crf 18 yuv420p` at `1920x1080@30fps`, 30s) and `streams/h265_watermarked.mp4` (`libx265 hvc1 yuv420p` at `2560x1440@25fps`, 30s, target 4 Mbps)
- [x] 7.4 Verify outputs: `ffprobe` reports the expected resolution/fps/duration for each MP4; spot-check three reference PNGs (start, middle, end) and confirm DataMatrix decodes to the expected frame number using the source-built `libdmtx` — **needs runtime verification after build.sh succeeds**

## 8. RTSP Server

- [x] 8.1 Write `rtsp_server/mediamtx.yml` that listens on `:8554`, forces RTSP-over-TCP, and declares `test/h264` and `test/h265` paths fed by `ffmpeg -re -stream_loop -1 -i ../streams/<file>.mp4 -c:v copy -f rtsp <publish_url>` (resolved via the source-built `ffmpeg` on `PATH`), with on-demand restart so streams come back up between runs
- [x] 8.2 Write `scripts/start_rtsp.sh` that launches `third_party/install/bin/mediamtx` with the project config, captures its PID into `rtsp_server/mediamtx.pid` for later cleanup, and tails MediaMTX logs into the run log
- [x] 8.3 Add a `health_check` helper (Bash function or small script) that runs `ffprobe -rtsp_transport tcp rtsp://localhost:8554/test/h264` with a bounded timeout and returns non-zero if the stream is not readable

## 9. Playwright Capture Runner

- [x] 9.1 Implement `runner.py` CLI accepting `--codec`, `--output`, `--duration`, and `--fps`
- [x] 9.2 Launch Chromium headless with `--disable-dev-shm-usage`, `--no-sandbox`, `--autoplay-policy=no-user-gesture-required`; new browser context per invocation; viewport `1920x1080`
- [x] 9.3 Navigate to `http://localhost:8080/play?codec=<codec>&autoplay=1`; wait up to 15s for `window.__PLAYER_READY__ === true`; on timeout read `window.__PLAYER_ERROR__` and exit non-zero with that reason
- [x] 9.4 Fail the round with a clear error if `[data-testid="player-video"]` cannot be located after readiness
- [x] 9.5 Capture element-only screenshots of `[data-testid="player-video"]` at the requested FPS for the requested duration, writing `shot_NNNNN.png` files into `--output/`
- [x] 9.6 Hook `page.on("pageerror")` and `page.on("console")` to collect browser-side errors; write a `timestamps.json` containing capture timestamps, the readiness reason if any, and any collected errors

## 10. Frame Analyzer

- [x] 10.1 Implement `analyzer.py` CLI accepting `--codec`, `--screenshots`, `--reference`, and `--output`
- [x] 10.2 Iterate screenshots in lexical order; for each, decode to RGB and attempt DataMatrix recognition via `pylibdmtx` (linked against the source-built `libdmtx`); on failure fall back to OCR via `pytesseract` constrained to digits, invoking the source-built `tesseract` on `PATH`
- [x] 10.3 Look up `reference/<codec>/frame_NNNNN.png` for the recognized frame number; resize the screenshot to the reference resolution (Lanczos) before running SSIM via `skimage.metrics.structural_similarity`
- [x] 10.4 Sample the four bottom color blocks from documented pixel regions and check each against its target RGB within tolerance (`+/- 20` per channel suggested, tune during validation)
- [x] 10.5 Aggregate per-codec metrics: `total_shots`, `watermark_recognized`, `color_blocks_passed`, `ssim_scores`, `frame_numbers`, `duration` (from `timestamps.json`), `unique_frame_count` (distinct recognized frame numbers), `measured_fps = unique_frame_count / duration`, `watermark_recognition_rate`, `color_check_rate`, `mean_ssim`; write to `<output>/<codec>_metrics.json`
- [x] 10.6 Verify frame-number-keyed matching across the MP4 loop boundary: feed a synthetic screenshot set that includes frames just before and just after the 30-second loop and confirm all match the correct reference PNGs — **needs runtime verification with real screenshots**

## 11. Scorer and Report Generator

- [x] 11.1 Implement `scorer.py` CLI accepting `--h264`, `--h265`, `--output`, and `--report`; read both metrics JSONs
- [x] 11.2 Score correctness per codec: full 10 when `watermark_recognition_rate >= 0.95` AND `color_check_rate >= 0.95` AND `mean_ssim >= 0.90`; partial 5 when `watermark_recognition_rate >= 0.80` AND `mean_ssim >= 0.75`; else 0
- [x] 11.3 Score FPS per codec: full 5 when `|measured_fps - expected| <= 1.0` (expected 30 for H.264, 25 for H.265); partial 3 when `<= 3.0`; else 0
- [x] 11.4 Write `score.json` with an H.264 block, an H.265 block, `objective_total`, `max_score = 30`, the recorded Chromium revision from `third_party/install/playwright_chromium.version`, and the underlying metrics preserved for audit
- [x] 11.5 Implement `report.py` (or merge into `scorer.py --report`) that renders `report.html` from the metrics and score JSONs: top summary including the toolchain fingerprint (submodule SHAs + Chromium revision), suspicious-screenshot gallery (watermark failures, color block failures, SSIM `< 0.7`), frame-number-over-time chart per codec, SSIM histogram per codec, and relative-path links to raw artifacts; all assets inline, no external network calls

## 12. Orchestration Script

- [x] 12.1 Implement `scripts/evaluator.sh` argument parsing for `<team_id>` and `<submission_zip>`; emit a usage message when arguments are missing
- [x] 12.2 Pre-flight check: verify `third_party/install/bin/ffmpeg`, `third_party/install/bin/mediamtx`, and `third_party/install/bin/tesseract` exist; if any is missing, exit with a clear message pointing at `build.sh`
- [x] 12.3 Source the env helper that prepends `third_party/install/` to `PATH`, `LD_LIBRARY_PATH`, `PKG_CONFIG_PATH` and activates the venv at `.venv/`
- [x] 12.4 Define the cleanup function and `trap` for `EXIT INT TERM` near the top of the script, before any step that allocates resources, so it runs on every exit path
- [x] 12.5 Create `results/<team_id>_<timestamp>/` and `submissions/<team_id>/`; redirect logging into `results/<team_id>_<timestamp>/evaluator.log` while still echoing the final score JSON to stdout
- [x] 12.6 Pre-run cleanup: kill any process holding `8080` (`lsof -ti:8080 | xargs -r kill -9`); kill leftover MediaMTX from prior runs if its PID file exists. The evaluator does not touch any other port — contestants pick their own internal ports and the process-group kill handles them.
- [x] 12.7 Ensure the RTSP server is running (start via `rtsp_server/start_server.sh` if not already up from `deploy.sh`); call the health-check helper; on failure, log `infrastructure failure`, exit with a non-contestant-fault status, and do NOT score 0 against the team
- [x] 12.8 Extract the submission zip into `submissions/<team_id>/`; if `start.sh` is missing, write a failure-mode `score.json` (objective_total 0, reason `missing start.sh`) and exit
- [x] 12.9 `chmod +x` contestant `start.sh` (and `stop.sh` if present); export `RTSP_SERVER_HOST=127.0.0.1`, `RTSP_SERVER_PORT=8554`, `FRONTEND_PORT=8080`; invoke `start.sh` via `setsid` and capture its PID
- [x] 12.10 Poll `http://localhost:8080/play?codec=h264&autoplay=1` for up to 60 seconds; on timeout, write a failure-mode `score.json` (objective_total 0, reason `startup timeout`) and proceed to cleanup
- [x] 12.11 Invoke `runner.py --codec h264 --output results/<run>/h264_screenshots --duration 30 --fps 30`; then `runner.py --codec h265 --output results/<run>/h265_screenshots --duration 30 --fps 30`
- [x] 12.12 Invoke `analyzer.py` for each codec, then `scorer.py --h264 ... --h265 ... --output results/<run>/score.json --report results/<run>/report.html`
- [x] 12.13 Invoke contestant `stop.sh` if present; SIGKILL the contestant process group; run post-run cleanup of port `8080` (MediaMTX is owned by `deploy.sh`/`teardown.sh`); print `score.json` to stdout

## 13. Reference Submission and Negative Cases (Validation)

- [x] 13.1 Build a minimal reference contestant submission that consumes `RTSP_SERVER_HOST/PORT`, decodes via WebCodecs / MSE, renders into a `<video data-testid="player-video">` at `1280x720`-or-larger, sets `window.__PLAYER_READY__ = true`, and provides `start.sh` and `stop.sh`; bundle as `test_submissions/reference.zip`. **Note**: this is a *self-test* reference, not a real WebCodecs/MSE player. It uses ffmpeg to pull 35s of RTSP into a local MP4 then serves it via `<video>` (native HTML5 playback). Sufficient to exercise the evaluator's pipeline end-to-end.
- [x] 13.2 Build a static-frame submission that displays a single still image with the watermark composited in; bundle as `test_submissions/static_frame.zip`
- [x] 13.3 Build an I-frame-only / repeated-frame submission that renders a small cycle of frames at the correct visual size but with very few unique frame numbers; bundle as `test_submissions/iframe_only.zip`
- [x] 13.4 Build a fake-overlay submission that draws a watermark-shaped overlay on a non-streaming background (so DataMatrix or color blocks fail); bundle as `test_submissions/fake_overlay.zip`
- [x] 13.5 Build a missing-`start.sh` zip and a never-ready zip (frontend serves on `8080` but `__PLAYER_READY__` is never set) and a missing-`data-testid` zip
- [x] 13.6 Encode the per-case expected outcomes inside `test.sh` so the automated regression gate in §6 actually runs them: reference near-full score; static frame and I-frame-only get 0 FPS points; fake overlay does not reach full correctness; missing `start.sh`, startup timeout, and missing `data-testid` each produce a clear failure-mode `score.json` and report

## 14. Acceptance Validation

- [x] 14.1 On a fresh Ubuntu 24.04 host with NONE of `ffmpeg`/`mediamtx`/`tesseract`/`leptonica`/`libdmtx` installed system-wide, run `setup.sh && build.sh && deploy.sh` and confirm all three exit zero
- [x] 14.2 Confirm `third_party/install/bin/{ffmpeg,mediamtx,tesseract}` all exist and are executable; confirm `which ffmpeg` resolves under `third_party/install/` when the evaluator env helper is sourced
- [x] 14.3 Confirm `ffprobe -rtsp_transport tcp rtsp://localhost:8554/test/h264` and the corresponding H.265 URL both succeed after `deploy.sh`
- [x] 14.4 Run `test.sh` and confirm every test case passes (reference near-full, negatives produce the expected reduced scores or failure reasons)
- [x] 14.5 Run the end-to-end `evaluator.sh` against the reference submission and confirm `objective_total >= 28` (near full marks) with both codec subtotals listed
- [x] 14.6 After every test in 14.4 and 14.5, confirm that port `8080` is free (`lsof -i :8080` returns no rows)
- [x] 14.7 Open `report.html` from any test run in a browser with the host offline and confirm it renders charts, screenshots, and the toolchain fingerprint without errors
- [x] 14.8 Re-run `build.sh` and confirm it is a near-no-op (no submodule rebuilt) and exits in under a few seconds; then run `build.sh --clean` and confirm a full rebuild succeeds
