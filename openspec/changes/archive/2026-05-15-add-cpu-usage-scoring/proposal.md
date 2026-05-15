## Why

评测机 Chrome 不支持 HEVC 硬解，contestant 在 H.265 上要拿分必须自带软解通路（典型为 WASM
ffmpeg / 自研 worker），解码是 CPU 密集型；但现行 30 分客观分**完全不衡量算力开销**——一个
100% 单核硬解出满分帧率的实现，和一个 30% 单核高效解出同样帧率的实现，得分一样。这次新增
10 分 CPU 占用率评测项，让算力效率进入客观维度，把"是否真在解码"以及"解得多省"显性化，
压缩"低质量水过 fps"的钻空子空间。本次仅覆盖原生 host 路径（`evaluator-local.sh`），
容器化 / portable bundle 路径用户后续会随打包方式调整时再补。

## What Changes

**`score.json` 顶层结构**
- From: `max_score: 30`，顶层仅含 `h264` / `h265` / `chromium_version` / `objective_total`
- To: `max_score: 40`，新增与 `h264` / `h265` 同级的 `cpu` 块（含 `points` / `mean_percent` /
  `sample_count` / `sample_window_ms` / `ncpu` / `normalization` / `measured_on_codec` /
  `thresholds_used` / `gated` / `gate_reason`）；`objective_total` 上限同步升 40
- Reason: 引入 10 分 CPU 评测维度
- Impact: 非破坏；仅看 `objective_total` 的消费者无感，看具体字段的消费者需识别新增 `cpu` 块

**CPU 评分函数（`scorer.py::score_cpu`）**
- New: `mean ≤ 5%` → 10；`5% < mean ≤ 20%` → `round((20-mean) / (20-6) * 10)` clamp [0,10]；
  `mean > 20%` → 0；门控 `measured_h265_fps / expected_h265_fps < 0.25` 时直接 0
- Reason: 用户规则 + 防止 H.265 直接放弃的 contestant 白拿 CPU 满分
- Impact: 6 个评分阈值（`CPU_GATE_H265_FPS_RATIO` / `CPU_FULL_THRESHOLD_PERCENT` /
  `CPU_PARTIAL_START_PERCENT` / `CPU_ZERO_THRESHOLD_PERCENT` / `CPU_MIN_SAMPLES` /
  `_cpu_sampler.DEFAULT_SAMPLE_HZ`）提为命名常量；实际生效值写入 `score.json.cpu.thresholds_used`

**CPU 采样（runner.py 内嵌 stdlib sampler 线程）**
- New: `runner.py` 新增 `--contestant-pgid INT`（可选）与 `--cpu-sample-hz FLOAT`（debug-only，
  稳定后回收）；仅 `codec=h265` 时起 daemon 线程，按 `DEFAULT_SAMPLE_HZ` 读 `/proc/<pid>/stat`
  累计 SID==PGID 子树的 CPU jiffies；汇总写 `<screenshots_dir>/capture_meta.json`
- Reason: 仅采 Playwright 稳态抓帧期，避免 start.sh 启动峰值 / 首帧冷启动 / 清理期污染均值
- Impact: 旧 CLI 兼容（参数可选，未传则 runner 行为退化为现状，不写 capture_meta.json）

**`scripts/evaluator.sh` 接线**
- From: `python runner.py --codec h265 ...`
- To: `python runner.py --codec h265 --contestant-pgid "$(cat results/<run>/contestant.pid)" ...`
- Reason: sampler 需要 PGID
- Impact: 仅 `evaluator-local.sh` 路径生效；`evaluator-host.sh` 容器路径下 scorer 一致返回
  `gate_reason="container_mode_unsupported"`、`points=0`

**`analyzer.py` 透传 CPU 字段**
- New: 检测 `<screenshots_dir>/capture_meta.json` 存在时把 `cpu` 子对象合入 `h265_metrics.json`；
  不存在则 metrics 中不写
- Reason: 数据契约与现有 runner→analyzer→scorer 链路同构
- Impact: 既有 metrics 字段全部保留

## Capabilities

### New Capabilities

（无。CPU 评分是 `evaluator` 已有评分管线的能力扩展，不需要独立 capability。）

### Modified Capabilities

- `evaluator`: 新增 CPU 评分维度——`score.json` 契约扩展（`max_score` 30→40 + `cpu` 块 +
  `objective_total` 上限升 40）；runner / analyzer / scorer 数据契约新增 CPU 字段；
  新增 6 个调参常量 + 1 个 debug CLI flag；H.265 round 在原生路径下采样 contestant PGID
  CPU 并按门控 + 线性衰减公式打分；容器路径下 CPU 一律门控为 0。

## Impact

- **代码**：`runner.py`（新可选参数 + sampler 线程入口）、`analyzer.py`（透传 cpu）、
  `scorer.py`（新 `score_cpu` + 5 常量 + 顶层 `cpu` 块 + `build_score` 签名扩展）、
  `_cpu_sampler.py`（新文件，纯 stdlib）、`scripts/evaluator.sh`（h265 行追加 `--contestant-pgid`）
- **测试**：新增 `tests/test_score_cpu.py`（表驱动覆盖满分线 / 零分线 / 过渡区典型值 /
  5 种 gate_reason / `thresholds_used` 字段回写）、`tests/test_cpu_sampler.py`
  （`os.setsid()` fork spin-CPU 子进程，±2% 容差验证）；`scripts/test.sh` 默认门
  `total_ge:13` 不动（reference.zip 在评测机上仍得 15 ≥ 13）
- **文档 / spec**：`openspec/specs/evaluator/spec.md`（文案 30→40，新增 CPU 评分需求段 +
  对应 Scenarios）、`CLAUDE.md` Overview（30→40，新增 CPU 评分小节）、`scorer.py` 文件 docstring
  （30→40 + CPU 说明）、`report.html` 模板（新增 CPU 块，第一版不画走势）
- **依赖**：零新 Python / 系统依赖（采样器纯 stdlib 实现）
- **不影响**：`portable-bundle` capability、contestant 接入契约（start.sh / stop.sh / 环境变量 /
  前端元素 / readiness 信号）、watermark / reference 流生成、MediaMTX 生命周期、Playwright
  capture 流程
- **回滚**：单 commit 回滚；`score.json` 新字段对消费者非破坏，回滚后字段消失而 `objective_total`
  仍可读
