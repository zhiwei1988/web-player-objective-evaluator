## Why

Contestants currently see only final scoring numbers in the `result.info` `info` block. When their submission fails during startup or browser capture, the actionable failure reason is confined to organizer-facing artifacts, so contestants cannot tell whether they missed `start.sh`, never exposed the frontend, failed the player readiness contract, or hit a visible browser/runtime error.

## What Changes

- Add contestant-visible execution feedback to the `result.info` `info` block when the failure is attributable to the submitted work.
- Keep the existing score breakdown in `info`, then append a concise `Execution Feedback:` section only when feedback is available.
- Derive feedback from evaluator-owned failure signals such as top-level contestant failure reason, per-profile capture reason, `window.__PLAYER_ERROR__`, selected browser diagnostics, and bounded tail lines from `contestant.log`.
- Sanitize and bound contestant-visible feedback so internal paths, run directories, Chromium version, thresholds, full Playwright stack traces, and organizer-only metrics remain out of `info`.
- Preserve organizer-facing diagnostics in `debug` for full auditability.
- Treat evaluator, host, infrastructure, and publication failures as untrusted result paths; their internal failure details remain in `debug`, not contestant-facing feedback.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `evaluator`: Update the Contest Platform Result Info contract so contestant-side execution failures can expose concise, sanitized feedback in `info`.

## Impact

- `result_info.py`: render an optional contestant feedback section in `info`.
- `scorer.py` / score shape: carry a structured or bounded `contestant_feedback` field into `score.json`.
- `scripts/evaluator.sh` and lifecycle helpers: collect bounded contestant log excerpts on contestant-side startup failures.
- `runner.py` / capture artifacts: expose sanitized capture/readiness failure feedback for scorer consumption.
- Tests and docs: update result.info renderer tests, shell publication tests, OpenSpec evaluator spec, and README artifact description.
