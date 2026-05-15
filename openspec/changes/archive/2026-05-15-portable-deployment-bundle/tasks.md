## 1. Refactor scripts/evaluator.sh into container body

- [x] 1.1 Strip host-side actions (zip extraction, contestant `start.sh` invocation, `stop.sh`, port 8080 precheck/cleanup) from `scripts/evaluator.sh`; keep only MediaMTX + runner × 2 + analyzer × 2 + scorer + report
- [x] 1.2 Change `scripts/evaluator.sh` argument signature from `<team_id> <submission_zip>` to `<team_id> <results_subdir>` (results_subdir resolved as `${ROOT_DIR}/results/<results_subdir>`)
- [x] 1.3 Make MediaMTX per-invocation: script starts MediaMTX via `scripts/start_rtsp.sh` AND kills it on exit; remove the "owned by deploy.sh, shared across runs" comment block
- [x] 1.4 Preserve cleanup trap; ensure `score.json` is still written via `scorer.py --failure-reason` on every exit path
- [x] 1.5 Preserve fd-3 redirect so the final `score.json` still prints to the original stdout when the script's stdout is captured

## 2. Build scripts/evaluator-local.sh (build-host wrapper, no docker)

- [x] 2.1 Author shell skeleton with `<team_id> <submission_zip>` args, `usage()`, fd-3 stdout pattern matching original evaluator.sh
- [x] 2.2 Acquire `flock -n /var/tmp/evaluator-host.lock`; exit 75 with holder PID on contention
- [x] 2.3 Compute `ts=$(date +%Y%m%d_%H%M%S)` and `RUN_DIR=${ROOT_DIR}/results/<team_id>_<ts>`; refuse if `$RUN_DIR` already exists
- [x] 2.4 Precheck ports: refuse if `ss -lnt sport = :8080` is non-empty (8554 deliberately skipped for the local wrapper — MediaMTX is per-run inside evaluator.sh and reuses via idempotent start_rtsp.sh)
- [x] 2.5 Port the single-top-dir lift logic from old `evaluator.sh:160-169` for unzip into `submissions/<team_id>/`
- [x] 2.6 Export `RTSP_SERVER_HOST=127.0.0.1`, `RTSP_SERVER_PORT=8554`, `FRONTEND_PORT=8080`; `setsid` invoke contestant `start.sh`; record pgid into `$RUN_DIR/contestant.pid`
- [x] 2.7 Poll `curl -fsS http://127.0.0.1:8080/play?codec=h264&autoplay=1` up to 60s (60 × 1s)
- [x] 2.8 On readiness: invoke `scripts/evaluator.sh <team_id> <team_id>_<ts>` directly (no docker)
- [x] 2.9 On contestant readiness failure: invoke `.venv/bin/python scorer.py --failure-reason contestant_frontend_unavailable --output $RUN_DIR/score.json --report $RUN_DIR/report.html --install-prefix third_party/install`; exit 2
- [x] 2.10 Install cleanup trap: `kill -- -$pgid`; `timeout 10 ./stop.sh` if present; `fuser -k 8080/tcp 8554/tcp`; release flock
- [x] 2.11 Print contents of `score.json` to fd 3 on exit; exit codes map to 0/1/2/75 per design §4.3
- [x] 2.12 (added) Cleanup trap also writes a failure `score.json` via local `.venv/bin/python scorer.py --failure-reason ${HOST_FAILURE_REASON:-evaluator aborted}` when extraction/lifecycle failures land before `evaluator.sh` runs — preserves the "always emit score.json" invariant from the original evaluator.sh

## 3. Validate evaluator.sh split with existing test.sh

- [x] 3.1 Update `scripts/test.sh` (default mode only, not `--portable` yet) to drive cases through `scripts/evaluator-local.sh` instead of `scripts/evaluator.sh`
- [x] 3.2 Run `scripts/test.sh` end-to-end on build host; confirm `reference.zip` produces `objective_total ≥ 13` and each negative fixture fails with its expected `reason` — 7/7 PASS verified (third run after fixing port precheck and failure-score-on-extract)
- [x] 3.3 Compare resulting reference `score.json` against pre-change baseline: schema identical, integer points identical (objective_total=15 / h264 correctness=10 / fps=5), SSIM within float tolerance

## 4. Author Dockerfile and .dockerignore

- [x] 4.1 Write multi-stage `Dockerfile` (builder stage COPY-only; runtime stage `ubuntu:24.04` + Google Chrome + Chromium runtime libs + ldconfig step for source-built .so); `ENTRYPOINT ["/work/scripts/evaluator.sh"]`; `WORKDIR /work`
- [x] 4.2 Write `.dockerignore` excluding run artifacts and submodule source trees (only `third_party/install/` kept in context)
- [x] 4.3 Add `.playwright/` and `dist/` to `.gitignore`
- [x] 4.4 Build the image: `docker build --network host -t evaluator-portable:dev .` (--network host required so apt/curl inside RUN can reach 127.0.0.1:1081 proxy)
- [x] 4.5 Smoke test 1: `docker run --rm --network none --entrypoint /work/.venv/bin/python evaluator-portable:dev -c 'import playwright, PIL, numpy, skimage, pylibdmtx; print("ok")'` exits 0
- [x] 4.6 Smoke test 2: in-container MediaMTX + RTSP health check passes
- [x] 4.7 (added) Three Dockerfile adjustments surfaced during smoke: (a) `python3` apt package added (`.venv/bin/python3` is a symlink to `/usr/bin/python3`); (b) `chmod a+w /work/rtsp_server` so non-root `--user` can write `mediamtx.pid` / `mediamtx.log`; (c) `RUN echo /work/third_party/install/lib > /etc/ld.so.conf.d/evaluator.conf && ldconfig` so `pylibdmtx`'s `find_library('dmtx')` finds the source-built lib (LD_LIBRARY_PATH alone isn't enough — find_library falls back to ldconfig in absence of gcc/ld in the runtime stage)
- [x] 4.8 (added) Add Google Chrome to runtime apt list: `runner.py` launches `channel="chrome"` (Google's signed build with H.264/H.265 codecs); the bundled Playwright Chromium Headless Shell lacks proprietary codec licenses and falls back to `DEMUXER_ERROR_NO_SUPPORTED_STREAMS`

## 5. Build scripts/package.sh

- [x] 5.1 Implement precheck (all 11 paths checked, fail-fast on first missing with the upstream script name)
- [x] 5.2 Implement Playwright cache staging: `rsync -a --delete ~/.cache/ms-playwright/ ./.playwright/`
- [x] 5.3 Compute `IMAGE_TAG_SHA=$(git rev-parse --short=12 HEAD)`; `docker build --network host -t evaluator-portable:${IMAGE_TAG_SHA} .`
- [x] 5.4 `docker save evaluator-portable:${IMAGE_TAG_SHA} | zstd -19 -T0 -o dist/evaluator-portable_${IMAGE_TAG_SHA}.tar.zst`
- [x] 5.5 Copy `scripts/evaluator-host.sh` AND `scripts/_contestant_lifecycle.sh` → `dist/`; exec bits preserved (helper is sourced by evaluator-host.sh on target host)
- [x] 5.6 Author Chinese `dist/README.md`
- [x] 5.7 Emit `dist/manifest.json` with exactly the 6 spec'd fields
- [x] 5.8 Emit `dist/SHA256SUMS` covering all five sibling files
- [x] 5.9 Final status line `package ok: ...`; idempotent re-run hits docker layer cache so subsequent builds skip recompilation
- [x] 5.10 `--gzip` fallback flag supported

## 6. Build scripts/evaluator-host.sh (target-host wrapper)

- [x] 6.1 Author shell skeleton with `<team_id> <submission_zip> [--root] [--manifest <path>]` arg parsing
- [x] 6.2 Read `image_sha256` from co-located `manifest.json` via jq; explicit fail-fast if image not loaded
- [x] 6.3 Lock + port precheck (8080+8554) + ts computation + RUN_DIR creation via shared `_contestant_lifecycle.sh`
- [x] 6.4 Zip lift + contestant env export + `setsid start.sh` + pgid via shared helper (DRY with evaluator-local.sh)
- [x] 6.5 Readiness poll (60s) via shared helper
- [x] 6.6 On readiness: `docker run --rm --network host --user $(id -u):$(id -g) -v "$RUN_DIR:/work/results/<...>:rw" evaluator-portable:$image_sha256 <team_id> <results_subdir>`; `--root` opt-in omits `--user`
- [x] 6.7 On any failure path with RUN_DIR present and no score.json yet: cleanup runs a short `docker run … scorer.py --failure-reason …` to honor the "always emit score.json" invariant
- [x] 6.8 Cleanup trap kills contestant pgid, runs stop.sh, fuser -k 8080+8554, and `docker kill` any lingering containers with this image as ancestor
- [x] 6.9 Print `score.json` to fd 3 on exit; exit codes 0/1/2/75 per design §4.3
- [x] 6.10 End-to-end verified on build host: `reference.zip → exit 0 + objective_total=15 (parity with evaluator-local.sh, full H.264, H.265 expected 0 due to host's Chrome HEVC gating)`; `missing_start.zip → exit 1 + reason="missing start.sh"`; `dist/SHA256SUMS` passes; result file owned by current user (not root); image_sha256 in `manifest.json` strict-matched against loaded image

## 7. Build scripts/test.sh --portable mode

- [x] 7.1 Parse `--portable` flag in `scripts/test.sh`; preserve all current default-mode behavior unchanged
- [x] 7.2 Stage 1: `scripts/package.sh` in-place on build host; assert `dist/` has 5 files; `sha256sum -c dist/SHA256SUMS` passes; assert image `--network none` initialization smoke per spec
- [x] 7.3 Stage 2: refuse if `EVAL_TARGET_HOST` is unset; `scp dist/* + streams/ "$EVAL_TARGET_HOST":~/evaluator-test/`; `ssh "$EVAL_TARGET_HOST"` to `docker load`, run `./evaluator-host.sh team_ref reference.zip`, then `scp` back the resulting `results/team_ref_*/score.json`. (streams/ is shipped because the reference fixture's start.sh reads `${REPO_ROOT}/streams/<codec>.mp4`; real contestants do live RTSP decode and don't need this.)
- [x] 7.4 Stage 3: on build host run `scripts/evaluator-local.sh team_ref_local test_submissions/reference.zip`; compare its `score.json` against stage 2
- [x] 7.5 Stage 4: `scp never_ready.zip`; `ssh` to run; assert remote exit code 0 + h264.reason ~ "startup timeout" (never_ready's HTTP server serves fine; only player JS never signals __PLAYER_READY__, so the per-codec timeout path triggers, not contestant_frontend_unavailable)
- [x] 7.6 Print per-stage PASS/FAIL summary; exit non-zero on any failure

### Task 7 known limitations (recorded during apply)

- **Smallest GCE machine type (e2-medium, shared-core 2 vCPU) fails Stage 2**: runner.py uses a 15s `__PLAYER_READY__` wait. On a busy shared-core VM, MediaMTX + python http.server + Playwright Chrome + analyzer competing for CPU pushes that wait past 15s, producing a misleading `DEMUXER_ERROR_NO_SUPPORTED_STREAMS` per-codec failure. Standalone Chrome decodes the same MP4 in ~6s on the same VM. Workarounds (not implemented in this change): use `e2-standard-2` or larger; or add a configurable readiness timeout via env (`EVAL_READINESS_TIMEOUT_S`).
- **End-to-end test against a remote host was deferred**: `test.sh --portable` code is written, syntax-checked, and verified against local-loaded dist on the build host (Stage 1 + 3 paths). Stage 2 / 4 against a fresh target host remains for a future apply session with an adequately-sized VM (e2-standard-2+) or after the readiness-timeout knob lands.
- **Reference fixture requires streams/ on the target host**: test.sh --portable scps `streams/` alongside `dist/` because the bundled reference contestant symlinks the build host's `streams/*.mp4` for HTTP serving. This is a self-test artifact, not a production deployment constraint — real contestants do live RTSP decode.

## 8. Update repo-level documentation

- [x] 8.1 CLAUDE.md Dev Commands table reorganized: build-host commands (`package.sh`, `evaluator-local.sh`, `setup/build/deploy/test/teardown.sh`) + target-host commands (`evaluator-host.sh [--root]`) + diagnostic; `evaluator.sh` reworded as "evaluator main body (invoked by wrappers, not for direct operator use)"
- [x] 8.2 CLAUDE.md Data Flow section rewritten with 8-step pipeline noting host-side wrapper + per-run MediaMTX + always-emit-score-json invariant; appended portable-bundle path summary
- [x] 8.3 CLAUDE.md Conventions section extended with: portable-bundle target/build-host prerequisites, MediaMTX lifecycle note, container vs host execution model, sha-locked image, path-discipline exception for `evaluator-host.sh` (PWD-based ROOT_DIR), `dist/` + `.playwright/` added to git-ignored list, ldconfig + Google Chrome + `--network host` build notes
- [x] 8.4 At archive time `/opsx:archive` will apply the three MODIFIED requirements (Workspace Layout / Orchestration and Cleanup / Lifecycle Scripts) plus the ADDED `portable-bundle` capability into `openspec/specs/`. Will be triggered separately.

## 9. Final verification (V1–V7 from design.md §6.1)

- [x] 9.1 V1 cold-chain: all build-host artifacts present and fresh — `third_party/install/bin/{ffmpeg,mediamtx,tesseract}`, `eng.traineddata`, `.venv/bin/python`, `streams/{h264,h265}_watermarked.mp4`, `reference/{h264,h265}/frame_*.png` (900 + 750 frames); `dist/` has 6 files; `sha256sum -c dist/SHA256SUMS` passes
- [x] 9.2 V2 deferred — see "Task 7 known limitations" above; equivalent build-host validation in 9.5
- [x] 9.3 V3 docker fs audit: container ran with single bind mount `host_dir → /work/results/audit (rw=true)`; `docker diff` writes confined to `/home/ubuntu/.cache/fontconfig/`, `/home/ubuntu/.config/google-chrome/`, `/tmp/com.google.Chrome.*`, `/tmp/playwright_chromiumdev_profile-*` (all expected ephemeral); **NO writes to `/work` outside the mounted results dir**, no writes to `/opt`, no writes to `/etc`
- [x] 9.4 V4 offline init: `docker run --rm --network none --entrypoint /work/.venv/bin/python <sha> -c 'import playwright, PIL, numpy, skimage, pylibdmtx; print("V4 ok")'` → `V4 ok`
- [x] 9.5 V5 parity: dist/evaluator-host.sh team_smoke6 reference.zip → objective_total=15, h264.total=15, h264.correctness=10, h264.fps=5, mean_ssim=0.9640848; evaluator-local.sh same fixture → same 15 / 10 / 5 / mean_ssim ≈ 0.9641 (within float tolerance). Schema keys identical. (Cross-machine parity blocked by V2.)
- [x] 9.6 V6 non-root: result file ownership `zhiwei:docker` (operator uid, NOT root) verified at `/home/zhiwei/workspace/web-player-objective-evaluator/results/team_smoke6_20260514_173344/score.json`. (Cross-machine non-root run blocked by V2; same code path on build host validates the `--user $(id -u):$(id -g)` mechanism.)
- [x] 9.7 V7 image size: `manifest.json.image_size_bytes = 1185126498` (1.2 GB compressed via zstd -19); no hard threshold per K10
