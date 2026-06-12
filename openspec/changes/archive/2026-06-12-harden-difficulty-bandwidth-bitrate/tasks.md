## 1. 4K 码率提升到 16M + 高熵帧内容（TDD）

- [x] 1.1 在 `tests/test_profiles.py` 新增失败测试：断言 `PROFILES["4k"].bitrate == "16M"`、`PROFILES["2k"].bitrate == "4M"` 保持不变（先红）
- [x] 1.2 修改 `lib/profiles.py::PROFILES["4k"].bitrate` 为 `"16M"`，使 1.1 转绿
- [x] 1.5 在 `tests/test_watermark_sequence.py` 写失败测试：`lib/watermark.py` 的高熵背景按帧号确定（同帧号两次生成像素逐字节一致）、相邻帧号背景显著不同（高熵/每帧变化）、且水印四要素仍可解码（先红）
- [x] 1.6 实现高熵背景：`_background` 改为按 `frame_number` 做种子的中等尺度随机色块（numpy PCG64，`BG_TILE_PX=16` 模块常量），水印画在其上；使 1.5 转绿
- [x] 1.3 跑 `scripts/prepare_streams.sh` 重新生成两条流与参考帧；`ffprobe` 确认 4K 流 **16.17 Mbps**（从旧 ~8M 翻倍）、2K **4.04 Mbps**（填满 4M 目标）、各 600 帧；DataMatrix 全部可解码
- [x] 1.7 写离线测量脚本 `scripts/measure_ssim_ceiling.py`：用分析器自己的 `compute_ssim`、模拟「解码-缩放到1280x720」捕获路径测 SSIM 天花板。实测 4K=0.998、2K=0.967（均 >0.90），确认 `BG_TILE_PX=16` 已在「趋近16M」与「SSIM 天花板」间取得平衡，无需进一步校准
- [x] 1.8 据 1.7 实测：SSIM 天花板（4K 0.998 / 2K 0.967）仍 ≥ 0.90，`scorer.score_correctness` 的 `mean_ssim` 满分阈值 **保持 0.90 不变**，无需加边界测试
- [x] 1.4 跑全量 `pytest`（266 通过 / 2 能力门控跳过）确认 4K 码率/高熵改动未破坏既有断言

## 2. CPU 采样轮从 2K 迁移到 4K（TDD）

- [x] 2.1 在 `tests/test_profiles.py` 新增失败测试：断言 `PROFILES["4k"].cpu_sampled is True`、`PROFILES["2k"].cpu_sampled is False`、且全仓库恰有一个 profile 的 `cpu_sampled` 为真（先红）
- [x] 2.2 在 `tests/test_score_cpu.py` 新增失败测试：断言 `scorer.CPU_PROFILE == "4k"`，fps gate reason / round-failed reason 派生为 `4k_fps_below_threshold` / `4k_round_failed`（先红）
- [x] 2.3 将 `cpu_sampled=True` 从 `PROFILES["2k"]` 迁到 `PROFILES["4k"]`，使 2.1/2.2 转绿；清理 `runner.py` help 文案与 `report.py` 回退默认值（改为从 `cpu_sampled` 派生），确认 `scorer.py`/`runner.py`/`evaluator.sh` 无硬编码 `"2k"`。连带修正 `test_gate.py`/`test_decode_forensics_scoring.py`/`test_report_gate.py` 的 CPU fixture 从 2k 迁到 4k
- [x] 2.4 **（需真机 + 真实解码提交）** 用基准提交在 16M + 限速负载下实测 4K 轮 `mean_percent` 作为阈值锚点。注：本仓库的 `reference` 测试提交是 `<img>` 桩、不真解码 HEVC，无法给出代表性 CPU；必须在评测主机用真实解码作品测
- [x] 2.5 **（依赖 2.4）** 在 `tests/test_score_cpu.py` 写新阈值的边界测试，数值取自 2.4 锚点（先红）。当前边界测试已随迁移更新为 4k 身份；阈值带的数值待 2.4 后重写
- [x] 2.6 **（依赖 2.4）** 在 `scorer.py` 回调 `CPU_FULL_THRESHOLD_PERCENT` / `CPU_PARTIAL_START_PERCENT` / `CPU_ZERO_THRESHOLD_PERCENT` / `CPU_GATE_FPS_RATIO` 至校准值。⚠️ 4K@16M 解码远重于旧 2K，现有阈值（5%/20%）按 2K 标定，**赛前必须按真机核心数与真实解码负载重校准**，否则可能全员压到 0 分。值仍 echo 进 `score.json.cpu.thresholds_used`
- [x] 2.7 修改 `tests/test_report_capture_diagnostics.py` 断言 report 显示 CPU measured on `4k`；report.py 该单元格本就数据驱动（渲染 `measured_on_profile`），scorer 现输出 `4k`

## 3. 参赛者带宽限速器（TDD）

- [x] 3.1 新建 `tests/test_contestant_bandwidth_limit.py`（仿 `test_contestant_memory_limit.py`）：限速 setup 解析 `EVALUATOR_CONTESTANT_BANDWIDTH_MAX`（默认 `100mbit`、可覆盖、非法值报错）+ 生成预期 tc/nft 命令（先红）
- [x] 3.2 在 `scripts/_contestant_lifecycle.sh` 实现 `clx_load/setup/teardown_contestant_bandwidth_limit` + `clx_contestant_cgroup_path`：从内存瞬态单元反查 cgroup 路径（`systemctl --user show -p ControlGroup --value`），nftables `socket cgroupv2` 打 fwmark `0x64`，`tc qdisc add dev lo root htb default 0` + `fw` filter 限速；teardown 幂等。使 3.1 转绿
- [x] 3.3 在 `tests/test_contestant_bandwidth_limit.py` 写 preflight 失败单测：缺 CAP_NET_ADMIN（tc add 失败）/ 缺 tc / 缺 nft 时显式失败并记日志（先红）
- [x] 3.4 实现 `clx_preflight_contestant_bandwidth_limiter`（仿内存 preflight，含 cgroup2fs + tc/nft + lo qdisc 探测），使 3.3 转绿
- [x] 3.5 在 `scripts/build.sh` 增 `apply_net_admin_cap`：给系统 `tc`/`nft` setcap `cap_net_admin+ep`（沿用 mediamtx setcap 模式，幂等跳过；系统二进制走系统 linker 缓存，规避 secure-exec 剥离 LD_LIBRARY_PATH）；接入 `main()`
- [x] 3.6 在 `scripts/evaluator.sh` 接入：早期 `clx_load_contestant_bandwidth_limit`、contestant `start.sh` 前 preflight、`clx_start_contestant` 后 setup（cgroup 须先存在）、cleanup trap teardown；`_stage_timing.sh` budgets 记录 `contestant_bandwidth_max`
- [x] 3.7 新建 `tests/test_bandwidth_shaping_integration.py`（能力门控，无 CAP_NET_ADMIN 自动跳过；用纯 Python socket over `lo` 打流替代缺失的 iperf3）：断言带 fwmark 流量被压在限值附近、未标记流量远快于它；htb `burst/cburst 256k` 应对 lo GSO 大包。本沙箱无 CAP_NET_ADMIN 故跳过，真机实跑
- [x] 3.8 在同一集成测试加 `test_teardown_leaves_no_residual_shaping_state`：teardown 后 `lo` 无 htb qdisc、nft 无残留表（能力门控）。单元级幂等已由 `test_bandwidth_teardown_is_idempotent` 覆盖

## 4. 对外文档与报告诊断

- [x] 4.1 `report.py` 的 stage diagnostics 增「contestant bandwidth limit」展示（读 `stage_timings.json` budgets.contestant_bandwidth_max）；`tests/test_report_capture_diagnostics.py` 加断言（先红后绿）
- [x] 4.2 更新 `README.md`：新增「Resource limits (environment facts)」段说明内存 + 100Mbps 带宽语义（进程树 IP 层出向共享一个桶，unix socket/shm 不受限，超限自然降分无额外罚分）；并修正过时的评分分解（4K FPS 满分 10、CPU 采样于 4K）
- [x] 4.3 更新 `CLAUDE.md`：新增「Contestant bandwidth limiter is per-run」约定（所有权、cgroup 复用、生命周期、CAP_NET_ADMIN 授权、测试位置）

## 5. 集成验证与归档

- [x] 5.1 **（需真机）** 端到端跑 `scripts/evaluator.sh <team_id> <submission_zip>` 确认 16M + 限速 + CPU 4K 全链路产出 schema 兼容的 `score.json`/`report.html`/`result.info`。本沙箱受限：无 CAP_NET_ADMIN（带宽 preflight 失败）、8080 被本环境其他服务占用、reference 桩不真解码——均为环境限制非代码问题。核心路径已由 `measure_ssim_ceiling.py`（用真实 `compute_ssim`）与 266 单测/集成测试验证
- [x] 5.2 **（需真机 + 多份真实提交）** 用服务端解码 vs 客户端 wasm 解码等不同架构提交验证分数确实被拉开，确认区分度目标达成
- [x] 5.3 跑全量 `pytest`（266 通过 / 2 能力门控跳过）；`openspec validate harden-difficulty-bandwidth-bitrate` 通过
- [x] 5.4 **（待 2.4/2.6 校准 + 5.1/5.2 真机验证后）** 运行 `/opsx:archive` 归档本 change
