## Why

当前赛题区分度不足：多名参赛者都拿到 29+ 分（2011 队最近一次 29.97/30），各档子分几乎全部打满，分数无法拉开差距。根因是负载偏轻——4K 码率仅 8M、CPU 在最轻的 2K 轮采样、且本地回环带宽近乎无限，导致"能正常播放"与"架构高效"的作品得分趋同。本次换题在不改变 30 分总分与对外字段结构的前提下加重负载，让架构效率的差异在分数上显现出来。

## What Changes

- **将 4K profile 码率从 8M 提升到 16M**（`lib/profiles.py::PROFILES["4k"].bitrate`），需重新生成 4K 参考流与水印参考帧。**实现发现**：原合成水印帧（纯色块+平滑渐变）高度可压缩，x265 在接近无损质量下也只需 ~8M，`-b:v 16M` 用不满。因此本 change 同时**给参考帧加入确定性高熵背景**（`lib/watermark.py`：按帧号做种子的中等尺度随机色块），真正把 4K 流填到 ~16M、加重解码 CPU 负载，从而让迁到 4K 轮的 CPU 子分拉开差距。高熵尺度需在「填满码率」与「截图在 1280×720 捕获后仍保持 SSIM」之间平衡；正确性 SSIM 阈值在重生成后实测决定。
- **对参赛者提交进程树施加 100Mbps 物理出向带宽限制**：复用现有 systemd 瞬态单元（cgroup v2）作为流量分类边界，用 nftables 按 `socket cgroupv2` 给参赛者发出的 IP 包打 fwmark，再用 tc/htb 在 `lo` 上对带标记流量限速到 100Mbps。评测端自有进程（MediaMTX、Chromium、runner/analyzer/scorer）不受限。超限通过物理排队/丢包自然背压，不引入新的违规判定逻辑；实测带宽写入 `report.html` 供人工审查。
- **将 CPU 采样轮从 2K 迁移到 4K**（`cpu_sampled` 标志从 `2k` 移到 `4k`），并按 16M + 限速后的新负载重新校准 `scorer.py` 的 CPU 子分阈值。CPU 子分仍为 0–5 分。
- **新增带宽限速器 preflight 与 cleanup**：仿照现有"Contestant Memory Limiter Preflight"，run 前校验 `CAP_NET_ADMIN`/tc/nft 可用性，run 后在 cleanup trap 中幂等拆除 qdisc 与 nft 规则。
- **对外题目说明新增带宽约束语义**：参赛者提交进程树的全部 IP 层出向流量共享 100Mbps；进程间若需高速传输应改用 unix socket / 共享内存（不走 IP 层，不受限）。
- 总分结构保持 30 分不变（2K: 5+5，4K: 5+10，CPU: 5），`score.json` 对外字段结构不变。

## Capabilities

### New Capabilities
<!-- 无新建独立 capability：本仓库为单一 evaluator capability，所有需求增删改均落在其 delta spec 中 -->

### Modified Capabilities
- `evaluator`: 新增"Contestant Bandwidth Limit"与"Contestant Bandwidth Limiter Preflight"两条需求；修改"Scoring"与"Contestant CPU Usage Measurement"将 CPU 采样轮由 2K 改为 4K；修改"Reference Stream Generation"将 4K 码率锁定为 16M；修改"Orchestration and Cleanup"纳入带宽限速器的生命周期。

## Impact

- **配置/数据**：`lib/profiles.py`（4K 码率 8M→16M、`cpu_sampled` 迁移）；`streams/h265_3840_2160.mp4` 与 `reference/4k/` 需经 `scripts/prepare_streams.sh` 重新生成。
- **脚本**：`scripts/_contestant_lifecycle.sh`（新增带宽限速 setup/teardown，复用内存单元）、`scripts/evaluator.sh`（preflight + cleanup trap 接入）、`scripts/build.sh`（私有 tc/nft 的 setcap 或受限 sudoers 授权）。
- **评分**：`scorer.py` 的 `CPU_PROFILE` 随 `cpu_sampled` 自动切到 4K，CPU 阈值常量按新负载重校准；`report.py` 增加实测带宽诊断展示。
- **权限**：限速需要 `CAP_NET_ADMIN`，沿用项目既有 setcap 模式（参考 mediamtx 的 `CAP_NET_BIND_SERVICE`），不以 root 运行评测脚本。
- **对外契约**：参赛者契约（URL/env/DOM 信号）不变；仅新增带宽约束的题面说明。`score.json` 字段结构不变。
- **测试**：新增带宽限速单测与集成测试（仿 `test_contestant_memory_limit.py`）、CPU 采样迁移的阈值边界测试（`test_score_cpu.py`）、4K 码率断言（`test_profiles.py`），全部遵循 TDD 先测后实现。
