#!/usr/bin/env bash
# Single operator-facing evaluator entry. It owns the host-side contestant
# lifecycle, MediaMTX lifecycle, capture, analysis, scoring, cleanup, and final
# score.json emission for one submission zip.
#
# Per-profile parameters (resolution, fps, reference dir, CPU sampling) come
# from lib/profiles.py::PROFILES; this script iterates that registry rather
# than hard-coding profile names.

set -uo pipefail
# NB: not `set -e` — explicit failure handling keeps cleanup and score emission
# predictable on every path that can produce a run directory.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
    cat >&2 <<'EOF'
Usage: scripts/evaluator.sh <team_id> <submission_zip>

Evaluates one contestant submission zip on this host. Writes artifacts under
results/<team_id>_<timestamp>/ and prints the final score.json to stdout.
EOF
    exit "${1:-64}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage 0
fi
(( $# == 2 )) || usage 64

TEAM_ID="$1"
SUBMISSION_ZIP="$(readlink -f "$2" 2>/dev/null || echo "$2")"

# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"
# shellcheck source=_contestant_lifecycle.sh
source "${SCRIPT_DIR}/_contestant_lifecycle.sh"
# shellcheck source=_result_info_lifecycle.sh
source "${SCRIPT_DIR}/_result_info_lifecycle.sh"
# shellcheck source=_stage_timing.sh
source "${SCRIPT_DIR}/_stage_timing.sh"

RUN_DIR=""
LOG_FILE=""
SCORE_FILE=""
REPORT_FILE=""
RESULT_INFO_FILE=""
FAILURE_REASON=""
PROFILES_TO_RUN=()
CONTESTANT_FEEDBACK=()
declare -A PROFILE_REASON=()
declare -A METRICS_PATH=()

# Wall-clock start in ns; result.info's |runtime| is derived from this in
# scripts/_result_info_lifecycle.sh::write_result_info.
RUN_START_NS="$(date +%s%N)"

# fd-3 holds the original stdout for the final score JSON once logging is
# redirected to the run log.
exec 3>&1

log()  { printf '[evaluator] %s\n' "$*"; }
die()  { printf 'evaluator failed: %s\n' "$*"; }

write_failure_score() {
    [[ -n "${SCORE_FILE}" ]] || return 0
    local reason="$1"
    [[ -n "${reason}" ]] || reason="contestant_frontend_unavailable"
    log "writing failure score (${reason})"
    local args=(--output "${SCORE_FILE}"
                --report "${REPORT_FILE}"
                --install-prefix "${ROOT_DIR}/third_party/install"
                --failure-reason "${reason}")
    if [[ -n "${EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE:-}" ]]; then
        args+=(--contestant-memory-limit "${EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE}")
    fi
    local feedback
    for feedback in "${HOST_CONTESTANT_FEEDBACK[@]:-}" "${CONTESTANT_FEEDBACK[@]:-}"; do
        [[ -n "${feedback}" ]] && args+=(--contestant-feedback "${feedback}")
    done
    local profile r
    for profile in "${PROFILES_TO_RUN[@]}"; do
        r="${PROFILE_REASON[${profile}]:-}"
        [[ -n "${r}" ]] && args+=(--profile-reason "${profile}=${r}")
    done
    local score_timeout="${EVALUATOR_SCORE_TIMEOUT_SECONDS:-60}"
    if command -v timeout >/dev/null 2>&1; then
        timeout --foreground --kill-after=10s "${score_timeout}s" \
            bash -c 'exec 9>&- || true; exec "$@"' _ \
            "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scorer.py" "${args[@]}" >/dev/null 2>&1 || true
    else
        clx_without_lock_fd "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scorer.py" "${args[@]}" >/dev/null 2>&1 || true
    fi
}

handle_contestant_memory_limit_failure() {
    FAILURE_REASON="${CONTESTANT_MEMORY_LIMIT_REASON}"
    log "contestant exceeded memory limit (${EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE:-10G})"
    clx_record_contestant_memory_feedback
    write_failure_score "${FAILURE_REASON}"
    exit 2
}

stop_mediamtx() {
    # MediaMTX is owned per-run by this script; kill it on every exit path.
    if [[ -f "${ROOT_DIR}/rtsp_server/mediamtx.pid" ]]; then
        local pid
        pid="$(cat "${ROOT_DIR}/rtsp_server/mediamtx.pid" 2>/dev/null || true)"
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            log "stopping mediamtx pid ${pid}"
            kill -TERM "${pid}" 2>/dev/null || true
            clx_without_lock_fd sleep 0.5
            kill -KILL "${pid}" 2>/dev/null || true
        fi
        rm -f "${ROOT_DIR}/rtsp_server/mediamtx.pid"
    fi
}

cleanup() {
    local rc=$?
    trap - EXIT INT TERM
    stg_stop_total_watchdog
    clx_cleanup_contestant
    clx_teardown_contestant_bandwidth_limit
    stop_mediamtx
    if [[ -n "${RUN_DIR:-}" && -d "${RUN_DIR}" && ! -f "${SCORE_FILE}" ]]; then
        write_failure_score "${FAILURE_REASON:-${HOST_FAILURE_REASON:-contestant_frontend_unavailable}}"
    fi
    if [[ -n "${SCORE_FILE:-}" && -f "${SCORE_FILE}" && ! -f "${RESULT_INFO_FILE}" ]]; then
        # Render+publish result.info from whatever score landed (normal or failure).
        if ! write_result_info; then
            log "evaluator failure: result.info render/publish failed during cleanup"
            (( rc == 0 )) && rc=1
        fi
    fi
    if [[ -n "${SCORE_FILE:-}" && -f "${SCORE_FILE}" ]]; then
        clx_without_lock_fd cat "${SCORE_FILE}" >&3 || true
    fi
    exit "${rc}"
}

clx_acquire_lock
clx_prepare_run_dir "${TEAM_ID}"
if ! clx_load_contestant_memory_limit; then
    FAILURE_REASON="contestant memory limiter configuration invalid"
    exit 1
fi
if ! clx_load_contestant_bandwidth_limit; then
    FAILURE_REASON="contestant bandwidth limiter configuration invalid"
    exit 1
fi
stg_load_timeout_budgets
stg_init_stage_timings

LOG_FILE="${RUN_DIR}/evaluator.log"
SCORE_FILE="${RUN_DIR}/score.json"
REPORT_FILE="${RUN_DIR}/report.html"
RESULT_INFO_FILE="${RUN_DIR}/result.info"

# Tee logs to the run log, but preserve stdout for the final score JSON.
exec > >(clx_close_lock_fd; tee -a "${LOG_FILE}" >&2) 2>&1
trap cleanup EXIT INT TERM
stg_start_total_watchdog

log "starting run team_id=${TEAM_ID} submission=${SUBMISSION_ZIP} run_dir=${RUN_DIR}"

# Pre-flight: source-built binaries must be present.
for bin in ffmpeg mediamtx tesseract; do
    if [[ ! -x "${ROOT_DIR}/third_party/install/bin/${bin}" ]]; then
        die "${bin} missing under third_party/install/bin; run scripts/build.sh"
        exit 1
    fi
done

if ! clx_preflight_contestant_memory_limiter; then
    FAILURE_REASON="contestant memory limiter unavailable"
    log "${FAILURE_REASON}"
    exit 1
fi

if ! clx_preflight_contestant_bandwidth_limiter; then
    FAILURE_REASON="contestant bandwidth limiter unavailable"
    log "${FAILURE_REASON}"
    exit 1
fi

clx_precheck_ports 8080
clx_extract_submission "${SUBMISSION_ZIP}"
clx_start_contestant
# The contestant cgroup exists only after the transient unit is started, so the
# egress shaper (which matches that cgroup) is applied here, before the
# readiness wait and any scored capture. MUST NOT silently run unshaped.
if ! clx_setup_contestant_bandwidth_limit; then
    FAILURE_REASON="contestant bandwidth limiter setup failed"
    log "${FAILURE_REASON}"
    exit 1
fi

if ! clx_wait_frontend_ready; then
    if clx_contestant_memory_limit_exceeded; then
        handle_contestant_memory_limit_failure
    fi
    FAILURE_REASON="contestant_frontend_unavailable"
    log "contestant frontend never became ready"
    clx_collect_contestant_log_feedback \
        "Frontend did not become reachable within the evaluator readiness window." \
        "${RUN_DIR}/contestant.log" \
        20
    write_failure_score "${FAILURE_REASON}"
    exit 2
fi

# Read profile names from the single truth source.
mapfile -t PROFILES_TO_RUN < <(clx_without_lock_fd "${ROOT_DIR}/.venv/bin/python" -c \
    "from lib.profiles import PROFILES; print('\n'.join(sorted(PROFILES.keys())))")
(( ${#PROFILES_TO_RUN[@]} > 0 )) || { die "no profiles defined in lib.profiles.PROFILES"; exit 1; }

log "chromium=$(clx_without_lock_fd tr '\n' ' ' < "${ROOT_DIR}/third_party/install/playwright_chromium.version" 2>/dev/null)"
log "profiles=${PROFILES_TO_RUN[*]}"

# Bring MediaMTX up for this run.
log "starting RTSP server"
if ! clx_without_lock_fd "${SCRIPT_DIR}/start_rtsp.sh"; then
    FAILURE_REASON="rtsp infrastructure failure"
    log "${FAILURE_REASON}"
    exit 1
fi
for profile in "${PROFILES_TO_RUN[@]}"; do
    if ! clx_without_lock_fd "${SCRIPT_DIR}/health_check.sh" "${profile}" 15; then
        FAILURE_REASON="rtsp infrastructure failure (${profile} unreadable)"
        log "${FAILURE_REASON}"
        exit 1
    fi
done

run_capture() {
    local profile="$1" out="$2"
    log "running runner.py --profile ${profile}"

    # CPU sampling is opt-in per profile via PROFILES[profile].cpu_sampled.
    local extra_args=()
    local cpu_sampled
    cpu_sampled="$(clx_without_lock_fd "${ROOT_DIR}/.venv/bin/python" -c \
        "from lib.profiles import PROFILES; print('1' if PROFILES['${profile}'].cpu_sampled else '0')")"
    if [[ "${cpu_sampled}" == "1" && -f "${RUN_DIR}/contestant.pid" ]]; then
        local pgid
        pgid="$(cat "${RUN_DIR}/contestant.pid" 2>/dev/null || true)"
        if [[ -n "${pgid}" ]]; then
            extra_args+=(--contestant-pgid "${pgid}")
        fi
    fi

    # Per-profile fps from the registry keeps capture cadence tied to source fps.
    local profile_fps
    profile_fps="$(clx_without_lock_fd "${ROOT_DIR}/.venv/bin/python" -c \
        "from lib.profiles import PROFILES; print(PROFILES['${profile}'].fps)")"

    local rc
    stg_run_stage capture "${profile}" "${EVALUATOR_CAPTURE_TIMEOUT_SECONDS}" \
        "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/runner.py" \
            --profile "${profile}" --output "${out}" \
            --duration 30 --fps "${profile_fps}" \
            "${extra_args[@]}"
    rc=$?
    if [[ "${rc}" == "0" ]]; then
        return 0
    fi
    if clx_contestant_memory_limit_exceeded; then
        local memory_reason="${CONTESTANT_MEMORY_LIMIT_REASON}"
        log "  ${profile} runner stopped after contestant exceeded memory limit"
        PROFILE_REASON[${profile}]="${memory_reason}"
        CONTESTANT_FEEDBACK+=("${profile}: $(clx_contestant_memory_feedback_line)")
        FAILURE_REASON="${memory_reason}"
        return 2
    fi
    if [[ "${rc}" == "124" || "${rc}" == "137" ]]; then
        local timeout_reason="capture timeout after ${EVALUATOR_CAPTURE_TIMEOUT_SECONDS}s"
        log "  ${profile} runner timed out: ${timeout_reason}"
        PROFILE_REASON[${profile}]="${timeout_reason}"
        CONTESTANT_FEEDBACK+=("${profile}: ${timeout_reason}")
        return 1
    fi
    local reason
    reason="$(clx_without_lock_fd python3 -c "import json; print(json.load(open('${out}/timestamps.json')).get('reason') or '')" 2>/dev/null || true)"
    log "  ${profile} runner failed: ${reason}"
    PROFILE_REASON[${profile}]="${reason}"
    local feedback
    while IFS= read -r feedback; do
        [[ -n "${feedback}" ]] && CONTESTANT_FEEDBACK+=("${profile}: ${feedback}")
    done < <(clx_without_lock_fd python3 -c "import json; data=json.load(open('${out}/timestamps.json')); print('\n'.join(data.get('contestant_feedback') or []))" 2>/dev/null || true)
    return 1
}

analyze_profile() {
    local profile="$1" shots="$2" metrics="$3"
    local refdir
    refdir="$(clx_without_lock_fd "${ROOT_DIR}/.venv/bin/python" -c "from lib.profiles import PROFILES; print(PROFILES['${profile}'].reference_dir)")"
    if ! compgen -G "${shots}/shot_*.png" >/dev/null && ! compgen -G "${shots}/shot_*.jpg" >/dev/null; then
        log "no ${profile} screenshots; skipping analyzer"
        stg_record_stage skipped analysis "${profile}" "${EVALUATOR_ANALYSIS_TIMEOUT_SECONDS}" "" "no screenshots"
        return 1
    fi
    local rc
    stg_run_stage analysis "${profile}" "${EVALUATOR_ANALYSIS_TIMEOUT_SECONDS}" \
        "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/analyzer.py" \
        --profile "${profile}" \
        --screenshots "${shots}" \
        --reference "${ROOT_DIR}/${refdir}" \
        --output "${metrics}"
    rc=$?
    if [[ "${rc}" == "124" || "${rc}" == "137" ]]; then
        local timeout_reason="analysis timeout after ${EVALUATOR_ANALYSIS_TIMEOUT_SECONDS}s"
        log "  ${profile} analyzer timed out: ${timeout_reason}"
        PROFILE_REASON[${profile}]="${timeout_reason}"
        return 1
    fi
    return "${rc}"
}

for profile in "${PROFILES_TO_RUN[@]}"; do
    shots_dir="${RUN_DIR}/${profile}_screenshots"
    metrics_path="${RUN_DIR}/${profile}_metrics.json"
    METRICS_PATH[${profile}]="${metrics_path}"
    run_capture "${profile}" "${shots_dir}" || true
    if [[ "${FAILURE_REASON:-}" == "${CONTESTANT_MEMORY_LIMIT_REASON}" ]]; then
        handle_contestant_memory_limit_failure
    fi
    analyze_profile "${profile}" "${shots_dir}" "${metrics_path}" || true
    if clx_contestant_memory_limit_exceeded; then
        handle_contestant_memory_limit_failure
    fi
done

# Score + report.
SCORER_ARGS=(--output "${SCORE_FILE}" --report "${REPORT_FILE}"
             --install-prefix "${ROOT_DIR}/third_party/install")
if [[ -n "${EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE:-}" ]]; then
    SCORER_ARGS+=(--contestant-memory-limit "${EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE}")
fi
for profile in "${PROFILES_TO_RUN[@]}"; do
    metrics_path="${METRICS_PATH[${profile}]:-}"
    [[ -n "${metrics_path}" && -f "${metrics_path}" ]] && SCORER_ARGS+=(--metrics "${profile}=${metrics_path}")
    reason="${PROFILE_REASON[${profile}]:-}"
    [[ -n "${reason}" ]] && SCORER_ARGS+=(--profile-reason "${profile}=${reason}")
done
for feedback in "${CONTESTANT_FEEDBACK[@]:-}"; do
    [[ -n "${feedback}" ]] && SCORER_ARGS+=(--contestant-feedback "${feedback}")
done

stg_run_stage scoring "" "${EVALUATOR_SCORE_TIMEOUT_SECONDS}" \
    "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scorer.py" "${SCORER_ARGS[@]}" >/dev/null
rc=$?
if [[ "${rc}" != "0" ]]; then
    if [[ "${rc}" == "124" || "${rc}" == "137" ]]; then
        FAILURE_REASON="scoring timeout after ${EVALUATOR_SCORE_TIMEOUT_SECONDS}s"
    else
        FAILURE_REASON="scoring failed"
    fi
    log "${FAILURE_REASON}"
    exit 1
fi

log "score written to ${SCORE_FILE}"
log "report written to ${REPORT_FILE}"

# Render + publish result.info from the normal-completion score. The cleanup
# trap also covers failure paths via the same helper.
if ! write_result_info 0; then
    log "evaluator failure: result.info render/publish failed after normal scoring"
    exit 1
fi
log "result.info written to ${RESULT_INFO_FILE}"

# Trap emits score.json to original stdout on exit.
