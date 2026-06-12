## Context

赛题区分度不足（多队 29+/30，子分普遍打满）。本仓库为单机评测：评测脚本与被测作品跑在同一台 Ubuntu 24.04 上，参赛者前端把解码后的视频帧通过本地 IP（localhost）喂给评测端用 Playwright 驱动的 Chromium 截图比对。现有负载偏轻——4K 仅 8M 码率、CPU 在最轻的 2K 轮采样、本地回环带宽近乎无限——使"能播"与"高效"的作品趋同。

约束（来自探索阶段决策与项目惯例）：
- 总分 30 不变，`score.json` 对外字段结构不变（已对外承诺）。
- 4K 码率锁定 16M，不做校准实验。
- 限速必须是**物理层**且**同机**：评测端与被测作品共存一台机器，限速只能限被测进程、不能误伤评测端自有进程。
- 项目已有 cgroup v2 强约束（内存限制 preflight 要求 `cgroup2fs`）与 setcap 授权先例（`build.sh` 给 mediamtx `CAP_NET_BIND_SERVICE`）。
- 源码构建规则：不得使用 apt 的 ffmpeg/x265 等，统一走 `third_party/install/`。
- TDD：先写失败测试再实现。

## Goals / Non-Goals

**Goals:**
- 4K profile 码率 8M→16M，重生成 4K 流与参考帧，加重解码与渲染负载。
- 对参赛者提交进程树施加 100Mbps 物理出向带宽上限，复用现有内存限制用的 systemd 瞬态单元（cgroup v2）作为分类边界。
- CPU 采样轮由 2K 迁移到 4K，并按新负载重校准 CPU 子分阈值。
- 全程保持总分 30、字段结构不变、参赛者契约（URL/env/DOM）不变。
- 新增带宽限速器的 preflight 与幂等 cleanup，失败即作为基础设施故障显式中止。

**Non-Goals:**
- 不引入"超带宽=违规零分"的判定逻辑——超限由物理排队/丢包自然背压、自然降分。
- 不做入向（ingress）整形；只整形参赛者出向（参赛者→浏览器的视频数据边）。
- 不改 `score.json` 字段结构、不改总分 30、不动 2K 码率（4M）。
- 不改参赛者运行契约；仅在对外题面增加带宽约束说明（文档层，不在本仓库 spec 的契约需求内）。
- 不做多路并发流、不做端到端延迟评分（本轮范围外）。

## Decisions

### D1. 限速分类边界：复用内存限制的 systemd 瞬态单元（cgroup v2）

参赛者 `start.sh` 及全部子进程已经跑在 `scripts/_contestant_lifecycle.sh` 创建的 systemd 瞬态单元里（`MemoryMax` 挂在其上，`setsid` 使会话首进程 PID = PGID）。该单元在 cgroup v2 下有唯一的 cgroup 路径。我们直接复用这个 cgroup 作为流量分类边界——无需为限速再建一个 cgroup，单元的生命周期已由内存限制逻辑管理。

**备选**：为限速单独建 net_cls cgroup（v1）。否决——host 是 cgroup v2 only（内存 preflight 已要求 `cgroup2fs`），net_cls 是 v1 机制，不可用。

### D2. 标记 + 整形：nftables `socket cgroupv2` 打 fwmark → tc/htb 在 `lo` 限速

同机所有 localhost 流量走 `lo`，不能直接对 `lo` 整体限速（会误伤评测端）。按"发包进程所属 cgroup"区分：

```
参赛者进程发包 → NF OUTPUT/POSTROUTING(nft): socket cgroupv2 == 参赛者单元 → meta mark 0x64
              → tc egress(lo): htb, filter fw 0x64 → class 100mbit；default → 直通不整形
```

- **标记**：nftables 在 output/postrouting 钩子按 `socket cgroupv2 level N "<unit-cgroup-path>"` 匹配参赛者发出的包，置 `meta mark 0x64`。本地生成包的内核路径 `LOCAL_OUT → POST_ROUTING → dev_queue_xmit(tc egress)`，保证 mark 在 tc 分类前已设置。
- **整形**：`tc qdisc add dev lo root htb default 0`（default 0 = 无标记流量走默认类，不整形直通），`fw` filter 把 `mark 0x64` 导入限速到 `rate 100mbit` 的 htb class。
- 评测端自有进程（MediaMTX 推 16M 源流、Chromium、runner/analyzer）不在该 cgroup，发包无标记，落入 default 类、不受影响。

**备选 a：CDP `Network.emulateNetworkConditions`**。否决——只覆盖 Chromium 自身 HTTP，参赛者若用 WebRTC/原生 socket/独立中继进程发流可绕过；非物理。
**备选 b：iptables `-m cgroup --path` 打标**。可行但 Ubuntu 24.04 默认 nft 后端，统一用 nftables 减少二进制依赖。

### D3. 100Mbps 的语义：提交进程树全部 IP 层出向流量共享一个桶

限速桶按 cgroup 聚合，因此参赛者进程树的**全部 IP 层出向流量共享 100Mbps**——包括进程间的 localhost TCP。这是合理工程约束：参赛者若需进程间高速传裸帧，应改用 unix socket / 共享内存 / pipe（不走 IP 层，不受限）。该语义写入对外题面说明，让参赛者设计时知情。

### D4. 限速放在哪个脚本层

- setup/teardown 函数放 `scripts/_contestant_lifecycle.sh`（与内存限制同文件，复用单元 cgroup 路径解析）。
- `scripts/evaluator.sh` 在 contestant 启动后、capture 前接入 setup；cleanup trap 中 teardown（幂等：先删 qdisc/nft 规则再退出，无规则也不报错）。
- preflight 仿 "Contestant Memory Limiter Preflight"：校验 `CAP_NET_ADMIN`、tc/nft 可用、`lo` 可加 qdisc；失败即基础设施故障，在跑 contestant 前显式中止。

### D5. 权限：沿用 setcap 模式，不以 root 跑评测

`tc`/`nft` 需 `CAP_NET_ADMIN`。沿用 `build.sh` 给 mediamtx setcap 的既有模式，给评测使用的 tc/nft（或一个薄封装）授 `CAP_NET_ADMIN`，或加一条最小化 sudoers 规则。注意 setcap 二进制在内核 secure-exec 下会被剥离 `LD_LIBRARY_PATH`——与 mediamtx 同样的坑，依赖 ldconfig 系统缓存解析（项目已注册 `third_party/install/lib`）。

### D6. CPU 采样轮 2K→4K：靠 profile 标志驱动，scorer 自动跟随

`scorer.CPU_PROFILE = next(name for spec.cpu_sampled)`，已经是数据驱动。把 `cpu_sampled=True` 从 `2k` 移到 `4k` 后，scorer/runner/evaluator.sh 全部自动跟随（无硬编码 "2k"）。需同步：

- `scorer.py` CPU 阈值常量（`CPU_FULL_THRESHOLD_PERCENT` 等）按 16M+限速后的 4K 负载重校准——4K 负载更重，原 2K 标定的 5%/20% 带不再合适。
- spec 中所有写死 "2K profile"/"measured_on_profile=2k" 的措辞改为 4K。
- `report.py` "CPU measured on 2K" 文案改 4K。

**校准来源**：4K 阈值的具体数值在实现阶段用基准提交实测确定（与 16M 直接锁定不同，阈值必须实测——否则区分度无从谈起）。设计层只锁定"采样轮=4K、子分仍 0–5、评分公式形状不变"。

### D7. 4K 码率 8M→16M：改 PROFILES + 高熵帧内容 + 重生成

`lib/profiles.py::PROFILES["4k"].bitrate = "16M"`。但**实现实测发现**：原 `lib/watermark.py::_background` 生成的是平滑渐变×32px 条纹，加上纯色水印块，内容极度可压缩——x265 在 Avg QP≈0.42（接近无损）下，4K 也只产出 ~8.2 Mbps，`-b:v 16M` 只是用不满的 VBV 上限。`-maxrate` 是天花板不是地板，可压缩内容无法靠 ABR 填到 16M。

因此把 4K 真正加重的唯一办法是**给参考帧注入确定性高熵内容**。设计：

- **中等尺度随机色块背景**：`_background` 改为按 `frame_number` 做种子的 numpy PRNG（PCG64，跨机确定性），生成 `(tiles_h, tiles_w, 3)` 随机 uint8 后 `np.repeat` 放大为 `BG_TILE_PX` 像素的色块。每帧不同 → 大帧间残差 → 重解码 + 高码率；确定性 → 参考 PNG 跨次重生成字节一致。水印四要素仍由 `draw_watermark` 画在背景**之上**，保持可解码。
- **尺度权衡（关键约束）**：捕获在 1280×720 视口，`analyzer.py` 把截图放大到参考尺寸再算 SSIM。4K 上比 ~6–9px 更细的内容在 720p 截图里被抹掉，会让所有人 SSIM 崩。所以 `BG_TILE_PX` 不能太小：细则填码率/加负载但杀 SSIM，粗则保 SSIM 但填不满。取折中（实测 16–24px@4K 量级）并校准。
- **两条 SSIM 上限分别度量**：(A) 编码上限 = SSIM(解码4K, 参考4K)，反映 yuv420p+x265 对内容的还原；(B) 捕获上限 = SSIM(放大(缩小(参考4K)), 参考4K)，反映 720p 截图路径本身的代价。分析器实际比较 ≈ (A)∘(B)∘选手解码。重生成后用「编码-回解-缩放」自一致脚本离线测出选手能达到的 SSIM 天花板，据此定阈值。

`prepare_streams.sh` 从 PROFILES 读参数重生成两条流与参考帧。x265 仍 `hvc1 yuv420p`、GOP 40、scenecut=0、20fps/30s/600 帧不变。高熵背景由共享的 `_background(width,height,frame_number)` 生成，因此 **2K 与 4K 都会用高熵背景**——这更简单（无需按 profile 分支）也更有区分度。2K 仍是轻负载基线：分辨率更低（2560×1440）且码率目标仍为 4M（不变），自然比 16M 的 4K 轻得多；CPU 子分只在 4K 轮采样，2K 的负载不进 CPU 计分。`BG_TILE_PX` 按 4K 的「趋近16M vs SSIM 天花板」校准，2K 复用同一尺度。

## Risks / Trade-offs

- **[lo 上 GSO 大包导致限速精度漂移]** → loopback 包可达 64KB，htb `burst`/`cburst` 需按大包校准；实现阶段用 `iperf3` 在 cgroup 内实测"标记流量压在 100M±5%、未标记流量不受影响"作为第一个集成测试门槛。必要时对 contestant cgroup 关 lo 的 GSO/GRO（或用 `tc ... mtu` 调整）。
- **[nft `socket cgroupv2` 路径解析]** → systemd `--user` 瞬态单元的 cgroup 路径在 `user@<uid>.service` 下层级较深，nft `level N` 需正确取到单元那一层；解析逻辑要从单元名反查 cgroup 路径（`systemctl --user show -p ControlGroup`），不能写死层级。
- **[setcap 剥离 LD_LIBRARY_PATH]** → 与 mediamtx 同坑；tc/nft 若依赖 `third_party/install/lib` 须经 ldconfig 缓存。优先用系统自带 tc/nft（它们不在源码构建清单内，属基础网络工具，非 ffmpeg/x265 那类）以规避。
- **[16M 4K 流体积与磁盘]** → 30s×16M ≈ 60MB MP4，可接受；`streams/`、`reference/` 本就 git-ignore。
- **[4K CPU 阈值重校准误伤]** → 阈值定太严会把多数队压到 0、定太松仍打满。缓解：用现有 29.97 分提交跑基准，取其 mean_percent 作为"满分/部分/零分"带的锚点，留出区分空间；阈值是模块级常量，回调一行。
- **[限速放大评测噪声]** → 物理限速 + 4K 重负载会让 FPS/CPU 测量方差变大；report.html 增加实测带宽诊断，便于人工复核异常低分是否限速副作用。
- **[回滚]** → 三块改动相互独立：码率、限速、CPU 采样轮可分别回退。码率回退=改回 8M 重生成；限速回退=不接入 setup（preflight 与函数留存但不调用）；CPU 轮回退=`cpu_sampled` 标志移回 2K。

## Migration Plan

1. 改 `PROFILES`（4K 16M + `cpu_sampled` 迁移），跑 `test_profiles.py` 断言（先红后绿）。
2. `prepare_streams.sh` 重生成 4K 流与参考帧。
3. 实现限速 setup/teardown/preflight + cleanup 接入 + setcap 授权，先过单测再过 `iperf3` 集成测试。
4. 用基准提交实测 4K 负载，校准 CPU 阈值常量，过 `test_score_cpu.py` 边界测试。
5. report.py 文案与实测带宽诊断。
6. 更新对外题面说明（带宽语义）。

## Open Questions

无（探索阶段已收敛：限速语义、执行层、CPU 采样轮、码率均已定）。阈值具体数值留待实现阶段基准实测，不属设计悬留。
