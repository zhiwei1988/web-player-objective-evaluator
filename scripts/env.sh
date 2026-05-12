# shellcheck shell=bash
# Source (do not execute) this file to prepend the evaluator's
# third_party/install/ prefix onto PATH/LD_LIBRARY_PATH/PKG_CONFIG_PATH, then
# activate the Python venv at .venv/.
#
# Callers may export ROOT_DIR before sourcing; otherwise it is derived from
# BASH_SOURCE so the script works regardless of cwd.

if [[ -z "${ROOT_DIR:-}" ]]; then
    ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

_install="${ROOT_DIR}/third_party/install"

export PATH="${_install}/bin:${PATH}"
export LD_LIBRARY_PATH="${_install}/lib:${LD_LIBRARY_PATH:-}"
export PKG_CONFIG_PATH="${_install}/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
# tesseract needs to find its language data.
export TESSDATA_PREFIX="${_install}/share/tessdata"

# Activate venv if present. We intentionally don't deactivate on script exit;
# scripts that source this run to completion and exit with the venv active.
if [[ -f "${ROOT_DIR}/.venv/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "${ROOT_DIR}/.venv/bin/activate"
fi

unset _install
