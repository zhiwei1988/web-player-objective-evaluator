## Why

The evaluator currently requests 750 screenshots for a 30 second 25 fps round, but Playwright/Chrome screenshot plus JPEG encoding takes roughly 50 ms per shot on the observed host, stretching captures to 38-40 seconds and capping observable sampling near 19 shots/s. This makes `measured_fps = unique_frame_count / actual_capture_duration` reflect evaluator screenshot throughput as much as contestant playback throughput, especially at 4K.

## What Changes

- Reduce the evaluator's default capture cost while preserving the contestant contract that `[data-testid="player-video"]` renders at least `1280x720` and remains proportionally uncropped.
- Add an explicit capture-throughput benchmark or diagnostic path so evaluator changes can be tested against shot interval, capture span, and recognition stability before changing scoring behavior.
- Introduce lower-cost capture strategies for the steady-state runner, with a preferred first step of rendering/capturing a smaller player viewport and an optional Chrome DevTools Protocol screenshot path for `optimizeForSpeed` experiments.
- Extend analyzer metrics with capture diagnostics such as screenshot sampling FPS, repeated-frame rate, frame-delta distribution, and frame-progress indicators for audit and tuning.
- Preserve the existing anti-cheat scoring semantics unless a later design proves a replacement FPS metric is robust against static, skipped, or synthetic frame-number behavior.
- Keep screenshot artifacts available for appeals, even if a future fast path samples browser pixels or writes downscaled artifacts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `evaluator`: Change capture runner requirements and metrics schema to support lower-cost capture, capture throughput diagnostics, and benchmarkable evidence that the evaluator can keep closer to the requested 25 fps sampling cadence without weakening correctness or anti-cheat checks.

## Impact

- Affected code: `runner.py`, `analyzer.py`, `scorer.py` if score/audit fields are surfaced, `report.py`, `scripts/evaluator.sh`, and focused tests under `tests/`.
- Affected specs: `openspec/specs/evaluator/spec.md`.
- Artifacts: metrics JSON and report output will gain capture diagnostics; screenshot naming and existing score fields should remain backward compatible where practical.
- Dependencies: no new runtime dependency is expected for the first pass. Chrome DevTools Protocol can be accessed through Playwright's existing CDP session support if selected in design.
