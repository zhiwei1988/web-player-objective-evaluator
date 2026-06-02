## ADDED Requirements

### Requirement: Stage Execution Budgets

The evaluator SHALL enforce bounded wall-clock execution budgets for long-running evaluation stages after the run directory has been created. By default, each per-profile capture runner invocation SHALL have a 120 second budget, each per-profile analyzer invocation SHALL have a 240 second budget, scoring/report generation SHALL have a 60 second budget, and the evaluator body from run-directory creation through score publication SHALL have a 600 second budget. The effective budgets SHALL be configurable by environment variables and SHALL be recorded in run artifacts.

When a per-profile capture or analysis stage times out, the evaluator SHALL record a profile-specific reason, preserve any partial artifacts already written, continue to later stages or profiles when possible, and still produce a schema-compatible `score.json`, `report.html`, and `result.info` when the scoring path can run. A stage timeout SHALL NOT fabricate successful metrics. Existing scoring formulas SHALL remain unchanged; missing metrics and profile reasons SHALL be handled through the existing failure/profile-reason scoring path.

The evaluator SHALL write a run-level machine-readable stage timing artifact under `results/<team_id>_<ts>/` containing one bounded record per major stage. Each record SHALL include at least stage name, profile when applicable, start epoch, end epoch when known, duration seconds when known, status (`success`, `failed`, `timeout`, or `skipped`), effective timeout seconds when applicable, exit code when available, and reason when available.

#### Scenario: Normal run records successful stages

- **WHEN** a submission completes both profile captures, both analyses, and scoring inside the default budgets
- **THEN** the run directory contains a stage timing artifact with successful records for 2K capture, 2K analysis, 4K capture, 4K analysis, scoring/report generation, and cleanup-relevant stages
- **THEN** `score.json` and `report.html` are produced using the existing scoring formulas

#### Scenario: Capture timeout is bounded and classified

- **WHEN** the 2K `runner.py` process exceeds the effective capture budget
- **THEN** the evaluator terminates that capture process, records the 2K profile reason as a capture timeout naming the budget, records a stage timing status of `timeout`, preserves any partial `2k_screenshots/` artifacts already written, and proceeds to the next profile when the host remains healthy
- **THEN** final scoring treats absent or incomplete 2K metrics as a failed 2K round rather than as successful playback

#### Scenario: Analyzer timeout does not block final score publication

- **WHEN** `analyzer.py` exceeds the effective analysis budget for a profile after screenshots were captured
- **THEN** the evaluator records an analysis timeout for that profile, leaves the raw screenshots and `timestamps.json` available for manual review, omits fabricated metrics for that profile, and continues toward final `score.json` publication

#### Scenario: Total evaluator timeout cleans up

- **WHEN** the evaluator body exceeds the effective total budget after creating a run directory
- **THEN** the evaluator runs its cleanup trap, terminates contestant and MediaMTX processes using the existing cleanup rules, releases the evaluator lock, and writes the clearest available failure artifacts without silently leaving the lock held

### Requirement: Capture Layout Diagnostics

`runner.py` SHALL write structured capture layout diagnostics into each profile's `timestamps.json`. The diagnostics SHALL be collected at least after readiness and before the steady-state screenshot loop, and SHALL be collected on readiness timeout when the page is reachable. The diagnostics SHALL be bounded and SHALL NOT dump the full DOM.

The diagnostics SHALL include at least: navigated URL, browser viewport, device pixel ratio, page scroll offsets, `document.readyState`, `window.__PLAYER_READY__`, `window.__PLAYER_ERROR__`, the final capture clip, the `[data-testid="player-video"]` host element bounding box and client/scroll/offset dimensions, selected host computed styles relevant to clipping and scaling, and a bounded list of descendant `<canvas>` and `<video>` elements with their bounding boxes, client dimensions, intrinsic media/canvas dimensions, IDs, data-testid values, and selected computed styles.

The diagnostics SHALL include derived warnings when the observed layout suggests likely partial capture or misleading readiness, including at least: host clip smaller than the contract size, descendant media/canvas larger than the host while host overflow clips, transform applied to the host or media element, descendant media/canvas missing, and readiness true while the primary media/canvas has zero intrinsic dimensions.

#### Scenario: Successful capture records player layout

- **WHEN** the contestant frontend reaches readiness and the player element is captured
- **THEN** `timestamps.json` contains `layout_diagnostics` with viewport, device pixel ratio, player host metrics, final clip, and a bounded list of descendant media/canvas metrics

#### Scenario: Oversized canvas inside clipped host is flagged

- **WHEN** `[data-testid="player-video"]` is `1280x720` but a descendant canvas is larger than the host and the host clips overflow
- **THEN** `timestamps.json.layout_diagnostics.warnings` contains a warning indicating that the screenshot may show only part of the rendered canvas

#### Scenario: Readiness timeout still records layout context

- **WHEN** the page loads but `window.__PLAYER_READY__` does not become `true` within the readiness window
- **THEN** `timestamps.json` records the startup timeout reason and includes whatever structured layout diagnostics can be collected from the page

#### Scenario: Diagnostics are bounded

- **WHEN** the contestant page contains many canvas or video elements
- **THEN** `runner.py` records only a fixed maximum number of descendant media/canvas entries and does not serialize arbitrary full DOM content

### Requirement: Runtime Diagnostics Surfacing

The evaluator SHALL surface stage timing, timeout, and capture layout diagnostics in organizer-facing artifacts without changing contestant-facing scoring semantics. `report.html` SHALL include a compact per-profile diagnostic summary and links to raw diagnostic artifacts. `scripts/diagnose_run.py` SHALL include stage timing and layout warning summaries when those artifacts are present, while continuing to handle older result directories where the new fields are absent.

`result.info` SHALL remain concise. It MAY include timeout/profile reasons and high-signal debug summaries, but SHALL NOT expose full layout diagnostics or internal browser logs as contestant-facing information.

#### Scenario: Report highlights timeout source

- **WHEN** a profile capture or analysis stage times out
- **THEN** `report.html` shows which profile and stage timed out, the effective timeout budget, and links to the available raw artifacts for that profile

#### Scenario: Report highlights likely partial capture

- **WHEN** `timestamps.json.layout_diagnostics.warnings` contains warnings about clipping, transform, zero intrinsic media size, or missing media/canvas elements
- **THEN** `report.html` displays a compact warning summary for that profile and links to the full `timestamps.json`

#### Scenario: Diagnose command remains backward compatible

- **WHEN** `scripts/diagnose_run.py` is run against an older result directory without stage timing or layout diagnostics
- **THEN** it still prints the existing score and FPS/capture-throughput diagnostics without failing
