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
CONTESTANT_MEMORY_LIMIT_REASON="contestant_memory_limit_exceeded"
CONTESTANT_SYSTEMD_UNIT=""

clx_log() { printf '[contestant] %s\n' "$*" >&2; }
clx_close_lock_fd() {
    exec 9>&- || true
}
clx_without_lock_fd() {
    (clx_close_lock_fd; "$@")
}
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

clx_load_contestant_memory_limit() {
    local raw="${EVALUATOR_CONTESTANT_MEMORY_MAX:-10G}"
    raw="${raw//[[:space:]]/}"
    local upper="${raw^^}"
    if [[ "${upper}" =~ ^([1-9][0-9]*)([KMGTPE]?)(B?)$ ]]; then
        EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE="${BASH_REMATCH[1]}${BASH_REMATCH[2]}"
        export EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE
        return 0
    fi
    printf 'evaluator: invalid EVALUATOR_CONTESTANT_MEMORY_MAX: %s\n' "${raw}" >&2
    return 1
}

clx_contestant_memory_feedback_line() {
    clx_load_contestant_memory_limit >/dev/null 2>&1 || true
    printf 'Submission exceeded evaluator memory limit of %s.' "${EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE:-10G}"
}

clx_record_contestant_memory_feedback() {
    clx_record_contestant_feedback "$(clx_contestant_memory_feedback_line)"
}

clx_systemd_unit_name() {
    local suffix="${RESULTS_SUBDIR:-run-$$}"
    suffix="${suffix//[^[:alnum:]_.-]/-}"
    printf 'evaluator-contestant-%s.scope' "${suffix}"
}

clx_systemd_memory_properties() {
    clx_load_contestant_memory_limit || return 1
    printf '%s\n' \
        "MemoryAccounting=yes" \
        "MemoryMax=${EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE}" \
        "MemorySwapMax=0" \
        "KillMode=control-group"
}

clx_preflight_contestant_memory_limiter() {
    clx_load_contestant_memory_limit || return 1
    local fs_type
    fs_type="$(stat -fc %T /sys/fs/cgroup 2>/dev/null || true)"
    if [[ "${fs_type}" != "cgroup2fs" ]]; then
        printf 'contestant memory limiter preflight failed: /sys/fs/cgroup is %s, expected cgroup2fs\n' "${fs_type:-unknown}" >&2
        return 1
    fi
    if ! command -v systemd-run >/dev/null 2>&1; then
        printf 'contestant memory limiter preflight failed: systemd-run not found\n' >&2
        return 1
    fi
    local unit="evaluator-contestant-preflight-$$.scope"
    if ! systemd-run --user --scope --quiet --unit="${unit}" \
        -p MemoryAccounting=yes \
        -p "MemoryMax=${EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE}" \
        -p MemorySwapMax=0 \
        -p KillMode=control-group \
        -- /bin/true >/dev/null 2>&1; then
        printf 'contestant memory limiter preflight failed: systemd-run could not apply MemoryMax=%s\n' "${EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE}" >&2
        return 1
    fi
    clx_log "contestant memory limiter ready: limit=${EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE}"
    return 0
}

clx_write_contestant_launcher() {
    local launcher="$1"
    cat > "${launcher}" <<'BASH'
#!/usr/bin/env bash
set -uo pipefail
pid_file="$1"
shift
exec setsid bash -c 'echo "$$" > "$1"; shift; exec "$@"' _ "${pid_file}" "$@"
BASH
    chmod 700 "${launcher}"
}

clx_read_contestant_systemd_unit() {
    if [[ -n "${CONTESTANT_SYSTEMD_UNIT:-}" ]]; then
        printf '%s' "${CONTESTANT_SYSTEMD_UNIT}"
        return 0
    fi
    if [[ -n "${RUN_DIR:-}" && -f "${RUN_DIR}/contestant.systemd_unit" ]]; then
        tr -d '\n' < "${RUN_DIR}/contestant.systemd_unit"
        return 0
    fi
    return 1
}

clx_stop_contestant_systemd_unit() {
    local unit
    unit="$(clx_read_contestant_systemd_unit 2>/dev/null || true)"
    [[ -n "${unit}" ]] || return 0
    command -v systemctl >/dev/null 2>&1 || return 0
    clx_without_lock_fd systemctl --user kill "${unit}" 2>/dev/null || true
    clx_without_lock_fd systemctl --user stop "${unit}" 2>/dev/null || true
}

clx_contestant_memory_limit_exceeded() {
    local unit
    unit="$(clx_read_contestant_systemd_unit 2>/dev/null || true)"
    [[ -n "${unit}" ]] || return 1
    command -v systemctl >/dev/null 2>&1 || return 1
    local state
    state="$(clx_without_lock_fd systemctl --user show "${unit}" \
        -p Result -p OOMKilled -p ExecMainStatus -p ActiveState -p SubState 2>/dev/null || true)"
    [[ -n "${state}" ]] || return 1
    if grep -Eq '(^Result=oom-kill|^OOMKilled=yes|^ExecMainStatus=(9|137)$)' <<<"${state}"; then
        return 0
    fi
    return 1
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
        holder="$(clx_without_lock_fd lsof -t "${LOCK_FILE}" 2>/dev/null | head -1 || echo unknown)"
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
    busy="$(clx_without_lock_fd ss -lntH "${query}" 2>/dev/null || true)"
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
    clx_without_lock_fd unzip -o -qq "${zip_path}" -d "${STAGE_DIR}" || clx_die "unzip failed" 1
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
    clx_load_contestant_memory_limit || return 1
    export RTSP_SERVER_HOST=127.0.0.1
    export RTSP_SERVER_PORT=554
    export FRONTEND_PORT=8080
    CONTESTANT_SYSTEMD_UNIT="$(clx_systemd_unit_name)"
    echo "${CONTESTANT_SYSTEMD_UNIT}" > "${RUN_DIR}/contestant.systemd_unit"
    local launcher="${RUN_DIR}/contestant-launcher.sh"
    clx_write_contestant_launcher "${launcher}"
    (clx_close_lock_fd; cd "${STAGE_DIR}" && exec systemd-run --user --scope --quiet --same-dir \
        --unit="${CONTESTANT_SYSTEMD_UNIT}" \
        -p MemoryAccounting=yes \
        -p "MemoryMax=${EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE}" \
        -p MemorySwapMax=0 \
        -p KillMode=control-group \
        -- "${launcher}" "${RUN_DIR}/contestant.pid" ./start.sh) > "${RUN_DIR}/contestant.log" 2>&1 &
    CONTESTANT_SYSTEMD_RUN_PID=$!
    local i
    for i in $(seq 1 50); do
        if [[ -s "${RUN_DIR}/contestant.pid" ]]; then
            break
        fi
        sleep 0.1
    done
    if [[ -s "${RUN_DIR}/contestant.pid" ]]; then
        CONTESTANT_PID="$(cat "${RUN_DIR}/contestant.pid")"
    else
        CONTESTANT_PID="${CONTESTANT_SYSTEMD_RUN_PID}"
        echo "${CONTESTANT_PID}" > "${RUN_DIR}/contestant.pid"
    fi
    clx_log "contestant pid=${CONTESTANT_PID} unit=${CONTESTANT_SYSTEMD_UNIT} memory_limit=${EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE}"
}

clx_wait_frontend_ready() {
    local i
    for i in $(seq 1 60); do
        if clx_without_lock_fd curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:8080/play?profile=2k&autoplay=1"; then
            clx_log "frontend reachable after ${i}s"
            return 0
        fi
        sleep 1
    done
    return 1
}

clx_cleanup_contestant() {
    clx_stop_contestant_systemd_unit
    if [[ -n "${CONTESTANT_PID:-}" ]] && kill -0 "${CONTESTANT_PID}" 2>/dev/null; then
        if [[ -x "${STAGE_DIR}/stop.sh" ]]; then
            (clx_close_lock_fd; cd "${STAGE_DIR}" && timeout 10 ./stop.sh) > /dev/null 2>&1 || true
        fi
        kill -- "-${CONTESTANT_PID}" 2>/dev/null || true
        sleep 1
        kill -9 -- "-${CONTESTANT_PID}" 2>/dev/null || true
    fi
    clx_without_lock_fd fuser -k 8080/tcp 2>/dev/null || true
}

clx_emit_score_to_fd3() {
    [[ -f "${RUN_DIR}/score.json" ]] && cat "${RUN_DIR}/score.json" >&3 || true
}
