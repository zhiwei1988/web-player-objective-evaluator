#!/usr/bin/env bash
# Bring the evaluator to a ready state: streams generated, RTSP up, health-check passes.
# Idempotent: skips stream regeneration if up to date, restarts MediaMTX if it has died.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

log()  { printf '[deploy] %s\n' "$*" >&2; }
die()  { printf 'deploy failed: %s\n' "$*" >&2; exit 1; }

ensure_streams() {
    local h264="${ROOT_DIR}/streams/h264_watermarked.mp4"
    local h265="${ROOT_DIR}/streams/h265_watermarked.mp4"
    local script="${ROOT_DIR}/lib/watermark.py"
    if [[ -f "${h264}" && -f "${h265}" \
          && "${h264}" -nt "${script}" && "${h265}" -nt "${script}" ]]; then
        log "streams up to date"
        return 0
    fi
    log "regenerating watermarked streams (this takes a couple minutes)"
    "${SCRIPT_DIR}/prepare_streams.sh"
}

ensure_rtsp() {
    log "starting RTSP server"
    "${SCRIPT_DIR}/start_rtsp.sh"
}

health_check() {
    log "RTSP health check"
    "${SCRIPT_DIR}/health_check.sh" h264 \
        || die "rtsp h264 health check failed"
    "${SCRIPT_DIR}/health_check.sh" h265 \
        || die "rtsp h265 health check failed"
}

main() {
    [[ -x "${ROOT_DIR}/third_party/install/bin/ffmpeg" ]] \
        || die "ffmpeg missing under third_party/install/bin — run scripts/build.sh first"
    [[ -x "${ROOT_DIR}/third_party/install/bin/mediamtx" ]] \
        || die "mediamtx missing under third_party/install/bin — run scripts/build.sh first"
    ensure_streams
    ensure_rtsp
    health_check
    printf 'deploy ok: ready for submissions\n'
}

main "$@"
