## REMOVED Requirements

### Requirement: Image Build Pipeline

**Reason**: docker / OCI 镜像构建链整体下线，`scripts/package.sh` 与 `Dockerfile` 一并删除。

**Migration**: 无替代物。评测环境改为"在哪台机器上评测就在哪台机器上构建"，由 `scripts/setup.sh` + `scripts/build.sh` + `scripts/deploy.sh` 在目标机本地完成全部依赖准备。

### Requirement: Dist Bundle Layout

**Reason**: 没有镜像就没有 `dist/` 分发产物，`manifest.json` sha-lock、SHA256SUMS、tar.zst 包等概念全部失效。

**Migration**: 无替代物。仓库自身（含 git submodule pin）即"构建可复现单位"，`git rev-parse HEAD` 与 `git submodule status` 取代 `manifest.json` 的版本指纹角色。

### Requirement: Target Host Operator Entry

**Reason**: `scripts/evaluator-host.sh`（容器化目标机入口）删除；评测入口坍缩为单一 `scripts/evaluator.sh`，相应行为约束以新 Requirement `Evaluator Entry Script` 形式落入 evaluator capability。

**Migration**: 操作员现在直接调用 `scripts/evaluator.sh <team_id> <submission_zip>`，不再需要 `docker load` 或 manifest 校验步骤。

### Requirement: Container Network Model

**Reason**: 没有容器，`--network host` / cap-add / 容器内 MediaMTX 与宿主 contestant 的对称 localhost 可达性约束全部失去对象。

**Migration**: 无替代物。MediaMTX 与 contestant 现在同处宿主 host 同一 net namespace，localhost 可达性是默认事实，不需要 spec 化。

### Requirement: Image Versioning and Verification

**Reason**: 没有镜像就没有镜像版本锁。

**Migration**: 无替代物。版本一致性由"评测机即构建机"前提加 git submodule pin 保证。

### Requirement: Concurrent Run Mutex

**Reason**: `scripts/evaluator-host.sh` 删除，其 flock `/var/tmp/evaluator-host.lock` 互斥锁约束同步下线；评测入口的互斥语义随 `Evaluator Entry Script` Requirement 迁入 evaluator capability，锁文件重命名为 `/var/tmp/evaluator.lock`。

**Migration**: 行为不变（`flock -n` 失败仍以 `EX_TEMPFAIL=75` 退出并打印持锁 PID），仅入口名与锁文件名变化。

### Requirement: Failure Score on Contestant Unavailable

**Reason**: 该约束本质是评测语义（contestant 起不来时如何生成失败 score.json），与"是否容器化"无关，归属 portable-bundle capability 是历史包袱。语义以新 Requirement `Failure Score on Contestant Unavailable` 形式迁入 evaluator capability。

**Migration**: scorer.py `--failure-reason contestant_frontend_unavailable` 输出契约、`max_score=30` / `objective_total=0` / 退出码 `2` 等约束在 evaluator capability 下完整保留，仅入口脚本从 `evaluator-host.sh` 改为 `evaluator.sh`。

### Requirement: Build Host Local Shortcut

**Reason**: `scripts/evaluator-local.sh` 删除，其全部 wrapper 职责合并进 `scripts/evaluator.sh`。"build-host shortcut vs target-host docker run"的对照关系本身随 docker 删除消失。

**Migration**: 用户改用 `scripts/evaluator.sh <team_id> <submission_zip>`；签名、退出码 (0/1/2/75)、互斥语义均保留，所有 host-side orchestration 行为以 evaluator capability `Evaluator Entry Script` Requirement 落规范。

### Requirement: Target Host Prerequisites

**Reason**: "目标机仅需 docker + zstd"的最小依赖论述只在便携包模型下成立。删除 docker 路径后，目标机即构建机，依赖等价于 `scripts/setup.sh` 的 apt 列表。

**Migration**: 目标机准备工作以 `scripts/setup.sh` + `scripts/build.sh` + `scripts/deploy.sh` 在该机器上跑一遍为准；具体 apt 包列表由 evaluator capability `Host Toolchain Documentation` Requirement 维护。

### Requirement: Portable Self-Test Mode

**Reason**: `scripts/test.sh --portable` 模式专门验证便携包 ↔ 本地的 score 一致性，docker 删除后失去断言对象。

**Migration**: `scripts/test.sh` 默认模式（既有 evaluator capability `Lifecycle Scripts` Requirement 已涵盖）继续作为唯一回归测试入口，覆盖全部 fixture 的正负样例断言。`--portable` 标志位与相应代码路径一并移除。
