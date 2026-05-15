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
while (( $# > 0 )); do
    case "$1" in
        --portable) MODE="portable" ;;
        -h|--help) cat >&2 <<'EOF'
Usage: scripts/test.sh [--portable]

Default mode: run scripts/evaluator-local.sh against each test_submissions/*.zip
on the build host and assert expected outcomes.

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

# test.sh owns the session in default mode: bring up infrastructure if not
# already up, and always tear it down on exit. evaluator-local.sh now owns
# MediaMTX per run via the idempotent start_rtsp.sh, so this upfront deploy
# is no longer strictly required — but kept for backwards compatibility with
# operators who expect `scripts/test.sh` to work standalone.
if [[ "${MODE}" == "default" ]]; then
    if ! "${SCRIPT_DIR}/health_check.sh" h264 >/dev/null 2>&1; then
        log "RTSP not reachable, running scripts/deploy.sh"
        "${SCRIPT_DIR}/deploy.sh" >&2 \
            || die "deploy.sh failed"
    fi
    # Always teardown on exit so the harness leaves nothing running.
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
    # The bundled reference contestant's start.sh symlinks
    # ${REPO_ROOT}/streams/<codec>_watermarked.mp4 into its web root and
    # serves the file directly via http.server. On the target host the bundle
    # does NOT include streams/ at the filesystem level (they live inside the
    # OCI image), so we ship them out-of-band for self-test only. Real
    # contestants do live RTSP decode and never read streams/.
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
def ssim(d, c):
    block = d.get(c)
    return block.get("mean_ssim", 0) if isinstance(block, dict) else 0
for c in ("h264", "h265"):
    if c in a and c in b:
        delta = abs(ssim(a, c) - ssim(b, c))
        assert delta < 0.05, f"{c} ssim drift: {delta}"
assert a["objective_total"] == b["objective_total"], \
    f"objective_total drift: remote={a['objective_total']} local={b['objective_total']}"
print("stage 3 ok")
PY

    log "stage 4: negative fixture (never_ready) on ${target}"
    scp -p "${ROOT_DIR}/test_submissions/never_ready.zip" "${target}:~/${remote_dir}/" >&2
    set +e
    ssh "${target}" "cd ~/${remote_dir} && ./evaluator-host.sh team_fail never_ready.zip" >/dev/null
    local rc=$?
    set -e
    # never_ready serves HTTP fine but the player never signals __PLAYER_READY__.
    # That triggers the per-codec runner timeout path (exit 0, per-codec reason
    # populated). It does NOT trigger contestant_frontend_unavailable (exit 2),
    # which would require start.sh failing to bind 8080 at all.
    [[ ${rc} -eq 0 ]] || die "stage 4: expected exit 0 (per-codec timeout), got ${rc}"
    local remote_fail_dir
    remote_fail_dir="$(ssh "${target}" "ls -td ~/${remote_dir}/results/team_fail_*" | head -1)"
    scp "${target}:${remote_fail_dir}/score.json" /tmp/portable_remote_fail.json >&2
    local h264_reason
    h264_reason="$(jq -r '.h264.reason // empty' /tmp/portable_remote_fail.json)"
    [[ "${h264_reason}" == *"startup timeout"* ]] \
        || die "stage 4: expected h264.reason ~ 'startup timeout', got: ${h264_reason}"

    printf '\n=== test.sh --portable summary ===\nALL STAGES PASS\n'
}

main() {
    if [[ "${MODE}" == "portable" ]]; then
        portable_main
        return
    fi
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
