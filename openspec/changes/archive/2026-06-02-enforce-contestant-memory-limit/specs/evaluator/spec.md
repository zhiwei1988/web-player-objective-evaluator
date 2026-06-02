## ADDED Requirements

### Requirement: Contestant Memory Limit

The evaluator SHALL enforce a hard memory limit on each contestant submission process tree. The default effective limit SHALL be `10G`; operators MAY override it with `EVALUATOR_CONTESTANT_MEMORY_MAX`, but the evaluator MUST NOT silently run a contestant submission without an effective hard memory limit on the canonical Ubuntu 24.04 host.

The limit SHALL apply to contestant `start.sh` and all descendant processes, including relay servers, transmuxers, decoders, Node/Python helpers, and any child process forked by the submission. The limit SHALL NOT apply to evaluator-owned processes such as MediaMTX, Playwright/Chromium, `runner.py`, `analyzer.py`, `scorer.py`, `report.py`, or cleanup helpers.

The evaluator SHALL use a cgroup/systemd memory boundary for enforcement. A per-process `ulimit` alone SHALL NOT satisfy this requirement.

#### Scenario: Default memory limit is applied

- **WHEN** `scripts/evaluator.sh` starts a contestant submission and `EVALUATOR_CONTESTANT_MEMORY_MAX` is not set
- **THEN** contestant `start.sh` and its descendants run under an effective hard memory limit of `10G`
- **THEN** the run artifacts record the effective contestant memory limit

#### Scenario: Environment override is applied

- **WHEN** an organizer runs `scripts/evaluator.sh` with `EVALUATOR_CONTESTANT_MEMORY_MAX=512M`
- **THEN** contestant `start.sh` and its descendants run under an effective hard memory limit of `512M`
- **THEN** the run artifacts record `512M` as the effective contestant memory limit

#### Scenario: Limit covers forked descendants

- **WHEN** contestant `start.sh` forks child worker processes that together allocate more than the effective memory limit
- **THEN** the contestant cgroup is stopped or killed without exhausting host memory
- **THEN** evaluator-owned processes remain outside that contestant memory limit and continue to cleanup and publish artifacts

#### Scenario: Evaluator helpers are not memory limited by contestant policy

- **WHEN** capture, analysis, scoring, report generation, MediaMTX, or Playwright/Chromium run during an evaluation
- **THEN** those evaluator-owned processes do not execute inside the contestant memory-limited cgroup

### Requirement: Contestant Memory Limiter Preflight

Before starting contestant code, the evaluator SHALL verify that the host can enforce the contestant memory limit. On the canonical Ubuntu 24.04 host, this preflight SHALL require cgroup v2 and a systemd transient unit mechanism capable of applying `MemoryMax` for the evaluator user. If the preflight fails, the evaluator SHALL fail loudly as an infrastructure failure before invoking contestant `start.sh`.

#### Scenario: Supported host passes preflight

- **WHEN** the evaluator user can create a transient systemd unit with `MemoryAccounting=yes`, `MemoryMax=<effective limit>`, `MemorySwapMax=0`, and `KillMode=control-group`
- **THEN** the evaluator proceeds to extract and start the contestant submission under that limit

#### Scenario: Unsupported host fails before contestant starts

- **WHEN** cgroup v2 is unavailable, `systemd-run --user` is unavailable, or a transient unit cannot apply `MemoryMax`
- **THEN** `scripts/evaluator.sh` exits with an infrastructure failure before invoking contestant `start.sh`
- **THEN** the evaluator does not run the submission without a hard memory limit

#### Scenario: Preflight result is logged

- **WHEN** the memory-limiter preflight passes or fails
- **THEN** `evaluator.log` records the effective memory limit and enough preflight detail for an organizer to diagnose host support

### Requirement: Contestant Memory Limit Failure Classification

When a contestant submission exceeds the effective memory limit, the evaluator SHALL classify the outcome as a contestant-side failure with reason `contestant_memory_limit_exceeded`. The evaluator SHALL still run normal cleanup, release the evaluator lock, stop the contestant unit or cgroup, free port `8080`, and publish schema-compatible `score.json`, `report.html`, and `result.info` artifacts whenever the scoring path can run.

Contestant-facing feedback for this failure SHALL be bounded and explicit, including the effective memory limit. The feedback SHALL be eligible for `result.info` because the failure is attributable to the contestant submission.

#### Scenario: Memory limit breach writes failure score

- **WHEN** contestant code exceeds the effective memory limit before or during evaluation
- **THEN** `score.json` exists with `objective_total = 0`, `max_score = 30`, and `reason = "contestant_memory_limit_exceeded"`
- **THEN** `score.json.contestant_feedback` includes a bounded message naming the effective memory limit
- **THEN** `report.html` and `result.info` are produced from the same score path

#### Scenario: Memory limit breach does not leave stale contestant processes

- **WHEN** contestant code is stopped because it exceeded the effective memory limit
- **THEN** cleanup stops the contestant cgroup or transient unit, kills the recorded contestant process group as a fallback, and frees port `8080`
- **THEN** a later evaluator invocation can acquire `/var/tmp/evaluator.lock` and start normally

#### Scenario: Memory limit breach during capture is preferred over generic timeout

- **WHEN** a capture round fails or times out after the contestant unit reports an OOM or memory-limit stop
- **THEN** the evaluator records `contestant_memory_limit_exceeded` rather than only a generic startup or capture timeout reason
