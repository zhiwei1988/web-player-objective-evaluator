#!/usr/bin/env bash
# Start (or restart) the source-built MediaMTX bound to :554 (privileged port,
# requires CAP_NET_BIND_SERVICE — set on the binary by scripts/build.sh) with the project's
# config. Writes the PID to rtsp_server/mediamtx.pid for later cleanup. Idempotent:
# if a previous instance is still alive and healthy, leaves it running.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

log()  { printf '[rtsp] %s\n' "$*" >&2; }
die()  { printf 'rtsp failed: %s\n' "$*" >&2; exit 1; }

CONFIG="${ROOT_DIR}/rtsp_server/mediamtx.yml"
PID_FILE="${ROOT_DIR}/rtsp_server/mediamtx.pid"
LOG_FILE="${ROOT_DIR}/rtsp_server/mediamtx.log"
BINARY="${ROOT_DIR}/third_party/install/bin/mediamtx"

[[ -x "${BINARY}" ]] || die "mediamtx binary missing at ${BINARY} (run scripts/build.sh)"
[[ -f "${CONFIG}" ]] || die "config missing at ${CONFIG}"

# Reuse a running instance if its PID is alive and port :554 is bound.
if [[ -f "${PID_FILE}" ]]; then
    pid="$(cat "${PID_FILE}")"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
        if ss -ltn 'sport = :554' 2>/dev/null | grep -q ':554'; then
            log "mediamtx already running (pid ${pid})"
            exit 0
        fi
    fi
    rm -f "${PID_FILE}"
fi

# Anyone else on :554 is stale; clear them so mediamtx can bind.
if lsof -ti:554 >/dev/null 2>&1; then
    log "clearing stale process on :554"
    lsof -ti:554 | xargs -r kill -9 || true
fi

log "launching mediamtx (config=${CONFIG})"
# Launch from the repo root so MediaMTX-spawned ffmpeg inherits cwd=ROOT_DIR
# and resolves `-i streams/...` from there. (MediaMTX does NOT expand env vars
# or Go templates in YAML, so we can't use ${ROOT_DIR} inside the config.)
cd "${ROOT_DIR}"
nohup "${BINARY}" "${CONFIG}" > "${LOG_FILE}" 2>&1 &
echo $! > "${PID_FILE}"

# Brief settle. MediaMTX binds quickly but on-demand paths only spawn ffmpeg
# at first connection, which is fine — health_check.sh triggers that.
sleep 1
if ! kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
    die "mediamtx died on startup; see ${LOG_FILE}"
fi
log "mediamtx pid $(cat "${PID_FILE}"), logs at ${LOG_FILE}"
