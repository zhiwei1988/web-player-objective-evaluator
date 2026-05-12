#!/usr/bin/env bash
# Automated regression test for the evaluator itself. Iterates test_submissions/*.zip,
# runs evaluator.sh against each, and asserts the expected outcome.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

log()  { printf '[test] %s\n' "$*" >&2; }

# Expected outcome per case name. Read by the assertions below.
# Each entry is: <case_name>:<assertion_kind>:<arg>
# assertion_kinds:
#   total_ge       — objective_total must be >= <arg>
#   fps_h264_eq    — H.264 FPS score must equal <arg>
#   fps_h265_eq    — H.265 FPS score must equal <arg>
#   correctness_lt — H.264 (or both) correctness < <arg>
#   reason_h264    — H.264 round failure reason must contain <arg>
#   reason_global  — top-level failure reason must contain <arg>
# NOTE on `reference`: the bundled reference submission only renders H.264
# (Chrome on the canonical Ubuntu 24.04 host has no HEVC decoder, and the spec
# explicitly requires contestants to ship their own software H.265 decoder).
# The reference therefore demonstrates a compliant H.264 path and intentionally
# fails the H.265 round — `total_ge:13` lets that be a PASS (h264 full marks
# = 15, h265 = 0). A real contestant submission shipping a WASM HEVC decoder
# would score higher and still PASS this gate.
declare -A EXPECTED=(
    [reference]="total_ge:13"
    [static_frame]="fps_h264_eq:0,fps_h265_eq:0"
    [iframe_only]="fps_h264_eq:0,fps_h265_eq:0"
    [fake_overlay]="correctness_lt:10"
    [missing_start]="reason_global:missing start.sh"
    [never_ready]="reason_h264:startup timeout"
    [missing_testid]="reason_h264:missing data-testid"
)

PASS=()
FAIL=()

# test.sh owns the session: bring up infrastructure if not already up, and
# always tear it down on exit. Running scripts/deploy.sh before calling us is
# still fine — start_rtsp.sh is idempotent.
if ! "${SCRIPT_DIR}/health_check.sh" h264 >/dev/null 2>&1; then
    log "RTSP not reachable, running scripts/deploy.sh"
    "${SCRIPT_DIR}/deploy.sh" >&2 \
        || { printf 'test.sh: deploy.sh failed\n' >&2; exit 1; }
fi

# Always teardown on exit so the harness leaves nothing running.
trap '"${SCRIPT_DIR}/teardown.sh" >&2 || true' EXIT INT TERM

run_case() {
    local case_name="$1"
    local zip="${ROOT_DIR}/test_submissions/${case_name}.zip"
    if [[ ! -f "${zip}" ]]; then
        log "SKIP ${case_name}: ${zip} missing"
        return
    fi
    log "running ${case_name}"
    local team_id="selftest_${case_name}"
    local out_dir
    out_dir="$(mktemp -d)"
    if ! "${SCRIPT_DIR}/evaluator.sh" "${team_id}" "${zip}" > "${out_dir}/stdout.json" 2> "${out_dir}/stderr.log"; then
        : # evaluator.sh may exit non-zero for legitimate contestant failures; keep going.
    fi
    # Locate the most recent results dir for this team_id.
    local result_dir
    result_dir="$(ls -td "${ROOT_DIR}/results/${team_id}_"* 2>/dev/null | head -1 || true)"
    [[ -n "${result_dir}" ]] || { FAIL+=("${case_name}: no results directory"); return; }
    [[ -f "${result_dir}/score.json" ]] || { FAIL+=("${case_name}: no score.json"); return; }
    assert_case "${case_name}" "${result_dir}/score.json"
}

assert_case() {
    local case_name="$1"
    local score_json="$2"
    local expectations="${EXPECTED[${case_name}]:-}"
    [[ -n "${expectations}" ]] || { FAIL+=("${case_name}: no expectation registered"); return; }
    local ok=1
    local IFS=','
    local entry
    for entry in ${expectations}; do
        local kind="${entry%%:*}"
        local arg="${entry#*:}"
        if ! python3 - "${score_json}" "${kind}" "${arg}" <<'PY'
import json, sys
score_path, kind, arg = sys.argv[1], sys.argv[2], sys.argv[3]
with open(score_path) as f:
    score = json.load(f)
def total(): return score.get("objective_total", 0)
def codec(c): return score.get(c, {}) if isinstance(score.get(c), dict) else {}
ok = False
if kind == "total_ge":
    ok = total() >= float(arg)
elif kind == "fps_h264_eq":
    ok = codec("h264").get("fps_points") == int(arg)
elif kind == "fps_h265_eq":
    ok = codec("h265").get("fps_points") == int(arg)
elif kind == "correctness_lt":
    ok = (codec("h264").get("correctness_points", 99) < int(arg)
          or codec("h265").get("correctness_points", 99) < int(arg))
elif kind == "reason_h264":
    ok = arg in (codec("h264").get("reason") or "")
elif kind == "reason_global":
    ok = arg in (score.get("reason") or "")
sys.exit(0 if ok else 1)
PY
        then
            ok=0
            FAIL+=("${case_name}: ${kind}=${arg} failed (score.json=${score_json})")
        fi
    done
    if (( ok )); then
        PASS+=("${case_name}")
        log "  PASS ${case_name}"
    else
        log "  FAIL ${case_name}"
    fi
}

main() {
    local cases=(reference static_frame iframe_only fake_overlay missing_start never_ready missing_testid)
    local c
    for c in "${cases[@]}"; do run_case "${c}"; done
    printf '\n=== test.sh summary ===\n'
    printf 'PASS: %d\n' "${#PASS[@]}"
    printf 'FAIL: %d\n' "${#FAIL[@]}"
    if (( ${#FAIL[@]} > 0 )); then
        printf '\nFailures:\n'
        local f; for f in "${FAIL[@]}"; do printf '  %s\n' "${f}"; done
        exit 1
    fi
}

main "$@"
