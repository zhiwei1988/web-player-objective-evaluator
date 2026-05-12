#!/usr/bin/env bash
# Per-submission evaluator entry point.
#   ./scripts/evaluator.sh <team_id> <submission_zip>
#
# Run order matters and the trap is defined BEFORE anything that can fail so
# cleanup runs on every exit path.

set -uo pipefail
# NB: not `set -e` — we want explicit failure handling per step so the score JSON
# always gets written.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
    cat >&2 <<'EOF'
Usage: scripts/evaluator.sh <team_id> <submission_zip>

Required:
  team_id          Short identifier for this run. Used in results/<team_id>_<timestamp>/.
  submission_zip   Path to the contestant submission zip.

The submission zip must extract to provide start.sh (required) and stop.sh (optional).
EOF
    exit 64
}

(( $# >= 2 )) || usage
TEAM_ID="$1"
SUBMISSION_ZIP="$(readlink -f "$2" 2>/dev/null || echo "$2")"
[[ -f "${SUBMISSION_ZIP}" ]] || { printf 'evaluator: zip not found: %s\n' "${SUBMISSION_ZIP}" >&2; exit 65; }

# Pre-flight: source-built binaries must be present.
for bin in ffmpeg mediamtx tesseract; do
    if [[ ! -x "${ROOT_DIR}/third_party/install/bin/${bin}" ]]; then
        printf 'evaluator: %s missing under third_party/install/bin — run scripts/build.sh\n' "${bin}" >&2
        exit 66
    fi
done

# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
RUN_DIR="${ROOT_DIR}/results/${TEAM_ID}_${TIMESTAMP}"
STAGE_DIR="${ROOT_DIR}/submissions/${TEAM_ID}"
mkdir -p "${RUN_DIR}" "${STAGE_DIR}"

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
CONTESTANT_PID=""
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

clean_ports() {
    # The only port the evaluator contracts for is 8080 (contestant HTTP
    # frontend). Any other ports the contestant uses internally are their
    # own concern; the process-group SIGKILL below cleans those up.
    local pids
    pids="$(lsof -ti:8080 2>/dev/null || true)"
    if [[ -n "${pids}" ]]; then
        log "killing pid(s) on :8080: ${pids}"
        kill -9 ${pids} 2>/dev/null || true
    fi
}

cleanup() {
    local rc=$?
    # MediaMTX is intentionally NOT killed — it's owned by deploy.sh and shared
    # across runs. We only kill what we started.
    if [[ -n "${CONTESTANT_PID}" ]] && kill -0 "${CONTESTANT_PID}" 2>/dev/null; then
        log "stopping contestant pid tree ${CONTESTANT_PID}"
        if [[ -x "${STAGE_DIR}/stop.sh" ]]; then
            (cd "${STAGE_DIR}" && timeout 10 ./stop.sh) || true
        fi
        # Belt-and-braces: kill the process group.
        kill -- "-${CONTESTANT_PID}" 2>/dev/null || true
        sleep 1
        kill -9 -- "-${CONTESTANT_PID}" 2>/dev/null || true
    fi
    clean_ports
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
log "submission=${SUBMISSION_ZIP}"
log "chromium=$(cat "${ROOT_DIR}/third_party/install/playwright_chromium.version" 2>/dev/null | tr '\n' ' ')"

# Pre-run cleanup.
log "pre-run cleanup of port 8080"
clean_ports

# Ensure RTSP server is up. deploy.sh idempotently starts it; if it's already
# running this is fast.
log "ensuring RTSP server is up"
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

# Extract submission.
log "extracting ${SUBMISSION_ZIP} into ${STAGE_DIR}"
rm -rf "${STAGE_DIR}"; mkdir -p "${STAGE_DIR}"
if ! unzip -qq "${SUBMISSION_ZIP}" -d "${STAGE_DIR}"; then
    FAILURE_REASON="unzip failed"
    log "${FAILURE_REASON}"
    exit 71
fi

# If the zip wrapped everything in a single top-level directory, lift it.
if [[ ! -f "${STAGE_DIR}/start.sh" ]]; then
    inner="$(find "${STAGE_DIR}" -mindepth 1 -maxdepth 1 -type d | head -1 || true)"
    if [[ -n "${inner}" && -f "${inner}/start.sh" ]]; then
        log "lifting submission contents out of ${inner}"
        shopt -s dotglob
        mv "${inner}"/* "${STAGE_DIR}/"
        shopt -u dotglob
        rmdir "${inner}" 2>/dev/null || true
    fi
fi

if [[ ! -f "${STAGE_DIR}/start.sh" ]]; then
    FAILURE_REASON="missing start.sh"
    log "${FAILURE_REASON}"
    exit 1
fi

chmod +x "${STAGE_DIR}/start.sh"
[[ -f "${STAGE_DIR}/stop.sh" ]] && chmod +x "${STAGE_DIR}/stop.sh"

# Export contestant env.
export RTSP_SERVER_HOST=127.0.0.1
export RTSP_SERVER_PORT=8554
export FRONTEND_PORT=8080

log "invoking contestant start.sh"
(cd "${STAGE_DIR}" && setsid ./start.sh) >"${RUN_DIR}/contestant.log" 2>&1 &
CONTESTANT_PID=$!
log "contestant pid=${CONTESTANT_PID}"

# Poll the H.264 autoplay URL up to 60s.
log "waiting up to 60s for http://localhost:${FRONTEND_PORT}/play?codec=h264&autoplay=1"
ready=0
for i in $(seq 1 60); do
    if curl -fsS -o /dev/null --max-time 2 "http://localhost:${FRONTEND_PORT}/play?codec=h264&autoplay=1"; then
        log "frontend reachable after ${i}s"
        ready=1
        break
    fi
    sleep 1
done
if (( ! ready )); then
    FAILURE_REASON="startup timeout"
    log "${FAILURE_REASON}"
    write_failure_score "${FAILURE_REASON}"
    exit 1
fi

# Run captures. We do not let a single round's failure abort the other one.
run_capture() {
    local codec="$1" out="$2"
    log "running runner.py --codec ${codec}"
    if "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/runner.py" \
            --codec "${codec}" --output "${out}" --duration 30 --fps 30; then
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
