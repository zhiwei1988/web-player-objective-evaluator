#!/usr/bin/env bash
# Build-host local-development entry. Same UX as evaluator-host.sh but
# invokes scripts/evaluator.sh natively (no docker). Useful when iterating
# on the evaluator pipeline before re-packaging the image.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
    cat >&2 <<'EOF'
Usage: scripts/evaluator-local.sh <team_id> <submission_zip>

Build-host dev shortcut; runs evaluator natively without docker. For
target-host evaluation use scripts/evaluator-host.sh instead.
EOF
    exit 64
}

(( $# >= 2 )) || usage
TEAM_ID="$1"
SUBMISSION_ZIP="$(readlink -f "$2" 2>/dev/null || echo "$2")"

# shellcheck source=_contestant_lifecycle.sh
source "${SCRIPT_DIR}/_contestant_lifecycle.sh"

clx_acquire_lock
# Only check 8080. evaluator.sh starts MediaMTX itself via the idempotent
# scripts/start_rtsp.sh; a pre-existing MediaMTX on 554 (e.g. from deploy.sh)
# will be reused, then torn down by evaluator.sh's cleanup trap. The native
# path relies on scripts/build.sh having setcap'd the mediamtx binary so
# binding :554 does not need root here.
clx_precheck_ports 8080
clx_prepare_run_dir "${TEAM_ID}"

# fd-3 holds the original stdout for the final score JSON; tee logs to file.
exec 3>&1
exec > >(tee -a "${RUN_DIR}/evaluator-host.log" >&2) 2>&1

cleanup() {
    local rc=$?
    clx_cleanup_contestant
    # If we set up a RUN_DIR but never produced a score.json, write a
    # failure score so callers always see one.
    if [[ -n "${RUN_DIR:-}" && -d "${RUN_DIR}" && ! -f "${RUN_DIR}/score.json" ]]; then
        "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scorer.py" \
            --output "${RUN_DIR}/score.json" \
            --report "${RUN_DIR}/report.html" \
            --install-prefix "${ROOT_DIR}/third_party/install" \
            --failure-reason "${HOST_FAILURE_REASON:-evaluator aborted}" \
            > /dev/null 2>&1 || true
    fi
    clx_emit_score_to_fd3
    exit "${rc}"
}
trap cleanup EXIT INT TERM

clx_extract_submission "${SUBMISSION_ZIP}"
clx_start_contestant

if ! clx_wait_frontend_ready; then
    clx_log "contestant frontend never became ready"
    "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scorer.py" \
        --output "${RUN_DIR}/score.json" \
        --report "${RUN_DIR}/report.html" \
        --install-prefix "${ROOT_DIR}/third_party/install" \
        --failure-reason "contestant_frontend_unavailable" \
        > /dev/null 2>&1 || true
    exit 2
fi

# Wrapper owns score-JSON emission via clx_emit_score_to_fd3; tell the inner
# script to skip its own emission so callers don't see two copies.
EVALUATOR_SKIP_STDOUT_EMIT=1 "${SCRIPT_DIR}/evaluator.sh" "${TEAM_ID}" "${RESULTS_SUBDIR}"
