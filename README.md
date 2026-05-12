# Web Player Objective Evaluator

Host-side automated scorer for the 30-point objective portion of the web plugin-free real-time media player challenge. Generates watermarked H.264 / H.265 RTSP streams, drives each contestant submission through Playwright, compares decoded frames against the watermarked references, and emits a `score.json` plus internal `report.html` per run.

This is for organizer / internal use only. Contestants do not see the report.

## Host requirements

- Ubuntu 24.04 (the only supported reference platform)
- A working C/C++ toolchain (installed by `setup.sh`)
- A Go toolchain (installed by `setup.sh`; needed to build MediaMTX)
- Python 3.12 + venv (installed by `setup.sh`)
- Network access during `setup.sh` (apt) and `build.sh` (Playwright Chromium download, Python wheels)
- Ports `8554` (RTSP) and `8080` (contestant frontend) free during a run

The evaluator does **not** depend on `apt`-installed copies of `ffmpeg`, `mediamtx`, `tesseract`, `leptonica`, or `libdmtx`. Those are all built from source from pinned git submodules under `third_party/` and installed into `third_party/install/`.

## Documented exceptions to the source-build rule

- **Python packages** are pip-installed from pinned versions in `requirements.txt`. pip+sdist is itself a source-build path; submoduling six pure-Python repos creates friction for negligible reproducibility benefit.
- **Chromium** is fetched by `playwright install chromium` at the revision the pinned Playwright version itself pins. Building Chromium from source is roughly 24 hours and >100 GB of disk; we trade against pinning the Playwright revision and recording the Chromium build string in every `score.json` / `report.html`.

## Lifecycle scripts (under `scripts/`)

| Script                    | What it does                                                                 | When to run                                                |
|---------------------------|------------------------------------------------------------------------------|------------------------------------------------------------|
| `scripts/setup.sh`        | apt toolchain, Python venv, `git submodule update --init --recursive`        | Once per host                                              |
| `scripts/build.sh`        | Build every submodule into `third_party/install/`, install Python deps, Chromium | Once per host, or `--clean` to force a full rebuild     |
| `scripts/deploy.sh`       | Regenerate streams if stale, start MediaMTX, RTSP health-check               | Once per evaluation session (also picked up by `evaluator.sh`) |
| `scripts/evaluator.sh`    | Per-submission run: clean ports, deploy contestant, capture, score, cleanup  | Once per contestant                                        |
| `scripts/test.sh`         | Run the bundled self-test suite (auto-runs `deploy.sh` and `teardown.sh`)    | After any change to the evaluator itself                   |
| `scripts/teardown.sh`     | Stop MediaMTX and free ports `8554`/`8080`                                   | After an evaluation session, or before bringing the host to idle |

All six are idempotent and safe to re-run. Internal helpers also under `scripts/`:
`prepare_streams.sh`, `start_rtsp.sh`, `health_check.sh`, `build_test_zips.sh`, and the shared `env.sh`.

## Cold-start recipe

```bash
./scripts/setup.sh        # apt install toolchain, create .venv, init submodules
./scripts/build.sh        # build ffmpeg/mediamtx/tesseract/leptonica/libdmtx/x264/x265; pip install; install Chromium
./scripts/deploy.sh       # generate watermarked streams (if missing), start MediaMTX, health-check RTSP
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

`git submodule status` is the source of truth for what was actually used in any given audit.

## Contestant runtime contract (reference)

Each contestant zip MUST contain at its root:

- `start.sh` — starts the contestant's HTTP frontend on `${FRONTEND_PORT}` and whatever internal processes they need
- `stop.sh` — optional cleanup hook

The contestant reads runtime config from env vars exported by `evaluator.sh`:

- `RTSP_SERVER_HOST=127.0.0.1`
- `RTSP_SERVER_PORT=8554`
- `FRONTEND_PORT=8080`

The frontend must expose `/play?codec=<h264|h265>&autoplay=1`, render the video into `[data-testid="player-video"]` at an actual rendered size of at least `1280x720`, and set `window.__PLAYER_READY__ = true` after the first frame. On failure it should populate `window.__PLAYER_ERROR__` with a human-readable string.

The evaluation host is Ubuntu 24.04 with Google Chrome (the exact Chrome version is recorded in every `score.json` and `report.html` as `chromium_version`). The evaluator screenshots `[data-testid="player-video"]` and scores the result; rendering strategy is up to the contestant.

## Result artifacts

Per run, under `results/<team_id>_<timestamp>/`:

- `h264_screenshots/`, `h265_screenshots/`
- `h264_metrics.json`, `h265_metrics.json`
- `score.json`
- `report.html` (internal-only)
- `evaluator.log`

## Layout

```
.
├── README.md / CLAUDE.md / requirements.txt / .gitignore / .gitmodules
├── runner.py / analyzer.py / scorer.py / report.py             # Python entry modules
├── lib/watermark.py                                            # shared lib
├── scripts/
│   ├── setup.sh / build.sh / deploy.sh / test.sh / evaluator.sh   # lifecycle
│   ├── prepare_streams.sh / start_rtsp.sh / health_check.sh        # internal helpers
│   ├── build_test_zips.sh
│   └── env.sh                                                  # sourced helper
├── rtsp_server/mediamtx.yml                                    # config (binary lives in third_party/install)
├── third_party/{ffmpeg,mediamtx,tesseract,leptonica,libdmtx,x264,x265}/   # git submodules
├── third_party/install/                                        # build output (git-ignored)
├── streams/{h264,h265}_watermarked.mp4                         # generated (git-ignored)
├── reference/{h264,h265}/frame_NNNNN.png                       # generated (git-ignored)
├── submissions/<team_id>/                                      # per-run staging (git-ignored)
├── results/<team_id>_<timestamp>/                              # per-run artifacts (git-ignored)
└── test_submissions/                                           # bundled self-test fixtures
    ├── src/<case>/                                             # source for build_test_zips.sh
    └── *.zip                                                   # generated (git-ignored)
```
