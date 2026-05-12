#!/usr/bin/env bash
# Generate the two watermarked reference streams used by the evaluator.
# Outputs:
#   streams/h264_watermarked.mp4   (1920x1080, 30fps, libx264 crf 18, 30s)
#   streams/h265_watermarked.mp4   (2560x1440, 25fps, libx265 4 Mbps,   30s)
#   reference/h264/frame_NNNNN.png  — full sequence
#   reference/h265/frame_NNNNN.png  — full sequence

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

log()  { printf '[prepare_streams] %s\n' "$*" >&2; }
die()  { printf 'prepare_streams failed: %s\n' "$*" >&2; exit 1; }

command -v ffmpeg >/dev/null   || die "ffmpeg not on PATH (run scripts/build.sh)"
command -v ffprobe >/dev/null  || die "ffprobe not on PATH (run scripts/build.sh)"

H264_DIR="${ROOT_DIR}/reference/h264"
H265_DIR="${ROOT_DIR}/reference/h265"
H264_MP4="${ROOT_DIR}/streams/h264_watermarked.mp4"
H265_MP4="${ROOT_DIR}/streams/h265_watermarked.mp4"

DURATION=30
H264_W=1920; H264_H=1080; H264_FPS=30
H265_W=2560; H265_H=1440; H265_FPS=25

# Run python from the repo root so `python -m lib.watermark` resolves the lib/
# package as a top-level module.
cd "${ROOT_DIR}"

log "generating H.264 reference PNGs (${H264_W}x${H264_H}@${H264_FPS}, ${DURATION}s)"
python -m lib.watermark \
    --codec h264 --width "${H264_W}" --height "${H264_H}" \
    --fps "${H264_FPS}" --duration "${DURATION}" --out "${H264_DIR}"

log "generating H.265 reference PNGs (${H265_W}x${H265_H}@${H265_FPS}, ${DURATION}s)"
python -m lib.watermark \
    --codec h265 --width "${H265_W}" --height "${H265_H}" \
    --fps "${H265_FPS}" --duration "${DURATION}" --out "${H265_DIR}"

mkdir -p "${ROOT_DIR}/streams"

# Force a 2-second GOP so a contestant that connects mid-stream waits at most
# 2s for the next IDR. Without this libx264 / libx265 default to keyint=250
# (~8s @ 30fps / ~10s @ 25fps), which combined with contestants that drop
# packets until they see an IDR can push first-frame latency past the runner's
# 15s readiness timeout. scenecut=0 keeps the GOP rigidly periodic so the
# evaluator's timing analysis stays predictable.
H264_GOP=$(( H264_FPS * 2 ))
H265_GOP=$(( H265_FPS * 2 ))

log "encoding H.264 MP4 (libx264 crf 18 yuv420p, GOP=${H264_GOP})"
ffmpeg -y -loglevel error \
    -framerate "${H264_FPS}" \
    -i "${H264_DIR}/frame_%05d.png" \
    -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p \
    -g "${H264_GOP}" -keyint_min "${H264_GOP}" \
    -x264-params "scenecut=0" \
    -movflags +faststart \
    "${H264_MP4}"

log "encoding H.265 MP4 (libx265 4 Mbps yuv420p, GOP=${H265_GOP})"
ffmpeg -y -loglevel error \
    -framerate "${H265_FPS}" \
    -i "${H265_DIR}/frame_%05d.png" \
    -c:v libx265 -tag:v hvc1 -b:v 4M -maxrate 4M -bufsize 8M -pix_fmt yuv420p \
    -x265-params "keyint=${H265_GOP}:min-keyint=${H265_GOP}:scenecut=0" \
    -movflags +faststart \
    "${H265_MP4}"

# Sanity: ffprobe should report the expected resolution/fps/duration.
verify() {
    local file="$1" exp_w="$2" exp_h="$3" exp_fps="$4"
    local fields
    fields="$(ffprobe -v error -select_streams v:0 \
        -show_entries stream=width,height,r_frame_rate,duration \
        -of default=nw=1:nk=1 "${file}")"
    log "  ${file}:"
    while IFS= read -r line; do log "    ${line}"; done <<< "${fields}"
    local w h
    w="$(printf '%s\n' "${fields}" | sed -n '1p')"
    h="$(printf '%s\n' "${fields}" | sed -n '2p')"
    [[ "${w}" == "${exp_w}" && "${h}" == "${exp_h}" ]] \
        || die "verification: expected ${exp_w}x${exp_h}, got ${w}x${h} in ${file}"
}

log "verifying outputs"
verify "${H264_MP4}" "${H264_W}" "${H264_H}" "${H264_FPS}"
verify "${H265_MP4}" "${H265_W}" "${H265_H}" "${H265_FPS}"

# Spot-check DataMatrix on three reference frames per codec. We crop the
# bottom-right corner where lib/watermark.py paints the code; scanning the full
# 2560x1440 H.265 frame is far too slow and pylibdmtx's C core does not honour
# SIGINT, so a naive full-image scan can hang the script un-killably.
spot_check() {
    local codec="$1" dir="$2" total="$3"
    local sample
    for sample in 0 $((total / 2)) $((total - 1)); do
        local png
        printf -v png '%s/frame_%05d.png' "${dir}" "${sample}"
        local got
        got="$(python -c "
from pylibdmtx.pylibdmtx import decode
from PIL import Image
img = Image.open('${png}').convert('RGB')
w, h = img.size
side = int(min(w, h) * 0.18)
crop = img.crop((w - side, int(h * 0.78), w, h))
res = decode(crop, max_count=1, timeout=2000)
print(res[0].data.decode() if res else 'NONE')")"
        if [[ "${got}" != "${sample}" ]]; then
            die "spot check ${codec}: frame ${sample} decoded as '${got}', expected '${sample}'"
        fi
        log "  ${codec} frame ${sample}: DataMatrix decoded OK"
    done
}

spot_check h264 "${H264_DIR}" "$(( H264_FPS * DURATION ))"
spot_check h265 "${H265_DIR}" "$(( H265_FPS * DURATION ))"

printf 'prepare_streams ok\n'
