## Context

评测器当前对参赛 web 播放器输出 30 分客观分（`scorer.py::build_score` → `score.json`），
两个 codec（H.264 / H.265）各 15 分，每分由 watermark 识别率 / 颜色块校验 / SSIM /
unique-frame fps 四项组合而成。

评测主机为 Ubuntu 24.04 + Google Chrome，Chrome 在此配置下**不支持 HEVC 硬解**
（`<video>` 与 `WebCodecs` 都 `supported: false`）。要在 H.265 拿分，contestant 必须
自带软解通路（典型实现：WASM ffmpeg / 自研解码 worker），单解码这件事就是 CPU 密集型。
当前 30 分客观分**不衡量算力开销**——一个 contestant 在 H.265 上用 100% 单核硬解出满分帧率，
和另一个用 30% 单核高效解出同样帧率的 contestant，得分一样。这次变更补上这个缺口。

执行模型上，contestant 是宿主机进程组（`setsid ./start.sh`，SID = PGID = `CONTESTANT_PID`），
即便走容器化评测路径，contestant 也跑在宿主侧。这对"如何采样 contestant 的 CPU"有直接约束。

用户已表态：portable bundle / 容器化路径短期内会重新打包，本次变更**只覆盖原生 host 路径**
（`evaluator-local.sh`）。

## Goals / Non-Goals

**Goals:**

- 在 `score.json` 顶层新增 0–10 分的 `cpu` 评分块，`max_score` 由 30 提至 40。
- 评分函数有明确门控、明确公式、所有阈值参数化，便于赛后微调。
- 采样契约与现有 runner → analyzer → scorer 流水线同构，复用既有数据落盘 / CLI 风格。
- 评分结果在 `score.json` 中自证（`thresholds_used` 字段记录该次实际生效的所有阈值）。

**Non-Goals:**

- **不**支持容器化 / portable bundle 路径下的 CPU 评分（容器 PID namespace 看不到宿主
  contestant 进程）。该路径下 `cpu.points = 0, gate_reason="container_mode_unsupported"`。
- **不**在 `report.html` 提供 CPU 走势图。第一版只汇总均值；走势图所需的全程时间序列
  数据契约留给后续升级。
- **不**新增能正向考核 CPU 评分的端到端 fixture（构造真软解 H.265 contestant 成本过高）。
  正向通路靠单元 / 集成测试 + 实人提交回归。
- **不**修改两个 codec 各 15 分的现有评分结构。CPU 不在 codec 之间拆分。
- **不**修改 contestant 契约：`start.sh` / `stop.sh` / 环境变量 / 前端元素 / readiness 信号
  全部保留。CPU 评分对 contestant 是**透明**的。

## Decisions

### D1 — 测量目标 = contestant PGID 进程组（归一化到整机所有核之和 = 100%）

**选择**：用 `/proc/<pid>/stat` 第 6 字段 `session` 等于 PGID 来枚举 contestant 子树，
按 `Σ Δjiffies / (Δwall · ncpu · CLK_TCK)` 计算 CPU 占用，归一化基准 = 整机所有核之和 = 100%。

**替代**：
- _按 contestant PGID 但归一化到单核 100%（top 风格）_：直观但 5% 在不同核数主机上物理含义
  不同（4 核机 5% 与 64 核机 5% 的绝对算力差 16 倍）。
- _测整机 user+system_：实现最简，但把评测器自身（Playwright、Chrome、MediaMTX、ffmpeg）的
  CPU 也算进去，规则对 contestant 不公平。

**理由**：
- "整机所有核之和 = 100%"对 contestant 是硬件无关的相对比值——硬件越强，contestant 拿满 5%
  的绝对算力越多，但相对压力始终一致。
- PGID 枚举能自动覆盖 contestant 在 `start.sh` 里 fork 出来的所有子进程（relay backend /
  decoder workers / 自启动的 browser tab 等），不漏算。
- `setsid` 起的 contestant 保证 SID = PGID = `CONTESTANT_PID`；现有 lifecycle 不动。
  实现注释要把"SID 等价 PGID 的前提"写明，防后人改 lifecycle 时踩坑。

**事后修正**：纯 PGID-subtree 模型对 **client-side decode** contestant 不公平——WASM /
WebCodecs 解码器跑在 Chrome 进程里，session ≠ contestant PGID，被 sampler 漏算。team 2000
实测（server fanout + WASM 解码）按此模型只采到 0.037%，结构性 0 信号。后续在 **D8 / D9**
扩展为 PGID 子树 ∪ Playwright Chrome 子树（减 GPU process 噪声）。

### D2 — 采样窗口 = 仅 H.265 round 的 Playwright 稳态抓帧期

**选择**：CPU 评分**只在 H.265 round 评 10 分**；采样窗口仅覆盖 runner.py 的真正抓帧循环，
排除 start.sh 启动峰值、首帧冷启动、清理期。H.264 round 不采样。

**替代**：
- _全评测过程平均_：实现最简，但 start.sh 的编译 / 拉镜像 / JIT 热身会拉高均值，H.264 轮
  后的 idle 会拉低均值，信噪比差。
- _两个 codec 各 5 分独立打_：与现有 30 分两 codec 拆分风格一致，但 H.264 上 Chrome 有
  硬解，CPU 压力低且不具区分度，5 分粒度浪费。
- _并发记录 + 后裁剪_：能在 report 画走势图，价值高但复杂度高一档（多一个长驻 sampler 进程
  + 时钟同步），第一版不必要。

**理由**：
- H.265 在评测机 Chrome 上无硬解，是 CPU 压力的"自然实验组"——能区分真软解 vs 假装播放。
- H.264 有硬解，contestant 之间 CPU 差异主要来自实现常数项，区分度低。
- "仅稳态期"让 sampler 生命周期天然等于 capture 循环，无需跨进程时钟同步。

### D3 — CPU 评分门控：H.265 fps 分项需先达到部分得分阈值

**选择**：`measured_h265_fps / expected_h265_fps < CPU_GATE_H265_FPS_RATIO`（默认 0.25）
时，CPU 分直接 = 0，`gate_reason="h265_fps_below_threshold"`。门槛复用 `score_fps` 的
partial-credit 阈值。

**替代**：
- _无门控_：contestant 在 H.265 直接放弃（player error / 零帧 / 黑屏静态图）→ CPU idle
  ≤ 5% → 白拿 10 分。不可接受。
- _更严：要求 fps 满分（≥50%×expected）_：能挡 i-frame-only 但 CPU 也低的钻空子，但同时
  扼杀"软解慢但稳"的合理实现（如 25 fps 解到 14 fps）。
- _绝对帧率阈值（如 ≥ 10 fps）_：与 `score_fps` 阶梯解耦，但调起来要同时改两处。

**理由**：
- 复用 `score_fps` 的 25% 阈值让"是否在动"的判定逻辑在评分系统里只有一个出处。
- 阈值参数化（`CPU_GATE_H265_FPS_RATIO`），赛后真有调参需求一行改完。

### D4 — 评分函数形态：≤5% 满分、6%–20% 线性衰减、>20% 零分

**选择**：

```
mean ≤ 5%       → 10
mean > 20%      → 0
5% < mean ≤ 20% → round((20 - mean) / (20 - 6) * 10), clamp [0, 10]
```

所有阈值（5 / 6 / 20）提为命名常量。5%–6% sliver 经 clamp 自然映射为 10，无跳变。

**替代**：
- _以 5% 为锚、20% 为零的线性衰减_：连续无跳变但 9% 之类的整点会偏离用户文字描述的"6%–20%
  按比例"含义。
- _保留 1 位小数_：score.json 里看到 9.3 这种分更精细，但与现有评分整数风格不一致。

**理由**：
- 6% 作 partial 起点锚 + 20% 作零分线，与用户题面字面意思最贴合。
- 取整与现有 `score_correctness` / `score_fps` 整数风格统一。

### D5 — 实现路径：runner.py 内嵌 stdlib sampler 线程

**选择**：runner.py 接收 `--contestant-pgid INT` 参数；仅在 `codec=h265` 时起一个 daemon
线程，进入正式 capture 循环前 `start()`、循环结束后 `stop() → Result`；输出汇总（mean /
sample_count / window_ms / ncpu / sample_hz_used / pgid）写
`results/<run>/h265_screenshots/capture_meta.json`。

**替代**：
- _独立 sampler 子进程 + 后裁剪_：能取 CPU 全程时间线（report 走势图），但多一条进程生命周期、
  evaluator.sh 多挂 cleanup trap。
- _让 analyzer 调 pidstat 复跑_：架构倒退——pidstat 不能回溯已结束的 capture 窗口。

**理由**：
- capture 窗口与采样窗口天然同步，无时钟同步顾虑。
- 零新依赖（纯 stdlib `/proc/<pid>/stat`），sampler 类逻辑 ~80 行可单测。
- 数据契约（capture_meta.json）若以后升级到独立 sampler 方案不破坏。

### D6 — 全部阈值参数化 + score.json 留审计痕

**选择**：scorer.py 顶部 5 个命名常量（`CPU_GATE_H265_FPS_RATIO`、
`CPU_FULL_THRESHOLD_PERCENT`、`CPU_PARTIAL_START_PERCENT`、`CPU_ZERO_THRESHOLD_PERCENT`、
`CPU_MIN_SAMPLES`），加 `_cpu_sampler.py` 的 `DEFAULT_SAMPLE_HZ` 常量及对应
`--cpu-sample-hz` CLI flag（debug-only，标记稳定后回收）。`score.json.cpu.thresholds_used`
块记录该次实际生效的 6 个值。

**当前默认值**（权威以 `scorer.py` 源码 + `score.json.cpu.thresholds_used` 审计字段为准）:

| 参数 | 当前默认 | 历史 |
|---|---|---|
| `CPU_GATE_H265_FPS_RATIO` | **0.10** | 起初 0.25（复用 `score_fps` partial 阈值）；apply 阶段调到 0.10 以容纳 i-frame-only 实现仍能进入 CPU 评分 |
| `CPU_FULL_THRESHOLD_PERCENT` | 5.0 | 未调 |
| `CPU_PARTIAL_START_PERCENT` | 6.0 | 未调 |
| `CPU_ZERO_THRESHOLD_PERCENT` | 20.0 | 未调 |
| `CPU_MIN_SAMPLES` | 3 | 未调 |
| `DEFAULT_SAMPLE_HZ` | 1.0 | 未调 |

**理由**：
- 赛后参数微调高发场景：调一处源码常量即可，无需触碰 CLI / 配置文件。
- score.json 留审计痕：contestant 申诉时能直接看到判定阈值，避免"阈值是不是中途改过"的争议。

### D7 — `score.json` 顶层 `max_score` 30 → 40，`cpu` 块与 `h264` / `h265` 同级

**选择**：新增同级 `cpu` 块；`objective_total` 上限同步升 40。不在 `h265` 块内嵌 CPU 字段。

**理由**：
- "CPU 是一个独立的 10 分项，恰好基于 H.265 采样"——同级表达更准确，把"度量来源"和"度量归属"
  解耦。
- 字段 `measured_on_codec: "h265"` 显式标注 CPU 是在哪轮采的，便于以后扩展（如未来同时采两 codec）。

### D8 — Sampler 同时纳入 Playwright Chrome 进程树（apply 阶段新增）

**选择**：`_cpu_sampler.Sampler` 新增 `extra_root_pid: int | None = None` 参数。每 tick
枚举两棵树的**并集**（PID 去重）:

1. session == pgid（D1 原口径，contestant 子树）
2. ppid descendants of `extra_root_pid`（Playwright 驱动 + Chrome 主进程 + 全部 Chrome 子进程）

`runner.py` 通过 Playwright 私有 API
`browser._impl_obj._connection._transport._proc.pid` 拿驱动 PID 传入；`AttributeError` 兜底
（SDK 升级时不静默崩，`extra_root_pid=null` 走老口径）。`capture_meta.json.cpu.extra_root_pid`
留痕。

**替代**:
- _保持 D1 纯 PGID 子树_：对 server-side decode contestant 公平，但 client-side decode
  （WASM / WebCodecs）的解码工作发生在 Chrome 进程里，会被漏算。team 2000 实测 0.037%
  → 白拿 10 分。
- _CDP 精确定位 contestant tab 的 renderer process_：用 site-isolation 边界 + DevTools
  Protocol 拿 targetId → PID 映射。最准确，但实施复杂度高一档；当前不必要。
- _baseline 减法_：先跑空白页测 Chrome baseline，再减 contestant 页。需要双倍跑时长 + 噪声大。

**理由**：
- contestant 选用 client-side decode 是 spec 明示允许的实现路径（"`<video>` / `<canvas>` /
  WebCodecs / WASM 都可"）；评测必须公平覆盖这条路径。
- 浏览器树整体纳入会把 Playwright 控制层（CDP 消息处理、screenshot 编码）也算到 contestant 头上，
  但这部分对所有 contestant 一致，相当于固定常数被 `1/ncpu` 归一化基准吸收，不影响相对排名。
- 私有 API 是 Playwright Python 同步绑定唯一稳定路径；带 try/except + 留 audit 字段是把
  "API 兼容性风险"降为"可观察"而非"沉默故障"。

### D9 — 默认排除 chrome GPU process（SwiftShader 噪声过滤；apply 阶段新增）

**选择**：sampler 新增 `exclude_chrome_gpu: bool = True` 参数。识别 chrome GPU process 的
依据是 `/proc/<pid>/cmdline` 字节流**包含**子串 `--gpu-preferences=` 或 `--type=gpu-process`；
命中则永久排除该 PID（不进 baseline / deltas / per_pid_delta）。
`capture_meta.json.cpu.exclude_chrome_gpu` + `excluded_gpu_pids` 留审计痕。

**替代**:
- _不剔除，把 GPU process 也算进 contestant_：但评测机的 headless Chrome 无真 GPU 管线
  配置（`--ozone-platform=headless`），GPU process 跑 SwiftShader 在 CPU 上做软件光栅化。
  team 2000 实测 GPU process 占 union 总量 84%（环境伪影），把 mean 从 3.14% 拉高到 19.54%。
- _改 Chrome 启动参数让它用真 GPU_（A2 路径）：理论可行（评测机有 Intel AlderLake-S 集显
  + `/dev/dri/renderD128`），但 headless Chrome + 真 GPU 在 Linux/Ozone/EGL 下兼容性历史
  不稳，引入更多风险。**留作未来优化项；当前 sampler 层兜底足够。**
- _baseline 减法_：见 D8 同款评估，仍嫌复杂。

**理由**：
- GPU process 在当前评测环境**不代表 contestant 算力**，把它算进总量是 false signal。
- 子串匹配（不是 `split(b"\x00") + startswith`）是因为 Chrome 子进程用
  `prctl(PR_SET_MM_*)` 把 `/proc/<pid>/cmdline` **改写成单一空格分隔字符串**——NUL 拆分
  后只剩一个 chunk，`startswith()` 永远 False。这条踩坑已固化为单测 `tests/test_cpu_sampler.py
  ::test_cmdline_is_chrome_gpu_handles_both_layouts`。
- 默认开启 + 可关闭（`exclude_chrome_gpu=False`）：未来若启用真 GPU 评测，可一行 flip 回去。

## Risks / Trade-offs

- [**Reference fixture 在评测机上 H.265 = 0**，整条 CPU 评分正向通路无 fixture 覆盖] →
  接受现状作为已知遗留；新增 `tests/test_score_cpu.py` + `tests/test_cpu_sampler.py` 覆盖
  scorer / sampler 单测；正向通路靠真实人提交回归。`scripts/test.sh` 门 `total_ge:13`
  不动（reference 仍 = 15 ≥ 13 通过）。

- [**1Hz × ~10s = 9 样本，统计方差较粗**] → 默认值 1.0 留 CLI flag (`--cpu-sample-hz`)
  调试入口；实战发现波动过大时先调 2–5Hz 调参，必要时再升采样率为默认。

- [**5% 阈值在不同核数主机上绝对算力含义不同**] → 这是 D1 归一化决策的内在结果，
  contestant 文档需要明示"占用率指 整机所有核之和 = 100%"。若以后想改回 per-core 归一化，
  只动 `_cpu_sampler.py` 一处。

- [**`evaluator-host.sh` 容器路径下 CPU 永远 = 0**] → 一致返回
  `gate_reason="container_mode_unsupported"`；用户已表态后续会改打包方式，届时再补。
  CI / 验收说明里需要标明"CPU 评分仅在 -local.sh 路径生效"。

- [**`/proc/<pid>/stat` session 字段语义依赖 `setsid` 起 contestant**] → 当前
  `clx_start_contestant` 已用 `setsid`；实现注释里把"SID = PGID = CONTESTANT_PID"前提
  写明，防后人改 lifecycle 时无声破坏 CPU 采样。

- [**进程在采样间消亡 / 新生的边界处理**] → 消亡：忽略 read error；新生：第一次见到的
  jiffies 作基线，下 tick 起计入差值（避免把进程历史 CPU 计入）。这两点写进单测。

- [**CPU sampler 自身的 CPU 开销被计入 contestant**] → sampler 是 runner.py 的 Python 线程，
  跑在 runner 进程里、不在 contestant PGID 内，**不会**自我污染。但 Playwright /
  ffmpeg / Chrome 等评测器进程的 CPU 现在（D8 后）会被计入 union（浏览器树纳入）——
  这是有意的设计取舍，详见 D8 理由段；它在所有 contestant 上是相同常数，归一化分母
  （`100/ncpu`）吸收常数项。

- [**D8 纳入浏览器树后，Playwright 控制层开销算进 contestant**] → 实测 team 2000:
  node driver ~1%、chrome main ~3%。这部分是评测器固有开销，相对排名不受扰。
  如未来发现影响，可升级到"CDP 精确定位 renderer process"（D8 替代方案）。

- [**D9 GPU process 排除依赖 Chrome cmdline 标记**] → `--gpu-preferences=` /
  `--type=gpu-process` 这两个 marker 在 Chrome 主线版本上稳定，但 Chrome rewrite cmdline
  的细节（空格分隔 vs NUL 分隔）可能随版本漂移。已用 `_cmdline_is_chrome_gpu(cmdline)`
  抽出纯函数 + 单测 `test_cmdline_is_chrome_gpu_handles_both_layouts` 固化两种 layout 都识别。
  Chrome 版本升级要回归这条单测。

- [**Playwright 私有 API `_impl_obj._connection._transport._proc.pid` 是不稳定接口**] →
  在 1.4x 系列稳定，但任意 minor bump 都可能改。runner.py 用 try/except AttributeError
  兜底——捕获不到时 `extra_root_pid=null` 退化为 D1 纯 PGID 行为，并写 audit 字段，
  不沉默失败。

## Migration Plan

- 部署：纯代码变更，无运行时迁移、无外部状态、无数据回填。提交合并后 `scripts/test.sh`
  通过即视为部署成功。
- 兼容：`runner.py` `--contestant-pgid` 与 `--cpu-sample-hz` 都是**可选**参数；未传时
  runner 行为退化到现状（不采样、不写 capture_meta.json）。`scorer.py` 读 `h265_metrics.cpu`
  缺失时按门控分支归零。这意味着旧版 `evaluator.sh`、第三方调用方继续工作。
- 回滚：单 commit 回滚即可。`score.json` 的 `max_score`/`cpu` 字段对消费者（仅赛务内部）
  无破坏，回滚后字段消失但消费者只看 `objective_total`。

## Open Questions

1. **容器路径未来如何打包**：用户已表态会改打包方式，第一版仅 host 原生路径生效；改造方案
   不在本变更范围。
2. **正向通路 fixture 缺失**：构造真软解 H.265 contestant fixture 成本过高，第一版接受
   "正向通路无端到端 fixture"作为遗留事项。team 2000（apply 阶段实测）实质充当了一次手动
   正向通路验证；未来仍应固化为 `test_submissions/` 自动 fixture。
3. **`DEFAULT_SAMPLE_HZ` CLI flag 何时回收**：标记为 debug-only；稳定后从 CLI 移回纯常量
   并更新 CLAUDE.md / spec。回收时机由后续 retrospective 决定。
4. **采样器是否升级为方案 B（全程时间线 + 走势图）**：取决于实战是否真有走势诊断需求；
   升级时 `score.json.cpu` 数据契约不破。
5. **D9 GPU 排除是兜底；最终是否切到真 GPU（A2 路径）**：评测机有 Intel AlderLake-S 集显 +
   `/dev/dri/renderD128`，理论上 Chrome 走 `--use-gl=angle --use-angle=gl-egl` 可以卸到
   硬件。headless + 真 GPU 在 Linux/Ozone 下兼容性历史不稳，需专项试。短期 D9 sampler
   层兜底足够。
6. **D8 浏览器树是否需要"只跟随 contestant tab 的 renderer"精度**：目前是粗粒度——整个
   Playwright 启的 Chrome 子树都纳入，含 chrome main / utility / 同进程其他 renderer。
   单 contestant 评测下浏览器内只有一个 tab，问题不大；若以后改成 multi-tab fixtures
   或并发评测，需要 CDP-precision 路径。
7. **`CPU_GATE_H265_FPS_RATIO` 默认值漂移到 0.10**（apply 阶段调整）：是否长期保留为
   0.10 仍是 open——调到 0.10 是为了让 i-frame-only 实现也进入 CPU 评分通路；正式赛场
   可能想调回 0.25 阻止低帧率"假装播放"白拿 CPU 分。retrospective 应给出推荐值。
