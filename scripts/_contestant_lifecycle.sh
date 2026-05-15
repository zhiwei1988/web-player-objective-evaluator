#!/usr/bin/env bash
# shellcheck shell=bash
# Functions sourced by scripts/evaluator-host.sh and scripts/evaluator-local.sh
# to manage contestant lifecycle on the host (lock, port precheck, unzip,
# start.sh / stop.sh, cleanup). Source this file with ROOT_DIR already set.

LOCK_FILE="/var/tmp/evaluator-host.lock"
# Reason for an in-script failure that occurred AFTER clx_prepare_run_dir
# (so RUN_DIR exists) but BEFORE evaluator.sh / docker run took over.
# The wrapper's cleanup trap reads this to write a failure score.json.
HOST_FAILURE_REASON=""

clx_log() { printf '[host] %s\n' "$*" >&2; }
clx_die() { printf 'evaluator-host: %s\n' "$*" >&2; exit "${2:-1}"; }
clx_die_with_reason() {
    HOST_FAILURE_REASON="$1"
    printf 'evaluator-host: %s\n' "$1" >&2
    exit "${2:-1}"
}

clx_acquire_lock() {
    exec 9>"${LOCK_FILE}"
    if ! flock -n 9; then
        local holder
        holder="$(lsof -t "${LOCK_FILE}" 2>/dev/null | head -1 || echo unknown)"
        clx_die "another evaluator run is in progress (pid=${holder})" 75
    fi
}

clx_precheck_ports() {
    # Args: ports to require free. Caller responsibility.
    # evaluator-local.sh checks only 8080 (MediaMTX is owned by evaluator.sh
    # via the idempotent start_rtsp.sh and may already be up from a prior
    # deploy.sh). evaluator-host.sh checks 8080 and 8554 (the container will
    # bind 8554 via --network host).
    local ports=("$@")
    (( ${#ports[@]} > 0 )) || { printf 'clx_precheck_ports: at least one port required\n' >&2; exit 1; }
    local query="" p
    for p in "${ports[@]}"; do
        [[ -n "${query}" ]] && query+=" or "
        query+="sport = :${p}"
    done
    local busy
    busy="$(ss -lntH "${query}" 2>/dev/null || true)"
    if [[ -n "${busy}" ]]; then
        printf 'evaluator-host: port(s) %s occupied:\n%s\n' "${ports[*]}" "${busy}" >&2
        exit 1
    fi
}

clx_prepare_run_dir() {
    local team_id="$1"
    TS="$(date +%Y%m%d_%H%M%S)"
    RESULTS_SUBDIR="${team_id}_${TS}"
    RUN_DIR="${ROOT_DIR}/results/${RESULTS_SUBDIR}"
    [[ ! -d "${RUN_DIR}" ]] || clx_die "results dir already exists (clock skew?): ${RUN_DIR}"
    mkdir -p "${RUN_DIR}"
    STAGE_DIR="${ROOT_DIR}/submissions/${team_id}"
}

clx_extract_submission() {
    local zip_path="$1"
    [[ -f "${zip_path}" ]] || clx_die_with_reason "submission zip not found: ${zip_path}"
    rm -rf "${STAGE_DIR}"; mkdir -p "${STAGE_DIR}"
    unzip -qq "${zip_path}" -d "${STAGE_DIR}" || clx_die_with_reason "unzip failed"
    # Lift single-top-dir layout if present.
    if [[ ! -f "${STAGE_DIR}/start.sh" ]]; then
        local inner
        inner="$(find "${STAGE_DIR}" -mindepth 1 -maxdepth 1 -type d | head -1 || true)"
        if [[ -n "${inner}" && -f "${inner}/start.sh" ]]; then
            shopt -s dotglob; mv "${inner}"/* "${STAGE_DIR}/"; shopt -u dotglob
            rmdir "${inner}" 2>/dev/null || true
        fi
    fi
    [[ -f "${STAGE_DIR}/start.sh" ]] || clx_die_with_reason "missing start.sh"
    chmod +x "${STAGE_DIR}/start.sh"
    [[ -f "${STAGE_DIR}/stop.sh" ]] && chmod +x "${STAGE_DIR}/stop.sh"
    return 0
}

clx_start_contestant() {
    export RTSP_SERVER_HOST=127.0.0.1
    export RTSP_SERVER_PORT=8554
    export FRONTEND_PORT=8080
    (cd "${STAGE_DIR}" && setsid ./start.sh) > "${RUN_DIR}/contestant.log" 2>&1 &
    CONTESTANT_PID=$!
    echo "${CONTESTANT_PID}" > "${RUN_DIR}/contestant.pid"
    clx_log "contestant pid=${CONTESTANT_PID}"
}

clx_wait_frontend_ready() {
    local i
    for i in $(seq 1 60); do
        if curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:8080/play?codec=h264&autoplay=1"; then
            clx_log "frontend reachable after ${i}s"
            return 0
        fi
        sleep 1
    done
    return 1
}

clx_cleanup_contestant() {
    if [[ -n "${CONTESTANT_PID:-}" ]] && kill -0 "${CONTESTANT_PID}" 2>/dev/null; then
        if [[ -x "${STAGE_DIR}/stop.sh" ]]; then
            (cd "${STAGE_DIR}" && timeout 10 ./stop.sh) > /dev/null 2>&1 || true
        fi
        kill -- "-${CONTESTANT_PID}" 2>/dev/null || true
        sleep 1
        kill -9 -- "-${CONTESTANT_PID}" 2>/dev/null || true
    fi
    fuser -k 8080/tcp 8554/tcp 2>/dev/null || true
}

clx_emit_score_to_fd3() {
    [[ -f "${RUN_DIR}/score.json" ]] && cat "${RUN_DIR}/score.json" >&3 || true
}
