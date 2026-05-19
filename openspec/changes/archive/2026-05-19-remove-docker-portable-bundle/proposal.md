## Why

评测环境现已自主可控，组织方在每台目标机器上都能直接执行源码构建链，"在哪台机器上评测就在哪台机器上构建"成为既定事实。docker + 便携 OCI 包这一整套分发与隔离机制因此失去存在理由——它带来的复杂度（多阶段 Dockerfile、manifest 摘要锁、setcap 与 cap-add 双层授权、container/host 网络模型对齐、`.playwright/` 构建上下文暂存、双 wrapper 脚本）远超它给评测准确性带来的价值。删除这条分发路径让仓库只保留一条简洁、可验证的执行流。

## What Changes

- **BREAKING** 删除整个便携包分发能力：`scripts/package.sh`、`Dockerfile`、`.dockerignore`、`dist/` 产物
- **BREAKING** 删除目标机入口 `scripts/evaluator-host.sh`
- **BREAKING** 删除原生入口 `scripts/evaluator-local.sh`，其 wrapper 职责（unzip、contestant lifecycle、flock 互斥、failure-score safety net）合并进 `scripts/evaluator.sh`，使后者成为面向操作员的唯一入口；签名变为 `scripts/evaluator.sh <team_id> <submission_zip>`
- 评测语义 `Failure Score on Contestant Unavailable` 与入口 `Build Host Local Shortcut`/`Portable Self-Test Mode` 从 portable-bundle 能力迁入 evaluator 能力——这些约束本就属于评测语义而非分发语义
- 评测入口的 flock 互斥约束（原仅在便携包侧 spec 化）作为 evaluator 能力的入口要求保留
- 文档全量重写：`CLAUDE.md` 的 Overview / 引用表 / 约定段落 / git-ignored 列表均清除 docker 与便携包相关内容；`README.md` 重写为单一原生流程
- `.gitignore` 移除 `dist/` 与 `.playwright/` 条目
- `scripts/build.sh` / `scripts/_contestant_lifecycle.sh` / `scripts/test.sh` 中遗留的 docker/oci 相关注释或分支清理

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `portable-bundle`：整个能力下线，所有 10 条 Requirement 全部 REMOVED；其中 3 条评测语义性的约束（Failure Score on Contestant Unavailable、Build Host Local Shortcut、Portable Self-Test Mode）以语义迁移的形式重新落到 evaluator 能力
- `evaluator`：新增 3 条 Requirement——单一评测入口（合并后的 `scripts/evaluator.sh`，接收 `<team_id> <submission_zip>`）、Contestant 启动失败时写出 0 分 JSON 的语义、`--self-test` 自检模式；评测入口的 flock 互斥要求作为入口约束的一部分明确化

## Impact

- **代码删除**：`Dockerfile`、`scripts/package.sh`、`scripts/evaluator-host.sh`、`scripts/evaluator-local.sh`、`.dockerignore`（如存在），合计约 530 行
- **代码合并**：`scripts/evaluator.sh` 接管 wrapper 职责，从 ~217 行扩张到约 290 行
- **文档**：`CLAUDE.md`、`README.md` 大幅改写
- **构建链不变**：`setup.sh` / `build.sh` / `deploy.sh` 不动；ldconfig、setcap 等 native build 配套机制保留
- **管线本体不变**：`runner.py` / `analyzer.py` / `scorer.py` / `report.py` / `lib/profiles.py` 不动
- **OpenSpec specs**：`openspec/specs/portable-bundle/` 在 sync 后整目录撤除
- **可复现性策略转变**：取消"目标机字节级等价于构建机"承诺，承诺改为"评测机即构建机"，由 `setup.sh` 锁定的 submodule pin + Playwright pin 保证一致性
