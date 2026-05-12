#!/usr/bin/env bash
# I-frame-only / repeated-frame cheat: cycle a handful of reference PNGs forever.
# Unique watermark numbers stay small → FPS points 0 or 3.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FE_PORT="${FRONTEND_PORT:-8080}"
REPO_ROOT="$(cd "${ROOT}/../.." && pwd)"

mkdir -p "${ROOT}/web/h264" "${ROOT}/web/h265"
# Five frames per codec — cycled, never live-decoded.
for n in 100 200 300 400 500; do
    printf -v src 'frame_%05d.png' "${n}"
    cp "${REPO_ROOT}/reference/h264/${src}" "${ROOT}/web/h264/${src}"
    cp "${REPO_ROOT}/reference/h265/${src}" "${ROOT}/web/h265/${src}"
done

cd "${ROOT}"
exec python3 server.py --port "${FE_PORT}" --root "${ROOT}/web"
