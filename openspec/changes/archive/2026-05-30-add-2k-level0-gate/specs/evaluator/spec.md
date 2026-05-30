## ADDED Requirements

### Requirement: Level-0 Gate (2K)

The 2K `correctness_points` and 2K `fps_points` sub-scores SHALL act as a **level-0
gate** that conditions every downstream measurement (the 4K round and the CPU
sub-score) on a perfect 2K result. This gate layers on top of, and does not alter,
the per-sub-score scoring defined in the Scoring requirement.

**Gate criterion.** `scorer.py` SHALL expose `gate_passed(metrics_2k) -> bool`,
which returns `True` iff BOTH `score_correctness(...) == 5` AND
`score_fps(..., "2k") == 5` for the 2K metrics — i.e. the gate reuses the existing
full-mark thresholds with NO new tunables: `watermark_recognition_rate >= 0.95`
AND `color_check_rate >= 0.95` AND `mean_ssim >= 0.90` (full correctness), AND
`measured_fps / expected_fps >= FPS_FULL_RATIO_BY_PROFILE["2k"]` (full FPS). If the
2K metrics are absent or lack the fields the criterion inspects, the gate SHALL be
treated as FAILED.

**Effect of a passing gate.** When the gate passes, the 4K round SHALL be captured
and the 4K and CPU sub-scores SHALL be produced exactly as the Scoring requirement
defines. A passing gate changes nothing about downstream scoring.

**Effect of a failing gate.** When the gate fails:

- the 2K block SHALL retain its actual sub-scores (`correctness_points`,
  `fps_points`, and `total` are whatever 2K measured — `0`–`8` combined, since a
  combined `10` would have passed the gate);
- the 4K round SHALL NOT be captured, and the `4k` block SHALL be scored `0` with
  `reason = "skipped_gate_failed"`;
- the CPU block SHALL be set to `points = 0`, `gated = true`,
  `gate_reason = "gate_failed"`, even though the 2K capture window already produced
  CPU samples — UNLESS the CPU block is already gated for a more specific reason
  (`decode_path_violation`, `2k_fps_below_threshold`, `sampler_no_data`,
  `2k_round_failed`, `container_mode_unsupported`, `host_failure`), in which case
  that more-informative reason is retained. `gate_failed` therefore appears only
  when the CPU sub-score would otherwise have scored (2K produced metrics and CPU
  data, and 2K throughput cleared the CPU fps floor) but the level-0 gate failed on
  correctness or on a sub-full FPS that still cleared that floor;
- `objective_total` SHALL therefore equal the 2K block total alone.

**Precedence with the decode-path gate.** A 2K `decode_forensics.verdict ==
"violation"` already forces 2K `correctness_points = 0` and `fps_points = 0` (per the
Scoring requirement), which necessarily fails the level-0 gate. In that case the CPU
block's `gate_reason` SHALL remain `"decode_path_violation"` (the decode-path gate
takes precedence over `"gate_failed"`), and the 4K round SHALL still be skipped.

**Single source of the gate decision.** `build_score` SHALL derive the gate verdict
itself from the supplied 2K metrics and enforce the failing-gate effects above
regardless of whether 4K metrics were passed in — so that a manual `scorer.py`
invocation carrying both `2k` and `4k` metrics where 2K is not perfect still zeroes
the 4K and CPU blocks. `score.json` SHALL include a top-level `gate` block recording
at least `{ "profile": "2k", "passed": <bool>, "correctness_points": <int>,
"fps_points": <number> }`.

**Capture short-circuit (Evaluator Entry Script).** Notwithstanding the per-profile
capture loop described in the Evaluator Entry Script requirement, `scripts/evaluator.sh`
SHALL capture and analyze the gate profile (`2k`) FIRST (explicitly, not by relying
on alphabetical ordering of `PROFILES`), then query the gate via
`scorer.py --gate-check 2k=<2k_metrics.json>` (which SHALL exit `0` when the gate
passes and non-zero when it fails). When the gate fails, the script SHALL skip the
capture and analysis of all remaining profiles. This short-circuit is a wall-clock
optimization only: it SHALL NOT be able to change the emitted score, because
`build_score` derives and enforces the gate independently of which profiles were
captured.

#### Scenario: Perfect 2K passes the gate and unlocks 4K and CPU

- **WHEN** a submission's 2K round scores `correctness_points = 5` and `fps_points = 5`
- **THEN** `score.json.gate = { "profile": "2k", "passed": true, "correctness_points": 5, "fps_points": 5 }`, the 4K round is captured and scored normally, the CPU sub-score is computed normally, and `objective_total = 2k.total + 4k.total + cpu.points` (up to `30`)

#### Scenario: 2K short of full correctness fails the gate

- **WHEN** a submission's 2K round scores `correctness_points = 2` and `fps_points = 5`
- **THEN** `score.json.gate.passed = false`, the 2K block keeps `total = 7`, the 4K round is not captured and `score.json.4k.reason = "skipped_gate_failed"` with `4k.total = 0`, `score.json.cpu.points = 0` with `cpu.gated = true` and `cpu.gate_reason = "gate_failed"`, and `objective_total = 7`

#### Scenario: 2K short of full FPS fails the gate

- **WHEN** a submission's 2K round scores `correctness_points = 5` and `fps_points = 3` (FPS in the partial band, but `measured_fps / expected_fps` still clearing the CPU fps floor `CPU_GATE_FPS_RATIO`)
- **THEN** `score.json.gate.passed = false`, the 2K block keeps `total = 8`, `score.json.4k.total = 0` with `reason = "skipped_gate_failed"`, `score.json.cpu.gate_reason = "gate_failed"` with `points = 0`, and `objective_total = 8`

#### Scenario: 2K below the CPU fps floor keeps the more specific CPU reason

- **WHEN** a submission's 2K round has `measured_fps / expected_fps < CPU_GATE_FPS_RATIO` (so the gate fails AND the CPU fps floor is not met)
- **THEN** the gate fails and the 4K round is skipped, but `score.json.cpu.gate_reason = "2k_fps_below_threshold"` (the more specific reason) rather than `"gate_failed"`, with `cpu.points = 0`

#### Scenario: 2K round produced no metrics fails the gate

- **WHEN** no `2k_metrics.json` was produced (the 2K round failed entirely)
- **THEN** the gate is treated as failed, `score.json.gate.passed = false`, the 4K round is not captured, `score.json.cpu.points = 0` with `cpu.gated = true`, and `objective_total` reflects only whatever the 2K block contributes (0)

#### Scenario: 2K decode-path violation keeps decode-path precedence over the gate

- **WHEN** the 2K round's `decode_forensics.verdict == "violation"`
- **THEN** 2K `correctness_points = 0` and `fps_points = 0` (decode-path gate), the level-0 gate consequently fails, the 4K round is skipped, and `score.json.cpu.gate_reason = "decode_path_violation"` (NOT `"gate_failed"`) with `cpu.points = 0`

#### Scenario: --gate-check CLI exit code drives the short-circuit

- **WHEN** `scorer.py --gate-check 2k=<path>` is invoked on a 2K metrics file
- **THEN** it exits `0` when `gate_passed` is true and non-zero when it is false, with no other side effects, so `scripts/evaluator.sh` can decide whether to capture the remaining profiles

#### Scenario: Short-circuit cannot change the score

- **WHEN** the gate fails and `scripts/evaluator.sh` skips 4K capture, versus a manual `scorer.py` run that is nonetheless handed a `4k` metrics file alongside a non-perfect `2k`
- **THEN** both produce the same `score.json`: `4k.total = 0` (`reason = "skipped_gate_failed"` in the orchestrated case), `cpu.points = 0`, and `objective_total` equal to the 2K total, because `build_score` enforces the failing-gate effects independently of the captured profiles
