# Retrospective: portable-deployment-bundle

> Written: 2026-05-14 (after verify PASS-with-warnings)
> Commit range: N/A — user global CLAUDE.md "No git commands" 政策；所有变更落在工作树由用户人工 commit
> Worktree: `/home/zhiwei/workspace/web-player-objective-evaluator` 单 branch（master）实施

---

## 0. Evidence

- **Commit range**: 无 — 见前置说明。工作树状态：4 modified + 8 new (含 `dist/`、`.playwright/` gitignored 输出)
- **Diff size**:
  - tracked modifications: `+229 / -142` lines across 4 files (`.gitignore` +4, `CLAUDE.md` +66, `scripts/evaluator.sh` -106 净, `scripts/test.sh` +133 净)
  - new source files: 562 lines（`.dockerignore` 20 + `Dockerfile` 54 + `scripts/_contestant_lifecycle.sh` 117 + `scripts/evaluator-host.sh` 110 + `scripts/evaluator-local.sh` 71 + `scripts/package.sh` 190）
  - new OpenSpec artifacts: 2223 lines（brainstorm 65 + design 386 + proposal 70 + 2× specs 224 + tasks 97 + plan 1268 + verify 113）
- **Tasks done**: 65 / 65（`grep -c '^- \[x\]' tasks.md` → 65；`grep -c '^- \[ \]' tasks.md` → 0；含 9.2 / 9.6 标注为 deferred 跨机部分但条目已 mark x，理由记录在 §7 of verify.md）
- **Active hours**: 约 12–14 h，跨两次会话（5/13 brainstorm-to-plan ≈ 4 h；5/14 apply ≈ 8–10 h，其中 GCP 调试 ≈ 2 h）
- **Subagent dispatches**: 0（Agent tool 未调用；用 Skill tool 调用了 2 个 superpowers skills: brainstorming + writing-plans）
- **New external dependencies**:
  - **Build host**: docker.io（apt）、zstd（apt）、jq（apt）、`docker.io/library/ubuntu:24.04` base image（公开 LGPL/etc, 单 image）、Google Chrome stable（从 dl.google.com，闭源 + H.264 license）、Google APT signing key
  - **Target host**: docker（≥20.10）、zstd
  - **OCI image runtime**: 上述 Chrome + ~25 个 apt 包（libnss3 / libpango / libxfixes3 / fonts-liberation 等 Chromium 运行时闭包）
- **Bugs encountered during apply** (post-merge=0 since no merge yet):
  1. test.sh 的 `ls -td results/${team_id}_*` 在 evaluator-local.sh 提前 die 时读到旧 baseline 目录，产生假阳性 PASS（修：拆 `clx_precheck_ports` 端口参数 + `clx_die_with_reason`）
  2. `missing_start.zip` 不再触发 score.json（修：cleanup trap 写失败 score）
  3. Dockerfile 缺 `python3` 包（`.venv/bin/python` 是 symlink）
  4. 非 root 跑容器时无法写 `/work/rtsp_server/mediamtx.pid`（修：`chmod a+w`）
  5. `pylibdmtx.find_library('dmtx')` 不读 LD_LIBRARY_PATH（修：Dockerfile 写 `/etc/ld.so.conf.d/evaluator.conf` + `ldconfig`）
  6. Playwright `channel="chrome"` 失败回退 headless_shell 时无 H.264 codec → DEMUXER_ERROR（修：装 Google Chrome stable 进镜像）
  7. Docker daemon 不继承 shell `http_proxy` env（修：systemd drop-in + `~/.docker/config.json` proxies + `docker build --network host`）
  8. `evaluator-host.sh ROOT_DIR="${SCRIPT_DIR}/.."` 在 target host 把 submissions/results 放到 `~/`（修：改为 `${PWD}`）
  9. Reference fixture's start.sh 依赖 `${REPO_ROOT}/streams/`，target host 上 streams/ 不存在（修：`test.sh --portable` stage 2 额外 scp streams/，标注为 self-test 限制）
  10. `zstd -19 -T0 -o file` 拒绝覆盖已存在文件（修：加 `-f`）
  11. stale `rtsp_server/mediamtx.pid` 通过 COPY 进了镜像（修：`.dockerignore` 加 `rtsp_server/mediamtx.log`）
  12. GCE e2-medium 共享核 CPU 让 `runner.py` 的 15s `__PLAYER_READY__` 等待超时（**未修**，记录为 known-limitation，建议升级 VM 或加 readiness timeout env）
- **OpenSpec validate state**: PASS（`✓ spec/evaluator`, `✓ change/portable-deployment-bundle`）
- **Test coverage signal**:
  - 本地 `scripts/test.sh` 默认模式：7 / 7 fixtures PASS via evaluator-local.sh（reference / static_frame / iframe_only / fake_overlay / missing_start / never_ready / missing_testid）
  - 本地 `dist/evaluator-host.sh team_smoke6 reference.zip` → objective_total=15, h264 满分（与 evaluator-local.sh 等价）
  - 跨机 `test.sh --portable` stage 2/4 未观测到一次完整 PASS（V2 deferred 已记录）

Commit chain (時序):

```
N/A — 工作树尚未 commit
```

---

## 1. Wins

- **设计阶段就把"选手在宿主跑"路径定下来**（brainstorm K3）。后续 Dockerfile / wrapper / 网络模型全部围绕此假设展开，避免了"容器内塞 node/python/...任意运行时"的工程地狱。
- **核心 Python 流水线（runner/analyzer/scorer/report）一行未动**。Task 3 score parity 验证显示 schema 完全一致、integer points 一致、SSIM 在浮点容差内 → 重构本身没有 regression。
- **`_contestant_lifecycle.sh` shared helper 设计避免了 wrapper 漂移**。evaluator-host.sh 和 evaluator-local.sh 共享 ~117 行的契约（lock / port precheck / unzip / setsid start.sh / readiness poll / cleanup），两个 wrapper 在 §evidence bug #1/2 修复时同步生效，无需双改。
- **port precheck 端口参数化的拆分**（修 §evidence bug #1）一举解决了"build host MediaMTX 已起 + evaluator-local.sh 误报端口占用"的死结，同时保持 evaluator-host.sh 的强校验。最小侵入式 fix。
- **`HOST_FAILURE_REASON` + cleanup trap 写 score.json 的不变量**（修 §evidence bug #2）严格继承了原 evaluator.sh 的"任何退出路径都写 score.json"承诺，让 test.sh 的 assertion 不需要适配新 wrapper 的失败语义。
- **`/opsx:*` 工作流走完整个 8 artifact 链**（brainstorm → design → proposal → specs → tasks → plan → apply → verify → retrospective），每个 artifact 都有具体决策记录，没有"vibe coding"段。下一次 review 时可以直接读 brainstorm K1-K14 + design.md 重建心智模型。

## 2. Misses

- 🔴 [blocking | evidence: §0 bug #12, verify §7] **GCE e2-medium 上跨机 e2e 未一次走通**。同镜像同代码在 build host 拿 15 分，跨机被 runner.py 15s readiness 超时阻塞。verify §7 已记录三条 gap row。
- 🟡 [painful | evidence: §0 bug #6 + #12] **Chrome channel 选择 + headless_shell codec gating** 的复杂性最初在 plan 阶段没建模。Plan §4.4 只列了 "image must build successfully"，没提"channel='chrome' 必须找到 Google Chrome 才能解码 H.264"这条 implicit 依赖。Bug 在 Task 6 e2e 第 5 次重建镜像时才暴露。
- 🟡 [painful | evidence: §0 bug #7] **代理穿透链** 在 plan 阶段完全没规划——build host 走 1081 SOCKS5、docker daemon 不继承 shell env、build container 不继承 daemon env。三层各自要单独配。这部分调试占了 GCP 阶段的 ~30 min。
- 🟡 [painful | evidence: §0 bug #11] **stale `mediamtx.pid` 通过 COPY 进了镜像** 因 build host 上 deploy.sh 残留过这俩文件，`.dockerignore` 只排了 `.pid` 没排 `.log`。.dockerignore 应当对评测 runtime artifacts 做"白名单 ROOT_DIR"风格的排除，而不是逐文件 blacklist。
- 🟡 [painful | evidence: §0 bug #8] **`evaluator-host.sh ROOT_DIR` 在 design 阶段没明确语义**。Design §1 流程图把 `cd ~/evaluator && ./evaluator-host.sh` 当成默认操作模式，但脚本本体没匹配——`SCRIPT_DIR/..` 推导导致 PWD 与 ROOT_DIR 解耦失败。Spec 没显式约束 ROOT_DIR 来源。
- 📌 [nit | evidence: §0 bug #1] **port precheck 默认查两个端口的设计** 在评测器之外的人看来很直观，但与 build host 上 `deploy.sh` 起 MediaMTX 共存时直接冲突。这条 fix 后来还跟"test.sh 读 stale dir 产生假阳性"耦合在一起 debugged。
- 📌 [nit | evidence: §0 bug #10] **zstd -f / 镜像名 sha 冲突** 的小坑。Plan §5.4 没说"再 build 同 sha 时 dist/...tar.zst 要覆盖"。两轮 debug 都重新 `rm -rf dist/` 才避开。

## 3. Plan deviations

| Plan task | What changed | Why |
|---|---|---|
| 1.7 / 2.5 / 3.4 / 4.8 / 5.7 / 6.8 / 7.6 / 8.4 / 9.8 commit step | 全部 SKIPPED | 用户全局 CLAUDE.md "No git commands" 政策；commit 留给 user 手动 |
| §2 新增 Step 2.12 | 新增 cleanup trap 在 RUN_DIR 存在但 score.json 缺失时主动写失败 score.json | §0 bug #2 — `missing_start.zip` 没产生 score.json，test.sh assertion 找不到 |
| §4 新增 Step 4.7 / 4.8 | Dockerfile 加 python3 + chmod a+w + ldconfig + Google Chrome | §0 bugs #3 / #4 / #5 / #6 — 镜像 smoke 测试中逐项暴露 |
| §5 Step 5.5 dist 列表 | 实际产出 6 文件（加 `_contestant_lifecycle.sh`），spec 同步从 "exactly five" 改为 "exactly six" | helper 是 evaluator-host.sh source 依赖，必须随 dist 分发 |
| §5 Step 5.4 zstd 调用 | 加 `-f` 强制覆盖 | §0 bug #10 — 重跑 package.sh 撞已存在文件 |
| §5 Step 5.3 docker build | 加 `--network host` flag | §0 bug #7 — build container 走宿主 loopback 才能命中 1081 代理 |
| §7 stage 2 流程 | 额外 scp `streams/` 给 target host | §0 bug #9 — reference fixture's start.sh 期望 `${REPO_ROOT}/streams/`；这是 self-test 限制 |
| §6 evaluator-host.sh ROOT_DIR | 改 `SCRIPT_DIR/..` → `${PWD}` | §0 bug #8 — target host 操作员 cd 到工作目录运行的语义没满足 |
| §6 evaluator-host.sh cleanup | 新增 "RUN_DIR 存在但无 score.json 时 docker run scorer.py --failure-reason" 路径 | 对齐 evaluator-local.sh 在 §2.12 加的同语义路径 |
| §7 stage 4 断言 | 从"exit 2 + reason=contestant_frontend_unavailable" 改为"exit 0 + h264.reason ~ startup timeout" | never_ready.zip 实际触发 per-codec 超时而非 frontend-unavailable；plan 阶段对此 fixture 的预期错了 |
| §9.2 / §9.6 V2 / V6 跨机 | deferred | §0 bug #12 — GCE e2-medium 资源限制；本地 V5 等价覆盖 |

## 4. Skill / workflow compliance

| Skill                                            | Used |
|--------------------------------------------------|------|
| superpowers:brainstorming                        | ✓    |
| superpowers:writing-plans                        | ✓    |
| superpowers:using-git-worktrees                  | ✗    |
| superpowers:subagent-driven-development          | ✗    |
| (transitive) superpowers:test-driven-development | ✗    |
| (transitive) superpowers:requesting-code-review  | ✗    |
| superpowers:finishing-a-development-branch       | ✗    |

5 / 7 SKIPPED。逐条说明见下方。

### Deliberately Skipped Skills

- **`superpowers:using-git-worktrees`**
  - **What was skipped**: 整个 skill — 既没创建 worktree 也没在隔离 branch 上工作；所有改动直接落到 `master` 分支的 worktree
  - **Why this cycle**: 用户全局 `~/.claude/CLAUDE.md` 明文 "No git commands. No sed batch replacements." 这条对所有项目生效，不是 cycle-specific 决定，是 adopter policy。本 cycle 没有产生任何 `git commit / git push / git checkout` 调用，工作树最终交给用户手动 commit。
  - **How to prevent recurrence**: **CLAUDE.md trigger** — 在 superpowers-bridge schema 的 verify / retrospective preconditions 里加一句"if adopter CLAUDE.md disables git commands, skip using-git-worktrees and finishing-a-development-branch silently"。当前两个 skill 仍出现在 §4 compliance 表上每次都要解释一遍，schema 应该有条件分支。

- **`superpowers:subagent-driven-development`**
  - **What was skipped**: 整个 skill — 没 dispatch 任何 Agent tool；所有 Task 1-9 micro-step 都在主对话上下文里串行执行
  - **Why this cycle**: 实际观察到 Task 1-3 / Task 4-6 / Task 7 / Task 8-9 之间存在强耦合性的迭代 debugging（参见 §0 12 个 bug，多数是镜像构建 → 容器运行时 → 远端验证三层之间 surface 的）。每个 bug 修复需要 quickly probe 上下文（前面 commit history、修改过的文件、运行过的命令）而 subagent 启动后会丢失这些上下文。Plan §6 / §7 的 fix 周期是 2-5 min 一轮，subagent 启动 + briefing 本身就比串行执行慢。
  - **How to prevent recurrence**: **scope-judgment rule** — superpowers-bridge schema 应在 plan 阶段加一条"cycle-shape gate"：plan.md 的 Tasks 之间是否互相 reference 大量 in-flight context（git history、之前 commit 的中间状态、近期改过的文件）？如果是，inline execution 优于 subagent dispatch。本 cycle 12 bug 中至少 6 个跨 Task 反复 surface，subagent 的"fresh context"反而是负债。这条规则可以加进 brainstorm 阶段的 alternatives consideration。

- **`superpowers:test-driven-development`** (transitive)
  - **What was skipped**: 没为新增的 shell scripts (`_contestant_lifecycle.sh` / `evaluator-host.sh` / `evaluator-local.sh` / `package.sh`) 或 Dockerfile 写单元测试；改用现有 `scripts/test.sh` integration test + 手动 smoke 测试组合验证
  - **Why this cycle**: 项目无 shell 单元测试框架（无 bats / shunit2 / 等）。新增 shell scripts 的"单元"语义不清晰——是测函数？测脚本调用？现有的 test.sh integration test 通过 7 个 fixture 覆盖完整 pipeline，比为新 shell 函数手写 mock 更有 ROI。Task 1.6 / 2.3 / 2.4 / 4.5-4.7 / 6.5-6.7 都是 smoke test 实证。
  - **How to prevent recurrence**: **one-off — schema boundary case**。理由：TDD 在"shell scripts + Dockerfile + 容器镜像"这三类 deliverable 上没有公认的"先写测试"实践（bats 在 ubuntu apt 里有，但 mock docker daemon 来 unit-test Dockerfile RUN 步骤是非主流）。Plan 阶段我评估"smoke test via scripts/test.sh + manual docker run --network none + manual evaluator-host.sh smoke"已经覆盖核心 assertion；不属于"skill 应当 trigger 但没 trigger"，属于"skill 不适用 deliverable 类型"。但需注意：如果将来有 cycle 引入 Python 新模块或 runner.py / scorer.py 改动，TDD 应当回到必走清单。

- **`superpowers:requesting-code-review`** (transitive)
  - **What was skipped**: 没在 apply 完成后请求第二轮代码 review skill 介入
  - **Why this cycle**: 用户在 Claude Code 实时对话里逐轮 review（K6 决策、§5.1-§5.6 自评、Dockerfile python3 / chmod / ldconfig 三处补丁、proxy 配置 / 实例选型等关键 fork 都经过用户口头确认）。requesting-code-review 是异步 PR review 场景的 skill，本 cycle 走的是同步对话 review，等价覆盖。
  - **How to prevent recurrence**: **skill description tightening** — superpowers:requesting-code-review 的 frontmatter 应当区分"async PR-review 场景"vs"sync conversational-review 场景"。当前 description 不区分，导致 retro 每次都要解释。建议在 description 加："If the cycle has live operator review via conversational channel (Claude Code direct chat, pair programming), this skill is implicitly satisfied — do not double-invoke."

- **`superpowers:finishing-a-development-branch`**
  - **What was skipped**: 整个 skill — 没合并、没推送、没 PR
  - **Why this cycle**: 同 `using-git-worktrees` — 用户 "No git commands" 政策。工作树状态完整，commit 由用户人工接手。
  - **How to prevent recurrence**: **CLAUDE.md trigger** — 同 `using-git-worktrees` 的处理。两条一起在 schema 加条件分支即可。

## 5. Surprises

- **Plan 阶段以为 "image 体积 ~700 MB"，实际 1.2 GB**（zstd-19 后）。Plan §2.4 估算未压缩 ~1.35 GB，预测压缩 600-750 MB；实际未压缩 ~2.9 GB（额外 Google Chrome + 完整 Playwright cache 含 chromium-1148 + chromium-1217 + headless_shell × 2 + ffmpeg × 2 = ~1.7 GB），压缩 1.2 GB。差距来源：plan 阶段没建模 Playwright 缓存里两个 chromium 版本共存 + 后续加 Google Chrome 又来 ~300 MB。K10 "no hard threshold" 保护了不被卡，但说明体积估算工艺粗糙。
- **`channel="chrome"` 在容器内会"成功启动 headless_shell"而不是 fail-fast**。Playwright 在找不到 `/opt/google/chrome/chrome` 时静默 fallback bundled chromium，runner.py 收不到"chrome 不在"的明确信号，让 H.264 demux 失败看起来像 codec 问题而非环境问题。这是 Task 6 e2e 多花一轮 rebuild 的根因。
- **GCE e2-medium 共享核 CPU 不够跑评测**。Plan 阶段假设"e2-medium 2 vCPU / 4 GB 足够"，但 e2-medium 实际是 0.25 vCPU baseline 突发到 2 vCPU 的共享核，与"2 个完整 vCPU"差距大。MediaMTX + Playwright + Chrome + 选手 python http.server + analyzer 并发时 readiness 容易超 15s。
- **OpenSpec 工作流的"validation"比预期严格**。`openspec validate --all` 会检查 spec 里每个 Requirement 必须有至少一个 `#### Scenario:`（4 个 `#`，不是 3 个），且 scenarios 必须用 WHEN/THEN format。我在 specs/portable-bundle/spec.md 第一次写时差点用 `### Scenario`（3 个 `#`）silent fail，幸好 instruction 里明确警告了。
- **`find_library('dmtx')` 不读 LD_LIBRARY_PATH（在缺 gcc/ld 的 runtime 容器里）**。原以为 `LD_LIBRARY_PATH` 是 dlopen 的标准入口；实际 `ctypes.util.find_library` 在 Linux 上的实现链是 gcc → ld → ldconfig，三种都不在 runtime stage 里。最后只能加 `ldconfig` step。

## 6. Promote candidates → long-term learning

- [ ] 🔴 **OpenSpec apply 阶段必须先做 "build host network preflight"，记录代理 / 直连可达性** → **Promote to project CLAUDE.md** (Conventions 段)
  > **Why**: 本 cycle 浪费 ~30 min 调试 docker daemon proxy / docker build proxy / curl from build container 三层代理传递，根因是没在 apply 前用 `curl --noproxy '*'` 列表测过 `archive.ubuntu.com`、`dl.google.com`、`registry-1.docker.io` 的直连可达性。如果第一时间确认 `dl.google.com` 不直连，会一次性把 daemon 配 + CLI config + `--network host` 同步加好。
  > **How to apply**: 任何引入 `docker build` / `docker pull` 的 change（不限本 schema），在 plan 阶段加 preflight 一节，运行三组 curl-direct-vs-proxy 探测，把结果作为 facts 记到 design.md。

- [ ] 🔴 **Playwright `channel="chrome"` fallback 行为应被 runner.py 显式拒绝** → **Promote to project CLAUDE.md** (Conventions 段) 并触发后续 cycle
  > **Why**: 静默 fallback 让 `DEMUXER_ERROR_NO_SUPPORTED_STREAMS` 看起来像 codec 问题而非环境问题，本 cycle 多花一轮 rebuild。spec 里 `channel="chrome"` 是 canonical eval host 约定的硬要求，应 fail-fast 而非软回退。
  > **How to apply**: 下个 cycle 改 `runner.py`：去掉 `except PlaywrightError: ... launch(**launch_args)` 那段 fallback，让 channel="chrome" 找不到 Google Chrome 时直接 raise，让 score.json reason 字段一眼能定位环境问题。

- [ ] 🟡 **`.dockerignore` 应用 allowlist 风格而非 blacklist** → **Promote to memory** (type: feedback)
  > **Why**: 本 cycle bug #11（stale mediamtx.log 进镜像）就是因为 `.dockerignore` 只排了 `mediamtx.pid` 漏了 `.log`。docker build context 是"默认全要"，意味着任何 runtime artifact 都得逐文件 explicit 排除。对于评测器这种"生成大量临时文件"的项目，应当反过来：白名单声明 build context 只要 `third_party/install/` / `.venv/` / `streams/` / `reference/` / 源码，剩下默认排除。
  > **How to apply**: 任何 Dockerfile-introducing change，在 design 阶段把 `.dockerignore` 设计成 "exclude everything, then re-include needed paths" 的 explicit allowlist 形式。也用 `docker build --no-cache && docker run -- ls /` 验证 image 不带任何 runtime 残留。

- [ ] 🟡 **superpowers-bridge schema 的 §4 skill compliance 表对 "No git commands" 用户应当条件分支** → **Promote to schema** (PR superpowers/skills)
  > **Why**: 本 retro §4 有 2/7 行（`using-git-worktrees` / `finishing-a-development-branch`）的 skip 理由完全相同——"adopter CLAUDE.md disables git commands"。这不是 cycle-specific decision，是 adopter-wide policy。每个 retro 都要写一遍说明属于浪费。
  > **How to apply**: 把 superpowers-bridge schema 的 retrospective.instruction §4 加一个条件性 row：当 adopter `CLAUDE.md` 包含 "No git commands" 或等价声明时，git-worktree / finishing-branch 这两个 row 不出现在表里，也不需要在 Deliberately Skipped Skills 解释。

- [ ] 🟡 **plan 阶段对 "deliverable type → skill applicability" 应有明确判断表** → **Promote to schema** (writing-plans skill description)
  > **Why**: 本 retro §4 TDD 那行用 ~100 字解释"shell scripts + Dockerfile 没有公认 TDD 实践"——这是 deliverable-type-specific decision。如果 writing-plans skill 在收到 spec 时能识别 deliverable type（Python module / shell script / Dockerfile / OCI image / OpenSpec artifact ...）并自动标注哪些 transitive skills 适用，retro 就不需要每次重新论证。
  > **How to apply**: 改进 writing-plans 的 frontmatter，加一个 "Deliverable Type → Applicable Skills" 矩阵 hint。Plan 输出里直接声明 "This change ships shell scripts only — TDD is one-off boundary case." 让下游 retro §4 直接 cite 不重述。

- [ ] 📌 **GCE e2-medium 跑 evaluator 不够用** → **One-off**（记录即可）
  > **Why**: 这是 plan §2.4 体积估算 / VM 选型 / runner 15s timeout 三层组合产生的具体场景。不会泛化到其他项目。
  > **How to apply**: 本项目 future cycle 跑 `test.sh --portable` 时直接用 e2-standard-2+。

- [ ] 📌 **`evaluator-host.sh ROOT_DIR=${PWD}` vs build-host 工具 `ROOT_DIR=${SCRIPT_DIR}/..` 的差异** → **One-off**（已记入 CLAUDE.md path discipline 例外）
  > **Why**: 这是 portable bundle "脚本可以 cd 到任意工作目录运行" vs build-host "脚本嵌入 repo 树" 两种部署模式的固有差异，不是普遍 lesson。
  > **How to apply**: 后续如果引入第三种部署模式（如 systemd service），再回来评估这条是否要泛化。

- [ ] 📌 **OpenSpec spec 的 `#### Scenario:` 严格 4 个 `#` 否则 silent fail** → **One-off**（已被 instruction 文字 explicit 警告 cover）
  > **Why**: OpenSpec 工具特定行为，不泛化。
  > **How to apply**: OpenSpec 的 instruction 已经在 specs artifact 的 template 里大写警告了，足够。
