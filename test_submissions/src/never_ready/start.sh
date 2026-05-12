#!/usr/bin/env bash
# Frontend reachable but window.__PLAYER_READY__ never becomes true.
# Expected: runner.py times out after 15s, evaluator records reason
# "startup timeout" on the round.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FE_PORT="${FRONTEND_PORT:-8080}"
cd "${ROOT}"
exec python3 server.py --port "${FE_PORT}" --root "${ROOT}/web"
