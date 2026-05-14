#!/usr/bin/env bash
# Evaluator main body — runs MediaMTX + capture + scoring inside a prepared
# host environment. Host-side concerns (zip extract, contestant start.sh /
# stop.sh, port precheck) belong to scripts/evaluator-host.sh (target host)
# or scripts/evaluator-local.sh (build host shortcut). MediaMTX is brought
# up AND torn down by this script per run; it is no longer shared with a
# host-resident daemon.

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
H264_SHOTS="${RUN_DIR}/h264_screenshots"
H265_SHOTS="${RUN_DIR}/h265_screenshots"
H264_METRICS="${RUN_DIR}/h264_metrics.json"
H265_METRICS="${RUN_DIR}/h265_metrics.json"

# Tee all log lines to the run log, but keep stdout for the final score JSON.
exec 3>&1
exec > >(tee -a "${LOG_FILE}" >&2) 2>&1

log()  { printf '[evaluator] %s\n' "$*"; }
die()  { printf 'evaluator failed: %s\n' "$*"; }

# State shared with the cleanup trap.
FAILURE_REASON=""
H264_REASON=""
H265_REASON=""

write_failure_score() {
    local reason="$1"
    log "writing failure score (${reason})"
    "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scorer.py" \
        --output "${SCORE_FILE}" \
        --report "${REPORT_FILE}" \
        --install-prefix "${ROOT_DIR}/third_party/install" \
        --failure-reason "${reason}" \
        ${H264_REASON:+--h264-reason "${H264_REASON}"} \
        ${H265_REASON:+--h265-reason "${H265_REASON}"} \
        >/dev/null 2>&1 || true
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
    # `evaluator.sh` get just the JSON line.
    if [[ -f "${SCORE_FILE}" ]]; then
        cat "${SCORE_FILE}" >&3 || true
    fi
    exit "${rc}"
}
trap cleanup EXIT INT TERM

log "starting run team_id=${TEAM_ID} run_dir=${RUN_DIR}"
log "chromium=$(tr '\n' ' ' < "${ROOT_DIR}/third_party/install/playwright_chromium.version" 2>/dev/null)"

# Bring MediaMTX up for this run.
log "starting RTSP server"
if ! "${SCRIPT_DIR}/start_rtsp.sh"; then
    FAILURE_REASON="rtsp infrastructure failure"
    log "${FAILURE_REASON}"
    exit 70
fi
if ! "${SCRIPT_DIR}/health_check.sh" h264 15; then
    FAILURE_REASON="rtsp infrastructure failure (h264 unreadable)"
    log "${FAILURE_REASON}"
    exit 70
fi
if ! "${SCRIPT_DIR}/health_check.sh" h265 15; then
    FAILURE_REASON="rtsp infrastructure failure (h265 unreadable)"
    log "${FAILURE_REASON}"
    exit 70
fi

# Run captures. We do not let a single round's failure abort the other one.
run_capture() {
    local codec="$1" out="$2"
    log "running runner.py --codec ${codec}"

    # Only H.265 round samples CPU. PGID comes from the wrapper's
    # clx_start_contestant (setsid → SID == PGID == CONTESTANT_PID).
    local extra_args=()
    if [[ "${codec}" == "h265" && -f "${RUN_DIR}/contestant.pid" ]]; then
        local pgid
        pgid="$(cat "${RUN_DIR}/contestant.pid" 2>/dev/null || true)"
        if [[ -n "${pgid}" ]]; then
            extra_args+=(--contestant-pgid "${pgid}")
        fi
    fi

    if "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/runner.py" \
            --codec "${codec}" --output "${out}" --duration 30 --fps 30 \
            "${extra_args[@]}"; then
        return 0
    fi
    # runner.py wrote timestamps.json with the reason; surface it.
    local reason
    reason="$(python3 -c "import json,sys;print(json.load(open('${out}/timestamps.json')).get('reason') or '')" 2>/dev/null || true)"
    log "  ${codec} runner failed: ${reason}"
    if [[ "${codec}" == "h264" ]]; then H264_REASON="${reason}"; else H265_REASON="${reason}"; fi
    return 1
}

run_capture h264 "${H264_SHOTS}" || true
run_capture h265 "${H265_SHOTS}" || true

# Analyze each codec whose screenshots directory has files.
analyze_codec() {
    local codec="$1" shots="$2" metrics="$3" refdir="${ROOT_DIR}/reference/$1"
    if ! compgen -G "${shots}/shot_*.png" >/dev/null && ! compgen -G "${shots}/shot_*.jpg" >/dev/null; then
        log "no ${codec} screenshots — skipping analyzer"
        return 1
    fi
    "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/analyzer.py" \
        --codec "${codec}" \
        --screenshots "${shots}" \
        --reference "${refdir}" \
        --output "${metrics}"
}

analyze_codec h264 "${H264_SHOTS}" "${H264_METRICS}" || true
analyze_codec h265 "${H265_SHOTS}" "${H265_METRICS}" || true

# Score + report.
SCORER_ARGS=(--output "${SCORE_FILE}" --report "${REPORT_FILE}"
             --install-prefix "${ROOT_DIR}/third_party/install")
[[ -f "${H264_METRICS}" ]] && SCORER_ARGS+=(--h264 "${H264_METRICS}")
[[ -f "${H265_METRICS}" ]] && SCORER_ARGS+=(--h265 "${H265_METRICS}")
[[ -n "${H264_REASON}" ]] && SCORER_ARGS+=(--h264-reason "${H264_REASON}")
[[ -n "${H265_REASON}" ]] && SCORER_ARGS+=(--h265-reason "${H265_REASON}")

"${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scorer.py" "${SCORER_ARGS[@]}" >/dev/null

log "score written to ${SCORE_FILE}"
log "report written to ${REPORT_FILE}"

# Trap will tee score.json to original stdout on exit.
