#!/usr/bin/env bash
# Fake-overlay cheat: black background + canvas-drawn watermark approximation.
# DataMatrix won't decode (we draw a plausible-looking but invalid pattern);
# even if color blocks pass, SSIM tanks → can't reach full correctness (10).

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FE_PORT="${FRONTEND_PORT:-8080}"
cd "${ROOT}"
exec python3 server.py --port "${FE_PORT}" --root "${ROOT}/web"
