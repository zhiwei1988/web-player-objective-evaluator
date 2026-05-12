#!/usr/bin/env bash
# Reference contestant submission for evaluator self-test.
#
# A real contestant would do live RTSP decode (MSE / WebCodecs). This stub
# short-circuits that: it serves the evaluator's own pre-recorded
# streams/<codec>_watermarked.mp4 directly so the evaluator pipeline (capture
# + analyze + score + report) can be exercised end-to-end without depending on
# Chromium being able to demux a re-muxed RTSP-pulled MP4 in headless mode.
#
# The RTSP code path is exercised separately by scripts/deploy.sh's
# health_check.sh probe and by any real contestant submission.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Submission lives at submissions/<team_id>/; repo root is two dirs up.
REPO_ROOT="$(cd "${ROOT}/../.." && pwd)"
FE_PORT="${FRONTEND_PORT:-8080}"

log() { printf '[ref-start] %s\n' "$*" >&2; }

WEB="${ROOT}/web"
mkdir -p "${WEB}"

# Symlink the pre-encoded streams into the web root under the names the
# frontend looks for. Symlinks are fine because http.server resolves them.
for codec in h264 h265; do
    src="${REPO_ROOT}/streams/${codec}_watermarked.mp4"
    dst="${WEB}/${codec}.mp4"
    [[ -f "${src}" ]] || { log "missing ${src}"; exit 1; }
    ln -sf "${src}" "${dst}"
    log "linked ${dst} -> ${src}"
done

log "starting http server on :${FE_PORT}"
cd "${ROOT}"
exec python3 server.py --port "${FE_PORT}" --root "${WEB}"
