#!/usr/bin/env bash
# Automated regression test for the evaluator itself. Iterates
# test_submissions/*.zip, runs evaluator.sh against each, and asserts the
# expected outcome.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

log()  { printf '[test] %s\n' "$*" >&2; }
die()  { printf 'test.sh: %s\n' "$*" >&2; exit 1; }

usage() {
    cat >&2 <<'EOF'
Usage: scripts/test.sh [--only <case>]...

Run the bundled evaluator self-test suite. The harness auto-runs deploy.sh if
generated streams are missing, drives every selected case through
scripts/evaluator.sh, and registers scripts/teardown.sh as an EXIT backstop.

Supported flags:
  --only <case>   Run only the named fixture. Repeatable.
  -h, --help      Show this help.

Removed flags:
  --portable      removed with the retired distribution workflow.
EOF
}

ONLY=()
while (( $# > 0 )); do
    case "$1" in
        --only)
            [[ -n "${2:-}" ]] || die "--only requires a case name"
            ONLY+=("$2")
            shift
            ;;
        --only=*) ONLY+=("${1#--only=}") ;;
        --portable)
            usage
            exit 64
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage
            die "unknown arg: $1"
            ;;
    esac
    shift
done

# Expected outcome per case name. Read by the assertions below.
# Each entry is comma-separated <assertion_kind>:<arg>; assertion kinds:
#   total_ge       - objective_total must be >= <arg>
#   total_le       - objective_total must be <= <arg>
#   fps_2k_eq      - 2K profile FPS score must equal <arg>
#   fps_4k_eq      - 4K profile FPS score must equal <arg>
#   fps_4k_le      - 4K profile FPS score must be <= <arg>
#   correctness_lt - at least one profile's correctness < <arg>
#   verdict_2k     - 2K decode_path.verdict must equal <arg>
#   verdict_4k     - 4K decode_path.verdict must equal <arg>
#   reason_2k      - 2K round failure reason must contain <arg>
#   reason_4k      - 4K round failure reason must contain <arg>
#   reason_global  - top-level failure reason must contain <arg>
declare -A EXPECTED=(
    [reference]="total_ge:10"
    [static_frame]="fps_2k_eq:0,fps_4k_le:0.1"
    [iframe_only]="fps_2k_eq:0,fps_4k_le:0.1"
    [fake_overlay]="correctness_lt:5"
    [transcode_to_h264]="verdict_2k:violation,verdict_4k:violation,total_le:0"
    [missing_start]="reason_global:contestant_frontend_unavailable"
    [never_ready]="reason_2k:startup timeout"
    [missing_testid]="reason_2k:missing data-testid"
)

PASS=()
FAIL=()

# test.sh owns the session: ensure watermarked streams exist before the first
# run and always tear down on exit so the harness leaves nothing running.
missing_stream=0
while IFS= read -r mp4; do
    [[ -f "${ROOT_DIR}/${mp4}" ]] || { missing_stream=1; break; }
done < <("${ROOT_DIR}/.venv/bin/python" -c \
    "from lib.profiles import PROFILES; print('\n'.join(s.stream_file for s in PROFILES.values()))")
if (( missing_stream )); then
    log "streams missing, running scripts/deploy.sh"
    "${SCRIPT_DIR}/deploy.sh" >&2 \
        || die "deploy.sh failed"
fi
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
    local upload_dir upload_zip
    upload_dir="${out_dir}/upload"
    mkdir -p "${upload_dir}"
    upload_zip="${upload_dir}/${case_name}.zip"
    cp -f "${zip}" "${upload_zip}"
    if ! EVALUATOR_REPO_ROOT="${ROOT_DIR}" "${SCRIPT_DIR}/evaluator.sh" "${team_id}" "${upload_zip}" > "${out_dir}/stdout.json" 2> "${out_dir}/stderr.log"; then
        : # Contestant failures can be legitimate expectations.
    fi
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
def profile(p): return score.get(p, {}) if isinstance(score.get(p), dict) else {}
ok = False
if kind == "total_ge":
    ok = total() >= float(arg)
elif kind == "total_le":
    ok = total() <= float(arg)
elif kind == "verdict_2k":
    ok = (profile("2k").get("decode_path") or {}).get("verdict") == arg
elif kind == "verdict_4k":
    ok = (profile("4k").get("decode_path") or {}).get("verdict") == arg
elif kind == "fps_2k_eq":
    ok = profile("2k").get("fps_points") == int(arg)
elif kind == "fps_4k_eq":
    ok = profile("4k").get("fps_points") == int(arg)
elif kind == "fps_4k_le":
    ok = float(profile("4k").get("fps_points") or 0) <= float(arg)
elif kind == "correctness_lt":
    ok = (profile("2k").get("correctness_points", 99) < int(arg)
          or profile("4k").get("correctness_points", 99) < int(arg))
elif kind == "reason_2k":
    ok = arg in (profile("2k").get("reason") or "")
elif kind == "reason_4k":
    ok = arg in (profile("4k").get("reason") or "")
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

if (( ${#ONLY[@]} > 0 )); then
    cases=("${ONLY[@]}")
    for c in "${cases[@]}"; do
        [[ -n "${EXPECTED[${c}]:-}" ]] || die "--only ${c}: unknown case (no EXPECTED entry)"
    done
else
    cases=(reference static_frame iframe_only fake_overlay transcode_to_h264 missing_start never_ready missing_testid)
fi

for c in "${cases[@]}"; do run_case "${c}"; done

printf '\n=== test.sh summary ===\n'
printf 'PASS: %d\n' "${#PASS[@]}"
printf 'FAIL: %d\n' "${#FAIL[@]}"
if (( ${#FAIL[@]} > 0 )); then
    printf '\nFailures:\n'
    for f in "${FAIL[@]}"; do printf '  %s\n' "${f}"; done
    exit 1
fi
