## 1. 入口脚本合并

- [x] 1.1 在 `scripts/evaluator.sh` 顶部加入 `<team_id> <submission_zip>` 入参解析、usage 文本与参数校验
- [x] 1.2 把 `scripts/evaluator-local.sh` 中获取 `flock -n /var/tmp/evaluator.lock`（注意锁文件名从 `evaluator-host.lock` 改为 `evaluator.lock`）的逻辑搬入 `scripts/evaluator.sh`，包含 `EX_TEMPFAIL=75` 失败与持锁 PID 打印
- [x] 1.3 把 `clx_precheck_ports 8080`、`clx_prepare_run_dir`、`clx_acquire_lock` 等调用从 `evaluator-local.sh` 搬入 `scripts/evaluator.sh`；保留 `_contestant_lifecycle.sh` 作为 source 模块
- [x] 1.4 把 `evaluator-local.sh` 的 unzip + 单顶层目录提升、contestant `start.sh` `chmod +x` + `setsid` 启动、`RTSP_SERVER_HOST` / `RTSP_SERVER_PORT` / `FRONTEND_PORT` env 导出、`http://127.0.0.1:8080/play?profile=2k&autoplay=1` 60s 轮询逻辑搬入 `scripts/evaluator.sh`
- [x] 1.5 把 `evaluator-local.sh` 中"未生成 score.json 时调 scorer.py 写失败 JSON"的 cleanup 兜底逻辑搬入 `scripts/evaluator.sh`；确保用 `scorer.py --failure-reason contestant_frontend_unavailable` 而非 shell 拼 JSON
- [x] 1.6 在 `scripts/evaluator.sh` 中安装 `trap cleanup EXIT INT TERM`，确保 contestant `stop.sh` (10s 超时)、`kill -- -<pgid>`、`fuser -k 8080/tcp`、MediaMTX 停止、flock 释放都在 cleanup 中
- [x] 1.7 调整 `scripts/evaluator.sh` 退出码：成功 0、contestant unavailable 2、锁占用 75、其他启动失败 1；移除原仅在 evaluator-local.sh 中的额外退出码语义
- [x] 1.8 让 `scripts/evaluator.sh` 在结束时把 `score.json` 内容打印到 stdout

## 2. 删除文件

- [x] 2.1 删除 `scripts/evaluator-host.sh`
- [x] 2.2 删除 `scripts/evaluator-local.sh`
- [x] 2.3 删除 `scripts/package.sh`
- [x] 2.4 删除根目录 `Dockerfile`
- [x] 2.5 删除 `.dockerignore`（如存在）
- [x] 2.6 删除已生成的 `dist/` 目录（如有）；删除已生成的 `.playwright/` 目录（如有）
- [x] 2.7 从 `.gitignore` 移除 `dist/` 与 `.playwright/` 条目

## 3. 清理 docker / 便携包痕迹

- [x] 3.1 修改 `scripts/test.sh`：删除 `--portable` flag 解析、`portable_main()` 函数及其所有调用；删除任何对 `evaluator-local.sh` / `evaluator-host.sh` / `package.sh` 的引用，统一替换为 `scripts/evaluator.sh`
- [x] 3.2 修改 `scripts/test.sh`：未识别的 `--portable` 参数 SHALL 触发 usage 错误并非零退出
- [x] 3.3 检查并清理 `scripts/build.sh` 中所有 docker / OCI / `.playwright/` 暂存相关分支或注释
- [x] 3.4 检查并清理 `scripts/_contestant_lifecycle.sh` 中所有 docker / cap-add / container 相关分支或注释
- [x] 3.5 检查 `scripts/teardown.sh` 是否引用了 `evaluator-host.sh` / `evaluator-local.sh`；如有则改为引用 `evaluator.sh`
- [x] 3.6 检查 `rtsp_server/mediamtx.yml` 是否有 docker 相关配置或注释；如有则清理

## 4. 文档同步

- [x] 4.1 重写 `CLAUDE.md` Overview 段落：删除"Two execution modes"双模式表述，只保留单一 native 流程（`setup.sh → build.sh → deploy.sh → scripts/evaluator.sh`）
- [x] 4.2 修改 `CLAUDE.md` "Where authoritative info lives" 引用表：删除 `portable-bundle` 一行
- [x] 4.3 修改 `CLAUDE.md` Conventions 段落："Source-build rule" 中关于容器内 ldconfig 的描述、"MediaMTX lifecycle is per-run, owned by scripts/evaluator.sh" 描述保留；删除任何引用 `evaluator-host.sh`、`evaluator-local.sh`、`package.sh`、`dist/`、容器 / OCI / docker 的措辞
- [x] 4.4 修改 `CLAUDE.md` "Contestant contract is frozen" 段落：删除"contestant 在 host 上跑、容器只承载 evaluator"那段对照说明
- [x] 4.5 修改 `CLAUDE.md` "Idempotent scripts" 列表：删除 `package.sh` / `evaluator-host.sh` / `evaluator-local.sh`，保留 `evaluator.sh`；删除 `/var/tmp/evaluator-host.lock` 的提及，改为 `/var/tmp/evaluator.lock`
- [x] 4.6 修改 `CLAUDE.md` "Path discipline" 段落：删除 `scripts/evaluator-host.sh` 的 `ROOT_DIR=PWD` 例外；若 `scripts/evaluator.sh` 沿用相同语义（操作员任意 cwd），保留例外但改名
- [x] 4.7 修改 `CLAUDE.md` "Generated dirs are git-ignored" 列表：删除 `dist/` 与 `.playwright/`
- [x] 4.8 重写 `README.md`：删除任何 docker / 便携包 / target host / dist 相关章节，留下单一原生流程的 quick-start
- [x] 4.9 验证 `openspec/changes/remove-docker-portable-bundle/` 下的 proposal / design / specs / tasks 互相一致；运行 `openspec validate remove-docker-portable-bundle` 通过

## 5. 自检与归档准备

- [x] 5.1 在已 setup+build+deploy 的本机运行 `scripts/evaluator.sh team_ref test_submissions/reference.zip`，确认 `results/team_ref_<ts>/score.json` 与 `report.html` 正常生成、stdout 打印 score.json、退出码 0
- [x] 5.2 在已 setup+build+deploy 的本机运行 `scripts/evaluator.sh team_broken test_submissions/never_starts.zip`（或等价的 contestant unavailable fixture），确认退出码 2、score.json 的 `reason == "contestant_frontend_unavailable"`、`objective_total == 0`、`max_score == 30`
- [x] 5.3 在第一次 evaluator.sh 仍在运行时启动第二个调用，确认退出码 75 并打印持锁 PID
- [x] 5.4 跑 `scripts/test.sh`，确认 7 个 fixture 全部通过
- [x] 5.5 在仓库内全局搜索 `docker|Dockerfile|evaluator-host|evaluator-local|package\.sh|\.dockerignore|portable-bundle|dist/|\.playwright/` 残留，确保 OpenSpec archive 与 docs/README 之外没有命中
- [x] 5.6 archive 阶段后：删除 `openspec/specs/portable-bundle/` 整目录（sync 后该 spec 文件已只剩标题与 Purpose，可整目录撤除）
