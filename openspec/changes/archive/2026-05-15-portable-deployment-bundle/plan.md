# Portable Deployment Bundle Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. OpenSpec users may invoke `/opsx:apply portable-deployment-bundle` instead.

**Goal:** Produce a portable OCI image bundle so the evaluator can run on any docker-equipped Ubuntu 24.04 server without setup / build / external network.

**Architecture:** Split current `scripts/evaluator.sh` into a container-internal "evaluator body" + two host wrappers (`evaluator-host.sh` for target hosts with docker, `evaluator-local.sh` for build-host development). A new `scripts/package.sh` produces `dist/` containing the saved image, the host wrapper script, a manifest, README, and SHA256SUMS. Target hosts only need docker (+ zstd to decompress).

**Tech Stack:** bash, docker (`buildx` + `save` + `load`), zstd, jq, multi-stage `Dockerfile` on `ubuntu:24.04`, plus the existing Python pipeline (Playwright / Pillow / NumPy / scikit-image / pylibdmtx / pytesseract) and source-built ffmpeg / mediamtx / tesseract / libdmtx / x264 / x265 unchanged.

**File map**

| Action | Path | Purpose |
|---|---|---|
| CREATE | `Dockerfile` | Multi-stage; builder COPY-only, runtime `ubuntu:24.04` |
| CREATE | `.dockerignore` | Exclude run artifacts and `.git/` from build context |
| MODIFY | `.gitignore` | Add `.playwright/` and `dist/` |
| CREATE | `scripts/_contestant_lifecycle.sh` | Shared functions sourced by the two wrappers |
| CREATE | `scripts/evaluator-host.sh` | Target host operator entry (uses docker) |
| CREATE | `scripts/evaluator-local.sh` | Build host dev shortcut (no docker) |
| CREATE | `scripts/package.sh` | Build dist/ bundle |
| MODIFY | `scripts/evaluator.sh` | Strip host-side logic; container/local body only |
| MODIFY | `scripts/test.sh` | Drive cases via evaluator-local.sh; add `--portable` mode |
| MODIFY | `CLAUDE.md` | Reflect new entry points and target-host story |

A note on the shared helper: `_contestant_lifecycle.sh` exists because evaluator-host.sh and evaluator-local.sh share ~50 lines of contestant-lifecycle logic (flock, port precheck, unzip+lift, setsid start.sh, poll readiness, cleanup). The helper keeps the two wrappers from drifting; it does not add behavior beyond what tasks.md already requires.

---

## Task 1: Refactor scripts/evaluator.sh into container body

**Files:**
- Modify: `scripts/evaluator.sh`

- [ ] **Step 1.1: Capture current behavior baseline**

Run:
```bash
cd /home/zhiwei/workspace/web-player-objective-evaluator
./scripts/test.sh > /tmp/baseline_test_output.txt 2>&1
cp results/selftest_reference_*/score.json /tmp/baseline_reference_score.json 2>/dev/null \
  || { ls -td results/selftest_reference_* | head -1 | xargs -I{} cp {}/score.json /tmp/baseline_reference_score.json; }
```
Expected: test.sh exits 0; baseline reference score saved.

- [ ] **Step 1.2: Edit signature and arg validation**

Replace lines 1-31 of `scripts/evaluator.sh` with:

```bash
#!/usr/bin/env bash
# Evaluator main body — runs MediaMTX + capture + scoring inside a prepared
# host environment. Host-side concerns (zip extract, contestant start.sh /
# stop.sh, port precheck) belong to scripts/evaluator-host.sh (target host)
# or scripts/evaluator-local.sh (build host shortcut). MediaMTX is brought
# up AND torn down by this script per run; it is no longer shared with a
# host-resident daemon.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
    cat >&2 <<'EOF'
Usage: scripts/evaluator.sh <team_id> <results_subdir>

Invoked by scripts/evaluator-host.sh or scripts/evaluator-local.sh after
the contestant frontend is already serving on http://127.0.0.1:8080.
Reads streams/ and reference/ from the workspace; writes capture artifacts
+ score.json + report.html under results/<results_subdir>/.
EOF
    exit 64
}

(( $# >= 2 )) || usage
TEAM_ID="$1"
RESULTS_SUBDIR="$2"

# Pre-flight: source-built binaries must be present.
for bin in ffmpeg mediamtx tesseract; do
    if [[ ! -x "${ROOT_DIR}/third_party/install/bin/${bin}" ]]; then
        printf 'evaluator: %s missing under third_party/install/bin — run scripts/build.sh\n' "${bin}" >&2
        exit 66
    fi
done

# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

RUN_DIR="${ROOT_DIR}/results/${RESULTS_SUBDIR}"
[[ -d "${RUN_DIR}" ]] || { printf 'evaluator: results dir does not exist: %s\n' "${RUN_DIR}" >&2; exit 65; }
```

Run:
```bash
bash -n scripts/evaluator.sh
```
Expected: no syntax errors.

- [ ] **Step 1.3: Remove host-side blocks**

Delete from current `scripts/evaluator.sh`:
- The `STAGE_DIR` / `submissions/` block (lines around 46-47 and the unzip section ~150-178)
- `clean_ports()` function and its call (lines 83-93, 128-129)
- The contestant invocation block (lines 180-206: `export RTSP_SERVER_HOST=...` through the `wait for autoplay` poll)
- The `CONTESTANT_PID` variable and the `kill -- "-${CONTESTANT_PID}"` call inside `cleanup()` (lines 65, 95-108)

Keep:
- `LOG_FILE`, `SCORE_FILE`, `REPORT_FILE`, `H264_SHOTS`, `H265_SHOTS`, `H264_METRICS`, `H265_METRICS` derivations
- The `exec 3>&1` fd-3 stdout-preserving pattern
- `write_failure_score()` (still needed for infrastructure failures)
- `cleanup()` minus contestant kill; it should now kill MediaMTX (added in next step)

- [ ] **Step 1.4: Add MediaMTX teardown to cleanup trap**

Inside `cleanup()` (after the failure-score write, before the `cat "${SCORE_FILE}" >&3`), add:

```bash
    # MediaMTX is owned per-run by this script; kill it on every exit path.
    if [[ -f "${ROOT_DIR}/rtsp_server/mediamtx.pid" ]]; then
        local pid
        pid="$(cat "${ROOT_DIR}/rtsp_server/mediamtx.pid" 2>/dev/null || true)"
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            kill -TERM "${pid}" 2>/dev/null || true
            sleep 0.5
            kill -KILL "${pid}" 2>/dev/null || true
        fi
        rm -f "${ROOT_DIR}/rtsp_server/mediamtx.pid"
    fi
```

- [ ] **Step 1.5: Confirm runner/analyzer/scorer/report flow still wired**

The remaining body should be: `start_rtsp.sh` → 2× `health_check.sh` (h264, h265) → `run_capture h264` → `run_capture h265` → `analyze_codec h264` → `analyze_codec h265` → `scorer.py`. Verify these blocks still exist; remove any references to `STAGE_DIR` from `run_capture` / `analyze_codec` (there should be none).

Run:
```bash
bash -n scripts/evaluator.sh
shellcheck scripts/evaluator.sh || true
```
Expected: parses; shellcheck warnings are OK to ignore if not new regressions.

- [ ] **Step 1.6: Smoke-test the new body manually**

```bash
# Start a contestant manually
cd test_submissions/src/reference && unzip -oq ../reference.zip -d /tmp/ref_contestant 2>/dev/null \
  || { mkdir -p /tmp/ref_contestant && cp -r ./* /tmp/ref_contestant/; }
cd /tmp/ref_contestant
RTSP_SERVER_HOST=127.0.0.1 RTSP_SERVER_PORT=8554 FRONTEND_PORT=8080 setsid ./start.sh &
PGID=$!
cd /home/zhiwei/workspace/web-player-objective-evaluator
# Wait for frontend up
for i in $(seq 1 60); do curl -fsS http://127.0.0.1:8080/play?codec=h264 >/dev/null 2>&1 && break; sleep 1; done
mkdir -p results/smoke_$$
./scripts/evaluator.sh smoke smoke_$$ > /tmp/smoke_score.json
cat /tmp/smoke_score.json | jq .objective_total
kill -- -$PGID 2>/dev/null || true
fuser -k 8080/tcp 2>/dev/null || true
```
Expected: `objective_total >= 13` (reference contestant on this host).

- [ ] **Step 1.7: Commit**

```bash
git add scripts/evaluator.sh
git commit -m "[refactor] 把 evaluator.sh 收窄为评测主体（剥离 host 侧）"
```

---

## Task 2: Build scripts/_contestant_lifecycle.sh + scripts/evaluator-local.sh

**Files:**
- Create: `scripts/_contestant_lifecycle.sh`
- Create: `scripts/evaluator-local.sh`

- [ ] **Step 2.1: Create scripts/_contestant_lifecycle.sh skeleton**

```bash
#!/usr/bin/env bash
# shellcheck shell=bash
# Functions sourced by scripts/evaluator-host.sh and scripts/evaluator-local.sh
# to manage contestant lifecycle on the host (lock, port precheck, unzip,
# start.sh / stop.sh, cleanup). Source this file with ROOT_DIR already set.

LOCK_FILE="/var/tmp/evaluator-host.lock"

clx_log() { printf '[host] %s\n' "$*" >&2; }
clx_die() { printf 'evaluator-host: %s\n' "$*" >&2; exit "${2:-1}"; }

clx_acquire_lock() {
    exec 9>"${LOCK_FILE}"
    if ! flock -n 9; then
        local holder
        holder="$(lsof -t "${LOCK_FILE}" 2>/dev/null | head -1 || echo unknown)"
        clx_die "another evaluator run is in progress (pid=${holder})" 75
    fi
}

clx_precheck_ports() {
    local busy
    busy="$(ss -lntH 'sport = :8080 or sport = :8554' 2>/dev/null || true)"
    if [[ -n "${busy}" ]]; then
        printf 'evaluator-host: port 8080 or 8554 occupied:\n%s\n' "${busy}" >&2
        exit 1
    fi
}

clx_prepare_run_dir() {
    local team_id="$1"
    TS="$(date +%Y%m%d_%H%M%S)"
    RESULTS_SUBDIR="${team_id}_${TS}"
    RUN_DIR="${ROOT_DIR}/results/${RESULTS_SUBDIR}"
    [[ ! -d "${RUN_DIR}" ]] || clx_die "results dir already exists (clock skew?): ${RUN_DIR}"
    mkdir -p "${RUN_DIR}"
    STAGE_DIR="${ROOT_DIR}/submissions/${team_id}"
}

clx_extract_submission() {
    local zip_path="$1"
    [[ -f "${zip_path}" ]] || clx_die "submission zip not found: ${zip_path}"
    rm -rf "${STAGE_DIR}"; mkdir -p "${STAGE_DIR}"
    unzip -qq "${zip_path}" -d "${STAGE_DIR}" || clx_die "unzip failed"
    # Lift single-top-dir layout if present.
    if [[ ! -f "${STAGE_DIR}/start.sh" ]]; then
        local inner
        inner="$(find "${STAGE_DIR}" -mindepth 1 -maxdepth 1 -type d | head -1 || true)"
        if [[ -n "${inner}" && -f "${inner}/start.sh" ]]; then
            shopt -s dotglob; mv "${inner}"/* "${STAGE_DIR}/"; shopt -u dotglob
            rmdir "${inner}" 2>/dev/null || true
        fi
    fi
    [[ -f "${STAGE_DIR}/start.sh" ]] || clx_die "missing start.sh in submission"
    chmod +x "${STAGE_DIR}/start.sh"
    [[ -f "${STAGE_DIR}/stop.sh" ]] && chmod +x "${STAGE_DIR}/stop.sh"
}

clx_start_contestant() {
    export RTSP_SERVER_HOST=127.0.0.1
    export RTSP_SERVER_PORT=8554
    export FRONTEND_PORT=8080
    (cd "${STAGE_DIR}" && setsid ./start.sh) > "${RUN_DIR}/contestant.log" 2>&1 &
    CONTESTANT_PID=$!
    echo "${CONTESTANT_PID}" > "${RUN_DIR}/contestant.pid"
    clx_log "contestant pid=${CONTESTANT_PID}"
}

clx_wait_frontend_ready() {
    local i
    for i in $(seq 1 60); do
        if curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:8080/play?codec=h264&autoplay=1"; then
            clx_log "frontend reachable after ${i}s"
            return 0
        fi
        sleep 1
    done
    return 1
}

clx_cleanup_contestant() {
    if [[ -n "${CONTESTANT_PID:-}" ]] && kill -0 "${CONTESTANT_PID}" 2>/dev/null; then
        if [[ -x "${STAGE_DIR}/stop.sh" ]]; then
            (cd "${STAGE_DIR}" && timeout 10 ./stop.sh) > /dev/null 2>&1 || true
        fi
        kill -- "-${CONTESTANT_PID}" 2>/dev/null || true
        sleep 1
        kill -9 -- "-${CONTESTANT_PID}" 2>/dev/null || true
    fi
    fuser -k 8080/tcp 8554/tcp 2>/dev/null || true
}

clx_emit_score_to_fd3() {
    [[ -f "${RUN_DIR}/score.json" ]] && cat "${RUN_DIR}/score.json" >&3 || true
}
```

Run:
```bash
bash -n scripts/_contestant_lifecycle.sh
```
Expected: no syntax errors.

- [ ] **Step 2.2: Create scripts/evaluator-local.sh skeleton**

```bash
#!/usr/bin/env bash
# Build-host local-development entry. Same UX as evaluator-host.sh but
# invokes scripts/evaluator.sh natively (no docker). Useful when iterating
# on the evaluator pipeline before re-packaging the image.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
    cat >&2 <<'EOF'
Usage: scripts/evaluator-local.sh <team_id> <submission_zip>

Build-host dev shortcut; runs evaluator natively without docker. For
target-host evaluation use scripts/evaluator-host.sh instead.
EOF
    exit 64
}

(( $# >= 2 )) || usage
TEAM_ID="$1"
SUBMISSION_ZIP="$(readlink -f "$2" 2>/dev/null || echo "$2")"

# shellcheck source=_contestant_lifecycle.sh
source "${SCRIPT_DIR}/_contestant_lifecycle.sh"

clx_acquire_lock
clx_precheck_ports
clx_prepare_run_dir "${TEAM_ID}"

# fd-3 holds the original stdout for the final score JSON; tee logs to file.
exec 3>&1
exec > >(tee -a "${RUN_DIR}/evaluator-host.log" >&2) 2>&1

cleanup() {
    local rc=$?
    clx_cleanup_contestant
    clx_emit_score_to_fd3
    exit "${rc}"
}
trap cleanup EXIT INT TERM

clx_extract_submission "${SUBMISSION_ZIP}"
clx_start_contestant

if ! clx_wait_frontend_ready; then
    clx_log "contestant frontend never became ready"
    "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scorer.py" \
        --output "${RUN_DIR}/score.json" \
        --report "${RUN_DIR}/report.html" \
        --install-prefix "${ROOT_DIR}/third_party/install" \
        --failure-reason "contestant_frontend_unavailable" \
        > /dev/null 2>&1 || true
    exit 2
fi

"${SCRIPT_DIR}/evaluator.sh" "${TEAM_ID}" "${RESULTS_SUBDIR}"
```

Run:
```bash
chmod +x scripts/_contestant_lifecycle.sh scripts/evaluator-local.sh
bash -n scripts/evaluator-local.sh
```
Expected: no syntax errors.

- [ ] **Step 2.3: Smoke-test evaluator-local.sh against reference**

```bash
./scripts/teardown.sh   # make sure no leftover MediaMTX/contestant
./scripts/evaluator-local.sh smoke_local test_submissions/reference.zip > /tmp/local_score.json
jq .objective_total /tmp/local_score.json
```
Expected: prints `15` (h264 full) or near it; `>=13` per reference gate.

- [ ] **Step 2.4: Smoke-test contestant-failure path**

```bash
./scripts/teardown.sh
./scripts/evaluator-local.sh smoke_fail test_submissions/never_ready.zip > /tmp/local_fail.json
echo "exit=$?"
jq -r .reason /tmp/local_fail.json
```
Expected: exit code 2 and `reason` contains `contestant_frontend_unavailable`.

- [ ] **Step 2.5: Commit**

```bash
git add scripts/_contestant_lifecycle.sh scripts/evaluator-local.sh
git commit -m "[feat] 新增 scripts/evaluator-local.sh（构建机本地评测入口）"
```

---

## Task 3: Validate split with existing test.sh default mode

**Files:**
- Modify: `scripts/test.sh`

- [ ] **Step 3.1: Replace evaluator.sh invocation with evaluator-local.sh**

Edit `scripts/test.sh` line 66 from:
```bash
    if ! "${SCRIPT_DIR}/evaluator.sh" "${team_id}" "${zip}" > "${out_dir}/stdout.json" 2> "${out_dir}/stderr.log"; then
```
to:
```bash
    if ! "${SCRIPT_DIR}/evaluator-local.sh" "${team_id}" "${zip}" > "${out_dir}/stdout.json" 2> "${out_dir}/stderr.log"; then
```

Run:
```bash
bash -n scripts/test.sh
```

- [ ] **Step 3.2: Run test.sh and confirm all cases still PASS**

```bash
./scripts/teardown.sh
./scripts/test.sh
```
Expected: all 7 cases PASS (reference, static_frame, iframe_only, fake_overlay, missing_start, never_ready, missing_testid).

- [ ] **Step 3.3: Verify score parity against pre-change baseline**

```bash
ls -td results/selftest_reference_* | head -1 | xargs -I{} jq . {}/score.json > /tmp/new_reference_score.json
python3 - <<'PY'
import json
old = json.load(open('/tmp/baseline_reference_score.json'))
new = json.load(open('/tmp/new_reference_score.json'))
assert old.keys() == new.keys(), f"key drift: {set(old)^set(new)}"
def ssim(d): return d.get('mean_ssim', 0)
for codec in ('h264', 'h265'):
    if codec in old and codec in new:
        delta = abs(ssim(old[codec]) - ssim(new[codec]))
        assert delta < 0.05, f"{codec} SSIM drift: {delta}"
print("OK: schema identical, SSIM within tolerance")
PY
```
Expected: prints `OK: schema identical, SSIM within tolerance`.

- [ ] **Step 3.4: Commit**

```bash
git add scripts/test.sh
git commit -m "[refactor] scripts/test.sh 走 evaluator-local.sh"
```

---

## Task 4: Author Dockerfile and .dockerignore

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Modify: `.gitignore`

- [ ] **Step 4.1: Add `.playwright/` and `dist/` to .gitignore**

Append to `.gitignore`:
```
.playwright/
dist/
```

- [ ] **Step 4.2: Create .dockerignore**

```
.git/
.gitignore
.gitmodules
submissions/
results/
test_submissions/*.zip
dist/
evaluator.log
rtsp_server/mediamtx.pid
openspec/
README.md
*.md
__pycache__/
.pytest_cache/
.mypy_cache/
```
Note: we do NOT ignore `.playwright/`, `third_party/install/`, `.venv/`, `streams/`, `reference/` — those go into the build context.

- [ ] **Step 4.3: Stage Playwright cache locally**

```bash
rsync -a --delete ~/.cache/ms-playwright/ ./.playwright/
ls -d .playwright/chromium-*
```
Expected: at least one `chromium-NNNN` directory.

- [ ] **Step 4.4: Author Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1.6
# Multi-stage portable evaluator image. The "builder" stage only COPIES
# pre-built host assets (third_party/install, .venv, .playwright, streams,
# reference). The "runtime" stage starts from ubuntu:24.04 and installs
# the Chromium runtime libraries Playwright needs. NO source compilation
# happens inside docker; build host scripts/setup.sh + scripts/build.sh +
# scripts/deploy.sh must have produced the inputs first.

FROM ubuntu:24.04 AS builder
WORKDIR /work
COPY third_party/install /work/third_party/install
COPY .venv               /work/.venv
COPY .playwright         /work/.playwright
COPY streams             /work/streams
COPY reference           /work/reference
COPY lib                 /work/lib
COPY rtsp_server         /work/rtsp_server
COPY scripts             /work/scripts
COPY runner.py analyzer.py scorer.py report.py requirements.txt /work/

FROM ubuntu:24.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/work/.playwright \
    TESSDATA_PREFIX=/work/third_party/install/share/tessdata
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        libnss3 libxkbcommon0 libdrm2 libxcomposite1 libxdamage1 libxrandr2 \
        libgbm1 libpango-1.0-0 libcairo2 libasound2t64 libatk-bridge2.0-0 \
        libatk1.0-0 libcups2 libxss1 libxshmfence1 \
        fonts-liberation \
        unzip lsof procps curl jq \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
COPY --from=builder /work/third_party/install /work/third_party/install
COPY --from=builder /work/.venv               /work/.venv
COPY --from=builder /work/.playwright         /work/.playwright
COPY --from=builder /work/streams             /work/streams
COPY --from=builder /work/reference           /work/reference
COPY --from=builder /work/lib                 /work/lib
COPY --from=builder /work/rtsp_server         /work/rtsp_server
COPY --from=builder /work/scripts             /work/scripts
COPY --from=builder /work/runner.py /work/analyzer.py /work/scorer.py /work/report.py /work/requirements.txt /work/
ENTRYPOINT ["/work/scripts/evaluator.sh"]
```

- [ ] **Step 4.5: Build the image**

```bash
docker build -t evaluator-portable:dev . 2>&1 | tail -30
docker image inspect evaluator-portable:dev --format '{{.Size}}' | numfmt --to=iec
```
Expected: build succeeds; size ~1.3–1.5 GB uncompressed.

- [ ] **Step 4.6: Smoke test 1 — image initializes offline**

```bash
docker run --rm --network none --entrypoint /work/.venv/bin/python \
    evaluator-portable:dev -c 'import playwright, PIL, numpy, skimage, pylibdmtx; print("ok")'
```
Expected: prints `ok`.

- [ ] **Step 4.7: Smoke test 2 — MediaMTX + health check in container**

```bash
./scripts/teardown.sh
docker run --rm --network host --entrypoint /bin/bash evaluator-portable:dev -c '
    /work/scripts/start_rtsp.sh
    sleep 2
    /work/scripts/health_check.sh h264 5
'
```
Expected: exits 0; `ffprobe` health check passes.

- [ ] **Step 4.8: Commit**

```bash
git add Dockerfile .dockerignore .gitignore
git commit -m "[feat] 新增 Dockerfile + .dockerignore（multi-stage 仅 COPY）"
```

---

## Task 5: Build scripts/package.sh

**Files:**
- Create: `scripts/package.sh`

- [ ] **Step 5.1: Create scripts/package.sh with precheck**

```bash
#!/usr/bin/env bash
# Produce dist/ portable bundle. Assumes scripts/setup.sh + build.sh +
# deploy.sh have already run on this build host. Idempotent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

log()  { printf '[package] %s\n' "$*" >&2; }
die()  { printf 'package failed: %s\n' "$*" >&2; exit 1; }

USE_GZIP=0
while (( $# > 0 )); do
    case "$1" in
        --gzip) USE_GZIP=1 ;;
        -h|--help) echo "Usage: $0 [--gzip]"; exit 0 ;;
        *) die "unknown arg: $1" ;;
    esac
    shift
done

precheck() {
    [[ -x "${ROOT_DIR}/third_party/install/bin/ffmpeg"    ]] || die "missing third_party/install/bin/ffmpeg — run scripts/build.sh"
    [[ -x "${ROOT_DIR}/third_party/install/bin/mediamtx"  ]] || die "missing third_party/install/bin/mediamtx — run scripts/build.sh"
    [[ -x "${ROOT_DIR}/third_party/install/bin/tesseract" ]] || die "missing third_party/install/bin/tesseract — run scripts/build.sh"
    [[ -f "${ROOT_DIR}/third_party/install/share/tessdata/eng.traineddata" ]] || die "missing eng.traineddata — run scripts/build.sh"
    [[ -x "${ROOT_DIR}/.venv/bin/python" ]] || die "missing .venv/bin/python — run scripts/setup.sh && scripts/build.sh"
    compgen -G "${HOME}/.cache/ms-playwright/chromium-*" >/dev/null \
        || die "missing ~/.cache/ms-playwright/chromium-* — run scripts/build.sh"
    [[ -f "${ROOT_DIR}/streams/h264_watermarked.mp4" ]] || die "missing streams — run scripts/deploy.sh"
    [[ -f "${ROOT_DIR}/streams/h265_watermarked.mp4" ]] || die "missing streams — run scripts/deploy.sh"
    compgen -G "${ROOT_DIR}/reference/h264/frame_*.png" >/dev/null \
        || die "missing reference/h264/ — run scripts/deploy.sh"
    compgen -G "${ROOT_DIR}/reference/h265/frame_*.png" >/dev/null \
        || die "missing reference/h265/ — run scripts/deploy.sh"
    command -v docker >/dev/null || die "docker not installed on build host"
    command -v jq     >/dev/null || die "jq not installed on build host"
    if (( ! USE_GZIP )); then
        command -v zstd >/dev/null || die "zstd not installed on build host (use --gzip to fall back)"
    fi
}
```

- [ ] **Step 5.2: Add staging + build steps**

Append to `scripts/package.sh`:

```bash
stage_playwright() {
    log "staging ~/.cache/ms-playwright/ → ./.playwright/"
    rsync -a --delete "${HOME}/.cache/ms-playwright/" "${ROOT_DIR}/.playwright/"
}

IMAGE_TAG_SHA=""
build_image() {
    IMAGE_TAG_SHA="$(git -C "${ROOT_DIR}" rev-parse --short=12 HEAD)"
    log "docker build -t evaluator-portable:${IMAGE_TAG_SHA} ."
    docker build -t "evaluator-portable:${IMAGE_TAG_SHA}" "${ROOT_DIR}"
}

DIST_DIR="${ROOT_DIR}/dist"
mkdir_dist() {
    mkdir -p "${DIST_DIR}"
}

save_image() {
    local out
    if (( USE_GZIP )); then
        out="${DIST_DIR}/evaluator-portable_${IMAGE_TAG_SHA}.tar.gz"
        log "docker save | gzip → ${out}"
        docker save "evaluator-portable:${IMAGE_TAG_SHA}" | gzip > "${out}"
    else
        out="${DIST_DIR}/evaluator-portable_${IMAGE_TAG_SHA}.tar.zst"
        log "docker save | zstd -19 -T0 → ${out}"
        docker save "evaluator-portable:${IMAGE_TAG_SHA}" | zstd -19 -T0 -o "${out}" -
    fi
    IMAGE_ARCHIVE="${out}"
}
```

- [ ] **Step 5.3: Add manifest emission**

Append to `scripts/package.sh`:

```bash
write_manifest() {
    local image_sha image_size git_full ts pw_ver submod_json
    image_sha="$(docker image inspect "evaluator-portable:${IMAGE_TAG_SHA}" --format '{{.Id}}')"
    image_size="$(stat -c %s "${IMAGE_ARCHIVE}")"
    git_full="$(git -C "${ROOT_DIR}" rev-parse HEAD)"
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    pw_ver="$(cat "${ROOT_DIR}/third_party/install/playwright_chromium.version" 2>/dev/null | tr '\n' ' ' | sed 's/  *$//')"
    submod_json="$(git -C "${ROOT_DIR}" submodule status \
        | awk '{ gsub(/^[ +-]/,"",$1); print "\""$2"\":\""$1"\"" }' \
        | paste -sd, -)"
    cat > "${DIST_DIR}/manifest.json" <<EOF
{
  "git_sha": "${git_full}",
  "build_timestamp": "${ts}",
  "submodule_status": { ${submod_json} },
  "playwright_chromium_version": "${pw_ver}",
  "image_sha256": "${image_sha}",
  "image_size_bytes": ${image_size}
}
EOF
    log "manifest written: ${DIST_DIR}/manifest.json"
}
```

- [ ] **Step 5.4: Add README + host script copy + SHA256SUMS**

Append to `scripts/package.sh`:

```bash
copy_host_script() {
    cp "${SCRIPT_DIR}/evaluator-host.sh" "${DIST_DIR}/evaluator-host.sh"
    chmod +x "${DIST_DIR}/evaluator-host.sh"
}

write_readme() {
    cat > "${DIST_DIR}/README.md" <<'EOF'
# 评测器便携包

## 目标机器前置条件

- x86_64 Linux（Ubuntu 24.04 推荐，glibc 兼容即可）
- Docker engine ≥ 20.10 或 rootful podman
- `zstd` 命令行工具（用 `--gzip` 模式打包则改用 `gzip`）
- 操作员可读写当前目录

## 一次性导入

```bash
sha256sum -c SHA256SUMS
zstd -d evaluator-portable_<sha>.tar.zst -o image.tar
docker load < image.tar && rm image.tar
mkdir -p submissions results
```

## 每个 submission 跑一次

```bash
./evaluator-host.sh <team_id> <submission_zip>
```

产出位置：`./results/<team_id>_<timestamp>/`

- `score.json`：可对外的最终分数
- `report.html`：内部审计报告
- `evaluator-host.log` / `evaluator.log`：日志

## 退出码

| 码 | 含义 |
|---|---|
| 0  | 评测完成（含拿 0 分） |
| 1  | 基础设施异常（docker / 端口 / 解压等） |
| 2  | 选手 frontend 起不来；已写极简 score.json |
| 75 | 另一次评测正在进行（flock 互斥） |

## 故障排查

- **镜像未加载**：`docker image inspect evaluator-portable:$(jq -r .image_sha256 manifest.json | cut -c8-19)`
- **端口被占**：`ss -lntp | grep -E ':(8080|8554)\b'`
- **sha 不一致**：重 `sha256sum -c SHA256SUMS` 验证；不一致禁止使用

## 清理旧镜像

```bash
docker images evaluator-portable --format '{{.Tag}}' | tail -n +3 | xargs -r -I{} docker rmi evaluator-portable:{}
```
EOF
}

write_sums() {
    (cd "${DIST_DIR}" && sha256sum \
        "$(basename "${IMAGE_ARCHIVE}")" \
        evaluator-host.sh \
        README.md \
        manifest.json \
        > SHA256SUMS)
}
```

- [ ] **Step 5.5: Add main() entry**

Append to `scripts/package.sh`:

```bash
main() {
    precheck
    mkdir_dist
    stage_playwright
    build_image
    save_image
    write_manifest
    copy_host_script
    write_readme
    write_sums
    local sz; sz="$(numfmt --to=iec --suffix=B "$(stat -c %s "${IMAGE_ARCHIVE}")")"
    log "package ok: ${IMAGE_ARCHIVE} (${sz})"
}

main "$@"
```

Run:
```bash
chmod +x scripts/package.sh
bash -n scripts/package.sh
```

- [ ] **Step 5.6: Defer first end-to-end package.sh run until Task 6**

Reason: `package.sh` copies `scripts/evaluator-host.sh` into dist/, but evaluator-host.sh doesn't exist yet. Continue to Task 6 first, then return for full package.sh verification in Task 6.

- [ ] **Step 5.7: Partial commit (without evaluator-host.sh dependency yet)**

Add a temporary placeholder so the commit is sensible on its own:
```bash
touch scripts/evaluator-host.sh
chmod +x scripts/evaluator-host.sh
git add scripts/package.sh scripts/evaluator-host.sh
git commit -m "[feat] 新增 scripts/package.sh（依赖 evaluator-host.sh 待补）"
```

---

## Task 6: Build scripts/evaluator-host.sh (target-host wrapper)

**Files:**
- Modify: `scripts/evaluator-host.sh` (currently empty placeholder)

- [ ] **Step 6.1: Author full scripts/evaluator-host.sh**

Overwrite `scripts/evaluator-host.sh` with:

```bash
#!/usr/bin/env bash
# Target-host operator entry. Reads manifest.json adjacent to this script
# (or in --manifest path) for image_sha256; pulls contestant zip apart on
# the host; runs evaluator.sh inside the portable OCI container via
# `docker run --network host`.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MANIFEST="${SCRIPT_DIR}/manifest.json"
[[ -f "${MANIFEST}" ]] || MANIFEST="${ROOT_DIR}/dist/manifest.json"

USE_ROOT=0
ARGS=()
while (( $# > 0 )); do
    case "$1" in
        --root) USE_ROOT=1 ;;
        --manifest) MANIFEST="$2"; shift ;;
        -h|--help) cat >&2 <<'EOF'
Usage: evaluator-host.sh [--root] [--manifest <path>] <team_id> <submission_zip>
EOF
            exit 0 ;;
        *) ARGS+=("$1") ;;
    esac
    shift
done
set -- "${ARGS[@]}"
(( $# >= 2 )) || { echo "usage: evaluator-host.sh <team_id> <submission_zip>" >&2; exit 64; }
TEAM_ID="$1"
SUBMISSION_ZIP="$(readlink -f "$2" 2>/dev/null || echo "$2")"

# shellcheck source=_contestant_lifecycle.sh
source "${SCRIPT_DIR}/_contestant_lifecycle.sh"

[[ -f "${MANIFEST}" ]] || clx_die "manifest.json not found at ${MANIFEST}"
command -v jq     >/dev/null || clx_die "jq required on target host"
command -v docker >/dev/null || clx_die "docker required on target host"

IMAGE_SHA="$(jq -r .image_sha256 "${MANIFEST}")"
[[ -n "${IMAGE_SHA}" && "${IMAGE_SHA}" != "null" ]] || clx_die "manifest.json missing image_sha256"
IMAGE_REF="${IMAGE_SHA}"   # docker image inspect accepts the full sha256:... id

docker image inspect "${IMAGE_REF}" >/dev/null 2>&1 \
    || clx_die "image ${IMAGE_REF} not loaded — run: docker load < evaluator-portable_<sha>.tar.zst"
docker info >/dev/null 2>&1 || clx_die "docker daemon unreachable"

clx_acquire_lock
clx_precheck_ports
clx_prepare_run_dir "${TEAM_ID}"

exec 3>&1
exec > >(tee -a "${RUN_DIR}/evaluator-host.log" >&2) 2>&1

DOCKER_USER_FLAG=()
(( ! USE_ROOT )) && DOCKER_USER_FLAG=(--user "$(id -u):$(id -g)")

cleanup() {
    local rc=$?
    clx_cleanup_contestant
    # Belt-and-braces: kill any container still tied to this image.
    docker ps -q --filter "ancestor=${IMAGE_REF}" 2>/dev/null | xargs -r docker kill 2>/dev/null || true
    clx_emit_score_to_fd3
    exit "${rc}"
}
trap cleanup EXIT INT TERM

clx_extract_submission "${SUBMISSION_ZIP}"
clx_start_contestant

if ! clx_wait_frontend_ready; then
    clx_log "contestant frontend never became ready — writing failure score via container"
    docker run --rm "${DOCKER_USER_FLAG[@]}" \
        -v "${RUN_DIR}:/work/results/${RESULTS_SUBDIR}:rw" \
        --entrypoint /work/.venv/bin/python \
        "${IMAGE_REF}" \
        /work/scorer.py \
            --output "/work/results/${RESULTS_SUBDIR}/score.json" \
            --report "/work/results/${RESULTS_SUBDIR}/report.html" \
            --install-prefix /work/third_party/install \
            --failure-reason "contestant_frontend_unavailable" \
        > /dev/null 2>&1 || true
    exit 2
fi

clx_log "invoking evaluator container"
docker run --rm --network host "${DOCKER_USER_FLAG[@]}" \
    -v "${RUN_DIR}:/work/results/${RESULTS_SUBDIR}:rw" \
    "${IMAGE_REF}" \
    "${TEAM_ID}" "${RESULTS_SUBDIR}"
```

Run:
```bash
chmod +x scripts/evaluator-host.sh
bash -n scripts/evaluator-host.sh
```

- [ ] **Step 6.2: Run scripts/package.sh end-to-end**

```bash
./scripts/teardown.sh
rm -rf dist/
./scripts/package.sh
ls -la dist/
```
Expected: `dist/` contains exactly 5 entries: `evaluator-portable_<sha>.tar.zst`, `evaluator-host.sh`, `README.md`, `SHA256SUMS`, `manifest.json`.

- [ ] **Step 6.3: Verify SHA256SUMS**

```bash
cd dist && sha256sum -c SHA256SUMS && cd ..
```
Expected: `OK` for all 4 files.

- [ ] **Step 6.4: Verify manifest schema**

```bash
jq 'keys' dist/manifest.json
```
Expected: array exactly `["build_timestamp","git_sha","image_sha256","image_size_bytes","playwright_chromium_version","submodule_status"]`.

- [ ] **Step 6.5: Smoke test — load and run reference on build host via host script**

```bash
./scripts/teardown.sh
SHA=$(jq -r .image_sha256 dist/manifest.json)
docker image inspect "${SHA}" >/dev/null || die "image not in daemon"  # already there from build
./dist/evaluator-host.sh team_smoke test_submissions/reference.zip > /tmp/host_score.json
echo "exit=$?"
jq .objective_total /tmp/host_score.json
```
Expected: exit 0; `objective_total >= 13`.

- [ ] **Step 6.6: Smoke test — file ownership**

```bash
ls -la results/team_smoke_*/score.json
```
Expected: owner is `zhiwei` (current user), NOT `root`.

- [ ] **Step 6.7: Smoke test — contestant failure path**

```bash
./scripts/teardown.sh
./dist/evaluator-host.sh team_fail test_submissions/never_ready.zip > /tmp/host_fail.json
echo "exit=$?"
jq -r .reason /tmp/host_fail.json
```
Expected: exit 2; `reason` contains `contestant_frontend_unavailable`.

- [ ] **Step 6.8: Commit**

```bash
git add scripts/evaluator-host.sh
git commit -m "[feat] 新增 scripts/evaluator-host.sh（目标机操作员入口）"
```

---

## Task 7: Build scripts/test.sh --portable mode

**Files:**
- Modify: `scripts/test.sh`

- [ ] **Step 7.1: Add --portable arg parsing**

Edit `scripts/test.sh` near the top (after `set -euo pipefail`, before the EXPECTED block):

```bash
MODE="default"
while (( $# > 0 )); do
    case "$1" in
        --portable) MODE="portable" ;;
        *) printf 'unknown arg: %s\n' "$1" >&2; exit 64 ;;
    esac
    shift
done
```

- [ ] **Step 7.2: Add portable_main()**

Append above `main()`:

```bash
portable_main() {
    local target="${EVAL_TARGET_HOST:-}"
    [[ -n "${target}" ]] || { printf 'test.sh --portable requires EVAL_TARGET_HOST=user@host\n' >&2; exit 1; }
    log "stage 1: package.sh"
    "${SCRIPT_DIR}/package.sh"
    (cd "${ROOT_DIR}/dist" && sha256sum -c SHA256SUMS) >&2
    log "stage 1: smoke offline init"
    local sha
    sha="$(jq -r .image_sha256 "${ROOT_DIR}/dist/manifest.json")"
    docker run --rm --network none --entrypoint /work/.venv/bin/python "${sha}" \
        -c 'import playwright; print("offline ok")' >&2

    log "stage 2: deploy + run on ${target}"
    ssh "${target}" 'mkdir -p ~/evaluator-test/{submissions,results}'
    scp "${ROOT_DIR}/dist"/* "${target}:~/evaluator-test/"
    scp "${ROOT_DIR}/test_submissions/reference.zip" "${target}:~/evaluator-test/"
    ssh "${target}" '
        cd ~/evaluator-test
        zstd -df evaluator-portable_*.tar.zst -o /tmp/eval_image.tar
        docker load < /tmp/eval_image.tar
        rm /tmp/eval_image.tar
        ./evaluator-host.sh team_ref reference.zip > /tmp/portable_remote_score.json
    '
    scp "${target}:~/evaluator-test/results/team_ref_*/score.json" /tmp/remote_score.json

    log "stage 3: parity check vs evaluator-local.sh"
    "${SCRIPT_DIR}/teardown.sh"
    "${SCRIPT_DIR}/evaluator-local.sh" team_ref_local "${ROOT_DIR}/test_submissions/reference.zip" \
        > /tmp/local_parity_score.json
    python3 - /tmp/remote_score.json /tmp/local_parity_score.json <<'PY'
import json, sys
a = json.load(open(sys.argv[1]))
b = json.load(open(sys.argv[2]))
assert a.keys() == b.keys(), f"key drift: {set(a)^set(b)}"
def ssim(d, c): return (d.get(c) or {}).get("mean_ssim", 0)
for c in ("h264", "h265"):
    if c in a and c in b:
        delta = abs(ssim(a, c) - ssim(b, c))
        assert delta < 0.05, f"{c} ssim drift: {delta}"
print("stage 3 ok")
PY

    log "stage 4: negative fixture exit code"
    scp "${ROOT_DIR}/test_submissions/never_ready.zip" "${target}:~/evaluator-test/" 2>/dev/null \
        || { printf 'never_ready.zip absent on build host — run scripts/build_test_zips.sh first\n' >&2; exit 1; }
    set +e
    ssh "${target}" 'cd ~/evaluator-test && ./evaluator-host.sh team_fail never_ready.zip > /tmp/fail_score.json'
    local rc=$?
    set -e
    [[ ${rc} -eq 2 ]] || { printf 'stage 4: expected exit 2, got %d\n' "${rc}" >&2; exit 1; }
    scp "${target}:~/evaluator-test/results/team_fail_*/score.json" /tmp/remote_fail.json
    local reason
    reason="$(jq -r .reason /tmp/remote_fail.json)"
    [[ "${reason}" == *contestant_frontend_unavailable* ]] \
        || { printf 'stage 4: bad reason: %s\n' "${reason}" >&2; exit 1; }

    printf '\n=== test.sh --portable summary ===\nALL STAGES PASS\n'
}
```

- [ ] **Step 7.3: Wire mode dispatcher in main()**

Add to the very top of `main()`:
```bash
    if [[ "${MODE}" == "portable" ]]; then
        portable_main
        return
    fi
```

- [ ] **Step 7.4: Smoke test --portable refuses without EVAL_TARGET_HOST**

```bash
unset EVAL_TARGET_HOST
./scripts/test.sh --portable
echo "exit=$?"
```
Expected: exit 1; message about EVAL_TARGET_HOST required.

- [ ] **Step 7.5: Run --portable against the second machine**

```bash
export EVAL_TARGET_HOST=<user>@<host>   # replace with the actual second eval host
./scripts/test.sh --portable
```
Expected: prints `ALL STAGES PASS`.

- [ ] **Step 7.6: Commit**

```bash
git add scripts/test.sh
git commit -m "[feat] scripts/test.sh 新增 --portable 子模式"
```

---

## Task 8: Update repo-level documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 8.1: Update Dev Commands table**

In `CLAUDE.md`, find the "Dev Commands" table. Replace the `./scripts/evaluator.sh ...` row and add new rows:

```markdown
| `./scripts/package.sh [--gzip]` | Build host: produce `dist/` portable bundle (zstd by default) |
| `./scripts/evaluator-host.sh <team_id> <submission.zip>` | Target host: score one contestant via OCI container; expects docker + loaded image |
| `./scripts/evaluator-local.sh <team_id> <submission.zip>` | Build host: native scoring shortcut, no docker |
| `./scripts/evaluator.sh <team_id> <results_subdir>` | Evaluator body (invoked by the two wrappers; not for direct operator use) |
```

- [ ] **Step 8.2: Update Data Flow section**

Append to the existing "Data Flow" section in `CLAUDE.md`:

```markdown
8. `scripts/package.sh` snapshots build-host assets into an OCI image (`evaluator-portable:<sha>`), saves it as `dist/evaluator-portable_<sha>.tar.zst`, and emits `dist/manifest.json` + `dist/SHA256SUMS` + `dist/README.md` + `dist/evaluator-host.sh`. Target hosts `docker load` and run `evaluator-host.sh` — same data flow inside the container as steps 1–7 above. Note: MediaMTX is per-container-run, not host-resident.
```

- [ ] **Step 8.3: Update Conventions section**

Append to "Conventions" in `CLAUDE.md`:

```markdown
- **Portable bundle**: `scripts/package.sh` produces `dist/` after a successful `setup.sh && build.sh && deploy.sh`. The bundle's target-host prerequisites are docker (or rootful podman) and zstd — nothing else. Rootless podman, arm64, and macOS hosts are explicit non-goals. The bundle's image is sha-locked: `evaluator-host.sh` refuses to run unless the loaded image matches `manifest.json`'s `image_sha256`.
- **Generated dir update**: `dist/` and `.playwright/` are now also git-ignored (in addition to the existing list).
```

- [ ] **Step 8.4: Commit**

```bash
git add CLAUDE.md
git commit -m "[doc] CLAUDE.md 反映便携包入口"
```

---

## Task 9: Final verification (V1–V7)

No source code changes; this task records evidence for the spec's verification clause and gates archival of the OpenSpec change.

- [ ] **Step 9.1: V1 cold-chain on build host**

```bash
# In a clean shell, starting from a freshly-cloned repo would be ideal;
# here we simulate by re-running the lifecycle.
./scripts/setup.sh
./scripts/build.sh
./scripts/deploy.sh
./scripts/package.sh
ls -la dist/
(cd dist && sha256sum -c SHA256SUMS)
```
Record: pass/fail; image size from package.sh's final log line.

- [ ] **Step 9.2: V2 e2e on second Ubuntu 24.04 machine**

```bash
export EVAL_TARGET_HOST=<user>@<second_host>
./scripts/test.sh --portable
```
Record: `ALL STAGES PASS` (or capture failure log).

- [ ] **Step 9.3: V3 docker post-run filesystem audit**

```bash
SHA="$(jq -r .image_sha256 dist/manifest.json)"
docker run -d --network host --user "$(id -u):$(id -g)" \
    -v "$PWD/results/audit_run:/work/results/audit_run:rw" \
    --name eval_audit "${SHA}" team_audit audit_run
sleep 3
docker inspect eval_audit --format '{{range .Mounts}}{{.Type}}:{{.Source}}:{{.Destination}}:{{.RW}} {{end}}'
docker diff eval_audit | head -20
docker kill eval_audit; docker rm eval_audit
```
Record: only `/work/results/audit_run` is RW; `docker diff` shows no writes outside that path (some `C /var/log` or `/tmp` from apt-cache may exist; record but not a regression).

- [ ] **Step 9.4: V4 offline-init confirmation**

```bash
SHA="$(jq -r .image_sha256 dist/manifest.json)"
docker run --rm --network none --entrypoint /work/.venv/bin/python "${SHA}" \
    -c 'import playwright, pylibdmtx.pylibdmtx; print("ok")'
```
Record: prints `ok`.

- [ ] **Step 9.5: V5 evaluator-local ↔ evaluator-host parity**

Already exercised in `scripts/test.sh --portable` stage 3. Re-record by:
```bash
./scripts/teardown.sh
./scripts/evaluator-local.sh v5_local test_submissions/reference.zip > /tmp/v5_local.json
./scripts/teardown.sh
./scripts/evaluator-host.sh v5_host test_submissions/reference.zip > /tmp/v5_host.json
diff <(jq -S 'del(.chromium_build_string, .build_timestamp)' /tmp/v5_local.json) \
     <(jq -S 'del(.chromium_build_string, .build_timestamp)' /tmp/v5_host.json) || true
```
Record: only allowed differences are timestamps / volatile metadata.

- [ ] **Step 9.6: V6 non-root operation on target machine**

On the second machine, as a non-root user:
```bash
id -u   # confirm non-zero
./evaluator-host.sh v6 reference.zip
ls -la results/v6_*/score.json   # owner must match `id -un`, not root
```
Record: file owner matches operator user.

- [ ] **Step 9.7: V7 image size recorded informationally**

```bash
jq .image_size_bytes dist/manifest.json
numfmt --to=iec --suffix=B "$(jq .image_size_bytes dist/manifest.json)"
```
Record: image size (no gate; design.md §6.1 V7 confirms no hard threshold).

- [ ] **Step 9.8: Tag verification milestone (no code change)**

```bash
git tag -a portable-bundle-v1-verified -m "V1-V7 evidence captured"
```

---

## Self-Review

**Spec coverage:**
- `Image Build Pipeline` — Task 4, Task 5
- `Dist Bundle Layout` — Task 5 steps 5.3, 5.4
- `Target Host Operator Entry` — Task 6
- `Container Network Model` — Task 4 (Dockerfile), Task 6.1 (--network host on docker run)
- `Image Versioning and Verification` — Task 6.1 (jq → manifest.json image_sha256)
- `Concurrent Run Mutex` — Task 2.1 (`clx_acquire_lock`)
- `Failure Score on Contestant Unavailable` — Task 6.1 (docker run scorer.py path)
- `Build Host Local Shortcut` — Task 2
- `Target Host Prerequisites` — Task 5 README + CLAUDE.md (Task 8.3)
- `Portable Self-Test Mode` — Task 7
- evaluator delta `Workspace Layout` — Task 8.1 (CLAUDE.md update reflects the new files)
- evaluator delta `Orchestration and Cleanup` — Task 1 (evaluator.sh refactor)
- evaluator delta `Lifecycle Scripts` — Task 5 (package.sh), Task 8 (docs)

**Placeholder scan:** No TBDs, no "implement later", no "similar to Task N", no orphaned function names.

**Type / signature consistency:**
- `clx_*` function names consistent across `_contestant_lifecycle.sh`, `evaluator-local.sh`, `evaluator-host.sh`
- `evaluator.sh <team_id> <results_subdir>` signature consistent in Task 1, Task 2.2 invocation, Task 6.1 docker run args
- `RUN_DIR` derived consistently as `${ROOT_DIR}/results/${TEAM_ID}_${TS}` everywhere
- `manifest.json` 6-field set consistent in Task 5.3 and Task 6.4 verification

**One known dependency cycle resolved by Task 5.6 / 5.7:** `package.sh` references `evaluator-host.sh`, but `evaluator-host.sh` itself isn't built until Task 6. Task 5.7 commits with an empty placeholder; Task 6.1 fills it in. Task 6.2 is the first full `package.sh` end-to-end run.

---

## Execution Handoff

OpenSpec workflow next step — implementation, not another artifact:

```
/opsx:apply portable-deployment-bundle
```

This drives the tasks above with the standard apply-mode review checkpoints. After implementation completes, `/opsx:continue` will write the `verify` artifact, then `retrospective`, then `/opsx:archive` finalizes the change and syncs the spec deltas.

Alternative (non-OpenSpec, raw Superpowers):
- Subagent-driven: dispatch a fresh subagent per Task 1..9
- Inline: walk the steps in this session with `superpowers:executing-plans`
