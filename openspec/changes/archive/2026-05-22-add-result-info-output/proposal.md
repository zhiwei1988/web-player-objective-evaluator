## Why

The contest platform expects a human-readable `result.info` file next to the submitted zip, while the evaluator currently only emits `score.json` and internal `report.html` under the run directory. Contestants need to see each scoring item directly, not just a total score.

## What Changes

- Add a `result.info` artifact for every evaluator run, formatted after `reference/result-sample.info`.
- Write `result.info` under `results/<team_id>_<timestamp>/` for auditability.
- Copy `result.info` to the directory containing the submitted zip (`dirname <submission_zip>/result.info`) for contest-platform consumption.
- Keep `score.json` and `report.html` as the authoritative internal artifacts; `result.info` is a contestant/platform-facing projection of the score.
- Populate `|info|` as a multi-line contestant-visible score breakdown containing only the total score and each scoring item's points.
- Populate `|debug|` with organizer-facing diagnostics such as run directory, failure reason, per-profile measured values, CPU gate details, and Chromium version.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `evaluator`: Add the `result.info` output contract and publication behavior.

## Impact

- Affected code: `scripts/evaluator.sh`, scoring/result formatting code, and focused tests under `tests/`.
- Affected artifacts: every completed run gains `results/<team_id>_<timestamp>/result.info`; the submitted zip's parent directory gains or overwrites `result.info`.
- Affected docs/specs: evaluator artifact and entry-script requirements need to mention `result.info`.
- No dependency or CLI argument changes are expected.
