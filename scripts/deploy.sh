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
    # Collect expected stream/reference shape from the profile registry. The
    # reference frame count catches old 25fps outputs after the source profile
    # changes to 20fps, even when mtimes alone look fresh.
    mapfile -t expected_profiles < <(.venv/bin/python -c \
        "from lib.profiles import PROFILES
for s in PROFILES.values():
    print(f'{s.stream_file}\t{s.reference_dir}\t{int(round(s.fps * s.duration_s))}\t{s.fps}')")
    local entry stale=0
    for entry in "${expected_profiles[@]}"; do
        local mp4 refdir expected_frames expected_fps
        IFS=$'\t' read -r mp4 refdir expected_frames expected_fps <<< "${entry}"
        local abs="${ROOT_DIR}/${mp4}"
        local ref_abs="${ROOT_DIR}/${refdir}"
        local frame_count=0
        local mp4_fps="" mp4_frames=""
        if [[ -d "${ref_abs}" ]]; then
            frame_count="$(find "${ref_abs}" -maxdepth 1 -type f -name 'frame_*.png' | wc -l)"
        fi
        if [[ -f "${abs}" ]]; then
            mapfile -t stream_meta < <(ffprobe -v error -select_streams v:0 \
                -show_entries stream=r_frame_rate,nb_frames \
                -of default=nw=1:nk=1 "${abs}" 2>/dev/null || true)
            mp4_fps="${stream_meta[0]:-}"
            mp4_frames="${stream_meta[1]:-}"
        fi
        if [[ ! -f "${abs}" || "${abs}" -ot "${script}" \
                || "${frame_count}" != "${expected_frames}" \
                || "${mp4_fps}" != "${expected_fps}/1" \
                || "${mp4_frames}" != "${expected_frames}" ]]; then
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
