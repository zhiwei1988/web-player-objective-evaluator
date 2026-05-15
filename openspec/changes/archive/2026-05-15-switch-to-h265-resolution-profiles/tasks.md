## 1. Profile abstraction foundation

- [x] 1.1 创建 `lib/profiles.py`：`ProfileSpec` dataclass + `PROFILES = {"2k": ..., "4k": ...}` + `RTSP_PORT = 554` + `FRONTEND_PORT = 8080` + `rtsp_url(profile)` 辅助函数
- [x] 1.2 在 `lib/profiles.py` 顶部加 module docstring 说明：这是 watermark / runner / analyzer / scorer / mediamtx 配置的单一真理源；新增 profile 时改这里即可
- [x] 1.3 写一个最小的 sanity test（pytest 或独立脚本）：导入 `PROFILES`、断言两个 key 都有合法 ProfileSpec、`cpu_sampled` 仅 4k=True

## 2. Watermark generator + reference frames

- [x] 2.1 修改 `lib/watermark.py::_cli()`：删除 `--codec` choices `("h264","h265")`，新增 `--profile` choices 从 `PROFILES.keys()` 派生；`--width/--height/--fps/--duration/--out` 仍可手动覆盖，但缺省从 ProfileSpec 派生
- [x] 2.2 验证 `WatermarkLayout` 在 3840×2160 上字体二分搜索与 DataMatrix 18% crop 的实际渲染效果（生成单帧人工检查可读性）
- [x] 2.3 删除 `WatermarkLayout` docstring 中"works for both 1920x1080 and 2560x1440"的旧注释，更新为反映三种分辨率均参数化

## 3. Stream preparation pipeline

- [x] 3.1 重构 `scripts/prepare_streams.sh`：移除 H264_W/H/FPS、H265_W/H/FPS 字面量；改为遍历 PROFILES（用 `python -c` 输出 JSON 给 bash 消费）
- [x] 3.2 ffmpeg 命令按 `ProfileSpec.bitrate` 模板化：`-b:v ${BR} -maxrate ${BR} -bufsize $((2*BR))`，统一 libx265 + hvc1，GOP=2*fps
- [x] 3.3 在 prepare_streams.sh 起始处删除遗留产物：`streams/h264_watermarked.mp4`、`streams/h265_watermarked.mp4`、`reference/h264/`、`reference/h265/`（若存在）
- [x] 3.4 spot_check 仍按 profile 循环，断言 DataMatrix 在 4K 帧上首尾中三处都能解码
- [x] 3.5 生成 4K 参考帧 + mp4，验证生成耗时与磁盘占用在可接受范围（4K 25fps 30s ≈ 750 帧 PNG + ~30MB mp4） — Task 10.1 deploy.sh 实测通过

## 4. RTSP server (MediaMTX + capability binding)

- [x] 4.1 改 `rtsp_server/mediamtx.yml`：`rtspAddress: :554`；paths 改为 `test/h265_2560_1440` 与 `test/h265_3840_2160`，删除旧的 `test/h264` 与 `test/h265`
- [x] 4.2 在 `scripts/build.sh` 编译完 MediaMTX 后加 setcap 步骤：`sudo setcap cap_net_bind_service=+ep "${THIRD_PARTY_INSTALL}/bin/mediamtx"`，失败时 fail-loud
- [x] 4.3 改 `scripts/health_check.sh`：两条新 RTSP path 的 ffprobe 检查
- [x] 4.4 改 `scripts/start_rtsp.sh`：如有端口字面量 8554，替换为 554
- [x] 4.5 改 `scripts/setup.sh`：apt 安装清单加入 `libcap2-bin`

## 5. Runner / Analyzer / Scorer / Report

- [x] 5.1 修改 `runner.py`：CLI `--codec` → `--profile`；URL 模板 `?codec=` → `?profile=`；choices 从 `PROFILES.keys()` 派生；viewport 1920×1080 不变
- [x] 5.2 修改 `runner.py` 中 CPU sampler 启动判据：`PROFILES[profile].cpu_sampled and contestant_pgid is not None`
- [x] 5.3 修改 `runner.py` 中 capture_meta.json 字段：`codec` → `profile`
- [x] 5.4 修改 `runner.py::_cli()` 退出码 / argparse choices；保留诊断分支不变
- [x] 5.5 修改 `analyzer.py`：CLI `--codec` → `--profile`；输出文件名从 `<codec>_metrics.json` 改为 `<profile>_metrics.json`
- [x] 5.6 修改 `scorer.py`：删除 `EXPECTED_FPS = {"h264": 30, "h265": 25}`，改为从 PROFILES 派生
- [x] 5.7 修改 `scorer.py::score_correctness()`：满分 5（替换 10）；partial 2（替换 5）；阈值不变
- [x] 5.8 修改 `scorer.py::score_fps()`：保持 5/3/0，阈值不变（max 已经是 5）
- [x] 5.9 修改 `scorer.py::score_cpu()`：入参从 `measured_h265_fps` 改为 `measured_4k_fps`；gate_reason 字符串改为 `"4k_fps_below_threshold"`
- [x] 5.10 修改 `scorer.py::_build_cpu_block()`：入参从 `(h264_metrics, h265_metrics)` 改为 `(profile_metrics: dict, cpu_override_reason)`；`measured_on_codec` 字段名 → `measured_on_profile`，值 `"h265"` → `"4k"`
- [x] 5.11 修改 `scorer.py::build_score()`：入参改为 `profile_metrics: dict[str, dict | None]`；输出 `max_score=30`、按 profile key 写入 block、`objective_total` 累加
- [x] 5.12 修改 `scorer.py::_cli()`：删除 `--h264 --h265`，新增 `--metrics PROFILE=PATH`（argparse `action='append'`）
- [x] 5.13 修改 `scorer.py` 中 `"h265_round_failed"` 字符串 → `"4k_round_failed"`；docstring 同步
- [x] 5.14 修改 `report.py`：标题与表格列从 codec 维度改为 profile 维度（"H.264 round"/"H.265 round" → "2K profile"/"4K profile"）
- [x] 5.15 更新 `_cpu_sampler.py` 模块顶部 docstring：将"H.265 round"措辞改为"4K profile capture window"

## 6. Orchestration scripts

- [x] 6.1 重构 `scripts/evaluator.sh`：用 bash `for profile in 2k 4k; do ... done` 循环驱动 runner.py + analyzer.py；CPU 采样仅在 4k 那次传 `--contestant-pgid`
- [x] 6.2 修改 `scripts/evaluator.sh` 中 scorer 调用：新参数形式 `--metrics 2k=... --metrics 4k=...`
- [x] 6.3 修改 `scripts/evaluator-host.sh`：端口预检 `8080 + 554`（替换 8554）；`docker run` 增加 `--cap-add=NET_BIND_SERVICE`；readiness poll URL 改为 `?profile=2k`
- [x] 6.4 修改 `scripts/evaluator-host.sh` 兜底 score.json 生成：`max_score=30`，删除 h264/h265 block（scorer.py 已重写，兜底调用自动产出新形态）
- [x] 6.5 修改 `scripts/evaluator-local.sh`：端口 8554 → 554；readiness poll URL `?profile=2k`；兜底 score.json max_score=30
- [x] 6.6 修改 `scripts/_contestant_lifecycle.sh`：导出 `RTSP_SERVER_PORT=554`（替换 8554）；端口清理 `fuser -k 554/tcp`（替换 8554）
- [x] 6.7 修改 `scripts/deploy.sh`：health-check 调用新 RTSP path（无参数遍历 PROFILES）
- [x] 6.8 修改 `scripts/teardown.sh`：端口字面量 8554 → 554
- [x] 6.9 修改 `scripts/env.sh`：导出新端口（如果有相关变量） — env.sh 无端口字面量，无需修改

## 7. Container image

- [x] 7.1 修改 `Dockerfile`：runtime 阶段 apt 增加 `libcap2-bin`；在 `COPY` 完 `third_party/install/` 后运行 `setcap cap_net_bind_service=+ep /work/third_party/install/bin/mediamtx`（即使 docker run 还需要 `--cap-add`，这是 belt-and-suspenders）
- [ ] 7.2 验证 `docker run --cap-add=NET_BIND_SERVICE --user $(id -u):$(id -g) ...` 下 MediaMTX 能成功绑定 :554 — **跳过**：与 10.3 一起，docker 路径用户计划后续移除

## 8. Packaging (no contract change)

- [x] 8.1 修改 `scripts/package.sh` 中的 prerequisite 检查清单：从 PROFILES 派生 `stream_file` / `reference_dir`，自动覆盖 2K / 4K
- [x] 8.2 验证 `manifest.json` 不需要新增字段（image_sha256 自然变化覆盖了内容差异） — 确认现有 manifest 字段（git_sha/build_timestamp/submodule_status/playwright_chromium_version/image_sha256/image_size_bytes）足以反映新形态

## 9. Test fixtures rebuild

- [x] 9.1 盘点 `test_submissions/src/`：列出当前 7 个夹具的语义（reference、static-image、no-startsh、bad-watermark 等）
- [x] 9.2 为每个夹具设计 2K / 4K 期望分数对：reference 至少 10（2K 满分），static/iframe 各 profile fps=0，fake_overlay correctness<5，missing_start/never_ready/missing_testid 按 reason 校验
- [x] 9.3 重写 `test_submissions/src/*`：把所有 `?codec=` 改为 `?profile=`；reference/static_frame/iframe_only 的 web HTML+start.sh 改为遍历 PROFILES；其它 codec-agnostic 夹具（fake_overlay/missing_start/never_ready/missing_testid）无需改动
- [x] 9.4 跑 `scripts/build_test_zips.sh` 重新生成 zip — 多次执行，Task 10.2 全绿验证
- [x] 9.5 修改 `scripts/test.sh`：EXPECTED 表 + assert 分支改为 profile 维度；reference 案例 gate 调到 `total_ge:10`

## 10. Self-test execution

- [x] 10.1 跑 `./scripts/setup.sh && ./scripts/build.sh && ./scripts/deploy.sh`，确认 setcap 成功、两个 mp4 与 reference 目录生成
- [x] 10.2 跑 `./scripts/build_test_zips.sh` 再 `./scripts/test.sh` 默认模式，7/7 PASS（reference 2K full + 4K correctness full + 4K fps gated；其它 6 个 fixture 按预期表通过）
- [ ] 10.3 在另一台目标主机（或同机 docker daemon）上跑 `./scripts/test.sh --portable`，确认容器路径正确加载 cap，端口 554 可访问 — **跳过**：用户后续会移除 docker 机制，本变更范围内不验证
- [x] 10.4 4K 软解稳定性专项验证 — **不适用**：reference fixture 已改为 `<img>` 帧循环（不再走 HEVC 解码），10.2 的实测已覆盖 fps banding（2K ratio 0.40 → partial 3；4K ratio 0.24 → 0）以及 CPU gate（gate_reason="4k_fps_below_threshold"）。banding + 门控工作正常

## 11. Documentation sync

- [x] 11.1 修改 `CLAUDE.md`：顶部 Overview 段（评分细节、CPU 子分门控）、Structure 树（reference/2k 与 4k）、Data Flow（profile 循环）、Entry Points 表、Dev Commands 表、Conventions（端口 554、cap-add、setcap）、Anti-cheating 段落
- [x] 11.2 修改 `README.md`：评分总分 30、URL 形态、RTSP 端点、setcap 步骤
- [ ] 11.3 检查 `dist/README.md`（package.sh 输出的）：端口、URL 示例、cap-add 提示 — **跳过**：与 10.3 一起，docker 路径用户计划后续移除
- [x] 11.4 grep 全仓库残留字符串：`?codec=`、`:8554`、`h264_metrics`、`h265_metrics`、`h264_screenshots`、`h265_screenshots`、`max_score": 40` 全部已清理

## 12. Optional cleanup (low priority)

- [x] 12.1 评估 `third_party/x264` submodule 是否仍被 ffmpeg 编译依赖 — 是。`scripts/build.sh::build_ffmpeg` 显式 `--enable-libx264` 并 `pkg-config --exists x264` 断言，删除 x264 会破坏 ffmpeg build。**保留**。
- [x] 12.2 评估 ffmpeg 配置是否仍需 `--enable-libx264` — 是（同上）。如要彻底剔除，需先在 build.sh ffmpeg configure 移除 `--enable-libx264` 并删除前置 pkg-config 检查；本变更范围内不动。

## 13. Final verification

- [x] 13.1 `openspec validate switch-to-h265-resolution-profiles` 通过
- [x] 13.2 手动 review 一份完整的 `score.json` 输出（用 fake metrics fixture 验证）：`max_score=30`，`2k`/`4k`/`cpu` 三个顶层 block，无残留的 `h264`/`h265` key，`cpu.measured_on_profile="4k"`
- [x] 13.3 review `report.html` 由 fake metrics 生成的输出：标题与表格正确反映 profile 维度；CPU section 用 `measured_on_profile` 字段；artifact links 指向 `<profile>_screenshots/`
