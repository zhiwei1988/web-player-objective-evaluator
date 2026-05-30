#!/usr/bin/env bash
# transcode_to_h264 cheat fixture (intent-B violation).
#
# Server-side TRANSCODES the contest H.265 source to H.264 and serves it to a
# native <video>. On the eval host Chrome cannot decode HEVC but decodes H.264
# fine, so this plays smoothly and would otherwise game the FPS dimension.
# Decode-Path Forensics MUST catch it: Check 1 sees a working <video> video
# decoder (kVideoDecoderName) on an HEVC-incapable host -> verdict=violation,
# and the scorer zeros correctness + FPS for both profiles.
#
# We transcode the local watermarked stream files (not a live RTSP pull) and cap
# duration/resolution so startup stays well inside the readiness window; the
# <video> loops. Content fidelity is irrelevant — the gate zeros it regardless.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${EVALUATOR_REPO_ROOT:-$(cd "${ROOT}/../.." && pwd)}"
FE_PORT="${FRONTEND_PORT:-8080}"
FFMPEG="${REPO_ROOT}/third_party/install/bin/ffmpeg"
WEB="${ROOT}/web"
mkdir -p "${WEB}"

log() { printf '[transcode-cheat] %s\n' "$*" >&2; }

[[ -x "${FFMPEG}" ]] || { log "missing ffmpeg at ${FFMPEG}"; exit 1; }

while IFS=$'\t' read -r profile stream; do
    src="${REPO_ROOT}/${stream}"
    out="${WEB}/${profile}.mp4"
    [[ -f "${src}" ]] || { log "missing source ${src}"; exit 1; }
    # HEVC -> H.264, scaled to the capture element size and capped at 10s for a
    # fast startup. faststart so the progressive <video> plays immediately.
    "${FFMPEG}" -y -i "${src}" -t 10 -vf scale=1280:720 \
        -c:v libx264 -preset ultrafast -pix_fmt yuv420p -movflags +faststart \
        "${out}" -loglevel error
    log "transcoded ${src} -> ${out}"
done < <(PYTHONPATH="${REPO_ROOT}" "${REPO_ROOT}/.venv/bin/python" -c "from lib.profiles import PROFILES
for s in PROFILES.values():
    print(f'{s.name}\t{s.stream_file}')")

log "serving H.264 on :${FE_PORT}"
cd "${ROOT}"
exec python3 server.py --port "${FE_PORT}" --root "${WEB}"
