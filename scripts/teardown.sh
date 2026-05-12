#!/usr/bin/env bash
# Tear down what deploy.sh brought up: stop MediaMTX, free ports 8554 (RTSP)
# and 8080 (contestant frontend, contract).
# Idempotent — safe to re-run, no-op if nothing is running.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

log()  { printf '[teardown] %s\n' "$*" >&2; }

PID_FILE="${ROOT_DIR}/rtsp_server/mediamtx.pid"

stop_mediamtx() {
    if [[ -f "${PID_FILE}" ]]; then
        local pid
        pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            log "stopping mediamtx pid ${pid}"
            kill "${pid}" 2>/dev/null || true
            # Give it 3s to exit cleanly, then SIGKILL.
            local i
            for i in 1 2 3; do
                kill -0 "${pid}" 2>/dev/null || break
                sleep 1
            done
            kill -9 "${pid}" 2>/dev/null || true
        fi
        rm -f "${PID_FILE}"
    fi
    # Backstop: kill anything bound to :8554 (e.g. an orphaned mediamtx with
    # no PID file, or an ffmpeg subprocess publishing on-demand).
    if lsof -ti:8554 >/dev/null 2>&1; then
        log "clearing residual processes on :8554"
        lsof -ti:8554 | xargs -r kill -9 || true
    fi
}

clean_frontend_port() {
    local pids
    pids="$(lsof -ti:8080 2>/dev/null || true)"
    if [[ -n "${pids}" ]]; then
        log "killing pid(s) on :8080: ${pids}"
        kill -9 ${pids} 2>/dev/null || true
    fi
}

stop_mediamtx
clean_frontend_port
printf 'teardown ok\n'
