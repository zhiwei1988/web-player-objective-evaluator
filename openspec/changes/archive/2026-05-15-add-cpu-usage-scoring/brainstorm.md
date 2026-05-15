## Design Summary

新增 10 分的 contestant CPU 占用率评测项，使评测器客观总分从 30 分升至 40 分。
CPU 评分**只在 H.265 抓帧轮**采样：评测机 Chrome 不支持 HEVC 硬解，contestant 必须自己软解，
CPU 压力是"是否真在解码"的最强信号。采样目标为 contestant 的 PGID 进程组（含所有 fork 子进程），
归一化到整机所有核之和 = 100%。采样窗口仅覆盖 runner.py 的稳态抓帧期，排除启动峰值与冷启动。
评分函数：`mean_cpu ≤ 5%` 满分 10；`6%–20%` 以 6% 为锚点线性衰减取整；`> 20%` 零分。
所有阈值（含 fps 门控比、CPU 满分/零分/区间起点、最小样本数、采样率）均提为命名常量，
便于事后微调；调参实际生效值写入 `score.json` 留痕。

实现位置：`runner.py` 接收 `--contestant-pgid` 参数，在 H.265 round 起一个 stdlib 实现的
sampler 守护线程；输出汇总写 `h265_screenshots/capture_meta.json`；analyzer 透传到
`h265_metrics.json`；scorer 新增 `score_cpu()` 出 0–10 分 + gate_reason，合入 `score.json`。

执行模式仅支持 `evaluator-local.sh` 原生路径；容器化 `evaluator-host.sh` 路径下 CPU 字段
直接门控为 0（容器 PID namespace 看不到宿主 contestant 进程），用户后续会改打包方式时再处理。

## Alternatives Considered

### 方案 A：runner.py 内嵌 sampler 线程（采纳）
- **做法**：runner.py 仅在 codec=h265 时起一个 1Hz daemon thread，进入正式 capture 循环时
  `start()`，循环结束时 `stop()`，把 mean/sample_count/window_ms 写 `capture_meta.json`。
- **优点**：capture 窗口与采样窗口天然同步、无时钟同步问题、无额外长驻进程、零新依赖
  （纯 stdlib `/proc/<pid>/stat`），评分流水线复用既有 runner→analyzer→scorer 链路。
- **缺点**：runner.py 多扛一个职责；若以后要在 report.html 画 CPU 全程走势需要扩展为方案 B。
- **为何未采用其他**：在"仅采稳态期"的约束下，方案 A 是最少动件、风险最低的路径。

### 方案 B：独立 sampler 子进程 + 后裁剪
- **做法**：evaluator.sh 启 contestant 后拉起 `python cpu_sampler.py --pgid X --output …jsonl`，
  采全程时间线；runner.py 在 metrics 写 capture 起止 epoch；scorer 阶段裁窗口求均值。
- **优点**：能在 report.html 画 CPU 全程走势图（具备诊断 / 取证价值）；采样器逻辑可独立单测。
- **缺点**：多一个长驻进程要管生命周期（cleanup trap）；evaluator.sh 复杂度上升；
  依赖容器内外 / 不同进程之间的系统时钟一致（在原生路径下问题不大，但仍是隐性依赖）。
- **为何未采用**：第一版没有走势图的需求，方案 A 已满足评分功能；若稳态后真要走势，再升级为 B
  不需要改数据契约。

### 方案 C：analyzer.py 调 pidstat 复跑
- **做法**：让 analyzer 在 capture 结束后调 `pidstat -p <pgid> -h 1 N`。
- **优点**：分析期统一在 analyzer 内，runner.py 不变。
- **缺点**：pidstat 不能"回溯"已结束的 capture 窗口；要么和 runner 并行（退化为方案 B），
  要么放弃"只采抓帧期"的核心约束。架构倒退。
- **为何未采用**：与"只采稳态期"约束根本矛盾。

## Agreed Approach

采纳**方案 A**。理由：

1. 与"只采 Playwright 稳态抓帧期"的约束严丝合缝——sampler 的生命周期就是 capture 循环。
2. 零新依赖、零新长驻进程、零时钟同步顾虑。
3. 数据契约（`capture_meta.json` → `h265_metrics.json` → `score.json.cpu`）与现有
   runner→analyzer→scorer 流水线同构，新增的是"再加一类指标"，不是"再加一条管线"。
4. 若以后需要全程走势，升级到方案 B 不破坏已有 `score.json.cpu` 字段。

## Key Decisions

1. **测量目标 = contestant PGID 进程组**；归一化基准 = 整机所有核之和 = 100%。
   5% 在 8 核机器约等于半核满载，在 4 核机器约等于 1/5 核——硬件越强相对越宽松，
   但这是用户主动选择的归一化方式（与 top per-core 显示不同，强调"整机算力占用"）。
2. **采样窗口仅 H.265 round 的稳态抓帧期**，排除 start.sh 启动峰值 / 冷启动 / 清理期。
   H.264 round 不采样、不写 `capture_meta.json`。
3. **CPU 分仅在 H.265 round 评 10 分**，不在两 codec 之间拆分。理由：H.265 在评测机
   Chrome 上无硬解，CPU 压力正是软解真实度的信号；H.264 上有硬解，CPU 压力低不具区分度。
4. **CPU 分门控 = H.265 fps 分项 ≥ 部分得分阈值**（默认 `measured/expected ≥ 0.25`，
   即 ≥ 6.25 fps）。低于此门 → CPU 分 = 0，`gate_reason="h265_fps_below_threshold"`。
   防止 contestant 在 H.265 直接放弃（CPU idle）反而白拿 CPU 满分。
5. **评分函数**（取整、命名常量）：
   - `mean ≤ 5%` → 10
   - `mean > 20%` → 0
   - 区间内：`round((20 - mean) / (20 - 6) * 10)`，clamp 到 [0, 10]
   - 5%–6% sliver 经 clamp 自然映射为 10，无跳变。
6. **所有阈值提为命名常量 / CLI flag**，便于事后微调：

   | 参数 | 位置 | 默认值 | 暴露方式 |
   |---|---|---|---|
   | `CPU_GATE_H265_FPS_RATIO` | `scorer.py` | 0.25 | 源码常量 |
   | `CPU_FULL_THRESHOLD_PERCENT` | `scorer.py` | 5.0 | 源码常量 |
   | `CPU_PARTIAL_START_PERCENT` | `scorer.py` | 6.0 | 源码常量 |
   | `CPU_ZERO_THRESHOLD_PERCENT` | `scorer.py` | 20.0 | 源码常量 |
   | `CPU_MIN_SAMPLES` | `scorer.py` | 3 | 源码常量 |
   | `DEFAULT_SAMPLE_HZ` | `_cpu_sampler.py` | 1.0 | 源码常量 **+ `--cpu-sample-hz` CLI**（debug-only，稳定后回收） |

   实际生效值写 `score.json.cpu.thresholds_used` 留审计痕。
7. **score.json 顶层 `max_score`：30 → 40**；新增同级 `cpu` 块（含 points, mean_percent,
   sample_count, sample_window_ms, ncpu, normalization, measured_on_codec,
   thresholds_used, gated, gate_reason）。`objective_total` 上限同步升 40。
8. **采样实现细节**（沿 Linux 内核约定）：
   - 用 `os.cpu_count()` + `os.sysconf("SC_CLK_TCK")` 计算归一化分母
   - 遍历 `/proc/[0-9]*/stat`，第 6 字段 `session` == PGID 即为 contestant 子树
     （`setsid` 起的 contestant 满足 SID = PGID = `CONTESTANT_PID`；实现注释要把这一点写明
     防后人改 lifecycle 时踩坑）
   - 进程消亡：忽略 read error；进程新生：第一次见到的 jiffies 作基线，下 tick 起计入
   - `mean_percent = Σ Δjiffies / (Δwall · ncpu · CLK_TCK) · 100`
9. **失败分支统一返回 `(0, gate_reason)`**：
   - `"h265_round_failed"`（h265_metrics.json 缺失）
   - `"h265_fps_below_threshold"`（fps 门控未过）
   - `"sampler_no_data"`（采样失败或 sample_count < CPU_MIN_SAMPLES）
   - `"host_failure"`（host 阶段失败，沿用既有兜底 score.json 路径）
   - `"container_mode_unsupported"`（暂搁置：evaluator-host.sh 容器路径下直接写此值，分=0）
10. **测试策略**：
    - 新增 `tests/test_score_cpu.py`：表驱动覆盖满分线 / 零分线 / 过渡区典型值 / 5 种
      gate_reason / `thresholds_used` 字段回写
    - 新增 `tests/test_cpu_sampler.py`：用 `os.setsid()` fork 一个 spin-CPU 子进程
      （已知占用约 1 核），验证 sampler 输出在 ±2% 容差内合理
    - `scripts/test.sh` 默认门 `total_ge:13` 不动：reference.zip 在评测机上 H.265 = 0 → CPU = 0
      （fps 门控），总分仍 = 15 ≥ 13 通过
    - 短期不新增专门压测 CPU 的 fixture（构造一个真软解 H.265 的 contestant fixture 成本太高）；
      接受"正向通路无 fixture 覆盖"作为遗留事项

## Open Questions

1. **容器路径（`evaluator-host.sh` / portable bundle）未来如何重新打包**——用户已表态会改打包方式，
   届时再补 CPU 评分跨容器路径。第一版仅原生 host 路径生效。
2. **缺一个能真正考 CPU 的自测 fixture**：reference.zip 在评测机上 H.265 = 0，整条 CPU
   路径走门控分支；正向通路只能靠单元 / 集成测试 + 实人提交回归验证。
3. **采样精度（1Hz × ~10s = 9 samples）方差**：若实战发现波动过大，先调高 `--cpu-sample-hz`
   到 2–5 调试；稳定后再决定是否调默认值（或升级为方案 B 取走势）。
4. **`DEFAULT_SAMPLE_HZ` CLI flag 何时回收**：标记为 debug-only，等参数稳定后从 CLI 移回纯常量。
