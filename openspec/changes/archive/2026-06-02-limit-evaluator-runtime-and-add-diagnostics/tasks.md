## 1. Timeout And Stage Timing Tests

- [x] 1.1 Add shell/unit coverage for default timeout budget constants and environment overrides used by `scripts/evaluator.sh`
- [x] 1.2 Add a regression test for stage timing JSON records with success, failure, timeout, and skipped statuses
- [x] 1.3 Add an evaluator integration test fixture where a capture stage exceeds its budget and the run still writes profile reason, stage timing, cleanup, and final score artifacts
- [x] 1.4 Add an evaluator integration test fixture where analysis times out after screenshots exist and the run preserves screenshots while omitting fabricated metrics

## 2. Capture Layout Diagnostic Tests

- [x] 2.1 Add unit tests for the layout diagnostic schema written into `timestamps.json`
- [x] 2.2 Add unit tests for derived layout warnings, including oversized canvas in clipped host, host/media transform, missing media/canvas, and zero intrinsic dimensions after readiness
- [x] 2.3 Add readiness-timeout coverage proving structured layout diagnostics are written when the page is reachable but `__PLAYER_READY__` never becomes true
- [x] 2.4 Add bounded-output coverage proving only a fixed maximum number of descendant media/canvas entries are recorded

## 3. Timeout Implementation

- [x] 3.1 Implement evaluator timeout budget constants and environment override parsing in `scripts/evaluator.sh`
- [x] 3.2 Add a reusable stage runner helper that records start/end/status/reason into `stage_timings.json`
- [x] 3.3 Wrap per-profile `runner.py` invocations with the capture timeout and propagate `capture timeout after <N>s` as the profile reason
- [x] 3.4 Wrap per-profile `analyzer.py` invocations with the analysis timeout and propagate `analysis timeout after <N>s` as the profile reason
- [x] 3.5 Wrap scoring/report generation with the scoring timeout and preserve existing cleanup/result publication behavior
- [x] 3.6 Add total evaluator body timeout handling that triggers cleanup and releases the evaluator lock

## 4. Layout Diagnostic Implementation

- [x] 4.1 Implement bounded Playwright page layout snapshot collection in `runner.py`
- [x] 4.2 Add warning derivation for likely partial capture and misleading readiness cases
- [x] 4.3 Store `layout_diagnostics` in `timestamps.json` on success and reachable readiness timeout paths
- [x] 4.4 Preserve existing `browser_errors` behavior while moving structured page state out of ad hoc diagnostic strings where practical

## 5. Diagnostic Surfacing

- [x] 5.1 Update `report.py` to render compact stage timeout and layout warning summaries with links to raw artifacts
- [x] 5.2 Update `scripts/diagnose_run.py` to print stage timing and layout warning summaries while remaining backward compatible
- [x] 5.3 Update `result_info.py` only for concise timeout/profile-reason debug output, avoiding full internal layout dumps

## 6. Verification

- [x] 6.1 Run focused unit tests for timeout helpers, stage timing serialization, layout diagnostics, report rendering, and diagnose output
- [x] 6.2 Run evaluator regression cases for normal completion, capture timeout, analysis timeout, and older-result backward compatibility
- [x] 6.3 Run `openspec validate limit-evaluator-runtime-and-add-diagnostics --strict`
