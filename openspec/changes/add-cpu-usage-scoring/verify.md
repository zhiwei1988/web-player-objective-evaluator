# Verification Report

**Change**: `add-cpu-usage-scoring`
**Verified at**: `2026-05-15 16:55` (rerun after §4 drift fix)
**Verifier**: claude-opus-4-7 via `/opsx:continue` → openspec-bridge verify slot

---

## 1. Structural Validation (`openspec validate --all --json`)

- [x] 全数 items `"valid": true`

**结果**:

```text
✓ change/add-cpu-usage-scoring
✓ spec/evaluator
✓ spec/portable-bundle
✓ change/portable-deployment-bundle
Totals: 4 passed, 0 failed (4 items)
```

无失败项。

---

## 2. Task Completion (`tasks.md`)

- [x] 所有 `- [ ]` 已变为 `- [x]`（45 / 45）

未完成任务：无。

---

## 3. Delta Spec Sync State

| Capability | Sync 状态 | 备注 |
|---|---|---|
| `evaluator` | ✗ 待 sync | delta 的 MODIFIED Scoring + ADDED Contestant CPU Usage Measurement 尚未合入 `openspec/specs/evaluator/spec.md`；主 spec 里 grep `40-point` / `score_cpu` / `Contestant CPU Usage Measurement` 全部 0 命中。**预期** — `/opsx:archive` 在执行时会自动 apply delta；本工件不阻塞此操作。 |

---

## 4. Design / Specs Coherence Spot Check

抽样比对 `design.md` 的决策是否反映在 `specs/evaluator/spec.md` 中：

| 抽样项 | design 描述 | specs 对应 | 差距 |
|---|---|---|---|
| D1 测量目标 + 整机归一化 | "PGID 进程组 + all_cores_total" | ADDED Requirement 含 "normalization: 'all_cores_total'" + setsid 前提 | ✓ |
| D2 仅 H.265 round 采样 | "排除冷启动/清理期" | ADDED Requirement Scenario "Sampler is a no-op for H.264" | ✓ |
| D3 fps 门控复用 partial 阈值 | `CPU_GATE_H265_FPS_RATIO` 默认 0.10（D6 表格记录从 0.25 calibrate 过来） | MODIFIED Scoring 5 步求值顺序 | ✓ |
| D4 评分公式形态 | "≤5 / 6–20 / >20" 线性衰减 | MODIFIED Scoring 含公式 + 评分表 | ✓ |
| D5 runner.py 内嵌 sampler | "stdlib daemon thread" | ADDED Requirement Scenarios | ✓ |
| D6 6 个阈值参数化 | "thresholds_used 留痕 + 当前默认值表" | MODIFIED Scoring + Scenario "Thresholds-used audit field" | ✓ |
| D7 max_score 30→40 + cpu 块 | "objective_total ≤ 40" | MODIFIED Scoring "max_score of 40" | ✓ |
| **D8 sampler 跟随 Chrome 进程树**（本轮新加） | "PGID 子树 ∪ extra_root_pid 的 ppid 后代，PID 去重" | ADDED Requirement 新增 SHALL 段 + Scenario "Sampler also follows the Playwright Chrome subtree" + Scenario "Playwright private-API failure degrades safely" | ✓ |
| **D9 默认排除 chrome GPU process**（本轮新加） | "cmdline 含 --gpu-preferences= 子串匹配；exclude_chrome_gpu=True；excluded_gpu_pids 留痕" | ADDED Requirement 新增 SHALL 段 + Scenario "Chrome GPU process is excluded by default" + Scenario "Chrome cmdline-rewrite layout is handled" | ✓ |

**漂移修复总结**（上一轮 verify 标的 drift 现已回写）:

| 来源 commit | 决策 | 回写位置 | 状态 |
|---|---|---|---|
| `4b112ef` A 方案 — sampler 跟 Playwright Chrome 树 | `design.md::D8` + spec delta 2 新 Scenario | ✓ |
| `b6ddf01` A1 — 排除 chrome GPU process | `design.md::D9` + spec delta 2 新 Scenario | ✓ |
| `CPU_GATE_H265_FPS_RATIO` 默认 0.25 → 0.10 | `design.md::D6` "当前默认值"表格 + Open Question #7 | ✓ |

复核命令（实测）:

```text
grep -c '^### D8\|^### D9' design.md       → 2 (expected 2)   ✓
grep -c <4 new scenarios> spec.md           → 4 (expected 4)   ✓
openspec validate add-cpu-usage-scoring     → valid            ✓
pytest tests/                               → 37 passed        ✓
```

无残留 drift。

---

## 5. Implementation Signal

- [x] 本变更相关的代码改动**已全部 commit**
- [ ] Worktree 内**无未 staged 的档案**——⚠️ 存在 pre-existing M / untracked 文件，与本变更无关

**本变更 commit 范围**（含 verify 阶段的 spec/design 回写，待 commit）:

```
b6ddf01 feat(cpu_sampler): exclude chrome GPU process (SwiftShader noise filter)
4b112ef feat(cpu_sampler): extend to follow Playwright Chrome process tree
d24dada test(opsx): close 3 deferred tasks with E2E evidence
c6bbefd chore(opsx): mark add-cpu-usage-scoring tasks complete (42/45, 3 deferred)
aa5632a docs(CLAUDE): bump objective total to 40 and document CPU sub-score scope
ef5de5b feat(report): render CPU sub-score block in evaluator HTML report
15cebfc feat(evaluator.sh): forward contestant PGID to H.265 runner for CPU sampling
c61ac98 feat(analyzer): pass capture_meta.json cpu block into metrics output
9f73cde feat(runner): embed CPU sampler in H.265 capture loop
8986dad feat(scorer): add CPU sub-score with 5 tunables and 40-point max_score
2704d5b feat(cpu_sampler): pure-stdlib /proc PGID sampler with daemon thread
b51c27c test: add failing tests for CPU sub-score and /proc sampler
+ pending commit: design.md (D8/D9) + spec delta (4 new scenarios) + verify.md rerun
```

**Worktree 残留 M / untracked**（与本变更无关，session 启动时已存在）:

```
M .gitignore
M openspec/specs/evaluator/spec.md   ← 主 spec；archive 时由 /opsx:archive 同步
M scripts/test.sh
?? .dockerignore Dockerfile
?? openspec/changes/portable-deployment-bundle/
?? openspec/schemas/ openspec/specs/portable-bundle/
?? scripts/_contestant_lifecycle.sh scripts/evaluator-host.sh
?? scripts/evaluator-local.sh scripts/package.sh
?? third_party/libdmtx
```

**评估**：本变更引入的代码改动全部已 commit；spec/design 回写即将 commit。残留 M / untracked
是用户其他 in-flight 工作，与本变更解耦。**非阻塞**。

---

## 6. Front-Door Routing Leak Detector

- [x] `docs/superpowers/specs/` 不存在（无泄漏）

无文件列出。✓

---

## 7. Deferred Manual Dogfood vs Automated Test Equivalence

`plan.md` 中**未使用 `[~]` 标记**任何 deferred 行；3 项 plan 中标 `[x]` 但 implementation 阶段
真正运行的证据如下:

| Deferred dogfood (plan §) | Equivalent automated test | Coverage assessment | 真正 gap? |
|---|---|---|---|
| Task 4.6 runner.py + live frontend | `tests/test_cpu_sampler.py::test_sampler_measures_single_core_spin` + E2E run `selftest_fake_overlay_20260515_113348` 实测 capture_meta.json 含 sample_count=44 / sample_hz_used=1.0 | sampler 启停 + capture_meta.json 落盘契约全覆盖 | ❌ 已等价覆盖 |
| Task 6.4 evaluator-local.sh + reference.zip | E2E `results/sanity_e2e_20260515_112129/score.json` 实测 max_score=40, cpu.gated=true gate_reason="h265_round_failed", objective_total=15 | 全管线 + 5 步门控 + score.json 形状 | ❌ 已等价覆盖 |
| Task 9.1 scripts/test.sh 全 fixture | E2E PASS 7/7 exit 0；capture_meta.json 仅在熬过 readiness 的 3 个 fixture（fake_overlay/iframe_only/static_frame）h265 round 落盘 | 全部 fixture 行为 + capture_meta 落盘契约 | ❌ 已等价覆盖 |

verify 阶段新增的 E2E（不属于 plan 任务，用于校准 D8/D9）:

| 验证项 | 路径 | 结论 |
|---|---|---|
| A 方案 effect 实测 | team 2000 跑（commit `4b112ef` 后）→ mean 0.037% → 19.54% | 浏览器树纳入正确 |
| A1 GPU 排除 effect 实测 | team 2000 跑（commit `b6ddf01` 后）→ mean 19.54% → 3.14%, excluded_gpu_pids=[1076878] | SwiftShader 噪声成功剔除 |
| GPU 检测器单测 | `tests/test_cpu_sampler.py::test_cmdline_is_chrome_gpu_handles_both_layouts` 5 case 全过 | Chrome cmdline 两种 layout 都识别 |

无 gap。

---

## Overall Decision

- [x] ✅ **PASS** — 可进入 retrospective 与 archive

**剩余非阻塞备注**:

1. **§3 主 spec 待 sync**：预期由 `/opsx:archive` 自动 apply delta 完成；verify 不处理。
2. **§5 Pre-existing M / untracked files**：与本变更无关，用户独立决定何时清理。
3. **`CPU_GATE_H265_FPS_RATIO` 默认值定档**：当前 0.10（calibrated during apply 期），
   是否调回 0.25 应在 retrospective 给出推荐——见 design.md Open Question #7。

**下一步**：commit spec/design 回写 → `/opsx:continue` 进入 `retrospective` → `/opsx:archive`。
