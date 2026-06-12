# Web Player Objective Evaluator

Host-side automated scorer for the 30-point objective portion of the web plugin-free real-time media player challenge. Generates watermarked H.265 RTSP streams at 2K (2560×1440) and 4K (3840×2160) resolutions, drives each contestant submission through Playwright, compares decoded frames against the watermarked references, and emits a `score.json` plus internal `report.html` per run.

This is for organizer / internal use only. Contestants do not see the report.

## Host requirements

- Ubuntu 24.04 (the only supported reference platform)
- A working C/C++ toolchain (installed by `setup.sh`)
- A Go toolchain (installed by `setup.sh`; needed to build MediaMTX)
- Python 3.12 + venv (installed by `setup.sh`)
- `libcap2-bin` for `setcap` (installed by `setup.sh`); MediaMTX needs `CAP_NET_BIND_SERVICE` to bind `:554`
- Network access during `setup.sh` (apt) and `build.sh` (Playwright Chromium download, Python wheels)
- Ports `554` (RTSP) and `8080` (contestant frontend) free during a run

The evaluator does **not** depend on `apt`-installed copies of `ffmpeg`, `mediamtx`, `tesseract`, `leptonica`, or `libdmtx`. Those are all built from source from pinned git submodules under `third_party/` and installed into `third_party/install/`.

## Documented exceptions to the source-build rule

- **Python packages** are pip-installed from pinned versions in `requirements.txt`. pip+sdist is itself a source-build path; submoduling six pure-Python repos creates friction for negligible reproducibility benefit.
- **Chromium** is fetched by `playwright install chromium` at the revision the pinned Playwright version itself pins. Building Chromium from source is roughly 24 hours and >100 GB of disk; we trade against pinning the Playwright revision and recording the Chromium build string in every `score.json` / `report.html`.

## Lifecycle scripts (under `scripts/`)

| Script                    | What it does                                                                 | When to run                                                |
|---------------------------|------------------------------------------------------------------------------|------------------------------------------------------------|
| `scripts/setup.sh`        | apt toolchain (incl. `libcap2-bin`), Python venv, `git submodule update --init --recursive` | Once per host                              |
| `scripts/build.sh`        | Build every submodule into `third_party/install/`, install Python deps, Chromium, apply `setcap cap_net_bind_service=+ep` to mediamtx (requires sudo) | Once per host, or `--clean` to force a full rebuild     |
| `scripts/deploy.sh`       | Regenerate streams if stale. Does NOT start MediaMTX (that's per-run, owned by `evaluator.sh`) | Once per host after `build.sh`, or whenever `lib/watermark.py` changes |
| `scripts/evaluator.sh`    | Per-submission run: acquire lock, stage zip, run contestant, capture, score, cleanup | Once per contestant                              |
| `scripts/test.sh`         | Run the bundled self-test suite (auto-runs `deploy.sh` and `teardown.sh`)    | After any change to the evaluator itself                   |
| `scripts/teardown.sh`     | Stop MediaMTX and free ports `554`/`8080`                                    | After an evaluation session, or before bringing the host to idle |

All six are idempotent and safe to re-run. Internal helpers also under `scripts/`:
`prepare_streams.sh`, `start_rtsp.sh`, `health_check.sh`, `build_test_zips.sh`,
`diagnose_run.py`, and the shared `env.sh`.

## Cold-start recipe

```bash
./scripts/setup.sh        # apt install toolchain, create .venv, init submodules
./scripts/build.sh        # build ffmpeg/mediamtx/tesseract/leptonica/libdmtx/x264/x265; pip install; install Chromium; setcap mediamtx
./scripts/deploy.sh       # generate watermarked 2K + 4K streams (if missing). RTSP starts per-run inside evaluator.sh
./scripts/evaluator.sh <team_id> /path/to/submission.zip
./scripts/teardown.sh     # when done: stop MediaMTX, free ports
```

Self-test the evaluator after changes (auto-deploys and auto-tears down):

```bash
./scripts/test.sh
```

## Submodule pin map

Recorded so any score can be mapped back to a known toolchain:

| Submodule                        | Upstream                                              | Pinned ref           | Commit       |
|----------------------------------|-------------------------------------------------------|----------------------|--------------|
| `third_party/ffmpeg`             | https://git.ffmpeg.org/ffmpeg.git                     | release tag `n7.0.2` | `e3a61e9103` |
| `third_party/mediamtx`           | https://github.com/bluenviron/mediamtx.git            | release tag `v1.9.3` | `6cd74878`   |
| `third_party/tesseract`          | https://github.com/tesseract-ocr/tesseract.git        | release tag `5.4.1`  | `b5f279ec`   |
| `third_party/leptonica`          | https://github.com/DanBloomberg/leptonica.git         | branch `1.84.1` (pre-tag) | `7e803e73` |
| `third_party/libdmtx`            | https://github.com/dmtx/libdmtx.git                   | release tag `v0.7.8` | `500d7af6`   |
| `third_party/x264`               | https://code.videolan.org/videolan/x264.git           | master HEAD (x264 has no release tags) | `0480cb05` |
| `third_party/x265`               | https://bitbucket.org/multicoreware/x265_git.git      | release tag `4.2`    | `e444744c`   |

`git submodule status` is the source of truth for what was actually used in any given audit. (x264 is still built because ffmpeg's configure pulls it in, but the evaluator no longer encodes any H.264 streams.)

## Contestant runtime contract (reference)

Each contestant zip MUST contain at its root:

- `start.sh` — starts the contestant's HTTP frontend on `${FRONTEND_PORT}` and whatever internal processes they need
- `stop.sh` — optional cleanup hook

The contestant reads runtime config from env vars exported by `evaluator.sh`:

- `RTSP_SERVER_HOST=127.0.0.1`
- `RTSP_SERVER_PORT=554`
- `FRONTEND_PORT=8080`

The frontend must expose `/play?profile=<2k|4k>&autoplay=1`, render the video into `[data-testid="player-video"]` at an actual rendered size of at least `1280x720`, and set `window.__PLAYER_READY__ = true` after the first frame. On failure it should populate `window.__PLAYER_ERROR__` with a human-readable string. The contestant SHALL pull from these fixed RTSP URLs:

- 2K: `rtsp://127.0.0.1:554/test/h265_2560_1440`
- 4K: `rtsp://127.0.0.1:554/test/h265_3840_2160`

The evaluation host is Ubuntu 24.04 with Google Chrome (the exact Chrome version is recorded in every `score.json` and `report.html` as `chromium_version`). The evaluator screenshots `[data-testid="player-video"]` and scores the result; rendering strategy is up to the contestant.

### Resource limits (environment facts)

The whole contestant process tree (everything `start.sh` launches) runs under two hard limits enforced by the evaluator:

- **Memory**: a hard memory cap on the process tree (default `10G`). Exceeding it fails the run as a contestant-side error.
- **Egress bandwidth**: the process tree's combined IP-layer egress is physically shaped to `100 Mbps` (loopback included). This is a single shared bucket for all the contestant's processes. Transport that does not cross the IP layer — unix domain sockets, shared memory, pipes — is **not** shaped; designs that ship decoded frames as raw pixels over localhost TCP will be throttled, while compressing before transport (or decoding in the browser) stays well under the cap. There is no separate penalty for hitting the cap; over-limit traffic simply queues/drops, lowering measured FPS.

## Scoring breakdown

- **2K profile** (10 pts): 5 correctness + 5 linear fps points (`min(measured_fps / 20, 1) * 5`)
- **4K profile** (15 pts): 5 correctness + 10 linear fps points (`min(measured_fps / 20, 1) * 10`); the 4K stream is encoded at 16 Mbps with a high-entropy reference so decode load is real
- **CPU sub-score** (5 pts): sampled during the **4K** capture window; gated to 0 if 4K fps fails to clear the CPU throughput gate
- **Total**: 30

## Result artifacts

Per run, under `results/<team_id>_<timestamp>/`:

- `2k_screenshots/`, `4k_screenshots/`
- `2k_metrics.json`, `4k_metrics.json`
- `score.json`
- `report.html` (internal-only)
- `result.info` (contest-platform projection; audit copy)
- `evaluator.log`

Additionally, the evaluator publishes a byte-identical `result.info` to
`$(dirname "<submission_zip>")/result.info` — the contest platform reads
`result.info` from the directory the submission zip lives in. The evaluator
also extracts the submission zip into that same directory and invokes
`start.sh` from there. The caller should provide one isolated directory per
submission zip because archive entries may overwrite files in that directory.
The published file is intentionally a lossy projection of `score.json`: its
`|info|` block is contestant-visible (total + five item scores: 2K correctness,
2K FPS, 4K correctness, 4K FPS, CPU, plus an optional sanitized
`Execution Feedback` section for contestant-side startup/capture failures);
its `|debug|` block carries organizer-facing diagnostics. Format spec is in
`openspec/specs/evaluator/spec.md` (Requirement: Contest Platform Result Info);
sample is at `reference/result-sample.info`.

## Capture throughput benchmark

To compare evaluator capture strategies without changing scoring, run a normal
fixture evaluation and summarize the resulting artifacts:

```bash
scripts/capture_benchmark.py \
  --screenshots results/<run>/2k_screenshots \
  --metrics results/<run>/2k_metrics.json
```

The command reports shot count, actual capture duration, capture sampling FPS,
average interval, p50/p90/p99 inter-shot intervals, and whether the companion
metrics still meet full-correctness thresholds. To compare the optional CDP
path, invoke `runner.py` with `--capture-strategy cdp` for a local benchmark
run and summarize that screenshot directory the same way. FPS scoring continues
to use `measured_fps`; benchmark output is audit/tuning data only.

## Layout

```
.
├── README.md / CLAUDE.md / requirements.txt / .gitignore / .gitmodules
├── runner.py / analyzer.py / scorer.py / report.py             # Python entry modules
├── lib/
│   ├── watermark.py                                            # frame generator
│   └── profiles.py                                             # ProfileSpec registry (single truth source)
├── scripts/
│   ├── setup.sh / build.sh / deploy.sh / test.sh / evaluator.sh   # lifecycle
│   ├── prepare_streams.sh / start_rtsp.sh / health_check.sh        # internal helpers
│   ├── build_test_zips.sh / diagnose_run.py
│   └── env.sh                                                  # sourced helper
├── rtsp_server/mediamtx.yml                                    # config (binary lives in third_party/install)
├── third_party/{ffmpeg,mediamtx,tesseract,leptonica,libdmtx,x264,x265}/   # git submodules
├── third_party/install/                                        # build output (git-ignored)
├── streams/h265_{2560_1440,3840_2160}.mp4                      # generated (git-ignored)
├── reference/{2k,4k}/frame_NNNNN.png                           # generated (git-ignored)
├── results/<team_id>_<timestamp>/                              # per-run artifacts (git-ignored)
└── test_submissions/                                           # bundled self-test fixtures
    ├── src/<case>/                                             # source for build_test_zips.sh
    └── *.zip                                                   # generated (git-ignored)
```
