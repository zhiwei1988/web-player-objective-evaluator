#!/usr/bin/env bash
# shellcheck shell=bash
# Functions sourced by scripts/evaluator.sh to manage contestant lifecycle on
# the host (lock, port precheck, unzip, start.sh / stop.sh, cleanup). Source
# this file with ROOT_DIR already set.

LOCK_FILE="/var/tmp/evaluator.lock"
# Reason for an in-script failure that occurred AFTER clx_prepare_run_dir
# (so RUN_DIR exists) but BEFORE the scoring pipeline produced score.json.
# The evaluator cleanup trap reads this to write a failure score.json.
HOST_FAILURE_REASON=""
HOST_CONTESTANT_FEEDBACK=()

clx_log() { printf '[contestant] %s\n' "$*" >&2; }
clx_die() {
    local message="$1"
    local rc="${2:-1}"
    printf 'evaluator: %s\n' "${message}" >&2
    exit "${rc}"
}
clx_die_with_reason() {
    HOST_FAILURE_REASON="$1"
    printf 'evaluator: %s\n' "$1" >&2
    exit "${2:-2}"
}

clx_record_contestant_feedback() {
    local line="${1:-}"
    [[ -n "${line}" ]] || return 0
    HOST_CONTESTANT_FEEDBACK+=("${line}")
}

clx_collect_contestant_log_feedback() {
    local summary="${1:-}" log_file="${2:-}" max_lines="${3:-20}"
    clx_record_contestant_feedback "${summary}"
    [[ -n "${log_file}" && -f "${log_file}" ]] || return 0
    local line
    while IFS= read -r line; do
        [[ -n "${line}" ]] || continue
        clx_record_contestant_feedback "${line}"
    done < <(tail -n "${max_lines}" "${log_file}" 2>/dev/null || true)
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
        printf 'evaluator: port(s) %s occupied:\n%s\n' "${ports[*]}" "${busy}" >&2
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
    STAGE_DIR=""
}

clx_extract_submission() {
    local zip_path="$1"
    [[ -f "${zip_path}" ]] || clx_die "submission zip not found: ${zip_path}" 1
    STAGE_DIR="$(cd "$(dirname "${zip_path}")" && pwd)"
    unzip -qq "${zip_path}" -d "${STAGE_DIR}" || clx_die "unzip failed" 1
    # Lift single-top-dir layout if present.
    if [[ ! -f "${STAGE_DIR}/start.sh" ]]; then
        local inner
        inner="$(find "${STAGE_DIR}" -mindepth 1 -maxdepth 1 -type d | head -1 || true)"
        if [[ -n "${inner}" && -f "${inner}/start.sh" ]]; then
            shopt -s dotglob; mv "${inner}"/* "${STAGE_DIR}/"; shopt -u dotglob
            rmdir "${inner}" 2>/dev/null || true
        fi
    fi
    if [[ ! -f "${STAGE_DIR}/start.sh" ]]; then
        clx_record_contestant_feedback "Submission is missing required start.sh."
        clx_die_with_reason "contestant_frontend_unavailable" 2
    fi
    chmod -R a+x "${STAGE_DIR}"
    return 0
}

clx_start_contestant() {
    export RTSP_SERVER_HOST=127.0.0.1
    export RTSP_SERVER_PORT=554
    export FRONTEND_PORT=8080
    (cd "${STAGE_DIR}" && setsid ./start.sh) > "${RUN_DIR}/contestant.log" 2>&1 &
    CONTESTANT_PID=$!
    echo "${CONTESTANT_PID}" > "${RUN_DIR}/contestant.pid"
    clx_log "contestant pid=${CONTESTANT_PID}"
}

clx_wait_frontend_ready() {
    local i
    for i in $(seq 1 60); do
        if curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:8080/play?profile=2k&autoplay=1"; then
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
    fuser -k 8080/tcp 2>/dev/null || true
}

clx_emit_score_to_fd3() {
    [[ -f "${RUN_DIR}/score.json" ]] && cat "${RUN_DIR}/score.json" >&3 || true
}
