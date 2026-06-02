#!/usr/bin/env bash
# shellcheck shell=bash
# Helpers sourced by scripts/evaluator.sh (and by focused tests under tests/)
# to render and publish result.info from a completed score.json. Source this
# file with ROOT_DIR already set.
#
# Required shell variables at call time:
#   ROOT_DIR          - evaluator repo root (env.sh contract)
#   RUN_DIR           - per-run directory (results/<team_id>_<timestamp>)
#   SCORE_FILE        - path to score.json under RUN_DIR
#   RESULT_INFO_FILE  - path to result.info under RUN_DIR
#   SUBMISSION_ZIP    - absolute path of the submission zip (publication
#                       target derives from dirname of this path)
#   RUN_START_NS      - wall-clock nanoseconds captured at evaluator start
#                       (date +%s%N), used to compute |runtime| milliseconds

# Reasons that still represent a trustworthy contestant outcome (result=0)
# rather than an evaluator/infrastructure failure (result=1). Keep this list
# in sync with CONTESTANT_SIDE_FAILURE_REASONS in result_info.py.
CONTESTANT_SIDE_REASONS=(
    "contestant_frontend_unavailable"
    "contestant_memory_limit_exceeded"
)

# Logging shim: prefer the caller's log() if defined.
if ! declare -F rinfo_log >/dev/null 2>&1; then
    rinfo_log() {
        if declare -F log >/dev/null 2>&1; then
            log "$@"
        else
            printf '[result_info] %s\n' "$*" >&2
        fi
    }
fi

# Classify a failure reason into the result.info |result| code:
#   0 = trustworthy contestant outcome (incl. valid zero-score)
#   1 = evaluator/host/infrastructure failure
classify_result_code() {
    local reason="${1:-}"
    [[ -z "${reason}" ]] && { printf 0; return; }
    local known
    for known in "${CONTESTANT_SIDE_REASONS[@]}"; do
        if [[ "${reason}" == "${known}" ]]; then
            printf 0
            return
        fi
    done
    printf 1
}

write_result_info() {
    # Render result.info from the just-written score.json. Idempotent;
    # callable from both the normal-completion path and the cleanup trap.
    [[ -n "${SCORE_FILE:-}" && -f "${SCORE_FILE}" ]] || return 0
    [[ -n "${RESULT_INFO_FILE:-}" ]] || return 0

    local result_code="${1:-}"
    if [[ -z "${result_code}" ]]; then
        result_code="$(classify_result_code "${FAILURE_REASON:-${HOST_FAILURE_REASON:-}}")"
    fi

    local runtime_ms=0
    if [[ -n "${RUN_START_NS:-}" ]]; then
        local now_ns
        now_ns="$(date +%s%N)"
        runtime_ms=$(( (now_ns - RUN_START_NS) / 1000000 ))
        (( runtime_ms < 0 )) && runtime_ms=0
    fi

    local renderer_args=(
        --score-json "${SCORE_FILE}"
        --runtime-ms "${runtime_ms}"
        --run-dir "${RUN_DIR}"
        --output "${RESULT_INFO_FILE}"
        --result-code "${result_code}"
    )
    local run_without_lock=()
    if declare -F clx_without_lock_fd >/dev/null 2>&1; then
        run_without_lock=(clx_without_lock_fd)
    fi

    local snapshot_args=(
        --score-json "${SCORE_FILE}"
        --run-dir "${RUN_DIR}"
        --submission-zip "${SUBMISSION_ZIP}"
        --public-base-url "${EVALUATOR_PUBLIC_ARTIFACT_BASE_URL:-}"
    )
    if [[ -n "${EVALUATOR_PUBLIC_ARTIFACT_ROOT:-}" ]]; then
        snapshot_args+=(--public-artifact-root "${EVALUATOR_PUBLIC_ARTIFACT_ROOT}")
    fi
    if ! "${run_without_lock[@]}" "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/render_snapshot_publication.py" "${snapshot_args[@]}"; then
        rinfo_log "rendered snapshot publication failed; result.info not rendered"
        return 1
    fi

    if ! "${run_without_lock[@]}" "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/result_info.py" "${renderer_args[@]}"; then
        rinfo_log "result.info renderer failed (result_code=${result_code}); leaving prior file if any"
        return 1
    fi

    # Publish to the upload directory next to the submission zip; by spec,
    # the platform reads <dirname submission_zip>/.
    local dest_dir dest
    dest_dir="$(dirname "${SUBMISSION_ZIP}")"
    if [[ -n "${dest_dir}" && -d "${dest_dir}" ]]; then
        dest="${dest_dir}/result.info"
        if "${run_without_lock[@]}" cp -f "${RESULT_INFO_FILE}" "${dest}"; then
            rinfo_log "result.info published to ${dest}"
        else
            rinfo_log "evaluator failure: failed to publish result.info to ${dest}"
            return 1
        fi
    else
        rinfo_log "evaluator failure: submission zip dirname '${dest_dir}' does not exist; cannot publish result.info"
        return 1
    fi
    return 0
}
