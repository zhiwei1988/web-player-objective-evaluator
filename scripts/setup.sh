#!/usr/bin/env bash
# One-time host bootstrap for the objective evaluator.
# Idempotent: safe to re-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

log()  { printf '[setup] %s\n' "$*" >&2; }
die()  { printf 'setup failed: %s\n' "$*" >&2; exit 1; }

# 1) Host toolchain via apt. We never auto-sudo without confirmation.
APT_PKGS=(
    build-essential
    cmake
    autoconf
    automake
    libtool
    pkg-config
    nasm
    yasm
    golang-go
    python3
    python3-venv
    python3-pip
    lsof
    unzip
    git
    ca-certificates
    libcap2-bin
)

install_apt() {
    log "checking apt toolchain"
    local missing=()
    for pkg in "${APT_PKGS[@]}"; do
        if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
            missing+=("${pkg}")
        fi
    done
    if (( ${#missing[@]} == 0 )); then
        log "apt toolchain already installed"
        return 0
    fi
    log "missing: ${missing[*]}"
    if [[ $EUID -ne 0 ]]; then
        if ! command -v sudo >/dev/null; then
            die "missing apt packages and no sudo: install manually with apt-get install ${missing[*]}"
        fi
        sudo apt-get update
        sudo apt-get install -y "${missing[@]}"
    else
        apt-get update
        apt-get install -y "${missing[@]}"
    fi
}

# 2) Python virtualenv.
make_venv() {
    if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
        log ".venv/ already exists"
        return 0
    fi
    log "creating .venv/"
    python3 -m venv "${REPO_ROOT}/.venv"
    "${REPO_ROOT}/.venv/bin/python" -m pip install --upgrade pip wheel
}

# 3) Submodules.
init_submodules() {
    log "initializing git submodules"
    cd "${REPO_ROOT}"
    git submodule update --init --recursive
}

main() {
    install_apt
    make_venv
    init_submodules
    printf 'setup ok\n'
}

main "$@"
