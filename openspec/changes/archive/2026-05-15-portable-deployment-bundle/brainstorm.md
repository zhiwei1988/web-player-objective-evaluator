# Brainstorm — portable-deployment-bundle

## Design Summary

把 `web-player-objective-evaluator` 的部署面从"在目标机上 setup + build + deploy"改造成"在构建机上一次性烤出 OCI 镜像 + 宿主操作脚本，在目标机上 `docker load && ./evaluator-host.sh ...` 即可评测"。目标机的前置依赖被压缩到 **docker（或 podman）+ 选手自身运行时**，评测框架本身（ffmpeg / mediamtx / tesseract / Playwright Chromium / Python venv / 流和参考帧）全部封装在镜像里。镜像通过 multi-stage Dockerfile 构建：builder 阶段直接 COPY 构建机上已有的 `third_party/install/` + `.venv/` + `.playwright/` + `streams/` + `reference/`（不重新编译），runtime 阶段是 `ubuntu:24.04` + Chromium 必需系统 .so 包。选手 `start.sh` 继续在宿主进程空间运行；评测器容器以 `--network host` 与之同名字空间互通；选手合约（zip 接口 / 端口 / readiness 信号）一字不改。

## Alternatives Considered

### 方案 A：单镜像 + `--network host` + 宿主薄包装脚本（**Agreed**）

- **做法**：`docker save` 出一个完整镜像 tarball；目标机 `docker load` 后用 `evaluator-host.sh` 一条命令评测一个 zip。Playwright Chromium 在容器内访问宿主 `localhost:8080`、选手 player 在宿主访问容器内 `rtsp://localhost:8554`，靠 `--network host` 共享 host net namespace 完成穿透。
- **优点**：runner / analyzer / scorer / report 四个核心 Python 模块零改动；宿主操作单条命令；跟现有"幂等独立"哲学契合；multi-stage Dockerfile 把 toolchain 剔除出最终镜像。
- **缺点**：`--network host` 是 Linux Docker 专属（macOS Docker Desktop 不支持，但本来就是评测服务器场景，不损失任何目标）；8080 / 8554 端口跟现状一样仍为单实例独占。
- **为何采用**：核心代码改动最浅；目标场景（Linux 评测服务器）完美匹配。

### 方案 B：单镜像 + 显式 port-publish + Chromium 走 `host.docker.internal`

- **做法**：`docker run -p 8554:8554 --add-host=host.docker.internal:host-gateway ...`；`runner.py` 读 `EVALUATOR_FRONTEND_HOST` 环境变量决定连接目标。
- **优点**：网络隔离更干净；跨 Linux/macOS Docker 都能跑。
- **缺点**：`runner.py` 必须改造一层 host 抽象（破坏"核心代码零改动"原则）；多一个 `host-gateway` Docker 版本依赖；操作员心智多一项。
- **为何未采用**：所牺牲的核心代码稳定性，去换的"macOS 兼容"在本项目语境里无意义。

### 方案 C：常驻容器 + 每次 submission `docker exec`

- **做法**：`evaluator-host.sh start / run / stop` 三段式；常驻容器复用 MediaMTX，免去每次冷启动。
- **优点**：连续多次 submission 时省 ~3 s 冷启动。
- **缺点**：引入容器生命周期状态（stale container / 端口冲突 / 健康检查）；跟现有"每个脚本幂等独立"哲学冲突；性能收益对 1-2 min 的评测时长占比极低。
- **为何未采用**：付出的复杂度换不回足够价值。

## Agreed Approach

**方案 A**。构成：

- 构建机产出 `dist/`：`evaluator-portable_<sha>.tar.zst`（zstd 压缩的 docker save 归档）+ `evaluator-host.sh`（宿主操作员入口）+ `README.md` + `SHA256SUMS` + `manifest.json`（toolchain 指纹）
- 目标机：`docker load` 后用 `evaluator-host.sh <team_id> <submission.zip>` 跑评测
- 选手 `start.sh` 在宿主进程空间运行；评测器容器靠 `--network host` 与之互通；现行选手合约不动
- 流和参考帧烤进镜像（章节 4 决策）
- Dockerfile 仅 COPY 构建机已有产物，不重新编译（章节 3 决策 3-2）
- `evaluator.sh` 内部职责拆分：宿主侧动作（unzip / start.sh / stop.sh）移到 `evaluator-host.sh`；本脚本仅保留容器内主体；`evaluator-local.sh` 保留作为本地开发不走 docker 的 shortcut

## Key Decisions


| #   | 决策                                                                                                                                | 来源          |
| --- | --------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| K1  | 打包形态 = OCI 镜像（`docker save`），不做 AppImage / squashfs / tarball-of-everything                                                       | Q1+Q2 用户答复  |
| K2  | 目标机需预装 docker 或 podman；其他零前置依赖                                                                                                    | Q2          |
| K3  | 选手 `start.sh` 在宿主跑（不进容器）                                                                                                          | Q3          |
| K4  | `streams/` 和 `reference/` 烤进镜像（不首启动生成）                                                                                            | Q4          |
| K5  | Dockerfile builder 阶段仅 COPY 构建机已有的 `third_party/install/` + `.venv/` + `.playwright/` + `streams/` + `reference/`，不重新跑 `build.sh` | 章节 3 方案 3-2 |
| K6  | `evaluator-host.sh` 锁定 `manifest.json` 中的 `image_sha`，禁止隐式回退 latest；host script 自身不做 sha 绑定，允许独立 hotfix                           | 章节 5.1 用户答复 |
| K7  | `docker run` 默认带 `--user $(id -u):$(id -g)`；提供 `--root` opt-in                                                                    | 章节 5.5 用户答复 |
| K8  | 选手 frontend 起不来时退出码 = 2，写极简 `score.json`（`total=0, reason="contestant_frontend_unavailable"`）                                     | 章节 4 用户答复   |
| K9  | results 目录命名沿用 `YYYYMMDD_HHMMSS`，跟 `scripts/evaluator.sh:44` 现状一致                                                                 | 章节 4 用户答复   |
| K10 | 镜像体积无硬阈值，`package.sh` 仅打印实际体积                                                                                                     | 章节 6 用户答复   |
| K11 | V2 端到端验收使用第二台真实 Ubuntu 24.04 评测机手工跑；本项目不配 CI                                                                                      | 章节 6 用户答复   |
| K12 | 非目标：不支持 macOS / Windows 宿主、不支持 arm64、**不支持 rootless podman**、不做 image signature、不做 mediamtx 常驻、不改选手合约、不上 public registry、不配 CI | 章节 6.4 用户确认 |
| K13 | `evaluator-host.sh` 锁文件路径固定为 `/var/tmp/evaluator-host.lock`，**不**做降级链——目标机不是多用户共享环境                                                  | OQ1 用户答复   |
| K14 | `manifest.json` 字段就 6 项（git_sha / build_timestamp / submodule_status / playwright_chromium_version / image_sha256 / image_size_bytes），不加 `base_image_digest` | OQ2 用户答复   |


## Open Questions

无。原 OQ1–OQ4 已在 brainstorm 阶段全部敲定（见 K11–K14 与 K12 内的 rootless podman 排除）。

