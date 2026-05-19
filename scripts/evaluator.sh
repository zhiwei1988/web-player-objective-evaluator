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

RUN_DIR=""
LOG_FILE=""
SCORE_FILE=""
REPORT_FILE=""
FAILURE_REASON=""
PROFILES_TO_RUN=()
declare -A PROFILE_REASON=()
declare -A METRICS_PATH=()

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
    local profile r
    for profile in "${PROFILES_TO_RUN[@]}"; do
        r="${PROFILE_REASON[${profile}]:-}"
        [[ -n "${r}" ]] && args+=(--profile-reason "${profile}=${r}")
    done
    "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scorer.py" "${args[@]}" >/dev/null 2>&1 || true
}

stop_mediamtx() {
    # MediaMTX is owned per-run by this script; kill it on every exit path.
    if [[ -f "${ROOT_DIR}/rtsp_server/mediamtx.pid" ]]; then
        local pid
        pid="$(cat "${ROOT_DIR}/rtsp_server/mediamtx.pid" 2>/dev/null || true)"
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            log "stopping mediamtx pid ${pid}"
            kill -TERM "${pid}" 2>/dev/null || true
            sleep 0.5
            kill -KILL "${pid}" 2>/dev/null || true
        fi
        rm -f "${ROOT_DIR}/rtsp_server/mediamtx.pid"
    fi
}

cleanup() {
    local rc=$?
    trap - EXIT INT TERM
    clx_cleanup_contestant
    stop_mediamtx
    if [[ -n "${RUN_DIR:-}" && -d "${RUN_DIR}" && ! -f "${SCORE_FILE}" ]]; then
        write_failure_score "${FAILURE_REASON:-${HOST_FAILURE_REASON:-contestant_frontend_unavailable}}"
    fi
    if [[ -n "${SCORE_FILE:-}" && -f "${SCORE_FILE}" ]]; then
        cat "${SCORE_FILE}" >&3 || true
    fi
    exit "${rc}"
}

clx_acquire_lock
clx_prepare_run_dir "${TEAM_ID}"

LOG_FILE="${RUN_DIR}/evaluator.log"
SCORE_FILE="${RUN_DIR}/score.json"
REPORT_FILE="${RUN_DIR}/report.html"

# Tee logs to the run log, but preserve stdout for the final score JSON.
exec > >(tee -a "${LOG_FILE}" >&2) 2>&1
trap cleanup EXIT INT TERM

log "starting run team_id=${TEAM_ID} submission=${SUBMISSION_ZIP} run_dir=${RUN_DIR}"

# Pre-flight: source-built binaries must be present.
for bin in ffmpeg mediamtx tesseract; do
    if [[ ! -x "${ROOT_DIR}/third_party/install/bin/${bin}" ]]; then
        die "${bin} missing under third_party/install/bin; run scripts/build.sh"
        exit 1
    fi
done

clx_precheck_ports 8080
clx_extract_submission "${SUBMISSION_ZIP}"
clx_start_contestant

if ! clx_wait_frontend_ready; then
    FAILURE_REASON="contestant_frontend_unavailable"
    log "contestant frontend never became ready"
    write_failure_score "${FAILURE_REASON}"
    exit 2
fi

# Read profile names from the single truth source.
mapfile -t PROFILES_TO_RUN < <("${ROOT_DIR}/.venv/bin/python" -c \
    "from lib.profiles import PROFILES; print('\n'.join(sorted(PROFILES.keys())))")
(( ${#PROFILES_TO_RUN[@]} > 0 )) || { die "no profiles defined in lib.profiles.PROFILES"; exit 1; }

log "chromium=$(tr '\n' ' ' < "${ROOT_DIR}/third_party/install/playwright_chromium.version" 2>/dev/null)"
log "profiles=${PROFILES_TO_RUN[*]}"

# Bring MediaMTX up for this run.
log "starting RTSP server"
if ! "${SCRIPT_DIR}/start_rtsp.sh"; then
    FAILURE_REASON="rtsp infrastructure failure"
    log "${FAILURE_REASON}"
    exit 1
fi
for profile in "${PROFILES_TO_RUN[@]}"; do
    if ! "${SCRIPT_DIR}/health_check.sh" "${profile}" 15; then
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
    cpu_sampled="$("${ROOT_DIR}/.venv/bin/python" -c \
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
    profile_fps="$("${ROOT_DIR}/.venv/bin/python" -c \
        "from lib.profiles import PROFILES; print(PROFILES['${profile}'].fps)")"

    if "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/runner.py" \
            --profile "${profile}" --output "${out}" \
            --duration 30 --fps "${profile_fps}" \
            "${extra_args[@]}"; then
        return 0
    fi
    local reason
    reason="$(python3 -c "import json; print(json.load(open('${out}/timestamps.json')).get('reason') or '')" 2>/dev/null || true)"
    log "  ${profile} runner failed: ${reason}"
    PROFILE_REASON[${profile}]="${reason}"
    return 1
}

analyze_profile() {
    local profile="$1" shots="$2" metrics="$3"
    local refdir
    refdir="$("${ROOT_DIR}/.venv/bin/python" -c "from lib.profiles import PROFILES; print(PROFILES['${profile}'].reference_dir)")"
    if ! compgen -G "${shots}/shot_*.png" >/dev/null && ! compgen -G "${shots}/shot_*.jpg" >/dev/null; then
        log "no ${profile} screenshots; skipping analyzer"
        return 1
    fi
    "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/analyzer.py" \
        --profile "${profile}" \
        --screenshots "${shots}" \
        --reference "${ROOT_DIR}/${refdir}" \
        --output "${metrics}"
}

for profile in "${PROFILES_TO_RUN[@]}"; do
    shots_dir="${RUN_DIR}/${profile}_screenshots"
    metrics_path="${RUN_DIR}/${profile}_metrics.json"
    METRICS_PATH[${profile}]="${metrics_path}"
    run_capture "${profile}" "${shots_dir}" || true
    analyze_profile "${profile}" "${shots_dir}" "${metrics_path}" || true
done

# Score + report.
SCORER_ARGS=(--output "${SCORE_FILE}" --report "${REPORT_FILE}"
             --install-prefix "${ROOT_DIR}/third_party/install")
for profile in "${PROFILES_TO_RUN[@]}"; do
    metrics_path="${METRICS_PATH[${profile}]}"
    [[ -f "${metrics_path}" ]] && SCORER_ARGS+=(--metrics "${profile}=${metrics_path}")
    reason="${PROFILE_REASON[${profile}]:-}"
    [[ -n "${reason}" ]] && SCORER_ARGS+=(--profile-reason "${profile}=${reason}")
done

"${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scorer.py" "${SCORER_ARGS[@]}" >/dev/null

log "score written to ${SCORE_FILE}"
log "report written to ${REPORT_FILE}"

# Trap emits score.json to original stdout on exit.
