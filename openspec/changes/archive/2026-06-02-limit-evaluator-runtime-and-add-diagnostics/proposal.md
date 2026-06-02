## Why

Some submissions can make the evaluator spend far longer than the intended two 30-second profile capture windows, tying up execution machines and leaving incomplete result directories when an operator interrupts the run. At the same time, screenshot disputes are hard to diagnose because current artifacts record the final clip rectangle but not enough page/layout state to explain partial captures, black first frames, or content cropped inside the player host element.

## What Changes

- Add explicit wall-clock budgets around the per-profile capture and analysis stages, plus an overall evaluator budget, so pathological submissions fail boundedly and still publish `score.json`, `report.html`, and `result.info` where possible.
- Preserve existing scoring formulas: timeout/overrun diagnostics explain why FPS or a profile round failed, but do not award extra score.
- Extend `runner.py` diagnostics with structured page/player layout snapshots around readiness and capture setup, including viewport, device pixel ratio, player host box, relevant CSS, child media/canvas boxes, and readiness/error state.
- Record stage start/end/timeout information in run artifacts and evaluator logs so operators can tell whether a long run was caused by contestant startup, capture, analysis, scoring, or cleanup.
- Surface timeout and layout diagnostics in reports for organizer review.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `evaluator`: add bounded per-stage execution, timeout classification, and richer capture/layout diagnostics to the existing evaluator workflow.

## Impact

- Affected code: `scripts/evaluator.sh`, `runner.py`, `analyzer.py`, `scorer.py`, `report.py`, `scripts/diagnose_run.py`, and focused tests under `tests/`.
- Affected artifacts: per-profile `timestamps.json`, optional run-level stage timing diagnostics, per-profile metrics pass-through, `score.json` profile reasons, `report.html`, and `result.info` debug output.
- No new external runtime dependencies are expected.
- No contestant API change is required beyond better enforcement and diagnosis of the existing `[data-testid="player-video"]` contract.
