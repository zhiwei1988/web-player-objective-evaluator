## Why

每台评测服务器目前都必须独立跑 `setup.sh`（apt 装 ~12 个包、venv、submodule init）、`build.sh`（~30 min 源码编译 ffmpeg / mediamtx / tesseract / leptonica / libdmtx / x264 / x265 + pip install + playwright install），并且在构建阶段需要外网下载 `eng.traineddata` 和 Playwright pinned Chromium。这是新评测机上线、内网/离线评测环境部署、以及多机并行评测的硬门槛。把构建后的评测框架封装成 OCI 镜像后，框架本身不再是各台目标机的本地问题：任何装了 docker 的服务器都能凭 `docker load && evaluator-host.sh team_id submission.zip` 直接进入评测状态。选手合约和核心 Python 流水线（runner / analyzer / scorer / report）零变更。

## What Changes

**评测器入口拆责**
- From: `scripts/evaluator.sh` 一脚本通吃宿主侧（unzip / start.sh / stop.sh / 端口清理）和评测主体（MediaMTX / runner / analyzer / scorer / report）
- To: `scripts/evaluator-host.sh`（宿主侧、操作员入口、含 flock 互斥与 image-sha 锁定）+ `scripts/evaluator.sh`（仅评测主体，预期在容器内被 entrypoint 调起）+ `scripts/evaluator-local.sh`（构建机本地开发 shortcut，非 docker 路径）
- Reason: 把"宿主动作"与"评测器主体"沿容器边界切开，使后者可以装进镜像
- Impact: non-breaking 选手合约；breaking 现有 `evaluator.sh <team_id> <zip>` 调用约定——旧入口的等价命令变成 `evaluator-host.sh <team_id> <zip>`

**MediaMTX 生命周期**
- From: 由 `scripts/deploy.sh` 拉起、跨多次 evaluator.sh 共享；evaluator.sh 不杀
- To: 在 OCI 镜像内由 `evaluator.sh` 每次启动新实例，容器退出即终止
- Reason: 镜像化后宿主不再常驻评测进程
- Impact: 每次评测多 ~3 s 冷启动；换得"目标机零常驻进程"

**分发形态**
- From: 每台目标机重跑 setup → build → deploy
- To: 构建机产 `dist/`（`evaluator-portable_<sha>.tar.zst` + `evaluator-host.sh` + `README.md` + `SHA256SUMS` + `manifest.json`）；目标机 `docker load` 后用 `evaluator-host.sh` 评测
- Reason: 一次构建多机复用；目标机零编译、零外网下载
- Impact: 目标机前置依赖从 `apt + python venv + 源码编译 + 外网` 收敛为 `docker(或 podman) + 选手运行时`

**新增脚本与镜像构建链**
- `Dockerfile`（multi-stage：builder 阶段仅 COPY 构建机已有产物，不重编；runtime 阶段 `ubuntu:24.04` + Chromium 必需系统 .so）
- `.dockerignore`
- `scripts/package.sh`（precheck 构建机资产 → stage Playwright cache → `docker build` → `docker save` → `zstd` → 写 manifest.json）
- `scripts/test.sh --portable` 子模式

## Capabilities

### New Capabilities

- `portable-bundle`：把已构建的评测框架打包为 OCI 镜像分发包的全套能力——multi-stage Dockerfile、`package.sh` 构建流程、`dist/` 产物布局、`manifest.json` toolchain 指纹、宿主操作员入口 `evaluator-host.sh`（含 flock 互斥、image-sha256 锁定、`--user $(id -u):$(id -g)` 默认、退出码 0/1/2/75 语义、`--root` opt-in）、构建机本地开发 shortcut `evaluator-local.sh`。

### Modified Capabilities

- `evaluator`：现 spec 中 Requirement: Workspace Layout 声明"All evaluator components MUST run natively on the host (no container required)"，本次改动放宽为"评测主体既可在宿主原生运行也可在 OCI 容器内运行"；workspace 文件清单需加入 `Dockerfile` / `.dockerignore` / `scripts/package.sh` / `scripts/evaluator-host.sh` / `scripts/evaluator-local.sh`；`scripts/evaluator.sh` 的职责描述需缩窄为"评测主体"（剥离 unzip / 选手生命周期 / 端口清理）。MediaMTX 生命周期注释（"owned by deploy.sh, shared across runs"）需更新为"per-run 容器内独立实例"。选手合约相关 requirement 不变。

## Impact

**新增源码**
- `Dockerfile`、`.dockerignore`、`scripts/package.sh`、`scripts/evaluator-host.sh`、`scripts/evaluator-local.sh`

**修改源码**
- `scripts/evaluator.sh`（剥离宿主侧动作，调用约定改为 `<team_id> <results_subdir>`）
- `scripts/test.sh`（新增 `--portable` 子模式）

**零改动源码**
- `runner.py`、`analyzer.py`、`scorer.py`、`report.py`、`lib/watermark.py`
- `scripts/setup.sh`、`scripts/build.sh`、`scripts/deploy.sh`、`scripts/teardown.sh`、`scripts/env.sh`、`scripts/prepare_streams.sh`、`scripts/start_rtsp.sh`、`scripts/health_check.sh`、`scripts/build_test_zips.sh`
- `rtsp_server/mediamtx.yml`、`requirements.txt`
- 7 个 git submodule

**新增工具依赖**
- 构建机：`docker`（buildx）、`zstd`
- 目标机：`docker` 或 `rootful podman`（rootless podman 明确不支持）

**操作员界面变更**
- 构建机：在 `setup.sh && build.sh && deploy.sh` 之后多一步 `scripts/package.sh`，产出 `dist/`
- 目标机：从"再跑一遍 setup/build/deploy"改为"`docker load` + `evaluator-host.sh <team_id> <zip>`"
- 现行 CLAUDE.md 中"`./scripts/evaluator.sh <team_id> <submission.zip>`"的操作员示例需替换为 `evaluator-host.sh` 等价命令

**端口与生命周期**
- 仍占用 8080（选手 frontend）与 8554（MediaMTX RTSP）；evaluator-host.sh 启动前 fail-fast 检查这两个端口空闲
- 新增宿主常驻文件：`/var/tmp/evaluator-host.lock`（flock 互斥，单实例）

**目标机非目标**
- 不支持 macOS / Windows、不支持 arm64、不支持 rootless podman、不上 public registry、不做 image signature、不配 CI（V2 stage 2 在第二台机器手工跑）
