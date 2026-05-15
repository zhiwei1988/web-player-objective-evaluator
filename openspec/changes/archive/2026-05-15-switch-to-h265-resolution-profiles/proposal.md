## Why

当前评测以「H.264 + H.265 双 codec 轮次」组织，但比赛实际只考核 H.265 解码能力——H.264 轮次的存在让评分维度与考核重点错位，并占用 15 分（37.5%）权重测试一个非考点。同时缺失对 4K 分辨率的考核，无法区分中高端解码方案。把维度从 codec 切换到分辨率（2K / 4K，统一 H.265）能让评分聚焦考点、引入显著的难度梯度、并为未来加入 8K 等更高分辨率预留干净的扩展点。本次也顺便统一 RTSP 端点（写死 IP/端口/路径）和选手前端 URL 参数（codec → profile），降低选手集成歧义。

## What Changes

**评分结构**
- From: H.264 (10 正确性 + 5 fps) + H.265 (10 正确性 + 5 fps) + CPU 10 = 40 分
- To: 2K H.265 (5 正确性 + 5 fps) + 4K H.265 (5 正确性 + 5 fps) + CPU 10 = 30 分
- Reason: 聚焦 H.265 考点 + 引入分辨率难度梯度；正确性满分按比例减半（10→5 / 5→2 / 0→0），fps 分档不变（5/3/0）
- Impact: 破坏性变更；score.json 形态变化，max_score 由 40 → 30，删除 `h264`/`h265` block，新增 `2k`/`4k` block

**参考流**
- From: `streams/h264_watermarked.mp4` (1920×1080@30, libx264) + `streams/h265_watermarked.mp4` (2560×1440@25, libx265 4M)
- To: `streams/h265_2560_1440.mp4` (2560×1440@25, libx265 4M) + `streams/h265_3840_2160.mp4` (3840×2160@25, libx265 8M)，均 30s 时长，hvc1，GOP=50
- Reason: 新增 4K 考点；旧 H.264 流不再使用，删除避免两套并存腐烂
- Impact: 破坏性；`prepare_streams.sh`、`lib/watermark.py`、参考帧目录结构改变

**RTSP 端点**
- From: `rtsp://127.0.0.1:8554/test/h264`、`rtsp://127.0.0.1:8554/test/h265`
- To: `rtsp://127.0.0.1:554/test/h265_2560_1440`、`rtsp://127.0.0.1:554/test/h265_3840_2160`
- Reason: 端口 554 是标准 RTSP 端口，与业界惯例对齐；路径写死含分辨率方便选手识别
- Impact: 破坏性；选手契约改变；容器需 `--cap-add=NET_BIND_SERVICE`，build host 上需 setcap mediamtx binary

**选手前端 URL**
- From: `http://127.0.0.1:8080/play?codec=h264|h265&autoplay=1`
- To: `http://127.0.0.1:8080/play?profile=2k|4k&autoplay=1`
- Reason: 评测维度变化的直接体现；profile 是更准确的语义
- Impact: 破坏性；选手前端路由必须改造；不保留 `?codec=` 兼容（内部评测系统无外部消费者）

**CPU 子分门控**
- From: 仅在 H.265 capture 期间采样；`measured_h265_fps / expected_h265_fps < 0.25` 门控
- To: 仅在 4K capture 期间采样；`measured_4k_fps / expected_4k_fps < 0.25` 门控；gate_reason 字符串相应更新
- Reason: 4K 负载更重，CPU 区分度更高；选手必须真实解码 4K 才能拿 CPU 分
- Impact: 非破坏性；同 H.265 时期的语义延续，仅维度名称改变

**代码抽象**
- From: codec 维度散落在 runner/analyzer/scorer/scripts 的字面量 `"h264"`/`"h265"` 中
- To: 新增 `lib/profiles.py`，定义 `ProfileSpec` 与 `PROFILES = {"2k", "4k"}` 注册表；上游模块统一查表
- Reason: 单一真理源；未来加 8K 只需追加一条配置
- Impact: 非破坏性的内部重构（CLI 入参从 `--codec` → `--profile` 是破坏性外部接口变化，但 spec 已声明不保留兼容）

## Capabilities

### New Capabilities

无。本次变更未引入新能力域，仅重塑现有 `evaluator` 与 `portable-bundle` 的现有 requirements。

### Modified Capabilities

- `evaluator`: 几乎每个 Requirement 都受影响——Reference Stream Generation（双 H.265 profile）、Local RTSP Server（端口 554、新路径）、Contestant Runtime Contract（环境变量端口、URL 参数）、Playwright Capture Runner（`--profile`）、Frame Analysis（`--profile`、metrics 文件命名）、Scoring（max_score 30、新公式、score.json 形态）、Contestant CPU Usage Measurement（4K profile 门控、gate_reason 字符串）、Orchestration and Cleanup（端口预检、profile 循环）、Result Artifacts（`<profile>_screenshots/`、`<profile>_metrics.json`）。
- `portable-bundle`: RTSP 端口 8554 → 554；`docker run` 需 `--cap-add=NET_BIND_SERVICE`；目标主机 prerequisite 中新增 `NET_BIND_SERVICE` capability 要求。

## Impact

**代码模块**：新增 `lib/profiles.py`；修改 `lib/watermark.py`、`runner.py`、`analyzer.py`、`scorer.py`、`report.py`、`_cpu_sampler.py`（注释）。

**脚本**：`scripts/prepare_streams.sh`、`scripts/evaluator.sh`、`scripts/evaluator-host.sh`、`scripts/evaluator-local.sh`、`scripts/deploy.sh`、`scripts/teardown.sh`、`scripts/health_check.sh`、`scripts/build.sh`（新增 setcap 步骤）、`scripts/env.sh`、`scripts/test.sh`、`scripts/build_test_zips.sh`。

**配置**：`rtsp_server/mediamtx.yml`（端口、路径）；`Dockerfile`（mediamtx setcap）。

**契约 / API**：选手前端 URL 参数；环境变量 `RTSP_SERVER_PORT` 由 8554 改 554；score.json 结构（max_score、profile keys）；runner/analyzer/scorer 三个 CLI 的 `--codec` 全替换为 `--profile`；scorer 的 `--h264/--h265` 改为 `--metrics PROFILE=PATH` 重复参数形式。

**资产**：`streams/h264_watermarked.mp4`、`streams/h265_watermarked.mp4`、`reference/h264/`、`reference/h265/` 删除；新增 `streams/h265_2560_1440.mp4`、`streams/h265_3840_2160.mp4`、`reference/2k/`、`reference/4k/`。

**测试夹具**：`test_submissions/` 下 7 个 zip 全部失效，需要重新设计覆盖 2K / 4K 的成功、静态图、伪 watermark、partial decode 等场景；reference.zip 需确认 HEVC 软解（4K）在无硬件 HEVC 主机上的稳定性，如不稳定可能需要切到 WebCodecs / wasm 路径。

**部署 / 运维**：容器需要 `NET_BIND_SERVICE` capability；build host 上 MediaMTX binary 一次性 `sudo setcap`；目标主机 `evaluator-host.sh` 命令行不变，但前置要求隐含变化（dockerd 必须允许 `--cap-add`）。

**依赖 / submodules**：本次变更不删除 x264 submodule（ffmpeg 配置可能依赖），但实际产物中不再使用——作为可选清理项放入 tasks。

**文档**：`CLAUDE.md`、`README.md`、`openspec/specs/evaluator/spec.md`、`openspec/specs/portable-bundle/spec.md` 全部需要同步。
