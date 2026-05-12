#!/usr/bin/env bash
# Build one zip per test case from test_submissions/src/<case>/ and place it
# next to the source dirs as test_submissions/<case>.zip.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEST_DIR="${ROOT_DIR}/test_submissions"
SRC="${TEST_DIR}/src"

[[ -d "${SRC}" ]] || { echo "missing ${SRC}" >&2; exit 1; }
command -v zip >/dev/null || { echo "zip(1) not on PATH (apt install zip)" >&2; exit 1; }

shopt -s nullglob
for case_dir in "${SRC}"/*/; do
    name="$(basename "${case_dir%/}")"
    out="${TEST_DIR}/${name}.zip"
    rm -f "${out}"
    chmod +x "${case_dir}"start.sh 2>/dev/null || true
    chmod +x "${case_dir}"stop.sh  2>/dev/null || true
    # Build the zip relative to case_dir so the inside layout is clean
    # (start.sh, server.py, web/ ...). Quiet zip output.
    (cd "${case_dir}" && zip -qr "${out}" .)
    printf '  built %s\n' "${out}"
done
shopt -u nullglob

printf 'all test zips built under %s/\n' "${TEST_DIR}"
