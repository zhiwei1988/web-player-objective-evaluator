#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FE_PORT="${FRONTEND_PORT:-8080}"
cd "${ROOT}"
exec python3 server.py --port "${FE_PORT}" --root "${ROOT}/web"
