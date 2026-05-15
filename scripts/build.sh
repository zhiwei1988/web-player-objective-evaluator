#!/usr/bin/env bash
# Build every third_party submodule from source into third_party/install/.
# Idempotent: per-step output staleness check skips work when nothing changed.
# Pass --clean to force a from-scratch rebuild.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
THIRD_PARTY="${ROOT_DIR}/third_party"
INSTALL_PREFIX="${THIRD_PARTY}/install"
JOBS="$(nproc)"
CLEAN=0

log()  { printf '[build] %s\n' "$*" >&2; }
die()  { printf 'build failed: %s\n' "$*" >&2; exit 1; }

# Argument parsing.
while (( $# > 0 )); do
    case "$1" in
        --clean) CLEAN=1 ;;
        -h|--help) echo "Usage: $0 [--clean]"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done

if (( CLEAN )); then
    log "--clean: wiping install prefix and per-submodule build state"
    rm -rf "${INSTALL_PREFIX}"
    # Per-submodule cleanups; ignore errors for ones not yet configured.
    git -C "${THIRD_PARTY}/leptonica" clean -fdx >/dev/null 2>&1 || true
    git -C "${THIRD_PARTY}/tesseract" clean -fdx >/dev/null 2>&1 || true
    git -C "${THIRD_PARTY}/libdmtx"   clean -fdx >/dev/null 2>&1 || true
    git -C "${THIRD_PARTY}/x264"      clean -fdx >/dev/null 2>&1 || true
    git -C "${THIRD_PARTY}/x265"      clean -fdx >/dev/null 2>&1 || true
    git -C "${THIRD_PARTY}/ffmpeg"    clean -fdx >/dev/null 2>&1 || true
    git -C "${THIRD_PARTY}/mediamtx"  clean -fdx >/dev/null 2>&1 || true
fi

mkdir -p "${INSTALL_PREFIX}"/{bin,lib,include}

# Exported so child configure/make picks up our prefix instead of system libs.
export PATH="${INSTALL_PREFIX}/bin:${PATH}"
export LD_LIBRARY_PATH="${INSTALL_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
export PKG_CONFIG_PATH="${INSTALL_PREFIX}/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
export CPPFLAGS="-I${INSTALL_PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${INSTALL_PREFIX}/lib ${LDFLAGS:-}"

# Staleness check: $1 = output marker file, $2... = source paths.
# Returns 0 (skip) if marker exists and is newer than every source path.
is_fresh() {
    local marker="$1"; shift
    [[ -e "${marker}" ]] || return 1
    local src
    for src in "$@"; do
        [[ -e "${src}" ]] || continue
        if [[ "${src}" -nt "${marker}" ]]; then
            return 1
        fi
    done
    return 0
}

build_leptonica() {
    local marker="${INSTALL_PREFIX}/lib/libleptonica.so"
    local src="${THIRD_PARTY}/leptonica"
    if is_fresh "${marker}" "${src}/src" "${src}/configure.ac"; then
        log "leptonica: up to date"
        return 0
    fi
    log "leptonica: configure + make"
    (
        cd "${src}"
        [[ -x configure ]] || ./autogen.sh
        ./configure --prefix="${INSTALL_PREFIX}" --disable-static --disable-dependency-tracking
        make -j"${JOBS}"
        make install
    )
}

build_tesseract() {
    local marker="${INSTALL_PREFIX}/bin/tesseract"
    local src="${THIRD_PARTY}/tesseract"
    if is_fresh "${marker}" "${src}/src" "${src}/configure.ac"; then
        log "tesseract: up to date"
        return 0
    fi
    log "tesseract: configure + make (depends on leptonica)"
    (
        cd "${src}"
        [[ -x configure ]] || ./autogen.sh
        ./configure --prefix="${INSTALL_PREFIX}" \
                    --disable-static \
                    --disable-dependency-tracking \
                    --disable-doc \
                    LIBLEPT_HEADERSDIR="${INSTALL_PREFIX}/include"
        make -j"${JOBS}"
        make install
    )
    # Tesseract needs at least the English traineddata at runtime.
    local tessdata="${INSTALL_PREFIX}/share/tessdata"
    if [[ ! -e "${tessdata}/eng.traineddata" ]]; then
        log "tesseract: fetching eng.traineddata"
        mkdir -p "${tessdata}"
        # If offline, this will fail and the operator can drop the file in place manually.
        curl -fsSL -o "${tessdata}/eng.traineddata" \
            https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata \
            || die "could not fetch eng.traineddata (offline?); place it under ${tessdata}/ manually"
    fi
}

build_libdmtx() {
    local marker="${INSTALL_PREFIX}/lib/libdmtx.so"
    local src="${THIRD_PARTY}/libdmtx"
    if is_fresh "${marker}" "${src}"/*.c "${src}/configure.ac"; then
        log "libdmtx: up to date"
        return 0
    fi
    log "libdmtx: configure + make"
    (
        cd "${src}"
        [[ -x configure ]] || ./autogen.sh
        ./configure --prefix="${INSTALL_PREFIX}" --disable-static
        make -j"${JOBS}"
        make install
    )
}

build_x264() {
    local marker="${INSTALL_PREFIX}/lib/libx264.so"
    local src="${THIRD_PARTY}/x264"
    if is_fresh "${marker}" "${src}/encoder" "${src}/common" "${src}/configure"; then
        log "x264: up to date"
        return 0
    fi
    log "x264: configure + make"
    (
        cd "${src}"
        ./configure --prefix="${INSTALL_PREFIX}" --enable-shared --disable-cli
        make -j"${JOBS}"
        make install
    )
}

build_x265() {
    local marker="${INSTALL_PREFIX}/lib/libx265.so"
    local src="${THIRD_PARTY}/x265"
    if is_fresh "${marker}" "${src}/source"; then
        log "x265: up to date"
        return 0
    fi
    log "x265: cmake + make"
    (
        local build="${src}/build/linux"
        mkdir -p "${build}"
        cd "${build}"
        cmake -G "Unix Makefiles" \
              -DCMAKE_INSTALL_PREFIX="${INSTALL_PREFIX}" \
              -DENABLE_SHARED=ON \
              -DENABLE_CLI=OFF \
              ../../source
        make -j"${JOBS}"
        make install
    )
}

build_ffmpeg() {
    local marker="${INSTALL_PREFIX}/bin/ffmpeg"
    local src="${THIRD_PARTY}/ffmpeg"
    if is_fresh "${marker}" "${src}/libavcodec" "${src}/libavformat" "${src}/configure"; then
        log "ffmpeg: up to date"
        return 0
    fi
    log "ffmpeg: configure + make"
    # Configure flags chosen for the evaluator's needs only:
    #   --enable-gpl: required by libx264/libx265
    #   --enable-libx264, --enable-libx265: H.264 / H.265 encode (from third_party/install)
    #   --disable-doc: no man pages / texinfo
    #   --disable-debug: smaller binaries
    # Network protocols ARE enabled (default) because the runOnDemand command in
    # rtsp_server/mediamtx.yml needs ffmpeg to *output* over rtsp://, which
    # requires the RTSP muxer + tcp protocol. Earlier `--disable-network` strips
    # all of them and produces "Requested output format 'rtsp' is not known".
    # PKG_CONFIG_PATH is already pointing at our prefix so the source-built
    # libx264/libx265 are picked up, not anything system-wide.
    pkg-config --exists x264 || die "ffmpeg: x264 not found in ${PKG_CONFIG_PATH} (build_x264 failed?)"
    pkg-config --exists x265 || die "ffmpeg: x265 not found in ${PKG_CONFIG_PATH} (build_x265 failed?)"
    (
        cd "${src}"
        ./configure --prefix="${INSTALL_PREFIX}" \
                    --enable-gpl \
                    --enable-libx264 \
                    --enable-libx265 \
                    --disable-doc \
                    --disable-debug
        make -j"${JOBS}"
        make install
    )
}

build_mediamtx() {
    local marker="${INSTALL_PREFIX}/bin/mediamtx"
    local src="${THIRD_PARTY}/mediamtx"
    if is_fresh "${marker}" "${src}/main.go" "${src}/go.sum"; then
        log "mediamtx: up to date"
        # Capability is applied to the binary, not the source — re-check on
        # every run so an existing binary built before the setcap step was
        # added still gets the capability.
        apply_mediamtx_cap
        return 0
    fi
    log "mediamtx: go generate (embeds VERSION + downloads hls.min.js)"
    # MediaMTX uses //go:embed for two files that are produced at build time:
    #   internal/core/VERSION                (written by versiongetter, falls back to v0.0.0)
    #   internal/servers/hls/hls.min.js      (downloaded from GitHub by hlsjsdownloader)
    # Both must exist before `go build`. The versiongetter expects a regular .git
    # directory; submodules have .git as a file, so we write VERSION ourselves
    # with the pinned tag and skip the versiongetter codepath. hls.min.js still
    # needs network access (GitHub release artifact, ~1MB).
    (
        cd "${src}"
        # Pin VERSION to whatever tag git submodule status reports, so the
        # mediamtx binary self-reports a sensible version. Fall back to v1.9.3
        # if git is unhappy with the submodule's .git file.
        local mediamtx_version
        mediamtx_version="$(git describe --tags --always 2>/dev/null || echo 'v1.9.3')"
        echo -n "${mediamtx_version}" > internal/core/VERSION
        log "  VERSION=${mediamtx_version}"
        # Only run the hls.js downloader — leave VERSION alone.
        (cd internal/servers/hls && go run ./hlsjsdownloader)
    )
    log "mediamtx: go build"
    (
        cd "${src}"
        go build -o "${INSTALL_PREFIX}/bin/mediamtx" .
    )
    apply_mediamtx_cap
}

# MediaMTX listens on :554, a privileged port (<1024). Granting
# CAP_NET_BIND_SERVICE on the binary lets it bind that port without running
# as root. The container path achieves the same via --cap-add=NET_BIND_SERVICE
# in scripts/evaluator-host.sh; this step covers the host-native path
# (scripts/evaluator-local.sh + scripts/deploy.sh).
apply_mediamtx_cap() {
    local bin="${INSTALL_PREFIX}/bin/mediamtx"
    command -v setcap >/dev/null \
        || die "setcap not on PATH; install libcap2-bin and re-run"
    # Skip if already applied so re-runs don't prompt for sudo unnecessarily.
    if getcap "${bin}" 2>/dev/null | grep -q "cap_net_bind_service=ep"; then
        log "mediamtx: cap_net_bind_service already set"
        return 0
    fi
    log "applying cap_net_bind_service to mediamtx (requires sudo)"
    if [[ $EUID -eq 0 ]]; then
        setcap cap_net_bind_service=+ep "${bin}"
    else
        sudo -n setcap cap_net_bind_service=+ep "${bin}" 2>/dev/null \
            || sudo setcap cap_net_bind_service=+ep "${bin}" \
            || die "setcap on mediamtx failed; cannot bind :554 natively"
    fi
    getcap "${bin}"
}

# Register third_party/install/lib with the system dynamic linker cache.
# Without this, mediamtx (which has CAP_NET_BIND_SERVICE via setcap) runs in
# secure-exec mode and the kernel strips LD_LIBRARY_PATH on exec; any ffmpeg
# subprocess it spawns then fails to load libx264/libx265/libdmtx from the
# source-built prefix. Registration via /etc/ld.so.conf.d/ makes the linker
# find them without env vars. Mirrors the Dockerfile's runtime-stage step.
apply_ldconfig() {
    local conf=/etc/ld.so.conf.d/evaluator.conf
    local libdir="${INSTALL_PREFIX}/lib"
    # Skip if the conf already points at our libdir AND the cache resolves a
    # known library (libx264). Re-runs stay silent on healthy hosts.
    if [[ -r "${conf}" ]] && grep -qx "${libdir}" "${conf}" \
            && ldconfig -p 2>/dev/null | grep -q "${libdir}/libx264"; then
        log "ldconfig: ${libdir} already registered"
        return 0
    fi
    log "registering ${libdir} with ldconfig (requires sudo)"
    if [[ $EUID -eq 0 ]]; then
        printf '%s\n' "${libdir}" > "${conf}"
        ldconfig
    else
        sudo -n bash -c "printf '%s\n' '${libdir}' > '${conf}' && ldconfig" 2>/dev/null \
            || sudo bash -c "printf '%s\n' '${libdir}' > '${conf}' && ldconfig" \
            || die "ldconfig registration failed; setcap'd mediamtx will not find libx264 at runtime"
    fi
}

install_python() {
    log "pip install -r requirements.txt"
    "${ROOT_DIR}/.venv/bin/pip" install --upgrade pip wheel
    "${ROOT_DIR}/.venv/bin/pip" install -r "${ROOT_DIR}/requirements.txt"
}

install_chromium() {
    log "playwright install chromium"
    "${ROOT_DIR}/.venv/bin/python" -m playwright install chromium
    # Record the bundled Chromium revision for later report inclusion.
    local rev
    rev="$("${ROOT_DIR}/.venv/bin/python" -c \
        'from playwright.sync_api import sync_playwright;
import json
with sync_playwright() as p:
    info = p.chromium.executable_path
    print(info)' 2>/dev/null || true)"
    local ver
    ver="$("${ROOT_DIR}/.venv/bin/python" -c \
        'import importlib.metadata; print(importlib.metadata.version("playwright"))' \
        2>/dev/null || true)"
    {
        printf 'playwright_version=%s\n' "${ver}"
        printf 'chromium_executable=%s\n' "${rev}"
        # The build string is what ends up in user-agent / about:version.
        if [[ -n "${rev}" && -x "${rev}" ]]; then
            "${rev}" --version 2>/dev/null || true
        fi
    } > "${INSTALL_PREFIX}/playwright_chromium.version"
}

main() {
    [[ -d "${ROOT_DIR}/.venv" ]] \
        || die ".venv/ missing — run scripts/setup.sh first"
    [[ -e "${THIRD_PARTY}/leptonica/.git" ]] \
        || die "submodules not initialized — run scripts/setup.sh first"

    build_leptonica
    build_tesseract
    build_libdmtx
    build_x264
    build_x265
    build_ffmpeg
    build_mediamtx
    apply_ldconfig
    install_python
    install_chromium

    printf 'build ok\n'
}

main "$@"
