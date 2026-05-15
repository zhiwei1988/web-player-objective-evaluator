# Design — switch-to-h265-resolution-profiles

## Goal

将客观评测从「H.264 + H.265 双 codec」结构改为「H.265 单 codec、2K + 4K 双 profile」结构，并统一 RTSP 取流端点与选手前端 URL 形态。总分由 40 → 30。

## Architecture

### 1. Profile 抽象层

新增 `lib/profiles.py` 作为整条流水线的单一真理源：

```python
# lib/profiles.py
from dataclasses import dataclass

@dataclass(frozen=True)
class ProfileSpec:
    name: str                  # "2k" | "4k"
    width: int
    height: int
    fps: int
    bitrate: str               # libx265 -b:v 参数，如 "4M" / "8M"
    rtsp_path: str             # MediaMTX path 名（不含 scheme/host/port），
                               # 例如 "test/h265_2560_1440"
    stream_file: str           # 相对 ROOT_DIR 的 mp4 路径
    reference_dir: str         # 相对 ROOT_DIR 的参考帧目录
    duration_s: int = 30
    cpu_sampled: bool = False  # 仅 4k=True

PROFILES: dict[str, ProfileSpec] = {
    "2k": ProfileSpec(
        name="2k", width=2560, height=1440, fps=25, bitrate="4M",
        rtsp_path="test/h265_2560_1440",
        stream_file="streams/h265_2560_1440.mp4",
        reference_dir="reference/2k",
    ),
    "4k": ProfileSpec(
        name="4k", width=3840, height=2160, fps=25, bitrate="8M",
        rtsp_path="test/h265_3840_2160",
        stream_file="streams/h265_3840_2160.mp4",
        reference_dir="reference/4k",
        cpu_sampled=True,
    ),
}

RTSP_PORT = 554
FRONTEND_PORT = 8080

def rtsp_url(profile: str, host: str = "127.0.0.1") -> str:
    return f"rtsp://{host}:{RTSP_PORT}/{PROFILES[profile].rtsp_path}"
```

`runner.py`、`analyzer.py`、`scorer.py`、`report.py`、`prepare_streams.sh`（通过 `python -c` 读取）以及 `mediamtx.yml`（人工保持同步，prepare_streams 内置一次性 sanity assert）一律从 `PROFILES` 查表，杜绝散落硬编码。

### 2. RTSP 服务

**`rtsp_server/mediamtx.yml`** — 端口与路径都改：

```yaml
rtspAddress: :554
paths:
  test/h265_2560_1440:
    runOnDemand: >-
      ffmpeg -hide_banner -loglevel warning
      -re -stream_loop -1
      -i streams/h265_2560_1440.mp4
      -c:v copy -an -f rtsp
      -rtsp_transport tcp
      rtsp://127.0.0.1:554/test/h265_2560_1440
    runOnDemandRestart: yes
    runOnDemandCloseAfter: 10s

  test/h265_3840_2160:
    runOnDemand: >-
      ffmpeg -hide_banner -loglevel warning
      -re -stream_loop -1
      -i streams/h265_3840_2160.mp4
      -c:v copy -an -f rtsp
      -rtsp_transport tcp
      rtsp://127.0.0.1:554/test/h265_3840_2160
    runOnDemandRestart: yes
    runOnDemandCloseAfter: 10s
```

旧的 `test/h264` / `test/h265` 路径彻底删除。

**端口 554 绑定**：

- 容器路径（`evaluator-host.sh`）：`docker run --cap-add=NET_BIND_SERVICE ...`。保留 `--user $(id -u):$(id -g)` 默认，仍然非 root；`NET_BIND_SERVICE` 让非 root 进程能绑 <1024 端口。
- Native 路径（`evaluator-local.sh` / `scripts/deploy.sh`）：`scripts/build.sh` 编译完 MediaMTX 后做一次性 `sudo setcap cap_net_bind_service=+ep "${THIRD_PARTY_INSTALL}/bin/mediamtx"`，该 binary 永久具备低端口绑定能力。如果 sudo 不可用或 setcap 失败，build.sh 显式报错退出，不静默回退。

### 3. 流生成

**`scripts/prepare_streams.sh`** — 重构为遍历 PROFILES：

```bash
# 简化伪代码
for profile in 2k 4k; do
    spec=$(python -c "from lib.profiles import PROFILES; import json; \
        s = PROFILES['$profile']; \
        print(json.dumps({'w': s.width, 'h': s.height, 'fps': s.fps, \
                          'bitrate': s.bitrate, 'dur': s.duration_s, \
                          'ref': s.reference_dir, 'mp4': s.stream_file}))")
    # 解析 + 调用 lib.watermark + ffmpeg libx265
done
```

ffmpeg 编码参数：

```
ffmpeg -framerate 25 -i reference/<profile>/frame_%05d.png \
    -c:v libx265 -tag:v hvc1 -b:v <bitrate> -maxrate <bitrate> \
    -bufsize <2*bitrate> -pix_fmt yuv420p \
    -x265-params "keyint=50:min-keyint=50:scenecut=0" \
    -movflags +faststart \
    streams/h265_<W>_<H>.mp4
```

GOP=2*fps=50 与现有 H.265 一致（连接后最迟 2s 等到 IDR）。

**`lib/watermark.py`** — CLI 入参：

```diff
- --codec h264|h265
+ --profile 2k|4k
```

实现：`profile` 决定 width/height/fps/duration 与输出目录；layout proportions 不变（DataMatrix 18% crop、4 color blocks bottom 10%、frame number top-left 20%×12%）。这些比例对 1920×1080、2560×1440、3840×2160 都已被 `WatermarkLayout` 设计为参数化，无需调整。

旧产物清理：在 prepare_streams 起始处删除 `streams/h264_watermarked.mp4`、`streams/h265_watermarked.mp4`、`reference/h264/`、`reference/h265/`（如果存在），避免两套并存腐烂。

### 4. 抓取 Runner

**`runner.py`** 变化：

- CLI：`--codec` → `--profile`；choices `("h264", "h265")` → `("2k", "4k")`
- URL：`http://localhost:{FRONTEND_PORT}/play?codec=...` → `?profile=...`
- CPU sampler 启动判据：从 `codec == "h265" and contestant_pgid is not None` 改为：

  ```python
  spec = PROFILES[profile]
  if spec.cpu_sampled and contestant_pgid is not None:
      sampler = _cpu_sampler.Sampler(...)
  ```

- Viewport 保持 1920×1080；为了让 4K 视频被等比缩放进 viewport 而不裁剪，contestant 的播放容器需要遵守原有 `[data-testid="player-video"]` ≥1280×720 的契约（spec 中未变）
- `capture_meta.json` 内 `codec` 字段改为 `profile`

### 5. 分析 / 评分 / 报告

**`analyzer.py`**

- CLI：`--codec` → `--profile`
- 输出文件名：`<profile>_metrics.json`（2k_metrics.json / 4k_metrics.json）
- COLOR_TARGETS / COLOR_TOLERANCE / SSIM_TARGET_W 不变

**`scorer.py`**

```python
EXPECTED_FPS = {name: spec.fps for name, spec in PROFILES.items()}  # {"2k": 25, "4k": 25}

def score_correctness(rate_wm, rate_color, mean_ssim) -> int:
    if rate_wm >= 0.95 and rate_color >= 0.95 and mean_ssim >= 0.90:
        return 5  # was 10
    if rate_wm >= 0.80 and mean_ssim >= 0.75:
        return 2  # was 5
    return 0

def score_fps(measured, expected) -> int:
    # 不变：5/3/0，阈值 0.50 / 0.25
    ...
```

`build_score` 接收 `profile_metrics: dict[str, dict | None]`（key 为 "2k" / "4k"），输出：

```json
{
  "max_score": 30,
  "objective_total": <int>,
  "2k": { "correctness_points": 5, "fps_points": 5, "total": 10, ... },
  "4k": { "correctness_points": 5, "fps_points": 5, "total": 10, ... },
  "cpu": { "points": 10, ... },
  "chromium_version": "..."
}
```

`_build_cpu_block` 改造：

- 入参从 `(h264_metrics, h265_metrics)` 改为 `(profile_metrics: dict, cpu_override_reason)`
- 取 `profile_metrics["4k"]` 作为 CPU 数据源
- `measured_on_codec: "h265"` → `measured_on_profile: "4k"`
- gate_reason 字符串：`"h265_fps_below_threshold"` → `"4k_fps_below_threshold"`；`"h265_round_failed"` → `"4k_round_failed"`

`_cpu_sampler.py`：模块内部不动；仅注释 docstring 中提到的「H.265 round」改为「4K profile capture window」。

**`report.py`**

- 顶部 summary 替换 codec 表为 profile 表
- 图表标题 "H.264 round" / "H.265 round" → "2K profile" / "4K profile"
- 嵌入 thumbnail 路径取自 `<profile>_screenshots/`

### 6. 编排脚本

**`scripts/evaluator.sh`**

```bash
# 旧
runner.py --codec h264 ...
runner.py --codec h265 --contestant-pgid $pgid ...
analyzer.py --codec h264 ...
analyzer.py --codec h265 ...
scorer.py --h264 ... --h265 ... --output score.json

# 新
runner.py --profile 2k ...
runner.py --profile 4k --contestant-pgid $pgid ...    # CPU 仅这里采样
analyzer.py --profile 2k ...
analyzer.py --profile 4k ...
scorer.py --metrics 2k=2k_metrics.json --metrics 4k=4k_metrics.json \
          --output score.json
```

scorer CLI 由两个固定 flag 改为通用的 `--metrics PROFILE=PATH` 反复传（argparse `action='append'`）。

**`scripts/health_check.sh`**

健康检查改为 ffprobe 两条新路径：

```bash
ffprobe ... rtsp://127.0.0.1:554/test/h265_2560_1440
ffprobe ... rtsp://127.0.0.1:554/test/h265_3840_2160
```

**`scripts/evaluator-host.sh`**

- 端口预检：`8080 + 554`（替换 `8080 + 8554`）
- `docker run` 命令增加 `--cap-add=NET_BIND_SERVICE`
- 失败时生成的兜底 score.json：`max_score=30`、删除 `h264`/`h265` block、保留 `cpu` block（gate_reason="host_failure"）

**`scripts/evaluator-local.sh` / `scripts/deploy.sh` / `scripts/teardown.sh`**

- 端口 8554 → 554
- 不需要 `--cap-add`（依赖 build.sh 安装的 setcap）

**`scripts/build.sh`**

新增一步：

```bash
sudo setcap cap_net_bind_service=+ep "${THIRD_PARTY_INSTALL}/bin/mediamtx" || {
    echo "FATAL: setcap failed; MediaMTX 无法绑定 :554" >&2
    exit 1
}
```

### 7. 容器镜像

**`Dockerfile`** — 镜像构建阶段也对 mediamtx binary 做 setcap（即使运行时还需要 `--cap-add`，但 docker 复制时 setcap bit 会丢，所以这里仅作为兼容性 belt-and-suspenders）。运行时绑定能力来自 `--cap-add=NET_BIND_SERVICE`。

**`scripts/package.sh`** — 不变（镜像构建产物形态相同；manifest.json `image_sha256` 仍然 sha-locked）。

### 8. 选手契约

环境变量与渲染契约：

```
RTSP_SERVER_HOST=127.0.0.1
RTSP_SERVER_PORT=554           # was 8554
FRONTEND_PORT=8080             # unchanged

拉流 URL (固定):
  rtsp://127.0.0.1:554/test/h265_2560_1440
  rtsp://127.0.0.1:554/test/h265_3840_2160

渲染 URL:
  http://127.0.0.1:8080/play?profile=2k&autoplay=1
  http://127.0.0.1:8080/play?profile=4k&autoplay=1

DOM 契约（不变）:
  [data-testid="player-video"]    ≥ 1280×720, 等比缩放
  window.__PLAYER_READY__         播放首帧后置 true
  window.__PLAYER_ERROR__         播放失败时字符串
```

### 9. 测试

- `test_submissions/*.zip`：7 个夹具全部基于 codec，需要重做。归到 tasks 阶段。
- `scripts/test.sh`：脚本本身的并行结构不变；只是 expected score / failure_reason 表会重写。
- `scripts/build_test_zips.sh`：生成逻辑跟着 src/ 走，无需大改。

### 10. OpenSpec specs

- `openspec/specs/evaluator/spec.md`：几乎每个 Requirement 都引用了 codec/8554 端口/`?codec=`/`h264_metrics.json` 等，需要全面 delta。包括：
  - Reference Stream Generation — 改为两 profile + libx265
  - Local RTSP Server — 端口 554，两条新 path
  - Contestant Runtime Contract — 选手环境变量、URL、路径
  - Playwright Capture Runner — `--profile`
  - Frame Analysis — `--profile`、metrics 文件命名
  - Scoring — 总分 30，新公式
  - Contestant CPU Usage Measurement — 4K 触发，gate_reason 字符串
  - Orchestration and Cleanup — 端口、profile 循环
  - Result Artifacts — `<profile>_screenshots/`、`<profile>_metrics.json`
- `openspec/specs/portable-bundle/spec.md`：RTSP 端口、`--cap-add=NET_BIND_SERVICE` 段落

### 11. 文档

`CLAUDE.md`、`scripts/env.sh` 中所有关于 codec / 8554 的描述同步更新。

## Data Flow

新流水线一次完整运行：

```
1. (一次性) scripts/build.sh  →  setcap on mediamtx binary
2. scripts/deploy.sh          →  prepare_streams.sh 生成 2K + 4K mp4 + reference
3. evaluator-host.sh / -local.sh:
   a. 端口预检 8080 + 554
   b. 解压 submission
   c. setsid start.sh
   d. 等待 http://127.0.0.1:8080/play?profile=2k&autoplay=1 readyish
4. scripts/evaluator.sh:
   a. MediaMTX :554 启动（容器内或宿主机均通过 setcap/cap-add 获得绑定能力）
   b. health_check.sh × 两条 RTSP path
   c. runner.py --profile 2k                       → 2k_screenshots/
   d. runner.py --profile 4k --contestant-pgid $P  → 4k_screenshots/ + capture_meta.json (CPU)
   e. analyzer.py --profile 2k                     → 2k_metrics.json
   f. analyzer.py --profile 4k                     → 4k_metrics.json
   g. scorer.py --metrics 2k=... --metrics 4k=...  → score.json (max_score=30)
   h. report.py                                    → report.html
   i. MediaMTX kill
5. wrapper cleanup (contestant stop.sh, pgid kill, port free, flock release)
```

## File Impact Summary

| 文件 | 改动类型 | 关键变化 |
|------|---------|---------|
| `lib/profiles.py` | 新增 | PROFILES 注册表 + ProfileSpec |
| `lib/watermark.py` | 修改 | `--codec` → `--profile`，按 spec 派生 |
| `runner.py` | 修改 | `--profile`、URL 参数、CPU 触发条件 |
| `analyzer.py` | 修改 | `--profile`、输出文件名 |
| `scorer.py` | 修改 | profile 循环、新公式、cpu_block 改用 4k key |
| `report.py` | 修改 | profile 循环、标题 |
| `_cpu_sampler.py` | 注释更新 | "H.265 round" → "4K profile" |
| `rtsp_server/mediamtx.yml` | 修改 | 端口 554、两条新 path |
| `scripts/prepare_streams.sh` | 修改 | 遍历 PROFILES，删除旧 H.264 路径 |
| `scripts/evaluator.sh` | 修改 | profile 循环 |
| `scripts/evaluator-host.sh` | 修改 | 端口 8554→554、`--cap-add` |
| `scripts/evaluator-local.sh` | 修改 | 端口 8554→554 |
| `scripts/deploy.sh` | 修改 | 端口 |
| `scripts/teardown.sh` | 修改 | 端口 |
| `scripts/health_check.sh` | 修改 | 新 RTSP path |
| `scripts/build.sh` | 修改 | mediamtx setcap |
| `scripts/env.sh` | 修改 | RTSP 端口环境变量 |
| `scripts/test.sh` | 修改 | expected score 表 |
| `scripts/build_test_zips.sh` | 评估 | 重新生成 fixtures |
| `test_submissions/` | 重做 | 7 个夹具按 profile 重新设计 |
| `Dockerfile` | 修改 | mediamtx setcap（belt-and-suspenders） |
| `openspec/specs/evaluator/spec.md` | 大量 delta | 几乎每个 Requirement |
| `openspec/specs/portable-bundle/spec.md` | 小量 delta | 端口、cap-add |
| `CLAUDE.md` | 文档 | 总览段落同步 |

## Failure Modes

- **MediaMTX 启动失败因端口 554 绑定权限不足**：被 health_check 捕获，wrapper 写入 `score.json` with `cpu.gate_reason="host_failure"`、`max_score=30`。
- **selectivly 4K 软解超出主机能力，导致 4K fps 不达标**：评分按现行规则——4K fps 0 分、4K correctness 可能仍达成、CPU 子分被 fps gate 触发为 0（reason="4k_fps_below_threshold"）。这是预期的正确行为。
- **2K 通过、4K 失败的混合场景**：score.json 中两个 profile 独立呈现；总分由 0+10+CPU 0 = 10 这种合法值组合产生。

## Backward Compatibility

不保留。理由：

- 评测系统为内部组织者使用，无第三方消费者
- 保留 `?codec=` / 8554 / `h264_metrics.json` 形态会让 scorer 必须维护两套并列路径，调试复杂度上升
- 干净替换更利于审计：score.json 的形态变化能直接用 `max_score` 区分新旧版本

## Out of Scope

- 8K profile / 第三种分辨率
- 多 codec 对比（AV1 / VP9）
- CPU 子分以外的「性能测试 N」其它项目（已为未来扩展预留 PROFILES 与 score.cpu 命名）
- 选手前端框架推荐 / SDK 提供（仍是 contestant 自由选择）
