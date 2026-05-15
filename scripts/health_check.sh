#!/usr/bin/env bash
# Verify the RTSP server is publishing readable streams.
# Usage:
#   health_check.sh                   # probe every profile in lib.profiles.PROFILES
#   health_check.sh <profile> [timeout_seconds]
# Exits 0 if all probed profiles are healthy, 1 otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

if (( $# >= 1 )); then
    PROFILES_TO_CHECK=("$1")
    TIMEOUT="${2:-15}"
else
    mapfile -t PROFILES_TO_CHECK < <(.venv/bin/python -c \
        "from lib.profiles import PROFILES; print('\n'.join(sorted(PROFILES.keys())))")
    TIMEOUT=15
fi

failed=0
for profile in "${PROFILES_TO_CHECK[@]}"; do
    url="$(.venv/bin/python -c "from lib.profiles import rtsp_url; print(rtsp_url('${profile}'))")"
    if ffprobe -v error \
            -rtsp_transport tcp \
            -timeout "$(( TIMEOUT * 1000000 ))" \
            -i "${url}" \
            -show_entries stream=codec_name,width,height \
            -of default=nw=1 >/dev/null 2>&1; then
        printf '[health_check] %s OK (%s)\n' "${profile}" "${url}" >&2
    else
        printf '[health_check] %s FAILED (%s, %ss timeout)\n' \
            "${profile}" "${url}" "${TIMEOUT}" >&2
        failed=1
    fi
done
exit "${failed}"
