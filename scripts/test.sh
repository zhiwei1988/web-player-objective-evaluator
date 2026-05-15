#!/usr/bin/env bash
# Automated regression test for the evaluator itself. Iterates test_submissions/*.zip,
# runs evaluator.sh against each, and asserts the expected outcome.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

log()  { printf '[test] %s\n' "$*" >&2; }
die()  { printf 'test.sh: %s\n' "$*" >&2; exit 1; }

MODE="default"
ONLY=()
while (( $# > 0 )); do
    case "$1" in
        --portable) MODE="portable" ;;
        --only) ONLY+=("$2"); shift ;;
        --only=*) ONLY+=("${1#--only=}") ;;
        -h|--help) cat >&2 <<'EOF'
Usage: scripts/test.sh [--portable] [--only <case>]...

Default mode: run scripts/evaluator-local.sh against each test_submissions/*.zip
on the build host and assert expected outcomes.

--only <case>: run only the named fixture (repeatable). Skips zip rebuild
and any teardown of unrelated state. Intended for iterating on a single
failing case so the full 7-fixture suite doesn't have to be re-run every
edit. Examples:
    scripts/test.sh --only reference
    scripts/test.sh --only static_frame --only iframe_only

--portable: exercise the full bundle path. Requires EVAL_TARGET_HOST=<ssh_alias>
(or <user>@<host>). Stage 1 rebuilds dist/ and smokes the image offline.
Stage 2 ships dist/ + reference.zip to the target host, docker-loads, runs
evaluator-host.sh. Stage 3 cross-checks score parity against an
evaluator-local.sh run on the build host. Stage 4 runs a negative fixture
on the target and asserts the contestant-failure path.
EOF
            exit 0 ;;
        *) die "unknown arg: $1" ;;
    esac
    shift
done

# Expected outcome per case name. Read by the assertions below.
# Each entry is comma-separated <assertion_kind>:<arg>; assertion kinds:
#   total_ge       — objective_total must be >= <arg>
#   fps_2k_eq      — 2K profile FPS score must equal <arg>
#   fps_4k_eq      — 4K profile FPS score must equal <arg>
#   correctness_lt — at least one profile's correctness < <arg>
#   reason_2k      — 2K round failure reason must contain <arg>
#   reason_4k      — 4K round failure reason must contain <arg>
#   reason_global  — top-level failure reason must contain <arg>
#
# NOTE on `reference`: the bundled reference submission plays the pre-encoded
# H.265 MP4 directly via <video> tag. On the canonical Ubuntu 24.04 + Chrome
# host (no hardware HEVC), Chrome falls back to software HEVC decode which
# may or may not keep up at 4K. The reference therefore reliably scores 2K
# full marks (10) but 4K is unstable — `total_ge:10` accepts a 2K-only PASS.
# CPU sub-score is gated by 4K fps, so it likely contributes 0 here. A real
# contestant submission with a wasm HEVC decoder would score higher and still
# PASS this gate.
declare -A EXPECTED=(
    [reference]="total_ge:10"
    [static_frame]="fps_2k_eq:0,fps_4k_eq:0"
    [iframe_only]="fps_2k_eq:0,fps_4k_eq:0"
    [fake_overlay]="correctness_lt:5"
    [missing_start]="reason_global:missing start.sh"
    [never_ready]="reason_2k:startup timeout"
    [missing_testid]="reason_2k:missing data-testid"
)

PASS=()
FAIL=()

# test.sh owns the session in default mode: ensure watermarked streams exist
# before the first run (deploy.sh is now a no-op once streams are fresh, since
# RTSP is brought up per-run by evaluator.sh), and always tear down on exit so
# the harness leaves nothing running.
if [[ "${MODE}" == "default" ]]; then
    missing_stream=0
    while IFS= read -r mp4; do
        [[ -f "${ROOT_DIR}/${mp4}" ]] || { missing_stream=1; break; }
    done < <(.venv/bin/python -c \
        "from lib.profiles import PROFILES; print('\n'.join(s.stream_file for s in PROFILES.values()))")
    if (( missing_stream )); then
        log "streams missing, running scripts/deploy.sh"
        "${SCRIPT_DIR}/deploy.sh" >&2 \
            || die "deploy.sh failed"
    fi
    trap '"${SCRIPT_DIR}/teardown.sh" >&2 || true' EXIT INT TERM
fi

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
    if ! "${SCRIPT_DIR}/evaluator-local.sh" "${team_id}" "${zip}" > "${out_dir}/stdout.json" 2> "${out_dir}/stderr.log"; then
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
def profile(p): return score.get(p, {}) if isinstance(score.get(p), dict) else {}
ok = False
if kind == "total_ge":
    ok = total() >= float(arg)
elif kind == "fps_2k_eq":
    ok = profile("2k").get("fps_points") == int(arg)
elif kind == "fps_4k_eq":
    ok = profile("4k").get("fps_points") == int(arg)
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

portable_main() {
    local target="${EVAL_TARGET_HOST:-}"
    [[ -n "${target}" ]] || die "test.sh --portable requires EVAL_TARGET_HOST=<ssh_alias_or_user@host>"
    local remote_dir="evaluator-test"

    log "stage 1a: rebuilding dist/ via scripts/package.sh"
    "${SCRIPT_DIR}/package.sh" >&2 || die "stage 1a: package.sh failed"
    (cd "${ROOT_DIR}/dist" && sha256sum -c SHA256SUMS) >&2 \
        || die "stage 1a: dist/SHA256SUMS verification failed"

    log "stage 1b: offline-init smoke (--network none)"
    local sha
    sha="$(jq -r .image_sha256 "${ROOT_DIR}/dist/manifest.json")"
    docker run --rm --network none --entrypoint /work/.venv/bin/python "${sha}" \
        -c 'import playwright, PIL, numpy, skimage, pylibdmtx; print("offline ok")' >&2 \
        || die "stage 1b: image fails to initialize offline"

    log "stage 2: ship dist + reference.zip + streams/ to ${target}"
    ssh "${target}" "rm -rf ~/${remote_dir} && mkdir -p ~/${remote_dir}/streams"
    scp -p "${ROOT_DIR}/dist"/* "${target}:~/${remote_dir}/" >&2
    scp -p "${ROOT_DIR}/test_submissions/reference.zip" "${target}:~/${remote_dir}/" >&2
    # The bundled reference contestant's start.sh symlinks each profile's
    # ${REPO_ROOT}/${stream_file} into its web root and serves the mp4 via
    # http.server. On the target host the bundle does NOT include streams/
    # at the filesystem level (they live inside the OCI image), so we ship
    # them out-of-band for self-test only. Real contestants do live RTSP
    # decode and never read streams/.
    scp -rp "${ROOT_DIR}/streams" "${target}:~/${remote_dir}/" >&2

    log "stage 2: docker load + evaluator-host.sh on ${target} (reference)"
    ssh "${target}" "bash -se" <<REMOTE >&2 || die "stage 2: remote evaluator-host.sh failed"
set -e
cd ~/${remote_dir}
sha256sum -c SHA256SUMS
zstd -df evaluator-portable_*.tar.zst -o /tmp/eval_image.tar
docker load < /tmp/eval_image.tar
rm /tmp/eval_image.tar
./evaluator-host.sh team_ref reference.zip > /tmp/portable_score.json
REMOTE
    local remote_score_dir
    remote_score_dir="$(ssh "${target}" "ls -td ~/${remote_dir}/results/team_ref_*" | head -1)"
    [[ -n "${remote_score_dir}" ]] || die "stage 2: no remote results dir"
    scp "${target}:${remote_score_dir}/score.json" /tmp/portable_remote_score.json >&2

    log "stage 3: parity check vs evaluator-local.sh"
    "${SCRIPT_DIR}/teardown.sh" >&2 || true
    "${SCRIPT_DIR}/evaluator-local.sh" team_ref_local "${ROOT_DIR}/test_submissions/reference.zip" \
        > /tmp/portable_local_score.json 2>&1 \
        || die "stage 3: local evaluator-local.sh failed"
    python3 - /tmp/portable_remote_score.json /tmp/portable_local_score.json <<'PY' >&2 \
        || die "stage 3: score parity check failed"
import json, sys
a = json.load(open(sys.argv[1]))
b = json.load(open(sys.argv[2]))
assert a.keys() == b.keys(), f"key drift: {set(a)^set(b)}"
def ssim(d, p):
    block = d.get(p)
    return block.get("mean_ssim", 0) if isinstance(block, dict) else 0
for p in ("2k", "4k"):
    if p in a and p in b:
        delta = abs(ssim(a, p) - ssim(b, p))
        assert delta < 0.05, f"{p} ssim drift: {delta}"
# CPU sub-score is allowed to diverge: container path reports
# container_mode_unsupported, native path runs actual sampling. Compare only
# per-profile totals + max_score.
assert a.get("max_score") == b.get("max_score") == 30, \
    f"max_score drift: remote={a.get('max_score')} local={b.get('max_score')}"
for p in ("2k", "4k"):
    at = a.get(p, {}).get("total") if isinstance(a.get(p), dict) else None
    bt = b.get(p, {}).get("total") if isinstance(b.get(p), dict) else None
    assert at == bt, f"{p} total drift: remote={at} local={bt}"
print("stage 3 ok")
PY

    log "stage 4: negative fixture (never_ready) on ${target}"
    scp -p "${ROOT_DIR}/test_submissions/never_ready.zip" "${target}:~/${remote_dir}/" >&2
    set +e
    ssh "${target}" "cd ~/${remote_dir} && ./evaluator-host.sh team_fail never_ready.zip" >/dev/null
    local rc=$?
    set -e
    # never_ready serves HTTP fine but the player never signals __PLAYER_READY__.
    # That triggers the per-profile runner timeout path (exit 0, per-profile
    # reason populated). It does NOT trigger contestant_frontend_unavailable
    # (exit 2), which would require start.sh failing to bind 8080 at all.
    [[ ${rc} -eq 0 ]] || die "stage 4: expected exit 0 (per-profile timeout), got ${rc}"
    local remote_fail_dir
    remote_fail_dir="$(ssh "${target}" "ls -td ~/${remote_dir}/results/team_fail_*" | head -1)"
    scp "${target}:${remote_fail_dir}/score.json" /tmp/portable_remote_fail.json >&2
    local two_k_reason
    two_k_reason="$(jq -r '.["2k"].reason // empty' /tmp/portable_remote_fail.json)"
    [[ "${two_k_reason}" == *"startup timeout"* ]] \
        || die "stage 4: expected 2k.reason ~ 'startup timeout', got: ${two_k_reason}"

    printf '\n=== test.sh --portable summary ===\nALL STAGES PASS\n'
}

main() {
    if [[ "${MODE}" == "portable" ]]; then
        portable_main
        return
    fi
    local cases
    if (( ${#ONLY[@]} > 0 )); then
        cases=("${ONLY[@]}")
        # Validate each --only matches a known EXPECTED entry; typos here are
        # easy to miss and silently degrade coverage.
        local c
        for c in "${cases[@]}"; do
            [[ -n "${EXPECTED[${c}]:-}" ]] || die "--only ${c}: unknown case (no EXPECTED entry)"
        done
    else
        cases=(reference static_frame iframe_only fake_overlay missing_start never_ready missing_testid)
    fi
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
