# Retrospective: add-cpu-usage-scoring

> Written: 2026-05-15 (after verify passed)
> Commit range: `ab8fe9f..efe3924` (13 commits)
> Worktree: master (in-place; no isolated worktree used)

---

## 0. Evidence

- **Commit range**: `ab8fe9f..efe3924` (13 commits)
- **Diff size**: +1867 / -162 lines across 15 files
- **Tasks done**: 45 / 45（`grep -cE '^\s*- \[x\]' tasks.md` → 45; `grep -cE '^\s*- \[[ x]\]' tasks.md` → 45）
- **Active hours**: ≈ 6h (单 session 内端到端，含 brainstorm → apply → verify drift fix → retrospective)
- **Subagent dispatches**: 0（全部在主 context 完成）
- **New external dependencies**: pytest 8.3.3（dev-only，requirements.txt）；运行时**零**新依赖（sampler 纯 stdlib）
- **Bugs encountered post-merge**: 尚未 merge（仍在 master HEAD）；apply 期发现 2 个内部 bug 在同会话内修复：
  - per_process_top label 全部 `?`（label 读取条件错放在 `prev is None` 分支）→ commit `b6ddf01` 前一次实测发现，同次提交内修
  - chrome cmdline split-on-NUL 永远 False（chrome 用 `prctl(PR_SET_MM_*)` 改写成空格分隔单字符串）→ 同次实测发现，子串匹配修复 + 单测固化
- **OpenSpec validate state**: ✓ valid（archive 前最后一次 `openspec validate add-cpu-usage-scoring`）
- **Test coverage signal**: pytest 37 / 37 PASS（17 score_cpu + 6 cpu_sampler 含 spin-CPU subprocess fixture / 真实 chrome cmdline 双 layout fixture / 14 misc）；`scripts/test.sh` 7 / 7 fixture PASS exit 0

**Commit chain（时序）**:

```
ab8fe9f [feat] 调整测试视频 I 帧间隔                          ← merge-base
b51c27c test: add failing tests for CPU sub-score and /proc sampler
2704d5b feat(cpu_sampler): pure-stdlib /proc PGID sampler with daemon thread
8986dad feat(scorer): add CPU sub-score with 5 tunables and 40-point max_score
9f73cde feat(runner): embed CPU sampler in H.265 capture loop
c61ac98 feat(analyzer): pass capture_meta.json cpu block into metrics output
15cebfc feat(evaluator.sh): forward contestant PGID to H.265 runner for CPU sampling
ef5de5b feat(report): render CPU sub-score block in evaluator HTML report
aa5632a docs(CLAUDE): bump objective total to 40 and document CPU sub-score scope
c6bbefd chore(opsx): mark add-cpu-usage-scoring tasks complete (42/45, 3 deferred)
d24dada test(opsx): close 3 deferred tasks with E2E evidence
4b112ef feat(cpu_sampler): extend to follow Playwright Chrome process tree
b6ddf01 feat(cpu_sampler): exclude chrome GPU process (SwiftShader noise filter)
efe3924 docs(opsx): sync design + spec delta with apply-stage decisions
```

---

## 1. Wins

- [evidence: `b51c27c` + `2704d5b` 提交顺序] **真正的 test-first 实施**：先写 21 个 score_cpu parametrize case + 4 个 sampler 测试（含 setsid spin-CPU subprocess fixture），跑确认全 fail（ImportError / AttributeError），再写实现。每个 commit 都对应一个明确的 test pass 边界，回滚粒度细。
- [evidence: `_cpu_sampler.py` 整文件 + commit `2704d5b`] **零运行时依赖**：纯 stdlib `/proc/[0-9]*/stat` + `threading` 实现，没动 requirements.txt 的运行时部分。免去了 portable bundle 重新 build 的烦恼，也意味着 sampler 出问题时不用怀疑 psutil / pidstat 版本兼容性。
- [evidence: `tests/test_cpu_sampler.py::test_cmdline_is_chrome_gpu_handles_both_layouts`] **踩坑固化为单测**：Chrome `prctl(PR_SET_MM_*)` 把 cmdline 改写成空格分隔单字符串这个非显然行为，被抽出为 `_cmdline_is_chrome_gpu(bytes) -> bool` 纯函数 + 5 case 单测（NUL/空格/type 标记/非 GPU/空），Chrome 升级回归这条单测立刻就能 catch。
- [evidence: `score.json.cpu.thresholds_used` 6 字段] **审计字段先于功能**：所有阈值改动都写进每次 run 的 score.json，contestant 申诉时能离线复算分数无需查源码。这个决策在 D6 时就定了，apply 期把 `CPU_GATE_H265_FPS_RATIO` 调到 0.10 时也无痛——审计自带 trace。
- [evidence: `4b112ef` → `b6ddf01` 两次 commit + verify §7] **诊断驱动迭代**：team 2000 实测 0.037% 异常 → 加 chrome 树（19.5%）→ 加 per_process_top 诊断 → 发现 GPU process 84% → 加 GPU 排除（3.14%）。每一步用真实数据驱动下一步，没有空想。
- [evidence: verify §4 PASS（rerun 后）] **drift 当场修补**：第一次 verify 标 §4 drift（D8/D9 未回写），用户选 A 路径回写 design.md + spec delta + 4 个 Scenario，rerun verify 立即升 PASS，没拖到 archive 后做 follow-up change。

## 2. Misses

- 🟡 [painful | evidence: commit `15cebfc` 和 `aa5632a` 的 diff 大小] **commit attribution 污染**：session 开始时 `scripts/evaluator.sh` 和 `CLAUDE.md` 在 git status 已是 M 状态（用户的 in-flight WIP）。Task 7 的 `git add scripts/evaluator.sh && git commit` 一并扫进了那些 pre-existing 重构（`SUBMISSION_ZIP → RESULTS_SUBDIR` 入参变更）。代码本身对，但 commit 的"该 commit 改了什么"叙述不准。手工 `git add -p` 或事先 `git stash` 这些 M 文件都能解决。
- 🟡 [painful | evidence: verify §4 第一次 PASS WITH WARNINGS] **D8/D9 实施时没即时回写 design/spec**：A 方案和 A1 都是 apply 阶段的实质设计变更（不是简单 calibration），应该在 commit 时同步 update design.md / spec delta，而不是攒到 verify 才发现。下次 spec-driven 工程里"非纯实现的代码改动 = 同步 update 设计文档"应是肌肉记忆。
- 📌 [nit | evidence: 第一次 per_process_top 实测 label 全 `?`] **诊断字段的"第一次观测"陷阱**：label 读取放在 `if prev is None:` 分支里，意思是"新见到的 PID 读 label"，但 baseline 阶段（start()）所有 PID 都 prev=None 不存在……不对，是"已在 baseline 里的 PID"在 _tick 永远不走该分支，label 没读到。一行 `if pid not in self._pid_label:` 重构修了，但说明"读取 once" 这种逻辑分支放错位置只会在实测时暴露——单测覆盖不到这条。
- 📌 [nit | evidence: D6 表格里"未调"字段太多] **calibration 流程缺一手段**：6 个阈值常量中只 1 个真被调过（gate_fps_ratio），其余 5 个还是初始值。说明"参数化"做得不错但"calibration evidence"还几乎为零——这也合理（只跑了一个真实 contestant），但 retrospective 里没法给 7 中 #7 一个推荐值。

## 3. Plan deviations

| Plan task | What changed | Why |
|---|---|---|
| Task 3（_cpu_sampler.py）合 4.6 / 6.4 / 9.1 manual | 起初标 `[ ]` deferred"需要 live frontend"，apply 期补跑了真实 E2E，全部转 `[x]` 并在 tasks.md 旁批写运行证据 | 工程师（=我）一开始低估了 E2E 跑通的成本（实际 1 个 fixture ~5 分钟，全 fixture ~7 分钟）。能跑就跑，不要 deferred + automated-equivalent 双轨 |
| Plan 中**未列**：D8 浏览器树 / D9 GPU 排除 | apply 期为修正 team 2000 实测异常**新增**的两条核心算法变更，commits `4b112ef` + `b6ddf01` | brainstorm/design 阶段假设了"server-side 解码 contestant 主导"，撞到 client-side decode 的真实 contestant 才发现假设错。这种"实测驱动新增决策"是预期的，但 design.md 没即时跟上是 §2 Misses 那条 |
| Plan §8.3 "spec.md::Purpose 不动" | 保留，archive 时由 `/opsx:archive` 自动 sync | 按项目 CLAUDE.md 规则，符合预期 |
| Plan §9.1 scripts/test.sh 跑全 fixture | 跑完（标 [x]）；commit `d24dada` 记录 | 没 deviation |

## 4. Skill / workflow compliance

| Skill | Used |
|---|---|
| superpowers:brainstorming | ✓ |
| superpowers:writing-plans | ✓ |
| superpowers:using-git-worktrees | ✗ |
| superpowers:subagent-driven-development | ✗ |
| (transitive) superpowers:test-driven-development | ✓ |
| (transitive) superpowers:requesting-code-review | ✗ |
| superpowers:finishing-a-development-branch | ✗ |

> Default expectation: 全 ✓。本周期有 4 项 ✗，下方逐项交代。

### Deliberately Skipped Skills

- **`superpowers:using-git-worktrees`**
  - **What was skipped**: 整个 skill。本周期直接在 master 上 commit，没起 isolated worktree。
  - **Why this cycle**: 触发条件 = `git status` 在 session 开始时已显示一批 pre-existing M / untracked 文件（用户的 in-flight 工作），起 worktree 会让那些工作脱离当前 working set；同时单工程师单 session，没有并行任务竞争。具体 trigger 是 conversation start gitStatus snapshot 的 `M .gitignore`、`M CLAUDE.md`、`M scripts/evaluator.sh` 等行。
  - **How to prevent recurrence**: **scope-judgment rule** — "session 开始时若主分支已有不属本变更的 uncommitted M/?? 文件，且没有并行任务需求，可省 worktree；但必须在 commit 前用 `git add <specific-files>` 而非 `git add .` 或 `git add <dir>`，并在 retrospective Misses 标出 'commit attribution 风险'"。本周期 §2 第 1 条 Miss 就是这条规则没贯彻的代价。

- **`superpowers:subagent-driven-development`**
  - **What was skipped**: plan.md 顶部的 "REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans" 这条建议；既没 dispatch subagent，也没显式调 executing-plans，而是主 context 内顺序执行。
  - **Why this cycle**: 触发条件 = plan 10 个 Task 之间强顺序依赖（test-first → sampler 实现 → scorer → runner → ...），每个 Task 的产出会被下一个 Task 直接消费；并行无收益。具体 trigger 是 plan.md 的"File Structure"表显示每个 task 都修改前序 task 引入的同一文件（scorer.py 在 Task 1 写测试、Task 4 写实现），subagent 拆分会引入 merge 开销。
  - **How to prevent recurrence**: **scope-judgment rule** — "plan 中若 task 之间存在 'A produces a symbol that B imports' 的链式依赖（如 'write failing test for X' → 'implement X'），单线程跑比并行更稳；plan.md 应在生成时检测这种依赖并在 header 显式标 'sequential — subagent dispatch not applicable'"。这条同时也是 §6 schema-tightening candidate。

- **`superpowers:requesting-code-review`**
  - **What was skipped**: 整个 skill。没在 merge 前自审或请 review。
  - **Why this cycle**: 触发条件 = 本变更没 merge 到远端（仍在本地 master HEAD），用户和 agent 在同 session 内联合 review（用户在 D8/D9 决策点接受/驳回方案、在 verify §4 drift 报警后选 A 路径），形态是"持续对话式 review"而非"一次性 PR review"。
  - **How to prevent recurrence**: **CLAUDE.md trigger** — "若 work-product 不进入 PR 流程（本地 master 直接 commit），'review' 在对话内完成本身合规，但 retrospective §4 应明示这点而不是简单标 ✗；建议 skill description 加 'when work doesn't go through PR, conversational review with the user counts as compliance'"。

- **`superpowers:finishing-a-development-branch`**
  - **What was skipped**: 整个 skill。
  - **Why this cycle**: 触发条件 = retrospective 写作时（now）还没决定怎么 finish（直接 push master? 开 PR? 等 archive 完再说?）；用户没明确 finish-branch 指令。
  - **How to prevent recurrence**: **schema graph fix** — 把 `superpowers:finishing-a-development-branch` 从 retrospective 之前调整为 retrospective 之后或并列；当前 schema 写法暗示 retro 写完前必须 finish branch，但实际工作流是"先 archive openspec change，最后再决定 push / PR"。具体改动：openspec/schemas/superpowers-bridge.yaml 把 finishing-a-development-branch 从 plan 阶段的暗示移到 archive 后的可选 follow-up。

## 5. Surprises

- **Chrome 用 `prctl(PR_SET_MM_*)` 把 cmdline 改写成单一空格分隔字符串**：以为 `/proc/<pid>/cmdline` 永远是 NUL 分隔的 argv 数组；实际 Chrome 子进程会把 argv 全部连接、用空格分隔、然后改写。`split(b"\x00")` 拿到的是一整个 chunk，`startswith()` 永远 False。第一次 GPU detection 实测时才发现。已固化为单测。
- **Headless Chrome 默认走 SwiftShader 软渲染 = GPU process 在 CPU 上**：以为 Chrome headless 模式下 GPU process 是个空壳/被禁用；实际它在跑 SwiftShader 软件光栅化，可以占 union 总量 84%。这是导致 team 2000 第一次实测 19.5% 异常的根因。评测机有真 Intel 集显 + `/dev/dri/renderD128`，理论上可以让 Chrome 走真 GPU（A2 路径），但 headless + Ozone + 真 GPU 在 Linux 上历史不稳，留作 follow-up。
- **client-side decode 完全合规但 PGID-subtree 模型看不到它**：spec 一开始就说 "`<video>` / `<canvas>` / WebCodecs / WASM 都可"——可这条 spec 跟"PGID 子树就是 contestant 算力"的隐含假设是矛盾的。设计阶段没意识到这种实现路径的存在，是 brainstorm/design 的盲点。team 2000 的 video_server 是个 AU fanout + 浏览器解码，结构性把 sampler 打成 0。
- **`measured_h265_fps` 在 Playwright 抓帧 cap 下 ≤ 一半 expected**：runner.py 默认 30 fps 抓帧，但 Playwright + Chrome headless 在 commodity 硬件实际只能跑 10-20 Hz，所以 H.265 expected 25 fps 在 measured 上看到 5-7 fps 是正常。这条 scorer.py 的 docstring 已经提过了；但我在最初 brainstorm D3 的 fps 门控选默认 0.25 时没充分考虑这个 ceiling，导致 apply 期发现要调到 0.10 才能让"勉强能解的 contestant"进入 CPU 评分。
- **commit 也能扫进 pre-existing WIP**：`git add <file>` 把整个文件的 working-tree diff staged，不是只 stage 这一刻我的 edit。下次该用 `git add -p`。

## 6. Promote candidates → long-term learning

- [ ] 🟡 **commit 前用 `git add -p` 检查 pre-existing M 状态的文件** → **Promote to** `~/.claude/CLAUDE.md`（Tools 段）
  > **Why**: 本周期 `scripts/evaluator.sh` 和 `CLAUDE.md` 在 session 开始就是 M（用户的 in-flight 工作），Task 7/9 的 commit 一并扫进去了，commit attribution 不准（§2 Miss）。
  > **How to apply**: 每次 `git status` 显示 M 文件中包含**与本任务无直接关系**的项目时，commit 阶段必须 `git add -p <file>` 选 hunk，不能裸 `git add <file>`。

- [ ] 🟡 **"非纯实现的代码改动" = 同步 update design.md / spec delta** → **Promote to** `~/.claude/CLAUDE.md`（OpenSpec 段）
  > **Why**: D8/D9 的 commit `4b112ef` + `b6ddf01` 引入了核心算法变更（采样树形态、默认排除规则），但当时只 commit 了代码、没回写文档；verify 第一次跑标 §4 drift 才发现，多走一轮 rerun。
  > **How to apply**: 在 apply 阶段，commit 一段"超出 plan/design 已记录决策的代码"前，先回到 design.md 加 D-N 段并更新 spec delta，再 commit 代码 + 文档作为同一 commit；这条触发条件 = "如果用一句话回答 'design.md 里现有的 D1..Dn 能预见到这次 commit 的行为吗?' 答 No"。

- [ ] 🟡 **plan.md "task 之间链式依赖检测器"** → **Promote to** writing-plans skill（schema / skill PR）
  > **Why**: §4 跳过 subagent-driven-development 的原因是 plan 的 10 个 Task 都改前序 Task 引入的同一文件，subagent 并行无收益。这种 sequential pattern 当前 plan.md 没在 header 显式标出，writing-plans skill 也没生成检测。
  > **How to apply**: writing-plans skill 应在生成 plan 时分析 "File Structure" 表，若多个 Task 改同一文件（`scorer.py` / `runner.py` 等），在 plan header 加 `> **Execution mode**: sequential — subagent dispatch not applicable due to chained file-edit dependencies` 标记。下次 §4 subagent 行可直接对照此标记标 ✓（"标记 = 主动声明并行无收益" = 合规跳过）。

- [ ] 📌 **诊断字段在 `start()` 阶段就读完所有 baseline PID 的状态** → **One-off**（已在 _cpu_sampler.py 修，不需要 promote）
  > **Why**: 第一次 per_process_top label 全 `?` 的 bug 是因为 label 读取条件错放在 `if prev is None:`，而 baseline PID 在 _tick 时永远 prev≠None。
  > **How to apply**: 此类 "label/metadata once-per-pid" 逻辑应在 start() 阶段就遍历 baseline 读，_tick 只补新进 PID。规则已落到代码 + 注释，不需要外溢到 memory。

- [ ] 🟡 **brainstorm/design 阶段强制问"contestant 的实现路径自由度"** → **Promote to** brainstorming skill 的 clarifying-questions section
  > **Why**: 本周期 brainstorm 没问"contestant 是不是会选 client-side decode"，导致 D1 用纯 PGID-subtree 模型，apply 期撞 team 2000 才补 D8/D9。spec 一直说 "rendering strategy is contestant's choice"，但 brainstorm 没把这一点折射到测量模型上。
  > **How to apply**: 凡是"测量 contestant 行为/资源"类的 brainstorm，应有 mandatory 问题"contestant 可能选哪些实现路径？测量目标在每种路径下都成立吗？"——尤其是当 spec 已明示"实现选择自由"时。

- [ ] 📌 **`CPU_GATE_H265_FPS_RATIO` 推荐值定档**（design.md Open Question #7 carry-forward）→ **One-off**（下次正式赛前再决定）
  > **Why**: 当前 0.10 是 apply 期为容纳 i-frame-only 实现 calibrate 下来的；正式赛场可能想调回 0.25 阻止低帧率"凑数"白拿 CPU 分。本周期只跑了 team 2000 + 7 个 fixture 的部分数据，样本不足以下定论。
  > **How to apply**: 下次 retro（或正式赛前一次 dry-run）应收集 ≥3 个真实 contestant 的 `score.json.cpu.thresholds_used.gate_fps_ratio` + `mean_percent`，做 ratio sweep 看哪个阈值最能 discriminate "真解码 vs 凑数"。
