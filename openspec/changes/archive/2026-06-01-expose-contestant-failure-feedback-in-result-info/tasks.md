## 1. Renderer Tests

- [x] 1.1 Add failing `result_info.py` unit tests proving `score.json.contestant_feedback` renders as an `Execution Feedback:` section after the five scoring items.
- [x] 1.2 Add failing renderer tests proving no feedback section is emitted when feedback is absent or empty.
- [x] 1.3 Add failing renderer tests proving internal diagnostics such as run directory, Chromium version, measured metrics, thresholds, and raw internal reasons do not enter `info`.

## 2. Feedback Model and Sanitization

- [x] 2.1 Add tests for a helper that normalizes contestant feedback lines by stripping control characters, trimming whitespace, dropping empty lines, and enforcing per-line and total limits.
- [x] 2.2 Implement the feedback normalization helper in the scoring/evaluator layer so only sanitized bounded lines are written to `score.json.contestant_feedback`.
- [x] 2.3 Add tests proving infrastructure, host, evaluator, RTSP, and publication failure reasons are not converted into contestant feedback.

## 3. Startup Failure Feedback

- [x] 3.1 Add shell or Python tests for frontend-unavailable startup failures showing bounded tail lines from `contestant.log` are carried into `score.json.contestant_feedback`.
- [x] 3.2 Update `scripts/evaluator.sh` or lifecycle helpers to collect sanitized startup feedback when the submitted frontend never becomes reachable.
- [x] 3.3 Ensure missing `start.sh` and frontend readiness timeout paths still produce `result=0`, `score=0`, and now include contestant-facing feedback when available.

## 4. Browser Capture Failure Feedback

- [x] 4.1 Add tests proving per-profile capture reasons such as `startup timeout`, missing `data-testid=player-video`, and `window.__PLAYER_ERROR__` can produce sanitized contestant feedback.
- [x] 4.2 Update `runner.py`, profile artifacts, and `scorer.py` inputs as needed so sanitized capture feedback reaches `score.json.contestant_feedback`.
- [x] 4.3 Preserve full browser diagnostics in existing debug/profile artifacts without copying them verbatim into `info`.

## 5. Rendering and Publication

- [x] 5.1 Implement `result_info.py` rendering for the optional `Execution Feedback:` section.
- [x] 5.2 Update shell publication tests so the run-directory and upload-directory `result.info` copies contain identical feedback.
- [x] 5.3 Update existing tests that previously required raw contestant failure reasons to stay entirely out of `info` so they now allow sanitized feedback but still reject raw internal diagnostics.

## 6. Documentation and Validation

- [x] 6.1 Update README result artifact documentation to describe the optional sanitized contestant feedback section.
- [x] 6.2 Run focused tests for result info rendering, scorer feedback handling, and result.info publication.
- [x] 6.3 Run `openspec validate expose-contestant-failure-feedback-in-result-info --strict` and resolve validation issues.
