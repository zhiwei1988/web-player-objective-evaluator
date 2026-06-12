## Why

Decode-path violations currently zero profile correctness and FPS, but the contestant-visible `result.info` info block only shows the resulting score and generic runtime metrics. This makes valid zero-score outcomes look like image-analysis or scoring bugs unless an organizer inspects `score.json` or `report.html`.

## What Changes

- Add contestant-visible decode-path violation lines to the `result.info` info block when one or more profiles are zeroed by `decode_path.verdict = "violation"`.
- Include the affected profile label and a concise, sanitized reason derived from the profile's `decode_path` evidence/checks.
- Keep non-violation verdicts (`ok`, `inconclusive`, absent) out of the contestant-visible info block.
- Preserve the existing `debug` field diagnostics and artifact schema.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `evaluator`: Change the Contest Platform Result Info requirement so decode-path violations are visible in the `info` field.

## Impact

- `result_info.py` info rendering logic.
- `tests/test_result_info.py` and any result-info publication tests that assert rendered text.
- `openspec/specs/evaluator/spec.md` after the change is implemented and archived.
