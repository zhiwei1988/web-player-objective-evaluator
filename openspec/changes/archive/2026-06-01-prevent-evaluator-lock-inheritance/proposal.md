## Why

The evaluator can report `another evaluator run is in progress` after a prior run has already finished because child processes can inherit the flock file descriptor for `/var/tmp/evaluator.lock`. This blocks operators from starting the next evaluation even when the evaluator parent process has exited normally.

## What Changes

- Prevent evaluator-owned lock file descriptors from being inherited by contestant, RTSP, capture, analysis, scoring, browser, and helper child processes.
- Preserve current lock semantics for true concurrent evaluator invocations: a second evaluator still exits immediately with `75 / EX_TEMPFAIL` while the first evaluator parent is active.
- Add regression coverage for both normal completion and failure/cleanup paths where child processes may outlive the evaluator parent.
- Make submission extraction non-interactive so repeated evaluations using the same zip directory cannot hang on `unzip` prompts.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `evaluator`: Tighten the Evaluator Entry Script lifecycle requirement so the flock is held only by the evaluator parent process and is released when that process exits, regardless of surviving child processes.

## Impact

- Affected scripts: `scripts/_contestant_lifecycle.sh`, `scripts/evaluator.sh`, `scripts/start_rtsp.sh`, and any helper invocation that can spawn long-lived descendants.
- Affected tests: lifecycle/entrypoint regression tests for lock inheritance and repeated extraction.
- No API or scoring schema changes.
