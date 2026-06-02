## Context

The evaluator currently has fixed logical capture windows (`30s` per profile at `20fps`) but no outer wall-clock budget around `runner.py`, `analyzer.py`, or scoring/report generation. A slow or stuck helper can hold the evaluator lock, leave incomplete `results/<team>_<ts>/` directories, and require manual interruption.

The screenshot pipeline intentionally captures only `[data-testid="player-video"]` by a precomputed Playwright clip. That protects scoring from page-background cheats, but current artifacts only record the final clip rectangle and browser error strings. They do not explain whether a partial-looking screenshot came from the evaluator clip, contestant CSS layout, an oversized inner canvas, `object-fit: cover`, transforms, black warm-up frames, or readiness being signaled too early.

## Goals / Non-Goals

**Goals:**

- Bound evaluator wall-clock time for pathological submissions without changing the scoring formulas.
- Preserve cleanup and result publication on timeout whenever the process reaches a run directory.
- Make timeout source obvious: startup, capture, analysis, scoring/reporting, or cleanup.
- Record structured player layout diagnostics that can explain clipped, partial, black, or stale screenshots.
- Surface the new diagnostics in `report.html` and `scripts/diagnose_run.py` for organizer review.

**Non-Goals:**

- Do not change the contestant runtime contract, profile duration, source RTSP streams, or scoring thresholds.
- Do not switch from element-only screenshots to full-page screenshots.
- Do not add external runtime dependencies.
- Do not make layout diagnostics contestant-facing by default.

## Decisions

### Stage Budgets Live in the Evaluator Orchestrator

`scripts/evaluator.sh` should wrap external stage commands with `timeout --foreground --kill-after=10s`, not rely on each Python helper to self-police. The shell already owns cleanup, score publication, and per-profile reason propagation, so this keeps timeout handling at the same layer that writes failure scores.

Default budgets:

- capture runner per profile: `120s`
- analyzer per profile: `240s`
- scoring/report generation: `60s`
- total evaluator body after run-dir creation: `600s`

Each budget should be overridable with an environment variable for operator calibration, but the defaults must remain bounded in normal use.

Alternative considered: make `runner.py` stop at exactly `duration_s`. That is useful later, but it does not cover Playwright hangs, analyzer stalls, report generation, or interrupted shell state. A shell-level budget gives broad protection first.

### Timeouts Produce Normal Artifacts When Possible

When a per-profile stage times out, the evaluator should record a profile reason such as `capture timeout after 120s` or `analysis timeout after 240s`, continue to later profiles when possible, and let `scorer.py` build the final `score.json` from whatever metrics exist. If a runner timeout already wrote partial screenshots or `timestamps.json`, those artifacts should remain available for audit. If not, the evaluator should create a minimal stage record rather than fabricating successful metrics.

Alternative considered: fail the entire submission on the first profile timeout. Continuing is more useful for appeals because it can still show whether the other profile works and keeps the final score schema stable.

### Add Run-Level Stage Timings

Add a machine-readable stage timing artifact, for example `stage_timings.json`, under the run directory. It should contain bounded records for each stage: name, profile if applicable, start/end epoch, duration seconds, status (`success`, `failed`, `timeout`, `skipped`), command class, timeout budget, exit code, and reason.

The evaluator log should print concise start/end/timeout lines, but JSON should be the source for diagnostics and tests.

Alternative considered: only parse `evaluator.log`. Logs are useful to humans but brittle for tests and scripts.

### Capture Layout Diagnostics Belong in `timestamps.json`

`runner.py` already owns Playwright page state and writes `timestamps.json`; it should add a structured `layout_diagnostics` object there. At minimum it should snapshot:

- page URL, viewport, device pixel ratio, scroll offsets, document readiness, `window.__PLAYER_READY__`, and `window.__PLAYER_ERROR__`
- host `[data-testid="player-video"]` box, client/scroll/offset dimensions, and selected computed CSS (`display`, `visibility`, `position`, `overflow`, `object-fit`, `transform`, `width`, `height`)
- descendant `<canvas>` and `<video>` elements with bounding boxes, CSS dimensions, intrinsic dimensions (`canvas.width/height`, `video.videoWidth/videoHeight`), and IDs/test IDs
- the final capture clip and whether host or child dimensions suggest content could be cropped or scaled

The snapshot should be bounded: inspect only a small fixed number of descendant media/canvas elements and avoid dumping full DOM or source code.

Alternative considered: store diagnostics only in `browser_errors`. The existing string-based diagnostic is hard to query, truncates poorly, and mixes contestant console output with evaluator state.

### Reports Summarize, Not Duplicate, Diagnostics

`report.html` should show a compact per-profile diagnostic summary and link to raw JSON. It should highlight high-signal mismatches such as clip below viewport, child canvas larger than host, host overflow hidden, transform applied, or black/empty suspicious samples. The report should not inline huge JSON blocks.

## Risks / Trade-offs

- [Risk] Conservative budgets may still be too low for an unexpectedly slow canonical host. → Mitigation: expose environment overrides and record the effective budgets in artifacts.
- [Risk] Killing a timed-out runner can leave Chrome descendants briefly alive. → Mitigation: use `timeout --kill-after`, keep existing process-group cleanup, and verify lock file descriptors are not inherited.
- [Risk] Analyzer timeout means partial metrics are unavailable even when screenshots exist. → Mitigation: record profile reason and leave raw screenshots/timestamps for manual re-analysis.
- [Risk] Layout diagnostics may accidentally grow large. → Mitigation: cap arrays and fields, and avoid full DOM serialization.
- [Risk] A contestant can manipulate CSS between diagnostic snapshot and later screenshots. → Mitigation: snapshot at readiness/capture setup and record the fixed clip; this is audit evidence, not a security boundary.

## Migration Plan

No data migration is required. Existing result directories remain readable. New runs add optional diagnostics fields and stage timing artifacts; consumers should treat missing fields as pre-change runs.

## Open Questions

None.
