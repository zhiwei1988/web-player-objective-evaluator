#!/usr/bin/env bash
# Install the supported contestant runtime baseline for Ubuntu 22.04.
#
# Scope:
# - Tools contestants are likely to call from start.sh while serving a web player.
# - Browser/media/build dependencies commonly needed by Node/Python/Go/Rust/C++ apps.
# - A version report that can be published to contestants after installation.
#
# Deliberately out of scope:
# - Docker or other container runtimes. Submissions run as host processes here.
# - System Gradle. Java teams should use a checked-in Gradle wrapper.

set -euo pipefail

NODE_MAJOR="${NODE_MAJOR:-22}"
PNPM_VERSION="${PNPM_VERSION:-9.15.4}"
YARN_VERSION="${YARN_VERSION:-1.22.22}"
RUST_TOOLCHAIN="${RUST_TOOLCHAIN:-stable}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmjs.org/}"
NPM_INSTALL_TIMEOUT_SECONDS="${NPM_INSTALL_TIMEOUT_SECONDS:-600}"
NPM_STRICT_SSL="${NPM_STRICT_SSL:-true}"
NPM_CAFILE="${NPM_CAFILE:-}"

RUNTIME_ROOT="${RUNTIME_ROOT:-/opt/contest-runtime}"
RUSTUP_HOME="${RUSTUP_HOME:-${RUNTIME_ROOT}/rustup}"
CARGO_HOME="${CARGO_HOME:-${RUNTIME_ROOT}/cargo}"
VERSION_REPORT="${VERSION_REPORT:-${RUNTIME_ROOT}/SUPPORTED_TOOLS.txt}"

FORCE=0
REPORT_ONLY=0
ORIGINAL_ARGS=("$@")

# Some competition hosts already have custom build toolchains under /opt in
# PATH. Prefer the distro-managed tools this script installs, especially node,
# npm, and corepack.
export PATH="/usr/bin:/usr/local/bin:/bin:/usr/sbin:/sbin:${PATH}"

log() { printf '[contest-runtime] %s\n' "$*" >&2; }
die() { printf 'contest-runtime failed: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: scripts/install_contestant_runtime_ubuntu2204.sh [--force] [--report-only]

Installs the Ubuntu 22.04 contestant runtime baseline and writes:
  /opt/contest-runtime/SUPPORTED_TOOLS.txt

Environment overrides:
  NODE_MAJOR=22
  PNPM_VERSION=9.15.4
  YARN_VERSION=1.22.22
  RUST_TOOLCHAIN=stable
  NPM_REGISTRY=https://registry.npmjs.org/
  NPM_INSTALL_TIMEOUT_SECONDS=600
  NPM_STRICT_SSL=true
  NPM_CAFILE=/path/to/corporate-ca.pem
  RUNTIME_ROOT=/opt/contest-runtime
EOF
}

while (( $# > 0 )); do
    case "$1" in
        --force) FORCE=1 ;;
        --report-only) REPORT_ONLY=1 ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done

if (( ! REPORT_ONLY && EUID != 0 )); then
    if command -v sudo >/dev/null 2>&1; then
        exec sudo -E bash "$0" "${ORIGINAL_ARGS[@]}"
    fi
    die "need root or sudo to install packages"
fi

apt_install() {
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
}

ensure_ubuntu_2204() {
    [[ -r /etc/os-release ]] || die "cannot read /etc/os-release"
    # shellcheck disable=SC1091
    . /etc/os-release
    if [[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "22.04" ]]; then
        return 0
    fi
    (( FORCE )) && {
        log "warning: expected Ubuntu 22.04, got ${PRETTY_NAME:-unknown}; continuing due to --force"
        return 0
    }
    die "expected Ubuntu 22.04, got ${PRETTY_NAME:-unknown}; pass --force to override"
}

bootstrap_apt() {
    log "installing apt repository prerequisites"
    apt-get update
    apt_install ca-certificates curl wget gnupg lsb-release software-properties-common
}

add_ppa_once() {
    local marker="$1"
    local ppa="$2"
    if grep -rqs "${marker}" /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
        log "${ppa} already configured"
        return 0
    fi
    log "adding ${ppa}"
    add-apt-repository -y "${ppa}"
}

configure_nodesource() {
    local keyring=/usr/share/keyrings/nodesource.gpg
    local source=/etc/apt/sources.list.d/nodesource.list
    log "configuring NodeSource Node.js ${NODE_MAJOR}.x repository"
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor --yes -o "${keyring}"
    printf 'deb [signed-by=%s] https://deb.nodesource.com/node_%s.x nodistro main\n' \
        "${keyring}" "${NODE_MAJOR}" > "${source}"
}

configure_google_chrome() {
    if [[ "$(dpkg --print-architecture)" != "amd64" ]]; then
        log "skipping Google Chrome repository: only amd64 packages are published"
        return 0
    fi
    local keyring=/usr/share/keyrings/google-linux.gpg
    local source=/etc/apt/sources.list.d/google-chrome.list
    log "configuring Google Chrome stable repository"
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor --yes -o "${keyring}"
    printf 'deb [arch=amd64 signed-by=%s] http://dl.google.com/linux/chrome/deb/ stable main\n' \
        "${keyring}" > "${source}"
}

install_apt_tools() {
    local packages=(
        bash
        build-essential
        gcc
        g++
        clang
        lld
        gdb
        make
        cmake
        ninja-build
        pkg-config
        autoconf
        automake
        libtool
        nasm
        yasm
        git
        git-lfs
        zip
        unzip
        xz-utils
        jq
        lsof
        net-tools
        iproute2
        procps
        python3.12
        python3.12-venv
        python3.12-dev
        python3-pip
        python-is-python3
        nodejs
        golang-1.22
        openjdk-17-jdk
        maven
        ffmpeg
        fonts-liberation
        libasound2
        libatk-bridge2.0-0
        libatk1.0-0
        libcairo2
        libcups2
        libdbus-1-3
        libdrm2
        libgbm1
        libglib2.0-0
        libgl1
        libgtk-3-0
        libnspr4
        libnss3
        libpango-1.0-0
        libu2f-udev
        libvulkan1
        libx11-xcb1
        libxcomposite1
        libxdamage1
        libxkbcommon0
        libxrandr2
        libxshmfence1
        xdg-utils
    )

    if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then
        packages+=(google-chrome-stable)
    fi

    log "installing contestant apt toolchain"
    apt-get update
    apt_install "${packages[@]}"
}

install_go_symlinks() {
    local tool
    for tool in go gofmt; do
        local target="/usr/lib/go-1.22/bin/${tool}"
        [[ -x "${target}" ]] || die "expected ${target} after installing golang-1.22"
        ln -sf "${target}" "/usr/local/bin/${tool}"
    done
}

npm_global_install() {
    local npm_cmd=/usr/bin/npm
    [[ -x "${npm_cmd}" ]] || npm_cmd="$(command -v npm)"
    local timeout_cmd=()
    if command -v timeout >/dev/null 2>&1; then
        timeout_cmd=(timeout --foreground "${NPM_INSTALL_TIMEOUT_SECONDS}s")
    else
        log "warning: timeout(1) not found; npm install cannot be time-boxed"
    fi

    "${npm_cmd}" config set registry "${NPM_REGISTRY}"
    "${npm_cmd}" config set fetch-retries 5
    "${npm_cmd}" config set fetch-retry-factor 2
    "${npm_cmd}" config set fetch-retry-mintimeout 20000
    "${npm_cmd}" config set fetch-retry-maxtimeout 120000
    "${npm_cmd}" config set progress true
    "${npm_cmd}" config set loglevel http
    "${npm_cmd}" config set strict-ssl "${NPM_STRICT_SSL}"
    if [[ -n "${NPM_CAFILE}" ]]; then
        [[ -r "${NPM_CAFILE}" ]] || die "NPM_CAFILE is not readable: ${NPM_CAFILE}"
        "${npm_cmd}" config set cafile "${NPM_CAFILE}"
        export NODE_EXTRA_CA_CERTS="${NPM_CAFILE}"
    fi

    log "npm registry: ${NPM_REGISTRY}"
    log "npm strict-ssl: ${NPM_STRICT_SSL}"
    [[ -z "${NPM_CAFILE}" ]] || log "npm cafile: ${NPM_CAFILE}"
    log "running npm ping before package installation"
    local ping_log
    ping_log="$(mktemp)"
    if ! "${timeout_cmd[@]}" "${npm_cmd}" ping \
        --registry "${NPM_REGISTRY}" \
        --loglevel=http 2>&1 | tee "${ping_log}"; then
        if grep -q "SELF_SIGNED_CERT_IN_CHAIN" "${ping_log}"; then
            rm -f "${ping_log}"
            die "npm TLS verification failed with SELF_SIGNED_CERT_IN_CHAIN; install your corporate CA and pass NPM_CAFILE=/path/to/ca.pem, or temporarily rerun with NPM_STRICT_SSL=false"
        fi
        rm -f "${ping_log}"
        die "npm registry is unreachable: ${NPM_REGISTRY}; try NPM_REGISTRY=https://registry.npmmirror.com"
    fi
    rm -f "${ping_log}"

    local attempt
    for attempt in 1 2 3; do
        log "npm install attempt ${attempt}/3: $*"
        if "${timeout_cmd[@]}" "${npm_cmd}" install -g --force \
            --registry "${NPM_REGISTRY}" \
            --loglevel=http \
            --progress=true \
            "$@"; then
            hash -r
            return 0
        fi
        local status=$?
        if [[ "${status}" -eq 124 ]]; then
            log "npm install attempt ${attempt} timed out after ${NPM_INSTALL_TIMEOUT_SECONDS}s"
        fi
        (( attempt < 3 )) || break
        log "npm install attempt ${attempt} failed with status ${status}; retrying"
        sleep $((attempt * 5))
    done
    die "npm global install failed after retries: $*; try NPM_REGISTRY=https://registry.npmmirror.com"
}

install_node_package_managers() {
    log "installing Node package managers"
    npm_global_install \
        "pnpm@${PNPM_VERSION}" \
        "yarn@${YARN_VERSION}" \
        serve@14 \
        http-server@14
}

write_rust_wrapper() {
    local tool="$1"
    local target="/usr/local/bin/${tool}"
    cat > "${target}" <<EOF
#!/usr/bin/env bash
export RUSTUP_HOME="${RUSTUP_HOME}"
export CARGO_HOME="${CARGO_HOME}"
exec "${CARGO_HOME}/bin/${tool}" "\$@"
EOF
    chmod 0755 "${target}"
}

install_rust() {
    log "installing Rust ${RUST_TOOLCHAIN} under ${RUNTIME_ROOT}"
    mkdir -p "${RUNTIME_ROOT}"
    if [[ ! -x "${CARGO_HOME}/bin/rustup" ]]; then
        curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs \
            | CARGO_HOME="${CARGO_HOME}" RUSTUP_HOME="${RUSTUP_HOME}" \
                sh -s -- -y --profile default --default-toolchain "${RUST_TOOLCHAIN}"
    else
        CARGO_HOME="${CARGO_HOME}" RUSTUP_HOME="${RUSTUP_HOME}" \
            "${CARGO_HOME}/bin/rustup" update "${RUST_TOOLCHAIN}"
    fi
    CARGO_HOME="${CARGO_HOME}" RUSTUP_HOME="${RUSTUP_HOME}" \
        "${CARGO_HOME}/bin/rustup" component add rustfmt clippy

    local tool
    for tool in rustup rustc cargo rustfmt clippy-driver; do
        write_rust_wrapper "${tool}"
    done
    chmod -R a+rX "${RUNTIME_ROOT}"
}

write_profile() {
    cat > /etc/profile.d/contest-runtime.sh <<EOF
export RUSTUP_HOME="${RUSTUP_HOME}"
export CARGO_HOME="${CARGO_HOME}"
export PATH="/usr/local/bin:${CARGO_HOME}/bin:\${PATH}"
EOF
    chmod 0644 /etc/profile.d/contest-runtime.sh
}

report_cmd() {
    local label="$1"
    shift
    local executable="$1"
    shift
    local output
    if command -v "${executable}" >/dev/null 2>&1; then
        if (( $# > 0 )) && [[ "$1" == "${executable}" ]]; then
            shift
        fi
        output="$("${executable}" "$@" 2>&1 | head -n 1 || true)"
        printf '%-24s %s\n' "${label}" "${output:-installed}"
    else
        printf '%-24s %s\n' "${label}" "not installed"
    fi
}

print_report() {
    {
        printf '# Supported contestant runtime\n'
        printf 'Generated: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        if [[ -r /etc/os-release ]]; then
            # shellcheck disable=SC1091
            . /etc/os-release
            printf 'OS: %s\n' "${PRETTY_NAME:-unknown}"
        fi
        printf '\n'
        printf 'Submission entrypoint: ./start.sh\n'
        printf 'Frontend port: ${FRONTEND_PORT} (8080 during evaluation)\n'
        printf 'RTSP inputs: rtsp://127.0.0.1:554/test/h265_2560_1440 and rtsp://127.0.0.1:554/test/h265_3840_2160\n'
        printf 'Container runtimes: not installed/supported for contestant submissions\n'
        printf 'Gradle: use a project-provided ./gradlew wrapper\n'
        printf '\n'
        printf '%-24s %s\n' 'Tool' 'Version'
        printf '%-24s %s\n' '----' '-------'
        report_cmd 'glibc / libc' ldd ldd --version
        report_cmd 'bash' bash bash --version
        report_cmd 'git' git git --version
        report_cmd 'git-lfs' git-lfs git-lfs version
        report_cmd 'gcc' gcc gcc --version
        report_cmd 'g++' g++ g++ --version
        report_cmd 'clang' clang clang --version
        report_cmd 'cmake' cmake cmake --version
        report_cmd 'ninja' ninja ninja --version
        report_cmd 'make' make make --version
        report_cmd 'pkg-config' pkg-config pkg-config --version
        report_cmd 'python3.12' python3.12 python3.12 --version
        report_cmd 'python' python python --version
        report_cmd 'pip (python3)' python3 python3 -m pip --version
        report_cmd 'node' node node --version
        report_cmd 'npm' npm npm --version
        report_cmd 'corepack' corepack corepack --version
        report_cmd 'pnpm' pnpm pnpm --version
        report_cmd 'yarn' yarn yarn --version
        report_cmd 'serve' serve serve --version
        report_cmd 'http-server' http-server http-server --version
        report_cmd 'go' go go version
        report_cmd 'gofmt (Go toolchain)' go go version
        report_cmd 'rustc' rustc rustc --version
        report_cmd 'cargo' cargo cargo --version
        report_cmd 'rustfmt' rustfmt rustfmt --version
        report_cmd 'clippy' clippy-driver clippy-driver --version
        report_cmd 'java' java java -version
        report_cmd 'javac' javac javac -version
        report_cmd 'maven' mvn mvn --version
        report_cmd 'ffmpeg' ffmpeg ffmpeg -version
        report_cmd 'ffprobe' ffprobe ffprobe -version
        report_cmd 'google-chrome' google-chrome google-chrome --version
        report_cmd 'jq' jq jq --version
        report_cmd 'zip' zip zip --version
        report_cmd 'unzip' unzip unzip -v
    }
}

main() {
    if (( REPORT_ONLY )); then
        print_report
        return 0
    fi

    ensure_ubuntu_2204
    bootstrap_apt
    add_ppa_once 'deadsnakes/ppa' 'ppa:deadsnakes/ppa'
    add_ppa_once 'longsleep/golang-backports' 'ppa:longsleep/golang-backports'
    configure_nodesource
    configure_google_chrome
    install_apt_tools
    install_go_symlinks
    git lfs install --system --skip-repo
    install_node_package_managers
    install_rust
    write_profile

    mkdir -p "${RUNTIME_ROOT}"
    print_report | tee "${VERSION_REPORT}"
    log "version report written to ${VERSION_REPORT}"
}

main "$@"
