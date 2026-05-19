## Context

仓库当前支持两条评测路径：build host 上 `evaluator-local.sh` 直接调用 native venv，以及目标机上 `evaluator-host.sh` 调用 docker 镜像。后者由 `scripts/package.sh` 打成 5/6 文件的 `dist/`，通过 `manifest.json` 摘要锁绑定。这套机制在 OpenSpec 中由独立 capability `portable-bundle` 描述，含 10 条 Requirement，约 530 行 shell/Dockerfile 代码与 157 行规范。

随着评测环境被组织方收归自主可控，"目标机 ≠ 构建机"这一前提不再成立；docker 分发链失去客观需求。继续维护它意味着持续承担 setcap × cap-add 双层授权对齐、container/host `--network host` localhost 对称性约束、`.playwright/` 构建上下文暂存、manifest sha-lock 校验、`evaluator-host.sh` 与 `evaluator-local.sh` 双 wrapper 同步等复杂度。

## Goals / Non-Goals

**Goals:**

- 完全删除 docker / OCI bundle 分发能力，包括所有 spec、脚本与文档痕迹
- 让评测入口在 spec 与代码层面坍缩为单一脚本 `scripts/evaluator.sh`
- 把原本写在便携包 spec 里、实则属于评测语义的 3 条 Requirement（Failure Score on Contestant Unavailable、Build Host Local Shortcut、Portable Self-Test Mode）的合理部分迁入 evaluator capability
- 保留 native build 配套机制（setcap、ldconfig）不动——它们服务于 build.sh，与 docker 删除无关
- 保留管线本体（runner.py / analyzer.py / scorer.py / report.py / lib/profiles.py）不动

**Non-Goals:**

- 不引入新的便携分发方案（tarball、systemd 包、ansible playbook 等）
- 不改变 contestant 运行时契约
- 不修改 scoring 阈值、profile registry、CPU 测量逻辑
- 不重命名 `scripts/evaluator.sh` 本身，也不改其内部管线编排逻辑
- 不修改 `scripts/test.sh` 的默认模式断言集合（只去掉 `--portable` 分支）

## Decisions

### 决策 1：三层脚本坍缩为单一 `scripts/evaluator.sh`

**选择**：删除 `scripts/evaluator-host.sh`、删除 `scripts/evaluator-local.sh`；后者承担的 wrapper 职责（unzip + 单顶层目录提升、contestant `chmod +x` + `setsid`、env vars 导出、`http://127.0.0.1:8080/play?profile=2k&autoplay=1` 60 秒可达性轮询、`stop.sh` + `kill -- -<pgid>`、`flock` 互斥、log 重定向、未生成 score.json 时的失败兜底）并入 `scripts/evaluator.sh`，签名变为 `scripts/evaluator.sh <team_id> <submission_zip>`。

**理由**：双 wrapper 的存在理由仅仅是 docker 边界（一份调 `docker run`，一份调 venv），边界消失后两个文件的差异退化为"是否走 docker"，没有保留必要。`scripts/evaluator.sh` 本就是面向 contestant 流水线的编排器，纳入 host-side 入口职责是最小命名熵的选择。

**替代方案**：保留 `scripts/evaluator.sh` 作为内部 body，把 `evaluator-local.sh` 改名为 `scripts/evaluator-run.sh` 或者沿用现名。被拒因为它制造一个不再有意义的"内部 vs 用户入口"分层，且和用户"名字不保留"的指示冲突。

### 决策 2：互斥锁文件路径与名称

**选择**：`scripts/evaluator.sh` 启动时获取 `flock -n /var/tmp/evaluator.lock`（从原 `/var/tmp/evaluator-host.lock` 重命名）。锁失败仍以 `EX_TEMPFAIL=75` 退出并打印持锁 PID，不阻塞排队。

**理由**：脚本名都不留了，锁文件名跟着改。`/var/tmp/` 路径保留——这是 Linux FHS 对跨重启 ephemeral 锁的推荐位置，避免 `/tmp/` 被 systemd-tmpfiles 老化清理。

### 决策 3：Failure Score 写出仍由 scorer.py 负责

**选择**：当 contestant readiness 60s 超时时，`scripts/evaluator.sh` 调 `scorer.py --failure-reason contestant_frontend_unavailable --output .../score.json --report .../report.html`，scorer.py 自己产出符合正常 schema 的 0/30 JSON 与 HTML，然后入口脚本以退出码 2 终止。

**理由**：原便携包 spec 已强制这一点（"score JSON written through this path MUST share schema with a normal scoring run"）。直接保留这条约束，避免 shell 用 jq 或 heredoc 拼 JSON 造成 schema 漂移。

**与现存 evaluator spec 的关系**：evaluator capability 已有 `Contestant Runtime Contract` / `Frontend never becomes ready` 场景，规定"分配 0 分、记 evaluator.log、清理"。新增的 `Failure Score on Contestant Unavailable` Requirement 是对该抽象规则的具体化（退出码 2、`max_score=30`、`reason="contestant_frontend_unavailable"` 字段），二者不冲突——前者是契约层抽象、后者是入口脚本行为。本变更不修改 `Frontend never becomes ready` 场景措辞。

### 决策 4：`.playwright/` 单删，不替代

**选择**：删除 `.gitignore` 中 `.playwright/`，不创建任何等价物。Playwright 仍由 `scripts/setup.sh` 安装到 `~/.cache/ms-playwright/`。

**理由**：`.playwright/` 是 `package.sh` 把 `~/.cache/ms-playwright/` 暂存到仓库根、为 `docker build` 提供 build context 的产物。docker 删除后这条数据流不再存在。

### 决策 5：Self-Test 折叠进 evaluator 的 `Lifecycle Scripts` Requirement

**选择**：原 portable-bundle 的 `Portable Self-Test Mode`（`scripts/test.sh --portable`）整条 REMOVE。evaluator 既有的 `Lifecycle Scripts` Requirement 已包含 `test.sh` 默认模式、Self-contained test session 场景；MODIFY 该 Requirement 删去 `--portable` 分支、删去 "Portable bundle regression test" 场景、删去 "Cold-start to bundle production" 场景，把所有引用 `evaluator-local.sh` 的措辞替换为 `evaluator.sh`，删去 `package.sh` / `evaluator-host.sh` / `evaluator-local.sh` 三条脚本条目。

**理由**：evaluator 已经有 self-test 概念，无需 ADD 新 Requirement——这是裁剪而非新增。

### 决策 6：`Workspace Layout` Requirement 的 MODIFY 边界

**选择**：MODIFY `Workspace Layout` Requirement，从必备文件清单中删除 `evaluator-host.sh`、`evaluator-local.sh`、`package.sh`、`Dockerfile`、`.dockerignore`、`dist/`；删除"either natively...or inside the portable OCI container"那段双模式表述；保留 `scripts/evaluator.sh` 作为唯一评测入口的描述。MODIFY `Workspace exists after setup` 场景，把对 `evaluator-host.sh` / `evaluator-local.sh` 的 usage 检查替换为对 `scripts/evaluator.sh` 的 usage 检查。

**理由**：`Workspace Layout` 是 evaluator capability 的根基性约束，必须随物理文件同步收缩。

### 决策 7：`portable-bundle` 能力目录在 sync 后撤除

**选择**：本 change 的 specs delta 仅做 REMOVED Requirements。`openspec sync` 将所有 10 条 Requirement 从 `openspec/specs/portable-bundle/spec.md` 移除后，该文件只剩 `# portable-bundle` 标题与 `## Purpose` 段。本 change 不在 spec delta 中处理"目录撤除"——这一步留给 sync 工序自然完成，或在 archive 阶段由组织方手动清理空目录。tasks.md 会标记一个尾部清理项以保证可见性。

**理由**：OpenSpec delta 模型按 Requirement 粒度操作；"capability 整体下线"的语义实际上等价于"所有 Requirement REMOVED"。直接在 specs/portable-bundle/spec.md 里描述这一点即可。

## Risks / Trade-offs

- **风险**：未来若评测需要回到异构目标机，需要重新引入分发方案 → **缓解**：本 change 在 archive 后即留存完整历史；将来重新引入分发能力时，可走新 capability，不必复用同名。

- **风险**：自检脚本 `scripts/test.sh --portable` 是验证便携包 ↔ 本地 score 一致性的唯一手段；删除后失去该断言能力 → **缓解**：评测机 = 构建机后，"一致性"概念退化为"同一进程不同次运行的可复现性"，由 `test.sh` 默认模式覆盖已足够。

- **风险**：`scripts/evaluator.sh` 单脚本膨胀到 ~290 行，可读性下降 → **缓解**：`_contestant_lifecycle.sh` 作为 helper 模块继续存在并被 source；entry 脚本本身只做"获锁 → 解压 → 启动 contestant → 等就绪 → 调度管线 → 清理"的线性流程，函数化即可。

- **风险**：现存 `Contestant Runtime Contract` / `Frontend never becomes ready` 场景与新 `Failure Score on Contestant Unavailable` Requirement 描述同一现象但用不同词汇（前者说"startup timeout"，后者说`reason="contestant_frontend_unavailable"`）→ **缓解**：本 change 不在 spec 层强行统一；代码实现以 scorer.py `--failure-reason` 输出为准，evaluator.log 文本是诊断信息不进 score.json。后续若发现混淆可再起 change 收口。

- **风险**：archive 后历史 PR 评审者看到曾有便携包功能而当前没有，可能困惑 → **缓解**：archive 目录下 `2026-05-15-portable-deployment-bundle/` 历史仍在，本 change 的 proposal 已点明"评测机即构建机"的前提变更。

## Migration Plan

1. 合入 spec delta，运行 `openspec validate` 通过
2. 按 tasks.md 顺序进行代码删除与合并，每一步独立可 commit
3. `scripts/test.sh` 默认模式跑通 7 个 fixture，作为整体回归
4. archive 本 change，sync 主 specs

无运行时数据迁移负担——`dist/` 是构建产物，删除即可；`submissions/` / `results/` 不受影响。

## Open Questions

无。Q1（Failure Score 归属迁移）与 Q2（`.playwright/` 单删）已在 explore 阶段确认。
