## Context

Contestant code currently starts in `scripts/_contestant_lifecycle.sh` via `setsid ./start.sh`, records `contestant.pid`, and is later cleaned up with process-group signals plus a port-8080 fallback. That gives the evaluator a useful contestant boundary for cleanup and CPU sampling, but it does not stop a broken or hostile submission from allocating enough memory to exhaust the host.

The canonical host is Ubuntu 24.04. Local probing on the development host confirmed cgroup v2, user systemd, and non-root transient units that accept `MemoryMax`. That is the right enforcement layer because the limit must apply to the entire contestant process tree, including child processes and helper binaries.

## Goals / Non-Goals

**Goals:**

- Enforce a default 10G hard memory ceiling for contestant `start.sh` and all descendants.
- Keep evaluator, MediaMTX, Playwright, analyzer, scorer, and cleanup outside the contestant memory limit.
- Preserve the existing contestant PGID recording used by CPU sampling and process-group cleanup.
- Detect unsupported hosts before starting contestant code.
- Surface memory-limit failures as contestant-side zero-score outcomes with bounded feedback and normal artifacts.
- Record the effective memory limit in run artifacts for auditability.

**Non-Goals:**

- Do not sandbox CPU, network, disk, GPU, or filesystem access in this change.
- Do not change scoring formulas or the level-0 gate.
- Do not containerize submissions.
- Do not rely on per-process `ulimit` as the primary enforcement mechanism.
- Do not make memory-limit support optional on canonical evaluation hosts.

## Decisions

### Decision 1: Use cgroup v2 through systemd transient units

Use `systemd-run --user` to start the contestant command in a transient unit or scope with:

- `MemoryAccounting=yes`
- `MemoryMax=<effective limit>`
- `MemorySwapMax=0`
- `KillMode=control-group`

Rationale: cgroup memory control is enforced by the kernel across the whole process tree. `ulimit -v` limits individual process address space and is easy to bypass by forking multiple workers. Direct cgroup filesystem management is lower-level and depends on delegation permissions; systemd already owns the user slice and exposes the needed resource-control API on Ubuntu 24.04.

Alternative considered: wrap `start.sh` in `ulimit`. Rejected because it is process-local and can misfire with runtimes that reserve virtual address space.

Alternative considered: run submissions in Docker or another container runtime. Rejected because the project has intentionally removed container mode and currently evaluates submissions natively.

### Decision 2: Treat limiter support as a preflight requirement

Before extracting or starting contestant code, the evaluator should verify:

- `/sys/fs/cgroup` is cgroup v2.
- `systemd-run --user` is available for the evaluator user.
- A transient unit can be created with `MemoryMax`.

If any check fails, exit as an infrastructure failure before contestant code starts. The evaluator must not silently run unbounded because that recreates the host-crash failure mode.

### Decision 3: Keep PGID as the public lifecycle handle

The current CPU sampler and cleanup path expect `RUN_DIR/contestant.pid` to contain a process-group id. The memory-limited launch wrapper should continue to execute `setsid ./start.sh` and record the resulting PGID. The systemd unit name should be recorded separately, for example `RUN_DIR/contestant.systemd_unit`, so cleanup can stop both the cgroup and the process group.

Rationale: cgroup enforcement solves memory containment, but PGID remains useful for existing CPU scoring and belt-and-braces cleanup. Keeping both avoids rewriting CPU sampling in this change.

### Decision 4: Classify memory-limit breaches as contestant-side failures

When the contestant unit reports OOM or the frontend/capture path fails because the contestant was killed after crossing the configured limit, the evaluator should write a failure score with reason `contestant_memory_limit_exceeded`. Contestant-visible feedback should be bounded and explicit, for example `Submission exceeded evaluator memory limit of 10G.`

Rationale: exceeding the published memory contract is a contestant outcome, not an organizer infrastructure fault. It should produce `score.json`, `report.html`, and `result.info` through the same scoring/reporting path as other contestant-side failures.

### Decision 5: Make the memory ceiling configurable but always effective

Default the limit to `10G` and allow an environment override such as `EVALUATOR_CONTESTANT_MEMORY_MAX`. The effective value should be normalized and recorded in `stage_timings.json` or a nearby run metadata artifact.

Rationale: 10G matches the requested policy while still giving operators a controlled way to tune the limit for dry-runs or future contests. The override should not provide a silent "unlimited" mode in production.

## Risks / Trade-offs

- User systemd unavailable in non-interactive service contexts -> document and test the preflight; if the evaluator later runs under a system service, switch the launcher to a system-level transient unit with explicit permissions.
- `systemd-run --scope` versus service-mode details can affect how `start.sh` stdout, PGID, and backgrounded descendants behave -> cover with lifecycle tests before implementation and preserve existing `contestant.log` semantics.
- OOM detection can be asynchronous relative to readiness/capture timeouts -> classify OOM by inspecting the recorded unit state during failure handling before falling back to generic frontend/capture reasons.
- Systemd unit cleanup failure could leave bounded but stale processes -> cleanup should stop the unit, kill the PGID, and finally free port `8080` as the existing backstop.
- `MemorySwapMax=0` may fail on hosts without swap accounting support -> include it in preflight so unsupported hosts fail before contestant code starts.

## Migration Plan

1. Add failing tests for memory limit defaults, preflight failure, limited contestant launch, artifact metadata, and over-limit failure classification.
2. Implement the lifecycle helper changes behind the default 10G policy.
3. Run focused unit tests first, then the existing lifecycle/stage/report/result-info tests.
4. On the target host, run the explicit preflight probe and an over-limit fixture before evaluating real submissions.

Rollback is to revert the change. There is no persisted data migration.

## Open Questions

None. The implementation should use cgroup/systemd hard limits, default to 10G, and fail loudly if the host cannot enforce them.
