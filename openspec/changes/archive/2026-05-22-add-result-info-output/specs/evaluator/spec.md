## ADDED Requirements

### Requirement: Contest Platform Result Info

For every evaluator invocation that creates a run directory and produces `score.json`, the evaluator SHALL also produce a `result.info` file formatted after `reference/result-sample.info`. The evaluator SHALL write the audit copy to `results/<team_id>_<timestamp>/result.info` and SHALL copy the same bytes to `dirname <submission_zip>/result.info`. The evaluator SHALL NOT publish the platform-facing `result.info` under `submissions/<team_id>/`, because that directory is internal staging.

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
- 4K FPS: 1.5 / 5
- CPU: 7 / 10
|debug|...
```

`score` SHALL equal `score.json.objective_total` formatted without unnecessary trailing zeroes. `runtime` SHALL be the evaluator wall-clock runtime in milliseconds for the current invocation. `info` SHALL be contestant-visible and SHALL contain only the total objective score and the five scoring item point values: 2K correctness, 2K FPS, 4K correctness, 4K FPS, and CPU. `debug` SHALL be organizer-facing and MAY contain multi-line diagnostics such as run directory, failure reason, per-profile metrics, CPU gate details, and Chromium version.

`result` SHALL be `0` when the evaluator produced a valid contestant result, including valid zero-score outcomes caused by the contestant submission. `result` SHALL be `1` when an evaluator, host, infrastructure, or publication failure makes the score untrustworthy.

#### Scenario: Successful run writes and publishes result info

- **WHEN** `scripts/evaluator.sh team_ref /uploads/team_ref.zip` completes a normal scoring run and `/uploads/` is writable
- **THEN** `results/team_ref_<timestamp>/result.info` exists
- **THEN** `/uploads/result.info` exists with identical contents
- **THEN** `submissions/team_ref/result.info` is not required and is not the platform publication target
- **THEN** the `result.info` `score` field equals `score.json.objective_total`
- **THEN** the `info` block lists exactly 2K correctness, 2K FPS, 4K correctness, 4K FPS, and CPU item scores

#### Scenario: Contestant failure remains a valid zero-score result

- **WHEN** the evaluator produces a contestant-side failure score, such as `contestant_frontend_unavailable`
- **THEN** `result.info` exists in the run directory and in `dirname <submission_zip>`
- **THEN** `|result|0` is written
- **THEN** `|score|0` is written
- **THEN** the `info` block shows `Objective Score: 0 / 30` and all five scoring items as `0 / <max>`
- **THEN** the contestant-visible `info` block does not include internal failure reasons
- **THEN** the internal failure reason appears in `debug`

#### Scenario: Infrastructure failure is marked untrusted

- **WHEN** the evaluator reaches a run directory but fails because of evaluator host or infrastructure problems, such as RTSP infrastructure failure
- **THEN** `result.info` exists in the run directory
- **THEN** `|result|1` is written
- **THEN** `|score|0` is written unless a trustworthy score was already produced
- **THEN** the failure reason appears in `debug`

#### Scenario: Info block is contestant-facing only

- **WHEN** a reviewer inspects `result.info`
- **THEN** the `info` block contains no measured FPS, SSIM, watermark recognition rate, color check rate, CPU mean percent, thresholds, run directory, Chromium version, or raw failure reason
- **THEN** those diagnostics, when available, are confined to `debug`, `score.json`, or `report.html`

#### Scenario: Result info follows the sample field protocol

- **WHEN** `result.info` is parsed as line-oriented fields
- **THEN** `|result|`, `|score|`, and `|runtime|` are single-line fields
- **THEN** `|info|` appears alone on its line and its multi-line value continues until the `|debug|` marker
- **THEN** `|debug|` starts the final multi-line field
- **THEN** the explanatory comments from `reference/result-sample.info` are not included
