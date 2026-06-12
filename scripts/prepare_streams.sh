#!/usr/bin/env bash
# Generate the two watermarked H.265 reference streams used by the evaluator.
# Per-profile parameters live in lib/profiles.py::PROFILES — this script
# iterates that registry; resolutions/fps/bitrates are never hard-coded here.
#
# Outputs (driven by PROFILES):
#   streams/h265_2560_1440.mp4   (2560x1440, 20fps, libx265 4 Mbps, 30s)
#   streams/h265_3840_2160.mp4   (3840x2160, 20fps, libx265 16 Mbps, 30s)
#   reference/2k/frame_NNNNN.png  — full sequence
#   reference/4k/frame_NNNNN.png  — full sequence
#
# Legacy assets (streams/h264_watermarked.mp4 etc) are deleted at the top of
# this script so the two layouts cannot coexist.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

log()  { printf '[prepare_streams] %s\n' "$*" >&2; }
die()  { printf 'prepare_streams failed: %s\n' "$*" >&2; exit 1; }

command -v ffmpeg >/dev/null   || die "ffmpeg not on PATH (run scripts/build.sh)"
command -v ffprobe >/dev/null  || die "ffprobe not on PATH (run scripts/build.sh)"

cd "${ROOT_DIR}"

# Remove legacy codec-dimensioned assets so two layouts cannot coexist on the
# same disk. Anything matching the new profile names is regenerated below.
log "removing legacy H.264 / H.265-by-codec assets if present"
rm -rf streams/h264_watermarked.mp4 streams/h265_watermarked.mp4 reference/h264 reference/h265

# Read PROFILES keys via python so the registry stays the single source.
PROFILE_KEYS=$("${ROOT_DIR}/.venv/bin/python" -c "from lib.profiles import PROFILES; print(' '.join(sorted(PROFILES.keys())))")

mkdir -p streams

for profile in ${PROFILE_KEYS}; do
    # Pull per-profile fields into shell vars via a single python invocation.
    eval "$("${ROOT_DIR}/.venv/bin/python" <<PY
from lib.profiles import PROFILES
s = PROFILES["${profile}"]
print(f"PW={s.width}; PH={s.height}; PFPS={s.fps}; PBR='{s.bitrate}'; "
      f"PDUR={s.duration_s}; PREF='{s.reference_dir}'; PMP4='{s.stream_file}'")
PY
)"
    PGOP=$(( PFPS * 2 ))
    # bufsize = 2 × bitrate; strip the trailing "M" to compute then re-append.
    PBR_NUM="${PBR%M}"
    PBUFSIZE="$(( PBR_NUM * 2 ))M"

    log "generating profile=${profile} reference PNGs (${PW}x${PH}@${PFPS}, ${PDUR}s)"
    "${ROOT_DIR}/.venv/bin/python" -m lib.watermark \
        --profile "${profile}" --out "${PREF}"

    log "encoding profile=${profile} MP4 (libx265 ${PBR}, GOP=${PGOP})"
    ffmpeg -y -loglevel error \
        -framerate "${PFPS}" \
        -i "${PREF}/frame_%05d.png" \
        -c:v libx265 -tag:v hvc1 -b:v "${PBR}" -maxrate "${PBR}" -bufsize "${PBUFSIZE}" -pix_fmt yuv420p \
        -x265-params "keyint=${PGOP}:min-keyint=${PGOP}:scenecut=0" \
        -movflags +faststart \
        "${PMP4}"

    # Sanity: ffprobe confirms resolution.
    fields="$(ffprobe -v error -select_streams v:0 \
        -show_entries stream=width,height,r_frame_rate \
        -of default=nw=1:nk=1 "${PMP4}")"
    log "  ${PMP4}:"
    while IFS= read -r line; do log "    ${line}"; done <<< "${fields}"
    w="$(printf '%s\n' "${fields}" | sed -n '1p')"
    h="$(printf '%s\n' "${fields}" | sed -n '2p')"
    [[ "${w}" == "${PW}" && "${h}" == "${PH}" ]] \
        || die "profile=${profile}: expected ${PW}x${PH}, got ${w}x${h} in ${PMP4}"

    # Spot-check DataMatrix on first / middle / last frame. Cropping to the
    # bottom-right ~18% per side speeds up decode and matches where
    # lib/watermark.py paints the code; full-frame scan on a 3840x2160 image
    # would be far too slow and pylibdmtx's C core doesn't honour SIGINT.
    total=$(( PFPS * PDUR ))
    for sample in 0 $((total / 2)) $((total - 1)); do
        printf -v png '%s/frame_%05d.png' "${PREF}" "${sample}"
        got="$("${ROOT_DIR}/.venv/bin/python" -c "
from pylibdmtx.pylibdmtx import decode
from PIL import Image
img = Image.open('${png}').convert('RGB')
w, h = img.size
side = int(min(w, h) * 0.18)
crop = img.crop((w - side, int(h * 0.78), w, h))
res = decode(crop, max_count=1, timeout=2000)
print(res[0].data.decode() if res else 'NONE')")"
        if [[ "${got}" != "${sample}" ]]; then
            die "spot check profile=${profile}: frame ${sample} decoded as '${got}', expected '${sample}'"
        fi
        log "  ${profile} frame ${sample}: DataMatrix decoded OK"
    done
done

printf 'prepare_streams ok\n'
