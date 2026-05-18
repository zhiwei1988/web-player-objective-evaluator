#!/usr/bin/env bash
# Evaluator main body — runs MediaMTX + capture + scoring inside a prepared
# host environment. Host-side concerns (zip extract, contestant start.sh /
# stop.sh, port precheck) belong to scripts/evaluator-host.sh (target host)
# or scripts/evaluator-local.sh (build host shortcut). MediaMTX is brought
# up AND torn down by this script per run; it is no longer shared with a
# host-resident daemon.
#
# Per-profile parameters (resolution, fps, reference dir, CPU sampling) come
# from lib/profiles.py::PROFILES; this script iterates that registry rather
# than hard-coding profile names.

set -uo pipefail
# NB: not `set -e` — we want explicit failure handling per step so the score JSON
# always gets written.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
    cat >&2 <<'EOF'
Usage: scripts/evaluator.sh <team_id> <results_subdir>

Invoked by scripts/evaluator-host.sh or scripts/evaluator-local.sh after
the contestant frontend is already serving on http://127.0.0.1:8080.
Reads streams/ and reference/ from the workspace; writes capture artifacts
+ score.json + report.html under results/<results_subdir>/.
EOF
    exit 64
}

(( $# >= 2 )) || usage
TEAM_ID="$1"
RESULTS_SUBDIR="$2"

# Pre-flight: source-built binaries must be present.
for bin in ffmpeg mediamtx tesseract; do
    if [[ ! -x "${ROOT_DIR}/third_party/install/bin/${bin}" ]]; then
        printf 'evaluator: %s missing under third_party/install/bin — run scripts/build.sh\n' "${bin}" >&2
        exit 66
    fi
done

# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

RUN_DIR="${ROOT_DIR}/results/${RESULTS_SUBDIR}"
[[ -d "${RUN_DIR}" ]] || { printf 'evaluator: results dir does not exist: %s\n' "${RUN_DIR}" >&2; exit 65; }

LOG_FILE="${RUN_DIR}/evaluator.log"
SCORE_FILE="${RUN_DIR}/score.json"
REPORT_FILE="${RUN_DIR}/report.html"

# Tee all log lines to the run log, but keep stdout for the final score JSON.
exec 3>&1
exec > >(tee -a "${LOG_FILE}" >&2) 2>&1

log()  { printf '[evaluator] %s\n' "$*"; }
die()  { printf 'evaluator failed: %s\n' "$*"; }

# Read profile names from the single truth source.
mapfile -t PROFILES_TO_RUN < <(.venv/bin/python -c \
    "from lib.profiles import PROFILES; print('\n'.join(sorted(PROFILES.keys())))")
(( ${#PROFILES_TO_RUN[@]} > 0 )) || { die "no profiles defined in lib.profiles.PROFILES"; exit 71; }

# Per-profile state populated by the capture loop.
declare -A PROFILE_REASON=()
declare -A METRICS_PATH

FAILURE_REASON=""

write_failure_score() {
    local reason="$1"
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
    stop_mediamtx
    # If no score file exists, this run died before scoring — write a failure.
    if [[ ! -f "${SCORE_FILE}" ]]; then
        write_failure_score "${FAILURE_REASON:-evaluator aborted}"
    fi
    # Emit final score JSON to the *original* stdout (fd 3), so callers piping
    # `evaluator.sh` get just the JSON line. Wrappers (evaluator-local.sh /
    # evaluator-host.sh) set EVALUATOR_SKIP_STDOUT_EMIT=1 because they own
    # emission themselves; without this gate, both layers would print the JSON
    # and the user would see two copies glued together.
    if [[ "${EVALUATOR_SKIP_STDOUT_EMIT:-0}" != "1" && -f "${SCORE_FILE}" ]]; then
        cat "${SCORE_FILE}" >&3 || true
    fi
    exit "${rc}"
}
trap cleanup EXIT INT TERM

log "starting run team_id=${TEAM_ID} run_dir=${RUN_DIR}"
log "chromium=$(tr '\n' ' ' < "${ROOT_DIR}/third_party/install/playwright_chromium.version" 2>/dev/null)"
log "profiles=${PROFILES_TO_RUN[*]}"

# Bring MediaMTX up for this run.
log "starting RTSP server"
if ! "${SCRIPT_DIR}/start_rtsp.sh"; then
    FAILURE_REASON="rtsp infrastructure failure"
    log "${FAILURE_REASON}"
    exit 70
fi
for profile in "${PROFILES_TO_RUN[@]}"; do
    if ! "${SCRIPT_DIR}/health_check.sh" "${profile}" 15; then
        FAILURE_REASON="rtsp infrastructure failure (${profile} unreadable)"
        log "${FAILURE_REASON}"
        exit 70
    fi
done

# Captures. A single profile's failure does not abort the other profile.
run_capture() {
    local profile="$1" out="$2"
    log "running runner.py --profile ${profile}"

    # CPU sampling is opt-in per profile via PROFILES[profile].cpu_sampled.
    # PGID comes from the wrapper's clx_start_contestant (setsid → SID==PGID).
    local extra_args=()
    local cpu_sampled
    cpu_sampled="$(.venv/bin/python -c \
        "from lib.profiles import PROFILES; print('1' if PROFILES['${profile}'].cpu_sampled else '0')")"
    if [[ "${cpu_sampled}" == "1" && -f "${RUN_DIR}/contestant.pid" ]]; then
        local pgid
        pgid="$(cat "${RUN_DIR}/contestant.pid" 2>/dev/null || true)"
        if [[ -n "${pgid}" ]]; then
            extra_args+=(--contestant-pgid "${pgid}")
        fi
    fi

    # Per-profile fps from the registry — keep evaluator's capture cadence
    # tied to the source mp4's framerate.
    local profile_fps
    profile_fps="$(.venv/bin/python -c \
        "from lib.profiles import PROFILES; print(PROFILES['${profile}'].fps)")"

    if "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/runner.py" \
            --profile "${profile}" --output "${out}" \
            --duration 30 --fps "${profile_fps}" \
            "${extra_args[@]}"; then
        return 0
    fi
    # runner.py wrote timestamps.json with the reason; surface it.
    local reason
    reason="$(python3 -c "import json,sys;print(json.load(open('${out}/timestamps.json')).get('reason') or '')" 2>/dev/null || true)"
    log "  ${profile} runner failed: ${reason}"
    PROFILE_REASON[${profile}]="${reason}"
    return 1
}

analyze_profile() {
    local profile="$1" shots="$2" metrics="$3"
    local refdir
    refdir="$(.venv/bin/python -c "from lib.profiles import PROFILES; print(PROFILES['${profile}'].reference_dir)")"
    if ! compgen -G "${shots}/shot_*.png" >/dev/null && ! compgen -G "${shots}/shot_*.jpg" >/dev/null; then
        log "no ${profile} screenshots — skipping analyzer"
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

# Trap will tee score.json to original stdout on exit.
