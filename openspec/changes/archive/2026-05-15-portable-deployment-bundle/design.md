# Design — portable-deployment-bundle

## 1. Architecture

```
┌─────────── BUILD HOST (Ubuntu 24.04，跑 scripts/setup+build+package) ───────────┐
│                                                                                  │
│   submodules ──▶ scripts/build.sh ──▶ third_party/install/  +  .venv/            │
│                                          │                                       │
│                                          ▼                                       │
│                              scripts/package.sh                                  │
│                                          │                                       │
│                              ┌──────────┴──────────┐                             │
│                              │ Dockerfile          │                             │
│                              │  - builder: COPY    │                             │
│                              │    构建机已有产物    │                             │
│                              │  - runtime: ubuntu  │                             │
│                              │    24.04 + 系统 .so  │                             │
│                              └──────────┬──────────┘                             │
│                                          ▼                                       │
│                          dist/evaluator-portable_<sha>.tar.zst                   │
│                          dist/evaluator-host.sh                                  │
│                          dist/README.md  +  SHA256SUMS  +  manifest.json         │
└─────────────────────────────────────────┬────────────────────────────────────────┘
                                          │ scp / 移动介质 / 内网仓库
                                          ▼
┌────────── TARGET HOST (Ubuntu 24.04 + docker ≥ 20.10，禁止外网) ────────────────┐
│                                                                                  │
│   sha256sum -c SHA256SUMS                                                        │
│   zstd -d evaluator-portable_<sha>.tar.zst | docker load                         │
│                                                                                  │
│   ./evaluator-host.sh <team_id> <submission.zip>                                 │
│             │                                                                    │
│             ▼ (宿主侧)                                                           │
│   unzip → 选手 start.sh (宿主进程空间) → wait :8080                              │
│             │                                                                    │
│             ▼                                                                    │
│   docker run --rm --network host --user $(id -u):$(id -g)                        │
│     -v $PWD/results/team_<id>_<ts>:/work/results/team_<id>_<ts>:rw               │
│     evaluator-portable:<sha>  team_<id>  team_<id>_<ts>                          │
│             │                                                                    │
│             ▼  (容器内)                                                          │
│   /work/scripts/evaluator.sh                                                     │
│     ├─ MediaMTX 起在 host net ns :8554                                           │
│     ├─ runner.py × 2 codec (Chromium 容器内访问 host :8080)                      │
│     ├─ analyzer.py → scorer.py → report.py                                       │
│     └─ MediaMTX 收尾                                                             │
│             │                                                                    │
│             ▼ (宿主侧 cleanup)                                                   │
│   选手 stop.sh + fuser -k 8080/8554                                              │
│                                                                                  │
│   产物：./results/team_<id>_<ts>/{score.json, report.html, ...}                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**关键不变量：**
- 容器内 `WORKDIR=/work` 与构建机 repo root 一一对应；`scripts/env.sh` 的相对路径推导继续工作
- `--network host` 让容器内外 `127.0.0.1:8080` / `127.0.0.1:8554` 引用一致；`runner.py` 不感知容器化
- 选手合约（zip 接口、`FRONTEND_PORT=8080`、`__PLAYER_READY__` 信号）零变更

**一项明确的行为变更：**

现行 `scripts/evaluator.sh:97-98` 注释明说 MediaMTX 由 `deploy.sh` 拉起、跨多次 evaluator.sh 共享、evaluator.sh 不杀。容器化后改为：MediaMTX 起在容器内、每次 `docker run --rm` 起一份、容器退出即终止。代价：每次 submission 额外 ~3 s MediaMTX 冷启动。收益：MediaMTX 不再是宿主常驻状态，目标机不需要先跑 deploy.sh。

## 2. Components

### 2.1 新增（5 个）

| 文件 | 角色 |
|---|---|
| `Dockerfile` | Multi-stage：`builder` 阶段 COPY 构建机已有产物（不重编）；`runtime` 阶段 `ubuntu:24.04` + 必要系统 .so + 项目源码 |
| `.dockerignore` | 排除 `submissions/`、`results/`、`test_submissions/*.zip`、`.git/`、`dist/`、`evaluator.log`、`rtsp_server/mediamtx.pid` |
| `scripts/package.sh` | 构建产出 `dist/`。流程：precheck（验证构建机已有所有资产）→ `docker build` → `docker save` → `zstd -19` → sha256sum → 写 manifest.json + README.md → 拷贝 `evaluator-host.sh` 进 dist |
| `scripts/evaluator-host.sh` | 目标机操作员入口。职责：flock 加锁 → precheck（docker / 镜像 / 端口）→ unzip → 启动选手 start.sh → docker run 调用容器内 evaluator.sh → cleanup（stop.sh / fuser）→ 退出码归因 |
| `scripts/evaluator-local.sh` | 构建机本地开发 shortcut。等价于 evaluator-host.sh 流程但直接调用 `scripts/evaluator.sh` 而非 `docker run`，免去打包-加载循环 |

### 2.2 修改（2 个）

| 文件 | 改动 |
|---|---|
| `scripts/evaluator.sh` | 拆责：剥离宿主侧动作（unzip / start.sh / stop.sh / 端口清理）；本脚本只保留容器内主体（MediaMTX 起停 + runner × 2 + analyzer + scorer + report）；调用约定改为 `evaluator.sh <team_id> <results_subdir>`，假设选手 frontend 已在 `127.0.0.1:8080` 就绪 |
| `scripts/test.sh` | 新增 `--portable` 子集：stage1 跑 package.sh、stage2 在第二台机器或 nested docker 上跑 evaluator-host.sh ref.zip、stage3 用 evaluator-local.sh 对照、stage4 negative fixture |

### 2.3 不动（核心 Python + 现有 setup/build/deploy/teardown）

`runner.py` / `analyzer.py` / `scorer.py` / `report.py` / `lib/watermark.py` / `scripts/setup.sh` / `scripts/build.sh` / `scripts/deploy.sh` / `scripts/teardown.sh` / `scripts/prepare_streams.sh` / `scripts/start_rtsp.sh` / `scripts/health_check.sh` / `scripts/env.sh` / `requirements.txt` / `rtsp_server/mediamtx.yml` / 7 个 submodule：零改动。

### 2.4 镜像体积参考（不设硬阈值）

| 层 | 体积 |
|---|---|
| `ubuntu:24.04` base | ~78 MB |
| Chromium runtime apt 闭包（libnss3 / libpango / fonts / libasound / libatk / libcups / ...） | ~120 MB |
| `third_party/install/` | ~205 MB |
| `.venv/` | ~445 MB |
| Playwright Chromium (`/work/.playwright/chromium-*`) | ~370 MB |
| `streams/` + `reference/` | ~130 MB |
| 源码 | < 1 MB |
| **未压缩合计** | **~1.35 GB**（zstd -19 后约 600–750 MB） |

## 3. Build Flow（build host 视角）

### 3.1 流水线

```
scripts/setup.sh        (一次性: apt + venv + submodule init)
       │
       ▼
scripts/build.sh        (现状不变: 编译 third_party + pip install + playwright install)
       │
       ▼
scripts/deploy.sh       (现状不变: 生成 streams/ + reference/，本地 dev 用)
       │
       ▼
scripts/package.sh
       │
   ┌───┴────┐
   ▼        ▼
precheck  docker build (Dockerfile)
   │        │
   │   evaluator-portable:<sha>
   │        │
   │   docker save | zstd -19 -T0
   │        │
   │   dist/evaluator-portable_<sha>.tar.zst
   │        │
   ▼        ▼
拼装 dist/ ← sha256sum + manifest.json + README.md + evaluator-host.sh
```

### 3.2 Dockerfile 骨架

```dockerfile
# ────────── builder ──────────
FROM ubuntu:24.04 AS builder
WORKDIR /work
# 关键：不重编。直接 COPY 构建机已有产物
COPY third_party/install /work/third_party/install
COPY .venv               /work/.venv
COPY .playwright         /work/.playwright
COPY streams             /work/streams
COPY reference           /work/reference
COPY lib                 /work/lib
COPY rtsp_server         /work/rtsp_server
COPY scripts             /work/scripts
COPY *.py                /work/
COPY requirements.txt    /work/

# ────────── runtime ──────────
FROM ubuntu:24.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/work/.playwright \
    TESSDATA_PREFIX=/work/third_party/install/share/tessdata
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libnss3 libxkbcommon0 libdrm2 libxcomposite1 libxdamage1 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2t64 libatk-bridge2.0-0 \
    libatk1.0-0 libcups2 libxss1 libxshmfence1 \
    fonts-liberation \
    unzip lsof procps \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
# 逐层 COPY，把大且少变的层放前面以利构建缓存
COPY --from=builder /work/third_party/install /work/third_party/install
COPY --from=builder /work/.venv               /work/.venv
COPY --from=builder /work/.playwright         /work/.playwright
COPY --from=builder /work/streams             /work/streams
COPY --from=builder /work/reference           /work/reference
COPY --from=builder /work/lib                 /work/lib
COPY --from=builder /work/rtsp_server         /work/rtsp_server
COPY --from=builder /work/scripts             /work/scripts
COPY --from=builder /work/*.py                /work/
COPY --from=builder /work/requirements.txt    /work/
ENTRYPOINT ["/work/scripts/evaluator.sh"]
```

### 3.3 `scripts/package.sh` 流程

#### precheck 清单（早死）

确保以下资产在构建机存在且新鲜：
- `third_party/install/bin/{ffmpeg,mediamtx,tesseract}`
- `third_party/install/share/tessdata/eng.traineddata`
- `.venv/bin/python` 可执行
- `~/.cache/ms-playwright/chromium-*/`（Playwright `build.sh:244` 装到此处，**不改 build.sh**）
- `streams/{h264,h265}_watermarked.mp4`、`reference/{h264,h265}/frame_*.png`

任何一项缺失 → 提示先跑 `setup.sh && build.sh && deploy.sh`。

#### staging 步骤（precheck 后，docker build 前）

- `rsync -a ~/.cache/ms-playwright/ ./.playwright/`（增量；后续重跑 package.sh 不重复全量拷贝）
- `./.playwright/` 进 `.gitignore`，与 `streams/` / `reference/` / `dist/` 同级
- `.dockerignore` **不**排除 `.playwright/`、`third_party/install/`、`.venv/`、`streams/`、`reference/`——这些正是要进 build context 的

### 3.4 `manifest.json` 字段

```json
{
  "git_sha": "<short>",
  "build_timestamp": "<ISO8601>",
  "submodule_status": { "ffmpeg": "<sha>", "mediamtx": "<sha>", ... },
  "playwright_chromium_version": "<from third_party/install/playwright_chromium.version>",
  "image_sha256": "<docker image sha>",
  "image_size_bytes": <int>,
  "base_image_digest": "<ubuntu:24.04 sha>"
}
```

字段就这 6 项，不加 `base_image_digest`（K14）。

## 4. Runtime Flow（target host 视角）

### 4.1 一次性准备

```bash
mkdir -p ~/evaluator && cd ~/evaluator
# scp dist/* 过来
sha256sum -c SHA256SUMS
zstd -d evaluator-portable_<sha>.tar.zst -o image.tar
docker load < image.tar && rm image.tar
mkdir -p submissions results
```

### 4.2 单次 submission 时序

`evaluator-host.sh <team_id> <zip_path>` 内部：

```
[0] 解析 args, 计算 ts=$(date +%Y%m%d_%H%M%S)
    RESULTS=$PWD/results/team_<id>_${ts}
    mkdir -p "$RESULTS"
[1] flock -n "/var/tmp/evaluator-host.lock" -c '...' (单实例)
[2] precheck:
      - docker info
      - docker image inspect evaluator-portable:$(jq -r .image_sha256 manifest.json)
      - ss -lnt 'sport = :8080 or sport = :8554' 应为空
[3] rm -rf submissions/team_<id>; unzip -qq zip → submissions/team_<id>/
    (若 zip 是单层目录则上移；沿用现行 evaluator.sh:160-169 lift 逻辑)
    pushd submissions/team_<id>
[4] export RTSP_SERVER_HOST=127.0.0.1 RTSP_SERVER_PORT=8554 \
           FRONTEND_PORT=8080
    setsid nohup ./start.sh > "$RESULTS/contestant.log" 2>&1 &
    echo $! > "$RESULTS/contestant.pgid"
[5] 轮询 curl -fsS http://127.0.0.1:8080/play?codec=h264&autoplay=1
    总超时 60 s（沿用现行 evaluator.sh:191-200）
    若失败 → goto [9b]
[6] docker run --rm --network host \
      --user $(id -u):$(id -g) \
      -v "$RESULTS:/work/results/team_<id>_${ts}:rw" \
      evaluator-portable:<sha> \
      team_<id> team_<id>_${ts}
    (容器 entrypoint = scripts/evaluator.sh)
[7] 容器内：
      a. scripts/start_rtsp.sh        起 MediaMTX
      b. scripts/health_check.sh × 2  RTSP 健康
      c. python runner.py --codec h264 --output /work/results/.../h264_screenshots
      d. python runner.py --codec h265 --output /work/results/.../h265_screenshots
      e. python analyzer.py × 2       → {h264,h265}_metrics.json
      f. python scorer.py             → score.json
      g. python report.py             → report.html
      h. kill MediaMTX (PID 文件在容器 /tmp)
[8] 容器退出
[9a] (normal) cleanup:
      kill -- -$(cat "$RESULTS/contestant.pgid")  (kill 进程组)
      [[ -x ./stop.sh ]] && timeout 10 ./stop.sh || true
      fuser -k 8080/tcp 8554/tcp 2>/dev/null || true
      popd
      退出码 = 0 (score.json 已写)
[9b] (contestant failure) cleanup 同 [9a]，但额外：
      # 走容器内 scorer.py 写失败 score.json（避免 jq 拼装与 schema 漂移）
      docker run --rm --user $(id -u):$(id -g) \
        -v "$RESULTS:/work/results/team_<id>_${ts}:rw" \
        --entrypoint /work/.venv/bin/python \
        evaluator-portable:<sha> \
        /work/scorer.py \
          --output  /work/results/team_<id>_${ts}/score.json \
          --report  /work/results/team_<id>_${ts}/report.html \
          --install-prefix /work/third_party/install \
          --failure-reason contestant_frontend_unavailable
      退出码 = 2
[9c] (infra failure: docker / 端口 / unzip) cleanup 同 [9a]，
      不写 score.json，退出码 = 1
```

### 4.3 退出码语义

`evaluator-host.sh` 是操作员唯一直接看见的入口，其退出码定义如下；容器内 `evaluator.sh` 的退出码（继承现行的 1/64/65/66/70/71）被 host 翻译聚合：

| Exit | 含义 | 来源 |
|---|---|---|
| 0 | 评测完成，`score.json` 已写出（含拿 0 分情况） | 容器内 `evaluator.sh` exit 0 |
| 1 | 基础设施异常：docker 不可用 / 镜像缺失 / 端口占用 / 解压失败 / docker run 非零退出 | host 检测，或容器内 evaluator.sh 返回 64/65/66/70/71 |
| 2 | 选手 zip 自身起不来；已写极简 `score.json` 含 `reason="contestant_frontend_unavailable"` | host `[5]` 步轮询超时 |
| 75 | 拿不到 flock（`EX_TEMPFAIL`）；另一次评测正在进行中 | host `[1]` 步 |

## 5. Error Handling & 跨次运行

### 5.1 镜像版本绑定

- `evaluator-host.sh` 启动时从 `manifest.json` 读 `image_sha256`，`docker image inspect` 严格匹配该 sha
- 拿不到 → fail-fast 提示重 load
- 不接受 `latest` 隐式回退
- `evaluator-host.sh` 自身不做 sha 绑定，允许独立 hotfix（K6）

### 5.2 并发互斥

- 锁文件固定 `/var/tmp/evaluator-host.lock`，无降级链（K13；目标机不是多用户共享环境，简单优先）
- 拿不到锁 → 退出码 75，打印持锁 PID
- 不做"排队等待"——评测时长不可控，并发请准备多机

### 5.3 残留状态清理

`evaluator-host.sh` 顶层 `trap cleanup EXIT INT TERM`，按章节 4.2 [9a] 步骤执行：
1. kill 选手进程组（pgid）
2. 调用选手 stop.sh（超时 10 s 兜底）
3. 镜像 ancestor 反查残留容器 `docker kill`
4. `fuser -k 8080/tcp 8554/tcp` 兜底（**仅这两个端口**）

### 5.4 选手进程树

为防 start.sh fork-and-exit 导致 PID 失效，wrapper 用 `setsid` 启动并记录 pgid（不是 pid），cleanup 时 `kill -- -$pgid`。

### 5.5 容器内 UID / 文件所有权

`docker run --user $(id -u):$(id -g)` 默认开（K7）。`/work/results/...` mount 进来的目录所有权由宿主用户创建，容器进程同 uid 可读写。`--root` 是显式 opt-in 旁路（不预期使用，留作 escape hatch）。

### 5.6 时钟跳变

`<ts>` 用宿主 `date +%Y%m%d_%H%M%S`。已存在同名 results 目录 → fail-fast，不静默覆盖（提示操作员检查时钟）。

## 6. Verification & Testing

### 6.1 验收清单

| # | 条件 | 验证 |
|---|---|---|
| V1 | 构建机一键 `setup.sh && build.sh && deploy.sh && package.sh` 通；dist/ 5 件齐；`sha256sum -c SHA256SUMS` 通 | `scripts/test.sh --portable` stage 1 |
| V2 | 把 dist/ scp 到第二台干净 Ubuntu 24.04 评测机（K11），`docker load` + `evaluator-host.sh team_ref reference.zip`，`score.json.total ≥ 13`（沿用现行 reference gate） | 第二台机器手工 e2e（不配 CI） |
| V3 | 容器除 `/work/results` 之外 mount 全只读；`docker diff` 退出后无意外改动 | stage 2 中加 `docker inspect` 断言 |
| V4 | `docker run --network none` 在仅初始化路径（不走 RTSP 健康检查）下不报错——证明运行时不依赖外网 | stage 1 子测试 |
| V5 | 构建机 `evaluator-local.sh team_ref reference.zip` 与 stage 2 容器化结果在 `score.json` 上等价（key 列表 + 浮点容差比较） | stage 3 |
| V6 | 操作员 `id -u ≠ 0` 时整套流程通 | 所有 e2e |
| V7 | 选手 frontend 起不来（negative fixture）→ exit 2 + 极简 `score.json` | stage 4 |

### 6.2 测试矩阵（`scripts/test.sh --portable`）

```
stage 1: 在构建机跑 package.sh，校验 dist/ 完整 + 镜像可 docker run --network none 初始化
stage 2: 在第二台真实评测机上手工跑 evaluator-host.sh reference.zip
         （env: EVAL_TARGET_HOST=user@host 指定靶机；脚本本地不做 nested docker 兜底）
stage 3: 在构建机跑 evaluator-local.sh reference.zip，与 stage 2 score.json diff
stage 4: 在 stage 2 同环境跑 broken_fixture.zip，断言 exit 2 + score.json.reason 字段
```

不配 CI（K11/K12）；stage 2 在第二台机器上是手工触发的 release-gate 步骤，不进自动流水线。

### 6.3 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Playwright Chromium 缓存目录名随包升级改变 | 中 | 镜像 broken | `package.sh` precheck 读 playwright `_constants.py` 的 revision，与 `.playwright/` 实际目录名比对 |
| `libasound2t64` 等 apt 包名跨 ubuntu 小版本漂移 | 低 | docker build 失败 | base 锁 `ubuntu:24.04`；`manifest.json` 记 `base_image_digest`（OQ2） |
| `--network host` 在 rootless podman 上行为不一致 | 低 | 8080/8554 不可达 | 明确**非目标**（K12）：仅承诺 Linux Docker / rootful podman；rootless podman 不在支持矩阵内 |
| 目标机无 `zstd` | 低 | 解包失败 | README 列明装 zstd 命令；`package.sh` 提供 `--gzip` fallback 模式 |
| glibc ABI 跨发行版 | 中 | runtime crash | 显式 non-goal：目标矩阵仅 Ubuntu 24.04（K12） |

### 6.4 非目标

- 不支持 macOS / Windows 宿主
- 不支持 arm64
- **不支持 rootless podman**（仅 Linux Docker / rootful podman）
- 不做 image signature / cosign（内部链路，sha256 + 内网投递足够）
- 不做 mediamtx 常驻容器
- 不改选手合约
- 不上 public registry
- **不配 CI**（V2 stage 2 在第二台机器手工触发）

## 7. Open Questions

无。原 brainstorm.md 中的 OQ1–OQ4 已在 brainstorm 阶段全部敲定：

- 锁文件路径固定（K13）
- `manifest.json` 字段定稿 6 项（K14）
- 不配 CI（K11/K12）
- rootless podman 排除（K12）
