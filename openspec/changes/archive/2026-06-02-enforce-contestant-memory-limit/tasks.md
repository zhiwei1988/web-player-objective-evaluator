## 1. Failing Tests

- [x] 1.1 Add tests for default `EVALUATOR_CONTESTANT_MEMORY_MAX=10G`, environment override parsing, invalid value rejection, and recording the effective limit in run metadata.
- [x] 1.2 Add tests for memory-limiter preflight success and failure, including the assertion that contestant `start.sh` is not invoked when cgroup/systemd hard limiting is unavailable.
- [x] 1.3 Add lifecycle tests proving the contestant launch command runs under a systemd/cgroup memory boundary, preserves `RUN_DIR/contestant.pid` as a PGID, records the systemd unit name, and keeps contestant stdout/stderr in `contestant.log`.
- [x] 1.4 Add cleanup tests proving the evaluator stops the contestant systemd unit or cgroup, kills the recorded process group as a fallback, frees port `8080`, and does not leave `/var/tmp/evaluator.lock` inherited.
- [x] 1.5 Add failure-classification tests for an over-limit contestant fixture, expecting `reason = "contestant_memory_limit_exceeded"`, `objective_total = 0`, bounded contestant feedback, and normal `report.html` / `result.info` publication.
- [x] 1.6 Add report/result-info/diagnose tests for surfacing the effective memory limit and memory-limit failure without exposing unbounded internal logs.

## 2. Preflight and Configuration

- [x] 2.1 Implement a lifecycle helper that loads, validates, and exports the effective contestant memory limit with default `10G` and operator override via `EVALUATOR_CONTESTANT_MEMORY_MAX`.
- [x] 2.2 Implement cgroup/systemd preflight checks for cgroup v2, `systemd-run --user`, and a transient unit that accepts `MemoryAccounting=yes`, `MemoryMax`, `MemorySwapMax=0`, and `KillMode=control-group`.
- [x] 2.3 Call the preflight before contestant `start.sh` can run, fail as infrastructure when unsupported, and log enough detail in `evaluator.log` for host diagnosis.
- [x] 2.4 Record the effective memory limit in run artifacts, preferably alongside existing stage timing budgets or adjacent run metadata.

## 3. Contestant Launch and Cleanup

- [x] 3.1 Update `clx_start_contestant` to launch `setsid ./start.sh` inside the memory-limited systemd transient unit while preserving the existing PGID contract for CPU sampling.
- [x] 3.2 Record the contestant systemd unit name in the run directory and include it in lifecycle logs.
- [x] 3.3 Update contestant cleanup to stop or kill the recorded systemd unit or cgroup before falling back to process-group and port cleanup.
- [x] 3.4 Ensure evaluator-owned helpers, MediaMTX, Playwright/Chromium, analysis, scoring, and cleanup commands still run outside the contestant memory-limited unit.

## 4. Failure Classification and Artifact Surfacing

- [x] 4.1 Add `contestant_memory_limit_exceeded` as a contestant-side failure reason accepted by scoring and result-info publication.
- [x] 4.2 Detect contestant OOM or memory-limit unit state during readiness, capture, and cleanup failure paths, preferring `contestant_memory_limit_exceeded` over generic startup or capture timeout reasons.
- [x] 4.3 Pass bounded contestant feedback naming the effective memory limit into `scorer.py` when the memory limit is exceeded.
- [x] 4.4 Surface the memory-limit failure and effective limit in `report.html` and keep `result.info` concise.
- [x] 4.5 Update `scripts/diagnose_run.py` to summarize memory-limit metadata when present while remaining backward compatible with older runs.

## 5. Verification

- [x] 5.1 Run focused tests for lifecycle, scoring, result-info, report, diagnose, and stage timing changes.
- [x] 5.2 Run `openspec validate enforce-contestant-memory-limit --strict`.
- [x] 5.3 On a cgroup v2 Ubuntu host, run a bounded OOM fixture with a small override such as `EVALUATOR_CONTESTANT_MEMORY_MAX=128M` and confirm the host survives and artifacts show `contestant_memory_limit_exceeded`.
- [x] 5.4 Run an end-to-end reference submission with the default `10G` limit and confirm normal scoring still works.
