#!/usr/bin/env bash
# Reference contestant submission for evaluator self-test.
#
# A real contestant would do live RTSP decode (MSE / WebCodecs / wasm). This
# stub short-circuits the codec layer: it symlinks the evaluator's own
# pre-rendered reference PNG sequences (reference/<profile>/frame_NNNNN.png)
# into its web root and lets the browser cycle through them at the profile's
# native fps via an <img>. That exercises the full evaluator pipeline
# (capture + analyze + score + report) without depending on Chromium being
# able to demux HEVC in headless mode — which it can't on the canonical
# Ubuntu 24.04 + Chrome host (no hardware HEVC).
#
# The RTSP code path is exercised separately by scripts/deploy.sh's
# health_check.sh probe and by any real contestant submission.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${EVALUATOR_REPO_ROOT:-$(cd "${ROOT}/../.." && pwd)}"
FE_PORT="${FRONTEND_PORT:-8080}"

log() { printf '[ref-start] %s\n' "$*" >&2; }

WEB="${ROOT}/web"
mkdir -p "${WEB}"

# Symlink the per-profile reference frame directories into the web root, so
# the browser can fetch /2k/frame_NNNNN.png etc. directly.
# PYTHONPATH because the submission workspace is not necessarily the repo root.
while IFS=$'\t' read -r profile refdir; do
    src="${REPO_ROOT}/${refdir}"
    dst="${WEB}/${profile}"
    [[ -d "${src}" ]] || { log "missing ${src}"; exit 1; }
    ln -sfn "${src}" "${dst}"
    log "linked ${dst} -> ${src}"
done < <(PYTHONPATH="${REPO_ROOT}" "${REPO_ROOT}/.venv/bin/python" -c "from lib.profiles import PROFILES
for s in PROFILES.values():
    print(f'{s.name}\t{s.reference_dir}')")

log "starting http server on :${FE_PORT}"
cd "${ROOT}"
exec python3 server.py --port "${FE_PORT}" --root "${WEB}"
