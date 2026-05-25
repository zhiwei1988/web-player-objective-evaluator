## Why

The evaluator currently stages every submission under `submissions/<team_id>/`, which decouples contestant files from the directory where the contest platform placed the zip. The desired operator/platform contract is simpler: the zip's containing directory is the submission workspace, so extracted files and platform-facing artifacts stay together.

## What Changes

- **BREAKING**: `scripts/evaluator.sh <team_id> <submission_zip>` will extract the submission into `dirname <submission_zip>` instead of `submissions/<team_id>/`.
- The evaluator will no longer create or clear `submissions/<team_id>/` as part of contestant staging.
- Single-top-level-directory lifting remains supported after extraction.
- After extraction and any lifting, the evaluator will grant executable permission to all extracted/staged contestant files.
- `start.sh` remains the required contestant entrypoint and `stop.sh` remains the optional cleanup hook, both invoked from the zip's containing directory.
- `result.info` publication remains in `dirname <submission_zip>/result.info`, now matching the contestant staging directory.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `evaluator`: Change submission staging from `submissions/<team_id>/` to the submission zip's containing directory, and simplify executable permission handling to apply to all extracted contestant files.

## Impact

- Affected scripts: `scripts/_contestant_lifecycle.sh`, indirectly `scripts/evaluator.sh`.
- Affected tests: evaluator lifecycle/self-test coverage around extraction location, single-directory lifting, and executable permissions.
- Affected documentation/specs: `openspec/specs/evaluator/spec.md`, `README.md`, and any repository layout references to `submissions/<team_id>/` as the active staging area.
- No new runtime dependencies or external services.
