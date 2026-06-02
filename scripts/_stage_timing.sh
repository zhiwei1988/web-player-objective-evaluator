#!/usr/bin/env bash
# shellcheck shell=bash
# Helpers sourced by scripts/evaluator.sh and focused tests to enforce bounded
# stage execution and write machine-readable stage timings.

STAGE_TIMINGS_FILE=""
STG_TOTAL_WATCHDOG_PID=""

stg_load_timeout_budgets() {
    EVALUATOR_CAPTURE_TIMEOUT_SECONDS="${EVALUATOR_CAPTURE_TIMEOUT_SECONDS:-120}"
    EVALUATOR_ANALYSIS_TIMEOUT_SECONDS="${EVALUATOR_ANALYSIS_TIMEOUT_SECONDS:-240}"
    EVALUATOR_SCORE_TIMEOUT_SECONDS="${EVALUATOR_SCORE_TIMEOUT_SECONDS:-60}"
    EVALUATOR_TOTAL_TIMEOUT_SECONDS="${EVALUATOR_TOTAL_TIMEOUT_SECONDS:-600}"
    export EVALUATOR_CAPTURE_TIMEOUT_SECONDS
    export EVALUATOR_ANALYSIS_TIMEOUT_SECONDS
    export EVALUATOR_SCORE_TIMEOUT_SECONDS
    export EVALUATOR_TOTAL_TIMEOUT_SECONDS
}

stg_init_stage_timings() {
    [[ -n "${RUN_DIR:-}" ]] || { printf 'stg_init_stage_timings: RUN_DIR required\n' >&2; return 1; }
    STAGE_TIMINGS_FILE="${RUN_DIR}/stage_timings.json"
    mkdir -p "${RUN_DIR}"
    python3 - "${STAGE_TIMINGS_FILE}" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = {
    "budgets": {
        "capture_timeout_seconds": int(os.environ["EVALUATOR_CAPTURE_TIMEOUT_SECONDS"]),
        "analysis_timeout_seconds": int(os.environ["EVALUATOR_ANALYSIS_TIMEOUT_SECONDS"]),
        "score_timeout_seconds": int(os.environ["EVALUATOR_SCORE_TIMEOUT_SECONDS"]),
        "total_timeout_seconds": int(os.environ["EVALUATOR_TOTAL_TIMEOUT_SECONDS"]),
        "contestant_memory_max": os.environ.get("EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE")
            or os.environ.get("EVALUATOR_CONTESTANT_MEMORY_MAX")
            or "10G",
    },
    "stages": [],
}
path.write_text(json.dumps(data, indent=2))
PY
}

stg_ensure_stage_timings() {
    [[ -n "${STAGE_TIMINGS_FILE:-}" ]] || STAGE_TIMINGS_FILE="${RUN_DIR:-}/stage_timings.json"
    [[ -n "${STAGE_TIMINGS_FILE}" ]] || return 0
    [[ -f "${STAGE_TIMINGS_FILE}" ]] && return 0
    mkdir -p "$(dirname "${STAGE_TIMINGS_FILE}")"
    python3 - "${STAGE_TIMINGS_FILE}" <<'PY'
import json
import os
import sys
from pathlib import Path

def as_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default

path = Path(sys.argv[1])
data = {
    "budgets": {
        "capture_timeout_seconds": as_int("EVALUATOR_CAPTURE_TIMEOUT_SECONDS", 120),
        "analysis_timeout_seconds": as_int("EVALUATOR_ANALYSIS_TIMEOUT_SECONDS", 240),
        "score_timeout_seconds": as_int("EVALUATOR_SCORE_TIMEOUT_SECONDS", 60),
        "total_timeout_seconds": as_int("EVALUATOR_TOTAL_TIMEOUT_SECONDS", 600),
        "contestant_memory_max": os.environ.get("EVALUATOR_CONTESTANT_MEMORY_MAX_EFFECTIVE")
            or os.environ.get("EVALUATOR_CONTESTANT_MEMORY_MAX")
            or "10G",
    },
    "stages": [],
}
path.write_text(json.dumps(data, indent=2))
PY
}

stg_record_stage_started() {
    local stage="$1" profile="${2:-}" timeout_seconds="${3:-}" start_epoch="${4:-}" reason="${5:-}"
    [[ -n "${STAGE_TIMINGS_FILE:-}" ]] || STAGE_TIMINGS_FILE="${RUN_DIR:-}/stage_timings.json"
    [[ -n "${STAGE_TIMINGS_FILE}" ]] || return 0
    stg_ensure_stage_timings
    local now
    now="$(python3 - <<'PY'
import time
print(f"{time.time():.6f}")
PY
)"
    [[ -n "${start_epoch}" ]] || start_epoch="${now}"
    [[ -n "${reason}" ]] || reason="stage started; if no later final record exists, evaluator stopped before classifying this stage"
    python3 - "${STAGE_TIMINGS_FILE}" "${stage}" "${profile}" "${timeout_seconds}" "${reason}" "${start_epoch}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text())
except Exception:
    data = {"budgets": {}, "stages": []}

stage, profile = sys.argv[2], sys.argv[3]
timeout_s, reason = sys.argv[4], sys.argv[5]
start_epoch = float(sys.argv[6])
record = {
    "stage": stage,
    "status": "running",
    "start_epoch": start_epoch,
    "reason": reason,
}
if profile:
    record["profile"] = profile
if timeout_s:
    record["timeout_seconds"] = int(float(timeout_s))
data.setdefault("stages", []).append(record)
path.write_text(json.dumps(data, indent=2))
PY
}

stg_record_stage() {
    local status="$1" stage="$2" profile="${3:-}" timeout_seconds="${4:-}" exit_code="${5:-}" reason="${6:-}"
    local start_epoch="${7:-}" end_epoch="${8:-}" duration_s="${9:-}"
    [[ -n "${STAGE_TIMINGS_FILE:-}" ]] || STAGE_TIMINGS_FILE="${RUN_DIR:-}/stage_timings.json"
    [[ -n "${STAGE_TIMINGS_FILE}" ]] || return 0
    stg_ensure_stage_timings
    local now
    now="$(python3 - <<'PY'
import time
print(f"{time.time():.6f}")
PY
)"
    [[ -n "${start_epoch}" ]] || start_epoch="${now}"
    [[ -n "${end_epoch}" ]] || end_epoch="${now}"
    if [[ -z "${duration_s}" ]]; then
        duration_s="$(python3 - "${start_epoch}" "${end_epoch}" <<'PY'
import sys
start = float(sys.argv[1])
end = float(sys.argv[2])
print(max(0.0, end - start))
PY
)"
    fi
    python3 - "${STAGE_TIMINGS_FILE}" "${status}" "${stage}" "${profile}" "${timeout_seconds}" "${exit_code}" "${reason}" "${start_epoch}" "${end_epoch}" "${duration_s}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text())
except Exception:
    data = {"budgets": {}, "stages": []}

status, stage, profile = sys.argv[2], sys.argv[3], sys.argv[4]
timeout_s, exit_code, reason = sys.argv[5], sys.argv[6], sys.argv[7]
start_epoch, end_epoch, duration_s = float(sys.argv[8]), float(sys.argv[9]), float(sys.argv[10])
record = {
    "stage": stage,
    "status": status,
    "start_epoch": start_epoch,
    "end_epoch": end_epoch,
    "duration_s": duration_s,
}
if profile:
    record["profile"] = profile
if timeout_s:
    record["timeout_seconds"] = int(float(timeout_s))
if exit_code:
    record["exit_code"] = int(exit_code)
if reason:
    record["reason"] = reason
data.setdefault("stages", []).append(record)
path.write_text(json.dumps(data, indent=2))
PY
}

stg_run_stage() {
    local stage="$1" profile="${2:-}" timeout_seconds="${3:-0}"
    shift 3
    local start_epoch end_epoch duration_s rc status reason
    start_epoch="$(python3 - <<'PY'
import time
print(f"{time.time():.6f}")
PY
)"
    stg_record_stage_started "${stage}" "${profile}" "${timeout_seconds}" "${start_epoch}"
    if [[ -n "${timeout_seconds}" && "${timeout_seconds}" != "0" ]]; then
        timeout --foreground --kill-after=10s "${timeout_seconds}s" \
            bash -c 'exec 9>&- || true; exec "$@"' _ "$@"
    else
        (exec 9>&- || true; "$@")
    fi
    rc=$?
    end_epoch="$(python3 - <<'PY'
import time
print(f"{time.time():.6f}")
PY
)"
    duration_s="$(python3 - "${start_epoch}" "${end_epoch}" <<'PY'
import sys
print(max(0.0, float(sys.argv[2]) - float(sys.argv[1])))
PY
)"
    if [[ "${rc}" == "124" || "${rc}" == "137" ]]; then
        status="timeout"
        reason="${stage} timeout after ${timeout_seconds}s"
    elif [[ "${rc}" == "0" ]]; then
        status="success"
        reason=""
    else
        status="failed"
        reason="${stage} failed with exit code ${rc}"
    fi
    stg_record_stage "${status}" "${stage}" "${profile}" "${timeout_seconds}" "${rc}" "${reason}" "${start_epoch}" "${end_epoch}" "${duration_s}"
    return "${rc}"
}

stg_start_total_watchdog() {
    [[ -n "${RUN_DIR:-}" ]] || return 0
    [[ -n "${EVALUATOR_TOTAL_TIMEOUT_SECONDS:-}" ]] || return 0
    local parent_pid="$$"
    (
        sleep_pid=""
        cleanup_watchdog_sleep() {
            if [[ -n "${sleep_pid:-}" ]]; then
                kill "${sleep_pid}" 2>/dev/null || true
                wait "${sleep_pid}" 2>/dev/null || true
            fi
        }
        trap cleanup_watchdog_sleep EXIT INT TERM
        sleep "${EVALUATOR_TOTAL_TIMEOUT_SECONDS}" &
        sleep_pid="$!"
        wait "${sleep_pid}" || exit 0
        sleep_pid=""
        trap - EXIT INT TERM
        STAGE_TIMINGS_FILE="${RUN_DIR}/stage_timings.json"
        stg_record_stage "timeout" "evaluator_body" "" "${EVALUATOR_TOTAL_TIMEOUT_SECONDS}" "" "evaluator total timeout after ${EVALUATOR_TOTAL_TIMEOUT_SECONDS}s"
        kill -TERM "${parent_pid}" 2>/dev/null || true
    ) &
    STG_TOTAL_WATCHDOG_PID=$!
}

stg_stop_total_watchdog() {
    if [[ -n "${STG_TOTAL_WATCHDOG_PID:-}" ]]; then
        kill "${STG_TOTAL_WATCHDOG_PID}" 2>/dev/null || true
        wait "${STG_TOTAL_WATCHDOG_PID}" 2>/dev/null || true
        STG_TOTAL_WATCHDOG_PID=""
    fi
}
