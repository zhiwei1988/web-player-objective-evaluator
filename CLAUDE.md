<!-- last synced: 2026-05-15 -->

## Overview

Organizer-internal automated scorer for the objective portion of a web plugin-free real-time media player challenge. Generates watermarked H.265 RTSP streams, drives each contestant submission through Playwright Chromium, compares decoded frames against watermarked references, and emits a contestant-visible `score.json` plus an internal `report.html`. Built and run on Ubuntu 24.04 with a source-built C/C++/Go toolchain (ffmpeg, mediamtx, tesseract, leptonica, libdmtx, x265) and Python 3.12.

The execution flow is single native host: `scripts/setup.sh → scripts/build.sh → scripts/deploy.sh → scripts/evaluator.sh <team_id> <submission_zip>`.

## Where authoritative info lives

CLAUDE.md is intentionally not a mirror. When a fact is answerable from source, look there:

| Question | Source of truth |
|---|---|
| Per-profile parameters (resolution, fps, bitrate, RTSP path, reference dir, CPU-sampling flag) | `lib/profiles.py::PROFILES` |
| Scoring thresholds and CPU sub-score tunables | `scorer.py` module-level constants + `openspec/specs/evaluator/spec.md` |
| Contestant runtime contract (env vars, URL format, DOM signals) | `openspec/specs/evaluator/spec.md` — Requirement: Contestant Runtime Contract |
| Reserved ports | `rtsp_server/mediamtx.yml` (RTSP) + `scripts/_contestant_lifecycle.sh` (frontend) |
| Toolchain pins | `git submodule status` |
| Per-script usage | `<script> --help` or the script's top comment |

Pipeline modules (`runner.py`, `analyzer.py`, `scorer.py`, `report.py`) each expose `_cli()` and are independently runnable via `python <module>.py --help` for diagnostic use.

## Conventions

These are project-level judgment calls that aren't self-evident from the code. Change them by writing an OpenSpec change, not by editing one site.

- **Internal use only.** Contestants only ever see `score.json`. Anything in `report.html` is free to be diagnostic / suspicious-frame-revealing.

- **Source-build rule.** Do NOT use apt-installed `ffmpeg`/`mediamtx`/`tesseract`/`leptonica`/`libdmtx`/`x265`. All consumed from pinned submodules → `third_party/install/`. Documented exceptions: Python wheels (pip) and Chromium (Playwright-pinned revision). On the build/evaluation host, `third_party/install/lib` is registered with `ldconfig` via `/etc/ld.so.conf.d/evaluator.conf`. The host-side registration is necessary because `scripts/build.sh` `setcap`s the mediamtx binary with `CAP_NET_BIND_SERVICE` so it can bind a privileged RTSP port without root; setcap'd binaries run under kernel secure-exec which strips `LD_LIBRARY_PATH` on `execve`, so ffmpeg children must resolve their libs via the system linker cache. (x264 is still built because ffmpeg's configure pulls it in; the evaluator pipeline does not encode H.264.)

- **Profile registry is the single truth source.** `lib/profiles.py::PROFILES` defines every per-round parameter. Pipeline modules and orchestration scripts read from it, never hard-code profile names or paths. Adding a profile = one entry in `PROFILES` + one new MediaMTX path in `rtsp_server/mediamtx.yml` + regenerate streams via `scripts/prepare_streams.sh`.

- **Contestant contract is frozen.** Changing the URL parameter, env vars, fixed RTSP source URLs, or DOM signals requires an OpenSpec change to `openspec/specs/evaluator/spec.md`. The contestant runs on the host as a native process group managed by `scripts/evaluator.sh`.

- **MediaMTX lifecycle is per-run, owned by `scripts/evaluator.sh`.** Start at evaluator-body entry, kill on every exit path via cleanup trap. `scripts/deploy.sh` does NOT start MediaMTX — it only generates streams. For interactive RTSP testing without an evaluator run, invoke `scripts/start_rtsp.sh` directly and tear down with `scripts/teardown.sh`.

- **Pipeline stages stay independently runnable.** Every Python module has `_cli()` + `if __name__ == "__main__"`. `scripts/evaluator.sh` invokes them as separate subprocesses, not as imports. New stages MUST preserve this.

- **Reserved ports** are cleaned only by `scripts/evaluator.sh` and `scripts/teardown.sh`. Contestants are free to bind any other local port internally; we don't prescribe or clean them — the contestant's process group is SIGKILLed as a whole at cleanup. Playwright drives Chrome over its own internal CDP channel; no fixed remote-debugging port is exposed.

- **Idempotent scripts.** `setup.sh`/`build.sh`/`deploy.sh`/`evaluator.sh` are all safe to re-run. `scripts/evaluator.sh` is flock-mutex'd at `/var/tmp/evaluator.lock`; concurrent invocations exit 75 rather than corrupting each other's port state.

- **Canonical evaluation host.** Ubuntu 24.04 + Google Chrome (the exact build is recorded in every `score.json` / `report.html` as `chromium_version`). No hardware HEVC decoder is assumed. The evaluator is codec-agnostic at the contestant boundary — it just screenshots `[data-testid="player-video"]`. **Do not** add contestant-facing prescriptions about "what they should do" to spec/README/contestant docs; the contract is the element + readiness signals only.

- **Path discipline.** Every shell script computes `SCRIPT_DIR = scripts/` then `ROOT_DIR = "${SCRIPT_DIR}/.."` and sources `scripts/env.sh`.

- **Generated dirs are git-ignored.** `streams/`, `reference/`, `submissions/`, `results/`, `third_party/install/`, `test_submissions/*.zip`. Don't commit run artifacts.

- **OpenSpec workflow** lives at `openspec/` (specs/, changes/). Treat as orthogonal to runtime code; use `/opsx:*` skills, not direct edits.
