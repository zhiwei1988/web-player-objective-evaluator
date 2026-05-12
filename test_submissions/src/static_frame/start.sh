#!/usr/bin/env bash
# Static-frame cheat: render a single watermarked reference PNG forever, never
# decode the live stream. Expected outcome: 0 FPS points per codec because
# unique_frame_count collapses to 1; correctness may pass for the frozen frame.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FE_PORT="${FRONTEND_PORT:-8080}"

# Grab one watermarked reference frame for each codec from the evaluator's
# reference directory. The submission is staged at submissions/<id>/, so the
# repo root (which holds reference/) is two directories up.
REPO_ROOT="$(cd "${ROOT}/../.." && pwd)"
mkdir -p "${ROOT}/web"
cp "${REPO_ROOT}/reference/h264/frame_00100.png" "${ROOT}/web/h264.png"
cp "${REPO_ROOT}/reference/h265/frame_00100.png" "${ROOT}/web/h265.png"

cd "${ROOT}"
exec python3 server.py --port "${FE_PORT}" --root "${ROOT}/web"
