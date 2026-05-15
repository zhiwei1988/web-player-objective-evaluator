#!/usr/bin/env bash
# Produce dist/ portable bundle. Assumes scripts/setup.sh + build.sh +
# deploy.sh have already run on this build host. Idempotent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

log()  { printf '[package] %s\n' "$*" >&2; }
die()  { printf 'package failed: %s\n' "$*" >&2; exit 1; }

USE_GZIP=0
while (( $# > 0 )); do
    case "$1" in
        --gzip) USE_GZIP=1 ;;
        -h|--help) echo "Usage: $0 [--gzip]"; exit 0 ;;
        *) die "unknown arg: $1" ;;
    esac
    shift
done

precheck() {
    [[ -x "${ROOT_DIR}/third_party/install/bin/ffmpeg"    ]] || die "missing third_party/install/bin/ffmpeg — run scripts/build.sh"
    [[ -x "${ROOT_DIR}/third_party/install/bin/mediamtx"  ]] || die "missing third_party/install/bin/mediamtx — run scripts/build.sh"
    [[ -x "${ROOT_DIR}/third_party/install/bin/tesseract" ]] || die "missing third_party/install/bin/tesseract — run scripts/build.sh"
    [[ -f "${ROOT_DIR}/third_party/install/share/tessdata/eng.traineddata" ]] || die "missing eng.traineddata — run scripts/build.sh"
    [[ -x "${ROOT_DIR}/.venv/bin/python" ]] || die "missing .venv/bin/python — run scripts/setup.sh && scripts/build.sh"
    compgen -G "${HOME}/.cache/ms-playwright/chromium-*" >/dev/null \
        || die "missing ~/.cache/ms-playwright/chromium-* — run scripts/build.sh"
    [[ -f "${ROOT_DIR}/streams/h264_watermarked.mp4" ]] || die "missing streams — run scripts/deploy.sh"
    [[ -f "${ROOT_DIR}/streams/h265_watermarked.mp4" ]] || die "missing streams — run scripts/deploy.sh"
    compgen -G "${ROOT_DIR}/reference/h264/frame_*.png" >/dev/null \
        || die "missing reference/h264/ — run scripts/deploy.sh"
    compgen -G "${ROOT_DIR}/reference/h265/frame_*.png" >/dev/null \
        || die "missing reference/h265/ — run scripts/deploy.sh"
    command -v docker >/dev/null || die "docker not installed on build host"
    command -v jq     >/dev/null || die "jq not installed on build host"
    if (( ! USE_GZIP )); then
        command -v zstd >/dev/null || die "zstd not installed on build host (use --gzip to fall back)"
    fi
}

stage_playwright() {
    log "staging ~/.cache/ms-playwright/ → ./.playwright/"
    rsync -a --delete "${HOME}/.cache/ms-playwright/" "${ROOT_DIR}/.playwright/"
}

IMAGE_TAG_SHA=""
build_image() {
    IMAGE_TAG_SHA="$(git -C "${ROOT_DIR}" rev-parse --short=12 HEAD)"
    log "docker build -t evaluator-portable:${IMAGE_TAG_SHA} ."
    # --network host: build container shares the host's network namespace so that
    # apt/curl RUN steps can reach proxies bound to 127.0.0.1 (~/.docker/config.json
    # injects HTTP_PROXY / HTTPS_PROXY env). Without this, the proxy URL points to
    # the *container's* loopback instead of the host's.
    docker build --network host -t "evaluator-portable:${IMAGE_TAG_SHA}" "${ROOT_DIR}"
}

DIST_DIR="${ROOT_DIR}/dist"
mkdir_dist() {
    mkdir -p "${DIST_DIR}"
}

IMAGE_ARCHIVE=""
save_image() {
    local out
    if (( USE_GZIP )); then
        out="${DIST_DIR}/evaluator-portable_${IMAGE_TAG_SHA}.tar.gz"
        log "docker save | gzip → ${out}"
        docker save "evaluator-portable:${IMAGE_TAG_SHA}" | gzip > "${out}"
    else
        out="${DIST_DIR}/evaluator-portable_${IMAGE_TAG_SHA}.tar.zst"
        log "docker save | zstd -19 -T0 → ${out}"
        docker save "evaluator-portable:${IMAGE_TAG_SHA}" | zstd -19 -T0 -f -o "${out}"
    fi
    IMAGE_ARCHIVE="${out}"
}

write_manifest() {
    local image_sha image_size git_full ts pw_ver submod_json
    image_sha="$(docker image inspect "evaluator-portable:${IMAGE_TAG_SHA}" --format '{{.Id}}')"
    image_size="$(stat -c %s "${IMAGE_ARCHIVE}")"
    git_full="$(git -C "${ROOT_DIR}" rev-parse HEAD)"
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    pw_ver="$(tr '\n' ' ' < "${ROOT_DIR}/third_party/install/playwright_chromium.version" 2>/dev/null | sed 's/  *$//')"
    submod_json="$(git -C "${ROOT_DIR}" submodule status \
        | awk '{ gsub(/^[ +-]/,"",$1); print "\""$2"\":\""$1"\"" }' \
        | paste -sd, -)"
    cat > "${DIST_DIR}/manifest.json" <<EOF
{
  "git_sha": "${git_full}",
  "build_timestamp": "${ts}",
  "submodule_status": { ${submod_json} },
  "playwright_chromium_version": "${pw_ver}",
  "image_sha256": "${image_sha}",
  "image_size_bytes": ${image_size}
}
EOF
    log "manifest written: ${DIST_DIR}/manifest.json"
}

copy_host_script() {
    cp "${SCRIPT_DIR}/evaluator-host.sh" "${DIST_DIR}/evaluator-host.sh"
    chmod +x "${DIST_DIR}/evaluator-host.sh"
    # Co-locate the lifecycle helper too — evaluator-host.sh sources it.
    cp "${SCRIPT_DIR}/_contestant_lifecycle.sh" "${DIST_DIR}/_contestant_lifecycle.sh"
}

write_readme() {
    cat > "${DIST_DIR}/README.md" <<'EOF'
# 评测器便携包

## 目标机器前置条件

- x86_64 Linux（Ubuntu 24.04 推荐，glibc 兼容即可）
- Docker engine ≥ 20.10 或 rootful podman
- `zstd` 命令行工具（用 `--gzip` 模式打包则改用 `gzip`）
- 操作员可读写当前目录

## 一次性导入

```bash
sha256sum -c SHA256SUMS
zstd -d evaluator-portable_<sha>.tar.zst -o image.tar
docker load < image.tar && rm image.tar
mkdir -p submissions results
```

## 每个 submission 跑一次

```bash
./evaluator-host.sh <team_id> <submission_zip>
```

产出位置：`./results/<team_id>_<timestamp>/`

- `score.json`：可对外的最终分数
- `report.html`：内部审计报告
- `evaluator-host.log` / `evaluator.log`：日志

## 退出码

| 码 | 含义 |
|---|---|
| 0  | 评测完成（含拿 0 分） |
| 1  | 基础设施异常（docker / 端口 / 解压等） |
| 2  | 选手 frontend 起不来；已写极简 score.json |
| 75 | 另一次评测正在进行（flock 互斥） |

## 故障排查

- **镜像未加载**：`docker image inspect $(jq -r .image_sha256 manifest.json)`
- **端口被占**：`ss -lntp | grep -E ':(8080|8554)\b'`
- **sha 不一致**：重 `sha256sum -c SHA256SUMS` 验证；不一致禁止使用

## 清理旧镜像

```bash
docker images evaluator-portable --format '{{.Tag}}' | tail -n +3 \
    | xargs -r -I{} docker rmi evaluator-portable:{}
```
EOF
}

write_sums() {
    (cd "${DIST_DIR}" && sha256sum \
        "$(basename "${IMAGE_ARCHIVE}")" \
        evaluator-host.sh \
        _contestant_lifecycle.sh \
        README.md \
        manifest.json \
        > SHA256SUMS)
}

main() {
    precheck
    mkdir_dist
    stage_playwright
    build_image
    save_image
    write_manifest
    copy_host_script
    write_readme
    write_sums
    local sz; sz="$(numfmt --to=iec --suffix=B "$(stat -c %s "${IMAGE_ARCHIVE}")")"
    log "package ok: ${IMAGE_ARCHIVE} (${sz})"
}

main "$@"
