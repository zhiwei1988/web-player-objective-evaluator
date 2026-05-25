#!/usr/bin/env bash
# I-frame-only / repeated-frame cheat: cycle a handful of reference PNGs forever.
# Unique watermark numbers stay small → FPS points 0 or 3.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FE_PORT="${FRONTEND_PORT:-8080}"
REPO_ROOT="${EVALUATOR_REPO_ROOT:-$(cd "${ROOT}/../.." && pwd)}"

# Five frames per profile — cycled, never live-decoded. Profile names and
# reference dirs come from lib/profiles.py.
while IFS=$'\t' read -r profile refdir; do
    mkdir -p "${ROOT}/web/${profile}"
    for n in 100 200 300 400 500; do
        printf -v src 'frame_%05d.png' "${n}"
        cp "${REPO_ROOT}/${refdir}/${src}" "${ROOT}/web/${profile}/${src}"
    done
done < <(PYTHONPATH="${REPO_ROOT}" "${REPO_ROOT}/.venv/bin/python" -c "from lib.profiles import PROFILES
for s in PROFILES.values():
    print(f'{s.name}\t{s.reference_dir}')")

cd "${ROOT}"
exec python3 server.py --port "${FE_PORT}" --root "${ROOT}/web"
