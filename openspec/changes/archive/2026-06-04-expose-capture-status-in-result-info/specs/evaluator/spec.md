## MODIFIED Requirements

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
- 2K FPS: 5 / 5
- 4K Correctness: 5 / 5
- 4K FPS: 3.0 / 10
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

`score` SHALL equal `score.json.objective_total` formatted without unnecessary trailing zeroes. `runtime` SHALL be the evaluator wall-clock runtime in milliseconds for the current invocation. `info` SHALL be contestant-visible and SHALL contain the total objective score and the five scoring item point values (2K correctness, 2K FPS, 4K correctness, 4K FPS, and CPU). The `info` block SHALL also contain a per-profile `Capture Status:` section that is always present, listing one line per profile (`2K` and `4K`) with that profile's terminal capture `phase` and its verbatim `detail` when present. The `info` block MAY contain a `Runtime Metrics:` section exposing measured FPS and CPU mean percent when those values are available, and, when available for contestant-side execution failures, a concise sanitized `Execution Feedback:` section. The CPU item score SHALL be formatted with exactly two digits after the decimal point. `debug` SHALL be organizer-facing and MAY contain multi-line diagnostics such as run directory, failure reason, per-profile metrics, CPU gate details, and Chromium version.

`result` SHALL be `0` when the evaluator produced a valid contestant result, including valid zero-score outcomes caused by the contestant submission. `result` SHALL be `1` when an evaluator, host, infrastructure, or publication failure makes the score untrustworthy.

The authorized `Capture Status:` and `Runtime Metrics:` sections are exempt from the `info` sanitization prohibition: the `Capture Status:` block MAY carry raw per-profile capture phase labels (including internal-only phases such as `forensics_installed`) and the verbatim `detail` dict, which MAY include Chromium version, URLs, selectors, clip coordinates, timeouts, filesystem-relative artifact paths, and internal failure reasons; the `Runtime Metrics:` block MAY carry measured FPS and CPU mean percent. Outside those authorized sections, the evaluator SHALL NOT place raw internal diagnostics in `info`. In particular, the `Execution Feedback:` section MUST be bounded and sanitized before it is written to `score.json` or `result.info`; it MUST NOT include run directories, host filesystem paths, Chromium version, scoring thresholds, measured FPS, SSIM, watermark recognition rate, color check rate, CPU mean percent, full Playwright stack traces, or organizer-only failure details.

#### Scenario: Successful run writes and publishes result info

- **WHEN** `scripts/evaluator.sh team_ref /uploads/team_ref.zip` completes a normal scoring run and `/uploads/` is writable
- **THEN** `results/team_ref_<timestamp>/result.info` exists
- **THEN** `/uploads/result.info` exists with identical contents
- **THEN** the `result.info` `score` field equals `score.json.objective_total`
- **THEN** the `info` block lists 2K correctness, 2K FPS, 4K correctness, 4K FPS, and CPU item scores
- **THEN** the `info` block contains a `Capture Status:` section listing both `2K` and `4K`, each showing phase `completed`
- **THEN** the `info` block does not contain an `Execution Feedback:` section unless contestant-side execution feedback was recorded

#### Scenario: Contestant failure remains a valid zero-score result

- **WHEN** the evaluator produces a contestant-side failure score, such as `contestant_frontend_unavailable`
- **THEN** `result.info` exists in the run directory and in `dirname <submission_zip>`
- **THEN** `|result|0` is written
- **THEN** `|score|0` is written
- **THEN** the `info` block shows `Objective Score: 0 / 30`, the CPU item as `0.00 / 5`, and the remaining scoring items as `0 / <max>`
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

### Requirement: Runtime Diagnostics Surfacing

The evaluator SHALL surface stage timing, timeout, capture status, and capture layout diagnostics in organizer-facing artifacts without changing contestant-facing scoring semantics. `report.html` SHALL include a compact per-profile diagnostic summary and links to raw diagnostic artifacts. `scripts/diagnose_run.py` SHALL include stage timing, capture status, and layout warning summaries when those artifacts are present, while continuing to handle older result directories where the new fields are absent. When `timestamps.json` is absent or lacks `layout_diagnostics`, organizer-facing diagnostics SHALL fall back to the standalone `<profile>_screenshots/layout_diagnostics.json` artifact when present.

`result.info` SHALL remain concise. It MAY include timeout/profile reasons, high-signal debug summaries, and a per-profile capture status section (the terminal capture `phase` and its raw `detail`) in the contestant-facing `info` block, but SHALL NOT expose full layout diagnostics or internal browser logs as contestant-facing information.

#### Scenario: Report highlights timeout source

- **WHEN** a profile capture or analysis stage times out
- **THEN** `report.html` shows which profile and stage timed out, the effective timeout budget, and links to the available raw artifacts for that profile
