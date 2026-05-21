#!/usr/bin/env bash
# One-time host bootstrap for the objective evaluator.
# Idempotent: safe to re-run.
# Supports Ubuntu 24.04 (canonical) and 22.04 (via PPAs for Go 1.22 +
# Python 3.12, which aren't in the 22.04 main archive).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

log()  { printf '[setup] %s\n' "$*" >&2; }
die()  { printf 'setup failed: %s\n' "$*" >&2; exit 1; }

# Detect Ubuntu version once. Empty string for non-Ubuntu / unparseable hosts;
# we fall back to the 24.04 package set in that case.
UBUNTU_VERSION=""
if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    [[ "${ID:-}" == "ubuntu" ]] && UBUNTU_VERSION="${VERSION_ID:-}"
fi

# Common apt packages — version-independent. The Go + Python toolchains are
# appended per-version below.
APT_COMMON=(
    build-essential
    cmake
    autoconf
    automake
    libtool
    pkg-config
    nasm
    yasm
    lsof
    unzip
    git
    ca-certificates
    libcap2-bin
)

case "${UBUNTU_VERSION}" in
    22.04)
        # 22.04 ships Go 1.18 (need 1.22+ for mediamtx v1.9.3) and Python 3.10
        # (project pins are validated against 3.12). Both come from PPAs below.
        APT_PKGS=(
            "${APT_COMMON[@]}"
            golang-1.22
            python3.12
            python3.12-venv
            python3.12-dev
        )
        VENV_PYTHON="python3.12"
        NEED_PPAS=1
        NEED_GO_SYMLINK=1
        ;;
    24.04|"")
        # 24.04 default (and any unrecognised host): use the main-archive
        # toolchain.
        [[ -z "${UBUNTU_VERSION}" ]] \
            && log "warning: could not detect Ubuntu version; using 24.04 package set"
        APT_PKGS=(
            "${APT_COMMON[@]}"
            golang-go
            python3
            python3-venv
            python3-pip
        )
        VENV_PYTHON="python3"
        NEED_PPAS=0
        NEED_GO_SYMLINK=0
        ;;
    *)
        log "warning: unsupported Ubuntu ${UBUNTU_VERSION}; using 24.04 package set"
        APT_PKGS=(
            "${APT_COMMON[@]}"
            golang-go
            python3
            python3-venv
            python3-pip
        )
        VENV_PYTHON="python3"
        NEED_PPAS=0
        NEED_GO_SYMLINK=0
        ;;
esac

# sudo -E preserves http_proxy/https_proxy/no_proxy so apt-get and the
# python-based add-apt-repository can reach archive.ubuntu.com / launchpad.net
# from behind a corporate proxy. For -E to actually keep the vars, sudoers
# must whitelist them (see /etc/sudoers.d/proxy in the project README).
_apt() {
    if [[ $EUID -eq 0 ]]; then
        apt-get "$@"
    elif command -v sudo >/dev/null; then
        sudo -E apt-get "$@"
    else
        die "need root or sudo to run 'apt-get $*'"
    fi
}

_add_ppa() {
    local ppa="$1"
    if [[ $EUID -eq 0 ]]; then
        add-apt-repository -y "${ppa}"
    elif command -v sudo >/dev/null; then
        sudo -E add-apt-repository -y "${ppa}"
    else
        die "need root or sudo to add ppa: ${ppa}"
    fi
}

# 22.04 only: pull in newer Go + Python via PPAs. Idempotent — skips PPAs
# already present in /etc/apt/sources.list.d/.
setup_ppas_2204() {
    if ! command -v add-apt-repository >/dev/null; then
        log "installing software-properties-common (for add-apt-repository)"
        _apt update
        _apt install -y software-properties-common
    fi
    local sources_dir=/etc/apt/sources.list.d
    if ! grep -rqs "longsleep/golang-backports" "${sources_dir}" 2>/dev/null; then
        log "adding ppa:longsleep/golang-backports (Go 1.22+ for 22.04)"
        _add_ppa ppa:longsleep/golang-backports
    else
        log "ppa:longsleep/golang-backports already configured"
    fi
    if ! grep -rqs "deadsnakes/ppa" "${sources_dir}" 2>/dev/null; then
        log "adding ppa:deadsnakes/ppa (Python 3.12 for 22.04)"
        _add_ppa ppa:deadsnakes/ppa
    else
        log "ppa:deadsnakes/ppa already configured"
    fi
}

# 1) Host toolchain via apt. We never auto-sudo without confirmation.
install_apt() {
    log "checking apt toolchain (ubuntu ${UBUNTU_VERSION:-unknown})"
    (( NEED_PPAS )) && setup_ppas_2204
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
    _apt update
    _apt install -y "${missing[@]}"
}

# 22.04 only: the golang-1.22 package installs `go` at
# /usr/lib/go-1.22/bin/go, not on the default PATH. Symlink into
# /usr/local/bin so scripts/build.sh's plain `go build` resolves it without
# any env tweaks.
ensure_go_on_path() {
    (( NEED_GO_SYMLINK )) || return 0
    local target=/usr/lib/go-1.22/bin/go
    local link=/usr/local/bin/go
    [[ -x "${target}" ]] || die "expected go-1.22 at ${target}; apt install failed?"
    if [[ -L "${link}" && "$(readlink -f "${link}")" == "${target}" ]]; then
        log "/usr/local/bin/go already points to go-1.22"
        return 0
    fi
    log "symlinking ${link} -> ${target}"
    if [[ $EUID -eq 0 ]]; then
        ln -sf "${target}" "${link}"
    elif command -v sudo >/dev/null; then
        sudo ln -sf "${target}" "${link}"
    else
        die "cannot create ${link}: need root or sudo"
    fi
}

# 2) Python virtualenv.
make_venv() {
    if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
        log ".venv/ already exists"
        return 0
    fi
    command -v "${VENV_PYTHON}" >/dev/null \
        || die "${VENV_PYTHON} not on PATH after apt install"
    log "creating .venv/ via ${VENV_PYTHON}"
    "${VENV_PYTHON}" -m venv "${REPO_ROOT}/.venv"
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
    ensure_go_on_path
    make_venv
    init_submodules
    printf 'setup ok\n'
}

main "$@"
