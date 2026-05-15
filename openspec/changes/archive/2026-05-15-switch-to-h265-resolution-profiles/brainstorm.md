# Brainstorm — switch-to-h265-resolution-profiles

## Design Summary

将评测从「双 codec 轮次（H.264 + H.265）」转换为「双 H.265 分辨率轮次（2K + 4K）」。删除 H.264 所有相关代码、流、参考帧、测试夹具；新增 4K（3840×2160 @ 25fps @ 8Mbps）轮次；统一 RTSP 取流端口为 554、路径写死；选手前端 URL 由 `?codec=` 改为 `?profile=`；总分由 40 → 30 重新分配（2K 10 + 4K 10 + CPU 10）。

实现采用 **方案 A：Profile 抽象层**——新增 `lib/profiles.py` 作为单一真理源，runner / analyzer / scorer / 脚本统一从 `PROFILES[name]` 获取 width/height/fps/bitrate/rtsp_path/reference_dir 等参数。

详细技术设计见 `design.md`。

## Alternatives Considered

### 方案 A：Profile 抽象层（采用）

- **做法**：新建 `lib/profiles.py`，定义 `ProfileSpec` 与 `PROFILES = {"2k": ..., "4k": ...}`。runner/analyzer/scorer/scripts 把 `--codec` 入参替换为 `--profile`，所有派生参数从 `PROFILES[name]` 查表。
- **优点**：
  - 单一真理源，width/height/fps/bitrate/RTSP path/reference dir 不会再散落各处
  - 未来加 8K profile 只需追加一条配置 + 一份录像文件
  - scorer.py 通过遍历 PROFILES 自然支持 N 个 profile；CPU 门控通过 `spec.cpu_sampled` 标志位声明
- **缺点**：改动面较大（10+ 个文件），需把 codec 命名习惯一次性迁掉
- **为何采用**：代码库规模小，重构成本可控；当前 codec 抽象正是「换维度」事件的引爆点，与其拼凑第二维度，不如换成更纯粹的 profile

### 方案 B：最小重命名（codec → profile）

- **做法**：在所有源文件中 find/replace `h264` → `2k`、`h265` → `4k`（按上下文），保留两套并列硬编码路径
- **优点**：diff 最小、思维转换最少
- **缺点**：未来加分辨率仍需类似规模重构；profile 维度与 codec 维度耦合在变量名里
- **为何未采用**：未来扩展性差；变量名残留 codec 概念会让后续维护者困惑

### 方案 C：双轴维度（codec 保留 + profile 新增）

- **做法**：把 codec="h265" 当 fixed，profile 作为新维度；保留 `EXPECTED_FPS["h265"]` 形态
- **优点**：评分模块概念保留、改动局部
- **缺点**：每处都要传 `(codec, profile)` 元组，复杂度反而增加；codec 只有一个值时双轴是过度设计
- **为何未采用**：YAGNI——目前没有跨 codec 的对比需求

## Agreed Approach

**方案 A**（Profile 抽象层），配合以下默认参数：

- 4K 流：3840×2160 @ 25fps @ 8Mbps，30 秒（与 2K 一致，复用现有 frame range 逻辑）
- RTSP 端口：554（容器内需 `--cap-add=NET_BIND_SERVICE`；build host 上 MediaMTX 二进制做 `setcap cap_net_bind_service=+ep`）
- 选手 URL：`http://127.0.0.1:8080/play?profile=2k|4k&autoplay=1`
- CPU 子分仅在 4K 拍摄窗口采样，仅以 4K fps 门控
- x264 submodule：保留（移除作为 tasks 中的可选清理项；ffmpeg 可能仍依赖配置）

## Key Decisions

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 总分重新分配 | 2K (5+5) + 4K (5+5) + CPU 10 = 30 | 用户给出的官方评分表 |
| 评分粒度 | 现行三档，按比例减半 | 正确性：5/2/0；fps：5/3/0（max 5 不变） |
| RTSP 端口 | 554（标准 RTSP），容器加 `NET_BIND_SERVICE` | 用户原始需求；contestant 看到标准端口 |
| CPU 采样范围 | 仅 4K profile 拍摄窗口 | 4K 负载更重，CPU 区分度更高 |
| Profile 抽象 | 新增 `lib/profiles.py` | 单一真理源；为未来加 profile 预留 |
| 4K 流参数 | 25fps、8Mbps、30s、libx265、GOP=50 | 与 2K 时长一致；带宽按用户要求 |
| 流文件命名 | `streams/h265_<W>_<H>.mp4` | 与 MediaMTX path 同形态 |
| 参考帧目录命名 | `reference/<profile>/`（`2k`/`4k`） | 删除 codec 维度 |
| 指标文件命名 | `<profile>_metrics.json` | 同上 |
| score.json key | `2k`、`4k`、`cpu`、`objective_total`、`max_score=30` | 删除 `h264`/`h265` 键 |
| URL 契约迁移 | 不保留 `?codec=` 兼容 | 内部评测系统，无外部依赖；干净替换 |

## Open Questions

下列问题不阻塞设计，留在 tasks/verify 阶段验证或决定：

1. **测试夹具（test_submissions/）需要重做**：7 个夹具全部基于 codec 概念，需要重新设计覆盖 2K / 4K 的成功、静态图、伪 watermark、partial decode 等场景。归到 tasks。
2. **reference.zip 在无硬件 HEVC 主机上的稳定性**：当前 reference 使用 `<video>` 标签播放 HEVC，依赖 Chrome 的 `--enable-features=PlatformHEVCDecoderSupport` 软解。删除 H.264 后 reference 完全依赖 HEVC 软解，若 4K 软解超出主机能力可能 fps 不达标。如果 reference 不稳定，考虑切到 WebCodecs / wasm 路径。verify 阶段单独评估。
3. **x264 submodule 是否删除**：本次保留，作为可选清理项放入 tasks。需先确认 ffmpeg 配置是否硬依赖 libx264（如果是，删除会破坏 build）。
4. **CLAUDE.md 文档同步**：现有 CLAUDE.md 大量描述 codec 维度与 8554 端口。归到 tasks，作为最后一步统一更新。
5. **Playwright 视口尺寸 vs 4K 截图**：runner 保持 1920×1080 视口，截图后由 analyzer 缩放到参考分辨率比较 SSIM。4K 参考帧 3840×2160 时，SSIM 阶段下采样到共同 960px——该路径 analyzer.py 已经支持，但需要在 verify 阶段实测确认 4K SSIM 不被缩放伪影污染。
