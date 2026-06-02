## Why

Some contestant submissions can leak or intentionally allocate enough memory to exhaust the evaluation host, causing the server and active evaluator run to crash. The evaluator needs a host-enforced memory ceiling so a broken submission fails in isolation instead of destabilizing the machine.

## What Changes

- Add a default 10G hard memory limit for each contestant submission process tree.
- Run contestant `start.sh` and all of its descendants inside a host-enforced cgroup/systemd memory boundary.
- Fail loudly before evaluation if the host cannot provide the required hard memory limiter.
- Classify memory-limit breaches as contestant-side failures with clear feedback and normal cleanup.
- Record the effective memory limit in run artifacts so organizers can audit which limit was applied.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `evaluator`: enforce a per-submission contestant process-tree memory limit, preflight limiter availability, and surface memory-limit failures in evaluator artifacts.

## Impact

- Affected scripts: `scripts/evaluator.sh`, `scripts/_contestant_lifecycle.sh`, and possibly `scripts/_stage_timing.sh` if memory-limit metadata is recorded with other run budgets.
- Affected artifacts: `score.json`, `report.html`, `result.info`, `stage_timings.json`, `contestant.log`, and `evaluator.log`.
- Affected tests: lifecycle shell tests for contestant startup/cleanup, memory-limiter preflight behavior, and TDD coverage for over-limit contestant failure classification.
- Host dependency: canonical Ubuntu 24.04 hosts must support cgroup v2 and user/systemd transient units with `MemoryMax`.
