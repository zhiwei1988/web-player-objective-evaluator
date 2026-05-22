## 1. Test Coverage

- [x] 1.1 Add unit tests for the `result.info` renderer covering the exact field order, standalone `|info|` marker, multi-line `info`, final `|debug|` field, and omission of sample explanatory comments
- [x] 1.2 Add unit tests that normal score data renders the five contestant-visible item scores: 2K correctness, 2K FPS, 4K correctness, 4K FPS, and CPU
- [x] 1.3 Add unit tests that contestant-side zero-score failures render `result=0`, `score=0`, all five item scores as zero, no raw failure reason in `info`, and the raw reason in `debug`
- [x] 1.4 Add unit tests that infrastructure/evaluator failures render `result=1`, preserve or default score appropriately, and include the failure reason in `debug`
- [x] 1.5 Add evaluator integration or shell-level tests proving `result.info` is written to `results/<team_id>_<timestamp>/result.info` and copied to `dirname <submission_zip>/result.info`, not to `submissions/<team_id>/` as the publication target

## 2. Renderer Implementation

- [x] 2.1 Add a focused Python renderer module (for example `result_info.py`) with a pure function that formats `result.info` from `score.json` data plus runtime and run metadata
- [x] 2.2 Implement score formatting without unnecessary trailing zeroes for total and item scores
- [x] 2.3 Implement contestant-visible `info` formatting with only total score and five item scores
- [x] 2.4 Implement organizer-facing `debug` formatting with run directory, top-level reason, per-profile measured diagnostics when present, CPU gate diagnostics when present, and Chromium version when present
- [x] 2.5 Add a CLI entry point for the renderer that reads `score.json`, accepts runtime/run metadata, and writes `result.info`

## 3. Evaluator Integration

- [x] 3.1 Capture evaluator wall-clock start/end time in `scripts/evaluator.sh` and compute runtime milliseconds for `result.info`
- [x] 3.2 Invoke the renderer after any `score.json` is produced, including failure-score paths handled by cleanup
- [x] 3.3 Write the run-directory audit copy at `${RUN_DIR}/result.info`
- [x] 3.4 Copy the same `result.info` bytes to `$(dirname "$SUBMISSION_ZIP")/result.info`
- [x] 3.5 Treat copy/render failures as evaluator-side failures, logging enough context for operators
- [x] 3.6 Preserve existing stdout behavior that prints `score.json`

## 4. Documentation and Verification

- [x] 4.1 Update README artifact documentation to include `result.info` and its publication target
- [x] 4.2 Run focused renderer and evaluator tests
- [x] 4.3 Run `openspec validate add-result-info-output --strict` and resolve any spec formatting issues
