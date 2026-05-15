#!/usr/bin/env bash
# Target-host operator entry. Reads manifest.json adjacent to this script
# (or in --manifest path) for image_sha256; pulls contestant zip apart on
# the host; runs evaluator.sh inside the portable OCI container via
# `docker run --network host`.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ROOT_DIR is the operator's current working directory. submissions/ and
# results/ land here. This lets target hosts deploy with a layout like
#   ~/evaluator-bundle/{evaluator-host.sh,manifest.json,...}
#   ~/evaluator-runs/    <- operator cd's here, runs evaluator-host.sh
# without coupling the run dir to wherever the script binary lives.
ROOT_DIR="${PWD}"
MANIFEST="${SCRIPT_DIR}/manifest.json"
[[ -f "${MANIFEST}" ]] || MANIFEST="${ROOT_DIR}/dist/manifest.json"

USE_ROOT=0
ARGS=()
while (( $# > 0 )); do
    case "$1" in
        --root) USE_ROOT=1 ;;
        --manifest) MANIFEST="$2"; shift ;;
        -h|--help) cat >&2 <<'EOF'
Usage: evaluator-host.sh [--root] [--manifest <path>] <team_id> <submission_zip>
EOF
            exit 0 ;;
        *) ARGS+=("$1") ;;
    esac
    shift
done
set -- "${ARGS[@]}"
(( $# >= 2 )) || { echo "usage: evaluator-host.sh <team_id> <submission_zip>" >&2; exit 64; }
TEAM_ID="$1"
SUBMISSION_ZIP="$(readlink -f "$2" 2>/dev/null || echo "$2")"

# shellcheck source=_contestant_lifecycle.sh
source "${SCRIPT_DIR}/_contestant_lifecycle.sh"

[[ -f "${MANIFEST}" ]] || clx_die "manifest.json not found at ${MANIFEST}"
command -v jq     >/dev/null || clx_die "jq required on target host"
command -v docker >/dev/null || clx_die "docker required on target host"

IMAGE_SHA="$(jq -r .image_sha256 "${MANIFEST}")"
[[ -n "${IMAGE_SHA}" && "${IMAGE_SHA}" != "null" ]] || clx_die "manifest.json missing image_sha256"
IMAGE_REF="${IMAGE_SHA}"   # docker image inspect accepts the full sha256:... id

docker image inspect "${IMAGE_REF}" >/dev/null 2>&1 \
    || clx_die "image ${IMAGE_REF} not loaded — run: docker load < evaluator-portable_<sha>.tar.zst"
docker info >/dev/null 2>&1 || clx_die "docker daemon unreachable"

clx_acquire_lock
# Container will bind 8554 (MediaMTX) and contestant frontend lives on 8080.
clx_precheck_ports 8080 8554
clx_prepare_run_dir "${TEAM_ID}"

exec 3>&1
exec > >(tee -a "${RUN_DIR}/evaluator-host.log" >&2) 2>&1

DOCKER_USER_FLAG=()
(( ! USE_ROOT )) && DOCKER_USER_FLAG=(--user "$(id -u):$(id -g)")

cleanup() {
    local rc=$?
    clx_cleanup_contestant
    # Belt-and-braces: kill any container still tied to this image.
    docker ps -q --filter "ancestor=${IMAGE_REF}" 2>/dev/null | xargs -r docker kill 2>/dev/null || true
    # If we set up a RUN_DIR but never produced a score.json, write a
    # failure score via a short docker run so callers always see one.
    if [[ -n "${RUN_DIR:-}" && -d "${RUN_DIR}" && ! -f "${RUN_DIR}/score.json" ]]; then
        docker run --rm "${DOCKER_USER_FLAG[@]}" \
            -v "${RUN_DIR}:/work/results/${RESULTS_SUBDIR}:rw" \
            --entrypoint /work/.venv/bin/python \
            "${IMAGE_REF}" \
            /work/scorer.py \
                --output "/work/results/${RESULTS_SUBDIR}/score.json" \
                --report "/work/results/${RESULTS_SUBDIR}/report.html" \
                --install-prefix /work/third_party/install \
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
    clx_log "contestant frontend never became ready — writing failure score via container"
    docker run --rm "${DOCKER_USER_FLAG[@]}" \
        -v "${RUN_DIR}:/work/results/${RESULTS_SUBDIR}:rw" \
        --entrypoint /work/.venv/bin/python \
        "${IMAGE_REF}" \
        /work/scorer.py \
            --output "/work/results/${RESULTS_SUBDIR}/score.json" \
            --report "/work/results/${RESULTS_SUBDIR}/report.html" \
            --install-prefix /work/third_party/install \
            --failure-reason "contestant_frontend_unavailable" \
        > /dev/null 2>&1 || true
    exit 2
fi

clx_log "invoking evaluator container"
docker run --rm --network host "${DOCKER_USER_FLAG[@]}" \
    -v "${RUN_DIR}:/work/results/${RESULTS_SUBDIR}:rw" \
    "${IMAGE_REF}" \
    "${TEAM_ID}" "${RESULTS_SUBDIR}"
