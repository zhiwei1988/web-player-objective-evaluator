#!/usr/bin/env bash
# Build-host development helper: ensure watermarked reference streams exist and
# are fresh. RTSP server lifecycle is owned per-run by scripts/evaluator.sh —
# this script no longer starts MediaMTX. After a successful deploy.sh, run
# scripts/evaluator.sh to execute an evaluation; MediaMTX will be brought up
# and torn down inside that lifecycle.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

log()  { printf '[deploy] %s\n' "$*" >&2; }
die()  { printf 'deploy failed: %s\n' "$*" >&2; exit 1; }

# Stream paths come from lib/profiles.py — keep this in lockstep with the
# registry. ensure_streams runs prepare_streams.sh which is the source of
# truth for filenames.
ensure_streams() {
    local script="${ROOT_DIR}/lib/watermark.py"
    # Collect expected mp4 paths from the profile registry.
    mapfile -t expected_mp4s < <(.venv/bin/python -c \
        "from lib.profiles import PROFILES; print('\n'.join(s.stream_file for s in PROFILES.values()))")
    local mp4 stale=0
    for mp4 in "${expected_mp4s[@]}"; do
        local abs="${ROOT_DIR}/${mp4}"
        if [[ ! -f "${abs}" || "${abs}" -ot "${script}" ]]; then
            stale=1; break
        fi
    done
    if (( ! stale )); then
        log "streams up to date"
        return 0
    fi
    log "regenerating watermarked streams (this takes a couple minutes)"
    "${SCRIPT_DIR}/prepare_streams.sh"
}

main() {
    [[ -x "${ROOT_DIR}/third_party/install/bin/ffmpeg" ]] \
        || die "ffmpeg missing under third_party/install/bin — run scripts/build.sh first"
    [[ -x "${ROOT_DIR}/third_party/install/bin/mediamtx" ]] \
        || die "mediamtx missing under third_party/install/bin — run scripts/build.sh first"
    ensure_streams
    printf 'deploy ok: streams ready (RTSP server starts per-run via evaluator.sh)\n'
}

main "$@"
