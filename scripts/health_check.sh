#!/usr/bin/env bash
# Verify the RTSP server is publishing a readable stream for the requested codec.
# Usage: health_check.sh <h264|h265> [timeout_seconds]
# Exits 0 on healthy, 1 on any failure.

set -euo pipefail

CODEC="${1:-h264}"
TIMEOUT="${2:-10}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

case "${CODEC}" in
    h264|h265) ;;
    *) printf 'health_check: unknown codec %s\n' "${CODEC}" >&2; exit 1 ;;
esac

URL="rtsp://127.0.0.1:8554/test/${CODEC}"

# `ffprobe` will trigger MediaMTX's runOnDemand if the path isn't already publishing.
# `-stimeout` is in microseconds.
if ffprobe -v error \
       -rtsp_transport tcp \
       -timeout "$(( TIMEOUT * 1000000 ))" \
       -i "${URL}" \
       -show_entries stream=codec_name,width,height \
       -of default=nw=1 >/dev/null 2>&1; then
    exit 0
fi

printf 'health_check: %s not readable within %ss\n' "${URL}" "${TIMEOUT}" >&2
exit 1
