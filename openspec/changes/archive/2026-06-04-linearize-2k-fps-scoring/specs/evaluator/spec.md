## MODIFIED Requirements

### Requirement: Scoring

`scorer.py` SHALL accept one or more `--metrics PROFILE=PATH` arguments (one per profile, e.g. `--metrics 2k=results/.../2k_metrics.json --metrics 4k=results/.../4k_metrics.json`), `--output`, and `--report`; score 2K for up to 10 points (5 correctness + 5 FPS), score 4K for up to 15 points (5 correctness + 10 FPS), compute an additional 0-5 point CPU sub-score based on contestant CPU usage measured during the **2K profile** round (see the Contestant CPU Usage Measurement requirement); and produce a `score.json` containing one block per profile (keys `2k`, `4k`), a top-level `cpu` block, a top-level `gate` block, `objective_total`, and `max_score` of `30`.

**Decode-path gate** (first, per profile, UNCHANGED): when the profile's `metrics.decode_forensics.verdict == "violation"`, that profile's `correctness_points` AND `fps_points` SHALL both be `0` regardless of measured rates or FPS, and the profile block SHALL include a `decode_path` sub-block (`verdict`, `checks`, `evidence`). When the violating profile is `2k`, the CPU block SHALL additionally be set to `points=0, gated=true, gate_reason="decode_path_violation"`. A verdict of `ok`, `inconclusive`, or an absent `decode_forensics` block SHALL NOT affect scoring (fail-open); an `inconclusive` verdict on any profile SHALL set top-level `score.json.review_required = true`.

**Correctness** (per profile): full 5 when `watermark_recognition_rate >= 0.95` AND `color_check_rate >= 0.95` AND `mean_ssim >= 0.90`; partial 2 when `watermark_recognition_rate >= 0.80` AND `mean_ssim >= 0.75`; otherwise 0. This applies to both 2K and 4K. 4K correctness SHALL remain scored even when 4K FPS is low — EXCEPT when that profile's decode-path verdict is `violation`.

**FPS** (linear absolute, both profiles): each profile's expected FPS SHALL come from `PROFILES[profile].fps` and default to `20`. Each profile's per-profile FPS full score SHALL come from `FPS_LINEAR_FULL_SCORE_BY_PROFILE` (`{"2k": 5, "4k": 10}`) as the single source of truth. `score.json.<profile>.fps_points` SHALL equal `round(min(max(measured_fps, 0) / expected_fps, 1.0) * FPS_LINEAR_FULL_SCORE_BY_PROFILE[profile], 2)`. Each profile block SHALL record `fps_scoring_mode = "linear_absolute"`, `fps_linear_full_score` (5 for 2K, 10 for 4K), and `expected_fps`. No profile block SHALL carry `fps_full_threshold_used` or `fps_partial_threshold_used`; threshold-based 2K FPS scoring and the `FPS_FULL_RATIO_BY_PROFILE` / `FPS_PARTIAL_RATIO_BY_PROFILE` tables are removed.

**CPU**: `scorer.score_cpu(...)` SHALL return an integer in `[0, 5]` with a nullable `gate_reason`, computed exactly as before (fps floor → `sampler_no_data` → full/zero/partial bands). The CPU block SHALL record `measured_on_profile = "2k"`, `gate_profile = "2k"`, `expected_fps`, `measured_fps`, `thresholds_used`, `mean_percent`, `sample_count`, and the existing audit fields.

**Level-0 gate conditions the total** (see the Level-0 Gate (Decode Correctness) requirement). The gate passes iff `2k.correctness_points == 5` AND `4k.correctness_points == 5`.

- **When the gate PASSES**, each profile's `total` SHALL equal `correctness_points + fps_points`, the CPU sub-score SHALL be scored normally, and `objective_total` SHALL equal `2k.total + 4k.total + cpu.points` (the full 30-point scheme).
- **When the gate FAILS**, level-1 SHALL NOT contribute: each profile's `total` SHALL equal its `correctness_points` alone (FPS still computed and recorded under `fps_points`, but excluded from `total`), the CPU block SHALL be set to `points=0, gated=true, gate_reason="gate_failed"` (UNLESS a more specific reason already applies — `decode_path_violation`, `2k_fps_below_threshold`, `sampler_no_data`, `2k_round_failed`, `container_mode_unsupported`, `host_failure` — which is retained), and `objective_total` SHALL equal `2k.correctness_points + 4k.correctness_points`.

`max_score` SHALL remain `30`. Because reaching `30` requires both full correctness (gate pass) and full level-1 performance, decode correctness alone caps the total at `≤ 10` ("level-0 has no full marks"). `objective_total` MAY be fractional (FPS is fractional for both profiles) and SHALL be rounded consistently for display. No profile block SHALL carry `reason = "skipped_gate_failed"` (both profiles are always captured per the Evaluator Entry Script requirement).

#### Scenario: Per-profile totals and the gate block

- **WHEN** scoring completes for a submission
- **THEN** `score.json` includes a `2k` block, a `4k` block, a top-level `cpu` block, a top-level `gate` block, `max_score = 30`, and an `objective_total`; the underlying metrics (rates, mean SSIM, measured FPS, `fps_points`, CPU mean percent, sample count, thresholds/formulas) are preserved for audit; the keys `h264` / `h265` SHALL NOT appear

#### Scenario: Perfect run scores the full 30

- **WHEN** both profiles reach full correctness and full FPS and CPU is at full marks (`2k`: 5 + 5, `4k`: 5 + 10, `cpu`: 5)
- **THEN** `score.json.gate.passed = true`, `score.json.2k.total = 10`, `score.json.4k.total = 15`, `score.json.cpu.points = 5`, and `objective_total = 30`

#### Scenario: Gate passes but performance is poor

- **WHEN** both profiles reach full correctness (`gate.passed = true`) but `2k.fps_points = 0`, `4k.fps_points = 0`, and CPU scores `0`
- **THEN** `score.json.2k.total = 5`, `score.json.4k.total = 5`, `objective_total = 10`, and the FPS/CPU values are recorded — the gate is open, the level-1 points were simply not earned

#### Scenario: Gate fails — only correctness counts

- **WHEN** `2k.correctness_points = 5` but `4k.correctness_points = 2` (so the gate fails)
- **THEN** `score.json.gate.passed = false`, `score.json.2k.total = 5`, `score.json.4k.total = 2`, the `fps_points` fields are still recorded but excluded from the totals, `score.json.cpu.points = 0` with `cpu.gated = true` and `cpu.gate_reason = "gate_failed"`, and `objective_total = 7`

#### Scenario: Decode-path violation fails the gate and keeps decode-path precedence

- **WHEN** the 4K round's `decode_forensics.verdict == "violation"` (so `4k.correctness_points = 0` and the gate fails)
- **THEN** `objective_total = 2k.correctness_points` (4K contributes 0), level-1 is not scored, and a 2K `violation` (if present) keeps `cpu.gate_reason = "decode_path_violation"` rather than `"gate_failed"`

#### Scenario: OK or inconclusive verdict does not change scoring

- **WHEN** a profile's decode-path verdict is `ok`, `inconclusive`, or absent
- **THEN** that profile is scored on its measured metrics exactly as without forensics; an `inconclusive` verdict additionally sets `score.json.review_required = true`

#### Scenario: Fake-overlay submission

- **WHEN** a submission draws a fake canvas overlay that fails DataMatrix, color block, or SSIM checks
- **THEN** it cannot reach full correctness (5) on that profile, the gate fails, and level-1 is not scored

#### Scenario: 2K FPS is scored linearly out of 5

- **WHEN** the 2K round measures `measured_fps = 10` against `expected_fps = 20`
- **THEN** `score.json.2k.fps_points = round(min(10 / 20, 1.0) * 5, 2) = 2.5`, the 2K block records `fps_scoring_mode = "linear_absolute"` and `fps_linear_full_score = 5`, and no `fps_full_threshold_used` / `fps_partial_threshold_used` field is present

#### Scenario: FPS at or above expected caps at the profile full score

- **WHEN** a profile measures `measured_fps >= expected_fps`
- **THEN** that profile's `fps_points` equals its `fps_linear_full_score` exactly (5 for 2K, 10 for 4K), never more

#### Scenario: Missing profile entry fails loudly

- **WHEN** `scorer.score_fps` is invoked with a profile name absent from `PROFILES`
- **THEN** `score_fps` raises a clear error (e.g. `KeyError`) rather than silently scoring against a default

### Requirement: Report Generation

`report.py` SHALL generate an internal-only `report.html` per run, containing: a top summary with `objective_total` and per-profile subtotals (`2k` and `4k`); a gallery of suspicious screenshots (watermark recognition failures, color block failures, samples with SSIM `< 0.7`); a frame-number-over-time chart for each profile; an SSIM histogram for each profile; capture-throughput diagnostics for each profile; CPU scoring details identifying that CPU was measured on the 2K profile; the 2K and 4K linear absolute FPS formulas and applied expected FPS; and relative-path links to the raw artifacts. The report MUST NOT be exposed to contestants by default.

#### Scenario: Report includes audit evidence

- **WHEN** scoring completes
- **THEN** `report.html` opens in a browser, renders all charts and galleries from local files only (no network calls), shows capture sampling FPS and capture overrun diagnostics for each completed profile, shows CPU measured on profile `2k`, shows the 2K and 4K linear FPS formulas, and links to `2k_screenshots/`, `4k_screenshots/`, both metrics JSONs, and `score.json`

#### Scenario: Empty failure gallery

- **WHEN** a submission passes every check
- **THEN** the report still renders, with the suspicious-screenshot gallery showing zero entries and a clear "no failures" message

### Requirement: Contest Platform Result Info

For every evaluator invocation that creates a run directory and produces `score.json`, the evaluator SHALL also produce a `result.info` file formatted after `reference/result-sample.info`. The evaluator SHALL write the audit copy to `results/<team_id>_<timestamp>/result.info` and SHALL copy the same bytes to `dirname <submission_zip>/result.info`.

`result.info` SHALL contain the fields `result`, `score`, `runtime`, `info`, and `debug` in that order:

```text
|result|0
|score|23.5
|runtime|64231
|info|
Objective Score: 23.5 / 30
Breakdown:
- 2K Correctness: 5 / 5
- 2K FPS: 5.00 / 5
- 4K Correctness: 5 / 5
- 4K FPS: 3.00 / 10
- CPU: 3.00 / 5

Runtime Metrics:
2k: measured_fps=30
4k: measured_fps=27
cpu: mean_percent=61

Capture Status:
- 2K: completed
- 4K: completed
|debug|...
```

`score` SHALL equal `score.json.objective_total` formatted without unnecessary trailing zeroes. `runtime` SHALL be the evaluator wall-clock runtime in milliseconds for the current invocation. `info` SHALL be contestant-visible and SHALL contain the total objective score and the five scoring item point values (2K correctness, 2K FPS, 4K correctness, 4K FPS, and CPU). The `info` block SHALL also contain a per-profile `Capture Status:` section that is always present, listing one line per profile (`2K` and `4K`) with that profile's terminal capture `phase` and its verbatim `detail` when present. The `info` block MAY contain a `Runtime Metrics:` section exposing measured FPS and CPU mean percent when those values are available, and, when available for contestant-side execution failures, a concise sanitized `Execution Feedback:` section. The 2K FPS, 4K FPS, and CPU item scores SHALL each be formatted with exactly two digits after the decimal point; the correctness item scores SHALL be formatted as integers. `debug` SHALL be organizer-facing and MAY contain multi-line diagnostics such as run directory, failure reason, per-profile metrics, CPU gate details, and Chromium version.

`result` SHALL be `0` when the evaluator produced a valid contestant result, including valid zero-score outcomes caused by the contestant submission. `result` SHALL be `1` when an evaluator, host, infrastructure, or publication failure makes the score untrustworthy.

The authorized `Capture Status:` and `Runtime Metrics:` sections are exempt from the `info` sanitization prohibition: the `Capture Status:` block MAY carry raw per-profile capture phase labels (including internal-only phases such as `forensics_installed`) and the verbatim `detail` dict, which MAY include Chromium version, URLs, selectors, clip coordinates, timeouts, filesystem-relative artifact paths, and internal failure reasons; the `Runtime Metrics:` block MAY carry measured FPS and CPU mean percent. Outside those authorized sections, the evaluator SHALL NOT place raw internal diagnostics in `info`. In particular, the `Execution Feedback:` section MUST be bounded and sanitized before it is written to `score.json` or `result.info`; it MUST NOT include run directories, host filesystem paths, Chromium version, scoring thresholds, measured FPS, SSIM, watermark recognition rate, color check rate, CPU mean percent, full Playwright stack traces, or organizer-only failure details.

#### Scenario: Successful run writes and publishes result info

- **WHEN** `scripts/evaluator.sh team_ref /uploads/team_ref.zip` completes a normal scoring run and `/uploads/` is writable
- **THEN** `results/team_ref_<timestamp>/result.info` exists
- **THEN** `/uploads/result.info` exists with identical contents
- **THEN** the `result.info` `score` field equals `score.json.objective_total`
- **THEN** the `info` block lists 2K correctness, 2K FPS, 4K correctness, 4K FPS, and CPU item scores
- **THEN** the 2K FPS, 4K FPS, and CPU items are each formatted with exactly two digits after the decimal point
- **THEN** the `info` block contains a `Capture Status:` section listing both `2K` and `4K`, each showing phase `completed`
- **THEN** the `info` block does not contain an `Execution Feedback:` section unless contestant-side execution feedback was recorded

#### Scenario: Contestant failure remains a valid zero-score result

- **WHEN** the evaluator produces a contestant-side failure score, such as `contestant_frontend_unavailable`
- **THEN** `result.info` exists in the run directory and in `dirname <submission_zip>`
- **THEN** `|result|0` is written
- **THEN** `|score|0` is written
- **THEN** the `info` block shows `Objective Score: 0 / 30`, the 2K FPS item as `0.00 / 5`, the 4K FPS item as `0.00 / 10`, the CPU item as `0.00 / 5`, and the correctness items as `0 / 5`
- **THEN** the `info` block includes an `Execution Feedback:` section when sanitized contestant feedback is available
- **THEN** the internal raw failure reason appears in `debug`

#### Scenario: Contestant startup log tail is surfaced safely

- **WHEN** the contestant frontend never becomes reachable and `contestant.log` contains output from the submitted `start.sh`
- **THEN** `score.json` records bounded sanitized contestant feedback derived from the startup failure and log tail
- **THEN** `result.info` includes that feedback under `Execution Feedback:` in the `info` block
- **THEN** the feedback is limited in size and excludes host paths, run directories, control characters, and organizer-only diagnostics

#### Scenario: Browser readiness failure is surfaced safely

- **WHEN** a profile capture fails because the page never sets `window.__PLAYER_READY__`, the player element is missing, or `window.__PLAYER_ERROR__` contains a contestant-facing error
- **THEN** `score.json` records bounded sanitized contestant feedback derived from the profile failure
- **THEN** `result.info` includes that feedback under `Execution Feedback:` in the `info` block
- **THEN** raw browser diagnostics and full Playwright errors remain available in `debug`, profile artifacts, or internal logs rather than being copied verbatim into the `Execution Feedback:` section

#### Scenario: Capture status section is always present for both profiles

- **WHEN** `result.info` is produced for any run that created the per-profile screenshot directories
- **THEN** the `info` block contains a `Capture Status:` section
- **THEN** the section lists exactly one line for `2K` and one line for `4K`, in that order
- **THEN** each line shows the terminal `phase` recorded in `<profile>_screenshots/capture_status.json`
- **THEN** when that status carries a `detail` object, the line renders the `detail` verbatim
- **THEN** internal-only phase labels such as `forensics_installed` and detail values such as Chromium version, URLs, selectors, and clip coordinates are passed through unchanged rather than sanitized

#### Scenario: Missing capture status is rendered explicitly

- **WHEN** a profile's `<profile>_screenshots/capture_status.json` is absent because that profile never began capture
- **THEN** the `info` block's `Capture Status:` line for that profile renders a stable placeholder such as `not run`
- **THEN** rendering does not fail and the other profile's line is still produced

#### Scenario: Infrastructure failure is marked untrusted

- **WHEN** the evaluator reaches a run directory but fails because of evaluator host or infrastructure problems, such as RTSP infrastructure failure
- **THEN** `result.info` exists in the run directory
- **THEN** `|result|1` is written
- **THEN** `|score|0` is written unless a trustworthy score was already produced
- **THEN** the failure reason appears in `debug`
- **THEN** the `Execution Feedback:` section, if present, does not expose infrastructure, host, evaluator, or publication failure details as contestant feedback

#### Scenario: Sanitization applies to execution feedback, not authorized blocks

- **WHEN** a reviewer inspects `result.info`
- **THEN** any `Execution Feedback:` lines in `info` are concise, sanitized messages that contain no measured FPS, SSIM, watermark recognition rate, color check rate, CPU mean percent, thresholds, run directory, Chromium version, full Playwright stack trace, or raw internal failure reason
- **THEN** those sanitization constraints do NOT apply to the authorized `Capture Status:` and `Runtime Metrics:` sections, which MAY contain raw capture phase/detail and measured runtime values
- **THEN** full Playwright stack traces, full layout diagnostics, and internal browser logs remain confined to `debug`, `score.json`, `report.html`, run logs, or profile artifacts

#### Scenario: Result info follows the sample field protocol

- **WHEN** `result.info` is parsed as line-oriented fields
- **THEN** `|result|`, `|score|`, and `|runtime|` are single-line fields
- **THEN** `|info|` appears alone on its line and its multi-line value continues until the `|debug|` marker
- **THEN** `|debug|` starts the final multi-line field
- **THEN** the explanatory comments from `reference/result-sample.info` are not included
